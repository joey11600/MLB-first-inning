#!/usr/bin/env python3
"""
backtest.py -- Historical backtester for the NRFI/YRFI Poisson model.

Replays the production model against past games using prior-season stats
only (leakage-free), grades against actual first-inning linescores, and
emits calibration + win-rate metrics by pick zone.

Why prior-season-only stats:
  The MLB StatsAPI's yearByYear / team_stats endpoints always return the
  current state of the requested season -- there is no historical
  "as of date X" query.  Querying full-2024 stats today would leak the
  rest-of-season into an April-2024 prediction.  Using full prior-year
  stats is leakage-free and matches the early-season behavior of the
  production model (which already shrinks current-year IP heavily toward
  prior-year + league average until the new season has accumulated data).

  This is a slight handicap to the backtest -- the model can't see
  late-season trends within the year being tested -- but it cleanly
  isolates "what would the model have predicted if run cold on day 1?"

Caching:
  All API responses are persisted under data/cache/ so repeat runs are
  effectively free.  Cache is keyed by stable identifiers (game_pk,
  player_id, team_id+season) and is safe to delete at any time.

Usage:
  python backtest.py --season 2025                    # full Apr-Sep 2025
  python backtest.py --date-from 04/01/2025 --date-to 04/30/2025
  python backtest.py --season 2025 --quick 3          # 3-day smoke test
  python backtest.py --season 2025 --no-cache         # bypass cache
"""

import argparse
import csv
import json
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Reuse the live model + helpers
from mlb_first_inning_predictor import (
    LEAGUE_AVG_BB9, LEAGUE_AVG_HR9, LEAGUE_AVG_K9,
    LEAGUE_AVG_OBP, LEAGUE_AVG_OPS, LEAGUE_AVG_RPG, LEAGUE_AVG_SLG,
    LEAGUE_AVG_ERA, LEAGUE_AVG_WHIP,
    TEAM_PRIOR_GAMES,
    VALID_GAME_TYPES,
    classify_pick, expected_half_inning_runs,
    park_factor, prob_nrfi, prob_yrfi, prob_over_1_5,
    safe_float, team_abbr,
    _parse_pitcher_splits,
)
from calibration import LambdaCalibrator, classify_pick_calibrated

try:
    import statsapi
except ImportError:
    sys.exit("Missing dependency: pip install mlb-statsapi")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CACHE_ROOT   = Path(__file__).parent / "data" / "cache"
BACKTEST_DIR = Path(__file__).parent / "data" / "backtests"

# Small delay after each *uncached* API call to be polite to MLB StatsAPI.
# Cache hits are instant -- this only fires on cold lookups.
API_SLEEP = 0.10

# ---------------------------------------------------------------------------
# Cache layer (filesystem JSON, one file per object)
#
# Per-category TTL (in seconds).  Categories not listed are cached forever
# (suitable for immutable historical data like finalized linescores).
#
# Live-data categories (team_batting, pitcher_gamelog, batter_gamelog) get a
# 12-hour TTL so the predictor automatically pulls fresh data each morning
# without losing cache benefits within a run.
# ---------------------------------------------------------------------------

import time as _time

CACHE_TTL_SECONDS: dict[str, int] = {
    "team_batting":          12 * 3600,    # 12h: changes after every game
    "pitcher_gamelog_v2":    12 * 3600,    # 12h: new starts add to gamelog
    "batter_gamelog":        12 * 3600,    # 12h: new at-bats every game
    "pitcher_yearbyyear":    12 * 3600,    # 12h: stats refresh as season advances
    "batter_yearbyyear":     12 * 3600,    # 12h
    "pitcher_fi":            12 * 3600,    # 12h: 1st-inning splits update with starts
    # Short-TTL caches (refreshed during hourly intraday runs):
    "schedule":              30 * 60,     # 30m: probable pitchers + game times can change
    "boxscore_top3":         30 * 60,     # 30m: lineup posts in the 1-3 hours pre-game
    "lineup":                30 * 60,     # 30m: same
    # Effectively immutable -- no TTL (cache forever):
    "linescore":              0,           # 0 = never expire (final games are fixed)
    "handedness":             0,
    "weather_season":         0,           # historical archive doesn't change
    "batter_splits":          0,
    "pitch_arsenal":          0,
}


def _cache_path(category: str, key: str) -> Path:
    p = CACHE_ROOT / category
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{key}.json"

def _cache_is_fresh(category: str, path: Path) -> bool:
    """Return True if the cached file is younger than the category's TTL.
    Categories not in CACHE_TTL_SECONDS or with TTL=0 never expire."""
    ttl = CACHE_TTL_SECONDS.get(category, 0)
    if ttl <= 0:
        return True
    try:
        age = _time.time() - path.stat().st_mtime
        return age < ttl
    except OSError:
        return True

def _cache_get(category: str, key: str, use_cache: bool) -> dict | list | None:
    if not use_cache:
        return None
    path = _cache_path(category, key)
    if not path.exists():
        return None
    if not _cache_is_fresh(category, path):
        return None  # stale -- forces a refresh
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _cache_put(category: str, key: str, data) -> None:
    try:
        with open(_cache_path(category, key), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _api_get(endpoint: str, params: dict) -> dict:
    """statsapi.get with a small post-call sleep for rate-limiting."""
    out = statsapi.get(endpoint, params)
    if API_SLEEP > 0:
        time.sleep(API_SLEEP)
    return out


# ---------------------------------------------------------------------------
# Schedule (cached per ISO date)
# ---------------------------------------------------------------------------

def fetch_schedule_iso(iso_date: str, use_cache: bool = True) -> list[dict]:
    """Schedule for a single date, with probable pitcher hydration. Cached."""
    cached = _cache_get("schedule", iso_date, use_cache)
    if cached is not None:
        return cached.get("games", [])

    # MLB API expects MM/DD/YYYY
    y, m, d = iso_date.split("-")
    try:
        raw = _api_get("schedule", {
            "date": f"{m}/{d}/{y}",
            "sportId": 1,
            "hydrate": "probablePitcher",
        })
    except Exception as exc:
        print(f"  [schedule {iso_date}] error: {exc}")
        return []

    games: list[dict] = []
    for date_obj in raw.get("dates", []):
        for game in date_obj.get("games", []):
            if game.get("gameType") not in VALID_GAME_TYPES:
                continue
            teams = game.get("teams", {})
            away  = teams.get("away", {})
            home  = teams.get("home", {})
            away_team = away.get("team", {})
            home_team = home.get("team", {})
            away_p = away.get("probablePitcher", {})
            home_p = home.get("probablePitcher", {})
            away_id = away_team.get("id", 0)
            home_id = home_team.get("id", 0)
            games.append({
                "game_pk":           game["gamePk"],
                "game_date":         game.get("gameDate", ""),
                "game_number":       game.get("gameNumber", 1),
                "double_header":     game.get("doubleHeader", "N"),
                "away_team_id":      away_id,
                "away_abbr":         team_abbr(away_id, away_team.get("abbreviation", "???")),
                "home_team_id":      home_id,
                "home_abbr":         team_abbr(home_id, home_team.get("abbreviation", "???")),
                "away_pitcher_id":   away_p.get("id"),
                "away_pitcher_name": away_p.get("fullName", "TBD"),
                "home_pitcher_id":   home_p.get("id"),
                "home_pitcher_name": home_p.get("fullName", "TBD"),
                "status":            game.get("status", {}).get("detailedState", ""),
            })

    _cache_put("schedule", iso_date, {"games": games})
    return games


# ---------------------------------------------------------------------------
# Pitcher game logs (used for last-N first-inning history)
# ---------------------------------------------------------------------------

def _parse_innings_pitched(ip_str) -> float:
    """
    MLB API innings-pitched is decimal-encoded thirds: 6.0 = 6 IP, 6.1 = 6 1/3,
    6.2 = 6 2/3.  Convert to true float innings.
    """
    if ip_str is None:
        return 0.0
    try:
        s = str(ip_str)
        if "." in s:
            whole, frac = s.split(".", 1)
            whole = int(whole) if whole else 0
            f = int(frac[0]) if frac and frac[0].isdigit() else 0
            return whole + (f / 3.0 if f in (1, 2) else 0.0)
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def fetch_pitcher_gamelog(player_id: int, season: int, use_cache: bool = True) -> list[dict]:
    """
    Per-game pitching log for a pitcher in a season.
    Each entry: gamePk, date, team_id, gs (1 if started), ip, er, k, bb, hr, h.
    Cached under 'pitcher_gamelog_v2' so the older lighter cache doesn't poison.
    """
    if not player_id:
        return []
    cache_key = f"{player_id}_{season}"
    cached = _cache_get("pitcher_gamelog_v2", cache_key, use_cache)
    if cached is not None:
        return cached

    try:
        data = _api_get("person", {
            "personId": player_id,
            "hydrate": f"stats(group=[pitching],type=[gameLog],season={season})",
        })
    except Exception:
        _cache_put("pitcher_gamelog_v2", cache_key, [])
        return []

    splits = (
        data.get("people", [{}])[0]
            .get("stats", [{}])[0]
            .get("splits", [])
    )
    games = []
    for sp in splits:
        s = sp.get("stat", {})
        gs = safe_float(s.get("gamesStarted"), 0)
        if gs < 1:
            continue
        gpk = sp.get("game", {}).get("gamePk")
        date_str = sp.get("date")
        if not gpk or not date_str:
            continue
        games.append({
            "gamePk":  gpk,
            "date":    date_str,
            "team_id": sp.get("team", {}).get("id"),
            "ip":      _parse_innings_pitched(s.get("inningsPitched")),
            "er":      safe_float(s.get("earnedRuns"),  0.0),
            "k":       safe_float(s.get("strikeOuts"),  0.0),
            "bb":      safe_float(s.get("baseOnBalls"), 0.0),
            "hr":      safe_float(s.get("homeRuns"),    0.0),
            "h":       safe_float(s.get("hits"),        0.0),
        })
    _cache_put("pitcher_gamelog_v2", cache_key, games)
    return games


def current_season_pitcher_stats(
    player_id:       int | None,
    target_date_iso: str,
    target_season:   int,
    use_cache:       bool = True,
    min_ip:          float = 5.0,
) -> dict | None:
    """
    Aggregate this pitcher's current-season-to-date stats from their game logs.
    Returns {ip, n_starts, era, fip, hr9, bb9, k9, whip} or None when sample
    is too small (< min_ip).  Caller falls back to prior-year for early-season.
    """
    if not player_id:
        return None
    log = fetch_pitcher_gamelog(player_id, target_season, use_cache)
    relevant = [g for g in log if g.get("date", "") < target_date_iso and g.get("ip", 0) > 0]
    if not relevant:
        return None

    total_ip = sum(g["ip"] for g in relevant)
    if total_ip < min_ip:
        return None

    total_er = sum(g["er"] for g in relevant)
    total_k  = sum(g["k"]  for g in relevant)
    total_bb = sum(g["bb"] for g in relevant)
    total_hr = sum(g["hr"] for g in relevant)
    total_h  = sum(g["h"]  for g in relevant)

    era  = total_er * 9.0 / total_ip
    # FIP_CONSTANT is imported from the predictor
    fip  = (13.0*total_hr + 3.0*total_bb - 2.0*total_k) / total_ip + 3.10
    hr9  = total_hr * 9.0 / total_ip
    bb9  = total_bb * 9.0 / total_ip
    k9   = total_k  * 9.0 / total_ip
    whip = (total_h + total_bb) / total_ip

    return {
        "ip":       total_ip,
        "n_starts": len(relevant),
        "era":      max(1.0, min(era,  9.0)),
        "fip":      max(1.0, min(fip,  9.0)),
        "hr9":      max(0.0, min(hr9,  4.0)),
        "bb9":      max(0.0, min(bb9,  9.0)),
        "k9":       max(1.0, min(k9,  20.0)),
        "whip":     max(0.7, min(whip, 2.0)),
    }


def pitcher_stats_blended(
    player_id:        int | None,
    target_date_iso:  str,
    target_season:    int,
    prior_stats:      dict,    # the prior-year-blended dict from prior_season_pitcher_stats
    use_cache:        bool = True,
) -> dict:
    """
    Get the freshest available pitcher stats: current-season-to-date when
    sample is large enough (>= 25 IP), otherwise fall back to prior-year.
    Mid-range samples (5 - 25 IP) are blended on the IP weight.

    Returns the same shape as prior_stats: {era, fip, hr9, bb9, k9, whip, ...}.
    Adds 'source' key: 'current'/'blend'/'prior'.
    """
    cur = current_season_pitcher_stats(player_id, target_date_iso, target_season, use_cache, min_ip=5.0)
    if cur is None:
        return {**prior_stats, "source": "prior"}

    # Blend weight: full current at >= 25 IP, mostly prior at 5 IP
    w_cur = min(cur["ip"] / 25.0, 1.0)
    w_pri = 1.0 - w_cur

    out = dict(prior_stats)
    out["era"]  = w_cur * cur["era"]  + w_pri * prior_stats.get("era",  4.20)
    out["fip"]  = w_cur * cur["fip"]  + w_pri * prior_stats.get("fip",  4.20)
    out["hr9"]  = w_cur * cur["hr9"]  + w_pri * prior_stats.get("hr9",  1.20)
    out["bb9"]  = w_cur * cur["bb9"]  + w_pri * prior_stats.get("bb9",  3.00)
    out["k9"]   = w_cur * cur["k9"]   + w_pri * prior_stats.get("k9",   8.90)
    out["whip"] = w_cur * cur["whip"] + w_pri * prior_stats.get("whip", 1.25)
    out["source"] = "current" if w_cur >= 0.95 else ("blend" if w_cur >= 0.20 else "prior")
    out["current_ip"]   = cur["ip"]
    out["current_starts"] = cur["n_starts"]
    return out


def pitcher_last_n_first_inning(
    player_id:        int | None,
    target_date_iso:  str,
    target_season:    int,
    n:                int = 5,
    use_cache:        bool = True,
) -> dict | None:
    """
    Look up a pitcher's most recent N starts BEFORE target_date and compute
    first-inning outcomes.  Naturally leakage-free (prior starts only).

    Returns {n, game_nrfi_rate, pitcher_nrfi_rate} or None if not enough data.
      game_nrfi_rate    = fraction of those starts where the GAME went NRFI
                          (combined first-inning runs == 0)
      pitcher_nrfi_rate = fraction where THIS pitcher gave up no runs in
                          their own half-inning
    """
    if not player_id:
        return None

    # Combine current and prior season game logs (sorted latest-first)
    current = fetch_pitcher_gamelog(player_id, target_season,     use_cache)
    prior   = fetch_pitcher_gamelog(player_id, target_season - 1, use_cache)
    pool    = list(current) + list(prior)
    pool    = [g for g in pool if g["date"] < target_date_iso]
    pool.sort(key=lambda g: g["date"], reverse=True)

    starts = pool[:n]
    if not starts:
        return None

    game_nrfi    = 0
    pitcher_nrfi = 0
    counted      = 0

    for s in starts:
        gpk     = s["gamePk"]
        team_id = s.get("team_id")
        ls = fetch_first_inning_result(gpk, use_cache)
        away_r = ls.get("away_runs")
        home_r = ls.get("home_runs")
        if away_r is None or home_r is None:
            continue   # game data unavailable -- skip but don't fail

        # Game-level NRFI/YRFI
        if (away_r + home_r) == 0:
            game_nrfi += 1

        # Pitcher-specific: did THIS pitcher give up runs in their half?
        # Need to know if pitcher was home or away in that game.
        sched = fetch_schedule_iso(s["date"], use_cache)
        game_info = next((g for g in sched if g["game_pk"] == gpk), None)
        if game_info is not None and team_id is not None:
            home_team_id   = game_info["home_team_id"]
            is_home_pitcher = (team_id == home_team_id)
            if is_home_pitcher:
                # Home pitcher pitches top of 1st (away batting)
                if away_r == 0:
                    pitcher_nrfi += 1
            else:
                # Away pitcher pitches bottom of 1st (home batting)
                if home_r == 0:
                    pitcher_nrfi += 1

        counted += 1

    if counted == 0:
        return None

    return {
        "n":                  counted,
        "game_nrfi_rate":     game_nrfi    / counted,
        "pitcher_nrfi_rate":  pitcher_nrfi / counted,
    }


# ---------------------------------------------------------------------------
# Pitcher stats -- prior-season only (no leakage)
# ---------------------------------------------------------------------------

_PVT_CACHE: dict | None = None
_PVT_PATH = Path(__file__).parent / "data" / "pitcher_vs_team.json"


def _load_pvt_cache() -> dict:
    """Lazy-load the pitcher-vs-team cache.  Empty dict if file missing."""
    global _PVT_CACHE
    if _PVT_CACHE is None:
        if _PVT_PATH.exists():
            try:
                _PVT_CACHE = json.load(open(_PVT_PATH, encoding="utf-8"))
            except Exception:
                _PVT_CACHE = {}
        else:
            _PVT_CACHE = {}
    return _PVT_CACHE


def pitcher_vs_team_nrfi_rate(
    player_id:        int | None,
    opp_team_id:      int | None,
    target_date_iso:  str,
) -> float:
    """Bayesian-shrunk NRFI rate for this pitcher vs this specific opposing
    team, using starts STRICTLY BEFORE target_date_iso (no leakage).

    Returns league NRFI rate (~0.52) when sample is empty.  With K=20 a
    pitcher with 5 prior starts vs the team only weights real data 20%,
    so small samples regress hard to the league average -- which is the
    point: "deGrom faced the Yankees only 3 times" should NOT swing the
    model based on those 3 outcomes alone."""
    cache = _load_pvt_cache()
    meta = cache.get("_meta", {})
    league = float(meta.get("league_nrfi_rate", 0.5183))
    K = int(meta.get("shrink_K", 20))

    if not player_id or not opp_team_id:
        return league

    pid_records = cache.get(str(player_id), {})
    opp_records = pid_records.get(str(opp_team_id), [])
    relevant = [s for s in opp_records if s.get("date", "") < target_date_iso]
    n = len(relevant)
    if n == 0:
        return league
    nrfi = sum(1 for s in relevant if not s.get("gave_up_run", True))
    raw = nrfi / n
    return (n * raw + K * league) / (n + K)


def pitcher_role_features(
    player_id:        int | None,
    target_date_iso:  str,
    target_season:    int,
    n:                int = 5,
    use_cache:        bool = True,
) -> dict | None:
    """Compute "starter role" features for a pitcher's last-N starts before
    target_date.  Used to detect openers / bulk pitchers / spot starters.

    Returns:
      n:                       count of starts considered
      avg_ip_per_start:        mean innings pitched
      max_ip:                  longest start in the window
      short_start_fraction:    fraction of starts <= 3 IP
                               (>= 0.4 strongly suggests opener)

    Returns None when the pitcher has zero prior starts (rookie debut).

    Combines current + prior season game logs and filters to dates STRICTLY
    BEFORE target_date_iso (no leakage).
    """
    if not player_id:
        return None
    current = fetch_pitcher_gamelog(player_id, target_season,     use_cache)
    prior   = fetch_pitcher_gamelog(player_id, target_season - 1, use_cache)
    pool    = list(current) + list(prior)
    pool    = [g for g in pool if g.get("date", "") < target_date_iso and g.get("ip", 0) > 0]
    pool.sort(key=lambda g: g["date"], reverse=True)

    starts = pool[:n]
    if not starts:
        return None

    ips = [float(s["ip"]) for s in starts]
    n_short = sum(1 for x in ips if x <= 3.0)

    return {
        "n":                    len(starts),
        "avg_ip_per_start":     sum(ips) / len(ips),
        "max_ip":               max(ips),
        "short_start_fraction": n_short / len(starts),
    }


def fetch_pitcher_yearbyyear(player_id: int, use_cache: bool = True) -> dict:
    """
    Cache the parsed yearByYear pitcher stats keyed by player_id.
    Returns {season_str: stats_dict}.
    """
    if not player_id:
        return {}
    key = str(player_id)
    cached = _cache_get("pitcher_yearbyyear", key, use_cache)
    if cached is not None:
        return cached

    try:
        data = _api_get("person", {
            "personId": player_id,
            "hydrate": "stats(group=[pitching],type=[yearByYear])",
        })
    except Exception:
        _cache_put("pitcher_yearbyyear", key, {})
        return {}

    people = data.get("people", [])
    if not people:
        _cache_put("pitcher_yearbyyear", key, {})
        return {}
    stats_blocks = people[0].get("stats", [])
    if not stats_blocks:
        _cache_put("pitcher_yearbyyear", key, {})
        return {}

    by_season = _parse_pitcher_splits(stats_blocks[0].get("splits", []))
    _cache_put("pitcher_yearbyyear", key, by_season)
    return by_season


def prior_season_pitcher_stats(
    player_id: int | None,
    name: str,
    target_season: int,
    use_cache: bool = True,
) -> tuple[dict, str]:
    """
    Pitcher inputs for a backtest game in target_season -- uses (target_season-1)
    full-year stats only.  Same Bayesian-blend math as the production model's
    prior-year fallback branch.
    """
    def _default(nm: str) -> tuple[dict, str]:
        return {
            "name": nm,
            "era":  LEAGUE_AVG_ERA, "whip": LEAGUE_AVG_WHIP, "fip": LEAGUE_AVG_ERA,
            "k_bb_ratio": 2.8,
            "k9": LEAGUE_AVG_K9, "bb9": LEAGUE_AVG_BB9, "hr9": LEAGUE_AVG_HR9,
        }, "avg"

    if not player_id:
        return _default(name)

    by_season = fetch_pitcher_yearbyyear(player_id, use_cache)
    prev = by_season.get(str(target_season - 1))
    if not prev or prev.get("ip", 0) < 20:
        return _default(name)

    w_prev   = min(prev["ip"] / 120.0, 0.75)
    w_league = 1.0 - w_prev
    blended = {
        "name": name,
        "era":  max(1.0, min(w_prev*prev["era"]  + w_league*LEAGUE_AVG_ERA,  9.0)),
        "whip": max(0.7, min(w_prev*prev["whip"] + w_league*LEAGUE_AVG_WHIP, 2.0)),
        "fip":  max(1.0, min(w_prev*prev["fip"]  + w_league*LEAGUE_AVG_ERA,  9.0)),
        "k_bb_ratio": max(0.5, min(w_prev*prev["k_bb"] + w_league*2.8, 8.0)),
        "k9":   max(1.0, min(w_prev*prev.get("k9",  LEAGUE_AVG_K9)  + w_league*LEAGUE_AVG_K9,  20.0)),
        "bb9":  max(0.0, min(w_prev*prev.get("bb9", LEAGUE_AVG_BB9) + w_league*LEAGUE_AVG_BB9,  9.0)),
        "hr9":  max(0.0, min(w_prev*prev.get("hr9", LEAGUE_AVG_HR9) + w_league*LEAGUE_AVG_HR9,  4.0)),
    }
    return blended, "ltd"


def prior_season_pitcher_fi(
    player_id: int | None,
    target_season: int,
    use_cache: bool = True,
) -> dict | None:
    """
    First-inning splits for the prior season.  Returns None when the
    pitcher has < 3 IP of first-inning sample (cap mirrors production).
    Empty-dict cache value means "no FI data" -- distinguishes from a
    cache miss.
    """
    if not player_id:
        return None
    season = target_season - 1
    cache_key = f"{player_id}_{season}"
    cached = _cache_get("pitcher_fi", cache_key, use_cache)
    if cached is not None:
        return cached or None  # {} -> None

    try:
        data = _api_get("person", {
            "personId": player_id,
            "hydrate": f"stats(group=[pitching],type=[statSplits],sitCodes=[i1],season={season})",
        })
    except Exception:
        _cache_put("pitcher_fi", cache_key, {})
        return None

    splits = (
        data.get("people", [{}])[0]
            .get("stats", [{}])[0]
            .get("splits", [])
    )
    for sp in splits:
        s  = sp.get("stat", {})
        ip = safe_float(s.get("inningsPitched"), 0.0)
        if ip < 3.0:
            continue
        era  = safe_float(s.get("era"),  LEAGUE_AVG_ERA)
        whip = safe_float(s.get("whip"), LEAGUE_AVG_WHIP)
        if not (0.0 < era  < 20.0): era  = LEAGUE_AVG_ERA
        if not (0.5 < whip <  4.0): whip = LEAGUE_AVG_WHIP
        result = {"ip": ip, "era": era, "whip": whip}
        _cache_put("pitcher_fi", cache_key, result)
        return result

    _cache_put("pitcher_fi", cache_key, {})
    return None


# ---------------------------------------------------------------------------
# Team batting -- prior season only
# ---------------------------------------------------------------------------

def prior_season_team_batting(
    team_id: int,
    target_season: int,
    use_cache: bool = True,
) -> tuple[dict, str]:
    """Team hitting stats from the prior season (full-year totals)."""
    def _default():
        return {
            "obp": LEAGUE_AVG_OBP, "slg": LEAGUE_AVG_SLG,
            "rpg": LEAGUE_AVG_RPG, "ops": LEAGUE_AVG_OPS,
        }, "avg"

    if not team_id:
        return _default()

    season    = target_season - 1
    cache_key = f"{team_id}_{season}"
    cached    = _cache_get("team_batting", cache_key, use_cache)
    if cached is not None:
        return cached["stats"], cached["quality"]

    try:
        data = _api_get("team_stats", {
            "teamId": team_id,
            "season": season,
            "group":  "hitting",
            "stats":  "season",
        })
    except Exception:
        return _default()

    splits = (data.get("stats", [{}])[0]).get("splits", [])
    if not splits:
        _cache_put("team_batting", cache_key, {"stats": _default()[0], "quality": "avg"})
        return _default()

    s     = splits[0].get("stat", {})
    ops   = safe_float(s.get("ops"), LEAGUE_AVG_OPS)
    runs  = safe_float(s.get("runs"), 0.0)
    games = safe_float(s.get("gamesPlayed"), 0.0)
    if games < 1:
        return _default()

    rpg = runs / games
    obp = safe_float(s.get("obp"), LEAGUE_AVG_OBP)
    slg = safe_float(s.get("slugging") or s.get("slg"), LEAGUE_AVG_SLG)

    if not (0.400 < ops < 1.200): ops = LEAGUE_AVG_OPS
    if not (0.5   < rpg < 12.0):  rpg = LEAGUE_AVG_RPG
    if not (0.250 < obp < 0.500): obp = LEAGUE_AVG_OBP
    if not (0.250 < slg < 0.700): slg = LEAGUE_AVG_SLG

    # Same Bayesian shrink as production
    w = games / (games + TEAM_PRIOR_GAMES)
    blended = {
        "obp": w * obp + (1 - w) * LEAGUE_AVG_OBP,
        "slg": w * slg + (1 - w) * LEAGUE_AVG_SLG,
        "rpg": w * rpg + (1 - w) * LEAGUE_AVG_RPG,
        "ops": w * ops + (1 - w) * LEAGUE_AVG_OPS,
    }
    quality = "live"  # full prior season -> games >> prior weight
    _cache_put("team_batting", cache_key, {"stats": blended, "quality": quality})
    return blended, quality


# ---------------------------------------------------------------------------
# Game result lookup -- first-inning linescore
# ---------------------------------------------------------------------------

def fetch_first_inning_result(game_pk: int, use_cache: bool = True) -> dict:
    """
    Pull first-inning runs from the linescore.  Only Final / Postponed /
    Suspended results are persisted to cache -- Live games refetch each run.
    """
    cached = _cache_get("linescore", str(game_pk), use_cache)
    if cached is not None:
        return cached

    try:
        data = _api_get("game", {
            "gamePk": game_pk,
            "fields": (
                "gameData,status,abstractGameState,detailedState,"
                "liveData,linescore,innings,num,home,away,runs"
            ),
        })
    except Exception as exc:
        return {"state": "ERROR", "detail": str(exc),
                "away_runs": None, "home_runs": None}

    status = data.get("gameData", {}).get("status", {})
    state  = status.get("abstractGameState", "")
    detail = status.get("detailedState", "")
    innings = data.get("liveData", {}).get("linescore", {}).get("innings", [])

    away_r = home_r = None
    if innings:
        inn1   = innings[0]
        away_r = inn1.get("away", {}).get("runs")
        home_r = inn1.get("home", {}).get("runs")

    result = {"state": state, "detail": detail,
              "away_runs": away_r, "home_runs": home_r}

    # Only cache outcomes that won't change
    if state == "Final" or detail in ("Postponed", "Suspended"):
        _cache_put("linescore", str(game_pk), result)
    return result


# ---------------------------------------------------------------------------
# Player handedness (bat side / throw hand) -- platoon-advantage signal
# ---------------------------------------------------------------------------

def fetch_player_info(player_id: int, use_cache: bool = True) -> dict:
    """
    Returns {'name': str, 'bats': 'L'|'R'|'S', 'throws': 'L'|'R'} for a player.
    Empty string for unknown fields.  Cached per-player.

    Separate from fetch_player_handedness() so the existing handedness cache
    (which doesn't include name) keeps working without invalidation.  Used
    by the predictor to render top-3 batter lineups on the dashboard.
    """
    if not player_id:
        return {"name": "", "bats": "", "throws": ""}
    cache_key = str(player_id)
    cached = _cache_get("player_info", cache_key, use_cache)
    if cached is not None:
        return cached
    try:
        data = _api_get("person", {"personId": player_id})
    except Exception:
        out = {"name": "", "bats": "", "throws": ""}
        _cache_put("player_info", cache_key, out)
        return out
    person = (data.get("people") or [{}])[0]
    out = {
        "name":   person.get("fullName", ""),
        "bats":   (person.get("batSide", {})   or {}).get("code", ""),
        "throws": (person.get("pitchHand", {}) or {}).get("code", ""),
    }
    _cache_put("player_info", cache_key, out)
    return out


def fetch_player_handedness(player_id: int, use_cache: bool = True) -> dict:
    """
    Returns {'bats': 'L'|'R'|'S', 'throws': 'L'|'R'} for a player.
    Empty string for unknown.  Cached per-player (rarely changes).
    """
    if not player_id:
        return {"bats": "", "throws": ""}
    cache_key = str(player_id)
    cached = _cache_get("handedness", cache_key, use_cache)
    if cached is not None:
        return cached
    try:
        data = _api_get("person", {"personId": player_id})
    except Exception:
        _cache_put("handedness", cache_key, {"bats": "", "throws": ""})
        return {"bats": "", "throws": ""}
    person = (data.get("people") or [{}])[0]
    out = {
        "bats":   (person.get("batSide", {})   or {}).get("code", ""),
        "throws": (person.get("pitchHand", {}) or {}).get("code", ""),
    }
    _cache_put("handedness", cache_key, out)
    return out


def has_platoon_advantage(bats: str, throws: str) -> bool:
    """
    Standard MLB platoon rule:
      LHB vs RHP -> advantage
      RHB vs LHP -> advantage
      Same hand  -> no advantage
      Switch hitter -> always advantage (chooses the matchup)
    """
    if not bats or not throws:
        return False
    if bats == "S":
        return True
    return bats != throws


def top3_platoon_summary(top3_ids: list[int], opp_pitcher_id: int,
                         use_cache: bool = True) -> dict:
    """
    Computes platoon features for a half-inning matchup:
      - platoon_count:  # of top-3 batters with handedness advantage vs the
                        opposing pitcher (0-3)
      - lhb_count:      # of LHBs in top 3
      - switch_count:   # of switch hitters in top 3
    """
    if not opp_pitcher_id:
        return {"platoon_count": 0, "lhb_count": 0, "switch_count": 0}
    pitcher_throws = fetch_player_handedness(opp_pitcher_id, use_cache).get("throws", "")
    plat = lhb = sw = 0
    for pid in top3_ids[:3]:
        if not pid:
            continue
        bats = fetch_player_handedness(pid, use_cache).get("bats", "")
        if bats == "S":
            sw += 1
        if bats == "L":
            lhb += 1
        if has_platoon_advantage(bats, pitcher_throws):
            plat += 1
    return {"platoon_count": plat, "lhb_count": lhb, "switch_count": sw}


# ---------------------------------------------------------------------------
# Lineup (boxscore) -- top-of-order batter IDs
# ---------------------------------------------------------------------------

def fetch_top3_batters(game_pk: int, use_cache: bool = True) -> dict:
    """
    Pull the actual batting order from a completed game's boxscore.
    Returns {"away_top3": [id,id,id], "home_top3": [id,id,id]}.
    Empty lists when the data isn't available (e.g. game not yet final).
    Cached only when both top-3 lists are non-empty.
    """
    cached = _cache_get("boxscore_top3", str(game_pk), use_cache)
    if cached is not None:
        return cached

    try:
        data = _api_get("game", {
            "gamePk": game_pk,
            "fields": "liveData,boxscore,teams,away,home,battingOrder",
        })
    except Exception:
        return {"away_top3": [], "home_top3": []}

    teams = data.get("liveData", {}).get("boxscore", {}).get("teams", {})
    away_order = teams.get("away", {}).get("battingOrder", []) or []
    home_order = teams.get("home", {}).get("battingOrder", []) or []

    def _to_ids(order: list) -> list[int]:
        out = []
        for item in order[:3]:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                pass
        return out

    result = {"away_top3": _to_ids(away_order), "home_top3": _to_ids(home_order)}
    if result["away_top3"] and result["home_top3"]:
        _cache_put("boxscore_top3", str(game_pk), result)
    return result


# ---------------------------------------------------------------------------
# Batter stats (yearByYear, prior-season only)
# ---------------------------------------------------------------------------

def _parse_batter_splits(splits: list) -> dict:
    """
    Aggregate batter splits per season.  Skips "Totals" rows missing team.id.
    For traded players, totals across teams are weighted by PA (for OBP) or
    AB (for SLG/AVG).  Returns {season_str: stats_dict}.
    """
    agg: dict[str, dict] = {}
    for split in splits:
        yr = split.get("season")
        if not yr:
            continue
        if not split.get("team", {}).get("id"):
            continue
        s  = split.get("stat", {})
        ab = safe_float(s.get("atBats"), 0.0)
        pa = safe_float(s.get("plateAppearances"), 0.0)
        if ab <= 0 or pa <= 0:
            continue
        obp = safe_float(s.get("obp"), 0.0)
        slg = safe_float(s.get("slg") or s.get("slugging"), 0.0)
        avg = safe_float(s.get("avg"), 0.0)
        hr  = safe_float(s.get("homeRuns"), 0.0)
        if yr not in agg:
            agg[yr] = {"ab": 0.0, "pa": 0.0,
                       "obp_x": 0.0, "slg_x": 0.0, "avg_x": 0.0, "hr": 0.0}
        d = agg[yr]
        d["ab"]    += ab
        d["pa"]    += pa
        d["obp_x"] += obp * pa
        d["slg_x"] += slg * ab
        d["avg_x"] += avg * ab
        d["hr"]    += hr

    result = {}
    for yr, d in agg.items():
        if d["pa"] <= 0 or d["ab"] <= 0:
            continue
        obp = d["obp_x"] / d["pa"]
        slg = d["slg_x"] / d["ab"]
        avg = d["avg_x"] / d["ab"]
        result[yr] = {
            "ab":  d["ab"],
            "pa":  d["pa"],
            "obp": obp,
            "slg": slg,
            "avg": avg,
            "iso": max(0.0, slg - avg),  # isolated power
            "hr":  d["hr"],
        }
    return result


def fetch_batter_yearbyyear(player_id: int, use_cache: bool = True) -> dict:
    """Cache the parsed yearByYear hitting stats keyed by player_id."""
    if not player_id:
        return {}
    key = str(player_id)
    cached = _cache_get("batter_yearbyyear", key, use_cache)
    if cached is not None:
        return cached
    try:
        data = _api_get("person", {
            "personId": player_id,
            "hydrate": "stats(group=[hitting],type=[yearByYear])",
        })
    except Exception:
        _cache_put("batter_yearbyyear", key, {})
        return {}

    people = data.get("people", [])
    if not people:
        _cache_put("batter_yearbyyear", key, {})
        return {}
    stats_blocks = people[0].get("stats", [])
    if not stats_blocks:
        _cache_put("batter_yearbyyear", key, {})
        return {}

    by_season = _parse_batter_splits(stats_blocks[0].get("splits", []))
    _cache_put("batter_yearbyyear", key, by_season)
    return by_season


# League-average ISO ~ league SLG - league AVG; AVG is roughly 0.245
LEAGUE_AVG_AVG = 0.245
LEAGUE_AVG_ISO = LEAGUE_AVG_SLG - LEAGUE_AVG_AVG


def prior_season_top3_stats(
    player_ids:    list[int],
    target_season: int,
    use_cache:     bool = True,
) -> dict:
    """
    PA-weighted aggregate of the top-3 batters' prior-season hitting stats.
    Returns {obp, slg, iso, n_with_data}.
    Falls back to league averages when no batter has >= 30 prior-year ABs.
    """
    season   = target_season - 1
    total_ab = 0.0
    total_pa = 0.0
    obp_x = slg_x = iso_x = 0.0
    n_with_data = 0

    for pid in player_ids[:3]:
        if not pid:
            continue
        by_season = fetch_batter_yearbyyear(pid, use_cache)
        prev = by_season.get(str(season))
        if not prev or prev.get("ab", 0) < 30:
            continue
        n_with_data += 1
        ab = prev["ab"]
        pa = prev["pa"]
        total_ab += ab
        total_pa += pa
        obp_x += prev["obp"] * pa
        slg_x += prev["slg"] * ab
        iso_x += prev["iso"] * ab

    if n_with_data == 0 or total_pa == 0 or total_ab == 0:
        return {
            "obp": LEAGUE_AVG_OBP,
            "slg": LEAGUE_AVG_SLG,
            "iso": LEAGUE_AVG_ISO,
            "n_with_data": 0,
        }

    return {
        "obp": obp_x / total_pa,
        "slg": slg_x / total_ab,
        "iso": iso_x / total_ab,
        "n_with_data": n_with_data,
    }


# ---------------------------------------------------------------------------
# Current-season top-3 stats (point-in-time, leakage-free)
# ---------------------------------------------------------------------------

def fetch_batter_gamelog(player_id: int, season: int, use_cache: bool = True) -> list[dict]:
    """
    Per-game hitting log for a batter in a season.
    Each entry: date, ab, pa, h, bb, hbp, sf, tb, hr.
    """
    if not player_id:
        return []
    cache_key = f"{player_id}_{season}"
    cached = _cache_get("batter_gamelog", cache_key, use_cache)
    if cached is not None:
        return cached

    try:
        data = _api_get("person", {
            "personId": player_id,
            "hydrate": f"stats(group=[hitting],type=[gameLog],season={season})",
        })
    except Exception:
        _cache_put("batter_gamelog", cache_key, [])
        return []

    splits = (
        data.get("people", [{}])[0]
            .get("stats", [{}])[0]
            .get("splits", [])
    )
    games = []
    for sp in splits:
        s = sp.get("stat", {})
        date_str = sp.get("date")
        if not date_str:
            continue
        games.append({
            "date": date_str,
            "ab":   safe_float(s.get("atBats"),           0.0),
            "pa":   safe_float(s.get("plateAppearances"), 0.0),
            "h":    safe_float(s.get("hits"),             0.0),
            "bb":   safe_float(s.get("baseOnBalls"),      0.0),
            "hbp":  safe_float(s.get("hitByPitch"),       0.0),
            "sf":   safe_float(s.get("sacFlies"),         0.0),
            "tb":   safe_float(s.get("totalBases"),       0.0),
            "hr":   safe_float(s.get("homeRuns"),         0.0),
        })
    _cache_put("batter_gamelog", cache_key, games)
    return games


def current_season_to_date_batter(
    player_id:       int,
    target_date_iso: str,
    target_season:   int,
    use_cache:       bool = True,
) -> dict | None:
    """
    Aggregate this batter's current-season-to-date hitting stats.
    Returns {ab, pa, obp, slg, iso, avg, hr} or None if no usable data.
    """
    if not player_id:
        return None
    log = fetch_batter_gamelog(player_id, target_season, use_cache)
    relevant = [g for g in log if g["date"] < target_date_iso]
    if not relevant:
        return None

    ab  = sum(g["ab"]  for g in relevant)
    pa  = sum(g["pa"]  for g in relevant)
    h   = sum(g["h"]   for g in relevant)
    bb  = sum(g["bb"]  for g in relevant)
    hbp = sum(g["hbp"] for g in relevant)
    sf  = sum(g["sf"]  for g in relevant)
    tb  = sum(g["tb"]  for g in relevant)
    if pa == 0 or ab == 0:
        return None

    obp_denom = ab + bb + hbp + sf
    obp = (h + bb + hbp) / obp_denom if obp_denom > 0 else 0.0
    avg = h  / ab
    slg = tb / ab
    iso = max(0.0, slg - avg)
    return {
        "ab":  ab,
        "pa":  pa,
        "obp": obp,
        "slg": slg,
        "iso": iso,
        "avg": avg,
        "hr":  sum(g["hr"] for g in relevant),
    }


def current_season_top3_stats(
    player_ids:      list[int],
    target_date_iso: str,
    target_season:   int,
    use_cache:       bool = True,
    min_ab_per:      int  = 10,
) -> dict:
    """
    PA-weighted current-season-to-date stats for the top-3 batters.
    Falls back to prior-year aggregate when no batter has >= min_ab_per ABs
    in the current season (early-April games, callups).
    """
    total_ab = 0.0
    total_pa = 0.0
    obp_x = slg_x = iso_x = 0.0
    n_with_data = 0

    for pid in player_ids[:3]:
        if not pid:
            continue
        s = current_season_to_date_batter(pid, target_date_iso, target_season, use_cache)
        if not s or s["ab"] < min_ab_per:
            continue
        n_with_data += 1
        total_ab += s["ab"]
        total_pa += s["pa"]
        obp_x += s["obp"] * s["pa"]
        slg_x += s["slg"] * s["ab"]
        iso_x += s["iso"] * s["ab"]

    if n_with_data == 0 or total_pa == 0 or total_ab == 0:
        # Cold start (early season) -- fall back to prior-year aggregate
        prior = prior_season_top3_stats(player_ids, target_season, use_cache)
        return {
            "obp":         prior["obp"],
            "slg":         prior["slg"],
            "iso":         prior["iso"],
            "n_with_data": 0,        # 0 = signaled "we used prior-year fallback"
            "source":      "prior",
        }

    return {
        "obp":         obp_x / total_pa,
        "slg":         slg_x / total_ab,
        "iso":         iso_x / total_ab,
        "n_with_data": n_with_data,
        "source":      "current",
    }


def current_season_top3_per_batter(
    player_ids:      list[int],
    target_date_iso: str,
    target_season:   int,
    use_cache:       bool = True,
) -> list[dict]:
    """
    Per-batter (not aggregate) current-season-to-date stats for the top-3
    batters in the lineup.  Used by the dashboard to render the actual
    matchup card (pitcher vs each top batter) instead of a single
    PA-weighted aggregate.

    Returns a list of up to 3 dicts:
        {"id": int, "name": str, "bats": "L"|"R"|"S"|"",
         "obp": float|None, "slg": float|None, "iso": float|None,
         "ab": int|None}

    `obp/slg/iso/ab` are None when the batter has no current-season log
    yet (early-April callup, etc.).  The aggregate falls back to prior-
    year in that case; the per-batter card just shows the name with em-
    dash stats so the user knows the lineup is set but the data is thin.
    """
    def _nn_float(v) -> float | None:
        """Non-negative float or None.  Defense-in-depth -- upstream
        already guards via safe_float, but a refactor that lets a
        negative leak through would be silently absorbed if we just
        cast with float().  Better to surface as None so the dashboard
        can render an em-dash than to feed bad data into the lineup
        card."""
        if v is None: return None
        try:
            f = float(v)
            return f if f >= 0.0 else None
        except (TypeError, ValueError):
            return None

    def _nn_int(v) -> int | None:
        if v is None: return None
        try:
            i = int(v)
            return i if i >= 0 else None
        except (TypeError, ValueError):
            return None

    out: list[dict] = []
    for pid in player_ids[:3]:
        if not pid:
            continue
        info = fetch_player_info(pid, use_cache)
        s    = current_season_to_date_batter(pid, target_date_iso, target_season, use_cache)
        out.append({
            "id":   pid,
            "name": info.get("name", "") or "",
            "bats": info.get("bats", "") or "",
            "obp":  _nn_float(s.get("obp")) if s else None,
            "slg":  _nn_float(s.get("slg")) if s else None,
            "iso":  _nn_float(s.get("iso")) if s else None,
            "ab":   _nn_int(s.get("ab"))    if s else None,
        })
    return out


# ---------------------------------------------------------------------------
# Empirical first-inning park factor (NRFI rate per park)
# ---------------------------------------------------------------------------

# Loaded lazily from FI_PARK_FACTORS_PATH, populated by --build-park-factors.
FI_PARK_FACTORS_PATH = Path(__file__).parent / "data" / "fi_park_factors.json"
_FI_PARK_CACHE: dict[str, float] | None = None
FI_PARK_DEFAULT = 0.50  # neutral when not in the cache


def load_fi_park_factors() -> dict[str, float]:
    """Load the empirical FI NRFI-rate-per-park dict (lazy, cached)."""
    global _FI_PARK_CACHE
    if _FI_PARK_CACHE is not None:
        return _FI_PARK_CACHE
    if not FI_PARK_FACTORS_PATH.exists():
        _FI_PARK_CACHE = {}
        return _FI_PARK_CACHE
    try:
        with open(FI_PARK_FACTORS_PATH, encoding="utf-8") as f:
            _FI_PARK_CACHE = json.load(f)
    except Exception:
        _FI_PARK_CACHE = {}
    return _FI_PARK_CACHE


def fi_park_nrfi_rate(home_abbr: str) -> float:
    """Empirical NRFI rate for this home park; 0.50 if unknown."""
    return load_fi_park_factors().get(home_abbr, FI_PARK_DEFAULT)


def build_fi_park_factors(csv_paths: list[Path], prior_games: int = 50,
                          ) -> dict[str, float]:
    """
    Compute NRFI rate per home park from one or more backtest CSVs.
    Bayesian-shrinks toward the overall mean with a `prior_games`-game prior
    so small samples don't dominate.
    """
    park_n:    dict[str, int] = {}
    park_nrfi: dict[str, int] = {}
    for path in csv_paths:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                park   = r.get("home", "")
                actual = r.get("actual_side", "")
                if not park or actual not in ("NRFI", "YRFI"):
                    continue
                park_n[park]    = park_n.get(park, 0) + 1
                park_nrfi[park] = park_nrfi.get(park, 0) + (1 if actual == "NRFI" else 0)

    overall_n    = sum(park_n.values())
    overall_nrfi = sum(park_nrfi.values())
    overall_rate = overall_nrfi / overall_n if overall_n else 0.5

    rates: dict[str, float] = {}
    for park, n in park_n.items():
        rates[park] = (park_nrfi[park] + prior_games * overall_rate) / (n + prior_games)
    return rates


# ---------------------------------------------------------------------------
# Weather (Open-Meteo Historical Weather API; free, no auth)
# ---------------------------------------------------------------------------
# Lat/lon for each MLB ballpark (rough; close-enough for grid-cell lookups).
PARK_COORDS: dict[str, tuple[float, float]] = {
    "ARI": (33.4453, -112.0667),  # Chase Field
    "ATL": (33.8907, -84.4677),   # Truist Park
    "BAL": (39.2839, -76.6217),   # Camden Yards
    "BOS": (42.3467, -71.0972),   # Fenway Park
    "CHC": (41.9484, -87.6553),   # Wrigley Field
    "CIN": (39.0975, -84.5067),   # Great American Ball Park
    "CLE": (41.4962, -81.6852),   # Progressive Field
    "COL": (39.7559, -104.9942),  # Coors Field
    "CWS": (41.83,   -87.6339),   # Guaranteed Rate Field
    "DET": (42.339,  -83.0485),   # Comerica Park
    "HOU": (29.7572, -95.3556),   # Minute Maid Park
    "KC":  (39.0517, -94.4803),   # Kauffman Stadium
    "LAA": (33.8003, -117.8827),  # Angel Stadium
    "LAD": (34.0739, -118.24),    # Dodger Stadium
    "MIA": (25.7781, -80.2197),   # loanDepot park
    "MIL": (43.0282, -87.9712),   # American Family Field
    "MIN": (44.9817, -93.2776),   # Target Field
    "NYM": (40.7571, -73.8458),   # Citi Field
    "NYY": (40.8296, -73.9262),   # Yankee Stadium
    "OAK": (37.7516, -122.2005),  # RingCentral Coliseum
    "PHI": (39.9061, -75.1665),   # Citizens Bank Park
    "PIT": (40.4469, -80.0057),   # PNC Park
    "SD":  (32.7073, -117.157),   # Petco Park
    "SEA": (47.5914, -122.3325),  # T-Mobile Park
    "SF":  (37.7786, -122.3893),  # Oracle Park
    "STL": (38.6226, -90.1928),   # Busch Stadium
    "TB":  (27.7682, -82.6534),   # Tropicana Field (dome)
    "TEX": (32.7473, -97.0838),   # Globe Life Field (retractable)
    "TOR": (43.6414, -79.3894),   # Rogers Centre (retractable)
    "WSH": (38.873,  -77.0074),   # Nationals Park
}

# Indoor-or-retractable parks where outdoor weather is irrelevant
DOMED_PARKS = {"TB", "TEX", "TOR", "ARI", "HOU", "MIA", "MIL", "SEA"}

# Approximate bearing from home plate to dead-center field, degrees clockwise
# from true north.  Most parks face NE (so the sun stays out of right-handed
# batters' eyes during day games).  Houston is the famous outlier (faces SW).
# Domed parks are included for completeness but their wind effect is zeroed out.
PARK_ORIENTATION_CF: dict[str, float] = {
    "ARI": 23.0,    # Chase Field (retractable)
    "ATL": 71.0,    # Truist Park
    "BAL": 32.0,    # Camden Yards
    "BOS": 45.0,    # Fenway Park
    "CHC": 18.0,    # Wrigley Field
    "CIN": 68.0,    # Great American Ball Park
    "CLE":  8.0,    # Progressive Field
    "COL":  7.0,    # Coors Field
    "CWS": 36.0,    # Guaranteed Rate Field
    "DET": 18.0,    # Comerica Park
    "HOU": 257.0,   # Minute Maid Park (faces SW! the train side)
    "KC":  47.0,    # Kauffman Stadium
    "LAA": 47.0,    # Angel Stadium
    "LAD": 22.0,    # Dodger Stadium
    "MIA": 35.0,    # loanDepot park (retractable)
    "MIL": 119.0,   # American Family Field (retractable)
    "MIN": 95.0,    # Target Field
    "NYM": 34.0,    # Citi Field
    "NYY": 76.0,    # Yankee Stadium
    "OAK": 56.0,    # RingCentral Coliseum
    "PHI": 16.0,    # Citizens Bank Park
    "PIT": 65.0,    # PNC Park
    "SD":  358.0,   # Petco Park
    "SEA":  5.0,    # T-Mobile Park (retractable)
    "SF":  25.0,    # Oracle Park
    "STL": 70.0,    # Busch Stadium
    "TB":  88.0,    # Tropicana Field (dome)
    "TEX": 86.0,    # Globe Life Field (retractable)
    "TOR":  0.0,    # Rogers Centre (retractable)
    "WSH": 33.0,    # Nationals Park
}


def wind_out_kmh(wind_speed: float | None, wind_deg: float | None,
                  park_abbr: str) -> float:
    """
    Component of wind blowing out toward center field, km/h.
    Positive = wind helping balls carry to CF (HR helper).
    Negative = wind blowing back in from CF (HR suppressor).
    Returns 0.0 for domed parks or missing data.

    Math: meteorological wind_deg is the direction wind is FROM.  A wind
    'going toward CF' arrives FROM the opposite bearing of CF, so the
    magnitude in the CF direction is -cos(wind_deg - park_to_cf) * speed.
    """
    if park_abbr in DOMED_PARKS:
        return 0.0
    if wind_speed is None or wind_deg is None:
        return 0.0
    park_to_cf = PARK_ORIENTATION_CF.get(park_abbr)
    if park_to_cf is None:
        return 0.0
    rel_rad = math.radians(wind_deg - park_to_cf)
    return -math.cos(rel_rad) * float(wind_speed)


def wind_cross_kmh(wind_speed: float | None, wind_deg: float | None,
                    park_abbr: str) -> float:
    """Cross-field wind component, km/h.  Positive = blowing toward right
    field (from the batter's perspective standing at home plate)."""
    if park_abbr in DOMED_PARKS:
        return 0.0
    if wind_speed is None or wind_deg is None:
        return 0.0
    park_to_cf = PARK_ORIENTATION_CF.get(park_abbr)
    if park_to_cf is None:
        return 0.0
    rel_rad = math.radians(wind_deg - park_to_cf)
    return -math.sin(rel_rad) * float(wind_speed)


def fetch_weather_season(home_abbr: str, season: int, use_cache: bool = True) -> dict:
    """
    Pull a season's worth of historical weather for a park in ONE API call.
    Returns a dict keyed by date_iso -> {temp_c, wind_kmh, wind_deg, humidity}.
    Picks the hourly value at a 7pm local-ish slot (game time proxy).
    Cached per (park, season).
    """
    if home_abbr not in PARK_COORDS:
        return {}
    cache_key = f"{home_abbr}_{season}"
    cached = _cache_get("weather_season", cache_key, use_cache)
    if cached is not None:
        return cached

    lat, lon = PARK_COORDS[home_abbr]
    start = f"{season}-04-01"
    end   = f"{season}-09-30"
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date={start}&end_date={end}"
        "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m"
        "&timezone=America/New_York"
    )
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  [weather {home_abbr} {season}] error: {exc}")
        _cache_put("weather_season", cache_key, {})
        return {}

    if API_SLEEP > 0:
        time.sleep(API_SLEEP)

    hourly = payload.get("hourly", {})
    times  = hourly.get("time", [])           # ISO timestamps "YYYY-MM-DDThh:00"
    temps  = hourly.get("temperature_2m", [])
    winds  = hourly.get("wind_speed_10m", [])
    dirs   = hourly.get("wind_direction_10m", [])
    hums   = hourly.get("relative_humidity_2m", [])

    # Pick 19:00 (7pm) as the canonical "evening game" weather row per date.
    # If 19:00 missing, fall back to the latest available hour that day.
    out: dict = {}
    for i, ts in enumerate(times):
        if not ts or "T" not in ts:
            continue
        date_iso, hh = ts.split("T", 1)
        hour = hh[:2]
        rec  = out.get(date_iso, {})
        # Prefer 19:00; otherwise keep latest pre-19 hour we've seen
        if hour == "19" or "hour" not in rec:
            rec["hour"]     = hour
            rec["temp_c"]   = temps[i] if i < len(temps) else None
            rec["wind_kmh"] = winds[i] if i < len(winds) else None
            rec["wind_deg"] = dirs[i]  if i < len(dirs)  else None
            rec["humidity"] = hums[i]  if i < len(hums)  else None
            out[date_iso] = rec
    _cache_put("weather_season", cache_key, out)
    return out


def game_weather(home_abbr: str, date_iso: str, season: int, use_cache: bool = True) -> dict:
    """
    Weather snapshot for a single game.  Returns {} for domed parks
    (outdoor weather doesn't matter) -- LR will see league-mean defaults.
    """
    if home_abbr in DOMED_PARKS:
        return {"temp_c": None, "wind_kmh": None, "wind_deg": None,
                "humidity": None, "is_dome": True}
    season_data = fetch_weather_season(home_abbr, season, use_cache)
    rec = season_data.get(date_iso, {})
    return {
        "temp_c":   rec.get("temp_c"),
        "wind_kmh": rec.get("wind_kmh"),
        "wind_deg": rec.get("wind_deg"),
        "humidity": rec.get("humidity"),
        "is_dome":  False,
    }


# ---------------------------------------------------------------------------
# Predict + grade pipeline
# ---------------------------------------------------------------------------

def predict_one_game(
    game:            dict,
    target_season:   int,
    target_date_iso: str = "",
    use_cache:       bool = True,
    calibrator:      LambdaCalibrator | None = None,
) -> dict:
    """
    Run the model on one game using prior-season inputs.
    If `calibrator` is provided, NRFI probability and pick zone are taken
    from the calibrated lambda->P(NRFI) curve instead of raw Poisson.
    Raw values are still recorded for comparison.
    """
    away_id = game["away_pitcher_id"]
    home_id = game["home_pitcher_id"]

    away_sp,  away_q  = prior_season_pitcher_stats(away_id, game["away_pitcher_name"], target_season, use_cache)
    home_sp,  home_q  = prior_season_pitcher_stats(home_id, game["home_pitcher_name"], target_season, use_cache)
    away_bat, away_bq = prior_season_team_batting(game["away_team_id"], target_season, use_cache)
    home_bat, home_bq = prior_season_team_batting(game["home_team_id"], target_season, use_cache)

    away_fi = prior_season_pitcher_fi(away_id, target_season, use_cache)
    home_fi = prior_season_pitcher_fi(home_id, target_season, use_cache)

    # Pitcher stats blended with current-season-to-date (replaces prior-year
    # stats when the pitcher has accumulated >= 25 IP this year).  Falls back
    # to prior_*_sp for early-season / small-sample cases.
    away_sp_blend = pitcher_stats_blended(away_id, target_date_iso, target_season, away_sp, use_cache)
    home_sp_blend = pitcher_stats_blended(home_id, target_date_iso, target_season, home_sp, use_cache)

    pf = park_factor(game["home_abbr"])

    # Same half-inning math as the live predictor
    away_lam = expected_half_inning_runs(home_sp, away_bat, pf, home=False, fi_pitcher=home_fi)
    home_lam = expected_half_inning_runs(away_sp, home_bat, pf, home=True,  fi_pitcher=away_fi)
    total    = away_lam + home_lam

    data_pts = sum(q != "avg" for q in [away_q, home_q, away_bq, home_bq])

    # Top-of-order batters (lineup-aware) and empirical first-inning park factor
    top3       = fetch_top3_batters(game["game_pk"], use_cache)
    away_top3_ids = top3.get("away_top3", [])
    home_top3_ids = top3.get("home_top3", [])
    away_top3  = prior_season_top3_stats(away_top3_ids, target_season, use_cache)
    home_top3  = prior_season_top3_stats(home_top3_ids, target_season, use_cache)
    fi_park    = fi_park_nrfi_rate(game["home_abbr"])

    # Handedness / platoon advantage
    # Top of 1st: away batters face HOME pitcher; bot of 1st: home batters face AWAY pitcher.
    away_plat = top3_platoon_summary(away_top3_ids, home_id, use_cache)
    home_plat = top3_platoon_summary(home_top3_ids, away_id, use_cache)
    away_pitcher_hand = fetch_player_handedness(away_id, use_cache).get("throws", "")
    home_pitcher_hand = fetch_player_handedness(home_id, use_cache).get("throws", "")

    # Current-season-to-date top-3 stats (point-in-time, falls back to prior year)
    away_top3c = current_season_top3_stats(top3.get("away_top3", []),
                                           target_date_iso, target_season, use_cache)
    home_top3c = current_season_top3_stats(top3.get("home_top3", []),
                                           target_date_iso, target_season, use_cache)

    # Pitcher first-inning history over the last 5 and last 10 starts
    away_l5  = pitcher_last_n_first_inning(away_id, target_date_iso, target_season, n=5,  use_cache=use_cache)
    home_l5  = pitcher_last_n_first_inning(home_id, target_date_iso, target_season, n=5,  use_cache=use_cache)
    away_l10 = pitcher_last_n_first_inning(away_id, target_date_iso, target_season, n=10, use_cache=use_cache)
    home_l10 = pitcher_last_n_first_inning(home_id, target_date_iso, target_season, n=10, use_cache=use_cache)

    # Weather (None for domed parks)
    wx = game_weather(game["home_abbr"], target_date_iso, target_season, use_cache)

    # Wind decomposition relative to park orientation (HR signal)
    wx_out_component   = wind_out_kmh(wx.get("wind_kmh"),   wx.get("wind_deg"),
                                       game["home_abbr"])
    wx_cross_component = wind_cross_kmh(wx.get("wind_kmh"), wx.get("wind_deg"),
                                         game["home_abbr"])

    # Raw Poisson probabilities (always recorded)
    raw_nrfi   = prob_nrfi(total)
    raw_yrfi   = prob_yrfi(total)
    raw_side, raw_conf = classify_pick(total, data_pts)

    # Calibrated probabilities + zone (when calibrator is in use)
    if calibrator is not None:
        cal_nrfi = calibrator.predict(total)
        cal_yrfi = 1.0 - cal_nrfi
        cal_side, cal_conf = classify_pick_calibrated(cal_nrfi, data_pts)
        # The "live" prediction = calibrated values
        nrfi_prob, yrfi_prob = cal_nrfi, cal_yrfi
        pick_side, pick_conf = cal_side, cal_conf
    else:
        nrfi_prob, yrfi_prob = raw_nrfi, raw_yrfi
        pick_side, pick_conf = raw_side, raw_conf

    return {
        "game_pk":            game["game_pk"],
        "away":               game["away_abbr"],
        "home":               game["home_abbr"],
        "away_pitcher":       away_sp["name"],
        "home_pitcher":       home_sp["name"],
        "away_pitcher_q":     away_q,
        "home_pitcher_q":     home_q,
        "away_batting_q":     away_bq,
        "home_batting_q":     home_bq,
        "park_factor":        pf,
        "away_lambda":        away_lam,
        "home_lambda":        home_lam,
        "lambda_total":       total,
        "nrfi_prob":          nrfi_prob,
        "yrfi_prob":          yrfi_prob,
        "over_1_5_prob":      prob_over_1_5(total),
        "pick_side":          pick_side,
        "pick_strength":      pick_conf,
        "data_points":        data_pts,
        # Raw (uncalibrated) values, always recorded for comparison
        "nrfi_prob_raw":      raw_nrfi,
        "yrfi_prob_raw":      raw_yrfi,
        "pick_side_raw":      raw_side,
        "pick_strength_raw":  raw_conf,
        # --- raw model inputs (for downstream learned models) ---
        "away_era":  away_sp["era"],
        "home_era":  home_sp["era"],
        "away_whip": away_sp["whip"],
        "home_whip": home_sp["whip"],
        "away_fip":  away_sp["fip"],
        "home_fip":  home_sp["fip"],
        "away_bb9":  away_sp.get("bb9", LEAGUE_AVG_BB9),
        "home_bb9":  home_sp.get("bb9", LEAGUE_AVG_BB9),
        "away_hr9":  away_sp.get("hr9", LEAGUE_AVG_HR9),
        "home_hr9":  home_sp.get("hr9", LEAGUE_AVG_HR9),
        "away_k9":   away_sp.get("k9",  LEAGUE_AVG_K9),
        "home_k9":   home_sp.get("k9",  LEAGUE_AVG_K9),
        "away_obp":  away_bat.get("obp", LEAGUE_AVG_OBP),
        "home_obp":  home_bat.get("obp", LEAGUE_AVG_OBP),
        "away_slg":  away_bat.get("slg", LEAGUE_AVG_SLG),
        "home_slg":  home_bat.get("slg", LEAGUE_AVG_SLG),
        "away_rpg":  away_bat["rpg"],
        "home_rpg":  home_bat["rpg"],
        # First-inning splits (None if insufficient sample)
        "away_fi_era":  away_fi["era"]  if away_fi else None,
        "home_fi_era":  home_fi["era"]  if home_fi else None,
        "away_fi_whip": away_fi["whip"] if away_fi else None,
        "home_fi_whip": home_fi["whip"] if home_fi else None,
        "away_fi_ip":   away_fi["ip"]   if away_fi else None,
        "home_fi_ip":   home_fi["ip"]   if home_fi else None,
        # Top-of-order batters (prior-season aggregate)
        "away_top3_obp":      away_top3["obp"],
        "home_top3_obp":      home_top3["obp"],
        "away_top3_slg":      away_top3["slg"],
        "home_top3_slg":      home_top3["slg"],
        "away_top3_iso":      away_top3["iso"],
        "home_top3_iso":      home_top3["iso"],
        "away_top3_n":        away_top3["n_with_data"],
        "home_top3_n":        home_top3["n_with_data"],
        # Top-of-order batters (current-season-to-date, point-in-time)
        "away_top3c_obp":     away_top3c["obp"],
        "home_top3c_obp":     home_top3c["obp"],
        "away_top3c_slg":     away_top3c["slg"],
        "home_top3c_slg":     home_top3c["slg"],
        "away_top3c_iso":     away_top3c["iso"],
        "home_top3c_iso":     home_top3c["iso"],
        "away_top3c_n":       away_top3c["n_with_data"],
        "home_top3c_n":       home_top3c["n_with_data"],
        "away_top3c_source":  away_top3c.get("source", ""),
        "home_top3c_source":  home_top3c.get("source", ""),
        # Pitcher first-inning history (last 5 / last 10 starts)
        "away_p_last5_game_nrfi":     (away_l5["game_nrfi_rate"]    if away_l5  else None),
        "away_p_last5_pitcher_nrfi":  (away_l5["pitcher_nrfi_rate"] if away_l5  else None),
        "away_p_last5_n":             (away_l5["n"]                 if away_l5  else 0),
        "home_p_last5_game_nrfi":     (home_l5["game_nrfi_rate"]    if home_l5  else None),
        "home_p_last5_pitcher_nrfi":  (home_l5["pitcher_nrfi_rate"] if home_l5  else None),
        "home_p_last5_n":             (home_l5["n"]                 if home_l5  else 0),
        "away_p_last10_game_nrfi":    (away_l10["game_nrfi_rate"]    if away_l10 else None),
        "away_p_last10_pitcher_nrfi": (away_l10["pitcher_nrfi_rate"] if away_l10 else None),
        "away_p_last10_n":            (away_l10["n"]                 if away_l10 else 0),
        "home_p_last10_game_nrfi":    (home_l10["game_nrfi_rate"]    if home_l10 else None),
        "home_p_last10_pitcher_nrfi": (home_l10["pitcher_nrfi_rate"] if home_l10 else None),
        "home_p_last10_n":            (home_l10["n"]                 if home_l10 else 0),
        # Empirical first-inning park factor (NRFI rate, shrunk)
        "fi_park_nrfi_rate":  fi_park,
        # Weather
        "wx_temp_c":     wx.get("temp_c"),
        "wx_wind_kmh":   wx.get("wind_kmh"),
        "wx_wind_deg":   wx.get("wind_deg"),
        "wx_humidity":   wx.get("humidity"),
        "wx_is_dome":    1 if wx.get("is_dome") else 0,
        # Park-relative wind: positive 'wind_out' = blowing out to CF (HR-helper)
        "wx_wind_out":   wx_out_component,
        "wx_wind_cross": wx_cross_component,
        # Pitcher stats blended with current-season-to-date (fresher than prior-year)
        "away_era_blend":  away_sp_blend["era"],
        "home_era_blend":  home_sp_blend["era"],
        "away_fip_blend":  away_sp_blend["fip"],
        "home_fip_blend":  home_sp_blend["fip"],
        "away_hr9_blend":  away_sp_blend["hr9"],
        "home_hr9_blend":  home_sp_blend["hr9"],
        "away_bb9_blend":  away_sp_blend["bb9"],
        "home_bb9_blend":  home_sp_blend["bb9"],
        "away_k9_blend":   away_sp_blend["k9"],
        "home_k9_blend":   home_sp_blend["k9"],
        "away_whip_blend": away_sp_blend["whip"],
        "home_whip_blend": home_sp_blend["whip"],
        "away_blend_source":      away_sp_blend.get("source", "prior"),
        "home_blend_source":      home_sp_blend.get("source", "prior"),
        "away_current_ip":        away_sp_blend.get("current_ip", 0),
        "home_current_ip":        home_sp_blend.get("current_ip", 0),
        "away_current_starts":    away_sp_blend.get("current_starts", 0),
        "home_current_starts":    home_sp_blend.get("current_starts", 0),
        # Handedness / platoon advantage
        # away_top3 face HOME pitcher (top of 1st);  home_top3 face AWAY pitcher.
        "away_top3_platoon":  away_plat["platoon_count"],
        "home_top3_platoon":  home_plat["platoon_count"],
        "away_top3_lhb":      away_plat["lhb_count"],
        "home_top3_lhb":      home_plat["lhb_count"],
        "away_top3_switch":   away_plat["switch_count"],
        "home_top3_switch":   home_plat["switch_count"],
        "away_pitcher_throws_l": 1 if away_pitcher_hand == "L" else 0,
        "home_pitcher_throws_l": 1 if home_pitcher_hand == "L" else 0,
    }


def run_backtest(
    iso_dates:     list[str],
    target_season: int,
    use_cache:     bool       = True,
    out_path:      Path | None = None,
    calibrator:    LambdaCalibrator | None = None,
) -> list[dict]:
    """Predict + grade every game across the given date list."""
    rows:    list[dict] = []
    skipped = errored   = 0

    print(f"\nBacktest  |  season {target_season}  "
          f"(prior-year priors from {target_season - 1})")
    print(f"  Dates       : {iso_dates[0]} - {iso_dates[-1]}  ({len(iso_dates)} days)")
    print(f"  Cache       : {'on' if use_cache else 'off'}  ({CACHE_ROOT})")
    if calibrator:
        cal_desc = (f"on (trained on {calibrator.train_n} games "
                    f"from {calibrator.train_seasons})")
    else:
        cal_desc = "off (raw Poisson)"
    print(f"  Calibrator  : {cal_desc}")
    print(f"  Output CSV  : {out_path or '--'}\n")

    for i, iso in enumerate(iso_dates, 1):
        try:
            schedule = fetch_schedule_iso(iso, use_cache)
        except Exception as exc:
            print(f"  [{iso}]  schedule error: {exc}")
            errored += 1
            continue

        if not schedule:
            print(f"  [{i:>3}/{len(iso_dates)}]  {iso}  (no games)")
            continue

        graded_today = 0
        for game in schedule:
            # No probable pitcher = can't predict
            if not game["away_pitcher_id"] or not game["home_pitcher_id"]:
                skipped += 1
                continue

            pred   = predict_one_game(
                game, target_season,
                target_date_iso=iso, use_cache=use_cache, calibrator=calibrator,
            )
            actual = fetch_first_inning_result(game["game_pk"], use_cache)
            state  = actual.get("state", "")
            detail = actual.get("detail", "")

            if detail in ("Postponed", "Suspended"):
                continue
            if actual["away_runs"] is None or actual["home_runs"] is None:
                # Live games / missing data
                skipped += 1
                continue

            total_runs  = actual["away_runs"] + actual["home_runs"]
            actual_side = "NRFI" if total_runs == 0 else "YRFI"
            pick        = pred["pick_side"]
            graded      = "PASS" if pick == "PASS" else ("WIN" if pick == actual_side else "LOSS")

            rows.append({
                "date":           iso,
                **pred,
                "fi_away_runs":   actual["away_runs"],
                "fi_home_runs":   actual["home_runs"],
                "fi_total_runs":  total_runs,
                "actual_side":    actual_side,
                "graded_result":  graded,
            })
            graded_today += 1

        print(f"  [{i:>3}/{len(iso_dates)}]  {iso}  "
              f"games={len(schedule):>2}  graded={graded_today:>2}  "
              f"total={len(rows)}")

    if out_path:
        write_backtest_csv(out_path, rows)

    print(f"\n  Final: graded={len(rows)}  skipped={skipped}  errored={errored}")
    return rows


def write_backtest_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date", "game_pk", "away", "home",
        "away_pitcher", "home_pitcher",
        "away_pitcher_q", "home_pitcher_q",
        "away_batting_q", "home_batting_q",
        "park_factor",
        "away_lambda", "home_lambda", "lambda_total",
        "nrfi_prob", "yrfi_prob", "over_1_5_prob",
        "pick_side", "pick_strength", "data_points",
        "nrfi_prob_raw", "yrfi_prob_raw",
        "pick_side_raw", "pick_strength_raw",
        # raw model inputs
        "away_era", "home_era",
        "away_whip", "home_whip",
        "away_fip", "home_fip",
        "away_bb9", "home_bb9",
        "away_hr9", "home_hr9",
        "away_k9",  "home_k9",
        "away_obp", "home_obp",
        "away_slg", "home_slg",
        "away_rpg", "home_rpg",
        "away_fi_era",  "home_fi_era",
        "away_fi_whip", "home_fi_whip",
        "away_fi_ip",   "home_fi_ip",
        # Top-of-order (prior-year) + FI park factor
        "away_top3_obp", "home_top3_obp",
        "away_top3_slg", "home_top3_slg",
        "away_top3_iso", "home_top3_iso",
        "away_top3_n",   "home_top3_n",
        # Top-of-order (current-season-to-date)
        "away_top3c_obp", "home_top3c_obp",
        "away_top3c_slg", "home_top3c_slg",
        "away_top3c_iso", "home_top3c_iso",
        "away_top3c_n",   "home_top3c_n",
        "away_top3c_source", "home_top3c_source",
        # Pitcher last-N first-inning history
        "away_p_last5_game_nrfi",     "home_p_last5_game_nrfi",
        "away_p_last5_pitcher_nrfi",  "home_p_last5_pitcher_nrfi",
        "away_p_last5_n",             "home_p_last5_n",
        "away_p_last10_game_nrfi",    "home_p_last10_game_nrfi",
        "away_p_last10_pitcher_nrfi", "home_p_last10_pitcher_nrfi",
        "away_p_last10_n",            "home_p_last10_n",
        "fi_park_nrfi_rate",
        # Weather
        "wx_temp_c", "wx_wind_kmh", "wx_wind_deg", "wx_humidity", "wx_is_dome",
        "wx_wind_out", "wx_wind_cross",
        # Pitcher stats blended with current-season-to-date
        "away_era_blend",  "home_era_blend",
        "away_fip_blend",  "home_fip_blend",
        "away_hr9_blend",  "home_hr9_blend",
        "away_bb9_blend",  "home_bb9_blend",
        "away_k9_blend",   "home_k9_blend",
        "away_whip_blend", "home_whip_blend",
        "away_blend_source",   "home_blend_source",
        "away_current_ip",     "home_current_ip",
        "away_current_starts", "home_current_starts",
        # Handedness / platoon
        "away_top3_platoon",   "home_top3_platoon",
        "away_top3_lhb",       "home_top3_lhb",
        "away_top3_switch",    "home_top3_switch",
        "away_pitcher_throws_l", "home_pitcher_throws_l",
        # actual outcome
        "fi_away_runs", "fi_home_runs", "fi_total_runs",
        "actual_side", "graded_result",
    ]
    float_fields = {
        "park_factor", "away_lambda", "home_lambda", "lambda_total",
        "nrfi_prob", "yrfi_prob", "over_1_5_prob",
        "nrfi_prob_raw", "yrfi_prob_raw",
        "away_era", "home_era",
        "away_whip", "home_whip",
        "away_fip", "home_fip",
        "away_bb9", "home_bb9",
        "away_hr9", "home_hr9",
        "away_k9",  "home_k9",
        "away_obp", "home_obp",
        "away_slg", "home_slg",
        "away_rpg", "home_rpg",
        "away_fi_era",  "home_fi_era",
        "away_fi_whip", "home_fi_whip",
        "away_fi_ip",   "home_fi_ip",
        "away_top3_obp", "home_top3_obp",
        "away_top3_slg", "home_top3_slg",
        "away_top3_iso", "home_top3_iso",
        "away_top3c_obp", "home_top3c_obp",
        "away_top3c_slg", "home_top3c_slg",
        "away_top3c_iso", "home_top3c_iso",
        "away_p_last5_game_nrfi",     "home_p_last5_game_nrfi",
        "away_p_last5_pitcher_nrfi",  "home_p_last5_pitcher_nrfi",
        "away_p_last10_game_nrfi",    "home_p_last10_game_nrfi",
        "away_p_last10_pitcher_nrfi", "home_p_last10_pitcher_nrfi",
        "fi_park_nrfi_rate",
        "wx_temp_c", "wx_wind_kmh", "wx_wind_deg", "wx_humidity",
        "wx_wind_out", "wx_wind_cross",
        "away_era_blend",  "home_era_blend",
        "away_fip_blend",  "home_fip_blend",
        "away_hr9_blend",  "home_hr9_blend",
        "away_bb9_blend",  "home_bb9_blend",
        "away_k9_blend",   "home_k9_blend",
        "away_whip_blend", "home_whip_blend",
    }
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in fields}
            for k in float_fields:
                v = row.get(k)
                if isinstance(v, float):
                    row[k] = f"{v:.4f}"
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier_score(rows: list[dict]) -> float:
    """Mean squared error between NRFI prob and 0/1 NRFI outcome."""
    if not rows:
        return float("nan")
    total = 0.0
    for r in rows:
        actual_nrfi = 1.0 if r["actual_side"] == "NRFI" else 0.0
        total += (r["nrfi_prob"] - actual_nrfi) ** 2
    return total / len(rows)


def log_loss(rows: list[dict]) -> float:
    if not rows:
        return float("nan")
    eps   = 1e-12
    total = 0.0
    for r in rows:
        p = max(eps, min(1 - eps, r["nrfi_prob"]))
        total += -math.log(p) if r["actual_side"] == "NRFI" else -math.log(1 - p)
    return total / len(rows)


def calibration_table(rows: list[dict], n_bins: int = 10) -> list[dict]:
    bins = [[] for _ in range(n_bins)]
    for r in rows:
        idx = min(int(r["nrfi_prob"] * n_bins), n_bins - 1)
        bins[idx].append(r)
    out = []
    for i, bin_rows in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bin_rows:
            out.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": 0,
                        "avg_pred": None, "obs_nrfi": None})
            continue
        avg_pred = sum(r["nrfi_prob"] for r in bin_rows) / len(bin_rows)
        obs      = sum(1 for r in bin_rows if r["actual_side"] == "NRFI") / len(bin_rows)
        out.append({"bin": f"{lo:.2f}-{hi:.2f}", "n": len(bin_rows),
                    "avg_pred": avg_pred, "obs_nrfi": obs})
    return out


def by_zone(rows: list[dict]) -> dict[str, dict]:
    zones: dict[str, list] = {
        "STRONG NRFI": [], "LEAN NRFI": [],
        "PASS":        [],
        "LEAN YRFI":   [], "STRONG YRFI": [],
    }
    for r in rows:
        side, conf = r["pick_side"], r["pick_strength"]
        if side == "PASS":
            zones["PASS"].append(r)
        elif side in ("NRFI", "YRFI"):
            key = f"{conf} {side}"
            zones.setdefault(key, []).append(r)

    out = {}
    for zone, zr in zones.items():
        wins   = sum(1 for r in zr if r["graded_result"] == "WIN")
        losses = sum(1 for r in zr if r["graded_result"] == "LOSS")
        n      = len(zr)
        out[zone] = {
            "n": n, "wins": wins, "losses": losses,
            "win_rate": wins / (wins + losses) if (wins + losses) else None,
        }
    return out


def lambda_distribution(rows: list[dict]) -> dict:
    if not rows:
        return {}
    lams = sorted(r["lambda_total"] for r in rows)
    n = len(lams)
    return {
        "n":   n,
        "min": lams[0],
        "max": lams[-1],
        "p25": lams[n // 4],
        "p50": lams[n // 2],
        "p75": lams[(3 * n) // 4],
        "mean": sum(lams) / n,
    }


def print_report(rows: list[dict]) -> None:
    if not rows:
        print("\n  No graded rows -- nothing to report.\n")
        return

    sep  = "=" * 64
    sep2 = "-" * 64

    actual_nrfi = sum(1 for r in rows if r["actual_side"] == "NRFI") / len(rows)
    avg_lam     = sum(r["lambda_total"] for r in rows) / len(rows)
    avg_pred    = sum(r["nrfi_prob"]    for r in rows) / len(rows)

    # Naive baselines for reference
    naive_yrfi_winrate = 1.0 - actual_nrfi
    naive_nrfi_winrate = actual_nrfi

    print(f"\n{sep}")
    print(f"  BACKTEST REPORT  --  {len(rows)} graded games")
    print(sep)
    print(f"  Actual NRFI rate    : {actual_nrfi*100:>5.1f}%   (~38% MLB baseline)")
    print(f"  Mean predicted NRFI : {avg_pred*100:>5.1f}%   (model bias = "
          f"{(avg_pred - actual_nrfi)*100:+.1f}pp)")
    print(f"  Mean lambda              : {avg_lam:.3f}")
    print(f"  Brier score         : {brier_score(rows):.4f}   "
          f"(climatology baseline = {actual_nrfi*(1-actual_nrfi):.4f})")
    print(f"  Log loss            : {log_loss(rows):.4f}   "
          f"(climatology baseline = "
          f"{-(actual_nrfi*math.log(max(1e-12,actual_nrfi)) + (1-actual_nrfi)*math.log(max(1e-12,1-actual_nrfi))):.4f})")
    print(f"  Naive 'always YRFI' : {naive_yrfi_winrate*100:.1f}% win rate")
    print(f"  Naive 'always NRFI' : {naive_nrfi_winrate*100:.1f}% win rate")

    print(f"\n{sep2}")
    print(f"  Lambda distribution:")
    ld = lambda_distribution(rows)
    print(f"    range : {ld['min']:.3f} - {ld['max']:.3f}")
    print(f"    p25/50/75 : {ld['p25']:.3f} / {ld['p50']:.3f} / {ld['p75']:.3f}")
    print(f"    mean  : {ld['mean']:.3f}")

    print(f"\n{sep2}")
    print(f"  Calibration  (predicted NRFI% -> observed NRFI rate):")
    print(f"    {'bin':<12}  {'n':>4}  {'pred':>7}  {'obs':>7}   drift")
    for c in calibration_table(rows):
        if c["n"] == 0:
            continue
        drift = c["obs_nrfi"] - c["avg_pred"]
        sign  = "+" if drift >= 0 else ""
        print(f"    {c['bin']:<12}  {c['n']:>4}  "
              f"{c['avg_pred']*100:>6.1f}%  {c['obs_nrfi']*100:>6.1f}%   "
              f"{sign}{drift*100:>5.1f}pp")

    print(f"\n{sep2}")
    print(f"  By pick zone:")
    print(f"    {'zone':<14}  {'n':>4}  {'W-L':>9}  {'win%':>6}  vs naive")
    zones = by_zone(rows)
    for zone in ["STRONG NRFI", "LEAN NRFI", "PASS", "LEAN YRFI", "STRONG YRFI"]:
        z  = zones.get(zone, {})
        n  = z.get("n", 0)
        w  = z.get("wins", 0)
        l  = z.get("losses", 0)
        wr = z.get("win_rate")

        # Compare to "always-pick-this-side" naive baseline
        if zone.endswith("NRFI"):
            naive = naive_nrfi_winrate
        elif zone.endswith("YRFI"):
            naive = naive_yrfi_winrate
        else:
            naive = None

        wr_s    = f"{wr*100:.1f}%" if wr is not None else "  --  "
        delta_s = f"{(wr-naive)*100:+5.1f}pp" if (wr is not None and naive is not None) else "    --"
        print(f"    {zone:<14}  {n:>4}  {w:>3}-{l:<3}    {wr_s:>6}  {delta_s}")

    print(sep)
    print()


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def date_range(iso_from: str, iso_to: str) -> list[str]:
    d0 = date.fromisoformat(iso_from)
    d1 = date.fromisoformat(iso_to)
    if d1 < d0:
        d0, d1 = d1, d0
    out, cur = [], d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def to_iso(date_str: str) -> str:
    if "/" in date_str:
        m, d, y = date_str.split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return date_str[:10]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest the NRFI/YRFI Poisson model against historical games.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python backtest.py --season 2025
  python backtest.py --date-from 04/01/2025 --date-to 04/30/2025
  python backtest.py --season 2025 --quick 3
  python backtest.py --season 2025 --no-cache
        """,
    )
    parser.add_argument("--season",     type=int, help="Backtest a full season (Apr 1 - Sep 30).")
    parser.add_argument("--date-from",  help="Start date MM/DD/YYYY or YYYY-MM-DD")
    parser.add_argument("--date-to",    help="End date MM/DD/YYYY or YYYY-MM-DD")
    parser.add_argument("--quick",      type=int, metavar="N",
                        help="Smoke test: only first N days of the range")
    parser.add_argument("--no-cache",   action="store_true",
                        help="Bypass local cache, refetch from MLB StatsAPI")
    parser.add_argument("--out",        help="Output CSV path")
    parser.add_argument("--calibrator", metavar="PATH",
                        help="Apply a fitted calibrator JSON to predictions")
    parser.add_argument("--build-park-factors", nargs="+", metavar="CSV",
                        help="Compute empirical FI park factors from CSV(s) "
                             "and write to data/fi_park_factors.json. Skips backtest.")
    args = parser.parse_args()

    if args.build_park_factors:
        paths = [Path(p) for p in args.build_park_factors]
        for p in paths:
            if not p.exists():
                sys.exit(f"CSV not found: {p}")
        rates = build_fi_park_factors(paths)
        FI_PARK_FACTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FI_PARK_FACTORS_PATH, "w", encoding="utf-8") as f:
            json.dump(rates, f, indent=2, sort_keys=True)
        print(f"\n  Computed FI park factors from {len(paths)} CSV(s)")
        print(f"  Output : {FI_PARK_FACTORS_PATH}")
        print(f"  Parks  : {len(rates)}")
        for park, rate in sorted(rates.items(), key=lambda x: x[1]):
            print(f"    {park:>3}  NRFI={rate*100:>5.1f}%")
        return

    if args.season:
        season   = args.season
        iso_from = f"{season}-04-01"
        iso_to   = f"{season}-09-30"
    elif args.date_from and args.date_to:
        iso_from = to_iso(args.date_from)
        iso_to   = to_iso(args.date_to)
        season   = int(iso_from[:4])
    else:
        parser.error("Provide --season YYYY  OR  --date-from + --date-to")

    dates = date_range(iso_from, iso_to)
    if args.quick:
        dates = dates[:args.quick]

    calibrator = LambdaCalibrator.load(args.calibrator) if args.calibrator else None

    if args.out:
        out_path = Path(args.out)
    else:
        BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
        parts = []
        if args.quick:
            parts.append(f"quick{args.quick}")
        if calibrator:
            parts.append("cal")
        suffix = ("_" + "_".join(parts)) if parts else ""
        out_path = BACKTEST_DIR / f"backtest_{iso_from}_to_{iso_to}{suffix}.csv"

    rows = run_backtest(
        iso_dates     = dates,
        target_season = season,
        use_cache     = not args.no_cache,
        out_path      = out_path,
        calibrator    = calibrator,
    )
    print_report(rows)


if __name__ == "__main__":
    main()

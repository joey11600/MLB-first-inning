#!/usr/bin/env python3
"""
MLB First Inning Run Predictor
================================
Predicts NRFI / YRFI probability (and over/under 1.5 runs) for today's games.

Methodology:
  - Fetches schedule via the raw MLB StatsAPI schedule endpoint with
    probablePitcher hydration, giving us both pitcher names AND IDs in
    one call (statsapi.schedule() strips the IDs - we bypass it).
  - Pulls per-pitcher season stats from /people/{id} with pitching
    hydration: ERA, WHIP, K, BB, HR, IP -> computes FIP in-script.
  - Pulls per-team season batting stats from the correct
    /teams/{teamId}/stats endpoint (team_stats in the statsapi map).
  - Models each half-inning as Poisson(lambda); sums both halves for total.
  - Classifies the game into NRFI/YRFI/PASS using thresholds calibrated
    against the real MLB baseline (YRFI ~ 62% for an average game).
  - Tracks data quality per game; games with <=1 real data point are
    shown as PASS / INSUFFICIENT DATA so they don't pollute the slate.

Usage:
  python mlb_first_inning_predictor.py              # today
  python mlb_first_inning_predictor.py --date 04/10/2025
  python mlb_first_inning_predictor.py --strong     # only LEAN+ picks
"""

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import statsapi
except ImportError:
    sys.exit("Missing dependency: pip install mlb-statsapi")

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False
    class _NoColor:
        def __getattr__(self, _): return ""
    Fore = Style = _NoColor()

# ---------------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")


def _today_et() -> date:
    """Return today's date in US Eastern Time (handles UTC server deployments)."""
    return datetime.now(tz=_ET).date()


# ---------------------------------------------------------------------------
# League-wide constants  (T3.11 — version-stamped)
# ---------------------------------------------------------------------------
# Source: MLB league averages, 2023-2024 seasons (combined).
# Last verified: 2026-01-15.
# When updating: refresh ALL constants to the same source season range,
# rebuild park factors (rebuild_park_factors.py), and refit the calibrator
# (recalibrate_v2.py).  Mismatched-vintage constants silently bias the
# Bayesian-blend toward older data when you only update one.
LEAGUE_CONSTANTS_VERSION  = "2023-2024"
LEAGUE_CONSTANTS_VERIFIED = "2026-01-15"

LEAGUE_FIRST_INNING_RUNS = 0.475   # avg runs per team per first half-inning
LEAGUE_AVG_ERA           = 4.20
LEAGUE_AVG_WHIP          = 1.25
LEAGUE_AVG_OPS           = 0.728
LEAGUE_AVG_RPG           = 4.45
FIP_CONSTANT             = 3.10

# Bayesian shrinkage: how many "prior" IP/games we add before trusting real data.
# At IP=PITCHER_PRIOR_IP  ->  50% real / 50% league avg.
# At IP=3xPITCHER_PRIOR_IP ->  75% real / 25% league avg.
PITCHER_PRIOR_IP  = 40.0
TEAM_PRIOR_GAMES  = 15.0

# League averages for expanded pitcher stats (2023-2024 MLB starters)
LEAGUE_AVG_K9   = 8.9
LEAGUE_AVG_BB9  = 3.20  # matches recalibrate_v2.py + regrade_and_rebet.py
LEAGUE_AVG_HR9  = 1.20  # matches recalibrate_v2.py + regrade_and_rebet.py

# League averages for expanded offense stats
LEAGUE_AVG_OBP  = 0.318
LEAGUE_AVG_SLG  = 0.414

# Weather defaults -- match the trainer (two_stage_model.py).  Used both
# for outdoor games where the open-meteo lookup failed and for indoor /
# retractable-roof parks where outdoor weather is irrelevant.  In the
# latter case, wx_is_dome=1 acts as the model's "ignore weather" switch.
WX_TEMP_DEFAULT     = 20.0
WX_WIND_DEFAULT     = 10.0
WX_HUMIDITY_DEFAULT = 60.0

# Stadiums where weather doesn't matter (indoor or retractable roof).
# Mirror of backtest.DOMED_PARKS -- if you change one, change the other.
#   ARI - Chase Field (retractable)
#   HOU - Daikin Park (retractable)
#   MIA - loanDepot park (retractable)
#   MIL - American Family Field (retractable)
#   SEA - T-Mobile Park (retractable)
#   TB  - Tropicana Field (true dome)
#   TEX - Globe Life Field (retractable)
#   TOR - Rogers Centre (retractable)
DOMED_PARKS = {"ARI", "HOU", "MIA", "MIL", "SEA", "TB", "TEX", "TOR"}

# Pitcher multiplier weights -- tuned for first-inning relevance
# BB/9 weighted highly: leadoff walks directly cause first-inning runs
# HR/9 weighted highly: solo HR = immediate YRFI
WHIP_WEIGHT  = 0.30
FIP_WEIGHT   = 0.25
BB9_WEIGHT   = 0.25
HR9_WEIGHT   = 0.20

# Offense multiplier weights -- OBP highest: getting on base is the
# primary first-inning scoring driver (3-batter sequences, not extra bases)
OBP_WEIGHT  = 0.45
RPG_WEIGHT  = 0.35
SLG_WEIGHT  = 0.20

# Capped multiplier ranges to prevent extreme outliers
PITCHER_MULT_MIN = 0.45
PITCHER_MULT_MAX = 1.80
OFFENSE_MULT_MIN = 0.60
OFFENSE_MULT_MAX = 1.55

HOME_RUN_FACTOR  = 1.025
AWAY_RUN_FACTOR  = 0.975

# ---------------------------------------------------------------------------
# Pick thresholds -- driven by combined first-inning lambda (projected runs)
#
# Calibrated to produce a realistic board distribution across a full slate.
# Model outputs cluster in the 0.80-0.95 range so thresholds are shifted up
# relative to the raw scale.  Revisit when model is recalibrated.
#
#   lambda <= 0.72            -> STRONG NRFI
#   0.73 <= lambda <= 0.80     -> LEAN NRFI
#   0.81 <= lambda <= 0.88     -> PASS  (dead zone)
#   0.89 <= lambda <= 0.99     -> LEAN YRFI
#   lambda >= 1.00            -> STRONG YRFI
# ---------------------------------------------------------------------------
NRFI_STRONG_LAM = 0.72   # lambda <= this -> STRONG NRFI
NRFI_LEAN_LAM   = 0.80   # lambda <= this -> LEAN NRFI
PASS_LAM_MAX    = 0.88   # lambda <= this -> PASS (dead zone)
YRFI_LEAN_LAM   = 0.99   # lambda <= this -> LEAN YRFI  (lambda > this -> STRONG YRFI)

# ---------------------------------------------------------------------------
# Stable MLB team ID -> abbreviation map (IDs do not change)
# ---------------------------------------------------------------------------
TEAM_ID_TO_ABBR: dict[int, str] = {
    108: "LAA",
    109: "ARI",
    110: "BAL",
    111: "BOS",
    112: "CHC",
    113: "CIN",
    114: "CLE",
    115: "COL",
    116: "DET",
    117: "HOU",
    118: "KC",
    119: "LAD",
    120: "WSH",
    121: "NYM",
    133: "OAK",
    134: "PIT",
    135: "SD",
    136: "SEA",
    137: "SF",
    138: "STL",
    139: "TB",
    140: "TEX",
    141: "TOR",
    142: "MIN",
    143: "PHI",
    144: "ATL",
    145: "CWS",
    146: "MIA",
    147: "NYY",
    158: "MIL",
}

# ---------------------------------------------------------------------------
# Park run factors  (FanGraphs 2022-2024 multi-year averages)
# Keyed by abbreviation from TEAM_ID_TO_ABBR.
# ---------------------------------------------------------------------------
PARK_FACTORS: dict[str, float] = {
    "COL": 1.22,  # Coors Field
    "CIN": 1.09,
    "TEX": 1.07,
    "BAL": 1.06,
    "BOS": 1.05,  # Fenway Park
    "PHI": 1.04,
    "MIL": 1.03,
    "NYY": 1.02,
    "TOR": 1.01,
    "CHC": 1.00,
    "ARI": 1.00,
    "HOU": 0.99,
    "LAA": 0.99,
    "CWS": 0.98,
    "ATL": 0.98,
    "DET": 0.98,
    "STL": 0.97,
    "WSH": 0.97,
    "LAD": 0.97,
    "MIN": 0.97,
    "KC":  0.96,
    "PIT": 0.96,
    "NYM": 0.96,
    "CLE": 0.96,
    "SD":  0.95,
    "MIA": 0.95,
    "SEA": 0.95,
    "SF":  0.94,
    "OAK": 0.93,
    "TB":  0.93,
}

# ---------------------------------------------------------------------------
# Poisson helpers
# ---------------------------------------------------------------------------

def poisson_pmf(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

# Raw Poisson helpers.  prob_over_1_5 is still used by the live predictor
# for the over/under-1.5 column (no LR model for that yet).  prob_nrfi /
# prob_yrfi are legacy -- only backtest.py imports them to re-emit the
# baseline lambda-based predictions stored as raw_* columns in backtest CSVs.
def prob_nrfi(lam: float)    -> float: return poisson_pmf(lam, 0)
def prob_yrfi(lam: float)    -> float: return 1.0 - prob_nrfi(lam)
def prob_over_1_5(lam: float) -> float: return 1.0 - poisson_pmf(lam,0) - poisson_pmf(lam,1)
def prob_under_1_5(lam: float)-> float: return poisson_pmf(lam,0) + poisson_pmf(lam,1)

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def safe_float(value, default: float) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else default
    except (TypeError, ValueError):
        return default

def team_abbr(team_id: int, fallback: str = "???") -> str:
    return TEAM_ID_TO_ABBR.get(team_id, fallback)

def park_factor(home_abbr: str) -> float:
    return PARK_FACTORS.get(home_abbr, 1.00)

# ---------------------------------------------------------------------------
# Schedule fetching
# ---------------------------------------------------------------------------
# We call the raw schedule endpoint with probablePitcher hydration instead
# of statsapi.schedule().  The high-level helper strips pitcher IDs out of
# its return dict, which is why the original code always got player_id=0.
# ---------------------------------------------------------------------------

VALID_GAME_TYPES = {"R", "F", "D", "L", "W"}   # regular + postseason; skip spring/all-star

def fetch_schedule(date_str: str) -> list[dict]:
    """
    Returns a list of game dicts, each with both team IDs/abbrs, pitcher
    IDs/names, game number, and doubleheader flag.

    Retries up to 3 times with exponential backoff on transient errors --
    MLB's StatsAPI occasionally 503s during high-traffic moments (slate
    posting, mid-game).  A bare `sys.exit` on the first failure used to
    take down the entire predictor for what was usually a 5-second blip.
    """
    import time as _time
    last_exc = None
    for attempt, sleep_s in enumerate([0, 2, 5, 10], start=1):
        if sleep_s:
            _time.sleep(sleep_s)
        try:
            raw = statsapi.get("schedule", {
                "date": date_str,
                "sportId": 1,
                "hydrate": "probablePitcher",
            })
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 4:
                print(f"  schedule fetch attempt {attempt}/4 failed: {exc} -- retrying...", file=sys.stderr)
            continue
    else:
        sys.exit(f"Failed to fetch schedule after 4 attempts: {last_exc}")

    games = []
    for date_obj in raw.get("dates", []):
        for game in date_obj.get("games", []):
            if game.get("gameType") not in VALID_GAME_TYPES:
                continue

            teams = game.get("teams", {})
            away  = teams.get("away", {})
            home  = teams.get("home", {})

            away_team = away.get("team", {})
            home_team = home.get("team", {})

            away_probable = away.get("probablePitcher", {})
            home_probable = home.get("probablePitcher", {})

            away_id = away_team.get("id", 0)
            home_id = home_team.get("id", 0)

            status_obj = game.get("status") or {}
            games.append({
                "game_pk":         game["gamePk"],
                "game_date":       game.get("gameDate", ""),
                "game_number":     game.get("gameNumber", 1),
                "double_header":   game.get("doubleHeader", "N"),  # N / Y / S
                "away_team_id":    away_id,
                "away_team_name":  away_team.get("name", "???"),
                "away_abbr":       team_abbr(away_id, away_team.get("abbreviation", "???")),
                "home_team_id":    home_id,
                "home_team_name":  home_team.get("name", "???"),
                "home_abbr":       team_abbr(home_id, home_team.get("abbreviation", "???")),
                # Pitcher IDs and names - None if not announced
                "away_pitcher_id":   away_probable.get("id"),
                "away_pitcher_name": away_probable.get("fullName", "TBD"),
                "home_pitcher_id":   home_probable.get("id"),
                "home_pitcher_name": home_probable.get("fullName", "TBD"),
                # Status flags (used by DH-Y placeholder resolution)
                "start_time_tbd":  bool(status_obj.get("startTimeTBD", False)),
                "abstract_state":  status_obj.get("abstractGameState", ""),
                "detailed_state":  status_obj.get("detailedState", ""),
            })

    # Doubleheader placeholder resolution -----------------------------------
    # For traditional ("Y") doubleheaders MLB returns a placeholder gameDate
    # for game 2 until game 1 ends -- the schedule endpoint reports
    # game-1's time + 5 minutes with status.startTimeTBD = true.  Once
    # game 1 actually plays, the schedule endpoint LAGS the real game-2
    # start: MLB.com may show the actual time (e.g. 3:55 PM) hours
    # before the API catches up.  We resolve in this priority order:
    #   1. start_time_tbd is FALSE -> trust gameDate as-is.
    #   2. start_time_tbd is TRUE + game 2 has plays -> use first play's
    #      startTime as the actual first pitch (most accurate signal).
    #   3. start_time_tbd is TRUE + game 1 has finished -> estimate
    #      game 2 start = game-1 endTime + 30 min (standard DH-Y
    #      intermission).  Better than the placeholder time.
    #   4. start_time_tbd is TRUE + game 1 still in progress -> fall
    #      back to the "After Game 1" chip so the user knows it's TBD.
    by_matchup: dict[tuple, list[dict]] = {}
    for g in games:
        if g.get("double_header") == "Y":
            key = (g["away_team_id"], g["home_team_id"])
            by_matchup.setdefault(key, []).append(g)
    for matchup_games in by_matchup.values():
        if len(matchup_games) < 2:
            continue
        matchup_games.sort(key=lambda x: x.get("game_number", 1))
        g1, g2 = matchup_games[0], matchup_games[1]

        # Belt-and-suspenders placeholder detection: prefer the explicit
        # start_time_tbd flag, fall back to the gap heuristic in case MLB
        # is lying about the flag (rare but observed).
        is_placeholder = bool(g2.get("start_time_tbd"))
        if not is_placeholder:
            try:
                t1 = datetime.fromisoformat(g1["game_date"].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(g2["game_date"].replace("Z", "+00:00"))
                if abs((t2 - t1).total_seconds()) < 90 * 60:
                    is_placeholder = True
            except Exception:
                pass
        if not is_placeholder:
            continue

        # Try to resolve the real start time from live game data.
        resolved = _resolve_dh_g2_start(g1["game_pk"], g2["game_pk"])
        if resolved:
            g2["game_date"]              = resolved
            g2["game_time_placeholder"]  = False
        else:
            g2["game_time_placeholder"]  = True

    return games


def _resolve_dh_g2_start(g1_pk: int, g2_pk: int) -> str | None:
    """
    Best-known real start time for DH-Y game 2 when MLB hasn't updated
    the schedule's gameDate yet.  Tries two signals in priority order:

      1. game 2's first play startTime  -- if any plays exist, the game
         has actually begun and this IS the real first-pitch timestamp.
      2. game 1's last play endTime + 30 min  -- standard DH-Y
         intermission, used when game 2 hasn't started yet.

    Returns an ISO-Z timestamp string the format_game_time() helper can
    parse, or None when neither signal is available (game 1 still in
    progress, or both API calls failed).
    """
    import statsapi as _statsapi
    from datetime import timedelta as _td
    # 1. game 2 first play startTime
    try:
        d2 = _statsapi.get("game", {
            "gamePk": g2_pk,
            "fields": "liveData,plays,allPlays,about,startTime",
        })
        plays2 = (d2.get("liveData") or {}).get("plays", {}).get("allPlays", [])
        if plays2:
            t = plays2[0].get("about", {}).get("startTime")
            if t:
                return t
    except Exception:
        pass
    # 2. game 1 last play endTime + 30 min
    try:
        d1 = _statsapi.get("game", {
            "gamePk": g1_pk,
            "fields": "liveData,plays,allPlays,about,endTime",
        })
        plays1 = (d1.get("liveData") or {}).get("plays", {}).get("allPlays", [])
        if plays1:
            t_end = plays1[-1].get("about", {}).get("endTime")
            if t_end:
                end_dt = datetime.fromisoformat(t_end.replace("Z", "+00:00"))
                est    = end_dt + _td(minutes=30)
                return est.isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Pitcher stat fetching
# ---------------------------------------------------------------------------

def _pitcher_quality_tag(ip: float, prior_ip: float = 0.0) -> str:
    """Quality label based on EFFECTIVE innings of evidence.

    T2.53: was current-season-IP-only.  That broke catastrophically in
    early/mid season -- e.g. on 2026-05-03 (32 days into the season),
    Max Fried had ~30 IP for 2026 but 1127 career IP including 165 IP
    in 2025.  Old logic tagged him "ltd" because curr_ip < 80; reality
    is he has overwhelming evidence.  The Bayesian blend in
    _blend_pitcher correctly uses prior-year data, so the stats fed to
    the model are accurate -- but the quality TAG was misleading the
    operator about data confidence.

    New logic: tag based on `effective_ip = curr + min(prior, 120)`.
    Prior-year IP only counts when >= 20 (matching the blend's prior
    threshold).  Capped at 120 since we don't trust >2y of history
    over current-year noise on a pitcher's recent change of stuff.

    Tier thresholds calibrated to season-progress neutrality:
      live (>=100 effective IP)  -- model has solid evidence
      ltd  (>=30  effective IP)  -- usable but with shrinkage
      sm   (>=1   effective IP)  -- thin sample
      avg  (0)                   -- no usable data, league fallback
    """
    effective = ip + (min(prior_ip, 120.0) if prior_ip >= 20 else 0.0)
    if effective >= 100: return "live"
    if effective >= 30:  return "ltd"
    if effective >= 1:   return "sm"
    return "avg"


def _parse_pitcher_splits(splits: list) -> dict:
    """
    Aggregate per-team yearByYear splits into a {season_str: stats_dict} map.
    Handles traded players (multiple rows per season) via IP-weighted averaging.
    Rows without a valid team.id are skipped to avoid double-counting totals rows.
    """
    agg: dict[str, dict] = {}
    for split in splits:
        yr = split.get("season")
        if not yr:
            continue
        # Skip "2 Teams" / totals rows that lack a real team id
        if not split.get("team", {}).get("id"):
            continue
        s  = split.get("stat", {})
        ip = safe_float(s.get("inningsPitched"), 0.0)
        if ip <= 0:
            continue
        era  = safe_float(s.get("era"),  LEAGUE_AVG_ERA)
        whip = safe_float(s.get("whip"), LEAGUE_AVG_WHIP)
        k    = safe_float(s.get("strikeOuts"),  0.0)
        bb   = safe_float(s.get("baseOnBalls"), 0.0)
        hr   = safe_float(s.get("homeRuns"),    0.0)
        if yr not in agg:
            agg[yr] = {"ip": 0.0, "era_x": 0.0, "whip_x": 0.0, "k": 0.0, "bb": 0.0, "hr": 0.0}
        d = agg[yr]
        d["ip"]    += ip
        d["era_x"] += era * ip
        d["whip_x"]+= whip * ip
        d["k"]     += k;  d["bb"] += bb;  d["hr"] += hr

    result = {}
    for yr, d in agg.items():
        ip = d["ip"]
        if ip > 0:
            era_r  = d["era_x"] / ip
            whip_r = d["whip_x"] / ip
            fip_r  = max(0.5, min((13*d["hr"] + 3*d["bb"] - 2*d["k"]) / ip + FIP_CONSTANT, 9.0))
            k_bb_r = d["k"] / d["bb"] if d["bb"] > 0 else 2.8
            k9_r  = d["k"]  * 9.0 / ip
            bb9_r = d["bb"] * 9.0 / ip
            hr9_r = d["hr"] * 9.0 / ip
            result[yr] = {
                "ip": ip, "era": era_r, "whip": whip_r, "fip": fip_r,
                "k_bb": k_bb_r,
                "k9":  max(0.0, min(k9_r,  20.0)),
                "bb9": max(0.0, min(bb9_r, 12.0)),
                "hr9": max(0.0, min(hr9_r,  5.0)),
            }
    return result


def _blend_pitcher(stat: dict, ip: float, extra_prior: dict | None = None) -> dict:
    """
    Bayesian-blend one season's pitcher stats toward league average.
    Optional extra_prior adds a prior-year contribution before the league-avg fill.
    """
    # How much weight the real current-year data gets
    w_real = ip / (ip + PITCHER_PRIOR_IP)

    if extra_prior and extra_prior.get("ip", 0) >= 20:
        # Fill the remaining weight partly with prior year, partly with league avg
        remaining  = 1.0 - w_real
        w_prior    = remaining * min(extra_prior["ip"] / 120.0, 1.0)
        w_league   = remaining - w_prior
        era   = w_real*stat["era"]  + w_prior*extra_prior["era"]  + w_league*LEAGUE_AVG_ERA
        whip  = w_real*stat["whip"] + w_prior*extra_prior["whip"] + w_league*LEAGUE_AVG_WHIP
        fip   = w_real*stat["fip"]  + w_prior*extra_prior["fip"]  + w_league*LEAGUE_AVG_ERA
        k_bb  = w_real*stat["k_bb"] + w_prior*extra_prior["k_bb"] + w_league*2.8
        k9    = w_real*stat["k9"]   + w_prior*extra_prior.get("k9",  LEAGUE_AVG_K9)  + w_league*LEAGUE_AVG_K9
        bb9   = w_real*stat["bb9"]  + w_prior*extra_prior.get("bb9", LEAGUE_AVG_BB9) + w_league*LEAGUE_AVG_BB9
        hr9   = w_real*stat["hr9"]  + w_prior*extra_prior.get("hr9", LEAGUE_AVG_HR9) + w_league*LEAGUE_AVG_HR9
    else:
        w_league = 1.0 - w_real
        era   = w_real*stat["era"]  + w_league*LEAGUE_AVG_ERA
        whip  = w_real*stat["whip"] + w_league*LEAGUE_AVG_WHIP
        fip   = w_real*stat["fip"]  + w_league*LEAGUE_AVG_ERA
        k_bb  = w_real*stat["k_bb"] + w_league*2.8
        k9    = w_real*stat["k9"]   + w_league*LEAGUE_AVG_K9
        bb9   = w_real*stat["bb9"]  + w_league*LEAGUE_AVG_BB9
        hr9   = w_real*stat["hr9"]  + w_league*LEAGUE_AVG_HR9

    return {
        "era":  max(1.0, min(era,  9.0)),
        "whip": max(0.7, min(whip, 2.0)),
        "fip":  max(1.0, min(fip,  9.0)),
        "k_bb_ratio": max(0.5, min(k_bb, 8.0)),
        "k9":   max(1.0, min(k9,  20.0)),
        "bb9":  max(0.0, min(bb9,  9.0)),
        "hr9":  max(0.0, min(hr9,  4.0)),
    }


def fetch_pitcher_stats(player_id: int | None, known_name: str, season: int) -> tuple[dict, str]:
    """
    Fetch pitcher stats via yearByYear hydration (one API call, all seasons).

    Strategy:
      1. Parse current-year splits -> Bayesian-blend with prior year + league avg.
      2. If current year has < 1 IP (hasn't started yet), fall back to prior year.
      3. If nothing usable, return full league-average defaults.

    Returns (stats_dict, quality_tag).
    quality_tag: "live" (IP>=80) | "ltd" (IP>=20) | "sm" (IP>=1) | "avg" (no data).
    The known_name is ALWAYS preserved so pitcher names are never "Unknown".
    """
    def _default(name: str) -> tuple[dict, str]:
        return {
            "name": name, "era": LEAGUE_AVG_ERA, "whip": LEAGUE_AVG_WHIP,
            "fip": LEAGUE_AVG_ERA, "k_bb_ratio": 2.8,
            "k9": LEAGUE_AVG_K9, "bb9": LEAGUE_AVG_BB9, "hr9": LEAGUE_AVG_HR9,
        }, "avg"

    if not player_id:
        return _default(known_name)

    try:
        data = statsapi.get("person", {
            "personId": player_id,
            "hydrate":  "stats(group=[pitching],type=[yearByYear])",
        })
    except Exception:
        return _default(known_name)

    people = data.get("people", [])
    if not people:
        return _default(known_name)

    person = people[0]
    name   = person.get("fullName", known_name)

    stats_blocks = person.get("stats", [])
    if not stats_blocks:
        return _default(name)

    by_season = _parse_pitcher_splits(stats_blocks[0].get("splits", []))
    if not by_season:
        return _default(name)

    curr = by_season.get(str(season))
    prev = by_season.get(str(season - 1))

    if curr and curr["ip"] >= 1:
        blended = _blend_pitcher(curr, curr["ip"], extra_prior=prev)
        # T2.53: consider prior year's IP when grading quality so a
        # mid-April Max Fried with 25 curr_IP + 165 prev_IP still tags
        # "live" instead of "ltd".  Falls through to current-only when
        # prior is missing or too thin.
        quality = _pitcher_quality_tag(
            curr["ip"],
            prior_ip=(prev["ip"] if prev and prev.get("ip", 0) >= 20 else 0.0),
        )
    elif prev and prev["ip"] >= 20:
        # Pitcher hasn't thrown in the new season yet - use prior year
        # with heavier league-avg regression (75% max trust for prior year)
        w_prev   = min(prev["ip"] / 120.0, 0.75)
        w_league = 1.0 - w_prev
        blended = {
            "era":  max(1.0, min(w_prev*prev["era"]  + w_league*LEAGUE_AVG_ERA,  9.0)),
            "whip": max(0.7, min(w_prev*prev["whip"] + w_league*LEAGUE_AVG_WHIP, 2.0)),
            "fip":  max(1.0, min(w_prev*prev["fip"]  + w_league*LEAGUE_AVG_ERA,  9.0)),
            "k_bb_ratio": max(0.5, min(w_prev*prev["k_bb"] + w_league*2.8,       8.0)),
            "k9":  max(1.0, min(w_prev*prev.get("k9",  LEAGUE_AVG_K9)  + w_league*LEAGUE_AVG_K9,  20.0)),
            "bb9": max(0.0, min(w_prev*prev.get("bb9", LEAGUE_AVG_BB9) + w_league*LEAGUE_AVG_BB9,  9.0)),
            "hr9": max(0.0, min(w_prev*prev.get("hr9", LEAGUE_AVG_HR9) + w_league*LEAGUE_AVG_HR9,  4.0)),
        }
        quality = "ltd"   # prior-year data = limited quality
    else:
        return _default(name)

    blended["name"] = name
    return blended, quality

# ---------------------------------------------------------------------------
# First-inning specific pitcher stat fetching
# ---------------------------------------------------------------------------

def _try_fetch_pitcher_fi_stats(player_id: int | None, season: int) -> dict | None:
    """
    Attempt to fetch first-inning-specific pitching stats via statSplits.
    Returns None if the pitcher has no ID, the endpoint fails, or sample < 3 IP.
    When successful returns {"ip": ..., "era": ..., "whip": ...}.
    """
    if not player_id:
        return None
    try:
        data = statsapi.get("person", {
            "personId": player_id,
            "hydrate": f"stats(group=[pitching],type=[statSplits],sitCodes=[i1],season={season})",
        })
        splits = (
            data.get("people", [{}])[0]
                .get("stats", [{}])[0]
                .get("splits", [])
        )
        for sp in splits:
            s  = sp.get("stat", {})
            ip = safe_float(s.get("inningsPitched"), 0.0)
            if ip < 3.0:   # < ~10 first-inning appearances -- too small
                return None
            era  = safe_float(s.get("era"),  LEAGUE_AVG_ERA)
            whip = safe_float(s.get("whip"), LEAGUE_AVG_WHIP)
            if not (0.0 < era  < 20.0): era  = LEAGUE_AVG_ERA
            if not (0.5 < whip <  4.0): whip = LEAGUE_AVG_WHIP
            return {"ip": ip, "era": era, "whip": whip}
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Team batting stat fetching
# ---------------------------------------------------------------------------

def _team_quality_tag(games: float) -> str:
    if games >= 20: return "live"
    if games >= 5:  return "ltd"
    if games >= 1:  return "sm"
    return "avg"


def fetch_team_batting(team_id: int, season: int) -> tuple[dict, str]:
    """
    Fetch season team hitting stats via /teams/{teamId}/stats.

    Returns (stats_dict, quality_tag).
    quality_tag: "live" (games>=20) | "ltd" (games>=5) | "sm" (games>=1) | "avg".

    Small samples are Bayesian-blended toward league average rather than
    rejected outright, so early-April data (2-3 games) still contributes
    partial signal instead of defaulting everything to league average.
    """
    def _default() -> tuple[dict, str]:
        return {"obp": LEAGUE_AVG_OBP, "slg": LEAGUE_AVG_SLG, "rpg": LEAGUE_AVG_RPG, "ops": LEAGUE_AVG_OPS}, "avg"

    if not team_id:
        return _default()

    try:
        data = statsapi.get("team_stats", {
            "teamId": team_id,
            "season": season,
            "group":  "hitting",
            "stats":  "season",
        })

        splits = (data.get("stats", [{}])[0]).get("splits", [])
        if not splits:
            return _default()

        s     = splits[0].get("stat", {})
        ops   = safe_float(s.get("ops"),        LEAGUE_AVG_OPS)
        runs  = safe_float(s.get("runs"),        0.0)
        games = safe_float(s.get("gamesPlayed"), 0.0)

        if games < 1:
            return _default()

        rpg = runs / games

        obp   = safe_float(s.get("obp"),              LEAGUE_AVG_OBP)
        slg   = safe_float(s.get("slugging") or s.get("slg"), LEAGUE_AVG_SLG)

        # Sanity-check raw values before blending
        if not (0.400 < ops < 1.200): ops = LEAGUE_AVG_OPS
        if not (0.5   < rpg < 12.0):  rpg = LEAGUE_AVG_RPG
        if not (0.250 < obp < 0.500): obp = LEAGUE_AVG_OBP
        if not (0.250 < slg < 0.700): slg = LEAGUE_AVG_SLG

        # Bayesian blend: small samples regress toward league average
        w     = games / (games + TEAM_PRIOR_GAMES)
        ops_b = w * ops + (1.0 - w) * LEAGUE_AVG_OPS
        rpg_b = w * rpg + (1.0 - w) * LEAGUE_AVG_RPG
        obp_b = w * obp + (1.0 - w) * LEAGUE_AVG_OBP
        slg_b = w * slg + (1.0 - w) * LEAGUE_AVG_SLG

        return {"obp": obp_b, "slg": slg_b, "rpg": rpg_b, "ops": ops_b}, _team_quality_tag(games)

    except Exception:
        return _default()

# ---------------------------------------------------------------------------
# Core prediction model
# ---------------------------------------------------------------------------

def pitcher_multiplier(stats: dict) -> float:
    """
    Translate pitcher stats into a run-scoring multiplier vs league average.
    Weighted for first-inning relevance: BB/9 and HR/9 receive extra weight
    because walks and homers are the primary first-inning YRFI triggers.
    """
    whip_mult = stats["whip"] / LEAGUE_AVG_WHIP
    fip_mult  = stats["fip"]  / LEAGUE_AVG_ERA
    bb9_mult  = stats.get("bb9", LEAGUE_AVG_BB9) / LEAGUE_AVG_BB9
    hr9_mult  = stats.get("hr9", LEAGUE_AVG_HR9) / LEAGUE_AVG_HR9

    # K/9 acts as a dampener: elite strikeout rate -> fewer baserunners/runs
    k9      = stats.get("k9", LEAGUE_AVG_K9)
    k9_adj  = 1.0 - (k9 - LEAGUE_AVG_K9) * 0.012   # +/-1.2% per unit above/below avg
    k9_adj  = max(0.90, min(k9_adj, 1.08))

    raw = (
        WHIP_WEIGHT * whip_mult +
        FIP_WEIGHT  * fip_mult  +
        BB9_WEIGHT  * bb9_mult  +
        HR9_WEIGHT  * hr9_mult
    ) * k9_adj

    return max(PITCHER_MULT_MIN, min(raw, PITCHER_MULT_MAX))


def offense_multiplier(batting: dict) -> float:
    """
    Translate team batting stats into a run-scoring multiplier vs league average.
    OBP weighted highest: getting on base is the primary first-inning scoring driver
    since only 3 batters come to the plate before the inning might end scoreless.
    """
    obp_mult = batting.get("obp", LEAGUE_AVG_OBP) / LEAGUE_AVG_OBP
    slg_mult = batting.get("slg", LEAGUE_AVG_SLG) / LEAGUE_AVG_SLG
    rpg_mult = batting.get("rpg", LEAGUE_AVG_RPG) / LEAGUE_AVG_RPG

    raw = OBP_WEIGHT * obp_mult + SLG_WEIGHT * slg_mult + RPG_WEIGHT * rpg_mult
    return max(OFFENSE_MULT_MIN, min(raw, OFFENSE_MULT_MAX))


def expected_half_inning_runs(
    pitcher:    dict,
    batting:    dict,
    pf:         float,
    home:       bool,
    fi_pitcher: dict | None = None,
) -> float:
    """
    Expected runs for ONE team in the first inning.
    If first-inning-specific pitcher stats are available (fi_pitcher), they are
    blended in -- weighted by how much first-inning sample the pitcher has.
    """
    # Blend season stats with first-inning-specific stats if available.
    # The cap of 40% used to be flat regardless of FI sample size, which
    # was too restrictive once a pitcher had 25+ FI IP (effectively a
    # full season of just-first-innings data).  T2.11: scale the cap
    # with sample size:
    #   <  3 FI IP  -> skip (too noisy)
    #   3-10 FI IP  -> max 25% weight
    #   10-20 FI IP -> max 40% weight
    #   20-30 FI IP -> max 55% weight
    #   30+ FI IP   -> max 65% weight (still cap below 100% so the
    #                  full-season trend stays a meaningful anchor)
    if fi_pitcher and fi_pitcher.get("ip", 0) >= 3.0:
        fi_ip = fi_pitcher["ip"]
        if   fi_ip <  10.0: cap = 0.25
        elif fi_ip <  20.0: cap = 0.40
        elif fi_ip <  30.0: cap = 0.55
        else:               cap = 0.65
        # Linear ramp within the bracket: at the floor of the bracket we
        # use roughly half the cap, at the ceiling we use the full cap.
        fi_w = min(fi_ip / 50.0, cap)
        blended = dict(pitcher)
        blended["era"]  = (1.0 - fi_w) * pitcher["era"]  + fi_w * fi_pitcher["era"]
        blended["whip"] = (1.0 - fi_w) * pitcher["whip"] + fi_w * fi_pitcher["whip"]
        p_mult = pitcher_multiplier(blended)
    else:
        p_mult = pitcher_multiplier(pitcher)

    o_mult    = offense_multiplier(batting)
    home_fact = HOME_RUN_FACTOR if home else AWAY_RUN_FACTOR
    lam = LEAGUE_FIRST_INNING_RUNS * o_mult * p_mult * pf * home_fact
    return max(0.05, lam)

# ---------------------------------------------------------------------------
# Pick classification
# ---------------------------------------------------------------------------

def classify_pick(lam: float, data_pts: int) -> tuple[str, str]:
    """
    Legacy lambda-threshold classifier. NOT used in production -- the live
    predictor uses classify_pick_lr() against the calibrated LR-v2 output.
    Retained because backtest.py imports it to regenerate historical CSVs
    that include the raw lambda-based pick as a baseline reference column.
    """
    if data_pts == 0:
        return "PASS", "NO DATA"

    if lam <= NRFI_STRONG_LAM:
        return "NRFI", "STRONG"
    if lam <= NRFI_LEAN_LAM:
        return "NRFI", "LEAN"
    if lam <= PASS_LAM_MAX:
        return "PASS", "NO EDGE"
    if lam <= YRFI_LEAN_LAM:
        return "YRFI", "LEAN"
    return "YRFI", "STRONG"


# ---------------------------------------------------------------------------
# LR-v3 two-stage + isotonic calibration -- the production predictor
# ---------------------------------------------------------------------------
# A first inning is two physically-separate half-innings, so we model them
# as two separate logistic regressions and multiply assuming independence:
#
#   T1 (top of 1st)  = home pitcher vs away offense  -> P(T1 has run)
#       features = fi_park_nrfi_rate, home_fip, away_obp
#   B1 (bot of 1st)  = away pitcher vs home offense  -> P(B1 has run)
#       features = fi_park_nrfi_rate, away_fip, home_obp
#
#   P(NRFI) = (1 - P(T1 run)) * (1 - P(B1 run))
#
# Why this shape: the V2 single-regression-on-11-features version produced
# multicollinearity-driven wrong-sign coefficients (5 of 11 features had
# the wrong sign because correlated pairs split each other's effect). Worse,
# it effectively ignored the away pitcher -- in a synthetic test, going
# from elite to trash away pitcher caused P(NRFI) to RISE by 2.4pp instead
# of fall.  The two-stage architecture forces each pitcher into the
# half-inning they actually pitch, so the away pitcher cannot be ignored.
#
# All weights now have intuitive signs:
#   T1:  fi_park (-)  home_fip (+)  away_obp (+)
#   B1:  fi_park (-)  away_fip (+)  home_obp (+)
#
# Output is passed through an isotonic calibrator (data/calibration_v2.json).
#
# All three files are required to run -- the predictor errors out cleanly
# if any is missing rather than silently falling back to anything else.

_LR_T1_PATH         = Path(__file__).parent / "data" / "lr_t1.json"
_LR_B1_PATH         = Path(__file__).parent / "data" / "lr_b1.json"
_LR_MODEL_PATH      = Path(__file__).parent / "data" / "lr_model.json"  # legacy V2 single-LR (kept for autopsy/backtests; unused at inference)
_LR_CAL_PATH        = Path(__file__).parent / "data" / "calibration_v2.json"
_FI_PARK_PATH       = Path(__file__).parent / "data" / "fi_park_factors.json"
_FI_PARK_NRFI_DEFAULT = 0.50

# Calibrated probability thresholds for pick zones (from calibration.py)
# Pick-zone thresholds.  Asymmetric per the 2026 retro forensic
# (analyze_zone_asymmetry.py):
#
#   STRONG NRFI threshold tightened 0.60 -> 0.62.  At 0.60 the zone went
#   9-7 (56.2%, +1.18u).  The 7 picks between 0.60 and 0.62 went 2-5 --
#   the underperformers.  At 0.62 the zone goes 7-2 (77.8%, +4.36u).
#
#   STRONG YRFI threshold loosened 0.40 -> 0.42 (i.e. P(NRFI) <= 0.42).
#   At 0.40 the zone went 43-23 (65.2%, +16.09u).  The 5 picks between
#   0.40 and 0.42 went 5-0.  Including them gives 48-23 (67.6%, +20.64u).
#
# These are sample-size-cautious adjustments.  Weekly recalibration may
# tune them further as 2026 graded games accumulate.
# Updated 2026-04-29 (last10_nrfi calibration shift): the +last10_nrfi
# addition shifted the calibrated probability distribution slightly --
# many borderline YRFI picks landed at ~0.43 instead of <0.42 under the
# new calibrator, leaving 70+ profitable YRFI picks classified PASS.
# Loosening to 0.56 / 0.44 recaptures those (live rebet: +27.7u, best
# of season vs +17.7u at 0.58/0.42 and +23.82u for the prior model).
# LEAN zones still collapsed -- only STRONG and PASS exist.
_LR_STRONG_NRFI_P = 0.56
_LR_LEAN_NRFI_P   = 0.56
_LR_PASS_LO_P     = 0.44
_LR_LEAN_YRFI_P   = 0.44

# Lambda floor for STRONG YRFI: minimum expected 1st-inning runs the
# model must project before we'll fire a STRONG YRFI bet, even when
# the calibrated NRFI prob clears the 0.44 threshold.
#
# Why: at lambda 0.74-0.78 the calibrator squashes raw NRFI prob hard
# enough to cross 0.44, but those games sit in a soft-edge zone where
# the historical hit rate falls to ~44%.  Backtest on the 132 graded
# 2026 STRONG YRFI picks shows that filtering out the lambda 0.74-0.78
# bucket (9 picks: 4W-5L) lifts the season +1.36u with no downside.
# Going higher than 0.78 starts cutting profitable bets.
#
# NRFI does NOT need a ceiling: all 39 graded STRONG NRFI picks have
# lambda <= 0.54, well below average, no bad zone to clip.
_LR_LAMBDA_YRFI_FLOOR = 0.78


def _write_thresholds_json() -> None:
    """Write the classifier thresholds to data/thresholds.json so the
    dashboard's TS tentative-classifier reads them at request time
    instead of duplicating hardcoded constants in BoardRow.tsx.  Cheap
    enough to call on every predictor run -- ensures any threshold
    tuning shows up on the dashboard immediately."""
    out = {
        "strongNrfiP":     _LR_STRONG_NRFI_P,
        "leanNrfiP":       _LR_LEAN_NRFI_P,
        "passLoP":         _LR_PASS_LO_P,
        "leanYrfiP":       _LR_LEAN_YRFI_P,
        "lambdaYrfiFloor": _LR_LAMBDA_YRFI_FLOOR,
        "writtenAtUtc":    datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds") + "Z",
    }
    try:
        path = Path(__file__).parent / "data" / "thresholds.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
    except Exception:
        # Non-fatal -- dashboard falls back to hardcoded defaults.
        pass


# Lazy-loaded singletons.  None = "tried to load and failed" (graceful fallback).
_lr_t1 = None
_lr_b1 = None
_lr_loaded = False
_lr_cal = None
_lr_cal_loaded = False
_fi_park_rates: dict | None = None
_fi_park_loaded = False

# Expected feature order for each half-inning model.
# Updated 2026-04-29: added SIGNED ERA GAP between starters as the 13th
# feature per half.  Encodes the user's "worse pitcher gives up the run"
# intuition LR cannot synthesize on its own.  test_era_gap.py shows
# +22u 3-split P&L improvement over Phase E.3 baseline.
#
# Convention: positive era_gap means THIS half's pitcher is worse than
# the opposing half's pitcher.
#   T1 (home pitcher pitches): home_era - away_era
#   B1 (away pitcher pitches): away_era - home_era
_T1_EXPECTED_FEATURES = [
    "fi_park_nrfi_rate", "home_fip", "away_obp",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "home_p_last5_pitcher_nrfi",
    "away_top3c_obp",
    "home_plate_ump_nrfi_rate",
    "home_xera",
    "home_whiff_pct_rank",
    "era_gap_t1",
    "home_p_last10_pitcher_nrfi",
    "away_top3c_slg",   # power signal: top-3 batters' slugging
    "away_top3c_iso",   # isolated power: SLG - AVG
    # Phase F: pitcher-vs-team familiarity + opener detection
    "home_pvt_nrfi_rate",      # career NRFI rate vs this opposing team (Bayesian-shrunk)
    "home_avg_ip_per_start",   # last-5 avg IP; <3 IP suggests opener
]
_B1_EXPECTED_FEATURES = [
    "fi_park_nrfi_rate", "away_fip", "home_obp",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "away_p_last5_pitcher_nrfi",
    "home_top3c_obp",
    "home_plate_ump_nrfi_rate",
    "away_xera",
    "away_whiff_pct_rank",
    "era_gap_b1",
    "away_p_last10_pitcher_nrfi",
    "home_top3c_slg",
    "home_top3c_iso",
    # Phase F: pitcher-vs-team familiarity + opener detection
    "away_pvt_nrfi_rate",
    "away_avg_ip_per_start",
]

# Defaults for new features when fetch fails (used in feature builders below)
_LEAGUE_NRFI_RATE = 0.50
_LEAGUE_AVG_XERA  = 4.20
_NEUTRAL_PCT_RANK = 50.0


def _load_lr_models() -> tuple[dict | None, dict | None]:
    """Load both half-inning LR models. Returns (t1_model, b1_model);
    either may be None if the corresponding file is missing or malformed.
    Validates feature_names match expected order to prevent silent
    weight-vs-input misalignment."""
    global _lr_t1, _lr_b1, _lr_loaded
    if _lr_loaded:
        return _lr_t1, _lr_b1
    _lr_loaded = True

    def _load_one(path: Path, expected: list[str]) -> dict | None:
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            names = d.get("feature_names", [])
            if names != expected:
                print(
                    f"  [warn] {path.name} feature order does not match expected\n"
                    f"         expected: {expected}\n"
                    f"         got:      {names}\n"
                    f"         model load aborted.",
                    file=sys.stderr,
                )
                return None
            if not (len(d["weights"]) == len(d["mean"]) == len(d["std"]) == len(names)):
                print(f"  [warn] {path.name} weight/mean/std/names length mismatch",
                      file=sys.stderr)
                return None
            return {
                "weights":       d["weights"],
                "bias":          float(d["bias"]),
                "feature_names": names,
                "mean":          d["mean"],
                "std":           d["std"],
            }
        except Exception as e:
            print(f"  [warn] {path.name} load failed: {e}", file=sys.stderr)
            return None

    _lr_t1 = _load_one(_LR_T1_PATH, _T1_EXPECTED_FEATURES)
    _lr_b1 = _load_one(_LR_B1_PATH, _B1_EXPECTED_FEATURES)
    return _lr_t1, _lr_b1


def _load_lr_calibrator():
    """Load the ProbCalibrator built from holdout predictions. Returns None if missing."""
    global _lr_cal, _lr_cal_loaded
    if _lr_cal_loaded:
        return _lr_cal
    _lr_cal_loaded = True
    if not _LR_CAL_PATH.exists():
        return None
    try:
        from calibration import ProbCalibrator
        _lr_cal = ProbCalibrator.load(_LR_CAL_PATH)
        # T4.6: validate the calibration curve's shape on load.  PAV-only
        # enforces monotonicity but allows large jumps between adjacent
        # bins (a sign of overfit to noise).  Compute max bin-to-bin
        # delta; warn if any neighboring pair is >5pp apart, since
        # well-calibrated curves are smooth.
        _validate_calibrator_shape(_lr_cal)
    except Exception:
        _lr_cal = None
    return _lr_cal


def _validate_calibrator_shape(cal) -> None:
    """T4.6: Sanity-check the calibrator's shape.  Logs WARNINGs (not
    fatal) if neighboring bins jump >5pp -- that's a sign the calibrator
    overfit on a small holdout and the curve has noise spikes."""
    try:
        # ProbCalibrator stores ascending (raw_p, calibrated_p) pairs in
        # `_xs` and `_ys`.  Defensively access via getattr so a future
        # refactor of calibration.py doesn't break us.
        xs = getattr(cal, "_xs", None) or getattr(cal, "xs", None)
        ys = getattr(cal, "_ys", None) or getattr(cal, "ys", None)
        if not xs or not ys or len(xs) != len(ys) or len(xs) < 2:
            return
        max_jump = 0.0
        worst_idx = 0
        for i in range(1, len(ys)):
            jump = abs(ys[i] - ys[i-1])
            if jump > max_jump:
                max_jump = jump
                worst_idx = i
        if max_jump > 0.05:
            print(
                f"  WARNING: calibrator shape has a {max_jump*100:.1f}pp jump "
                f"between bin {worst_idx-1} (x={xs[worst_idx-1]:.3f}, y={ys[worst_idx-1]:.3f}) "
                f"and bin {worst_idx} (x={xs[worst_idx]:.3f}, y={ys[worst_idx]:.3f}).  "
                f"Smooth curves are typical of well-calibrated models; large jumps "
                f"suggest overfitting on small holdouts.  Consider refitting with "
                f"more data or smoothing.",
                file=sys.stderr,
            )
    except Exception:
        pass  # validation is non-fatal


def _load_fi_park_rates() -> dict:
    """Load the empirical FI park factors. Returns {} if unavailable.
    T3.9: warn loudly when the file is missing or fails to load -- the
    LR model relies on per-park first-inning NRFI rates as a feature,
    and a silent fallback to the neutral default of 0.50 across all
    parks effectively de-features the model without anyone knowing."""
    global _fi_park_rates, _fi_park_loaded
    if _fi_park_loaded:
        return _fi_park_rates or {}
    _fi_park_loaded = True
    if not _FI_PARK_PATH.exists():
        print(
            f"  WARNING: FI park factors file not found at {_FI_PARK_PATH}.\n"
            f"           All games will use the neutral default ({_FI_PARK_NRFI_DEFAULT:.2f}),\n"
            f"           silently de-featuring the LR model's park signal.\n"
            f"           Run: python rebuild_park_factors.py",
            file=sys.stderr,
        )
        _fi_park_rates = {}
        return _fi_park_rates
    try:
        with open(_FI_PARK_PATH, encoding="utf-8") as f:
            _fi_park_rates = json.load(f)
        if not _fi_park_rates:
            print(
                f"  WARNING: FI park factors file at {_FI_PARK_PATH} is empty.\n"
                f"           Run: python rebuild_park_factors.py",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"  WARNING: failed to parse FI park factors at {_FI_PARK_PATH}: {exc}\n"
            f"           Falling back to neutral default for all parks.",
            file=sys.stderr,
        )
        _fi_park_rates = {}
    return _fi_park_rates


def _fetch_open_meteo_forecast(lat: float, lon: float, date_iso: str) -> dict | None:
    """
    Hit the open-meteo FORECAST endpoint for recent / current dates.
    Returns {temp_c, wind_kmh, humidity} for the 19:00 local-ish slot
    on date_iso, or None on failure.

    The archive API only has data through ~5 days ago, so for the live
    predictor we need the forecast endpoint (which actually returns
    past + future via past_days/forecast_days params).
    """
    import urllib.request
    import json as _json
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,wind_speed_10m,relative_humidity_2m"
        "&past_days=92&forecast_days=2"
        "&timezone=America/New_York"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    hourly = payload.get("hourly", {})
    times  = hourly.get("time",                 [])
    temps  = hourly.get("temperature_2m",       [])
    winds  = hourly.get("wind_speed_10m",       [])
    hums   = hourly.get("relative_humidity_2m", [])
    # Pick 19:00 on date_iso, fallback to last available pre-19 hour
    rec = None
    for i, ts in enumerate(times):
        if not ts.startswith(date_iso):
            continue
        hh = ts.split("T", 1)[1][:2]
        if hh == "19":
            return {
                "temp_c":   float(temps[i]) if i < len(temps) and temps[i] is not None else None,
                "wind_kmh": float(winds[i]) if i < len(winds) and winds[i] is not None else None,
                "humidity": float(hums[i])  if i < len(hums)  and hums[i]  is not None else None,
            }
        if int(hh) <= 19:
            rec = {
                "temp_c":   float(temps[i]) if i < len(temps) and temps[i] is not None else None,
                "wind_kmh": float(winds[i]) if i < len(winds) and winds[i] is not None else None,
                "humidity": float(hums[i])  if i < len(hums)  and hums[i]  is not None else None,
            }
    return rec


def fetch_game_weather(home_abbr: str, date_iso: str, season: int) -> dict:
    """
    Returns the weather dict expected by t1_features/b1_features:
      {temp_c, wind_kmh, humidity, is_dome}

    For domed / retractable-roof parks (DOMED_PARKS), returns neutral
    league-mean defaults with is_dome=1 -- the model uses is_dome as the
    "ignore weather" switch.

    Outdoor parks: tries backtest.game_weather (archive cache, fast for
    historical dates), then falls back to a live open-meteo forecast
    fetch for current-year dates the archive doesn't have yet.  If both
    fail, returns neutral defaults with is_dome=0.
    """
    if home_abbr in DOMED_PARKS:
        return {
            "temp_c":   WX_TEMP_DEFAULT,
            "wind_kmh": WX_WIND_DEFAULT,
            "humidity": WX_HUMIDITY_DEFAULT,
            "is_dome":  1.0,
        }
    wx = {}
    try:
        from backtest import game_weather, PARK_COORDS
        wx = game_weather(home_abbr, date_iso, season, use_cache=True) or {}
    except Exception:
        wx = {}
        PARK_COORDS = {}
    temp = wx.get("temp_c")
    wind = wx.get("wind_kmh")
    hum  = wx.get("humidity")
    # Archive miss?  Try the forecast endpoint (handles current year).
    if (temp is None or wind is None or hum is None) and home_abbr in PARK_COORDS:
        lat, lon = PARK_COORDS[home_abbr]
        fc = _fetch_open_meteo_forecast(lat, lon, date_iso)
        if fc is not None:
            if temp is None: temp = fc.get("temp_c")
            if wind is None: wind = fc.get("wind_kmh")
            if hum  is None: hum  = fc.get("humidity")
    return {
        "temp_c":   float(temp) if temp is not None else WX_TEMP_DEFAULT,
        "wind_kmh": float(wind) if wind is not None else WX_WIND_DEFAULT,
        "humidity": float(hum)  if hum  is not None else WX_HUMIDITY_DEFAULT,
        "is_dome":  0.0,
    }


# Phase E.3 lookup helpers -- lazy-load caches once

_statcast_cache = None
_ump_cache = None
_ump_rates_data = None


def _load_statcast_cache() -> dict:
    global _statcast_cache
    if _statcast_cache is not None:
        return _statcast_cache
    p = Path(__file__).parent / "data" / "statcast_pitcher_cache.json"
    if not p.exists():
        _statcast_cache = {}
        return _statcast_cache
    try:
        with open(p, encoding="utf-8") as f:
            _statcast_cache = json.load(f)
    except Exception:
        _statcast_cache = {}
    return _statcast_cache


# T4.2: priors-aware pitcher Statcast cache.
#
# WHY THIS EXISTS
# ---------------
# Production used to read raw season-to-date Statcast aggregates from
# data/statcast_pitcher_cache.json.  In April-May of a fresh season,
# pitchers have ~3-6 starts each, and per-pitcher xera computed on
# 30-80 batted balls is wildly noisy (observed 2026 stdev=1.66 vs
# stable-state 2025 stdev=0.89, with min=0.64 and max=14.71).
# Feeding that noise into the LR model as if it were a true-talent
# estimate produced over-confident picks that all moved together
# inside the calibrator's flat zones (e.g. 5/03: three different
# YRFI bets all calibrated to P(YRFI)=0.638, all lost: -3u that
# should have been three independent decisions).
#
# WHAT THIS DOES
# --------------
# Bayesian-pooled per-pitcher per-date snapshot built by
# tools/build_truepit_2026_with_priors.py:
#
#   pooled_xera[pid, date] = pool( 2025_full_season, 2026_thru_yesterday )
#
# where pooling weights each pitch equally regardless of season (alpha=1
# full pooling).  Early-season picks lean ~95% on the 2025 prior; by
# season's end, ~50/50.  Rookies without 2025 data fall through to
# the raw cache, then league average.
#
# WIRE-UP CONTRACT
# ----------------
# - JSON shape: {"per_pitcher": {pid_str: {date_iso: {xera, whiff_pct_rank, ...}}}}
# - Lookup: for (pid, date_iso), find latest entry <= date_iso (or
#   earliest entry if date_iso predates everything).
# - The fallback chain is: priors -> raw season cache -> league average.
#
# T-V21-LOCKIN-2026-05-06: priors-pooling is the only path.  The
# `_USE_TRUEPIT_PRIORS` toggle (locked-on at T4.10) was deleted along
# with the V2-vs-V2.1 shadow surface.  fetch_pitcher_statcast() now
# unconditionally tries the priors snapshot first and falls through to
# the raw season cache only when the priors JSON has no entry for the
# pitcher (rookies without 2025 data).  Reverting this would
# re-introduce the small-sample-noise pathology that caused 5/03's
# disaster (xera=14.71 driving STRONG YRFI on a single 5-batted-ball
# sample) -- don't.
#
# This is the official MLB First-Inning predictor model.
# Refer to docs/PLAYBOOK.md for the diagnostic stack that monitors it.

# Model version label.  V2.1 = V2 LR (18 features per half-inning) +
# T4.2 priors-pooled xera/whiff features + V2 calibrator.  Logged into
# pick_reasoning JSONs so the dashboard / future post-mortems can tell
# which exact model produced any given pick.  Bump the minor (V2.2)
# for feature-engineering improvements that keep the 18-feature LR
# architecture; bump major (V3) for actual architecture changes.
MODEL_VERSION = "V2.1"

_truepit_priors_cache: dict | None = None


def _load_truepit_priors() -> dict:
    """Load priors-aware pitcher Statcast snapshots.  Cached.  Returns
    {} on any error so production never breaks if the file is missing
    or corrupted."""
    global _truepit_priors_cache
    if _truepit_priors_cache is not None:
        return _truepit_priors_cache
    p = (Path(__file__).parent / "data" / "v2_perfect_2026"
         / "truepit_priors_per_pitcher_per_date.json")
    if not p.exists():
        _truepit_priors_cache = {}
        return _truepit_priors_cache
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        _truepit_priors_cache = data.get("per_pitcher", {}) or {}
    except Exception:    # noqa: BLE001
        _truepit_priors_cache = {}
    return _truepit_priors_cache


def _truepit_lookup(player_id: int, date_iso: str) -> dict | None:
    """Find the priors-pooled snapshot for (pid, date) -- the latest
    snapshot <= date_iso (so a 2026-05-04 prediction uses everything
    we knew at the start of 2026-05-04, including all prior 2026
    appearances + the 2025 season prior).

    Returns None if the pitcher isn't in the priors cache.  Returns the
    earliest snapshot if date_iso predates everything (still better than
    raw cache: at minimum it's the 2025 prior + zero 2026 data, which
    is what we want for season-debut starts)."""
    priors = _load_truepit_priors()
    pid_entries = priors.get(str(player_id))
    if not pid_entries:
        return None
    # All keys are ISO date strings; lexical sort = chronological.
    sorted_dates = sorted(pid_entries.keys())
    # Find latest date <= date_iso.  bisect_right would also work but
    # the data is small (<200 dates per pitcher) so a linear scan is fine.
    chosen = None
    for d in sorted_dates:
        if d <= date_iso:
            chosen = d
        else:
            break
    if chosen is None:
        # Date predates all snapshots -- use the earliest one
        # (it's just the 2025 prior + 0 2026 pitches, perfectly fine
        # for a season debut).
        chosen = sorted_dates[0]
    return pid_entries.get(chosen)


def _load_ump_data() -> tuple[dict, dict]:
    """Returns (game_pk -> ump record, ump_id -> rate record)."""
    global _ump_cache, _ump_rates_data
    if _ump_cache is not None:
        return _ump_cache, _ump_rates_data
    cache_p = Path(__file__).parent / "data" / "umpire_cache.json"
    rates_p = Path(__file__).parent / "data" / "umpire_rates.json"
    _ump_cache = {}
    _ump_rates_data = {}
    try:
        if cache_p.exists():
            with open(cache_p, encoding="utf-8") as f:
                _ump_cache = json.load(f)
    except Exception:
        pass
    try:
        if rates_p.exists():
            with open(rates_p, encoding="utf-8") as f:
                _ump_rates_data = json.load(f)
    except Exception:
        pass
    return _ump_cache, _ump_rates_data


def fetch_pitcher_statcast(player_id: int, season: int,
                            date_iso: str | None = None) -> dict:
    """Returns Statcast aggregates for a pitcher: xera + whiff_pct_rank.

    Lookup priority (T4.2):
      1. If date_iso is provided AND priors are enabled, look up the
         priors-pooled (2025 prior + 2026 thru-yesterday) snapshot.
         This is the calibrated, low-variance source.
      2. Otherwise (or on miss) fall through to the raw season-to-date
         cache (data/statcast_pitcher_cache.json keyed by season+pid).
         This still has the small-sample-noise problem for early-season
         pitchers but is better than nothing.
      3. On full miss, return neutral defaults (league-average xera +
         50th-percentile whiff rank).

    Backward compatible: callers that don't pass date_iso get the
    legacy raw-cache behavior, so no caller is force-changed by this
    refactor."""
    if date_iso:
        snap = _truepit_lookup(player_id, date_iso)
        if snap is not None:
            return {
                "xera":           float(snap["xera"])
                                  if snap.get("xera") is not None else _LEAGUE_AVG_XERA,
                "whiff_pct_rank": float(snap["whiff_pct_rank"])
                                  if snap.get("whiff_pct_rank") is not None
                                  else _NEUTRAL_PCT_RANK,
            }
    cache = _load_statcast_cache()
    season_cache = cache.get(str(season), {})
    rec = season_cache.get(str(player_id))
    if not rec:
        return {"xera": _LEAGUE_AVG_XERA, "whiff_pct_rank": _NEUTRAL_PCT_RANK}
    return {
        "xera":           float(rec["xera"]) if rec.get("xera") is not None else _LEAGUE_AVG_XERA,
        "whiff_pct_rank": float(rec["whiff_pct_rank"]) if rec.get("whiff_pct_rank") is not None else _NEUTRAL_PCT_RANK,
    }


def fetch_umpire_rate(game_pk: int) -> tuple[str, float]:
    """Look up home plate umpire's career NRFI rate for a game.
    Returns (ump_id, rate).  Falls back to league avg if game/ump not in cache.

    Tries the cache first; if miss, fetches via schedule API to populate
    today's games (cache rebuilds happen separately)."""
    cache, rates = _load_ump_data()
    league = rates.get("league_nrfi_rate", _LEAGUE_NRFI_RATE)
    rec = cache.get(str(game_pk))
    if not rec:
        # Try a live API fetch for today's games (not in cache yet)
        try:
            data = statsapi.get("schedule", {
                "sportId": 1, "gamePk": game_pk,
                "hydrate": "officials",
                "fields": "dates,games,gamePk,officials,officialType,official,fullName,id",
            })
            for d in data.get("dates", []):
                for g in d.get("games", []):
                    if int(g.get("gamePk") or 0) != int(game_pk): continue
                    for o in g.get("officials", []):
                        if o.get("officialType") == "Home Plate":
                            ump_id = str(int(o.get("official", {}).get("id") or 0))
                            if ump_id and ump_id != "0":
                                u = rates.get("umpires", {}).get(ump_id)
                                rate = u["shrunk_nrfi"] if u else league
                                return ump_id, rate
        except Exception:
            pass
        return "", league
    ump_id = str(rec["hp_id"])
    u = rates.get("umpires", {}).get(ump_id)
    return ump_id, (u["shrunk_nrfi"] if u else league)


def t1_features(home_abbr: str, home_pitcher: dict, away_offense: dict,
                 wx: dict | None = None,
                 home_pitcher_id: int = 0,
                 away_pitcher: dict | None = None,
                 home_last5_nrfi: float = _LEAGUE_NRFI_RATE,
                 home_last10_nrfi: float = _LEAGUE_NRFI_RATE,
                 away_top3c_obp:  float = LEAGUE_AVG_OBP,
                 away_top3c_slg:  float = LEAGUE_AVG_SLG,
                 away_top3c_iso:  float = 0.169,
                 ump_rate:        float = _LEAGUE_NRFI_RATE,
                 home_pvt_nrfi:   float = _LEAGUE_NRFI_RATE,
                 home_avg_ip_per_start: float = 5.0,
                 season: int = 2026,
                 date_iso: str | None = None) -> list[float]:
    """T1 (top of 1st) feature vector: home pitcher vs away offense at home park.
    Order MUST match _T1_EXPECTED_FEATURES.

    Phase F: 18 features per half (added pvt_nrfi familiarity + opener
    detection via avg IP per start).

    T4.2: when date_iso is provided, the pitcher Statcast lookup uses
    priors-pooled (2025 prior + 2026 thru-yesterday) values instead of
    the raw season cache.  This dramatically reduces small-sample noise
    in early-season (April/May) predictions."""
    fi_park = _load_fi_park_rates().get(home_abbr, _FI_PARK_NRFI_DEFAULT)
    if wx is None:
        wx = {"temp_c": WX_TEMP_DEFAULT, "wind_kmh": WX_WIND_DEFAULT,
              "humidity": WX_HUMIDITY_DEFAULT, "is_dome": 0.0}
    sc = fetch_pitcher_statcast(home_pitcher_id, season, date_iso=date_iso) if home_pitcher_id else {
        "xera": _LEAGUE_AVG_XERA, "whiff_pct_rank": _NEUTRAL_PCT_RANK,
    }
    h_era = home_pitcher.get("era", LEAGUE_AVG_ERA)
    a_era = (away_pitcher or {}).get("era", LEAGUE_AVG_ERA)
    era_gap_t1 = h_era - a_era
    return [
        fi_park,
        home_pitcher.get("fip", LEAGUE_AVG_ERA),
        away_offense.get("obp", LEAGUE_AVG_OBP),
        wx.get("temp_c",   WX_TEMP_DEFAULT),
        wx.get("wind_kmh", WX_WIND_DEFAULT),
        wx.get("humidity", WX_HUMIDITY_DEFAULT),
        wx.get("is_dome",  0.0),
        home_last5_nrfi,
        away_top3c_obp,
        ump_rate,
        sc["xera"],
        sc["whiff_pct_rank"],
        era_gap_t1,
        home_last10_nrfi,
        away_top3c_slg,
        away_top3c_iso,
        home_pvt_nrfi,                # Phase F: pitcher vs this team familiarity
        home_avg_ip_per_start,        # Phase F: opener detection (low IP -> opener)
    ]


def b1_features(home_abbr: str, away_pitcher: dict, home_offense: dict,
                 wx: dict | None = None,
                 away_pitcher_id: int = 0,
                 home_pitcher: dict | None = None,
                 away_last5_nrfi: float = _LEAGUE_NRFI_RATE,
                 away_last10_nrfi: float = _LEAGUE_NRFI_RATE,
                 home_top3c_obp:  float = LEAGUE_AVG_OBP,
                 home_top3c_slg:  float = LEAGUE_AVG_SLG,
                 home_top3c_iso:  float = 0.169,
                 ump_rate:        float = _LEAGUE_NRFI_RATE,
                 away_pvt_nrfi:   float = _LEAGUE_NRFI_RATE,
                 away_avg_ip_per_start: float = 5.0,
                 season: int = 2026,
                 date_iso: str | None = None) -> list[float]:
    """B1 (bottom of 1st) feature vector: away pitcher vs home offense at home park.
    Order MUST match _B1_EXPECTED_FEATURES.  Phase F: 18 features.

    T4.2: see t1_features for the date_iso priors-pooling rationale."""
    fi_park = _load_fi_park_rates().get(home_abbr, _FI_PARK_NRFI_DEFAULT)
    if wx is None:
        wx = {"temp_c": WX_TEMP_DEFAULT, "wind_kmh": WX_WIND_DEFAULT,
              "humidity": WX_HUMIDITY_DEFAULT, "is_dome": 0.0}
    sc = fetch_pitcher_statcast(away_pitcher_id, season, date_iso=date_iso) if away_pitcher_id else {
        "xera": _LEAGUE_AVG_XERA, "whiff_pct_rank": _NEUTRAL_PCT_RANK,
    }
    a_era = away_pitcher.get("era", LEAGUE_AVG_ERA)
    h_era = (home_pitcher or {}).get("era", LEAGUE_AVG_ERA)
    era_gap_b1 = a_era - h_era
    return [
        fi_park,
        away_pitcher.get("fip", LEAGUE_AVG_ERA),
        home_offense.get("obp", LEAGUE_AVG_OBP),
        wx.get("temp_c",   WX_TEMP_DEFAULT),
        wx.get("wind_kmh", WX_WIND_DEFAULT),
        wx.get("humidity", WX_HUMIDITY_DEFAULT),
        wx.get("is_dome",  0.0),
        away_last5_nrfi,
        home_top3c_obp,
        ump_rate,
        sc["xera"],
        sc["whiff_pct_rank"],
        era_gap_b1,
        away_last10_nrfi,
        home_top3c_slg,
        home_top3c_iso,
        away_pvt_nrfi,                # Phase F: pitcher vs this team familiarity
        away_avg_ip_per_start,        # Phase F: opener detection
    ]


# T3.8: One-time warning per (model, feature_idx) pair when we encounter
# std <= 0 -- previously silently skipped, hiding the fact that a feature
# the model expects has zero variance in the training set (model is
# broken / mis-fit).  Cron logs would surface the warning in workflow output.
_lr_zero_var_warned: set[tuple[int, int]] = set()


def _lr_feature_contributions(
    features: list[float],
    m: dict,
    feature_names: list[str],
    top_n: int = 5,
) -> list[dict]:
    """T4.15: For the dashboard's "Why this pick?" panel.  Returns the top-N
    features by absolute signed contribution `w * (x - mean) / std`.
    A positive contribution means "pushes toward NRFI" (lower P(run)),
    a negative one means "pushes toward YRFI".  The bias term is omitted
    because it's the same for every game.
    """
    contribs: list[tuple[str, float, float]] = []
    for i, (name, x, mean, std, w) in enumerate(zip(
        feature_names, features, m["mean"], m["std"], m["weights"],
    )):
        if std <= 0:
            continue
        z_part = w * (x - mean) / std
        contribs.append((name, x, z_part))
    contribs.sort(key=lambda t: abs(t[2]), reverse=True)
    return [
        {"name": n, "value": round(x, 4), "contribution": round(z, 4)}
        for n, x, z in contribs[:top_n]
    ]

def _lr_predict_one(features: list[float], m: dict) -> float:
    """Apply a single standardized-feature LR forward pass."""
    z = m["bias"]
    model_id = id(m)
    for i, (x, mean, std, w) in enumerate(zip(features, m["mean"], m["std"], m["weights"])):
        if std <= 0:
            key = (model_id, i)
            if key not in _lr_zero_var_warned:
                _lr_zero_var_warned.add(key)
                print(
                    f"  WARNING: LR feature index {i} has std={std} (zero variance) -- "
                    f"feature contributes nothing to the prediction.  Likely a "
                    f"training-set issue; consider refitting.",
                    file=sys.stderr,
                )
            continue
        z += w * (x - mean) / std
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def lr_predict_nrfi(t1_feats: list[float], b1_feats: list[float]) -> float | None:
    """
    Two-stage NRFI prediction.  Returns None when either half-inning model
    is missing.  Each stage is a logistic regression fit to predict whether
    that half-inning will have a run; we assume independence and combine:

        P(NRFI) = (1 - P(T1 has run)) * (1 - P(B1 has run))

    Pure-Python forward pass so the live predictor doesn't need numpy.
    """
    m_t1, m_b1 = _load_lr_models()
    if m_t1 is None or m_b1 is None:
        return None
    p_t1 = _lr_predict_one(t1_feats, m_t1)
    p_b1 = _lr_predict_one(b1_feats, m_b1)
    return (1.0 - p_t1) * (1.0 - p_b1)


def _weather_adjusted_floor(
    base: float,
    wx_temp_c:    float | None = None,
    wx_wind_kmh:  float | None = None,
    wx_is_dome:   bool = False,
) -> float:
    """T4.3: Scale the lambda floor by weather.  Hot + windy outdoor games
    naturally produce more runs (carry distance + ball flight), so we
    can require a slightly higher lambda before committing to YRFI --
    the model's calibrated probability already accounts for weather
    via t1_feats/b1_feats, but the floor is a guardrail and benefits
    from explicit weather context when the calibrator is borderline.

    Adjustment range is intentionally narrow (±0.04) to avoid
    overcorrecting on a feature already in the LR.  Dome games skip
    the adjustment entirely (weather is neutralized).
    """
    if wx_is_dome:
        return base
    delta = 0.0
    # Hot games (>= 28°C / 82°F): bump floor up 0.02 -- ball carries
    # easier, marginal YRFI plays should clear a higher bar.
    if wx_temp_c is not None and wx_temp_c >= 28.0:
        delta += 0.02
    # Cold games (<= 12°C / 54°F): lower floor 0.02 -- ball doesn't
    # carry, so the same lambda represents a more meaningful YRFI
    # signal.
    elif wx_temp_c is not None and wx_temp_c <= 12.0:
        delta -= 0.02
    # Strong wind (>= 24 km/h / 15 mph): bump floor 0.02.  We don't
    # know direction here without parsing wx more deeply; treat any
    # strong wind as a YRFI booster (most parks have prevailing
    # out-to-CF) and require higher conviction.
    if wx_wind_kmh is not None and wx_wind_kmh >= 24.0:
        delta += 0.02
    return max(0.40, min(1.20, base + delta))


def classify_pick_lr(p_nrfi: float, data_pts: int,
                     lambda_total: float | None = None,
                     wx_temp_c:    float | None = None,
                     wx_wind_kmh:  float | None = None,
                     wx_is_dome:   bool = False) -> tuple[str, str]:
    """
    Pick zone based on calibrated NRFI probability + a (weather-aware)
    lambda floor on the YRFI side.

    `lambda_total` is the model's raw expected 1st-inning runs (lambda_t1
    + lambda_b1).  Optional for backwards-compat: if not provided, the
    lambda gate is skipped (legacy behavior).

    YRFI demotion rule: if a game would otherwise classify as STRONG/LEAN
    YRFI but its lambda is below the floor, demote to PASS.  T4.3 makes
    the floor weather-aware (±0.02 per condition).
    """
    if data_pts == 0:
        return "PASS", "NO DATA"
    if p_nrfi >= _LR_STRONG_NRFI_P:
        return "NRFI", "STRONG"
    if p_nrfi >= _LR_LEAN_NRFI_P:
        return "NRFI", "LEAN"
    if p_nrfi >= _LR_PASS_LO_P:
        return "PASS", "NO EDGE"
    # Below the PASS_LO threshold we'd normally fire a YRFI pick.  Apply
    # the (weather-adjusted) lambda floor first -- if the model's raw
    # expected runs is below the floor, the YRFI signal isn't strong
    # enough to bet on.  Tag "LOW LAMBDA" so the demotion is visible.
    floor = _weather_adjusted_floor(_LR_LAMBDA_YRFI_FLOOR, wx_temp_c, wx_wind_kmh, wx_is_dome)
    if lambda_total is not None and lambda_total < floor:
        return "PASS", "LOW LAMBDA"
    if p_nrfi >= _LR_LEAN_YRFI_P:
        return "YRFI", "LEAN"
    return "YRFI", "STRONG"


def lr_active() -> bool:
    """True when both T1+B1 models AND park factors are loaded successfully."""
    t1, b1 = _load_lr_models()
    return t1 is not None and b1 is not None and bool(_load_fi_park_rates())


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

BAR_WIDTH = 28

def prob_bar(p: float) -> str:
    filled = round(p * BAR_WIDTH)
    return f"[{'#'*filled}{'-'*(BAR_WIDTH-filled)}] {p*100:.1f}%"

def _c(color_attr: str, text: str) -> str:
    if not HAS_COLOR:
        return text
    return getattr(Fore, color_attr, "") + text + Style.RESET_ALL

def color_for_prob(p: float, text: str) -> str:
    if p >= 0.70: return _c("GREEN",  text)
    if p >= 0.60: return _c("YELLOW", text)
    return _c("RED", text)

def data_tag(quality: str) -> str:
    """Color-coded quality label: live / ltd / sm / avg."""
    colors = {"live": "GREEN", "ltd": "YELLOW", "sm": "CYAN", "avg": "RED"}
    col = colors.get(quality, "RED")
    return _c(col, quality) if HAS_COLOR else quality

def format_game_time(iso_str: str, placeholder: bool = False) -> str:
    """
    MLB API returns UTC ISO timestamps (e.g. '2026-04-25T23:10:00Z').
    Convert to US Eastern wall-clock time and format as '07:10 PM ET'.
    Auto-handles EDT vs EST via the zoneinfo db.

    When `placeholder=True` (DH-Y game 2 before game 1 ends, MLB still
    reports the same time as game 1), return "After Game 1" instead of
    the misleading wall-clock time so the dashboard doesn't pretend it
    knows the start.
    """
    if placeholder:
        return "After Game 1"
    try:
        dt_utc = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        dt_et  = dt_utc.astimezone(_ET)
        # %I gives zero-padded 12h on Linux, but %#I (Windows) / removing zero with lstrip is fine.
        return dt_et.strftime("%I:%M %p ET").lstrip("0") or "12:00 AM ET"
    except Exception:
        return iso_str or "TBD"

def dh_label(game: dict) -> str:
    dh = game.get("double_header", "N")
    num = game.get("game_number", 1)
    if dh in ("Y", "S"):
        return f" (DH Game {num})"
    return ""


def print_game_result(game: dict, only_strong: bool = False) -> None:
    away_abbr = game["away"]["abbr"]
    home_abbr = game["home"]["abbr"]
    lam       = game["lambda_total"]

    # Use pre-computed values stored in the result dict
    nrfi    = game["nrfi_prob"]
    yrfi    = game["yrfi_prob"]
    over15  = game["over_1_5_prob"]
    under15 = game["under_1_5_prob"]
    side    = game["pick_side"]
    conf    = game["pick_conf"]

    # --strong flag: skip PASS and pure NO-EDGE games
    if only_strong and conf not in ("STRONG", "LEAN"):
        return

    sep = "=" * 70
    print(sep)

    # Header: matchup + doubleheader label
    print(
        f"  {away_abbr:>3}  @  {home_abbr:<3}   "
        f"Game time: {game['time']}{dh_label(game)}"
    )
    # Pitchers with data-quality tags
    ap = game["away"]
    hp = game["home"]
    print(
        f"  {ap['pitcher_name']:<28} [{data_tag(ap['pitcher_q'])}]  "
        f"vs  {hp['pitcher_name']:<28} [{data_tag(hp['pitcher_q'])}]"
    )
    print(
        f"  Offense: {away_abbr} [{data_tag(ap['batting_q'])}]  "
        f"vs  {home_abbr} [{data_tag(hp['batting_q'])}]"
    )
    live_count = sum(1 for q in [ap['pitcher_q'], hp['pitcher_q'], ap['batting_q'], hp['batting_q']] if q != "avg")
    print(f"  Park factor: {game['park_factor']:.3f}  |  Live/blended inputs: {live_count}/4")

    # Pitcher recent first-inning record (last 5 / last 10 starts).  Game-level
    # NRFI rate = how often the pitcher's start went NRFI overall.  Pitcher
    # NRFI rate = how often THIS pitcher gave up no runs in their own half.
    def _last_n_str(rec):
        if not rec:
            return "no recent data"
        n   = rec["n"]
        gp  = rec["game_nrfi_rate"]    * 100
        pp  = rec["pitcher_nrfi_rate"] * 100
        return f"NRFI {gp:>4.0f}%  |  ownNRFI {pp:>4.0f}% (n={n})"

    a5 = ap.get("last5"); a10 = ap.get("last10")
    h5 = hp.get("last5"); h10 = hp.get("last10")
    if any([a5, a10, h5, h10]):
        print(f"  Recent (last 5):  {away_abbr} {_last_n_str(a5):<32}  "
              f"{home_abbr} {_last_n_str(h5)}")
        print(f"  Recent (last 10): {away_abbr} {_last_n_str(a10):<32}  "
              f"{home_abbr} {_last_n_str(h10)}")
    print()

    # Projected runs
    print("  Projected 1st-inning runs:")
    print(f"    {away_abbr} bats (top 1st)   : {ap['lambda']:.3f}")
    print(f"    {home_abbr} bats (bot 1st)   : {hp['lambda']:.3f}")
    print(f"    Combined lambda              : {lam:.3f}")
    print()

    # Probability table
    print("  Probabilities:")
    print(f"    NRFI  (0 runs)     {color_for_prob(nrfi,   prob_bar(nrfi))}")
    print(f"    YRFI  (1+ runs)    {color_for_prob(yrfi,   prob_bar(yrfi))}")
    print(f"    Over  1.5 runs     {color_for_prob(over15, prob_bar(over15))}")
    print(f"    Under 1.5 runs     {color_for_prob(under15,prob_bar(under15))}")
    print()

    # Pick line -- under LR the baseline is the empirical ~50% NRFI rate, so
    # we just show the predicted probability directly with a signed edge.
    conf_colors = {"STRONG": "GREEN", "LEAN": "YELLOW", "NO EDGE": "RED", "NO DATA": "RED"}

    def _signed(pp: float) -> str:
        return f"+{pp:.1f}pp" if pp >= 0 else f"{pp:.1f}pp"

    # Empirically-derived realistic win rates per zone, computed from the
    # 2024+2025 backtest (4,802 games of v1 LR predictions vs actual outcomes).
    # Use these (not raw model probability) for breakeven odds -- the model
    # is slightly overconfident at the extremes.
    _ZONE_WIN_RATE = {
        ("NRFI",  "STRONG"):  0.575,   # observed Q5 NRFI rate
        ("NRFI",  "LEAN"):    0.535,
        ("YRFI",  "LEAN"):    0.545,
        ("YRFI",  "STRONG"):  0.629,   # observed STRONG YRFI rate
    }

    def _be_from_winrate(wr: float) -> str:
        """American-odds break-even given empirical win rate."""
        wr = max(0.001, min(0.999, wr))
        if wr >= 0.5:
            return f"-{round(100 * wr / (1 - wr))}"
        return f"+{round(100 * (1 - wr) / wr)}"

    if lr_active():
        baseline = 0.50
        if side in ("NRFI", "YRFI"):
            wr_emp = _ZONE_WIN_RATE.get((side, conf), 0.50)
            be     = _be_from_winrate(wr_emp)
            if side == "NRFI":
                edge = round((nrfi - baseline) * 100, 1)
                pick_line = (
                    f"  >> {conf} NRFI  |  NRFI {nrfi*100:.1f}%  "
                    f"({_signed(edge)} vs 50%)  |  hist wins {wr_emp*100:.1f}%, "
                    f"bet at any line >= {be}"
                )
            else:
                edge = round((yrfi - baseline) * 100, 1)
                pick_line = (
                    f"  >> {conf} YRFI  |  YRFI {yrfi*100:.1f}%  "
                    f"({_signed(edge)} vs 50%)  |  hist wins {wr_emp*100:.1f}%, "
                    f"bet at any line >= {be}"
                )
        elif conf == "NO DATA":
            pick_line = f"  >> PASS  |  Insufficient data  (NRFI {nrfi*100:.1f}%)"
        else:
            pick_line = f"  >> PASS  |  No meaningful edge  (NRFI {nrfi*100:.1f}%)"
    else:
        BASELINE_NRFI = 0.38
        BASELINE_YRFI = 0.62
        if side == "NRFI":
            edge = round((nrfi - BASELINE_NRFI) * 100, 1)
            pick_line = (
                f"  >> {conf} NRFI  |  NRFI {nrfi*100:.1f}%  "
                f"({_signed(edge)} above {BASELINE_NRFI*100:.0f}% avg)"
            )
        elif side == "YRFI":
            edge = round((yrfi - BASELINE_YRFI) * 100, 1)
            pick_line = f"  >> {conf} YRFI  |  YRFI {yrfi*100:.1f}%  ({_signed(edge)} above avg)"
        elif conf == "NO DATA":
            pick_line = f"  >> PASS  |  Insufficient data  (YRFI {yrfi*100:.1f}%)"
        else:
            pick_line = f"  >> PASS  |  No meaningful edge  (YRFI {yrfi*100:.1f}%)"

    print(_c(conf_colors.get(conf, ""), pick_line) if HAS_COLOR else pick_line)
    print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _target_iso(target_date: str) -> str:
    """Convert MM/DD/YYYY to YYYY-MM-DD."""
    if "/" in target_date:
        m, d, y = target_date.split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return target_date[:10]


def run(target_date: str, only_strong: bool = False, debug: bool = False) -> None:
    season = int(target_date.split("/")[-1]) if "/" in target_date else date.today().year
    target_iso = _target_iso(target_date)

    if not lr_active():
        sys.exit(
            "LR two-stage models not available -- expected data/lr_t1.json, "
            "data/lr_b1.json, and data/fi_park_factors.json. Refit with "
            "two_stage_model.py."
        )
    cal = _load_lr_calibrator()
    if cal is None:
        sys.exit(
            "Calibrator not available -- expected data/calibration_v2.json. "
            "Refit with: python recalibrate_v2.py"
        )
    print(f"\nMLB First Inning Run Predictor  |  {target_date}  (season {season})")
    print(f"  Model: LR-v3 two-stage (T1 + B1) + isotonic calibration")
    print("Fetching schedule and stats...\n")

    # Lazy import -- backtest module reuses cache + last-N helpers without
    # creating a circular import at module load.
    try:
        from backtest import pitcher_last_n_first_inning
        recency_available = True
    except Exception:
        pitcher_last_n_first_inning = None
        recency_available = False

    schedule = fetch_schedule(target_date)

    if not schedule:
        print("No regular-season games found for this date.")
        return

    results = []

    for game in schedule:
        home_ab = game["home_abbr"]
        away_ab = game["away_abbr"]
        pf      = park_factor(home_ab)

        away_sp,  away_sp_q  = fetch_pitcher_stats(game["away_pitcher_id"], game["away_pitcher_name"], season)
        home_sp,  home_sp_q  = fetch_pitcher_stats(game["home_pitcher_id"], game["home_pitcher_name"], season)
        away_bat, away_bat_q = fetch_team_batting(game["away_team_id"], season)
        home_bat, home_bat_q = fetch_team_batting(game["home_team_id"], season)

        away_fi_sp = _try_fetch_pitcher_fi_stats(game["away_pitcher_id"], season)
        home_fi_sp = _try_fetch_pitcher_fi_stats(game["home_pitcher_id"], season)

        # Each half: batting team hits against the *opposing* pitcher
        # away team bats vs home SP; home team bats vs away SP
        away_lam  = expected_half_inning_runs(home_sp, away_bat, pf, home=False, fi_pitcher=home_fi_sp)
        home_lam  = expected_half_inning_runs(away_sp, home_bat, pf, home=True,  fi_pitcher=away_fi_sp)
        total_lam = away_lam + home_lam

        # data_points counts how many inputs are not pure league-average fallback
        data_pts = sum(q != "avg" for q in [away_sp_q, home_sp_q, away_bat_q, home_bat_q])

        if debug:
            print(
                f"[DEBUG] {game['game_pk']}  "
                f"{game['away_abbr']}({game['away_team_id']}) @ "
                f"{game['home_abbr']}({game['home_team_id']})"
                f"  DH={game['double_header']} G#{game['game_number']}"
            )
            fi_away = f" FI-ERA={away_fi_sp['era']:.2f}({away_fi_sp['ip']:.0f}ip)" if away_fi_sp else ""
            fi_home = f" FI-ERA={home_fi_sp['era']:.2f}({home_fi_sp['ip']:.0f}ip)" if home_fi_sp else ""
            print(
                f"  away SP: {game['away_pitcher_name']} (id={game['away_pitcher_id']}) "
                f"-> ERA {away_sp['era']:.2f} WHIP {away_sp['whip']:.2f} FIP {away_sp['fip']:.2f} "
                f"BB/9 {away_sp.get('bb9',3.0):.1f} HR/9 {away_sp.get('hr9',1.2):.1f} K/9 {away_sp.get('k9',8.9):.1f} "
                f"[{away_sp_q}]{fi_away}"
            )
            print(
                f"  home SP: {game['home_pitcher_name']} (id={game['home_pitcher_id']}) "
                f"-> ERA {home_sp['era']:.2f} WHIP {home_sp['whip']:.2f} FIP {home_sp['fip']:.2f} "
                f"BB/9 {home_sp.get('bb9',3.0):.1f} HR/9 {home_sp.get('hr9',1.2):.1f} K/9 {home_sp.get('k9',8.9):.1f} "
                f"[{home_sp_q}]{fi_home}"
            )
            print(
                f"  away bat: OBP {away_bat.get('obp', 0.318):.3f} SLG {away_bat.get('slg', 0.414):.3f} "
                f"RPG {away_bat['rpg']:.2f} [{away_bat_q}]  "
                f"home bat: OBP {home_bat.get('obp', 0.318):.3f} SLG {home_bat.get('slg', 0.414):.3f} "
                f"RPG {home_bat['rpg']:.2f} [{home_bat_q}]"
            )
            print()

        # Recent-form (informational; not part of the model decision).
        # Last-N first-inning history per pitcher.
        away_l5 = away_l10 = home_l5 = home_l10 = None
        if recency_available and pitcher_last_n_first_inning is not None:
            away_l5  = pitcher_last_n_first_inning(game["away_pitcher_id"], target_iso, season, n=5)
            away_l10 = pitcher_last_n_first_inning(game["away_pitcher_id"], target_iso, season, n=10)
            home_l5  = pitcher_last_n_first_inning(game["home_pitcher_id"], target_iso, season, n=5)
            home_l10 = pitcher_last_n_first_inning(game["home_pitcher_id"], target_iso, season, n=10)

        # Over/under 1.5 still uses raw Poisson on the lambda total --
        # there's no LR model for that prediction yet.
        over15p = prob_over_1_5(total_lam)

        # Primary NRFI/YRFI pick is LR-v3 Phase E.3 two-stage + isotonic
        # calibration.  T1 = home pitcher vs away offense; B1 = away
        # pitcher vs home offense.  Combined: P(NRFI) = (1 - P(T1)) * (1 - P(B1)).
        # Weather + Phase D + E.3 features (12 per half).  See
        # _T1/_B1_EXPECTED_FEATURES for the canonical order.
        wx = fetch_game_weather(home_ab, target_iso, season)

        # Phase D features (last5) + Phase F features (last10)
        away_last5 = _LEAGUE_NRFI_RATE
        home_last5 = _LEAGUE_NRFI_RATE
        away_last10 = _LEAGUE_NRFI_RATE
        home_last10 = _LEAGUE_NRFI_RATE
        if recency_available and pitcher_last_n_first_inning is not None:
            try:
                d_a = pitcher_last_n_first_inning(game["away_pitcher_id"], target_iso, season, n=5)
                if d_a and d_a.get("pitcher_nrfi_rate") is not None:
                    away_last5 = float(d_a["pitcher_nrfi_rate"])
            except Exception: pass
            try:
                d_h = pitcher_last_n_first_inning(game["home_pitcher_id"], target_iso, season, n=5)
                if d_h and d_h.get("pitcher_nrfi_rate") is not None:
                    home_last5 = float(d_h["pitcher_nrfi_rate"])
            except Exception: pass
            try:
                d_a10 = pitcher_last_n_first_inning(game["away_pitcher_id"], target_iso, season, n=10)
                if d_a10 and d_a10.get("pitcher_nrfi_rate") is not None:
                    away_last10 = float(d_a10["pitcher_nrfi_rate"])
            except Exception: pass
            try:
                d_h10 = pitcher_last_n_first_inning(game["home_pitcher_id"], target_iso, season, n=10)
                if d_h10 and d_h10.get("pitcher_nrfi_rate") is not None:
                    home_last10 = float(d_h10["pitcher_nrfi_rate"])
            except Exception: pass

        # Top-3 current-season-to-date OBP / SLG / ISO (lineup-aware via
        # fetch_top3_batters).  When the lineup hasn't posted yet (typical
        # at 9am ET predict time), fall back to the team's full-season
        # batting stats -- much closer to actual top-3 stats than league
        # average defaults.  We track the data source per side so a pick
        # built on fallback data is forced to PASS - LINEUP PENDING below
        # (parallel to STARTER PENDING for missing pitcher info).
        #   "lineup"         = real top-3 aggregate from posted lineup
        #   "team_fallback"  = team-level OBP/SLG used as a proxy
        #   "league_default" = LEAGUE_AVG used (team data also missing)
        away_top3c_source = "team_fallback" if away_bat.get("obp") is not None else "league_default"
        home_top3c_source = "team_fallback" if home_bat.get("obp") is not None else "league_default"
        away_top3c_obp = away_bat.get("obp", LEAGUE_AVG_OBP)
        home_top3c_obp = home_bat.get("obp", LEAGUE_AVG_OBP)
        away_top3c_slg = away_bat.get("slg", LEAGUE_AVG_SLG)
        home_top3c_slg = home_bat.get("slg", LEAGUE_AVG_SLG)
        # ISO = SLG - AVG; team_batting doesn't expose AVG directly so
        # approximate with league-average ISO when team data missing.
        # (When lineup is posted, current_season_top3_stats provides real ISO.)
        away_top3c_iso = 0.169
        home_top3c_iso = 0.169
        # Per-batter lineup detail surfaced to the dashboard so the user can
        # see WHO the pitcher is facing (name + per-batter OBP/SLG/ISO),
        # not just the aggregate.  Empty list when lineup hasn't posted.
        away_lineup: list[dict] = []
        home_lineup: list[dict] = []
        try:
            from backtest import (
                fetch_top3_batters,
                current_season_top3_stats,
                current_season_top3_per_batter,
            )
            top3 = fetch_top3_batters(int(game["game_pk"]))
            if top3.get("away_top3"):
                a_agg = current_season_top3_stats(top3["away_top3"], target_iso, season)
                if a_agg:
                    a_real = False
                    if a_agg.get("obp") is not None:
                        away_top3c_obp = float(a_agg["obp"]); a_real = True
                    if a_agg.get("slg") is not None:
                        away_top3c_slg = float(a_agg["slg"]); a_real = True
                    if a_agg.get("iso") is not None:
                        away_top3c_iso = float(a_agg["iso"]); a_real = True
                    if a_real:
                        away_top3c_source = "lineup"
                # Always populate the per-batter list when the lineup is in
                # (regardless of cold-start fallback) -- the dashboard wants
                # names even when stats are thin.
                away_lineup = current_season_top3_per_batter(
                    top3["away_top3"], target_iso, season,
                )
            if top3.get("home_top3"):
                h_agg = current_season_top3_stats(top3["home_top3"], target_iso, season)
                if h_agg:
                    h_real = False
                    if h_agg.get("obp") is not None:
                        home_top3c_obp = float(h_agg["obp"]); h_real = True
                    if h_agg.get("slg") is not None:
                        home_top3c_slg = float(h_agg["slg"]); h_real = True
                    if h_agg.get("iso") is not None:
                        home_top3c_iso = float(h_agg["iso"]); h_real = True
                    if h_real:
                        home_top3c_source = "lineup"
                home_lineup = current_season_top3_per_batter(
                    top3["home_top3"], target_iso, season,
                )
        except Exception: pass

        # Home plate umpire NRFI rate (Phase D)
        ump_id, ump_rate = fetch_umpire_rate(int(game["game_pk"]))

        # Phase F features: pitcher-vs-team familiarity + opener detection
        home_pvt_nrfi = away_pvt_nrfi = _LEAGUE_NRFI_RATE
        home_avg_ip = away_avg_ip = 5.0
        try:
            from backtest import pitcher_vs_team_nrfi_rate, pitcher_role_features
            home_pvt_nrfi = pitcher_vs_team_nrfi_rate(
                game["home_pitcher_id"], game.get("away_team_id"), target_iso,
            )
            away_pvt_nrfi = pitcher_vs_team_nrfi_rate(
                game["away_pitcher_id"], game.get("home_team_id"), target_iso,
            )
            h_role = pitcher_role_features(game["home_pitcher_id"], target_iso, season)
            a_role = pitcher_role_features(game["away_pitcher_id"], target_iso, season)
            if h_role: home_avg_ip = h_role["avg_ip_per_start"]
            if a_role: away_avg_ip = a_role["avg_ip_per_start"]
        except Exception:
            pass

        t1_feats = t1_features(
            home_ab, home_sp, away_bat, wx,
            home_pitcher_id=game["home_pitcher_id"],
            away_pitcher=away_sp,                  # for era_gap_t1
            home_last5_nrfi=home_last5,
            home_last10_nrfi=home_last10,
            away_top3c_obp=away_top3c_obp,
            away_top3c_slg=away_top3c_slg,         # power signal
            away_top3c_iso=away_top3c_iso,
            ump_rate=ump_rate,
            home_pvt_nrfi=home_pvt_nrfi,           # Phase F: familiarity
            home_avg_ip_per_start=home_avg_ip,     # Phase F: opener detection
            season=season,
            date_iso=target_iso,                   # T4.2: priors-pooled Statcast
        )
        b1_feats = b1_features(
            home_ab, away_sp, home_bat, wx,
            away_pitcher_id=game["away_pitcher_id"],
            home_pitcher=home_sp,                  # for era_gap_b1
            away_last5_nrfi=away_last5,
            away_last10_nrfi=away_last10,
            home_top3c_obp=home_top3c_obp,
            home_top3c_slg=home_top3c_slg,
            home_top3c_iso=home_top3c_iso,
            ump_rate=ump_rate,
            away_pvt_nrfi=away_pvt_nrfi,
            away_avg_ip_per_start=away_avg_ip,
            season=season,
            date_iso=target_iso,                   # T4.2: priors-pooled Statcast
        )
        # Compute each half's P(run) so we can persist lambda projections
        # for display (the screenshot-style "expected runs per half").
        m_t1, m_b1 = _load_lr_models()
        p_t1_run = _lr_predict_one(t1_feats, m_t1)
        p_b1_run = _lr_predict_one(b1_feats, m_b1)
        # T4.15: top-5 contributing features per half for the dashboard's
        # "Why this pick?" panel.  Cheap (~30 multiplies per game) and
        # gives the user transparency into what's driving each verdict.
        top_factors_t1 = _lr_feature_contributions(t1_feats, m_t1, _T1_EXPECTED_FEATURES, top_n=5) if m_t1 else []
        top_factors_b1 = _lr_feature_contributions(b1_feats, m_b1, _B1_EXPECTED_FEATURES, top_n=5) if m_b1 else []
        lr_nrfi_raw  = (1.0 - p_t1_run) * (1.0 - p_b1_run)
        # Convert each half's run-probability into expected runs (Poisson lambda)
        # so the dashboard can show projections in the same units the user is
        # used to:  P(>=1 run) = 1 - e^-lambda  =>  lambda = -ln(1 - P(>=1 run))
        lambda_t1 = -math.log(max(1e-9, 1.0 - p_t1_run))
        lambda_b1 = -math.log(max(1e-9, 1.0 - p_b1_run))
        lambda_lr_total = lambda_t1 + lambda_b1
        # T3.13: persist raw + calibrated separately so downstream can replay
        # alternate calibrators (e.g. Variant K in db/variants.py uses raw +
        # calibration_v3 to compare v3 vs v2 production calibrator).  Keeping
        # raw at predict-time avoids the lossy calibrator-inversion path.
        lr_nrfi = cal.predict(lr_nrfi_raw)
        nrfi_p, yrfi_p = lr_nrfi, 1.0 - lr_nrfi
        nrfi_p_raw = lr_nrfi_raw
        yrfi_p_raw = 1.0 - lr_nrfi_raw
        # T4.3: pass weather context into the classifier so the lambda
        # floor scales appropriately (hot/windy = higher floor; dome =
        # neutralized).  Reuse the values already computed above.
        wx_temp   = None if home_ab in DOMED_PARKS else wx.get("temp_c")
        wx_wind   = None if home_ab in DOMED_PARKS else wx.get("wind_kmh")
        wx_dome   = home_ab in DOMED_PARKS
        pick_side, pick_conf = classify_pick_lr(
            lr_nrfi, data_pts, lambda_lr_total,
            wx_temp_c=wx_temp, wx_wind_kmh=wx_wind, wx_is_dome=wx_dome,
        )

        # T2.53: track ALL applicable PASS reasons so a row that hits
        # multiple guards (e.g. lineup not posted AND a debut pitcher)
        # surfaces both, not just the most-terminal one.  Old behavior
        # silently overrode "LINEUP PENDING" with "NO DATA" when a
        # pitcher_q was 'avg', hiding the lineup-pending fact from
        # the operator.  Confirmed via 2026-05-03 BAL@NYY: lineup
        # was empty AND Trey Gibson was a debut (avg quality), but
        # the row showed only "PASS - No data" -- operator couldn't
        # tell the lineup also wasn't posted.
        pass_reasons: list[str] = []

        # Lineup-pending guard: lineup data drives top3c_obp / top3c_slg /
        # top3c_iso (the strongest offense features).  When lineups haven't
        # posted yet we substitute team-level full-season averages, which
        # produce false-confidence picks that flip when the real top-3
        # aggregate arrives a few hours later (e.g. DET @ ATL on 2026-04-30:
        # PASS - No edge -> STRONG NRFI on a 2.26pp probability shift).
        # Force PASS - LINEUP PENDING so the user knows the verdict is
        # tentative until the lineup is in.  Parallel to STARTER PENDING.
        # Don't downgrade a NO DATA verdict (data_pts==0) -- that's a
        # strictly more severe missing-data condition than LINEUP PENDING.
        if (pick_conf != "NO DATA"
                and (away_top3c_source != "lineup" or home_top3c_source != "lineup")):
            pick_side = "PASS"
            pass_reasons.append("LINEUP PENDING")

        # T2.24 -- Differentiate "starter not announced" from "starter
        # announced but limited MLB stats" (rookie debut, recent call-up,
        # mid-season trade with sparse history).  Both trigger
        # pitcher_q='avg' (the model is using LEAGUE_AVG_* placeholders),
        # but the user-facing label should accurately reflect the cause:
        #
        #   STARTER PENDING -- name is "TBD"/unannounced; MLB hasn't
        #     published a probable pitcher yet.  Will resolve at game time.
        #
        #   NO DATA -- name IS announced (real player + ID) but their
        #     MLB stats history is too thin for the model to draw a real
        #     prediction from.  Will NOT resolve unless the pitcher
        #     accumulates innings.  System will not bet either way.
        if away_sp_q == "avg" or home_sp_q == "avg":
            def _name_announced(name: str) -> bool:
                s = (name or "").strip().upper()
                return bool(s) and s not in ("TBD", "TBA", "UNDECIDED", "UNKNOWN")
            away_unannounced = away_sp_q == "avg" and not _name_announced(game.get("away_pitcher_name", ""))
            home_unannounced = home_sp_q == "avg" and not _name_announced(game.get("home_pitcher_name", ""))
            pick_side = "PASS"
            if away_unannounced or home_unannounced:
                # At least one avg-quality pitcher has no name -> truly STARTER PENDING.
                pass_reasons.append("STARTER PENDING")
            else:
                # All avg-quality pitchers have real names -> data is the issue.
                # Both verdicts are PASS, but NO DATA is the truthful label.
                pass_reasons.append("NO DATA")

        # T2.53: collapse the list into a primary conf for `pick_strength`
        # plus the full ordered list of reasons for label assembly.
        # `pick_strength` stays a single token (so existing dashboard /
        # filter / variant code that does `pick_strength == "LINEUP PENDING"`
        # keeps working).  `pass_reasons` flows through to tracker.log_picks
        # which composes the full compound label like "PASS - Lineup pending
        # + No data" so the operator sees every applicable guard.
        if pass_reasons:
            # Stable order: LINEUP PENDING first (transient -- resolves on
            # its own when the lineup posts).  STARTER PENDING / NO DATA
            # are permanent for the day, surfaced after the transient one.
            order = ["LINEUP PENDING", "STARTER PENDING", "NO DATA",
                     "LOW LAMBDA", "NO EDGE"]
            pass_reasons.sort(key=lambda r: order.index(r) if r in order else 99)
            # Primary token = first in the ordered list (most actionable)
            pick_conf = pass_reasons[0]

        results.append({
            "game_pk":       game["game_pk"],
            "game_number":   game["game_number"],
            "double_header": game["double_header"],
            "time":          format_game_time(game["game_date"],
                                                placeholder=bool(game.get("game_time_placeholder"))),
            "park_factor":   pf,
            "data_points":   data_pts,
            # pre-computed probabilities and pick (used by tracker and display)
            "nrfi_prob":     nrfi_p,
            "yrfi_prob":     yrfi_p,
            # T3.13: raw (uncalibrated) probs persisted so Variant K can
            # apply alternate calibrators post-hoc (e.g. v3 truepit calibrator)
            "nrfi_prob_raw": nrfi_p_raw,
            "yrfi_prob_raw": yrfi_p_raw,
            "over_1_5_prob": over15p,
            "under_1_5_prob":1.0 - over15p,
            # LR-derived expected runs per half-inning (for "Slate Projections"
            # display where 0.5 total = strong NRFI, 1.0 total = strong YRFI)
            "lambda_lr_t1":     lambda_t1,
            "lambda_lr_b1":     lambda_b1,
            "lambda_lr_total":  lambda_lr_total,
            # Weather inputs persisted so retro re-bets / calibration use the
            # exact values the live predictor saw at pick time.  Domed parks
            # use neutral defaults with is_dome=1 (model's "ignore weather"
            # switch).
            "wx_temp_c":     wx["temp_c"]   if home_ab not in DOMED_PARKS else "",
            "wx_wind_kmh":   wx["wind_kmh"] if home_ab not in DOMED_PARKS else "",
            "wx_humidity":   wx["humidity"] if home_ab not in DOMED_PARKS else "",
            "wx_is_dome":    1 if home_ab in DOMED_PARKS else 0,
            # Phase D features: pitcher last-5 NRFI rate, top-3 batters'
            # current-season OBP, home plate umpire's career NRFI rate
            "home_p_last5_pitcher_nrfi":  home_last5,
            "away_p_last5_pitcher_nrfi":  away_last5,
            # Phase F: pitcher last-10 NRFI rate (longer recency window)
            "home_p_last10_pitcher_nrfi": home_last10,
            "away_p_last10_pitcher_nrfi": away_last10,
            "home_top3c_obp":             home_top3c_obp,
            "away_top3c_obp":             away_top3c_obp,
            # Power signal: top-3 SLG + ISO (added 2026-04-29)
            "home_top3c_slg":             home_top3c_slg,
            "away_top3c_slg":             away_top3c_slg,
            "home_top3c_iso":             home_top3c_iso,
            "away_top3c_iso":             away_top3c_iso,
            # Data-source provenance so the LINEUP PENDING guard above and
            # downstream transparency know whether top3c was real or imputed
            # ("lineup" vs "team_fallback" vs "league_default").
            "home_top3c_source":          home_top3c_source,
            "away_top3c_source":          away_top3c_source,
            # Per-batter top-3 lineups (JSON-encoded list of dicts with
            # name, mlbId, bats hand, OBP, SLG, ISO, AB).  Empty array
            # when lineup hasn't posted yet.  The dashboard renders this
            # under each pitcher card as "Vs <opp> top of order" so the
            # user can see WHO the pitcher will face.
            "away_lineup_json":           json.dumps(away_lineup),
            "home_lineup_json":           json.dumps(home_lineup),
            # T4.15: top contributing features per half for "Why this pick?" panel.
            "top_factors_t1_json":        json.dumps(top_factors_t1),
            "top_factors_b1_json":        json.dumps(top_factors_b1),
            "home_plate_ump_id":          ump_id,
            "home_plate_ump_nrfi_rate":   ump_rate,
            # Phase E.3 Statcast features: xera + whiff_pct_rank per pitcher
            "home_xera":                  t1_feats[10],   # index 10 in T1 vector
            "home_whiff_pct_rank":        t1_feats[11],
            "away_xera":                  b1_feats[10],
            "away_whiff_pct_rank":        b1_feats[11],
            # Phase F: pitcher-vs-team familiarity + opener detection
            "home_pvt_nrfi_rate":         home_pvt_nrfi,
            "away_pvt_nrfi_rate":         away_pvt_nrfi,
            "home_avg_ip_per_start":      home_avg_ip,
            "away_avg_ip_per_start":      away_avg_ip,
            "pick_side":     pick_side,
            "pick_conf":     pick_conf,
            # T2.53: full ordered list of PASS reasons (empty for non-PASS
            # rows or single-reason PASS where pick_conf already says it).
            # Tracker.log_picks composes the compound label from this.
            "pass_reasons":  list(pass_reasons),
            "away": {
                "abbr":         away_ab,
                "pitcher_id":   game["away_pitcher_id"],
                "pitcher_name": away_sp["name"],
                "pitcher_q":    away_sp_q,
                "batting_q":    away_bat_q,
                "lambda":       away_lam,
                # pitcher model inputs (for CSV logging)
                "era":  away_sp["era"],
                "whip": away_sp["whip"],
                "fip":  away_sp["fip"],
                "bb9":  away_sp.get("bb9", LEAGUE_AVG_BB9),
                "hr9":  away_sp.get("hr9", LEAGUE_AVG_HR9),
                "k9":   away_sp.get("k9",  LEAGUE_AVG_K9),
                # batting model inputs
                "obp":  away_bat.get("obp", LEAGUE_AVG_OBP),
                "slg":  away_bat.get("slg", LEAGUE_AVG_SLG),
                "rpg":  away_bat["rpg"],
                # first-inning pitcher split (None if unavailable)
                "fi_era":  away_fi_sp["era"]  if away_fi_sp else None,
                "fi_whip": away_fi_sp["whip"] if away_fi_sp else None,
                "fi_ip":   away_fi_sp["ip"]   if away_fi_sp else None,
                # last-N first-inning history (informational)
                "last5":  away_l5,
                "last10": away_l10,
            },
            "home": {
                "abbr":         home_ab,
                "pitcher_id":   game["home_pitcher_id"],
                "pitcher_name": home_sp["name"],
                "pitcher_q":    home_sp_q,
                "batting_q":    home_bat_q,
                "lambda":       home_lam,
                # pitcher model inputs
                "era":  home_sp["era"],
                "whip": home_sp["whip"],
                "fip":  home_sp["fip"],
                "bb9":  home_sp.get("bb9", LEAGUE_AVG_BB9),
                "hr9":  home_sp.get("hr9", LEAGUE_AVG_HR9),
                "k9":   home_sp.get("k9",  LEAGUE_AVG_K9),
                # batting model inputs
                "obp":  home_bat.get("obp", LEAGUE_AVG_OBP),
                "slg":  home_bat.get("slg", LEAGUE_AVG_SLG),
                "rpg":  home_bat["rpg"],
                # first-inning pitcher split
                "fi_era":  home_fi_sp["era"]  if home_fi_sp else None,
                "fi_whip": home_fi_sp["whip"] if home_fi_sp else None,
                "fi_ip":   home_fi_sp["ip"]   if home_fi_sp else None,
                # last-N first-inning history (informational)
                "last5":  home_l5,
                "last10": home_l10,
            },
            "lambda_total": total_lam,
        })

    # Sort: best picks first (highest deviation from 50/50), then PASS.
    # Uses the model's calibrated YRFI/NRFI directly (set above), not the
    # legacy lambda-based prob_yrfi() function.
    def sort_key(g):
        yrfi = g["yrfi_prob"]
        nrfi = g["nrfi_prob"]
        edge = max(abs(yrfi - 0.50), abs(nrfi - 0.50))
        # Data quality bonus so games with real data surface first
        return (-(g["data_points"]), -edge)

    results.sort(key=sort_key)

    printed = 0
    for game in results:
        print_game_result(game, only_strong=only_strong)
        printed += 1

    if printed == 0:
        print("No games meet the filter for today.")
        return

    # Auto-log all picks to CSV (including PASS games)
    try:
        from tracker import log_picks
        written = log_picks(target_date, season, results)
        print(f"  Logged {written} picks -> data/picks_{season}.csv")
    except Exception as exc:
        print(f"  Warning: could not write picks log ({exc})")

    # T2.9: Sync classifier thresholds to data/thresholds.json so the
    # dashboard's tentative-classifier never drifts from Python.
    _write_thresholds_json()

    # Footer summary -- count actual pick assignments (LR or lambda).
    print("=" * 70)
    lams = [g["lambda_total"] for g in results]
    avg_lam   = sum(lams) / len(lams)
    nrfi_cnt  = sum(1 for g in results if g["pick_side"] == "NRFI")
    yrfi_cnt  = sum(1 for g in results if g["pick_side"] == "YRFI")
    pass_cnt  = len(results) - nrfi_cnt - yrfi_cnt
    hi_qual   = sum(1 for g in results if g["data_points"] >= 3)

    print(f"  Games today         : {len(results)}")
    print(f"  Avg combined lambda      : {avg_lam:.3f}")
    print(f"  NRFI picks          : {nrfi_cnt}  |  YRFI picks: {yrfi_cnt}  |  PASS: {pass_cnt}")
    print(f"  Blended inputs >=3/4 : {hi_qual} games")
    print(f"  Quality key         : [live]>=80IP/20G  [ltd]>=20IP/5G  [sm]>=1IP/1G  [avg]=league default")
    print()

    print_board(results, target_date)


# ---------------------------------------------------------------------------
# Ranking board
# ---------------------------------------------------------------------------

# Zone display: (pick_side, pick_conf) -> (color_marker_text, pick_description)
_BOARD_ZONE: dict[tuple[str, str], tuple[str, str, str]] = {
    # marker_text, colorama_attr, pick_desc
    ("NRFI", "STRONG"): ("[GREEN]", "GREEN",  "** STRONG NRFI"),
    ("NRFI", "LEAN"):   ("[GREEN]", "GREEN",  "*  LEAN NRFI  "),
    ("PASS", "NO EDGE"):("[GRAY] ", "WHITE",  "--  PASS       "),
    ("PASS", "NO DATA"):("[GRAY] ", "WHITE",  "--  NO DATA    "),
    ("PASS", "STARTER PENDING"): ("[GRAY] ", "WHITE", "--  STARTER PEND."),
    ("PASS", "LINEUP PENDING"):  ("[GRAY] ", "WHITE", "--  LINEUP PEND. "),
    ("PASS", "LOW LAMBDA"):      ("[GRAY] ", "WHITE", "--  LOW LAMBDA   "),
    ("YRFI", "LEAN"):   ("[RED]  ", "RED",    "*  LEAN YRFI  "),
    ("YRFI", "STRONG"): ("[RED]  ", "RED",    "** STRONG YRFI"),
}

# T3.21: _BOARD_CSV_FIELDS is intentionally NOT a subset of tracker.FIELDS
# -- the two CSVs have different schemas by design:
#   - board_<date>.csv = compact slate ranking (this list).  Drives the
#     dashboard's row table.  Has rank, derived %s, no pitcher details.
#   - picks_<season>.csv = full season ledger (tracker.FIELDS).  Drives
#     the GameDetail join.  Has full pitcher / offense / lineup / odds /
#     grade history per game.
# Trying to "canonicalize" them would either bloat the board CSV or
# strip the ledger.  Document the contract here so future contributors
# know they're meant to be separate.
_BOARD_CSV_FIELDS = [
    "rank", "away", "home", "lambda",
    "pick_side", "pick_strength", "pick_label",
    "nrfi_pct", "yrfi_pct",
    # Doubleheader disambiguation
    "game_pk", "game_number", "double_header", "game_time_et",
]


def _load_locked_picks(iso_date: str) -> dict[str, dict]:
    """
    Pull the locked (pre-game-frozen) pick label AND probabilities for
    every game on the given slate from data/picks_<season>.csv, so the
    board CSV writer can use them instead of the latest model verdict
    for games that have already started.

    Returns {game_pk: {pick_side, pick_strength, pick_label, nrfi_prob,
    yrfi_prob, combined_lambda}} for each locked row.  Empty dict on
    any error (the caller falls back to the live model output, which
    is the pre-fix behavior).

    Probabilities are returned as floats (or None when unparseable);
    pick fields are returned as the raw strings.

    Why include probabilities?  Without this, the board CSV would show
    the LATEST model's nrfi%/yrfi% next to the LOCKED pick label --
    looking inconsistent ("58.7% YRFI but pick=PASS, why?").  The user
    can't distinguish "high YRFI prob at lockout, demoted by lambda
    floor" from "model evolved post-lock to high YRFI".  Showing the
    locked probabilities makes the lockout snapshot self-consistent.
    """
    import csv as _csv
    from pathlib import Path
    season = iso_date[:4]
    p = Path(__file__).parent / "data" / f"picks_{season}.csv"
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    try:
        # Late import to avoid a hard dep when tracker isn't on the path.
        from tracker import _pick_is_locked
    except Exception:
        return {}

    def _f(s: str | None) -> float | None:
        if not s:
            return None
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    try:
        with open(p, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                if (row.get("date") or "").strip() != iso_date:
                    continue
                if not _pick_is_locked(row, iso_date):
                    continue
                pk = (row.get("game_pk") or "").strip()
                if not pk:
                    continue
                out[pk] = {
                    "pick_side":     row.get("pick_side")     or "",
                    "pick_strength": row.get("pick_strength") or "",
                    "pick_label":    row.get("pick_label")    or "",
                    "nrfi_prob":     _f(row.get("nrfi_prob")),
                    "yrfi_prob":     _f(row.get("yrfi_prob")),
                    "combined_lambda": _f(row.get("combined_lambda")),
                }
    except Exception:
        return {}
    return out


def print_board(results: list[dict], target_date: str) -> None:
    """
    Print a ranking board sorted by combined lambda (highest first) and
    save a CSV snapshot to data/boards/board_YYYY_MM_DD.csv.
    """
    import csv as _csv
    from pathlib import Path

    if not results:
        return

    board = sorted(results, key=lambda g: g["lambda_total"], reverse=True)

    # Normalize to ISO date for display and filename
    if "/" in target_date:
        m, d, y = target_date.split("/")
        iso_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    else:
        iso_date = target_date[:10]

    sep  = "=" * 68
    sep2 = "-" * 68

    print(f"\n{sep}")
    print(f"  RANKING BOARD  --  {iso_date}  (combined lambda, highest -> lowest)")
    print(sep)
    print(f"  {'#':<3}  {'Matchup':<11}  {'  lambda':>5}  {'Zone':<8}  {'Pick':<16}  {'NRFI%':>6}  {'YRFI%':>6}")
    print(sep2)

    # Lock-aware overrides: log_picks() preserves the original pre-game pick
    # for any row whose game has already started (so the bet snapshot stays
    # frozen).  Without the same override here, the board CSV would write
    # the LATEST model verdict for those games, then the dashboard would
    # show e.g. "PASS" next to a "WIN" badge graded against the locked
    # STRONG NRFI from picks_2026.csv -- visually it looks like a random
    # mis-grade.  Pull the locked snapshot back out of picks_2026 and use
    # those (pick_side, pick_strength) for any locked row instead.
    locked_picks = _load_locked_picks(iso_date)

    csv_rows = []
    for rank, g in enumerate(board, 1):
        gpk_str  = str(g.get("game_pk", ""))
        locked   = locked_picks.get(gpk_str)
        if locked:
            # Locked snapshot: pick label, probabilities, and combined-lambda
            # all come from picks_2026 so the row reads self-consistently
            # ("58.7% YRFI, PASS - LOW LAMBDA" -> "53.1% YRFI, PASS - NO EDGE"
            # depending on what the model actually said at lockout).
            side     = locked["pick_side"]
            conf     = locked["pick_strength"]
            n_p      = locked.get("nrfi_prob")
            y_p      = locked.get("yrfi_prob")
            lam_lk   = locked.get("combined_lambda")
            nrfi_pct = (n_p if n_p is not None else g["nrfi_prob"]) * 100
            yrfi_pct = (y_p if y_p is not None else g["yrfi_prob"]) * 100
            lam      = lam_lk if lam_lk is not None else g["lambda_total"]
        else:
            side     = g["pick_side"]
            conf     = g["pick_conf"]
            nrfi_pct = g["nrfi_prob"] * 100
            yrfi_pct = g["yrfi_prob"] * 100
            lam      = g["lambda_total"]
        away     = g["away"]["abbr"]
        home     = g["home"]["abbr"]
        matchup  = f"{away} @ {home}"

        marker, color_attr, desc = _BOARD_ZONE.get(
            (side, conf), ("[GRAY] ", "WHITE", f"   {conf} {side:<6}")
        )

        line = (
            f"  {rank:<3}  {matchup:<11}  {lam:>5.3f}  {marker}  {desc}  "
            f"{nrfi_pct:>5.1f}%  {yrfi_pct:>5.1f}%"
        )
        print(_c(color_attr, line) if HAS_COLOR else line)

        pick_label = f"{conf} {side}" if side != "PASS" else "PASS"
        csv_rows.append({
            "rank":          rank,
            "away":          away,
            "home":          home,
            "lambda":        f"{lam:.4f}",
            "pick_side":     side,
            "pick_strength": conf,
            "pick_label":    pick_label,
            "nrfi_pct":      f"{nrfi_pct:.1f}",
            "yrfi_pct":      f"{yrfi_pct:.1f}",
            # Doubleheader disambiguation
            "game_pk":       g.get("game_pk", ""),
            "game_number":   g.get("game_number", 1),
            "double_header": g.get("double_header", "N"),
            "game_time_et":  g.get("time", ""),
        })

    print(sep)

    # Distribution summary -- count by actual pick assignments (works for both
    # LR-based and lambda-threshold modes since both produce the same labels).
    zone_order = ["STRONG NRFI", "LEAN NRFI", "PASS", "LEAN YRFI", "STRONG YRFI"]
    counts = {name: 0 for name in zone_order}
    for g in board:
        side, conf = g["pick_side"], g["pick_conf"]
        if side == "PASS":
            counts["PASS"] += 1
        elif side in ("NRFI", "YRFI"):
            key = f"{conf} {side}"
            if key in counts:
                counts[key] += 1
    lam_vals = [g["lambda_total"] for g in board]
    dist_parts = "  ".join(f"{name}: {counts[name]}" for name in zone_order)
    print(f"  Distribution  ->  {dist_parts}")
    print(f"  lambda range       ->  {min(lam_vals):.3f} - {max(lam_vals):.3f}  "
          f"(avg {sum(lam_vals)/len(lam_vals):.3f})")

    # Save CSV board
    boards_dir = Path(__file__).parent / "data" / "boards"
    boards_dir.mkdir(parents=True, exist_ok=True)
    board_path = boards_dir / f"board_{iso_date.replace('-', '_')}.csv"
    with open(board_path, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=_BOARD_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"  Board saved -> {board_path}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MLB First Inning NRFI/YRFI Predictor with pick tracking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mlb_first_inning_predictor.py                          # predict today
  python mlb_first_inning_predictor.py --date 04/10/2025        # specific date
  python mlb_first_inning_predictor.py --strong                 # LEAN+ picks only
  python mlb_first_inning_predictor.py --grade                  # grade today's picks
  python mlb_first_inning_predictor.py --grade --date 04/05/2026
  python mlb_first_inning_predictor.py --summary
  python mlb_first_inning_predictor.py --summary --season 2025
  python mlb_first_inning_predictor.py --summary --last 50
  python mlb_first_inning_predictor.py --summary --date-from 04/01/2026 --date-to 04/30/2026
        """,
    )
    parser.add_argument(
        "--date",
        default=_today_et().strftime("%m/%d/%Y"),
        help="Game date MM/DD/YYYY (default: today in US Eastern Time)",
    )
    parser.add_argument("--strong",               action="store_true", help="Only show LEAN or STRONG picks")
    parser.add_argument("--debug",                action="store_true", help="Print raw IDs and blended stat values")
    parser.add_argument("--grade",                action="store_true", help="Grade logged picks against actual results")
    parser.add_argument("--summary",              action="store_true", help="Show performance summary from CSV")
    # Odds capture (re-enabled 2026-04-29 -- import real -110-style sportsbook
    # odds so P&L reflects actual prices instead of flat -110)
    parser.add_argument("--import-odds",          metavar="FILE",
                        help="Import market odds from a CSV (date,game_pk,away,home,nrfi_odds,yrfi_odds[,sportsbook])")
    parser.add_argument("--export-odds-template", action="store_true",
                        help="Write an odds-entry template for today's slate to data/odds/template_<date>.csv")
    parser.add_argument("--min-edge",             type=float, default=0.00, metavar="FRAC",
                        help="Minimum model edge to consider a bet (default 0.00 = bet whenever pick is STRONG/LEAN)")
    parser.add_argument("--units-lean",           type=float, default=0.5,  metavar="U", help="LEAN bet size (units)")
    parser.add_argument("--units-strong",         type=float, default=1.0,  metavar="U", help="STRONG bet size (units)")
    parser.add_argument("--output",               metavar="FILE", help=argparse.SUPPRESS)
    parser.add_argument("--audit-csv",            action="store_true", help="Audit picks CSV for data integrity issues")
    parser.add_argument("--repair-csv",           action="store_true", help="Auto-repair recoverable corrupted rows in picks CSV")
    parser.add_argument("--dry-run",              action="store_true", help="With --repair-csv: show what would change without writing")
    parser.add_argument("--season",               type=int,            help="Season year (default: derived from --date or current year)")
    parser.add_argument("--last",                 type=int, metavar="N",            help="Summary: most recent N graded bets")
    parser.add_argument("--date-from",            metavar="MM/DD/YYYY",             help="Summary: start date (inclusive)")
    parser.add_argument("--date-to",              metavar="MM/DD/YYYY",             help="Summary: end date (inclusive)")
    args = parser.parse_args()

    season = args.season or (int(args.date.split("/")[-1]) if "/" in args.date else _today_et().year)

    if args.grade:
        from tracker import grade_date
        grade_date(args.date, season)

    elif args.summary:
        from tracker import show_summary
        show_summary(
            season    = season,
            last_n    = args.last,
            date_from = args.date_from,
            date_to   = args.date_to,
        )

    elif args.import_odds:
        from tracker import import_odds
        import_odds(
            odds_path    = args.import_odds,
            season       = season,
            min_edge     = args.min_edge,
            units_lean   = args.units_lean,
            units_strong = args.units_strong,
        )

    elif args.export_odds_template:
        from tracker import export_odds_template
        export_odds_template(
            date_str    = args.date,
            season      = season,
            output_path = args.output,
        )

    elif args.audit_csv:
        from tracker import audit_csv
        audit_csv(season=season)

    elif args.repair_csv:
        from tracker import repair_csv
        repair_csv(season=season, dry_run=args.dry_run)

    else:
        run(args.date, only_strong=args.strong, debug=args.debug)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
MLB First Inning Run Predictor
================================
Predicts NRFI / YRFI probability (and over/under 1.5 runs) for today's games.

Methodology:
  - Fetches schedule via the raw MLB StatsAPI schedule endpoint with
    probablePitcher hydration, giving us both pitcher names AND IDs in
    one call (statsapi.schedule() strips the IDs – we bypass it).
  - Pulls per-pitcher season stats from /people/{id} with pitching
    hydration: ERA, WHIP, K, BB, HR, IP → computes FIP in-script.
  - Pulls per-team season batting stats from the correct
    /teams/{teamId}/stats endpoint (team_stats in the statsapi map).
  - Models each half-inning as Poisson(λ); sums both halves for total.
  - Classifies the game into NRFI/YRFI/PASS using thresholds calibrated
    against the real MLB baseline (YRFI ≈ 62% for an average game).
  - Tracks data quality per game; games with ≤1 real data point are
    shown as PASS / INSUFFICIENT DATA so they don't pollute the slate.

Usage:
  python mlb_first_inning_predictor.py              # today
  python mlb_first_inning_predictor.py --date 04/10/2025
  python mlb_first_inning_predictor.py --strong     # only LEAN+ picks
"""

import argparse
import math
import sys
from datetime import date, datetime
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
# League-wide constants  (2023-2024 MLB averages)
# ---------------------------------------------------------------------------
LEAGUE_FIRST_INNING_RUNS = 0.475   # avg runs per team per first half-inning
LEAGUE_AVG_ERA           = 4.20
LEAGUE_AVG_WHIP          = 1.25
LEAGUE_AVG_OPS           = 0.728
LEAGUE_AVG_RPG           = 4.45
FIP_CONSTANT             = 3.10

# Bayesian shrinkage: how many "prior" IP/games we add before trusting real data.
# At IP=PITCHER_PRIOR_IP  →  50% real / 50% league avg.
# At IP=3×PITCHER_PRIOR_IP →  75% real / 25% league avg.
PITCHER_PRIOR_IP  = 40.0
TEAM_PRIOR_GAMES  = 15.0

# League averages for expanded pitcher stats (2023-2024 MLB starters)
LEAGUE_AVG_K9   = 8.9
LEAGUE_AVG_BB9  = 3.0
LEAGUE_AVG_HR9  = 1.2

# League averages for expanded offense stats
LEAGUE_AVG_OBP  = 0.318
LEAGUE_AVG_SLG  = 0.414

# Pitcher multiplier weights — tuned for first-inning relevance
# BB/9 weighted highly: leadoff walks directly cause first-inning runs
# HR/9 weighted highly: solo HR = immediate YRFI
WHIP_WEIGHT  = 0.30
FIP_WEIGHT   = 0.25
BB9_WEIGHT   = 0.25
HR9_WEIGHT   = 0.20

# Offense multiplier weights — OBP highest: getting on base is the
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
# Pick thresholds — driven by combined first-inning lambda (projected runs)
#
#   λ ≤ 0.50            → STRONG NRFI
#   0.51 ≤ λ ≤ 0.69     → LEAN NRFI
#   0.70 ≤ λ ≤ 0.80     → PASS  (dead zone)
#   0.81 ≤ λ ≤ 0.99     → LEAN YRFI
#   λ ≥ 1.00            → STRONG YRFI
# ---------------------------------------------------------------------------
NRFI_STRONG_LAM = 0.50   # λ ≤ this → STRONG NRFI
NRFI_LEAN_LAM   = 0.69   # λ ≤ this → LEAN NRFI
PASS_LAM_MAX    = 0.80   # λ ≤ this → PASS (dead zone)
YRFI_LEAN_LAM   = 0.99   # λ ≤ this → LEAN YRFI  (λ > this → STRONG YRFI)

# ---------------------------------------------------------------------------
# Stable MLB team ID → abbreviation map (IDs do not change)
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
    """
    try:
        raw = statsapi.get("schedule", {
            "date": date_str,
            "sportId": 1,
            "hydrate": "probablePitcher",
        })
    except Exception as exc:
        sys.exit(f"Failed to fetch schedule: {exc}")

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
                # Pitcher IDs and names – None if not announced
                "away_pitcher_id":   away_probable.get("id"),
                "away_pitcher_name": away_probable.get("fullName", "TBD"),
                "home_pitcher_id":   home_probable.get("id"),
                "home_pitcher_name": home_probable.get("fullName", "TBD"),
            })
    return games

# ---------------------------------------------------------------------------
# Pitcher stat fetching
# ---------------------------------------------------------------------------

def _pitcher_quality_tag(ip: float) -> str:
    """Quality label based on innings pitched in the reference season."""
    if ip >= 80: return "live"
    if ip >= 20: return "ltd"
    if ip >= 1:  return "sm"
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
      1. Parse current-year splits → Bayesian-blend with prior year + league avg.
      2. If current year has < 1 IP (hasn't started yet), fall back to prior year.
      3. If nothing usable, return full league-average defaults.

    Returns (stats_dict, quality_tag).
    quality_tag: "live" (IP≥80) | "ltd" (IP≥20) | "sm" (IP≥1) | "avg" (no data).
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
        quality = _pitcher_quality_tag(curr["ip"])
    elif prev and prev["ip"] >= 20:
        # Pitcher hasn't thrown in the new season yet – use prior year
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
            if ip < 3.0:   # < ~10 first-inning appearances — too small
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
    quality_tag: "live" (games≥20) | "ltd" (games≥5) | "sm" (games≥1) | "avg".

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

    # K/9 acts as a dampener: elite strikeout rate → fewer baserunners/runs
    k9      = stats.get("k9", LEAGUE_AVG_K9)
    k9_adj  = 1.0 - (k9 - LEAGUE_AVG_K9) * 0.012   # ±1.2% per unit above/below avg
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
    blended in — weighted by how much first-inning sample the pitcher has.
    """
    # Blend season stats with first-inning specific stats if available
    if fi_pitcher and fi_pitcher.get("ip", 0) >= 3.0:
        fi_w = min(fi_pitcher["ip"] / 25.0, 0.40)   # cap FI weight at 40%
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
    Classify a game based on combined first-inning lambda (projected runs).

    Returns (side, confidence):
      side:       'NRFI' | 'YRFI' | 'PASS'
      confidence: 'STRONG' | 'LEAN' | 'NO EDGE' | 'NO DATA'
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

def format_game_time(iso_str: str) -> str:
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).strftime("%I:%M %p ET")
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
    print()

    # Projected runs
    print("  Projected 1st-inning runs:")
    print(f"    {away_abbr} bats (top 1st)   : {ap['lambda']:.3f}")
    print(f"    {home_abbr} bats (bot 1st)   : {hp['lambda']:.3f}")
    print(f"    Combined λ              : {lam:.3f}")
    print()

    # Probability table
    print("  Probabilities:")
    print(f"    NRFI  (0 runs)     {color_for_prob(nrfi,   prob_bar(nrfi))}")
    print(f"    YRFI  (1+ runs)    {color_for_prob(yrfi,   prob_bar(yrfi))}")
    print(f"    Over  1.5 runs     {color_for_prob(over15, prob_bar(over15))}")
    print(f"    Under 1.5 runs     {color_for_prob(under15,prob_bar(under15))}")
    print()

    # Pick line — format varies by side so the probability is never ambiguous
    # NRFI shows edge above the ~38% baseline; YRFI shows probability directly.
    BASELINE_NRFI = 0.38
    BASELINE_YRFI = 0.62
    conf_colors = {"STRONG": "GREEN", "LEAN": "YELLOW", "NO EDGE": "RED", "NO DATA": "RED"}

    if side == "NRFI":
        edge = round((nrfi - BASELINE_NRFI) * 100, 1)
        pick_line = (
            f"  >> {conf} NRFI  |  NRFI {nrfi*100:.1f}%  "
            f"(+{edge}pp above {BASELINE_NRFI*100:.0f}% avg)"
        )
    elif side == "YRFI":
        edge = round((yrfi - BASELINE_YRFI) * 100, 1)
        pick_line = f"  >> {conf} YRFI  |  YRFI {yrfi*100:.1f}%  (+{edge}pp above avg)"
    elif conf == "NO DATA":
        pick_line = f"  >> PASS  |  Insufficient data  (YRFI {yrfi*100:.1f}%)"
    else:
        pick_line = f"  >> PASS  |  No meaningful edge  (YRFI {yrfi*100:.1f}%)"

    print(_c(conf_colors.get(conf, ""), pick_line) if HAS_COLOR else pick_line)
    print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(target_date: str, only_strong: bool = False, debug: bool = False) -> None:
    season = int(target_date.split("/")[-1]) if "/" in target_date else date.today().year

    print(f"\nMLB First Inning Run Predictor  |  {target_date}  (season {season})")
    print("Fetching schedule and stats...\n")

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
                f"→ ERA {away_sp['era']:.2f} WHIP {away_sp['whip']:.2f} FIP {away_sp['fip']:.2f} "
                f"BB/9 {away_sp.get('bb9',3.0):.1f} HR/9 {away_sp.get('hr9',1.2):.1f} K/9 {away_sp.get('k9',8.9):.1f} "
                f"[{away_sp_q}]{fi_away}"
            )
            print(
                f"  home SP: {game['home_pitcher_name']} (id={game['home_pitcher_id']}) "
                f"→ ERA {home_sp['era']:.2f} WHIP {home_sp['whip']:.2f} FIP {home_sp['fip']:.2f} "
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

        nrfi_p  = prob_nrfi(total_lam)
        yrfi_p  = prob_yrfi(total_lam)
        over15p = prob_over_1_5(total_lam)
        pick_side, pick_conf = classify_pick(total_lam, data_pts)

        results.append({
            "game_pk":       game["game_pk"],
            "game_number":   game["game_number"],
            "double_header": game["double_header"],
            "time":          format_game_time(game["game_date"]),
            "park_factor":   pf,
            "data_points":   data_pts,
            # pre-computed probabilities and pick (used by tracker and display)
            "nrfi_prob":     nrfi_p,
            "yrfi_prob":     yrfi_p,
            "over_1_5_prob": over15p,
            "under_1_5_prob":1.0 - over15p,
            "pick_side":     pick_side,
            "pick_conf":     pick_conf,
            "away": {
                "abbr":         away_ab,
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
            },
            "home": {
                "abbr":         home_ab,
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
            },
            "lambda_total": total_lam,
        })

    # Sort: best picks first (highest deviation from 0.62 baseline), then PASS
    def sort_key(g):
        lam   = g["lambda_total"]
        yrfi  = prob_yrfi(lam)
        nrfi  = 1.0 - yrfi
        edge  = max(abs(yrfi - 0.62), abs(nrfi - 0.38))
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
        print(f"  Logged {written} picks → data/picks_{season}.csv")
    except Exception as exc:
        print(f"  Warning: could not write picks log ({exc})")

    # Footer summary
    print("=" * 70)
    lams = [g["lambda_total"] for g in results]
    avg_lam   = sum(lams) / len(lams)
    nrfi_cnt  = sum(1 for g in results if classify_pick(g["lambda_total"], g["data_points"])[0] == "NRFI")
    yrfi_cnt  = sum(1 for g in results if classify_pick(g["lambda_total"], g["data_points"])[0] == "YRFI")
    pass_cnt  = len(results) - nrfi_cnt - yrfi_cnt
    hi_qual   = sum(1 for g in results if g["data_points"] >= 3)

    print(f"  Games today         : {len(results)}")
    print(f"  Avg combined λ      : {avg_lam:.3f}")
    print(f"  NRFI picks          : {nrfi_cnt}  |  YRFI picks: {yrfi_cnt}  |  PASS: {pass_cnt}")
    print(f"  Blended inputs ≥3/4 : {hi_qual} games")
    print(f"  Quality key         : [live]≥80IP/20G  [ltd]≥20IP/5G  [sm]≥1IP/1G  [avg]=league default")
    print()

    print_board(results, target_date)


# ---------------------------------------------------------------------------
# Ranking board
# ---------------------------------------------------------------------------

# Zone display: (pick_side, pick_conf) → (color_marker_text, pick_description)
_BOARD_ZONE: dict[tuple[str, str], tuple[str, str, str]] = {
    # marker_text, colorama_attr, pick_desc
    ("NRFI", "STRONG"): ("[GREEN]", "GREEN",  "★★ STRONG NRFI"),
    ("NRFI", "LEAN"):   ("[GREEN]", "GREEN",  "★  LEAN NRFI  "),
    ("PASS", "NO EDGE"):("[GRAY] ", "WHITE",  "--  PASS       "),
    ("PASS", "NO DATA"):("[GRAY] ", "WHITE",  "--  NO DATA    "),
    ("YRFI", "LEAN"):   ("[RED]  ", "RED",    "★  LEAN YRFI  "),
    ("YRFI", "STRONG"): ("[RED]  ", "RED",    "★★ STRONG YRFI"),
}

_BOARD_CSV_FIELDS = [
    "rank", "away", "home", "lambda",
    "pick_side", "pick_strength", "pick_label",
    "nrfi_pct", "yrfi_pct",
]


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
    print(f"  RANKING BOARD  —  {iso_date}  (combined λ, highest → lowest)")
    print(sep)
    print(f"  {'#':<3}  {'Matchup':<11}  {'  λ':>5}  {'Zone':<8}  {'Pick':<16}  {'NRFI%':>6}  {'YRFI%':>6}")
    print(sep2)

    csv_rows = []
    for rank, g in enumerate(board, 1):
        lam      = g["lambda_total"]
        side     = g["pick_side"]
        conf     = g["pick_conf"]
        nrfi_pct = g["nrfi_prob"] * 100
        yrfi_pct = g["yrfi_prob"] * 100
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
        })

    print(sep)

    # Save CSV board
    boards_dir = Path(__file__).parent / "data" / "boards"
    boards_dir.mkdir(parents=True, exist_ok=True)
    board_path = boards_dir / f"board_{iso_date.replace('-', '_')}.csv"
    with open(board_path, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=_BOARD_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"  Board saved → {board_path}")
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
    # odds system temporarily disabled — flags kept so old shell scripts don't break
    parser.add_argument("--import-odds",          metavar="FILE",      help=argparse.SUPPRESS)
    parser.add_argument("--export-odds-template", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--min-edge",             type=float, default=0.00, metavar="FRAC", help=argparse.SUPPRESS)
    parser.add_argument("--units-lean",           type=float, default=0.5,  metavar="U",    help=argparse.SUPPRESS)
    parser.add_argument("--units-strong",         type=float, default=1.0,  metavar="U",    help=argparse.SUPPRESS)
    parser.add_argument("--output",               metavar="FILE",      help=argparse.SUPPRESS)
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
        # odds system temporarily disabled
        print("Odds functionality is currently disabled.")

    elif args.export_odds_template:
        # odds system temporarily disabled
        print("Odds functionality is currently disabled.")

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

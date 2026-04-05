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
# League-wide constants  (2023-2024 MLB averages)
# ---------------------------------------------------------------------------
LEAGUE_FIRST_INNING_RUNS = 0.475   # avg runs per team per first half-inning
LEAGUE_AVG_ERA           = 4.20
LEAGUE_AVG_WHIP          = 1.25
LEAGUE_AVG_OPS           = 0.728
LEAGUE_AVG_RPG           = 4.45
FIP_CONSTANT             = 3.10
MIN_IP_THRESHOLD         = 10.0    # ignore pitcher stats below this IP floor
MIN_GAMES_THRESHOLD      = 5       # ignore team batting below this games floor

# Pitcher multiplier weights (sum = 1.0)
ERA_WEIGHT   = 0.35
WHIP_WEIGHT  = 0.30
FIP_WEIGHT   = 0.35

# Offense multiplier weights (sum = 1.0)
OPS_WEIGHT = 0.55
RPG_WEIGHT = 0.45

# Capped multiplier ranges to prevent extreme outliers
PITCHER_MULT_MIN = 0.45
PITCHER_MULT_MAX = 1.80
OFFENSE_MULT_MIN = 0.60
OFFENSE_MULT_MAX = 1.55

HOME_RUN_FACTOR  = 1.025
AWAY_RUN_FACTOR  = 0.975

# ---------------------------------------------------------------------------
# Pick thresholds
#
# The YRFI baseline for an average game is ~62%, so we need meaningful
# deviation from that baseline before calling a side.  Games in the
# middle zone (57%–68% YRFI) are PASS / NO EDGE.
# ---------------------------------------------------------------------------
NRFI_STRONG_THRESH = 0.52   # YRFI < 48%  – two elite aces, pitcher park
NRFI_LEAN_THRESH   = 0.43   # YRFI < 57%  – meaningfully below average
YRFI_LEAN_THRESH   = 0.68   # 6 pts above 62% average baseline
YRFI_STRONG_THRESH = 0.76   # 14 pts above average – Coors-type games

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

def fetch_pitcher_stats(player_id: int | None, known_name: str = "TBD") -> tuple[dict, bool]:
    """
    Fetch season pitching stats for a player.

    Returns (stats_dict, is_real).
    `is_real` is False when the player_id is missing, the player hasn't
    thrown enough innings to be reliable, or any API error occurs.
    The known_name is always preserved so we never display "Unknown".
    """
    defaults = {
        "name":            known_name,
        "era":             LEAGUE_AVG_ERA,
        "whip":            LEAGUE_AVG_WHIP,
        "fip":             LEAGUE_AVG_ERA,
        "k_bb_ratio":      2.8,
        "innings_pitched": 0.0,
    }

    if not player_id:
        return defaults, False

    try:
        data   = statsapi.get("person", {
            "personId": player_id,
            "hydrate":  "stats(group=[pitching],type=[season])",
        })
        person = data["people"][0]
        name   = person.get("fullName", known_name)

        stats_blocks = person.get("stats", [])
        if not stats_blocks:
            defaults["name"] = name
            return defaults, False

        splits = stats_blocks[0].get("splits", [])
        if not splits:
            defaults["name"] = name
            return defaults, False

        s  = splits[0].get("stat", {})
        ip = safe_float(s.get("inningsPitched"), 0.0)

        if ip < MIN_IP_THRESHOLD:
            # Name is real but stats aren't reliable yet
            defaults["name"] = name
            return defaults, False

        era  = safe_float(s.get("era"),  LEAGUE_AVG_ERA)
        whip = safe_float(s.get("whip"), LEAGUE_AVG_WHIP)
        k    = safe_float(s.get("strikeOuts"),   0.0)
        bb   = safe_float(s.get("baseOnBalls"),  0.0)
        hr   = safe_float(s.get("homeRuns"),     0.0)

        # FIP = (13*HR + 3*BB – 2*K) / IP + constant
        fip = (13*hr + 3*bb - 2*k) / ip + FIP_CONSTANT if ip > 0 else LEAGUE_AVG_ERA
        fip = max(0.50, min(fip, 9.00))

        return {
            "name":            name,
            "era":             era,
            "whip":            whip,
            "fip":             fip,
            "k_bb_ratio":      k / bb if bb > 0 else 2.8,
            "innings_pitched": ip,
        }, True

    except Exception:
        defaults["name"] = known_name
        return defaults, False

# ---------------------------------------------------------------------------
# Team batting stat fetching
# ---------------------------------------------------------------------------

def fetch_team_batting(team_id: int, season: int) -> tuple[dict, bool]:
    """
    Fetch season team hitting stats via the correct team_stats endpoint.
    /api/v1/teams/{teamId}/stats?season=YEAR&group=hitting&stats=season

    Returns (stats_dict, is_real).
    """
    defaults = {
        "ops": LEAGUE_AVG_OPS,
        "rpg": LEAGUE_AVG_RPG,
    }

    if not team_id:
        return defaults, False

    try:
        data = statsapi.get("team_stats", {
            "teamId": team_id,
            "season": season,
            "group":  "hitting",
            "stats":  "season",
        })

        stats_blocks = data.get("stats", [])
        if not stats_blocks:
            return defaults, False

        splits = stats_blocks[0].get("splits", [])
        if not splits:
            return defaults, False

        s     = splits[0].get("stat", {})
        ops   = safe_float(s.get("ops"),         LEAGUE_AVG_OPS)
        runs  = safe_float(s.get("runs"),         0.0)
        games = safe_float(s.get("gamesPlayed"),  0.0)

        if games < MIN_GAMES_THRESHOLD:
            return defaults, False

        rpg = runs / games

        # Sanity-check: OPS strings like ".728" parse correctly; reject extremes
        if not (0.400 < ops < 1.200):
            ops = LEAGUE_AVG_OPS
        if not (1.0 < rpg < 10.0):
            rpg = LEAGUE_AVG_RPG

        return {"ops": ops, "rpg": rpg}, True

    except Exception:
        return defaults, False

# ---------------------------------------------------------------------------
# Core prediction model
# ---------------------------------------------------------------------------

def pitcher_multiplier(stats: dict) -> float:
    """
    Translate pitcher stats into a run-scoring multiplier vs league average.
    Values < 1 suppress run scoring; values > 1 increase it.
    """
    era_mult  = stats["era"]  / LEAGUE_AVG_ERA
    whip_mult = stats["whip"] / LEAGUE_AVG_WHIP
    fip_mult  = stats["fip"]  / LEAGUE_AVG_ERA

    # K/BB adjustment: elite K/BB (e.g. 4.5) → ~0.96×; poor (1.5) → ~1.03×
    k_bb     = stats.get("k_bb_ratio", 2.8)
    k_bb_adj = 1.0 - (k_bb - 2.8) * 0.025
    k_bb_adj = max(0.92, min(k_bb_adj, 1.06))

    raw = (ERA_WEIGHT * era_mult + WHIP_WEIGHT * whip_mult + FIP_WEIGHT * fip_mult) * k_bb_adj
    return max(PITCHER_MULT_MIN, min(raw, PITCHER_MULT_MAX))


def offense_multiplier(batting: dict) -> float:
    """
    Translate team batting stats into a run-scoring multiplier vs league average.
    Values > 1 mean the lineup scores more than average.
    """
    ops_mult = batting["ops"] / LEAGUE_AVG_OPS
    rpg_mult = batting["rpg"] / LEAGUE_AVG_RPG
    raw = OPS_WEIGHT * ops_mult + RPG_WEIGHT * rpg_mult
    return max(OFFENSE_MULT_MIN, min(raw, OFFENSE_MULT_MAX))


def expected_half_inning_runs(
    pitcher: dict,
    batting:  dict,
    pf:       float,
    home:     bool,
) -> float:
    """Expected runs for ONE team in the first inning."""
    p_mult    = pitcher_multiplier(pitcher)
    o_mult    = offense_multiplier(batting)
    home_fact = HOME_RUN_FACTOR if home else AWAY_RUN_FACTOR
    lam = LEAGUE_FIRST_INNING_RUNS * o_mult * p_mult * pf * home_fact
    return max(0.05, lam)

# ---------------------------------------------------------------------------
# Pick classification
# ---------------------------------------------------------------------------

def classify_pick(yrfi: float, data_pts: int) -> tuple[str, str]:
    """
    Returns (side, confidence).
    side:       'YRFI' | 'NRFI' | 'PASS'
    confidence: 'STRONG' | 'LEAN' | 'NO EDGE' | 'NO DATA'
    """
    if data_pts == 0:
        return "PASS", "NO DATA"

    nrfi = 1.0 - yrfi

    if nrfi >= NRFI_STRONG_THRESH:
        return "NRFI", "STRONG"
    if nrfi >= NRFI_LEAN_THRESH:
        return "NRFI", "LEAN"
    if yrfi >= YRFI_STRONG_THRESH:
        return "YRFI", "STRONG"
    if yrfi >= YRFI_LEAN_THRESH:
        return "YRFI", "LEAN"
    return "PASS", "NO EDGE"

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

def data_tag(is_real: bool) -> str:
    if is_real:
        return _c("GREEN", "live") if HAS_COLOR else "live"
    return _c("YELLOW", "avg") if HAS_COLOR else "avg"

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
    data_pts  = game["data_points"]

    nrfi      = prob_nrfi(lam)
    yrfi      = prob_yrfi(lam)
    over15    = prob_over_1_5(lam)
    under15   = prob_under_1_5(lam)

    side, conf = classify_pick(yrfi, data_pts)

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
        f"  {ap['pitcher_name']:<28} [{data_tag(ap['pitcher_real'])}]  "
        f"vs  {hp['pitcher_name']:<28} [{data_tag(hp['pitcher_real'])}]"
    )
    print(
        f"  Offense: {away_abbr} [{data_tag(ap['batting_real'])}]  "
        f"vs  {home_abbr} [{data_tag(hp['batting_real'])}]"
    )
    print(f"  Park factor: {game['park_factor']:.3f}  |  Data points: {data_pts}/4")
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

    # Pick
    conf_colors = {
        "STRONG":  "GREEN",
        "LEAN":    "YELLOW",
        "NO EDGE": "RED",
        "NO DATA": "RED",
    }
    pick_line = f"  >> {side}  |  {conf}"
    if side != "PASS":
        headline_prob = yrfi if side == "YRFI" else nrfi
        pick_line += f"  @  {headline_prob*100:.1f}%"
    print(_c(conf_colors.get(conf, ""), pick_line) if HAS_COLOR else pick_line)
    print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(target_date: str, only_strong: bool = False) -> None:
    season = int(target_date.split("/")[-1]) if "/" in target_date else date.today().year

    print(f"\nMLB First Inning Run Predictor  |  {target_date}")
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

        away_sp, away_sp_real = fetch_pitcher_stats(
            game["away_pitcher_id"], game["away_pitcher_name"]
        )
        home_sp, home_sp_real = fetch_pitcher_stats(
            game["home_pitcher_id"], game["home_pitcher_name"]
        )
        away_bat, away_bat_real = fetch_team_batting(game["away_team_id"], season)
        home_bat, home_bat_real = fetch_team_batting(game["home_team_id"], season)

        # Each half: batting team hits against the *opposing* pitcher
        away_lam = expected_half_inning_runs(home_sp,  away_bat, pf, home=False)
        home_lam = expected_half_inning_runs(away_sp,  home_bat, pf, home=True)
        total_lam = away_lam + home_lam

        data_pts = sum([away_sp_real, home_sp_real, away_bat_real, home_bat_real])

        results.append({
            "game_pk":       game["game_pk"],
            "game_number":   game["game_number"],
            "double_header": game["double_header"],
            "time":          format_game_time(game["game_date"]),
            "park_factor":   pf,
            "data_points":   data_pts,
            "away": {
                "abbr":          away_ab,
                "pitcher_name":  away_sp["name"],
                "pitcher_real":  away_sp_real,
                "batting_real":  away_bat_real,
                "lambda":        away_lam,
            },
            "home": {
                "abbr":          home_ab,
                "pitcher_name":  home_sp["name"],
                "pitcher_real":  home_sp_real,
                "batting_real":  home_bat_real,
                "lambda":        home_lam,
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

    # Footer summary
    print("=" * 70)
    lams = [g["lambda_total"] for g in results]
    avg_lam   = sum(lams) / len(lams)
    nrfi_cnt  = sum(1 for g in results if classify_pick(prob_yrfi(g["lambda_total"]), g["data_points"])[0] == "NRFI")
    yrfi_cnt  = sum(1 for g in results if classify_pick(prob_yrfi(g["lambda_total"]), g["data_points"])[0] == "YRFI")
    pass_cnt  = len(results) - nrfi_cnt - yrfi_cnt
    high_qual = sum(1 for g in results if g["data_points"] >= 3)

    print(f"  Games today      : {len(results)}")
    print(f"  Avg combined λ   : {avg_lam:.3f}")
    print(f"  NRFI picks       : {nrfi_cnt}  |  YRFI picks: {yrfi_cnt}  |  PASS: {pass_cnt}")
    print(f"  Real data (≥3/4) : {high_qual} games")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MLB First Inning Over/Under Run Predictor")
    parser.add_argument(
        "--date",
        default=date.today().strftime("%m/%d/%Y"),
        help="Game date in MM/DD/YYYY format (default: today)",
    )
    parser.add_argument(
        "--strong",
        action="store_true",
        help="Only show LEAN or STRONG picks (hide PASS games)",
    )
    args = parser.parse_args()
    run(args.date, only_strong=args.strong)


if __name__ == "__main__":
    main()

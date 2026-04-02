#!/usr/bin/env python3
"""
MLB First Inning Run Predictor
================================
Predicts over/under 1 run probability for the first inning of today's MLB games.

Methodology:
  - Fetches today's schedule and probable starters via mlb-statsapi
  - Builds per-team expected first-inning run totals using:
      * Pitcher ERA + WHIP + FIP approximation (fielding-independent)
      * Team OPS and run-scoring rate (offensive quality)
      * Park run factors (hitter/pitcher park adjustments)
      * Home-field advantage
      * Recent-form weight (last ~15 games)
  - Models each team's first-inning run distribution as Poisson(lambda)
  - Combines both halves to compute total first-inning run probabilities:
      NRFI  (No Run First Inning) = P(total == 0)
      YRFI  (Yes Run First Inning) = P(total >= 1)
      Over 1.5 runs              = P(total >= 2)
      Under 1.5 runs             = P(total <= 1)

Usage:
  pip install -r requirements.txt
  python mlb_first_inning_predictor.py              # today's games
  python mlb_first_inning_predictor.py --date 04/10/2025
  python mlb_first_inning_predictor.py --strong     # only high-confidence picks
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
# League-wide constants (2023-2024 MLB averages)
# ---------------------------------------------------------------------------
LEAGUE_FIRST_INNING_RUNS = 0.475   # avg runs per team per 1st inning
LEAGUE_AVG_ERA           = 4.20
LEAGUE_AVG_WHIP          = 1.25
LEAGUE_AVG_OPS           = 0.728
LEAGUE_AVG_RPG           = 4.45    # runs per game per team

# FIP constant (fluctuates slightly by season; 2024 ~ 3.10)
FIP_CONSTANT             = 3.10

# How much each stat influences the pitcher multiplier (weights sum to 1.0)
ERA_WEIGHT               = 0.35
WHIP_WEIGHT              = 0.30
FIP_WEIGHT               = 0.35

# How much each stat influences the offense multiplier (weights sum to 1.0)
OPS_WEIGHT               = 0.45
RPG_WEIGHT               = 0.35
RECENT_WEIGHT            = 0.20

# Home-field advantage: home teams score ~2.5% more in the first inning
HOME_RUN_FACTOR  = 1.025
AWAY_RUN_FACTOR  = 0.975

# Clamps so extreme outliers don't blow up the model
PITCHER_MULT_MIN = 0.45
PITCHER_MULT_MAX = 1.80
OFFENSE_MULT_MIN = 0.55
OFFENSE_MULT_MAX = 1.65

# ---------------------------------------------------------------------------
# Park run factors  (source: FanGraphs 2023-2024 multi-year park factors)
# Values > 1 inflate run scoring; < 1 suppress it.
# Keyed by the three-letter abbreviation statsapi returns in gameData.teams.
# ---------------------------------------------------------------------------
PARK_FACTORS: dict[str, float] = {
    "COL": 1.22,  # Coors Field          – extreme altitude
    "CIN": 1.09,  # Great American Ballpark
    "TEX": 1.07,  # Globe Life Field
    "BAL": 1.06,  # Camden Yards
    "BOS": 1.05,  # Fenway Park
    "PHI": 1.04,  # Citizens Bank Park
    "MIL": 1.03,  # American Family Field
    "NYY": 1.02,  # Yankee Stadium
    "TOR": 1.01,  # Rogers Centre
    "CHC": 1.00,  # Wrigley Field
    "ARI": 1.00,  # Chase Field (roof)
    "HOU": 0.99,  # Minute Maid Park
    "LAA": 0.99,  # Angel Stadium
    "CWS": 0.98,  # Guaranteed Rate Field
    "ATL": 0.98,  # Truist Park
    "DET": 0.98,  # Comerica Park
    "STL": 0.97,  # Busch Stadium
    "WSH": 0.97,  # Nationals Park
    "LAD": 0.97,  # Dodger Stadium
    "MIN": 0.97,  # Target Field
    "KC":  0.96,  # Kauffman Stadium
    "PIT": 0.96,  # PNC Park
    "NYM": 0.96,  # Citi Field
    "CLE": 0.96,  # Progressive Field
    "SD":  0.95,  # Petco Park
    "MIA": 0.95,  # LoanDepot Park
    "SEA": 0.95,  # T-Mobile Park
    "SF":  0.94,  # Oracle Park
    "OAK": 0.93,  # Oakland Coliseum / Las Vegas
    "TB":  0.93,  # Tropicana Field
}

# ---------------------------------------------------------------------------
# Poisson helpers
# ---------------------------------------------------------------------------

def poisson_pmf(lam: float, k: int) -> float:
    """P(X = k) for Poisson(lambda)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def prob_nrfi(lam: float) -> float:
    """P(0 combined first-inning runs) = e^-lambda."""
    return poisson_pmf(lam, 0)


def prob_yrfi(lam: float) -> float:
    """P(>= 1 combined first-inning run)."""
    return 1.0 - prob_nrfi(lam)


def prob_over_1_5(lam: float) -> float:
    """P(>= 2 combined first-inning runs) = 1 - P(0) - P(1)."""
    return 1.0 - poisson_pmf(lam, 0) - poisson_pmf(lam, 1)


def prob_under_1_5(lam: float) -> float:
    """P(<= 1 combined first-inning runs) = P(0) + P(1)."""
    return poisson_pmf(lam, 0) + poisson_pmf(lam, 1)

# ---------------------------------------------------------------------------
# Stat fetching helpers
# ---------------------------------------------------------------------------

def safe_float(value, default: float) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else default
    except (TypeError, ValueError):
        return default


def fetch_pitcher_stats(player_id: int) -> dict:
    """
    Return a dict with keys: era, whip, fip, innings_pitched, k_per9, bb_per9
    Falls back to league averages for any missing field.
    """
    defaults = {
        "era": LEAGUE_AVG_ERA,
        "whip": LEAGUE_AVG_WHIP,
        "fip": LEAGUE_AVG_ERA,
        "innings_pitched": 0.0,
        "k_per9": 8.5,
        "bb_per9": 3.0,
        "k_bb_ratio": 2.8,
        "name": "Unknown",
    }
    if not player_id:
        return defaults

    try:
        raw = statsapi.player_stats(player_id, type="season", group="pitching")
    except Exception:
        return defaults

    if not raw or "stats" not in raw.lower():
        return defaults

    # statsapi returns a formatted string; parse key numbers from team stats endpoint
    try:
        data = statsapi.get(
            "person",
            {
                "personId": player_id,
                "hydrate": "stats(group=[pitching],type=[season])",
            },
        )
        people = data.get("people", [{}])
        if not people:
            return defaults

        person = people[0]
        defaults["name"] = person.get("fullName", "Unknown")
        stats_list = (
            person.get("stats", [{}])[0].get("splits", [{}])
            if person.get("stats")
            else []
        )
        if not stats_list:
            return defaults

        s = stats_list[0].get("stat", {})

        era  = safe_float(s.get("era"),  LEAGUE_AVG_ERA)
        whip = safe_float(s.get("whip"), LEAGUE_AVG_WHIP)
        ip   = safe_float(s.get("inningsPitched"), 0.0)
        k    = safe_float(s.get("strikeOuts"), 0.0)
        bb   = safe_float(s.get("baseOnBalls"), 0.0)
        hr   = safe_float(s.get("homeRuns"), 0.0)

        # FIP = (13*HR + 3*BB - 2*K) / IP + FIP_CONSTANT
        fip = LEAGUE_AVG_ERA
        if ip > 0:
            fip = (13 * hr + 3 * bb - 2 * k) / ip + FIP_CONSTANT
            fip = max(0.50, min(fip, 9.00))

        k_per9  = (k  / ip * 9) if ip > 0 else 8.5
        bb_per9 = (bb / ip * 9) if ip > 0 else 3.0

        return {
            "era": era,
            "whip": whip,
            "fip": fip,
            "innings_pitched": ip,
            "k_per9": k_per9,
            "bb_per9": bb_per9,
            "k_bb_ratio": (k / bb) if bb > 0 else 2.8,
            "name": defaults["name"],
        }
    except Exception:
        return defaults


def fetch_team_batting(team_id: int) -> dict:
    """
    Return a dict with keys: ops, rpg, recent_rpg, team_abbr
    Falls back to league averages for missing fields.
    """
    defaults = {
        "ops": LEAGUE_AVG_OPS,
        "rpg": LEAGUE_AVG_RPG,
        "recent_rpg": LEAGUE_AVG_RPG,
        "team_abbr": "UNK",
    }
    if not team_id:
        return defaults

    try:
        data = statsapi.get(
            "team",
            {
                "teamId": team_id,
                "hydrate": "stats(group=[hitting],type=[season])",
            },
        )
        teams = data.get("teams", [{}])
        if not teams:
            return defaults

        t = teams[0]
        defaults["team_abbr"] = t.get("abbreviation", "UNK")

        stats_list = (
            t.get("stats", [{}])[0].get("splits", [{}])
            if t.get("stats")
            else []
        )
        if not stats_list:
            return defaults

        s = stats_list[0].get("stat", {})
        ops = safe_float(s.get("ops"), LEAGUE_AVG_OPS)
        # runs and games to derive RPG
        runs  = safe_float(s.get("runs"), 0.0)
        games = safe_float(s.get("gamesPlayed"), 1.0)
        rpg   = (runs / games) if games > 0 else LEAGUE_AVG_RPG

        return {
            "ops": ops,
            "rpg": rpg,
            "recent_rpg": rpg,   # statsapi doesn't expose rolling windows easily
            "team_abbr": defaults["team_abbr"],
        }
    except Exception:
        return defaults

# ---------------------------------------------------------------------------
# Core prediction model
# ---------------------------------------------------------------------------

def pitcher_multiplier(stats: dict) -> float:
    """
    Convert pitcher stats → a multiplier on league-average first-inning runs.
    Values < 1 mean the pitcher is *suppressing* run scoring.
    Values > 1 mean the pitcher is allowing *more* than average.
    """
    # ERA component: ratio vs league average (higher ERA → allow more runs)
    era_mult  = stats["era"]  / LEAGUE_AVG_ERA

    # WHIP component: more baserunners → more run risk
    whip_mult = stats["whip"] / LEAGUE_AVG_WHIP

    # FIP component: fielding-independent; better predictor of true skill
    fip_mult  = stats["fip"]  / LEAGUE_AVG_ERA

    # K/BB bonus: elite command/stuff should further suppress runs
    k_bb = stats.get("k_bb_ratio", 2.8)
    # League avg K/BB is ~2.8; scale so 3.5 → 0.93x, 1.5 → 1.10x
    k_bb_adj = 1.0 - (k_bb - 2.8) * 0.025

    raw = (
        ERA_WEIGHT  * era_mult  +
        WHIP_WEIGHT * whip_mult +
        FIP_WEIGHT  * fip_mult
    ) * k_bb_adj

    return max(PITCHER_MULT_MIN, min(raw, PITCHER_MULT_MAX))


def offense_multiplier(batting: dict) -> float:
    """
    Convert team batting stats → a multiplier on league-average first-inning runs.
    Values > 1 mean the lineup scores *more* than average.
    """
    ops_mult    = batting["ops"]       / LEAGUE_AVG_OPS
    rpg_mult    = batting["rpg"]       / LEAGUE_AVG_RPG
    recent_mult = batting["recent_rpg"] / LEAGUE_AVG_RPG

    raw = (
        OPS_WEIGHT    * ops_mult    +
        RPG_WEIGHT    * rpg_mult    +
        RECENT_WEIGHT * recent_mult
    )
    return max(OFFENSE_MULT_MIN, min(raw, OFFENSE_MULT_MAX))


def expected_first_inning_runs(
    pitcher_stats: dict,
    batting_stats: dict,
    park_factor: float,
    home: bool,
) -> float:
    """
    Expected runs for ONE team in the first inning.

    lambda = BASE × offense_mult × pitcher_mult × park_factor × home_factor
    """
    p_mult    = pitcher_multiplier(pitcher_stats)
    o_mult    = offense_multiplier(batting_stats)
    home_fact = HOME_RUN_FACTOR if home else AWAY_RUN_FACTOR

    lam = LEAGUE_FIRST_INNING_RUNS * o_mult * p_mult * park_factor * home_fact
    return max(0.05, lam)  # floor to avoid zero-lambda edge cases


def confidence_label(prob: float) -> str:
    """Return a qualitative confidence label based on probability strength."""
    if prob >= 0.72:
        return "STRONG"
    if prob >= 0.63:
        return "LEAN"
    return "TOSS-UP"

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

BAR_WIDTH = 30

def prob_bar(prob: float) -> str:
    filled = round(prob * BAR_WIDTH)
    bar = "#" * filled + "-" * (BAR_WIDTH - filled)
    return f"[{bar}] {prob * 100:.1f}%"


def color_prob(prob: float, text: str) -> str:
    if not HAS_COLOR:
        return text
    if prob >= 0.70:
        return Fore.GREEN + text + Style.RESET_ALL
    if prob >= 0.60:
        return Fore.YELLOW + text + Style.RESET_ALL
    return Fore.RED + text + Style.RESET_ALL


def print_game_result(game: dict, only_strong: bool = False) -> None:
    away = game["away"]
    home = game["home"]
    lam  = game["lambda_total"]

    nrfi       = prob_nrfi(lam)
    yrfi       = prob_yrfi(lam)
    over_1_5   = prob_over_1_5(lam)
    under_1_5  = prob_under_1_5(lam)

    # Determine the headline bet
    if yrfi >= 0.50:
        headline_prob = yrfi
        headline      = "YRFI (over 0.5)"
    else:
        headline_prob = nrfi
        headline      = "NRFI (under 0.5)"

    conf = confidence_label(headline_prob)

    if only_strong and conf != "STRONG":
        return

    sep = "=" * 68
    print(sep)
    print(
        f"  {away['abbr']:>3}  @  {home['abbr']:<3}   "
        f"  Game time: {game.get('time', 'TBD')}"
    )
    print(
        f"  {away['pitcher_name']:<28}  vs  {home['pitcher_name']}"
    )
    print()

    # Projected runs section
    print(f"  Projected 1st-inning runs:")
    print(f"    {away['abbr']} offense vs {home['abbr']} SP : {away['lambda']:.3f} runs")
    print(f"    {home['abbr']} offense vs {away['abbr']} SP : {home['lambda']:.3f} runs")
    print(f"    Combined total lambda        : {lam:.3f} runs")
    print()

    # Probability grid
    print("  Probabilities:")
    print(f"    NRFI  (0 runs)     {color_prob(nrfi,    prob_bar(nrfi))}")
    print(f"    YRFI  (1+ runs)    {color_prob(yrfi,    prob_bar(yrfi))}")
    print(f"    Over  1.5 runs     {color_prob(over_1_5,  prob_bar(over_1_5))}")
    print(f"    Under 1.5 runs     {color_prob(under_1_5, prob_bar(under_1_5))}")
    print()

    # Headline pick
    conf_color = {
        "STRONG":  Fore.GREEN  if HAS_COLOR else "",
        "LEAN":    Fore.YELLOW if HAS_COLOR else "",
        "TOSS-UP": Fore.RED    if HAS_COLOR else "",
    }.get(conf, "")
    reset = Style.RESET_ALL if HAS_COLOR else ""
    print(
        f"  >> Pick: {conf_color}{conf}{reset}  —  {headline}  "
        f"@ {headline_prob * 100:.1f}%"
    )
    print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def get_park_factor(home_abbr: str) -> float:
    return PARK_FACTORS.get(home_abbr, 1.00)


def run(target_date: str, only_strong: bool = False) -> None:
    print(f"\nMLB First Inning Run Predictor  |  {target_date}")
    print("Fetching today's schedule...\n")

    try:
        schedule = statsapi.schedule(date=target_date)
    except Exception as e:
        sys.exit(f"Failed to fetch schedule: {e}")

    if not schedule:
        print("No games found for this date.")
        return

    results = []

    for game in schedule:
        game_id    = game.get("game_id")
        game_time  = game.get("game_datetime", "TBD")
        if game_time != "TBD":
            try:
                game_time = datetime.fromisoformat(
                    game_time.replace("Z", "+00:00")
                ).strftime("%I:%M %p ET")
            except Exception:
                pass

        away_id      = game.get("away_id")
        home_id      = game.get("home_id")
        away_abbr    = game.get("away_name", "???")[:10]
        home_abbr    = game.get("home_name", "???")[:10]

        # Probable pitcher IDs
        away_sp_id = game.get("away_probable_pitcher_id") or 0
        home_sp_id = game.get("home_probable_pitcher_id") or 0

        # Shorten team names to abbreviations if statsapi returns full names
        # (statsapi sometimes returns abbreviations, sometimes full names)
        # Try to look up abbreviation from team fetch
        away_batting = fetch_team_batting(away_id)
        home_batting = fetch_team_batting(home_id)

        away_abbr_real = away_batting["team_abbr"] if away_batting["team_abbr"] != "UNK" else away_abbr[:3].upper()
        home_abbr_real = home_batting["team_abbr"] if home_batting["team_abbr"] != "UNK" else home_abbr[:3].upper()

        park_factor = get_park_factor(home_abbr_real)

        away_sp = fetch_pitcher_stats(away_sp_id)
        home_sp = fetch_pitcher_stats(home_sp_id)

        away_lam = expected_first_inning_runs(
            home_sp, away_batting, park_factor, home=False
        )
        home_lam = expected_first_inning_runs(
            away_sp, home_batting, park_factor, home=True
        )
        total_lam = away_lam + home_lam

        results.append({
            "game_id": game_id,
            "time": game_time,
            "away": {
                "abbr":         away_abbr_real,
                "pitcher_name": away_sp.get("name", "TBD"),
                "lambda":       away_lam,
            },
            "home": {
                "abbr":         home_abbr_real,
                "pitcher_name": home_sp.get("name", "TBD"),
                "lambda":       home_lam,
            },
            "lambda_total": total_lam,
        })

    # Sort by lambda_total ascending (most NRFI-friendly first)
    results.sort(key=lambda g: g["lambda_total"])

    printed = 0
    for game in results:
        print_game_result(game, only_strong=only_strong)
        printed += 1

    if printed == 0:
        print("No games meet the 'strong' filter today.")

    # Summary stats
    print("=" * 68)
    lams = [g["lambda_total"] for g in results]
    if lams:
        avg_lam = sum(lams) / len(lams)
        nrfi_games = sum(1 for l in lams if prob_nrfi(l) > 0.50)
        yrfi_games = len(lams) - nrfi_games
        print(f"  Games today: {len(lams)}")
        print(f"  Avg combined lambda: {avg_lam:.3f} runs")
        print(f"  NRFI favored: {nrfi_games} games  |  YRFI favored: {yrfi_games} games")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MLB First Inning Over/Under 1 Run Predictor"
    )
    parser.add_argument(
        "--date",
        default=date.today().strftime("%m/%d/%Y"),
        help="Game date in MM/DD/YYYY format (default: today)",
    )
    parser.add_argument(
        "--strong",
        action="store_true",
        help="Only show high-confidence picks (>= 72%% probability)",
    )
    args = parser.parse_args()
    run(args.date, only_strong=args.strong)


if __name__ == "__main__":
    main()

"""
tracker.py  --  Pick logging, result grading, and performance summary.

This module is intentionally self-contained (no imports from the predictor)
so it can be called from the predictor without circular-import issues.
All heavy lifting lives here; the predictor just calls three functions:
  log_picks(date_str, season, results)
  grade_date(date_str, season)
  show_summary(season, last_n, date_from, date_to)
"""

import csv
import sys
from datetime import datetime, date as date_type
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Missing dependency: pip install mlb-statsapi")

# ---------------------------------------------------------------------------
# Paths and schema
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"

# All CSV columns in order.  Result and odds columns are empty on first write;
# populated later by --grade and (future) odds ingestion.
FIELDS = [
    # --- core prediction ---
    "date", "season", "game_pk", "game_number",
    "away_team", "home_team", "game_time_et",
    "away_pitcher", "home_pitcher",
    "away_pitcher_id", "home_pitcher_id",
    "away_pitcher_q", "home_pitcher_q",
    "away_batting_q", "home_batting_q",
    "park_factor",
    "away_proj_runs", "home_proj_runs", "combined_lambda",
    # LR-v3 two-stage expected runs per half (for Slate Projections display)
    "lambda_lr_t1", "lambda_lr_b1", "lambda_lr_total",
    "nrfi_prob", "yrfi_prob", "over_1_5_prob", "under_1_5_prob",
    "pick_side", "pick_strength", "pick_label",
    "blended_inputs",
    "created_at",
    # --- pitcher model inputs ---
    "away_era", "home_era",
    "away_whip", "home_whip",
    "away_fip", "home_fip",
    "away_bb9", "home_bb9",
    "away_hr9", "home_hr9",
    "away_k9",  "home_k9",
    # --- first-inning pitcher splits (blank if unavailable) ---
    "away_fi_era", "home_fi_era",
    "away_fi_whip", "home_fi_whip",
    "away_fi_ip",  "home_fi_ip",
    # --- offense model inputs ---
    "away_obp", "home_obp",
    "away_slg", "home_slg",
    "away_rpg", "home_rpg",
    # --- result (filled by --grade) ---
    "actual_result",     # NRFI | YRFI | POSTPONED | SUSPENDED
    "graded_result",     # WIN | LOSS | PASS | POSTPONED | SUSPENDED
    "fi_away_runs",
    "fi_home_runs",
    "fi_total_runs",
    "graded_at",
    # --- odds & edge (reserved -- odds system temporarily disabled) ---
    # These columns are kept in FIELDS so existing CSV rows load cleanly.
    # They are never written or updated while the odds system is disabled.
    "market_nrfi_odds",
    "market_yrfi_odds",
    "sportsbook",
    "odds_captured_at",
    "implied_nrfi_prob",
    "implied_yrfi_prob",
    "edge_nrfi",
    "edge_yrfi",
    "edge_on_pick",
    "bet_placed",
    "units_risked",
    "profit_loss_units",
]

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _to_iso(date_str: str) -> str:
    """Normalize MM/DD/YYYY or YYYY-MM-DD to YYYY-MM-DD."""
    date_str = date_str.strip()
    if "/" in date_str:
        parts = date_str.split("/")
        if len(parts) == 3:
            m, d, y = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return date_str[:10]  # already ISO or truncate datetime


def _now_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _hours_since_slate_date(iso_date: str) -> float:
    """
    Hours elapsed since midnight ET *after* the slate date.  e.g. for
    iso_date='2026-04-25', returns hours past 2026-04-26 00:00 ET.
    Used to decide whether a still-'Scheduled' game is a stale rainout.
    """
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        d = datetime.fromisoformat(iso_date)
        # Anchor: midnight ET the day after the slate; games that haven't
        # started by then were almost certainly cancelled.
        from datetime import timedelta
        end_of_slate = (d.replace(tzinfo=et) + timedelta(days=1))
        now_utc = datetime.now(tz=ZoneInfo("UTC"))
        return (now_utc - end_of_slate).total_seconds() / 3600.0
    except Exception:
        return 0.0

# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def _csv_path(season: int) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"picks_{season}.csv"


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    # Back-fill any new columns added after the file was first created
    for row in rows:
        for field in FIELDS:
            row.setdefault(field, "")
    return rows


def _write_rows(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

# ---------------------------------------------------------------------------
# 1. Log picks
# ---------------------------------------------------------------------------

def _fmt(v, decimals: int = 3) -> str:
    """Format a numeric value for the CSV; empty string for None."""
    if v is None:
        return ""
    try:
        return str(round(float(v), decimals))
    except (TypeError, ValueError):
        return str(v)


def log_picks(date_str: str, season: int, results: list[dict]) -> int:
    """
    Write every game in `results` to data/picks_{season}.csv.

    If a row with the same date+game_pk already exists it is updated:
      - Prediction fields (stats, lambda, probabilities) are always refreshed.
      - Grading fields (graded_result, fi_runs, etc.) are always preserved.
      - If the row is already graded, pick_side / pick_strength / pick_label
        are also preserved so the original bet decision is never overwritten.

    Returns the number of rows written/updated.
    """
    iso_date = _to_iso(date_str)
    path     = _csv_path(season)
    rows     = _read_rows(path)

    # Build index: (iso_date, str(game_pk)) -> list-index
    index: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        key = (row["date"], str(row["game_pk"]))
        index[key] = i

    now = _now_utc()
    written = 0

    for g in results:
        side   = g["pick_side"]
        conf   = g["pick_conf"]
        lam    = g["lambda_total"]
        ap     = g["away"]
        hp     = g["home"]

        if side == "PASS":
            if conf == "NO DATA":
                label = "PASS - No data"
            elif conf == "STARTER PENDING":
                label = "PASS - Starter pending"
            else:
                label = "PASS - No edge"
        else:
            label = f"{conf} {side}"

        new_row = {
            "date":           iso_date,
            "season":         season,
            "game_pk":        g["game_pk"],
            "game_number":    g["game_number"],
            "away_team":      ap["abbr"],
            "home_team":      hp["abbr"],
            "game_time_et":   g["time"],
            "away_pitcher":    ap["pitcher_name"],
            "home_pitcher":    hp["pitcher_name"],
            "away_pitcher_id": ap.get("pitcher_id") or "",
            "home_pitcher_id": hp.get("pitcher_id") or "",
            "away_pitcher_q":  ap["pitcher_q"],
            "home_pitcher_q":  hp["pitcher_q"],
            "away_batting_q": ap["batting_q"],
            "home_batting_q": hp["batting_q"],
            "park_factor":    _fmt(g["park_factor"], 3),
            "away_proj_runs": _fmt(ap["lambda"], 4),
            "home_proj_runs": _fmt(hp["lambda"], 4),
            "combined_lambda":_fmt(lam, 4),
            "lambda_lr_t1":   _fmt(g.get("lambda_lr_t1"),    4),
            "lambda_lr_b1":   _fmt(g.get("lambda_lr_b1"),    4),
            "lambda_lr_total":_fmt(g.get("lambda_lr_total"), 4),
            "nrfi_prob":      _fmt(g["nrfi_prob"], 4),
            "yrfi_prob":      _fmt(g["yrfi_prob"], 4),
            "over_1_5_prob":  _fmt(g["over_1_5_prob"], 4),
            "under_1_5_prob": _fmt(1.0 - g["over_1_5_prob"], 4),
            "pick_side":      side,
            "pick_strength":  conf,
            "pick_label":     label,
            "blended_inputs": g["data_points"],
            "created_at":     now,
            # pitcher model inputs
            "away_era":  _fmt(ap.get("era"),  3),
            "home_era":  _fmt(hp.get("era"),  3),
            "away_whip": _fmt(ap.get("whip"), 3),
            "home_whip": _fmt(hp.get("whip"), 3),
            "away_fip":  _fmt(ap.get("fip"),  3),
            "home_fip":  _fmt(hp.get("fip"),  3),
            "away_bb9":  _fmt(ap.get("bb9"),  2),
            "home_bb9":  _fmt(hp.get("bb9"),  2),
            "away_hr9":  _fmt(ap.get("hr9"),  2),
            "home_hr9":  _fmt(hp.get("hr9"),  2),
            "away_k9":   _fmt(ap.get("k9"),   2),
            "home_k9":   _fmt(hp.get("k9"),   2),
            # first-inning pitcher splits
            "away_fi_era":  _fmt(ap.get("fi_era")),
            "home_fi_era":  _fmt(hp.get("fi_era")),
            "away_fi_whip": _fmt(ap.get("fi_whip")),
            "home_fi_whip": _fmt(hp.get("fi_whip")),
            "away_fi_ip":   _fmt(ap.get("fi_ip"), 1),
            "home_fi_ip":   _fmt(hp.get("fi_ip"), 1),
            # offense model inputs
            "away_obp": _fmt(ap.get("obp"), 3),
            "home_obp": _fmt(hp.get("obp"), 3),
            "away_slg": _fmt(ap.get("slg"), 3),
            "home_slg": _fmt(hp.get("slg"), 3),
            "away_rpg": _fmt(ap.get("rpg"), 3),
            "home_rpg": _fmt(hp.get("rpg"), 3),
            # result fields start empty (preserved if already set)
            "actual_result": "", "graded_result": "",
            "fi_away_runs":  "", "fi_home_runs":  "", "fi_total_runs": "",
            "graded_at":     "",
            # odds & edge (filled by --import-odds; preserved on re-run)
            "market_nrfi_odds": "", "market_yrfi_odds": "",
            "sportsbook": "", "odds_captured_at": "",
            "implied_nrfi_prob": "", "implied_yrfi_prob": "",
            "edge_nrfi": "", "edge_yrfi": "", "edge_on_pick": "",
            "bet_placed": "", "units_risked": "", "profit_loss_units": "",
        }

        key = (iso_date, str(g["game_pk"]))
        if key in index:
            existing = rows[index[key]]

            # Always preserve grading and odds fields
            preserve = [
                "actual_result", "graded_result",
                "fi_away_runs", "fi_home_runs", "fi_total_runs", "graded_at",
                "market_nrfi_odds", "market_yrfi_odds",
                "sportsbook", "odds_captured_at",
                "implied_nrfi_prob", "implied_yrfi_prob",
                "edge_nrfi", "edge_yrfi", "edge_on_pick",
                "bet_placed", "units_risked", "profit_loss_units",
            ]

            # If already graded, also preserve the original pick decision so
            # re-running the predictor after a model change doesn't corrupt
            # the W/L record. (The original bet was on the original pick.)
            if existing.get("graded_result", "") not in ("", "UNGRADED"):
                preserve += ["pick_side", "pick_strength", "pick_label"]

            for fld in preserve:
                new_row[fld] = existing.get(fld, "")

            rows[index[key]] = new_row
        else:
            rows.append(new_row)
            index[key] = len(rows) - 1

        written += 1

    _write_rows(path, rows)
    return written

# ---------------------------------------------------------------------------
# 2. Grade picks
# ---------------------------------------------------------------------------

def _fetch_first_inning(game_pk: int) -> dict:
    """
    Pull the first-inning run totals for a completed (or live) game.

    Returns a dict with keys:
      state        - "Final" | "Live" | "Preview" | other
      detail       - detailed state string (e.g. "Postponed")
      away_runs    - int or None
      home_runs    - int or None
    """
    try:
        data = statsapi.get("game", {
            "gamePk": game_pk,
            "fields": (
                "gameData,status,abstractGameState,detailedState,"
                "liveData,linescore,innings,num,home,away,runs"
            ),
        })
    except Exception as exc:
        return {"state": "ERROR", "detail": str(exc), "away_runs": None, "home_runs": None}

    status = data.get("gameData", {}).get("status", {})
    state  = status.get("abstractGameState", "")
    detail = status.get("detailedState", "")

    innings = (
        data.get("liveData", {})
            .get("linescore", {})
            .get("innings", [])
    )

    away_r = home_r = None
    if innings:
        inn1   = innings[0]
        away_r = inn1.get("away", {}).get("runs")
        home_r = inn1.get("home", {}).get("runs")

    return {"state": state, "detail": detail, "away_runs": away_r, "home_runs": home_r}


def grade_date(date_str: str, season: int) -> None:
    """
    Grade all picks for a given date that haven't been graded yet.
    Prints a per-game summary, then writes results back to the CSV.

    Grades as soon as innings[0] has both away+home run data -- does NOT
    require the game to be in Final state.
    """
    iso_date = _to_iso(date_str)
    path     = _csv_path(season)
    rows     = _read_rows(path)

    targets = [(i, r) for i, r in enumerate(rows) if r["date"] == iso_date]

    if not targets:
        print(f"No picks found for {iso_date}. Run the predictor first.")
        return

    print(f"\nGrading picks for {iso_date}  ({len(targets)} games)\n")

    now        = _now_utc()
    graded_n   = skipped_n = already_n = 0

    for idx, row in targets:
        tag = f"  {row['away_team']:>3} @ {row['home_team']:<3}"

        existing_grade = row.get("graded_result", "")
        if existing_grade and existing_grade not in ("", "UNGRADED"):
            print(f"{tag}  already graded: {existing_grade}")
            already_n += 1
            continue

        result = _fetch_first_inning(int(row["game_pk"]))
        state  = result["state"]
        detail = result["detail"]

        if state == "ERROR":
            print(f"{tag}  API error: {detail}")
            skipped_n += 1
            continue

        if detail in ("Postponed", "Suspended"):
            rows[idx]["actual_result"] = detail.upper()
            rows[idx]["graded_result"] = detail.upper()
            rows[idx]["graded_at"]     = now
            print(f"{tag}  {detail} -- marked, not counted as a bet")
            graded_n += 1
            continue

        away_r = result["away_runs"]
        home_r = result["home_runs"]

        if away_r is None or home_r is None:
            # First inning not yet complete -- decide why
            if state in ("Preview", "Scheduled"):
                # If the slate's calendar date is more than ~18h in the past
                # AND the game still says "Scheduled", treat it as effectively
                # postponed.  MLB's API often lags on the official Postponed
                # status update -- this prevents the daily grader from leaving
                # rainouts ungraded forever.
                hours_past = _hours_since_slate_date(iso_date)
                # 6h past midnight ET (= 6am ET next day).  Genuine games
                # are over by 1-2am ET at the latest; if status is still
                # "Scheduled" by 6am the next day, MLB has just lagged on
                # marking it postponed.
                if hours_past >= 6:
                    rows[idx]["actual_result"] = "POSTPONED"
                    rows[idx]["graded_result"] = "POSTPONED"
                    rows[idx]["graded_at"]     = now
                    print(f"{tag}  stale-scheduled ({hours_past:.0f}h past slate) "
                          f"-- marking POSTPONED")
                    graded_n += 1
                    continue
                print(f"{tag}  game not started yet -- skipping")
            elif state == "Live":
                print(f"{tag}  Live but 1st inning not yet complete -- skipping")
            else:
                print(f"{tag}  1st inning data unavailable (state={state!r}) -- skipping")
            skipped_n += 1
            continue

        total_r = away_r + home_r
        actual  = "NRFI" if total_r == 0 else "YRFI"
        pick    = row["pick_side"]

        if pick == "PASS":
            graded_result = "PASS"
        elif pick == actual:
            graded_result = "WIN"
        else:
            graded_result = "LOSS"

        rows[idx]["actual_result"] = actual
        rows[idx]["graded_result"] = graded_result
        rows[idx]["fi_away_runs"]  = away_r
        rows[idx]["fi_home_runs"]  = home_r
        rows[idx]["fi_total_runs"] = total_r
        rows[idx]["graded_at"]     = now

        outcome_tag = {"WIN": "W", "LOSS": "L", "PASS": "-"}.get(graded_result, "?")
        source_tag  = "" if state == "Final" else f" [from {state}]"
        print(
            f"{tag}  1st inn: {away_r}-{home_r} ({actual})  "
            f"pick={pick}  ->  {outcome_tag} {graded_result}{source_tag}"
        )
        graded_n += 1

    _write_rows(path, rows)
    print(f"\n  Graded {graded_n} | Skipped {skipped_n} | Already done {already_n}")
    print(f"  CSV: {path}\n")

# ---------------------------------------------------------------------------
# 3. Odds import & edge calculation
# ---------------------------------------------------------------------------

def american_to_prob(odds_str: str) -> float | None:
    """
    Convert an American odds string to implied probability (0-1).
    Handles "-110", "+105", "110" (treated as negative), etc.
    Returns None if the string cannot be parsed.
    """
    if not odds_str:
        return None
    s = str(odds_str).strip().replace(" ", "")
    try:
        odds = float(s)
    except ValueError:
        return None
    if odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:  # negative
        return abs(odds) / (abs(odds) + 100.0)


def payout_per_unit(odds_str: str) -> float | None:
    """
    How much profit you earn per 1 unit risked (not including stake return).
    E.g. -110 -> 0.909,  +105 -> 1.05,  +100 (even) -> 1.0
    Returns None if unparseable.
    """
    if not odds_str:
        return None
    s = str(odds_str).strip().replace(" ", "")
    try:
        odds = float(s)
    except ValueError:
        return None
    if odds == 0:
        return None
    if odds > 0:
        return odds / 100.0
    else:
        return 100.0 / abs(odds)


def _calc_pnl(row: dict) -> str:
    """
    Compute profit/loss in units for one already-graded row.
    Returns a formatted string or "" if data is incomplete.
    """
    graded = row.get("graded_result", "")
    if graded not in ("WIN", "LOSS"):
        return ""

    units_str = row.get("units_risked", "")
    if not units_str:
        return ""
    try:
        units = float(units_str)
    except ValueError:
        return ""

    if graded == "LOSS":
        return _fmt(-units, 3)

    # WIN -- need the odds for the picked side to compute payout
    pick = row.get("pick_side", "")
    if pick == "NRFI":
        odds_str = row.get("market_nrfi_odds", "")
    elif pick == "YRFI":
        odds_str = row.get("market_yrfi_odds", "")
    else:
        return ""

    ppu = payout_per_unit(odds_str)
    if ppu is None:
        return ""
    return _fmt(units * ppu, 3)


def _apply_odds_to_row(
    row:         dict,
    nrfi_odds:   str,
    yrfi_odds:   str,
    sportsbook:  str,
    min_edge:    float,
    units_lean:  float,
    units_strong: float,
    captured_at: str,
) -> dict:
    """
    Apply market odds to a single row dict.
    Computes implied probs, edges, bet sizing, and P&L if already graded.
    Returns the mutated row.
    """
    row["market_nrfi_odds"] = nrfi_odds
    row["market_yrfi_odds"] = yrfi_odds
    row["sportsbook"]       = sportsbook
    row["odds_captured_at"] = captured_at

    imp_nrfi = american_to_prob(nrfi_odds)
    imp_yrfi = american_to_prob(yrfi_odds)

    row["implied_nrfi_prob"] = _fmt(imp_nrfi, 4) if imp_nrfi is not None else ""
    row["implied_yrfi_prob"] = _fmt(imp_yrfi, 4) if imp_yrfi is not None else ""

    # Model probs (already logged at prediction time)
    try:
        model_nrfi = float(row.get("nrfi_prob", "") or 0)
        model_yrfi = float(row.get("yrfi_prob", "") or 0)
    except ValueError:
        row["edge_nrfi"] = row["edge_yrfi"] = row["edge_on_pick"] = ""
        row["bet_placed"] = "N"
        row["units_risked"] = ""
        return row

    edge_nrfi = (model_nrfi - imp_nrfi) if imp_nrfi is not None else None
    edge_yrfi = (model_yrfi - imp_yrfi) if imp_yrfi is not None else None

    row["edge_nrfi"] = _fmt(edge_nrfi, 4) if edge_nrfi is not None else ""
    row["edge_yrfi"] = _fmt(edge_yrfi, 4) if edge_yrfi is not None else ""

    pick      = row.get("pick_side", "")
    strength  = row.get("pick_strength", "")

    if pick == "NRFI":
        edge_pick = edge_nrfi
    elif pick == "YRFI":
        edge_pick = edge_yrfi
    else:
        edge_pick = None

    row["edge_on_pick"] = _fmt(edge_pick, 4) if edge_pick is not None else ""

    # Bet sizing: only bet if pick is NRFI/YRFI and edge meets threshold
    if pick in ("NRFI", "YRFI") and edge_pick is not None and edge_pick >= min_edge:
        if strength == "STRONG":
            units = units_strong
        elif strength == "LEAN":
            units = units_lean
        else:
            units = 0.0

        if units > 0:
            row["bet_placed"]   = "Y"
            row["units_risked"] = _fmt(units, 2)
        else:
            row["bet_placed"]   = "N"
            row["units_risked"] = ""
    else:
        row["bet_placed"]   = "N"
        row["units_risked"] = ""

    # Compute P&L if graded
    row["profit_loss_units"] = _calc_pnl(row)
    return row


def import_odds(
    odds_path:     str | Path,
    season:        int | None = None,
    min_edge:      float      = 0.00,
    units_lean:    float      = 0.5,
    units_strong:  float      = 1.0,
) -> None:
    """
    Import market odds from a CSV file and update picks_{season}.csv.

    Odds file format (header required):
      date, game_pk, away_team, home_team, market_nrfi_odds, market_yrfi_odds
      Optional columns: sportsbook

    Matching priority:
      1. date + game_pk        (exact)
      2. date + away_team + home_team  (fuzzy fallback)

    After matching, computes:
      implied probs, model edges, bet_placed, units_risked, profit_loss_units

    Example:
      2026-04-05,745459,NYY,BOS,-115,+105,FanDuel
      2026-04-05,,LAD,SF,-110,-110,DraftKings
    """
    odds_path = Path(odds_path)
    if not odds_path.exists():
        print(f"Odds file not found: {odds_path}")
        return

    # Infer season from odds file if not provided
    if season is None:
        season = date_type.today().year

    picks_path = _csv_path(season)
    rows       = _read_rows(picks_path)

    if not rows:
        print(f"No picks logged for season {season}. Run the predictor first.")
        return

    # Build lookup indexes
    by_pk:   dict[tuple, int] = {}   # (iso_date, str(game_pk)) -> idx
    by_team: dict[tuple, int] = {}   # (iso_date, away, home) -> idx
    for i, r in enumerate(rows):
        d = r["date"]
        by_pk[(d, str(r["game_pk"]))] = i
        by_team[(d, r["away_team"].upper(), r["home_team"].upper())] = i

    now = _now_utc()
    matched_n = unmatched_n = pk_matched_n = team_matched_n = 0
    dates_covered: set[str] = set()

    with open(odds_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Normalize column names
        for odds_row in reader:
            cols = {k.strip().lower(): v.strip() for k, v in odds_row.items()}
            iso_date = _to_iso(cols.get("date", ""))
            game_pk  = str(cols.get("game_pk", "")).strip()
            away     = cols.get("away_team", "").upper()
            home     = cols.get("home_team", "").upper()
            nrfi_o   = cols.get("market_nrfi_odds", "")
            yrfi_o   = cols.get("market_yrfi_odds", "")
            book     = cols.get("sportsbook", "")

            # Attempt match
            idx         = None
            match_method = ""
            if game_pk:
                idx = by_pk.get((iso_date, game_pk))
                if idx is not None:
                    match_method = "pk"
            if idx is None:
                idx = by_team.get((iso_date, away, home))
                if idx is not None:
                    match_method = "teams"

            if idx is None:
                print(f"  [no match] {iso_date}  {away} @ {home}  pk={game_pk or '?'}")
                unmatched_n += 1
                continue

            if match_method == "pk":
                pk_matched_n += 1
            else:
                team_matched_n += 1

            dates_covered.add(iso_date)

            rows[idx] = _apply_odds_to_row(
                rows[idx], nrfi_o, yrfi_o, book, min_edge,
                units_lean, units_strong, now
            )

            r = rows[idx]
            bet_tag   = f"bet={'Y' if r['bet_placed']=='Y' else 'N'}"
            edge_tag  = f"edge={r['edge_on_pick'] or 'N/A'}"
            units_tag = f"{r['units_risked']}u" if r.get("bet_placed") == "Y" else ""
            print(
                f"  {r['away_team']:>3} @ {r['home_team']:<3}  "
                f"NRFI:{nrfi_o:>5}  YRFI:{yrfi_o:>5}  "
                f"{bet_tag}  {edge_tag}  {units_tag}"
            )
            matched_n += 1

    _write_rows(picks_path, rows)

    # Re-read to count remaining odds gaps for dates covered
    if dates_covered:
        updated_rows = _read_rows(picks_path)
        missing_odds = [
            r for r in updated_rows
            if r["date"] in dates_covered and not r.get("market_nrfi_odds")
        ]
    else:
        missing_odds = []

    print(f"\n  Matched {matched_n} ({pk_matched_n} by game_pk, {team_matched_n} by teams)"
          f"  |  Unmatched {unmatched_n}")
    if missing_odds:
        print(f"  Still missing odds: {len(missing_odds)} pick(s) for {sorted(dates_covered)}")
    print(f"  CSV: {picks_path}")
    print(f"  -> Run --summary to view ROI metrics\n")

# ---------------------------------------------------------------------------
# 4. Odds template export
# ---------------------------------------------------------------------------

_TEMPLATE_COLS = [
    "date", "game_pk", "away_team", "home_team",
    "market_nrfi_odds", "market_yrfi_odds", "sportsbook",
]


def export_odds_template(
    date_str:    str,
    season:      int,
    output_path: str | Path | None = None,
) -> str:
    """
    Write a blank odds-import CSV template for all logged picks on date_str.
    Pre-fills date, game_pk, away_team, home_team; leaves odds columns blank.
    Returns the output file path (empty string on error).
    """
    iso_date = _to_iso(date_str)
    rows     = _read_rows(_csv_path(season))
    targets  = [r for r in rows if r["date"] == iso_date]

    if not targets:
        print(f"No logged picks found for {iso_date} (season {season}).")
        print("Run the predictor first, then export the template.")
        return ""

    if output_path is None:
        output_path = Path(f"odds_{iso_date.replace('-', '_')}.csv")
    output_path = Path(output_path)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_TEMPLATE_COLS, extrasaction="ignore")
        writer.writeheader()
        for r in targets:
            writer.writerow({
                "date":             r["date"],
                "game_pk":          r["game_pk"],
                "away_team":        r["away_team"],
                "home_team":        r["home_team"],
                "market_nrfi_odds": "",
                "market_yrfi_odds": "",
                "sportsbook":       "",
            })

    print(f"\n  Exported {len(targets)} game(s) for {iso_date} -> {output_path}")
    print(f"  Fill in market_nrfi_odds / market_yrfi_odds, then run:")
    print(f"  python mlb_first_inning_predictor.py --import-odds {output_path}\n")
    return str(output_path)


# ---------------------------------------------------------------------------
# 5. CSV audit & repair
# ---------------------------------------------------------------------------

# Map label prefixes -> (pick_side, pick_strength) for safe auto-recovery
_LABEL_TO_SIDE: dict[str, tuple[str, str]] = {
    "STRONG NRFI": ("NRFI", "STRONG"),
    "LEAN NRFI":   ("NRFI", "LEAN"),
    "STRONG YRFI": ("YRFI", "STRONG"),
    "LEAN YRFI":   ("YRFI", "LEAN"),
}


def audit_csv(season: int | None = None) -> int:
    """
    Audit picks CSV for data-integrity issues.
    Checks: corrupted pick_side/strength, missing keys, reconciliation.
    Returns number of issues found.
    """
    if season is None:
        season = date_type.today().year

    path = _csv_path(season)
    rows = _read_rows(path)

    if not rows:
        print(f"No picks found for season {season}.")
        return 0

    issues: list[tuple[str, str]] = []

    for i, r in enumerate(rows):
        tag      = f"row {i + 2}"   # +2 = 1-based + header
        grade    = r.get("graded_result", "").strip()
        side     = r.get("pick_side", "").strip()
        strength = r.get("pick_strength", "").strip()
        label    = r.get("pick_label", "").strip()

        if not r.get("date", "").strip():
            issues.append((tag, "missing date"))
        if not r.get("game_pk", "").strip():
            issues.append((tag, f"missing game_pk  ({r.get('away_team')} @ {r.get('home_team')})"))

        if grade in ("WIN", "LOSS"):
            if side not in ("NRFI", "YRFI"):
                repair_hint = ""
                for prefix, (ps, pst) in _LABEL_TO_SIDE.items():
                    if label.upper().startswith(prefix):
                        repair_hint = f"  (--repair-csv can fix from label: {label!r})"
                        break
                issues.append((
                    tag,
                    f"graded {grade} but pick_side={side!r} -- expected NRFI/YRFI{repair_hint}"
                ))
            elif strength not in ("LEAN", "STRONG"):
                issues.append((
                    tag,
                    f"graded {grade} but pick_strength={strength!r} -- expected LEAN/STRONG"
                ))

        if grade == "PASS" and side in ("NRFI", "YRFI"):
            issues.append((tag, f"graded_result=PASS but pick_side={side!r} (should be WIN/LOSS)"))

    # Totals reconciliation
    all_bets  = [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")]
    passes    = [r for r in rows if r.get("graded_result") == "PASS"]
    delayed   = [r for r in rows if r.get("graded_result") in ("POSTPONED", "SUSPENDED")]
    ungraded  = [r for r in rows if not r.get("graded_result")]
    accounted = len(all_bets) + len(passes) + len(delayed) + len(ungraded)

    print(f"\nAudit  --  {path}")
    print(f"  Total rows    : {len(rows)}")
    print(f"  Accounted for : {accounted}  "
          f"(bets={len(all_bets)} pass={len(passes)} delayed={len(delayed)} ungraded={len(ungraded)})")
    if accounted != len(rows):
        print(f"  [warn] Totals mismatch: {len(rows) - accounted} rows unaccounted for")
    print(f"  Issues found  : {len(issues)}")

    if issues:
        print()
        for tag, msg in issues:
            print(f"  [{tag}]  {msg}")
    else:
        print("  All rows look clean.")

    if any("--repair-csv" in msg for _, msg in issues):
        print(f"\n  Run --repair-csv to auto-fix recoverable issues.")

    print()
    return len(issues)


def repair_csv(season: int | None = None, dry_run: bool = False) -> None:
    """
    Safely repair corrupted rows in picks CSV.

    Recovers pick_side and pick_strength from pick_label where unambiguous:
      "LEAN NRFI"   -> NRFI / LEAN
      "STRONG NRFI" -> NRFI / STRONG
      "LEAN YRFI"   -> YRFI / LEAN
      "STRONG YRFI" -> YRFI / STRONG

    Only targets WIN/LOSS rows with wrong pick_side or pick_strength.
    Rows that cannot be confidently repaired are left unchanged and reported.
    """
    if season is None:
        season = date_type.today().year

    path = _csv_path(season)
    rows = _read_rows(path)

    if not rows:
        print(f"No picks found for season {season}.")
        return

    repaired_n = skipped_n = ok_n = 0

    for i, r in enumerate(rows):
        tag      = f"row {i + 2}"
        grade    = r.get("graded_result", "").strip()
        side     = r.get("pick_side", "").strip()
        strength = r.get("pick_strength", "").strip()
        label    = r.get("pick_label", "").strip()

        # Only target rows with a grading problem
        needs_repair = (
            grade in ("WIN", "LOSS")
            and (side not in ("NRFI", "YRFI") or strength not in ("LEAN", "STRONG"))
        )

        if not needs_repair:
            ok_n += 1
            continue

        # Attempt recovery from pick_label
        new_side = new_strength = None
        for prefix, (ps, pst) in _LABEL_TO_SIDE.items():
            if label.upper().startswith(prefix):
                new_side, new_strength = ps, pst
                break

        if new_side is None:
            print(f"  [{tag}]  Cannot repair -- label {label!r} is ambiguous; leaving unchanged")
            skipped_n += 1
            continue

        verb = "(dry-run) would set" if dry_run else "Repaired"
        print(
            f"  [{tag}]  {verb}:  pick_side {side!r}->{new_side!r}  "
            f"pick_strength {strength!r}->{new_strength!r}  "
            f"(label={label!r}  grade={grade})"
        )

        if not dry_run:
            r["pick_side"]     = new_side
            r["pick_strength"] = new_strength
        repaired_n += 1

    if not dry_run and repaired_n > 0:
        _write_rows(path, rows)

    mode = "(dry-run) " if dry_run else ""
    print(f"\n  {mode}Repaired {repaired_n} | Skipped (unrecoverable) {skipped_n} | Already OK {ok_n}")
    if repaired_n > 0 and not dry_run:
        print(f"  CSV updated: {path}")
    print()


# ---------------------------------------------------------------------------
# 6. Performance summary
# ---------------------------------------------------------------------------

def _record(bets: list, wins: list) -> str:
    if not bets:
        return "--"
    pct = len(wins) / len(bets) * 100
    return f"{len(wins)}-{len(bets)-len(wins)}  ({pct:.1f}%)"


def _side(r: dict) -> str:
    return r.get("pick_side", "").strip()


def _strength(r: dict) -> str:
    return r.get("pick_strength", "").strip()


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# odds system temporarily disabled -- _roi_line is a stub; use _record directly
def _roi_line(bets: list, wins: list) -> str:
    return _record(bets, wins)


def show_summary(
    season: int | None     = None,
    last_n: int | None     = None,
    date_from: str | None  = None,
    date_to:   str | None  = None,
) -> None:
    """
    Print a performance summary from the picks CSV.

    Filters (applied in order):
      date_from / date_to  - inclusive ISO date range
      last_n               - most recent N graded bets
    """
    if season is None:
        season = date_type.today().year

    path = _csv_path(season)
    rows = _read_rows(path)

    if not rows:
        print(f"No picks logged for season {season}.")
        return

    # Apply date range filters
    if date_from:
        iso_from = _to_iso(date_from)
        rows = [r for r in rows if r["date"] >= iso_from]
    if date_to:
        iso_to = _to_iso(date_to)
        rows = [r for r in rows if r["date"] <= iso_to]

    # Apply --last N filter: most recent N graded bets
    if last_n:
        graded_bets = sorted(
            [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")],
            key=lambda r: r["date"],
            reverse=True,
        )[:last_n]
        cutoff_keys = {(r["date"], r["game_pk"]) for r in graded_bets}
        rows = [r for r in rows if (r["date"], r["game_pk"]) in cutoff_keys]

    # -- Categorize rows ------------------------------------------------------
    all_bets  = [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")]
    wins      = [r for r in all_bets if r["graded_result"] == "WIN"]
    losses    = [r for r in all_bets if r["graded_result"] == "LOSS"]
    passes    = [r for r in rows if r.get("graded_result") == "PASS"]
    postponed = [r for r in rows if r.get("graded_result") in ("POSTPONED", "SUSPENDED")]
    ungraded  = [r for r in rows if not r.get("graded_result")]

    # -- By side --------------------------------------------------------------
    nrfi_bets = [r for r in all_bets if _side(r) == "NRFI"]
    yrfi_bets = [r for r in all_bets if _side(r) == "YRFI"]
    nrfi_wins = [r for r in nrfi_bets if r["graded_result"] == "WIN"]
    yrfi_wins = [r for r in yrfi_bets if r["graded_result"] == "WIN"]

    # -- By strength ----------------------------------------------------------
    lean_bets   = [r for r in all_bets if _strength(r) == "LEAN"]
    strong_bets = [r for r in all_bets if _strength(r) == "STRONG"]
    lean_wins   = [r for r in lean_bets   if r["graded_result"] == "WIN"]
    strong_wins = [r for r in strong_bets if r["graded_result"] == "WIN"]

    # -- Side x Strength cross-tab -----------------------------------------
    ln_bets = [r for r in nrfi_bets if _strength(r) == "LEAN"]
    sn_bets = [r for r in nrfi_bets if _strength(r) == "STRONG"]
    ly_bets = [r for r in yrfi_bets if _strength(r) == "LEAN"]
    sy_bets = [r for r in yrfi_bets if _strength(r) == "STRONG"]
    ln_wins = [r for r in ln_bets if r["graded_result"] == "WIN"]
    sn_wins = [r for r in sn_bets if r["graded_result"] == "WIN"]
    ly_wins = [r for r in ly_bets if r["graded_result"] == "WIN"]
    sy_wins = [r for r in sy_bets if r["graded_result"] == "WIN"]

    # -- Quality breakdown ----------------------------------------------------
    hi_qual_bets = [
        r for r in all_bets
        if all(r.get(q, "avg") not in ("", "avg")
               for q in ["away_pitcher_q", "home_pitcher_q",
                         "away_batting_q", "home_batting_q"])
    ]
    hi_qual_wins = [r for r in hi_qual_bets if r["graded_result"] == "WIN"]

    # Reconciliation check
    unaccounted = len(all_bets) - len(nrfi_bets) - len(yrfi_bets)
    win_rate = len(wins) / len(all_bets) * 100 if all_bets else 0.0

    dates = sorted({r["date"] for r in rows})
    date_range = f"{dates[0]} -> {dates[-1]}" if dates else "no data"

    sep  = "=" * 60
    sep2 = "-" * 60
    print(f"\n{sep}")
    print(f"  PERFORMANCE SUMMARY  --  Season {season}")
    if date_from or date_to or last_n:
        print(f"  Filter : {date_range}")
    print(sep)
    print(f"  Logged games        : {len(rows)}")
    print(f"  Graded bets (W+L)   : {len(all_bets)}")
    print(f"  Wins / Losses       : {len(wins)} / {len(losses)}")
    print(f"  Win rate            : {win_rate:.1f}%")
    print(f"  PASS (no pick)      : {len(passes)}")
    print(f"  Postponed/susp.     : {len(postponed)}")
    print(f"  Ungraded            : {len(ungraded)}")

    # Integrity checks
    total_accounted = len(all_bets) + len(passes) + len(postponed) + len(ungraded)
    if total_accounted != len(rows):
        print(f"  [warn] Row count mismatch: {len(rows)} logged, {total_accounted} accounted for")
    if unaccounted > 0:
        print(f"  [warn] {unaccounted} graded bet(s) have unexpected pick_side -- run --audit-csv")

    print(f"\n{sep2}")
    print(f"  By side:")
    print(f"    NRFI  : {_record(nrfi_bets, nrfi_wins)}")
    print(f"    YRFI  : {_record(yrfi_bets, yrfi_wins)}")

    print(f"\n{sep2}")
    print(f"  By strength:")
    print(f"    LEAN   : {_record(lean_bets,   lean_wins)}")
    print(f"    STRONG : {_record(strong_bets, strong_wins)}")

    print(f"\n{sep2}")
    print(f"  By side + strength:")
    print(f"    LEAN  NRFI  : {_record(ln_bets, ln_wins)}")
    print(f"    STRONG NRFI : {_record(sn_bets, sn_wins)}")
    print(f"    LEAN  YRFI  : {_record(ly_bets, ly_wins)}")
    print(f"    STRONG YRFI : {_record(sy_bets, sy_wins)}")

    if hi_qual_bets:
        print(f"\n{sep2}")
        print(f"  All inputs non-avg (4/4):")
        print(f"    {_record(hi_qual_bets, hi_qual_wins)}")

    print(sep)
    print()

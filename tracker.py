"""
tracker.py  —  Pick logging, result grading, and performance summary.

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
    # prediction
    "date", "season", "game_pk", "game_number",
    "away_team", "home_team", "game_time_et",
    "away_pitcher", "home_pitcher",
    "away_pitcher_q", "home_pitcher_q",
    "away_batting_q", "home_batting_q",
    "park_factor",
    "away_proj_runs", "home_proj_runs", "combined_lambda",
    "nrfi_prob", "yrfi_prob", "over_1_5_prob", "under_1_5_prob",
    "pick_side", "pick_strength", "pick_label",
    "blended_inputs",
    "created_at",
    # result (filled by --grade)
    "actual_result",     # NRFI | YRFI | POSTPONED | SUSPENDED
    "graded_result",     # WIN | LOSS | PASS | POSTPONED | SUSPENDED
    "fi_away_runs",
    "fi_home_runs",
    "fi_total_runs",
    "graded_at",
    # odds / ROI placeholders (for future use)
    "market_nrfi_odds",
    "market_yrfi_odds",
    "implied_nrfi_prob",
    "implied_yrfi_prob",
    "model_edge_nrfi",
    "model_edge_yrfi",
    "units",
    "profit_loss",
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

def log_picks(date_str: str, season: int, results: list[dict]) -> int:
    """
    Write every game in `results` to data/picks_{season}.csv.

    If a row with the same date+game_pk already exists it is updated
    (prediction fields overwritten, grading fields preserved).

    Returns the number of rows written/updated.
    """
    iso_date = _to_iso(date_str)
    path     = _csv_path(season)
    rows     = _read_rows(path)

    # Build index: (iso_date, str(game_pk)) → list-index
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

        if side == "PASS":
            label = f"PASS - {'No data' if conf == 'NO DATA' else 'No edge'}"
        else:
            label = f"{conf} {side}"

        new_row = {
            "date":          iso_date,
            "season":        season,
            "game_pk":       g["game_pk"],
            "game_number":   g["game_number"],
            "away_team":     g["away"]["abbr"],
            "home_team":     g["home"]["abbr"],
            "game_time_et":  g["time"],
            "away_pitcher":  g["away"]["pitcher_name"],
            "home_pitcher":  g["home"]["pitcher_name"],
            "away_pitcher_q":g["away"]["pitcher_q"],
            "home_pitcher_q":g["home"]["pitcher_q"],
            "away_batting_q":g["away"]["batting_q"],
            "home_batting_q":g["home"]["batting_q"],
            "park_factor":   round(g["park_factor"], 3),
            "away_proj_runs":round(g["away"]["lambda"], 4),
            "home_proj_runs":round(g["home"]["lambda"], 4),
            "combined_lambda":round(lam, 4),
            "nrfi_prob":     round(g["nrfi_prob"], 4),
            "yrfi_prob":     round(g["yrfi_prob"], 4),
            "over_1_5_prob": round(g["over_1_5_prob"], 4),
            "under_1_5_prob":round(g["under_1_5_prob"], 4),
            "pick_side":     side,
            "pick_strength": conf,
            "pick_label":    label,
            "blended_inputs":g["data_points"],
            "created_at":    now,
            # result fields start empty
            "actual_result": "", "graded_result": "",
            "fi_away_runs":  "", "fi_home_runs":  "", "fi_total_runs": "",
            "graded_at":     "",
            # odds placeholders
            "market_nrfi_odds": "", "market_yrfi_odds": "",
            "implied_nrfi_prob": "", "implied_yrfi_prob": "",
            "model_edge_nrfi":   "", "model_edge_yrfi":   "",
            "units": "", "profit_loss": "",
        }

        key = (iso_date, str(g["game_pk"]))
        if key in index:
            existing = rows[index[key]]
            # Preserve grading and odds fields when re-running the predictor
            for fld in ["actual_result", "graded_result",
                        "fi_away_runs", "fi_home_runs", "fi_total_runs", "graded_at",
                        "market_nrfi_odds", "market_yrfi_odds",
                        "implied_nrfi_prob", "implied_yrfi_prob",
                        "model_edge_nrfi", "model_edge_yrfi",
                        "units", "profit_loss"]:
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
    Pull the first-inning run totals for a completed game.

    Uses the live feed endpoint filtered to just linescore/innings data
    (same source as statsapi.linescore(), but lower overhead).

    Returns a dict with keys:
      state        – "Final" | "Live" | "Preview" | other
      detail       – detailed state string (e.g. "Postponed")
      away_runs    – int or None
      home_runs    – int or None
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
            print(f"{tag}  {detail} — marked, not counted as a bet")
            graded_n += 1
            continue

        if state != "Final":
            print(f"{tag}  not final (state={state!r}) — skipping")
            skipped_n += 1
            continue

        away_r = result["away_runs"]
        home_r = result["home_runs"]

        if away_r is None or home_r is None:
            print(f"{tag}  inning-1 data missing — skipping")
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

        outcome_tag = {"WIN": "✓", "LOSS": "✗", "PASS": "-"}.get(graded_result, "?")
        print(
            f"{tag}  1st inn: {away_r}-{home_r} ({actual})  "
            f"pick={pick}  →  {outcome_tag} {graded_result}"
        )
        graded_n += 1

    _write_rows(path, rows)
    print(f"\n  Graded {graded_n} | Skipped {skipped_n} | Already done {already_n}")
    print(f"  CSV: {path}\n")

# ---------------------------------------------------------------------------
# 3. Performance summary
# ---------------------------------------------------------------------------

def _record(bets: list, wins: list) -> str:
    if not bets:
        return "—"
    pct = len(wins) / len(bets) * 100
    return f"{len(wins)}-{len(bets)-len(wins)}  ({pct:.1f}%)"


def show_summary(
    season: int | None     = None,
    last_n: int | None     = None,
    date_from: str | None  = None,
    date_to:   str | None  = None,
) -> None:
    """
    Print a performance summary from the picks CSV.

    Filters (applied in order):
      date_from / date_to  – inclusive ISO date range
      last_n               – most recent N graded bets
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

    # Categorize rows
    all_bets   = [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")]
    wins       = [r for r in all_bets if r["graded_result"] == "WIN"]
    losses     = [r for r in all_bets if r["graded_result"] == "LOSS"]
    passes     = [r for r in rows if r.get("graded_result") == "PASS"]
    postponed  = [r for r in rows if r.get("graded_result") in ("POSTPONED", "SUSPENDED")]
    ungraded   = [r for r in rows if not r.get("graded_result")]

    nrfi_bets  = [r for r in all_bets if r["pick_side"] == "NRFI"]
    yrfi_bets  = [r for r in all_bets if r["pick_side"] == "YRFI"]
    nrfi_wins  = [r for r in nrfi_bets if r["graded_result"] == "WIN"]
    yrfi_wins  = [r for r in yrfi_bets if r["graded_result"] == "WIN"]

    lean_bets   = [r for r in all_bets if r["pick_strength"] == "LEAN"]
    strong_bets = [r for r in all_bets if r["pick_strength"] == "STRONG"]
    lean_wins   = [r for r in lean_bets   if r["graded_result"] == "WIN"]
    strong_wins = [r for r in strong_bets if r["graded_result"] == "WIN"]

    # Quality-bucket breakdown
    live_bets = [r for r in all_bets
                 if all(r.get(q, "") == "live"
                        for q in ["away_pitcher_q", "home_pitcher_q",
                                  "away_batting_q", "home_batting_q"])]
    live_wins = [r for r in live_bets if r["graded_result"] == "WIN"]

    win_rate = len(wins) / len(all_bets) * 100 if all_bets else 0.0

    # Date range string for header
    dates = sorted({r["date"] for r in rows})
    date_range = f"{dates[0]} → {dates[-1]}" if dates else "no data"

    sep = "=" * 52
    print(f"\n{sep}")
    print(f"  PERFORMANCE SUMMARY  —  Season {season}")
    if date_from or date_to or last_n:
        print(f"  Filter : {date_range}")
    print(sep)
    print(f"  Logged games      : {len(rows)}")
    print(f"  Total bets placed : {len(all_bets)}")
    print(f"  Wins              : {len(wins)}")
    print(f"  Losses            : {len(losses)}")
    print(f"  Win rate          : {win_rate:.1f}%")
    print(f"  PASS (skipped)    : {len(passes)}")
    print(f"  Postponed/susp.   : {len(postponed)}")
    print(f"  Ungraded          : {len(ungraded)}")
    print()
    print(f"  By side:")
    print(f"    NRFI  : {_record(nrfi_bets, nrfi_wins)}")
    print(f"    YRFI  : {_record(yrfi_bets, yrfi_wins)}")
    print()
    print(f"  By strength:")
    print(f"    LEAN  : {_record(lean_bets,   lean_wins)}")
    print(f"    STRONG: {_record(strong_bets, strong_wins)}")
    if live_bets:
        print()
        print(f"  All-live inputs (4/4 live):")
        print(f"    {_record(live_bets, live_wins)}")
    print(sep)
    print()

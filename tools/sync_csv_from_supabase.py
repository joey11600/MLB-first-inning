"""Pull odds / grade / bet columns from Supabase into picks_<season>.csv.

PROBLEM
=======
Two writers, two stores, one row.  picks_<season>.csv is updated by:
  - GHA cron's predict step (writes predict-owned fields)
  - GHA cron's grade step (writes graded_result, profit_loss_units via
    tracker._calc_pnl -- which falls back to -110 when CSV has no odds)
Supabase is updated by:
  - Railway predict cycle (predict + import_odds, every ~5 min)
  - Railway live worker (live grade + real-odds P&L, every ~10s)
  - tools/end_of_day_check.py (orphan-bet safety net)

When the worker grades a STRONG bet at +105 and writes pl=1.05 to
Supabase, then a GHA grade-today cron runs the same row through
_calc_pnl with empty CSV market_yrfi_odds, _calc_pnl falls back to
-110 and stamps pl=0.909 in the CSV.  Cron commits.  Now CSV and
Supabase disagree.  pl_calc.py reads CSV and reports the wrong pl.

FIX
===
Before any GHA cron computes anything from CSV, pull the
"non-predict-owned" columns from Supabase into the matching CSV rows.
After the sync, CSV reflects whatever Railway has authoritatively
captured for odds + bet flags + grade results, and downstream
computations (pl_calc, grade_picks, etc.) operate on the same numbers
the dashboard shows.

The sync is INTO the CSV only.  We never overwrite predict-owned
fields (pitcher inputs, lambdas, lineup, park factor, etc.).
Composite PK (date, game_pk) is the join key; rows that exist in
Supabase but not in CSV are LOGGED but not inserted (those are
handled by the predict step).  Rows that exist in CSV but not in
Supabase are left alone.

Usage:
  # Sync the recent window (default: today + last 7 days, ET):
  python tools/sync_csv_from_supabase.py

  # Sync a specific date:
  python tools/sync_csv_from_supabase.py --date 2026-05-06

  # Sync the entire season (idempotent, safe to run any time):
  python tools/sync_csv_from_supabase.py --all
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.supabase_writer import _get_client, _PRESERVE_ON_BLANK_FIELDS
import tracker
from tracker import FIELDS, _csv_path, _read_rows, _write_rows


# Columns to pull from Supabase and merge into CSV.  Exactly the set
# the predictor does NOT own -- captured odds, derived edges, opening
# line, CLV, bet placement, and grade results.  Mirrors
# _PRESERVE_ON_BLANK_FIELDS in db.supabase_writer; we re-list here to
# document which columns are sync targets and to keep the import
# coupling shallow.
_SYNC_COLUMNS = sorted(_PRESERVE_ON_BLANK_FIELDS) + ["sportsbook"]
# Strip duplicates if any (sportsbook is already in the preserve set).
_SYNC_COLUMNS = sorted(set(_SYNC_COLUMNS))

# Columns that record WHEN something was observed.  These are merged
# through tracker.advance_capture_ts (monotonic) instead of a plain
# assignment -- see the block comment at the merge loop below, and the
# "Capture-timestamp monotonicity" section in tracker.py.
_CAPTURE_TS_COLUMNS = frozenset({"odds_captured_at", "opened_captured_at"})


def _et_today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _et_window(days: int) -> list[str]:
    """ET-anchored window of `days` calendar dates, ending today."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def _coerce_supabase_value(col: str, val) -> str:
    """Convert a Supabase column value back to its CSV representation.

    Supabase stores most columns as their native Postgres types
    (numeric, integer, text, timestamp).  CSV stores everything as
    strings.  Use the same conventions tracker.py uses when writing
    CSV: blank for None, str() for everything else, with rounding
    where the predictor's _fmt would round."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Y" if val else "N"
    if isinstance(val, (int, float)):
        # Mirror tracker._fmt: trim trailing zeros after a fixed
        # precision matched to the column.  We don't have access to
        # _fmt's per-column rounding here, so use a sensible default.
        if isinstance(val, int):
            return str(val)
        # 4dp covers probs / edges / lambdas; pl is 3dp upstream
        # but 4dp formatted with rstrip won't lose data.
        if col == "profit_loss_units" or col == "units_risked":
            return f"{val:.3f}".rstrip("0").rstrip(".") or "0"
        if "prob" in col or "edge" in col or "lambda" in col:
            return f"{val:.4f}".rstrip("0").rstrip(".") or "0"
        return str(val)
    return str(val).strip()


def fetch_supabase_rows(
    client, season: int, dates: list[str] | None
) -> list[dict]:
    """Fetch the sync columns + composite PK for the listed dates from
    Supabase, or all rows if dates is None.  Returns empty list on
    error (caller treats as no-op)."""
    table = f"picks_{season}"
    cols = ["date", "game_pk", *_SYNC_COLUMNS]
    select_str = ", ".join(cols)
    try:
        q = client.table(table).select(select_str)
        if dates is not None:
            q = q.in_("date", dates)
        return q.execute().data or []
    except Exception as exc:    # noqa: BLE001
        print(f"[sync] supabase select failed: {exc!r}", file=sys.stderr)
        return []


def sync_csv(season: int, dates: list[str] | None, dry_run: bool) -> int:
    """Sync the CSV's odds/grade/bet columns from Supabase.  Returns
    the number of CSV rows updated."""
    client = _get_client()
    if client is None:
        print("[sync] Supabase env vars not set; cannot sync.",
              file=sys.stderr)
        return 0

    csv_path = _csv_path(season)
    if not csv_path.exists():
        print(f"[sync] CSV not found at {csv_path}", file=sys.stderr)
        return 0

    rows = _read_rows(csv_path)
    csv_index: dict[tuple[str, str], int] = {
        (r.get("date") or "", str(r.get("game_pk") or "")): i
        for i, r in enumerate(rows)
    }

    sb_rows = fetch_supabase_rows(client, season, dates)
    if not sb_rows:
        print("[sync] no Supabase rows fetched; nothing to do.")
        return 0

    print(f"[sync] fetched {len(sb_rows)} Supabase row(s); merging into "
          f"{csv_path.name}")

    updated = unmatched = unchanged = 0
    ts_advanced = ts_rejected = 0
    for sb in sb_rows:
        key = (sb.get("date") or "", str(sb.get("game_pk") or ""))
        idx = csv_index.get(key)
        if idx is None:
            unmatched += 1
            continue
        row = rows[idx]
        # Apply each sync column.  Only overwrite if Supabase has a
        # non-empty value -- this protects against a Supabase row that
        # happens to be missing a column that the CSV does have (e.g. a
        # legacy backfill).  In every realistic case, Supabase is the
        # fresher writer for these columns.
        row_changed = False
        for col in _SYNC_COLUMNS:
            sb_val = sb.get(col)
            csv_val = (row.get(col) or "").strip()
            new_val = _coerce_supabase_value(col, sb_val)
            if not new_val:
                continue
            # 2026-07-29 -- CAPTURE TIMESTAMPS ARE HIGH-WATER MARKS.
            #
            # This loop is where `odds_captured_at` learned to run
            # backwards on ~10% of rows.  Each column is merged
            # INDEPENDENTLY and any column blank in Supabase is skipped,
            # so the merged row can be assembled out of two different
            # capture moments: a lagging mirror's `odds_captured_at`
            # landing beside an `opened_captured_at` the CSV already had
            # from a fresher GHA import.  The result was rows whose
            # "latest price seen" predated their own "first price seen".
            #
            # The comment above ("Supabase is the fresher writer") holds
            # for VALUES but not for TIME: a GHA import writes the CSV
            # directly and Railway's mirror can be minutes behind it.
            if col in _CAPTURE_TS_COLUMNS:
                if tracker.advance_capture_ts(row, col, new_val):
                    row_changed = True
                    ts_advanced += 1
                elif tracker.capture_ts_regressed(csv_val, new_val):
                    ts_rejected += 1
                continue
            if new_val != csv_val:
                row[col] = new_val
                row_changed = True
        if row_changed:
            updated += 1
        else:
            unchanged += 1

    print(f"[sync] {updated} row(s) updated, {unchanged} unchanged, "
          f"{unmatched} Supabase-only (no CSV row)")
    if ts_advanced or ts_rejected:
        print(f"[sync] capture timestamps: {ts_advanced} advanced, "
              f"{ts_rejected} rejected as backwards")

    if dry_run:
        print("[sync] --dry-run set; CSV NOT written.")
        return updated

    if updated > 0:
        _write_rows(csv_path, rows)
        print(f"[sync] wrote {len(rows)} rows back to {csv_path.name}")
    else:
        print("[sync] no changes; CSV left as-is.")

    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--date", help="ET slate date YYYY-MM-DD")
    grp.add_argument("--all", action="store_true",
                     help="Sync the entire season")
    grp.add_argument("--days", type=int, default=8,
                     help="Trailing window size (default 8 = today + last 7)")
    parser.add_argument("--season", type=int,
                        default=int(_et_today()[:4]))
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change; do not write CSV.")
    args = parser.parse_args(argv)

    if args.date:
        dates = [args.date]
    elif args.all:
        dates = None
    else:
        dates = _et_window(args.days)

    return 0 if sync_csv(args.season, dates, args.dry_run) >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())

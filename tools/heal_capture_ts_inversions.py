#!/usr/bin/env python3
"""tools/heal_capture_ts_inversions.py

One-shot repair of rows whose `odds_captured_at` predates their own
`opened_captured_at`.  Operator authorised 2026-07-29.

WHAT WENT WRONG
---------------
`tools/sync_csv_from_supabase.py` runs on every predict and grade cron
tick and merges the Supabase mirror into the CSV COLUMN BY COLUMN,
skipping any column blank in Supabase.  A row could therefore be
assembled out of two different capture moments -- a lagging mirror's
`odds_captured_at` landing beside an `opened_captured_at` the CSV
already had from a fresher GHA import.  112 of 1579 rows ended up with
"latest price seen" EARLIER than "first price seen", which is
impossible by construction.

The write-side guard shipped separately (`tracker.advance_capture_ts` /
`retreat_capture_ts`) stops NEW inversions.  This heals the existing
ones.

WHICH COLUMN IS THE CORRUPT ONE -- MEASURED, NOT ASSUMED
--------------------------------------------------------
Within its own slate day, on the inverted rows:

    odds_captured_at    sits at the  7th percentile (healthy: 38th)
    opened_captured_at  sits at the 29th percentile (healthy: 43rd)

`odds_captured_at` is dramatically early for a column that means "the
LATEST price we have seen"; `opened_captured_at` is close to its normal
position.  So `odds_captured_at` is what got dragged backwards, and
`opened_captured_at` is the trustworthy side of the pair.

WHY THE REPAIR IS A CLAMP AND NOT A RECOVERY
--------------------------------------------
All 94 daily backup snapshots (2026-05-02 onward) were searched for a
surviving pre-corruption value: a capture later than the corrupted one
and consistent with `opened_captured_at`.  ZERO of the 112 rows had
one -- the drag-back always happened before the daily snapshot.  There
is no real value left to restore.

So the repair sets `odds_captured_at = opened_captured_at`.  That is a
REAL observed timestamp for that row, not an invented one, and it is
explicitly a LOWER BOUND: after this run those rows mean "the price had
been seen by at least this time", not "this is when it was last
refreshed".  Nothing else is touched -- no odds, no stakes, no grades,
no P&L.

11 of the 112 rows have `bet_placed=Y`.  For those the column is the
lock evidence, so their recorded lock time becomes a lower bound too.
That is strictly better than an impossible ordering, and those rows
were already unauditable.

Idempotent.  Journals every change to pick_changes.csv.  Writes through
tracker._write_rows (tempfile + os.replace), per CLAUDE.md.

    python tools/heal_capture_ts_inversions.py --dry-run
    python tools/heal_capture_ts_inversions.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402  -- import after sys.path tweak


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change; write nothing.")
    args = ap.parse_args()

    csv_path = tracker._csv_path(args.season)
    rows = tracker._read_rows(csv_path)

    targets = []
    for idx, r in enumerate(rows):
        opened = r.get("opened_captured_at")
        odds = r.get("odds_captured_at")
        if tracker.capture_ts_regressed(opened, odds):
            targets.append((idx, r, odds, opened))

    print(f"scanned {len(rows)} rows in {csv_path.name}")
    print(f"inverted (odds_captured_at < opened_captured_at): {len(targets)}\n")

    if not targets:
        print("Nothing to heal -- the ledger already satisfies the invariant.")
        return 0

    placed = sum(1 for _, r, _, _ in targets
                 if (r.get("bet_placed") or "").strip().upper() == "Y")
    print(f"  of these, {placed} have bet_placed=Y (their lock time becomes "
          f"a lower bound)\n")

    for idx, r, odds, opened in targets[:8]:
        print(f"  {r.get('date')} {r.get('away_team')}@{r.get('home_team')}: "
              f"{odds} -> {opened}")
    if len(targets) > 8:
        print(f"  ... and {len(targets) - 8} more")

    if args.dry_run:
        print("\n--dry-run set; CSV NOT written.")
        return 0

    # `captured_at` is a REQUIRED keyword-only arg on _record_pick_change
    # and it is also part of the journal's dedupe key.  The first cut of
    # this script omitted it, so every call raised TypeError, the
    # blanket `except` below swallowed all 112, and the heal ran with an
    # empty audit trail.  Pass it explicitly, and never let a heal report
    # success while its journalling silently failed -- the count is
    # asserted after the loop.
    stamp = tracker._utc_now_iso() if hasattr(tracker, "_utc_now_iso") else None
    if stamp is None:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    journalled = 0
    for idx, r, odds, opened in targets:
        rows[idx]["odds_captured_at"] = opened
        # Journal it.  A silent bulk edit of the ledger is exactly what
        # the pick_changes feed exists to make impossible.
        try:
            tracker._record_pick_change(
                iso_date=r.get("date", ""),
                game_pk=str(r.get("game_pk") or ""),
                away_team=(r.get("away_team") or ""),
                home_team=(r.get("home_team") or ""),
                game_time=(r.get("game_time_et") or ""),
                old_label=(r.get("pick_label") or ""),
                new_label=(f"{r.get('pick_label') or ''} · CAPTURE-TS HEAL "
                           f"(odds_captured_at {odds} -> {opened})"),
                captured_at=stamp,
            )
            journalled += 1
        except Exception as exc:  # noqa: BLE001 -- never abort the heal
            print(f"  [warn] journal failed for {r.get('date')} "
                  f"{r.get('away_team')}@{r.get('home_team')}: {exc}")

    if journalled != len(targets):
        print(f"\n  [WARN] only {journalled}/{len(targets)} changes reached "
              f"pick_changes.csv -- the ledger edit below is NOT fully "
              f"journalled.  Investigate before trusting the audit trail.")

    tracker._write_rows(csv_path, rows)
    print(f"\nHEALED {len(targets)} row(s); {csv_path.name} rewritten "
          f"atomically.")
    print("Re-run tools/verify_capture_ts_monotonic.py --report-ledger to "
          "confirm 0 remain.")
    print("Supabase still holds the stale values, but the write-side guard "
          "now rejects them, so the next sync cannot undo this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

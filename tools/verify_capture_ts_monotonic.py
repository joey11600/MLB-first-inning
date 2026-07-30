#!/usr/bin/env python3
"""tools/verify_capture_ts_monotonic.py

Guards the 2026-07-29 fix: a capture timestamp is a HIGH-WATER MARK.

THE BUG THIS EXISTS TO PREVENT
------------------------------
112 of 1129 ledger rows (9.9%) carried an `odds_captured_at` EARLIER
than their own `opened_captured_at`.  Both are assigned from the same
value on the first import that sees a price, and `odds_captured_at`
only ever moves forward afterwards, so that ordering is impossible by
construction.

The cause was `tools/sync_csv_from_supabase.py` merging the Supabase
mirror into the CSV column by column and skipping any column blank in
Supabase.  A row could therefore be assembled out of two different
capture moments -- a lagging mirror's `odds_captured_at` beside a
fresher `opened_captured_at` the CSV already had.

No money moved: `bet_placed` and `units_risked` on affected rows were
correct.  But the T2.23 lock freezes `odds_captured_at` when a bet
commits, making it the only ledger evidence of WHEN a bet locked -- the
audit trail for the T2.58 window -- and a timestamp that can rewind
makes that unauditable and corrupts open-to-bet CLV.

Run after any change to the odds-import or Supabase-sync path:

    python tools/verify_capture_ts_monotonic.py
    python tools/verify_capture_ts_monotonic.py --report-ledger
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402  -- import after sys.path tweak


T1 = "2026-07-29T15:00:25+00:00"   # earlier
T2 = "2026-07-29T18:30:00+00:00"   # later
T2_Z = "2026-07-29T18:30:00Z"      # same instant, Z form
T2_NAIVE = "2026-07-29T18:30:00"   # same instant, naive (read as UTC)


def check(label: str, got, want) -> bool:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
          + ("" if ok else f"   got {got!r}, want {want!r}"))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-ledger", action="store_true",
                    help="Also scan data/picks_<season>.csv and report any "
                         "surviving inversions (report only, never writes).")
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args()

    ok = True

    print("=== CHECK 1: parsing is tolerant of every form in the ledger ===")
    ok &= check("offset-aware parses", tracker.parse_capture_ts(T2) is not None, True)
    ok &= check("Z form parses",       tracker.parse_capture_ts(T2_Z) is not None, True)
    ok &= check("naive parses as UTC", tracker.parse_capture_ts(T2_NAIVE) is not None, True)
    ok &= check("Z == offset form",
                tracker.parse_capture_ts(T2_Z) == tracker.parse_capture_ts(T2), True)
    ok &= check("naive == offset form (naive is UTC)",
                tracker.parse_capture_ts(T2_NAIVE) == tracker.parse_capture_ts(T2), True)
    ok &= check("blank -> None",       tracker.parse_capture_ts("") is None, True)
    ok &= check("garbage -> None",     tracker.parse_capture_ts("not a date") is None, True)

    print("\n=== CHECK 2: regression detection ===")
    ok &= check("older than current IS a regression",
                tracker.capture_ts_regressed(T2, T1), True)
    ok &= check("newer than current is NOT",
                tracker.capture_ts_regressed(T1, T2), False)
    ok &= check("equal is NOT a regression",
                tracker.capture_ts_regressed(T2, T2), False)
    # Unknowns must never be called regressions, or a first write or a
    # repair of a corrupt cell would be silently refused.
    ok &= check("blank current -> not a regression",
                tracker.capture_ts_regressed("", T1), False)
    ok &= check("blank new -> not a regression",
                tracker.capture_ts_regressed(T2, ""), False)
    ok &= check("garbage -> not a regression",
                tracker.capture_ts_regressed("xx", T1), False)

    print("\n=== CHECK 3: advance_capture_ts is a high-water mark ===")
    r = {"odds_captured_at": T1}
    changed = tracker.advance_capture_ts(r, "odds_captured_at", T2)
    ok &= check("forward move applies", (changed, r["odds_captured_at"]), (True, T2))

    r = {"odds_captured_at": T2}
    changed = tracker.advance_capture_ts(r, "odds_captured_at", T1)
    ok &= check("BACKWARD move refused", (changed, r["odds_captured_at"]), (False, T2))

    r = {"odds_captured_at": ""}
    changed = tracker.advance_capture_ts(r, "odds_captured_at", T1)
    ok &= check("first write applies", (changed, r["odds_captured_at"]), (True, T1))

    r = {"odds_captured_at": T1}
    changed = tracker.advance_capture_ts(r, "odds_captured_at", "")
    ok &= check("blank never clobbers", (changed, r["odds_captured_at"]), (False, T1))

    r = {"odds_captured_at": T2}
    changed = tracker.advance_capture_ts(r, "odds_captured_at", T2)
    ok &= check("no-op reports unchanged", changed, False)

    print("\n=== CHECK 4: the original defect, replayed ===")
    # Exactly the shape the sync produced: the CSV already holds a fresh
    # value from a GHA import; the lagging Supabase mirror offers an
    # older one for odds_captured_at while opened_captured_at is fresher.
    row = {"odds_captured_at": T2, "opened_captured_at": T2}
    tracker.advance_capture_ts(row, "odds_captured_at", T1)   # stale mirror
    inverted = tracker.capture_ts_regressed(row["opened_captured_at"],
                                            row["odds_captured_at"])
    ok &= check("stale mirror can no longer invert the pair", inverted, False)

    if args.report_ledger:
        print("\n=== LEDGER SCAN (report only, nothing written) ===")
        path = tracker._csv_path(args.season)
        try:
            with open(path, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as exc:
            print(f"  could not read {path}: {exc}")
            return 0 if ok else 1
        both = bad = 0
        worst: dict[str, int] = {}
        for r in rows:
            o, c = r.get("opened_captured_at"), r.get("odds_captured_at")
            if not tracker.parse_capture_ts(o) or not tracker.parse_capture_ts(c):
                continue
            both += 1
            if tracker.capture_ts_regressed(o, c):
                bad += 1
                worst[r.get("date", "?")] = worst.get(r.get("date", "?"), 0) + 1
        pct = (bad / both * 100) if both else 0.0
        print(f"  rows with both timestamps: {both}")
        print(f"  odds_captured_at < opened_captured_at: {bad} ({pct:.1f}%)")
        if worst:
            top = sorted(worst.items(), key=lambda kv: -kv[1])[:6]
            print(f"  worst dates: {top}")
        print("  NOTE: pre-existing rows are NOT repaired by this fix. The"
              " guard stops new inversions; healing history rewrites the"
              " ledger and needs operator sign-off (CLAUDE.md data rules).")

    print("\n" + ("ALL CHECKS PASSED" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

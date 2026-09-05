#!/usr/bin/env python3
"""tools/heal_phantom_reschedule_rows.py -- T8.41: re-grade the ledger rows
that were scored with a game they were never part of.

WHAT WENT WRONG
===============
A postponed game keeps its MLB `game_pk` when it is replayed on the makeup
date, and the predictor logs a fresh row on the makeup date -- so the ledger
holds TWO rows for one game_pk.  `tracker.grade_date` fetched the linescore
by game_pk, which after the makeup is the MAKEUP's first inning, and its
"was POSTPONED, re-checking for makeup/resume" branch wrote that inning onto
the ORIGINAL-date row: WIN / LOSS / PASS under starters who never threw it.
Thirteen rows in the 2026 ledger; one carried money (2026-07-27 CLE@CIN,
STRONG YRFI 1u at -125, +0.80u booked from the 07-28 makeup -- a bet locked
3.5 h after a first pitch that never happened, and voided at any book).

The grader now stops at MLB's `officialDate` (CHANGELOG [2026-09-05f]); this
tool applies the same rule to the rows graded before the fix.  It is the
inverse of nothing: `grade_date` skips terminally-graded rows, so those rows
would otherwise keep the wrong result forever.

WHAT IT CHANGES, AND WHAT IT LEAVES ALONE
=========================================
For every game_pk that appears on more than one date, MLB is asked once for
the game's official date.  A row whose date is NOT the official date gets
`tracker._phantom_reschedule_grade` -- POSTPONED, or SUSPENDED for the
resume-day listing of a suspended game -- applied through
`tracker._mark_phantom_reschedule_row`, the same function the live grader
uses:

    actual_result / graded_result   -> POSTPONED | SUSPENDED
    graded_at                       -> now
    fi_away_runs / fi_home_runs / fi_total_runs -> blank (they belong to
                                       the official date's row)
    profit_loss_units               -> tracker._calc_pnl(row), which is ""
                                       for any grade but WIN/LOSS

bet_placed, units_risked and every odds / capture column are NOT touched.
They record what the system published (CLE@CIN really was announced as a
1u bet); the money rules forbid editing them outside the journaled override
path, and a voided bet with no P&L is exactly how a sportsbook shows it.

Rows already carrying the phantom grade are skipped, so the tool is
idempotent.  The makeup-date row (date == official date) is never touched.

SAFETY
======
Dry run by default; `--apply` writes.  Every changed cell is journaled to
data/diagnostics/heals/phantom_reschedule_<utc>.csv (before / after).  The
CSV is written atomically through `tracker._write_rows`; Supabase gets the
changed rows through the ordinary mirror PLUS an explicit clear of the
blanked columns (the mirror preserves blank grade/money fields on purpose
and would otherwise leave the stale runs and P&L standing).

USAGE
=====
  python tools/heal_phantom_reschedule_rows.py                 # report only
  python tools/heal_phantom_reschedule_rows.py --apply         # write
  python tools/heal_phantom_reschedule_rows.py --game-pk 824490 --apply

Then: python tools/pl_calc.py --date 2026-07-27  (and --window season).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker  # noqa: E402

JOURNAL_DIR = Path(tracker.__file__).resolve().parent / "data" / "diagnostics" / "heals"
_JOURNAL_FIELDS = ("date", "game_pk", "away_team", "home_team", "field",
                   "before", "after", "official_date", "healed_at")
_TRACKED = ("actual_result", "graded_result", "graded_at",
            "fi_away_runs", "fi_home_runs", "fi_total_runs", "profit_loss_units")


def _clear_supabase(rows: list[dict], season: int, fields: list[str]) -> int:
    """Explicit NULL of the blanked columns.  Separate so tests can stub it."""
    if not rows or not fields:
        return 0
    try:
        from db.supabase_writer import clear_pick_fields
        return clear_pick_fields(rows, season, fields)
    except Exception as exc:    # noqa: BLE001 -- CSV is the source of truth
        print(f"   supabase clear failed (non-fatal): {exc!r}", file=sys.stderr)
        return 0


def _already_healed(row: dict, grade: str) -> bool:
    if (row.get("graded_result") or "").strip().upper() != grade:
        return False
    if any((row.get(f) or "").strip() for f in
           ("fi_away_runs", "fi_home_runs", "fi_total_runs")):
        return False
    return (row.get("profit_loss_units") or "").strip() == tracker._calc_pnl(row)


def run(season: int, apply: bool, only_pk: str | None = None) -> int:
    path = tracker._csv_path(season)
    rows = tracker._read_rows(path)
    by_pk: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        pk = (r.get("game_pk") or "").strip()
        if pk:
            by_pk[pk].append(i)
    groups = {pk: idxs for pk, idxs in by_pk.items()
              if len({rows[i]["date"] for i in idxs}) > 1
              and (only_pk is None or pk == only_pk)}
    print(f"[heal-phantom] season={season} rows={len(rows)} "
          f"game_pks on more than one date={len(groups)} "
          f"mode={'APPLY' if apply else 'dry-run'}")

    now = tracker._now_utc()
    journal: list[dict] = []
    changed: list[dict] = []
    blanked_union: set[str] = set()
    n_skipped_ok = n_unreadable = 0

    for pk in sorted(groups, key=int):
        status = tracker._fetch_first_inning(int(pk))
        if status.get("state") == "ERROR" or not status.get("official_date"):
            n_unreadable += 1
            print(f"  pk {pk}: MLB status unreadable ({status.get('detail')!r}) -- skipped")
            continue
        official = status["official_date"]
        for i in sorted(groups[pk], key=lambda k: rows[k]["date"]):
            row = rows[i]
            tag = f"  {row['date']} {row['away_team']:>3}@{row['home_team']:<3} pk {pk}"
            grade = tracker._phantom_reschedule_grade(row["date"], status)
            if not grade:
                print(f"{tag}  official {official}: the game's row -- untouched "
                      f"(graded {row.get('graded_result') or '-'})")
                continue
            if _already_healed(row, grade):
                n_skipped_ok += 1
                print(f"{tag}  official {official}: already {grade} -- nothing to do")
                continue
            before = {f: row.get(f, "") for f in _TRACKED}
            blanked = tracker._mark_phantom_reschedule_row(row, grade, now)
            blanked_union.update(blanked)
            changed.append(row)
            for f in _TRACKED:
                if before[f] != row.get(f, ""):
                    journal.append({
                        "date": row["date"], "game_pk": pk,
                        "away_team": row["away_team"], "home_team": row["home_team"],
                        "field": f, "before": before[f], "after": row.get(f, ""),
                        "official_date": official, "healed_at": now,
                    })
            print(f"{tag}  official {official}: {before['graded_result'] or '-'}"
                  f" (P&L {before['profit_loss_units'] or '-'}, "
                  f"1st inn {before['fi_away_runs'] or '-'}-{before['fi_home_runs'] or '-'})"
                  f"  ->  {grade}  bet_placed={row.get('bet_placed') or '-'} kept")

    print(f"\n[heal-phantom] to change: {len(changed)} row(s), {len(journal)} cell(s); "
          f"already healed: {n_skipped_ok}; unreadable: {n_unreadable}")
    if not changed:
        return 0
    if not apply:
        print("[heal-phantom] dry run -- nothing written.  Re-run with --apply.")
        return 0

    tracker._write_rows(path, rows)
    print(f"[heal-phantom] wrote {path.name}")
    tracker._mirror_picks_to_supabase(season, changed)
    cleared = _clear_supabase(changed, season, sorted(blanked_union))
    print(f"[heal-phantom] supabase: mirrored {len(changed)} row(s), "
          f"cleared {sorted(blanked_union)} on {cleared} row(s)")

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jpath = JOURNAL_DIR / f"phantom_reschedule_{stamp}.csv"
    with open(jpath, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_JOURNAL_FIELDS)
        w.writeheader()
        w.writerows(journal)
    print(f"[heal-phantom] journal: {jpath}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, default=datetime.now().year)
    ap.add_argument("--apply", action="store_true",
                    help="write the ledger + Supabase (default: report only)")
    ap.add_argument("--game-pk", default=None, help="restrict to one game_pk")
    args = ap.parse_args()
    return run(args.season, apply=args.apply, only_pk=args.game_pk)


if __name__ == "__main__":
    sys.exit(main())

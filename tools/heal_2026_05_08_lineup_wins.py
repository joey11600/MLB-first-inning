#!/usr/bin/env python3
"""tools/heal_2026_05_08_lineup_wins.py

ONE-SHOT retroactive bet-placement heal for two LINEUP PENDING games
on 2026-05-08 that resolved as NRFI wins but were never bet:

  823794  NYY @ MIL  STRONG NRFI  -140  WIN  +0.7143u
  824116  DET @ KC   STRONG NRFI  -125  WIN  +0.8000u

Background.  Both games locked at PASS - LINEUP PENDING because one
team's batting order hadn't posted by T-60.  The model's tentative
NRFI lean (nrfi_p ~ 0.642 / 0.644 = STRONG NRFI) ended up correct --
both first innings were 0-0 -- but the LINEUP PENDING guard kept
either row from auto-betting.  Operator made the call to count them
as bets retroactively.

This script runs in three independently-idempotent phases:

  1. CSV phase.  Flip pick_side / pick_strength / pick_label to
     STRONG NRFI, set bet_placed=Y + units_risked=1, lock
     market_nrfi_odds to lock-time price (= opened_nrfi_odds), stamp
     graded_result=WIN, recompute profit_loss_units via
     tracker._calc_pnl.  Skipped per-row when CSV already shows the
     healed shape.  Also writes a pick_changes.csv journal entry.

  2. Supabase mirror phase.  Always called for the target rows so a
     CSV-only heal (e.g. committed locally without Supabase env vars)
     still propagates to the dashboard on the next run that DOES have
     the env vars.  tracker._mirror_picks_to_supabase is no-op when
     env vars are missing -- safe to call always.

  3. Telegram notify phase.  Fires the standard
     _notify_strong_graded_telegram for each row.  notifications_log
     has a 24h dedup window for event_type=strong_graded so re-runs
     won't double-ping.

Net: safe to call every cron tick.  First run with full env applies
all three phases, subsequent runs are no-ops.

Usage:
  python tools/heal_2026_05_08_lineup_wins.py            # apply heal
  python tools/heal_2026_05_08_lineup_wins.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402  -- import after sys.path tweak

ISO_DATE = "2026-05-08"
SEASON   = 2026

# Each entry: (game_pk, away, home, lock-time NRFI price as captured
# in opened_nrfi_odds the slate-day before lock).
OVERRIDES = [
    {"game_pk": "823794", "away": "NYY", "home": "MIL", "lock_price": "-140"},
    {"game_pk": "824116", "away": "DET", "home": "KC",  "lock_price": "-125"},
]


def _row_already_healed(row: dict) -> bool:
    return (
        (row.get("graded_result") or "").strip().upper() == "WIN"
        and (row.get("bet_placed")    or "").strip().upper() == "Y"
        and (row.get("pick_side")     or "").strip().upper() == "NRFI"
        and (row.get("pick_strength") or "").strip().upper() == "STRONG"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change; don't write CSV / Supabase / Telegram.")
    args = p.parse_args()

    csv_path = tracker._csv_path(SEASON)
    rows = tracker._read_rows(csv_path)
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    target_indices: list[int] = []   # all targets (for mirror + notify)
    csv_mutated_indices: list[int] = []   # rows we actually changed in CSV

    for o in OVERRIDES:
        target_idx = None
        for idx, row in enumerate(rows):
            if row.get("date") != ISO_DATE:
                continue
            if str(row.get("game_pk") or "").strip() != o["game_pk"]:
                continue
            target_idx = idx
            break

        tag = f"  {o['away']:>3} @ {o['home']:<3} (pk={o['game_pk']})"

        if target_idx is None:
            print(f"{tag}: NO MATCH in {csv_path} -- skipping")
            continue

        row = rows[target_idx]
        target_indices.append(target_idx)

        if _row_already_healed(row):
            print(f"{tag}: CSV already healed (WIN / bet=Y / STRONG NRFI) -- mirror+notify only")
            continue

        old_label = (row.get("pick_label") or "").strip()

        # Apply override.  market_nrfi_odds is locked to lock-time price
        # so _calc_pnl computes the WIN against the actual DK juice we
        # would have laid at lock; this matches the system's standard
        # "once bet_placed=Y, market_* is the locked price" invariant
        # (T2.23) -- no closing-line drift.
        row["pick_side"]         = "NRFI"
        row["pick_strength"]     = "STRONG"
        row["pick_label"]        = "STRONG NRFI"
        row["bet_placed"]        = "Y"
        row["units_risked"]      = "1"
        row["graded_result"]     = "WIN"
        row["market_nrfi_odds"]  = o["lock_price"]
        # Recompute P&L now that pick_side / bet_placed / market_* are set.
        row["profit_loss_units"] = tracker._calc_pnl(row)

        csv_mutated_indices.append(target_idx)

        verb = "WOULD HEAL" if args.dry_run else "HEAL"
        print(
            f"{tag}: {verb} -> STRONG NRFI WIN @ {o['lock_price']} "
            f"=> {row['profit_loss_units']}u "
            f"(was: {old_label!r}, fi {row.get('fi_away_runs')}-{row.get('fi_home_runs')})"
        )

        if args.dry_run:
            continue

        # Journal the override so the dashboard's pick_changes feed
        # (and any future reader of the change log) sees a permanent
        # record of the manual override.
        try:
            tracker._record_pick_change(
                iso_date    = ISO_DATE,
                game_pk     = str(row.get("game_pk") or ""),
                away_team   = (row.get("away_team") or "").upper(),
                home_team   = (row.get("home_team") or "").upper(),
                game_time   = (row.get("game_time_et") or ""),
                old_label   = old_label,
                new_label   = "STRONG NRFI · MANUAL OVERRIDE (heal_2026_05_08_lineup_wins)",
                captured_at = now,
            )
        except Exception as exc:    # noqa: BLE001 -- journal is advisory
            print(f"{tag}: journal write failed (non-fatal): {exc!r}", file=sys.stderr)

    if not target_indices:
        print("\nNo target rows present in CSV -- nothing to do.")
        return 0

    if args.dry_run:
        print(f"\n[dry-run] Found {len(target_indices)} target(s); "
              f"would heal {len(csv_mutated_indices)} CSV row(s).  No writes performed.")
        return 0

    # Persist CSV mutation only if anything actually changed.  Atomic
    # via tracker._write_rows (tempfile + os.replace).
    if csv_mutated_indices:
        tracker._write_rows(csv_path, rows)

    # Always mirror the targets to Supabase so a CSV-only heal (e.g.
    # one committed locally without Supabase env vars) still propagates
    # to the dashboard the first time this script runs in an env that
    # HAS the credentials.  tracker._mirror_picks_to_supabase is a
    # no-op without env vars.
    try:
        tracker._mirror_picks_to_supabase(SEASON, [rows[i] for i in target_indices])
    except Exception as exc:    # noqa: BLE001
        print(f"  Supabase mirror failed (will retry next run): {exc!r}",
              file=sys.stderr)

    # Always fire the standard STRONG WIN Telegram for each target row
    # so the operator gets the same notification path a normally-bet
    # STRONG WIN would produce.  Today record / P&L recomputed against
    # the freshly-mutated row set so the running totals are accurate.
    today_record, today_pl = tracker._aggregate_today_record(rows, ISO_DATE)
    for idx in target_indices:
        try:
            tracker._notify_strong_graded_telegram(rows[idx], today_record, today_pl)
        except Exception as exc:    # noqa: BLE001
            print(f"  Telegram ping failed for row {idx} (non-fatal): {exc!r}",
                  file=sys.stderr)

    if csv_mutated_indices:
        print(f"\nHealed {len(csv_mutated_indices)} CSV row(s); mirrored {len(target_indices)} target(s).")
    else:
        print(f"\nCSV already in healed shape; mirrored {len(target_indices)} target(s) "
              f"to Supabase + fired Telegram (deduped if previously sent).")
    print(f"  CSV          : {csv_path}")
    print(f"  Today record : {today_record[0]}-{today_record[1]}-{today_record[2]} "
          f"| {today_pl:+.3f}u")

    return 0


if __name__ == "__main__":
    sys.exit(main())

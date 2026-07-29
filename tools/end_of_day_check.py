#!/usr/bin/env python3
"""
tools/end_of_day_check.py — nightly safety net for missed STRONG bet
placements.

Why this exists
---------------
The auto-bet flow (`tracker.import_odds` → bet_placed=Y → BET LOCKED
Telegram) only fires when DraftKings still has the game in their API
at the moment the cycle runs.  If DK closes the market early, or
Railway has a cycle-skip right around T-60min, or DK's API blocks the
scraper for that one tick, the row stays bet_placed='' / units_risked=''
forever -- the bet effectively "disappears" from the tracked record.

This is exactly what happened on 2026-05-05: 4 STRONG picks (early
games) had their DK markets close before the curl_cffi fix landed,
so even after Railway started scraping successfully, those 4 rows
never got bet_placed=Y.  The user noticed; without them noticing,
those 4 W/L outcomes would have been silently excluded from the
day's tracked P&L.

What this does
--------------
Run nightly (around midnight ET).  For each STRONG NRFI/YRFI pick on
the target date that's already terminal (graded WIN / LOSS / PASS) or
whose game has clearly started:

  1. If `bet_placed` is empty AND `pick_strength == "STRONG"`,
     retroactively flag it: bet_placed=Y, units_risked=1.0,
     profit_loss_units recomputed via `tracker._calc_pnl`.
  2. Patch ONLY those three fields to Supabase using
     `_patch_picks_to_supabase` (the safer primitive that won't
     wipe other columns).
  3. Send a single Telegram alert summarising the rows that were
     retro-fixed -- so the operator sees what happened.

If everything was placed correctly during the day, this script is
silent (no Telegram, exit 0).

Usage
-----
  # Default: yesterday's slate in ET (typical cron usage just after
  # midnight ET, when "yesterday" is the slate that just finished).
  python tools/end_of_day_check.py

  # Specific date:
  python tools/end_of_day_check.py --date 2026-05-05

  # Today's slate (e.g. mid-day diagnostic):
  python tools/end_of_day_check.py --today

  # Dry-run -- print what would be fixed without touching CSV / Supabase / Telegram:
  python tools/end_of_day_check.py --dry-run

Schedule
--------
The companion GHA workflow step fires this at 04:30 UTC (~12:30 AM ET)
with --date set to the prior calendar ET date.  See daily.yml.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tracker import (    # noqa: E402
    _calc_pnl,
    _csv_path,
    _read_rows,
    _write_rows,
    _patch_picks_to_supabase,
    _notify_event_telegram,
    _dashboard_link,
)

ET = ZoneInfo("America/New_York")


def yesterday_et_iso() -> str:
    """Yesterday's date in America/New_York as YYYY-MM-DD."""
    return (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")


def today_et_iso() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def find_orphaned_strong_bets(rows: list[dict], iso_date: str) -> list[int]:
    """Return indices of rows that are STRONG NRFI/YRFI for `iso_date`,
    have `bet_placed` empty/blank, AND are graded (WIN/LOSS/PASS) or
    POSTPONED/SUSPENDED (game-state confirmed).  POSTPONED/SUSPENDED
    rows aren't real bets, so we DON'T flip those -- but we do flag
    any STRONG row whose game ran without bet_placed."""
    out = []
    for i, r in enumerate(rows):
        if (r.get("date") or "").strip() != iso_date:
            continue
        strength = (r.get("pick_strength") or "").strip().upper()
        if strength != "STRONG":
            continue
        side = (r.get("pick_side") or "").strip().upper()
        if side not in ("NRFI", "YRFI"):
            continue
        bet_placed = (r.get("bet_placed") or "").strip().upper()
        if bet_placed == "Y":
            continue   # already correctly placed
        # 2026-07-28 P0 FIX (code audit): only heal rows whose bet_placed
        # is genuinely EMPTY -- the "odds pipeline never saw this row"
        # case this tool was written for (see module docstring).  The old
        # predicate skipped only "Y", which swept up every DELIBERATE
        # bet_placed="N": Kelly's zero-stake edge gate, the daily
        # exposure cap zeroing a pick, and T2.58 pre-lock pendings that
        # never committed.  Those rows then got retroactively stamped
        # bet_placed=Y at a flat 1.00u -- fabricating bets that were
        # never made, overwriting recorded Kelly stakes, and (because
        # current_bankroll_units compounds realized P&L) mis-sizing
        # every real stake placed later the same night.
        if bet_placed == "N":
            # 2026-07-28 REFINEMENT.  The blanket skip above was too broad
            # and it cost a real bet the same night it shipped.
            #
            # T2.58 records a STRONG pick's Kelly stake in units_risked and
            # leaves bet_placed="N" until the game's lock window opens,
            # then commits it to "Y".  If nothing runs during that window
            # the row GRADES while still pending, freezes at "N", and
            # _calc_pnl books 0 -- so a bet the system sized, told the
            # operator to place, and that he did place, records as nothing.
            # That is 2026-07-28 NYY@CWS: STRONG YRFI, -115, edge +17.8%,
            # sized 9.56u, graded LOSS, booked 0.00u.
            #
            # The discriminator is the STAKE.  A DELIBERATE no-bet has no
            # positive stake to record -- Kelly's zero-edge gate and the
            # daily-cap zeroing both produce units_risked "" or 0, and LEAN
            # never reaches here (STRONG-only filter above).  A pending
            # that never committed carries a real positive Kelly stake.
            # Heal only the latter, at the stake the system actually sized.
            try:
                staked = float((r.get("units_risked") or "").strip() or 0.0)
            except ValueError:
                staked = 0.0
            if staked <= 0:
                continue   # genuine no-bet decision -- never fabricate it
        graded = (r.get("graded_result") or "").strip().upper()
        # Only retro-fix games whose result is in: WIN / LOSS.
        # PASS shouldn't happen for STRONG NRFI/YRFI rows (PASS is
        # only for pick_side='PASS' rows).
        # POSTPONED / SUSPENDED: the bet was voided, not placed --
        # don't flip those.
        if graded not in ("WIN", "LOSS"):
            continue
        out.append(i)
    return out


def _resend_missing_strong_graded(rows: list[dict], iso_date: str) -> None:
    """T-V21-2026-05-06h: fire strong_graded Telegram for any placed
    STRONG graded bet on the date that hasn't already pinged.  Dedup
    in _notify_event_telegram (notifications_log query, 24h window)
    silently filters bets that already pinged today, so this is a
    safe one-shot recovery sweep.

    Catches the failure mode where a row was orphaned at grade time
    (bet_placed='' when grade_picks ran), got auto-flagged later by
    this script, but never sent its WIN/LOSS alert because grading is
    idempotent and won't re-call the notifier on already-graded rows."""
    try:
        from tracker import (
            _notify_strong_graded_telegram, _aggregate_today_record,
        )
    except Exception as exc:    # noqa: BLE001
        print(f"  [warn] cannot import notifier: {exc!r}", file=sys.stderr)
        return

    candidates = [
        r for r in rows
        if (r.get("date") or "").strip() == iso_date
        and (r.get("pick_strength") or "").strip().upper() == "STRONG"
        and (r.get("pick_side") or "").strip().upper() in ("NRFI", "YRFI")
        and (r.get("bet_placed") or "").strip().upper() == "Y"
        and (r.get("graded_result") or "").strip().upper() in ("WIN", "LOSS")
    ]
    if not candidates:
        return

    today_record, today_pl = _aggregate_today_record(rows, iso_date)
    print(f"  [recovery] sweeping {len(candidates)} placed STRONG graded bet(s) "
          f"for missing WIN/LOSS pings (dedup will filter already-fired)")
    for r in candidates:
        try:
            _notify_strong_graded_telegram(r, today_record, today_pl)
        except Exception as exc:    # noqa: BLE001 -- best effort
            print(f"  [warn] strong_graded recovery ping failed for "
                  f"{r.get('away_team')}@{r.get('home_team')}: {exc!r}",
                  file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--date",
        help="ET slate date YYYY-MM-DD.  Defaults to yesterday ET (typical "
             "cron usage right after midnight).",
    )
    grp.add_argument(
        "--today",
        action="store_true",
        help="Use today's ET slate instead of yesterday.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be fixed; do NOT write CSV / Supabase / Telegram.",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season override (defaults to year of the target date).",
    )
    args = parser.parse_args()

    if args.today:
        target_date = today_et_iso()
    elif args.date:
        target_date = args.date
    else:
        target_date = yesterday_et_iso()

    season = args.season or int(target_date[:4])
    csv_path = _csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2

    rows = _read_rows(csv_path)
    indices = find_orphaned_strong_bets(rows, target_date)

    print(f"end-of-day check  date={target_date}  "
          f"orphaned-STRONG-bets={len(indices)}  "
          f"dry_run={args.dry_run}")

    # T-V21-2026-05-06h: even when no orphans need repair, sweep all
    # placed STRONG graded bets on the date and try firing strong_graded
    # for each.  Catches the case where a row was an orphan earlier in
    # the day, got fixed by an interim end_of_day_check run, and the
    # WIN/LOSS Telegram never went out (because grade_picks already
    # skipped the row's notify when bet_placed was still '').
    # Dedup in _notify_event_telegram filters out already-fired pings,
    # so this is safe to call even when everything's healthy.
    if not args.dry_run:
        _resend_missing_strong_graded(rows, target_date)

    if not indices:
        print("All STRONG bets correctly placed.  No action.")
        return 0

    # Show what we found
    print()
    print("Orphaned STRONG bets (graded but bet_placed empty):")
    for i in indices:
        r = rows[i]
        print(f"  {r['away_team']}@{r['home_team']:<5} "
              f"{r['pick_strength']} {r['pick_side']:<5} "
              f"graded={r.get('graded_result'):<5} "
              f"P&L_before={r.get('profit_loss_units') or '-'}")

    if args.dry_run:
        print("\n[dry-run] no changes made.")
        return 0

    # Apply the fix in-memory
    fixed_rows: list[dict] = []
    for i in indices:
        rows[i]["bet_placed"] = "Y"
        # 2026-07-28: preserve an already-recorded stake instead of
        # blanket-overwriting with flat 1.00.  A healed row can carry a
        # real Kelly stake computed pre-lock; flattening it rewrites the
        # position size after the fact.  Flat 1.00 remains the fallback
        # for truly blank rows, matching what the live path does for a
        # priceless bet (Kelly returns None -> flat stake).
        if not (rows[i].get("units_risked") or "").strip():
            rows[i]["units_risked"] = "1.00"
        rows[i]["profit_loss_units"] = _calc_pnl(rows[i])
        fixed_rows.append(rows[i])

    # Persist to CSV (atomic write via tracker._write_rows)
    _write_rows(csv_path, rows)
    print(f"\nWrote {len(rows)} rows back to {csv_path.name}")

    # Patch Supabase (only the 3 changed fields -- never wipe anything else)
    _patch_picks_to_supabase(
        season,
        fixed_rows,
        ["bet_placed", "units_risked", "profit_loss_units"],
    )
    print(f"Patched {len(fixed_rows)} rows to Supabase "
          f"(bet_placed, units_risked, profit_loss_units only)")

    # T-V21-2026-05-06h: fire the missed strong_graded WIN/LOSS pings
    # for the orphans we just repaired.  _resend_missing_strong_graded
    # already runs unconditionally above (catches THIS slate's orphan-
    # then-fixed gaps too) but we re-call it here against the freshly-
    # patched rows so the per-bet WIN/LOSS surface lights up the same
    # cycle the safety net repairs the row.  Dedup in
    # _notify_event_telegram filters duplicates.
    _resend_missing_strong_graded(rows, target_date)

    # Telegram alert
    body_lines = [
        f"<b>End-of-day safety net fired ({target_date})</b>",
        f"{len(fixed_rows)} STRONG bet(s) had no <code>bet_placed</code> "
        f"flag at end-of-day.  Auto-flagged at 1.0u flat:",
        "",
    ]
    for r in fixed_rows:
        pl = r["profit_loss_units"]
        sign = "+" if (pl and not pl.startswith("-")) else ""
        body_lines.append(
            f"• {r['away_team']} @ {r['home_team']} · "
            f"STRONG {r['pick_side']} · {r['graded_result']} · "
            f"{sign}{pl}u"
        )
    body_lines.append("")
    body_lines.append(
        "(Likely root cause: DK closed the market before our scraper "
        "captured odds.  No real-world bet impact -- just bookkeeping.)"
    )
    body_lines.append(_dashboard_link(target_date))

    fired = _notify_event_telegram(
        "eod_safety_net",
        f"eod_safety_net:{target_date}:{len(fixed_rows)}",
        "\n".join(body_lines),
    )
    if fired:
        print("Telegram alert sent.")
    elif not (os.environ.get("TELEGRAM_BOT_TOKEN") and
              os.environ.get("TELEGRAM_CHAT_ID")):
        print("Telegram env vars unset -- alert skipped (silent ok).")
    else:
        print("Telegram alert dedup'd or transient error.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

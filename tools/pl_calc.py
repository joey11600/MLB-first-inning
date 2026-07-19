#!/usr/bin/env python3
"""
tools/pl_calc.py — single source of truth for "what's the P&L" answers.

Why this exists: on 2026-05-05 I (Claude) quoted +3.22u in chat by adding
the column wrong in my head, then a separate backfill bug pushed the
displayed dashboard total to +2.55u, and the actual answer was +2.22u.
Three different numbers for the same slate.  That can't happen again.

This script is the ONE place the right answer comes from.  Run it before
you quote a P&L number anywhere -- chat, Telegram, audit, anything.  It
reads the canonical CSV ledger, recomputes P&L per row using the same
helper the rest of the system uses (`tracker._calc_pnl`), cross-checks
against the stored `profit_loss_units` column, and prints a clean
breakdown plus the verified total.

Usage
-----
  # Today's slate (ET):
  python tools/pl_calc.py

  # Specific date:
  python tools/pl_calc.py --date 2026-05-05

  # Trailing window:
  python tools/pl_calc.py --window 7d
  python tools/pl_calc.py --window 30d
  python tools/pl_calc.py --window season

  # Include LEAN bets too (default: STRONG only -- user's policy is
  # "we only bet STRONG"):
  python tools/pl_calc.py --include-lean

  # Include PASS / ungraded rows in the listing for completeness:
  python tools/pl_calc.py --verbose

What gets reported
------------------
* Per-row breakdown: matchup, pick, units, odds, result, P&L.
* Grand totals: W / L record, hit-rate, P&L sum.
* "Consistency check": flags any row where the stored
  profit_loss_units value differs from what we recompute now.  A drift
  here means either the row was modified by something that didn't
  call _calc_pnl, or a backfill mirror clobbered a real odds value
  with a blank.
* "-110 fallback" tag on every row whose stored market odds are blank
  (P&L was computed using the standard -110 payout fallback in
  _calc_pnl).  Those rows are the ones most likely to be off if the
  real-world bet was placed at a different price.

This script never writes anything.  Pure read + report.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Import the canonical P&L helper from tracker.py so we compute the
# same number the rest of the system (live grade worker, --import-odds,
# nightly cron) computes.  If this drifts from tracker, that's the bug,
# not pl_calc.
from tracker import _calc_pnl, _csv_path, _read_rows  # noqa: E402

ET = ZoneInfo("America/New_York")


def _load_rows(season: int) -> tuple[list[dict], str]:
    """Load picks rows.  Supabase first (live, includes real-odds pl);
    CSV fallback when Supabase is unavailable (offline / no env vars)
    or returns nothing.  Returns (rows, source-description-for-display)."""
    csv_path = _csv_path(season)

    # Try Supabase first.
    try:
        from db.supabase_writer import _get_client
        client = _get_client()
        if client is not None:
            # PostgREST caps every response at ~1000 rows.  A bare
            # .select("*").execute() therefore silently returns only the
            # OLDEST 1000 picks -- so mid-season it drops every recent bet
            # and the P&L looks frozen weeks in the past.  This was the
            # root cause of the 2026-07 "won units not tracking" report:
            # the tool could not see anything after 2026-06-14.  Page
            # through the table (ordered by a stable key) so we read the
            # entire season, not just the first page.
            PAGE = 1000
            offset = 0
            sb_rows: list[dict] = []
            while True:
                res = (
                    client.table(f"picks_{season}")
                    .select("*")
                    .order("date")
                    .order("game_pk")
                    .range(offset, offset + PAGE - 1)
                    .execute()
                )
                batch = res.data or []
                sb_rows.extend(batch)
                if len(batch) < PAGE:
                    break
                offset += PAGE
            if sb_rows:
                return _supabase_rows_to_csv_shape(sb_rows), (
                    f"Supabase picks_{season} ({len(sb_rows)} rows)"
                )
    except Exception as exc:    # noqa: BLE001 -- fall through to CSV
        print(f"[pl_calc] Supabase read failed ({exc!r}); using CSV.",
              file=sys.stderr)

    # CSV fallback.
    if not csv_path.exists():
        print(f"ERROR: ledger CSV not found at {csv_path}", file=sys.stderr)
        return [], str(csv_path)
    return _read_rows(csv_path), str(csv_path)


def _supabase_rows_to_csv_shape(rows: list[dict]) -> list[dict]:
    """Coerce Supabase row dicts to the same string-based shape that
    CSV gives us, so filter_rows / _calc_pnl don't need to know the
    difference.  Numerics become formatted strings (matching tracker's
    _fmt conventions); None becomes "" everywhere."""
    out = []
    for r in rows:
        row: dict = {}
        for k, v in r.items():
            if v is None:
                row[k] = ""
            elif isinstance(v, bool):
                row[k] = "Y" if v else "N"
            elif isinstance(v, (int, float)):
                if k in ("profit_loss_units", "units_risked"):
                    row[k] = f"{v:.3f}".rstrip("0").rstrip(".") or "0"
                elif isinstance(v, int):
                    row[k] = str(v)
                else:
                    row[k] = f"{v:.4f}".rstrip("0").rstrip(".") or "0"
            else:
                row[k] = str(v)
        out.append(row)
    return out


def today_et_iso() -> str:
    """Today's date in America/New_York as YYYY-MM-DD."""
    return datetime.now(ET).strftime("%Y-%m-%d")


def parse_window(window: str) -> tuple[str, str]:
    """Translate '7d' / '30d' / 'season' into (start_iso, end_iso) inclusive.
    Always ends today ET."""
    end = today_et_iso()
    if window == "season":
        return (f"{end[:4]}-01-01", end)
    days = int(window.rstrip("d"))
    start_dt = datetime.now(ET) - timedelta(days=days - 1)
    return (start_dt.strftime("%Y-%m-%d"), end)


def filter_rows(
    rows: list[dict],
    start_iso: str,
    end_iso: str,
    include_lean: bool,
    include_verbose: bool,
) -> list[dict]:
    """Reduce the full ledger to the rows we want to report on."""
    out = []
    allowed_strengths = {"STRONG"}
    if include_lean:
        allowed_strengths.add("LEAN")
    for r in rows:
        date = (r.get("date") or "").strip()
        if not (start_iso <= date <= end_iso):
            continue
        strength = (r.get("pick_strength") or "").strip().upper()
        if not include_verbose and strength not in allowed_strengths:
            continue
        out.append(r)
    return out


def fmt_pl(value: float | None) -> str:
    """Format a P&L number with leading sign, 3 decimal places."""
    if value is None:
        return "       -"
    return f"{value:+.3f}u"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        help="Single ET slate date (YYYY-MM-DD).  Defaults to today ET.",
    )
    parser.add_argument(
        "--window",
        choices=["7d", "30d", "season"],
        help="Trailing window ending today ET.  Mutually exclusive with --date.",
    )
    parser.add_argument(
        "--include-lean",
        action="store_true",
        help="Include LEAN bets (default: STRONG only).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show PASS / ungraded rows too (default: only graded bets).",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season override.  Defaults to year of the queried date(s).",
    )
    args = parser.parse_args()

    if args.date and args.window:
        parser.error("--date and --window are mutually exclusive")

    # Resolve date range
    if args.window:
        start_iso, end_iso = parse_window(args.window)
        title = f"window={args.window}  ({start_iso} to {end_iso})"
    elif args.date:
        start_iso = end_iso = args.date
        title = f"date={args.date}"
    else:
        d = today_et_iso()
        start_iso = end_iso = d
        title = f"date={d} (today ET)"

    season = args.season or int(end_iso[:4])

    # T-V21-2026-05-07f: Supabase is the canonical source.  CSV is a
    # mirror that auto-syncs via the reconciler + sync_csv_from_supabase
    # step in every Railway / GHA cycle, so if both are healthy they
    # agree.  But if the user's local CSV is stale (between sync cycles,
    # or the user just `git pull`ed a snapshot) Supabase has the
    # freshest truth -- including real-odds pl values that the GHA
    # cron's CSV write doesn't reflect.  Fall back to CSV when Supabase
    # env vars are unset (offline / local dev) or the query errors.
    rows, source = _load_rows(season)

    target = filter_rows(rows, start_iso, end_iso, args.include_lean, args.verbose)

    print(f"P&L Calculator -- {title}")
    print(f"Source: {source}")
    print(f"Filter: {'STRONG + LEAN' if args.include_lean else 'STRONG only'}"
          f"{', verbose (incl. PASS / ungraded)' if args.verbose else ''}")
    print()

    if not target:
        print("No matching rows.")
        return 0

    # Per-row breakdown
    header = (
        f"{'Date':<11} {'Game':<10} {'Pick':<14} "
        f"{'Result':<8} {'Units':<6} {'Odds':<8} {'P&L (CSV)':<11} "
        f"{'P&L (recalc)':<13} {'Flag'}"
    )
    print(header)
    print("-" * len(header))

    wins = losses = passes = ungraded = 0
    pnl_csv_total = 0.0
    pnl_recalc_total = 0.0
    fallback_count = 0
    drift_rows: list[str] = []

    for r in sorted(target, key=lambda x: (x.get("date", ""), x.get("game_time_et", ""))):
        date = (r.get("date") or "").strip()
        away = r.get("away_team") or ""
        home = r.get("home_team") or ""
        side = (r.get("pick_side") or "").strip().upper()
        strength = (r.get("pick_strength") or "").strip().upper()
        graded = (r.get("graded_result") or "").strip().upper()
        game = f"{away}@{home}"
        pick = f"{strength} {side}".strip()

        # Stored P&L (what the CSV says)
        pl_stored_str = (r.get("profit_loss_units") or "").strip()
        try:
            pl_stored = float(pl_stored_str) if pl_stored_str else None
        except ValueError:
            pl_stored = None

        # Recomputed P&L (what the canonical helper says NOW)
        pl_recalc_str = _calc_pnl(r)
        try:
            pl_recalc = float(pl_recalc_str) if pl_recalc_str else None
        except ValueError:
            pl_recalc = None

        # Pick the odds we used (relevant side only, for display)
        if side == "NRFI":
            odds_field = (r.get("market_nrfi_odds") or "").strip()
        elif side == "YRFI":
            odds_field = (r.get("market_yrfi_odds") or "").strip()
        else:
            odds_field = ""
        is_fallback = bool(odds_field) is False and graded in ("WIN", "LOSS")
        if is_fallback:
            fallback_count += 1
        odds_disp = odds_field or ("-110*" if is_fallback else "-")

        units = (r.get("units_risked") or "").strip() or "-"

        # Drift detection
        drift = ""
        if pl_stored is not None and pl_recalc is not None:
            if abs(pl_stored - pl_recalc) > 0.001:
                drift = "DRIFT"
                drift_rows.append(
                    f"  {date} {game} {pick}: stored={pl_stored:+.3f}, "
                    f"recalc={pl_recalc:+.3f}"
                )
        elif pl_stored is not None and pl_recalc is None:
            drift = "stored-only"
        elif pl_stored is None and pl_recalc is not None:
            drift = "recalc-only"

        # Tally
        if graded == "WIN":
            wins += 1
        elif graded == "LOSS":
            losses += 1
        elif graded == "PASS":
            passes += 1
        else:
            ungraded += 1
        if pl_stored is not None:
            pnl_csv_total += pl_stored
        if pl_recalc is not None:
            pnl_recalc_total += pl_recalc

        print(
            f"{date:<11} {game:<10} {pick:<14} "
            f"{graded or '-':<8} {units:<6} {odds_disp:<8} "
            f"{fmt_pl(pl_stored):<11} {fmt_pl(pl_recalc):<13} {drift}"
        )

    # Totals
    print("-" * len(header))
    n_bets = wins + losses
    hit_rate = (wins / n_bets * 100.0) if n_bets > 0 else 0.0
    print(
        f"Record:  {wins}W / {losses}L "
        f"({hit_rate:.1f}% hit)"
        + (f"  + {passes} PASS" if passes else "")
        + (f"  + {ungraded} ungraded" if ungraded else "")
    )
    print(f"P&L (CSV stored):    {pnl_csv_total:+.3f}u")
    print(f"P&L (recomputed):    {pnl_recalc_total:+.3f}u")
    if abs(pnl_csv_total - pnl_recalc_total) > 0.001:
        print(
            f"!! DRIFT: stored total differs from recomputed by "
            f"{pnl_csv_total - pnl_recalc_total:+.3f}u"
        )

    if fallback_count:
        print(
            f"\n* {fallback_count} graded bet(s) used the -110 payout "
            f"fallback (no captured DK odds).  Real-world P&L on those "
            f"rows depends on the actual price you placed at."
        )

    if drift_rows:
        print(f"\nPer-row drift ({len(drift_rows)} row(s)):")
        for line in drift_rows:
            print(line)
        return 1   # non-zero so a CI check / Telegram bot can detect drift

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

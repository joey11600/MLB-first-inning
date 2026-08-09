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
* "STAKE DRIFT" (T8.18): a SEPARATE check, printed straight after the
  P&L one.  They are deliberately not merged.  P&L drift recomputes
  profit_loss_units *from* units_risked and so treats the stake as
  given; stake drift asks whether units_risked itself is what the
  sizing rule would produce.  A row can pass one and fail the other,
  and on 2026-08-02 DET@OAK exactly that happened -- the P&L was a
  faithful settlement of a 7u bet the rule says should have been 1u.
  See tools/stake_drift.py.
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

# T8.18 -- the stake-side sibling of the P&L consistency check.  Imported
# rather than reimplemented so pl_calc and tools/reconcile.py's I5 can
# never disagree about what the sizing rule says.
from tools import stake_drift as _stake_drift  # noqa: E402

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


# ---------------------------------------------------------------------------
# THE No.1 PICK SERIES  (--top-pick)
# ---------------------------------------------------------------------------
#
# WHY THIS LIVES HERE. CLAUDE.md: "Before stating any P&L figure to the
# user ... run tools/pl_calc.py and copy the number it prints."  The
# dashboard publishes a No.1-pick record (45-21, +81.76u at quarter-
# Kelly) that this tool could NOT reproduce, because it had no way to
# reduce the ledger to one bet a night.  A published figure the
# canonical calculator cannot check is exactly the setup that produced
# three contradictory numbers for one slate on 2026-05-05.
#
# THE RULES ARE COPIED FROM dashboard/lib/top-pick.ts, deliberately and
# explicitly, because the two must agree:
#   1. YRFI only          -- STRONG NRFI was switched off 2026-06-07
#   2. from 2026-05-26    -- when the live model weights were fit
#   3. placed AND graded  -- with a real captured price
#   4. one per night      -- best by confidence, then the better price,
#                            then the game name (top-pick-rank.ts)
#
# TWO BASES, BOTH PRINTED. `realized` is what the ledger recorded;
# `atKelly` restates every night at the published quarter-Kelly rule.
# They differ a lot (+21.86u vs +81.76u) because Kelly only went live
# 2026-07-27 and 356 of 377 placed bets were logged at a flat 1u.  Per
# the unit memory, the AT-KELLY figure is the system's record and the
# realized one is carried beside it, never as a correction.

CURRENT_SYSTEM_FROM = "2026-05-26"
# The date STRONG NRFI was switched off for losing in every band. Named here
# rather than inlined because the subscriber-facing ledger message discloses
# it -- the record is the top YRFI play of each night, and a reader is
# entitled to know which population that is.
NRFI_OFF_FROM = "2026-06-07"


def _implied(odds: float) -> float:
    return (-odds / (-odds + 100.0)) if odds < 0 else (100.0 / (odds + 100.0))


def _payout(odds: float) -> float:
    return odds / 100.0 if odds > 0 else 100.0 / -odds


def select_top_picks(rows: list[dict]) -> list[dict]:
    """One row per night: the No.1 play, under the dashboard's rule.

    RANK FIRST, THEN REQUIRE A RESULT. The order matters and it used to be
    the other way round: unsettled rows were filtered out BEFORE ranking, so
    a night whose top play was POSTPONED silently promoted the second-best
    game and counted ITS result as the No.1's.

    Measured on the live ledger, 2026-06-11: the most confident YRFI play was
    ATL@CWS (p_nrfi 0.3219) and it was postponed; the record counted CHC@COL
    (0.3543), which LOST. A game the system would never have graded that
    night contributed a loss to the published record.

    The system would have bet the postponed game. A postponement is NO
    ACTION -- not a result, and not a licence to substitute a different
    game. Such nights are now EXCLUDED and counted, so the exclusion is
    visible rather than silent (same discipline as `noEdgeUnderKelly`).

    NRFI IS STILL EXCLUDED, AND THAT IS DELIBERATE -- see the header of
    dashboard/lib/top-pick.ts. STRONG NRFI was switched off 2026-06-07 for
    losing in every band, and the operator's instruction (2026-08-03) is that
    showing those nights as the record of a system that would not place them
    is wrong. This series is therefore a CURRENT-RULES REPLAY, not a log of
    what was alerted at the time; every surface that publishes it says so.
    """
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        date = (r.get("date") or "").strip()
        if date < CURRENT_SYSTEM_FROM:
            continue
        if (r.get("pick_side") or "").strip().upper() != "YRFI":
            continue
        if (r.get("bet_placed") or "").strip().upper() != "Y":
            continue
        # Ranking needs only the probability and the price. Settlement is
        # checked AFTER the winner is chosen -- see the docstring.
        try:
            p_nrfi = float((r.get("nrfi_prob") or "").strip())
            odds = float((r.get("market_yrfi_odds") or "").strip())
        except (TypeError, ValueError):
            continue          # unpriced -- excluded, never guessed
        if odds == 0:
            continue
        r = dict(r)
        r["_rank"] = p_nrfi                      # YRFI: lower p(no run) = stronger
        r["_implied"] = _implied(odds)
        r["_odds"] = odds
        by_day.setdefault(date, []).append(r)

    out: list[dict] = []
    for date in sorted(by_day):
        night = by_day[date]
        night.sort(key=lambda x: (
            x["_rank"],
            x["_implied"],
            f"{x.get('away_team','')}@{x.get('home_team','')}",
        ))
        top = night[0]
        if (top.get("graded_result") or "").strip().upper() not in ("WIN", "LOSS"):
            continue          # the night's No.1 never settled -> no result
        try:
            float((top.get("units_risked") or "").strip())
            float((top.get("profit_loss_units") or "").strip())
        except (TypeError, ValueError):
            continue
        out.append(top)
    return out


def top_pick_summary(picks: list[dict]) -> dict:
    """Record + the two unit bases. Kelly stakes come from
    tracker.kelly_stake_units, never a local re-implementation."""
    try:
        import tracker          # already on sys.path -- see REPO_ROOT above
    except Exception:                                    # noqa: BLE001
        tracker = None                                   # type: ignore

    realized = at_kelly = at_flat = staked_kelly = 0.0
    wins = 0
    counted: list[dict] = []
    no_edge = 0
    for r in picks:
        odds = r["_odds"]
        won = (r.get("graded_result") or "").upper() == "WIN"

        # KELLY ALSO DROPS BETS, and the record must drop them too.
        # `stakeUnitsFor` returns 0 when the model has no edge at the
        # price actually paid -- today's rule would not place that bet at
        # all, so counting it in the win-loss line would credit the
        # system for a night it declines. dashboard/lib/top-pick.ts does
        # exactly this (`if (stake <= 0) { noEdgeUnderKelly++; continue }`).
        #
        # This is not academic: including them printed 47-21 against the
        # published 45-21 while the MONEY matched to the cent (+81.76u,
        # 329.00u staked) -- because those nights stake zero and so
        # contribute nothing but a win. Money agreeing while the record
        # disagrees is precisely the shape of bug this tool exists to catch.
        k = None
        if tracker is not None:
            k = tracker.kelly_stake_units(1.0 - r["_rank"], str(int(odds)))
        if not k or k <= 0:
            no_edge += 1
            continue

        counted.append(r)
        wins += 1 if won else 0
        realized += float(r.get("profit_loss_units") or 0.0)
        at_flat += _payout(odds) if won else -1.0
        staked_kelly += k
        at_kelly += (k * _payout(odds)) if won else -k

    picks = counted
    n = len(picks)
    return {
        "bets": n, "wins": wins, "losses": n - wins,
        "hit": (wins / n * 100.0) if n else 0.0,
        "realized": realized, "atKelly": at_kelly, "atFlat1u": at_flat,
        "stakedKelly": staked_kelly,
        "roiKelly": (at_kelly / staked_kelly * 100.0) if staked_kelly else 0.0,
        "noEdgeUnderKelly": no_edge,
        "from": picks[0]["date"] if picks else "", "to": picks[-1]["date"] if picks else "",
    }


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
    parser.add_argument(
        "--top-pick",
        action="store_true",
        help="Only the No.1 play of each night (the published series). "
             "Reports the record plus BOTH unit bases: realized (what the "
             "ledger booked) and at quarter-Kelly (the published rule).",
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

    # --top-pick short-circuits the normal report: it is a different
    # POPULATION (one bet a night, YRFI only, from the weights-fit date),
    # so mixing it into the per-row table would invite reading two
    # different books as one.
    if args.top_pick:
        if not args.date and not args.window:
            start_iso, end_iso = CURRENT_SYSTEM_FROM, today_et_iso()
            title = f"the No.1 series ({start_iso} to {end_iso})"
        picks = [p for p in select_top_picks(rows)
                 if start_iso <= p["date"] <= end_iso]
        s = top_pick_summary(picks)
        print(f"P&L Calculator -- THE No.1 PICK -- {title}")
        print(f"Source: {source}")
        print(f"Rules:  YRFI only, from {CURRENT_SYSTEM_FROM}, placed + graded "
              f"+ really priced, one per night (dashboard/lib/top-pick.ts)")
        print()
        if not picks:
            print("No qualifying No.1 plays in range.")
            return 0
        hdr = f"{'Date':<11} {'Game':<10} {'Odds':>7} {'Result':<7} {'Realized':>10} {'Kelly':>8}"
        print(hdr); print("-" * len(hdr))
        for p in picks:
            won = (p.get("graded_result") or "").upper() == "WIN"
            k = None
            try:
                import tracker as _t
                k = _t.kelly_stake_units(1.0 - p["_rank"], str(int(p["_odds"])))
            except Exception:                            # noqa: BLE001
                pass
            kpl = ((k * _payout(p["_odds"])) if won else -k) if k else None
            print(f"{p['date']:<11} "
                  f"{(p.get('away_team') or '')+'@'+(p.get('home_team') or ''):<10} "
                  f"{int(p['_odds']):>7} {'WIN' if won else 'LOSS':<7} "
                  f"{float(p.get('profit_loss_units') or 0):>+9.2f}u "
                  f"{(f'{kpl:+.2f}u' if kpl is not None else '-'):>8}")
        print("-" * len(hdr))
        print(f"Record:              {s['wins']}-{s['losses']}  ({s['hit']:.1f}% hit)  "
              f"over {s['bets']} nights")
        print(f"Units AT QUARTER-KELLY: {s['atKelly']:+.2f}u   "
              f"(staked {s['stakedKelly']:.2f}u, {s['roiKelly']:+.1f}% per unit risked)")
        print(f"Units at a flat 1u:     {s['atFlat1u']:+.2f}u")
        print(f"Units AS REALIZED:      {s['realized']:+.2f}u   "
              f"(what the ledger booked; a flat unit was staked until "
              f"quarter-Kelly went live 2026-07-27)")
        print()
        print("All three are exact sums on a FIXED unit basis, so each means "
              "the same on any bankroll.  1 unit = 1% of bank.")
        return 0

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

    # ---- STAKE DRIFT (T8.18) -------------------------------------------
    # A SEPARATE check from the P&L drift above, and it must stay separate.
    # The block above recomputes profit_loss_units *from* units_risked, so
    # it takes the stake as given and can only ever catch a settlement
    # error.  This one asks the question that block cannot: is
    # units_risked itself what the sizing rule would have produced from
    # the row's own probability and price?
    #
    # 2026-08-02 DET@OAK is the worked example -- a flawless P&L on a 7u
    # bet where the rule said 1u.  Clean above, six units wrong here.
    #
    # It runs on the SAME rows pl_calc just loaded (Supabase-first), not a
    # fresh read, so the two sections can never be reporting on different
    # copies of the ledger.  Bounded to the requested window, but never
    # earlier than the era floor.
    print()
    sd_since = max(start_iso, _stake_drift.STAKE_DRIFT_ERA_FLOOR)
    stake_rep = _stake_drift.check_rows(
        rows, season=season, since=sd_since, until=end_iso,
        exempt_keys=_stake_drift.load_exemptions(),
    )
    print(f"STAKE DRIFT ({sd_since} to {end_iso}) -- does units_risked still "
          f"match the sizing rule?")
    for line in _stake_drift.render(stake_rep):
        print(line)

    # One exit code for both checks: a CI job or Telegram watcher only
    # needs to know "the ledger disagrees with itself somewhere".
    return 1 if (drift_rows or stake_rep.violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())

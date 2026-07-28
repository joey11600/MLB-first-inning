#!/usr/bin/env python3
"""
tools/kelly_season_backfill.py -- what the whole 2026 season WOULD have
looked like under Kelly staking.

READ-ONLY BY DESIGN.  This never writes to picks_2026.csv.  The ledger
records what was ACTUALLY bet (flat 1u); overwriting units_risked /
profit_loss_units with counterfactual Kelly stakes would destroy the
record of the real position and is exactly the failure mode of the
2026-05-05 backfill-mirror incident.  Counterfactual belongs in a
report, not in the ledger.

WHAT IT SIMULATES
  Every graded, placed 2026 bet, replayed day by day, sizing with the
  SHIPPED `tracker.kelly_stake_units` rather than a local copy of the
  formula -- so this measures the code that would actually run.

  Same-day bets are all sized off the bankroll as it stood that MORNING;
  you cannot compound intraday across games that run concurrently.

APRIL IS EXCLUDED FROM THE HEADLINE.  April captured a real DK price on
only 6 of 176 placed bets (3%); the rest settled at the -110 fallback.
Kelly cannot size a bet whose price we never saw, and pretending we
could is how April's +39u was manufactured in the first place.  April is
reported separately and clearly marked.

Usage:
    python tools/kelly_season_backfill.py
    python tools/kelly_season_backfill.py --bankroll 40   # sensitivity
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from tools.kelly_backtest import load_bets, decimal_b, implied  # noqa: E402

GATE = 0.64          # shipped _LR_STRONG_YRFI_P = 0.36  <=>  YRFI >= 0.64


def run(bets, frac, start_bank, gate=None, top_n=None, rank="edge"):
    """Day-by-day bankroll simulation.  frac=None means flat 1u."""
    sel = [b for b in bets if gate is None or b["p_claimed"] >= gate]
    by_day = defaultdict(list)
    for b in sel:
        by_day[b["date"]].append(b)

    bank = peak = start_bank
    maxdd = 0.0
    top_stake = 0.0
    n = w = skipped = 0
    curve = []
    streak = worst_streak = 0

    orig = tracker.KELLY_FRACTION
    if frac is not None:
        tracker.KELLY_FRACTION = frac
    try:
        for day in sorted(by_day):
            todays = by_day[day]
            if top_n is not None:
                key = (lambda b: -(b["p_claimed"] - implied(b["odds"]))) \
                    if rank == "edge" else (lambda b: -b["p_claimed"])
                todays = sorted(todays, key=key)[:top_n]
            morning = bank
            tracker._bankroll_cache = morning
            pnl = 0.0
            for b in todays:
                if frac is None:
                    stake = 1.0
                else:
                    stake = tracker.kelly_stake_units(
                        b["p_claimed"], str(int(b["odds"]))) or 0.0
                    if stake <= 0:
                        skipped += 1
                        continue
                n += 1
                top_stake = max(top_stake, stake / morning if morning else 0)
                if b["win"]:
                    w += 1
                    pnl += stake * decimal_b(b["odds"])
                    streak = 0
                else:
                    pnl -= stake
                    streak += 1
                    worst_streak = max(worst_streak, streak)
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                maxdd = max(maxdd, (peak - bank) / peak)
            curve.append((day, bank))
            if bank <= 0:
                break
    finally:
        tracker.KELLY_FRACTION = orig

    return {
        "n": n, "w": w, "final": bank, "profit": bank - start_bank,
        "maxdd": 100 * maxdd, "top_stake": 100 * top_stake,
        "skipped": skipped, "curve": curve, "worst_streak": worst_streak,
        "growth": (bank / start_bank - 1) * 100,
    }


def line(label, r):
    hit = 100 * r["w"] / r["n"] if r["n"] else 0
    print(f"  {label:<38}{r['n']:>5}{hit:>7.1f}%{r['final']:>10.2f}u"
          f"{r['profit']:>+10.2f}u{r['growth']:>+9.1f}%{r['maxdd']:>8.1f}%"
          f"{r['top_stake']:>9.1f}%{r['worst_streak']:>6}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bankroll", type=float, default=100.0,
                    help="starting bankroll in units (default 100 => 1u = 1%%)")
    args = ap.parse_args()
    BANK = args.bankroll

    allb = load_bets()
    apr = [b for b in allb if b["date"] < "2026-05-01"]
    bets = [b for b in allb if b["date"] >= "2026-05-01"]

    print("=" * 104)
    print("  KELLY SEASON BACKFILL -- counterfactual only, nothing written to the ledger")
    print("=" * 104)
    print(f"  Starting bankroll assumed: {BANK:.0f} units "
          f"(so 1 unit = {100/BANK:.2f}% of bank)")
    print(f"  Bets with a real captured DK price: {len(allb)}")
    print(f"    April (excluded from headline -- only 3% of April bets ever "
          f"had a captured price): {len(apr)}")
    print(f"    May 1 onward (the honest window):  {len(bets)}")
    days = sorted({b['date'] for b in bets})
    print(f"    spanning {len(days)} betting days, "
          f"{bets[0]['date']} to {bets[-1]['date']}")

    print("\n" + "=" * 104)
    print("  A. WHAT ACTUALLY HAPPENED vs KELLY -- on the slate you were actually betting")
    print("     (old gate: every STRONG pick. This is the apples-to-apples 'if we'd had")
    print("      Kelly on the whole time, changing nothing else' answer.)")
    print("=" * 104)
    print(f"  {'staking':<38}{'bets':>5}{'hit%':>7}{'final':>10}{'profit':>11}"
          f"{'growth':>9}{'maxDD':>8}{'top bet':>9}{'LmaxL':>6}")
    base = run(bets, None, BANK)
    line("flat 1u  (WHAT YOU ACTUALLY DID)", base)
    for f, lbl in ((0.125, "1/8 Kelly"), (0.25, "1/4 Kelly (shipped default)"),
                   (0.5, "1/2 Kelly"), (1.0, "full Kelly")):
        line(lbl, run(bets, f, BANK))

    print("\n" + "=" * 104)
    print(f"  B. KELLY ON TOP OF THE NEW GATE (model p >= {GATE}, shipped 2026-07-27)")
    print("=" * 104)
    print(f"  {'staking':<38}{'bets':>5}{'hit%':>7}{'final':>10}{'profit':>11}"
          f"{'growth':>9}{'maxDD':>8}{'top bet':>9}{'LmaxL':>6}")
    gbase = run(bets, None, BANK, gate=GATE)
    line("flat 1u", gbase)
    for f, lbl in ((0.125, "1/8 Kelly"), (0.25, "1/4 Kelly (shipped default)"),
                   (0.5, "1/2 Kelly"), (1.0, "full Kelly")):
        line(lbl, run(bets, f, BANK, gate=GATE))

    print("\n" + "=" * 104)
    print("  C. KELLY + ONE-PICK-PER-DAY (both selectivity layers stacked)")
    print("=" * 104)
    print(f"  {'staking':<38}{'bets':>5}{'hit%':>7}{'final':>10}{'profit':>11}"
          f"{'growth':>9}{'maxDD':>8}{'top bet':>9}{'LmaxL':>6}")
    for tn in (1, 2):
        for f, lbl in ((None, "flat 1u"), (0.25, "1/4 Kelly")):
            line(f"top {tn}/day by edge + {lbl}",
                 run(bets, f, BANK, top_n=tn, rank="edge"))

    print("\n" + "=" * 104)
    print("  D. MONTH BY MONTH -- 1/4 Kelly on the new gate vs flat 1u")
    print("=" * 104)
    print(f"  {'month':<10}{'bets':>6}{'flat 1u':>12}{'1/4 Kelly':>12}{'difference':>14}")
    bym = defaultdict(list)
    for b in bets:
        bym[b["date"][:7]].append(b)
    for m in sorted(bym):
        f_ = run(bym[m], None, BANK, gate=GATE)
        k_ = run(bym[m], 0.25, BANK, gate=GATE)
        print(f"  {m:<10}{k_['n']:>6}{f_['profit']:>+11.2f}u{k_['profit']:>+11.2f}u"
              f"{k_['profit']-f_['profit']:>+13.2f}u")

    print("\n" + "=" * 104)
    print("  E. BANKROLL SENSITIVITY -- 1/4 Kelly on the new gate")
    print("     Kelly is a PERCENTAGE of bankroll. If your real bank is smaller than")
    print("     assumed, the same % is a bigger share of your money and the swings hurt more.")
    print("=" * 104)
    print(f"  {'assumed bank':<16}{'largest single bet':>22}{'profit':>12}{'growth':>10}")
    for bk in (25, 50, 100, 200):
        r = run(bets, 0.25, bk, gate=GATE)
        print(f"  {bk:>4.0f} units{'':<6}{r['top_stake']*bk/100:>17.2f}u"
              f"{r['profit']:>+11.2f}u{r['growth']:>+9.1f}%")

    print("\n" + "=" * 104)
    print("  APRIL, reported separately and NOT included above")
    print("=" * 104)
    print(f"  {len(apr)} April bets had a real captured price, out of ~176 placed.")
    if apr:
        a = run(apr, 0.25, BANK)
        print(f"  1/4 Kelly on just those {a['n']}: {a['profit']:+.2f}u")
    print("  The other ~170 settled at the flat -110 fallback, which is a placeholder,")
    print("  not a price. Kelly cannot size a bet whose price was never observed, and")
    print("  simulating one would recreate the artefact that made April look like +39u.")

    print("\n" + "=" * 104)
    print("  HOW TO READ THIS")
    print("=" * 104)
    print("  * 'growth' is the honest Kelly metric -- Kelly compounds, so its profit")
    print("    depends on the starting bank in a way flat staking's does not.")
    print("  * 'LmaxL' is the longest run of consecutive losing bets. That, not the")
    print("    profit column, is what you actually have to sit through.")
    print("  * Sections B and C use a gate chosen with hindsight over this same season.")
    print("    The walk-forward version of that gate returned roughly a third as much.")
    print("    Treat these as an upper bound, not a forecast.")


if __name__ == "__main__":
    main()

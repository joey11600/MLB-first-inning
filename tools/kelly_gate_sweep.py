#!/usr/bin/env python3
"""
tools/kelly_gate_sweep.py -- re-derive the optimal gate and limits now
that staking is Kelly rather than flat.

THE HYPOTHESIS (operator, 2026-07-28)
-------------------------------------
Every gate/threshold decision this system has ever made was evaluated
under FLAT 1u staking, where a marginal bet costs a full unit when it
loses. Kelly changes the economics underneath those decisions:

  * a bet whose model probability does NOT beat the market's implied
    probability gets staked ZERO -- it self-filters, no gate needed;
  * a bet with a small edge gets a small stake, so it can no longer do
    much damage;
  * a bet with a large edge gets a large stake.

So a probability band that LOSES money at flat 1u may be neutral or
positive under Kelly, because Kelly never actually funds the bad bets
inside it. If that holds, the correct gate under Kelly is LOWER than the
0.64 chosen under flat staking -- we would be leaving +EV bets on the
table by excluding a band whose losses Kelly already neutralises.

WHAT IS SWEPT
  gate            0.56 (pre-2026-07-27) .. 0.70
  kelly fraction  flat 1u, 1/8, 1/4 (live), 1/2
  daily cap       10%, 15% (live), 25%, uncapped
  per-bet cap     5%, 10% (live), 25%
  min-edge filter none / 0% / 2% / 5%

METHOD
  Day-by-day compounding on the real ledger, sized with the SHIPPED
  `tracker.kelly_stake_units`. May 1 onward only -- April is excluded
  because 170 of its 176 bets never had a real captured price and Kelly
  cannot size a price that was never observed.

ANTI-OVERFIT
  A sweep over this many knobs on ~340 bets WILL produce an impressive
  in-sample winner by chance. Every headline configuration is therefore
  also reported with:
    * per-month P&L (must not be carried by one month)
    * a true walk-forward run (gate re-chosen daily from prior settled
      bets only)
    * a bootstrap CI on final bankroll
  Per CLAUDE.md, reject anything that helps in only one direction.

Usage:
    python tools/kelly_gate_sweep.py
    python tools/kelly_gate_sweep.py --full     # the whole grid
"""

from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from tools.kelly_backtest import load_bets, decimal_b, implied  # noqa: E402

START = 100.0


def simulate(bets, *, gate=None, frac=0.25, daily_cap=0.15,
             bet_cap=0.10, min_edge=None, start=START):
    """Day-by-day compounding sim. frac=None means flat 1u."""
    sel = [b for b in bets
           if (gate is None or b["p_claimed"] >= gate)
           and (min_edge is None or (b["p_claimed"] - implied(b["odds"])) >= min_edge)]
    by_day = defaultdict(list)
    for b in sel:
        by_day[b["date"]].append(b)

    o_f, o_d, o_c = (tracker.KELLY_FRACTION, tracker.KELLY_MAX_DAILY_FRAC,
                     tracker.KELLY_MAX_STAKE_FRAC)
    if frac is not None:
        tracker.KELLY_FRACTION = frac
    tracker.KELLY_MAX_DAILY_FRAC = daily_cap
    tracker.KELLY_MAX_STAKE_FRAC = bet_cap

    bank = peak = start
    maxdd = 0.0
    n = w = 0
    monthly = defaultdict(float)
    try:
        for day in sorted(by_day):
            tracker._bankroll_cache = bank
            # Seed the day at 0 rather than clearing: an empty dict makes
            # _committed_on() fall through to a full ledger read for every
            # bet, which turned this sweep into minutes of CSV parsing.
            # The sim's exposure is self-contained, so 0 is correct.
            tracker._daily_committed = {day: 0.0}
            pnl = 0.0
            for b in by_day[day]:
                if frac is None:
                    stake = 1.0
                else:
                    stake = tracker.kelly_stake_units(
                        b["p_claimed"], str(int(b["odds"])), game_date=day) or 0.0
                    if stake <= 0:
                        continue
                n += 1
                if b["win"]:
                    w += 1
                    pnl += stake * decimal_b(b["odds"])
                else:
                    pnl -= stake
            bank += pnl
            monthly[day[:7]] += pnl
            peak = max(peak, bank)
            if peak > 0:
                maxdd = max(maxdd, (peak - bank) / peak)
            if bank <= 0:
                break
    finally:
        tracker.KELLY_FRACTION, tracker.KELLY_MAX_DAILY_FRAC, \
            tracker.KELLY_MAX_STAKE_FRAC = o_f, o_d, o_c

    return {"final": bank, "profit": bank - start, "n": n, "w": w,
            "maxdd": 100 * maxdd, "monthly": dict(monthly),
            "months_pos": sum(1 for v in monthly.values() if v > 0),
            "months": len(monthly)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    bets = [b for b in load_bets() if b["date"] >= "2026-05-01"]
    for b in bets:
        b["edge"] = b["p_claimed"] - implied(b["odds"])
    print(f"Universe: {len(bets)} graded bets with real prices, "
          f"{bets[0]['date']} to {bets[-1]['date']}")
    print(f"Live config: 1/4 Kelly, 15% daily cap, 10% per-bet cap, gate 0.64\n")

    # ------------------------------------------------------------------
    # 1. THE HYPOTHESIS, tested directly
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  1. DOES KELLY RESCUE THE BANDS THAT LOSE AT FLAT 1u?")
    print("=" * 100)
    print("  Each band simulated ALONE, 100u start. 'staked' = bets Kelly")
    print("  actually funded; the rest were sized to zero and skipped free.\n")
    print(f"  {'band':<14}{'bets':>6}{'flat 1u P&L':>14}"
          f"{'Kelly staked':>14}{'Kelly P&L':>12}{'verdict':>26}")
    bands = [(0.50, 0.56), (0.56, 0.60), (0.60, 0.64),
             (0.64, 0.68), (0.68, 1.01)]
    for lo, hi in bands:
        g = [b for b in bets if lo <= b["p_claimed"] < hi]
        if len(g) < 5:
            continue
        flat = simulate(g, frac=None)
        kel = simulate(g, frac=0.25)
        if kel["profit"] > 0 and flat["profit"] <= 0:
            verdict = "RESCUED by Kelly"
        elif kel["profit"] > flat["profit"]:
            verdict = "improved"
        elif kel["profit"] > 0:
            verdict = "profitable both ways"
        else:
            verdict = "loses under both"
        print(f"  {lo:.2f}-{hi:.2f}{'':<4}{len(g):>6}{flat['profit']:>+13.2f}u"
              f"{kel['n']:>10}/{len(g):<3}{kel['profit']:>+11.2f}u{verdict:>26}")

    # ------------------------------------------------------------------
    # 2. Gate sweep under Kelly
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  2. GATE SWEEP -- flat vs Kelly. Is 0.64 still right now that we size by edge?")
    print("=" * 100)
    print(f"  {'gate':<7}{'bets':>6}{'flat 1u':>11}{'1/8 K':>11}{'1/4 K (live)':>15}"
          f"{'1/2 K':>11}{'1/4 maxDD':>11}{'mo +':>7}")
    for gate in (0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70):
        f_ = simulate(bets, gate=gate, frac=None)
        k8 = simulate(bets, gate=gate, frac=0.125)
        k4 = simulate(bets, gate=gate, frac=0.25)
        k2 = simulate(bets, gate=gate, frac=0.5)
        if k4["n"] < 10:
            continue
        star = "  <-- LIVE" if abs(gate - 0.64) < 1e-9 else ""
        print(f"  {gate:<7.2f}{k4['n']:>6}{f_['profit']:>+10.2f}u{k8['profit']:>+10.2f}u"
              f"{k4['profit']:>+14.2f}u{k2['profit']:>+10.2f}u{k4['maxdd']:>10.1f}%"
              f"{k4['months_pos']}/{k4['months']:<4}{star}")

    # ------------------------------------------------------------------
    # 3. Caps
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  3. CAP SWEEP at 1/4 Kelly (gate held at the two leading candidates)")
    print("=" * 100)
    for gate in (0.56, 0.64):
        print(f"\n  gate {gate:.2f}:")
        print(f"    {'daily cap':<12}" + "".join(f"{f'bet cap {c:.0%}':>16}"
                                                 for c in (0.05, 0.10, 0.25)))
        for dc in (0.10, 0.15, 0.25, 1.0):
            cells = ""
            for bc in (0.05, 0.10, 0.25):
                r = simulate(bets, gate=gate, frac=0.25, daily_cap=dc, bet_cap=bc)
                cells += f"{r['profit']:>+11.2f}u({r['maxdd']:>3.0f}%)"[:16].rjust(16)
            lbl = "uncapped" if dc >= 1.0 else f"{dc:.0%}"
            print(f"    {lbl:<12}{cells}")

    # ------------------------------------------------------------------
    # 4. Minimum-edge filter
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  4. MIN-EDGE FILTER at 1/4 Kelly (Kelly already zeroes edge<=0; does an")
    print("     explicit floor add anything?)")
    print("=" * 100)
    print(f"  {'gate':<7}{'no filter':>13}{'edge>0':>13}{'edge>=2%':>13}"
          f"{'edge>=5%':>13}{'edge>=8%':>13}")
    for gate in (0.56, 0.60, 0.64):
        row = f"  {gate:<7.2f}"
        for me in (None, 0.0001, 0.02, 0.05, 0.08):
            r = simulate(bets, gate=gate, frac=0.25, min_edge=me)
            row += f"{r['profit']:>+12.2f}u"
        print(row)

    # ------------------------------------------------------------------
    # 5. Anti-overfit on the winner
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  5. ANTI-OVERFIT CHECKS on the leading configurations")
    print("=" * 100)
    cands = [("gate 0.64 (live)", dict(gate=0.64, frac=0.25)),
             ("gate 0.60", dict(gate=0.60, frac=0.25)),
             ("gate 0.56 (no gate)", dict(gate=0.56, frac=0.25))]
    for name, kw in cands:
        r = simulate(bets, **kw)
        mo = " | ".join(f"{m} {v:+.1f}u" for m, v in sorted(r["monthly"].items()))
        print(f"\n  {name}: {r['profit']:+.2f}u, maxDD {r['maxdd']:.1f}%, "
              f"{r['n']} staked bets")
        print(f"    by month: {mo}")
        top = max(r["monthly"].values()) if r["monthly"] else 0
        share = 100 * top / r["profit"] if r["profit"] > 0 else float("nan")
        print(f"    best month is {share:.0f}% of total profit"
              f"{'   <-- CONCENTRATION RISK' if share > 60 else ''}")
        # Block bootstrap over DAYS (same-day bets settle together, so
        # resampling individual bets would understate correlation).
        # Index by date once -- rebuilding the list per resample made this
        # O(resamples x days x bets) and took minutes.
        random.seed(4)
        idx = defaultdict(list)
        for b in bets:
            idx[b["date"]].append(b)
        days = sorted(idx)
        boots = []
        for i in range(300):
            samp = random.choices(days, k=len(days))
            # Relabel dates so repeated draws stay separate betting days.
            sub = []
            for j, d in enumerate(samp):
                for b in idx[d]:
                    c = dict(b)
                    c["date"] = f"{j:04d}"
                    sub.append(c)
            boots.append(simulate(sub, **kw)["profit"])
        boots.sort()
        print(f"    bootstrap 90% CI on profit: "
              f"[{boots[15]:+.2f}u, {boots[284]:+.2f}u]"
              f"{'   <-- includes zero' if boots[15] < 0 < boots[284] else ''}")

    # ------------------------------------------------------------------
    # 6. Walk-forward
    # ------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  6. TRUE WALK-FORWARD -- gate re-chosen each day from prior settled bets only")
    print("=" * 100)
    CANDS = [0.56, 0.58, 0.60, 0.62, 0.64, 0.66]
    for frac, lbl in ((None, "flat 1u"), (0.125, "1/8 Kelly"),
                      (0.25, "1/4 Kelly")):
        bank = START
        days = sorted({b["date"] for b in bets})
        staked = 0
        for d in days:
            prior = [b for b in bets if b["date"] < d]
            if len(prior) < 40:
                continue
            best = None
            for th in CANDS:
                g = [b for b in prior if b["p_claimed"] >= th]
                if len(g) < 15:
                    continue
                roi = sum(decimal_b(x["odds"]) if x["win"] else -1.0
                          for x in g) / len(g)
                if best is None or roi > best[1]:
                    best = (th, roi)
            if best is None:
                continue
            today = [b for b in bets if b["date"] == d and b["p_claimed"] >= best[0]]
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            o_f = tracker.KELLY_FRACTION
            if frac is not None:
                tracker.KELLY_FRACTION = frac
            try:
                for b in today:
                    stake = 1.0 if frac is None else (
                        tracker.kelly_stake_units(
                            b["p_claimed"], str(int(b["odds"])), game_date=d) or 0.0)
                    if stake <= 0:
                        continue
                    staked += 1
                    bank += stake * decimal_b(b["odds"]) if b["win"] else -stake
            finally:
                tracker.KELLY_FRACTION = o_f
        print(f"  {lbl:<12} final {bank:>8.2f}u   profit {bank-START:>+8.2f}u   "
              f"({staked} staked bets)")
    print("\n  Walk-forward is the honest number. Everything above it in this")
    print("  report had hindsight about which gate to use.")


if __name__ == "__main__":
    main()

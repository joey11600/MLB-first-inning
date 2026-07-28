#!/usr/bin/env python3
"""
tools/season_record.py -- the complete 2026 season record under the
CURRENT production stack. Every graded game considered, nothing dropped.

PRICE POLICY, stated once and applied everywhere below:
    404 of 1531 graded games never had a DraftKings price captured, and
    they skew heavily to April. Two bad options and one honest one:
      * drop them        -> biases the record toward the second half
      * fill at -110     -> the silent fallback that inflated the season
                            total by ~15u in the first place
      * fill at -125     -> an EXPLICIT, LABELLED assumption
    This report fills at -125 and marks every affected bet, so you can
    always see how much of a number is assumption. -110/-140 sensitivity
    is printed at the end; if a verdict flips between them, it is an
    artefact of the fill rather than a property of the system.

WHAT IS BEING REPLAYED
    LR weights   data/lr_t1.json + lr_b1.json      (fit 2026-05-26)
    calibrator   WALK-FORWARD -- refit at each date from strictly PRIOR
                 games only, so no game is scored by a curve that has
                 seen it. This is the defensible version; the deployed
                 calibrator was fit on 2025+2026 and would be scoring
                 its own training data.
    gate         p_nrfi < _LR_STRONG_YRFI_P (read live)
    NRFI         NOT bet -- disabled in production (_LR_STRONG_NRFI_P
                 = 1.01). Reported separately for visibility.
    staking      quarter Kelly, per-bet and same-day caps, read live.

Usage:
    python tools/season_record.py
    python tools/season_record.py --games      # every bet, one per line
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
import mlb_first_inning_predictor as P  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402

START = 100.0
FILL = -125.0


def build(rows, probs, fill):
    bets = select(rows, probs, side="YRFI", gate=P._LR_STRONG_YRFI_P, fill=fill)
    real = {(r["date"], r["away"], r["home"]) for r in rows if r["yrfi_odds"] is not None}
    # tag which bets used the fill
    bykey = {}
    for r in rows:
        bykey.setdefault(r["date"], []).append(r)
    for b in bets:
        b["assumed"] = abs(b["odds"] - fill) < 1e-9 if fill is not None else False
    return bets


def simulate(bets, frac=0.25, start=START):
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    curve = []
    staked = []
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])), game_date=d) or 0.0
                b["stake"] = s
                if s <= 0:
                    continue
                staked.append(b)
                pnl += s * payout(b["odds"]) if b["win"] else -s
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
            curve.append((d, bank))
    finally:
        tracker.KELLY_FRACTION = o
    return bank, 100 * mdd, curve, staked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", action="store_true")
    args = ap.parse_args()

    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    bets = build(rows, wf, FILL)
    bank, mdd, curve, staked = simulate(bets)

    n = len(staked)
    w = sum(1 for b in staked if b["win"])
    flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in staked)
    need = st.mean([implied(b["odds"]) for b in staked])
    assumed = sum(1 for b in staked if b["assumed"])
    days = sorted({r["date"] for r in rows})
    betdays = sorted({b["date"] for b in staked})

    print("=" * 100)
    print("  2026 SEASON RECORD -- current model + strategy, walk-forward")
    print("=" * 100)
    print(f"  gate             p_nrfi < {P._LR_STRONG_YRFI_P}")
    print(f"  staking          {tracker.KELLY_FRACTION:.2f} Kelly, "
          f"{tracker.KELLY_MAX_STAKE_FRAC:.0%}/bet, {tracker.KELLY_MAX_DAILY_FRAC:.0%}/day")
    print(f"  starting bank    {START:.0f}u")
    print(f"  missing prices   filled at {FILL:.0f}")
    print(f"  graded games     {len(rows)}   slate days {len(days)}")
    print()
    print(f"  RECORD           {w}W - {n-w}L   ({100*w/n:.1f}%)")
    print(f"  break-even need  {100*need:.1f}%   ->  edge {100*(w/n - need):+.1f} pts")
    print(f"  bets placed      {n} over {len(betdays)} days "
          f"({len(days)-len(betdays)} days with no bet)")
    print(f"  of those, priced by assumption: {assumed} ({100*assumed/n:.0f}%)")
    print()
    print(f"  FLAT 1u PROFIT   {flat:+.2f}u        (this is the EDGE)")
    print(f"  KELLY BANKROLL   {START:.0f}u -> {bank:.2f}u   =  {bank-START:+.2f}u"
          f"   ({100*(bank/START-1):+.1f}%)")
    print(f"  max drawdown     {mdd:.1f}%")
    if staked:
        ss = sorted(b["stake"] for b in staked)
        print(f"  stakes           min {ss[0]:.2f}u  median {ss[len(ss)//2]:.2f}u  max {ss[-1]:.2f}u")

    print("\n" + "-" * 100)
    print("  MONTH BY MONTH")
    print("-" * 100)
    print(f"  {'month':<9}{'bets':>6}{'W':>5}{'L':>5}{'hit%':>8}{'flat':>10}"
          f"{'bank end':>12}{'assumed':>9}")
    bym = defaultdict(list)
    for b in staked:
        bym[b["date"][:7]].append(b)
    bank_at = dict(curve)
    for m in sorted(bym):
        g = bym[m]
        mw = sum(1 for b in g if b["win"])
        mf = sum(payout(b["odds"]) if b["win"] else -1.0 for b in g)
        last = max(b["date"] for b in g)
        ma = sum(1 for b in g if b["assumed"])
        print(f"  {m:<9}{len(g):>6}{mw:>5}{len(g)-mw:>5}{100*mw/len(g):>7.1f}%"
              f"{mf:>+9.2f}u{bank_at.get(last, float('nan')):>11.2f}u{ma:>9}")

    print("\n" + "-" * 100)
    print("  PRICE-ASSUMPTION SENSITIVITY (does the verdict depend on the fill?)")
    print("-" * 100)
    print(f"  {'fill':<22}{'bets':>6}{'hit%':>8}{'flat':>10}{'Kelly bank':>13}{'maxDD':>8}")
    for f_, lbl in ((None, "real prices only"), (-110, "-110"), (-125, "-125 (used above)"),
                    (-140, "-140")):
        bb = build(rows, wf, f_)
        bk, dd, _, sk = simulate(bb)
        if not sk:
            continue
        ww = sum(1 for b in sk if b["win"])
        ff = sum(payout(b["odds"]) if b["win"] else -1.0 for b in sk)
        print(f"  {lbl:<22}{len(sk):>6}{100*ww/len(sk):>7.1f}%{ff:>+9.2f}u"
              f"{bk:>12.2f}u{dd:>7.1f}%")

    print("\n" + "-" * 100)
    print("  NRFI -- predicted but NOT bet (disabled live)")
    print("-" * 100)
    for g in (0.58, 0.60, 0.62):
        nb = select(rows, wf, side="NRFI", gate=g, fill=FILL)
        if not nb:
            continue
        nw = sum(1 for b in nb if b["win"])
        nf = sum(payout(b["odds"]) if b["win"] else -1.0 for b in nb)
        na = sum(1 for b in nb if abs(b["odds"] - FILL) < 1e-9)
        print(f"  p_nrfi >= {g:.2f}: {len(nb):>4} games  {nw}W-{len(nb)-nw}L "
              f"({100*nw/len(nb):.1f}%)  flat {nf:+.2f}u  "
              f"[{na} of {len(nb)} priced by assumption]")

    if args.games:
        print("\n" + "-" * 100)
        print("  EVERY STAKED BET")
        print("-" * 100)
        print(f"  {'date':<12}{'p(YRFI)':>9}{'odds':>7}{'stake':>8}{'result':>8}{'P&L':>9}  src")
        for b in sorted(staked, key=lambda x: x["date"]):
            pl = b["stake"] * payout(b["odds"]) if b["win"] else -b["stake"]
            print(f"  {b['date']:<12}{b['p']:>9.3f}{b['odds']:>7.0f}{b['stake']:>7.2f}u"
                  f"{'WIN' if b['win'] else 'LOSS':>8}{pl:>+8.2f}u  "
                  f"{'ASSUMED' if b['assumed'] else 'real'}")

    print("\n" + "=" * 100)
    print("  Flat 1u is the edge. The Kelly line is that same edge levered, and it")
    print("  is real only while the hit rate holds -- it unwinds at the same speed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

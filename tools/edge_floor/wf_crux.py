#!/usr/bin/env python3
"""
tools/edge_floor/wf_crux.py -- the two questions everything else hangs on.

  1. HOW MUCH MONEY IS ACTUALLY AT STAKE IN THE TAIL A FLOOR WOULD CUT?
     Kelly stakes proportionally to edge, so a thin-edge bet is already
     a small bet. If the sub-floor tail is 2% of staked volume, a floor
     cannot move the season by more than roughly 2% of turnover no
     matter how badly those bets do -- and any larger number in a
     backtest is the compounding path, not the rule.

  2. DOES THE PRICE TERM CARRY INDEPENDENT INFORMATION?
     edge = p_model - implied(price). We already gate on p_model. Edge
     is only a NEW filter to the extent implied(price) predicts the
     outcome after p_model is accounted for. Tested with a logistic fit
     (win ~ p_model + implied) and with a 2x2 median split, which is
     assumption-free.

Usage:  python tools/edge_floor/wf_crux.py
"""

from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tracker  # noqa: E402
from tools.edge_floor.wf_common import (  # noqa: E402
    START, universe, live_bets, by_day, kelly_run, flat_stats, implied, payout)


def hdr(t):
    print("\n" + "=" * 96)
    print("  " + t)
    print("=" * 96)


def stakes_in_incumbent(bets, frac=0.25):
    """Replay the incumbent and record the stake each bet actually got."""
    old_f, old_b = tracker.KELLY_FRACTION, tracker._bankroll_cache
    tracker.KELLY_FRACTION = frac
    bank = START
    try:
        for i, (d, dd) in enumerate(by_day(bets)):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {f"{d}#{i}": 0.0}
            pnl = 0.0
            for b in dd:
                s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])),
                                              game_date=f"{d}#{i}") or 0.0
                b["stake"] = s
                if s > 0:
                    pnl += s * payout(b["odds"]) if b["win"] else -s
            bank += pnl
    finally:
        tracker.KELLY_FRACTION = old_f
        tracker._bankroll_cache = old_b
    return bets


def logistic(X, y, iters=200):
    """Plain IRLS logistic regression, with a ridge nudge for stability."""
    X = np.asarray(X, float)
    X = np.column_stack([np.ones(len(X)), X])
    y = np.asarray(y, float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ b))
        W = np.clip(p * (1 - p), 1e-6, None)
        H = X.T @ (X * W[:, None]) + 1e-4 * np.eye(X.shape[1])
        g = X.T @ (y - p) - 1e-4 * b
        step = np.linalg.solve(H, g)
        b += step
        if np.max(np.abs(step)) < 1e-9:
            break
    p = 1 / (1 + np.exp(-X @ b))
    W = np.clip(p * (1 - p), 1e-6, None)
    cov = np.linalg.inv(X.T @ (X * W[:, None]) + 1e-4 * np.eye(X.shape[1]))
    se = np.sqrt(np.diag(cov))
    return b, se


def main():
    rows, ins, wf = universe()
    bets = live_bets(rows, wf)
    stakes_in_incumbent(bets)

    hdr("1.  WHAT KELLY ALREADY DOES TO THE THIN TAIL")
    print(f"  n={len(bets)} live-rule bets, walk-forward probabilities, real DK prices.")
    print(f"\n  {'edge band':<16}{'bets':>6}{'hit%':>7}{'mean stake':>12}"
          f"{'total staked':>14}{'% of volume':>13}{'flat u':>9}{'Kelly u':>10}")
    tot = sum(b["stake"] for b in bets)
    bands = [(-9, 0.00), (0.00, 0.02), (0.02, 0.04), (0.04, 0.06),
             (0.06, 0.08), (0.08, 0.12), (0.12, 9)]
    for lo, hi in bands:
        s = [b for b in bets if lo <= b["edge"] < hi]
        if not s:
            continue
        v = sum(b["stake"] for b in s)
        fs = flat_stats(s)
        kp = sum(b["stake"] * payout(b["odds"]) if b["win"] else -b["stake"] for b in s)
        lbl = (f"< 0.00" if lo < 0 else f">= {lo:.2f}" if hi > 1 else f"{lo:.2f}-{hi:.2f}")
        print(f"  {lbl:<16}{fs['bets']:>6}{fs['hit']:>7.1f}{v/max(len(s),1):>12.2f}"
              f"{v:>13.2f}u{100*v/tot:>12.1f}%{fs['pl']:>+8.2f}u{kp:>+9.2f}u")
    print(f"\n  TOTAL staked across the season: {tot:.2f}u "
          f"(bank started at {START:.0f}u and compounded)")
    cut = [b for b in bets if b["edge"] < 0.04]
    print(f"  Bets below edge 0.04: {len(cut)} of {len(bets)} tickets "
          f"({100*len(cut)/len(bets):.0f}%) but "
          f"{100*sum(b['stake'] for b in cut)/tot:.1f}% of money at risk.")
    zero = [b for b in bets if b["stake"] <= 0]
    print(f"  Bets Kelly ALREADY refuses to stake (stake = 0): {len(zero)} "
          f"-- an edge floor at 0.00 is a literal no-op.")

    hdr("2.  DOES THE PRICE TERM ADD INFORMATION BEYOND p_model?")
    y = [1 if b["win"] else 0 for b in bets]
    Xp = [[b["p"]] for b in bets]
    Xpi = [[b["p"], implied(b["odds"])] for b in bets]
    b1, se1 = logistic(Xp, y)
    b2, se2 = logistic(Xpi, y)
    print(f"  win ~ p_model                : coef(p) = {b1[1]:+.2f}  (SE {se1[1]:.2f}, "
          f"z = {b1[1]/se1[1]:+.2f})")
    print(f"  win ~ p_model + implied      : coef(p) = {b2[1]:+.2f}  (SE {se2[1]:.2f}, "
          f"z = {b2[1]/se2[1]:+.2f})")
    print(f"                                 coef(implied) = {b2[2]:+.2f}  "
          f"(SE {se2[2]:.2f}, z = {b2[2]/se2[2]:+.2f})")
    print("\n  If edge were a genuinely new filter, coef(implied) would be clearly")
    print("  NEGATIVE and significant -- a longer price predicting more wins once")
    print("  the model probability is known. |z| under about 1.6 is indistinguishable")
    print("  from zero at this sample size.")

    print("\n  Assumption-free 2x2: split at the median of each term.")
    mp = st.median([b["p"] for b in bets])
    mi = st.median([implied(b["odds"]) for b in bets])
    print(f"  median p_model = {mp:.3f}   median implied = {mi:.3f}")
    print(f"\n  {'':<22}{'cheap price (implied < med)':>30}{'dear price':>18}")
    for plab, sel in (("high p_model", lambda b: b["p"] >= mp),
                      ("low  p_model", lambda b: b["p"] < mp)):
        cells = []
        for ilab, isel in (("cheap", lambda b: implied(b["odds"]) < mi),
                           ("dear", lambda b: implied(b["odds"]) >= mi)):
            s = [b for b in bets if sel(b) and isel(b)]
            fs = flat_stats(s)
            cells.append(f"{fs['bets']:>3} bets {fs['hit']:>5.1f}% {fs['pl']:>+6.2f}u"
                         if s else "  --")
        print(f"  {plab:<22}{cells[0]:>30}{cells[1]:>18}")
    print("\n  Read the ROWS: within a fixed model-probability band, does the cheaper")
    print("  price also win more often? That is the only thing edge adds over the")
    print("  probability gate we already run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Step 4 -- how impressive is a monotone in-sample edge curve, really?

PLACEBO.  Replace `edge` with a RANDOM number drawn from the same
distribution (a permutation of the real edges across games).  The bets,
prices and outcomes are untouched -- only the ranking is destroyed.  Then
run the identical threshold sweep and record:
    * how often the hit rate climbs monotonically across the 5 cut points
      the operator reported
    * the best ROI improvement found over 11 candidate floors

If a shuffled column reproduces the operator's picture often enough, the
picture is not evidence.

Also: a block-bootstrap CI on the 2025 hit-rate SLOPE, and the exact
count of bets a 0.04 floor removes from the shipped Kelly book.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.edge_floor.common import (load_2026, load_backtest, payout,  # noqa: E402
                                     implied, passes_lambda_floor)

ROOT = Path(__file__).resolve().parent.parent.parent
OP_CUTS = [0.00, 0.04, 0.08, 0.12, 0.16]
SWEEP = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.16]


def hdr(s):
    print("\n" + "=" * 92)
    print("  " + s)
    print("=" * 92)


def hits(bets, cuts):
    out = []
    for c in cuts:
        s = [b for b in bets if b["e"] >= c]
        out.append((len(s), 100 * sum(b["win"] for b in s) / len(s) if s else float("nan")))
    return out


def monotone(hs, minn=8):
    vals = [h for n, h in hs if n >= minn]
    return len(vals) >= 4 and all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))


def roi(bets):
    if not bets:
        return float("nan")
    return 100 * sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets) / len(bets)


def main():
    r26, _ = load_2026()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    p26 = [cal.predict(r["raw"]) for r in r26]

    # ---- the operator's exact population: ALL priced games, no gate ----
    pop = []
    for r, p in zip(r26, p26):
        if r["yrfi_odds"] is None:
            continue
        e = (1 - p) - implied(r["yrfi_odds"])
        pop.append({"date": r["date"], "odds": r["yrfi_odds"], "win": r["yrfi_hit"],
                    "e": e, "p_y": 1 - p})
    pop = [b for b in pop if b["e"] >= 0.0]

    hdr("PLACEBO -- shuffle the edge column, keep everything else")
    real_h = hits(pop, OP_CUTS)
    print(f"  REAL: " + "  ".join(f"{c:.2f}:n={n},{h:.1f}%" for c, (n, h)
                                  in zip(OP_CUTS, real_h)))
    print(f"  real monotone across the 5 reported cuts? "
          f"{'YES' if monotone(real_h) else 'NO'}")
    base_roi = roi(pop)
    real_best = max(roi([b for b in pop if b["e"] >= c]) - base_roi
                    for c in SWEEP if len([b for b in pop if b["e"] >= c]) >= 10)
    print(f"  real best ROI lift over {len(SWEEP)} candidate floors: "
          f"{real_best:+.1f} percentage points\n")

    rng = random.Random(17)
    edges = [b["e"] for b in pop]
    n_mono = 0
    best_lifts = []
    ITERS = 2000
    for _ in range(ITERS):
        sh = edges[:]
        rng.shuffle(sh)
        fake = [{**b, "e": e} for b, e in zip(pop, sh)]
        h = hits(fake, OP_CUTS)
        if monotone(h):
            n_mono += 1
        bl = max((roi([b for b in fake if b["e"] >= c]) - base_roi
                  for c in SWEEP if len([b for b in fake if b["e"] >= c]) >= 10),
                 default=0.0)
        best_lifts.append(bl)
    best_lifts.sort()
    print(f"  shuffled runs: {ITERS}")
    print(f"  monotone across the 5 cuts by pure chance : "
          f"{100*n_mono/ITERS:.1f}% of runs")
    print(f"  best-of-{len(SWEEP)}-floors ROI lift on SHUFFLED edge:")
    print(f"    median {best_lifts[ITERS//2]:+.1f}pp   "
          f"95th pct {best_lifts[int(.95*ITERS)]:+.1f}pp   "
          f"max {best_lifts[-1]:+.1f}pp")
    p_val = sum(1 for x in best_lifts if x >= real_best) / ITERS
    print(f"  P(a MEANINGLESS edge column beats the real one) = {100*p_val:.1f}%")
    print("  -> this is the multiple-comparison price of sweeping 11 thresholds.")

    hdr("2025 SHAPE SLOPE -- block bootstrap over days")
    r25, _ = load_backtest(2025)
    dates = sorted({r["date"] for r in r25})
    fold = {d: i % 5 for i, d in enumerate(dates)}
    pv = [None] * len(r25)
    for k in range(5):
        tr = [r for r in r25 if fold[r["date"]] != k]
        c = CIRCalibrator.fit([r["raw"] for r in tr], [r["y_nrfi"] for r in tr], 20, ["cv"])
        for i, r in enumerate(r25):
            if fold[r["date"]] == k:
                pv[i] = c.predict(r["raw"])
    A = -125.0
    b25 = [{"date": r["date"], "odds": A, "win": r["yrfi_hit"],
            "e": (1 - p) - implied(A)} for r, p in zip(r25, pv)]
    b25 = [b for b in b25 if b["e"] >= 0.0]

    b26 = [b for b in pop]

    def slope_ci(bets, lo_cut, hi_cut, seed):
        byday = defaultdict(list)
        for b in bets:
            byday[b["date"]].append(b)
        days = list(byday)
        rng2 = random.Random(seed)
        out = []
        for _ in range(4000):
            nl = wl = nh = wh = 0
            for _ in range(len(days)):
                for b in byday[rng2.choice(days)]:
                    if b["e"] >= hi_cut:
                        nh += 1
                        wh += b["win"]
                    elif b["e"] >= lo_cut:
                        nl += 1
                        wl += b["win"]
            if nl and nh:
                out.append(100 * wh / nh - 100 * wl / nl)
        out.sort()
        return out[int(.025 * len(out))], out[len(out) // 2], out[int(.975 * len(out))]

    print("  'DEEP minus SHALLOW' hit-rate gap = hit%(edge>=0.08) - hit%(0<=edge<0.08)")
    for name, bb in (("2026 (the searched season, real prices)", b26),
                     ("2025 (the confirmation season, assumed price)", b25)):
        deep = [b for b in bb if b["e"] >= 0.08]
        shal = [b for b in bb if b["e"] < 0.08]
        lo, md, hi = slope_ci(bb, 0.0, 0.08, 23)
        print(f"    {name}")
        print(f"      deep n={len(deep)}  shallow n={len(shal)}   "
              f"gap {md:+.1f}pp   95% CI [{lo:+.1f}, {hi:+.1f}]")

    hdr("WHAT A 0.04 FLOOR ACTUALLY REMOVES FROM THE SHIPPED KELLY BOOK")
    gate = P._LR_STRONG_YRFI_P
    live = []
    for r, p in zip(r26, p26):
        if p >= gate or not passes_lambda_floor(r, "lam_csv"):
            continue
        if r["yrfi_odds"] is None:
            continue
        live.append({"date": r["date"], "p_y": 1 - p, "odds": r["yrfi_odds"],
                     "e": (1 - p) - implied(r["yrfi_odds"]), "win": r["yrfi_hit"]})
    byday = defaultdict(list)
    for b in live:
        byday[b["date"]].append(b)
    bank = 100.0
    old = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = 0.25
    rows = []
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p_y"], str(int(b["odds"])), game_date=d) or 0.0
                g = (s * payout(b["odds"]) if b["win"] else -s) if s > 0 else 0.0
                pnl += g
                rows.append((b, s, g))
            bank += pnl
    finally:
        tracker.KELLY_FRACTION = old
    tot_stake = sum(s for _, s, _ in rows)
    tot_pl = sum(g for _, _, g in rows)
    for f in (0.02, 0.04, 0.06):
        cut = [(b, s, g) for b, s, g in rows if b["e"] < f]
        staked = sum(s for _, s, _ in cut)
        nz = sum(1 for _, s, _ in cut if s > 0)
        print(f"  floor {f:.2f}: removes {len(cut)} of {len(rows)} qualifying games; "
              f"only {nz} were STAKED AT ALL")
        print(f"             that tail is {100*staked/tot_stake:.1f}% of total stake "
              f"({staked:.1f}u of {tot_stake:.1f}u) and {sum(g for _,_,g in cut):+.2f}u "
              f"of {tot_pl:+.2f}u P&L")
    print("\n  Kelly already zero-stakes every negative-edge bet, so the floor's")
    print("  entire marginal effect lives in that handful of staked thin bets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

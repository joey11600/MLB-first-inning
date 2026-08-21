#!/usr/bin/env python3
"""
L2 sweep + money impact for the 2026 model-decay repair.

harness.py found exactly one variant that beat the shipped configuration on
AUC in all three splits: raising L2 from 0.05 to 0.50.  Nothing else did --
dropping either half of the collinear slg/iso pair moved AUC by <=0.002 in
either direction, and re-shrinking the park factor moved it by <=0.0002.

This script does three things harness.py deliberately did not:
  1. sweeps L2 across a grid, so 0.50 is not just the one alternative tried
  2. bootstraps LOGLOSS and BRIER, where the gain is much clearer than AUC
  3. carries the winner through to the bet population -- the gate, the hit
     rate, and flat P&L -- because a probability improvement that never
     changes a bet is not worth a model change

THE SWEEP IS A SEARCH.  Picking the best cell of a grid and then quoting
that cell's CI is the artifact documented in 2026-08-03_gate_sweep_artifact.
The defence used here is that the grid must be MONOTONE-ish and the winner
must hold in all three splits independently, not that any one cell clears
a significance bar.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (T1_SHIPPED, B1_SHIPPED, auc, brier, build_park, fit_lr,  # noqa: E402
                     load, logloss, matrix, predict)

GATE = 0.42          # _LR_STRONG_YRFI_P: STRONG YRFI needs p_nrfi < 0.42


def fit_predict(train, test, l2, K=50, t1f=T1_SHIPPED, b1f=B1_SHIPPED):
    park_map, base = build_park(train, K)
    wt, mt, st = fit_lr(matrix(train, t1f, park_map, base), train["y_t1"].values, l2)
    wb, mb, sb = fit_lr(matrix(train, b1f, park_map, base), train["y_b1"].values, l2)
    pt = predict(wt, mt, st, matrix(test, t1f, park_map, base))
    pb = predict(wb, mb, sb, matrix(test, b1f, park_map, base))
    return 1 - (1 - pt) * (1 - pb), (wt, wb)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260820)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    bt = ROOT / "data" / "backtests"
    d24 = load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024)
    d25 = load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025)
    d26 = load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)
    d26["date"] = pd.to_datetime(d26["date"])
    splits = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
              ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]

    grid = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
    print("=" * 92)
    print("L2 SWEEP -- test-set logloss (lower is better).  L2=0.05 is what ships today.")
    print(f"  {'L2':>6} " + " ".join(f"{lab:>22}" for lab, _, _ in splits))
    table = {}
    for l2 in grid:
        row = []
        for lab, tr, te in splits:
            p, _ = fit_predict(tr, te, l2)
            table[(l2, lab)] = (te["y"].values, p)
            row.append(f"{logloss(te['y'].values, p):.5f} / {auc(te['y'].values, p):.4f}")
        print(f"  {l2:>6} " + " ".join(f"{r:>22}" for r in row))
    print("   (each cell is  logloss / AUC)")

    print("\n" + "=" * 92)
    print("BOOTSTRAPPED GAIN vs L2=0.05, on the metric where the effect is clearest")
    for lab, _, _ in splits:
        y, p0 = table[(0.05, lab)]
        print(f"\n  {lab}  (n={len(y)})")
        for l2 in grid[1:]:
            _, p1 = table[(l2, lab)]
            dl, db = [], []
            for _ in range(args.boot):
                i = rng.integers(0, len(y), len(y))
                dl.append(logloss(y[i], p0[i]) - logloss(y[i], p1[i]))
                db.append(brier(y[i], p0[i]) - brier(y[i], p1[i]))
            dl, db = np.array(dl), np.array(db)
            print(f"    L2={l2:<5} logloss gain {logloss(y,p0)-logloss(y,p1):+.5f} "
                  f"[{np.percentile(dl,5):+.5f},{np.percentile(dl,95):+.5f}] P={(dl>0).mean():.0%}"
                  f"   |  Brier gain {brier(y,p0)-brier(y,p1):+.5f} "
                  f"[{np.percentile(db,5):+.5f},{np.percentile(db,95):+.5f}] P={(db>0).mean():.0%}")

    # ---- money impact on 2026, the only split with a real bet ledger ----
    tr = pd.concat([d24, d25], ignore_index=True)
    print("\n" + "=" * 92)
    print("MONEY IMPACT on 2026 -- what the gate would have fired, and how it did")
    print(f"  gate: STRONG YRFI when p_nrfi < {GATE}  (i.e. p_yrfi > {1-GATE:.2f})")
    y26 = d26["y"].values
    for l2 in [0.05, 0.5, 2.0, 8.0]:
        p, _ = fit_predict(tr, d26, l2)
        fires = p > (1 - GATE)
        n = int(fires.sum())
        hit = y26[fires].mean() if n else float("nan")
        print(f"    L2={l2:<5} fires n={n:4d}  hit={hit:.3f}  mean p={p[fires].mean() if n else float('nan'):.3f}"
              f"  bias on fired={(p[fires].mean()-hit) if n else float('nan'):+.3f}")

    print("\n  same, split by month (hit rate of the games the gate fires on):")
    hdr = None
    for l2 in [0.05, 0.5, 2.0, 8.0]:
        p, _ = fit_predict(tr, d26, l2)
        d26["_p"] = p
        d26["_f"] = p > (1 - GATE)
        g = d26[d26._f].groupby(d26.date.dt.to_period("M")).agg(n=("y", "size"), hit=("y", "mean"))
        if hdr is None:
            hdr = list(g.index.astype(str))
            print("        " + " ".join(f"{m:>14}" for m in hdr))
        cells = []
        for m in hdr:
            if m in g.index.astype(str).tolist():
                r = g.loc[g.index.astype(str) == m].iloc[0]
                cells.append(f"{int(r['n']):>3}@{r['hit']:.3f}")
            else:
                cells.append("  -")
        print(f"  L2={l2:<4} " + " ".join(f"{c:>14}" for c in cells))

    # ---- weight stability: the diagnostic that motivated this ----
    print("\n" + "=" * 92)
    print("WEIGHT MAGNITUDE (sum |w|, T1) -- the collinear slg/iso pair is the reason")
    for l2 in grid:
        _, (wt, _) = fit_predict(tr, d26, l2)
        slg = wt[1 + T1_SHIPPED.index("away_top3c_slg")]
        iso = wt[1 + T1_SHIPPED.index("away_top3c_iso")]
        print(f"    L2={l2:<5} sum|w|={np.abs(wt[1:]).sum():6.3f}   "
              f"away_top3c_slg={slg:+.4f}  away_top3c_iso={iso:+.4f}  net={slg+iso:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

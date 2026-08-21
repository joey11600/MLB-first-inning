#!/usr/bin/env python3
"""
Is the L2 money result skill, or is it the train/test base-rate mismatch?

THE SUSPICION.  money.py showed flat ROI at the 0.42 gate of +3.7% / -12.0% /
+18.4% (shipped) across the three splits.  Those line up perfectly with the
direction of the base-rate error between train and test season:

    split          train YRFI   test YRFI   model error   shipped ROI
    2024->2025        46.5%       50.3%     under-predict     +3.7%
    2025->2024        50.3%       46.5%     OVER-predict     -12.0%
    24+25->2026       48.4%       50.7%     under-predict    +18.4%

A fixed cut point on a probability whose LEVEL is wrong does not measure
skill -- it measures which side of the gate the level error pushed you.
Under-predict and the gate turns ultra-selective and looks brilliant;
over-predict and it floods and looks terrible.  That is the same defect as
finding 1 of the 2026-08-20 diagnosis, showing up inside the validation.

THE TEST.  Re-run every candidate with an ORACLE level correction: shift the
calibrated probability so its mean equals the test season's actual base rate.
That is not shippable -- it uses the answer -- but it removes level error
entirely, so whatever ROI difference SURVIVES is skill and whatever
disappears was level.

If the L2 advantage survives, it is a real repair.  If it vanishes, then
"raise L2" is just another way of moving the level, and the honest fix is to
track the level directly rather than to change the model.
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

from harness import T1_SHIPPED, B1_SHIPPED, auc, drop, load, logloss  # noqa: E402
from money import GATE_NRFI, flat_pnl, pipeline  # noqa: E402


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def oracle_shift(p_yrfi, y):
    """Add a constant in log-odds space so mean(p) == mean(y).  Monotone, so
    AUC is untouched; only the LEVEL moves."""
    lo, hi = -5.0, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(logit(p_yrfi) + mid)))).mean() < y.mean():
            lo = mid
        else:
            hi = mid
    return 1 / (1 + np.exp(-(logit(p_yrfi) + (lo + hi) / 2)))


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

    CANDS = {
        "shipped (L2=0.05)": dict(l2=0.05, K=50),
        "L2=0.5":            dict(l2=0.50, K=50),
        "L2=1.0":            dict(l2=1.00, K=50),
        "L2=0.5 + drop_slg": dict(l2=0.50, K=50,
                                  t1f=drop(T1_SHIPPED, "away_top3c_slg"),
                                  b1f=drop(B1_SHIPPED, "home_top3c_slg")),
    }
    splits = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
              ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]

    print("=" * 100)
    print("BASE RATES -- the confound")
    for lab, tr, te in splits:
        print(f"  {lab:<13} train YRFI={tr.y.mean():.4f}  test YRFI={te.y.mean():.4f}  "
              f"gap={tr.y.mean()-te.y.mean():+.4f}")

    store = {}
    for name, kw in CANDS.items():
        for lab, tr, te in splits:
            p_nrfi, _ = pipeline(tr, te, **kw)
            store[(name, lab)] = (te["y"].values, 1 - p_nrfi, te)

    print("\n" + "=" * 100)
    print("GATE RESULTS -- AS-IS vs with an ORACLE level correction")
    print(f"  {'candidate':<20} {'split':<13} {'bets':>5} {'hit':>7} {'ROI':>8}   ||"
          f" {'bets':>5} {'hit':>7} {'ROI':>8}   (right block = level-corrected)")
    roi = {}
    for name in CANDS:
        for lab, _, _ in splits:
            y, p, te = store[(name, lab)]
            po = oracle_shift(p, y)
            out = []
            for pp in (p, po):
                f = (1 - pp) < GATE_NRFI
                n = int(f.sum())
                if n == 0:
                    out.append((0, float("nan"), float("nan")))
                else:
                    out.append((n, y[f].mean(), flat_pnl(y[f] == 1) / n * 100))
            roi[(name, lab)] = out
            a, b = out
            print(f"  {name:<20} {lab:<13} {a[0]:>5} {a[1]:>7.3f} {a[2]:>+7.1f}%   ||"
                  f" {b[0]:>5} {b[1]:>7.3f} {b[2]:>+7.1f}%")

    print("\n" + "=" * 100)
    print("VERDICT -- does the L2 advantage SURVIVE the level correction?")
    print(f"  {'candidate':<20} " + " ".join(f"{l:>26}" for l, _, _ in splits))
    for name in CANDS:
        if name.startswith("shipped"):
            continue
        cells = []
        for lab, _, _ in splits:
            raw = roi[(name, lab)][0][2] - roi[("shipped (L2=0.05)", lab)][0][2]
            cor = roi[(name, lab)][1][2] - roi[("shipped (L2=0.05)", lab)][1][2]
            cells.append(f"as-is {raw:+6.1f} / lvl-fix {cor:+6.1f}")
        print(f"  {name:<20} " + " ".join(f"{c:>26}" for c in cells))
    print("  (percentage points of flat ROI vs shipped.  If 'lvl-fix' collapses toward")
    print("   zero, the as-is gain was the base-rate artifact, not model skill.)")

    print("\n" + "=" * 100)
    print("WHAT THE LEVEL ALONE IS WORTH -- shipped model, oracle level, per split")
    for lab, _, _ in splits:
        a, b = roi[("shipped (L2=0.05)", lab)]
        print(f"  {lab:<13} as-is {a[0]:>4} bets @ {a[1]:.3f} = {a[2]:+6.1f}%   ->   "
              f"level-fixed {b[0]:>4} bets @ {b[1]:.3f} = {b[2]:+6.1f}%")

    print("\n" + "=" * 100)
    print("RANKING IS UNCHANGED BY ANY OF THIS (AUC, level-invariant)")
    for name in CANDS:
        cells = [f"{auc(store[(name,lab)][0], store[(name,lab)][1]):.4f}" for lab, _, _ in splits]
        print(f"  {name:<20} " + " ".join(f"{c:>12}" for c in cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

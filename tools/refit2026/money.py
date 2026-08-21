#!/usr/bin/env python3
"""
Money impact of the 2026 repair candidates, WITH the shipped calibrator in
the loop.

WHY THE CALIBRATOR MATTERS HERE.  l2_sweep.py's first pass gated on the raw
two-stage output and concluded that L2>=0.5 fires zero bets.  That was an
artifact: production gates on CIR-CALIBRATED probability, and the 0.42 cut
point was tuned for that scale.  Raising L2 compresses the raw output toward
the base rate, so a raw-scale gate goes silent -- but the calibrator maps
the compressed range back out.  Any money claim has to run the same pipeline
production runs:

    two-stage LR  ->  CIRCalibrator (fit on TRAIN only)  ->  gate at 0.42

The calibrator is fit on the training split and applied to the test split,
which is what the live system does with historical graded data.

Everything here is out-of-sample by construction: park map, LR weights and
calibrator all come from the training seasons only.
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

from calibration import CIRCalibrator  # noqa: E402
from harness import (T1_SHIPPED, B1_SHIPPED, auc, brier, build_park, drop,  # noqa: E402
                     fit_lr, load, logloss, matrix, predict)

GATE_NRFI = 0.42          # STRONG YRFI requires p_nrfi < 0.42


def pipeline(train, test, l2=0.05, K=50, t1f=T1_SHIPPED, b1f=B1_SHIPPED, n_bins=20):
    """Full production shape: two-stage LR -> CIR calibrator -> p_nrfi."""
    park_map, base = build_park(train, K)
    Xt_tr, Xb_tr = matrix(train, t1f, park_map, base), matrix(train, b1f, park_map, base)
    wt, mt, st = fit_lr(Xt_tr, train["y_t1"].values, l2)
    wb, mb, sb = fit_lr(Xb_tr, train["y_b1"].values, l2)

    def raw_nrfi(d):
        pt = predict(wt, mt, st, matrix(d, t1f, park_map, base))
        pb = predict(wb, mb, sb, matrix(d, b1f, park_map, base))
        return (1 - pt) * (1 - pb)

    tr_nrfi = raw_nrfi(train)
    cal = CIRCalibrator.fit(list(tr_nrfi), list((train["y"] == 0).astype(int)),
                            n_bins=n_bins)
    te_nrfi = raw_nrfi(test)
    return np.array([cal.predict(float(v)) for v in te_nrfi]), te_nrfi


def flat_pnl(hit_mask, price=-115.0):
    """Flat 1u P&L at a fixed price -- deliberately NOT Kelly.  tools/edge_floor
    concluded you must never judge a filter on final Kelly bank; the flat
    column is what decides."""
    win = 100.0 / abs(price) if price < 0 else price / 100.0
    return float(np.where(hit_mask, win, -1.0).sum())


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
        "shipped (L2=0.05)":  dict(l2=0.05, K=50),
        "L2=0.25":            dict(l2=0.25, K=50),
        "L2=0.5":             dict(l2=0.50, K=50),
        "L2=1.0":             dict(l2=1.00, K=50),
        "L2=0.5 + park309":   dict(l2=0.50, K=309),
        "L2=0.5 + drop_slg":  dict(l2=0.50, K=50,
                                   t1f=drop(T1_SHIPPED, "away_top3c_slg"),
                                   b1f=drop(B1_SHIPPED, "home_top3c_slg")),
    }

    splits = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
              ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]

    print("=" * 104)
    print("CALIBRATED PROBABILITY QUALITY (CIR fit on train, applied to test)")
    print(f"  {'candidate':<20} " + " ".join(f"{l:>24}" for l, _, _ in splits))
    store = {}
    for name, kw in CANDS.items():
        cells = []
        for lab, tr, te in splits:
            p_nrfi, _ = pipeline(tr, te, **kw)
            y = te["y"].values
            p_yrfi = 1 - p_nrfi
            store[(name, lab)] = (y, p_yrfi, te)
            cells.append(f"{logloss(y, p_yrfi):.5f} / {auc(y, p_yrfi):.4f}")
        print(f"  {name:<20} " + " ".join(f"{c:>24}" for c in cells))
    print("   (logloss / AUC).  Calibration is monotone so AUC is unchanged by it.")

    print("\n" + "=" * 104)
    print(f"BETS THE GATE WOULD FIRE  (STRONG YRFI when p_nrfi < {GATE_NRFI})")
    print(f"  {'candidate':<20} {'split':<13} {'bets':>5} {'hit':>7} {'model p':>8} "
          f"{'bias':>8} {'flat P&L':>9} {'ROI':>7}")
    for name in CANDS:
        for lab, _, _ in splits:
            y, p_yrfi, te = store[(name, lab)]
            fires = (1 - p_yrfi) < GATE_NRFI
            n = int(fires.sum())
            if n == 0:
                print(f"  {name:<20} {lab:<13} {0:>5}       -        -        -         -       -")
                continue
            hit = y[fires].mean()
            pnl = flat_pnl(y[fires] == 1)
            print(f"  {name:<20} {lab:<13} {n:>5} {hit:>7.3f} {p_yrfi[fires].mean():>8.3f} "
                  f"{p_yrfi[fires].mean()-hit:>+8.3f} {pnl:>+9.2f}u {pnl/n*100:>+6.1f}%")

    print("\n" + "=" * 104)
    print("2026 ONLY, BY MONTH -- does the candidate hold up through the August decay?")
    for name in CANDS:
        y, p_yrfi, te = store[(name, "24+25->2026")]
        te = te.copy(); te["_p"] = p_yrfi; te["_f"] = (1 - p_yrfi) < GATE_NRFI
        f = te[te._f]
        if not len(f):
            print(f"  {name:<20} no bets"); continue
        g = f.groupby(f.date.dt.to_period("M")).agg(n=("y", "size"), hit=("y", "mean"))
        cells = " ".join(f"{str(m)[-2:]}: {int(r.n):>3}@{r.hit:.3f}" for m, r in g.iterrows())
        print(f"  {name:<20} {cells}")

    print("\n" + "=" * 104)
    print("HEAD-TO-HEAD vs shipped on 2026 bets -- bootstrap over SLATE DAYS")
    print("  (resampling whole days, not games: same-night picks are correlated)")
    y0, p0, te0 = store[("shipped (L2=0.05)", "24+25->2026")]
    days = te0["date"].dt.normalize()
    uniq = days.unique()
    for name in CANDS:
        if name.startswith("shipped"):
            continue
        y1, p1, te1 = store[(name, "24+25->2026")]
        d = []
        for _ in range(args.boot):
            pick = rng.choice(len(uniq), len(uniq), replace=True)
            idx = np.concatenate([np.where(days.values == uniq[k])[0] for k in pick])
            f0 = (1 - p0[idx]) < GATE_NRFI
            f1 = (1 - p1[idx]) < GATE_NRFI
            r0 = flat_pnl(y0[idx][f0] == 1) / max(f0.sum(), 1)
            r1 = flat_pnl(y1[idx][f1] == 1) / max(f1.sum(), 1)
            d.append(r1 - r0)
        d = np.array(d) * 100
        print(f"  {name:<20} ROI diff {d.mean():>+6.2f}pp   90% CI "
              f"[{np.percentile(d,5):+6.2f},{np.percentile(d,95):+6.2f}]   P(better)={(d>0).mean():.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

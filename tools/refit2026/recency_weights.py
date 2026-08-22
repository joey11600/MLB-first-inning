#!/usr/bin/env python3
"""
Recency-weighted training: does weighting recent games more (exponential
decay by age at the END of the training window) help out of sample?

Different from the 2026-07-28 "refit on recent data" test (which re-fit the
same recipe on shorter windows and lost): here every game stays in, older
ones just count less.  Half-lives 60 / 120 / 240 / 480 days vs none, on the
candidate feature set (+fi_xwoba) at L2 0.5, three splits.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, load, logloss, matrix  # noqa: E402
from test_fi_pooled import attach  # noqa: E402


def fit_w(X, y, w, l2=0.5, iters=300):
    mu = np.average(X, axis=0, weights=w); sd = np.sqrt(np.average((X - mu) ** 2, axis=0, weights=w))
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]; b = np.zeros(Z.shape[1])
    R = np.eye(Z.shape[1]) * l2; R[0, 0] = 0
    wn = w / w.mean()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Z @ b))
        g = Z.T @ (wn * (y - p)) / len(y) - R @ b
        H = (Z * (wn * p * (1 - p))[:, None]).T @ Z / len(y) + R + 1e-8 * np.eye(Z.shape[1])
        st = np.linalg.solve(H, g); b += st
        if np.max(np.abs(st)) < 1e-9: break
    return b, mu, sd


def pred(b, mu, sd, X):
    return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), (X - mu) / sd] @ b)))


def main() -> int:
    rng = np.random.default_rng(20260821)
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    d26 = attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fac)
    T1 = T1_SHIPPED + ["home_fi_xwoba"]; B1 = B1_SHIPPED + ["away_fi_xwoba"]
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"])
        for c in ("home_fi_xwoba", "away_fi_xwoba"):
            d[c] = d[c].fillna(d[c].mean())
    defs = [("24->25", d24, d25), ("25->24", d25, d24),
            ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    print("candidate feature set (+fi_xwoba), L2=0.5; weights = 0.5^(age_days/half_life)")
    print(f"  {'half-life':>10} " + " ".join(f"{l:>28}" for l, _, _ in defs))
    base = {}
    for hl in [None, 480, 240, 120, 60]:
        cells = []
        for lab, tr, te in defs:
            age = (tr.date.max() - tr.date).dt.days.values.astype(float)
            w = np.ones(len(tr)) if hl is None else 0.5 ** (age / hl)
            pk, b0 = build_park(tr, 50)
            bt_, mt, st = fit_w(matrix(tr, T1, pk, b0), tr.y_t1.values, w)
            bb_, mb, sb = fit_w(matrix(tr, B1, pk, b0), tr.y_b1.values, w)
            p = 1 - (1 - pred(bt_, mt, st, matrix(te, T1, pk, b0))) * (1 - pred(bb_, mb, sb, matrix(te, B1, pk, b0)))
            y = te.y.values
            if hl is None:
                base[lab] = p; cells.append(f"{auc(y,p):.4f}/{logloss(y,p):.5f} (base)")
            else:
                p0 = base[lab]
                dl = np.array([logloss(y[i], p0[i]) - logloss(y[i], p[i])
                               for i in (rng.integers(0, len(y), len(y)) for _ in range(400))])
                cells.append(f"{auc(y,p)-auc(y,p0):+.4f}/{dl.mean()*1000:+.2f}[{np.percentile(dl,5)*1000:+.2f},{np.percentile(dl,95)*1000:+.2f}]")
        print(f"  {str(hl or 'none'):>10} " + " ".join(f"{c:>28}" for c in cells))
    print("  (cells after the first row: dAUC / dlogloss x1000 [90% CI] vs unweighted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

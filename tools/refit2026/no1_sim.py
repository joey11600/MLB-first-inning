#!/usr/bin/env python3
"""
THE PRODUCT METRIC, done properly: the night's No.1, out-of-sample on 2026.

For each configuration, fit on 2024+2025, score 2026 through the real shape
(LR -> CIR -> gate 0.42), and each night pick the lowest calibrated p_nrfi
among gate-firing games -- the simulated No.1.  Report its hit rate, its
flat P&L at -112, and a SLATE-DAY bootstrap CI on the difference vs today's
model.  Then month by month, because the question that started all this was
August.

2x2 so the source of any gain is visible:
    shipped features x L2 {0.05, 0.50}   x   +fi_xwoba {no, yes}
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration import CIRCalibrator  # noqa: E402
from harness import T1_SHIPPED, B1_SHIPPED, build_park, fit_lr, load, matrix, predict  # noqa: E402
from money import GATE_NRFI  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

DEC = 100.0 / 112.0     # flat -112


def fit_score(tr, te, t1f, b1f, l2):
    tr, te = tr.copy(), te.copy()
    for c in [x for x in t1f + b1f if x.endswith("fi_xwoba")]:
        mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, l2)
    wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, l2)
    def raw(d):
        return (1 - predict(wt, mt, st, matrix(d, t1f, pk, b0))) * (1 - predict(wb, mb, sb, matrix(d, b1f, pk, b0)))
    cal = CIRCalibrator.fit(list(raw(tr)), list((tr.y == 0).astype(int)), n_bins=20)
    return np.array([cal.predict(float(v)) for v in raw(te)])


def no1_table(te, pn):
    t = te[["date", "y", "game_pk"]].copy(); t["pn"] = pn
    t = t[t.pn < GATE_NRFI]
    if not len(t):
        return t.iloc[0:0]
    return t.loc[t.groupby("date").pn.idxmin()].sort_values("date")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    d26 = attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fac)
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    tr = pd.concat([d24, d25], ignore_index=True)
    T1F = T1_SHIPPED + ["home_fi_xwoba"]; B1F = B1_SHIPPED + ["away_fi_xwoba"]

    cfgs = {
        "TODAY  (shipped, L2=0.05)":      (T1_SHIPPED, B1_SHIPPED, 0.05),
        "L2 only (shipped, L2=0.50)":     (T1_SHIPPED, B1_SHIPPED, 0.50),
        "feature only (+fi_xwoba, 0.05)": (T1F, B1F, 0.05),
        "CANDIDATE (+fi_xwoba, L2=0.50)": (T1F, B1F, 0.50),
    }
    tabs = {}
    print("2026, fit on 2024+2025.  No.1 = lowest calibrated p_nrfi among gate-firing games each night.")
    print(f"  {'config':<32} {'slates':>6} {'No.1 hit':>9} {'flat P&L':>9} {'ROI':>7}   {'gate bets':>9}")
    for name, (t1f, b1f, l2) in cfgs.items():
        pn = fit_score(tr, d26, t1f, b1f, l2)
        t = no1_table(d26, pn); tabs[name] = (t, pn)
        pnl = float(np.where(t.y == 1, DEC, -1.0).sum())
        print(f"  {name:<32} {len(t):>6} {t.y.mean():>9.3f} {pnl:>+8.2f}u {pnl/len(t)*100:>+6.1f}%   "
              f"{int((pn < GATE_NRFI).sum()):>9}")

    print("\nSLATE-DAY BOOTSTRAP: candidate vs today, on the nights BOTH have a No.1")
    base_t = tabs["TODAY  (shipped, L2=0.05)"][0].set_index("date")
    for name in list(cfgs)[1:]:
        t = tabs[name][0].set_index("date")
        both = base_t.index.intersection(t.index)
        a = base_t.loc[both, "y"].values; b = t.loc[both, "y"].values
        d = np.array([b[i].mean() - a[i].mean() for i in (rng.integers(0, len(both), len(both))
                                                            for _ in range(args.boot))])
        print(f"  {name:<32} common nights={len(both):3d}  today {a.mean():.3f} -> {b.mean():.3f}  "
              f"dHIT {d.mean():+.3f} [{np.percentile(d,5):+.3f},{np.percentile(d,95):+.3f}]  "
              f"P(better)={(d>0).mean():.0%}   same game picked on "
              f"{(base_t.loc[both, 'game_pk'].values == t.loc[both, 'game_pk'].values).mean()*100:.0f}% of nights")

    print("\nBY MONTH, No.1 hit (slates): today vs candidate")
    a = tabs["TODAY  (shipped, L2=0.05)"][0]; b = tabs["CANDIDATE (+fi_xwoba, L2=0.50)"][0]
    for m in sorted(set(a.date.dt.to_period("M")) | set(b.date.dt.to_period("M"))):
        ma = a[a.date.dt.to_period("M") == m]; mb = b[b.date.dt.to_period("M") == m]
        print(f"  {m}   today {ma.y.mean() if len(ma) else float('nan'):.3f} ({len(ma):>2})   "
              f"candidate {mb.y.mean() if len(mb) else float('nan'):.3f} ({len(mb):>2})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

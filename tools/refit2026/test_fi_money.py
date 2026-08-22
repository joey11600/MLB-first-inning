#!/usr/bin/env python3
"""
Money test for the pooled first-inning pitcher xwOBA feature, through the
REAL pipeline shape: two-stage LR (+feature) -> CIR calibrator -> gate 0.42.

Why this exists even though the feature already passed the protocol: the
statcast backlog's hardest-won lesson is that "the gate amplifies feature
noise rather than filtering it" -- xwOBA (season-to-date) once fired 171
bets instead of 104 at a lower hit rate.  So a feature must be shown to
help AT THE GATE, with the level confound controlled (baserate_control),
on slate-day bootstraps, and month by month on 2026.

Also prints the fitted coefficient on the feature in every split: a sign
that flips between splits is the umpire-CSAE failure mode.
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
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load, logloss, matrix, predict  # noqa: E402
from money import GATE_NRFI, flat_pnl  # noqa: E402
from baserate_control import oracle_shift  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

FEAT = "fi_xwoba"


def pipe(tr, te, t1f, b1f, want_coef=False):
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, 0.05)
    wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, 0.05)
    def raw(d):
        return (1 - predict(wt, mt, st, matrix(d, t1f, pk, b0))) * \
               (1 - predict(wb, mb, sb, matrix(d, b1f, pk, b0)))
    cal = CIRCalibrator.fit(list(raw(tr)), list((tr.y == 0).astype(int)), n_bins=20)
    p_nrfi = np.array([cal.predict(float(v)) for v in raw(te)])
    coef = None
    if want_coef:
        coef = (wt[1 + t1f.index(f"home_{FEAT}")], wb[1 + b1f.index(f"away_{FEAT}")])
    return p_nrfi, coef


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", default="factor_fi_pooled.csv")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    fac = pd.read_csv(ROOT / "data" / "candidates" / args.factor)
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    d26 = attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fac)
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"])
    T1 = T1_SHIPPED + [f"home_{FEAT}"]; B1 = B1_SHIPPED + [f"away_{FEAT}"]

    defs = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
            ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    print(f"factor file: {args.factor}")
    print("=" * 100)
    print(f"  {'split':<12} {'coef T1/B1':>16} | {'shipped: bets hit ROI':>26} | {'+fi_xwoba: bets hit ROI':>26} | lvl-fixed dROI")
    store = {}
    for lab, tr, te in defs:
        tr, te = tr.copy(), te.copy()
        for d in (tr, te):
            for c in (f"home_{FEAT}", f"away_{FEAT}"):
                d[c] = d[c].fillna(tr[c].mean())
        y = te.y.values
        p0, _ = pipe(tr, te, T1_SHIPPED, B1_SHIPPED)
        p1, coef = pipe(tr, te, T1, B1, want_coef=True)
        store[lab] = (y, p0, p1, te)
        def gate(p_nrfi):
            f = p_nrfi < GATE_NRFI; n = int(f.sum())
            return n, (y[f].mean() if n else np.nan), (flat_pnl(y[f] == 1) / n * 100 if n else np.nan)
        g0, g1 = gate(p0), gate(p1)
        # level-controlled: oracle shift both to the test base rate, then gate
        q0 = 1 - oracle_shift(1 - p0, y); q1 = 1 - oracle_shift(1 - p1, y)
        h0, h1 = gate(q0), gate(q1)
        print(f"  {lab:<12} {coef[0]:>+7.4f}/{coef[1]:>+7.4f} | "
              f"{g0[0]:>5} {g0[1]:>6.3f} {g0[2]:>+7.1f}%     | "
              f"{g1[0]:>5} {g1[1]:>6.3f} {g1[2]:>+7.1f}%     | {h1[2]-h0[2]:>+6.1f}pp "
              f"(lvl-fixed bets {h0[0]}->{h1[0]})")
    print("  (coef = standardized LR weight on the feature; must be POSITIVE and stable:")
    print("   higher first-inning xwOBA allowed -> more runs.  ROI at flat -112.)")

    print("\n" + "=" * 100)
    print("2026 BY MONTH (gate bets, hit, flat ROI)  shipped vs +fi_xwoba")
    y, p0, p1, te = store["24+25->2026"]
    te = te.copy(); te["_p0"] = p0; te["_p1"] = p1
    for m, g in te.groupby(te.date.dt.to_period("M")):
        f0 = g._p0 < GATE_NRFI; f1 = g._p1 < GATE_NRFI
        s = f"  {m}  shipped {int(f0.sum()):>3} @ {g.y[f0].mean() if f0.sum() else np.nan:.3f}"
        s += f" {flat_pnl(g.y[f0]==1)/max(f0.sum(),1)*100:>+6.1f}%"
        s += f"   | +fi {int(f1.sum()):>3} @ {g.y[f1].mean() if f1.sum() else np.nan:.3f}"
        s += f" {flat_pnl(g.y[f1]==1)/max(f1.sum(),1)*100:>+6.1f}%"
        print(s)

    print("\n" + "=" * 100)
    print("SLATE-DAY BOOTSTRAP on 2026: ROI(+fi_xwoba) - ROI(shipped) at the gate")
    days = te.date.dt.normalize().values; uniq = np.unique(days)
    diffs, dhit = [], []
    for _ in range(args.boot):
        pick = rng.choice(len(uniq), len(uniq), replace=True)
        idx = np.concatenate([np.where(days == uniq[k])[0] for k in pick])
        f0 = p0[idx] < GATE_NRFI; f1 = p1[idx] < GATE_NRFI
        r0 = flat_pnl(y[idx][f0] == 1) / max(f0.sum(), 1) * 100
        r1 = flat_pnl(y[idx][f1] == 1) / max(f1.sum(), 1) * 100
        diffs.append(r1 - r0); dhit.append(y[idx][f1].mean() - y[idx][f0].mean())
    diffs, dhit = np.array(diffs), np.array(dhit)
    print(f"  dROI {diffs.mean():+.2f}pp  90% CI [{np.percentile(diffs,5):+.2f},{np.percentile(diffs,95):+.2f}]  "
          f"P(better)={(diffs>0).mean():.0%}   |   dHIT {dhit.mean():+.3f} "
          f"[{np.percentile(dhit,5):+.3f},{np.percentile(dhit,95):+.3f}]")
    print(f"  probability quality on ALL 2026 games: AUC {auc(y,1-p0):.4f} -> {auc(y,1-p1):.4f}, "
          f"logloss {logloss(y,1-p0):.5f} -> {logloss(y,1-p1):.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Robustness of the pooled first-inning xwOBA feature.

(1) Five builds of the factor at different shrinkage settings -- a real
    effect is a PLATEAU across K_PA / prior-season weight; a spike at one
    setting is the gate-sweep artifact (2026-08-03) wearing a new hat.
(2) Does it STACK with the other validated change (L2 0.05 -> 0.50)?
Metric: per-half refit, three splits, dAUC and dlogloss vs shipped, with a
paired bootstrap CI on logloss.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load, logloss, matrix, predict  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

FEAT = "fi_xwoba"
rng = np.random.default_rng(20260821)
bt = ROOT / "data" / "backtests"
BASE = {2024: load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024),
        2025: load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025),
        2026: load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)}


def run(tr, te, t1f, b1f, l2):
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, l2)
    wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, l2)
    return 1 - (1 - predict(wt, mt, st, matrix(te, t1f, pk, b0))) * \
               (1 - predict(wb, mb, sb, matrix(te, b1f, pk, b0)))


def evaluate(fac_name, l2_base, l2_feat, label):
    fac = pd.read_csv(ROOT / "data" / "candidates" / fac_name)
    d = {y: attach(v, fac) for y, v in BASE.items()}
    defs = [("24->25", d[2024], d[2025]), ("25->24", d[2025], d[2024]),
            ("->2026", pd.concat([d[2024], d[2025]], ignore_index=True), d[2026])]
    T1 = T1_SHIPPED + [f"home_{FEAT}"]; B1 = B1_SHIPPED + [f"away_{FEAT}"]
    cells, allpos = [], True
    for lab, tr, te in defs:
        tr, te = tr.copy(), te.copy()
        for x in (tr, te):
            for c in (f"home_{FEAT}", f"away_{FEAT}"):
                x[c] = x[c].fillna(tr[c].mean())
        y = te.y.values
        p0 = run(tr, te, T1_SHIPPED, B1_SHIPPED, l2_base)
        p1 = run(tr, te, T1, B1, l2_feat)
        dl = np.array([logloss(y[i], p0[i]) - logloss(y[i], p1[i])
                       for i in (rng.integers(0, len(y), len(y)) for _ in range(500))])
        da = auc(y, p1) - auc(y, p0)
        allpos &= (dl.mean() > 0) and (da > 0)
        cells.append(f"{da:+.4f}/{dl.mean()*1000:+.2f}[{np.percentile(dl,5)*1000:+.2f},{np.percentile(dl,95)*1000:+.2f}]")
    print(f"  {label:<34} " + "  ".join(f"{c:>30}" for c in cells) + f"   {'ALL+' if allpos else '-'}")


def main() -> int:
    print("cells = dAUC / dlogloss x1000 [90% CI]   vs the comparison model")
    print(f"  {'variant':<34} {'24->25':>30}  {'25->24':>30}  {'->2026':>30}")
    print("-- (1) shrinkage robustness: +fi_xwoba vs shipped, L2=0.05 both --")
    for f, lab in [("factor_fi_pooled_k30.csv", "K_PA=30,  prior-season w=0.6"),
                   ("factor_fi_pooled.csv", "K_PA=60,  prior-season w=0.6 (base)"),
                   ("factor_fi_pooled_k120.csv", "K_PA=120, prior-season w=0.6"),
                   ("factor_fi_pooled_w03.csv", "K_PA=60,  prior-season w=0.3"),
                   ("factor_fi_pooled_w10.csv", "K_PA=60,  prior-season w=1.0")]:
        evaluate(f, 0.05, 0.05, lab)
    print("-- (2) stacking with L2: (+fi_xwoba, L2=0.5) vs (shipped, L2=0.5) --")
    evaluate("factor_fi_pooled.csv", 0.5, 0.5, "over the L2=0.5 model")
    print("-- (3) both changes vs shipped as-is: (+fi_xwoba, L2=0.5) vs (shipped, L2=0.05) --")
    evaluate("factor_fi_pooled.csv", 0.05, 0.5, "fi_xwoba + L2=0.5 vs today")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

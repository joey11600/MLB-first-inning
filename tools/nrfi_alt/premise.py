#!/usr/bin/env python3
"""Does the PREMISE hold? Two checks.

1. 2025 axis. The proposal needs captured prices (offset / payout weight /
   EV target are all undefined without them). 2025 has none. State the
   measurable thing instead: how much NRFI discrimination does the model
   carry into a season it was not fit on?

2. Market-vs-model on the 2026 priced pool: can ANY blend of the model on
   top of the de-vigged market beat the market alone out of sample?
   That is the offset model's premise stripped to one coefficient.
"""
from __future__ import annotations
import sys, csv
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import recalibrate_v2 as rc  # noqa
from profit_target_wf import load, logit, fit_logistic, pred_logistic  # noqa


def check_2025():
    p = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    fi = rc.load_fi_park()
    Xt, Xb, y, sk = rc.gather_from_backtest(rows, fi)
    t1, b1 = rc.load_lr_models()
    raw = np.asarray(rc.lr_predict_two_stage(t1, b1, Xt, Xb), float)
    print(f"2025 backtest n={len(y)} (skipped {sk})  NRFI rate {y.mean():.4f}")
    print(f"   model AUC on 2025 NRFI outcome: {roc_auc_score(y, raw):.4f}")
    print("   NOTE: this file has NO odds columns -> "
          f"{'market_nrfi_odds' in rows[0]}, so no profit target is definable on 2025.")


def check_premise():
    meta, X = load()
    meta = meta.copy()
    meta["week"] = meta["date"].dt.to_period("W").astype(str)
    weeks = sorted(meta["week"].unique())
    lm = logit(meta.dev_n.values)
    lp = logit(meta.p_raw.values)
    Z = np.column_stack([lm, lp])
    oo = np.full(len(meta), np.nan)      # 2-feature blend, walk-forward
    om = np.full(len(meta), np.nan)      # market-only refit (fair comparator)
    for wi, wk in enumerate(weeks):
        te = (meta.week == wk).values; tr = (meta.week < wk).values
        if wi < 4 or tr.sum() < 250 or te.sum() == 0:
            continue
        y = meta.y_nrfi.values
        mu, sd = Z[tr].mean(0), Z[tr].std(0); sd[sd < 1e-9] = 1
        A, B = (Z[tr] - mu) / sd, (Z[te] - mu) / sd
        b = fit_logistic(A, y[tr], lam=1.0)
        oo[te] = pred_logistic(b, B)
        b = fit_logistic(A[:, :1], y[tr], lam=1.0)
        om[te] = pred_logistic(b, B[:, :1])
    m = meta[~np.isnan(oo)].copy()
    m["blend"] = oo[~np.isnan(oo)]; m["mktonly"] = om[~np.isnan(oo)]
    y = m.y_nrfi.values
    print(f"\n2026 priced pool, walk-forward n={len(m)}")
    print(f"   market de-vig raw      AUC {roc_auc_score(y, m.dev_n):.4f}")
    print(f"   market-only refit      AUC {roc_auc_score(y, m.mktonly):.4f}")
    print(f"   market + model blend   AUC {roc_auc_score(y, m.blend):.4f}"
          "   <- the offset model's premise, one coefficient")
    print(f"   model alone (raw)      AUC {roc_auc_score(y, m.p_raw):.4f}")
    # bootstrap the AUC delta over days
    rng = np.random.default_rng(7)
    days = m.date.values; uq = np.unique(days)
    idx = {d: np.where(days == d)[0] for d in uq}
    dl = []
    for _ in range(3000):
        pick = np.concatenate([idx[uq[j]] for j in rng.choice(len(uq), len(uq), True)])
        yy = y[pick]
        if yy.min() == yy.max():
            continue
        dl.append((roc_auc_score(yy, m.blend.values[pick]) -
                   roc_auc_score(yy, m.dev_n.values[pick]),
                   roc_auc_score(yy, m.blend.values[pick]) -
                   roc_auc_score(yy, m.mktonly.values[pick])))
    dl = np.array(dl)
    for j, lab in ((0, "vs raw market de-vig"), (1, "vs market-only refit (apples-to-apples)")):
        c = dl[:, j]
        print(f"   AUC(blend) - AUC({lab}): {c.mean():+.4f}  "
              f"day-block CI [{np.percentile(c,2.5):+.4f}, {np.percentile(c,97.5):+.4f}]")
    print("   (per-week intercepts break global monotonicity, which is why the "
          "market-only REFIT scores below raw dev_n; both refits share that penalty)")


if __name__ == "__main__":
    check_2025()
    check_premise()

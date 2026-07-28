#!/usr/bin/env python3
"""Adversarial follow-ups to profit_target_wf.py.

  A. Period effect -- is the July "win" the model, or just July?
  B. Best-shot sweep -- regularisation x feature set x burn-in.  If the
     BEST of a big optimistically-biased sweep still cannot clear the
     break-even price, the idea is dead regardless of tuning.
  C. Search-exposure null -- shuffle outcomes within day, rerun the whole
     selection, and see how big a "winner" pure search noise produces.
  D. Discrimination -- AUC of the fitted PROBABILITY (not the EV score),
     so the price-ranking artefact is separated from real signal.
  E. Break-even arithmetic -- what hit rate would each cell need.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from profit_target_wf import (load, logit, fit_ridge, pred_ridge,  # noqa
                             fit_logistic, pred_logistic, day_block_boot,
                             haircut_units)

RNG = np.random.default_rng(4242)


def run_wf(meta, X, lam_reg, burn_weeks, min_train, feat_mask, y_override=None):
    m = meta.copy()
    m["week"] = m["date"].dt.to_period("W").astype(str)
    weeks = sorted(m["week"].unique())
    y = m.y_nrfi.values if y_override is None else y_override
    units_n = np.where(y == 1, m.pay_n.values, -1.0)
    Xf = X[:, feat_mask]
    res = {k: np.full(len(m), np.nan) for k in ("T_UNITS", "T_OFFSET", "T_WEIGHT")}
    pr = {k: np.full(len(m), np.nan) for k in ("T_OFFSET", "T_WEIGHT")}
    tested = np.zeros(len(m), bool)
    for wi, wk in enumerate(weeks):
        te = (m["week"] == wk).values
        tr = (m["week"] < wk).values
        if wi < burn_weeks or tr.sum() < min_train or te.sum() == 0:
            continue
        mu, sd = Xf[tr].mean(0), Xf[tr].std(0)
        sd[sd < 1e-9] = 1.0
        A, B = (Xf[tr] - mu) / sd, (Xf[te] - mu) / sd
        b = fit_ridge(A, units_n[tr], lam=lam_reg)
        res["T_UNITS"][te] = pred_ridge(b, B)
        oa, ob = logit(m.dev_n.values[tr]), logit(m.dev_n.values[te])
        bb = fit_logistic(A, y[tr], offset=oa, lam=lam_reg)
        p = pred_logistic(bb, B, offset=ob)
        pr["T_OFFSET"][te] = p
        res["T_OFFSET"][te] = p * m.pay_n.values[te] - (1 - p)
        w = np.where(y[tr] == 1, m.pay_n.values[tr], 1.0)
        bb = fit_logistic(A, y[tr], w=w, lam=lam_reg)
        p = pred_logistic(bb, B)
        pr["T_WEIGHT"][te] = p
        res["T_WEIGHT"][te] = p * m.pay_n.values[te] - (1 - p)
        tested[te] = True
    m["units_n"] = units_n
    m["y_used"] = y
    for k, v in res.items():
        m[k] = v
    for k, v in pr.items():
        m["P_" + k] = v
    return m[tested].reset_index(drop=True)


DEPTHS = [0.05, 0.10, 0.20, 0.30, 0.50, "EV>0"]
TARGETS = ["T_UNITS", "T_OFFSET", "T_WEIGHT"]


def cells(df):
    out = []
    for c in TARGETS:
        s = df[c].values
        o = np.argsort(-s)
        for d in DEPTHS:
            if d == "EV>0":
                mask = s > 0
            else:
                k = max(1, int(round(d * len(df))))
                mask = np.zeros(len(df), bool); mask[o[:k]] = True
            if mask.sum() < 20:
                continue
            u = df.units_n.values[mask]
            out.append((c, d, int(mask.sum()), 100 * u.mean(), mask))
    return out


def main():
    meta, X = load()
    nfeat = X.shape[1]
    ALL = np.ones(nfeat, bool)
    MKT_ONLY = np.zeros(nfeat, bool); MKT_ONLY[-7:] = True     # market/model block
    NO_MKT = np.ones(nfeat, bool); NO_MKT[-7:-2] = False

    # ---------- A. period effect ----------
    print("=" * 72)
    print("A.  PERIOD EFFECT -- bet-everything-NRFI baseline by month")
    base = run_wf(meta, X, 5.0, 4, 250, ALL)
    for mo, g in base.groupby(base.date.dt.to_period("M")):
        u = g.units_n.values
        print(f"   {mo}  n={len(g):4d}  NRFI hit {100*g.y_nrfi.mean():5.2f}%  "
              f"break-even {100*g.imp_n.mean():5.2f}%  blind ROI {100*u.mean():7.2f}%")
    jul = base[base.date >= "2026-07-01"]
    lo, hi = day_block_boot(jul, np.ones(len(jul), bool))
    print(f"   July blind-NRFI ROI {100*jul.units_n.mean():.2f}%  "
          f"day-block CI [{lo:.2f}, {hi:.2f}]  <-- the tide, not the model")

    # ---------- B. best-shot sweep ----------
    print("=" * 72)
    print("B.  BEST-SHOT SWEEP (optimistically biased: report the MAX cell)")
    grid = []
    n_cells = 0
    for lam in (0.5, 2.0, 5.0, 20.0, 100.0):
        for fname, fm in (("all", ALL), ("mkt-only", MKT_ONLY), ("no-mkt-price", NO_MKT)):
            for burn in (4, 8):
                df = run_wf(meta, X, lam, burn, 250, fm)
                cs = cells(df)
                n_cells += len(cs)
                best = max(cs, key=lambda t: t[3])
                grid.append(dict(lam=lam, feats=fname, burn=burn, n_pool=len(df),
                                 best_target=best[0], best_depth=str(best[1]),
                                 n=best[2], roi=best[3]))
    g = pd.DataFrame(grid).sort_values("roi", ascending=False)
    print(g.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))
    print(f"   total cells searched in sweep: {n_cells}")
    top = g.iloc[0]
    print(f"   SWEEP MAXIMUM: {top.best_target} {top.best_depth} lam={top.lam} "
          f"feats={top.feats} burn={top.burn}  ROI {top.roi:.2f}%  n={top.n}")

    # re-run the winner to get its CI + haircut + July-only behaviour
    fm = {"all": ALL, "mkt-only": MKT_ONLY, "no-mkt-price": NO_MKT}[top.feats]
    dfw = run_wf(meta, X, float(top.lam), int(top.burn), 250, fm)
    for c, d, n, roi, mask in cells(dfw):
        if c == top.best_target and str(d) == top.best_depth:
            lo, hi = day_block_boot(dfw, mask)
            uh = haircut_units(dfw[mask])
            print(f"   winner CI [{lo:.2f}, {hi:.2f}]   after 10c worse price "
                  f"{100*uh.mean():.2f}%   hit {100*dfw.y_nrfi.values[mask].mean():.2f}% "
                  f"vs break-even {100*dfw.imp_n.values[mask].mean():.2f}%")
            sub = dfw[mask]
            for half, lab in ((sub[sub.date < "2026-07-01"], "May-Jun"),
                              (sub[sub.date >= "2026-07-01"], "July")):
                if len(half):
                    print(f"     {lab:8s} n={len(half):3d} ROI {100*half.units_n.mean():7.2f}%")

    # ---------- C. search-exposure null ----------
    print("=" * 72)
    print("C.  SEARCH-EXPOSURE NULL (outcomes shuffled WITHIN day, 24-cell search)")
    real_best = max(c[3] for c in cells(base))
    nulls = []
    for it in range(60):
        yv = meta.y_nrfi.values.copy()
        for _, idx in meta.groupby(meta.date).groups.items():
            idx = np.asarray(list(idx))
            yv[idx] = RNG.permutation(yv[idx])
        d = run_wf(meta, X, 5.0, 4, 250, ALL, y_override=yv)
        nulls.append(max(c[3] for c in cells(d)))
    nulls = np.array(nulls)
    print(f"   real best-of-24 ROI      {real_best:7.2f}%")
    print(f"   null best-of-24 ROI      mean {nulls.mean():6.2f}%  "
          f"p50 {np.percentile(nulls,50):6.2f}%  p95 {np.percentile(nulls,95):6.2f}%  "
          f"max {nulls.max():6.2f}%")
    print(f"   P(null best >= real best) = {(nulls >= real_best).mean():.3f}")

    # ---------- D. discrimination ----------
    print("=" * 72)
    print("D.  DISCRIMINATION on the NRFI outcome (fitted probability, not EV)")
    from sklearn.metrics import roc_auc_score, log_loss
    y = base.y_nrfi.values
    for nm, s in [("market de-vig", base.dev_n.values),
                  ("shipped p_live", base.p_live.values),
                  ("T_OFFSET prob", base.P_T_OFFSET.values),
                  ("T_WEIGHT prob", base.P_T_WEIGHT.values)]:
        print(f"   {nm:16s} AUC {roc_auc_score(y, s):.4f}   "
              f"logloss {log_loss(y, np.clip(s,1e-6,1-1e-6)):.4f}   "
              f"mean {s.mean():.4f}")

    # ---------- E. break-even arithmetic ----------
    print("=" * 72)
    print("E.  THE WALL -- hit rate vs price, pooled and in the best cells")
    print(f"   pool: NRFI hit {100*base.y_nrfi.mean():.2f}%  "
          f"vig-on break-even {100*base.imp_n.mean():.2f}%  "
          f"gap {100*(base.imp_n.mean()-base.y_nrfi.mean()):.2f}pp")
    print(f"   de-vigged fair break-even {100*base.dev_n.mean():.2f}%  "
          f"gap vs actual {100*(base.dev_n.mean()-base.y_nrfi.mean()):.2f}pp")
    for c, d, n, roi, mask in cells(base):
        if str(d) in ("0.1", "0.2"):
            print(f"   {c:9s} {d}  n={n:3d}  hit {100*base.y_nrfi.values[mask].mean():5.2f}%  "
                  f"needs {100*base.imp_n.values[mask].mean():5.2f}%  "
                  f"short by {100*(base.imp_n.values[mask].mean()-base.y_nrfi.values[mask].mean()):5.2f}pp")


if __name__ == "__main__":
    main()

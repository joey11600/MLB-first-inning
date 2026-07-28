#!/usr/bin/env python3
"""P06 -- the decisive test.

"Learn where the book is wrong" only pays if the book's error VARIES with
something observable.  If DK's error is a single constant level shift,
there is nothing to model: the entire finding collapses to "pick a side".

Test: walk-forward, compare
    (a) offset model with ONLY an intercept    -- learns one number
    (b) offset model with all 42 features + p  -- learns a function
on out-of-sample log-loss.  If (b) does not beat (a), the book's error is
a constant and no profitability model can exist.

Also: OOS R^2 of regressing the raw residual (y - book_p) on the features.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

sys.path.insert(0, str(Path(__file__).resolve().parent))
import price_common as pc

d = pc.load()
d["l_model"] = pc.logit(d["p_model"])
FULL = pc.FEATS + ["l_model"]
MIN_TRAIN = 250


def offset_fit(tr, te, cols, C=0.05, iters=60):
    off_tr = pc.logit(tr["book_nrfi"].values)
    off_te = pc.logit(te["book_nrfi"].values)
    if cols:
        sc = StandardScaler().fit(tr[cols].values)
        X = np.c_[np.ones(len(tr)), sc.transform(tr[cols].values)]
        Xt = np.c_[np.ones(len(te)), sc.transform(te[cols].values)]
    else:
        X = np.ones((len(tr), 1))
        Xt = np.ones((len(te), 1))
    y = tr["y_nrfi"].values.astype(float)
    w = np.zeros(X.shape[1])
    lam = 1.0 / C
    pen = np.diag(np.r_[0, np.ones(len(w) - 1)]) if len(w) > 1 else np.zeros((1, 1))
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(off_tr + X @ w)))
        g = X.T @ (p - y) + lam * (np.r_[0, w[1:]] if len(w) > 1 else 0.0)
        Wt = p * (1 - p) + 1e-9
        H = X.T @ (X * Wt[:, None]) + lam * pen
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        w -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return 1 / (1 + np.exp(-(off_te + Xt @ w))), w


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


pieces = []
ws = []
for wk in sorted(d["dt"].dt.to_period("W").unique()):
    te = d[d["dt"].dt.to_period("W") == wk]
    tr = d[d["dt"] < te["dt"].min()]
    if len(tr) < MIN_TRAIN or len(te) < 8:
        continue
    p_int, w_int = offset_fit(tr, te, [])
    p_full, w_full = offset_fit(tr, te, FULL)
    ws.append(w_full)
    r = te[["date", "y_nrfi", "book_nrfi", "pay_nrfi", "u_nrfi", "u_yrfi",
            "pay_yrfi", "p_model"]].copy()
    r["p_int"] = p_int
    r["p_full"] = p_full
    # residual regression
    rr = make_pipeline(StandardScaler(), Ridge(alpha=100.0))
    resid_tr = tr["y_nrfi"].values - tr["book_nrfi"].values
    rr.fit(tr[FULL].values, resid_tr)
    r["resid_hat"] = rr.predict(te[FULL].values)
    r["resid"] = te["y_nrfi"].values - te["book_nrfi"].values
    pieces.append(r)

W = pd.concat(pieces, ignore_index=True)
n = len(W)
print(f"pooled walk-forward OOS n={n}")
print()
print("=== does modelling the book's error as a FUNCTION beat modelling it "
      "as a CONSTANT? ===")
print(f"  raw DK de-vigged book                 OOS log-loss = {ll(W.y_nrfi, W.book_nrfi):.5f}")
print(f"  book + learned INTERCEPT only         OOS log-loss = {ll(W.y_nrfi, W.p_int):.5f}")
print(f"  book + intercept + 43 features        OOS log-loss = {ll(W.y_nrfi, W.p_full):.5f}")
print(f"  production p_nrfi (no book)           OOS log-loss = {ll(W.y_nrfi, W.p_model):.5f}")
print()
print(f"  AUC  book={pc.auc(W.y_nrfi, W.book_nrfi):.4f}  "
      f"intercept-only={pc.auc(W.y_nrfi, W.p_int):.4f}  "
      f"full={pc.auc(W.y_nrfi, W.p_full):.4f}")
print(f"  mean learned intercept shift (logit)  = {np.mean([w[0] for w in ws]):+.4f}"
      f"  -> {100*(1/(1+np.exp(-(pc.logit(W.book_nrfi.mean())+np.mean([w[0] for w in ws]))))-W.book_nrfi.mean()):+.2f}pp")
print(f"  mean max|feature coef|                = {np.mean([np.abs(w[1:]).max() for w in ws]):.4f}")
print()


def bootdiff(col_a, col_b, B=4000, seed=3):
    rng = np.random.default_rng(seed)
    g = [x for _, x in W.groupby("date")]
    idx = rng.integers(0, len(g), size=(B, len(g)))
    out = []
    for row in idx:
        z = pd.concat([g[i] for i in row], ignore_index=True)
        out.append(ll(z.y_nrfi, z[col_a]) - ll(z.y_nrfi, z[col_b]))
    a = np.array(out)
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))


lo, hi = bootdiff("p_int", "p_full", B=800)
print(f"  logloss(intercept-only) - logloss(full features) = "
      f"{ll(W.y_nrfi, W.p_int) - ll(W.y_nrfi, W.p_full):+.5f}  "
      f"day-block 95% CI [{lo:+.5f}, {hi:+.5f}]")
print("  (positive => features help; CI spanning/below 0 => the book's")
print("   error is a constant, not a function)")
print()

ssr = np.sum((W.resid - W.resid_hat) ** 2)
sst = np.sum((W.resid - W.resid.mean()) ** 2)
print(f"=== OOS R^2 of predicting the book's error (y - book_p) from features ===")
print(f"  R^2 = {1 - ssr/sst:+.5f}   corr(pred, actual) = "
      f"{np.corrcoef(W.resid_hat, W.resid)[0,1]:+.4f}")
print(f"  mean actual residual = {W.resid.mean():+.4f}  "
      f"sd of PREDICTED residual = {W.resid_hat.std():.4f} "
      f"(sd of actual = {W.resid.std():.4f})")
print()

print("=== so the only lever is SIDE.  Bet-everything, real DK prices ===")
for lbl, c in (("NRFI every game", "u_nrfi"), ("YRFI every game", "u_yrfi")):
    lo, hi = pc.day_boot_mean(W, c, B=6000, scale=100.0)
    print(f"  {lbl:18s} n={n}  {W[c].sum():+8.2f}u  ROI {100*W[c].mean():+6.2f}%  "
          f"95% CI [{lo:+.2f}, {hi:+.2f}]")

full = pc.load()
print()
print("  (whole priced 2026 universe, not just walk-forward window)")
for lbl, c in (("NRFI every game", "u_nrfi"), ("YRFI every game", "u_yrfi")):
    lo, hi = pc.day_boot_mean(full, c, B=6000, scale=100.0)
    print(f"  {lbl:18s} n={len(full)} {full[c].sum():+8.2f}u  ROI {100*full[c].mean():+6.2f}%  "
          f"95% CI [{lo:+.2f}, {hi:+.2f}]")

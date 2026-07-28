#!/usr/bin/env python3
"""P03 -- CHANGE THE TARGET.  Train on PROFITABILITY, not on the outcome.

Three genuinely different objectives, all fed the book's implied
probability as an input so the model can learn "where DK is wrong"
rather than "what will happen":

  T1  PROFIT REGRESSION  -- target u = +payout on a win, -1 on a loss.
      Unlike log-loss this weights a win by its price, so the fit is
      pulled toward EV rather than likelihood.
  T2  BOOK-RESIDUAL      -- logistic with logit(book) as a fixed OFFSET.
      The model can ONLY learn a correction to the market.
  T3  EV FROM A PRICE-AWARE CLASSIFIER -- classify y with the book as a
      feature, then EV = p*payout - (1-p).

Everything is evaluated OUT OF SAMPLE on real captured DraftKings prices.

POSITIVE CONTROL: the identical machinery is run on the YRFI side, where
an edge is already known to exist.  If the method finds YRFI and not
NRFI, the NRFI null is a property of NRFI, not of the method.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

sys.path.insert(0, str(Path(__file__).resolve().parent))
import price_common as pc

d = pc.load()
d["l_model"] = pc.logit(d["p_model"])
d["l_book_n"] = pc.logit(d["book_nrfi"])
d["l_book_y"] = pc.logit(1 - d["book_nrfi"])
COLS = pc.FEATS + ["l_model", "l_book_n"]

CUT = pd.Timestamp("2026-06-30")


def offset_logit(tr, te, cols, off_tr, off_te, C=0.05, iters=60):
    """Logistic regression with a FIXED offset (statsmodels-free).
    Fits w on standardized X to model  y ~ sigma(offset + Xw)  by simple
    Newton/IRLS-free gradient descent with L2 shrinkage."""
    sc = StandardScaler().fit(tr[cols].values)
    X = np.c_[np.ones(len(tr)), sc.transform(tr[cols].values)]
    Xt = np.c_[np.ones(len(te)), sc.transform(te[cols].values)]
    y = tr["y_nrfi"].values.astype(float)
    w = np.zeros(X.shape[1])
    lam = 1.0 / C
    for _ in range(iters):
        eta = off_tr + X @ w
        p = 1 / (1 + np.exp(-eta))
        g = X.T @ (p - y) + lam * np.r_[0, w[1:]]
        W = p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + lam * np.diag(np.r_[0, np.ones(len(w) - 1)])
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return 1 / (1 + np.exp(-(off_te + Xt @ w))), w


def report(te, score, ucol, label, fracs=(1.0, 0.5, 0.3, 0.2, 0.1)):
    """Rank test rows by `score` (higher = more bettable) and report
    realized ROI on the top slice."""
    t = te.copy()
    t["_s"] = score
    t = t.sort_values("_s", ascending=False)
    print(f"  {label}")
    print(f"    {'top':>6} {'n':>5} {'hit%':>7} {'units':>9} {'ROI%':>8} {'ROI 95% CI':>22}")
    for f in fracs:
        k = int(round(len(t) * f))
        if k < 25:
            continue
        s = t.head(k)
        u = s[ucol].sum()
        roi = 100 * u / k
        win = (s[ucol] > 0).mean()

        lo, hi = pc.day_boot_mean(s, ucol, B=4000, scale=100.0)
        print(f"    {f:>6.0%} {k:>5} {100*win:>6.1f}% {u:>+9.2f} {roi:>+8.2f} "
              f"[{lo:>+7.2f}, {hi:>+7.2f}]")


def run(side):
    ucol = "u_nrfi" if side == "NRFI" else "u_yrfi"
    paycol = "pay_nrfi" if side == "NRFI" else "pay_yrfi"
    ytgt = "y_nrfi" if side == "NRFI" else "y_yrfi"

    tr = d[d.dt <= CUT].copy()
    te = d[d.dt > CUT].copy()
    for f in (tr, te):
        f["y_yrfi"] = 1 - f["y_nrfi"]

    print(f"########## {side} -- train n={len(tr)} (<= {CUT.date()}), "
          f"test n={len(te)} (July) ##########")
    flat = te[ucol].sum()
    print(f"  baseline: bet EVERY July game {side} at the real DK price -> "
          f"{flat:+.2f}u  ROI {100*flat/len(te):+.2f}%  hit {100*(te[ucol]>0).mean():.1f}%")
    print()

    # --- T1 profit regression -----------------------------------------
    m = make_pipeline(StandardScaler(), Ridge(alpha=200.0))
    m.fit(tr[COLS].values, tr[ucol].values)
    s1 = m.predict(te[COLS].values)
    report(te, s1, ucol, "T1  profit regression (target = units won/lost)")
    print()

    # --- T2 book-residual (offset) ------------------------------------
    off_tr = pc.logit(tr["book_nrfi"].values)
    off_te = pc.logit(te["book_nrfi"].values)
    p2, w2 = offset_logit(tr, te, pc.FEATS + ["l_model"], off_tr, off_te)
    p2s = p2 if side == "NRFI" else 1 - p2
    ev2 = p2s * te[paycol].values - (1 - p2s)
    report(te, ev2, ucol, "T2  book-residual offset model -> EV")
    print(f"    intercept shift={w2[0]:+.4f}  max|coef|={np.abs(w2[1:]).max():.4f}")
    print()

    # --- T3 price-aware classifier -> EV ------------------------------
    c = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=4000))
    c.fit(tr[COLS].values, tr["y_nrfi"].values)
    p3 = c.predict_proba(te[COLS].values)[:, 1]
    p3s = p3 if side == "NRFI" else 1 - p3
    ev3 = p3s * te[paycol].values - (1 - p3s)
    report(te, ev3, ucol, "T3  price-aware classifier -> EV")
    print(f"    EV>0 count: {int((ev3>0).sum())}/{len(te)}")
    print()

    # --- reference: production model EV, no price learning ------------
    pm = te["p_model"].values
    pms = pm if side == "NRFI" else 1 - pm
    ev0 = pms * te[paycol].values - (1 - pms)
    report(te, ev0, ucol, "REF production p_nrfi -> EV (the incumbent rule)")
    print()


run("NRFI")
run("YRFI")

#!/usr/bin/env python3
"""P04 -- the P03 targets, but WALK-FORWARD across the whole priced season
so the out-of-sample pool is ~800 bets instead of 321.

At each week W the model is refit on every priced game strictly BEFORE W
and scores only week W.  Nothing in the scoring set was ever trained on.

Also answers "what is the profit-target model actually selecting?" -- if
its score is just a proxy for a long NRFI price, it is the already-dead
'bet NRFI when cheap' rule wearing a new hat.
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
d["y_yrfi"] = 1 - d["y_nrfi"]
COLS = pc.FEATS + ["l_model", "l_book_n"]
MIN_TRAIN = 250


def offset_logit(tr, te, cols, C=0.05, iters=50):
    sc = StandardScaler().fit(tr[cols].values)
    X = np.c_[np.ones(len(tr)), sc.transform(tr[cols].values)]
    Xt = np.c_[np.ones(len(te)), sc.transform(te[cols].values)]
    y = tr["y_nrfi"].values.astype(float)
    off_tr = pc.logit(tr["book_nrfi"].values)
    off_te = pc.logit(te["book_nrfi"].values)
    w = np.zeros(X.shape[1])
    lam = 1.0 / C
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(off_tr + X @ w)))
        g = X.T @ (p - y) + lam * np.r_[0, w[1:]]
        W = p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + lam * np.diag(np.r_[0, np.ones(len(w) - 1)])
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        w -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return 1 / (1 + np.exp(-(off_te + Xt @ w)))


rows = []
weeks = sorted(d["dt"].dt.to_period("W").unique())
for wk in weeks:
    te = d[d["dt"].dt.to_period("W") == wk]
    tr = d[d["dt"] < te["dt"].min()]
    if len(tr) < MIN_TRAIN or len(te) < 8:
        continue
    z = te.copy()

    r1 = make_pipeline(StandardScaler(), Ridge(alpha=200.0))
    r1.fit(tr[COLS].values, tr["u_nrfi"].values)
    z["s_T1n"] = r1.predict(te[COLS].values)
    r1y = make_pipeline(StandardScaler(), Ridge(alpha=200.0))
    r1y.fit(tr[COLS].values, tr["u_yrfi"].values)
    z["s_T1y"] = r1y.predict(te[COLS].values)

    p2 = offset_logit(tr, te, pc.FEATS + ["l_model"])
    z["s_T2n"] = p2 * te["pay_nrfi"].values - (1 - p2)
    z["s_T2y"] = (1 - p2) * te["pay_yrfi"].values - p2

    c3 = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=4000))
    c3.fit(tr[COLS].values, tr["y_nrfi"].values)
    p3 = c3.predict_proba(te[COLS].values)[:, 1]
    z["s_T3n"] = p3 * te["pay_nrfi"].values - (1 - p3)
    z["s_T3y"] = (1 - p3) * te["pay_yrfi"].values - p3

    pm = te["p_model"].values
    z["s_REFn"] = pm * te["pay_nrfi"].values - (1 - pm)
    z["s_REFy"] = (1 - pm) * te["pay_yrfi"].values - pm
    rows.append(z)

W = pd.concat(rows, ignore_index=True)
print(f"pooled walk-forward OOS rows n={len(W)}  "
      f"({W.date.min()} .. {W.date.max()})  NRFI rate={W.y_nrfi.mean():.4f}")
print(f"bet-everything NRFI: {W.u_nrfi.sum():+.2f}u ({100*W.u_nrfi.mean():+.2f}%)   "
      f"YRFI: {W.u_yrfi.sum():+.2f}u ({100*W.u_yrfi.mean():+.2f}%)")
print()

FRACS = (0.5, 0.3, 0.2, 0.1, 0.05)


def slice_report(scol, ucol, label):
    t = W.sort_values(scol, ascending=False)
    print(f"  {label}")
    print(f"    {'top':>5} {'n':>5} {'hit%':>7} {'units':>9} {'ROI%':>8} "
          f"{'ROI 95% CI':>22} {'medPrice':>9}")
    for f in FRACS:
        k = int(round(len(t) * f))
        if k < 30:
            continue
        s = t.head(k)
        lo, hi = pc.day_boot_mean(s, ucol, B=4000, scale=100.0)
        pcol = "o_nrfi" if ucol == "u_nrfi" else "o_yrfi"
        print(f"    {f:>5.0%} {k:>5} {100*(s[ucol]>0).mean():>6.1f}% "
              f"{s[ucol].sum():>+9.2f} {100*s[ucol].mean():>+8.2f} "
              f"[{lo:>+7.2f}, {hi:>+7.2f}] {s[pcol].median():>+9.0f}")


for side, suf, ucol in (("NRFI", "n", "u_nrfi"), ("YRFI", "y", "u_yrfi")):
    print(f"########## {side} ##########")
    slice_report(f"s_T1{suf}", ucol, "T1  profit regression")
    slice_report(f"s_T2{suf}", ucol, "T2  book-residual offset -> EV")
    slice_report(f"s_T3{suf}", ucol, "T3  price-aware classifier -> EV")
    slice_report(f"s_REF{suf}", ucol, "REF production p_nrfi -> EV")
    print()

print("=== what is the score correlated with? (Spearman) ===")
for s in ["s_T1n", "s_T2n", "s_T3n"]:
    print(f"  {s:8s} vs de-vigged book NRFI p : {W[s].corr(W.book_nrfi, method='spearman'):+.3f}"
          f"   vs NRFI payout : {W[s].corr(W.pay_nrfi, method='spearman'):+.3f}"
          f"   vs production p_nrfi : {W[s].corr(W.p_model, method='spearman'):+.3f}")

W.to_csv(Path(__file__).parent / "p04_walkforward_scores.csv", index=False)
print("\nwrote p04_walkforward_scores.csv")

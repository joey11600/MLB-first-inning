#!/usr/bin/env python3
"""R6 -- interrogate the 'add the de-vigged market price as a feature' half
of the proposal specifically.  ANALYSIS ONLY.

Three questions:
  1. Is the captured price even usable as a pre-game feature?  47% of the
     odds_captured_at stamps are AFTER first pitch.
  2. Does the de-vigged book probability carry information our model does
     not?  (Joint logit: y ~ logit(p_model) + logit(book).)
  3. Even in the BEST case -- fit the price feature IN SAMPLE, no holdout at
     all -- can the resulting selection clear the pricing wall?  That is the
     ceiling; if the ceiling is under the wall the idea is dead regardless
     of estimation.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from price_common import load, logit, auc, FEATS  # noqa: E402
import r_common as R  # noqa: E402
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
try:
    import statsmodels.api as sm
except ModuleNotFoundError:
    sm = None

d = load(priced_only=True)
d["date"] = d["date"].astype(str)
y = d["y_nrfi"].to_numpy(int)

# ------------------------------------------------------------------- (1)
print("=== 1. is the captured price a PRE-GAME quantity? ===")
late = d["lead_h"] < 0
print(f"  captured after first pitch: {late.sum()}/{len(d)} ({100*late.mean():.1f}%)")
for lab, m in (("captured BEFORE first pitch", ~late), ("captured AFTER first pitch", late)):
    s = d[m]
    print(f"  {lab:28s} n={len(s):4d}  AUC(book_nrfi -> NRFI) = "
          f"{auc(s['y_nrfi'], s['book_nrfi']):.4f}   "
          f"AUC(model p)={auc(s['y_nrfi'], s['p_model']):.4f}")
print("  if the AFTER group's book AUC is materially higher, part of any")
print("  'price feature' gain is information that did not exist at bet time.")

# ------------------------------------------------------------------- (2)
print("\n=== 2. does the de-vigged book add information over our model? ===")
Xj = np.column_stack([np.ones(len(d)), logit(d["p_model"]), logit(d["book_nrfi"])])
if sm is not None:
    r = sm.Logit(y, Xj).fit(disp=0)
    names = ["const", "logit(p_model)", "logit(book_nrfi)"]
    for n, c, se, p in zip(names, r.params, r.bse, r.pvalues):
        print(f"  {n:20s} coef={c:+7.3f}  se={se:5.3f}  p={p:.4f}")
    print(f"  pseudo-R2 = {r.prsquared:.5f}")
else:
    # sklearn logit + day-block bootstrap for the standard errors
    m = LogisticRegression(C=1e6, max_iter=5000).fit(Xj[:, 1:], y)
    rng = np.random.default_rng(5)
    days = [g.index.to_numpy() for _, g in d.groupby("date")]
    boots = []
    for _ in range(600):
        idx = np.concatenate([days[i] for i in
                              rng.integers(0, len(days), size=len(days))])
        try:
            mb = LogisticRegression(C=1e6, max_iter=3000).fit(Xj[idx, 1:], y[idx])
            boots.append(mb.coef_[0])
        except Exception:
            pass
    boots = np.asarray(boots)
    for n, c, col in zip(["logit(p_model)", "logit(book_nrfi)"],
                         m.coef_[0], boots.T):
        lo, hi = np.percentile(col, [2.5, 97.5])
        print(f"  {n:20s} coef={c:+7.3f}  day-block 95% CI [{lo:+.3f}, {hi:+.3f}]"
              f"   {'SIGNIFICANT' if lo*hi > 0 else 'spans zero'}")
print(f"  AUC model-only = {auc(y, d['p_model']):.4f}")
print(f"  AUC book-only  = {auc(y, d['book_nrfi']):.4f}")
print(f"  AUC 50/50 blend= {auc(y, 0.5*logit(d['p_model'])+0.5*logit(d['book_nrfi'])):.4f}")

# ------------------------------------------------------------------- (3)
print("\n=== 3. CEILING: fit IN SAMPLE (no holdout), then bet the top-k ===")
print("    every number below is optimistically biased on purpose.")
sc = StandardScaler()
Xb = sc.fit_transform(np.column_stack([
    d[FEATS].to_numpy(float), logit(d["p_model"]), logit(d["book_nrfi"]),
    d["pay_nrfi"].to_numpy(float)]))
u = d["u_nrfi"].to_numpy(float)

variants = {
    "in-sample ridge PROFIT+price": Ridge(alpha=1.0).fit(Xb, u).predict(Xb),
    "in-sample logit outcome+price":
        LogisticRegression(C=1.0, max_iter=5000).fit(Xb, y).predict_proba(Xb)[:, 1]
        * (1 + d["pay_nrfi"].to_numpy(float)) - 1,
}
for name, s in variants.items():
    line = f"  {name:32s}"
    for k in R.DEPTHS:
        n, uu, roi, hit, sub = R.topk_units(d, s, k)
        be = 100 * sub["imp_nrfi"].mean()
        line += f" {int(k*100):>2d}%:{uu:+6.1f}u({hit-be:+5.1f}pp)"
    print(line)

print("\n  PERFECT-FORESIGHT reference (bet only games that actually went NRFI,")
print("  top 20% by price) -- the arithmetic maximum, for scale:")
best = d[d["y_nrfi"] == 1].nlargest(int(0.20 * len(d)), "pay_nrfi")
print(f"    n={len(best)}  units={best['u_nrfi'].sum():+.1f}u")

# ------------------------------------------------------------------- (4)
print("\n=== 4. what the price feature actually does to the SELECTION ===")
print("  correlation of each score with the price itself (Spearman vs pay_nrfi):")
tr = d[d["date"] < "2026-06-20"]; te = d[d["date"] >= "2026-06-20"]
for name in ("B_shipped_EV", "G_ridge_PROFIT", "H_ridge_PROFIT+price",
             "C_bookEV"):
    s = pd.Series(R.MODELS[name](tr, te), index=te.index)
    print(f"    {name:22s} rho(score, payout) = "
          f"{s.corr(te['pay_nrfi'], method='spearman'):+.3f}   "
          f"rho(score, model_p) = {s.corr(te['p_model'], method='spearman'):+.3f}")
print("  a strong positive rho with payout means the 'new target' is mostly")
print("  a DOG FILTER: it bets the longest NRFI prices.  Those are long")
print("  because the market thinks the game is likely to score.")

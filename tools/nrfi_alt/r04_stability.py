#!/usr/bin/env python3
"""R04 -- stability / search-exposure attacks on the book-as-feature claim."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from r01_book_feature_refute import *

d = load()

print("=== 1. RAW market advantage over the model, whole priced universe ===")
t = d.copy()
f = lambda x: auc(x.y, x.book) - auc(x.y, x.p_model)
lo, hi = dayboot(t, f)
print(f"   AUC(book) - AUC(model) = {f(t):+.4f}  day-block 95% CI [{lo:+.4f},{hi:+.4f}]  n={len(t)}")
print(f"   -> CI spans zero: {lo < 0 < hi}\n")

print("=== 2. Is the market advantage stable within the one season we have? ===")
mid = d.dt.quantile(0.5)
for lab, s in [("first half", d[d.dt <= mid]), ("second half", d[d.dt > mid])]:
    print(f"   {lab:12s} n={len(s):>4}  AUC(book)={auc(s.y,s.book):.4f}  "
          f"AUC(model)={auc(s.y,s.p_model):.4f}  delta={auc(s.y,s.book)-auc(s.y,s.p_model):+.4f}")
for m in sorted(d.dt.dt.month.unique()):
    s = d[d.dt.dt.month == m]
    print(f"   month {m:>2}      n={len(s):>4}  AUC(book)={auc(s.y,s.book):.4f}  "
          f"AUC(model)={auc(s.y,s.p_model):.4f}  delta={auc(s.y,s.book)-auc(s.y,s.p_model):+.4f}")
print()

print("=== 3. Regularization sensitivity of the 'gain' (search exposure) ===")
cols_D = FEATS + ["l_model"]; cols_E = FEATS + ["l_model", "l_book"]
def fit(tr, te, cols, C):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=6000))
    m.fit(tr[cols].values, tr.y.values)
    return m.predict_proba(te[cols].values)[:, 1], m
cells = 0
print(f"   {'cut':12s} {'C':>7} {'dAUC(+book)':>12} {'book coef':>11}")
for cut in ["2026-05-31", "2026-06-15", "2026-06-30", "2026-07-15"]:
    c = pd.Timestamp(cut); tr, te = d[d.dt <= c], d[d.dt > c]
    for C in [0.01, 0.05, 0.2, 1.0]:
        cells += 1
        pD, _ = fit(tr, te, cols_D, C); pE, mE = fit(tr, te, cols_E, C)
        coef = mE[-1].coef_[0][cols_E.index("l_book")]
        print(f"   {cut:12s} {C:>7.2f} {auc(te.y,pE)-auc(te.y,pD):>+12.4f} {coef:>+11.4f}")
print(f"   cells searched here = {cells}  (plus 20 in r02) -> 36 total specs tried\n")

print("=== 4. What would the book feature have to be worth to clear the wall? ===")
be = d.o_n.map(implied)
print(f"   mean DK NRFI break-even {be.mean()*100:.2f}%  vs actual NRFI {d.y.mean()*100:.2f}%"
      f"  -> gap {(be.mean()-d.y.mean())*100:.2f}pp")
print("   A pure ranking improvement cannot move the base rate; it can only reorder.")
for q in [0.60, 0.75, 0.85, 0.95]:
    s = d[d.book >= d.book.quantile(q)]
    print(f"   book's OWN top {int((1-q)*100):>2}% NRFI picks: n={len(s):>4} hit={s.y.mean()*100:>5.1f}% "
          f"breakeven={s.o_n.map(implied).mean()*100:>5.1f}% "
          f"ROI={np.where(s.y==1,s.o_n.map(payout),-1.0).mean()*100:>6.2f}%")
print("   (if even the BOOK's own strongest NRFI opinions lose, no amount of")
print("    copying the book into our model can produce a winning NRFI bet.)")

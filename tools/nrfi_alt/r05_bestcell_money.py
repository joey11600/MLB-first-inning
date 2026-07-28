#!/usr/bin/env python3
"""R05 -- take the ONE cell the proposal cites as a win (train<=6/30, test Jul,
dAUC=+0.0094) and ask whether that AUC gain is worth a single dollar."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from r01_book_feature_refute import *

d = load()
cut = pd.Timestamp("2026-06-30")
tr, te = d[d.dt <= cut].copy(), d[d.dt > cut].copy()
cD, cE = FEATS + ["l_model"], FEATS + ["l_model", "l_book"]
def fit(cols, C=0.05):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=6000))
    m.fit(tr[cols].values, tr.y.values); return m.predict_proba(te[cols].values)[:, 1]
te["pD"], te["pE"] = fit(cD), fit(cE)
print(f"BEST CELL: train<=6/30 n={len(tr)}, test Jul n={len(te)}, days={te.date.nunique()}")
print(f"  AUC  D={auc(te.y,te.pD):.4f}  E={auc(te.y,te.pE):.4f}  delta={auc(te.y,te.pE)-auc(te.y,te.pD):+.4f}\n")

def econ(pcol, side, thr, cents=0):
    on = te.o_n.map(lambda o: worsen(o, cents)); oy = te.o_y.map(lambda o: worsen(o, cents))
    pn, py = on.map(payout), oy.map(payout); p = te[pcol]
    e = p*(pn+1)-1 if side == "NRFI" else (1-p)*(py+1)-1
    u = np.where(te.y == 1, pn, -1.0) if side == "NRFI" else np.where(te.y == 0, py, -1.0)
    m = (e > thr).values
    r = te[m].copy(); r["u"] = u[m]
    return r

print(f"{'side':5s} {'model':6s} {'thr':>5} {'n':>4} {'units':>8} {'ROI%':>7}   95% CI (day-block)")
for side in ["NRFI", "YRFI"]:
    for pcol, lab in [("pD", "D"), ("pE", "E+bk")]:
        for thr in [0.0, 0.02, 0.05, 0.10]:
            r = econ(pcol, side, thr)
            if len(r) < 5: print(f"{side:5s} {lab:6s} {thr:>5.2f} {len(r):>4}   --"); continue
            lo, hi = dayboot(r, lambda x: x.u.mean()*100)
            print(f"{side:5s} {lab:6s} {thr:>5.2f} {len(r):>4} {r.u.sum():>8.2f} "
                  f"{r.u.mean()*100:>7.2f}   [{lo:+7.2f},{hi:+7.2f}]")
print()
print("head-to-head units delta (E minus D), same threshold, real prices:")
for side in ["NRFI", "YRFI"]:
    for thr in [0.0, 0.02, 0.05, 0.10]:
        a, b = econ("pD", side, thr), econ("pE", side, thr)
        print(f"   {side} thr={thr:.2f}: D={a.u.sum():+7.2f}u (n={len(a)})  "
              f"E={b.u.sum():+7.2f}u (n={len(b)})  delta={b.u.sum()-a.u.sum():+7.2f}u")
print()
print("same, with 10 cents of worse pricing:")
for side in ["NRFI", "YRFI"]:
    for thr in [0.02, 0.05]:
        a, b = econ("pD", side, thr, 10), econ("pE", side, thr, 10)
        print(f"   {side} thr={thr:.2f}: D={a.u.sum():+7.2f}u (n={len(a)})  "
              f"E={b.u.sum():+7.2f}u (n={len(b)})  delta={b.u.sum()-a.u.sum():+7.2f}u")

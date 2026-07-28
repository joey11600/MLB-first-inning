#!/usr/bin/env python3
"""R02 -- forward-split AUC/log-loss for the book-as-feature proposal."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from r01_book_feature_refute import *

d = load()
SPECS = {
    "A model only":            ["l_model"],
    "B book only":             ["l_book"],
    "C model + book":          ["l_model", "l_book"],
    "D feats + model":         FEATS + ["l_model"],
    "E feats + model + book":  FEATS + ["l_model", "l_book"],
}

def fit(tr, te, cols, C=0.05):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=5000))
    m.fit(tr[cols].values, tr.y.values)
    return m.predict_proba(te[cols].values)[:, 1]

CUTS = ["2026-05-31", "2026-06-15", "2026-06-30", "2026-07-15"]
print(f"SEARCH EXPOSURE: {len(CUTS)} time cuts x {len(SPECS)} specs = {len(CUTS)*len(SPECS)} fits\n")
for cut in CUTS:
    c = pd.Timestamp(cut)
    tr, te = d[d.dt <= c], d[d.dt > c]
    P = {k: fit(tr, te, v) for k, v in SPECS.items()}
    print(f"=== cut {cut}  train n={len(tr)}  test n={len(te)}  test NRFI={te.y.mean():.4f} ===")
    for k in SPECS:
        print(f"   {k:24s} AUC={auc(te.y,P[k]):.4f}  LL={ll(te.y,P[k]):.4f}")
    t = te.copy(); t["_a"], t["_b"] = P["D feats + model"], P["E feats + model + book"]
    dA = lambda x: auc(x.y, x._b) - auc(x.y, x._a)
    dL = lambda x: ll(x.y, x._a) - ll(x.y, x._b)     # positive = book helps
    lo, hi = dayboot(t, dA); lo2, hi2 = dayboot(t, dL)
    print(f"   >>> dAUC(+book) = {dA(t):+.4f}  CI [{lo:+.4f},{hi:+.4f}]")
    print(f"   >>> dLL (+book) = {dL(t):+.4f}  CI [{lo2:+.4f},{hi2:+.4f}]  (>0 = book helps)")
    t["_m"], t["_k"] = P["A model only"], P["B book only"]
    dM = lambda x: auc(x.y, x._k) - auc(x.y, x._m)
    lo3, hi3 = dayboot(t, dM)
    print(f"   >>> AUC(book)-AUC(model) = {dM(t):+.4f}  CI [{lo3:+.4f},{hi3:+.4f}]\n")

# ---- expanding weekly walk-forward -----------------------------------
print("=== expanding-window weekly walk-forward (min 250 train rows) ===")
weeks = sorted(d.dt.dt.to_period("W").unique())
acc = {k: [] for k in SPECS}; used = 0
for w in weeks:
    te = d[d.dt.dt.to_period("W") == w]; tr = d[d.dt < te.dt.min()]
    if len(tr) < 250 or len(te) < 10: continue
    used += len(te)
    for k, v in SPECS.items():
        acc[k].append(pd.DataFrame({"y": te.y.values, "p": fit(tr, te, v),
                                    "date": te.date.values}))
print(f"    pooled test rows n={used}")
pool = {k: pd.concat(v, ignore_index=True) for k, v in acc.items() if v}
for k, z in pool.items():
    print(f"   {k:24s} AUC={auc(z.y,z.p):.4f}  LL={ll(z.y,z.p):.4f}")
t = pool["D feats + model"].rename(columns={"p": "_a"}); t["_b"] = pool["E feats + model + book"].p.values
lo, hi = dayboot(t, lambda x: auc(x.y, x._b) - auc(x.y, x._a))
print(f"   >>> dAUC(+book) = {auc(t.y,t._b)-auc(t.y,t._a):+.4f}  CI [{lo:+.4f},{hi:+.4f}]")
lo, hi = dayboot(t, lambda x: ll(x.y, x._a) - ll(x.y, x._b))
print(f"   >>> dLL (+book) = {ll(t.y,t._a)-ll(t.y,t._b):+.4f}  CI [{lo:+.4f},{hi:+.4f}]")

#!/usr/bin/env python3
"""R03 -- ECONOMICS. Take honest walk-forward out-of-sample probabilities
with and without the book feature, and ask whether the book feature makes
or loses money at real captured DraftKings prices.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from r01_book_feature_refute import *

d = load()
SPECS = {
    "D feats+model":       FEATS + ["l_model"],
    "E feats+model+book":  FEATS + ["l_model", "l_book"],
    "P production p_nrfi": None,
}

def fit(tr, te, cols, C=0.05):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=5000))
    m.fit(tr[cols].values, tr.y.values)
    return m.predict_proba(te[cols].values)[:, 1]

# walk-forward OOS probabilities, aligned row-for-row
parts = []
for w in sorted(d.dt.dt.to_period("W").unique()):
    te = d[d.dt.dt.to_period("W") == w]; tr = d[d.dt < te.dt.min()]
    if len(tr) < 250 or len(te) < 10: continue
    z = te.copy()
    z["pD"] = fit(tr, te, SPECS["D feats+model"])
    z["pE"] = fit(tr, te, SPECS["E feats+model+book"])
    z["pP"] = te.p_model.values
    parts.append(z)
W = pd.concat(parts, ignore_index=True)
print(f"walk-forward priced OOS universe n={len(W)}  days={W.date.nunique()}  "
      f"NRFI={W.y.mean():.4f}\n")

print("--- how much does adding the book SHRINK the model toward the market? ---")
for c in ["pD", "pE"]:
    print(f"  {c}: sd={W[c].std():.4f}  mean|p-book|={np.mean(np.abs(W[c]-W.book)):.4f}  "
          f"corr(p,book)={np.corrcoef(W[c],W.book)[0,1]:+.3f}")
print()

def bets(df, pcol, thr, side, cents=0):
    on = df.o_n.map(lambda o: worsen(o, cents)); oy = df.o_y.map(lambda o: worsen(o, cents))
    pn, py = on.map(payout), oy.map(payout)
    p = df[pcol]
    en = p*(pn+1) - 1; ey = (1-p)*(py+1) - 1
    un = np.where(df.y == 1, pn, -1.0); uy = np.where(df.y == 0, py, -1.0)
    if side == "NRFI":  m, u, hit = en > thr, un, df.y == 1
    elif side == "YRFI": m, u, hit = ey > thr, uy, df.y == 0
    else:
        m = (en > thr) | (ey > thr)
        pick_n = en >= ey
        u = np.where(pick_n, un, uy); hit = np.where(pick_n, df.y == 1, df.y == 0)
    r = df[m].copy(); r["u"] = np.asarray(u)[np.asarray(m)]; r["hit"] = np.asarray(hit)[np.asarray(m)]
    return r

for side in ["NRFI", "YRFI"]:
    print(f"===== {side} bets, walk-forward OOS, real captured DK prices =====")
    print(f"{'model':22s} {'thr':>5} {'n':>5} {'hit%':>7} {'units':>8} {'ROI%':>7}  day-block 95% CI on ROI")
    for pcol, lab in [("pP", "P production"), ("pD", "D feats+model"), ("pE", "E +book")]:
        for thr in [0.0, 0.02, 0.05, 0.10]:
            r = bets(W, pcol, thr, side)
            if len(r) < 5:
                print(f"{lab:22s} {thr:>5.2f} {len(r):>5}   --"); continue
            roi = r.u.mean()*100
            lo, hi = dayboot(r, lambda x: x.u.mean()*100)
            print(f"{lab:22s} {thr:>5.2f} {len(r):>5} {r.hit.mean()*100:>7.1f} "
                  f"{r.u.sum():>8.2f} {roi:>7.2f}  [{lo:+7.2f},{hi:+7.2f}]")
    print()

print("===== 10 CENTS OF WORSE PRICING (robustness) =====")
for side in ["NRFI", "YRFI"]:
    for pcol, lab in [("pD", "D feats+model"), ("pE", "E +book")]:
        for thr in [0.02, 0.05]:
            r0 = bets(W, pcol, thr, side, cents=0)
            r1 = bets(W, pcol, thr, side, cents=10)
            f = lambda r: f"n={len(r):>4} ROI={r.u.mean()*100:>6.2f}%" if len(r) else "n=0"
            print(f"{side:5s} {lab:16s} thr={thr:.2f}  fair: {f(r0)}   -10c: {f(r1)}")
print()

print("===== does E ever beat the 5.65pp NRFI wall? =====")
be = (W.o_n.map(implied)).mean()
print(f"  mean DK NRFI break-even = {be*100:.2f}%   actual NRFI rate = {W.y.mean()*100:.2f}%")
for pcol, lab in [("pD", "D"), ("pE", "E+book")]:
    for q in [0.5, 0.7, 0.8, 0.9]:
        cut = W[pcol].quantile(q)
        s = W[W[pcol] >= cut]
        u = np.where(s.y == 1, s.o_n.map(payout), -1.0)
        print(f"  {lab:6s} top {int((1-q)*100):>2}% by p_nrfi: n={len(s):>4} "
              f"NRFIhit={s.y.mean()*100:>5.1f}%  breakeven={s.o_n.map(implied).mean()*100:>5.1f}%  "
              f"ROI={u.mean()*100:>6.2f}%")

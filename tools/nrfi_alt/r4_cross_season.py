#!/usr/bin/env python3
"""R4 -- cross-season out-of-sample.  ANALYSIS ONLY.

Two structural facts this script establishes:

  1. The 2025 backtest has NO odds columns at all.  A price feature cannot
     be built there, and a profit target has to invent a constant price.
  2. With a CONSTANT price, the profit target is an EXACT affine transform
     of the 0/1 outcome target:  u = pay*y - 1*(1-y) = (1+pay)*y - 1.
     Least squares on u and least squares on y therefore produce the SAME
     ranking.  The 'different target' is only a different target where the
     price VARIES -- i.e. only on the 1,128 priced 2026 rows.

Then: train on 2025 (2,393 games, outcome target -- the only thing
possible), test on the 2026 priced universe at real captured prices.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[1]
from price_common import FEATS, load, logit  # noqa: E402
import r_common as R  # noqa: E402
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------- fact 1 & 2
d26 = load(priced_only=True)
d26["date"] = d26["date"].astype(str)

bt25 = pd.read_csv(ROOT / "data" / "backtests" /
                   "backtest_2025-04-01_to_2025-09-30_truepit.csv",
                   low_memory=False)
print("2025 backtest columns containing 'odds'/'market':",
      [c for c in bt25.columns if "odds" in c.lower() or "market" in c.lower()])
bt25 = bt25[bt25["actual_side"].isin(["NRFI", "YRFI"])].copy()
bt25["y_nrfi"] = (bt25["actual_side"] == "NRFI").astype(int)
for c in FEATS:
    if c not in bt25.columns:
        bt25[c] = np.nan
    bt25[c] = pd.to_numeric(bt25[c], errors="coerce")
bt25[FEATS] = bt25[FEATS].fillna(d26[FEATS].median(numeric_only=True))
print(f"2025 usable games: {len(bt25)}  NRFI rate {bt25['y_nrfi'].mean():.4f}")

# affine-identity proof: constant price -> identical ranking
X = StandardScaler().fit_transform(bt25[FEATS].to_numpy(float))
y = bt25["y_nrfi"].to_numpy(float)
u_const = np.where(y == 1, 100.0 / 110.0, -1.0)          # every game at -110
s_y = Ridge(alpha=50.0).fit(X, y).predict(X)
s_u = Ridge(alpha=50.0).fit(X, u_const).predict(X)
print("\nCONSTANT-PRICE IDENTITY CHECK (ridge on y vs ridge on profit):")
print(f"  Pearson  = {np.corrcoef(s_y, s_u)[0,1]:.10f}")
print(f"  Spearman = {pd.Series(s_y).corr(pd.Series(s_u), method='spearman'):.10f}")
print("  -> identical ordering.  A profit target is ONLY a new target where")
print("     the price varies, i.e. only on the 1,128 priced 2026 rows.")

# ------------------------------------------------- train 2025 -> test 2026
sc = StandardScaler().fit(bt25[FEATS].to_numpy(float))
Xtr = sc.transform(bt25[FEATS].to_numpy(float))
Xte = sc.transform(d26[FEATS].to_numpy(float))

print("\n=== TRAIN 2025 (n=%d) -> TEST 2026 priced (n=%d), real prices ==="
      % (len(bt25), len(d26)))
cands = {}
p = LogisticRegression(C=0.1, max_iter=4000).fit(Xtr, y).predict_proba(Xte)[:, 1]
cands["outcome_logit"] = p * (1 + d26["pay_nrfi"].to_numpy(float)) - 1
cands["ridge_PROFIT(-110)"] = Ridge(alpha=50.0).fit(Xtr, u_const).predict(Xte)
w = np.where(y == 1, 100.0 / 110.0, 1.0)
pw = LogisticRegression(C=0.1, max_iter=4000).fit(
    Xtr, y, sample_weight=w).predict_proba(Xte)[:, 1]
cands["payout_weighted"] = pw * (1 + d26["pay_nrfi"].to_numpy(float)) - 1
cands["shipped_EV"] = R.m_shipped_ev(None, d26)
cands["bookEV"] = R.m_book_ev(None, d26)

for name, s in cands.items():
    line = f"{name:22s}"
    for k in R.DEPTHS:
        n, u, roi, hit, sub = R.topk_units(d26, s, k)
        be = 100 * sub["imp_nrfi"].mean()
        line += f"  {int(k*100):>2d}%:{u:+7.1f}u({hit-be:+5.1f}pp)"
    print(line)

print("\nsame, top 5% and 10% with day-block CI:")
for name, s in cands.items():
    for k in (0.05, 0.10, 0.20):
        n, u, roi, hit, sub = R.topk_units(d26, s, k)
        lo, hi = R.day_boot_roi(sub)
        be = 100 * sub["imp_nrfi"].mean()
        print(f"  {name:22s} top{int(k*100):3d}%  n={n:4d}  hit={hit:5.1f}%  "
              f"need={be:5.1f}%  edge={hit-be:+6.2f}pp  units={u:+7.2f}  "
              f"ROI={roi:+6.2f}%  CI[{lo:+6.1f},{hi:+6.1f}]")

# --------------------------------------- reverse: train 2026 profit -> 2025
print("\n=== REVERSE: train the PROFIT target on priced 2026 -> score 2025 ===")
print("(2025 has no odds, so only the hit rate at each depth is measurable)")
sc2 = StandardScaler().fit(d26[FEATS].to_numpy(float))
m = Ridge(alpha=50.0).fit(sc2.transform(d26[FEATS].to_numpy(float)),
                          d26["u_nrfi"].to_numpy(float))
s25 = m.predict(sc2.transform(bt25[FEATS].to_numpy(float)))
base = bt25["y_nrfi"].mean()
for k in R.DEPTHS:
    n = int(round(k * len(bt25)))
    idx = np.argsort(-s25)[:n]
    print(f"  top{int(k*100):3d}%  n={n:4d}  NRFI hit={100*bt25['y_nrfi'].to_numpy()[idx].mean():5.2f}%"
          f"   (2025 base rate {100*base:.2f}%)   lift={100*(bt25['y_nrfi'].to_numpy()[idx].mean()-base):+5.2f}pp")
print("  a real NRFI-selection skill would show a solid positive lift here;")
print("  note a +2pp lift is worth nothing against a 5.6pp pricing wall.")

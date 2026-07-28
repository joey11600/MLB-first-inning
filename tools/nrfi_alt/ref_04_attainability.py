#!/usr/bin/env python3
"""Can the discrimination gap actually be closed, out of sample, and does
closing it make money at real prices?  READ-ONLY."""
from __future__ import annotations
import sys, math
from pathlib import Path
import numpy as np
from scipy.stats import norm, spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))
import alt_common as AC  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402


def f(s):
    try:
        v = float(s)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return (100.0 / (o + 100.0)) if o > 0 else (abs(o) / (abs(o) + 100.0))


D26 = AC.load("2026picks")
D25 = AC.load("2025bt")

p26, y26, dt26 = D26["cal"], D26["y"], D26["dates"]
X26 = np.hstack([D26["X_t1"], D26["X_b1"]])
o_n = np.array([f(r.get("market_nrfi_odds")) or np.nan for r in D26["rows"]])
priced = np.isfinite(o_n) & np.isfinite(p26)
pay = np.array([payout(v) if np.isfinite(v) else np.nan for v in o_n])
be = np.array([implied(v) if np.isfinite(v) else np.nan for v in o_n])
HI = np.where(priced & (p26 >= 0.50))[0]

p25, y25 = D25["cal"], D25["y"]
X25 = np.hstack([D25["X_t1"], D25["X_b1"]])
HI25 = np.where(p25 >= 0.50)[0]


def roi(idx, cents=0.0):
    if len(idx) == 0:
        return np.nan
    o = o_n[idx] - cents
    return float(np.mean(np.where(y26[idx] == 1,
                                  np.array([payout(v) for v in o]), -1.0)))


def clean(X):
    X = np.array(X, float)
    X[~np.isfinite(X)] = 0.0
    return X


print("### ATTACK 7: honestly TRY to close the gap.")
print("  Fit a fresh in-regime logistic (all Phase E.3 features, both halves)")
print("  on 2025 NRFI-regime games; test on 2026 priced NRFI-regime games.")
sc = StandardScaler().fit(clean(X25[HI25]))
clf = LogisticRegression(max_iter=2000, C=0.1).fit(sc.transform(clean(X25[HI25])), y25[HI25])
tr = clf.predict_proba(sc.transform(clean(X25[HI25])))[:, 1]
te = clf.predict_proba(sc.transform(clean(X26[HI])))[:, 1]
print(f"  in-sample 2025 in-regime AUC = {AC.auc(tr, y25[HI25]):.4f}  (n={len(HI25)})")
print(f"  OUT-OF-SAMPLE 2026 in-regime AUC = {AC.auc(te, y26[HI]):.4f}  (n={len(HI)})")
k = max(1, int(round(0.25 * len(HI))))
top = HI[np.argsort(-te)[:k]]
print(f"  top 25% (n={k}): NRFIhit={y26[top].mean():.4f}  BE={np.mean(be[top]):.4f}  "
      f"ROI={roi(top)*100:+.2f}%   (-10c: {roi(top,10)*100:+.2f}%)")

print("\n  Same, trained on 2026 first-half days, tested on 2026 second-half:")
udays = np.unique(dt26[HI])
cut = udays[len(udays) // 2]
trI = HI[dt26[HI] < cut]
teI = HI[dt26[HI] >= cut]
sc2 = StandardScaler().fit(clean(X26[trI]))
clf2 = LogisticRegression(max_iter=2000, C=0.1).fit(sc2.transform(clean(X26[trI])), y26[trI])
te2 = clf2.predict_proba(sc2.transform(clean(X26[teI])))[:, 1]
print(f"  train n={len(trI)} in-sample AUC="
      f"{AC.auc(clf2.predict_proba(sc2.transform(clean(X26[trI])))[:,1], y26[trI]):.4f}")
print(f"  test  n={len(teI)} OOS in-regime AUC={AC.auc(te2, y26[teI]):.4f}")
k2 = max(1, int(round(0.25 * len(teI))))
top2 = teI[np.argsort(-te2)[:k2]]
print(f"  top 25% (n={k2}): NRFIhit={y26[top2].mean():.4f}  ROI={roi(top2)*100:+.2f}%")

# ------------------------------------------------------------------
print("\n### ATTACK 8: price-AWARE ceiling.  The synthetic score in the")
print("  proposal is independent of the DK price. Any real NRFI-discriminating")
print("  score is correlated with the market's NRFI opinion.")
rho_model = spearmanr(p26[HI], be[HI]).statistic
print(f"  observed Spearman(model p_nrfi, break-even) in HI regime = {rho_model:+.3f}")
zbe = (be[HI] - be[HI].mean()) / be[HI].std()


def synth_corr(ys, target_auc, w, rng):
    """score = mu*y + w*z_be + noise, mu re-solved so AUC hits target."""
    base = w * zbe + rng.standard_normal(len(ys))
    lo, hi_ = 0.0, 6.0
    for _ in range(30):
        mid = (lo + hi_) / 2
        a = AC.auc(mid * ys + base, ys)
        if a < target_auc:
            lo = mid
        else:
            hi_ = mid
    return (lo + hi_) / 2 * ys + base


for w in (0.0, 0.5, 1.0, 1.5):
    rng = np.random.default_rng(21)
    for a in (0.58, 0.65):
        vals, rhos = [], []
        for _ in range(800):
            s = synth_corr(y26[HI], a, w, rng)
            kk = max(1, int(round(0.25 * len(HI))))
            sel = HI[np.argsort(-s)[:kk]]
            vals.append(roi(sel))
            rhos.append(spearmanr(s, be[HI]).statistic)
        vals = np.array(vals)
        print(f"  w={w:.1f} rho(s,BE)={np.mean(rhos):+.3f}  AUC={a:.2f}  "
              f"meanROI={vals.mean()*100:+6.2f}%  P(>0)={np.mean(vals>0):.3f}")

#!/usr/bin/env python3
"""Fully-stressed ceiling: day-block resampling + a synthetic score whose
correlation with the DK price matches what the real model score shows,
optionally at 10 cents worse pricing.  READ-ONLY."""
from __future__ import annotations
import sys, math
from pathlib import Path
import numpy as np
from scipy.stats import norm, spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))
import alt_common as AC  # noqa: E402


def f(s):
    try:
        v = float(s)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


D = AC.load("2026picks")
p, y, dates = D["cal"], D["y"], D["dates"]
o_n = np.array([f(r.get("market_nrfi_odds")) or np.nan for r in D["rows"]])
priced = np.isfinite(o_n) & np.isfinite(p)
HI = np.where(priced & (p >= 0.50))[0]
zbe_all = np.full(len(p), np.nan)
b = np.array([(100.0 / (v + 100.0)) if v > 0 else (abs(v) / (abs(v) + 100.0))
              if np.isfinite(v) else np.nan for v in o_n])
zbe_all[HI] = (b[HI] - b[HI].mean()) / b[HI].std()

rho_obs = spearmanr(p[HI], b[HI]).statistic
print(f"HI regime n={len(HI)}  observed Spearman(model score, break-even) = {rho_obs:+.3f}")

uniq = np.unique(dates[HI])
by_day = {u: HI[dates[HI] == u] for u in uniq}


def roi(idx, cents):
    o = o_n[idx] - cents
    return float(np.mean(np.where(y[idx] == 1, np.array([payout(v) for v in o]), -1.0)))


def make_score(ii, auc, w, rng):
    ys = y[ii]
    base = w * zbe_all[ii] + rng.standard_normal(len(ii))
    lo, hi_ = 0.0, 6.0
    for _ in range(24):
        mid = (lo + hi_) / 2
        if AC.auc(mid * ys + base, ys) < auc:
            lo = mid
        else:
            hi_ = mid
    return (lo + hi_) / 2 * ys + base


# calibrate w so that rho(score, BE) ~= rho_obs
rng = np.random.default_rng(2)
best_w, best_d = 0.0, 9e9
for w in np.arange(0.0, 1.01, 0.05):
    r = np.mean([spearmanr(make_score(HI, 0.58, w, rng), b[HI]).statistic for _ in range(40)])
    if abs(r - rho_obs) < best_d:
        best_w, best_d, best_r = w, abs(r - rho_obs), r
print(f"matched w={best_w:.2f} -> rho={best_r:+.3f}\n")


def stressed(auc, cents, nboot=1200, seed=17):
    rng = np.random.default_rng(seed)
    out = np.empty(nboot)
    for i in range(nboot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by_day[u] for u in pick])
        s = make_score(ii, auc, best_w, rng)
        k = max(1, int(round(0.25 * len(ii))))
        out[i] = roi(ii[np.argsort(-s)[:k]], cents)
    return out


for cents in (0.0, 10.0):
    print(f"-- price shade {cents:.0f} cents --")
    for a in (0.58, 0.60, 0.62, 0.65, 0.70):
        o = stressed(a, cents)
        print(f"   AUC={a:.2f}  meanROI={o.mean()*100:+6.2f}%  "
              f"5-95%=[{np.percentile(o,5)*100:+6.2f},{np.percentile(o,95)*100:+6.2f}]  "
              f"P(>0)={np.mean(o>0):.3f}")

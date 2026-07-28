#!/usr/bin/env python3
"""Are the ceiling's own inputs (regime base rate, regime price) stable?
Only one season has captured prices, so this is the only OOS handle.
READ-ONLY."""
from __future__ import annotations
import sys, math
from pathlib import Path
import numpy as np
from scipy.stats import norm

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


def implied(o):
    return (100.0 / (o + 100.0)) if o > 0 else (abs(o) / (abs(o) + 100.0))


D = AC.load("2026picks")
p, y, dates = D["cal"], D["y"], D["dates"]
o_n = np.array([f(r.get("market_nrfi_odds")) or np.nan for r in D["rows"]])
priced = np.isfinite(o_n) & np.isfinite(p)
be = np.array([implied(v) if np.isfinite(v) else np.nan for v in o_n])
HI = np.where(priced & (p >= 0.50))[0]
months = np.array([d[:7] for d in dates])

rng = np.random.default_rng(31)


def ceil_roi(ix, auc, ndraw=1500):
    mu = math.sqrt(2.0) * norm.ppf(auc)
    k = max(1, int(round(0.25 * len(ix))))
    v = []
    for _ in range(ndraw):
        s = mu * y[ix] + rng.standard_normal(len(ix))
        sel = ix[np.argsort(-s)[:k]]
        v.append(np.mean(np.where(y[sel] == 1, [payout(o) for o in o_n[sel]], -1.0)))
    return float(np.mean(v))


print(f"{'window':10s} {'n':>4s} {'NRFIhit':>8s} {'BE':>7s} {'wall_pp':>8s} "
      f"{'ceil@.58':>9s} {'ceil@.65':>9s}")
for m in sorted(set(months[HI])):
    ix = HI[months[HI] == m]
    if len(ix) < 30:
        continue
    print(f"{m:10s} {len(ix):4d} {y[ix].mean()*100:7.2f}% {np.mean(be[ix])*100:6.2f}% "
          f"{(np.mean(be[ix])-y[ix].mean())*100:7.2f} "
          f"{ceil_roi(ix,0.58)*100:+8.2f}% {ceil_roi(ix,0.65)*100:+8.2f}%")

ud = np.unique(dates[HI])
cut = ud[len(ud) // 2]
for nm, ix in (("H1", HI[dates[HI] < cut]), ("H2", HI[dates[HI] >= cut])):
    print(f"{nm:10s} {len(ix):4d} {y[ix].mean()*100:7.2f}% {np.mean(be[ix])*100:6.2f}% "
          f"{(np.mean(be[ix])-y[ix].mean())*100:7.2f} "
          f"{ceil_roi(ix,0.58)*100:+8.2f}% {ceil_roi(ix,0.65)*100:+8.2f}%")
print(f"{'ALL':10s} {len(HI):4d} {y[HI].mean()*100:7.2f}% {np.mean(be[HI])*100:6.2f}% "
      f"{(np.mean(be[HI])-y[HI].mean())*100:7.2f} "
      f"{ceil_roi(HI,0.58)*100:+8.2f}% {ceil_roi(HI,0.65)*100:+8.2f}%")

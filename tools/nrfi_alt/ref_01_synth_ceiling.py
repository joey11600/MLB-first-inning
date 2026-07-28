#!/usr/bin/env python3
"""REFUTATION of alt:discrimination -- "close the discrimination gap and bet
the top 25% of the NRFI regime".

Re-derives the synthetic-AUC ceiling from scratch on REAL captured DK prices,
then attacks it.  READ-ONLY.
"""
from __future__ import annotations
import csv, sys, math
from pathlib import Path
import numpy as np

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


# ---------------------------------------------------------------- data
D = AC.load("2026picks")
rows = D["rows"]
p_now = D["cal"]           # current model, recomputed
y = D["y"]                 # 1 = NRFI
dates = D["dates"]

p_live = np.array([f(r.get("nrfi_prob")) if f(r.get("nrfi_prob")) is not None
                   else np.nan for r in rows])
o_n = np.array([f(r.get("market_nrfi_odds")) if f(r.get("market_nrfi_odds")) is not None
                else np.nan for r in rows])
o_y = np.array([f(r.get("market_yrfi_odds")) if f(r.get("market_yrfi_odds")) is not None
                else np.nan for r in rows])

priced = np.isfinite(o_n) & np.isfinite(o_y)
print(f"graded rows={len(rows)}  priced={priced.sum()}")

pay_n = np.where(priced, [payout(v) if np.isfinite(v) else np.nan for v in o_n], np.nan)
be_n = np.where(priced, [implied(v) if np.isfinite(v) else np.nan for v in o_n], np.nan)


def roi_nrfi(idx):
    """ROI per unit staked betting NRFI on rows idx at real DK prices."""
    if len(idx) == 0:
        return np.nan
    r = np.where(y[idx] == 1, pay_n[idx], -1.0)
    return float(np.mean(r))


def hit(idx):
    return float(np.mean(y[idx])) if len(idx) else np.nan


for label, p in (("current-model p_nrfi", p_now), ("as-live CSV p_nrfi", p_live)):
    m = priced & np.isfinite(p)
    hi = np.where(m & (p >= 0.50))[0]
    lo = np.where(m & (p < 0.50))[0]
    print(f"\n[{label}]  priced n={m.sum()}")
    for nm, ix in (("HI regime p>=.50", hi), ("LO regime p<.50", lo)):
        print(f"  {nm:18s} n={len(ix):4d}  NRFI hit={hit(ix):.4f}  "
              f"mean break-even={np.nanmean(be_n[ix]):.4f}  "
              f"flat-NRFI ROI={roi_nrfi(ix)*100:+.2f}%  "
              f"within-regime AUC(p vs NRFI)={AC.auc(p[ix], y[ix]):.4f}")

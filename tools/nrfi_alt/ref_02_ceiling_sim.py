#!/usr/bin/env python3
"""Re-derive the synthetic-AUC ceiling, then attack it.  READ-ONLY."""
from __future__ import annotations
import sys
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


def shade(o, cents):
    """Make the price `cents` worse for the bettor (American odds)."""
    return o - cents if o > 0 else o - cents  # -150 -> -160 ; +120 -> +110


D = AC.load("2026picks")
rows = D["rows"]
p = D["cal"]
y = D["y"]
dates = D["dates"]
o_n = np.array([f(r.get("market_nrfi_odds")) or np.nan for r in rows])
o_y = np.array([f(r.get("market_yrfi_odds")) or np.nan for r in rows])
priced = np.isfinite(o_n) & np.isfinite(o_y) & np.isfinite(p)

HI = np.where(priced & (p >= 0.50))[0]
LO = np.where(priced & (p < 0.50))[0]


def roi(idx, side="NRFI", cents=0.0):
    if len(idx) == 0:
        return np.nan
    o = o_n[idx] if side == "NRFI" else o_y[idx]
    o = np.array([shade(v, cents) for v in o])
    win = (y[idx] == 1) if side == "NRFI" else (y[idx] == 0)
    pay = np.array([payout(v) for v in o])
    return float(np.mean(np.where(win, pay, -1.0)))


print("=== baselines on real DK prices (n stated) ===")
for nm, ix in (("HI p>=.50", HI), ("LO p<.50", LO)):
    print(f"{nm}: n={len(ix)} NRFIhit={y[ix].mean():.4f} "
          f"BE_nrfi={np.mean([implied(v) for v in o_n[ix]]):.4f} "
          f"flatNRFI={roi(ix,'NRFI')*100:+.2f}% flatYRFI={roi(ix,'YRFI')*100:+.2f}% "
          f"AUC_in_regime={AC.auc(p[ix], y[ix]):.4f}")

# ------------------------------------------------------------------
# 1. Synthetic score with a target AUC by construction (binormal).
#    s = mu*y + N(0,1),  AUC = Phi(mu/sqrt2)  ->  mu = sqrt2 * Phi^-1(AUC)
# ------------------------------------------------------------------
def synth(y_sub, target_auc, rng):
    mu = math.sqrt(2.0) * norm.ppf(target_auc)
    return mu * y_sub + rng.standard_normal(len(y_sub))


import math  # noqa: E402

TOPF = 0.25
NDRAW = 4000


def ceiling(idx, target_auc, topf=TOPF, cents=0.0, ndraw=NDRAW, seed=11):
    rng = np.random.default_rng(seed)
    ys = y[idx]
    k = max(1, int(round(topf * len(idx))))
    out = np.empty(ndraw)
    aucs = np.empty(ndraw)
    for i in range(ndraw):
        s = synth(ys, target_auc, rng)
        sel = idx[np.argsort(-s)[:k]]
        out[i] = roi(sel, "NRFI", cents)
        aucs[i] = AC.auc(s, ys)
    return out, aucs, k


print("\n=== A. reproduce the ceiling: HI regime, top 25%, real prices ===")
for a in (0.55, 0.58, 0.60, 0.62, 0.65, 0.70):
    o, ac, k = ceiling(HI, a)
    print(f"  AUC={a:.2f} n_bet={k:3d} realisedAUC={ac.mean():.3f} "
          f"meanROI={o.mean()*100:+6.2f}%  5-95%=[{np.percentile(o,5)*100:+6.2f},"
          f"{np.percentile(o,95)*100:+6.2f}]  P(ROI>0)={np.mean(o>0):.3f}")

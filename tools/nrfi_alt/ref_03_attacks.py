#!/usr/bin/env python3
"""Attacks on the alt:discrimination ceiling.  READ-ONLY."""
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
rows = D["rows"]
p = D["cal"]
y = D["y"]
dates = D["dates"]
o_n = np.array([f(r.get("market_nrfi_odds")) or np.nan for r in rows])
priced = np.isfinite(o_n) & np.isfinite(p)
HI = np.where(priced & (p >= 0.50))[0]
LO = np.where(priced & (p < 0.50))[0]
pay_n = np.array([payout(v) if np.isfinite(v) else np.nan for v in o_n])
be_n = np.array([implied(v) if np.isfinite(v) else np.nan for v in o_n])


def roi_idx(idx, cents=0.0):
    if len(idx) == 0:
        return np.nan
    o = o_n[idx] - cents
    pay = np.array([payout(v) for v in o])
    return float(np.mean(np.where(y[idx] == 1, pay, -1.0)))


def synth(ys, a, rng):
    return math.sqrt(2.0) * norm.ppf(a) * ys + rng.standard_normal(len(ys))


# =====================================================================
print("### ATTACK 1: the published band is synthetic-draw noise only.")
print("    Proper CI must ALSO resample the 420 games (block over days).")
uniq = np.unique(dates[HI])
by_day = {u: HI[dates[HI] == u] for u in uniq}
print(f"    HI regime: n={len(HI)} games over {len(uniq)} distinct days")


def double_boot(target_auc, topf=0.25, nboot=3000, seed=5, cents=0.0):
    rng = np.random.default_rng(seed)
    out = np.empty(nboot)
    for i in range(nboot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by_day[u] for u in pick])
        s = synth(y[ii], target_auc, rng)
        k = max(1, int(round(topf * len(ii))))
        sel = ii[np.argsort(-s)[:k]]
        out[i] = roi_idx(sel, cents)
    return out


for a in (0.58, 0.60, 0.62, 0.65, 0.70):
    o = double_boot(a)
    print(f"    AUC={a:.2f}  meanROI={o.mean()*100:+6.2f}%  "
          f"5-95%=[{np.percentile(o,5)*100:+6.2f},{np.percentile(o,95)*100:+6.2f}]  "
          f"2.5-97.5%=[{np.percentile(o,2.5)*100:+6.2f},{np.percentile(o,97.5)*100:+6.2f}]  "
          f"P(>0)={np.mean(o>0):.3f}")

# =====================================================================
print("\n### ATTACK 2: 10 cents of worse pricing (day-block CI).")
for a in (0.58, 0.62, 0.65, 0.70):
    o = double_boot(a, cents=10.0)
    print(f"    AUC={a:.2f} -10c  meanROI={o.mean()*100:+6.2f}%  "
          f"5-95%=[{np.percentile(o,5)*100:+6.2f},{np.percentile(o,95)*100:+6.2f}]  "
          f"P(>0)={np.mean(o>0):.3f}")

# =====================================================================
print("\n### ATTACK 3: search exposure -- top-fraction was a free parameter.")
for topf in (0.10, 0.15, 0.20, 0.25, 0.33, 0.50):
    rng = np.random.default_rng(3)
    vals = []
    for _ in range(2000):
        s = synth(y[HI], 0.58, rng)
        k = max(1, int(round(topf * len(HI))))
        vals.append(roi_idx(HI[np.argsort(-s)[:k]]))
    vals = np.array(vals)
    print(f"    top {topf:.0%} (n={int(round(topf*len(HI)))}): "
          f"meanROI={vals.mean()*100:+6.2f}%  P(>0)={np.mean(vals>0):.3f}")

# =====================================================================
print("\n### ATTACK 4: is AUC 0.58 attainable INSIDE the NRFI regime at all?")
print("    (model's own p_nrfi, within-regime AUC vs NRFI outcome)")
for src in ("2026picks", "2025bt", "2026bt"):
    d = AC.load(src) if src != "2026picks" else D
    pp, yy = d["cal"], d["y"]
    hi = np.where(pp >= 0.50)[0]
    lo = np.where(pp < 0.50)[0]
    print(f"    {src:10s} HI n={len(hi):5d} AUC={AC.auc(pp[hi], yy[hi]):.4f} | "
          f"LO n={len(lo):5d} AUC={AC.auc(pp[lo], yy[lo]):.4f} | "
          f"ALL n={len(yy):5d} AUC={AC.auc(pp, yy):.4f}")

# =====================================================================
print("\n### ATTACK 5: EMPIRICAL analogue -- run the top-25% rule with a REAL")
print("    score in the one place a ~0.58 within-regime AUC actually exists")
print("    (LO regime, AUC 0.5795), and in HI regime with the model score.")
for nm, ix in (("HI regime", HI), ("LO regime", LO)):
    order = np.argsort(-p[ix])
    k = max(1, int(round(0.25 * len(ix))))
    top = ix[order[:k]]
    bot = ix[order[-k:]]
    print(f"    {nm}: top25% n={k} NRFIhit={y[top].mean():.4f} "
          f"BE={np.mean(be_n[top]):.4f} ROI={roi_idx(top)*100:+.2f}%   | "
          f"bot25% NRFIhit={y[bot].mean():.4f} ROI={roi_idx(bot)*100:+.2f}%")

# =====================================================================
print("\n### ATTACK 6: the synthetic score is PRICE-BLIND. A real NRFI-")
print("    discriminating score correlates with the market, so the top")
print("    quartile carries a worse price. Break-even by p_nrfi quartile:")
for nm, ix in (("HI regime", HI), ("LO regime", LO), ("all priced", np.where(priced)[0])):
    order = ix[np.argsort(-p[ix])]
    qs = np.array_split(order, 4)
    print(f"    {nm}: " + "  ".join(
        f"Q{i+1} BE={np.mean(be_n[q]):.4f}(hit={y[q].mean():.3f})" for i, q in enumerate(qs)))

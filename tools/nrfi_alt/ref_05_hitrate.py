#!/usr/bin/env python3
"""What hit rate does each target AUC buy in the top 25%, and what is the
wall it has to clear inside the NRFI regime?  READ-ONLY."""
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


def implied(o):
    return (100.0 / (o + 100.0)) if o > 0 else (abs(o) / (abs(o) + 100.0))


D = AC.load("2026picks")
p, y = D["cal"], D["y"]
o_n = np.array([f(r.get("market_nrfi_odds")) or np.nan for r in D["rows"]])
priced = np.isfinite(o_n) & np.isfinite(p)
be = np.array([implied(v) if np.isfinite(v) else np.nan for v in o_n])
HI = np.where(priced & (p >= 0.50))[0]

print(f"NRFI regime, real DK prices: n={len(HI)}")
print(f"  base NRFI hit           = {y[HI].mean()*100:.2f}%")
print(f"  mean break-even (vig-in) = {np.mean(be[HI])*100:.2f}%")
print(f"  WALL inside this regime  = {(np.mean(be[HI])-y[HI].mean())*100:.2f} pp"
      f"   (whole-sample wall quoted in the brief: 5.65 pp)")

rng = np.random.default_rng(9)
print("\n target-AUC -> realised hit rate of the top 25% (2000 draws)")
for a in (0.55, 0.58, 0.60, 0.62, 0.65, 0.70, 0.75):
    hits, bes = [], []
    mu = math.sqrt(2.0) * norm.ppf(a)
    k = int(round(0.25 * len(HI)))
    for _ in range(2000):
        s = mu * y[HI] + rng.standard_normal(len(HI))
        sel = HI[np.argsort(-s)[:k]]
        hits.append(y[sel].mean())
        bes.append(np.mean(be[sel]))
    print(f"  AUC={a:.2f}  hit={np.mean(hits)*100:5.2f}%  "
          f"lift={np.mean(hits)*100-y[HI].mean()*100:+5.2f}pp  "
          f"BE_of_selected={np.mean(bes)*100:.2f}%  "
          f"margin={(np.mean(hits)-np.mean(bes))*100:+5.2f}pp")

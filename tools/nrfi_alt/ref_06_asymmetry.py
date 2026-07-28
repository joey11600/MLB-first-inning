#!/usr/bin/env python3
"""The binormal synthetic score assumes discrimination is spread evenly.
Real scores in this domain concentrate it in the LOSING tail.  Measure the
gap between binormal-predicted top-quartile lift and the real thing.
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


def binormal_top_lift(ys, auc, topf=0.25, ndraw=1500, seed=4):
    rng = np.random.default_rng(seed)
    mu = math.sqrt(2.0) * norm.ppf(auc)
    k = max(1, int(round(topf * len(ys))))
    v = [ys[np.argsort(-(mu * ys + rng.standard_normal(len(ys))))[:k]].mean()
         for _ in range(ndraw)]
    return float(np.mean(v)) - float(ys.mean())


def real_top_lift(score, ys, topf=0.25):
    k = max(1, int(round(topf * len(ys))))
    o = np.argsort(-score)
    return (float(ys[o[:k]].mean()) - float(ys.mean()),
            float(ys[o[-k:]].mean()) - float(ys.mean()))


cases = []
for src in ("2026picks", "2025bt", "2026bt"):
    d = AC.load(src)
    p, y = d["cal"], d["y"]
    for nm, m in (("HI p>=.50", p >= 0.50), ("LO p<.50", p < 0.50), ("ALL", np.ones_like(p, bool))):
        ix = np.where(m)[0]
        if len(ix) < 150:
            continue
        cases.append((f"{src} {nm}", p[ix], y[ix]))

print(f"{'case':26s} {'n':>5s} {'AUC':>7s} {'top25 lift':>11s} {'binorm pred':>12s} "
      f"{'ratio':>7s} {'bot25 lift':>11s}")
for nm, s, y in cases:
    a = AC.auc(s, y)
    up, dn = real_top_lift(s, y)
    pred = binormal_top_lift(y, min(max(a, 0.5001), 0.95))
    print(f"{nm:26s} {len(y):5d} {a:7.4f} {up*100:+10.2f}pp {pred*100:+11.2f}pp "
          f"{(up/pred if pred>0 else float('nan')):7.2f} {dn*100:+10.2f}pp")

#!/usr/bin/env python3
"""R2 -- chronological train/test splits inside 2026 for every candidate
target.  ANALYSIS ONLY.

Question: does retargeting the model to realised 1u NRFI profit (or adding
the de-vigged price as a feature) beat the shipped probability at picking
NRFI bets, on data the fit never saw?

Every number is flat 1u at the REAL captured DraftKings price.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from price_common import load  # noqa: E402
import r_common as R  # noqa: E402

d = load(priced_only=True)
d["date"] = d["date"].astype(str)

CUTS = ["2026-05-20", "2026-05-31", "2026-06-10", "2026-06-20",
        "2026-06-30", "2026-07-05", "2026-07-10", "2026-07-15"]

rows = []
cells = 0
for cut in CUTS:
    tr = d[d["date"] < cut]
    te = d[d["date"] >= cut]
    if len(tr) < 250 or len(te) < 120:
        continue
    for name, fn in R.MODELS.items():
        sc = fn(tr, te)
        for k in R.DEPTHS:
            n, u, roi, hit, sub = R.topk_units(te, sc, k)
            be = 100 * sub["imp_nrfi"].mean()
            rows.append(dict(cut=cut, ntr=len(tr), nte=len(te), model=name,
                             depth=k, n=n, units=u, roi=roi, hit=hit,
                             be=be, edge_pp=hit - be))
            cells += 1

df = pd.DataFrame(rows)
df.to_csv(Path(__file__).parent / "r2_splits.csv", index=False)

print(f"cells searched in this script: {cells}"
      f"  ({len(CUTS)} cuts x {len(R.MODELS)} models x {len(R.DEPTHS)} depths)\n")

print("=== mean across all 8 chronological cuts (ROI% on real prices) ===")
piv = df.pivot_table(index="model", columns="depth", values="roi", aggfunc="mean")
print(piv.round(2).to_string())

print("\n=== how many of the 8 cuts are PROFITABLE (units>0) ===")
pos = df.assign(win=(df["units"] > 0).astype(int)).pivot_table(
    index="model", columns="depth", values="win", aggfunc="sum")
print(pos.astype(int).to_string())

print("\n=== total units summed across all 8 cuts (overlapping tests) ===")
tot = df.pivot_table(index="model", columns="depth", values="units", aggfunc="sum")
print(tot.round(1).to_string())

print("\n=== the single cut the proposal cites: 2026-06-20 ===")
c = df[df["cut"] == "2026-06-20"]
print(c.pivot_table(index="model", columns="depth",
                    values="edge_pp", aggfunc="mean").round(2).to_string())
print("\n(edge_pp = hit rate minus the break-even the captured price demands."
      "\n positive = the bets actually cleared the pricing wall.)")

print("\n=== per-cut detail for the two headline candidates at depth 0.20 ===")
for name in ("G_ridge_PROFIT", "H_ridge_PROFIT+price", "I_gbm_PROFIT",
             "J_gbm_PROFIT+price", "B_shipped_EV"):
    s = df[(df["model"] == name) & (df["depth"] == 0.20)]
    line = "  ".join(f"{r.cut[5:]}:{r.units:+6.1f}" for r in s.itertuples())
    print(f"{name:24s} {line}   TOTAL {s['units'].sum():+7.1f}")

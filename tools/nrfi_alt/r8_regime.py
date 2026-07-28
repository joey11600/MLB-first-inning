#!/usr/bin/env python3
"""R8 -- why do the LATE chronological cuts look positive?
ANALYSIS ONLY.

The proposal notes the positive split points are all late cuts and argues
there is no base-rate shift to justify them (NRFI 48.47% before vs 48.46%
after).  Hit rate is only half the picture -- the PRICING regime is the
other half.  Check both.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from price_common import load  # noqa: E402
import r_common as R  # noqa: E402

d = load(priced_only=True)
d["date"] = d["date"].astype(str)
d["half"] = np.where(d["date"] < "2026-06-20", "pre-0620", "post-0620")
d["month"] = d["date"].str[:7]

print("=== flat 1u NRFI on EVERY priced game, by month ===")
g = d.groupby("month").agg(
    n=("u_nrfi", "size"), hit=("y_nrfi", "mean"),
    be=("imp_nrfi", "mean"), units=("u_nrfi", "sum"),
    roi=("u_nrfi", "mean"), vig=("vig", "mean"),
    pay=("pay_nrfi", "mean"))
g["hit"] *= 100; g["be"] *= 100; g["roi"] *= 100
g["wall_pp"] = g["be"] - g["hit"]
print(g.round(3).to_string())

print("\n=== same, split at the cut the proposal cites ===")
g2 = d.groupby("half").agg(
    n=("u_nrfi", "size"), hit=("y_nrfi", "mean"),
    be=("imp_nrfi", "mean"), units=("u_nrfi", "sum"),
    roi=("u_nrfi", "mean"), spread_pay=("pay_nrfi", "std"))
g2["hit"] *= 100; g2["be"] *= 100; g2["roi"] *= 100
g2["wall_pp"] = g2["be"] - g2["hit"]
print(g2.round(3).to_string())
print("\n  the hit rate barely moves -- but if the WALL moves, every")
print("  selection rule scored on the late half is graded against an")
print("  easier bar, and 'the late cuts are positive' is a property of")
print("  the test period, not of the target.")

print("\n=== dispersion of the NRFI price (what a price-aware target eats) ===")
for h, s in d.groupby("half"):
    print(f"  {h:10s} n={len(s):4d}  payout mean={s['pay_nrfi'].mean():.3f}"
          f"  sd={s['pay_nrfi'].std():.3f}"
          f"  frac plus-money={100*(s['o_nrfi']>0).mean():5.1f}%"
          f"  mean vig={s['vig'].mean():.4f}")

print("\n=== per-week flat NRFI ROI (is the late window simply kinder?) ===")
d["wk"] = pd.to_datetime(d["date"]).dt.isocalendar().week
w = d.groupby("wk").agg(n=("u_nrfi", "size"), units=("u_nrfi", "sum"),
                        hit=("y_nrfi", "mean"), be=("imp_nrfi", "mean"))
w["wall_pp"] = 100 * (w["be"] - w["hit"])
print(w.round(3).to_string())
lo, hi = R.day_boot_roi(d[d["half"] == "post-0620"])
print(f"\npost-0620 flat NRFI ROI CI: [{lo:+.1f}%, {hi:+.1f}%]  "
      f"(n={(d['half']=='post-0620').sum()})")
lo, hi = R.day_boot_roi(d[d["half"] == "pre-0620"])
print(f"pre-0620  flat NRFI ROI CI: [{lo:+.1f}%, {hi:+.1f}%]  "
      f"(n={(d['half']=='pre-0620').sum()})")

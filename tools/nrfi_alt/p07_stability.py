#!/usr/bin/env python3
"""P07 -- stability of the two headline numbers, and a search-cell count.

(1) Raw (no refit, nothing fitted) AUC of the production probability vs
    the DK de-vigged price, month by month.  This is the cleanest possible
    read on 'how much does the market know that we do not'.
(2) Whether the model's EV ranking on the YRFI side -- the side that is
    supposed to work -- is discrimination or just a level tilt.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import price_common as pc

d = pc.load()
d["mon"] = d["dt"].dt.to_period("M").astype(str)

print("=== raw AUC by month, nothing fitted (n>=60 only) ===")
print(f"{'month':>9} {'n':>5} {'AUC model':>10} {'AUC book':>9} {'diff':>8} "
      f"{'NRFI%':>7} {'book_p':>7}")
for m, g in d.groupby("mon"):
    if len(g) < 60:
        continue
    am, ab = pc.auc(g.y_nrfi, g.p_model), pc.auc(g.y_nrfi, g.book_nrfi)
    print(f"{m:>9} {len(g):>5} {am:>10.4f} {ab:>9.4f} {ab-am:>+8.4f} "
          f"{g.y_nrfi.mean():>7.4f} {g.book_nrfi.mean():>7.4f}")
am, ab = pc.auc(d.y_nrfi, d.p_model), pc.auc(d.y_nrfi, d.book_nrfi)
print(f"{'ALL':>9} {len(d):>5} {am:>10.4f} {ab:>9.4f} {ab-am:>+8.4f} "
      f"{d.y_nrfi.mean():>7.4f} {d.book_nrfi.mean():>7.4f}")


def aucdiff(x):
    return pc.auc(x.y_nrfi, x.book_nrfi) - pc.auc(x.y_nrfi, x.p_model)


lo, hi = pc.day_bootstrap(d, aucdiff, B=2000)
print(f"\n  AUC(DK price) - AUC(our model) = {ab-am:+.4f}  "
      f"day-block 95% CI [{lo:+.4f}, {hi:+.4f}]")
print()

print("=== is the YRFI side discrimination, or just a level tilt? ===")
print("    rank every priced game by the production model's YRFI EV,")
print("    then walk down the ranking.  A flat profile = level tilt only.")
d["ev_y"] = (1 - d.p_model) * d.pay_yrfi - d.p_model
t = d.sort_values("ev_y", ascending=False)
print(f"    {'top':>6} {'n':>5} {'hit%':>7} {'units':>9} {'ROI%':>8} {'ROI 95% CI':>22}")
for f in (1.0, 0.5, 0.3, 0.2, 0.1, 0.05):
    k = int(round(len(t) * f))
    s = t.head(k)
    lo, hi = pc.day_boot_mean(s, "u_yrfi", B=6000, scale=100.0)
    print(f"    {f:>6.0%} {k:>5} {100*(s.u_yrfi>0).mean():>6.1f}% {s.u_yrfi.sum():>+9.2f} "
          f"{100*s.u_yrfi.mean():>+8.2f} [{lo:>+7.2f}, {hi:>+7.2f}]")
print("    NOTE: this is IN-SAMPLE ranking on the full priced season "
      "(the p04 walk-forward version of the same ranking is the honest one).")
print()

print("=== search-cell accounting for this whole investigation ===")
cells = {
    "p01 capture-lead bins x 2 scores": 6 * 2,
    "p02 feature sets x 3 splits": 6 * 3,
    "p03 4 targets x 5 slices x 2 sides (single July split)": 4 * 5 * 2,
    "p04 4 targets x 5 slices x 2 sides (walk-forward)": 4 * 5 * 2,
    "p05 book deciles + model deciles + price buckets + vig quartiles": 10 + 10 + 8 + 4,
    "p06 3 offset specs + 1 residual regression": 4,
    "p07 monthly AUC + YRFI EV slices": 4 + 6,
}
for k, v in cells.items():
    print(f"    {v:>4}  {k}")
print(f"    {sum(cells.values()):>4}  TOTAL cells examined")
print("    winners that survived out-of-sample confirmation: 0")

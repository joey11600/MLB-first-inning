#!/usr/bin/env python3
"""P05 -- where, if anywhere, is DraftKings actually WRONG on NRFI?

A profitability model can only work if the book is miscalibrated
somewhere.  This maps it directly: bin by the de-vigged book probability
and by the raw price, and compare realised NRFI rate to the break-even
rate the price demands.  No model involved -- this is the ceiling on
every 'learn where the book is wrong' idea.

All bins are reported.  Nothing is cherry-picked; any bin that looks
beatable is then re-tested on a forward time split.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import price_common as pc

d = pc.load()
print(f"n={len(d)}  NRFI rate={d.y_nrfi.mean():.4f}")
print(f"vig: mean={d.vig.mean():.4f} median={d.vig.median():.4f} "
      f"p10={d.vig.quantile(.1):.4f} p90={d.vig.quantile(.9):.4f}")
print()

print("=== BOOK calibration: de-vigged DK NRFI probability, deciles ===")
print(f"{'decile':>7} {'n':>5} {'book_p':>8} {'actual':>8} {'err':>8} "
      f"{'breakeven':>10} {'NRFI ROI%':>10} {'ROI 95% CI':>22}")
d["_dec"] = pd.qcut(d["book_nrfi"], 10, labels=False, duplicates="drop")
for k, g in d.groupby("_dec"):
    be = g["imp_nrfi"].mean()
    lo, hi = pc.day_boot_mean(g, "u_nrfi", B=4000, scale=100.0)
    print(f"{int(k)+1:>7} {len(g):>5} {g.book_nrfi.mean():>8.4f} "
          f"{g.y_nrfi.mean():>8.4f} {g.y_nrfi.mean()-g.book_nrfi.mean():>+8.4f} "
          f"{be:>10.4f} {100*g.u_nrfi.mean():>+10.2f} [{lo:>+7.2f}, {hi:>+7.2f}]")
print()

print("=== MODEL calibration on the same priced universe, deciles ===")
d["_mdec"] = pd.qcut(d["p_model"], 10, labels=False, duplicates="drop")
print(f"{'decile':>7} {'n':>5} {'model_p':>8} {'actual':>8} {'err':>8} {'book_p':>8}")
for k, g in d.groupby("_mdec"):
    print(f"{int(k)+1:>7} {len(g):>5} {g.p_model.mean():>8.4f} "
          f"{g.y_nrfi.mean():>8.4f} {g.y_nrfi.mean()-g.p_model.mean():>+8.4f} "
          f"{g.book_nrfi.mean():>8.4f}")
print()

print("=== by raw NRFI price bucket (both sides) ===")
edges = [-400, -160, -140, -125, -115, -105, 100, 120, 400]
d["_pb"] = pd.cut(d["o_nrfi"], edges)
print(f"{'price bucket':>16} {'n':>5} {'hit%':>7} {'BE%':>7} {'gap pp':>8} "
      f"{'NRFI ROI%':>10} {'ROI 95% CI':>22} {'YRFI ROI%':>10}")
for k, g in d.groupby("_pb", observed=True):
    if len(g) < 20:
        continue
    be = 100 * g["imp_nrfi"].mean()
    lo, hi = pc.day_boot_mean(g, "u_nrfi", B=4000, scale=100.0)
    print(f"{str(k):>16} {len(g):>5} {100*g.y_nrfi.mean():>6.1f}% {be:>6.1f}% "
          f"{100*g.y_nrfi.mean()-be:>+8.2f} {100*g.u_nrfi.mean():>+10.2f} "
          f"[{lo:>+7.2f}, {hi:>+7.2f}] {100*g.u_yrfi.mean():>+10.2f}")
print()

print("=== by VIG bucket (is a low-vig game beatable?) ===")
d["_vb"] = pd.qcut(d["vig"], 4, labels=False, duplicates="drop")
print(f"{'quartile':>9} {'n':>5} {'vig':>7} {'hit%':>7} {'BE%':>7} "
      f"{'NRFI ROI%':>10} {'YRFI ROI%':>10}")
for k, g in d.groupby("_vb"):
    print(f"{int(k)+1:>9} {len(g):>5} {g.vig.mean():>7.4f} "
          f"{100*g.y_nrfi.mean():>6.1f}% {100*g.imp_nrfi.mean():>6.1f}% "
          f"{100*g.u_nrfi.mean():>+10.2f} {100*g.u_yrfi.mean():>+10.2f}")
print()

print("=== the wall, restated ===")
be_all = d["imp_nrfi"].mean()
print(f"  mean break-even NRFI rate demanded by DK : {100*be_all:.2f}%")
print(f"  realised NRFI rate                       : {100*d.y_nrfi.mean():.2f}%")
print(f"  gap                                      : {100*(d.y_nrfi.mean()-be_all):+.2f}pp")
print(f"  gap after stripping ALL vig (de-vigged)  : "
      f"{100*(d.y_nrfi.mean()-d.book_nrfi.mean()):+.2f}pp")
print(f"  best-case: perfect foresight of WHICH HALF of the price")
print(f"    distribution to bet is worth at most the spread of the")
print(f"    decile table above.")

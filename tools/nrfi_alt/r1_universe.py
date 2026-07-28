#!/usr/bin/env python3
"""R1 -- establish the universe and the arithmetic ceiling, from scratch.

ANALYSIS ONLY.  Reads data/picks_2026.csv, writes nothing.

Purpose: before testing "retarget the model to profit", confirm
  (a) how many priced+graded 2026 games there are,
  (b) the NRFI hit rate vs the break-even the prices demand,
  (c) that E[profit | x] is an EXACT monotone function of p(x) once the
      price is known -- i.e. a profit target cannot carry information a
      calibrated probability + the price does not already carry.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from price_common import load, implied, payout, day_boot_mean  # noqa: E402

d = load(priced_only=True)
print(f"priced+graded 2026 rows: {len(d)}   days: {d['date'].nunique()}")
print(f"date range: {d['date'].min()} .. {d['date'].max()}")
print(f"NRFI actual rate: {d['y_nrfi'].mean():.4f}")
print(f"mean implied NRFI (raw, with vig): {d['imp_nrfi'].mean():.4f}")
print(f"mean de-vigged book NRFI          : {d['book_nrfi'].mean():.4f}")
print(f"mean vig: {d['vig'].mean():.4f}")
print(f"break-even needed at captured NRFI prices: {d['imp_nrfi'].mean():.4f}")
print(f"WALL (break-even - actual) = {100*(d['imp_nrfi'].mean()-d['y_nrfi'].mean()):.2f} pp")

print("\nflat 1u on EVERY priced game:")
for side in ("nrfi", "yrfi"):
    u = d[f"u_{side}"]
    lo, hi = day_boot_mean(d, f"u_{side}", scale=100.0)
    print(f"  {side.upper():4s} n={len(u)}  units={u.sum():+8.2f}  "
          f"ROI={100*u.mean():+6.2f}%  day-block CI [{lo:+.1f}%, {hi:+.1f}%]")

print("\nNRFI price distribution (American):")
print(d["o_nrfi"].describe().to_string())

print("\ncapture lead time (hours before first pitch):")
print(d["lead_h"].describe().to_string())
neg = (d["lead_h"] < 0).sum()
print(f"  rows with capture AFTER first pitch: {neg} "
      f"({100*neg/len(d):.1f}%)  <- these would be look-ahead if used as a feature")

# ---------------------------------------------------------------------------
# The identity that kills the 'different target' mechanism.
#   u_nrfi(x) = pay(x)  if NRFI else -1
#   E[u|x]    = p(x)*pay(x) - (1-p(x)) = p(x)*(1+pay(x)) - 1
# So for a FIXED price, E[u|x] is strictly increasing and affine in p(x).
# A profit target therefore learns nothing about the game that a
# probability + the (already-known) price does not.
# ---------------------------------------------------------------------------
p = np.linspace(0.30, 0.70, 5)
print("\nIDENTITY CHECK  E[u] = p*(1+pay) - 1  at a few prices:")
for o in (-140.0, -120.0, 100.0, 130.0):
    ev = p * (1 + payout(o)) - 1
    print(f"  odds {o:+7.0f} pay={payout(o):.3f} be={implied(o):.3f} : "
          + " ".join(f"p={a:.2f}->EV={b:+.3f}" for a, b in zip(p, ev)))

# empirical: rank correlation between EV computed from the SHIPPED model
# and the shipped probability itself, within the priced universe.
ev_model = d["p_model"] * (1 + d["pay_nrfi"]) - 1
r = pd.Series(ev_model).corr(pd.Series(d["p_model"]), method="spearman")
print(f"\nSpearman(EV_from_model_p , model_p) on the priced universe = {r:.4f}")
print("  (1.0 would mean price adds no re-ordering at all; <1 means price")
print("   re-orders games -- that re-ordering is the ONLY thing a profit")
print("   target or a price feature can contribute.)")

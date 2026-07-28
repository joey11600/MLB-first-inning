#!/usr/bin/env python3
"""R5 -- (a) how big a 'win' does pure noise produce at this search budget,
         (b) does anything survive 10 cents of worse pricing.
ANALYSIS ONLY.

(a) PLACEBO.  Permute the first-inning OUTCOME among games played on the
    SAME DAY, keeping every feature and every captured price attached to
    its own game.  The features then carry zero information about the
    result, but the day structure, the price distribution and the sample
    size are untouched.  Re-run the whole walk-forward grid.  The
    distribution of the BEST cell tells you what "+5.6u at top 10%" is
    worth when it is the max of a 55-cell search.

(b) SHOCK.  Take the best real cell and re-price every bet 10 American
    cents worse (+0.021 implied).
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from price_common import load  # noqa: E402
import r_common as R  # noqa: E402

LINEAR = ["D_refit_outcome", "E_outcome+price", "F_payout_weighted",
          "G_ridge_PROFIT", "H_ridge_PROFIT+price", "K_PROFIT+price+pmodel"]
WARM, STEP = 350, 7


def weeks(d):
    days = sorted(d["date"].unique())
    out, i = [], 0
    while i < len(days):
        if (d["date"] < days[i]).sum() >= WARM:
            out.append(days[i]); i += STEP
        else:
            i += 1
    return out


def run_grid(d, starts, models=LINEAR):
    """Returns dict[(model, depth)] = units."""
    picks = {m: [] for m in models}
    for wi, s in enumerate(starts):
        e = starts[wi + 1] if wi + 1 < len(starts) else None
        tr = d[d["date"] < s]
        te = d[d["date"] >= s] if e is None else d[(d["date"] >= s) & (d["date"] < e)]
        if len(te) == 0:
            continue
        for m in models:
            t = te.copy()
            t["score"] = R.MODELS[m](tr, t)
            t["rank_pct"] = t["score"].rank(pct=True, ascending=False)
            picks[m].append(t)
    out = {}
    for m in models:
        a = pd.concat(picks[m])
        for k in R.DEPTHS:
            sub = a[a["rank_pct"] <= k]
            out[(m, k)] = (sub["u_nrfi"].sum(), len(sub))
    return out


d = load(priced_only=True)
d["date"] = d["date"].astype(str)
starts = weeks(d)

t0 = time.time()
real = run_grid(d, starts)
print(f"real grid in {time.time()-t0:.1f}s   cells = {len(real)}")
best_cell = max(real, key=lambda c: real[c][0])
print(f"best REAL cell: {best_cell[0]} @ top{int(best_cell[1]*100)}%  "
      f"= {real[best_cell][0]:+.2f}u over n={real[best_cell][1]}")

# ------------------------------------------------------------------ placebo
B = int(sys.argv[1]) if len(sys.argv) > 1 else 200
rng = np.random.default_rng(2026)
maxes, gcell = [], []
t0 = time.time()
for b in range(B):
    p = d.copy()
    # permute the OUTCOME within each calendar day
    y = p.groupby("date")["y_nrfi"].transform(
        lambda s: pd.Series(rng.permutation(s.to_numpy()), index=s.index))
    p["y_nrfi"] = y.to_numpy()
    p["u_nrfi"] = np.where(p["y_nrfi"] == 1, p["pay_nrfi"], -1.0)
    p["u_yrfi"] = np.where(p["y_nrfi"] == 0, p["pay_yrfi"], -1.0)
    g = run_grid(p, starts)
    maxes.append(max(v[0] for v in g.values()))
    gcell.append(g[("G_ridge_PROFIT", 0.10)][0])
print(f"\n{B} placebo grids in {time.time()-t0:.0f}s")

maxes = np.asarray(maxes); gcell = np.asarray(gcell)
obs_max = real[best_cell][0]
obs_g = real[("G_ridge_PROFIT", 0.10)][0]
print("\n=== PLACEBO: best-of-30-cells units, features carry NO information ===")
for q in (50, 75, 90, 95, 99):
    print(f"  p{q:2d} = {np.percentile(maxes, q):+7.2f}u")
print(f"  observed best real cell = {obs_max:+7.2f}u"
      f"   -> placebo p-value = {(maxes >= obs_max).mean():.3f}")
print("\n=== PLACEBO: the specific cell G_ridge_PROFIT @ top10% ===")
print(f"  placebo mean {gcell.mean():+.2f}u  sd {gcell.std():.2f}u  "
      f"p95 {np.percentile(gcell,95):+.2f}u")
print(f"  observed {obs_g:+.2f}u  -> one-cell p-value = {(gcell >= obs_g).mean():.3f}")

# -------------------------------------------------------------------- shock
print("\n=== 10-CENT PRICE SHOCK on the walk-forward results ===")
picks = {m: [] for m in LINEAR}
for wi, s in enumerate(starts):
    e = starts[wi + 1] if wi + 1 < len(starts) else None
    tr = d[d["date"] < s]
    te = d[d["date"] >= s] if e is None else d[(d["date"] >= s) & (d["date"] < e)]
    if len(te) == 0:
        continue
    for m in LINEAR:
        t = te.copy()
        t["score"] = R.MODELS[m](tr, t)
        t["rank_pct"] = t["score"].rank(pct=True, ascending=False)
        picks[m].append(t)
for m in LINEAR:
    a = pd.concat(picks[m])
    a["pay_shaded"] = R.shade_payout(a["pay_nrfi"].to_numpy(float))
    a["u_shaded"] = np.where(a["y_nrfi"] == 1, a["pay_shaded"], -1.0)
    line = f"{m:24s}"
    for k in R.DEPTHS:
        sub = a[a["rank_pct"] <= k]
        line += f"  {int(k*100):>2d}%: {sub['u_nrfi'].sum():+6.1f} -> {sub['u_shaded'].sum():+6.1f}"
    print(line)

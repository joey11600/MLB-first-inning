#!/usr/bin/env python3
"""R3 -- honest walk-forward.  ANALYSIS ONLY.

The 8 chronological splits in R2 all TEST ON OVERLAPPING TAILS, so summing
them double-counts.  This does the thing you would actually deploy: refit
every 7 days on everything strictly before that week, bet the coming week,
never look forward.  Every bet in the ledger appears exactly once.

Flat 1u NRFI at the real captured DraftKings price.  Day-block bootstrap CIs.
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
days = sorted(d["date"].unique())

WARM = 350          # rows needed before the first live week
STEP = 7            # refit cadence in calendar days

# build the week boundaries
starts = []
i = 0
while i < len(days):
    if (d["date"] < days[i]).sum() >= WARM:
        starts.append(days[i])
        i += STEP
    else:
        i += 1
print(f"walk-forward weeks: {len(starts)}   first live day {starts[0]}"
      f"   last {starts[-1]}")

picks = {m: [] for m in R.MODELS}
for wi, s in enumerate(starts):
    e = starts[wi + 1] if wi + 1 < len(starts) else None
    tr = d[d["date"] < s]
    te = d[(d["date"] >= s)] if e is None else d[(d["date"] >= s) & (d["date"] < e)]
    if len(te) == 0:
        continue
    for name, fn in R.MODELS.items():
        sc = fn(tr, te)
        t = te.copy()
        t["score"] = sc
        t["rank_pct"] = t["score"].rank(pct=True, ascending=False)
        picks[name].append(t)

print(f"\nlive rows scored per model: {len(pd.concat(picks['A_shipped_p']))}")
print("\n=== WALK-FORWARD, flat 1u NRFI at real prices ===")
hdr = f"{'model':24s}" + "".join(f"{int(k*100):>7d}%" for k in R.DEPTHS)
print(hdr)
res = {}
for name in R.MODELS:
    allp = pd.concat(picks[name])
    line = f"{name:24s}"
    res[name] = {}
    for k in R.DEPTHS:
        sub = allp[allp["rank_pct"] <= k]
        u = sub["u_nrfi"].to_numpy(float)
        roi = 100 * u.mean() if len(u) else float("nan")
        res[name][k] = (len(u), u.sum(), roi, sub)
        line += f" {u.sum():+6.1f}"
    print(line)

print("\n=== same, as ROI% with day-block 95% CI ===")
for name in R.MODELS:
    for k in (0.05, 0.10, 0.20):
        n, u, roi, sub = res[name][k]
        lo, hi = R.day_boot_roi(sub)
        be = 100 * sub["imp_nrfi"].mean()
        hit = 100 * sub["y_nrfi"].mean()
        print(f"{name:24s} top{int(k*100):3d}%  n={n:4d}  hit={hit:5.1f}%  "
              f"need={be:5.1f}%  edge={hit-be:+6.2f}pp  units={u:+7.2f}  "
              f"ROI={roi:+6.2f}%  CI[{lo:+6.1f},{hi:+6.1f}]")
    print()

# ---- does the PROFIT target beat simply refitting the OUTCOME target? ----
print("=== paired: PROFIT target minus OUTCOME target, same rows selected? ===")
for k in R.DEPTHS:
    g = res["G_ridge_PROFIT"][k][3]
    dd = res["D_refit_outcome"][k][3]
    inter = len(set(map(tuple, g[["date", "away_team", "home_team"]].to_numpy())) &
                set(map(tuple, dd[["date", "away_team", "home_team"]].to_numpy())))
    print(f"  depth {k:.2f}: PROFIT {res['G_ridge_PROFIT'][k][1]:+7.2f}u   "
          f"OUTCOME {res['D_refit_outcome'][k][1]:+7.2f}u   "
          f"overlap {inter}/{res['G_ridge_PROFIT'][k][0]} games")

out = Path(__file__).parent / "r3_walkforward.csv"
pd.DataFrame([
    dict(model=m, depth=k, n=res[m][k][0], units=res[m][k][1], roi=res[m][k][2])
    for m in R.MODELS for k in R.DEPTHS]).to_csv(out, index=False)
print(f"\nwrote {out}")

#!/usr/bin/env python3
"""R7 -- is the walk-forward failure an artefact of MY choices?
ANALYSIS ONLY.

Vary the warm-up size and the refit cadence, and also restrict to the
subset where the captured price is genuinely PRE-first-pitch (the cleaner
half of the price data).  If the profit target were real it should show up
somewhere in here.
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

MODELS = ["B_shipped_EV", "D_refit_outcome", "G_ridge_PROFIT",
          "H_ridge_PROFIT+price", "K_PROFIT+price+pmodel"]


def walk(d, warm, step, models=MODELS):
    days = sorted(d["date"].unique())
    starts, i = [], 0
    while i < len(days):
        if (d["date"] < days[i]).sum() >= warm:
            starts.append(days[i]); i += step
        else:
            i += 1
    if not starts:
        return None, 0
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
    n_live = 0
    for m in models:
        a = pd.concat(picks[m]); n_live = len(a)
        for k in R.DEPTHS:
            sub = a[a["rank_pct"] <= k]
            out[(m, k)] = sub["u_nrfi"].sum()
    return out, n_live


d = load(priced_only=True)
d["date"] = d["date"].astype(str)

print("=== walk-forward units, sweeping warm-up x refit cadence ===")
print("(a positive entry anywhere is the proposal's only hope)")
cells = 0
allvals = []
for warm in (250, 350, 450, 550):
    for step in (7, 14, 28):
        res, n_live = walk(d, warm, step)
        if res is None:
            continue
        print(f"\nwarm={warm} step={step}d  live rows={n_live}")
        for m in MODELS:
            line = f"  {m:24s}"
            for k in R.DEPTHS:
                v = res[(m, k)]
                line += f" {int(k*100):>2d}%:{v:+7.2f}"
                cells += 1
                allvals.append((warm, step, m, k, v))
            print(line)
print(f"\ncells in this sensitivity sweep: {cells}")

av = pd.DataFrame(allvals, columns=["warm", "step", "model", "depth", "units"])
print("\nfraction of cells positive, by model:")
print((av.groupby("model")["units"].apply(lambda s: (s > 0).mean())
       .round(3).to_string()))
print("\nmean units by model:")
print(av.groupby("model")["units"].mean().round(2).to_string())
print("\nbest cell overall:")
b = av.loc[av["units"].idxmax()]
print(f"  {b['model']} warm={b['warm']} step={b['step']} depth={b['depth']} "
      f"= {b['units']:+.2f}u")
print("  (compare: the R5 placebo, with the outcome shuffled within day and")
print("   only 30 cells, produced a best cell of +4.9u at the MEDIAN and")
print("   +17.7u at the 95th percentile.)")

# ---------------------------------------------------- pre-first-pitch subset
print("\n=== restricted to rows whose price was captured BEFORE first pitch ===")
pre = d[d["lead_h"] > 0].copy()
print(f"n={len(pre)}  days={pre['date'].nunique()}  "
      f"NRFI rate={pre['y_nrfi'].mean():.4f}  "
      f"break-even={pre['imp_nrfi'].mean():.4f}  "
      f"wall={100*(pre['imp_nrfi'].mean()-pre['y_nrfi'].mean()):.2f}pp")
res, n_live = walk(pre, 250, 7)
if res:
    print(f"live rows={n_live}")
    for m in MODELS:
        line = f"  {m:24s}"
        for k in R.DEPTHS:
            line += f" {int(k*100):>2d}%:{res[(m,k)]:+7.2f}"
        print(line)

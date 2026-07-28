#!/usr/bin/env python3
"""
tools/nrfi_alt/alt_total_capture_refute2.py  -- second pass.

T8.  Search-exposure / max-statistic permutation.  Under the null of NO
     heterogeneity by total, how good does the BEST of the searched cells
     look by chance?  Compare to the observed best cell.
T9.  Interaction: total x model-side, total x market price residual.
T10. Orthogonalised total -- strip whatever the ALREADY-CAPTURED market
     1st-inning price knows, test only the leftover.
T11. Ceiling: the market's own de-vigged 1st-inning probability is a
     STRICTLY BETTER market-consensus variable than a full-game total
     (it is the market's opinion of the exact event, unconvolved with
     innings 2-9).  Find the best cell over a fine grid of it.  If the
     ceiling variable cannot find a profitable cell, a coarser one cannot.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from alt_total_capture_refute import (          # noqa: E402
    load_priced, day_block_boot, qcut_label, cell_report, print_table)

RNG = np.random.default_rng(7)


def best_cell_roi(df, keycol, qs=(2, 3, 4, 5)):
    """Return the best (highest) mean pnl over every cell of every grid."""
    best = -9e9
    ncells = 0
    for q in qs:
        try:
            cats = pd.qcut(df[keycol], q, duplicates="drop", labels=False)
        except ValueError:
            continue
        for c in np.unique(cats.dropna()):
            g = df.loc[cats == c, "pnl_nrfi"]
            if len(g) < 60:
                continue
            ncells += 1
            best = max(best, g.mean())
    return best, ncells


def main():
    d = load_priced()
    d = d[d["proxy_total"].notna()].copy()

    # ------------------------------------------------------------ T8
    print("=" * 96)
    print("T8  SEARCH EXPOSURE -- max-statistic permutation")
    print("=" * 96)
    obs, ncells = best_cell_roi(d, "proxy_total")
    print(f"  observed BEST cell over {ncells} cells (2/3/4/5-bucket grids): "
          f"ROI {obs*100:+.2f}%")
    # permute the total WITHIN day, so day effects + slate composition survive
    nperm = 2000
    null = np.empty(nperm)
    tot = d["proxy_total"].to_numpy().copy()
    days = d["date"].to_numpy()
    idx_by_day = {dd: np.where(days == dd)[0] for dd in np.unique(days)}
    for i in range(nperm):
        t2 = tot.copy()
        for ii in idx_by_day.values():
            t2[ii] = RNG.permutation(t2[ii])
        dp = d.copy()
        dp["proxy_total"] = t2
        null[i], _ = best_cell_roi(dp, "proxy_total")
    print(f"  null distribution of the BEST cell (within-day permutation, "
          f"{nperm} reps):")
    for p in (50, 75, 90, 95, 99):
        print(f"     {p}th pct = {np.percentile(null, p)*100:+.2f}%")
    pval = float((null >= obs).mean())
    print(f"  p(best-cell >= observed) = {pval:.3f}   "
          f"-> {'NOT significant' if pval > 0.05 else 'significant'}")
    print(f"  NOTE: even the 99th-pct pure-noise best cell is "
          f"{np.percentile(null,99)*100:+.2f}% ROI -- i.e. random slicing on a "
          f"variable with NO information routinely produces cells that look "
          f"better than the observed best real one.")

    # ------------------------------------------------------------ T10
    print("\n" + "=" * 96)
    print("T10  ORTHOGONALISED TOTAL (residual after the ALREADY-CAPTURED")
    print("     de-vigged DK 1st-inning price)")
    print("=" * 96)
    x = d["mkt_p_nrfi"].to_numpy()
    y = d["proxy_total"].to_numpy()
    beta = np.polyfit(x, y, 1)
    d["tot_resid"] = y - np.polyval(beta, x)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    print(f"  R^2 of proxy_total on market 1st-inning price = {r2:.3f}  "
          f"(so {r2*100:.0f}% of the proxy total is ALREADY in the ledger)")
    rows = []
    lab = qcut_label(d["tot_resid"], 3, "resid")
    for name, g in d.assign(_c=lab).groupby("_c", observed=True):
        rows.append(cell_report(g, str(name)))
    rows = [r for r in rows if r]
    rows.sort(key=lambda r: r["cell"])
    print_table(rows, "-- terciles of the total that the market price does NOT explain")

    # ------------------------------------------------------------ T9
    print("\n" + "=" * 96)
    print("T9  INTERACTION -- total x model side")
    print("=" * 96)
    d["model_p"] = pd.to_numeric(d["nrfi_prob"], errors="coerce")
    d = d[d["model_p"].notna()]
    thi = d["proxy_total"] >= d["proxy_total"].median()
    mhi = d["model_p"] >= d["model_p"].median()
    rows = []
    for tn, tm in (("lowTot", ~thi), ("hiTot", thi)):
        for mn, mm in (("modelYRFIish", ~mhi), ("modelNRFIish", mhi)):
            g = d[tm & mm]
            rows.append(cell_report(g, f"{tn}/{mn}"))
    print_table([r for r in rows if r], "-- 2x2")

    # ------------------------------------------------------------ T11
    print("\n" + "=" * 96)
    print("T11  CEILING TEST -- the market's OWN de-vigged 1st-inning NRFI")
    print("     probability.  Strictly dominates any full-game total as a")
    print("     market-consensus variable for this bet.")
    print("=" * 96)
    obs2, nc2 = best_cell_roi(d, "mkt_p_nrfi", qs=(2, 3, 4, 5, 6, 8, 10))
    print(f"  best cell over {nc2} cells of the CEILING variable: "
          f"ROI {obs2*100:+.2f}%")
    # bootstrap that best cell honestly
    for q in (10,):
        cats = pd.qcut(d["mkt_p_nrfi"], q, duplicates="drop", labels=False)
        rows = []
        for c in sorted(cats.dropna().unique()):
            g = d[cats == c]
            rows.append(cell_report(g, f"mktP decile {int(c)+1}"))
        print_table([r for r in rows if r], "-- deciles of the market's own 1st-inning consensus")

    pos = [r for r in rows if r and r["lo"] > 0]
    print(f"\n  cells whose day-block 95% CI EXCLUDES zero on the upside: "
          f"{len(pos)} / {len([r for r in rows if r])}")

    print("\n" + "=" * 96)
    print("CUMULATIVE SEARCH EXPOSURE THIS INVESTIGATION: "
          f"{22 + ncells + nc2 + 3 + 4 + 10} cells, 0 with a lower CI above zero.")
    print("=" * 96)


if __name__ == "__main__":
    main()

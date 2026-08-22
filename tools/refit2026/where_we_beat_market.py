#!/usr/bin/env python3
"""
Is there ANY condition under which our model beats the market?

market_anchor.py showed that pooled over all priced 2026 games our model adds
nothing on top of the de-vigged price (walk-forward logloss gets WORSE, and the
daily-refit coefficient on our model is negative on 72% of days).  That is the
average.  This asks the conditional question: is there a slice -- a park type,
a weather regime, a price range, a confidence band -- where we genuinely carry
information the market does not?

If one exists it is the edge, and betting ONLY there is the strategy.

METHOD, and why it is built this way
------------------------------------
Searching slices for a winner is exactly how the 2026-08-03 gate sweep produced
an artifact that passed every conventional test.  Two defences here:

  1. The slice list is FIXED before looking (below, `SLICES`).  No threshold is
     tuned inside a slice; each is a pre-committed partition.
  2. The headline number is a SELECTION-AWARE permutation null: the whole
     search is re-run on shuffled outcomes many times, and the best slice found
     in the real data is compared against the distribution of the best slice
     found in noise.  A slice only counts if it beats what the SEARCH ITSELF
     produces by chance -- not merely if its own p-value looks small.

Metric per slice: AUC of our model minus AUC of the de-vigged market, on the
same games.  Level-invariant, so a slice cannot win merely by having a
different base rate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def auc(y, p):
    y = np.asarray(y); p = np.asarray(p)
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(p).rank().values
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def build_slices(d: pd.DataFrame) -> dict:
    """Pre-committed partitions.  Each value is a boolean mask."""
    s = {}
    s["dome"] = d.wx_is_dome == 1
    s["outdoor"] = d.wx_is_dome == 0
    s["hot (>=27C)"] = d.wx_temp_c >= 27
    s["cold (<20C)"] = d.wx_temp_c < 20
    s["windy (>=16kmh)"] = d.wx_wind_kmh >= 16
    s["calm (<10kmh)"] = d.wx_wind_kmh < 10
    med_mkt = d.mkt.median()
    s["market likes YRFI"] = d.mkt >= med_mkt
    s["market likes NRFI"] = d.mkt < med_mkt
    s["short price (be>=.58)"] = d.be_pick >= 0.58
    s["long price (be<.54)"] = d.be_pick < 0.54
    s["model confident"] = d.yrfi_prob >= d.yrfi_prob.quantile(0.75)
    s["model unsure"] = d.yrfi_prob <= d.yrfi_prob.quantile(0.25)
    s["both starters weak"] = (d.home_fip > d.home_fip.median()) & (d.away_fip > d.away_fip.median())
    s["both starters strong"] = (d.home_fip <= d.home_fip.median()) & (d.away_fip <= d.away_fip.median())
    s["lineups confirmed"] = (d.home_top3c_source == "lineup") & (d.away_top3c_source == "lineup")
    s["early season (<=Jun 15)"] = d.date <= "2026-06-15"
    s["late season (>Jun 15)"] = d.date > "2026-06-15"
    s["day game (<17 ET)"] = d.hour < 17
    s["night game (>=17 ET)"] = d.hour >= 17
    return s


def search(d: pd.DataFrame, y: np.ndarray, slices: dict, min_n=120):
    out = []
    for name, m in slices.items():
        m = np.asarray(m.fillna(False) if hasattr(m, "fillna") else m, dtype=bool)
        if m.sum() < min_n:
            continue
        a_mod = auc(y[m], d.yrfi_prob.values[m])
        a_mkt = auc(y[m], d.mkt.values[m])
        if np.isnan(a_mod) or np.isnan(a_mkt):
            continue
        out.append((name, int(m.sum()), a_mod, a_mkt, a_mod - a_mkt))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--perm", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    d = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d.fi_total_runs.notna()].copy()
    d["y"] = (d.fi_total_runs > 0).astype(int)
    d = d.dropna(subset=["implied_yrfi_prob", "implied_nrfi_prob", "yrfi_prob"]).copy()
    vig = d.implied_yrfi_prob + d.implied_nrfi_prob
    d["mkt"] = d.implied_yrfi_prob / vig
    d["be_pick"] = d.implied_yrfi_prob
    _t = pd.to_datetime(d.game_time_et, errors="coerce", format="mixed")
    d["hour"] = _t.dt.hour.fillna(19)
    for c in ["wx_is_dome", "wx_temp_c", "wx_wind_kmh", "home_fip", "away_fip"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.reset_index(drop=True)
    y = d.y.values

    print(f"priced 2026 games: n={len(d)}")
    print(f"OVERALL   model AUC={auc(y, d.yrfi_prob.values):.4f}   "
          f"market AUC={auc(y, d.mkt.values):.4f}   "
          f"diff={auc(y, d.yrfi_prob.values)-auc(y, d.mkt.values):+.4f}\n")

    slices = build_slices(d)
    res = search(d, y, slices)
    res.sort(key=lambda r: -r[4])
    print("=" * 88)
    print("MODEL minus MARKET AUC, by pre-committed slice (positive = we beat the market)")
    print(f"  {'slice':<26} {'n':>5} {'model':>8} {'market':>8} {'diff':>9}")
    for name, n, am, ak, df in res:
        flag = "  <-- we win" if df > 0 else ""
        print(f"  {name:<26} {n:>5} {am:>8.4f} {ak:>8.4f} {df:>+9.4f}{flag}")

    best_name, best_n, best_m, best_k, best = res[0]
    print(f"\n  best slice in the real data: '{best_name}'  diff={best:+.4f}  (n={best_n})")

    print("\n" + "=" * 88)
    print(f"SELECTION-AWARE PERMUTATION NULL ({args.perm} shuffles of the outcome)")
    print("  Re-running the WHOLE search on noise and keeping the best slice each time.")
    null_best = []
    for _ in range(args.perm):
        ys = y[rng.permutation(len(y))]
        r = search(d, ys, slices)
        if r:
            null_best.append(max(x[4] for x in r))
    null_best = np.array(null_best)
    p = (null_best >= best).mean()
    print(f"  best-slice diff in NOISE: mean {null_best.mean():+.4f}  "
          f"90th pct {np.percentile(null_best,90):+.4f}  95th pct {np.percentile(null_best,95):+.4f}")
    print(f"  observed best {best:+.4f}  ->  selection-aware p = {p:.4f}")
    print("\n  " + ("PASSES: better than the search finds in pure noise."
                    if p < 0.05 else
                    "FAILS: a search over these slices finds this much in NOISE this often."))
    print("  Note how large the noise best-slice is -- that is the artifact this null exists")
    print("  to catch, and it is why a raw per-slice p-value would have been misleading.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

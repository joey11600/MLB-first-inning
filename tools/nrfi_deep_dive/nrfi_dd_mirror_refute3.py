#!/usr/bin/env python3
"""part 3: fairness checks on the out-of-sample comparison + the mechanism test.
Read-only."""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BT = ROOT / "data" / "backtests"
PICKS = ROOT / "data" / "picks_2026.csv"

print("=" * 118)
print("  F. IS THE 2024/2025 COMPARISON FAIR? overall calibration of each dataset")
print("=" * 118)
files = {
    "2024 backtest": "backtest_2024-04-01_to_2024-09-30_truepit.csv",
    "2025 backtest": "backtest_2025-04-01_to_2025-09-30_truepit.csv",
    "2026 bt 04/01-05/11": "backtest_2026-04-01_to_2026-05-11_truepit.csv",
    "2026 bt 05/12-05/26": "backtest_2026-05-12_to_2026-05-26_truepit.csv",
}
sets = {}
for k, f in files.items():
    d = pd.read_csv(BT / f).dropna(subset=["nrfi_prob", "fi_total_runs"])
    sets[k] = d
dl = pd.read_csv(PICKS).drop_duplicates(subset=["game_pk", "game_number"], keep="last")
sets["2026 LIVE picks"] = dl.dropna(subset=["nrfi_prob", "fi_total_runs"])
print(f"  {'dataset':<22}{'n':>7}{'mean pred':>11}{'actual NRFI':>13}{'overall gap':>13}")
for k, d in sets.items():
    a = (d["fi_total_runs"] == 0).mean()
    print(f"  {k:<22}{len(d):>7}{d['nrfi_prob'].mean():>11.4f}{a:>13.4f}"
          f"{(d['nrfi_prob'].mean()-a)*100:>+12.2f}pp")
print("  -> if a dataset is globally well calibrated, its top-decile gap is a")
print("     genuine shape finding, not a level shift.")

print("\n  Top-decile gap AFTER removing each dataset's global level shift")
print("  (recenters pred so mean(pred) == actual base rate; isolates SHAPE):")
print(f"  {'dataset':<22}{'n':>6}{'recentred pred':>16}{'actual':>9}{'gap':>10}")
for k, d in sets.items():
    shift = d["nrfi_prob"].mean() - (d["fi_total_runs"] == 0).mean()
    p = d["nrfi_prob"] - shift
    cut = p.quantile(.9)
    m = p >= cut
    a = (d.loc[m, "fi_total_runs"] == 0).mean()
    print(f"  {k:<22}{int(m.sum()):>6}{p[m].mean():>16.4f}{a:>9.4f}"
          f"{(p[m].mean()-a)*100:>+9.2f}pp")

print("\n" + "=" * 118)
print("  G. MECHANISM: the mirror needs ACTUAL NRFI < DEVIGGED MARKET NRFI.")
print("     Check that inequality directly, by model band, 2026 live priced games.")
print("=" * 118)
d = dl.copy()
d = d[d["market_nrfi_odds"].notna() & d["market_yrfi_odds"].notna()]
d = d.dropna(subset=["fi_total_runs", "nrfi_prob"])


def imp(n):
    n = float(n)
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


d["iN"] = d["market_nrfi_odds"].map(imp)
d["iY"] = d["market_yrfi_odds"].map(imp)
d["mkt"] = d["iN"] / (d["iN"] + d["iY"])
d["act"] = (d["fi_total_runs"] == 0).astype(int)
bands = [(0, .40), (.40, .45), (.45, .50), (.50, .55), (.55, .60), (.60, 1.0)]
print(f"  {'model band':<14}{'n':>6}{'model p':>10}{'devig mkt':>11}{'actual':>9}"
      f"{'mkt error':>11}{'model error':>13}")
for lo, hi in bands:
    s = d[(d["nrfi_prob"] >= lo) & (d["nrfi_prob"] < hi)]
    if not len(s):
        continue
    print(f"  [{lo:.2f},{hi:.2f})  {len(s):>6}{s['nrfi_prob'].mean():>10.4f}"
          f"{s['mkt'].mean():>11.4f}{s['act'].mean():>9.4f}"
          f"{(s['mkt'].mean()-s['act'].mean())*100:>+10.2f}pp"
          f"{(s['nrfi_prob'].mean()-s['act'].mean())*100:>+12.2f}pp")
print("\n  Same table, MARKET error only, split pre/post 2026-06-07:")
for lab, s0 in (("PRE  <06-07", d[d["date"] < "2026-06-07"]),
                ("POST >=06-07", d[d["date"] >= "2026-06-07"])):
    s = s0[s0["nrfi_prob"] >= .50]
    if len(s):
        print(f"    {lab:<14} n={len(s):>4}  devig mkt {s['mkt'].mean():.4f}  "
              f"actual {s['act'].mean():.4f}  mkt error "
              f"{(s['mkt'].mean()-s['act'].mean())*100:+.2f}pp")

print("\n" + "=" * 118)
print("  H. COVERAGE FAIRNESS: within the odds-capture era only")
print("=" * 118)
g = dl.dropna(subset=["nrfi_prob", "fi_total_runs"])
g = g[g["nrfi_prob"] >= .50]
era = g[g["date"] >= "2026-04-29"]
pr = era["market_yrfi_odds"].notna() & era["market_nrfi_odds"].notna()
print(f"  p_nrfi>=0.50 games on/after 2026-04-29 : {len(era)}  "
      f"priced {int(pr.sum())}  un-priced {int((~pr).sum())}")
print(f"    priced   YRFI hit {(era[pr]['fi_total_runs']>0).mean()*100:.2f}%")
if (~pr).sum():
    print(f"    unpriced YRFI hit {(era[~pr]['fi_total_runs']>0).mean()*100:.2f}% "
          f"(n={int((~pr).sum())})")
pre = g[g["date"] < "2026-04-29"]
print(f"  p_nrfi>=0.50 games BEFORE 2026-04-29 (no odds captured): n={len(pre)}  "
      f"YRFI hit {(pre['fi_total_runs']>0).mean()*100:.2f}%")
print("  -> the mirror's window is a survivorship window, not a random subset:")
print("     the model's earliest 2026 stretch ran hard the OTHER way.")

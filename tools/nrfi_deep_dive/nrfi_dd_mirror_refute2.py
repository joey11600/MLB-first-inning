#!/usr/bin/env python3
"""tools/nrfi_dd_mirror_refute2.py -- part 2: out-of-sample availability,
priced-vs-unpriced selection bias, and a genuine prospective slice.
Read-only."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
BT = ROOT / "data" / "backtests"
SEED, B = 20260728, 20000


def imp(a):
    try:
        n = float(str(a).strip())
    except (ValueError, TypeError):
        return None
    if not n:
        return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(a):
    try:
        n = float(str(a).strip())
    except (ValueError, TypeError):
        return None
    if not n:
        return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


def boot_days(rows, seed=SEED, b=B):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r["pl"])
    days = list(byday)
    if len(days) < 5:
        return float("nan"), float("nan")
    sums = np.array([sum(byday[d]) for d in days], float)
    cnts = np.array([len(byday[d]) for d in days], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(b, len(days)))
    m = sums[idx].sum(1) / np.maximum(cnts[idx].sum(1), 1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


print("=" * 120)
print("  A. WHY THE 3-SPLIT CANNOT BE RUN: distribution of nrfi_prob by dataset")
print("=" * 120)
files = {
    "2024 backtest": "backtest_2024-04-01_to_2024-09-30_truepit.csv",
    "2025 backtest": "backtest_2025-04-01_to_2025-09-30_truepit.csv",
    "2026 bt 04/01-05/11": "backtest_2026-04-01_to_2026-05-11_truepit.csv",
    "2026 bt 05/12-05/26": "backtest_2026-05-12_to_2026-05-26_truepit.csv",
}
print(f"  {'dataset':<22}{'n':>7}{'mean':>8}{'p50':>8}{'p90':>8}{'p99':>8}"
      f"{'max':>8}{'n>=.50':>8}{'%>=.50':>8}")
for k, f in files.items():
    d = pd.read_csv(BT / f).dropna(subset=["nrfi_prob"])
    v = d["nrfi_prob"]
    print(f"  {k:<22}{len(v):>7}{v.mean():>8.3f}{v.quantile(.5):>8.3f}"
          f"{v.quantile(.9):>8.3f}{v.quantile(.99):>8.3f}{v.max():>8.3f}"
          f"{(v>=.5).sum():>8}{(v>=.5).mean()*100:>7.1f}%")
dl = pd.read_csv(PICKS).drop_duplicates(subset=["game_pk", "game_number"], keep="last")
v = dl["nrfi_prob"].dropna()
print(f"  {'2026 LIVE picks':<22}{len(v):>7}{v.mean():>8.3f}{v.quantile(.5):>8.3f}"
      f"{v.quantile(.9):>8.3f}{v.quantile(.99):>8.3f}{v.max():>8.3f}"
      f"{(v>=.5).sum():>8}{(v>=.5).mean()*100:>7.1f}%")

print("\n  -> the 2024/2025 backtests almost never emit p_nrfi>=0.50, so the")
print("     rule's SELECTION SET is essentially empty out of sample.")
print("     Fallback: test the same rule at each dataset's OWN top decile.")
print(f"\n  {'dataset':<22}{'cut(p90)':>10}{'n':>7}{'mean p':>9}"
      f"{'actual NRFI':>13}{'YRFI hit':>10}{'calib gap':>11}")
for k, f in files.items():
    d = pd.read_csv(BT / f).dropna(subset=["nrfi_prob", "fi_total_runs"])
    cut = d["nrfi_prob"].quantile(.9)
    s = d[d["nrfi_prob"] >= cut]
    act = (s["fi_total_runs"] == 0).mean()
    print(f"  {k:<22}{cut:>10.3f}{len(s):>7}{s['nrfi_prob'].mean():>9.3f}"
          f"{act:>13.3f}{(1-act)*100:>9.1f}%{(s['nrfi_prob'].mean()-act)*100:>+10.2f}pp")
d = dl.dropna(subset=["nrfi_prob", "fi_total_runs"])
cut = d["nrfi_prob"].quantile(.9)
s = d[d["nrfi_prob"] >= cut]
act = (s["fi_total_runs"] == 0).mean()
print(f"  {'2026 LIVE picks':<22}{cut:>10.3f}{len(s):>7}{s['nrfi_prob'].mean():>9.3f}"
      f"{act:>13.3f}{(1-act)*100:>9.1f}%{(s['nrfi_prob'].mean()-act)*100:>+10.2f}pp")

print("\n" + "=" * 120)
print("  B. THE MIRROR'S EDGE LIVES ENTIRELY IN THE ODDS-CAPTURE SUBSET")
print("=" * 120)
d = dl.dropna(subset=["nrfi_prob", "fi_total_runs"])
s = d[d["nrfi_prob"] >= 0.50].copy()
s["_priced"] = s["market_yrfi_odds"].notna() & s["market_nrfi_odds"].notna()
print(f"  ALL graded games with p_nrfi>=0.50            n={len(s)}  "
      f"YRFI hit {(s['fi_total_runs']>0).mean()*100:.2f}%")
for flag, lab in ((True, "odds captured (the 324)"), (False, "odds MISSING")):
    g = s[s["_priced"] == flag]
    print(f"    {lab:<28} n={len(g):>4}  YRFI hit {(g['fi_total_runs']>0).mean()*100:>6.2f}%  "
          f"mean p_nrfi {g['nrfi_prob'].mean():.4f}")
print("  break-even on the priced subset was 50.62%.")
print(f"  If the missing-odds games had been priced at that same 50.62% break-even,")
allhit = (s["fi_total_runs"] > 0).mean()
print(f"    full-coverage YRFI hit = {allhit*100:.2f}%  vs need 50.62%  "
      f"=> edge {(allhit-0.5062)*100:+.2f}pp (was {(53.70-50.62):+.2f}pp)")
# month distribution of unpriced
up = s[~s["_priced"]]
print(f"  missing-odds games by month: "
      f"{up['date'].str[:7].value_counts().sort_index().to_dict()}")

print("\n" + "=" * 120)
print("  C. GENUINELY PROSPECTIVE SLICE (after 2026-06-07, the date the prior")
print("     rework closed and the last date any NRFI hypothesis was formed)")
print("=" * 120)
raw = list(csv.DictReader(open(PICKS, encoding="utf-8")))
seen = {}
for r in raw:
    seen[(r.get("game_pk", ""), r.get("game_number", ""))] = r
rows = []
for r in seen.values():
    try:
        fa = int(float(r["fi_away_runs"])); fh = int(float(r["fi_home_runs"]))
        m = float(r["nrfi_prob"])
    except (ValueError, TypeError, KeyError):
        continue
    iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
    pY = payout(r.get("market_yrfi_odds"))
    if not iN or not iY or pY is None:
        continue
    nrfi = 1 if (fa + fh) == 0 else 0
    rows.append({"date": r["date"], "model": m, "nrfi": nrfi, "be": iY,
                 "pl": (-1.0 if nrfi else pY)})
rows.sort(key=lambda x: x["date"])
for lab, sub in (("PRE  < 2026-06-07", [r for r in rows if r["date"] < "2026-06-07" and r["model"] >= .5]),
                 ("POST >= 2026-06-07", [r for r in rows if r["date"] >= "2026-06-07" and r["model"] >= .5]),
                 ("POST >= 2026-07-01", [r for r in rows if r["date"] >= "2026-07-01" and r["model"] >= .5])):
    n = len(sub); pl = sum(r["pl"] for r in sub)
    be = sum(r["be"] for r in sub)/n; w = sum(1-r["nrfi"] for r in sub)
    lo, hi = boot_days(sub)
    print(f"  {lab:<22} n={n:>4}  hit {w/n*100:>5.2f}% (need {be*100:5.2f}%)  "
          f"ROI {pl/n*100:>+6.2f}%  P&L {pl:>+7.2f}u  dayCI[{lo*100:+.1f}%,{hi*100:+.1f}%]")

print("\n" + "=" * 120)
print("  D. CUMULATIVE P&L PATH -- is the +19.3u a trend or a step?")
print("=" * 120)
sel = [r for r in rows if r["model"] >= .5]
byday = defaultdict(float)
for r in sel:
    byday[r["date"]] += r["pl"]
run = 0.0; path = []
for dte in sorted(byday):
    run += byday[dte]; path.append((dte, run))
step = len(path)//12 or 1
for i in range(0, len(path), step):
    print(f"    {path[i][0]}  cum {path[i][1]:+7.2f}u")
print(f"    {path[-1][0]}  cum {path[-1][1]:+7.2f}u   (final)")
peak = max(p[1] for p in path)
print(f"  peak cum {peak:+.2f}u on {[p[0] for p in path if p[1]==peak][0]}; "
      f"gave back {peak-path[-1][1]:.2f}u since")

print("\n" + "=" * 120)
print("  E. GATE SENSITIVITY -- is 0.50 special, or is it a ridge you can fall off?")
print("=" * 120)
print(f"  {'gate':>6}{'n':>7}{'hit':>9}{'need':>9}{'ROI':>9}{'P&L':>9}{'day CI':>22}")
for g in (0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62):
    sub = [r for r in rows if r["model"] >= g]
    if len(sub) < 20:
        print(f"  {g:>6.2f}{len(sub):>7}   (too few)"); continue
    n = len(sub); pl = sum(r["pl"] for r in sub)
    be = sum(r["be"] for r in sub)/n; w = sum(1-r["nrfi"] for r in sub)
    lo, hi = boot_days(sub)
    print(f"  {g:>6.2f}{n:>7}{w/n*100:>8.2f}%{be*100:>8.2f}%{pl/n*100:>+8.2f}%"
          f"{pl:>+9.2f}u   [{lo*100:+.1f}%,{hi*100:+.1f}%]")

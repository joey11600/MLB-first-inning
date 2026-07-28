#!/usr/bin/env python3
"""part 4: vig decomposition, concentration, and the 'is the book sharp there'
check. Read-only."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
dl = pd.read_csv(ROOT / "data" / "picks_2026.csv").drop_duplicates(
    subset=["game_pk", "game_number"], keep="last")


def imp(n):
    n = float(n)
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def pay(n):
    n = float(n)
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


d = dl[dl["market_nrfi_odds"].notna() & dl["market_yrfi_odds"].notna()]
d = d.dropna(subset=["fi_total_runs", "nrfi_prob"]).copy()
d["iN"] = d["market_nrfi_odds"].map(imp)
d["iY"] = d["market_yrfi_odds"].map(imp)
d["over"] = d["iN"] + d["iY"]
d["mkt"] = d["iN"] / d["over"]
d["nrfi"] = (d["fi_total_runs"] == 0).astype(int)
d["pl"] = np.where(d["nrfi"] == 1, -1.0, d["market_yrfi_odds"].map(pay))

print("=" * 112)
print("  I. WHERE THE MIRROR'S MONEY ACTUALLY COMES FROM")
print("=" * 112)
print(f"  ALL priced games            n={len(d)}  devig mkt NRFI "
      f"{d['mkt'].mean():.4f}  actual NRFI {d['nrfi'].mean():.4f}  "
      f"market error {(d['mkt'].mean()-d['nrfi'].mean())*100:+.2f}pp")
print(f"  mean DK overround (vig)     {d['over'].mean():.4f}  "
      f"=> {(d['over'].mean()-1)*100:.2f}% hold")
print("  So the SEASON-WIDE book bias toward NRFI is "
      f"{(d['mkt'].mean()-d['nrfi'].mean())*100:+.2f}pp, and the hold is "
      f"{(d['over'].mean()-1)*100:.2f}pp.")
print("  Blind YRFI on every priced game therefore returns "
      f"{d['pl'].mean()*100:+.2f}% -- the hold eats the bias.")
sel = d[d["nrfi_prob"] >= .50]
print(f"  The mirror (p>=0.50) claims to beat that by selecting {len(sel)} games "
      f"where market error is {(sel['mkt'].mean()-sel['nrfi'].mean())*100:+.2f}pp.")

print("\n" + "=" * 112)
print("  J. CONCENTRATION -- how much of the +19.3u is a handful of slates?")
print("=" * 112)
byday = sel.groupby("date")["pl"].sum().sort_values()
tot = byday.sum()
print(f"  total {tot:+.2f}u over {len(byday)} slates")
print(f"  best  5 slates contribute {byday.tail(5).sum():+.2f}u "
      f"({byday.tail(5).sum()/tot*100:.0f}% of total)")
print(f"  best 10 slates contribute {byday.tail(10).sum():+.2f}u "
      f"({byday.tail(10).sum()/tot*100:.0f}% of total)")
print(f"  median slate P&L {byday.median():+.3f}u ; "
      f"{int((byday>0).sum())}/{len(byday)} slates positive")
# jackknife over days
jl = [(tot - byday[dte]) / (len(sel) - (sel['date'] == dte).sum())
      for dte in byday.index]
print(f"  leave-one-slate-out ROI range: {min(jl)*100:+.2f}% .. {max(jl)*100:+.2f}%")

print("\n" + "=" * 112)
print("  K. IS DK SHARPEST EXACTLY WHERE THE MIRROR CLAIMS EDGE?")
print("     (proxy for sharpness: the hold DK charges; tighter hold = more")
print("      confident/liquid market)")
print("=" * 112)
sel = sel.copy()
sel["vt"] = pd.qcut(sel["over"], 3, labels=["tight", "mid", "wide"])
print(f"  {'hold tercile':<14}{'n':>6}{'mean hold':>11}{'YRFI hit':>10}"
      f"{'need':>9}{'ROI':>9}")
for t, g in sel.groupby("vt", observed=True):
    need = g["iY"].mean()
    print(f"  {str(t):<14}{len(g):>6}{(g['over'].mean()-1)*100:>10.2f}%"
          f"{(1-g['nrfi'].mean())*100:>9.2f}%{need*100:>8.2f}%"
          f"{g['pl'].mean()*100:>+8.2f}%")

print("\n" + "=" * 112)
print("  L. DOES THE MODEL ADD ANYTHING OVER JUST USING THE BOOK'S OWN LINE?")
print("     (bet YRFI whenever the DEVIGGED MARKET says NRFI >= 0.50)")
print("=" * 112)
for lab, g in (("model p_nrfi >= .50", d[d["nrfi_prob"] >= .50]),
               ("market devig >= .50", d[d["mkt"] >= .50]),
               ("BOTH", d[(d["nrfi_prob"] >= .50) & (d["mkt"] >= .50)]),
               ("model only (mkt<.50)", d[(d["nrfi_prob"] >= .50) & (d["mkt"] < .50)])):
    if not len(g):
        continue
    print(f"  {lab:<24} n={len(g):>4}  YRFI hit {(1-g['nrfi'].mean())*100:>5.2f}% "
          f"(need {g['iY'].mean()*100:5.2f}%)  ROI {g['pl'].mean()*100:>+6.2f}%  "
          f"P&L {g['pl'].sum():>+7.2f}u")
print("  -> if the market line alone reproduces the effect, the MODEL is not the")
print("     selector and the rule is just 'fade every game the book leans NRFI on'.")

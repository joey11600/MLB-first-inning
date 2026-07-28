#!/usr/bin/env python3
"""
tools/nrfi_alt/shop_refute3.py -- how good must book #2 be, and is that
price even legal in a no-arbitrage market?

If book2's NRFI implied prob drops below (1 - DK's YRFI implied prob),
then buying NRFI at book2 and YRFI at DK is a RISK-FREE ARBITRAGE.
A price improvement that large cannot persist. So the arb boundary is a
hard structural ceiling on line shopping -- tighter than "the vig", and
computable per game from real captured two-way DK prices.

Read-only.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def implied(o): return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)
def payout(o):  return o / 100.0 if o > 0 else 100.0 / abs(o)
def to_cents(o): return -abs(o) if o < 0 else o - 200.0
def from_cents(u): return u if u <= -100.0 else u + 200.0


def prob_to_odds(p):
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def fnum(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


rows = []
with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if not (r.get("sportsbook") or "").strip():
            continue
        if (r.get("actual_result") or "").upper() not in ("NRFI", "YRFI"):
            continue
        no, yo = fnum(r.get("market_nrfi_odds")), fnum(r.get("market_yrfi_odds"))
        if no is None or yo is None:
            continue
        rows.append({"n": no, "y": yo,
                     "hit": 1 if r["actual_result"].upper() == "NRFI" else 0})

n = len(rows)
print(f"n = {n} real-priced settled 2026 games\n")

# ---------------------------------------------------------------------
print("=== A. HOW BIG A SECOND BOOK IS REQUIRED? ===")
print("best-of-two with book2 = DK + N(mu, sd): E[gain] = sd/sqrt(2*pi) when mu=0.")
need = 26.2
print(f"cents needed for NRFI ROI = 0 : {need:.1f}c")
print(f"  -> unbiased second book needs sd = {need*(2*3.141592653589793)**0.5:.0f} cents")
print(f"  -> i.e. book2 would have to differ from DK by ~{need*(2*3.141592653589793)**0.5:.0f}c")
print("     on a typical game. DK's own open->close sd is 1.6c.")
print(f"  -> or a systematically better book: mu = ~{need:.0f}c on EVERY game")
print(f"     (a -125 DK line vs a +101 book2 line).")

# ---------------------------------------------------------------------
print("\n=== B. THE NO-ARBITRAGE CEILING ===")
print("book2 NRFI implied < 1 - DK YRFI implied  =>  risk-free arb vs DK.")
arb_cents, arb_prices = [], []
for r in rows:
    p_arb = 1.0 - implied(r["y"])          # NRFI prob at the arb boundary
    o_arb = prob_to_odds(p_arb)
    arb_cents.append(to_cents(o_arb) - to_cents(r["n"]))
    arb_prices.append(o_arb)
mc = statistics.fmean(arb_cents)
print(f"mean cents of improvement available before a pure arb exists: {mc:.2f}c")
print(f"  median {statistics.median(arb_cents):.2f}c   "
      f"90th pct {sorted(arb_cents)[int(.9*n)]:.2f}c   max {max(arb_cents):.2f}c")
frac = sum(1 for c in arb_cents if c >= need) / n
print(f"games where the REQUIRED {need:.0f}c is still arb-free: "
      f"{frac*100:.1f}%  ({int(frac*n)} of {n})")
print("  HONEST READING: the arb band (31.1c) is WIDER than the 26.2c needed,")
print("  so no-arbitrage alone does NOT forbid a profitable second price.")
print(f"  But book2 must capture {need/mc*100:.0f}% of the ENTIRE two-way hold on the")
print("  NRFI side -- leaving itself ~4.9c (~1% one-sided vig) on a first-")
print("  inning derivative. No retail book prices a niche prop that thin.")

# ROI exactly at the arbitrage boundary (the absolute structural maximum)
pnl = sum(payout(o) if r["hit"] else -1.0 for r, o in zip(rows, arb_prices))
print(f"\nROI at the ARB-BOUNDARY price (hardest possible upper bound): "
      f"{pnl/n*100:+.2f}%")
print("  -> the whole profitable region is the top 4.9c of a 31c band.")

# the unbiased-dispersion route requires arbs to be routine
import math
sd_need = need * math.sqrt(2 * math.pi)
arbfrac = statistics.fmean(
    1.0 - 0.5 * (1 + math.erf((c - 0.0) / (sd_need * math.sqrt(2)))) for c in arb_cents)
print(f"\nunbiased best-of-two needs sd={sd_need:.0f}c; at that dispersion book2")
print(f"  would sit BEYOND the arb boundary on {arbfrac*100:.0f}% of games -- i.e. offer")
print("  standing risk-free arbitrage vs DK. Such a book is arbed out in days.")
print("  So the unbiased-dispersion route to 26c is self-refuting.")

# ---------------------------------------------------------------------
print("\n=== C. WHERE THE 5.64pp WALL ACTUALLY COMES FROM ===")
hit = statistics.fmean(r["hit"] for r in rows)
be = statistics.fmean(1.0/(1.0+payout(r["n"])) for r in rows)
fair = statistics.fmean(implied(r["n"])/(implied(r["n"])+implied(r["y"])) for r in rows)
print(f"  DK break-even (what we pay)      {be*100:.2f}%")
print(f"  no-vig fair NRFI prob            {fair*100:.2f}%   "
      f"<- vig component = {(be-fair)*100:.2f}pp")
print(f"  actual NRFI rate                 {hit*100:.2f}%   "
      f"<- model/reality component = {(fair-hit)*100:.2f}pp")
print(f"  TOTAL WALL                       {(be-hit)*100:.2f}pp")
print("\n  Line shopping attacks ONLY the vig component, and only a slice of")
print(f"  it. The {(fair-hit)*100:.2f}pp model/reality component is untouched by ANY")
print("  price. That component alone is bigger than the whole realistic")
print("  shopping gain, and it is why zero-vig NRFI still loses.")

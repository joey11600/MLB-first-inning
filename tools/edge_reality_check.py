#!/usr/bin/env python3
"""
tools/edge_reality_check.py -- is the betting edge REAL or is it noise?

Motivated by the finding that ~90% of recent YRFI profit came from one
week (2026-05-25), which on its own wasn't even statistically
significant.  This asks the blunt question across the whole season:

  Given how many bets we've placed and how they actually graded, is our
  realized profit distinguishable from luck, or is it inside the noise
  band of a near-zero-edge bettor paying the vig?

METHOD
------
Population = every STRONG bet actually placed (bet_placed=Y), graded
WIN/LOSS, WITH real captured odds (so P&L is the true price, not the
-110 fallback).  For each bet:
  pl = payout-on-win (from the real American odds) if WIN, else -1.0.

For ALL bets, and split by side (YRFI / NRFI), and by time period:
  - n, hit%, avg market-implied% (vig-inclusive break-even), realized
    ROI per unit, total P&L.
  - 95% bootstrap confidence interval on ROI (10k resamples).  If the
    lower bound is > 0, the edge is real at 95%.  If the interval spans
    0, the edge is NOT proven -- it's inside the noise.
  - Concentration: drop the single best calendar week, recompute.
  - Durability: ROI per month.

Read-only.
"""

from __future__ import annotations

import csv
import datetime
import sys
from collections import defaultdict
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
SEED = 12345           # fixed for reproducibility
B = 10000              # bootstrap resamples


def implied(amer: str):
    s = (amer or "").strip()
    try:
        n = int(s)
    except ValueError:
        return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(amer: str):
    s = (amer or "").strip()
    try:
        n = int(s)
    except ValueError:
        return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


def load():
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("pick_strength", "") != "STRONG":
                continue
            if r.get("bet_placed", "") != "Y":
                continue
            res = r.get("graded_result", "")
            if res not in ("WIN", "LOSS"):
                continue
            side = r.get("pick_side", "")
            odds_col = "market_yrfi_odds" if side == "YRFI" else "market_nrfi_odds"
            raw = r.get(odds_col, "").strip()
            pay = payout(raw)
            imp = implied(raw)
            if pay is None or imp is None:
                continue                       # no real captured odds -> skip (don't pollute with -110 fallback)
            won = (res == "WIN")
            rows.append({
                "date": r["date"], "side": side, "won": won,
                "imp": imp, "pl": (pay if won else -1.0),
            })
    return rows


def boot_ci(pls, b=B, seed=SEED):
    if len(pls) < 5:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    arr = np.asarray(pls)
    means = rng.choice(arr, size=(b, len(arr)), replace=True).mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def summ(rows, label):
    if not rows:
        print(f"  {label:<34} (no bets)")
        return
    n = len(rows)
    w = sum(1 for r in rows if r["won"])
    pls = [r["pl"] for r in rows]
    roi = sum(pls) / n
    mkt = sum(r["imp"] for r in rows) / n
    lo, hi = boot_ci(pls)
    verdict = "REAL edge (95% CI > 0)" if lo > 0 else ("not proven (CI spans 0)" if hi > 0 else "NEGATIVE")
    print(f"  {label:<34} n={n:>3}  {w}-{n-w} ({w/n*100:.0f}%)  mkt {mkt*100:.0f}%  "
          f"ROI {roi*100:+.1f}%  P&L {sum(pls):+.1f}u  95%CI[{lo*100:+.1f}%,{hi*100:+.1f}%]  -> {verdict}")


def main():
    rows = load()
    if not rows:
        sys.exit("No graded STRONG bets with real captured odds.")
    dmin = min(r["date"] for r in rows)
    dmax = max(r["date"] for r in rows)
    print(f"Population: {len(rows)} STRONG bets with REAL captured odds, {dmin} -> {dmax}")
    print(f"(bets without captured odds are excluded so P&L = true price, not -110 fallback)\n")

    print("=== Overall edge + bootstrap 95% CI (the 'is it luck?' test) ===")
    summ(rows, "ALL STRONG bets")
    summ([r for r in rows if r["side"] == "YRFI"], "YRFI only")
    summ([r for r in rows if r["side"] == "NRFI"], "NRFI only")

    print("\n=== Durability: ROI by month (edge should persist, not be one spike) ===")
    bym = defaultdict(list)
    for r in rows:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        summ(bym[m], f"month {m}")

    print("\n=== Concentration: drop the single best calendar week ===")
    byw = defaultdict(list)
    for r in rows:
        d = datetime.date.fromisoformat(r["date"])
        wk = (d - datetime.timedelta(days=d.weekday())).isoformat()
        byw[wk].append(r)
    best_wk = max(byw, key=lambda w: sum(x["pl"] for x in byw[w]))
    best_pl = sum(x["pl"] for x in byw[best_wk])
    print(f"  best week = {best_wk}  (+{best_pl:.1f}u, {len(byw[best_wk])} bets)")
    summ([r for r in rows if not (datetime.date.fromisoformat(r['date']) - datetime.timedelta(days=datetime.date.fromisoformat(r['date']).weekday())).isoformat() == best_wk],
         "ALL bets EXCEPT best week")

    print("\n=== How many bets to CONFIRM a +2% ROI edge at 95%? (power, rough) ===")
    # std of per-bet pl ~ 1.0 (win ~ +0.9, loss -1).  To distinguish a +2%
    # ROI from 0 at 95% (1.96 SE), need SE < 0.02/1.96 -> n > (1.0*1.96/0.02)^2.
    sd = float(np.std([r["pl"] for r in rows]))
    for target in (0.05, 0.03, 0.02):
        need = (sd * 1.96 / target) ** 2
        print(f"  to confirm a {target*100:.0f}% ROI edge: ~{need:,.0f} bets "
              f"(at ~5 bets/day = ~{need/5/30:.0f} months)")
    print("\n  Read: if the CI lower bound is below 0, we do NOT yet have proof of an edge --")
    print("  the realized profit is inside the range a near-zero-edge bettor could hit by luck.")


if __name__ == "__main__":
    main()

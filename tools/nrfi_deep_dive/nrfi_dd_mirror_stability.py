#!/usr/bin/env python3
"""tools/nrfi_dd_mirror_stability.py -- is the mirror trade's +6% ROI a real
structure or one hot stretch?

Three stress tests on the REAL-PRICED 2026 sample:
  1. MONOTONICITY -- YRFI-side ROI as a smooth function of p_nrfi. A genuine
     "the book overprices NRFI worst where our model likes NRFI most" effect
     should get MORE positive as p_nrfi rises. A single spiking bucket is noise.
  2. TIME STABILITY -- month by month, plus leave-one-week-out jackknife. The
     2026-06-04 investigation found ~90% of a previous "edge" was one week.
  3. PRICE DECOMPOSITION -- split the edge into (book's implied YRFI) vs
     (actual YRFI). Shows whether we are being paid for a pricing error or
     just for the YRFI side being cheap league-wide (which needs no model).

Read-only. Analysis only.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
SEED, B = 20260728, 20000


def imp(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    return None if n == 0 else ((abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100)))


def payout(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    return None if n == 0 else ((n / 100.0) if n > 0 else (100.0 / abs(n)))


def load():
    seen = {}
    for r in csv.DictReader(open(PICKS, encoding="utf-8")):
        seen[(r.get("game_pk", ""), r.get("game_number", ""))] = r
    out = []
    for r in seen.values():
        try:
            tot = int(float(r["fi_away_runs"])) + int(float(r["fi_home_runs"]))
            m = float(r["nrfi_prob"])
        except (ValueError, TypeError, KeyError):
            continue
        iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
        pY = payout(r.get("market_yrfi_odds"))
        if not iN or not iY or pY is None:
            continue
        yrfi = 1 if tot > 0 else 0
        out.append({
            "date": r["date"], "model": m,
            "mkt_nrfi": iN / (iN + iY),
            "be_yrfi": iY, "yrfi": yrfi,
            "pl": (pY if yrfi else -1.0),
        })
    out.sort(key=lambda x: x["date"])
    return out


def boot_days(rows, seed=SEED):
    if len(rows) < 8:
        return float("nan"), float("nan")
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r["pl"])
    keys = list(byday)
    if len(keys) < 5:
        return float("nan"), float("nan")
    sums = np.array([sum(byday[k]) for k in keys], float)
    cnts = np.array([len(byday[k]) for k in keys], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    m = sums[idx].sum(axis=1) / np.maximum(cnts[idx].sum(axis=1), 1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def stat(rows, label, ci=False):
    if not rows:
        print(f"  {label:<30} n=   0")
        return
    n = len(rows)
    pl = sum(r["pl"] for r in rows)
    hit = sum(r["yrfi"] for r in rows) / n
    be = sum(r["be_yrfi"] for r in rows) / n
    s = (f"  {label:<30} n={n:>4}  YRFI hit {hit*100:>5.1f}%  book needs {be*100:>5.1f}%"
         f"  edge {(hit-be)*100:>+5.1f}pp  ROI {pl/n*100:>+6.1f}%  P&L {pl:>+7.2f}u")
    if ci:
        lo, hi = boot_days(rows)
        s += f"  dayCI[{lo*100:+.0f}%,{hi*100:+.0f}%]"
    print(s)


def main():
    rows = load()
    print("=" * 122)
    print("  MIRROR-TRADE STABILITY -- bet YRFI at the captured DK YRFI price, 2026 real prices only")
    print("=" * 122)
    print(f"  {len(rows)} priced graded games, {len({r['date'] for r in rows})} slates\n")

    print("  1. MONOTONICITY -- YRFI-side result by model p_nrfi band")
    print("  " + "-" * 118)
    bands = [(0.00, 0.36), (0.36, 0.44), (0.44, 0.48), (0.48, 0.50),
             (0.50, 0.52), (0.52, 0.56), (0.56, 0.62), (0.62, 1.01)]
    for lo, hi in bands:
        sub = [r for r in rows if lo <= r["model"] < hi]
        stat(sub, f"p_nrfi {lo:.2f}-{hi:.2f}")
    print("\n  (the production system already bets the p_nrfi < 0.36 band. Everything")
    print("   from 0.50 up is the NEW territory the mirror trade would add.)")

    sel = [r for r in rows if r["model"] >= 0.50]
    print("\n  2. TIME STABILITY of the mirror selection (p_nrfi >= 0.50)")
    print("  " + "-" * 118)
    stat(sel, "FULL SAMPLE", ci=True)
    bym = defaultdict(list)
    for r in sel:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        stat(bym[m], f"  month {m}")

    print("\n  leave-one-WEEK-out jackknife (does any single week carry it?):")
    byw = defaultdict(list)
    for r in sel:
        d = dt.date.fromisoformat(r["date"])
        byw[f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"].append(r)
    base_roi = sum(r["pl"] for r in sel) / len(sel)
    worst = []
    for w in sorted(byw):
        rest = [r for r in sel if r not in byw[w]]
        roi = sum(r["pl"] for r in rest) / len(rest) if rest else float("nan")
        worst.append((roi, w, len(byw[w])))
    worst.sort()
    print(f"    full-sample ROI {base_roi*100:+.1f}%.  ROI with each week REMOVED:")
    for roi, w, n in worst[:4]:
        print(f"      drop {w} (n={n:>3}) -> {roi*100:+6.1f}%   <- most load-bearing")
    for roi, w, n in worst[-2:]:
        print(f"      drop {w} (n={n:>3}) -> {roi*100:+6.1f}%")
    pos = sum(1 for roi, _, _ in worst if roi > 0)
    print(f"    {pos}/{len(worst)} leave-one-week-out refits stay positive.")

    print("\n  3. PRICE DECOMPOSITION -- where does the edge come from?")
    print("  " + "-" * 118)
    allr = rows
    print(f"    league-wide (bet YRFI on ALL {len(allr)} priced games):")
    stat(allr, "    baseline YRFI, no model", ci=True)
    print(f"    the model's contribution is the DIFFERENCE between the two lines.")
    stat(sel, "    mirror selection >=0.50", ci=True)
    d = (sum(r["pl"] for r in sel) / len(sel)) - (sum(r["pl"] for r in allr) / len(allr))
    print(f"\n    model adds {d*100:+.1f}pp of ROI over betting YRFI blind.")
    print(f"    the mirror selection fires on {len(sel)}/{len(allr)} "
          f"({100*len(sel)/len(allr):.0f}%) of priced games.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""tools/nrfi_dd_mirror_attack.py -- independent attempt to REFUTE the
"MIRROR TRADE" rule:

    bet YRFI at the captured DK YRFI price whenever p_nrfi >= 0.50
    claim: 324 bets, 53.7% hit vs 50.6% break-even

Everything is re-derived from the raw CSVs; nothing is taken on faith from the
originating script. Read-only.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
BT = ROOT / "data" / "backtests"
SEED, B = 424242, 20000


def imp(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(a, shade=0.0):
    """decimal profit per 1u risked. `shade` worsens the price by that many
    points of implied probability (i.e. we pay more vig)."""
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    p = (n / 100.0) if n > 0 else (100.0 / abs(n))
    if shade:
        q = 1.0 / (1.0 + p)
        q = min(q + shade, 0.98)
        p = (1.0 - q) / q
    return p


def load_priced():
    raw = list(csv.DictReader(open(PICKS, encoding="utf-8")))
    seen = {}
    for r in raw:
        seen[(r.get("game_pk", ""), r.get("game_number", ""))] = r
    rows = []
    for r in seen.values():
        try:
            fa = int(float(r["fi_away_runs"]))
            fh = int(float(r["fi_home_runs"]))
            p = float(r["nrfi_prob"])
        except (ValueError, TypeError, KeyError):
            continue
        iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
        if not iN or not iY:
            continue
        rows.append({
            "date": r["date"], "p": p, "nrfi": 1 if (fa + fh) == 0 else 0,
            "iN": iN, "iY": iY,
            "oN": r.get("market_nrfi_odds", ""), "oY": r.get("market_yrfi_odds", ""),
            "bet_placed": r.get("bet_placed", ""),
            "side": r.get("pick_side", ""), "strength": r.get("pick_strength", ""),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def yrfi_pl(rows, shade=0.0):
    return [(payout(r["oY"], shade) if r["nrfi"] == 0 else -1.0) for r in rows]


def boot_days(rows, pls, seed=SEED, ret_dist=False):
    byday = defaultdict(list)
    for r, x in zip(rows, pls):
        byday[r["date"]].append(x)
    days = list(byday)
    if len(days) < 5:
        return (float("nan"), float("nan")) if not ret_dist else (float("nan"), float("nan"), None)
    sums = np.array([sum(byday[d]) for d in days], dtype=float)
    cnts = np.array([len(byday[d]) for d in days], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(B, len(days)))
    means = sums[idx].sum(axis=1) / np.maximum(cnts[idx].sum(axis=1), 1)
    lo, hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return (lo, hi, means) if ret_dist else (lo, hi)


def line(label, rows, shade=0.0, ci=True):
    if not rows:
        print(f"  {label:<42} n=0")
        return None
    pls = yrfi_pl(rows, shade)
    n = len(rows)
    hit = sum(1 for r in rows if r["nrfi"] == 0) / n
    be = sum(r["iY"] for r in rows) / n
    roi = sum(pls) / n
    s = (f"  {label:<42} n={n:>4} d={len({r['date'] for r in rows}):>3}  "
         f"hit {hit*100:>5.1f}% (need {be*100:.1f}%)  ROI {roi*100:>+6.1f}%  "
         f"P&L {sum(pls):>+7.1f}u")
    if ci:
        lo, hi = boot_days(rows, pls)
        s += f"  dayCI[{lo*100:+.1f}%,{hi*100:+.1f}%] {'CI>0' if lo > 0 else 'SPANS 0'}"
    print(s)


def season_band(path, lo, hi=1.01):
    n = k = 0
    psum = 0.0
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            p = float(r["nrfi_prob"])
            fa = float(r["fi_away_runs"]); fh = float(r["fi_home_runs"])
        except (ValueError, TypeError, KeyError):
            continue
        if math.isnan(p) or math.isnan(fa) or math.isnan(fh):
            continue
        if lo <= p < hi:
            n += 1; psum += p
            k += 1 if (fa + fh) == 0 else 0
    return n, (k / n if n else float("nan")), (psum / n if n else float("nan"))


def main():
    rows = load_priced()
    sel = [r for r in rows if r["p"] >= 0.50]
    print("=" * 122)
    print("  REFUTATION PASS -- MIRROR TRADE: bet YRFI when p_nrfi >= 0.50")
    print("=" * 122)
    print(f"  priced+graded 2026 games: {len(rows)}   slates: {len({r['date'] for r in rows})}\n")

    print("  --- A. reproduce the claim (real captured DK YRFI prices) ---")
    line("MIRROR  p_nrfi >= 0.50  (the rule)", sel)
    line("complement p_nrfi < 0.50 (bet YRFI)", [r for r in rows if r["p"] < 0.50])
    line("ALL priced games (bet YRFI)", rows)
    line("live gate zone p_nrfi < 0.36 (bet YRFI)", [r for r in rows if r["p"] < 0.36])
    line("dead zone 0.36 <= p < 0.50 (bet YRFI)", [r for r in rows if 0.36 <= r["p"] < 0.50])

    print("\n  --- B. monotonicity: does the edge GROW with p_nrfi? ---")
    for a, b in [(0.50, 0.52), (0.52, 0.54), (0.54, 0.56), (0.56, 0.60), (0.60, 1.01)]:
        line(f"p_nrfi [{a:.2f},{b:.2f})", [r for r in rows if a <= r["p"] < b], ci=False)

    print("\n  --- C. price robustness (shade = extra vig we pay) ---")
    for sh in (0.0, 0.01, 0.02, 0.025, 0.05):
        line(f"MIRROR >=0.50, prices {sh*100:.1f}c worse", sel, shade=sh)

    print("\n  --- D. time stability ---")
    months = sorted({r["date"][:7] for r in sel})
    for m in months:
        line(f"MIRROR >=0.50  {m}", [r for r in sel if r["date"][:7] == m], ci=False)
    print("     leave-one-month-out:")
    for m in months:
        line(f"MIRROR >=0.50  drop {m}", [r for r in sel if r["date"][:7] != m], ci=False)
    pls = yrfi_pl(sel)
    byday = defaultdict(list)
    for r, x in zip(sel, pls):
        byday[r["date"]].append(x)
    tot, n = sum(pls), len(pls)
    drops = sorted(((sum(v), len(v), d) for d, v in byday.items()), reverse=True)
    print("     single-slate jackknife:")
    for s, c, d in drops[:3]:
        print(f"       best  {d}: {s:+.2f}u / {c} bets -> ROI w/o it {(tot-s)/(n-c)*100:+.1f}%")
    for s, c, d in drops[-2:]:
        print(f"       worst {d}: {s:+.2f}u / {c} bets -> ROI w/o it {(tot-s)/(n-c)*100:+.1f}%")

    print("\n  --- E. MECHANISM TEST out-of-sample (hit rate only, no odds needed) ---")
    print("      For the mirror to be structural the model must OVER-PREDICT NRFI")
    print("      in its own p>=0.50 tail. Must hold in 2024 AND 2025, not just 2026.")
    print(f"      {'sample':<36}{'n':>7}{'mean pred':>12}{'actual':>10}{'gap p-a':>10}"
          f"{'YRFI hit':>10}")
    for name, f in [
        ("2024 backtest", BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"),
        ("2025 backtest", BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"),
        ("2026 backtest 04-01..05-11", BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv"),
        ("2026 backtest 05-12..05-26", BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"),
    ]:
        nn, act, pred = season_band(f, 0.50)
        print(f"      {name:<36}{nn:>7}{pred:>12.4f}{act:>10.4f}{pred-act:>+10.4f}"
              f"{(1-act)*100:>9.1f}%")
    nn = kk = 0; psum = 0.0
    for r in csv.DictReader(open(PICKS, encoding="utf-8")):
        try:
            p = float(r["nrfi_prob"]); fa = float(r["fi_away_runs"]); fh = float(r["fi_home_runs"])
        except (ValueError, TypeError, KeyError):
            continue
        if p >= 0.50:
            nn += 1; psum += p; kk += 1 if (fa + fh) == 0 else 0
    print(f"      {'2026 LIVE all graded':<36}{nn:>7}{psum/nn:>12.4f}{kk/nn:>10.4f}"
          f"{psum/nn-kk/nn:>+10.4f}{(1-kk/nn)*100:>9.1f}%")
    mp = sum(r["p"] for r in sel)/len(sel); ma = sum(r["nrfi"] for r in sel)/len(sel)
    print(f"      {'2026 LIVE priced subset (THE RULE)':<36}{len(sel):>7}{mp:>12.4f}"
          f"{ma:>10.4f}{mp-ma:>+10.4f}{(1-ma)*100:>9.1f}%")

    print("\n  --- F. is the book or the model closer to truth on these games? ---")
    bk = sum(r["iN"] / (r["iN"] + r["iY"]) for r in sel) / len(sel)
    print(f"      mean model P(NRFI)        {mp:.4f}   |err| {abs(mp-ma):.4f}")
    print(f"      mean book de-vig P(NRFI)  {bk:.4f}   |err| {abs(bk-ma):.4f}")
    print(f"      mean ACTUAL NRFI          {ma:.4f}")
    print(f"      mean raw implied YRFI (break-even) {sum(r['iY'] for r in sel)/len(sel):.4f}")

    print("\n  --- G. null probability given search exposure ---")
    arr = np.asarray(pls)
    lo, hi, means = boot_days(sel, pls, ret_dist=True)
    centered = means - means.mean()
    p_one = float((centered >= arr.mean()).mean())
    for k in (1, 20, 70):
        print(f"      k={k:>3} cells: raw one-sided p={p_one:.4f}  "
              f"Sidak family-wise p={1-(1-p_one)**k:.4f}  "
              f"Bonferroni alpha={0.05/k:.5f} -> "
              f"{'PASSES' if p_one < 0.05/k else 'FAILS'}")
    print(f"      day-block 95% CI on ROI: [{lo*100:+.1f}%, {hi*100:+.1f}%]")

    print("\n  --- H. selection-on-price-availability check ---")
    allrows = list(csv.DictReader(open(PICKS, encoding="utf-8")))
    g = [r for r in allrows if (r.get("fi_total_runs") or "").strip() != ""]
    def band(rs):
        out = []
        for r in rs:
            try:
                p = float(r["nrfi_prob"])
            except (ValueError, TypeError):
                continue
            if p >= 0.50:
                out.append(r)
        return out
    gb = band(g)
    priced = [r for r in gb if imp(r.get("market_nrfi_odds")) and imp(r.get("market_yrfi_odds"))]
    unpriced = [r for r in gb if r not in priced]
    def nrfirate(rs):
        k = 0; n = 0
        for r in rs:
            try:
                t = float(r["fi_total_runs"])
            except (ValueError, TypeError):
                continue
            n += 1; k += 1 if t == 0 else 0
        return n, (k/n if n else float("nan"))
    n1, a1 = nrfirate(priced); n2, a2 = nrfirate(unpriced)
    print(f"      p>=0.50 graded WITH captured prices   n={n1:>4}  actual NRFI {a1:.4f}"
          f"  -> YRFI hit {(1-a1)*100:.1f}%")
    print(f"      p>=0.50 graded WITHOUT captured prices n={n2:>4}  actual NRFI {a2:.4f}"
          f"  -> YRFI hit {(1-a2)*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

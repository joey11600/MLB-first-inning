#!/usr/bin/env python3
"""
tools/market_signal_check.py -- monthly re-check of the market-derived
edge signal.  Re-run as the 2026 odds sample grows.

Premise (the one untested-but-promising lever as of 2026-06-04): our
real edge shows up as beating the book, so the bets where our model most
DISAGREES with the (de-vigged) market line should be our best ones.  On
2026-06-04 (140 bets) the 10pp+ disagreement bucket was +12% ROI on 70
bets -- suggestive but the 3-way split was non-monotonic = likely noise,
and the bootstrap CI spanned 0.  WATCH this: when the 10pp+ bucket's 95%
CI lower bound clears 0, it becomes a shippable "only bet when we
strongly disagree with the book" filter.

Also reports CLV (open->close line move).  On 2026-06-04 CLV was ~flat on
111/114 bets -- the first-inning lines barely move, so CLV is a weak
edge-confirmation here; re-check whether that changes.

Read-only.  Usage: python tools/market_signal_check.py [--since YYYY-MM-DD]
"""
from __future__ import annotations
import argparse, csv, sys
from pathlib import Path
try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")
ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
SEED, B = 20260604, 10000


def imp(a):
    s = (a or "").strip()
    try: n = int(s)
    except ValueError: return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(a):
    s = (a or "").strip()
    try: n = int(s)
    except ValueError: return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


def boot_ci(pls):
    if len(pls) < 5:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(SEED)
    arr = np.asarray(pls)
    means = rng.choice(arr, size=(B, len(arr)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def load(since):
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] < since:
                continue
            if r.get("pick_strength") != "STRONG" or r.get("bet_placed") != "Y":
                continue
            if r.get("graded_result") not in ("WIN", "LOSS"):
                continue
            side = r["pick_side"]
            mn, my = imp(r.get("market_nrfi_odds", "")), imp(r.get("market_yrfi_odds", ""))
            if not mn or not my:
                continue
            mkt = (mn / (mn + my)) if side == "NRFI" else (my / (mn + my))   # de-vig, our side
            try: mnrfi = float(r.get("nrfi_prob", "") or "")
            except ValueError: continue
            model = mnrfi if side == "NRFI" else (1 - mnrfi)
            pay = payout(r.get("market_" + side.lower() + "_odds", ""))
            won = r["graded_result"] == "WIN"
            on, oy = imp(r.get("opened_nrfi_odds", "")), imp(r.get("opened_yrfi_odds", ""))
            clv = None
            if on and oy:
                op = (on / (on + oy)) if side == "NRFI" else (oy / (on + oy))
                clv = mkt - op
            rows.append({"dis": model - mkt, "won": won, "pl": (pay if won else -1.0), "clv": clv})
    return rows


def show(rs, label, ci=False):
    if not rs:
        print(f"  {label:<30} (0)"); return
    n = len(rs); w = sum(1 for r in rs if r["won"]); pl = sum(r["pl"] for r in rs)
    s = f"  {label:<30} n={n:>3}  {w}-{n-w} ({w/n*100:>3.0f}%)  ROI {pl/n*100:>+5.0f}%  P&L {pl:+.1f}u"
    if ci:
        lo, hi = boot_ci([r["pl"] for r in rs])
        verdict = "REAL (CI>0)" if lo > 0 else "unproven (CI spans 0)"
        s += f"  95%CI[{lo*100:+.0f}%,{hi*100:+.0f}%] -> {verdict}"
    print(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()
    rows = load(args.since)
    print(f"STRONG bets with real closing odds since {args.since}: {len(rows)}\n")

    print("=== Model-vs-market disagreement -> ROI (the lever to watch) ===")
    show([r for r in rows if r["dis"] < 0.05], "weak edge (<5pp)")
    show([r for r in rows if 0.05 <= r["dis"] < 0.10], "mid edge (5-10pp)")
    show([r for r in rows if r["dis"] >= 0.10], "BIG edge (10pp+)", ci=True)
    print("  -> SHIP a disagreement filter once BIG-edge bucket CI lower bound clears 0.")

    print("\n=== CLV (open->close line move toward us) ===")
    cl = [r for r in rows if r["clv"] is not None]
    print(f"  bets with opening odds: {len(cl)}")
    show([r for r in cl if r["clv"] > 0.01], "positive CLV")
    show([r for r in cl if -0.01 <= r["clv"] <= 0.01], "flat CLV")
    show([r for r in cl if r["clv"] < -0.01], "negative CLV")


if __name__ == "__main__":
    main()

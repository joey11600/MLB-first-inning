#!/usr/bin/env python3
"""
tools/top_n_per_day.py -- what if we only bet the single best pick each day?

Operator question, 2026-07-27. The selectivity finding (raising the
STRONG YRFI gate) showed that fewer, higher-conviction bets massively
outperform volume. The natural next question is the limit case: one bet
per day.

Two ways to rank "best", both reported because they disagree and the
difference is informative:

  by CONFIDENCE -- highest model probability on the side we bet.
                   Ignores price entirely.
  by EDGE       -- model probability minus the market's implied
                   probability. This is the quantity that actually
                   determines expected value, and it is what Kelly
                   sizes on.

Universe is every graded, placed 2026 bet that has a REAL captured DK
price. Rows without a captured price are excluded rather than settled at
the -110 fallback, because that fallback is what inflated the April
figures (see CHANGELOG 2026-07-27).

Usage:
    python tools/top_n_per_day.py
    python tools/top_n_per_day.py --gate 0.64   # restrict to the new STRONG gate first
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.kelly_backtest import load_bets, decimal_b, implied  # noqa: E402


def settle(b):
    return decimal_b(b["odds"]) if b["win"] else -1.0


def summarize(name, bets):
    if not bets:
        print(f"  {name:<34}{'0':>6}   (no qualifying bets)")
        return None
    n = len(bets)
    w = sum(1 for b in bets if b["win"])
    pl = sum(settle(b) for b in bets)
    need = st.mean([implied(b["odds"]) for b in bets])
    print(f"  {name:<34}{n:>6}{w:>5}{n-w:>5}{100*w/n:>7.1f}%{100*need:>7.1f}%"
          f"{pl:>+9.2f}u{100*pl/n:>8.2f}%")
    return pl


def longest_losing_streak(bets):
    """Bets must already be in chronological order."""
    worst = cur = 0
    for b in bets:
        cur = 0 if b["win"] else cur + 1
        worst = max(worst, cur)
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=float, default=None,
                    help="only consider bets with model p >= this first")
    args = ap.parse_args()

    bets = load_bets()
    if args.gate is not None:
        bets = [b for b in bets if b["p_claimed"] >= args.gate]
        print(f"Pre-filtered to model p >= {args.gate}: {len(bets)} bets")
    print(f"Universe: {len(bets)} graded placed bets with a real captured DK price")

    by_day = defaultdict(list)
    for b in bets:
        b["edge"] = b["p_claimed"] - implied(b["odds"])
        by_day[b["date"]].append(b)
    days = sorted(by_day)
    print(f"Spanning {len(days)} betting days "
          f"({st.mean([len(by_day[d]) for d in days]):.1f} bets/day currently)\n")

    print(f"  {'strategy':<34}{'bets':>6}{'W':>5}{'L':>5}{'hit%':>7}{'need':>7}"
          f"{'P&L':>10}{'ROI%':>9}")
    print("  " + "-" * 84)

    summarize("bet everything (baseline)", bets)
    print()

    results = {}
    for rank_key, label in (("p_claimed", "confidence"), ("edge", "edge")):
        for k in (1, 2, 3):
            sel = []
            for d in days:
                ordered = sorted(by_day[d], key=lambda b: -b[rank_key])
                sel.extend(ordered[:k])
            sel.sort(key=lambda b: b["date"])
            pl = summarize(f"top {k} per day by {label}", sel)
            results[(rank_key, k)] = (sel, pl)
        print()

    # Focused detail on the headline answer
    print("=" * 88)
    print("  THE ANSWER: one bet per day")
    print("=" * 88)
    for rank_key, label in (("p_claimed", "confidence"), ("edge", "edge")):
        sel, pl = results[(rank_key, 1)]
        n = len(sel)
        w = sum(1 for b in sel if b["win"])
        streak = longest_losing_streak(sel)
        avg_odds = st.mean([b["odds"] for b in sel])
        print(f"\n  Ranking by {label}:")
        print(f"    {n} bets over {len(days)} days -- {w}W-{n-w}L ({100*w/n:.1f}%)")
        print(f"    P&L {pl:+.2f}u   ROI {100*pl/n:+.2f}%   avg price {avg_odds:.0f}")
        print(f"    longest losing streak: {streak} bets")
        # month split -- does it hold up throughout?
        bym = defaultdict(list)
        for b in sel:
            bym[b["date"][:7]].append(b)
        parts = []
        for m in sorted(bym):
            g = bym[m]
            parts.append(f"{m} {sum(settle(x) for x in g):+.2f}u ({len(g)})")
        print(f"    by month: {' | '.join(parts)}")

    print("\n" + "=" * 88)
    print("  READ THIS BEFORE ACTING ON THE NUMBER ABOVE")
    print("=" * 88)
    print("  Ranking by CONFIDENCE ignores price -- it will happily take the")
    print("  chalkiest game on the board. Ranking by EDGE is the EV-correct")
    print("  criterion and is what Kelly stakes on.")
    print("  Both are computed with hindsight over one partial season on a")
    print("  small sample. A one-per-day strategy concentrates all variance")
    print("  into very few bets: check the losing-streak line, not just P&L.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
analyze_strong_zones.py -- audit our model's performance specifically on
STRONG NRFI / STRONG YRFI picks and simulate the user's parlay strategy.

The user's actual betting strategy:
  - Each slate, pick the top-3 most confident games and parlay them.
  - "Top 3 most probable" = highest |P(NRFI) - 0.50| games.
  - Parlay leg type (NRFI / YRFI) follows whichever side the model favors.

This script answers:
  1. What fraction of STRONG NRFI / STRONG YRFI picks won historically?
  2. If we parlayed the top-3 each day, what fraction of those parlays won?
  3. Per day, how many STRONG picks did we have?
  4. What threshold maximizes STRONG NRFI / YRFI hit rate?

Reads picks_2026.csv directly (which has the production model's calls).
"""

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
PICKS = ROOT / "data" / "picks_2026.csv"


def load_graded():
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            try:
                p_nrfi = float(r.get("nrfi_prob", "0.5"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "date":        r.get("date", ""),
                "game_pk":     r.get("game_pk", ""),
                "away":        r.get("away_team", ""),
                "home":        r.get("home_team", ""),
                "p_nrfi":      p_nrfi,
                "p_yrfi":      1.0 - p_nrfi,
                "edge":        abs(p_nrfi - 0.50),
                "actual":      actual,
                "pick_label":  r.get("pick_label", ""),
                "pick_side":   r.get("pick_side", ""),
                "pick_strength": r.get("pick_strength", ""),
                "graded_result": r.get("graded_result", ""),
            })
    return rows


def hit_at_threshold(rows, *, p_strong_nrfi: float, p_strong_yrfi: float):
    """For given thresholds, classify each row STRONG NRFI / STRONG YRFI / OTHER
    and return zone hit rates."""
    nrfi_picks = [r for r in rows if r["p_nrfi"] >= p_strong_nrfi]
    yrfi_picks = [r for r in rows if r["p_nrfi"] <= p_strong_yrfi]

    nrfi_wins = sum(1 for r in nrfi_picks if r["actual"] == "NRFI")
    yrfi_wins = sum(1 for r in yrfi_picks if r["actual"] == "YRFI")

    return {
        "nrfi_n":   len(nrfi_picks),
        "nrfi_w":   nrfi_wins,
        "nrfi_pct": nrfi_wins / len(nrfi_picks) if nrfi_picks else 0.0,
        "yrfi_n":   len(yrfi_picks),
        "yrfi_w":   yrfi_wins,
        "yrfi_pct": yrfi_wins / len(yrfi_picks) if yrfi_picks else 0.0,
    }


def per_day_top_k(rows, k: int):
    """Group by date, sort each day by edge desc, take top K, simulate parlay."""
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)

    parlay_hits = parlay_total = 0
    leg_hits = leg_total = 0
    days_with_k = 0
    pick_breakdown = {"NRFI": 0, "YRFI": 0}

    daily_results = []
    for date in sorted(by_day):
        day_games = sorted(by_day[date], key=lambda r: -r["edge"])
        top = day_games[:k]
        if len(top) < k:
            continue
        days_with_k += 1
        all_won = True
        for r in top:
            # Pick the side the model favors
            side = "NRFI" if r["p_nrfi"] >= 0.5 else "YRFI"
            won = r["actual"] == side
            leg_total += 1
            if won:
                leg_hits += 1
            else:
                all_won = False
            pick_breakdown[side] += 1
        parlay_total += 1
        if all_won:
            parlay_hits += 1
        daily_results.append((date, top, all_won))

    return {
        "days_with_k":    days_with_k,
        "parlay_hits":    parlay_hits,
        "parlay_total":   parlay_total,
        "parlay_pct":     parlay_hits / parlay_total if parlay_total else 0.0,
        "leg_hits":       leg_hits,
        "leg_total":      leg_total,
        "leg_pct":        leg_hits / leg_total if leg_total else 0.0,
        "pick_breakdown": pick_breakdown,
        "daily_results":  daily_results,
    }


def main():
    rows = load_graded()
    print(f"Loaded {len(rows)} graded 2026 games\n")

    # ---- 1. Current production thresholds (0.62 / 0.42) ----
    print("=" * 72)
    print("  Current production thresholds: STRONG NRFI >= 0.62, STRONG YRFI <= 0.42")
    print("=" * 72)
    h = hit_at_threshold(rows, p_strong_nrfi=0.62, p_strong_yrfi=0.42)
    print(f"  STRONG NRFI: {h['nrfi_w']}-{h['nrfi_n']-h['nrfi_w']}  ({h['nrfi_pct']*100:.1f}%)  n={h['nrfi_n']}")
    print(f"  STRONG YRFI: {h['yrfi_w']}-{h['yrfi_n']-h['yrfi_w']}  ({h['yrfi_pct']*100:.1f}%)  n={h['yrfi_n']}")

    # ---- 2. Threshold sweep ----
    print()
    print("=" * 72)
    print("  Threshold sweep: hit rate vs sample size tradeoff")
    print("=" * 72)
    print("  STRONG NRFI side:")
    print(f"  {'threshold':>10} {'n':>5} {'hits':>5} {'rate':>7}  parlay-3 hit (NRFI^3)")
    for t in [0.55, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72]:
        nrfi_picks = [r for r in rows if r["p_nrfi"] >= t]
        n = len(nrfi_picks)
        w = sum(1 for r in nrfi_picks if r["actual"] == "NRFI")
        rate = w / n if n else 0
        p3 = rate ** 3
        print(f"  {t:>10.2f} {n:>5} {w:>5} {rate*100:>6.1f}%  {p3*100:>5.1f}%")

    print("\n  STRONG YRFI side:")
    print(f"  {'threshold':>10} {'n':>5} {'hits':>5} {'rate':>7}  parlay-3 hit (YRFI^3)")
    for t in [0.45, 0.42, 0.40, 0.38, 0.35, 0.33, 0.30, 0.28, 0.25]:
        yrfi_picks = [r for r in rows if r["p_nrfi"] <= t]
        n = len(yrfi_picks)
        w = sum(1 for r in yrfi_picks if r["actual"] == "YRFI")
        rate = w / n if n else 0
        p3 = rate ** 3
        print(f"  {t:>10.2f} {n:>5} {w:>5} {rate*100:>6.1f}%  {p3*100:>5.1f}%")

    # ---- 3. Per-day top-K parlay simulation ----
    print()
    print("=" * 72)
    print("  Daily TOP-K parlay strategy (rank by edge = |P(NRFI) - 0.50|)")
    print("=" * 72)
    for k in [1, 2, 3, 4, 5]:
        out = per_day_top_k(rows, k)
        print(f"  K={k}: days with K games {out['days_with_k']:>3}, "
              f"parlay {out['parlay_hits']}-{out['parlay_total']-out['parlay_hits']} "
              f"({out['parlay_pct']*100:5.1f}%)  "
              f"per-leg {out['leg_hits']}-{out['leg_total']-out['leg_hits']} "
              f"({out['leg_pct']*100:5.1f}%)  "
              f"NRFI/YRFI mix {out['pick_breakdown']['NRFI']}/{out['pick_breakdown']['YRFI']}")

    # ---- 4. Per-day top-K SAME-SIDE parlay (top 3 NRFI or top 3 YRFI separately) ----
    print()
    print("=" * 72)
    print("  Per-side daily TOP-K parlay (top-K NRFI and top-K YRFI as separate parlays)")
    print("=" * 72)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)

    for k in [1, 2, 3, 4]:
        nrfi_parlay_hits = nrfi_parlay_total = 0
        yrfi_parlay_hits = yrfi_parlay_total = 0
        nrfi_legs_w = nrfi_legs_n = 0
        yrfi_legs_w = yrfi_legs_n = 0

        for date, games in by_day.items():
            # Top-K NRFI: highest P(NRFI)
            n_sorted = sorted(games, key=lambda r: -r["p_nrfi"])
            top_n = n_sorted[:k]
            if len(top_n) >= k:
                nrfi_parlay_total += 1
                won_all = True
                for r in top_n:
                    nrfi_legs_n += 1
                    if r["actual"] == "NRFI":
                        nrfi_legs_w += 1
                    else:
                        won_all = False
                if won_all:
                    nrfi_parlay_hits += 1

            # Top-K YRFI: lowest P(NRFI) = highest P(YRFI)
            y_sorted = sorted(games, key=lambda r: r["p_nrfi"])
            top_y = y_sorted[:k]
            if len(top_y) >= k:
                yrfi_parlay_total += 1
                won_all = True
                for r in top_y:
                    yrfi_legs_n += 1
                    if r["actual"] == "YRFI":
                        yrfi_legs_w += 1
                    else:
                        won_all = False
                if won_all:
                    yrfi_parlay_hits += 1

        nrfi_leg_pct = nrfi_legs_w / nrfi_legs_n * 100 if nrfi_legs_n else 0
        yrfi_leg_pct = yrfi_legs_w / yrfi_legs_n * 100 if yrfi_legs_n else 0
        nrfi_par_pct = nrfi_parlay_hits / nrfi_parlay_total * 100 if nrfi_parlay_total else 0
        yrfi_par_pct = yrfi_parlay_hits / yrfi_parlay_total * 100 if yrfi_parlay_total else 0

        print(f"  K={k}: NRFI legs {nrfi_legs_w}-{nrfi_legs_n - nrfi_legs_w} ({nrfi_leg_pct:5.1f}%)  "
              f"NRFI parlays {nrfi_parlay_hits}-{nrfi_parlay_total-nrfi_parlay_hits} ({nrfi_par_pct:5.1f}%)  "
              f"YRFI legs {yrfi_legs_w}-{yrfi_legs_n - yrfi_legs_w} ({yrfi_leg_pct:5.1f}%)  "
              f"YRFI parlays {yrfi_parlay_hits}-{yrfi_parlay_total-yrfi_parlay_hits} ({yrfi_par_pct:5.1f}%)")

    # ---- 5. P&L estimate at -110 odds for each parlay strategy ----
    print()
    print("=" * 72)
    print("  Estimated parlay P&L (assumes ~+600 / 6.0x payout for 3-leg @ -110)")
    print("=" * 72)
    PARLAY_PAYOUT_3 = 6.0  # 3-leg parlay rough payout
    for k in [3]:
        # Mixed top-3
        out = per_day_top_k(rows, k)
        # Per-side
        nrfi_p_w = nrfi_p_n = yrfi_p_w = yrfi_p_n = 0
        for date, games in by_day.items():
            n_sorted = sorted(games, key=lambda r: -r["p_nrfi"])
            y_sorted = sorted(games, key=lambda r: r["p_nrfi"])
            top_n = n_sorted[:k]; top_y = y_sorted[:k]
            if len(top_n) >= k:
                nrfi_p_n += 1
                if all(r["actual"] == "NRFI" for r in top_n):
                    nrfi_p_w += 1
            if len(top_y) >= k:
                yrfi_p_n += 1
                if all(r["actual"] == "YRFI" for r in top_y):
                    yrfi_p_w += 1

        # P&L: each parlay = -1u stake; win = +6u; loss = -1u
        for label, w, n in [("Top-3 mixed", out["parlay_hits"], out["parlay_total"]),
                              ("Top-3 NRFI side", nrfi_p_w, nrfi_p_n),
                              ("Top-3 YRFI side", yrfi_p_w, yrfi_p_n),
                              ("Both sides daily (2 parlays/day)", nrfi_p_w + yrfi_p_w, nrfi_p_n + yrfi_p_n)]:
            stake = n
            pnl = w * PARLAY_PAYOUT_3 - (n - w)
            roi = pnl / stake * 100 if stake else 0
            print(f"  {label:<35} {w:>3}/{n:<3}  P&L: {pnl:+7.2f}u   ROI: {roi:+6.1f}%")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/nrfi_alt/shop_refute.py -- adversarial test of the "add a second
sportsbook and take the better NRFI price" proposal.

Read-only. Touches no production config or model.

Everything below is measured on data/picks_2026.csv rows that carry a REAL
captured DraftKings price (sportsbook non-blank) AND a settled first inning.
No synthesized odds, no -110 placeholders.
"""
from __future__ import annotations

import csv
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "picks_2026.csv"


# --------------------------------------------------------------------------
# american-odds helpers
# --------------------------------------------------------------------------
def implied(o: float) -> float:
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def payout(o: float) -> float:
    """profit per 1u risked on a win."""
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def to_cents(o: float) -> float:
    """
    Monotone 'cents' index of an American price.
      -120 -> -120 ; -100 -> -100 ; +100 -> -100 ; +110 -> -90
    So a -110/-110 two-way market is a 20-cent line, matching trade usage.
    """
    return -abs(o) if o < 0 else o - 200.0


def from_cents(u: float) -> float:
    return u if u <= -100.0 else u + 200.0


def bump(o: float, cents: float) -> float:
    return from_cents(to_cents(o) + cents)


# --------------------------------------------------------------------------
def load():
    rows = []
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            book = (r.get("sportsbook") or "").strip()
            res = (r.get("actual_result") or "").upper()
            no = r.get("market_nrfi_odds") or ""
            yo = r.get("market_yrfi_odds") or ""
            if not book or res not in ("NRFI", "YRFI") or not no:
                continue
            try:
                nrfi_odds = float(no)
                yrfi_odds = float(yo) if yo else None
            except ValueError:
                continue
            rows.append({
                "date": r["date"],
                "month": r["date"][:7],
                "nrfi_odds": nrfi_odds,
                "yrfi_odds": yrfi_odds,
                "nrfi_hit": 1 if res == "NRFI" else 0,
                "p_nrfi": float(r["nrfi_prob"]) if r.get("nrfi_prob") else None,
                "side": (r.get("pick_side") or "").strip(),
                "strength": (r.get("pick_strength") or "").strip(),
            })
    return rows


def roi_nrfi(rows, cents=0.0):
    """flat 1u NRFI on every row, at DK price shifted by `cents`."""
    if not rows:
        return None
    pnl = 0.0
    for r in rows:
        o = bump(r["nrfi_odds"], cents)
        pnl += payout(o) if r["nrfi_hit"] else -1.0
    return pnl / len(rows)


def breakeven(rows, cents=0.0):
    """average break-even hit rate 1/(1+payout) at the shifted price."""
    return statistics.fmean(1.0 / (1.0 + payout(bump(r["nrfi_odds"], cents)))
                            for r in rows)


# --------------------------------------------------------------------------
def day_block_ci(rows, fn, iters=4000, seed=11):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    rnd = random.Random(seed)
    out = []
    for _ in range(iters):
        samp = []
        for _ in range(len(days)):
            samp.extend(byday[days[rnd.randrange(len(days))]])
        v = fn(samp)
        if v is not None:
            out.append(v)
    out.sort()
    lo = out[int(0.025 * len(out))]
    hi = out[int(0.975 * len(out))]
    return lo, hi


# --------------------------------------------------------------------------
def main():
    rows = load()
    print(f"real-priced + settled 2026 rows: n={len(rows)}  "
          f"days={len(set(r['date'] for r in rows))}")

    hit = statistics.fmean(r["nrfi_hit"] for r in rows)
    be0 = breakeven(rows, 0.0)
    print(f"\n=== 1. THE WALL, re-derived ===")
    print(f"actual NRFI hit rate      : {hit*100:.2f}%  (n={len(rows)})")
    print(f"avg break-even @ DK price : {be0*100:.2f}%")
    print(f"WALL                      : {(be0-hit)*100:.2f} pp")
    print(f"flat-1u NRFI ROI @ DK     : {roi_nrfi(rows)*100:+.2f}%")
    lo, hi = day_block_ci(rows, lambda s: roi_nrfi(s))
    print(f"  day-block 95% CI        : [{lo*100:+.2f}%, {hi*100:+.2f}%]")

    # no-vig reference
    two = [r for r in rows if r["yrfi_odds"] is not None]
    hold = statistics.fmean(implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]) - 1.0
                            for r in two)
    fair = statistics.fmean(implied(r["nrfi_odds"]) /
                            (implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]))
                            for r in two)
    hit2 = statistics.fmean(r["nrfi_hit"] for r in two)
    print(f"\navg two-way hold          : {hold*100:.2f}%  (n={len(two)})")
    print(f"devigged fair NRFI prob   : {fair*100:.2f}%  vs actual {hit2*100:.2f}%")
    print(f"  -> zero-vig NRFI ROI    : "
          f"{(hit2/fair-1)*100:+.2f}%   (line shopping cannot beat this)")

    # ---------------------------------------------------------------
    print(f"\n=== 2. WHAT A FIXED CENT BUMP BUYS (real outcomes) ===")
    print(f"{'cents':>6} {'break-even':>11} {'wall pp':>8} {'wall closed':>12} {'ROI':>9}")
    base_wall = be0 - hit
    for c in (0, 2, 3, 4, 5, 10, 15, 20, 22, 25, 30):
        be = breakeven(rows, c)
        w = be - hit
        print(f"{c:>6} {be*100:>10.2f}% {w*100:>7.2f}% "
              f"{(1 - w/base_wall)*100:>11.1f}% {roi_nrfi(rows, c)*100:>8.2f}%")

    # cents needed for break-even
    lo_c, hi_c = 0.0, 200.0
    for _ in range(60):
        mid = (lo_c + hi_c) / 2
        if roi_nrfi(rows, mid) < 0:
            lo_c = mid
        else:
            hi_c = mid
    print(f"\ncents required to reach ROI = 0 : {hi_c:.1f}")

    # ---------------------------------------------------------------
    print(f"\n=== 3. REALISTIC BEST-OF-TWO SIMULATION ===")
    print("book2 price = DK price + N(mu, sd) cents, snapped to the 5c ladder;")
    print("we take max(DK, book2) on every game. 400 sims, real outcomes.")
    print(f"{'mu':>5} {'sd':>4} {'eff.cents':>10} {'ROI':>9} {'d ROI':>8} {'wall closed':>12}")
    base_roi = roi_nrfi(rows)
    for mu, sd in ((0, 5), (0, 8), (0, 10), (0, 15), (0, 20),
                   (-5, 10), (-3, 10), (3, 10), (5, 10), (5, 15)):
        rnd = random.Random(99)
        rois, gains = [], []
        for _ in range(400):
            pnl = 0.0
            gsum = 0.0
            for r in rows:
                u0 = to_cents(r["nrfi_odds"])
                u1 = 5.0 * round((u0 + rnd.gauss(mu, sd)) / 5.0)
                u = max(u0, u1)
                gsum += u - u0
                pnl += payout(from_cents(u)) if r["nrfi_hit"] else -1.0
            rois.append(pnl / len(rows))
            gains.append(gsum / len(rows))
        r_m = statistics.fmean(rois)
        g_m = statistics.fmean(gains)
        w = breakeven(rows, g_m) - hit
        print(f"{mu:>5} {sd:>4} {g_m:>9.2f}c {r_m*100:>8.2f}% "
              f"{(r_m-base_roi)*100:>+7.2f}% {(1-w/base_wall)*100:>11.1f}%")

    # ---------------------------------------------------------------
    print(f"\n=== 4. OUT-OF-SAMPLE: does +4c ever flip a period positive? ===")
    print(f"{'period':>12} {'n':>5} {'hit':>7} {'BE@DK':>7} {'wall':>7} "
          f"{'ROI@DK':>8} {'ROI+4c':>8} {'ROI+10c':>8}")
    groups = [("2026-04+05", [r for r in rows if r["month"] in ("2026-04", "2026-05")]),
              ("2026-06", [r for r in rows if r["month"] == "2026-06"]),
              ("2026-07", [r for r in rows if r["month"] == "2026-07"])]
    halves = sorted(set(r["date"] for r in rows))
    cut = halves[len(halves) // 2]
    groups.append((f"1st half", [r for r in rows if r["date"] < cut]))
    groups.append((f"2nd half", [r for r in rows if r["date"] >= cut]))
    for name, g in groups:
        if not g:
            continue
        h = statistics.fmean(r["nrfi_hit"] for r in g)
        b = breakeven(g, 0.0)
        print(f"{name:>12} {len(g):>5} {h*100:>6.1f}% {b*100:>6.1f}% "
              f"{(b-h)*100:>6.2f} {roi_nrfi(g)*100:>7.2f}% "
              f"{roi_nrfi(g,4)*100:>7.2f}% {roi_nrfi(g,10)*100:>7.2f}%")

    # ---------------------------------------------------------------
    print(f"\n=== 5. SUBSETS WE WOULD ACTUALLY BET ===")
    subs = [
        ("all priced", rows),
        ("model side=NRFI", [r for r in rows if r["side"] == "NRFI"]),
        ("STRONG NRFI", [r for r in rows if r["side"] == "NRFI" and r["strength"] == "STRONG"]),
        ("p_nrfi>=0.55", [r for r in rows if r["p_nrfi"] and r["p_nrfi"] >= 0.55]),
        ("p_nrfi>=0.60", [r for r in rows if r["p_nrfi"] and r["p_nrfi"] >= 0.60]),
        ("edge>=2pp", [r for r in rows if r["p_nrfi"] and
                       r["p_nrfi"] - implied(r["nrfi_odds"]) >= 0.02]),
        ("edge>=5pp", [r for r in rows if r["p_nrfi"] and
                       r["p_nrfi"] - implied(r["nrfi_odds"]) >= 0.05]),
    ]
    print(f"{'subset':>16} {'n':>5} {'hit':>7} {'BE@DK':>7} {'wall':>7} "
          f"{'ROI@DK':>8} {'ROI+4c':>8} {'+4c CI':>20}")
    for name, g in subs:
        if len(g) < 20:
            print(f"{name:>16} {len(g):>5}  (too few)")
            continue
        h = statistics.fmean(r["nrfi_hit"] for r in g)
        b = breakeven(g, 0.0)
        lo, hi = day_block_ci(g, lambda s: roi_nrfi(s, 4.0), iters=2000)
        print(f"{name:>16} {len(g):>5} {h*100:>6.1f}% {b*100:>6.1f}% "
              f"{(b-h)*100:>6.2f} {roi_nrfi(g)*100:>7.2f}% "
              f"{roi_nrfi(g,4)*100:>7.2f}% [{lo*100:>+6.1f},{hi*100:>+6.1f}]")

    # ---------------------------------------------------------------
    print(f"\n=== 6. SURVIVE 10 CENTS OF WORSE PRICING? ===")
    print("i.e. if DK's captured price is 10c optimistic vs what we could")
    print("actually get (stale scrape, limits, line move):")
    for c in (0, -5, -10):
        print(f"  base {c:>3}c: ROI@base {roi_nrfi(rows, c)*100:>6.2f}%   "
              f"ROI@base+4c {roi_nrfi(rows, c+4)*100:>6.2f}%   "
              f"ROI@base+10c {roi_nrfi(rows, c+10)*100:>6.2f}%")

    # ---------------------------------------------------------------
    print(f"\n=== 7. SAME LEVER APPLIED TO YRFI (opportunity cost) ===")
    y = [r for r in rows if r["yrfi_odds"] is not None]
    def roi_y(rs, cents=0.0):
        if not rs:
            return None
        return statistics.fmean(
            (payout(bump(r["yrfi_odds"], cents)) if not r["nrfi_hit"] else -1.0)
            for r in rs)
    for name, g in (("all YRFI", y),
                    ("STRONG YRFI", [r for r in y if r["side"] == "YRFI"
                                     and r["strength"] == "STRONG"])):
        if len(g) < 20:
            continue
        print(f"{name:>14} n={len(g):>4}  ROI@DK {roi_y(g)*100:>+6.2f}%  "
              f"+4c {roi_y(g,4)*100:>+6.2f}%  "
              f"delta {(roi_y(g,4)-roi_y(g))*100:>+5.2f}pp")


if __name__ == "__main__":
    main()

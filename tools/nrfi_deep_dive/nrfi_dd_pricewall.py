#!/usr/bin/env python3
"""
tools/nrfi_dd_pricewall.py -- characterise the NRFI PRICE WALL.

Premise (from the 2026-06-07 rework): the model already out-predicts the
book on NRFI. So better prediction is not the lever. The only way NRFI
becomes +EV is finding a SUBSET of games where the ACTUAL NRFI rate
EXCEEDS the book's implied probability at the price we actually got.

This script is read-only analysis. It touches no production config.

Everything here is measured on data/picks_2026.csv rows that carry a REAL
captured market_nrfi_odds (sportsbook column non-blank), i.e. no assumed
prices, no -110 placeholders.
"""
from __future__ import annotations

import csv
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def load():
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    out = []
    for r in raw:
        no = fnum(r.get("market_nrfi_odds"))
        yo = fnum(r.get("market_yrfi_odds"))
        book = (r.get("sportsbook") or "").strip()
        actual = (r.get("actual_result") or "").upper()
        out.append({
            "date": r["date"],
            "month": r["date"][:7],
            "away": r.get("away_team", ""),
            "home": r.get("home_team", ""),
            "book": book,
            "nrfi_odds": no,
            "yrfi_odds": yo,
            "real": bool(book) and no is not None,
            "settled": actual in ("NRFI", "YRFI"),
            "nrfi_hit": 1 if actual == "NRFI" else 0,
            "p_nrfi": fnum(r.get("nrfi_prob")),
            "p_nrfi_raw": fnum(r.get("nrfi_prob_raw")),
            "lam": fnum(r.get("lambda_lr_total")),
            "park": fnum(r.get("park_factor")),
            "strength": (r.get("pick_strength") or "").strip(),
            "side": (r.get("pick_side") or "").strip(),
        })
    return out


# ---------------------------------------------------------------------------
# day-block bootstrap: resample DAYS with replacement, not bets
# ---------------------------------------------------------------------------
def day_block_ci(rows, stat_fn, iters=4000, seed=7):
    """rows: list of dicts each with 'date'. stat_fn(list)->float or None."""
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    vals = []
    for _ in range(iters):
        samp = []
        for _ in range(len(days)):
            samp.extend(byday[days[rnd.randrange(len(days))]])
        v = stat_fn(samp)
        if v is not None and not math.isnan(v):
            vals.append(v)
    if len(vals) < 100:
        return (float("nan"), float("nan"))
    vals.sort()
    lo = vals[int(0.025 * len(vals))]
    hi = vals[int(0.975 * len(vals)) - 1]
    return (lo, hi)


def roi_units(rows):
    """Flat 1u NRFI bet on every row, at the real captured price."""
    if not rows:
        return None
    u = 0.0
    for r in rows:
        u += payout(r["nrfi_odds"]) if r["nrfi_hit"] else -1.0
    return u / len(rows)


def gap_stat(rows):
    if not rows:
        return None
    act = sum(r["nrfi_hit"] for r in rows) / len(rows)
    imp = sum(implied(r["nrfi_odds"]) for r in rows) / len(rows)
    return act - imp


def report_buckets(title, rows, keyfn, edges, labelfmt="{lo:.3f}-{hi:.3f}"):
    print()
    print("=" * 96)
    print(title)
    print("=" * 96)
    print(f"{'bucket':>16} {'n':>5} {'actualNRFI':>11} {'impliedNRFI':>12} "
          f"{'gap':>8} {'u/bet':>8} {'total_u':>9}  {'gap 95% CI (day-block)':>26}")
    buckets = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sel = [r for r in rows if keyfn(r) is not None and lo <= keyfn(r) < hi]
        buckets.append((lo, hi, sel))
    for lo, hi, sel in buckets:
        lab = labelfmt.format(lo=lo, hi=hi)
        if not sel:
            print(f"{lab:>16} {0:>5}")
            continue
        n = len(sel)
        act = sum(r["nrfi_hit"] for r in sel) / n
        imp = sum(implied(r["nrfi_odds"]) for r in sel) / n
        upb = roi_units(sel)
        ci = day_block_ci(sel, gap_stat) if n >= 25 else (float("nan"), float("nan"))
        cis = (f"[{ci[0]:+.3f},{ci[1]:+.3f}]"
               if not math.isnan(ci[0]) else "n<25 - no CI")
        flag = ""
        if n < 30:
            flag = "  <-- n too small"
        print(f"{lab:>16} {n:>5} {act:>11.3f} {imp:>12.3f} {act - imp:>+8.3f} "
              f"{upb:>+8.3f} {upb * n:>+9.2f}  {cis:>26}{flag}")


def main():
    rows = load()
    real = [r for r in rows if r["real"] and r["settled"]]
    allreal = [r for r in rows if r["real"]]

    print("#" * 96)
    print("# 1. HOW MUCH REAL-PRICED NRFI DATA EXISTS")
    print("#" * 96)
    print(f"total rows in picks_2026.csv          : {len(rows)}")
    print(f"rows with a REAL captured NRFI price  : {len(allreal)}")
    print(f"  ...of those, settled (NRFI/YRFI)    : {len(real)}   <-- the analysis sample")
    bym = defaultdict(int)
    bymr = defaultdict(int)
    for r in rows:
        bym[r["month"]] += 1
    for r in real:
        bymr[r["month"]] += 1
    print()
    print(f"{'month':>10} {'games':>7} {'real-priced+settled':>21} {'coverage':>9}")
    for m in sorted(bym):
        cov = bymr[m] / bym[m] if bym[m] else 0
        print(f"{m:>10} {bym[m]:>7} {bymr[m]:>21} {cov:>8.0%}")
    print()
    days = len(set(r["date"] for r in real))
    print(f"distinct slate-days in sample: {days}")
    base_act = sum(r["nrfi_hit"] for r in real) / len(real)
    base_imp = sum(implied(r["nrfi_odds"]) for r in real) / len(real)
    base_u = roi_units(real)
    lo, hi = day_block_ci(real, gap_stat)
    print(f"OVERALL actual NRFI rate      : {base_act:.4f}")
    print(f"OVERALL book-implied NRFI     : {base_imp:.4f}   (vig included)")
    print(f"OVERALL gap (act - implied)   : {base_act - base_imp:+.4f}  "
          f"95% CI [{lo:+.4f},{hi:+.4f}]")
    print(f"Flat-1u NRFI on EVERY game    : {base_u:+.4f} u/bet, "
          f"{base_u * len(real):+.2f}u over {len(real)} bets")

    # -----------------------------------------------------------------
    print()
    print("#" * 96)
    print("# 4. THE VIG -- is NRFI the juiced side?  (games with BOTH prices)")
    print("#" * 96)
    both = [r for r in allreal if r["nrfi_odds"] is not None and r["yrfi_odds"] is not None]
    print(f"games with both sides captured: {len(both)}")
    if both:
        tot = [implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]) for r in both]
        overround = sum(tot) / len(tot)
        # de-vig proportionally, then measure how the juice splits
        share_n = []
        excess_n = []
        excess_y = []
        for r in both:
            i_n, i_y = implied(r["nrfi_odds"]), implied(r["yrfi_odds"])
            s = i_n + i_y
            fair_n = i_n / s
            fair_y = i_y / s
            share_n.append(fair_n)
            excess_n.append(i_n - fair_n)
            excess_y.append(i_y - fair_y)
        print(f"mean total implied (overround): {overround:.4f}  "
              f"-> {100 * (overround - 1):.2f}% vig on the pair")
        print(f"mean juice loaded on NRFI side: {100 * sum(excess_n) / len(excess_n):+.2f} pp")
        print(f"mean juice loaded on YRFI side: {100 * sum(excess_y) / len(excess_y):+.2f} pp")
        bs = [r for r in both if r["settled"]]
        if bs:
            fn = [implied(r["nrfi_odds"]) / (implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]))
                  for r in bs]
            act = sum(r["nrfi_hit"] for r in bs) / len(bs)
            print(f"settled subset n={len(bs)}: mean DE-VIGGED fair NRFI "
                  f"{sum(fn) / len(fn):.4f} vs actual {act:.4f} "
                  f"-> book's TRUE opinion is off by {act - sum(fn) / len(fn):+.4f}")
        print()
        print("Juice split by NRFI price level (is the wall uniform?):")
        print(f"{'nrfi price':>14} {'n':>5} {'overround':>10} {'NRFI excess':>12} {'YRFI excess':>12}")
        for lo_, hi_ in [(-400, -140), (-140, -120), (-120, -105), (-105, 105),
                         (105, 130), (130, 400)]:
            sel = [r for r in both if lo_ <= r["nrfi_odds"] < hi_]
            if not sel:
                continue
            ov = sum(implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]) for r in sel) / len(sel)
            ex_n = sum(implied(r["nrfi_odds"]) -
                       implied(r["nrfi_odds"]) / (implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]))
                       for r in sel) / len(sel)
            ex_y = sum(implied(r["yrfi_odds"]) -
                       implied(r["yrfi_odds"]) / (implied(r["nrfi_odds"]) + implied(r["yrfi_odds"]))
                       for r in sel) / len(sel)
            print(f"{lo_:>6} to {hi_:>4} {len(sel):>5} {ov:>10.4f} "
                  f"{100 * ex_n:>+11.2f}pp {100 * ex_y:>+11.2f}pp")

    # -----------------------------------------------------------------
    print()
    print("#" * 96)
    print("# 2/3. WHERE (IF ANYWHERE) DOES ACTUAL EXCEED IMPLIED?")
    print("#      positive 'gap' = book UNDERPRICES NRFI = the only place profit can live")
    print("#" * 96)

    report_buckets("BY BOOK-IMPLIED NRFI PROBABILITY (the price itself)",
                   real, lambda r: implied(r["nrfi_odds"]),
                   [0.30, 0.45, 0.48, 0.51, 0.54, 0.57, 0.60, 0.80])

    report_buckets("BY MODEL p_nrfi (as-served calibrated prob, nrfi_prob column)",
                   real, lambda r: r["p_nrfi"],
                   [0.0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.64, 0.68, 1.01])

    report_buckets("BY LAMBDA (lambda_lr_total) -- low lambda = quiet game",
                   real, lambda r: r["lam"],
                   [0.0, 0.55, 0.65, 0.75, 0.85, 0.95, 1.10, 5.0])

    report_buckets("BY PARK FACTOR",
                   real, lambda r: r["park"],
                   [0.0, 0.92, 0.97, 1.00, 1.03, 1.08, 3.0])

    # month, to see if the wall moves
    print()
    print("=" * 96)
    print("BY MONTH (does the wall move over the season?)")
    print("=" * 96)
    print(f"{'month':>10} {'n':>5} {'actual':>8} {'implied':>8} {'gap':>8} {'u/bet':>8} {'total_u':>9}")
    for m in sorted(set(r["month"] for r in real)):
        sel = [r for r in real if r["month"] == m]
        n = len(sel)
        act = sum(r["nrfi_hit"] for r in sel) / n
        imp = sum(implied(r["nrfi_odds"]) for r in sel) / n
        u = roi_units(sel)
        print(f"{m:>10} {n:>5} {act:>8.3f} {imp:>8.3f} {act - imp:>+8.3f} "
              f"{u:>+8.3f} {u * n:>+9.2f}")


if __name__ == "__main__":
    main()

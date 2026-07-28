#!/usr/bin/env python3
"""
tools/nrfi_dd_devig.py -- decompose the NRFI price wall into its two parts.

The wall (gap between what DK charges and what actually happens) is:

    charged_implied - actual  =  [vig loaded on the NRFI side]
                              +  [how wrong the book's TRUE opinion is]

Separating them answers a question the raw gap cannot: if we got PERFECT
no-vig prices (line shopping, exchange, whatever), would NRFI be +EV?
If the second term is still negative, better pricing does not save it.

De-vig is proportional (i_n / (i_n + i_y)), the standard two-way method.
Both sides' real DK prices are required, so this runs on the 1,128-game
both-sides-captured settled subset.

Read-only.
"""
from __future__ import annotations

import csv
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402


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


def day_ci(rows, fn, iters=4000, seed=23):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    vals = []
    for _ in range(iters):
        s = []
        for _ in range(len(days)):
            s.extend(byday[days[rnd.randrange(len(days))]])
        v = fn(s)
        if v is not None and not math.isnan(v):
            vals.append(v)
    if len(vals) < 100:
        return (float("nan"), float("nan"))
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1])


def load():
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    keep = []
    for r in raw:
        no, yo = fnum(r.get("market_nrfi_odds")), fnum(r.get("market_yrfi_odds"))
        if not (r.get("sportsbook") or "").strip() or no is None or yo is None:
            continue
        a = (r.get("actual_result") or "").upper()
        if a not in ("NRFI", "YRFI"):
            continue
        fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        keep.append((r, tv, bv, no, yo, 1 if a == "NRFI" else 0))
    Xt = np.asarray([k[1] for k in keep], dtype=float)
    Xb = np.asarray([k[2] for k in keep], dtype=float)
    rawp = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    out = []
    for (r, _, _, no, yo, y), pr in zip(keep, rawp):
        i_n, i_y = implied(no), implied(yo)
        out.append({
            "date": r["date"], "y": y, "nrfi_odds": no,
            "charged": i_n,
            "fair": i_n / (i_n + i_y),
            "vig_n": i_n - i_n / (i_n + i_y),
            "p_cur": cal.predict(float(pr)),
            "p_served": fnum(r.get("nrfi_prob")),
            "lam": fnum(r.get("lambda_lr_total")),
            "park": fnum(r.get("park_factor")),
        })
    return out


def block(title, rows, keyfn, edges, fmt="{lo:.2f}-{hi:.2f}"):
    print()
    print("=" * 110)
    print(title)
    print("=" * 110)
    print(f"{'bucket':>14} {'n':>5} {'actual':>8} {'charged':>8} {'FAIR':>8} "
          f"{'vig_pp':>7} {'act-charged':>12} {'act-FAIR':>10}  {'act-FAIR 95% CI':>22}")
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sel = [r for r in rows if keyfn(r) is not None and lo <= keyfn(r) < hi]
        lab = fmt.format(lo=lo, hi=hi)
        if len(sel) < 5:
            print(f"{lab:>14} {len(sel):>5}")
            continue
        n = len(sel)
        act = sum(r["y"] for r in sel) / n
        ch = sum(r["charged"] for r in sel) / n
        fa = sum(r["fair"] for r in sel) / n
        vg = sum(r["vig_n"] for r in sel) / n
        ci = (day_ci(sel, lambda s: sum(x["y"] for x in s) / len(s)
                     - sum(x["fair"] for x in s) / len(s))
              if n >= 25 else (float("nan"), float("nan")))
        cis = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if not math.isnan(ci[0]) else "n<25 no CI"
        flag = "  <-- n small" if n < 30 else ""
        print(f"{lab:>14} {n:>5} {act:>8.3f} {ch:>8.3f} {fa:>8.3f} "
              f"{100 * vg:>6.2f}pp {act - ch:>+12.3f} {act - fa:>+10.3f}  {cis:>22}{flag}")


def main():
    rows = load()
    n = len(rows)
    act = sum(r["y"] for r in rows) / n
    ch = sum(r["charged"] for r in rows) / n
    fa = sum(r["fair"] for r in rows) / n
    vg = sum(r["vig_n"] for r in rows) / n
    print(f"both-sides real-priced settled games: {n} over "
          f"{len(set(r['date'] for r in rows))} days\n")
    print("THE WALL, DECOMPOSED (whole sample, blind NRFI on every game)")
    print(f"  actual NRFI rate                     {act:.4f}")
    print(f"  DK charged (implied, vig in)         {ch:.4f}")
    print(f"  DK de-vigged FAIR opinion            {fa:.4f}")
    print(f"  --------------------------------------------")
    print(f"  wall component 1: vig on NRFI side   {100 * vg:5.2f} pp")
    print(f"  wall component 2: book opinion error {100 * (fa - act):5.2f} pp  "
          f"(book too bullish on NRFI by this much)")
    print(f"  TOTAL WALL                           {100 * (ch - act):5.2f} pp")
    lo, hi = day_ci(rows, lambda s: sum(x["y"] for x in s) / len(s)
                    - sum(x["fair"] for x in s) / len(s))
    print(f"\n  KEY TEST -- at a hypothetical ZERO-VIG price, blind NRFI ROI = "
          f"{100 * (act - fa):+.2f}%  95% CI [{100 * lo:+.2f}%,{100 * hi:+.2f}%]")
    print("  (if this is negative and its CI excludes 0, better pricing cannot save NRFI)")

    block("BY DK CHARGED PRICE (implied prob incl. vig)", rows,
          lambda r: r["charged"],
          [0.30, 0.45, 0.48, 0.51, 0.54, 0.57, 0.60, 0.85])
    block("BY CURRENT-MODEL p_nrfi (recomputed, leaky/optimistic)", rows,
          lambda r: r["p_cur"],
          [0.0, 0.42, 0.46, 0.50, 0.54, 0.58, 0.62, 1.01])
    block("BY LAMBDA", rows, lambda r: r["lam"],
          [0.0, 0.55, 0.65, 0.75, 0.85, 0.95, 1.10, 5.0])
    block("BY PARK FACTOR", rows, lambda r: r["park"],
          [0.85, 0.95, 0.98, 1.01, 1.05, 3.0])

    # how many de-vig buckets came out positive, across all of the above?
    print()
    print("=" * 110)
    print("SCORECARD: buckets with act-FAIR > 0 (book's TRUE opinion beatable)")
    print("=" * 110)
    tot = pos = 0
    for keyfn, edges in [
        (lambda r: r["charged"], [0.30, 0.45, 0.48, 0.51, 0.54, 0.57, 0.60, 0.85]),
        (lambda r: r["p_cur"], [0.0, 0.42, 0.46, 0.50, 0.54, 0.58, 0.62, 1.01]),
        (lambda r: r["lam"], [0.0, 0.55, 0.65, 0.75, 0.85, 0.95, 1.10, 5.0]),
        (lambda r: r["park"], [0.85, 0.95, 0.98, 1.01, 1.05, 3.0]),
    ]:
        for i in range(len(edges) - 1):
            sel = [r for r in rows if keyfn(r) is not None
                   and edges[i] <= keyfn(r) < edges[i + 1]]
            if len(sel) < 25:
                continue
            tot += 1
            a = sum(r["y"] for r in sel) / len(sel)
            f = sum(r["fair"] for r in sel) / len(sel)
            if a - f > 0:
                pos += 1
    print(f"buckets with n>=25 examined: {tot}   of those act > FAIR: {pos}")
    print(f"expected by chance if book is perfectly fair: ~{tot / 2:.0f}")


if __name__ == "__main__":
    main()

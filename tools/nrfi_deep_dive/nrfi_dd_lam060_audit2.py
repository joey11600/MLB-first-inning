#!/usr/bin/env python3
"""tools/nrfi_dd_lam060_audit2.py -- follow-ups to the lam<=0.60 / price>=-115 audit.

  A  does the PRICE half of the rule carry any signal, or is it just a
     re-labelling of "the book disagrees with us"?
  B  profit concentration -- how many single days carry the whole +1.34u?
  C  leave-one-day-out sensitivity
  D  2024 vs 2025 lambda-ordering stability (the 3-split direction test),
     with day-block CIs on the lift
  E  is the model actually more accurate than the DEVIGGED book inside
     the cell?  (log-loss / Brier head-to-head)
Read-only.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402
from tools.nrfi_dd_lam060_audit import (  # noqa: E402
    load_2026, add_model_lambda, load_backtest, implied, payout, devig,
    cell_stats, day_boot, BT,
)


def main():
    rows = add_model_lambda(load_2026())
    pr = [r for r in rows if r["odds"] is not None and r["yodds"] is not None
          and r["lam_mdl"] is not None and r["lam_csv"] is not None]

    print("=" * 98)
    print("  A.  DOES THE PRICE FILTER CARRY SIGNAL, OR IS IT THE DISAGREEMENT FILTER?")
    print("=" * 98)
    for lk in ("lam_mdl", "lam_csv"):
        band = [r for r in pr if r[lk] <= 0.60]
        print(f"\n  lambda def {lk}: all games with lambda<=0.60, split by DK NRFI price")
        print(f"    {'price bucket':<20}{'n':>5}{'hit%':>8}{'need%':>8}{'ROI%':>9}"
              f"{'book devig%':>13}{'model raw%':>12}")
        for lo, hi, tag in ((-999, -141, "worse than -140"), (-140, -126, "-140..-126"),
                            (-125, -116, "-125..-116"), (-115, -106, "-115..-106"),
                            (-105, 99, "-105..-100"), (100, 999, "plus money")):
            sub = [r for r in band if lo <= r["odds"] <= hi]
            if not sub:
                continue
            s = cell_stats(sub)
            bk = st.mean([devig(r["odds"], r["yodds"]) for r in sub])
            md = st.mean([math.exp(-r[lk]) for r in sub])
            print(f"    {tag:<20}{s['n']:>5}{100*s['hit']:>8.1f}{100*s['need']:>8.1f}"
                  f"{100*s['roi']:>+9.1f}{100*bk:>13.1f}{100*md:>12.1f}")
        print("    (monotone-in-price improvement would be the signature of real pricing edge)")

    print("\n" + "=" * 98)
    print("  B.  PROFIT CONCENTRATION inside the winning cell")
    print("=" * 98)
    for lk in ("lam_mdl", "lam_csv"):
        sub = [r for r in pr if r[lk] <= 0.60 and r["odds"] >= -115]
        byday = defaultdict(float)
        for r in sub:
            byday[r["date"]] += payout(r["odds"]) if r["y"] else -1.0
        tot = sum(byday.values())
        top = sorted(byday.items(), key=lambda kv: -kv[1])
        print(f"\n  {lk}: n={len(sub)} bets on {len(byday)} days, total {tot:+.2f}u")
        print("    best 3 days: " + ", ".join(f"{d} {v:+.2f}u" for d, v in top[:3]))
        cum = 0.0
        for i, (d, v) in enumerate(top, 1):
            cum += v
            if cum >= tot and tot > 0:
                print(f"    the top {i} of {len(byday)} days account for 100% of the profit "
                      f"(the remaining {len(byday)-i} days are net {tot-cum:+.2f}u)")
                break

    print("\n" + "=" * 98)
    print("  C.  LEAVE-ONE-DAY-OUT -- how many days must be removed to kill the edge?")
    print("=" * 98)
    for lk in ("lam_mdl", "lam_csv"):
        sub = [r for r in pr if r[lk] <= 0.60 and r["odds"] >= -115]
        days = sorted({r["date"] for r in sub})
        rois = []
        for d in days:
            keep = [r for r in sub if r["date"] != d]
            rois.append((cell_stats(keep)["roi"], d))
        rois.sort()
        base = cell_stats(sub)["roi"]
        neg = sum(1 for v, _ in rois if v <= 0)
        print(f"  {lk}: full-sample ROI {100*base:+.1f}%; dropping ONE day gives a range of "
              f"[{100*rois[0][0]:+.1f}%, {100*rois[-1][0]:+.1f}%]")
        print(f"        {neg} of {len(days)} single-day deletions push ROI to <= 0 "
              f"(worst: drop {rois[0][1]})")

    print("\n" + "=" * 98)
    print("  D.  3-SPLIT DIRECTION TEST on the lambda half (2024 / 2025 / 2026)")
    print("      NOTE: the live LR was retrained on 2024+2025+2026YTD, so 2024 and 2025")
    print("      are IN-SAMPLE here. Any lift shown below is therefore optimistic.")
    print("=" * 98)
    store = {}
    for name, path in (("2024", BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"),
                       ("2025", BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv")):
        if path.exists():
            store[name] = load_backtest(path, "actual_side", "home")
    store["2026"] = [r for r in rows if r["lam_mdl"] is not None]
    print(f"\n  {'season':<8}{'base%':>8}{'lam<=.60 hit%':>16}{'lift pp':>10}"
          f"{'lift 95% CI (day block)':>30}")
    for k in ("2024", "2025", "2026"):
        rr = store.get(k)
        if not rr:
            continue
        base = sum(r["y"] for r in rr) / len(rr)
        s = [r for r in rr if r["lam_mdl"] <= 0.60]
        h = sum(r["y"] for r in s) / len(s)
        lo, hi = day_boot(s, lambda r: r["y"])
        print(f"  {k:<8}{100*base:>8.1f}{100*h:>16.1f}{100*(h-base):>+10.1f}"
              f"       [{100*(lo-base):+.1f}pp, {100*(hi-base):+.1f}pp]")

    print("\n  Is the lambda ordering even stable?  hit-rate lift over that season's base:")
    print(f"  {'lam<=':>8}{'2024':>12}{'2025':>12}{'2026':>12}   same sign?")
    for c in (0.48, 0.52, 0.56, 0.60, 0.65, 0.70):
        vals = []
        line = f"  {c:>8.2f}"
        for k in ("2024", "2025", "2026"):
            rr = store.get(k) or []
            base = sum(r["y"] for r in rr) / len(rr)
            ss = [r for r in rr if r["lam_mdl"] <= c]
            v = (sum(r["y"] for r in ss) / len(ss) - base) if ss else float("nan")
            vals.append(v)
            line += f"{100*v:>+11.1f}pp"
        ok = all(v > 0 for v in vals) or all(v < 0 for v in vals)
        print(line + f"   {'yes' if ok else 'NO -- flips'}")

    print("\n" + "=" * 98)
    print("  E.  INSIDE THE CELL, IS THE MODEL ACTUALLY BEATING THE DEVIGGED BOOK?")
    print("=" * 98)
    for lk in ("lam_mdl", "lam_csv"):
        sub = [r for r in pr if r[lk] <= 0.60 and r["odds"] >= -115]
        y = np.array([r["y"] for r in sub], float)
        bk = np.array([devig(r["odds"], r["yodds"]) for r in sub])
        md = np.array([r["p"] for r in sub], float)   # calibrated model prob
        raw = np.array([math.exp(-r[lk]) for r in sub])
        def ll(p):
            p = np.clip(p, 1e-6, 1 - 1e-6)
            return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        def br(p):
            return np.mean((p - y) ** 2)
        print(f"\n  {lk}  n={len(sub)}")
        print(f"    {'':<18}{'log-loss':>11}{'Brier':>10}")
        for tag, p in (("devigged book", bk), ("model calibrated", md), ("model raw", raw)):
            print(f"    {tag:<18}{ll(p):>11.4f}{br(p):>10.4f}")
        print("    (lower is better; if the book wins here, the 'we out-predict them' premise")
        print("     does not hold in the very subset the rule wants to bet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

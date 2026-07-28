#!/usr/bin/env python3
"""
tools/nrfi_alt/alt_depth_attack.py

Second-pass attack on "raise the NRFI bar / bet only the deepest p_nrfi".
Targets the ONE cell that looked positive in pass 1 (2025 top 5-10%) and
tests whether the ladder shape is stable at all.

  1. Rank by RAW p_nrfi -- calibration-invariant. Depth selection is a pure
     rank operation, so a monotone calibrator must not change the answer.
     Any difference between raw-ranked and CIR-ranked ladders is a PLATEAU
     TIE artefact, not signal.
  2. Day-block bootstrap CI on the 2025 deep-slice HIT RATE.
  3. 2025 split-half stability of the deep slice.
  4. Pool 2025+2026 at each depth.
  5. 2026 month-by-month decomposition of the top-10% slice.
  6. Fine 1%-granularity depth sweep with day-block CIs -> how many of the
     cells searched are positive, and how many survive a CI excluding zero.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))

from alt_depth_ladder import (load_rows, payout, implied, day_bootstrap,  # noqa
                              RNG)
from calibration import ProbCalibrator  # noqa: E402


def hit_bootstrap(rows, n_boot=4000):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r["y_nrfi"])
    days = list(byday)
    arrs = [np.asarray(byday[d], float) for d in days]
    idx = RNG.integers(0, len(days), size=(n_boot, len(days)))
    out = np.empty(n_boot)
    for b in range(n_boot):
        cat = np.concatenate([arrs[i] for i in idx[b]])
        out[b] = cat.mean()
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def main():
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    rows26, _ = load_rows(ROOT / "data" / "picks_2026.csv", "2026")
    rows25, _ = load_rows(
        ROOT / "data" / "backtests" /
        "backtest_2025-04-01_to_2025-09-30_truepit.csv", "2025")
    for r in rows26 + rows25:
        r["p"] = cal.predict(r["raw"])

    DEPTHS = (0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.05, 0.02)

    print("=" * 104)
    print("  1. CALIBRATION-INVARIANCE:  rank by RAW p_nrfi instead of calibrated")
    print("=" * 104)
    priced = [r for r in rows26 if r["nrfi_odds"] is not None]
    print(f"  2026 real-priced n={len(priced)}")
    print(f"    {'depth':>7}{'n':>6}{'hit% raw-rank':>15}{'hit% CIR-rank':>15}"
          f"{'ROI% raw':>10}{'ROI% CIR':>10}{'overlap':>9}")
    for d in DEPTHS:
        k = max(1, int(round(d * len(priced))))
        raw_s = sorted(priced, key=lambda r: -r["raw"])[:k]
        cir_s = sorted(priced, key=lambda r: -r["p"])[:k]
        def stat(sub):
            u = sum(payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0 for r in sub)
            return np.mean([r["y_nrfi"] for r in sub]), u / len(sub)
        hr, rr = stat(raw_s)
        hc, rc_ = stat(cir_s)
        ov = len({id(x) for x in raw_s} & {id(x) for x in cir_s}) / k
        print(f"    {d:>7.0%}{k:>6}{100*hr:>15.2f}{100*hc:>15.2f}"
              f"{100*rr:>+10.2f}{100*rc_:>+10.2f}{100*ov:>8.0f}%")

    print("\n" + "=" * 104)
    print("  2. THE ONLY POSITIVE-LOOKING CELL: 2025 deep slices")
    print("     (reminder: LR weights trained on 2024+2025+2026YTD, calibrator on")
    print("      2025+2026 -- so 2025 is IN-SAMPLE for both. Not a holdout.)")
    print("=" * 104)
    s25 = sorted(rows25, key=lambda r: -r["p"])
    print(f"    {'depth':>7}{'n':>6}{'NRFI hit%':>11}   95% day-block CI on hit%"
          f"      vs 2026 same-depth break-even")
    # 2026 break-evens at each depth, real prices
    p26 = sorted([r for r in rows26 if r["nrfi_odds"] is not None],
                 key=lambda r: -r["p"])
    be = {}
    for d in DEPTHS:
        k = max(1, int(round(d * len(p26))))
        be[d] = np.mean([implied(r["nrfi_odds"]) for r in p26[:k]])
    for d in DEPTHS:
        k = max(1, int(round(d * len(s25))))
        sub = s25[:k]
        hit = np.mean([r["y_nrfi"] for r in sub])
        lo, hi = hit_bootstrap(sub)
        mark = "CLEARS" if lo > be[d] else ("overlaps" if hi > be[d] else "MISSES")
        print(f"    {d:>7.0%}{k:>6}{100*hit:>11.2f}   [{100*lo:.2f}%, {100*hi:.2f}%]"
              f"        {100*be[d]:.2f}%  -> CI {mark}")

    print("\n" + "=" * 104)
    print("  3. 2025 SPLIT-HALF STABILITY of the deep slice (does it repeat?)")
    print("=" * 104)
    d25 = sorted({r["date"] for r in rows25})
    mid = d25[len(d25) // 2]
    h1 = [r for r in rows25 if r["date"] < mid]
    h2 = [r for r in rows25 if r["date"] >= mid]
    print(f"  cut {mid}: H1 n={len(h1)}  H2 n={len(h2)}")
    for d in (0.30, 0.20, 0.15, 0.10, 0.05):
        out = []
        for tag, hh in (("H1", h1), ("H2", h2)):
            ss = sorted(hh, key=lambda r: -r["p"])
            k = max(1, int(round(d * len(ss))))
            out.append(f"{tag} n={k:>4} hit={100*np.mean([r['y_nrfi'] for r in ss[:k]]):>6.2f}%")
        print(f"    depth {d:>5.0%}   " + "   ".join(out))

    print("\n" + "=" * 104)
    print("  4. POOLED 2025 + 2026 at each depth (hit rate only; 2025 has no odds)")
    print("=" * 104)
    print(f"    {'depth':>7}{'2025 hit%':>11}{'2026 hit%':>11}{'pooled hit%':>13}"
          f"{'2026 break-even%':>18}")
    all26 = sorted(rows26, key=lambda r: -r["p"])
    for d in DEPTHS:
        k5 = max(1, int(round(d * len(s25))))
        k6 = max(1, int(round(d * len(all26))))
        a5 = [r["y_nrfi"] for r in s25[:k5]]
        a6 = [r["y_nrfi"] for r in all26[:k6]]
        pool = np.mean(a5 + a6)
        print(f"    {d:>7.0%}{100*np.mean(a5):>11.2f}{100*np.mean(a6):>11.2f}"
              f"{100*pool:>13.2f}{100*be[d]:>18.2f}")

    print("\n" + "=" * 104)
    print("  5. 2026 TOP-10% SLICE, MONTH BY MONTH (real prices)")
    print("=" * 104)
    k = max(1, int(round(0.10 * len(p26))))
    top = p26[:k]
    bym = defaultdict(list)
    for r in top:
        bym[r["date"][:7]].append(r)
    print(f"    {'month':>9}{'n':>5}{'hit%':>8}{'need%':>8}{'ROI%':>9}{'units':>9}")
    for m in sorted(bym):
        sub = bym[m]
        u = sum(payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0 for r in sub)
        print(f"    {m:>9}{len(sub):>5}"
              f"{100*np.mean([r['y_nrfi'] for r in sub]):>8.2f}"
              f"{100*np.mean([implied(r['nrfi_odds']) for r in sub]):>8.2f}"
              f"{100*u/len(sub):>+9.2f}{u:>+9.2f}")

    print("\n" + "=" * 104)
    print("  6. FINE DEPTH SWEEP -- 1% granularity, 2026 real prices, day-block CI")
    print("     (this is the SEARCH EXPOSURE accounting)")
    print("=" * 104)
    cells = 0
    positive = []
    sig = []
    for pct in range(1, 81):
        d = pct / 100.0
        cells += 1
        k = max(1, int(round(d * len(p26))))
        sub = p26[:k]
        recs = [{"date": r["date"],
                 "pnl": payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0}
                for r in sub]
        roi = np.mean([x["pnl"] for x in recs])
        lo, hi = day_bootstrap(recs, lambda x: x["pnl"], n_boot=1500)
        if roi > 0:
            positive.append((pct, k, roi, lo, hi))
        if lo > 0:
            sig.append((pct, k, roi, lo, hi))
    print(f"  cells searched                       : {cells}  (depths 1%..80%)")
    print(f"  cells with POSITIVE point-estimate ROI: {len(positive)}")
    print(f"  cells whose 95% day-block CI EXCLUDES 0 on the positive side: {len(sig)}")
    if positive:
        for pct, k, roi, lo, hi in positive:
            print(f"      depth {pct:>2}%  n={k:>4}  ROI {100*roi:+.2f}%  "
                  f"CI [{100*lo:+.1f}%, {100*hi:+.1f}%]")
    print(f"\n  For reference, the SAME sweep on the YRFI side:")
    p26y = sorted([r for r in rows26 if r["yrfi_odds"] is not None],
                  key=lambda r: r["p"])
    ysig = 0
    ypos = 0
    for pct in range(1, 81):
        k = max(1, int(round(pct / 100.0 * len(p26y))))
        sub = p26y[:k]
        recs = [{"date": r["date"],
                 "pnl": payout(r["yrfi_odds"]) if not r["y_nrfi"] else -1.0}
                for r in sub]
        roi = np.mean([x["pnl"] for x in recs])
        lo, _ = day_bootstrap(recs, lambda x: x["pnl"], n_boot=1500)
        ypos += roi > 0
        ysig += lo > 0
    print(f"  YRFI: {ypos}/80 cells positive, {ysig}/80 with CI excluding 0.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())

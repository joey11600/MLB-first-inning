#!/usr/bin/env python3
"""
tools/nrfi_dd_power3.py -- the 3-split the NRFI gate has never had.

The deployed CIR calibrator (data/calibration_v2.json) was fit on
2025 + 2026.  Its TOP knot is centre 0.630 -> rate 0.659; every game a
0.60+ NRFI gate fires on lives in that one bin.  Scoring 2025 with it is
therefore scoring the training set.  This runs the project's mandated
3-split on the CALIBRATOR, which is the object the gate actually sits on:

    fit 2024        -> score 2025
    fit 2025        -> score 2024
    fit 2024+2025   -> score 2026

plus a RAW-probability gate sweep (no calibrator at all) so we can see
whether the high-p_nrfi tail is a property of the model or of the
calibrator's top bin.

No odds exist for 2024/2025, so every number here is a HIT RATE, not a
profit.  Break-even reference = 58.5%, the mean vig-inclusive implied
NRFI price actually captured from DraftKings in 2026.

Analysis only.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import CIRCalibrator  # noqa: E402
from tools.nrfi_dd_power2 import load_bt, BT, wilson  # noqa: E402

GATES = (0.55, 0.58, 0.60, 0.62, 0.65)
BREAKEVEN = 0.585


def two_prop_z(k1, n1, k2, n2):
    if not n1 or not n2:
        return float("nan"), float("nan")
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    pv = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, pv


def band(rows, gate, probs):
    sel = [(x, p) for x, p in zip(rows, probs)
           if p >= gate
           and not (x["lambda"] is not None and x["lambda"] > P._LR_LAMBDA_NRFI_CEILING)]
    n = len(sel)
    k = sum(x["y"] for x, _ in sel)
    return k, n


def line(tag, k, n):
    if n == 0:
        return f"    {tag:<28}{0:>6}"
    lo, hi = wilson(k, n)
    v = 100 * (k / n - BREAKEVEN)
    mark = ""
    if lo > BREAKEVEN:
        mark = "  CI ABOVE break-even"
    elif hi < BREAKEVEN:
        mark = "  CI BELOW break-even"
    return (f"    {tag:<28}{n:>6}{100*k/n:>9.1f}%  [{100*lo:>5.1f},{100*hi:>5.1f}]"
            f"{v:>+8.1f}pp{mark}")


def main():
    print("=" * 100)
    print("  C. 3-SPLIT ON THE CALIBRATOR -- does a high-p_nrfi gate survive")
    print("     being scored on a season the calibrator has never seen?")
    print(f"     break-even reference = {100*BREAKEVEN:.1f}% (real 2026 DK NRFI prices)")
    print("=" * 100)

    data = {}
    for lbl, path in BT:
        if path.exists():
            data[lbl] = load_bt(path)
    d24, d25 = data.get("2024", []), data.get("2025", [])
    d26 = (data.get("2026a", []) or []) + (data.get("2026b", []) or [])
    print(f"\n  loaded: 2024 n={len(d24)}  2025 n={len(d25)}  2026(backtest) n={len(d26)}")

    def fitcal(rows, tag):
        return CIRCalibrator.fit([x["raw"] for x in rows], [x["y"] for x in rows], 20, [tag])

    splits = []
    if d24 and d25:
        splits.append(("fit 2024 -> score 2025", fitcal(d24, "24"), d25))
        splits.append(("fit 2025 -> score 2024", fitcal(d25, "25"), d24))
    if d24 and d25 and d26:
        splits.append(("fit 2024+2025 -> score 2026", fitcal(d24 + d25, "2425"), d26))

    print(f"\n  {'split':<30}{'gate':>6}{'n':>6}{'NRFI hit%':>10}{'Wilson 90%':>17}"
          f"{'vs need':>11}")
    store = {}
    for tag, cal, test in splits:
        print()
        for g in GATES:
            pr = [cal.predict(x["raw"]) for x in test]
            k, n = band(test, g, pr)
            store[(tag, g)] = (k, n)
            print(f"    {tag:<30}{g:>6.2f}" + line("", k, n).lstrip()[6:] if False
                  else f"    {tag:<30}{g:>6.2f}" +
                  (f"{n:>6}{100*k/n:>9.1f}%  [{100*wilson(k,n)[0]:>5.1f},"
                   f"{100*wilson(k,n)[1]:>5.1f}]{100*(k/n-BREAKEVEN):>+9.1f}pp"
                   + ("  BELOW" if wilson(k, n)[1] < BREAKEVEN else
                      ("  ABOVE" if wilson(k, n)[0] > BREAKEVEN else ""))
                   if n else f"{0:>6}"))

    # direction agreement
    print("\n  --- direction test (project rule: reject anything that only works one way) ---")
    for g in GATES:
        a = store.get(("fit 2024 -> score 2025", g))
        b = store.get(("fit 2025 -> score 2024", g))
        if not a or not b or not a[1] or not b[1]:
            continue
        ra, rb = a[0] / a[1], b[0] / b[1]
        z, pv = two_prop_z(a[0], a[1], b[0], b[1])
        agree = "AGREE" if (ra > BREAKEVEN) == (rb > BREAKEVEN) else "DISAGREE"
        print(f"    gate {g:.2f}: on2025 {100*ra:>5.1f}% (n={a[1]})  vs  on2024 "
              f"{100*rb:>5.1f}% (n={b[1]})   gap {100*abs(ra-rb):>5.1f}pp  "
              f"z={z:>+6.2f} p={pv:.2e}  {agree}")

    # ------- RAW (uncalibrated) gate: is the tail a model or calibrator fact?
    print("\n" + "=" * 100)
    print("  D. SAME SWEEP ON THE RAW (UNCALIBRATED) MODEL PROBABILITY")
    print("     If the tail is real, the raw top decile should beat break-even in")
    print("     BOTH seasons.  Raw has no fitted-on-outcomes component at all.")
    print("=" * 100)
    for lbl, rows in (("2024", d24), ("2025", d25), ("2026bt", d26)):
        if not rows:
            continue
        raws = sorted(x["raw"] for x in rows)
        print(f"\n  --- {lbl} (n={len(rows)}, raw mean {np.mean(raws):.4f}) ---")
        print(f"    {'raw top':<12}{'cutoff':>8}{'n':>7}{'NRFI hit%':>11}{'Wilson 90%':>17}")
        for frac in (0.20, 0.10, 0.05, 0.02, 0.01):
            cut = np.quantile(raws, 1 - frac)
            sel = [x for x in rows if x["raw"] >= cut
                   and not (x["lambda"] is not None
                            and x["lambda"] > P._LR_LAMBDA_NRFI_CEILING)]
            n = len(sel)
            if not n:
                continue
            k = sum(x["y"] for x in sel)
            lo, hi = wilson(k, n)
            print(f"    top {100*frac:>4.0f}%{'':<3}{cut:>8.4f}{n:>7}{100*k/n:>10.1f}%"
                  f"  [{100*lo:>5.1f},{100*hi:>5.1f}]")

    # ------- how big does the effect have to be, and can 2024+2025 see it?
    print("\n" + "=" * 100)
    print("  E. WHAT THE BIG BACKTESTS COULD RESOLVE (if we had prices for them)")
    print("=" * 100)
    for g in GATES:
        n24 = band(d24, g, [CIRCalibrator.fit([x["raw"] for x in d25],
                                              [x["y"] for x in d25], 20, ["25"]).predict(x["raw"])
                            for x in d24])[1] if d24 and d25 else 0
        print(f"    gate {g:.2f}: 2024 out-of-sample n={n24}  -> Wilson half-width at "
              f"p=0.55 is {100*1.645*math.sqrt(0.55*0.45/max(1,n24)):.1f}pp")


if __name__ == "__main__":
    main()

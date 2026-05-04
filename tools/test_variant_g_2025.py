#!/usr/bin/env python3
"""
tools/test_variant_g_2025.py — out-of-sample test of Variant G on 2025.

CONTEXT
-------
Variant G ("skip STRONG YRFI in calibrated 0.37-0.40 P(NRFI) band") looked
profitable in the 2026-04-04..2026-05-03 backfill (+6.27u vs production
over 30 days, 156 bets at 66.0%).  But that band was IDENTIFIED on the same
30 days we tested it on -- pure selection bias.  This script does the
honest test: train all parts of the pipeline on data the model has never
seen 2025 results for, then check whether the 0.37-0.40 "losing valley"
reproduces on 2025.

PIPELINE (every step trained on 2024 only, no 2025 leakage)
-----------------------------------------------------------
1. Load 2024 leakfree backtest CSV (xera/whiff replaced with prior-year
   values; see tools/backfill_xera_whiff_pit.py from this morning's audit)
2. Train two-stage LR (T1 + B1 halves, phase_e3 16-feature variant) on 2024
3. Predict 2024 in-sample to get raw P(NRFI) values
4. Fit ProbCalibrator on (2024 raw predictions, 2024 actual NRFI outcomes)
5. Load 2025 leakfree backtest CSV
6. Predict 2025 with the 2024-trained LR -> raw P(NRFI)
7. Apply the 2024-trained calibrator -> calibrated P(NRFI)
8. Identify "STRONG bets" using production thresholds:
     STRONG NRFI: calibrated P(NRFI) >= 0.58
     STRONG YRFI: calibrated P(NRFI) <= 0.42
9. Tabulate hit rates by calibrated band -- does 0.37-0.40 lose on 2025?
10. Compute production P/L and Variant G P/L on 2025 -- does the variant
    beat production on a season the model has never seen?

VIG ASSUMPTION: same as walk_forward.py (-120 vig, 0.8333u win, -1.0u loss).

VERDICT
-------
- Variant G validates if 0.37-0.40 band is bottom-2 of the 5 buckets AND
  Variant G P/L > production P/L on 2025.
- Variant G FAILS if 0.37-0.40 wins or breaks even -- the 30d "valley" was
  selection bias on a single sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg
from calibration import ProbCalibrator
from two_stage_model import (
    gather, load_fi_park,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
)


STRONG_NRFI_THR = 0.58
STRONG_YRFI_THR = 0.42

# Variant G band: skip STRONG YRFI bets where calibrated P(NRFI) is in this range.
VARIANT_G_LO = 0.37
VARIANT_G_HI = 0.40

# -120 vig assumption.  Win pays 100/120 = 0.8333u; loss costs 1.0u.
WIN_PAYOUT = 100.0 / 120.0


def grade_strong_bets(p_nrfi: np.ndarray, y_nrfi: np.ndarray):
    """Return per-bet outcomes under production thresholds.

    Yields tuples (calibrated_p, side, won_bool) for each bet that would
    have fired."""
    out = []
    for p, y in zip(p_nrfi, y_nrfi):
        if p >= STRONG_NRFI_THR:
            out.append((float(p), "NRFI", y == 1))
        elif p <= STRONG_YRFI_THR:
            out.append((float(p), "YRFI", y == 0))
    return out


def pl(bets, skip_predicate):
    n_bets = n_wins = 0
    n_yrfi = n_yrfi_wins = 0
    n_nrfi = n_nrfi_wins = 0
    pl_total = 0.0
    skipped = []
    for p, side, won in bets:
        if skip_predicate(p, side):
            skipped.append((p, side, won))
            continue
        n_bets += 1
        if side == "YRFI":
            n_yrfi += 1
            if won: n_yrfi_wins += 1
        else:
            n_nrfi += 1
            if won: n_nrfi_wins += 1
        if won:
            n_wins += 1
            pl_total += WIN_PAYOUT
        else:
            pl_total -= 1.0
    return {
        "n_bets":  n_bets, "n_wins": n_wins, "n_losses": n_bets - n_wins,
        "n_yrfi":  n_yrfi, "n_yrfi_wins": n_yrfi_wins,
        "n_nrfi":  n_nrfi, "n_nrfi_wins": n_nrfi_wins,
        "pl":      pl_total,
        "hit":     (n_wins / n_bets) if n_bets else 0.0,
        "roi":     (pl_total / n_bets) if n_bets else 0.0,
        "skipped_n": len(skipped),
        "skipped_w": sum(1 for _, _, w in skipped if w),
        "skipped_pl_avoided": sum(WIN_PAYOUT if w else -1.0 for _, _, w in skipped),
    }


def bucket_yrfi(bets):
    """Group STRONG YRFI bets into [0.30, 0.34), [0.34, 0.36), [0.36, 0.37),
    [0.37, 0.38), [0.38, 0.40), [0.40, 0.42), [0.42, 0.43)."""
    bands = [(0.30, 0.34), (0.34, 0.36), (0.36, 0.37), (0.37, 0.38),
             (0.38, 0.40), (0.40, 0.42), (0.42, 0.43)]
    out = {b: [] for b in bands}
    for p, side, won in bets:
        if side != "YRFI":
            continue
        for lo, hi in bands:
            if lo <= p < hi:
                out[(lo, hi)].append((p, won))
                break
    return out


def bucket_nrfi(bets):
    """Group STRONG NRFI bets into [0.58, 0.60), [0.60, 0.62), [0.62, 0.64),
    [0.64, 0.66), [0.66, 1.00)."""
    bands = [(0.58, 0.60), (0.60, 0.62), (0.62, 0.64), (0.64, 0.66), (0.66, 1.00)]
    out = {b: [] for b in bands}
    for p, side, won in bets:
        if side != "NRFI":
            continue
        for lo, hi in bands:
            if lo <= p < hi:
                out[(lo, hi)].append((p, won))
                break
    return out


def run_test(train_csv: Path, test_csv: Path, label: str):
    """Train LR + calibrator on train_csv, evaluate Variant G on test_csv."""
    fi_park = load_fi_park()
    print()
    print("=" * 100)
    print(f"  {label}")
    print(f"    train: {train_csv.name}")
    print(f"    test : {test_csv.name}")
    print("=" * 100)
    train = gather(train_csv, fi_park, phase_e3=True)
    if train is None:
        print(f"  gather() returned None for train CSV"); return
    print(f"  Train: {train['n']} rows")
    m_t1 = LogReg.fit(train["X_t1"], train["y_t1"], T1_PHASE_E3_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(train["X_b1"], train["y_b1"], B1_PHASE_E3_FEATURES, l2=0.05)
    p_t1_train = m_t1.predict_proba(train["X_t1"])
    p_b1_train = m_b1.predict_proba(train["X_b1"])
    p_nrfi_train_raw = (1.0 - p_t1_train) * (1.0 - p_b1_train)
    cal = ProbCalibrator.fit(
        predictions=p_nrfi_train_raw.tolist(),
        actuals=train["y_nrfi"].tolist(),
        n_bins=20,
    )
    print(f"  Calibrator: rate range [{min(cal.rates):.4f}, {max(cal.rates):.4f}]")
    test = gather(test_csv, fi_park, phase_e3=True)
    if test is None:
        print(f"  gather() returned None for test CSV"); return
    print(f"  Test:  {test['n']} rows")
    p_t1_test = m_t1.predict_proba(test["X_t1"])
    p_b1_test = m_b1.predict_proba(test["X_b1"])
    p_nrfi_test_raw = (1.0 - p_t1_test) * (1.0 - p_b1_test)
    p_nrfi_test_cal = np.array([cal.predict(float(p)) for p in p_nrfi_test_raw])
    bets = grade_strong_bets(p_nrfi_test_cal, test["y_nrfi"])
    n_yrfi = sum(1 for _, side, _ in bets if side == "YRFI")
    n_nrfi = sum(1 for _, side, _ in bets if side == "NRFI")
    print(f"  STRONG bets: {len(bets)} ({n_nrfi} NRFI, {n_yrfi} YRFI)")

    # Bucket-by-band
    print()
    print(f"  STRONG YRFI hit rate by calibrated P(NRFI) band:")
    print(f"    {'band':<14} {'n':>4} {'W-L':>10} {'hit':>7} {'P/L':>10}")
    yrfi_buckets = bucket_yrfi(bets)
    for (lo, hi), bucket in yrfi_buckets.items():
        if not bucket:
            print(f"    [{lo:.2f}-{hi:.2f}) {0:>4}  (no bets)")
            continue
        n = len(bucket)
        w = sum(1 for _, won in bucket if won)
        l = n - w
        pl_band = sum(WIN_PAYOUT if won else -1.0 for _, won in bucket)
        hit = w / n * 100
        flag = "  <<< Variant G" if (lo, hi) in [(0.37, 0.38), (0.38, 0.40)] else ""
        print(f"    [{lo:.2f}-{hi:.2f}) {n:>4} {w}-{l:<6} {hit:>5.1f}% {pl_band:>+8.2f}u{flag}")

    prod = pl(bets, lambda p, side: False)
    var_g = pl(bets, lambda p, side: side == "YRFI" and VARIANT_G_LO <= p < VARIANT_G_HI)

    print()
    print(f"  PRODUCTION:  bets={prod['n_bets']:>4}  W-L={prod['n_wins']}-{prod['n_losses']:<3}  "
          f"hit={prod['hit']*100:>4.1f}%  P/L={prod['pl']:>+7.2f}u  ROI={prod['roi']*100:>+5.1f}%")
    print(f"  VARIANT G :  bets={var_g['n_bets']:>4}  W-L={var_g['n_wins']}-{var_g['n_losses']:<3}  "
          f"hit={var_g['hit']*100:>4.1f}%  P/L={var_g['pl']:>+7.2f}u  ROI={var_g['roi']*100:>+5.1f}%")
    delta = var_g["pl"] - prod["pl"]
    print(f"  -> Variant G {'wins' if delta > 0 else 'loses'} by {abs(delta):.2f}u  "
          f"({var_g['skipped_n']} bets skipped)")
    return delta, prod, var_g


def main():
    BTS = REPO_ROOT / "data" / "backtests"

    # Test 1: train on 2024 LEAK-FREE (prior-year proxy), test on 2025 LEAK-FREE
    run_test(
        BTS / "backtest_2024-04-01_to_2024-09-30_leakfree.csv",
        BTS / "backtest_2025-04-01_to_2025-09-30_leakfree.csv",
        "Test 1: leak-free (prior-year proxy) 2024 -> leak-free 2025",
    )

    # Test 2: train on ORIGINAL 2024, test on ORIGINAL 2025 (matches production
    # methodology -- same xera leak in both train and test, so calibrator
    # behavior reproduces the production calibrator's range)
    run_test(
        BTS / "backtest_2024-04-01_to_2024-09-30.csv",
        BTS / "backtest_2025-04-01_to_2025-09-30.csv",
        "Test 2: original (leaky) 2024 -> original (leaky) 2025  (matches prod methodology)",
    )

    # Test 3: train on TRUE point-in-time 2024, test on TRUE point-in-time 2025
    # (strict walk-forward; xera/whiff are cumulative-through-yesterday from
    # per-pitch Statcast data, not season-aggregate or prior-year proxy)
    truepit_2024 = BTS / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
    truepit_2025 = BTS / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
    if truepit_2024.exists() and truepit_2025.exists():
        run_test(
            truepit_2024, truepit_2025,
            "Test 3: TRUE point-in-time 2024 -> 2025  (strict walk-forward)",
        )
    else:
        print()
        print("=" * 100)
        print("  Test 3: TRUE point-in-time CSVs not yet built.")
        print(f"    Run tools/backfill_xera_pit_perpitch.py first to generate them.")
        print("=" * 100)
    return  # bypass the rest of the original main()
    fi_park = load_fi_park()
    BTS = REPO_ROOT / "data" / "backtests"
    train_csv = BTS / "backtest_2024-04-01_to_2024-09-30_leakfree.csv"
    test_csv  = BTS / "backtest_2025-04-01_to_2025-09-30_leakfree.csv"
    if not train_csv.exists() or not test_csv.exists():
        sys.exit(f"Missing leakfree CSV(s): {train_csv} or {test_csv}.  "
                 f"Run tools/backfill_xera_whiff_pit.py first.")

    print()
    print("=" * 100)
    print("  Out-of-sample test: Variant G on 2025 (model trained on 2024 only)")
    print("=" * 100)

    # Step 1-2: train phase_e3 LR on 2024 leakfree
    train = gather(train_csv, fi_park, phase_e3=True)
    if train is None:
        sys.exit("gather() returned None for 2024 train CSV")
    print(f"  Train (2024): {train['n']} rows")
    m_t1 = LogReg.fit(train["X_t1"], train["y_t1"], T1_PHASE_E3_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(train["X_b1"], train["y_b1"], B1_PHASE_E3_FEATURES, l2=0.05)

    # Step 3-4: in-sample predictions on 2024 -> fit calibrator
    p_t1_train = m_t1.predict_proba(train["X_t1"])
    p_b1_train = m_b1.predict_proba(train["X_b1"])
    p_nrfi_train_raw = (1.0 - p_t1_train) * (1.0 - p_b1_train)
    cal = ProbCalibrator.fit(
        predictions=p_nrfi_train_raw.tolist(),
        actuals=train["y_nrfi"].tolist(),
        n_bins=20,
        train_seasons=["2024"],
    )
    print(f"  Calibrator fit on 2024: {len(cal.centers)} bins, "
          f"rate range [{min(cal.rates):.4f}, {max(cal.rates):.4f}]")

    # Step 5-7: predict 2025 with 2024 LR + apply 2024 calibrator
    test = gather(test_csv, fi_park, phase_e3=True)
    if test is None:
        sys.exit("gather() returned None for 2025 test CSV")
    print(f"  Test (2025):  {test['n']} rows")
    p_t1_test = m_t1.predict_proba(test["X_t1"])
    p_b1_test = m_b1.predict_proba(test["X_b1"])
    p_nrfi_test_raw = (1.0 - p_t1_test) * (1.0 - p_b1_test)
    p_nrfi_test_cal = np.array([cal.predict(float(p)) for p in p_nrfi_test_raw])
    y_nrfi_test = test["y_nrfi"]

    # Step 8: identify STRONG bets
    bets = grade_strong_bets(p_nrfi_test_cal, y_nrfi_test)
    n_yrfi = sum(1 for _, side, _ in bets if side == "YRFI")
    n_nrfi = sum(1 for _, side, _ in bets if side == "NRFI")
    print(f"\n  STRONG bets identified on 2025: {len(bets)} total ({n_nrfi} NRFI, {n_yrfi} YRFI)")

    # Step 9: bucket-by-band hit rate within STRONG YRFI
    print()
    print("=" * 100)
    print("  STRONG YRFI hit rate by calibrated P(NRFI) band on 2025 holdout")
    print("=" * 100)
    print(f"  {'band':<14} {'n':>4} {'W-L':>10} {'hit':>7} {'P/L':>10}")
    print("  " + "-" * 50)
    yrfi_buckets = bucket_yrfi(bets)
    for (lo, hi), bucket in yrfi_buckets.items():
        if not bucket:
            print(f"  [{lo:.2f}-{hi:.2f}) {0:>4}  (no bets)")
            continue
        n = len(bucket)
        w = sum(1 for _, won in bucket if won)
        l = n - w
        pl_band = sum(WIN_PAYOUT if won else -1.0 for _, won in bucket)
        hit = w / n * 100
        flag = "  <<< Variant G skips this band" if (lo, hi) in [(0.37, 0.38), (0.38, 0.40)] else ""
        print(f"  [{lo:.2f}-{hi:.2f}) {n:>4} {w}-{l:<6} {hit:>5.1f}% {pl_band:>+8.2f}u{flag}")

    # Step 10: compare Production P/L vs Variant G P/L on 2025
    print()
    print("=" * 100)
    print("  Production-policy vs Variant G-policy on 2025 holdout")
    print("=" * 100)

    prod = pl(bets, lambda p, side: False)
    var_g = pl(bets, lambda p, side: side == "YRFI" and VARIANT_G_LO <= p < VARIANT_G_HI)

    def show(label, x):
        print(f"  {label:<14}  bets={x['n_bets']:>4}  W-L={x['n_wins']}-{x['n_losses']:<3}  "
              f"hit={x['hit']*100:>4.1f}%  P/L={x['pl']:>+7.2f}u  ROI={x['roi']*100:>+5.1f}%   "
              f"(NRFI {x['n_nrfi_wins']}-{x['n_nrfi']-x['n_nrfi_wins']}, "
              f"YRFI {x['n_yrfi_wins']}-{x['n_yrfi']-x['n_yrfi_wins']})")
    show("PRODUCTION", prod)
    show("VARIANT G",  var_g)

    delta = var_g["pl"] - prod["pl"]
    print()
    print(f"  Delta: Variant G {'WINS' if delta > 0 else 'LOSES'} by {abs(delta):.2f}u "
          f"on the 2025 holdout ({var_g['skipped_n']} bets skipped, "
          f"avoiding {var_g['skipped_pl_avoided']:+.2f}u of net P/L).")

    # Verdict
    print()
    print("-" * 100)
    print("  Verdict")
    print("-" * 100)
    band_037_040 = yrfi_buckets[(0.37, 0.38)] + yrfi_buckets[(0.38, 0.40)]
    if band_037_040:
        n = len(band_037_040); w = sum(1 for _, won in band_037_040 if won)
        band_pl = sum(WIN_PAYOUT if won else -1.0 for _, won in band_037_040)
        print(f"  0.37-0.40 band on 2025: {n} bets, {w}-{n-w} = {100*w/n:.1f}% hit, P/L={band_pl:+.2f}u")
        if delta > 1.0:
            print(f"  PASS -- Variant G beats production by {delta:+.2f}u on out-of-sample 2025.")
            print(f"  The losing valley reproduces.  Worth shipping (subject to seasonal-shift caveats).")
        elif delta > 0:
            print(f"  MARGINAL -- Variant G slightly beats production on 2025, but the lift is")
            print(f"  smaller than the 30d in-sample (+6.27u).  Some real signal + some sample noise.")
        else:
            print(f"  FAIL -- Variant G regresses on the 2025 holdout.  The 30d in-sample +6.27u")
            print(f"  was selection bias.  Do NOT ship the threshold change to production.")
    else:
        print("  0.37-0.40 band has zero bets on 2025 -- Variant G has no effect (no test).")
    print()


if __name__ == "__main__":
    main()

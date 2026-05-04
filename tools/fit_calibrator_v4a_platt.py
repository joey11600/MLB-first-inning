#!/usr/bin/env python3
"""
tools/fit_calibrator_v4a_platt.py -- T4-V4 candidate A: Platt calibrator.

Fits a two-parameter sigmoid (Platt) calibrator on the SAME 2024+2025
truepit corpus that v3's isotonic calibrator was trained on.  Saves to
data/calibration_v4a_platt.json (separate file, parallel to v2 / v3).

THEORETICAL MOTIVATION (pre-registered)
----------------------------------------
v3's isotonic calibrator is non-parametric -- it fits 20 quantile bins
and pool-adjacent-violators-smooths them into a monotone curve.  This
is flexible but can wiggle: a single noisy bin can pull adjacent bins
toward it, and the calibrator's range gets compressed [0.3833, 0.6116]
because extreme bins lose mass to the smoother.

A Platt sigmoid has only 2 parameters (a + b * p inside a sigmoid),
which:
  * forces a globally smooth monotone curve (no local wiggles)
  * is defined on the entire (0, 1) interval (no flat extrapolation
    at the tails of training data)
  * is more robust to small-N because there's no per-bin overfitting risk

Hypothesis: Platt may produce a CALIBRATED probability range closer to
v2's [0.3623, 0.6620] (less compression than v3's [0.3833, 0.6116])
while still being trained on leak-free truepit data.  If so, it should
fire more STRONG bets than v3 while remaining honest about ground truth.

This script trains, computes Brier on training data, and writes the
calibrator JSON.  No production swap, no auto-deploy.

USAGE
-----
  python tools/fit_calibrator_v4a_platt.py
  python tools/fit_calibrator_v4a_platt.py --out data/calibration_v4a_platt.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg
from calibration import ProbCalibrator
from two_stage_model import (
    gather, load_fi_park, brier,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
)
from tools.v4_calibrators import PlattCalibrator


def load_truepit_blocks() -> tuple[dict, dict]:
    """Returns (2024_block, 2025_block) gathered from truepit CSVs."""
    fi_park = load_fi_park()
    BTS = REPO_ROOT / "data" / "backtests"
    paths = {
        2024: BTS / "backtest_2024-04-01_to_2024-09-30_truepit.csv",
        2025: BTS / "backtest_2025-04-01_to_2025-09-30_truepit.csv",
    }
    for s, p in paths.items():
        if not p.exists():
            sys.exit(f"Missing {p}.  Run tools/backfill_xera_pit_perpitch.py first.")
    blocks = {}
    for s, p in paths.items():
        b = gather(p, fi_park, phase_e3=True)
        if b is None:
            sys.exit(f"gather() returned None for {p}")
        blocks[s] = b
    return blocks[2024], blocks[2025]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", default="data/calibration_v4a_platt.json",
                    help="Output path (default: data/calibration_v4a_platt.json).")
    args = ap.parse_args()

    print("=" * 92)
    print("  Fitting Platt calibrator on 2024+2025 truepit corpus (v4 candidate A)")
    print("=" * 92)

    block_2024, block_2025 = load_truepit_blocks()
    print(f"  2024 truepit: {block_2024['n']} rows")
    print(f"  2025 truepit: {block_2025['n']} rows")

    # Train LR on combined corpus -- mirrors the v3 fitter exactly so
    # raw P(NRFI) values fed to the calibrator are identical to those
    # used for v3.
    print()
    print("  Training LR on 2024+2025 truepit (combined)...")
    X_t1 = np.vstack([block_2024["X_t1"], block_2025["X_t1"]])
    y_t1 = np.concatenate([block_2024["y_t1"], block_2025["y_t1"]])
    X_b1 = np.vstack([block_2024["X_b1"], block_2025["X_b1"]])
    y_b1 = np.concatenate([block_2024["y_b1"], block_2025["y_b1"]])
    m_t1 = LogReg.fit(X_t1, y_t1, T1_PHASE_E3_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(X_b1, y_b1, B1_PHASE_E3_FEATURES, l2=0.05)
    print(f"    T1 LR: {len(y_t1)} rows, base rate {y_t1.mean()*100:.2f}%")
    print(f"    B1 LR: {len(y_b1)} rows, base rate {y_b1.mean()*100:.2f}%")

    p_t1_all = m_t1.predict_proba(X_t1)
    p_b1_all = m_b1.predict_proba(X_b1)
    p_nrfi_raw = (1.0 - p_t1_all) * (1.0 - p_b1_all)
    y_nrfi_all = np.concatenate([block_2024["y_nrfi"], block_2025["y_nrfi"]])

    # Fit Platt
    print()
    print("  Fitting PlattCalibrator (Newton-Raphson)...")
    cal_v4a = PlattCalibrator.fit(
        predictions=p_nrfi_raw.tolist(),
        actuals=y_nrfi_all.tolist(),
        train_seasons=["2024_truepit", "2025_truepit"],
    )
    print(f"    Fitted: a={cal_v4a.a:+.4f}, b={cal_v4a.b:+.4f}")
    print(f"    cal(0.40) = {cal_v4a.predict(0.40):.4f}")
    print(f"    cal(0.50) = {cal_v4a.predict(0.50):.4f}")
    print(f"    cal(0.60) = {cal_v4a.predict(0.60):.4f}")

    # Sample range across raw inputs
    sample_inputs = [round(x, 3) for x in np.linspace(p_nrfi_raw.min(),
                                                        p_nrfi_raw.max(),
                                                        20)]
    cal_outs = [cal_v4a.predict(p) for p in sample_inputs]
    print()
    print(f"  Output range across training inputs: [{min(cal_outs):.4f}, {max(cal_outs):.4f}]")

    # Compare ranges to v2 and v3
    cal_v2 = ProbCalibrator.load(REPO_ROOT / "data" / "calibration_v2.json")
    cal_v3 = ProbCalibrator.load(REPO_ROOT / "data" / "calibration_v3.json")
    print()
    print("=" * 92)
    print("  Calibrator range comparison")
    print("=" * 92)
    print(f"    v2 (production, isotonic, 2025+2026 leaky):  "
          f"[{min(cal_v2.rates):.4f}, {max(cal_v2.rates):.4f}], n={cal_v2.train_n}")
    print(f"    v3 (isotonic, 2024+2025 truepit):            "
          f"[{min(cal_v3.rates):.4f}, {max(cal_v3.rates):.4f}], n={cal_v3.train_n}")
    print(f"    v4a (Platt, 2024+2025 truepit):              "
          f"[{min(cal_outs):.4f}, {max(cal_outs):.4f}], n={cal_v4a.train_n}")

    # Per-input comparison at the same anchor probabilities
    print()
    print("  Per-input comparison:")
    print(f"    {'raw P(NRFI)':>11}  {'v2':>8}  {'v3':>8}  {'v4a (Platt)':>12}")
    for raw in sample_inputs:
        p_v2  = cal_v2.predict(raw)
        p_v3  = cal_v3.predict(raw)
        p_v4a = cal_v4a.predict(raw)
        print(f"    {raw:>10.3f}   {p_v2:>7.4f}  {p_v3:>7.4f}  {p_v4a:>11.4f}")

    # Brier comparison
    p_v2_arr  = np.array([cal_v2.predict(float(p))  for p in p_nrfi_raw])
    p_v3_arr  = np.array([cal_v3.predict(float(p))  for p in p_nrfi_raw])
    p_v4a_arr = np.array([cal_v4a.predict(float(p)) for p in p_nrfi_raw])
    print()
    print("  In-sample Brier on combined 2024+2025 truepit:")
    print(f"    raw model      : {brier(p_nrfi_raw, y_nrfi_all):.4f}")
    print(f"    v2 calibrator  : {brier(p_v2_arr,    y_nrfi_all):.4f}")
    print(f"    v3 calibrator  : {brier(p_v3_arr,    y_nrfi_all):.4f}")
    print(f"    v4a (Platt)    : {brier(p_v4a_arr,   y_nrfi_all):.4f}")

    # Save
    out_path = REPO_ROOT / args.out
    cal_v4a.save(out_path)
    print()
    print(f"  Saved -> {out_path}")
    print()
    print("  v4a sits NEXT TO v2 / v3.  Production unchanged.  Nothing")
    print("  in the live system reads v4a yet -- only the v4 backfill +")
    print("  evaluation pipeline does.")


if __name__ == "__main__":
    main()

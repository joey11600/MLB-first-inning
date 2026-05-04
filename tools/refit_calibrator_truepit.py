#!/usr/bin/env python3
"""
tools/refit_calibrator_truepit.py — refit the production probability
calibrator on leak-free corpus.

CONTEXT (T3.12 followup #1)
---------------------------
The current production calibrator (data/calibration_v2.json) was fit on
2025+2026 raw model outputs.  Both training corpora had leaky xera/whiff
features (Statcast cache keyed by season_aggregate -- end-of-season values
applied to early-season games).  See T3.11-AUDIT.

Test 3 of tools/test_variant_g_2025.py revealed that a calibrator fit on
LEAK-FREE 2024 data has range [0.4583, 0.6357] -- much narrower than
production's [0.3623, 0.6620].  The wider production range came from
leaky training data inflating the model's confidence in its predictions.

Net effect on the live model: production fires "STRONG YRFI" bets at
calibrated P(NRFI) = 0.36-0.42, but a leak-free calibrator wouldn't even
output values that low.  Those YRFI bets are an artifact of leakage.

This script refits the calibrator on the leak-free corpus produced by
tools/backfill_xera_pit_perpitch.py, then writes data/calibration_v3.json
as the candidate replacement.  No production swap happens here -- v3
sits next to v2 and the next step is comparison + validation, not auto-
deploy.

PIPELINE
--------
1. Load 2024 + 2025 truepit CSVs (per-pitch xera + cross-pitcher whiff_pct_rank)
2. Train T1+B1 LR on 2024+2025 truepit (the LR architecture is unchanged;
   only the feature values differ from production's training corpus)
3. Predict on 2024+2025 truepit -> get raw P(NRFI) per game
4. Fit ProbCalibrator on (raw_p, actual_nrfi) pairs
5. Save data/calibration_v3.json
6. Print rate-range comparison v2 vs v3 + per-bin diff
7. Re-grade Test 2 / Test 3 of tools/test_variant_g_2025.py with the new
   calibrator (post-hoc) so we can see how the leak-free calibrator affects
   the production-style P/L

USAGE
-----
  python tools/refit_calibrator_truepit.py
  python tools/refit_calibrator_truepit.py --diff-only  # don't write file
"""

from __future__ import annotations

import argparse
import json
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


STRONG_NRFI_THR = 0.58
STRONG_YRFI_THR = 0.42
WIN_PAYOUT      = 100.0 / 120.0   # -120 vig


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


def fit_lr_pair(block: dict) -> tuple[LogReg, LogReg]:
    """Train T1 + B1 LR on a single season block."""
    m_t1 = LogReg.fit(block["X_t1"], block["y_t1"], T1_PHASE_E3_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(block["X_b1"], block["y_b1"], B1_PHASE_E3_FEATURES, l2=0.05)
    return m_t1, m_b1


def predict_nrfi_raw(m_t1: LogReg, m_b1: LogReg, block: dict) -> np.ndarray:
    p_t1 = m_t1.predict_proba(block["X_t1"])
    p_b1 = m_b1.predict_proba(block["X_b1"])
    return (1.0 - p_t1) * (1.0 - p_b1)


def grade_with_cal(p_raw: np.ndarray, y_nrfi: np.ndarray, cal: ProbCalibrator):
    """Apply calibrator + production thresholds, return (n_bets, W-L, P/L)."""
    p_cal = np.array([cal.predict(float(p)) for p in p_raw])
    n_bets = n_wins = 0
    pl_total = 0.0
    n_yrfi = n_yrfi_wins = 0
    n_nrfi = n_nrfi_wins = 0
    for p, y in zip(p_cal, y_nrfi):
        if p >= STRONG_NRFI_THR:
            n_bets += 1; n_nrfi += 1
            if y == 1:
                n_wins += 1; n_nrfi_wins += 1; pl_total += WIN_PAYOUT
            else:
                pl_total -= 1.0
        elif p <= STRONG_YRFI_THR:
            n_bets += 1; n_yrfi += 1
            if y == 0:
                n_wins += 1; n_yrfi_wins += 1; pl_total += WIN_PAYOUT
            else:
                pl_total -= 1.0
    return {
        "n_bets":  n_bets, "n_wins": n_wins, "n_losses": n_bets - n_wins,
        "n_nrfi":  n_nrfi, "n_nrfi_wins": n_nrfi_wins,
        "n_yrfi":  n_yrfi, "n_yrfi_wins": n_yrfi_wins,
        "pl":      pl_total,
        "hit":     (n_wins / n_bets) if n_bets else 0.0,
        "roi":     (pl_total / n_bets) if n_bets else 0.0,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--diff-only", action="store_true",
                    help="Compute and print v2 vs v3 diff but don't write v3.")
    ap.add_argument("--out", default="data/calibration_v3.json",
                    help="Output path (default: data/calibration_v3.json).")
    args = ap.parse_args()

    print("=" * 92)
    print("  Refitting probability calibrator on leak-free truepit corpus (T3.12 followup #1)")
    print("=" * 92)

    block_2024, block_2025 = load_truepit_blocks()
    print(f"  2024 truepit: {block_2024['n']} rows")
    print(f"  2025 truepit: {block_2025['n']} rows")

    # Train LR on combined 2024+2025 truepit (mirrors production's
    # multi-season training).  The LR weights here may differ from
    # production because the feature distribution is different (xera +
    # whiff are point-in-time, not season-aggregate-leaked).  That's the
    # point.
    print()
    print("  Training LR on 2024+2025 truepit (combined)...")
    X_t1 = np.vstack([block_2024["X_t1"], block_2025["X_t1"]])
    y_t1 = np.concatenate([block_2024["y_t1"], block_2025["y_t1"]])
    X_b1 = np.vstack([block_2024["X_b1"], block_2025["X_b1"]])
    y_b1 = np.concatenate([block_2024["y_b1"], block_2025["y_b1"]])
    m_t1 = LogReg.fit(X_t1, y_t1, T1_PHASE_E3_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(X_b1, y_b1, B1_PHASE_E3_FEATURES, l2=0.05)
    print(f"    T1 LR: {len(y_t1)} train rows, base rate {y_t1.mean()*100:.2f}%")
    print(f"    B1 LR: {len(y_b1)} train rows, base rate {y_b1.mean()*100:.2f}%")

    # Predict on the COMBINED corpus (in-sample for calibrator, since
    # the calibrator's job is to map raw -> observed on the SAME data
    # the LR was trained on).  This mirrors production's calibration
    # methodology.
    p_t1_all = m_t1.predict_proba(X_t1)
    p_b1_all = m_b1.predict_proba(X_b1)
    p_nrfi_raw = (1.0 - p_t1_all) * (1.0 - p_b1_all)
    y_nrfi_all = np.concatenate([block_2024["y_nrfi"], block_2025["y_nrfi"]])

    print()
    print("  Fitting ProbCalibrator (20 quantile bins)...")
    cal_v3 = ProbCalibrator.fit(
        predictions=p_nrfi_raw.tolist(),
        actuals=y_nrfi_all.tolist(),
        n_bins=20,
        train_seasons=["2024_truepit", "2025_truepit"],
    )

    # Compare to existing production calibrator
    cal_v2_path = REPO_ROOT / "data" / "calibration_v2.json"
    cal_v2 = ProbCalibrator(**{
        "bin_centers":   json.load(open(cal_v2_path))["centers"],
        "bin_rates":     json.load(open(cal_v2_path))["rates"],
        "train_seasons": json.load(open(cal_v2_path)).get("train_seasons"),
        "train_n":       json.load(open(cal_v2_path)).get("train_n", 0),
    })

    print()
    print("=" * 92)
    print("  Calibrator comparison: v2 (production, leaky) vs v3 (truepit, leak-free)")
    print("=" * 92)
    print(f"  v2 (current): rate range [{min(cal_v2.rates):.4f}, {max(cal_v2.rates):.4f}], "
          f"n={cal_v2.train_n}, seasons={cal_v2.train_seasons}")
    print(f"  v3 (new)    : rate range [{min(cal_v3.rates):.4f}, {max(cal_v3.rates):.4f}], "
          f"n={cal_v3.train_n}, seasons={cal_v3.train_seasons}")
    print()
    print("  Per-bin comparison (sample inputs aligned to v3 centers):")
    print(f"    {'raw P(NRFI)':>12}  {'v2 calibrated':>13}  {'v3 calibrated':>13}  {'delta':>8}")
    sample_inputs = [round(c, 3) for c in cal_v3.centers]
    for raw in sample_inputs:
        p_v2 = cal_v2.predict(raw)
        p_v3 = cal_v3.predict(raw)
        d    = p_v3 - p_v2
        print(f"    {raw:>11.3f}   {p_v2:>11.4f}    {p_v3:>11.4f}   {d:+7.4f}")

    # Brier comparison
    p_v2_arr = np.array([cal_v2.predict(float(p)) for p in p_nrfi_raw])
    p_v3_arr = np.array([cal_v3.predict(float(p)) for p in p_nrfi_raw])
    print()
    print(f"  In-sample Brier on combined 2024+2025 truepit:")
    print(f"    raw model      : {brier(p_nrfi_raw, y_nrfi_all):.4f}")
    print(f"    v2 calibrator  : {brier(p_v2_arr,    y_nrfi_all):.4f}")
    print(f"    v3 calibrator  : {brier(p_v3_arr,    y_nrfi_all):.4f}")

    # Production-policy P/L under each calibrator (sample of what
    # would-have-happened on the truepit holdout under each calibrator)
    print()
    print("=" * 92)
    print("  Production-policy P/L on 2024+2025 truepit, by calibrator:")
    print("=" * 92)
    pl_v2 = grade_with_cal(p_nrfi_raw, y_nrfi_all, cal_v2)
    pl_v3 = grade_with_cal(p_nrfi_raw, y_nrfi_all, cal_v3)
    def show(label, x):
        print(f"  {label:<24}  bets={x['n_bets']:>4}  W-L={x['n_wins']}-{x['n_losses']:<4}  "
              f"hit={x['hit']*100:>4.1f}%  P/L={x['pl']:>+7.2f}u  ROI={x['roi']*100:>+5.1f}%   "
              f"(NRFI {x['n_nrfi_wins']}-{x['n_nrfi']-x['n_nrfi_wins']}, "
              f"YRFI {x['n_yrfi_wins']}-{x['n_yrfi']-x['n_yrfi_wins']})")
    show("v2 (leaky calibrator)", pl_v2)
    show("v3 (truepit calibrator)", pl_v3)
    delta = pl_v3["pl"] - pl_v2["pl"]
    print(f"\n  Delta on truepit corpus: v3 vs v2 = {delta:+.2f}u "
          f"({pl_v3['n_bets']} vs {pl_v2['n_bets']} bets)")

    # Save v3
    if not args.diff_only:
        out_path = REPO_ROOT / args.out
        cal_v3.save(out_path)
        print(f"\n  Saved -> {out_path}")
        print()
        print("  v3 sits NEXT TO v2 -- production still uses v2 (data/calibration_v2.json).")
        print("  No threshold/calibration swap until manual review + walk-forward validation.")


if __name__ == "__main__":
    main()

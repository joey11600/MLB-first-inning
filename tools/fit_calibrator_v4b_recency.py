#!/usr/bin/env python3
"""
tools/fit_calibrator_v4b_recency.py -- T4-V4 candidate B: recency-weighted
isotonic calibrator.

Same isotonic / quantile-bin / PAV-smoothing family as v3, but applies
exponential recency decay to per-sample weights so 2025 data dominates
2024 data.  Saves to data/calibration_v4b_recency.json.

THEORETICAL MOTIVATION (pre-registered)
----------------------------------------
v3's calibrator is fit on 2024 + 2025 truepit data with each sample
weighted equally.  But run environment shifts year-over-year:
  - Ball composition changes (de-juiced vs juiced)
  - Strike-zone enforcement varies between umpire crews
  - Lineup compositions shift (more contact vs more power)
  - Pitcher usage patterns shift (more openers, more HK days)

A calibrator fit on equal-weighted 2024+2025 data treats 2024's run
environment as equally informative as 2025's about *2026* games.  If
2025's environment is closer to 2026's than 2024's was (likely), this
dilutes the signal.

Recency decay corrects this.  We use a ~1-year half-life: a 2024 sample
gets weight ~0.5x of a 2025 sample.  The calibrator produces a curve
that tracks 2025's calibration more closely while still using 2024 as
prior.

This is NOT a "let me try every decay constant and pick the best" sweep.
The 1-year half-life is fixed by theory (one full season is the
natural unit) and committed BEFORE peeking at the design data.

USAGE
-----
  python tools/fit_calibrator_v4b_recency.py
  python tools/fit_calibrator_v4b_recency.py --out data/calibration_v4b_recency.json
"""

from __future__ import annotations

import argparse
import math
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
from tools.v4_calibrators import WeightedProbCalibrator


# Half-life in seasons.  A sample from N seasons ago gets weight 0.5^N.
# Pre-registered = 1.0 (one full season).  Lock this in.
HALF_LIFE_SEASONS = 1.0

# Reference season -- "now" anchor for the decay.  Set to the season in
# which the calibrator will be deployed (2026), so 2025 -> weight 0.5,
# 2024 -> weight 0.25.
REFERENCE_SEASON = 2026


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


def season_weight(season: int) -> float:
    """Exponential decay: weight 0.5^(seasons_ago / half_life)."""
    seasons_ago = REFERENCE_SEASON - season
    return math.pow(0.5, seasons_ago / HALF_LIFE_SEASONS)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", default="data/calibration_v4b_recency.json",
                    help="Output path (default: data/calibration_v4b_recency.json).")
    args = ap.parse_args()

    print("=" * 92)
    print("  Fitting recency-weighted calibrator on 2024+2025 truepit (v4 candidate B)")
    print("=" * 92)
    print(f"  Half-life: {HALF_LIFE_SEASONS} season(s) -- ref season {REFERENCE_SEASON}")
    print(f"  2024 weight: {season_weight(2024):.4f}")
    print(f"  2025 weight: {season_weight(2025):.4f}")

    block_2024, block_2025 = load_truepit_blocks()
    print()
    print(f"  2024 truepit: {block_2024['n']} rows  -> effective {block_2024['n']*season_weight(2024):.0f}")
    print(f"  2025 truepit: {block_2025['n']} rows  -> effective {block_2025['n']*season_weight(2025):.0f}")

    # Train LR on combined corpus (UN-WEIGHTED -- only the calibrator
    # uses recency weights; the LR itself uses all rows equally because
    # the LR's job is to learn feature->prob mapping, which doesn't
    # year-shift the way calibration does).  This is identical to v3's
    # LR step.  Only the post-LR calibration step changes.
    print()
    print("  Training LR on combined 2024+2025 truepit (un-weighted)...")
    X_t1 = np.vstack([block_2024["X_t1"], block_2025["X_t1"]])
    y_t1 = np.concatenate([block_2024["y_t1"], block_2025["y_t1"]])
    X_b1 = np.vstack([block_2024["X_b1"], block_2025["X_b1"]])
    y_b1 = np.concatenate([block_2024["y_b1"], block_2025["y_b1"]])
    m_t1 = LogReg.fit(X_t1, y_t1, T1_PHASE_E3_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(X_b1, y_b1, B1_PHASE_E3_FEATURES, l2=0.05)

    p_t1_all = m_t1.predict_proba(X_t1)
    p_b1_all = m_b1.predict_proba(X_b1)
    p_nrfi_raw = (1.0 - p_t1_all) * (1.0 - p_b1_all)
    y_nrfi_all = np.concatenate([block_2024["y_nrfi"], block_2025["y_nrfi"]])

    # Build per-sample weights based on which season each row came from.
    # 2024 block comes first (because we vstack 2024 then 2025), so the
    # first n_2024 entries of p_nrfi_raw are 2024 samples.
    n_2024 = block_2024["n"]
    n_2025 = block_2025["n"]
    weights = np.concatenate([
        np.full(n_2024, season_weight(2024), dtype=float),
        np.full(n_2025, season_weight(2025), dtype=float),
    ])

    # Fit weighted isotonic
    print()
    print("  Fitting WeightedProbCalibrator (20 quantile bins, recency-weighted)...")
    cal_v4b = WeightedProbCalibrator.fit(
        predictions=p_nrfi_raw.tolist(),
        actuals=y_nrfi_all.tolist(),
        weights=weights.tolist(),
        n_bins=20,
        train_seasons=["2024_truepit", "2025_truepit"],
        weight_decay=f"half_life={HALF_LIFE_SEASONS}_seasons,ref={REFERENCE_SEASON}",
    )
    print(f"    Output range: [{min(cal_v4b.rates):.4f}, {max(cal_v4b.rates):.4f}]")

    # Compare to v2 and v3
    cal_v2 = ProbCalibrator.load(REPO_ROOT / "data" / "calibration_v2.json")
    cal_v3 = ProbCalibrator.load(REPO_ROOT / "data" / "calibration_v3.json")
    print()
    print("=" * 92)
    print("  Calibrator range comparison")
    print("=" * 92)
    print(f"    v2  (production, isotonic, 2025+2026 leaky):    "
          f"[{min(cal_v2.rates):.4f}, {max(cal_v2.rates):.4f}], n={cal_v2.train_n}")
    print(f"    v3  (isotonic, 2024+2025 truepit, equal):        "
          f"[{min(cal_v3.rates):.4f}, {max(cal_v3.rates):.4f}], n={cal_v3.train_n}")
    print(f"    v4b (isotonic, 2024+2025 truepit, recency):      "
          f"[{min(cal_v4b.rates):.4f}, {max(cal_v4b.rates):.4f}], n={cal_v4b.train_n}")

    # Per-input comparison
    sample_inputs = [round(x, 3) for x in np.linspace(p_nrfi_raw.min(),
                                                        p_nrfi_raw.max(),
                                                        20)]
    print()
    print("  Per-input comparison:")
    print(f"    {'raw P(NRFI)':>11}  {'v2':>8}  {'v3':>8}  {'v4b (recency)':>14}")
    for raw in sample_inputs:
        p_v2  = cal_v2.predict(raw)
        p_v3  = cal_v3.predict(raw)
        p_v4b = cal_v4b.predict(raw)
        print(f"    {raw:>10.3f}   {p_v2:>7.4f}  {p_v3:>7.4f}  {p_v4b:>13.4f}")

    # Brier comparison
    p_v2_arr  = np.array([cal_v2.predict(float(p))  for p in p_nrfi_raw])
    p_v3_arr  = np.array([cal_v3.predict(float(p))  for p in p_nrfi_raw])
    p_v4b_arr = np.array([cal_v4b.predict(float(p)) for p in p_nrfi_raw])
    print()
    print("  In-sample Brier on combined 2024+2025 truepit:")
    print(f"    raw model      : {brier(p_nrfi_raw, y_nrfi_all):.4f}")
    print(f"    v2 calibrator  : {brier(p_v2_arr,    y_nrfi_all):.4f}")
    print(f"    v3 calibrator  : {brier(p_v3_arr,    y_nrfi_all):.4f}")
    print(f"    v4b (recency)  : {brier(p_v4b_arr,   y_nrfi_all):.4f}")

    out_path = REPO_ROOT / args.out
    cal_v4b.save(out_path)
    print()
    print(f"  Saved -> {out_path}")
    print()
    print("  v4b sits NEXT TO v2 / v3.  Production unchanged.")


if __name__ == "__main__":
    main()

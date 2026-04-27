#!/usr/bin/env python3
"""
recalibrate_v2.py -- Refit the LR-v2 isotonic calibrator on combined
2025 holdout + 2026 graded-to-date data.

Why: the diagnostic in diagnose_strong_nrfi.py showed that 2026's base
NRFI rate is 47.4% vs 2025's 49.7% -- a real environmental shift. The
calibrator was fit on 2025 alone, so it systematically over-predicts
NRFI by ~2pp on 2026 games. STRONG NRFI is 16-19 (45.7%) on N=35 partly
because of this drift.

This script:
  1. Loads the saved LR-v2 model (data/lr_model.json) -- features and
     weights stay frozen; only the post-LR isotonic mapping is refit.
  2. Loads 2025 backtest CSV and runs raw LR forward to get
     (raw_pred, actual) pairs.
  3. Loads picks_2026.csv graded rows, looks up fi_park_nrfi_rate per
     home team from data/fi_park_factors.json, builds the same 11-feature
     vector, runs raw LR forward.
  4. Concatenates both sources, fits ProbCalibrator.
  5. Writes data/calibration_v2.json (overwrites).

After running, regenerate today's picks: `python mlb_first_inning_predictor.py`
"""

import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Missing numpy. Run: pip install numpy")

from calibration import ProbCalibrator

ROOT          = Path(__file__).parent
LR_T1_PATH    = ROOT / "data" / "lr_t1.json"
LR_B1_PATH    = ROOT / "data" / "lr_b1.json"
FI_PARK_PATH  = ROOT / "data" / "fi_park_factors.json"
BT_2025_PATH  = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026    = ROOT / "data" / "picks_2026.csv"
CAL_OUT_PATH  = ROOT / "data" / "calibration_v2.json"

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_HR9 = 1.20
LEAGUE_AVG_BB9 = 3.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_SLG = 0.414
FI_PARK_DEFAULT = 0.50

# Match the order of feature_names saved in lr_model.json
T1_FEATURES = ["fi_park_nrfi_rate", "home_fip", "away_obp"]
B1_FEATURES = ["fi_park_nrfi_rate", "away_fip", "home_obp"]


def _load_one(path: Path, expected: list[str]) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d["feature_names"] != expected:
        sys.exit(f"{path.name} feature order mismatch.\n"
                 f"  expected: {expected}\n"
                 f"  actual:   {d['feature_names']}")
    return {
        "weights": np.asarray(d["weights"], dtype=float),
        "bias":    float(d["bias"]),
        "mean":    np.asarray(d["mean"],    dtype=float),
        "std":     np.asarray(d["std"],     dtype=float),
    }


def load_lr_models():
    """Returns (t1_model, b1_model) tuple matching the two-stage architecture."""
    return _load_one(LR_T1_PATH, T1_FEATURES), _load_one(LR_B1_PATH, B1_FEATURES)


# Backward-compat shim for any caller that still imports load_lr_model().
def load_lr_model():
    sys.exit(
        "recalibrate_v2.load_lr_model() is deprecated -- the production "
        "model is now two-stage.  Call load_lr_models() and use "
        "lr_predict_two_stage() instead."
    )


def load_fi_park():
    if not FI_PARK_PATH.exists():
        return {}
    with open(FI_PARK_PATH, encoding="utf-8") as f:
        return json.load(f)


def lr_predict_raw(model, X):
    """Standardize and apply logistic; returns (n,) probabilities."""
    Xn = (X - model["mean"]) / model["std"]
    z  = Xn @ model["weights"] + model["bias"]
    return 1.0 / (1.0 + np.exp(-z))


def lr_predict_two_stage(t1_model, b1_model, X_t1, X_b1):
    """Combined P(NRFI) = (1 - P(T1 run)) * (1 - P(B1 run))."""
    p_t1 = lr_predict_raw(t1_model, X_t1)
    p_b1 = lr_predict_raw(b1_model, X_b1)
    return (1.0 - p_t1) * (1.0 - p_b1)


def coerce_float(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def gather_from_backtest(rows, fi_park_map=None):
    """Returns (X_t1, X_b1, y_nrfi, skipped). Backtest CSVs use 'home' for
    park abbr and 'actual_side' for outcome.  fi_park_map is the fresh park
    lookup so calibration sees the same distribution the live predictor uses."""
    Xt, Xb, y = [], [], []
    skipped = 0
    for r in rows:
        actual = r.get("actual_side") or ""
        if actual not in ("NRFI", "YRFI"):
            skipped += 1; continue
        if fi_park_map is not None:
            home = r.get("home", "")
            fi_park = fi_park_map.get(home, FI_PARK_DEFAULT)
        else:
            fi_park = coerce_float(r.get("fi_park_nrfi_rate"), FI_PARK_DEFAULT)
        try:
            t1_vec = [
                fi_park,
                coerce_float(r.get("home_fip"), LEAGUE_AVG_ERA),
                coerce_float(r.get("away_obp"), LEAGUE_AVG_OBP),
            ]
            b1_vec = [
                fi_park,
                coerce_float(r.get("away_fip"), LEAGUE_AVG_ERA),
                coerce_float(r.get("home_obp"), LEAGUE_AVG_OBP),
            ]
        except Exception:
            skipped += 1; continue
        Xt.append(t1_vec); Xb.append(b1_vec)
        y.append(1 if actual == "NRFI" else 0)
    return (np.asarray(Xt, dtype=float),
            np.asarray(Xb, dtype=float),
            np.asarray(y, dtype=int),
            skipped)


def gather_from_picks(rows, fi_park_map):
    """picks_2026.csv: 'home_team' and 'actual_result' instead of backtest's
    'home' and 'actual_side'. Returns (X_t1, X_b1, y_nrfi, skipped)."""
    Xt, Xb, y = [], [], []
    skipped = 0
    for r in rows:
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            skipped += 1; continue
        home_abbr = r.get("home_team") or ""
        fi_park = fi_park_map.get(home_abbr, FI_PARK_DEFAULT)
        try:
            t1_vec = [
                fi_park,
                coerce_float(r.get("home_fip"), LEAGUE_AVG_ERA),
                coerce_float(r.get("away_obp"), LEAGUE_AVG_OBP),
            ]
            b1_vec = [
                fi_park,
                coerce_float(r.get("away_fip"), LEAGUE_AVG_ERA),
                coerce_float(r.get("home_obp"), LEAGUE_AVG_OBP),
            ]
        except Exception:
            skipped += 1; continue
        Xt.append(t1_vec); Xb.append(b1_vec)
        y.append(1 if actual == "NRFI" else 0)
    return (np.asarray(Xt, dtype=float),
            np.asarray(Xb, dtype=float),
            np.asarray(y, dtype=int),
            skipped)


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def main():
    print("=" * 64)
    print("  Recalibrating two-stage LR isotonic mapping on 2025 + 2026 combined")
    print("=" * 64)

    if not LR_T1_PATH.exists() or not LR_B1_PATH.exists():
        sys.exit("Missing two-stage models. Run two_stage_model.py first.")

    t1_model, b1_model = load_lr_models()
    fi_park_map = load_fi_park()

    # 2025 backtest
    with open(BT_2025_PATH, encoding="utf-8") as f:
        bt_rows = list(csv.DictReader(f))
    X25_t1, X25_b1, y25, skip25 = gather_from_backtest(bt_rows, fi_park_map)
    p25 = lr_predict_two_stage(t1_model, b1_model, X25_t1, X25_b1)
    print(f"\n2025 holdout : N={len(y25)} (skipped {skip25})")
    print(f"  Mean raw pred   : {p25.mean()*100:.2f}%")
    print(f"  Actual NRFI rate: {y25.mean()*100:.2f}%")
    print(f"  Raw Brier       : {brier(p25, y25):.4f}")

    # 2026 graded so far
    with open(PICKS_2026, encoding="utf-8") as f:
        pk_rows = list(csv.DictReader(f))
    X26_t1, X26_b1, y26, skip26 = gather_from_picks(pk_rows, fi_park_map)
    p26 = lr_predict_two_stage(t1_model, b1_model, X26_t1, X26_b1)
    print(f"\n2026 graded  : N={len(y26)} (skipped {skip26})")
    print(f"  Mean raw pred   : {p26.mean()*100:.2f}%")
    print(f"  Actual NRFI rate: {y26.mean()*100:.2f}%")
    print(f"  Raw Brier       : {brier(p26, y26):.4f}")

    # Combined
    p_all = np.concatenate([p25, p26])
    y_all = np.concatenate([y25, y26])
    print(f"\nCombined     : N={len(y_all)}")
    print(f"  Mean raw pred   : {p_all.mean()*100:.2f}%")
    print(f"  Actual NRFI rate: {y_all.mean()*100:.2f}%")
    print(f"  Raw bias        : {(p_all.mean() - y_all.mean())*100:+.2f}pp")
    print(f"  Raw Brier       : {brier(p_all, y_all):.4f}")

    # Compare to old calibrator if present
    if CAL_OUT_PATH.exists():
        old_cal = ProbCalibrator.load(CAL_OUT_PATH)
        p25_old_cal = np.array([old_cal.predict(float(p)) for p in p25])
        p26_old_cal = np.array([old_cal.predict(float(p)) for p in p26])
        print(f"\nOld calibrator (2025-only) on each split:")
        print(f"  2025 Brier  raw -> cal:  {brier(p25, y25):.4f} -> {brier(p25_old_cal, y25):.4f}")
        print(f"  2026 Brier  raw -> cal:  {brier(p26, y26):.4f} -> {brier(p26_old_cal, y26):.4f}")
        print(f"  2026 mean   raw -> cal:  {p26.mean()*100:.2f}% -> {p26_old_cal.mean()*100:.2f}% "
              f"(actual {y26.mean()*100:.2f}%)")

    # Fit new calibrator on combined
    n_bins = max(5, min(20, len(y_all) // 130))  # target ~130 picks/bin
    print(f"\nFitting ProbCalibrator on combined N={len(y_all)} with {n_bins} bins...")
    new_cal = ProbCalibrator.fit(
        predictions   = p_all.tolist(),
        actuals       = y_all.tolist(),
        n_bins        = n_bins,
        train_seasons = ["2025", "2026"],
    )

    # Evaluate new calibrator
    p25_new = np.array([new_cal.predict(float(p)) for p in p25])
    p26_new = np.array([new_cal.predict(float(p)) for p in p26])
    p_all_new = np.concatenate([p25_new, p26_new])
    print(f"\nNew calibrator (2025+2026) on each split:")
    print(f"  2025 Brier  raw -> cal:  {brier(p25, y25):.4f} -> {brier(p25_new, y25):.4f}")
    print(f"  2026 Brier  raw -> cal:  {brier(p26, y26):.4f} -> {brier(p26_new, y26):.4f}")
    print(f"  Combined    raw -> cal:  {brier(p_all, y_all):.4f} -> {brier(p_all_new, y_all):.4f}")
    print(f"  2026 mean   raw -> cal:  {p26.mean()*100:.2f}% -> {p26_new.mean()*100:.2f}% "
          f"(actual {y26.mean()*100:.2f}%)")

    # Print the curve
    print(f"\nNew calibration curve (raw LR pred -> calibrated NRFI prob):")
    print(f"  {'pred bin':>10}    {'observed':>9}")
    for c, r in zip(new_cal.centers, new_cal.rates):
        print(f"  {c*100:>9.1f}%    {r*100:>8.1f}%")

    new_cal.save(CAL_OUT_PATH)
    print(f"\nSaved updated calibrator -> {CAL_OUT_PATH}")
    print(f"  Bins: {len(new_cal.centers)}, fit on N={new_cal.train_n} "
          f"({len(y25)} from 2025, {len(y26)} from 2026)")


if __name__ == "__main__":
    main()

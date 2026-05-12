#!/usr/bin/env python3
"""
feature_importance.py -- what's actually driving the LR-v2 model?

For each feature, reports:
  - Standardized weight (coef in z-space; magnitude alone is misleading)
  - Effect size = abs(weight) * std (typical pp shift in z * weight space)
  - Sign expected vs sign actual (multicollinearity check)
  - Single-feature ablation Brier (drop this feature -> what does it cost?)
  - Permutation importance on a holdout (real practical impact)
"""

import csv
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install: pip install numpy")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from lr_baseline import LogReg, V2_FEATURES, load_dataset, brier
from calibration import ProbCalibrator


# Intuitive expected sign for each feature (in z-space):
EXPECTED_SIGN = {
    "fi_park_nrfi_rate":  +1,  # higher park NRFI rate -> more NRFI
    "home_fip":           -1,  # higher home pitcher FIP = bad pitcher -> YRFI
    "home_hr9":           -1,  # more HRs -> YRFI
    "home_bb9":           -1,  # more walks -> YRFI
    "away_obp":           -1,  # better away offense -> YRFI in T1
    "away_slg":           -1,  # better away offense -> YRFI in T1
    "away_fip":           -1,  # higher away pitcher FIP -> YRFI in B1
    "away_hr9":           -1,  # more HRs -> YRFI
    "away_bb9":           -1,  # more walks -> YRFI
    "home_obp":           -1,  # better home offense -> YRFI in B1
    "home_slg":           -1,  # better home offense -> YRFI in B1
}


def main():
    # Load saved production model
    with open(ROOT / "data" / "lr_model.json", encoding="utf-8") as f:
        m = json.load(f)
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")

    # Load 2025 holdout for ablation/permutation tests
    bt_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
    X, y, feats, _ = load_dataset(bt_2025, V2_FEATURES, False)

    weights = np.asarray(m["weights"])
    means   = np.asarray(m["mean"])
    stds    = np.asarray(m["std"])
    bias    = float(m["bias"])

    print("=" * 78)
    print("  LR-v2 feature importance (production weights, after park-factor fix)")
    print("=" * 78)
    print(f"\n  Bias term: {bias:+.4f}")
    print(f"\n  {'feature':<22}  {'coef':>7}  {'std':>7}  {'|coef|*std':>11}  "
          f"  {'expected':>8}  verdict")
    print("  " + "-" * 76)

    rows = []
    for name, coef, mn, sd in zip(feats, weights, means, stds):
        effect = abs(coef) * sd  # not informative; standardized features have std=1
        # Better: just |coef| since features were standardized to unit variance
        importance = abs(coef)
        exp_sign = EXPECTED_SIGN.get(name, 0)
        actual_sign = +1 if coef > 0 else -1 if coef < 0 else 0
        if exp_sign == 0:
            verdict = ""
        elif actual_sign == exp_sign:
            verdict = "ok"
        else:
            verdict = "WRONG SIGN"
        rows.append((name, coef, sd, importance, exp_sign, verdict))

    # Sort by importance descending
    rows.sort(key=lambda r: -r[3])

    for (name, coef, sd, imp, exp_sign, verdict) in rows:
        print(f"  {name:<22}  {coef:>+7.3f}  {sd:>7.4f}  {imp:>11.3f}  "
              f"  {exp_sign:>+8}  {verdict}")

    # ---------- Single-feature ablation ----------
    print("\n  Drop-one ablation: train without each feature, score on 2025 holdout")
    print("  " + "-" * 76)

    # Refit V2 on its own training data (2024) and ablate
    bt_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
    X_tr, y_tr, _, _ = load_dataset(bt_2024, V2_FEATURES, False)
    full = LogReg.fit(X_tr, y_tr, V2_FEATURES, l2=0.01)
    p_full = full.predict_proba(X)
    base_brier = brier(p_full, y)
    print(f"  Full V2 holdout Brier: {base_brier:.4f}\n")
    print(f"  {'dropped':<22}  {'Brier':>7}  {'delta':>7}  {'rank':<6}")

    deltas = []
    for i, name in enumerate(feats):
        feats_minus = [f for j, f in enumerate(feats) if j != i]
        X_tr_m = np.delete(X_tr, i, axis=1)
        X_m    = np.delete(X,    i, axis=1)
        m_min = LogReg.fit(X_tr_m, y_tr, feats_minus, l2=0.01)
        b = brier(m_min.predict_proba(X_m), y)
        deltas.append((name, b, b - base_brier))

    # Sort by Brier worsening (largest delta = most important to keep)
    deltas.sort(key=lambda r: -(r[2]))
    for k, (name, b, d) in enumerate(deltas, 1):
        verdict = "important" if d > 0.0005 else "noise" if abs(d) < 0.0001 else "ok"
        print(f"  {name:<22}  {b:.4f}  {d:>+7.4f}  #{k}  {verdict}")

    # ---------- Permutation importance ----------
    print("\n  Permutation importance: shuffle each feature, measure Brier degradation")
    print("  (large positive = feature is genuinely contributing predictive signal)")
    print("  " + "-" * 76)

    rng = np.random.default_rng(seed=42)
    perm_results = []
    for i, name in enumerate(feats):
        deltas_i = []
        for _ in range(20):  # 20 shuffles
            X_perm = X.copy()
            rng.shuffle(X_perm[:, i])  # in-place shuffle of column i
            p_perm = full.predict_proba(X_perm)
            deltas_i.append(brier(p_perm, y) - base_brier)
        mean_delta = float(np.mean(deltas_i))
        std_delta  = float(np.std(deltas_i))
        perm_results.append((name, mean_delta, std_delta))

    perm_results.sort(key=lambda r: -r[1])
    print(f"  {'feature':<22}  {'mean dB':>9}  {'std dB':>9}  meaning")
    for name, m_d, s_d in perm_results:
        verdict = (
            "real signal"   if m_d >  3 * s_d else
            "weak signal"   if m_d >    s_d else
            "noise"
        )
        print(f"  {name:<22}  {m_d:>+8.4f}  {s_d:>9.4f}  {verdict}")

    print()


if __name__ == "__main__":
    main()

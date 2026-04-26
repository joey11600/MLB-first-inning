#!/usr/bin/env python3
"""
gbm_baseline.py -- Gradient boosting baseline.

Tests whether non-linear models (GradientBoostingClassifier) can extract
more signal from the 20-feature backtest CSV than logistic regression can.
LR found that fi_park_nrfi_rate is the dominant linear signal; GBM might
find feature interactions (park x temp, lineup x pitcher) that LR misses.

Usage:
  python gbm_baseline.py --train data/.../2024.csv --test data/.../2025.csv
  python gbm_baseline.py --train ... --test ... --feature-set full
"""

import argparse
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
except ImportError:
    sys.exit("Missing dependencies: pip install numpy scikit-learn")

from lr_baseline import (
    LEGACY_FEATURES, LINEUP_FEATURES, V1_FEATURES,
    PITCHER_RECENCY_FEATURES, CURRENT_FEATURES, WEATHER_FEATURES, FULL_FEATURES,
    FEATURE_SETS, FEATURE_DEFAULTS, load_dataset,
    brier, log_loss, calibration_bins, quintile_table,
)


def _print_report(p_te: np.ndarray, y_te: np.ndarray, n_train: int,
                   features: list[str], importances: np.ndarray | None = None,
                   model_label: str = "GBM") -> None:
    sep  = "=" * 64
    sep2 = "-" * 64
    base = float(y_te.mean())
    climatology_brier = base * (1 - base)
    climatology_ll = -(base * math.log(max(1e-12, base))
                       + (1 - base) * math.log(max(1e-12, 1 - base)))

    print(f"\n{sep}")
    print(f"  {model_label} BASELINE")
    print(sep)
    print(f"  Train N             : {n_train}")
    print(f"  Test  N             : {len(y_te)}")
    print(f"  Test NRFI rate      : {base*100:.1f}%")
    print(f"  Mean predicted NRFI : {p_te.mean()*100:.1f}%   "
          f"(model bias = {(p_te.mean() - base)*100:+.1f}pp)")
    print(f"  Brier score         : {brier(p_te, y_te):.4f}   "
          f"(climatology = {climatology_brier:.4f})")
    print(f"  Log loss            : {log_loss(p_te, y_te):.4f}   "
          f"(climatology = {climatology_ll:.4f})")
    print(f"  Pred range          : {p_te.min()*100:.1f}% - {p_te.max()*100:.1f}%")
    print(f"  Pred std            : {p_te.std()*100:.2f}pp")

    print(f"\n{sep2}")
    print(f"  Calibration (predicted NRFI -> observed):")
    print(f"    {'bin':<12}  {'n':>4}  {'pred':>7}  {'obs':>7}   drift")
    for label, n, avg_p, obs in calibration_bins(p_te, y_te, n_bins=10):
        d = obs - avg_p
        sign = "+" if d >= 0 else ""
        print(f"    {label:<12}  {n:>4}  {avg_p*100:>6.1f}%  {obs*100:>6.1f}%   "
              f"{sign}{d*100:>5.1f}pp")

    print(f"\n{sep2}")
    print(f"  By predicted-probability quintile:")
    print(f"    {'q':>2}  {'n':>4}  {'avg pred':>9}  {'obs NRFI':>9}  vs base")
    for q, n, avg_p, obs, _ in quintile_table(p_te, y_te, n_q=5):
        d = obs - base
        sign = "+" if d >= 0 else ""
        print(f"    Q{q}  {n:>4}  {avg_p*100:>8.1f}%  {obs*100:>8.1f}%  {sign}{d*100:>5.1f}pp")

    if importances is not None:
        print(f"\n{sep2}")
        print(f"  Feature importances (Gini):")
        ranked = sorted(zip(features, importances), key=lambda p: -p[1])
        for name, imp in ranked:
            tag = "***" if imp > 0.10 else ("** " if imp > 0.05 else "   ")
            print(f"    {tag} {name:<30}  {imp:.4f}")
    print(sep)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gradient-boosting baseline on backtest features.",
    )
    parser.add_argument("--train", nargs="+", required=True, help="Training CSV(s)")
    parser.add_argument("--test",  required=True, help="Held-out test CSV")
    parser.add_argument("--features", nargs="+", default=None)
    parser.add_argument("--feature-set", choices=list(FEATURE_SETS.keys()),
                        default="full")
    parser.add_argument("--model", choices=["gbm", "rf"], default="gbm",
                        help="Model type (gbm = GradientBoosting, rf = RandomForest)")
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--max-depth",    type=int, default=3)
    parser.add_argument("--lr",           type=float, default=0.05,
                        help="Learning rate (GBM only)")
    args = parser.parse_args()

    if args.features:
        features = args.features
    else:
        features = FEATURE_SETS[args.feature_set]

    X_parts, y_parts = [], []
    for p_str in args.train:
        p = Path(p_str)
        print(f"\nLoading train: {p.name}")
        X_p, y_p, feats, _ = load_dataset(p, features)
        print(f"  N={len(y_p)}  features={len(feats)}")
        if X_p.size:
            X_parts.append(X_p); y_parts.append(y_p)
    X_tr = np.vstack(X_parts)
    y_tr = np.concatenate(y_parts)

    test_path = Path(args.test)
    print(f"\nLoading test: {test_path.name}")
    X_te, y_te, _, _ = load_dataset(test_path, features)
    print(f"  N={len(y_te)}")

    if args.model == "gbm":
        print(f"\nFitting GBM (n_est={args.n_estimators}, max_depth={args.max_depth}, lr={args.lr})...")
        model = GradientBoostingClassifier(
            n_estimators  = args.n_estimators,
            max_depth     = args.max_depth,
            learning_rate = args.lr,
            random_state  = 42,
            subsample     = 0.85,   # mild stochasticity helps generalization
        )
        label = f"GBM (n_est={args.n_estimators}, depth={args.max_depth})"
    else:
        print(f"\nFitting RandomForest (n_est={args.n_estimators}, max_depth={args.max_depth})...")
        model = RandomForestClassifier(
            n_estimators = args.n_estimators,
            max_depth    = args.max_depth,
            random_state = 42,
            n_jobs       = -1,
        )
        label = f"RF (n_est={args.n_estimators}, depth={args.max_depth})"

    model.fit(X_tr, y_tr)
    p_tr = model.predict_proba(X_tr)[:, 1]
    p_te = model.predict_proba(X_te)[:, 1]
    print(f"\nIn-sample (train) Brier  : {brier(p_tr, y_tr):.4f}")
    print(f"Out-of-sample (test) Brier: {brier(p_te, y_te):.4f}")

    _print_report(p_te, y_te, len(y_tr), features,
                  importances=model.feature_importances_, model_label=label)


if __name__ == "__main__":
    main()

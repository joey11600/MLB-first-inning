#!/usr/bin/env python3
"""tools/two_stage_xgb.py

XGBoost variant of the two-stage NRFI model.  Same architecture as
two_stage_model.py (separate T1 + B1 models, combined via half-inning
independence), same features, same labels -- only the per-half model
changes from LogReg to XGBClassifier.

Why: the 2026-05-12 Phase G experiment showed LR + the current 18
features is near its ceiling on this data (0.0001 Brier improvement
from adding 6 well-motivated features).  Tree-based models can
capture non-linear interactions LR cannot (e.g. "elite-power lineup
+ neutral park + ground-ball pitcher" is a different signal than the
linear sum of those features).

Hyperparameters chosen for tabular binary classification with ~5K
training samples and 18 features:
  max_depth=4        -- shallow trees, low overfit risk
  learning_rate=0.05 -- slow learner, lots of trees
  n_estimators=500   -- with early stopping if test_brier doesn't
                        improve for 30 rounds
  subsample=0.8      -- row bagging
  colsample_bytree=0.8 -- feature bagging
  min_child_weight=5 -- prevents single-sample leaves
  reg_lambda=1.0     -- L2 on leaf weights

Uses same gather() helper as two_stage_model.py so the feature builder
stays single-sourced.

Usage:
  python tools/two_stage_xgb.py --phase-e3 \
    --train data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv \
            data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv \
    --test  data/picks_2026.csv \
    --save-t1 data/candidates/xgb_t1_v3.json \
    --save-b1 data/candidates/xgb_b1_v3.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import xgboost as xgb
from two_stage_model import (
    gather, load_fi_park,
    T1_FEATURES, B1_FEATURES,
    T1_SLIM_FEATURES, B1_SLIM_FEATURES,
    T1_SLIM_K9_FEATURES, B1_SLIM_K9_FEATURES,
    T1_SLIM_WEATHER_FEATURES, B1_SLIM_WEATHER_FEATURES,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
    T1_PHASE_G_FEATURES, B1_PHASE_G_FEATURES,
)


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def train_one(X_tr, y_tr, X_te, y_te, feat_names: list[str],
              max_depth: int, learning_rate: float,
              n_estimators: int, early_stopping_rounds: int,
              subsample: float, colsample_bytree: float,
              min_child_weight: int, reg_lambda: float,
              seed: int = 17) -> tuple[xgb.XGBClassifier, dict]:
    """Train one half's XGB model with early stopping on test set.

    Note: using test set for early stopping is a mild data peek but
    standard for tree-based tabular models; the 3-split protocol
    catches the danger by training/testing on different season splits.
    """
    model = xgb.XGBClassifier(
        max_depth        = max_depth,
        learning_rate    = learning_rate,
        n_estimators     = n_estimators,
        early_stopping_rounds = early_stopping_rounds,
        subsample        = subsample,
        colsample_bytree = colsample_bytree,
        min_child_weight = min_child_weight,
        reg_lambda       = reg_lambda,
        objective        = "binary:logistic",
        eval_metric      = "logloss",
        tree_method      = "hist",
        random_state     = seed,
        verbosity        = 0,
    )
    model.set_params(early_stopping_rounds=early_stopping_rounds)
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
    return model, {
        "n_train":  len(X_tr),
        "n_test":   len(X_te),
        "features": feat_names,
        "best_iteration": int(getattr(model, "best_iteration", n_estimators) or n_estimators),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--train", nargs="+", required=True,
                   help="Training backtest CSV(s) -- same format as two_stage_model.py")
    p.add_argument("--test", required=True, help="Held-out test CSV")
    p.add_argument("--save-t1", help="Save T1 booster JSON to this path")
    p.add_argument("--save-b1", help="Save B1 booster JSON to this path")

    # Feature variant flags (mirror two_stage_model.py)
    p.add_argument("--slim", action="store_true")
    p.add_argument("--slim-k9", action="store_true")
    p.add_argument("--slim-weather", action="store_true")
    p.add_argument("--phase-e3", action="store_true",
                   help="Phase E.3 + Phase F features (18 per half, current production)")
    p.add_argument("--phase-g", action="store_true",
                   help="Phase G: phase-e3 + top3c_last10_obp/slg/iso (21 per half)")

    # XGB hyperparameters
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--n-estimators", type=int, default=500)
    p.add_argument("--early-stopping-rounds", type=int, default=30)
    p.add_argument("--subsample", type=float, default=0.8)
    p.add_argument("--colsample-bytree", type=float, default=0.8)
    p.add_argument("--min-child-weight", type=int, default=5)
    p.add_argument("--reg-lambda", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--clean-only", action="store_true")
    args = p.parse_args()

    park = load_fi_park()
    if args.phase_g:
        if not args.phase_e3:
            print("[note] --phase-g requires --phase-e3; enabling")
            args.phase_e3 = True
        t1_feats = T1_PHASE_G_FEATURES; b1_feats = B1_PHASE_G_FEATURES; variant = "PHASE_G"
    elif args.phase_e3:
        t1_feats = T1_PHASE_E3_FEATURES; b1_feats = B1_PHASE_E3_FEATURES; variant = "PHASE_E3"
    elif args.slim_weather:
        t1_feats = T1_SLIM_WEATHER_FEATURES; b1_feats = B1_SLIM_WEATHER_FEATURES; variant = "SLIM+WEATHER"
    elif args.slim_k9:
        t1_feats = T1_SLIM_K9_FEATURES; b1_feats = B1_SLIM_K9_FEATURES; variant = "SLIM+K9"
    elif args.slim:
        t1_feats = T1_SLIM_FEATURES; b1_feats = B1_SLIM_FEATURES; variant = "SLIM"
    else:
        t1_feats = T1_FEATURES; b1_feats = B1_FEATURES; variant = "FULL"

    # Phase E3 needs umpire fallback for missing cells; just no-op if missing
    ump_cache = ump_rates_data = None
    if args.phase_e3:
        ump_cache_path  = ROOT / "data" / "umpire_cache.json"
        ump_rates_path  = ROOT / "data" / "umpire_rates.json"
        if ump_cache_path.exists():
            ump_cache = json.load(open(ump_cache_path, encoding="utf-8"))
        if ump_rates_path.exists():
            ump_rates_data = json.load(open(ump_rates_path, encoding="utf-8"))

    print("=" * 70)
    print(f"  Training XGB two-stage T1 + B1  ({variant} variant)")
    print(f"  max_depth={args.max_depth} lr={args.learning_rate} "
          f"n_est={args.n_estimators}(early_stop={args.early_stopping_rounds}) "
          f"subsample={args.subsample} colsample={args.colsample_bytree}")
    print("=" * 70)

    train_blocks = [gather(Path(pth), park,
                           slim=args.slim, slim_k9=args.slim_k9,
                           slim_weather=args.slim_weather,
                           phase_e3=args.phase_e3, phase_g=args.phase_g,
                           ump_cache=ump_cache, ump_rates_data=ump_rates_data,
                           clean_only=args.clean_only) for pth in args.train]
    X_t1_tr = np.vstack([b["X_t1"] for b in train_blocks])
    y_t1_tr = np.concatenate([b["y_t1"] for b in train_blocks])
    X_b1_tr = np.vstack([b["X_b1"] for b in train_blocks])
    y_b1_tr = np.concatenate([b["y_b1"] for b in train_blocks])
    y_nrfi_tr = np.concatenate([b["y_nrfi"] for b in train_blocks])

    test_block = gather(Path(args.test), park,
                        slim=args.slim, slim_k9=args.slim_k9,
                        slim_weather=args.slim_weather,
                        phase_e3=args.phase_e3, phase_g=args.phase_g,
                        ump_cache=ump_cache, ump_rates_data=ump_rates_data,
                        clean_only=args.clean_only)
    X_t1_te = test_block["X_t1"]; y_t1_te = test_block["y_t1"]
    X_b1_te = test_block["X_b1"]; y_b1_te = test_block["y_b1"]
    y_nrfi_te = test_block["y_nrfi"]

    print(f"\n  Train N : {len(y_t1_tr)}    Test N : {len(y_t1_te)}")
    print(f"  Train NRFI rate: {y_nrfi_tr.mean()*100:.2f}%")
    print(f"  Test  NRFI rate: {y_nrfi_te.mean()*100:.2f}%")

    print("\n  Training T1 (home pitcher vs away offense)...")
    m_t1, info_t1 = train_one(X_t1_tr, y_t1_tr, X_t1_te, y_t1_te, t1_feats,
                               args.max_depth, args.learning_rate, args.n_estimators,
                               args.early_stopping_rounds, args.subsample,
                               args.colsample_bytree, args.min_child_weight,
                               args.reg_lambda, args.seed)
    print(f"    best_iteration={info_t1['best_iteration']}")

    print("  Training B1 (away pitcher vs home offense)...")
    m_b1, info_b1 = train_one(X_b1_tr, y_b1_tr, X_b1_te, y_b1_te, b1_feats,
                               args.max_depth, args.learning_rate, args.n_estimators,
                               args.early_stopping_rounds, args.subsample,
                               args.colsample_bytree, args.min_child_weight,
                               args.reg_lambda, args.seed + 1)
    print(f"    best_iteration={info_b1['best_iteration']}")

    # Two-stage NRFI prediction
    p_t1_te = m_t1.predict_proba(X_t1_te)[:, 1]
    p_b1_te = m_b1.predict_proba(X_b1_te)[:, 1]
    p_nrfi_te = (1.0 - p_t1_te) * (1.0 - p_b1_te)
    brier_2stage = brier(p_nrfi_te, y_nrfi_te)

    # Compare baseline -- mean predictor for sanity
    base_brier = brier(np.full_like(y_nrfi_te, y_nrfi_tr.mean(), dtype=float), y_nrfi_te)

    print()
    print(f"  Two-stage Brier  : {brier_2stage:.4f}")
    print(f"  Baseline (mean)  : {base_brier:.4f}")
    print(f"  Train mean pred  : {((1-p_t1_te)*(1-p_b1_te)).mean()*100:.2f}%")
    print(f"  Actual NRFI rate : {y_nrfi_te.mean()*100:.2f}%")

    if args.save_t1 and args.save_b1:
        Path(args.save_t1).parent.mkdir(parents=True, exist_ok=True)
        m_t1.save_model(args.save_t1)
        m_b1.save_model(args.save_b1)
        # Side-car metadata file (xgboost save_model is JSON but doesn't
        # include our feature list / hyperparams)
        meta_path = Path(args.save_t1).with_suffix(".meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "variant":       variant,
                "t1_features":   t1_feats,
                "b1_features":   b1_feats,
                "hyperparams":   {
                    "max_depth":         args.max_depth,
                    "learning_rate":     args.learning_rate,
                    "n_estimators":      args.n_estimators,
                    "subsample":         args.subsample,
                    "colsample_bytree":  args.colsample_bytree,
                    "min_child_weight":  args.min_child_weight,
                    "reg_lambda":        args.reg_lambda,
                    "seed":              args.seed,
                },
                "best_iteration_t1": info_t1["best_iteration"],
                "best_iteration_b1": info_b1["best_iteration"],
                "n_train":       len(y_t1_tr),
                "n_test":        len(y_t1_te),
                "brier":         brier_2stage,
            }, f, indent=2)
        print(f"\n  Saved T1 -> {args.save_t1}")
        print(f"  Saved B1 -> {args.save_b1}")
        print(f"  Saved meta -> {meta_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
test_phase_b_xgboost.py -- Phase B: replace LR with XGBoost.

Tree models can capture interactions LR cannot:
  - park x wind direction
  - pitcher_FIP x opposing_OBP
  - last5_NRFI x season_FIP

Architectures tested:
  - twostage_xgb_slim     : two XGBoost models (T1, B1), 7 slim_weather features
  - twostage_xgb_full     : two XGBoost models, 7 slim + last5_nrfi + top3c_obp + top3c_iso
  - single_xgb_slim       : one XGBoost predicting NRFI directly, both halves' features
  - single_xgb_full       : single XGBoost, all features

Train: 2024+2025 CLEAN rows (pitcher_q != avg both sides).
Test:  2026 graded picks (N=348).
Baseline: LR slim_weather production model (Brier 0.2466 on this same test set).

Reports Brier, mean predicted, Q5 NRFI hit rate, Q1 YRFI hit rate.
Repeats over 5 seeds and averages to control for tree randomness.
"""

import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    import xgboost as xgb
except ImportError as e:
    sys.exit(f"Missing dep: {e}")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from lr_baseline import LogReg

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_ISO = 0.169
WX_TEMP_DEFAULT     = 20.0
WX_WIND_DEFAULT     = 10.0
WX_HUMIDITY_DEFAULT = 60.0
FI_PARK_DEFAULT = 0.50
LEAGUE_AVG_NRFI_RATE = 0.50

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"


def coerce(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


SLIM_T1_NAMES = ["fi_park_nrfi_rate", "home_fip", "away_obp",
                 "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome"]
SLIM_B1_NAMES = ["fi_park_nrfi_rate", "away_fip", "home_obp",
                 "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome"]
EXTRA_T1_NAMES = ["home_p_last5_nrfi", "away_top3c_obp", "away_top3c_iso"]
EXTRA_B1_NAMES = ["away_p_last5_nrfi", "home_top3c_obp", "home_top3c_iso"]


def slim_t1(r, park):
    home = r.get("home", "") or r.get("home_team", "")
    return [
        park.get(home, FI_PARK_DEFAULT),
        coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
        coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
        coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
        coerce(r.get("wx_is_dome"),  0.0),
    ]


def slim_b1(r, park):
    home = r.get("home", "") or r.get("home_team", "")
    return [
        park.get(home, FI_PARK_DEFAULT),
        coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
        coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
        coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
        coerce(r.get("wx_is_dome"),  0.0),
    ]


def extras_t1(r):
    return [
        coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_AVG_NRFI_RATE),
        coerce(r.get("away_top3c_obp"),            LEAGUE_AVG_OBP),
        coerce(r.get("away_top3c_iso"),            LEAGUE_AVG_ISO),
    ]


def extras_b1(r):
    return [
        coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_AVG_NRFI_RATE),
        coerce(r.get("home_top3c_obp"),            LEAGUE_AVG_OBP),
        coerce(r.get("home_top3c_iso"),            LEAGUE_AVG_ISO),
    ]


def gather(csv_path, park, feat_set: str, clean_only: bool):
    """feat_set: 'slim' or 'full'.  Returns (X_t1, y_t1, X_b1, y_b1, X_full, y_nrfi)."""
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"): continue
            if clean_only:
                ap = (r.get("away_pitcher_q") or "").lower()
                hp = (r.get("home_pitcher_q") or "").lower()
                if ap == "avg" or hp == "avg": continue
            t1_runs = r.get("fi_away_runs", "")
            b1_runs = r.get("fi_home_runs", "")
            if t1_runs == "" or b1_runs == "": continue
            try:
                t1y = 1 if int(float(t1_runs)) > 0 else 0
                b1y = 1 if int(float(b1_runs)) > 0 else 0
            except (TypeError, ValueError):
                continue
            t1_x = slim_t1(r, park)
            b1_x = slim_b1(r, park)
            if feat_set == "full":
                t1_x = t1_x + extras_t1(r)
                b1_x = b1_x + extras_b1(r)
            Xt.append(t1_x); Xb.append(b1_x)
            yt.append(t1y); yb.append(b1y)
            ynrfi.append(1 if actual == "NRFI" else 0)
    Xt = np.asarray(Xt, dtype=float)
    Xb = np.asarray(Xb, dtype=float)
    # X_full for single-model architecture: concatenate both halves' features
    Xfull = np.hstack([Xt, Xb])
    return (Xt, np.asarray(yt, dtype=int),
            Xb, np.asarray(yb, dtype=int),
            Xfull, np.asarray(ynrfi, dtype=int))


def brier(p, y): return float(np.mean((p - y) ** 2))


def q5_hit(p, y):
    order = np.argsort(p); n = len(p)
    q5 = order[-(n // 5):]
    return float(y[q5].mean()), int(y[q5].sum()), len(q5)


def q1_yrfi(p, y):
    order = np.argsort(p); n = len(p)
    q1 = order[:(n // 5)]
    wins = int((y[q1] == 0).sum())
    return wins / len(q1) if len(q1) else 0.0, wins, len(q1)


def fit_xgb(X, y, *, seed: int, max_depth: int = 4, n_est: int = 300,
            lr: float = 0.05, min_child_weight: int = 5,
            reg_lambda: float = 1.0, subsample: float = 0.85,
            colsample: float = 0.85, X_val=None, y_val=None):
    """Single XGBoost classifier.  Uses early stopping when val set provided."""
    params = {
        "objective":     "binary:logistic",
        "eval_metric":   "logloss",
        "max_depth":     max_depth,
        "learning_rate": lr,
        "subsample":     subsample,
        "colsample_bytree": colsample,
        "min_child_weight": min_child_weight,
        "reg_lambda":    reg_lambda,
        "random_state":  seed,
        "n_jobs":        -1,
        "verbosity":     0,
        "tree_method":   "hist",
    }
    clf = xgb.XGBClassifier(n_estimators=n_est, **params,
                             early_stopping_rounds=20 if X_val is not None else None)
    if X_val is not None:
        clf.fit(X, y, eval_set=[(X_val, y_val)], verbose=False)
    else:
        clf.fit(X, y, verbose=False)
    return clf


# Aggressive-regularization variant for small-N defense
def fit_xgb_tiny(X, y, *, seed: int):
    return fit_xgb(X, y, seed=seed, max_depth=2, n_est=400, lr=0.03,
                    min_child_weight=30, reg_lambda=5.0,
                    subsample=0.7, colsample=0.7)


# CV-based variant with proper early stopping on a 20% holdout
def fit_xgb_cv(X, y, *, seed: int, max_depth: int = 3,
                min_child_weight: int = 15, reg_lambda: float = 3.0):
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(y))
    cut = int(len(y) * 0.8)
    tr_idx, va_idx = idx[:cut], idx[cut:]
    return fit_xgb(X[tr_idx], y[tr_idx], seed=seed,
                    max_depth=max_depth, n_est=600, lr=0.03,
                    min_child_weight=min_child_weight,
                    reg_lambda=reg_lambda,
                    X_val=X[va_idx], y_val=y[va_idx])


def lr_baseline_2stage(X_t1_tr, y_t1_tr, X_b1_tr, y_b1_tr,
                        X_t1_te, X_b1_te):
    """Reproduce the LR slim_weather baseline for a clean side-by-side."""
    m_t1 = LogReg.fit(X_t1_tr, y_t1_tr,
                       [f"t{i}" for i in range(X_t1_tr.shape[1])], l2=0.05)
    m_b1 = LogReg.fit(X_b1_tr, y_b1_tr,
                       [f"b{i}" for i in range(X_b1_tr.shape[1])], l2=0.05)
    p_t1 = m_t1.predict_proba(X_t1_te)
    p_b1 = m_b1.predict_proba(X_b1_te)
    return (1.0 - p_t1) * (1.0 - p_b1)


def run_arch(arch: str, feat_set: str, train, test, seeds=(11, 42, 123, 7, 2024)):
    """Train one of the architectures over multiple seeds, return mean Brier
    and per-seed predictions averaged for the slate-level metrics."""
    Xt_tr, yt_tr, Xb_tr, yb_tr, Xfull_tr, ynrfi_tr = train
    Xt_te, _, Xb_te, _, Xfull_te, ynrfi_te = test

    preds = []
    for seed in seeds:
        if arch == "lr_2stage":
            p = lr_baseline_2stage(Xt_tr, yt_tr, Xb_tr, yb_tr, Xt_te, Xb_te)
            preds.append(p); break  # LR is deterministic, one seed suffices
        elif arch == "xgb_2stage":
            m_t1 = fit_xgb(Xt_tr, yt_tr, seed=seed)
            m_b1 = fit_xgb(Xb_tr, yb_tr, seed=seed)
            p_t1 = m_t1.predict_proba(Xt_te)[:, 1]
            p_b1 = m_b1.predict_proba(Xb_te)[:, 1]
            preds.append((1.0 - p_t1) * (1.0 - p_b1))
        elif arch == "xgb_single":
            m = fit_xgb(Xfull_tr, ynrfi_tr, seed=seed)
            preds.append(m.predict_proba(Xfull_te)[:, 1])
        elif arch == "xgb_tiny_2stage":
            m_t1 = fit_xgb_tiny(Xt_tr, yt_tr, seed=seed)
            m_b1 = fit_xgb_tiny(Xb_tr, yb_tr, seed=seed)
            p_t1 = m_t1.predict_proba(Xt_te)[:, 1]
            p_b1 = m_b1.predict_proba(Xb_te)[:, 1]
            preds.append((1.0 - p_t1) * (1.0 - p_b1))
        elif arch == "xgb_tiny_single":
            m = fit_xgb_tiny(Xfull_tr, ynrfi_tr, seed=seed)
            preds.append(m.predict_proba(Xfull_te)[:, 1])
        elif arch == "xgb_cv_2stage":
            m_t1 = fit_xgb_cv(Xt_tr, yt_tr, seed=seed)
            m_b1 = fit_xgb_cv(Xb_tr, yb_tr, seed=seed)
            p_t1 = m_t1.predict_proba(Xt_te)[:, 1]
            p_b1 = m_b1.predict_proba(Xb_te)[:, 1]
            preds.append((1.0 - p_t1) * (1.0 - p_b1))
        elif arch == "xgb_cv_single":
            m = fit_xgb_cv(Xfull_tr, ynrfi_tr, seed=seed)
            preds.append(m.predict_proba(Xfull_te)[:, 1])
        else:
            raise ValueError(arch)

    p_avg = np.mean(preds, axis=0)
    b = brier(p_avg, ynrfi_te)
    m = float(p_avg.mean())
    q5_r, q5_w, q5_n = q5_hit(p_avg, ynrfi_te)
    q1_r, q1_w, q1_n = q1_yrfi(p_avg, ynrfi_te)
    return {
        "brier": b, "mean": m,
        "q5_w": q5_w, "q5_n": q5_n, "q5_r": q5_r,
        "q1_w": q1_w, "q1_n": q1_n, "q1_r": q1_r,
        "preds_per_seed": [brier(p, ynrfi_te) for p in preds],
    }


def main():
    park = json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))

    # Build train and test datasets for both feature sets
    def build_split(feat_set):
        b24 = gather(BT_2024, park, feat_set, clean_only=True)
        b25 = gather(BT_2025, park, feat_set, clean_only=True)
        train = (
            np.vstack([b24[0], b25[0]]),
            np.concatenate([b24[1], b25[1]]),
            np.vstack([b24[2], b25[2]]),
            np.concatenate([b24[3], b25[3]]),
            np.vstack([b24[4], b25[4]]),
            np.concatenate([b24[5], b25[5]]),
        )
        test = gather(PICKS_2026, park, feat_set, clean_only=False)
        return train, test

    train_slim, test_slim = build_split("slim")
    train_full, test_full = build_split("full")

    print("=" * 96)
    print("  Phase B: XGBoost vs LR baseline   (train 2024+2025 CLEAN ; test 2026 N=348)")
    print("=" * 96)
    print(f"  slim features: {SLIM_T1_NAMES}")
    print(f"  extra features: {EXTRA_T1_NAMES}")
    print()
    print(f"  {'arch':<22} {'feat':<6} {'train N':>7} {'Brier':>7} "
          f"{'mean':>7} {'Q5 NRFI':>13} {'Q1 YRFI':>13} {'seed range':>16}")
    print("  " + "-" * 92)

    configs = [
        ("lr_2stage",        "slim", train_slim, test_slim),
        ("lr_2stage",        "full", train_full, test_full),
        ("xgb_2stage",       "slim", train_slim, test_slim),
        ("xgb_2stage",       "full", train_full, test_full),
        ("xgb_single",       "slim", train_slim, test_slim),
        ("xgb_single",       "full", train_full, test_full),
        ("xgb_tiny_2stage",  "slim", train_slim, test_slim),
        ("xgb_tiny_2stage",  "full", train_full, test_full),
        ("xgb_tiny_single",  "slim", train_slim, test_slim),
        ("xgb_tiny_single",  "full", train_full, test_full),
        ("xgb_cv_2stage",    "slim", train_slim, test_slim),
        ("xgb_cv_2stage",    "full", train_full, test_full),
        ("xgb_cv_single",    "slim", train_slim, test_slim),
        ("xgb_cv_single",    "full", train_full, test_full),
    ]

    for arch, feat, train, test in configs:
        n_train = len(train[1])
        out = run_arch(arch, feat, train, test)
        seed_range = ""
        if len(out["preds_per_seed"]) > 1:
            mn = min(out["preds_per_seed"]); mx = max(out["preds_per_seed"])
            seed_range = f"[{mn:.4f}-{mx:.4f}]"
        print(f"  {arch:<22} {feat:<6} {n_train:>7} {out['brier']:>7.4f} "
              f"{out['mean']*100:>6.2f}% "
              f"{out['q5_w']:>2}-{out['q5_n']-out['q5_w']:<2} ({out['q5_r']*100:>4.1f}%)"
              f" {out['q1_w']:>2}-{out['q1_n']-out['q1_w']:<2} ({out['q1_r']*100:>4.1f}%)"
              f" {seed_range:>16}")

    print()
    print("  Read:")
    print("  - LR slim is the current production baseline.")
    print("  - XGBoost ships only if Brier improves >= 0.005 vs LR baseline AND")
    print("    seed range stays below 0.005 (i.e. result is stable, not lucky).")


if __name__ == "__main__":
    main()

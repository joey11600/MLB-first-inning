#!/usr/bin/env python3
"""
Three-split validation harness for the 2026 model-decay repair.

WHY THIS EXISTS.  The 2026-08-20 investigation found the shipped two-stage
LR has AUC ~0.52 in 2026 (in-sample 0.5208 / out-of-sample 0.5241 -- the
SAME, so it is not overfitting) against 0.54/0.57 on 2024/2025.  This
harness tests the repair for two identified causes together, because they
cannot be separated:

  1. COLLINEAR PAIR.  top3c_slg and top3c_iso correlate 0.90 and carry the
     model's two largest weights with OPPOSITE signs (T1 -0.327 / +0.345,
     net +0.019).  ISO = SLG - AVG, so they are near-redundant by
     construction.  Empirically away_top3c_slg has corr +0.0001 with the
     outcome while carrying the second-largest weight.

  2. PARK FACTOR.  data/fi_park_factors.json was built 2026-05-19 FROM
     picks_2026.csv, so its apparent skill was measured on its own
     training data (+0.690 in-sample vs -0.057 out-of-sample).  Park FI
     rates barely repeat year to year and the file is ~3x under-shrunk.

Park factors are rebuilt inside each split from the TRAINING seasons only
and never read from disk, so a result here cannot be contaminated the way
the shipped file was.

Usage:  python tools/refit2026/harness.py [--boot 2000]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Defaults mirror two_stage_model.py exactly, so a refit here is comparable
# to the shipped fit rather than to a differently-imputed one.
LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, LEAGUE_AVG_ISO = 4.17, 0.316, 0.407, 0.169
LEAGUE_AVG_XERA, LEAGUE_AVG_OPS, LEAGUE_NRFI_RATE = 4.20, 0.723, 0.50
WX_TEMP_DEFAULT, WX_WIND_DEFAULT, WX_HUMIDITY_DEFAULT = 20.0, 10.0, 60.0
NEUTRAL_PCT_RANK = 50.0

DEFAULTS = {
    "fi_park_nrfi_rate": 0.50, "home_plate_ump_nrfi_rate": LEAGUE_NRFI_RATE,
    "home_fip": LEAGUE_AVG_ERA, "away_fip": LEAGUE_AVG_ERA,
    "home_obp": LEAGUE_AVG_OBP, "away_obp": LEAGUE_AVG_OBP,
    "home_top3c_obp": LEAGUE_AVG_OBP, "away_top3c_obp": LEAGUE_AVG_OBP,
    "home_top3c_slg": LEAGUE_AVG_SLG, "away_top3c_slg": LEAGUE_AVG_SLG,
    "home_top3c_iso": LEAGUE_AVG_ISO, "away_top3c_iso": LEAGUE_AVG_ISO,
    "home_xera": LEAGUE_AVG_XERA, "away_xera": LEAGUE_AVG_XERA,
    "home_whiff_pct_rank": NEUTRAL_PCT_RANK, "away_whiff_pct_rank": NEUTRAL_PCT_RANK,
    "home_p_last5_pitcher_nrfi": LEAGUE_NRFI_RATE, "away_p_last5_pitcher_nrfi": LEAGUE_NRFI_RATE,
    "home_p_last10_pitcher_nrfi": LEAGUE_NRFI_RATE, "away_p_last10_pitcher_nrfi": LEAGUE_NRFI_RATE,
    "home_pvt_nrfi_rate": LEAGUE_NRFI_RATE, "away_pvt_nrfi_rate": LEAGUE_NRFI_RATE,
    "home_avg_ip_per_start": 5.0, "away_avg_ip_per_start": 5.0,
    "home_top3_ops_vs_oppHand": LEAGUE_AVG_OPS, "away_top3_ops_vs_oppHand": LEAGUE_AVG_OPS,
    "wx_temp_c": WX_TEMP_DEFAULT, "wx_wind_kmh": WX_WIND_DEFAULT,
    "wx_humidity": WX_HUMIDITY_DEFAULT, "wx_is_dome": 0.0,
}

# The shipped 19, verbatim from mlb_first_inning_predictor._T1/_B1_EXPECTED_FEATURES.
T1_SHIPPED = ["fi_park_nrfi_rate", "home_fip", "away_obp", "wx_temp_c", "wx_wind_kmh",
              "wx_humidity", "wx_is_dome", "home_p_last5_pitcher_nrfi", "away_top3c_obp",
              "home_plate_ump_nrfi_rate", "home_xera", "home_whiff_pct_rank", "era_gap_t1",
              "home_p_last10_pitcher_nrfi", "away_top3c_slg", "away_top3c_iso",
              "home_pvt_nrfi_rate", "home_avg_ip_per_start", "away_top3_ops_vs_oppHand"]
B1_SHIPPED = ["fi_park_nrfi_rate", "away_fip", "home_obp", "wx_temp_c", "wx_wind_kmh",
              "wx_humidity", "wx_is_dome", "away_p_last5_pitcher_nrfi", "home_top3c_obp",
              "home_plate_ump_nrfi_rate", "away_xera", "away_whiff_pct_rank", "era_gap_b1",
              "away_p_last10_pitcher_nrfi", "home_top3c_slg", "home_top3c_iso",
              "away_pvt_nrfi_rate", "away_avg_ip_per_start", "home_top3_ops_vs_oppHand"]


def drop(feats, *names):
    return [f for f in feats if f not in names]


# name -> (t1 feats, b1 feats, park shrinkage prior K, l2)
VARIANTS = {
    "shipped":          (T1_SHIPPED, B1_SHIPPED, 50, 0.05),
    "park309":          (T1_SHIPPED, B1_SHIPPED, 309, 0.05),
    "drop_iso":         (drop(T1_SHIPPED, "away_top3c_iso"),
                         drop(B1_SHIPPED, "home_top3c_iso"), 50, 0.05),
    "drop_slg":         (drop(T1_SHIPPED, "away_top3c_slg"),
                         drop(B1_SHIPPED, "home_top3c_slg"), 50, 0.05),
    "drop_iso+park309": (drop(T1_SHIPPED, "away_top3c_iso"),
                         drop(B1_SHIPPED, "home_top3c_iso"), 309, 0.05),
    "drop_slg+park309": (drop(T1_SHIPPED, "away_top3c_slg"),
                         drop(B1_SHIPPED, "home_top3c_slg"), 309, 0.05),
    "l2_10x":           (T1_SHIPPED, B1_SHIPPED, 50, 0.50),
    # only the features with a real univariate relationship in 2026
    "lean_pitching":    (["fi_park_nrfi_rate", "home_fip", "home_xera", "era_gap_t1",
                          "home_avg_ip_per_start", "away_top3c_obp", "wx_temp_c"],
                         ["fi_park_nrfi_rate", "away_fip", "away_xera", "era_gap_b1",
                          "away_avg_ip_per_start", "home_top3c_obp", "wx_temp_c"], 309, 0.05),
}


def load(path: Path, park_col: str, season: int) -> pd.DataFrame:
    d = pd.read_csv(path, low_memory=False)
    d = d[d["fi_total_runs"].notna()].copy()
    d["park"] = d[park_col]
    d["y"] = (d["fi_total_runs"] > 0).astype(int)
    d["y_t1"] = (d["fi_away_runs"] > 0).astype(int)     # top 1st: away bats
    d["y_b1"] = (d["fi_home_runs"] > 0).astype(int)     # bottom 1st: home bats
    d["season"] = season
    return d


def build_park(train: pd.DataFrame, K: float):
    """Bayesian-shrunk per-park first-inning NRFI rate, from TRAIN ONLY."""
    nrfi = (train["y"] == 0).astype(int)
    base = float(nrfi.mean())
    g = nrfi.groupby(train["park"]).agg(["size", "sum"])
    park_map = {p: (r["sum"] + K * base) / (r["size"] + K) for p, r in g.iterrows()}
    return park_map, base


def matrix(d: pd.DataFrame, feats, park_map: dict, park_base: float) -> np.ndarray:
    X = np.empty((len(d), len(feats)))
    h_era = pd.to_numeric(d.get("home_era"), errors="coerce").fillna(LEAGUE_AVG_ERA)
    a_era = pd.to_numeric(d.get("away_era"), errors="coerce").fillna(LEAGUE_AVG_ERA)
    for j, f in enumerate(feats):
        if f == "fi_park_nrfi_rate":
            v = d["park"].map(park_map).fillna(park_base)
        elif f == "era_gap_t1":
            v = h_era - a_era
        elif f == "era_gap_b1":
            v = a_era - h_era
        elif f in d.columns:
            v = pd.to_numeric(d[f], errors="coerce").fillna(DEFAULTS.get(f, 0.0))
        else:
            v = pd.Series(DEFAULTS.get(f, 0.0), index=d.index)
        X[:, j] = v.values
    return X


def fit_lr(X, y, l2=0.05, iters=300):
    """Standardised logistic regression via Newton steps.  The intercept is
    never penalised -- penalising it would bias the base rate, which is the
    one thing the shipped model already gets wrong."""
    mu, sd = X.mean(0), X.std(0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]
    w = np.zeros(Z.shape[1])
    R = np.eye(Z.shape[1]) * l2
    R[0, 0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (y - p) / len(y) - R @ w
        H = (Z * (p * (1 - p))[:, None]).T @ Z / len(y) + R + 1e-8 * np.eye(Z.shape[1])
        step = np.linalg.solve(H, g)
        w += step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w, mu, sd


def predict(w, mu, sd, X):
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]
    return 1 / (1 + np.exp(-Z @ w))


def auc(y, p):
    y = np.asarray(y); p = np.asarray(p)
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(p).rank().values
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def logloss(y, p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def run_variant(train: pd.DataFrame, test: pd.DataFrame, name: str) -> np.ndarray:
    t1f, b1f, K, l2 = VARIANTS[name]
    park_map, base = build_park(train, K)
    wt, mt, st = fit_lr(matrix(train, t1f, park_map, base), train["y_t1"].values, l2)
    wb, mb, sb = fit_lr(matrix(train, b1f, park_map, base), train["y_b1"].values, l2)
    pt = predict(wt, mt, st, matrix(test, t1f, park_map, base))
    pb = predict(wb, mb, sb, matrix(test, b1f, park_map, base))
    return 1 - (1 - pt) * (1 - pb)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260820)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    bt = ROOT / "data" / "backtests"
    d24 = load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024)
    d25 = load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025)
    d26 = load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)
    print(f"coverage: 2024 n={len(d24)}  2025 n={len(d25)}  2026 n={len(d26)}")
    print(f"YRFI base: 2024 {d24.y.mean():.4f}  2025 {d25.y.mean():.4f}  "
          f"2026 {d26.y.mean():.4f}\n")

    splits = [("2024 -> 2025", d24, d25),
              ("2025 -> 2024", d25, d24),
              ("2024+2025 -> 2026", pd.concat([d24, d25], ignore_index=True), d26)]

    results = {}
    for lab, tr, te in splits:
        y = te["y"].values
        preds = {n: run_variant(tr, te, n) for n in VARIANTS}
        results[lab] = (y, preds)
        print("=" * 100)
        print(f"SPLIT {lab}   (train n={len(tr)}, test n={len(te)})")
        print(f"  {'variant':<18} {'AUC':>7} {'vs shipped (90% CI)':>26}  "
              f"{'logloss':>8} {'Brier':>7} {'bias':>8}")
        base_auc = auc(y, preds["shipped"])
        for n, p in preds.items():
            a = auc(y, p)
            if n == "shipped":
                cmp = "(baseline)"
            else:
                d = np.array([auc(y[i], p[i]) - auc(y[i], preds["shipped"][i])
                              for i in (rng.integers(0, len(y), len(y))
                                        for _ in range(args.boot))])
                cmp = (f"{a - base_auc:+.4f} "
                       f"[{np.nanpercentile(d, 5):+.4f},{np.nanpercentile(d, 95):+.4f}]")
            print(f"  {n:<18} {a:>7.4f} {cmp:>26}  {logloss(y, p):>8.5f} "
                  f"{brier(y, p):>7.5f} {p.mean() - y.mean():>+8.4f}")

    print("\n" + "=" * 100)
    print("VERDICT -- a variant must beat 'shipped' on AUC in ALL THREE splits to qualify")
    print(f"  {'variant':<18} " + " ".join(f"{l:>18}" for l, _, _ in splits) + "   all 3?")
    for n in VARIANTS:
        if n == "shipped":
            continue
        deltas = [auc(results[l][0], results[l][1][n])
                  - auc(results[l][0], results[l][1]["shipped"]) for l, _, _ in splits]
        ok = all(d > 0 for d in deltas)
        print(f"  {n:<18} " + " ".join(f"{d:>+18.4f}" for d in deltas)
              + f"   {'YES' if ok else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

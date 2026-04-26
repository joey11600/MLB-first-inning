#!/usr/bin/env python3
"""
lr_baseline.py -- Logistic regression baseline for NRFI prediction.

Reads a backtest CSV (with raw model inputs as columns), fits an L2-regularized
logistic regression mapping {pitcher stats, offense stats, park factor} -> P(NRFI),
and evaluates on a held-out CSV.  Reports Brier, log loss, calibration table,
feature weights, and by-quintile win rates -- so we can tell whether the
hand-tuned multiplicative formula was the bottleneck or whether the inputs
themselves don't have enough signal.

Usage:
  python lr_baseline.py --train data/backtests/backtest_2024-...csv \
                        --test  data/backtests/backtest_2025-...csv
  python lr_baseline.py --train ...csv --test ...csv --l2 0.1 --include-lambda
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
    from scipy.optimize import minimize
except ImportError:
    sys.exit("Missing dependencies: pip install numpy scipy")


# Feature sets.
# legacy   = the original 19 hand-formula inputs.
# lineup   = legacy + prior-year top-of-order + empirical FI park factor.
# v1       = the 4-feature production model (FI park + home FIP/HR9/BB9).
# pitcherN = v1 + pitcher last-N first-inning history (5 + 10).
# current  = v1 + current-season top-3 stats.
# weather  = v1 + weather features.
# full     = v1 + all of the above.

LEGACY_FEATURES: list[str] = [
    "away_era", "home_era",
    "away_whip", "home_whip",
    "away_fip", "home_fip",
    "away_bb9", "home_bb9",
    "away_hr9", "home_hr9",
    "away_k9",  "home_k9",
    "away_obp", "home_obp",
    "away_slg", "home_slg",
    "away_rpg", "home_rpg",
    "park_factor",
]

LINEUP_EXTRAS: list[str] = [
    "away_top3_obp", "home_top3_obp",
    "away_top3_slg", "home_top3_slg",
    "away_top3_iso", "home_top3_iso",
    "fi_park_nrfi_rate",
]

LINEUP_FEATURES: list[str] = LEGACY_FEATURES + LINEUP_EXTRAS

# v1: the production 4-feature model (prior-year pitcher stats)
V1_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    "home_fip", "home_hr9", "home_bb9",
]

# v1away: v1 + away pitcher (raw, no blending) -- the bilateral version
V1_AWAY_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    "home_fip", "home_hr9", "home_bb9",
    "away_fip", "away_hr9", "away_bb9",
]

# v1fi: v1 + first-inning-specific pitcher splits (the NRFI-specific signal)
V1_FI_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    "home_fip", "home_hr9", "home_bb9",
    "home_fi_era", "home_fi_whip",
    "away_fi_era", "away_fi_whip",
]

# v1bilat: v1 + away pitcher full-season + first-inning splits
V1_BILAT_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    "home_fip", "home_hr9", "home_bb9",
    "away_fip", "away_hr9", "away_bb9",
    "home_fi_era", "away_fi_era",
]

# v2: structurally complete bilateral model -- both pitchers + both offenses + park
# Top-1 = home pitcher vs away offense.  Bot-1 = away pitcher vs home offense.
V2_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    # T1: home pitcher vs away offense
    "home_fip", "home_hr9", "home_bb9",
    "away_obp", "away_slg",
    # B1: away pitcher vs home offense
    "away_fip", "away_hr9", "away_bb9",
    "home_obp", "home_slg",
]

# v2 + handedness extras (platoon splits, LHB counts, pitcher throws_l)
V2_HAND_FEATURES: list[str] = V2_FEATURES + [
    "away_top3_platoon",  "home_top3_platoon",
    "away_top3_lhb",      "home_top3_lhb",
    "away_top3_switch",   "home_top3_switch",
    "away_pitcher_throws_l", "home_pitcher_throws_l",
]

# v2 + pitcher first-inning recency (last 5 / last 10 game NRFI rate)
V2_RECENCY_FEATURES: list[str] = V2_FEATURES + [
    "away_p_last5_game_nrfi",     "home_p_last5_game_nrfi",
    "away_p_last5_pitcher_nrfi",  "home_p_last5_pitcher_nrfi",
    "away_p_last10_game_nrfi",    "home_p_last10_game_nrfi",
    "away_p_last10_pitcher_nrfi", "home_p_last10_pitcher_nrfi",
]

# v2 + current-season top-of-order stats (point-in-time, not prior-year)
V2_TOP3CUR_FEATURES: list[str] = V2_FEATURES + [
    "away_top3c_obp", "home_top3c_obp",
    "away_top3c_iso", "home_top3c_iso",
]

# v2 + weather
V2_WEATHER_FEATURES: list[str] = V2_FEATURES + [
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
]

# v2 + day-game flag (afternoon games score ~0.3 fewer runs/game on average)
V2_DAYGAME_FEATURES: list[str] = V2_FEATURES + ["is_day_game"]

# v2 + everything we have
V2_ALL_FEATURES: list[str] = V2_FEATURES + [
    # handedness
    "away_top3_platoon", "home_top3_platoon",
    "away_top3_lhb",     "home_top3_lhb",
    "away_top3_switch",  "home_top3_switch",
    "away_pitcher_throws_l", "home_pitcher_throws_l",
    # pitcher recency
    "away_p_last5_pitcher_nrfi",  "home_p_last5_pitcher_nrfi",
    "away_p_last10_pitcher_nrfi", "home_p_last10_pitcher_nrfi",
    # current top-3
    "away_top3c_obp", "home_top3c_obp",
    "away_top3c_iso", "home_top3c_iso",
    # weather
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
]

# v1-blend: same shape, but pitcher stats are blended with current-season-to-date
V1_BLEND_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    "home_fip_blend", "home_hr9_blend", "home_bb9_blend",
]

# v1-blend + away pitcher (full bilateral)
V1_BLEND_AWAY_FEATURES: list[str] = [
    "fi_park_nrfi_rate",
    "home_fip_blend", "home_hr9_blend", "home_bb9_blend",
    "away_fip_blend", "away_hr9_blend", "away_bb9_blend",
]

# Pitcher recency (last 5 + last 10 first-inning history)
PITCHER_RECENCY_EXTRAS: list[str] = [
    "away_p_last5_game_nrfi",     "home_p_last5_game_nrfi",
    "away_p_last5_pitcher_nrfi",  "home_p_last5_pitcher_nrfi",
    "away_p_last10_game_nrfi",    "home_p_last10_game_nrfi",
    "away_p_last10_pitcher_nrfi", "home_p_last10_pitcher_nrfi",
]

# Current-season top-3 stats (point-in-time)
CURRENT_TOP3_EXTRAS: list[str] = [
    "away_top3c_obp", "home_top3c_obp",
    "away_top3c_iso", "home_top3c_iso",
]

WEATHER_EXTRAS: list[str] = [
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
]

PITCHER_RECENCY_FEATURES = V1_FEATURES + PITCHER_RECENCY_EXTRAS
CURRENT_FEATURES         = V1_FEATURES + CURRENT_TOP3_EXTRAS
WEATHER_FEATURES         = V1_FEATURES + WEATHER_EXTRAS
FULL_FEATURES            = V1_FEATURES + PITCHER_RECENCY_EXTRAS + CURRENT_TOP3_EXTRAS + WEATHER_EXTRAS

HAND_EXTRAS: list[str] = [
    "away_top3_platoon",  "home_top3_platoon",
    "away_top3_lhb",      "home_top3_lhb",
    "away_top3_switch",   "home_top3_switch",
    "away_pitcher_throws_l", "home_pitcher_throws_l",
]

V1_HAND_FEATURES: list[str] = V1_FEATURES + HAND_EXTRAS

FEATURE_SETS = {
    "legacy":      LEGACY_FEATURES,
    "lineup":      LINEUP_FEATURES,
    "v1":          V1_FEATURES,
    "v1away":      V1_AWAY_FEATURES,
    "v1fi":        V1_FI_FEATURES,
    "v1bilat":     V1_BILAT_FEATURES,
    "v2":          V2_FEATURES,
    "v2hand":      V2_HAND_FEATURES,
    "v2recency":   V2_RECENCY_FEATURES,
    "v2top3cur":   V2_TOP3CUR_FEATURES,
    "v2weather":   V2_WEATHER_FEATURES,
    "v2daygame":   V2_DAYGAME_FEATURES,
    "v2all":       V2_ALL_FEATURES,
    "v1blend":     V1_BLEND_FEATURES,
    "v1blendaway": V1_BLEND_AWAY_FEATURES,
    "v1hand":      V1_HAND_FEATURES,
    "pitcherN":    PITCHER_RECENCY_FEATURES,
    "current":     CURRENT_FEATURES,
    "weather":     WEATHER_FEATURES,
    "full":        FULL_FEATURES,
    "all":         FULL_FEATURES,
}

# Sensible league-mean defaults so missing values (None / "") become valid floats.
# This lets early-season pitchers with no prior starts and domed-park games
# stay in the dataset.
FEATURE_DEFAULTS: dict[str, float] = {
    # Pitcher last-N: 0.50 = NRFI/YRFI coin flip
    "away_p_last5_game_nrfi": 0.50,  "home_p_last5_game_nrfi": 0.50,
    "away_p_last5_pitcher_nrfi": 0.50, "home_p_last5_pitcher_nrfi": 0.50,
    "away_p_last10_game_nrfi": 0.50, "home_p_last10_game_nrfi": 0.50,
    "away_p_last10_pitcher_nrfi": 0.50, "home_p_last10_pitcher_nrfi": 0.50,
    # Weather: league-typical evening conditions; 0/0/0 for indoor games
    "wx_temp_c":   20.0,
    "wx_wind_kmh": 10.0,
    "wx_wind_deg": 180.0,
    "wx_humidity": 60.0,
    "wx_is_dome":  0.0,
    # Top-3 current: fall back to league averages
    "away_top3c_obp": 0.318, "home_top3c_obp": 0.318,
    "away_top3c_slg": 0.414, "home_top3c_slg": 0.414,
    "away_top3c_iso": 0.169, "home_top3c_iso": 0.169,
    # First-inning splits: fall back to overall ERA/WHIP league avg when missing
    "away_fi_era":  4.20, "home_fi_era":  4.20,
    "away_fi_whip": 1.25, "home_fi_whip": 1.25,
    "away_fi_ip":   0.0,  "home_fi_ip":   0.0,
    # Handedness: platoon split = 0 (no advantage), 1 LHB out of 3, 0 switch
    "away_top3_platoon":     0.0, "home_top3_platoon":     0.0,
    "away_top3_lhb":         1.0, "home_top3_lhb":         1.0,
    "away_top3_switch":      0.0, "home_top3_switch":      0.0,
    "away_pitcher_throws_l": 0.0, "home_pitcher_throws_l": 0.0,
}

DEFAULT_FEATURES: list[str] = FULL_FEATURES


# ---------------------------------------------------------------------------
# Logistic regression (L2-regularized, fit via L-BFGS)
# ---------------------------------------------------------------------------

class LogReg:
    """
    L2-regularized logistic regression for binary classification.
    Stores feature means/stds for inference-time standardization.
    """

    def __init__(self, weights, bias, feature_names, mean, std):
        self.w = np.asarray(weights, dtype=float)
        self.b = float(bias)
        self.feature_names = list(feature_names)
        self.mean = np.asarray(mean, dtype=float)
        self.std  = np.asarray(std,  dtype=float)

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, feature_names: list[str],
            l2: float = 0.01) -> "LogReg":
        """X: (n, d) features.  y: (n,) labels in {0, 1}."""
        n, d = X.shape
        if y.shape != (n,):
            raise ValueError("y must have shape (n,)")
        if len(feature_names) != d:
            raise ValueError("feature_names must have length d")

        # Standardize -- zero mean, unit variance.  Greatly improves L-BFGS
        # convergence on heterogeneous-scale features (era ~4, k9 ~9, obp ~0.3).
        mean = X.mean(axis=0)
        std  = X.std(axis=0) + 1e-9
        Xn   = (X - mean) / std

        def neg_log_lik(params: np.ndarray) -> float:
            w = params[:d]
            b = params[d]
            z = Xn @ w + b
            # log(sigmoid(z)) and log(1 - sigmoid(z)) -- numerically stable
            log_p   = -np.logaddexp(0.0, -z)
            log_1_p = -np.logaddexp(0.0,  z)
            nll     = -float(np.sum(y * log_p + (1.0 - y) * log_1_p))
            reg     = 0.5 * l2 * float(np.sum(w * w))
            return nll + reg

        def grad(params: np.ndarray) -> np.ndarray:
            w = params[:d]
            b = params[d]
            z = Xn @ w + b
            p = 1.0 / (1.0 + np.exp(-z))
            err = p - y
            gw  = Xn.T @ err + l2 * w
            gb  = float(np.sum(err))
            return np.concatenate([gw, [gb]])

        x0 = np.zeros(d + 1)
        result = minimize(
            neg_log_lik, x0, jac=grad, method="L-BFGS-B",
            options={"maxiter": 500, "gtol": 1e-7},
        )
        if not result.success:
            print(f"  [warn] L-BFGS did not fully converge: {result.message}")

        w_norm = result.x[:d]
        b_norm = result.x[d]

        return cls(w_norm, b_norm, feature_names, mean, std)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Xn = (X - self.mean) / self.std
        z = Xn @ self.w + self.b
        return 1.0 / (1.0 + np.exp(-z))

    def predict_proba_one(self, features: list[float]) -> float:
        """Predict P(class=1) for a single feature vector (no numpy required at call site)."""
        x = np.asarray(features, dtype=float).reshape(1, -1)
        return float(self.predict_proba(x)[0])

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "weights":       self.w.tolist(),
                "bias":          self.b,
                "feature_names": self.feature_names,
                "mean":          self.mean.tolist(),
                "std":           self.std.tolist(),
            }, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "LogReg":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            weights       = d["weights"],
            bias          = d["bias"],
            feature_names = d["feature_names"],
            mean          = d["mean"],
            std           = d["std"],
        )


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def _coerce_feature(raw_value: str, feature_name: str) -> float | None:
    """Convert a CSV cell to float; fall back to FEATURE_DEFAULTS for empty/None.
       Returns None when the value is unparseable AND no default exists."""
    if raw_value is None or raw_value == "":
        if feature_name in FEATURE_DEFAULTS:
            return FEATURE_DEFAULTS[feature_name]
        return None
    try:
        return float(raw_value)
    except ValueError:
        if feature_name in FEATURE_DEFAULTS:
            return FEATURE_DEFAULTS[feature_name]
        return None


def load_dataset(path: Path, features: list[str], include_lambda: bool = False
                 ) -> tuple[np.ndarray, np.ndarray, list[str], list[dict]]:
    """
    Returns (X, y, used_features, raw_rows).
    Missing values for features in FEATURE_DEFAULTS are filled with the default
    (so we don't drop early-season pitchers, domed-park games, etc.).
    Rows with truly unfillable missing features are skipped.
    """
    feats = list(features)
    if include_lambda and "lambda_total" not in feats:
        feats.append("lambda_total")

    X_rows: list[list[float]] = []
    y_rows: list[int]         = []
    raw:    list[dict]        = []
    skipped = 0
    fills_used = 0

    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = r.get("actual_side", "")
            if actual not in ("NRFI", "YRFI"):
                skipped += 1
                continue
            vec: list[float] = []
            ok = True
            for k in feats:
                v = _coerce_feature(r.get(k, ""), k)
                if v is None:
                    ok = False
                    break
                if (r.get(k, "") == "" or r.get(k) is None) and k in FEATURE_DEFAULTS:
                    fills_used += 1
                vec.append(v)
            if not ok:
                skipped += 1
                continue
            X_rows.append(vec)
            y_rows.append(1 if actual == "NRFI" else 0)
            raw.append(r)

    if skipped:
        print(f"  [{path.name}]  skipped {skipped} rows (missing feature/outcome)")
    if fills_used:
        print(f"  [{path.name}]  filled {fills_used} missing values from FEATURE_DEFAULTS")

    return (
        np.asarray(X_rows, dtype=float),
        np.asarray(y_rows, dtype=int),
        feats,
        raw,
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    eps = 1e-12
    p = np.clip(p, eps, 1.0 - eps)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def calibration_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10
                     ) -> list[tuple[str, int, float, float]]:
    out = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        avg_pred = float(p[mask].mean())
        obs      = float(y[mask].mean())
        out.append((f"{lo:.2f}-{hi:.2f}", n, avg_pred, obs))
    return out


def quintile_table(p: np.ndarray, y: np.ndarray, n_q: int = 5
                   ) -> list[tuple[int, int, float, float, float]]:
    """Sort by predicted prob, split into N quantiles, show NRFI rate per quantile."""
    n = len(p)
    order = np.argsort(p)
    out = []
    per = n // n_q
    for q in range(n_q):
        lo = q * per
        hi = (q + 1) * per if q < n_q - 1 else n
        idx = order[lo:hi]
        avg_p = float(p[idx].mean())
        obs   = float(y[idx].mean())
        # If we always-bet NRFI in this quintile: win when obs == NRFI rate
        out.append((q + 1, len(idx), avg_p, obs, obs))
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(model: LogReg, p_test: np.ndarray, y_test: np.ndarray,
                 features: list[str], train_n: int) -> None:
    sep  = "=" * 64
    sep2 = "-" * 64

    base = float(y_test.mean())  # NRFI rate in test set
    climatology_brier = base * (1 - base)
    h = base
    climatology_ll = -(h * math.log(max(1e-12, h)) + (1 - h) * math.log(max(1e-12, 1 - h)))

    print(f"\n{sep}")
    print(f"  LOGISTIC REGRESSION BASELINE")
    print(sep)
    print(f"  Train N             : {train_n}")
    print(f"  Test  N             : {len(y_test)}")
    print(f"  Test NRFI rate      : {base*100:.1f}%")
    print(f"  Mean predicted NRFI : {p_test.mean()*100:.1f}%   "
          f"(model bias = {(p_test.mean() - base)*100:+.1f}pp)")
    print(f"  Brier score         : {brier(p_test, y_test):.4f}   "
          f"(climatology = {climatology_brier:.4f})")
    print(f"  Log loss            : {log_loss(p_test, y_test):.4f}   "
          f"(climatology = {climatology_ll:.4f})")

    print(f"\n{sep2}")
    print(f"  Predicted-probability range : "
          f"{p_test.min()*100:.1f}% - {p_test.max()*100:.1f}%")
    print(f"  Std of predictions          : {p_test.std()*100:.2f}pp "
          f"(spread = how much discrimination)")

    print(f"\n{sep2}")
    print(f"  Calibration  (predicted NRFI% -> observed NRFI rate):")
    print(f"    {'bin':<12}  {'n':>4}  {'pred':>7}  {'obs':>7}   drift")
    for label, n, avg_p, obs in calibration_bins(p_test, y_test, n_bins=10):
        drift = obs - avg_p
        sign = "+" if drift >= 0 else ""
        print(f"    {label:<12}  {n:>4}  "
              f"{avg_p*100:>6.1f}%  {obs*100:>6.1f}%   "
              f"{sign}{drift*100:>5.1f}pp")

    print(f"\n{sep2}")
    print(f"  By predicted-probability quintile (Q1=lowest pred NRFI, Q5=highest):")
    print(f"    {'q':>2}  {'n':>4}  {'avg pred':>9}  {'obs NRFI':>9}  vs base")
    for q, n, avg_p, obs, _ in quintile_table(p_test, y_test, n_q=5):
        delta = obs - base
        sign = "+" if delta >= 0 else ""
        print(f"    Q{q}  {n:>4}  {avg_p*100:>8.1f}%  {obs*100:>8.1f}%  "
              f"{sign}{delta*100:>5.1f}pp")

    print(f"\n{sep2}")
    print(f"  Feature weights (in standardized space; |w| ranks importance):")
    pairs = sorted(
        zip(features, model.w), key=lambda p: -abs(p[1])
    )
    print(f"    {'feature':<20}  {'weight':>8}  effect on NRFI prob")
    for name, w in pairs:
        # Positive weight on standardized feature -> higher value pushes toward NRFI
        effect = "->NRFI" if w > 0 else "->YRFI"
        if abs(w) < 0.02:
            effect = "(near zero)"
        print(f"    {name:<20}  {w:>+8.3f}  {effect}")

    print(sep)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Logistic regression baseline on backtest features.",
    )
    parser.add_argument("--train", nargs="+", required=True,
                        help="Training backtest CSV(s) -- multiple are concatenated")
    parser.add_argument("--test",  default=None,
                        help="Held-out backtest CSV (omit when only fitting for production)")
    parser.add_argument("--l2", type=float, default=0.01,
                        help="L2 regularization (default: 0.01)")
    parser.add_argument("--include-lambda", action="store_true",
                        help="Include lambda_total as a feature")
    parser.add_argument("--features", nargs="+", default=None,
                        help="Override default feature list")
    parser.add_argument("--feature-set", choices=list(FEATURE_SETS.keys()),
                        default="lineup",
                        help="Predefined feature set (default: lineup)")
    parser.add_argument("--save", metavar="PATH",
                        help="Save fitted LR model JSON for production use")
    parser.add_argument("--save-calibration", metavar="PATH",
                        help="Fit ProbCalibrator on test-set predictions and save to PATH "
                             "(requires --test). Calibration is applied at inference time "
                             "to correct systematic over/under-prediction.")
    parser.add_argument("--cal-bins", type=int, default=20,
                        help="Quantile bins for the probability calibrator (default: 20)")
    args = parser.parse_args()

    train_paths = [Path(p) for p in args.train]
    test_path   = Path(args.test) if args.test else None

    if args.features:
        features = args.features
    else:
        features = FEATURE_SETS[args.feature_set]

    X_tr_parts: list[np.ndarray] = []
    y_tr_parts: list[np.ndarray] = []
    used_feats = features
    for p in train_paths:
        print(f"\nLoading train: {p.name}")
        X_p, y_p, feats, _ = load_dataset(p, features, args.include_lambda)
        print(f"  N={len(y_p)}  features={len(feats)}")
        if X_p.size:
            X_tr_parts.append(X_p)
            y_tr_parts.append(y_p)
            used_feats = feats
    X_tr = np.vstack(X_tr_parts)
    y_tr = np.concatenate(y_tr_parts)
    print(f"\nTotal training N={len(y_tr)}  features={len(used_feats)}")

    print(f"\nFitting LR (L2={args.l2})...")
    model = LogReg.fit(X_tr, y_tr, used_feats, l2=args.l2)

    p_tr = model.predict_proba(X_tr)
    print(f"\nIn-sample (train) Brier  : {brier(p_tr, y_tr):.4f}")

    if test_path is not None:
        print(f"\nLoading test : {test_path.name}")
        X_te, y_te, _, _ = load_dataset(test_path, features, args.include_lambda)
        print(f"  N={len(y_te)}")
        p_te = model.predict_proba(X_te)
        print(f"Out-of-sample (test) Brier: {brier(p_te, y_te):.4f}")
        print_report(model, p_te, y_te, used_feats, train_n=len(y_tr))

    if args.save:
        model.save(args.save)
        print(f"\nSaved model -> {args.save}")
        print(f"  Features ({len(used_feats)}): {used_feats}")

    if args.save_calibration:
        if test_path is None:
            sys.exit("--save-calibration requires --test (need OOS predictions to fit on)")
        from calibration import ProbCalibrator
        cal = ProbCalibrator.fit(
            predictions   = p_te.tolist(),
            actuals       = y_te.tolist(),
            n_bins        = args.cal_bins,
            train_seasons = [test_path.stem],
        )
        cal.save(args.save_calibration)
        print(f"\nSaved calibration -> {args.save_calibration}")
        print(f"  Bins: {len(cal.centers)}, fit on N={cal.train_n}")
        # Sanity: in-sample Brier on the SAME test set after calibration
        p_cal = np.array([cal.predict(float(p)) for p in p_te])
        print(f"  Test Brier  raw -> calibrated:  "
              f"{brier(p_te, y_te):.4f} -> {brier(p_cal, y_te):.4f}")
        print(f"  Mean pred   raw -> calibrated:  "
              f"{p_te.mean()*100:.1f}% -> {p_cal.mean()*100:.1f}%   "
              f"(actual {y_te.mean()*100:.1f}%)")
        print(f"  Calibration curve:")
        print(f"    {'pred bin':>10}    {'observed':>9}")
        for c, r in zip(cal.centers, cal.rates):
            print(f"    {c*100:>9.1f}%    {r*100:>8.1f}%")


if __name__ == "__main__":
    main()

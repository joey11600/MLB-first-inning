#!/usr/bin/env python3
"""
calibration.py -- Empirical lambda -> P(NRFI) calibration.

Why this exists:
  The 2024 + 2025 backtests showed that first-inning runs are strongly
  overdispersed -- way more zeros AND way more big innings than Poisson(lambda)
  predicts.  Treating the raw model lambda as if `prob_nrfi(lam) = exp(-lam)`
  systematically under-predicts NRFI by 9-15pp.

  This module fits a non-parametric isotonic mapping from model lambda to
  actual NRFI probability using historical backtest data.  The fitted curve
  preserves the model's *relative ranking* of games while producing
  honest absolute probabilities.

Pick zones (calibrated probability space):
  These replace the raw-lambda thresholds when a calibrator is in use.
  Symmetric around 50/50 with explicit edge requirements.

  P(NRFI) >= 0.60         -> STRONG NRFI
  0.53 <= P(NRFI) < 0.60  -> LEAN NRFI
  0.47 <= P(NRFI) < 0.53  -> PASS
  0.40 <= P(NRFI) < 0.47  -> LEAN YRFI
  P(NRFI) < 0.40          -> STRONG YRFI

CLI:
  python calibration.py --csv data/backtests/backtest_2024-04-01_to_2024-09-30.csv
  python calibration.py --csv X.csv --csv Y.csv --out data/calibration.json --bins 20
"""

import argparse
import csv
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Pick zones (calibrated probability space)
# ---------------------------------------------------------------------------

CAL_STRONG_NRFI_P = 0.60
CAL_LEAN_NRFI_P   = 0.53
CAL_PASS_LO_P     = 0.47
CAL_LEAN_YRFI_P   = 0.40
# implied:
#   STRONG YRFI: P(NRFI) < 0.40
#   LEAN YRFI:   0.40 <= P(NRFI) < 0.47
#   PASS:        0.47 <= P(NRFI) < 0.53
#   LEAN NRFI:   0.53 <= P(NRFI) < 0.60
#   STRONG NRFI: P(NRFI) >= 0.60


def classify_pick_calibrated(p_nrfi: float, data_pts: int = 4) -> tuple[str, str]:
    """
    Classify a game using calibrated NRFI probability.
    Mirrors classify_pick() signature but operates on probability, not lambda.
    """
    if data_pts == 0:
        return "PASS", "NO DATA"
    if p_nrfi >= CAL_STRONG_NRFI_P:
        return "NRFI", "STRONG"
    if p_nrfi >= CAL_LEAN_NRFI_P:
        return "NRFI", "LEAN"
    if p_nrfi >= CAL_PASS_LO_P:
        return "PASS", "NO EDGE"
    if p_nrfi >= CAL_LEAN_YRFI_P:
        return "YRFI", "LEAN"
    return "YRFI", "STRONG"


# ---------------------------------------------------------------------------
# Pool-Adjacent-Violators (isotonic regression)
# ---------------------------------------------------------------------------

def _pav(values: list[float], weights: list[int], increasing: bool) -> list[float]:
    """
    Generic Pool-Adjacent-Violators isotonic regression, weighted.
    `increasing=True`  enforces non-decreasing output (use case: predicted
                       probability up -> observed rate up).
    `increasing=False` enforces non-increasing output (use case: lambda up
                       -> NRFI rate down).
    """
    if not values:
        return []
    pools: list[tuple[float, int, list[int]]] = [
        (v, w, [i]) for i, (v, w) in enumerate(zip(values, weights))
    ]
    def violates(v_left: float, v_right: float) -> bool:
        return (v_right < v_left) if increasing else (v_right > v_left)

    i = 0
    while i < len(pools) - 1:
        v1, w1, idx1 = pools[i]
        v2, w2, idx2 = pools[i + 1]
        if violates(v1, v2):
            new_v   = (v1 * w1 + v2 * w2) / (w1 + w2)
            new_w   = w1 + w2
            new_idx = idx1 + idx2
            pools[i] = (new_v, new_w, new_idx)
            del pools[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    out = [0.0] * len(values)
    for v, _w, idx_list in pools:
        for j in idx_list:
            out[j] = v
    return out


def _pav_decreasing(values: list[float], weights: list[int]) -> list[float]:
    """Backward-compatible wrapper around _pav for lambda-space calibration."""
    return _pav(values, weights, increasing=False)


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------

class LambdaCalibrator:
    """
    Empirical lambda -> P(NRFI) mapping.

    fit(): bins training samples by lambda quantile, computes per-bin NRFI
    rate, and runs PAV to enforce monotonic-decreasing structure.

    predict(): linear interpolation between bin centers.  Outside training
    range, extrapolates flatly (returns first/last bin's rate).
    """

    def __init__(self, bin_centers: list[float], bin_rates: list[float],
                 train_n: int = 0, train_seasons: list[str] | None = None):
        if len(bin_centers) != len(bin_rates):
            raise ValueError("bin_centers and bin_rates must be same length")
        # Ensure centers are sorted (they should be from .fit())
        order = sorted(range(len(bin_centers)), key=lambda i: bin_centers[i])
        self.centers = [bin_centers[i] for i in order]
        self.rates   = [bin_rates[i]   for i in order]
        self.train_n        = train_n
        self.train_seasons  = train_seasons or []

    @classmethod
    def fit(cls, lambdas: list[float], actuals: list[int],
            n_bins: int = 20, train_seasons: list[str] | None = None
            ) -> "LambdaCalibrator":
        """
        Fit calibration on training data.

        lambdas:  model lambda values
        actuals:  1 if game was NRFI, 0 if YRFI
        n_bins:   number of equal-count quantile bins
        """
        if len(lambdas) != len(actuals):
            raise ValueError("lambdas and actuals must be same length")
        n = len(lambdas)
        if n < n_bins:
            raise ValueError(f"Need at least {n_bins} samples to fit {n_bins} bins")

        pairs   = sorted(zip(lambdas, actuals), key=lambda p: p[0])
        per_bin = n // n_bins

        centers: list[float] = []
        rates:   list[float] = []
        weights: list[int]   = []

        for i in range(n_bins):
            lo = i * per_bin
            hi = (i + 1) * per_bin if i < n_bins - 1 else n
            chunk = pairs[lo:hi]
            if not chunk:
                continue
            center = sum(p[0] for p in chunk) / len(chunk)
            rate   = sum(p[1] for p in chunk) / len(chunk)
            centers.append(center)
            rates.append(rate)
            weights.append(len(chunk))

        smoothed = _pav_decreasing(rates, weights)
        return cls(
            bin_centers   = centers,
            bin_rates     = smoothed,
            train_n       = n,
            train_seasons = train_seasons,
        )

    def predict(self, lam: float) -> float:
        """Calibrated P(NRFI) for a model lambda."""
        if not self.centers:
            return 0.5
        if lam <= self.centers[0]:
            return self.rates[0]
        if lam >= self.centers[-1]:
            return self.rates[-1]
        # Binary search the bracket
        lo, hi = 0, len(self.centers) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.centers[mid] <= lam:
                lo = mid
            else:
                hi = mid
        c0, c1 = self.centers[lo], self.centers[hi]
        r0, r1 = self.rates[lo],   self.rates[hi]
        if c1 == c0:
            return (r0 + r1) / 2
        t = (lam - c0) / (c1 - c0)
        return r0 + t * (r1 - r0)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "centers":       self.centers,
                "rates":         self.rates,
                "train_n":       self.train_n,
                "train_seasons": self.train_seasons,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "LambdaCalibrator":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            bin_centers   = d["centers"],
            bin_rates     = d["rates"],
            train_n       = d.get("train_n", 0),
            train_seasons = d.get("train_seasons", []),
        )


# ---------------------------------------------------------------------------
# Probability-space calibrator (for the LR-v2 model)
# ---------------------------------------------------------------------------

class ProbCalibrator:
    """
    Isotonic-regression calibration in *probability* space, monotonic
    increasing: as the model's predicted P(NRFI) goes up, the observed
    NRFI rate should also go up.

    Use case: V2's raw output systematically over-predicts NRFI by
    ~3pp at every probability bin.  Fitting this on (V2 prediction,
    actual_outcome) pairs produces a corrective lookup that pulls
    each bin toward its observed rate.

    File format (data/calibration_v2.json):
      {
        "centers": [...],     # bin midpoint of model predictions, sorted
        "rates":   [...],     # PAV-smoothed observed NRFI rate per bin
        "train_n": int,
        "train_seasons": ["2025"],
      }
    """

    def __init__(self, bin_centers: list[float], bin_rates: list[float],
                 train_n: int = 0, train_seasons: list[str] | None = None):
        if len(bin_centers) != len(bin_rates):
            raise ValueError("bin_centers and bin_rates must be same length")
        order = sorted(range(len(bin_centers)), key=lambda i: bin_centers[i])
        self.centers = [bin_centers[i] for i in order]
        self.rates   = [bin_rates[i]   for i in order]
        self.train_n        = train_n
        self.train_seasons  = train_seasons or []

    @classmethod
    def fit(cls, predictions: list[float], actuals: list[int],
            n_bins: int = 20, train_seasons: list[str] | None = None
            ) -> "ProbCalibrator":
        if len(predictions) != len(actuals):
            raise ValueError("predictions and actuals must be same length")
        n = len(predictions)
        if n < n_bins:
            raise ValueError(f"Need >= {n_bins} samples to fit {n_bins} bins")

        pairs   = sorted(zip(predictions, actuals), key=lambda p: p[0])
        per_bin = n // n_bins

        centers: list[float] = []
        rates:   list[float] = []
        weights: list[int]   = []

        for i in range(n_bins):
            lo = i * per_bin
            hi = (i + 1) * per_bin if i < n_bins - 1 else n
            chunk = pairs[lo:hi]
            if not chunk:
                continue
            center = sum(p[0] for p in chunk) / len(chunk)
            rate   = sum(p[1] for p in chunk) / len(chunk)
            centers.append(center)
            rates.append(rate)
            weights.append(len(chunk))

        smoothed = _pav(rates, weights, increasing=True)
        return cls(
            bin_centers   = centers,
            bin_rates     = smoothed,
            train_n       = n,
            train_seasons = train_seasons,
        )

    def predict(self, p: float) -> float:
        """Calibrated P(NRFI) given a raw model prediction."""
        if not self.centers:
            return p
        if p <= self.centers[0]:
            return self.rates[0]
        if p >= self.centers[-1]:
            return self.rates[-1]
        lo, hi = 0, len(self.centers) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.centers[mid] <= p:
                lo = mid
            else:
                hi = mid
        c0, c1 = self.centers[lo], self.centers[hi]
        r0, r1 = self.rates[lo],   self.rates[hi]
        if c1 == c0:
            return (r0 + r1) / 2
        t = (p - c0) / (c1 - c0)
        return r0 + t * (r1 - r0)

    def predict_with_band(self, p: float) -> tuple[float, dict]:
        """Calibrated P(NRFI) plus band/flat-zone metadata about where
        the input landed in the isotonic curve.

        Returned dict contains:
          band       : "below_min" / "above_max" / "bin_<lo>-<hi>"
          is_flat    : True if flat_size >= 3 (multiple bins share rate)
          flat_size  : number of consecutive bins around (lo, hi) that
                       share the same rate as r[lo] (leftward) and r[hi]
                       (rightward).  Picks landing in a high flat_size
                       region are ones the calibrator cannot meaningfully
                       distinguish from neighboring games -- at the
                       margin, STRONG picks here are guessing.
          flat_rate  : the shared rate, if is_flat; else None

        Used by the predictor's flat-zone guard to demote ambiguous
        STRONG picks, and by tools/pick_reasoning_log.py for the per-
        pick diagnostic JSON.
        """
        c, r = self.centers, self.rates
        if not c:
            return p, {"band": "no_calibrator", "is_flat": False, "flat_size": 0, "flat_rate": None}
        if p <= c[0]:
            return r[0], {"band": "below_min", "is_flat": False, "flat_size": 0, "flat_rate": None}
        if p >= c[-1]:
            return r[-1], {"band": "above_max", "is_flat": False, "flat_size": 0, "flat_rate": None}
        lo, hi = 0, len(c) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if c[mid] <= p:
                lo = mid
            else:
                hi = mid
        if c[hi] == c[lo]:
            return (r[lo] + r[hi]) / 2, {"band": f"bin_{lo}-{hi}", "is_flat": False, "flat_size": 0, "flat_rate": None}
        # Walk outward to measure the flat zone surrounding (lo, hi).
        flat_left = lo
        flat_right = hi
        while flat_left > 0 and abs(r[flat_left - 1] - r[lo]) < 1e-9:
            flat_left -= 1
        while flat_right < len(r) - 1 and abs(r[flat_right + 1] - r[hi]) < 1e-9:
            flat_right += 1
        flat_size = flat_right - flat_left + 1
        t = (p - c[lo]) / (c[hi] - c[lo])
        cal_p = r[lo] + t * (r[hi] - r[lo])
        return cal_p, {
            "band":       f"bin_{lo}-{hi}",
            "is_flat":    flat_size >= 3,
            "flat_size":  flat_size,
            "flat_rate":  r[lo] if flat_size >= 3 else None,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "centers":       self.centers,
                "rates":         self.rates,
                "train_n":       self.train_n,
                "train_seasons": self.train_seasons,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "ProbCalibrator":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            bin_centers   = d["centers"],
            bin_rates     = d["rates"],
            train_n       = d.get("train_n", 0),
            train_seasons = d.get("train_seasons", []),
        )


# ---------------------------------------------------------------------------
# Fit CLI
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> tuple[list[float], list[int], str]:
    """Returns (lambdas, actuals, season_label)."""
    lambdas: list[float] = []
    actuals: list[int]   = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                lam = float(r["lambda_total"])
            except (KeyError, ValueError):
                continue
            actual = r.get("actual_side", "")
            if actual not in ("NRFI", "YRFI"):
                continue
            lambdas.append(lam)
            actuals.append(1 if actual == "NRFI" else 0)
    # season label = first 4 chars of first date if present
    label = path.stem
    if "_" in label:
        # backtest_2024-04-01_to_2024-09-30 -> 2024
        for tok in label.split("_"):
            if len(tok) >= 4 and tok[:4].isdigit():
                label = tok[:4]
                break
    return lambdas, actuals, label


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit empirical lambda -> P(NRFI) calibration from backtest CSV(s).",
    )
    parser.add_argument("--csv", action="append", required=True,
                        help="Backtest CSV path (repeatable)")
    parser.add_argument("--out", default="data/calibration.json",
                        help="Output JSON path (default: data/calibration.json)")
    parser.add_argument("--bins", type=int, default=20,
                        help="Number of quantile bins (default: 20)")
    args = parser.parse_args()

    all_lambdas: list[float] = []
    all_actuals: list[int]   = []
    seasons:     list[str]   = []

    for path_s in args.csv:
        path = Path(path_s)
        if not path.exists():
            sys.exit(f"CSV not found: {path}")
        lams, acts, label = _load_csv(path)
        print(f"  Loaded {len(lams):>5} graded games from {path.name}  (label={label})")
        all_lambdas.extend(lams)
        all_actuals.extend(acts)
        seasons.append(label)

    if not all_lambdas:
        sys.exit("No usable rows found in any CSV.")

    print(f"\nFitting on N={len(all_lambdas)} games  |  bins={args.bins}  "
          f"|  seasons={seasons}")

    cal = LambdaCalibrator.fit(
        lambdas       = all_lambdas,
        actuals       = all_actuals,
        n_bins        = args.bins,
        train_seasons = seasons,
    )

    out_path = Path(args.out)
    cal.save(out_path)

    # Print fitted curve
    print(f"\nFitted calibration  ->  {out_path}")
    print(f"  {'lambda':>8}    {'P(NRFI)':>8}")
    for c, r in zip(cal.centers, cal.rates):
        print(f"  {c:>8.3f}    {r:>8.3f}")

    # Sanity: re-apply to training data and report Brier
    sse  = 0.0
    n    = len(all_lambdas)
    for lam, actual in zip(all_lambdas, all_actuals):
        p = cal.predict(lam)
        sse += (p - actual) ** 2
    print(f"\nIn-sample Brier on training data: {sse / n:.4f}")
    actual_nrfi_rate = sum(all_actuals) / n
    climatology = actual_nrfi_rate * (1 - actual_nrfi_rate)
    print(f"Climatology baseline             : {climatology:.4f}  "
          f"(NRFI rate {actual_nrfi_rate*100:.1f}%)")


if __name__ == "__main__":
    main()

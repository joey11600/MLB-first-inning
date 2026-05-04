"""
tools/v4_calibrators.py -- T4-V4 candidate calibrators.

NEW FILE.  Lives entirely in tools/.  Does NOT modify production
`calibration.py` -- the production ProbCalibrator class is unchanged.

Provides two calibrator classes used by v4 candidate variants:

  * PlattCalibrator (variant v4-platt)
      Two-parameter sigmoid mapping raw P(NRFI) -> calibrated P(NRFI).
      Fit by maximum-likelihood logistic regression on (raw_p, actual_nrfi)
      pairs.  More robust to small-N than isotonic because it imposes a
      monotone smooth functional form (no local wiggles, no jumps at bin
      boundaries).

  * WeightedProbCalibrator (variant v4-recency)
      Same isotonic/PAV/quantile-bin family as the production
      ProbCalibrator, but accepts per-sample weights at fit time.  Used
      to apply exponential recency decay so 2025 data weighs more
      heavily than 2024.

Both classes save/load JSON files in distinct formats so production code
(which only knows ProbCalibrator's centers+rates JSON) cannot accidentally
load one of these.  Production stays on data/calibration_v2.json.

These classes have NO side effects on import.  They're pure helpers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Variant v4-platt: two-parameter sigmoid Platt calibrator
# ---------------------------------------------------------------------------

class PlattCalibrator:
    """
    Sigmoid calibration: cal(p) = 1 / (1 + exp(-(a + b * p))).

    Fit by maximum-likelihood / weighted logistic regression with L-BFGS-
    style coordinate descent (gradient-only Newton-Raphson on a 2-D
    surface, since we only have two scalars to optimize).

    The Platt family is more constrained than isotonic regression:
      - it CANNOT produce non-monotone calibration curves
      - it has no local "bins" so a single anomaly in one prob region
        can't produce a wiggle there
      - it's defined on the entire (0, 1) interval -- no flat extrapolation
        outside training range

    Trade-off: if the true calibration curve is genuinely non-sigmoid,
    Platt forces a worse fit than isotonic.  For our case (probabilities
    clustered between 0.36-0.66 with smooth-looking deviations) this is
    likely a good fit.
    """

    def __init__(self, a: float, b: float, train_n: int = 0,
                 train_seasons: list[str] | None = None):
        self.a = float(a)
        self.b = float(b)
        self.train_n = int(train_n)
        self.train_seasons = list(train_seasons or [])

    @staticmethod
    def _sigmoid(z: float) -> float:
        # Numerically safe sigmoid.
        if z >= 0:
            ez = math.exp(-z)
            return 1.0 / (1.0 + ez)
        ez = math.exp(z)
        return ez / (1.0 + ez)

    @classmethod
    def fit(cls, predictions: Sequence[float], actuals: Sequence[int],
            train_seasons: list[str] | None = None,
            max_iter: int = 200, tol: float = 1e-7) -> "PlattCalibrator":
        """
        Fit (a, b) by Newton-Raphson on the Bernoulli log-likelihood of
        sigmoid(a + b*p) given (p, y) pairs.  Two-parameter so the
        Hessian is 2x2 -- closed-form invert each step.
        """
        if len(predictions) != len(actuals):
            raise ValueError("predictions and actuals must be same length")
        n = len(predictions)
        if n < 2:
            raise ValueError("need at least 2 samples")

        a, b = 0.0, 1.0  # initial guess: identity-ish mapping
        for _ in range(max_iter):
            # First and second derivatives of negative log-likelihood
            #   L = -sum [ y * log(s) + (1-y) * log(1-s) ]
            #   where s = sigmoid(a + b*p)
            #   dL/da = sum (s - y)
            #   dL/db = sum (s - y) * p
            #   d2L/da2  = sum s*(1-s)
            #   d2L/dadb = sum s*(1-s) * p
            #   d2L/db2  = sum s*(1-s) * p^2
            ga = 0.0
            gb = 0.0
            haa = 0.0
            hab = 0.0
            hbb = 0.0
            for p, y in zip(predictions, actuals):
                s = cls._sigmoid(a + b * p)
                err = s - y
                w = s * (1.0 - s)
                ga  += err
                gb  += err * p
                haa += w
                hab += w * p
                hbb += w * p * p
            # Newton step: solve H * dx = -g
            det = haa * hbb - hab * hab
            if abs(det) < 1e-12:
                # Singular Hessian -- fall back to a small gradient step.
                a -= 0.01 * ga
                b -= 0.01 * gb
                continue
            da = -(hbb * ga - hab * gb) / det
            db = -(-hab * ga + haa * gb) / det
            a += da
            b += db
            if abs(da) + abs(db) < tol:
                break

        return cls(a=a, b=b, train_n=n, train_seasons=train_seasons)

    def predict(self, p: float) -> float:
        return self._sigmoid(self.a + self.b * p)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "type":          "platt",
                "a":             self.a,
                "b":             self.b,
                "train_n":       self.train_n,
                "train_seasons": self.train_seasons,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "PlattCalibrator":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("type") != "platt":
            raise ValueError(f"{path} is not a Platt calibrator (type={d.get('type')})")
        return cls(
            a=d["a"],
            b=d["b"],
            train_n=d.get("train_n", 0),
            train_seasons=d.get("train_seasons", []),
        )


# ---------------------------------------------------------------------------
# Variant v4-recency: weighted isotonic ProbCalibrator
# ---------------------------------------------------------------------------

def _pav_weighted(values: list[float], weights: list[float],
                  increasing: bool = True) -> list[float]:
    """
    Pool-Adjacent-Violators isotonic regression with continuous weights.
    Mirrors the production calibration._pav helper but accepts float
    weights (production version takes ints) so we can plug in
    exponential-decay sample weights.
    """
    if not values:
        return []
    pools: list[tuple[float, float, list[int]]] = [
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


class WeightedProbCalibrator:
    """
    Isotonic-regression calibration in probability space with per-sample
    weights.  Same wire format as production ProbCalibrator (centers +
    rates JSON) -- but the bin centers/rates are computed from
    weight-normalized chunks so a sample with weight=2 contributes twice
    as much to its bin's center and rate as a weight=1 sample.

    Rationale (variant v4-recency): exponential decay (e.g. half-life
    one year) lets recent seasons dominate the calibrator without
    discarding older data entirely.  Same monotone family as production,
    just with a recency prior.
    """

    def __init__(self, bin_centers: list[float], bin_rates: list[float],
                 train_n: int = 0, train_seasons: list[str] | None = None,
                 weight_decay: float | None = None):
        if len(bin_centers) != len(bin_rates):
            raise ValueError("bin_centers and bin_rates must be same length")
        order = sorted(range(len(bin_centers)), key=lambda i: bin_centers[i])
        self.centers = [bin_centers[i] for i in order]
        self.rates   = [bin_rates[i]   for i in order]
        self.train_n        = int(train_n)
        self.train_seasons  = list(train_seasons or [])
        self.weight_decay   = weight_decay

    @classmethod
    def fit(cls, predictions: Sequence[float], actuals: Sequence[int],
            weights: Sequence[float], n_bins: int = 20,
            train_seasons: list[str] | None = None,
            weight_decay: float | None = None) -> "WeightedProbCalibrator":
        """
        Fit weighted-isotonic calibration.

        Equal-count quantile bins on `predictions` (sorted), then
        per-bin: weighted mean of predictions (center) + weighted mean
        of actuals (rate).  PAV-smooth the rate sequence.
        """
        if not (len(predictions) == len(actuals) == len(weights)):
            raise ValueError("predictions, actuals, weights must be same length")
        n = len(predictions)
        if n < n_bins:
            raise ValueError(f"Need >= {n_bins} samples to fit {n_bins} bins")

        # Sort by prediction so quantile bins are dense in p-space.
        triples = sorted(zip(predictions, actuals, weights), key=lambda t: t[0])
        per_bin = n // n_bins

        centers: list[float] = []
        rates:   list[float] = []
        bin_w:   list[float] = []
        for i in range(n_bins):
            lo = i * per_bin
            hi = (i + 1) * per_bin if i < n_bins - 1 else n
            chunk = triples[lo:hi]
            if not chunk:
                continue
            wsum = sum(t[2] for t in chunk)
            if wsum <= 0:
                continue
            center = sum(t[0] * t[2] for t in chunk) / wsum
            rate   = sum(t[1] * t[2] for t in chunk) / wsum
            centers.append(center)
            rates.append(rate)
            bin_w.append(wsum)

        smoothed = _pav_weighted(rates, bin_w, increasing=True)
        return cls(
            bin_centers   = centers,
            bin_rates     = smoothed,
            train_n       = n,
            train_seasons = train_seasons,
            weight_decay  = weight_decay,
        )

    def predict(self, p: float) -> float:
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

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "type":          "weighted_isotonic",
                "centers":       self.centers,
                "rates":         self.rates,
                "train_n":       self.train_n,
                "train_seasons": self.train_seasons,
                "weight_decay":  self.weight_decay,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "WeightedProbCalibrator":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("type") != "weighted_isotonic":
            raise ValueError(
                f"{path} is not a WeightedProbCalibrator (type={d.get('type')})"
            )
        return cls(
            bin_centers   = d["centers"],
            bin_rates     = d["rates"],
            train_n       = d.get("train_n", 0),
            train_seasons = d.get("train_seasons", []),
            weight_decay  = d.get("weight_decay"),
        )

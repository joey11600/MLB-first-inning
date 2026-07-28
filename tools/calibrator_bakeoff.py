#!/usr/bin/env python3
"""
tools/calibrator_bakeoff.py -- compare candidate probability calibrators
out-of-sample, and measure how much "plateau mass" each one produces.

WHY THIS EXISTS (2026-07-27 investigation)
------------------------------------------
The production calibrator (data/calibration_v2.json, ProbCalibrator =
20 equal-count quantile bins -> PAV isotonic -> linear interp between
BIN CENTERS) develops wide flat steps.  PAV pools adjacent bins whenever
they violate monotonicity, and with ~147 games/bin the per-bin noise
(~4pp standard error) swamps the true bin-to-bin signal (~1-2pp) through
the middle of the distribution.  Pooled bins share one rate, and
interpolating between two centers that share a rate is FLAT.

Live consequence: bins 1-3 all mapped to calibrated NRFI 0.40639
(= YRFI 0.5936).  106 placed bets across 98 DISTINCT lambda values got
that identical probability.  Because 0.5936 clears the STRONG gate
(_LR_PASS_LO_P = 0.44), every game the model could not tell apart became
an automatic STRONG bet: 107 graded, 51.4% hit vs 54.9% market-implied,
-7.18u.  The plateau IS the leak.

CANDIDATES
----------
  iso20   current production: PAV, 20 bins, interp between bin centers
  iso40   same, 40 bins
  cir20   Centered Isotonic Regression (Oron & Flournoy 2017): PAV, then
          collapse to pools and interpolate between POOL CENTROIDS.  Keeps
          monotonicity, removes plateaus by construction.
  cir40   CIR with 40 bins
  platt   Platt scaling: 2-parameter logistic on logit(raw).  Smooth,
          cannot plateau, but cannot capture non-logistic shape either.
  blend   0.5 * cir20 + 0.5 * platt

EVALUATION -- the CLAUDE.md 3-split out-of-sample rule, no exceptions:
    2024        -> 2025
    2025        -> 2024
    2024 + 2025 -> 2026
A candidate that only wins in one direction is rejected.

Metrics: Brier, log loss, ECE (expected calibration error), and
plateau mass = share of holdout games whose calibrated probability is
shared by >=3% of the sample (i.e. landed on a flat step).

Usage:
    python tools/calibrator_bakeoff.py
    python tools/calibrator_bakeoff.py --money    # add the 2026 P&L test
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calibration import ProbCalibrator, _pav  # noqa: E402
import recalibrate_v2 as rc  # noqa: E402

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"

# STRONG YRFI fires when calibrated P(NRFI) < this.  Mirrors
# mlb_first_inning_predictor._LR_PASS_LO_P.
PASS_LO_P = 0.44


# ---------------------------------------------------------------------------
# Candidate calibrators
# ---------------------------------------------------------------------------

class CIRCalibrator:
    """Centered Isotonic Regression.

    Same PAV backbone as ProbCalibrator, but after pooling we collapse each
    PAV pool to a single (weighted-mean-x, pooled-y) knot and interpolate
    between KNOTS rather than between original bin centers.  Two adjacent
    bins that PAV merged no longer produce a flat segment -- they produce
    one knot, and the curve ramps smoothly to the next knot.

    This is the minimal change that removes plateaus while preserving the
    non-parametric shape and the monotonicity guarantee.
    """

    def __init__(self, centers, rates, train_n=0, train_seasons=None):
        order = sorted(range(len(centers)), key=lambda i: centers[i])
        self.centers = [centers[i] for i in order]
        self.rates = [rates[i] for i in order]
        self.train_n = train_n
        self.train_seasons = train_seasons or []

    @classmethod
    def fit(cls, predictions, actuals, n_bins=20, train_seasons=None):
        n = len(predictions)
        pairs = sorted(zip(predictions, actuals), key=lambda p: p[0])
        per_bin = max(n // n_bins, 1)
        centers, rates, weights = [], [], []
        for i in range(n_bins):
            lo = i * per_bin
            hi = (i + 1) * per_bin if i < n_bins - 1 else n
            chunk = pairs[lo:hi]
            if not chunk:
                continue
            centers.append(sum(p[0] for p in chunk) / len(chunk))
            rates.append(sum(p[1] for p in chunk) / len(chunk))
            weights.append(len(chunk))

        smoothed = _pav(rates, weights, increasing=True)

        # Collapse consecutive bins that PAV gave the same rate into one
        # knot at their weight-weighted mean x.  This is what kills the
        # plateau: 3 pooled bins -> 1 knot, not 3 knots sharing a y.
        knot_x, knot_y = [], []
        i = 0
        while i < len(smoothed):
            j = i
            while j + 1 < len(smoothed) and abs(smoothed[j + 1] - smoothed[i]) < 1e-12:
                j += 1
            wsum = sum(weights[i:j + 1])
            xbar = sum(centers[k] * weights[k] for k in range(i, j + 1)) / wsum
            knot_x.append(xbar)
            knot_y.append(smoothed[i])
            i = j + 1

        return cls(knot_x, knot_y, train_n=n, train_seasons=train_seasons)

    def predict(self, p: float) -> float:
        c, r = self.centers, self.rates
        if not c:
            return p
        if len(c) == 1:
            return r[0]
        if p <= c[0]:
            return r[0]
        if p >= c[-1]:
            return r[-1]
        lo, hi = 0, len(c) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if c[mid] <= p:
                lo = mid
            else:
                hi = mid
        c0, c1, r0, r1 = c[lo], c[hi], r[lo], r[hi]
        if c1 == c0:
            return (r0 + r1) / 2
        return r0 + (p - c0) / (c1 - c0) * (r1 - r0)

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "centers": self.centers,
                "rates": self.rates,
                "train_n": self.train_n,
                "train_seasons": self.train_seasons,
                "kind": "cir",
            }, f, indent=2)


class PlattCalibrator:
    """2-parameter logistic recalibration: sigmoid(a * logit(p) + b).

    Fit by Newton-Raphson on the binomial log-likelihood.  Smooth and
    monotone by construction, so plateau mass is structurally zero.
    """

    def __init__(self, a=1.0, b=0.0, train_n=0, train_seasons=None):
        self.a, self.b = float(a), float(b)
        self.train_n = train_n
        self.train_seasons = train_seasons or []

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))

    @classmethod
    def fit(cls, predictions, actuals, train_seasons=None, iters=100):
        z = cls._logit(predictions)
        y = np.asarray(actuals, dtype=float)
        a, b = 1.0, 0.0
        for _ in range(iters):
            eta = a * z + b
            mu = 1.0 / (1.0 + np.exp(-eta))
            w = np.clip(mu * (1 - mu), 1e-9, None)
            resid = y - mu
            # 2x2 Newton step
            g = np.array([np.sum(resid * z), np.sum(resid)])
            H = np.array([
                [-np.sum(w * z * z), -np.sum(w * z)],
                [-np.sum(w * z), -np.sum(w)],
            ])
            try:
                step = np.linalg.solve(H, -g)
            except np.linalg.LinAlgError:
                break
            a, b = a + step[0], b + step[1]
            if np.max(np.abs(step)) < 1e-10:
                break
        return cls(a, b, train_n=len(y), train_seasons=train_seasons)

    def predict(self, p: float) -> float:
        z = float(self._logit([p])[0])
        return float(1.0 / (1.0 + np.exp(-(self.a * z + self.b))))

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"a": self.a, "b": self.b, "train_n": self.train_n,
                       "train_seasons": self.train_seasons, "kind": "platt"}, f, indent=2)


class BlendCalibrator:
    """Average of a CIR curve and a Platt curve.  CIR supplies shape,
    Platt supplies smoothness and stability in sparse tails."""

    def __init__(self, cir, platt, w=0.5):
        self.cir, self.platt, self.w = cir, platt, w

    @classmethod
    def fit(cls, predictions, actuals, n_bins=20, train_seasons=None):
        return cls(
            CIRCalibrator.fit(predictions, actuals, n_bins, train_seasons),
            PlattCalibrator.fit(predictions, actuals, train_seasons),
        )

    def predict(self, p: float) -> float:
        return self.w * self.cir.predict(p) + (1 - self.w) * self.platt.predict(p)


CANDIDATES = {
    "iso20 (current)": lambda P, Y, s: ProbCalibrator.fit(list(P), list(Y), 20, s),
    "iso40":           lambda P, Y, s: ProbCalibrator.fit(list(P), list(Y), 40, s),
    "cir20":           lambda P, Y, s: CIRCalibrator.fit(list(P), list(Y), 20, s),
    "cir40":           lambda P, Y, s: CIRCalibrator.fit(list(P), list(Y), 40, s),
    "platt":           lambda P, Y, s: PlattCalibrator.fit(list(P), list(Y), s),
    "blend(cir20+platt)": lambda P, Y, s: BlendCalibrator.fit(list(P), list(Y), 20, s),
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p, y, bins=10):
    """Expected calibration error over equal-count bins."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(p)
    p, y = p[order], y[order]
    n = len(p)
    per = max(n // bins, 1)
    tot = 0.0
    for i in range(bins):
        lo, hi = i * per, (i + 1) * per if i < bins - 1 else n
        if hi <= lo:
            continue
        seg_p, seg_y = p[lo:hi], y[lo:hi]
        tot += len(seg_p) * abs(seg_p.mean() - seg_y.mean())
    return float(tot / n)


def plateau_mass(p, min_share=0.03):
    """Share of predictions whose exact calibrated value is shared by at
    least `min_share` of the sample -- i.e. games sitting on a flat step
    where the calibrator cannot discriminate."""
    p = np.round(np.asarray(p, dtype=float), 6)
    n = len(p)
    c = Counter(p.tolist())
    return float(sum(k for k in c.values() if k >= max(min_share * n, 2)) / n)


def betting_zone_plateau(cal, lo=0.30, hi=0.50, steps=400):
    """Largest contiguous raw-input width that maps to a single calibrated
    output, scanned across the STRONG-YRFI decision region."""
    xs = np.linspace(lo, hi, steps)
    ys = np.array([round(cal.predict(float(x)), 6) for x in xs])
    best = cur = 0
    for i in range(1, len(ys)):
        cur = cur + 1 if ys[i] == ys[i - 1] else 0
        best = max(best, cur)
    return float(best * (hi - lo) / (steps - 1))


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def raw_preds_for(path: Path, kind: str, fi_park, t1, b1):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if kind == "backtest":
        Xt, Xb, y, _ = rc.gather_from_backtest(rows, fi_park)
    else:
        Xt, Xb, y, _ = rc.gather_from_picks(rows, fi_park)
    p = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    return np.asarray(p), np.asarray(y), rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--money", action="store_true",
                    help="also run the 2026 real-odds P&L test")
    args = ap.parse_args()

    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()

    print("Loading and running the frozen two-stage LR forward...")
    p24, y24, _ = raw_preds_for(BT_2024, "backtest", fi_park, t1, b1)
    p25, y25, _ = raw_preds_for(BT_2025, "backtest", fi_park, t1, b1)
    p26, y26, rows26 = raw_preds_for(PICKS_2026, "picks", fi_park, t1, b1)
    for lbl, p, y in (("2024", p24, y24), ("2025", p25, y25), ("2026", p26, y26)):
        print(f"  {lbl}: N={len(y):<5} raw mean {p.mean()*100:5.2f}%  "
              f"actual NRFI {y.mean()*100:5.2f}%  raw Brier {brier(p,y):.4f}")

    splits = [
        ("2024 -> 2025", (p24, y24, ["2024"]), (p25, y25)),
        ("2025 -> 2024", (p25, y25, ["2025"]), (p24, y24)),
        ("2024+2025 -> 2026",
         (np.concatenate([p24, p25]), np.concatenate([y24, y25]), ["2024", "2025"]),
         (p26, y26)),
    ]

    print("\n" + "=" * 92)
    print("  OUT-OF-SAMPLE CALIBRATOR BAKE-OFF  (lower Brier / logloss / ECE / plateau = better)")
    print("=" * 92)

    agg = {k: [] for k in CANDIDATES}
    for split_name, (Ptr, Ytr, seasons), (Pte, Yte) in splits:
        print(f"\n--- {split_name}   (train N={len(Ytr)}, test N={len(Yte)}) ---")
        print(f"  {'candidate':<22}{'Brier':>9}{'logloss':>10}{'ECE':>9}"
              f"{'plateau':>10}{'flat width':>12}")
        for name, fit in CANDIDATES.items():
            cal = fit(Ptr, Ytr, seasons)
            q = np.array([cal.predict(float(x)) for x in Pte])
            b, ll, e = brier(q, Yte), logloss(q, Yte), ece(q, Yte)
            pm, fw = plateau_mass(q), betting_zone_plateau(cal)
            agg[name].append((b, ll, e, pm))
            print(f"  {name:<22}{b:>9.5f}{ll:>10.5f}{e:>9.5f}"
                  f"{pm:>9.1%}{fw:>12.4f}")

    print("\n" + "=" * 92)
    print("  AVERAGE ACROSS ALL 3 SPLITS  (a candidate must win in every direction)")
    print("=" * 92)
    print(f"  {'candidate':<22}{'Brier':>9}{'logloss':>10}{'ECE':>9}{'plateau':>10}"
          f"   {'beats current on Brier?'}")
    base = np.mean([x[0] for x in agg["iso20 (current)"]])
    ranked = sorted(agg.items(), key=lambda kv: np.mean([x[0] for x in kv[1]]))
    for name, vals in ranked:
        b = np.mean([x[0] for x in vals])
        ll = np.mean([x[1] for x in vals])
        e = np.mean([x[2] for x in vals])
        pm = np.mean([x[3] for x in vals])
        wins = sum(1 for i, v in enumerate(vals)
                   if v[0] <= agg["iso20 (current)"][i][0] + 1e-12)
        tag = f"{wins}/3 splits" + ("  <-- WINNER" if name == ranked[0][0] else "")
        print(f"  {name:<22}{b:>9.5f}{ll:>10.5f}{e:>9.5f}{pm:>9.1%}   {tag}")
    print(f"\n  (current iso20 average Brier = {base:.5f})")

    if args.money:
        money_test(p26, y26, rows26, p24, y24, p25, y25)


# ---------------------------------------------------------------------------
# 2026 real-money test
# ---------------------------------------------------------------------------

def money_test(p26, y26, rows26, p24, y24, p25, y25):
    """Re-decide every 2026 pick under each calibrator (fit on 2024+2025
    ONLY, so 2026 is untouched holdout) and settle at the real captured
    DK price."""
    print("\n" + "=" * 92)
    print("  2026 MONEY TEST -- calibrators fit on 2024+2025 only, applied to live 2026 slate")
    print("=" * 92)

    graded = [r for r in rows26
              if (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")]
    if len(graded) != len(y26):
        print(f"  !! row/pred misalignment ({len(graded)} vs {len(y26)}) -- aborting money test")
        return

    def fnum(v):
        try:
            if v in (None, "", "None"):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    Ptr = np.concatenate([p24, p25])
    Ytr = np.concatenate([y24, y25])

    print(f"  {'candidate':<22}{'bets':>6}{'W':>5}{'L':>5}{'hit%':>8}"
          f"{'need':>7}{'P&L':>10}{'ROI%':>8}")
    for name, fit in CANDIDATES.items():
        cal = fit(Ptr, Ytr, ["2024", "2025"])
        n = w = 0
        pl = 0.0
        need = []
        for r, praw, actual in zip(graded, p26, y26):
            p_nrfi = cal.predict(float(praw))
            if p_nrfi >= PASS_LO_P:      # not a STRONG YRFI under this calibrator
                continue
            odds = fnum(r.get("market_yrfi_odds"))
            if odds is None:             # no real price captured -> excluded
                continue
            n += 1
            hit = (actual == 0)          # actual==0 means YRFI happened
            w += hit
            pl += (odds / 100.0 if odds > 0 else 100.0 / abs(odds)) if hit else -1.0
            need.append(abs(odds) / (abs(odds) + 100.0) if odds < 0
                        else 100.0 / (odds + 100.0))
        if n == 0:
            print(f"  {name:<22}{'0':>6}   (no qualifying bets)")
            continue
        print(f"  {name:<22}{n:>6}{w:>5}{n-w:>5}{100*w/n:>7.1f}%"
              f"{100*np.mean(need):>6.1f}%{pl:>+9.2f}u{100*pl/n:>7.2f}%")
    print("\n  NOTE: STRONG-YRFI gate held at the CURRENT p_nrfi < 0.44 for every")
    print("        candidate, so this isolates the calibrator's effect alone.")


if __name__ == "__main__":
    main()

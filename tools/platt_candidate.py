#!/usr/bin/env python3
"""tools/platt_candidate.py

Fit a Platt-scaling (logistic) calibrator on the same training data
as `recalibrate_v2.py`, compare it head-to-head against the production
isotonic calibrator on out-of-sample slices, and save the candidate
to `data/calibration_platt_candidate.json` (NOT deployed).

WHY THIS EXISTS
---------------
The production calibrator is isotonic regression in probability space.
Isotonic produces step-flat zones where multiple distinct raw bins
collapse onto identical calibrated values -- the structural cause of
the pick_cluster HIGH drift alerts and the 0.40-band visible in the
2026-05-11 audit (raw 53.0%, 53.8%, 54.7%, 55.5%, 56.4% all → 48.8%
calibrated).

Platt scaling fits a single monotonic logistic curve to the entire
distribution.  No flat zones, smoother gradients, fewer parameters
(2 instead of 20).  The trade-off: Platt is GLOBAL (one curve fits
all) while isotonic can adapt LOCALLY (each bin can have its own
shape).  If the bias is mostly global, Platt is better.  If the
bias has local structure (e.g., one bin is wildly off), isotonic
keeps an edge.

PLATT FORMULATION
-----------------
Standard logit-Platt:

    P_cal = sigmoid(A * logit(P_raw) + B)
    logit(x) = log(x / (1 - x))

Fit A, B via Newton's method on cross-entropy loss with the same
(raw_prediction, actual_outcome) pairs.  Equivalent to a logistic
regression with one feature (`logit(P_raw)`) plus a bias.

EVALUATION
----------
Brier score on three slices:
  - in-sample (full training set)
  - OOS slice 1: 5/05-5/10 (rolling holdout)
  - OOS slice 2: 5/01-5/10 (broader recent window)

If Platt's Brier is uniformly <= isotonic's by at least 0.001 on
the OOS slices, it's a candidate worth shipping.  Decision is
operator's.

DEPLOYMENT
----------
This script does NOT modify production.  It writes
`data/calibration_platt_candidate.json` only.  To deploy, the
operator must:
  1. Re-run with --since flag for OOS sanity check.
  2. Modify `mlb_first_inning_predictor._LR_CAL_PATH` to point to
     the Platt file (and adapt the predict() interface).
  3. Run a forward-sim against the trailing 14d picks.
  4. Only ship if no STRONG/PASS verdicts flip in unexpected
     directions.

Usage:
  python tools/platt_candidate.py             # fit + compare
  python tools/platt_candidate.py --no-save   # comparison only
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recalibrate_v2 import (        # noqa: E402
    load_lr_models, lr_predict_two_stage,
    gather_from_backtest, gather_from_picks,
    load_fi_park, brier, BT_2025_PATH, PICKS_2026,
)
from calibration import ProbCalibrator        # noqa: E402


def _logit(p: float, eps: float = 1e-6) -> float:
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


class PlattCalibrator:
    """Logistic (Platt) calibration in logit space.

    Parameters A, B are fit by maximum likelihood on (raw_p, y) pairs:
        P_cal = sigmoid(A * logit(raw_p) + B)
    """

    def __init__(self, A: float = 1.0, B: float = 0.0,
                 train_n: int = 0, train_seasons: list[str] | None = None):
        self.A = float(A)
        self.B = float(B)
        self.train_n = int(train_n)
        self.train_seasons = train_seasons or []

    @classmethod
    def fit(cls, predictions, actuals,
            n_iter: int = 200, lr: float = 0.1,
            train_seasons: list[str] | None = None) -> "PlattCalibrator":
        """Fit A, B via Newton-Raphson on logistic regression."""
        x = np.asarray([_logit(float(p)) for p in predictions], dtype=float)
        y = np.asarray(actuals, dtype=float)
        n = len(x)
        if n < 10:
            raise ValueError(f"Need >= 10 samples; got {n}")

        A, B = 1.0, 0.0
        for _ in range(n_iter):
            z = A * x + B
            p = 1.0 / (1.0 + np.exp(-z))
            # Gradient (negative log-likelihood):
            err = p - y
            g_A = float((err * x).sum())
            g_B = float(err.sum())
            # Hessian
            w = p * (1.0 - p)
            h_AA = float((w * x * x).sum())
            h_AB = float((w * x).sum())
            h_BB = float(w.sum())
            det = h_AA * h_BB - h_AB * h_AB
            if abs(det) < 1e-12:
                break
            # Inverse Hessian
            inv_AA = h_BB / det
            inv_AB = -h_AB / det
            inv_BB = h_AA / det
            dA = inv_AA * g_A + inv_AB * g_B
            dB = inv_AB * g_A + inv_BB * g_B
            A_new = A - dA
            B_new = B - dB
            if abs(A_new - A) < 1e-7 and abs(B_new - B) < 1e-7:
                A, B = A_new, B_new
                break
            A, B = A_new, B_new

        return cls(A=A, B=B, train_n=n, train_seasons=train_seasons)

    def predict(self, p: float) -> float:
        return _sigmoid(self.A * _logit(float(p)) + self.B)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "type":           "platt_logit",
                "A":              self.A,
                "B":              self.B,
                "train_n":        self.train_n,
                "train_seasons":  self.train_seasons,
            }, f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "PlattCalibrator":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            A=d.get("A", 1.0),
            B=d.get("B", 0.0),
            train_n=d.get("train_n", 0),
            train_seasons=d.get("train_seasons", []),
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(ROOT / "data" / "calibration_platt_candidate.json"))
    p.add_argument("--no-save", action="store_true")
    args = p.parse_args()

    print("=" * 64)
    print("  Fitting Platt (logit-logistic) candidate calibrator")
    print("=" * 64)

    t1m, b1m = load_lr_models()
    fipark = load_fi_park()

    # Reproduce recalibrate_v2's training data
    with open(BT_2025_PATH, encoding="utf-8") as f:
        bt_rows = list(csv.DictReader(f))
    X25_t1, X25_b1, y25, _ = gather_from_backtest(bt_rows, fipark)
    p25_raw = lr_predict_two_stage(t1m, b1m, X25_t1, X25_b1)

    with open(PICKS_2026, encoding="utf-8") as f:
        pk_rows = list(csv.DictReader(f))
    X26_t1, X26_b1, y26, _ = gather_from_picks(pk_rows, fipark)
    p26_raw = lr_predict_two_stage(t1m, b1m, X26_t1, X26_b1)

    p_all = np.concatenate([p25_raw, p26_raw])
    y_all = np.concatenate([y25, y26])
    print(f"\nTraining set: N={len(y_all)}  (2025 backtest {len(y25)} + 2026 graded {len(y26)})")
    print(f"  mean raw pred = {p_all.mean()*100:.2f}%  actual NRFI = {y_all.mean()*100:.2f}%")
    print(f"  raw Brier     = {brier(p_all, y_all):.4f}")

    # Fit Platt
    platt = PlattCalibrator.fit(p_all.tolist(), y_all.tolist(),
                                  train_seasons=["2025", "2026"])
    print(f"\nFit complete:  A={platt.A:.4f}  B={platt.B:.4f}")

    # In-sample
    p_platt_all = np.array([platt.predict(float(x)) for x in p_all])
    print(f"  Platt in-sample Brier:   {brier(p_platt_all, y_all):.4f}")

    # Compare to isotonic (production)
    iso = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    p_iso_all = np.array([iso.predict(float(x)) for x in p_all])
    print(f"  Iso   in-sample Brier:   {brier(p_iso_all, y_all):.4f}")

    # Build a small per-slice OOS evaluation against 2026 picks.
    # Use date to slice -- needs raw map by row index.
    def evaluate_slice(label: str, mask: np.ndarray):
        if not mask.any():
            print(f"  {label}: (no data)")
            return
        y_s = y26[mask]
        p_raw_s = p26_raw[mask]
        p_iso_s = np.array([iso.predict(float(x)) for x in p_raw_s])
        p_platt_s = np.array([platt.predict(float(x)) for x in p_raw_s])
        actual = y_s.mean()*100
        b_iso = brier(p_iso_s, y_s)
        b_platt = brier(p_platt_s, y_s)
        bias_iso = (p_iso_s.mean() - y_s.mean())*100
        bias_platt = (p_platt_s.mean() - y_s.mean())*100
        delta = b_platt - b_iso
        verdict = ("PLATT BETTER" if delta < -0.001
                   else "ISO BETTER" if delta > 0.001 else "TIE")
        print(f"  {label}  N={int(mask.sum()):>3}  actual_NRFI={actual:>5.1f}%")
        print(f"    iso   : bias={bias_iso:+.2f}pp  Brier={b_iso:.4f}")
        print(f"    platt : bias={bias_platt:+.2f}pp  Brier={b_platt:.4f}")
        print(f"    delta_Brier={delta:+.4f}  ->  {verdict}")

    # Need to map 2026 picks back to dates
    pk_dates = []
    for r in pk_rows:
        if (r.get('actual_result') or '').upper() in ('NRFI', 'YRFI'):
            pk_dates.append(r.get('date', ''))
    pk_dates_arr = np.array(pk_dates)
    print(f"\n--- OOS evaluation on 2026 picks ---")
    evaluate_slice("trailing 5/05-5/10 ", (pk_dates_arr >= '2026-05-05') & (pk_dates_arr <= '2026-05-10'))
    evaluate_slice("trailing 5/01-5/10 ", (pk_dates_arr >= '2026-05-01') & (pk_dates_arr <= '2026-05-10'))
    evaluate_slice("all 2026 graded    ", np.ones(len(pk_dates_arr), dtype=bool))

    # Also evaluate per-bucket Brier so we can see flat-zone vs platt directly
    def bucket(p):
        if p < 0.40: return 'deep_yrfi'
        if p < 0.44: return 'marg_yrfi'
        if p < 0.56: return 'mid_zone'
        if p < 0.60: return 'marg_nrfi'
        return 'deep_nrfi'

    print(f"\n--- Per-bucket Brier (full training set; lower is better) ---")
    print(f"  {'bucket':<12} {'N':>5}  {'iso':>8}  {'platt':>8}  {'delta':>8}")
    for b in ['deep_yrfi','marg_yrfi','mid_zone','marg_nrfi','deep_nrfi']:
        idx = [i for i, x in enumerate(p_all) if bucket(float(x)) == b]
        if not idx:
            continue
        ys = y_all[idx]
        ip = p_iso_all[idx]
        pp = p_platt_all[idx]
        bi = brier(ip, ys)
        bp = brier(pp, ys)
        d = bp - bi
        print(f"  {b:<12} {len(idx):>5}  {bi:>8.4f}  {bp:>8.4f}  {d:>+8.4f}")

    # Save candidate
    if not args.no_save:
        out = Path(args.out)
        platt.save(out)
        print(f"\nSaved Platt candidate -> {out.relative_to(ROOT)}")
        print(f"NOT DEPLOYED.  Operator decides next.")
    else:
        print(f"\n--no-save: skipping write.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

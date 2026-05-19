#!/usr/bin/env python3
"""tools/walkforward_model_refresh.py -- Phase 5 validation for model-refresh-2026-05.

Compare hypothetical P&L if the model-refresh changes had been applied vs the
production decisions actually made.  Two passes:

  PASS A -- in-sample (optimistic):  the new calibrator was fit on the same
            2026 data we're scoring against.  Bias toward favorable results.

  PASS B -- temporal holdout (honest): refit the calibrator on 2026 picks
            with date < HOLDOUT_CUTOFF, then evaluate on picks with date >=
            HOLDOUT_CUTOFF.  Out-of-sample for the new calibrator.

Only graded NRFI/YRFI rows are used.  Postponements/ungraded are skipped.

Usage:
    python tools/walkforward_model_refresh.py --since 2026-04-01 --holdout 2026-05-05
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibration import ProbCalibrator
from recalibrate_v2 import (
    gather_from_picks, load_fi_park, load_lr_models, lr_predict_two_stage,
)

NEW_CAL  = ROOT / "data" / "calibration_v2.json"
OLD_CAL  = ROOT / "data" / "backups" / "model-refresh-2026-05-pre" / "calibration_v2.json"
PICKS    = ROOT / "data" / "picks_2026.csv"

STRONG_NRFI_P     = 0.56
LEAN_NRFI_P       = 0.50
PASS_LO_P         = 0.44
LAMBDA_YRFI_FLOOR_NEW = 0.838
LAMBDA_YRFI_FLOOR_OLD = 0.78


def _payout(odds_str: str | None) -> float:
    if not odds_str:
        return 100.0 / 110.0
    try:
        o = float(str(odds_str).strip())
    except ValueError:
        return 100.0 / 110.0
    if o == 0:
        return 100.0 / 110.0
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def classify(p_nrfi: float, lambda_total: float | None, floor: float) -> tuple[str, str]:
    if p_nrfi >= STRONG_NRFI_P:
        return "NRFI", "STRONG"
    if p_nrfi >= LEAN_NRFI_P:
        return "NRFI", "LEAN"
    if p_nrfi > PASS_LO_P:
        if lambda_total is not None and lambda_total >= floor:
            return "YRFI", "LEAN"
        return "PASS", "NO EDGE"
    if p_nrfi >= PASS_LO_P:
        return "PASS", "NO EDGE"
    if lambda_total is not None and lambda_total < floor:
        return "PASS", "LOW LAMBDA"
    return "YRFI", "STRONG"


def evaluate(rows, raw_p, cal_p, lambda_scale, floor, side_label):
    """Returns (strong_picks, lean_picks, total_pnl, strong_pnl)"""
    strong_n = lean_n = 0
    strong_w = strong_l = lean_w = lean_l = 0
    strong_pnl = 0.0
    lean_pnl = 0.0

    for i, r in enumerate(rows):
        p_cal = float(cal_p[i])
        try:
            lam_t = float(r.get("lambda_lr_total", "") or r.get("combined_lambda", "") or 0.0)
            lam_t *= lambda_scale
        except (ValueError, TypeError):
            lam_t = None
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue

        side, strength = classify(p_cal, lam_t, floor)

        if strength == "STRONG":
            strong_n += 1
            won = (side == actual)
            if won:
                strong_w += 1
                # Use captured odds if present
                odds_col = f"market_{side.lower()}_odds"
                strong_pnl += _payout(r.get(odds_col))
            else:
                strong_l += 1
                strong_pnl -= 1.0
        elif strength == "LEAN":
            lean_n += 1
            won = (side == actual)
            if won:
                lean_w += 1
                odds_col = f"market_{side.lower()}_odds"
                lean_pnl += _payout(r.get(odds_col))
            else:
                lean_l += 1
                lean_pnl -= 1.0

    print(f"  {side_label}: STRONG {strong_w}-{strong_l} ({strong_w/max(strong_n,1)*100:.1f}%)  "
          f"P&L {strong_pnl:+.2f}u  "
          f"| LEAN {lean_w}-{lean_l}  P&L {lean_pnl:+.2f}u")
    return strong_n, lean_n, strong_pnl, lean_pnl


def actual_pnl_summary(rows, side_label):
    """Sum the stored profit_loss_units for rows that were actually bet_placed=Y."""
    strong_n = strong_w = strong_l = 0
    strong_pnl = 0.0
    for r in rows:
        if (r.get("pick_strength") or "").upper() != "STRONG":
            continue
        if (r.get("bet_placed") or "").upper() != "Y":
            continue
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue
        side = (r.get("pick_side") or "").upper()
        strong_n += 1
        won = (side == actual)
        if won:
            strong_w += 1
        else:
            strong_l += 1
        try:
            strong_pnl += float(r.get("profit_loss_units") or 0.0)
        except ValueError:
            pass
    print(f"  {side_label}: STRONG {strong_w}-{strong_l} ({strong_w/max(strong_n,1)*100:.1f}%)  "
          f"P&L {strong_pnl:+.2f}u  (n={strong_n} placed)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-04-01")
    ap.add_argument("--holdout", default="2026-05-05",
                    help="Date cutoff for Pass B (out-of-sample) test")
    args = ap.parse_args()

    t1, b1 = load_lr_models()
    fipark = load_fi_park()
    new_cal = ProbCalibrator.load(NEW_CAL)
    old_cal = ProbCalibrator.load(OLD_CAL)

    with open(PICKS, encoding="utf-8") as f:
        all_rows = [r for r in csv.DictReader(f) if (r.get("date") or "") >= args.since]

    # Keep only graded rows for evaluation
    graded = [r for r in all_rows if (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")]

    print(f"\nLoaded {len(all_rows)} rows from {args.since} ({len(graded)} graded)")
    print(f"Holdout cutoff for Pass B: {args.holdout}\n")

    # Raw LR forward pass on all graded rows
    Xt, Xb, _, _ = gather_from_picks(graded, fipark)
    raw_p = lr_predict_two_stage(t1, b1, Xt, Xb)
    new_p = np.array([new_cal.predict(float(p)) for p in raw_p])
    old_p = np.array([old_cal.predict(float(p)) for p in raw_p])

    print("=" * 72)
    print(f"ACTUAL (production, real money placed)")
    print("=" * 72)
    actual_pnl_summary(graded, "PROD")

    # ---- PASS A: in-sample ----
    print()
    print("=" * 72)
    print(f"PASS A -- IN-SAMPLE (new calibrator fit on this same window -- optimistic)")
    print("=" * 72)

    print("Old model (existing calibrator + 0.78 floor):")
    evaluate(graded, raw_p, old_p, lambda_scale=1.000, floor=LAMBDA_YRFI_FLOOR_OLD, side_label="OLD")

    print("New model (new calibrator + 0.838 floor):")
    evaluate(graded, raw_p, new_p, lambda_scale=0.510/0.475, floor=LAMBDA_YRFI_FLOOR_NEW, side_label="NEW")

    # ---- PASS B: temporal holdout ----
    print()
    print("=" * 72)
    print(f"PASS B -- TEMPORAL HOLDOUT (refit cal on date < {args.holdout}, eval on date >= {args.holdout})")
    print("=" * 72)

    pre_holdout = [r for r in graded if (r.get("date") or "") < args.holdout]
    post_holdout = [r for r in graded if (r.get("date") or "") >= args.holdout]

    if len(pre_holdout) < 100:
        print(f"  Pre-holdout sample too small (n={len(pre_holdout)}) -- skipping Pass B")
        return

    Xt_pre, Xb_pre, y_pre, _ = gather_from_picks(pre_holdout, fipark)
    raw_pre = lr_predict_two_stage(t1, b1, Xt_pre, Xb_pre)

    n_bins = max(5, min(20, len(y_pre) // 130))
    cal_pre = ProbCalibrator.fit(
        predictions=raw_pre.tolist(), actuals=y_pre.tolist(),
        n_bins=n_bins, train_seasons=["2026-pre-holdout"],
    )

    Xt_post, Xb_post, _, _ = gather_from_picks(post_holdout, fipark)
    raw_post = lr_predict_two_stage(t1, b1, Xt_post, Xb_post)
    holdout_cal_p = np.array([cal_pre.predict(float(p)) for p in raw_post])
    holdout_old_p = np.array([old_cal.predict(float(p)) for p in raw_post])

    print(f"  Pre-holdout fit: n={len(y_pre)}, {n_bins} bins")
    print(f"  Holdout eval: n={len(post_holdout)} graded games")
    print()
    print("Old model on holdout:")
    evaluate(post_holdout, raw_post, holdout_old_p, lambda_scale=1.000, floor=LAMBDA_YRFI_FLOOR_OLD, side_label="OLD")
    print("New model on holdout:")
    evaluate(post_holdout, raw_post, holdout_cal_p, lambda_scale=0.510/0.475, floor=LAMBDA_YRFI_FLOOR_NEW, side_label="NEW")
    print("Actual on holdout (production):")
    actual_pnl_summary(post_holdout, "PROD")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/weekly_refit.py -- validated re-fit of the production T1+B1+
calibrator using the latest 2026 data, with a holdout test gate
before shipping.

Procedure:
  1. Build a 2026 truepit CSV through (yesterday - 7 days) for training.
     The most recent 7 days are reserved as a holdout.
  2. Train candidate T1+B1+calibrator on (2024 + 2025 + 2026 partial).
  3. Evaluate candidate AND current production on the 7-day holdout
     (which neither model has seen).
  4. If candidate beats production (P&L >= prod_pl - tolerance AND
     Brier <= prod_brier + 0.005), back up production files and
     overwrite with candidate.  Print a summary.
  5. If candidate doesn't pass, print why and exit non-zero (so a
     CI run shows red).

Manual trigger only at the moment; intended to be wired into a
GitHub Actions workflow_dispatch step once trusted.

Exit codes:
  0 = candidate shipped
  1 = candidate failed validation (production unchanged)
  2 = data/script error
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lr_baseline import LogReg
from calibration import ProbCalibrator
from walk_forward_eval import (
    load_parks, load_truepit_csv, load_picks_2026,
    fit_two_stage, fit_calibrator, evaluate,
    TRAIN_2024, TRAIN_2025,
)


PROD_T1 = ROOT / "data" / "lr_t1.json"
PROD_B1 = ROOT / "data" / "lr_b1.json"
PROD_CAL = ROOT / "data" / "calibration_v2.json"
CAND_DIR = ROOT / "data" / "candidates" / "sliding_window"

# Tolerance: candidate is allowed to be slightly worse on a 7-day
# holdout (variance-dominated) but not much.  Block ship if it's
# clearly worse.
PL_TOLERANCE_U   = 1.0      # candidate P&L must be >= prod_pl - 1.0
BRIER_TOLERANCE  = 0.005    # candidate Brier must be <= prod_brier + 0.005


def main():
    today = date.today()
    holdout_end   = today - timedelta(days=1)
    holdout_start = holdout_end - timedelta(days=6)
    train_end     = holdout_start - timedelta(days=1)
    print(f"Today is {today.isoformat()}")
    print(f"Train window: 2024 + 2025 + 2026-04-01 to {train_end.isoformat()}")
    print(f"Holdout: {holdout_start.isoformat()} to {holdout_end.isoformat()}")
    print()

    parks = load_parks()
    rows_2024 = load_truepit_csv(TRAIN_2024, parks)
    rows_2025 = load_truepit_csv(TRAIN_2025, parks)
    rows_2026 = load_picks_2026(parks)
    rows_2026.sort(key=lambda r: r["date"])

    train_2026 = [r for r in rows_2026 if r["date"] <= train_end.isoformat()]
    holdout    = [r for r in rows_2026 if holdout_start.isoformat() <= r["date"] <= holdout_end.isoformat()]
    if len(holdout) < 10:
        print(f"WARN: only {len(holdout)} graded games in holdout window; refusing to ship on tiny sample")
        return 1

    print(f"Loaded: {len(rows_2024)} 2024 + {len(rows_2025)} 2025 + {len(train_2026)} 2026 training + {len(holdout)} holdout")
    print()

    # --- Fit candidate ---
    cand_train = rows_2024 + rows_2025 + train_2026
    cand_t1, cand_b1 = fit_two_stage(cand_train)
    cand_cal = fit_calibrator(cand_train, cand_t1, cand_b1)

    # --- Evaluate candidate ---
    cand_res = evaluate(cand_t1, cand_b1, cand_cal, holdout)
    if cand_res is None:
        print("Candidate eval returned None -- aborting")
        return 2

    # --- Evaluate current production ---
    prod_t1  = LogReg.load(str(PROD_T1))
    prod_b1  = LogReg.load(str(PROD_B1))
    prod_cal = ProbCalibrator.load(PROD_CAL)
    prod_res = evaluate(prod_t1, prod_b1, prod_cal, holdout)
    if prod_res is None:
        print("Production eval returned None -- aborting")
        return 2

    print(f"Holdout ({holdout_start} to {holdout_end}, {len(holdout)} games):")
    print(f"  PRODUCTION  : STRONG_NRFI {prod_res['w_nrfi']}/{prod_res['n_strong_nrfi']}  "
          f"STRONG_YRFI {prod_res['w_yrfi']}/{prod_res['n_strong_yrfi']}  "
          f"Brier={prod_res['brier']:.4f}  P&L={prod_res['pl']:+.2f}u")
    print(f"  CANDIDATE   : STRONG_NRFI {cand_res['w_nrfi']}/{cand_res['n_strong_nrfi']}  "
          f"STRONG_YRFI {cand_res['w_yrfi']}/{cand_res['n_strong_yrfi']}  "
          f"Brier={cand_res['brier']:.4f}  P&L={cand_res['pl']:+.2f}u")
    print(f"  Delta P&L   : {cand_res['pl'] - prod_res['pl']:+.2f}u  (tolerance: candidate must be >= prod - {PL_TOLERANCE_U}u)")
    print(f"  Delta Brier : {cand_res['brier'] - prod_res['brier']:+.4f}  (tolerance: candidate must be <= prod + {BRIER_TOLERANCE})")
    print()

    # --- Decision gate ---
    pl_ok    = cand_res["pl"]    >= prod_res["pl"]    - PL_TOLERANCE_U
    brier_ok = cand_res["brier"] <= prod_res["brier"] + BRIER_TOLERANCE
    if not (pl_ok and brier_ok):
        reasons = []
        if not pl_ok:    reasons.append(f"candidate P&L {cand_res['pl']:+.2f}u worse than prod {prod_res['pl']:+.2f}u by more than {PL_TOLERANCE_U}u tolerance")
        if not brier_ok: reasons.append(f"candidate Brier {cand_res['brier']:.4f} worse than prod {prod_res['brier']:.4f} by more than {BRIER_TOLERANCE} tolerance")
        print("VALIDATION FAILED -- candidate NOT shipped:")
        for r in reasons:
            print(f"  - {r}")
        return 1

    # --- Ship ---
    ts = today.strftime("%Y-%m-%d")
    backup_t1  = ROOT / "data" / f"lr_t1.json.bak-{ts}"
    backup_b1  = ROOT / "data" / f"lr_b1.json.bak-{ts}"
    backup_cal = ROOT / "data" / f"calibration_v2.json.bak-{ts}"
    shutil.copy2(PROD_T1, backup_t1)
    shutil.copy2(PROD_B1, backup_b1)
    shutil.copy2(PROD_CAL, backup_cal)
    print(f"Backed up production: {backup_t1.name}, {backup_b1.name}, {backup_cal.name}")

    CAND_DIR.mkdir(parents=True, exist_ok=True)
    cand_t1.save(str(CAND_DIR / "lr_t1.json"))
    cand_b1.save(str(CAND_DIR / "lr_b1.json"))
    cand_cal.save(CAND_DIR / "calibration.json")
    shutil.copy2(CAND_DIR / "lr_t1.json", PROD_T1)
    shutil.copy2(CAND_DIR / "lr_b1.json", PROD_B1)
    shutil.copy2(CAND_DIR / "calibration.json", PROD_CAL)
    print(f"VALIDATION PASSED -- shipped candidate to production paths")
    print(f"  Trained on: {len(cand_train)} rows (2024 + 2025 + 2026 thru {train_end.isoformat()})")
    print(f"  Validated against {len(holdout)} holdout games ({holdout_start} to {holdout_end})")
    print(f"  Holdout improvement: P&L {cand_res['pl'] - prod_res['pl']:+.2f}u, Brier {cand_res['brier'] - prod_res['brier']:+.4f}")
    print(f"  Rollback: cp {backup_t1.name} {PROD_T1.name} (similarly for b1 and cal)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

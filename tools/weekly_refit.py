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
# Minimum holdout size before a refit decision is even attempted.  A
# 7-day window is ~90 games, where Brier moves ~0.005 on noise alone.
MIN_HOLDOUT_GAMES = 90



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
    #
    # TIGHTENED 2026-07-28.  The previous gate was "P&L >= prod - 1.0u AND
    # Brier <= prod + 0.005" -- both ASYMMETRIC and generous, so a
    # candidate that was measurably WORSE on both still shipped.  The
    # 2026-07-28 run did exactly that: delta P&L +0.00u, delta Brier
    # +0.0037 (worse), and it shipped.  Independent review
    # (tools/verify_refit.py) then found the new model churned 51 of ~100
    # STRONG picks and, even IN-SAMPLE where it should be flattered, gave
    # back most of the Kelly bankroll (+78u vs +300u) at nearly triple the
    # drawdown.  It was rolled back.
    #
    # The incumbent is now the DEFAULT.  A refit perturbs every live
    # prediction -- and, since 2026-07-27, every Kelly stake -- so it has
    # to earn its place, not merely fail to embarrass itself:
    #
    #   * Brier must IMPROVE (no "within tolerance" pass), and
    #   * that improvement must survive a block bootstrap on the holdout,
    #     because a 90-odd-game window moves ~0.005 on noise alone, and
    #   * P&L must not regress at all.
    #
    # If the holdout is too small to distinguish the two, that is a
    # verdict: keep production and wait for more data.
    holdout_n = len(holdout)
    if holdout_n < MIN_HOLDOUT_GAMES:
        print(f"VALIDATION SKIPPED -- holdout is {holdout_n} games, "
              f"need >= {MIN_HOLDOUT_GAMES} to distinguish signal from noise.")
        print("  Production unchanged.")
        return 1

    d_pl    = cand_res["pl"] - prod_res["pl"]
    d_brier = cand_res["brier"] - prod_res["brier"]

    # Block bootstrap over holdout games on the Brier delta.
    rng = np.random.default_rng(20260728)
    idx = np.arange(holdout_n)
    deltas = []
    for _ in range(4000):
        s_ = rng.choice(idx, holdout_n, replace=True)
        sub = [holdout[i] for i in s_]
        c_ = evaluate(cand_t1, cand_b1, cand_cal, sub)
        p_ = evaluate(prod_t1, prod_b1, prod_cal, sub)
        if c_ and p_:
            deltas.append(c_["brier"] - p_["brier"])
    deltas.sort()
    lo = deltas[int(0.05 * len(deltas))] if deltas else 0.0
    hi = deltas[int(0.95 * len(deltas))] if deltas else 0.0
    print(f"  Bootstrap 90% CI on the Brier delta: [{lo:+.5f}, {hi:+.5f}]")

    pl_ok         = d_pl >= 0.0
    brier_ok      = d_brier < 0.0
    significant   = hi < 0.0          # entire CI favours the candidate

    if not (pl_ok and brier_ok and significant):
        print("VALIDATION FAILED -- candidate NOT shipped:")
        if not pl_ok:
            print(f"  - P&L regressed: {d_pl:+.2f}u")
        if not brier_ok:
            print(f"  - Brier did not improve: {d_brier:+.5f}")
        if brier_ok and not significant:
            print(f"  - Brier improvement is inside the noise band "
                  f"(CI upper bound {hi:+.5f} >= 0) on {holdout_n} games")
        print("  Production unchanged -- the incumbent is the default.")
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

#!/usr/bin/env python3
"""
compare_calibrators.py -- did including 2026's small noisy sample in the
calibrator make the model WORSE on 2026 itself?

Hypothesis: 2026 has only 343 graded games -> base NRFI rate of 47.4%
has a binomial 95% CI of ~+/-5.3pp.  The "drift" from 49.7% (2025) might be
mostly noise.  Including it in the calibrator pulls ALL predictions down,
including STRONG NRFIs that historically won.

This script fits two calibrators:
  A. 2025-only (the original)
  B. 2025 + 2026 combined (current production)

Then scores them on:
  1. 2026 graded games (out-of-sample for A, in-sample for B)
  2. The retroactive Q5 win rate and unit P&L per zone

If A scores better than B on 2026, the recalibration HURT the model.
"""

import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from calibration import ProbCalibrator
from recalibrate_v2 import (
    load_lr_model, load_fi_park, lr_predict_raw,
    gather_from_backtest, gather_from_picks, brier,
    BT_2025_PATH, PICKS_2026,
)


def q5_hit_rate(p, y):
    order = np.argsort(p)
    n = len(p)
    q5 = order[-(n // 5):]
    return float(y[q5].mean())


def main():
    print("=" * 70)
    print("  Calibrator A/B: 2025-only vs 2025+2026 -- effect on 2026 only")
    print("=" * 70)

    model = load_lr_model()
    park  = load_fi_park()

    # 2025 holdout
    with open(BT_2025_PATH, encoding="utf-8") as f:
        bt = list(csv.DictReader(f))
    X25, y25, _ = gather_from_backtest(bt)
    p25 = lr_predict_raw(model, X25)

    # 2026 graded
    with open(PICKS_2026, encoding="utf-8") as f:
        pk = list(csv.DictReader(f))
    X26, y26, _ = gather_from_picks(pk, park)
    p26 = lr_predict_raw(model, X26)

    print(f"\n2025 holdout  N = {len(y25)}, NRFI rate = {y25.mean()*100:.2f}%")
    print(f"2026 graded   N = {len(y26)}, NRFI rate = {y26.mean()*100:.2f}%")

    # Binomial CI on 2026 base rate
    n26 = len(y26)
    p_hat = float(y26.mean())
    se = math.sqrt(p_hat * (1 - p_hat) / n26)
    print(f"\n2026 base-rate 95% CI : [{(p_hat - 1.96*se)*100:.1f}%, "
          f"{(p_hat + 1.96*se)*100:.1f}%]")
    print(f"  -> overlaps 2025's 49.7%? "
          f"{'YES' if (p_hat - 1.96*se) <= 0.497 <= (p_hat + 1.96*se) else 'NO'}")
    print(f"  -> the 2.3pp drift from 2025 may be sample noise.")

    # Calibrator A: 2025-only
    cal_A = ProbCalibrator.fit(p25.tolist(), y25.tolist(), n_bins=15,
                               train_seasons=["2025"])
    # Calibrator B: 2025 + 2026 combined
    p_all = np.concatenate([p25, p26])
    y_all = np.concatenate([y25, y26])
    cal_B = ProbCalibrator.fit(p_all.tolist(), y_all.tolist(), n_bins=20,
                               train_seasons=["2025", "2026"])

    # Apply each to 2026 only
    p26_A = np.array([cal_A.predict(float(p)) for p in p26])
    p26_B = np.array([cal_B.predict(float(p)) for p in p26])

    print("\n" + "-" * 70)
    print(f"  {'metric':<28}  {'cal A (2025-only)':>20}  {'cal B (2025+26)':>20}")
    print("-" * 70)
    print(f"  {'2026 Brier':<28}  {brier(p26_A, y26):>20.4f}  {brier(p26_B, y26):>20.4f}")
    print(f"  {'2026 mean predicted':<28}  {p26_A.mean()*100:>19.2f}%  {p26_B.mean()*100:>19.2f}%")
    print(f"  {'2026 actual NRFI rate':<28}  {y26.mean()*100:>19.2f}%  {y26.mean()*100:>19.2f}%")
    print(f"  {'2026 calibration error':<28}  "
          f"{(p26_A.mean() - y26.mean())*100:>+18.2f}pp  "
          f"{(p26_B.mean() - y26.mean())*100:>+18.2f}pp")
    print(f"  {'2026 Q5 NRFI hit rate':<28}  "
          f"{q5_hit_rate(p26_A, y26)*100:>19.2f}%  "
          f"{q5_hit_rate(p26_B, y26)*100:>19.2f}%")

    # Top-quintile bet: P&L at -110 if we bet that bin NRFI
    win_unit = 100/110
    def quintile_pl(p, y):
        order = np.argsort(p)
        q5 = order[-(len(p)//5):]
        wins = int(y[q5].sum()); losses = len(q5) - wins
        return wins * win_unit + losses * (-1.0), wins, losses
    pl_A, w_A, l_A = quintile_pl(p26_A, y26)
    pl_B, w_B, l_B = quintile_pl(p26_B, y26)
    print(f"  {'2026 top-quintile NRFI bet':<28}  "
          f"{w_A}-{l_A}  P&L={pl_A:+.2f}u".rjust(22) +
          f"  {w_B}-{l_B}  P&L={pl_B:+.2f}u".rjust(22))

    # Bottom-quintile YRFI bet (predicted YRFI)
    def quintile_yrfi_pl(p, y):
        order = np.argsort(p)
        q1 = order[:(len(p)//5)]  # lowest predicted NRFI = highest YRFI
        # Bet YRFI: win if y == 0
        wins = int((y[q1] == 0).sum()); losses = len(q1) - wins
        return wins * win_unit + losses * (-1.0), wins, losses
    yp_A, yw_A, yl_A = quintile_yrfi_pl(p26_A, y26)
    yp_B, yw_B, yl_B = quintile_yrfi_pl(p26_B, y26)
    print(f"  {'2026 bottom-quintile YRFI':<28}  "
          f"{yw_A}-{yl_A}  P&L={yp_A:+.2f}u".rjust(22) +
          f"  {yw_B}-{yl_B}  P&L={yp_B:+.2f}u".rjust(22))

    print()
    print("-" * 70)
    if brier(p26_A, y26) < brier(p26_B, y26):
        print(f"  *** Cal A (2025-only) IS BETTER on 2026 -- recalibration hurt ***")
    elif brier(p26_A, y26) > brier(p26_B, y26):
        print(f"  Cal B (combined) is better on 2026 -- recalibration helped")
    else:
        print(f"  TIE on Brier within precision shown.")

    # Curve comparison at the 60% threshold
    print("\nCalibration curves -- where does raw 60% map under each?")
    for raw in [0.55, 0.58, 0.60, 0.63, 0.65]:
        pa = cal_A.predict(raw); pb = cal_B.predict(raw)
        print(f"  raw {raw*100:.0f}%  ->  A={pa*100:5.1f}%   B={pb*100:5.1f}%   delta={(pb-pa)*100:+.1f}pp")
    print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
test_park_refresh.py -- does refreshing fi_park_factors.json with fresher
data fix the 2026 NRFI-side inversion?

Hypothesis: the dominant feature (fi_park_nrfi_rate, weight +0.21) was
built from 2024+2025. 2024 was a high-NRFI outlier (53.5% base rate vs
49.7% in 2025). The current park factors are biased toward NRFI, so the
model confidently picks NRFI in parks that were high-NRFI in 2024 but
aren't in 2026.

Test 4 park-factor sources, run each through the LR-v2 model + calibrator,
score on 2026 graded games (Q5 NRFI hit rate is the key metric):

  1. CURRENT  -- whatever's in data/fi_park_factors.json now
  2. v2024+25 -- rebuild from 2024+2025 backtests (sanity: should match #1)
  3. v2025    -- rebuild from 2025 alone (drops the 2024 anomaly)
  4. v2025+26 -- rebuild from 2025 + 2026 partial (most recent)
"""

import csv
import json
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
    load_lr_model, lr_predict_raw, brier,
    LEAGUE_AVG_ERA, LEAGUE_AVG_HR9, LEAGUE_AVG_BB9,
    LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, FI_PARK_DEFAULT,
    coerce_float,
)

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_26 = ROOT / "data" / "picks_2026.csv"
CURRENT_FI_PARK = ROOT / "data" / "fi_park_factors.json"


def build_park_factors(rows: list[dict], park_col: str, actual_col: str,
                       prior_games: int = 50) -> dict[str, float]:
    """Bayesian-shrunk per-park NRFI rate.  Mirror of backtest.build_fi_park_factors,
    parameterized for different column names so picks_2026.csv works too."""
    park_n: dict[str, int]    = {}
    park_nrfi: dict[str, int] = {}
    for r in rows:
        park = r.get(park_col, "")
        actual = (r.get(actual_col, "") or "").upper()
        if not park or actual not in ("NRFI", "YRFI"):
            continue
        park_n[park]    = park_n.get(park, 0) + 1
        park_nrfi[park] = park_nrfi.get(park, 0) + (1 if actual == "NRFI" else 0)
    if not park_n:
        return {}
    overall_n    = sum(park_n.values())
    overall_nrfi = sum(park_nrfi.values())
    overall_rate = overall_nrfi / overall_n if overall_n else 0.5
    return {
        p: (park_nrfi[p] + prior_games * overall_rate) / (n + prior_games)
        for p, n in park_n.items()
    }


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_features(rows: list[dict], park_map: dict[str, float],
                   home_col: str, actual_col: str) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for r in rows:
        actual = (r.get(actual_col, "") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue
        home = r.get(home_col, "")
        fi_park = park_map.get(home, FI_PARK_DEFAULT)
        vec = [
            fi_park,
            coerce_float(r.get("home_fip"), LEAGUE_AVG_ERA),
            coerce_float(r.get("home_hr9"), LEAGUE_AVG_HR9),
            coerce_float(r.get("home_bb9"), LEAGUE_AVG_BB9),
            coerce_float(r.get("away_obp"), LEAGUE_AVG_OBP),
            coerce_float(r.get("away_slg"), LEAGUE_AVG_SLG),
            coerce_float(r.get("away_fip"), LEAGUE_AVG_ERA),
            coerce_float(r.get("away_hr9"), LEAGUE_AVG_HR9),
            coerce_float(r.get("away_bb9"), LEAGUE_AVG_BB9),
            coerce_float(r.get("home_obp"), LEAGUE_AVG_OBP),
            coerce_float(r.get("home_slg"), LEAGUE_AVG_SLG),
        ]
        X.append(vec)
        y.append(1 if actual == "NRFI" else 0)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int)


def q5_hit(p, y):
    order = np.argsort(p)
    n = len(p)
    q5 = order[-(n // 5):]
    return float(y[q5].mean()), int(y[q5].sum()), len(q5)


def q1_yrfi(p, y):
    """Bottom quintile (predicted lowest NRFI = highest YRFI). Win = y == 0."""
    order = np.argsort(p)
    n = len(p)
    q1 = order[:(n // 5)]
    wins = int((y[q1] == 0).sum())
    return wins / len(q1), wins, len(q1)


def main():
    print("=" * 76)
    print("  Park-factor refresh test: does 2026 NRFI inversion fix with new park map?")
    print("=" * 76)

    bt24 = read_csv(BT_2024)
    bt25 = read_csv(BT_2025)
    pk26 = read_csv(PICKS_26)
    with open(CURRENT_FI_PARK, encoding="utf-8") as f:
        cur = json.load(f)

    # 4 candidate park maps
    park_maps = {
        "CURRENT (saved 2024+25 mix)" : cur,
        "v2024+2025 (rebuilt fresh)"   : build_park_factors(bt24 + bt25, "home", "actual_side"),
        "v2025 only"                    : build_park_factors(bt25, "home", "actual_side"),
        "v2025 + 2026 partial"          : build_park_factors(
            bt25 + [{"home": r.get("home_team", ""),
                     "actual_side": r.get("actual_result", "")} for r in pk26],
            "home", "actual_side"),
    }

    # Test each on 2026 graded games using the saved LR model + cal
    model = load_lr_model()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")

    print(f"\nScoring each park map on 2026 graded games (N=348):\n")
    print(f"  {'park map':<30}  {'mean':>6}  {'Q5 NRFI':>13}  {'Q1 YRFI':>13}  {'Q5 P&L':>9}  {'Q1 P&L':>9}")
    print("  " + "-" * 95)

    for label, pm in park_maps.items():
        X, y = build_features(pk26, pm, "home_team", "actual_result")
        if X.size == 0:
            continue
        p_raw = lr_predict_raw(model, X)
        p_cal = np.array([cal.predict(float(p)) for p in p_raw])
        mean_pred = float(p_cal.mean())

        q5_rate, q5_w, q5_n = q5_hit(p_cal, y)
        q1_rate, q1_w, q1_n = q1_yrfi(p_cal, y)

        win = 100 / 110
        q5_pl = q5_w * win + (q5_n - q5_w) * (-1.0)
        q1_pl = q1_w * win + (q1_n - q1_w) * (-1.0)

        print(f"  {label:<30}  {mean_pred*100:>5.1f}%  "
              f"{q5_w}-{q5_n - q5_w} ({q5_rate*100:>4.1f}%)  "
              f"{q1_w}-{q1_n - q1_w} ({q1_rate*100:>4.1f}%)  "
              f"{q5_pl:>+8.2f}u  {q1_pl:>+8.2f}u")

    # Show the parks that shifted most between current and 2025+2026
    print(f"\nPark factors shifted most (current -> 2025+26):")
    new_pm = park_maps["v2025 + 2026 partial"]
    diffs = []
    for park in sorted(set(cur) & set(new_pm)):
        diffs.append((park, cur[park], new_pm[park], new_pm[park] - cur[park]))
    diffs.sort(key=lambda x: -abs(x[3]))
    for p, c_, n_, d in diffs[:15]:
        sign = "+" if d > 0 else ""
        print(f"  {p:<5}  {c_*100:>5.1f}% -> {n_*100:>5.1f}%   {sign}{d*100:>+5.1f}pp")


if __name__ == "__main__":
    main()

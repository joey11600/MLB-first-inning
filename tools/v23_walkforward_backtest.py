#!/usr/bin/env python3
"""tools/v23_walkforward_backtest.py

Walk-forward backtest of the proposed V2.3 model (LR refit on
rolling 2026-only training data) from the start of the 2026 season.

Methodology:
  For each day D in 2026:
    1. Training set = all graded 2026 picks with date < D.
    2. If |train| >= 100, refit LR on this 2026-only data ("V2.3").
       Otherwise fall back to the production V2.2 weights
       (trained on 2024+2025).  This mimics how V2.3 would deploy
       in real time -- accumulating 2026 data until we have enough.
    3. For every game on day D, run the LR forward, apply the
       production calibrator (calibration_v2.json) and the same
       STRONG/PASS thresholds the predictor uses.
    4. Compute hypothetical P&L:
       - STRONG win:  + payout-per-unit at captured odds (fallback -110)
       - STRONG loss: -1.00u
       - PASS:        0.00u
       - Thin-pitcher demotion is applied identically to live system.

Output: per-day cumulative comparison + summary at end.

Caveats made explicit:
  - First ~10 days of season use 2024+2025 fallback (no 2026 data yet).
    Result: V2.3's "from-start-of-season" curve is identical to V2.2
    for that window.
  - Calibrator is fixed at production v2 (not refit).  Refitting the
    calibrator daily is a different experiment.
  - Refits per DAY (not per game), matching realistic deploy cadence.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from calibration import ProbCalibrator
from recalibrate_v2 import (
    T1_FEATURES, B1_FEATURES,
    gather_from_picks, load_fi_park,
    lr_predict_two_stage,
)
from two_stage_model import gather as gather_for_train
from lr_baseline import LogReg

CAL_PATH = ROOT / "data" / "calibration_v2.json"
PICKS = ROOT / "data" / "picks_2026.csv"
BT_24 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
BT_25 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"

STRONG_NRFI_P     = 0.56
PASS_LO_P         = 0.44
LAMBDA_YRFI_FLOOR = 0.78
MIN_TRAIN_N       = 100   # below this, fall back to 2024+2025 production model
THIN_PQ           = {"sm", "ltd"}


def _coerce(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _pq_worst(aq: str, hq: str) -> str:
    order = {"sm": 0, "ltd": 1, "live": 2}
    a = order.get((aq or "").lower(), 9)
    h = order.get((hq or "").lower(), 9)
    rev = {0: "sm", 1: "ltd", 2: "live"}
    return rev.get(min(a, h), "avg")


def _payout_per_unit(odds_str: str) -> float:
    """Fallback -110 if no captured odds."""
    if not odds_str:
        return 100.0 / 110.0
    s = str(odds_str).strip().replace(" ", "")
    try:
        odds = float(s)
    except ValueError:
        return 100.0 / 110.0
    if odds == 0:
        return 100.0 / 110.0
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def fit_lr(train_rows: list[dict], features: list[str], half: str, fipark: dict):
    """Fit a single-side LR on graded picks_2026.csv rows.

    half = 't1' (home pitcher vs away offense) or 'b1' (away vs home).
    """
    # We need to reuse the gather() helper but feed it a temp file so
    # it can build feature vectors with phase_e3 enabled.  Simplest: build
    # X / y inline using the same coerce defaults as recalibrate_v2.
    LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, LEAGUE_AVG_ISO = 4.20, 0.318, 0.414, 0.169
    FI_PARK_DEFAULT = 0.50
    WX_DEFS = (20.0, 10.0, 60.0)
    LEAGUE_NRFI = 0.50; LEAGUE_AVG_XERA = 4.20; NEUTRAL_PCT = 50
    X, y = [], []
    for r in train_rows:
        home = r.get("home_team", "")
        fi_park = fipark.get(home, FI_PARK_DEFAULT)
        wx = [_coerce(r.get("wx_temp_c"), WX_DEFS[0]),
              _coerce(r.get("wx_wind_kmh"), WX_DEFS[1]),
              _coerce(r.get("wx_humidity"), WX_DEFS[2]),
              _coerce(r.get("wx_is_dome"), 0.0)]
        ump = _coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI)
        h_era = _coerce(r.get("home_era"), LEAGUE_AVG_ERA)
        a_era = _coerce(r.get("away_era"), LEAGUE_AVG_ERA)
        if half == "t1":
            vec = [
                fi_park,
                _coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                _coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                wx[0], wx[1], wx[2], wx[3],
                _coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
                _coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP),
                ump,
                _coerce(r.get("home_xera"), LEAGUE_AVG_XERA),
                _coerce(r.get("home_whiff_pct_rank"), NEUTRAL_PCT),
                h_era - a_era,
                _coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
                _coerce(r.get("away_top3c_slg"), LEAGUE_AVG_SLG),
                _coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO),
                _coerce(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI),
                _coerce(r.get("home_avg_ip_per_start"), 5.0),
            ]
            label = 1 if _coerce(r.get("fi_away_runs"), 0) > 0 else 0
        else:
            vec = [
                fi_park,
                _coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                _coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                wx[0], wx[1], wx[2], wx[3],
                _coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
                _coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP),
                ump,
                _coerce(r.get("away_xera"), LEAGUE_AVG_XERA),
                _coerce(r.get("away_whiff_pct_rank"), NEUTRAL_PCT),
                a_era - h_era,
                _coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
                _coerce(r.get("home_top3c_slg"), LEAGUE_AVG_SLG),
                _coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO),
                _coerce(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI),
                _coerce(r.get("away_avg_ip_per_start"), 5.0),
            ]
            label = 1 if _coerce(r.get("fi_home_runs"), 0) > 0 else 0
        X.append(vec); y.append(label)
    X = np.asarray(X, dtype=float); y = np.asarray(y, dtype=int)
    model = LogReg.fit(X, y, features, l2=0.05)
    return {
        "weights": np.asarray(model.w, dtype=float),
        "bias": float(model.b),
        "mean": np.asarray(model.mean, dtype=float),
        "std":  np.asarray(model.std,  dtype=float),
    }


def predict_pair(t1m, b1m, t1v, b1v):
    X1 = np.asarray([t1v], dtype=float)
    X2 = np.asarray([b1v], dtype=float)
    z1 = (X1 - t1m["mean"]) / t1m["std"] @ t1m["weights"] + t1m["bias"]
    z2 = (X2 - b1m["mean"]) / b1m["std"] @ b1m["weights"] + b1m["bias"]
    p_t1 = float(1.0 / (1.0 + np.exp(-z1))[0])
    p_b1 = float(1.0 / (1.0 + np.exp(-z2))[0])
    return (1.0 - p_t1) * (1.0 - p_b1)


def feat_vec(r: dict, half: str, fipark: dict):
    LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, LEAGUE_AVG_ISO = 4.20, 0.318, 0.414, 0.169
    FI_PARK_DEFAULT = 0.50
    WX_DEFS = (20.0, 10.0, 60.0)
    LEAGUE_NRFI = 0.50; LEAGUE_AVG_XERA = 4.20; NEUTRAL_PCT = 50
    home = r.get("home_team", "")
    fi_park = fipark.get(home, FI_PARK_DEFAULT)
    wx = [_coerce(r.get("wx_temp_c"), WX_DEFS[0]),
          _coerce(r.get("wx_wind_kmh"), WX_DEFS[1]),
          _coerce(r.get("wx_humidity"), WX_DEFS[2]),
          _coerce(r.get("wx_is_dome"), 0.0)]
    ump = _coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI)
    h_era = _coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = _coerce(r.get("away_era"), LEAGUE_AVG_ERA)
    if half == "t1":
        return [
            fi_park,
            _coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
            _coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
            wx[0], wx[1], wx[2], wx[3],
            _coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
            _coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP),
            ump,
            _coerce(r.get("home_xera"), LEAGUE_AVG_XERA),
            _coerce(r.get("home_whiff_pct_rank"), NEUTRAL_PCT),
            h_era - a_era,
            _coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
            _coerce(r.get("away_top3c_slg"), LEAGUE_AVG_SLG),
            _coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO),
            _coerce(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI),
            _coerce(r.get("home_avg_ip_per_start"), 5.0),
        ]
    return [
        fi_park,
        _coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        _coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        wx[0], wx[1], wx[2], wx[3],
        _coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
        _coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP),
        ump,
        _coerce(r.get("away_xera"), LEAGUE_AVG_XERA),
        _coerce(r.get("away_whiff_pct_rank"), NEUTRAL_PCT),
        a_era - h_era,
        _coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        _coerce(r.get("home_top3c_slg"), LEAGUE_AVG_SLG),
        _coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO),
        _coerce(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI),
        _coerce(r.get("away_avg_ip_per_start"), 5.0),
    ]


def main():
    fipark = load_fi_park()
    # Production V2.2 weights (fallback when not enough 2026 data yet)
    v22_t1 = {
        "weights": np.asarray(json.load(open(ROOT / "data" / "lr_t1.json"))["weights"], dtype=float),
        "bias":    float(json.load(open(ROOT / "data" / "lr_t1.json"))["bias"]),
        "mean":    np.asarray(json.load(open(ROOT / "data" / "lr_t1.json"))["mean"], dtype=float),
        "std":     np.asarray(json.load(open(ROOT / "data" / "lr_t1.json"))["std"], dtype=float),
    }
    v22_b1 = {
        "weights": np.asarray(json.load(open(ROOT / "data" / "lr_b1.json"))["weights"], dtype=float),
        "bias":    float(json.load(open(ROOT / "data" / "lr_b1.json"))["bias"]),
        "mean":    np.asarray(json.load(open(ROOT / "data" / "lr_b1.json"))["mean"], dtype=float),
        "std":     np.asarray(json.load(open(ROOT / "data" / "lr_b1.json"))["std"], dtype=float),
    }
    cal = ProbCalibrator.load(CAL_PATH)

    with open(PICKS, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    graded = [
        r for r in all_rows
        if (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")
        and (r.get("fi_away_runs") or "") != ""
        and (r.get("fi_home_runs") or "") != ""
    ]
    graded.sort(key=lambda r: r.get("date", ""))
    print(f"Total graded 2026 picks: {len(graded)}")

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for r in graded:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date.keys())
    print(f"Days: {len(dates)}  ({dates[0]} → {dates[-1]})")
    print()

    # Walk forward
    cum_v23  = 0.0
    cum_v22  = 0.0   # for comparison: production V2.2 + thin-pitcher demotion
    cum_v23_demoted = 0.0   # V2.3 + thin-pitcher demotion
    prior_train: list[dict] = []

    print(f"{'date':<11}  {'train_n':>7}  {'mode':<10}  "
          f"{'V2.2 bets':>9} {'V2.2 P&L':>9}  "
          f"{'V2.3 bets':>9} {'V2.3 P&L':>9}  "
          f"{'V2.3+dem bets':>13} {'V2.3+dem P&L':>13}")

    for d in dates:
        rows_today = by_date[d]
        n_prior = len(prior_train)
        if n_prior >= MIN_TRAIN_N:
            t1m_v23 = fit_lr(prior_train, T1_FEATURES, "t1", fipark)
            b1m_v23 = fit_lr(prior_train, B1_FEATURES, "b1", fipark)
            mode = "V2.3"
        else:
            t1m_v23 = v22_t1; b1m_v23 = v22_b1
            mode = "fallback"

        v23_bets = 0; v23_pnl = 0.0
        v22_bets = 0; v22_pnl = 0.0
        v23d_bets = 0; v23d_pnl = 0.0

        for r in rows_today:
            t1v = feat_vec(r, "t1", fipark)
            b1v = feat_vec(r, "b1", fipark)
            actual = (r.get("actual_result") or "").upper()
            lam = _coerce(r.get("combined_lambda"), 1.0)
            worst_pq = _pq_worst(r.get("away_pitcher_q",""), r.get("home_pitcher_q",""))

            # V2.3
            p_raw23 = predict_pair(t1m_v23, b1m_v23, t1v, b1v)
            p23 = cal.predict(p_raw23)
            v23_side, v23_str = _classify(p23, lam)
            if v23_str == "STRONG" and v23_side in ("NRFI", "YRFI"):
                v23_bets += 1
                if actual == v23_side:
                    odds = r.get("market_nrfi_odds" if v23_side=="NRFI" else "market_yrfi_odds", "")
                    v23_pnl += _payout_per_unit(odds)
                else:
                    v23_pnl -= 1.0
                # V2.3 + thin-pitcher demotion
                if worst_pq in THIN_PQ:
                    pass  # demoted, no bet
                else:
                    v23d_bets += 1
                    if actual == v23_side:
                        odds = r.get("market_nrfi_odds" if v23_side=="NRFI" else "market_yrfi_odds", "")
                        v23d_pnl += _payout_per_unit(odds)
                    else:
                        v23d_pnl -= 1.0

            # V2.2 (production, with thin-pitcher demotion)
            p_raw22 = predict_pair(v22_t1, v22_b1, t1v, b1v)
            p22 = cal.predict(p_raw22)
            v22_side, v22_str = _classify(p22, lam)
            if v22_str == "STRONG" and v22_side in ("NRFI", "YRFI"):
                if worst_pq in THIN_PQ:
                    pass  # demoted
                else:
                    v22_bets += 1
                    if actual == v22_side:
                        odds = r.get("market_nrfi_odds" if v22_side=="NRFI" else "market_yrfi_odds", "")
                        v22_pnl += _payout_per_unit(odds)
                    else:
                        v22_pnl -= 1.0

        cum_v23  += v23_pnl
        cum_v22  += v22_pnl
        cum_v23_demoted += v23d_pnl

        print(f"{d:<11}  {n_prior:>7}  {mode:<10}  "
              f"{v22_bets:>9} {v22_pnl:>+8.3f}u  "
              f"{v23_bets:>9} {v23_pnl:>+8.3f}u  "
              f"{v23d_bets:>13} {v23d_pnl:>+13.3f}u")

        # Add today's graded games to training pool for tomorrow
        prior_train.extend(rows_today)

    print()
    print(f"{'='*120}")
    print(f"SEASON SUMMARY:")
    print(f"  V2.2 + thin-pitcher demotion : cumulative P&L = {cum_v22:+.3f}u")
    print(f"  V2.3 (raw, no demotion)      : cumulative P&L = {cum_v23:+.3f}u")
    print(f"  V2.3 + thin-pitcher demotion : cumulative P&L = {cum_v23_demoted:+.3f}u")
    print(f"  Delta V2.3 vs V2.2           : {cum_v23 - cum_v22:+.3f}u")
    print(f"  Delta V2.3+dem vs V2.2       : {cum_v23_demoted - cum_v22:+.3f}u")
    print(f"  Actual V2.1 in real-life     : +35.535u (from pl_calc.py season)")


def _classify(p_cal: float, lam_total: float) -> tuple[str, str]:
    if p_cal >= STRONG_NRFI_P:
        return "NRFI", "STRONG"
    if p_cal < PASS_LO_P:
        if lam_total >= LAMBDA_YRFI_FLOOR:
            return "YRFI", "STRONG"
        return "PASS", "NO EDGE"
    return "PASS", "NO EDGE"


if __name__ == "__main__":
    main()

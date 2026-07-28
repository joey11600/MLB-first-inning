#!/usr/bin/env python3
"""
tools/walk_forward_eval.py -- robust validation of the sliding-window
retrain hypothesis.

Idea: for each week_start in a series of 2026 weeks, train the model
on (2024 + 2025 + 2026 through week_start - 1) and test on that week.
Compare to a fixed-baseline train (2024 + 2025 only) on the same week.

If the sliding-window approach consistently beats the fixed baseline
across many weeks, the +10u result from a single 14-day holdout is
not just variance.

Reads from picks_2026.csv (single source of 2026 truepit-equivalent
data) so we don't have to rebuild CSVs for each window.
"""

from __future__ import annotations

import csv
import json
import math
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
from calibration import ProbCalibrator, CIRCalibrator

PARKS_JSON = ROOT / "data" / "fi_park_factors.json"
TRAIN_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
TRAIN_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"

STRONG_NRFI_P = 0.56
STRONG_YRFI_P = 0.44

LEAGUE_AVG_ERA = 4.17
LEAGUE_AVG_OBP = 0.316
LEAGUE_AVG_SLG = 0.407
LEAGUE_AVG_ISO = 0.169
LEAGUE_NRFI_RATE = 0.50
LEAGUE_AVG_XERA  = 4.20
LEAGUE_AVG_OPS_VSHAND = 0.720
NEUTRAL_PCT_RANK = 50
FI_PARK_DEFAULT  = 0.50
WX_TEMP_DEFAULT  = 20.0
WX_WIND_DEFAULT  = 10.0
WX_HUMIDITY_DEFAULT = 60.0
LR_L2 = 0.05


def coerce(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def load_parks():
    with open(PARKS_JSON, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for t, v in d.items():
        out[t] = float(v.get("nrfi_rate") or v.get("rate") or 0.5) if isinstance(v, dict) else float(v)
    return out


def build_row_features(r: dict, parks: dict):
    """Return (t1_x, b1_x, t1_y, b1_y, y_nrfi, meta) or None if row not usable."""
    actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
    if actual not in ("NRFI", "YRFI"):
        return None
    try:
        fi_a = int(float(r.get("fi_away_runs", "") or "nan"))
        fi_h = int(float(r.get("fi_home_runs", "") or "nan"))
    except (ValueError, TypeError):
        return None
    home = r.get("home_team", "") or r.get("home", "")
    fi_park = parks.get(home, FI_PARK_DEFAULT)
    if r.get("fi_park_nrfi_rate"):
        fi_park = coerce(r.get("fi_park_nrfi_rate"), fi_park)
    wx_t = coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT)
    wx_w = coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT)
    wx_h = coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT)
    wx_d = coerce(r.get("wx_is_dome"),  0.0)
    ump_rate = coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI_RATE)
    h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
    t1 = [
        fi_park,
        coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
        wx_t, wx_w, wx_h, wx_d,
        coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        coerce(r.get("away_top3c_obp"),            LEAGUE_AVG_OBP),
        ump_rate,
        coerce(r.get("home_xera"),                 LEAGUE_AVG_XERA),
        coerce(r.get("home_whiff_pct_rank"),       NEUTRAL_PCT_RANK),
        h_era - a_era,
        coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        coerce(r.get("away_top3c_slg"),            LEAGUE_AVG_SLG),
        coerce(r.get("away_top3c_iso"),            LEAGUE_AVG_ISO),
        coerce(r.get("home_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
        coerce(r.get("home_avg_ip_per_start"),     5.0),
        coerce(r.get("away_top3_ops_vs_oppHand"),  LEAGUE_AVG_OPS_VSHAND),
    ]
    b1 = [
        fi_park,
        coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        wx_t, wx_w, wx_h, wx_d,
        coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        coerce(r.get("home_top3c_obp"),            LEAGUE_AVG_OBP),
        ump_rate,
        coerce(r.get("away_xera"),                 LEAGUE_AVG_XERA),
        coerce(r.get("away_whiff_pct_rank"),       NEUTRAL_PCT_RANK),
        a_era - h_era,
        coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        coerce(r.get("home_top3c_slg"),            LEAGUE_AVG_SLG),
        coerce(r.get("home_top3c_iso"),            LEAGUE_AVG_ISO),
        coerce(r.get("away_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
        coerce(r.get("away_avg_ip_per_start"),     5.0),
        coerce(r.get("home_top3_ops_vs_oppHand"),  LEAGUE_AVG_OPS_VSHAND),
    ]
    t1_y = 1 if fi_a > 0 else 0
    b1_y = 1 if fi_h > 0 else 0
    y_nrfi = 1 if (fi_a + fi_h) == 0 else 0
    return {
        "date": r.get("date", ""),
        "t1": t1, "b1": b1,
        "t1_y": t1_y, "b1_y": b1_y, "y_nrfi": y_nrfi,
        "away": r.get("away_team", "") or r.get("away", ""),
        "home": home,
    }


def load_truepit_csv(path: Path, parks: dict) -> list:
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = build_row_features(r, parks)
            if row is not None:
                rows.append(row)
    return rows


def load_picks_2026(parks: dict) -> list:
    rows = []
    with open(PICKS_2026, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = build_row_features(r, parks)
            if row is not None:
                rows.append(row)
    return rows


def fit_two_stage(rows):
    Xt = np.asarray([r["t1"] for r in rows])
    yt = np.asarray([r["t1_y"] for r in rows])
    Xb = np.asarray([r["b1"] for r in rows])
    yb = np.asarray([r["b1_y"] for r in rows])
    # We don't have feature_names from lr_baseline -- pass empties; just need predict_proba
    feature_names = ["f{}".format(i) for i in range(Xt.shape[1])]
    m_t1 = LogReg.fit(Xt, yt, feature_names, l2=LR_L2)
    m_b1 = LogReg.fit(Xb, yb, feature_names, l2=LR_L2)
    return m_t1, m_b1


def fit_calibrator(rows, m_t1, m_b1):
    Xt = np.asarray([r["t1"] for r in rows])
    Xb = np.asarray([r["b1"] for r in rows])
    pt = m_t1.predict_proba(Xt)
    pb = m_b1.predict_proba(Xb)
    raw = (1 - pt) * (1 - pb)
    actuals = np.asarray([r["y_nrfi"] for r in rows])
    # 2026-07-28: CIR, not plain PAV.  This function feeds
    # tools/weekly_refit.py, which OVERWRITES data/calibration_v2.json on
    # a successful refit -- leaving it on ProbCalibrator.fit would have
    # silently reverted the production curve to a plateaued one and
    # undone the CIR ship, reintroducing the flat step that Kelly sizes
    # stakes off.  See calibration.CIRCalibrator.
    return CIRCalibrator.fit(raw.tolist(), actuals.tolist(), n_bins=20, train_seasons=["pf"])


def evaluate(m_t1, m_b1, cal, test_rows):
    if not test_rows:
        return None
    Xt = np.asarray([r["t1"] for r in test_rows])
    Xb = np.asarray([r["b1"] for r in test_rows])
    pt = m_t1.predict_proba(Xt)
    pb = m_b1.predict_proba(Xb)
    raw = (1 - pt) * (1 - pb)
    calp = np.array([cal.predict(float(p)) for p in raw])
    y = np.array([r["y_nrfi"] for r in test_rows])
    n_snrfi = sum(1 for p in calp if p >= STRONG_NRFI_P)
    n_syrfi = sum(1 for p in calp if p < STRONG_YRFI_P)
    w_nrfi = sum(1 for p, yy in zip(calp, y) if p >= STRONG_NRFI_P and yy == 1)
    w_yrfi = sum(1 for p, yy in zip(calp, y) if p < STRONG_YRFI_P and yy == 0)
    # P&L at flat -110 (no DK odds here)
    pl = 0.0
    for p, yy in zip(calp, y):
        if p >= STRONG_NRFI_P:
            pl += (100.0/110.0) if yy == 1 else -1.0
        elif p < STRONG_YRFI_P:
            pl += (100.0/110.0) if yy == 0 else -1.0
    brier = float(np.mean((calp - y)**2))
    return {
        "n": len(test_rows),
        "n_strong_nrfi": n_snrfi, "w_nrfi": w_nrfi,
        "n_strong_yrfi": n_syrfi, "w_yrfi": w_yrfi,
        "brier": brier,
        "pl": pl,
    }


def main():
    parks = load_parks()
    rows_2024 = load_truepit_csv(TRAIN_2024, parks)
    rows_2025 = load_truepit_csv(TRAIN_2025, parks)
    rows_2026 = load_picks_2026(parks)
    rows_2026.sort(key=lambda r: r["date"])
    print(f"Loaded {len(rows_2024)} 2024 + {len(rows_2025)} 2025 + {len(rows_2026)} 2026 graded rows")
    print()

    # Define test weeks: each Monday from 4/14 onward, 7-day windows.
    # Stop when window end exceeds 2026-05-26.
    week_starts = []
    d = date(2026, 4, 14)
    while d <= date(2026, 5, 26):
        week_end = min(d + timedelta(days=6), date(2026, 5, 26))
        week_starts.append((d.isoformat(), week_end.isoformat()))
        d += timedelta(days=7)

    print(f"Walk-forward windows: {len(week_starts)}")
    print()

    print(f"{'window':<23} {'n_test':>6} {'mode':<8} {'NRFI':>10} {'YRFI':>10} {'Brier':>6} {'P&L':>8}")
    print('-' * 80)
    summary = {"prod": {"pl": 0.0, "n": 0}, "cand": {"pl": 0.0, "n": 0}}
    for ws, we in week_starts:
        test = [r for r in rows_2026 if ws <= r["date"] <= we]
        if not test:
            continue
        # ---- Production baseline: train on 2024 + 2025 only ----
        prod_train = rows_2024 + rows_2025
        m_t1, m_b1 = fit_two_stage(prod_train)
        cal = fit_calibrator(prod_train, m_t1, m_b1)
        prod_res = evaluate(m_t1, m_b1, cal, test)

        # ---- Candidate: train on 2024 + 2025 + 2026 thru (ws - 1 day) ----
        prior_2026 = [r for r in rows_2026 if r["date"] < ws]
        cand_train = rows_2024 + rows_2025 + prior_2026
        m_t1, m_b1 = fit_two_stage(cand_train)
        cal = fit_calibrator(cand_train, m_t1, m_b1)
        cand_res = evaluate(m_t1, m_b1, cal, test)

        def fmt(res):
            nrfi_str = f"{res['w_nrfi']}/{res['n_strong_nrfi']}"
            yrfi_str = f"{res['w_yrfi']}/{res['n_strong_yrfi']}"
            return f"{nrfi_str:>10} {yrfi_str:>10} {res['brier']:>6.3f} {res['pl']:>+8.3f}"

        print(f"{ws}/{we}  {prod_res['n']:>6} {'prod':<8} {fmt(prod_res)}")
        print(f"{'':23s}  {'':>6} {'cand':<8} {fmt(cand_res)}")
        summary["prod"]["pl"] += prod_res["pl"]
        summary["prod"]["n"] += prod_res["n"]
        summary["cand"]["pl"] += cand_res["pl"]
        summary["cand"]["n"] += cand_res["n"]

    print()
    print(f"=== Walk-forward totals across {sum(1 for r in rows_2026 if date(2026,4,14).isoformat() <= r['date'] <= '2026-05-26')} graded rows ===")
    print(f"  Production (static train on 2024+2025):  P&L = {summary['prod']['pl']:+.3f}u")
    print(f"  Candidate (sliding window):              P&L = {summary['cand']['pl']:+.3f}u")
    print(f"  Net difference: {summary['cand']['pl'] - summary['prod']['pl']:+.3f}u")


if __name__ == "__main__":
    main()

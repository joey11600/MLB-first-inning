#!/usr/bin/env python3
"""
tools/sliding_window_eval.py -- head-to-head evaluation of the
candidate sliding-window refit vs current production on the 14-day
holdout (2026-05-12 to 2026-05-26).

For each test game:
  - Run BOTH models' T1+B1+calibrator pipelines
  - Compute calibrated P(NRFI) and resulting STRONG/PASS verdict
  - For STRONG verdicts, compute hypothetical P&L using the actual
    DK odds we logged (from picks_2026.csv) and the actual graded
    result.

Report:
  - Picks made by production vs candidate
  - W/L record and P&L for each side
  - Agreement / disagreement breakdown
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lr_baseline import LogReg
from calibration import ProbCalibrator

# Production paths
PROD_T1 = ROOT / "data" / "lr_t1.json"
PROD_B1 = ROOT / "data" / "lr_b1.json"
PROD_CAL = ROOT / "data" / "calibration_v2.json"

# Candidate paths
CAND_T1 = ROOT / "data" / "candidates" / "sliding_window" / "lr_t1.json"
CAND_B1 = ROOT / "data" / "candidates" / "sliding_window" / "lr_b1.json"
# Candidate calibrator we'll fit below
CAND_CAL = ROOT / "data" / "candidates" / "sliding_window" / "calibration.json"

# Data paths
PICKS_CSV = ROOT / "data" / "picks_2026.csv"
PARKS_JSON = ROOT / "data" / "fi_park_factors.json"
TRAIN_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
TRAIN_2026 = ROOT / "data" / "backtests" / "backtest_2026-04-01_to_2026-05-11_truepit.csv"
HOLDOUT   = ROOT / "data" / "backtests" / "backtest_2026-05-12_to_2026-05-26_truepit.csv"

# Pick thresholds (calibrated probability space, match production)
STRONG_NRFI_P = 0.56
STRONG_YRFI_P = 0.44

# Defaults / league averages (match two_stage_model.py + recalibrate_v2.py)
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


def gather_features(csv_path: Path, parks: dict):
    """Build T1 + B1 feature matrices from a truepit CSV (Phase E.3 + VSHAND)."""
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            try:
                fi_a = int(float(r.get("fi_away_runs", "") or "nan"))
                fi_h = int(float(r.get("fi_home_runs", "") or "nan"))
            except (ValueError, TypeError):
                continue
            home = r.get("home_team", "") or r.get("home", "")
            fi_park = parks.get(home, FI_PARK_DEFAULT)
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
            rows.append({
                "date":    r["date"],
                "game_pk": r["game_pk"],
                "away":    r.get("away_team","") or r.get("away",""),
                "home":    home,
                "t1": t1, "b1": b1,
                "t1_y": t1_y, "b1_y": b1_y, "y_nrfi": y_nrfi,
            })
    return rows


def classify(p_nrfi: float):
    if p_nrfi >= STRONG_NRFI_P:
        return "STRONG NRFI"
    if p_nrfi < STRONG_YRFI_P:
        return "STRONG YRFI"
    return "PASS"


def amer_to_payout(s: str):
    s = (s or "").strip()
    if not s: return None
    try: n = int(s)
    except: return None
    return n / 100.0 if n > 0 else 100.0 / abs(n)


def load_odds_map():
    """date,game_pk -> (nrfi_odds_payout, yrfi_odds_payout) -- for hypothetical P&L."""
    out = {}
    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] < "2026-05-12" or r["date"] > "2026-05-26":
                continue
            n = amer_to_payout(r.get("market_nrfi_odds",""))
            y = amer_to_payout(r.get("market_yrfi_odds",""))
            out[(r["date"], r["game_pk"])] = (n, y)
    return out


def main():
    parks = load_parks()

    # ---- Load models ----
    prod_t1 = LogReg.load(str(PROD_T1))
    prod_b1 = LogReg.load(str(PROD_B1))
    prod_cal = ProbCalibrator.load(PROD_CAL)
    cand_t1 = LogReg.load(str(CAND_T1))
    cand_b1 = LogReg.load(str(CAND_B1))

    # ---- Fit candidate calibrator on training data ----
    print("Fitting candidate calibrator on combined 2025 + 2026YTD raw predictions...")
    train_rows = gather_features(TRAIN_2025, parks) + gather_features(TRAIN_2026, parks)
    Xt1 = np.asarray([r["t1"] for r in train_rows])
    Xb1 = np.asarray([r["b1"] for r in train_rows])
    p_t1 = cand_t1.predict_proba(Xt1)
    p_b1 = cand_b1.predict_proba(Xb1)
    raw_nrfi = (1 - p_t1) * (1 - p_b1)
    actuals = np.asarray([r["y_nrfi"] for r in train_rows])
    cand_cal = ProbCalibrator.fit(
        predictions   = raw_nrfi.tolist(),
        actuals       = actuals.tolist(),
        n_bins        = 20,
        train_seasons = ["2025", "2026"],
    )
    CAND_CAL.parent.mkdir(parents=True, exist_ok=True)
    cand_cal.save(CAND_CAL)
    print(f"  candidate calibrator: {len(cand_cal.centers)} bins, n_train={len(train_rows)}")
    print()

    # ---- Score holdout with BOTH models ----
    holdout = gather_features(HOLDOUT, parks)
    Xt1 = np.asarray([r["t1"] for r in holdout])
    Xb1 = np.asarray([r["b1"] for r in holdout])
    pt_prod = prod_t1.predict_proba(Xt1)
    pb_prod = prod_b1.predict_proba(Xb1)
    raw_prod = (1 - pt_prod) * (1 - pb_prod)
    cal_prod = np.array([prod_cal.predict(float(p)) for p in raw_prod])
    pt_cand = cand_t1.predict_proba(Xt1)
    pb_cand = cand_b1.predict_proba(Xb1)
    raw_cand = (1 - pt_cand) * (1 - pb_cand)
    cal_cand = np.array([cand_cal.predict(float(p)) for p in raw_cand])

    odds = load_odds_map()

    def evaluate(label, cal_p_arr):
        n_strong_nrfi = n_strong_yrfi = n_pass = 0
        wins_nrfi = wins_yrfi = 0
        pl = 0.0
        for r, cal_p in zip(holdout, cal_p_arr):
            verdict = classify(cal_p)
            if verdict == "PASS":
                n_pass += 1
                continue
            actual_nrfi = r["y_nrfi"] == 1
            key = (r["date"], r["game_pk"])
            n_payout, y_payout = odds.get(key, (None, None))
            if verdict == "STRONG NRFI":
                n_strong_nrfi += 1
                if actual_nrfi: wins_nrfi += 1
                payout = n_payout if n_payout is not None else 100.0 / 110.0
                pl += payout if actual_nrfi else -1.0
            else:
                n_strong_yrfi += 1
                if not actual_nrfi: wins_yrfi += 1
                payout = y_payout if y_payout is not None else 100.0 / 110.0
                pl += payout if not actual_nrfi else -1.0
        n = n_strong_nrfi + n_strong_yrfi
        w = wins_nrfi + wins_yrfi
        print(f"{label}")
        print(f"  STRONG NRFI: {wins_nrfi}/{n_strong_nrfi} ({wins_nrfi/max(1,n_strong_nrfi)*100:.1f}%)")
        print(f"  STRONG YRFI: {wins_yrfi}/{n_strong_yrfi} ({wins_yrfi/max(1,n_strong_yrfi)*100:.1f}%)")
        print(f"  PASS:        {n_pass}")
        print(f"  Total STRONG: {n}  ({w}W/{n-w}L = {w/max(1,n)*100:.1f}%)")
        print(f"  P&L (at logged DK odds, -110 fallback): {pl:+.3f}u")
        print()

    print(f"Holdout: {len(holdout)} games (5/12 -> 5/26)")
    print(f"Actual NRFI rate: {sum(r['y_nrfi'] for r in holdout) / len(holdout) * 100:.1f}%")
    print()
    evaluate("=== PRODUCTION (current lr_t1/lr_b1 + calibration_v2) ===", cal_prod)
    evaluate("=== CANDIDATE (sliding window refit) ===", cal_cand)

    # ---- Disagreement table ----
    print("=== Disagreements (production STRONG vs candidate STRONG) ===")
    disagreements = []
    for r, cp_prod, cp_cand in zip(holdout, cal_prod, cal_cand):
        vp = classify(float(cp_prod))
        vc = classify(float(cp_cand))
        if vp != vc:
            disagreements.append((r, cp_prod, cp_cand, vp, vc))
    if not disagreements:
        print("  (none)")
    else:
        print(f"  count: {len(disagreements)}")
        print(f"  {'date':>10} {'matchup':>10} {'cal_prod':>9} {'verdict_prod':>13} {'cal_cand':>9} {'verdict_cand':>13} {'actual':>6}")
        for r, cpp, cpc, vp, vc in disagreements:
            actual = "NRFI" if r["y_nrfi"] == 1 else "YRFI"
            print(f"  {r['date']:>10} {r['away']+'@'+r['home']:>10} {cpp:>9.4f} {vp:>13} {cpc:>9.4f} {vc:>13} {actual:>6}")


if __name__ == "__main__":
    main()

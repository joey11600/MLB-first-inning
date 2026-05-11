#!/usr/bin/env python3
"""tools/v22_feature_ablation.py

R8 follow-up: test whether dropping top3c_slg (or top3c_iso) from the
LR feature set fixes the multicollinearity seesaw and improves elite-
offense game predictions.

Strategy: refit T1 + B1 LRs on 2024+2025 truepit data with each
variant; evaluate Brier on full 2026 picks AND on the elite-power
subset specifically.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recalibrate_v2 import (
    load_fi_park, T1_FEATURES, B1_FEATURES,
    BT_2025_PATH, PICKS_2026,
)
from lr_baseline import LogReg

BT_24 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_SLG = 0.414
LEAGUE_AVG_ISO = 0.169
FI_PARK_DEFAULT = 0.50
WX_TEMP_DEFAULT, WX_WIND_DEFAULT, WX_HUMIDITY_DEFAULT = 20.0, 10.0, 60.0
LEAGUE_NRFI_RATE = 0.50
LEAGUE_AVG_XERA = 4.20
NEUTRAL_PCT_RANK = 50


def coerce(s, d):
    try:
        return float(s)
    except (TypeError, ValueError):
        return d


def build_features(r, fipark, is_pick):
    home = r.get("home_team", "") if is_pick else r.get("home", "")
    fi_park = (fipark.get(home, FI_PARK_DEFAULT)
               if fipark else coerce(r.get("fi_park_nrfi_rate"), FI_PARK_DEFAULT))
    wx = [coerce(r.get("wx_temp_c"), WX_TEMP_DEFAULT),
          coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
          coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
          coerce(r.get("wx_is_dome"), 0.0)]
    ump = coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI_RATE)
    h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
    t1 = {
        "fi_park_nrfi_rate": fi_park,
        "home_fip":          coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        "away_obp":          coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
        "wx_temp_c":         wx[0], "wx_wind_kmh": wx[1],
        "wx_humidity":       wx[2], "wx_is_dome":  wx[3],
        "home_p_last5_pitcher_nrfi":  coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        "away_top3c_obp":             coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP),
        "home_plate_ump_nrfi_rate":   ump,
        "home_xera":                  coerce(r.get("home_xera"), LEAGUE_AVG_XERA),
        "home_whiff_pct_rank":        coerce(r.get("home_whiff_pct_rank"), NEUTRAL_PCT_RANK),
        "era_gap_t1":                 h_era - a_era,
        "home_p_last10_pitcher_nrfi": coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        "away_top3c_slg":             coerce(r.get("away_top3c_slg"), LEAGUE_AVG_SLG),
        "away_top3c_iso":             coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO),
        "home_pvt_nrfi_rate":         coerce(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI_RATE),
        "home_avg_ip_per_start":      coerce(r.get("home_avg_ip_per_start"), 5.0),
    }
    b1 = {
        "fi_park_nrfi_rate": fi_park,
        "away_fip":          coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        "home_obp":          coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        "wx_temp_c":         wx[0], "wx_wind_kmh": wx[1],
        "wx_humidity":       wx[2], "wx_is_dome":  wx[3],
        "away_p_last5_pitcher_nrfi":  coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        "home_top3c_obp":             coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP),
        "home_plate_ump_nrfi_rate":   ump,
        "away_xera":                  coerce(r.get("away_xera"), LEAGUE_AVG_XERA),
        "away_whiff_pct_rank":        coerce(r.get("away_whiff_pct_rank"), NEUTRAL_PCT_RANK),
        "era_gap_b1":                 a_era - h_era,
        "away_p_last10_pitcher_nrfi": coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        "home_top3c_slg":             coerce(r.get("home_top3c_slg"), LEAGUE_AVG_SLG),
        "home_top3c_iso":             coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO),
        "away_pvt_nrfi_rate":         coerce(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI_RATE),
        "away_avg_ip_per_start":      coerce(r.get("away_avg_ip_per_start"), 5.0),
    }
    return t1, b1


def gather(csv_path, fipark, is_pick):
    Xt, Xb, y_pairs = [], [], []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") if not is_pick else r.get("actual_result")) or ""
            actual = actual.upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            t1r = r.get("fi_away_runs", "")
            b1r = r.get("fi_home_runs", "")
            if t1r == "" or b1r == "":
                continue
            try:
                yt1 = 1 if int(float(t1r)) > 0 else 0
                yb1 = 1 if int(float(b1r)) > 0 else 0
            except (TypeError, ValueError):
                continue
            try:
                t1, b1 = build_features(r, fipark, is_pick)
            except Exception:
                continue
            Xt.append(t1)
            Xb.append(b1)
            y_pairs.append((yt1, yb1, 1 if actual == "NRFI" else 0))
    return Xt, Xb, y_pairs


def fit_evaluate(t1_feats, b1_feats, Xt_tr, Xb_tr, y_tr,
                  Xt_te, Xb_te, y_te, l2: float, label: str) -> tuple:
    Xt_tr_a = np.array([[r[f] for f in t1_feats] for r in Xt_tr])
    Xb_tr_a = np.array([[r[f] for f in b1_feats] for r in Xb_tr])
    yt_tr_a = np.array([y[0] for y in y_tr])
    yb_tr_a = np.array([y[1] for y in y_tr])
    Xt_te_a = np.array([[r[f] for f in t1_feats] for r in Xt_te])
    Xb_te_a = np.array([[r[f] for f in b1_feats] for r in Xb_te])
    y_te_a  = np.array([y[2] for y in y_te])

    m_t1 = LogReg.fit(Xt_tr_a, yt_tr_a, t1_feats, l2=l2)
    m_b1 = LogReg.fit(Xb_tr_a, yb_tr_a, b1_feats, l2=l2)
    p_t1 = m_t1.predict_proba(Xt_te_a)
    p_b1 = m_b1.predict_proba(Xb_te_a)
    p_nrfi = (1 - p_t1) * (1 - p_b1)
    brier_full = float(np.mean((p_nrfi - y_te_a) ** 2))

    coefs = dict(zip(t1_feats, m_t1.w))
    iso_w = coefs.get("away_top3c_iso", 0.0)
    slg_w = coefs.get("away_top3c_slg", 0.0)
    print(f"  {label:<28}  Brier={brier_full:.4f}  iso={iso_w:+.4f}  slg={slg_w:+.4f}")

    # Elite-power slice
    elite_idx = [i for i, r in enumerate(Xt_te)
                 if max(r["away_top3c_iso"], Xb_te[i]["home_top3c_iso"]) >= 0.25]
    if elite_idx:
        p_e = p_nrfi[elite_idx]
        y_e = y_te_a[elite_idx]
        b_e = float(np.mean((p_e - y_e) ** 2))
        bias_e = float((p_e.mean() - y_e.mean()) * 100)
        print(f"      elite-power (n={len(elite_idx)}):  Brier={b_e:.4f}  bias={bias_e:+.2f}pp")
    return m_t1, m_b1, brier_full


def main():
    print("Loading data...")
    fipark = load_fi_park()
    Xt_24, Xb_24, y_24 = gather(BT_24, fipark, is_pick=False)
    Xt_25, Xb_25, y_25 = gather(BT_2025_PATH, fipark, is_pick=False)
    Xt_26, Xb_26, y_26 = gather(PICKS_2026, fipark, is_pick=True)
    print(f"  2024: {len(y_24)}, 2025: {len(y_25)}, 2026: {len(y_26)}")

    Xt_tr = Xt_24 + Xt_25
    Xb_tr = Xb_24 + Xb_25
    y_tr  = y_24 + y_25
    print(f"  combined train: {len(y_tr)}")

    print(f"\nFeature variant Brier on 2026:")
    fit_evaluate(T1_FEATURES, B1_FEATURES,
                 Xt_tr, Xb_tr, y_tr, Xt_26, Xb_26, y_26,
                 l2=0.05, label="FULL (current production)")

    no_slg_t1 = [f for f in T1_FEATURES if f != "away_top3c_slg"]
    no_slg_b1 = [f for f in B1_FEATURES if f != "home_top3c_slg"]
    fit_evaluate(no_slg_t1, no_slg_b1,
                 Xt_tr, Xb_tr, y_tr, Xt_26, Xb_26, y_26,
                 l2=0.05, label="drop SLG (keep ISO)")

    no_iso_t1 = [f for f in T1_FEATURES if f != "away_top3c_iso"]
    no_iso_b1 = [f for f in B1_FEATURES if f != "home_top3c_iso"]
    fit_evaluate(no_iso_t1, no_iso_b1,
                 Xt_tr, Xb_tr, y_tr, Xt_26, Xb_26, y_26,
                 l2=0.05, label="drop ISO (keep SLG)")


if __name__ == "__main__":
    main()

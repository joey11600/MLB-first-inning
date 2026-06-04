#!/usr/bin/env python3
"""
tools/recent_form_retrain_test.py -- does adding TOP-3 BATTER RECENT FORM
(last-10-game OBP/SLG/ISO) to the model improve out-of-sample accuracy?

The live model (Phase E.3 + VSHAND, 19 feat/half) uses SEASON batter
stats and pitcher recent form, but NOT batter recent form -- even though
the last-10 features are backfilled for all 2024+2025+2026 games.  This
is the operator's "streakiness" hypothesis tested the decisive way: train
BASE (current features) vs TREAT (current + last-10 batter form) on the
SAME data, evaluate on a clean holdout, let the regression find any
signal (momentum, reversion, interaction).

Decisive metric = Brier (lower = more accurate).  Also STRONG-zone hit
rates + flat-(-110) P&L.

Splits (both fully last-10-covered):
  A. train 2024, test 2025   (clean OOS, the primary read)
  B. train 2024+2025, test 2026 (production-relevant; 2026 last-10 ~60% covered)
Read-only.
"""
from __future__ import annotations
import csv, glob, sys
from pathlib import Path
try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lr_baseline import LogReg
from calibration import ProbCalibrator
from sliding_window_eval import (  # noqa: F401
    load_parks, coerce,
    LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, LEAGUE_AVG_ISO,
    LEAGUE_NRFI_RATE, LEAGUE_AVG_XERA, LEAGUE_AVG_OPS_VSHAND,
    NEUTRAL_PCT_RANK, FI_PARK_DEFAULT, WX_TEMP_DEFAULT, WX_WIND_DEFAULT,
    WX_HUMIDITY_DEFAULT,
)

STRONG_NRFI_P, STRONG_YRFI_P = 0.56, 0.44   # use the OLD 0.56 so both variants see the same zone


def gather(path, parks, treat):
    """Return list of dicts with t1/b1 feature vectors + labels.
    treat=True appends the 3 last-10 batter features per side."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or "").upper()
            try:
                fa = int(float(r.get("fi_away_runs", "") or "nan"))
                fh = int(float(r.get("fi_home_runs", "") or "nan"))
            except (ValueError, TypeError):
                continue
            home = r.get("home_team", "") or r.get("home", "")
            fi_park = parks.get(home, FI_PARK_DEFAULT)
            if r.get("fi_park_nrfi_rate"):
                fi_park = coerce(r.get("fi_park_nrfi_rate"), fi_park)
            wx = [coerce(r.get("wx_temp_c"), WX_TEMP_DEFAULT), coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
                  coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT), coerce(r.get("wx_is_dome"), 0.0)]
            ump = coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI_RATE)
            h_era, a_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA), coerce(r.get("away_era"), LEAGUE_AVG_ERA)

            def base(side_p, off, pitch_x, pitch_w, last5, last10, top_slg, top_iso, top_obp, pvt, aip, vshand, gap):
                v = [fi_park, coerce(r.get(pitch_x[0]), LEAGUE_AVG_ERA), coerce(r.get(off+"_obp"), LEAGUE_AVG_OBP)] + wx + [
                    coerce(r.get(last5), LEAGUE_NRFI_RATE), coerce(r.get(top_obp), LEAGUE_AVG_OBP), ump,
                    coerce(r.get(pitch_x[1]), LEAGUE_AVG_XERA), coerce(r.get(pitch_w), NEUTRAL_PCT_RANK), gap,
                    coerce(r.get(last10), LEAGUE_NRFI_RATE), coerce(r.get(top_slg), LEAGUE_AVG_SLG),
                    coerce(r.get(top_iso), LEAGUE_AVG_ISO), coerce(r.get(pvt), LEAGUE_NRFI_RATE),
                    coerce(r.get(aip), 5.0), coerce(r.get(vshand), LEAGUE_AVG_OPS_VSHAND)]
                return v

            t1 = base("home", "away", ("home_fip","home_xera"), "home_whiff_pct_rank",
                      "home_p_last5_pitcher_nrfi", "home_p_last10_pitcher_nrfi",
                      "away_top3c_slg", "away_top3c_iso", "away_top3c_obp",
                      "home_pvt_nrfi_rate", "home_avg_ip_per_start", "away_top3_ops_vs_oppHand", h_era - a_era)
            b1 = base("away", "home", ("away_fip","away_xera"), "away_whiff_pct_rank",
                      "away_p_last5_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
                      "home_top3c_slg", "home_top3c_iso", "home_top3c_obp",
                      "away_pvt_nrfi_rate", "away_avg_ip_per_start", "home_top3_ops_vs_oppHand", a_era - h_era)
            if treat:
                # append last-10 batter form (default to SEASON value -> 0 incremental info when missing)
                t1 += [coerce(r.get("away_top3c_last10_obp"), coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP)),
                       coerce(r.get("away_top3c_last10_slg"), coerce(r.get("away_top3c_slg"), LEAGUE_AVG_SLG)),
                       coerce(r.get("away_top3c_last10_iso"), coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO))]
                b1 += [coerce(r.get("home_top3c_last10_obp"), coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP)),
                       coerce(r.get("home_top3c_last10_slg"), coerce(r.get("home_top3c_slg"), LEAGUE_AVG_SLG)),
                       coerce(r.get("home_top3c_last10_iso"), coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO))]
            rows.append({"t1": t1, "b1": b1, "t1y": 1 if fa > 0 else 0, "b1y": 1 if fh > 0 else 0,
                         "ynrfi": 1 if (fa + fh) == 0 else 0})
    return rows


def fit_eval(train_rows, test_rows, label):
    Xt = np.asarray([r["t1"] for r in train_rows]); yt = np.asarray([r["t1y"] for r in train_rows])
    Xb = np.asarray([r["b1"] for r in train_rows]); yb = np.asarray([r["b1y"] for r in train_rows])
    fn = [f"f{i}" for i in range(Xt.shape[1])]
    m_t1 = LogReg.fit(Xt, yt, fn, l2=0.05); m_b1 = LogReg.fit(Xb, yb, fn, l2=0.05)
    raw_tr = (1 - m_t1.predict_proba(Xt)) * (1 - m_b1.predict_proba(Xb))
    cal = ProbCalibrator.fit(raw_tr.tolist(), [r["ynrfi"] for r in train_rows], n_bins=20)
    Xt2 = np.asarray([r["t1"] for r in test_rows]); Xb2 = np.asarray([r["b1"] for r in test_rows])
    raw = (1 - m_t1.predict_proba(Xt2)) * (1 - m_b1.predict_proba(Xb2))
    cp = np.array([cal.predict(float(p)) for p in raw])
    y = np.array([r["ynrfi"] for r in test_rows])
    brier = float(np.mean((cp - y) ** 2))
    snrfi = [(p, yy) for p, yy in zip(cp, y) if p >= STRONG_NRFI_P]
    syrfi = [(p, yy) for p, yy in zip(cp, y) if p < STRONG_YRFI_P]
    nw = sum(1 for p, yy in snrfi if yy == 1); yw = sum(1 for p, yy in syrfi if yy == 0)
    pl = sum((100/110 if yy == 1 else -1) for p, yy in snrfi) + sum((100/110 if yy == 0 else -1) for p, yy in syrfi)
    print(f"  {label:<26} Brier={brier:.4f}  STRONG_NRFI {nw}/{len(snrfi)} ({nw/max(1,len(snrfi))*100:.0f}%)  "
          f"STRONG_YRFI {yw}/{len(syrfi)} ({yw/max(1,len(syrfi))*100:.0f}%)  flat-110 P&L {pl:+.1f}u")
    return brier, pl


def run(train_glob, test_glob, name, parks):
    tr_files = sorted(sum((glob.glob(g) for g in train_glob), []))
    te_files = sorted(sum((glob.glob(g) for g in test_glob), []))
    print(f"\n=== {name}  (train {[Path(x).name[:16] for x in tr_files]} -> test {[Path(x).name[:16] for x in te_files]}) ===")
    for treat, lbl in [(False, "BASE (current feats)"), (True, "TREAT (+ recent form)")]:
        tr = sum((gather(p, parks, treat) for p in tr_files), [])
        te = sum((gather(p, parks, treat) for p in te_files), [])
        fit_eval(tr, te, lbl)
    print("  (lower Brier = more accurate.  TREAT must clearly beat BASE to justify shipping.)")


def main():
    parks = load_parks()
    run(["data/backtests/backtest_2024-*_truepit.csv"], ["data/backtests/backtest_2025-*_truepit.csv"],
        "SPLIT A: train 2024 -> test 2025 (clean OOS, full last-10 coverage)", parks)
    run(["data/backtests/backtest_2024-*_truepit.csv", "data/backtests/backtest_2025-*_truepit.csv"],
        ["data/backtests/backtest_2026-04-01_to_2026-05-11_truepit.csv"],
        "SPLIT B: train 2024+2025 -> test 2026YTD (production-relevant)", parks)


if __name__ == "__main__":
    main()

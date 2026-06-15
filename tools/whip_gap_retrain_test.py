#!/usr/bin/env python3
"""tools/whip_gap_retrain_test.py -- does a SIGNED WHIP GAP feature improve
out-of-sample 1st-inning prediction (esp. NRFI)?

whip_gap is the WHIP analogue of the already-shipped era_gap (which was
worth +22u in its own 3-split). A 1st-inning run is mostly a baserunner
problem; WHIP (walks+hits/IP) captures command+contact that FIP/xERA fold
away. Signed convention matches era_gap:
   whip_gap_t1 = home_whip - away_whip   (T1: home pitcher pitches; + = home worse)
   whip_gap_b1 = away_whip - home_whip

BASE = current production feature set (Phase E.3 + VSHAND, 19 feat/half).
TREAT = BASE + whip_gap.  Same data, same calibrator, OOS holdout.

Decisive metric = Brier (lower=better). Also STRONG-zone hit + flat-110 P&L,
split NRFI vs YRFI so we can see if whip_gap helps the WEAK (NRFI) side.

3-split (reject anything that helps only one direction):
  A. train 2024 -> test 2025
  B. train 2025 -> test 2024
  C. train 2024+2025 -> test 2026YTD
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

STRONG_NRFI_P, STRONG_YRFI_P = 0.62, 0.44   # current production thresholds
LEAGUE_AVG_WHIP = 1.30


def gather(path, parks, treat):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
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
            h_whip, a_whip = coerce(r.get("home_whip"), LEAGUE_AVG_WHIP), coerce(r.get("away_whip"), LEAGUE_AVG_WHIP)

            # BASE = Phase E.3 + VSHAND, mirroring two_stage_model.py:262-301
            def half(off, pit, last5, top_obp, xera, whiff, gap, last10, top_slg, top_iso, pvt, aip, vshand):
                return [fi_park, coerce(r.get(pit + "_fip"), LEAGUE_AVG_ERA), coerce(r.get(off + "_obp"), LEAGUE_AVG_OBP)] + wx + [
                    coerce(r.get(last5), LEAGUE_NRFI_RATE), coerce(r.get(top_obp), LEAGUE_AVG_OBP), ump,
                    coerce(r.get(xera), LEAGUE_AVG_XERA), coerce(r.get(whiff), NEUTRAL_PCT_RANK), gap,
                    coerce(r.get(last10), LEAGUE_NRFI_RATE), coerce(r.get(top_slg), LEAGUE_AVG_SLG),
                    coerce(r.get(top_iso), LEAGUE_AVG_ISO), coerce(r.get(pvt), LEAGUE_NRFI_RATE),
                    coerce(r.get(aip), 5.0), coerce(r.get(vshand), LEAGUE_AVG_OPS_VSHAND)]
            t1 = half("away", "home", "home_p_last5_pitcher_nrfi", "away_top3c_obp", "home_xera",
                      "home_whiff_pct_rank", h_era - a_era, "home_p_last10_pitcher_nrfi",
                      "away_top3c_slg", "away_top3c_iso", "home_pvt_nrfi_rate", "home_avg_ip_per_start",
                      "away_top3_ops_vs_oppHand")
            b1 = half("home", "away", "away_p_last5_pitcher_nrfi", "home_top3c_obp", "away_xera",
                      "away_whiff_pct_rank", a_era - h_era, "away_p_last10_pitcher_nrfi",
                      "home_top3c_slg", "home_top3c_iso", "away_pvt_nrfi_rate", "away_avg_ip_per_start",
                      "home_top3_ops_vs_oppHand")
            if treat:
                t1 = t1 + [h_whip - a_whip]   # whip_gap_t1
                b1 = b1 + [a_whip - h_whip]   # whip_gap_b1
            rows.append({"t1": t1, "b1": b1, "t1y": 1 if fa > 0 else 0, "b1y": 1 if fh > 0 else 0,
                         "ynrfi": 1 if (fa + fh) == 0 else 0})
    return rows


def fit_eval(tr, te, label):
    Xt = np.asarray([r["t1"] for r in tr]); yt = np.asarray([r["t1y"] for r in tr])
    Xb = np.asarray([r["b1"] for r in tr]); yb = np.asarray([r["b1y"] for r in tr])
    fn = [f"f{i}" for i in range(Xt.shape[1])]
    m_t1 = LogReg.fit(Xt, yt, fn, l2=0.05); m_b1 = LogReg.fit(Xb, yb, fn, l2=0.05)
    raw_tr = (1 - m_t1.predict_proba(Xt)) * (1 - m_b1.predict_proba(Xb))
    cal = ProbCalibrator.fit(raw_tr.tolist(), [r["ynrfi"] for r in tr], n_bins=20)
    Xt2 = np.asarray([r["t1"] for r in te]); Xb2 = np.asarray([r["b1"] for r in te])
    raw = (1 - m_t1.predict_proba(Xt2)) * (1 - m_b1.predict_proba(Xb2))
    cp = np.array([cal.predict(float(p)) for p in raw])
    y = np.array([r["ynrfi"] for r in te])
    brier = float(np.mean((cp - y) ** 2))
    snrfi = [(p, yy) for p, yy in zip(cp, y) if p >= STRONG_NRFI_P]
    syrfi = [(p, yy) for p, yy in zip(cp, y) if p < STRONG_YRFI_P]
    nw = sum(1 for p, yy in snrfi if yy == 1); yw = sum(1 for p, yy in syrfi if yy == 0)
    pl_n = sum((100/110 if yy == 1 else -1) for p, yy in snrfi)
    pl_y = sum((100/110 if yy == 0 else -1) for p, yy in syrfi)
    print(f"  {label:<22} Brier={brier:.4f}  "
          f"NRFI {nw}/{len(snrfi)} ({nw/max(1,len(snrfi))*100:.0f}%, {pl_n:+.1f}u)  "
          f"YRFI {yw}/{len(syrfi)} ({yw/max(1,len(syrfi))*100:.0f}%, {pl_y:+.1f}u)")
    return brier, pl_n, pl_y


def run(train_glob, test_glob, name, parks):
    tr_files = sorted(sum((glob.glob(str(ROOT / g)) for g in train_glob), []))
    te_files = sorted(sum((glob.glob(str(ROOT / g)) for g in test_glob), []))
    print(f"\n=== {name} ===")
    res = {}
    for treat, lbl in [(False, "BASE (E3+VSHAND)"), (True, "TREAT (+whip_gap)")]:
        tr = sum((gather(p, parks, treat) for p in tr_files), [])
        te = sum((gather(p, parks, treat) for p in te_files), [])
        res[lbl] = fit_eval(tr, te, lbl)
    db = res["TREAT (+whip_gap)"][0] - res["BASE (E3+VSHAND)"][0]
    print(f"  -> Brier delta (TREAT-BASE): {db:+.4f}  ({'BETTER' if db < 0 else 'WORSE/none'})  "
          f"[ship needs clearly better in ALL 3 splits]")


def main():
    parks = load_parks()
    run(["data/backtests/backtest_2024-*_truepit.csv"], ["data/backtests/backtest_2025-*_truepit.csv"],
        "SPLIT A: train 2024 -> test 2025", parks)
    run(["data/backtests/backtest_2025-*_truepit.csv"], ["data/backtests/backtest_2024-*_truepit.csv"],
        "SPLIT B: train 2025 -> test 2024", parks)
    run(["data/backtests/backtest_2024-*_truepit.csv", "data/backtests/backtest_2025-*_truepit.csv"],
        ["data/backtests/backtest_2026-04-01_to_2026-05-11_truepit.csv"],
        "SPLIT C: train 2024+2025 -> test 2026YTD", parks)


if __name__ == "__main__":
    main()

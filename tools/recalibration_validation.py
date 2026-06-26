#!/usr/bin/env python3
"""tools/recalibration_validation.py -- does refitting the isotonic
calibrator (LR weights FROZEN) beat the stale 5/26 calibrator
out-of-sample, and does it help or HURT YRFI?

Motivation: reliability since 5/26 shows the model is overconfident at the
extremes (0.40-0.44 band: says 41.5% NRFI, actual 50.5%). That's a
calibration drift a fresh calibrator could fix.

Method (no look-ahead):
  - Run the FROZEN production LR (lr_t1/b1.json) forward on all graded
    games -> raw P(NRFI). Weights untouched -- only the calibrator changes.
  - WALK-FORWARD over recent weeks: for each week, fit a fresh calibrator
    on ALL data before that week, compare frozen-vs-fresh on that week:
      * Brier (accuracy)
      * YRFI betting: a STRONG YRFI bet = calibrated<0.44 AND lambda>=0.838.
        Simulate each calibrator's YRFI bet set at real captured odds.
  - Sanity check: recomputed frozen cal should ~match stored nrfi_prob.

Ship a recalibration only if fresh BEATS frozen on Brier AND does not hurt
YRFI P&L across the weeks. Read-only.
"""
from __future__ import annotations
import csv, glob, json, math, sys
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

YRFI_FLOOR, STRONG_YRFI_P = 0.838, 0.44
M_T1 = LogReg.load(ROOT / "data" / "lr_t1.json")
M_B1 = LogReg.load(ROOT / "data" / "lr_b1.json")
_cj = json.load(open(ROOT / "data" / "calibration_v2.json", encoding="utf-8"))
FROZEN = ProbCalibrator(_cj["centers"], _cj["rates"])


def imp(a):
    s = (a or "").strip()
    try: n = int(float(s))
    except (ValueError, TypeError): return None
    return (abs(n)/(abs(n)+100)) if n < 0 else (100/(n+100))


def payout(a):
    s = (a or "").strip()
    try: n = int(float(s))
    except (ValueError, TypeError): return None
    return (n/100.0) if n > 0 else (100.0/abs(n))


def half(r, off, pit, last5, top_obp, xera, whiff, gap, last10, top_slg, top_iso, pvt, aip, vshand, wx, fi_park, ump):
    return [fi_park, coerce(r.get(pit+"_fip"), LEAGUE_AVG_ERA), coerce(r.get(off+"_obp"), LEAGUE_AVG_OBP)] + wx + [
        coerce(r.get(last5), LEAGUE_NRFI_RATE), coerce(r.get(top_obp), LEAGUE_AVG_OBP), ump,
        coerce(r.get(xera), LEAGUE_AVG_XERA), coerce(r.get(whiff), NEUTRAL_PCT_RANK), gap,
        coerce(r.get(last10), LEAGUE_NRFI_RATE), coerce(r.get(top_slg), LEAGUE_AVG_SLG),
        coerce(r.get(top_iso), LEAGUE_AVG_ISO), coerce(r.get(pvt), LEAGUE_NRFI_RATE),
        coerce(r.get(aip), 5.0), coerce(r.get(vshand), LEAGUE_AVG_OPS_VSHAND)]


def gather(path, parks, is2026):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            fa = int(float(r.get("fi_away_runs", "") or "nan")); fh = int(float(r.get("fi_home_runs", "") or "nan"))
        except (ValueError, TypeError): continue
        home = r.get("home_team", "") or r.get("home", "")
        fi_park = parks.get(home, FI_PARK_DEFAULT)
        if r.get("fi_park_nrfi_rate"): fi_park = coerce(r.get("fi_park_nrfi_rate"), fi_park)
        wx = [coerce(r.get("wx_temp_c"), WX_TEMP_DEFAULT), coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
              coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT), coerce(r.get("wx_is_dome"), 0.0)]
        ump = coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI_RATE)
        he, ae = coerce(r.get("home_era"), LEAGUE_AVG_ERA), coerce(r.get("away_era"), LEAGUE_AVG_ERA)
        t1 = half(r, "away", "home", "home_p_last5_pitcher_nrfi", "away_top3c_obp", "home_xera", "home_whiff_pct_rank",
                  he-ae, "home_p_last10_pitcher_nrfi", "away_top3c_slg", "away_top3c_iso", "home_pvt_nrfi_rate",
                  "home_avg_ip_per_start", "away_top3_ops_vs_oppHand", wx, fi_park, ump)
        b1 = half(r, "home", "away", "away_p_last5_pitcher_nrfi", "home_top3c_obp", "away_xera", "away_whiff_pct_rank",
                  ae-he, "away_p_last10_pitcher_nrfi", "home_top3c_slg", "home_top3c_iso", "away_pvt_nrfi_rate",
                  "away_avg_ip_per_start", "home_top3_ops_vs_oppHand", wx, fi_park, ump)
        pt1 = M_T1.predict_proba_one(t1); pb1 = M_B1.predict_proba_one(b1)
        raw = (1-pt1)*(1-pb1)
        lam = coerce(r.get("lambda_lr_total"), -math.log(max(1e-9, 1-pt1)) - math.log(max(1e-9, 1-pb1)))
        d = {"raw": raw, "nrfi": 1 if (fa+fh) == 0 else 0, "date": r.get("date", ""), "lam": lam}
        if is2026:
            d["yodds"] = r.get("market_yrfi_odds", "")
            d["stored_cal"] = coerce(r.get("nrfi_prob"), None)
        rows.append(d)
    return rows


def main():
    parks = load_parks()
    pool = []
    for g in ["data/backtests/backtest_2024-*_truepit.csv", "data/backtests/backtest_2025-*_truepit.csv"]:
        for p in glob.glob(str(ROOT/g)): pool += gather(p, parks, False)
    p26 = gather(str(ROOT/"data"/"picks_2026.csv"), parks, True)
    pool += p26
    print(f"pool: {len(pool)} games ({len(p26)} are 2026)")

    # Sanity: recomputed frozen-cal vs stored nrfi_prob on 2026
    chk = [(FROZEN.predict(x["raw"]), x["stored_cal"]) for x in p26 if x.get("stored_cal") is not None]
    if chk:
        mad = sum(abs(a-b) for a, b in chk)/len(chk)
        print(f"SANITY: recomputed frozen cal vs stored nrfi_prob, mean abs diff = {mad:.4f} "
              f"({'OK <0.03' if mad < 0.03 else 'WARN -- pipeline may differ from prod'})\n")

    # FAIR baseline: a "stale" calibrator fit on the SAME pipeline through
    # 5/26 (mimics the frozen production cutoff, but built from my raw so
    # the comparison is apples-to-apples -- no pipeline-mismatch advantage).
    stale_train = [x for x in pool if x["date"] < "2026-05-26"]
    STALE = ProbCalibrator.fit([x["raw"] for x in stale_train], [x["nrfi"] for x in stale_train])

    weeks = [("2026-06-01", "2026-06-08"), ("2026-06-08", "2026-06-15"), ("2026-06-15", "2026-06-25")]
    tot = {"bf": 0.0, "bF": 0.0, "n": 0,
           "fz_pl": 0.0, "fz_n": 0, "fz_w": 0, "fr_pl": 0.0, "fr_n": 0, "fr_w": 0}
    print(f"  (baseline = STALE calibrator fit through 5/26 on same pipeline)")
    print(f"  {'week':<22}{'Brier stale':>12}{'Brier fresh':>12}{'YRFI stale':>16}{'YRFI fresh':>16}")
    for ws, we in weeks:
        train = [x for x in pool if x["date"] < ws]
        fresh = ProbCalibrator.fit([x["raw"] for x in train], [x["nrfi"] for x in train])
        hold = [x for x in p26 if ws <= x["date"] < we]
        bf = bF = 0.0; fz = [0, 0, 0.0]; fr = [0, 0, 0.0]  # n,w,pl
        for x in hold:
            cf, cF = STALE.predict(x["raw"]), fresh.predict(x["raw"])
            bf += (cf-x["nrfi"])**2; bF += (cF-x["nrfi"])**2
            ywon = x["nrfi"] == 0; pay = payout(x["yodds"]) or (100/110)
            if cf < STRONG_YRFI_P and x["lam"] >= YRFI_FLOOR:
                fz[0]+=1; fz[1]+=ywon; fz[2]+= (pay if ywon else -1)
            if cF < STRONG_YRFI_P and x["lam"] >= YRFI_FLOOR:
                fr[0]+=1; fr[1]+=ywon; fr[2]+= (pay if ywon else -1)
        n = len(hold)
        print(f"  {ws+'->'+we:<22}{bf/n:>11.4f}{bF/n:>12.4f}"
              f"{f'{fz[1]}-{fz[0]-fz[1]} {fz[2]:+.1f}u':>16}{f'{fr[1]}-{fr[0]-fr[1]} {fr[2]:+.1f}u':>16}")
        tot["bf"]+=bf; tot["bF"]+=bF; tot["n"]+=n
        tot["fz_pl"]+=fz[2]; tot["fz_n"]+=fz[0]; tot["fz_w"]+=fz[1]
        tot["fr_pl"]+=fr[2]; tot["fr_n"]+=fr[0]; tot["fr_w"]+=fr[1]
    print(f"\n  TOTAL Brier: stale {tot['bf']/tot['n']:.4f}  vs fresh {tot['bF']/tot['n']:.4f}  "
          f"(delta {(tot['bF']-tot['bf'])/tot['n']:+.4f}, {'fresh better' if tot['bF']<tot['bf'] else 'stale better/none'})")
    print(f"  TOTAL YRFI bets: stale {tot['fz_w']}-{tot['fz_n']-tot['fz_w']} {tot['fz_pl']:+.2f}u   "
          f"fresh {tot['fr_w']}-{tot['fr_n']-tot['fr_w']} {tot['fr_pl']:+.2f}u")
    print(f"  -> recalibration {'HELPS' if tot['fr_pl']>tot['fz_pl'] and tot['bF']<tot['bf'] else 'does NOT clearly help'} "
          f"(YRFI delta {tot['fr_pl']-tot['fz_pl']:+.2f}u)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""tools/full_retrain_validation.py -- does a FULL retrain (LR weights +
calibrator) on fresh data beat the 5/26-vintage model? The real "recalibrate
again" test (the calibrator-only refit was a null -- see
recalibration_validation.py).

Fair walk-forward, same pipeline both sides (no production-mismatch edge):
  STALE = full model (LR_t1, LR_b1, calibrator) trained on pool < 2026-05-26.
  For each recent week W:
    FRESH = full model retrained on pool < W.
    Evaluate both on week W (2026 holdout): Brier + YRFI betting P&L
    (STRONG YRFI = calibrated<0.44 AND lambda>=0.838, at real odds).

Ship a retrain only if FRESH beats STALE on Brier AND YRFI across the weeks.
Read-only. ~4 model fits; takes ~10-20s.
"""
from __future__ import annotations
import csv, glob, math, sys
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
FN = [f"f{i}" for i in range(19)]


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
        d = {"t1": t1, "b1": b1, "t1y": 1 if fa > 0 else 0, "b1y": 1 if fh > 0 else 0,
             "nrfi": 1 if (fa+fh) == 0 else 0, "date": r.get("date", "")}
        if is2026:
            d["yodds"] = r.get("market_yrfi_odds", "")
            d["lam"] = coerce(r.get("lambda_lr_total"), None)
        rows.append(d)
    return rows


def train(rows):
    Xt = np.asarray([r["t1"] for r in rows]); yt = np.asarray([r["t1y"] for r in rows])
    Xb = np.asarray([r["b1"] for r in rows]); yb = np.asarray([r["b1y"] for r in rows])
    mt = LogReg.fit(Xt, yt, FN, l2=0.05); mb = LogReg.fit(Xb, yb, FN, l2=0.05)
    raw = (1 - mt.predict_proba(Xt)) * (1 - mb.predict_proba(Xb))
    cal = ProbCalibrator.fit(raw.tolist(), [r["nrfi"] for r in rows])
    return mt, mb, cal


def cal_of(model, r):
    mt, mb, cal = model
    raw = (1 - mt.predict_proba_one(r["t1"])) * (1 - mb.predict_proba_one(r["b1"]))
    lam = r.get("lam")
    if lam is None:
        lam = -math.log(max(1e-9, 1-mt.predict_proba_one(r["t1"]))) - math.log(max(1e-9, 1-mb.predict_proba_one(r["b1"])))
    return cal.predict(raw), lam


def main():
    parks = load_parks()
    pool = []
    for g in ["data/backtests/backtest_2024-*_truepit.csv", "data/backtests/backtest_2025-*_truepit.csv"]:
        for p in glob.glob(str(ROOT/g)): pool += gather(p, parks, False)
    p26 = gather(str(ROOT/"data"/"picks_2026.csv"), parks, True)
    pool += p26
    print(f"pool: {len(pool)} games ({len(p26)} 2026). Training STALE (<5/26)...")
    STALE = train([x for x in pool if x["date"] < "2026-05-26"])

    weeks = [("2026-06-01", "2026-06-08"), ("2026-06-08", "2026-06-15"), ("2026-06-15", "2026-06-25")]
    T = {"bs": 0.0, "bf": 0.0, "n": 0, "s_pl": 0.0, "s_n": 0, "s_w": 0, "f_pl": 0.0, "f_n": 0, "f_w": 0}
    print(f"  {'week':<22}{'Brier stale':>12}{'Brier fresh':>12}{'YRFI stale':>16}{'YRFI fresh':>16}")
    for ws, we in weeks:
        FRESH = train([x for x in pool if x["date"] < ws])
        hold = [x for x in p26 if ws <= x["date"] < we]
        bs = bf = 0.0; s = [0, 0, 0.0]; f = [0, 0, 0.0]
        for x in hold:
            cs, lam = cal_of(STALE, x); cf, _ = cal_of(FRESH, x)
            bs += (cs-x["nrfi"])**2; bf += (cf-x["nrfi"])**2
            ywon = x["nrfi"] == 0; pay = payout(x["yodds"]) or (100/110)
            if cs < STRONG_YRFI_P and lam >= YRFI_FLOOR:
                s[0]+=1; s[1]+=ywon; s[2]+= (pay if ywon else -1)
            if cf < STRONG_YRFI_P and lam >= YRFI_FLOOR:
                f[0]+=1; f[1]+=ywon; f[2]+= (pay if ywon else -1)
        n = len(hold)
        print(f"  {ws+'->'+we:<22}{bs/n:>12.4f}{bf/n:>12.4f}"
              f"{f'{s[1]}-{s[0]-s[1]} {s[2]:+.1f}u':>16}{f'{f[1]}-{f[0]-f[1]} {f[2]:+.1f}u':>16}")
        T["bs"]+=bs; T["bf"]+=bf; T["n"]+=n
        T["s_pl"]+=s[2]; T["s_n"]+=s[0]; T["s_w"]+=s[1]; T["f_pl"]+=f[2]; T["f_n"]+=f[0]; T["f_w"]+=f[1]
    print(f"\n  TOTAL Brier: stale {T['bs']/T['n']:.4f} vs fresh {T['bf']/T['n']:.4f} "
          f"(delta {(T['bf']-T['bs'])/T['n']:+.4f}, {'FRESH better' if T['bf']<T['bs'] else 'stale better/none'})")
    print(f"  TOTAL YRFI: stale {T['s_w']}-{T['s_n']-T['s_w']} {T['s_pl']:+.2f}u   "
          f"fresh {T['f_w']}-{T['f_n']-T['f_w']} {T['f_pl']:+.2f}u")
    helps = T['bf'] < T['bs'] and T['f_pl'] > T['s_pl']
    print(f"  -> full retrain {'HELPS' if helps else 'does NOT clearly help'} "
          f"(Brier {(T['bf']-T['bs'])/T['n']:+.4f}, YRFI {T['f_pl']-T['s_pl']:+.2f}u)")


if __name__ == "__main__":
    main()

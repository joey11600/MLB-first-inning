#!/usr/bin/env python3
"""
test_phase_e1.py -- test untested columns against winning baseline,
evaluated on STRONG zone hit rates (not Brier).

Baseline (winning variant from Phase D):
  slim_weather + last5_pitcher_nrfi + top3c_obp + ump_both
  = 10 features per half

Candidate additions:
  E1a. K9 (per side)
  E1b. BB9 (per side)
  E1c. K/BB ratio (derived)
  E1d. HR9 (per side)
  E1e. Wind components (wx_wind_out, wx_wind_cross) replacing wx_wind_kmh
  E1f. is_day_game (no per-side, just 1 feature added to both halves)
  E1g. days_rest (per side)
  E1h. top3c_iso (per side)
  E1i. opener proxy (current_starts < 5)

Each variant evaluated on:
  - 2026 holdout (348 games, 26 days) using train 2024+2025 CLEAN
  - 2024->2025 cross-year (172 days) for stability

Metrics:
  - STRONG NRFI hit rate at P>=0.55
  - STRONG YRFI hit rate at P<=0.44
  - Brier (sanity check, not optimization target)
  - Total P&L at -110

Note: features missing from 2026 picks (top3c_iso, wind_out, days_rest,
current_starts, is_day_game) get default values at test time on the 2026
holdout.  The 2024->2025 split tests them with full data on both sides.
"""

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from lr_baseline import LogReg
from calibration import ProbCalibrator

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_ISO = 0.169
LEAGUE_AVG_K9  = 8.9
LEAGUE_AVG_BB9 = 3.20
LEAGUE_AVG_HR9 = 1.20
WX_TEMP = 20.0
WX_WIND = 10.0
WX_HUMID = 60.0
WX_WIND_OUT_DEFAULT = 0.0   # no wind component
WX_WIND_CROSS_DEFAULT = 0.0
FI_PARK = 0.50
LEAGUE_NRFI = 0.50
DAYS_REST_DEFAULT = 5.0
STARTS_DEFAULT = 10.0  # neutral midseason

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"

PAYOUT = 0.91  # +91c per $1 stake at -110


def coerce(s, d):
    try: f = float(s); return f if math.isfinite(f) else d
    except: return d


def load_park():
    return json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))


def load_ump():
    cache = json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8"))
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    return cache, rates["umpires"], rates["league_nrfi_rate"]


def get_ump(pk, ump_cache, ump_rates, league):
    rec = ump_cache.get(str(pk))
    if not rec: return league
    u = ump_rates.get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


def base_winning_t1(r, park, ump):
    """Winning variant T1 (10 features): slim_weather + last5 + top3c_obp + ump."""
    h = r.get("home", "") or r.get("home_team", "")
    return [
        park.get(h, FI_PARK),
        coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP),
        coerce(r.get("wx_wind_kmh"), WX_WIND),
        coerce(r.get("wx_humidity"), WX_HUMID),
        coerce(r.get("wx_is_dome"),  0.0),
        coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
        coerce(r.get("away_top3c_obp"),            LEAGUE_AVG_OBP),
        ump,
    ]


def base_winning_b1(r, park, ump):
    h = r.get("home", "") or r.get("home_team", "")
    return [
        park.get(h, FI_PARK),
        coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP),
        coerce(r.get("wx_wind_kmh"), WX_WIND),
        coerce(r.get("wx_humidity"), WX_HUMID),
        coerce(r.get("wx_is_dome"),  0.0),
        coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
        coerce(r.get("home_top3c_obp"),            LEAGUE_AVG_OBP),
        ump,
    ]


def build_features(r, park, ump, variant):
    """Return (t1, b1) feature vectors for given variant."""
    if variant == "winning_baseline":
        return base_winning_t1(r, park, ump), base_winning_b1(r, park, ump)

    t1 = base_winning_t1(r, park, ump)
    b1 = base_winning_b1(r, park, ump)

    if variant == "+k9":
        t1.append(coerce(r.get("home_k9"), LEAGUE_AVG_K9))
        b1.append(coerce(r.get("away_k9"), LEAGUE_AVG_K9))
    elif variant == "+bb9":
        t1.append(coerce(r.get("home_bb9"), LEAGUE_AVG_BB9))
        b1.append(coerce(r.get("away_bb9"), LEAGUE_AVG_BB9))
    elif variant == "+hr9":
        t1.append(coerce(r.get("home_hr9"), LEAGUE_AVG_HR9))
        b1.append(coerce(r.get("away_hr9"), LEAGUE_AVG_HR9))
    elif variant == "+kbb_ratio":
        # K/BB ratio per side
        h_k9 = coerce(r.get("home_k9"), LEAGUE_AVG_K9)
        h_bb = max(coerce(r.get("home_bb9"), LEAGUE_AVG_BB9), 0.5)
        a_k9 = coerce(r.get("away_k9"), LEAGUE_AVG_K9)
        a_bb = max(coerce(r.get("away_bb9"), LEAGUE_AVG_BB9), 0.5)
        t1.append(h_k9 / h_bb)
        b1.append(a_k9 / a_bb)
    elif variant == "+wind_components":
        # Replace wx_wind_kmh (index 4) with wind_out and wind_cross
        wind_out = coerce(r.get("wx_wind_out"),   WX_WIND_OUT_DEFAULT)
        wind_cross = coerce(r.get("wx_wind_cross"), WX_WIND_CROSS_DEFAULT)
        # Replace index 4 (wind_kmh) with wind_out, append wind_cross
        t1[4] = wind_out; t1.append(wind_cross)
        b1[4] = wind_out; b1.append(wind_cross)
    elif variant == "+is_day_game":
        d = coerce(r.get("is_day_game"), 0.0)
        t1.append(d); b1.append(d)
    elif variant == "+days_rest":
        t1.append(coerce(r.get("home_days_rest"), DAYS_REST_DEFAULT))
        b1.append(coerce(r.get("away_days_rest"), DAYS_REST_DEFAULT))
    elif variant == "+top3c_iso":
        # Top-3 power for the OPPOSING offense in each half
        t1.append(coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO))
        b1.append(coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO))
    elif variant == "+opener_flag":
        # Opener proxy: current_starts < 5 means likely opener / spot starter
        h_starts = coerce(r.get("home_current_starts"), STARTS_DEFAULT)
        a_starts = coerce(r.get("away_current_starts"), STARTS_DEFAULT)
        t1.append(1.0 if h_starts < 5 else 0.0)
        b1.append(1.0 if a_starts < 5 else 0.0)
    elif variant == "+all_e1_easy":
        # K9 + K/BB ratio + top3c_iso + day_game + days_rest
        t1.append(coerce(r.get("home_k9"), LEAGUE_AVG_K9))
        b1.append(coerce(r.get("away_k9"), LEAGUE_AVG_K9))
        h_kbb = coerce(r.get("home_k9"), LEAGUE_AVG_K9) / max(coerce(r.get("home_bb9"), LEAGUE_AVG_BB9), 0.5)
        a_kbb = coerce(r.get("away_k9"), LEAGUE_AVG_K9) / max(coerce(r.get("away_bb9"), LEAGUE_AVG_BB9), 0.5)
        t1.append(h_kbb); b1.append(a_kbb)
        t1.append(coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO))
        b1.append(coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO))
        d = coerce(r.get("is_day_game"), 0.0)
        t1.append(d); b1.append(d)
        t1.append(coerce(r.get("home_days_rest"), DAYS_REST_DEFAULT))
        b1.append(coerce(r.get("away_days_rest"), DAYS_REST_DEFAULT))
    else:
        raise ValueError(variant)
    return t1, b1


def gather(csv_path, park, variant, ump_cache, ump_rates, league, clean):
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    meta = []
    for r in csv.DictReader(open(csv_path, encoding="utf-8")):
        actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"): continue
        if clean:
            ap = (r.get("away_pitcher_q") or "").lower()
            hp = (r.get("home_pitcher_q") or "").lower()
            if ap == "avg" or hp == "avg": continue
        t1r = r.get("fi_away_runs", "")
        b1r = r.get("fi_home_runs", "")
        if t1r == "" or b1r == "": continue
        try:
            t1y = 1 if int(float(t1r)) > 0 else 0
            b1y = 1 if int(float(b1r)) > 0 else 0
        except: continue
        ump = get_ump(r.get("game_pk", ""), ump_cache, ump_rates, league)
        x_t, x_b = build_features(r, park, ump, variant)
        Xt.append(x_t); Xb.append(x_b)
        yt.append(t1y); yb.append(b1y)
        ynrfi.append(1 if actual == "NRFI" else 0)
        meta.append({"date": r.get("date", ""), "actual": actual})
    return {
        "Xt": np.asarray(Xt, dtype=float), "yt": np.asarray(yt, dtype=int),
        "Xb": np.asarray(Xb, dtype=float), "yb": np.asarray(yb, dtype=int),
        "ynrfi": np.asarray(ynrfi, dtype=int),
        "meta": meta,
    }


def predict(t1m, b1m, X_t, X_b):
    return (1 - t1m.predict_proba(X_t)) * (1 - b1m.predict_proba(X_b))


def evaluate(p, y, t_nrfi=0.55, t_yrfi=0.44):
    n_picks = [(i, p[i]) for i in range(len(p)) if p[i] >= t_nrfi]
    y_picks = [(i, p[i]) for i in range(len(p)) if p[i] <= t_yrfi]
    n_w = sum(1 for i, _ in n_picks if y[i] == 1)
    y_w = sum(1 for i, _ in y_picks if y[i] == 0)
    n_n = len(n_picks); y_n = len(y_picks)
    total_n = n_n + y_n
    total_w = n_w + y_w
    pnl = total_w * PAYOUT - (total_n - total_w) * 1.0
    brier = float(np.mean((p - y) ** 2))
    return {
        "brier": brier,
        "n_n": n_n, "n_w": n_w, "n_rate": n_w / n_n if n_n else 0,
        "y_n": y_n, "y_w": y_w, "y_rate": y_w / y_n if y_n else 0,
        "total_n": total_n, "total_w": total_w,
        "total_rate": total_w / total_n if total_n else 0,
        "pnl": pnl,
    }


def run(variant, train_paths, test_path, park, ump_cache, ump_rates, league):
    blocks = [gather(p, park, variant, ump_cache, ump_rates, league, clean=True)
              for p in train_paths]
    Xt = np.vstack([b["Xt"] for b in blocks])
    yt = np.concatenate([b["yt"] for b in blocks])
    Xb = np.vstack([b["Xb"] for b in blocks])
    yb = np.concatenate([b["yb"] for b in blocks])
    ynrfi_tr = np.concatenate([b["ynrfi"] for b in blocks])
    m_t = LogReg.fit(Xt, yt, [f"t{i}" for i in range(Xt.shape[1])], l2=0.05)
    m_b = LogReg.fit(Xb, yb, [f"b{i}" for i in range(Xb.shape[1])], l2=0.05)
    p_tr = predict(m_t, m_b, Xt, Xb)
    cal = ProbCalibrator.fit([float(p) for p in p_tr],
                              [int(y) for y in ynrfi_tr], n_bins=20)
    te = gather(test_path, park, variant, ump_cache, ump_rates, league, clean=False)
    p_raw = predict(m_t, m_b, te["Xt"], te["Xb"])
    p_cal = np.array([cal.predict(float(p)) for p in p_raw])
    return evaluate(p_cal, te["ynrfi"])


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump()

    variants = [
        "winning_baseline",
        "+k9",
        "+bb9",
        "+hr9",
        "+kbb_ratio",
        "+wind_components",
        "+is_day_game",
        "+days_rest",
        "+top3c_iso",
        "+opener_flag",
        "+all_e1_easy",
    ]

    print("=" * 130)
    print("  Phase E.1: untested columns vs winning baseline")
    print("  Metrics: STRONG NRFI@>=0.55 hit rate, STRONG YRFI@<=0.44 hit rate, Brier")
    print("=" * 130)

    print("\n  TEST 1: train 2024+2025 CLEAN -> test 2026 graded (348 games, 26 days)")
    print(f"  {'variant':<22} {'Brier':>7} | {'STR NRFI':>14} {'STR YRFI':>14} {'TOTAL':>14} | {'P&L':>7}")
    print("  " + "-" * 92)
    for v in variants:
        out = run(v, [BT_2024, BT_2025], PICKS_2026, park, ump_cache, ump_rates, league)
        print(f"  {v:<22} {out['brier']:>7.4f} | "
              f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
              f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
              f"{out['total_w']:>3}/{out['total_n']:<3} ({out['total_rate']*100:>4.1f}%) | "
              f"{out['pnl']:>+6.1f}u")

    print("\n  TEST 2: train 2024 CLEAN -> test 2025 CLEAN (~1500 games, 172 days)")
    print(f"  {'variant':<22} {'Brier':>7} | {'STR NRFI':>14} {'STR YRFI':>14} {'TOTAL':>14} | {'P&L':>7}")
    print("  " + "-" * 92)
    for v in variants:
        out = run(v, [BT_2024], BT_2025, park, ump_cache, ump_rates, league)
        print(f"  {v:<22} {out['brier']:>7.4f} | "
              f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
              f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
              f"{out['total_w']:>3}/{out['total_n']:<3} ({out['total_rate']*100:>4.1f}%) | "
              f"{out['pnl']:>+6.1f}u")

    print("\n  TEST 3: train 2025 CLEAN -> test 2024 CLEAN (~1400 games, 174 days)")
    print(f"  {'variant':<22} {'Brier':>7} | {'STR NRFI':>14} {'STR YRFI':>14} {'TOTAL':>14} | {'P&L':>7}")
    print("  " + "-" * 92)
    for v in variants:
        out = run(v, [BT_2025], BT_2024, park, ump_cache, ump_rates, league)
        print(f"  {v:<22} {out['brier']:>7.4f} | "
              f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
              f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
              f"{out['total_w']:>3}/{out['total_n']:<3} ({out['total_rate']*100:>4.1f}%) | "
              f"{out['pnl']:>+6.1f}u")

    print("\n  Read: a feature ships if STRONG NRFI hit rate improves >= 3pp on at least 2 of 3 splits")
    print("  AND total P&L improves on average across the 3 splits.")


if __name__ == "__main__":
    main()

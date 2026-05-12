#!/usr/bin/env python3
"""
test_adaptive_strong.py -- find absolute probability thresholds for STRONG
that allow VARIABLE picks per slate.

User insight: some slates genuinely have 4 strong NRFI + 1 strong YRFI;
others have 1 of each.  A fixed top-K per side ignores this.  The right
definition is an absolute threshold so the daily count adapts to the
slate's actual confidence distribution.

Goal: find (T_nrfi, T_yrfi) thresholds such that:
  - NRFI hit rate >= 60%  AND  YRFI hit rate >= 60%
  - Average picks per day is meaningful (>= 0.7)
  - Not too many no-pick days (< 30%)
  - On busy days the model can flag 3-5 picks per side
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
WX_TEMP_DEFAULT = 20.0
WX_WIND_DEFAULT = 10.0
WX_HUMID_DEFAULT = 60.0
FI_PARK_DEFAULT = 0.50
LEAGUE_NRFI = 0.50

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"

PAYOUT = 0.91  # +91c per $1 stake at -110 odds


def coerce(s, d):
    try: f = float(s); return f if math.isfinite(f) else d
    except: return d


def load_park():
    return json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))


def load_ump():
    cache = json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8"))
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    return cache, rates["umpires"], rates["league_nrfi_rate"]


def slim_t1(r, park):
    h = r.get("home", "") or r.get("home_team", "")
    return [park.get(h, FI_PARK_DEFAULT),
            coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
            coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
            coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
            coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
            coerce(r.get("wx_humidity"), WX_HUMID_DEFAULT),
            coerce(r.get("wx_is_dome"),  0.0)]


def slim_b1(r, park):
    h = r.get("home", "") or r.get("home_team", "")
    return [park.get(h, FI_PARK_DEFAULT),
            coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
            coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
            coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
            coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
            coerce(r.get("wx_humidity"), WX_HUMID_DEFAULT),
            coerce(r.get("wx_is_dome"),  0.0)]


def get_ump(pk, ump_cache, ump_rates, league):
    rec = ump_cache.get(str(pk))
    if not rec: return league
    u = ump_rates.get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


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
        if variant == "slim_weather":
            x_t = slim_t1(r, park); x_b = slim_b1(r, park)
        elif variant == "winning":
            x_t = slim_t1(r, park) + [
                coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
                coerce(r.get("away_top3c_obp"),            LEAGUE_AVG_OBP),
                ump,
            ]
            x_b = slim_b1(r, park) + [
                coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
                coerce(r.get("home_top3c_obp"),            LEAGUE_AVG_OBP),
                ump,
            ]
        else:
            raise ValueError(variant)
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


def run_model(variant, train_paths, test_path, park, ump_cache, ump_rates, league):
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
    return p_cal, te["ynrfi"], te["meta"]


def evaluate(p, y, meta, t_nrfi, t_yrfi):
    """Variable-count STRONG: any game with P >= t_nrfi or P <= t_yrfi qualifies."""
    by_day = defaultdict(lambda: {"nrfi": [], "yrfi": []})
    n_total = n_w = y_total = y_w = 0
    for i in range(len(p)):
        date = meta[i]["date"]
        if p[i] >= t_nrfi:
            n_total += 1
            won = y[i] == 1
            if won: n_w += 1
            by_day[date]["nrfi"].append((i, p[i], won))
        elif p[i] <= t_yrfi:
            y_total += 1
            won = y[i] == 0
            if won: y_w += 1
            by_day[date]["yrfi"].append((i, p[i], won))

    days = len(set(m["date"] for m in meta))
    days_with_pick = sum(1 for d in by_day.values() if d["nrfi"] or d["yrfi"])
    days_no_pick   = days - days_with_pick

    # Distribution of picks-per-slate
    counts = []
    for d, picks in by_day.items():
        counts.append(len(picks["nrfi"]) + len(picks["yrfi"]))
    if not counts:
        counts = [0]

    n_rate = n_w / n_total if n_total else 0
    y_rate = y_w / y_total if y_total else 0
    total_n = n_total + y_total
    total_w = n_w + y_w
    total_rate = total_w / total_n if total_n else 0
    pnl = total_w * PAYOUT - (total_n - total_w) * 1.0

    return {
        "n_total":   n_total, "n_w": n_w, "n_rate": n_rate,
        "y_total":   y_total, "y_w": y_w, "y_rate": y_rate,
        "total":     total_n, "wins": total_w, "rate": total_rate,
        "days":      days,
        "days_with_pick": days_with_pick,
        "days_no_pick":   days_no_pick,
        "pnl":       pnl,
        "max_picks_one_day": max(counts),
        "median_picks":      sorted(counts)[len(counts)//2],
        "avg_per_day":       sum(counts) / len(counts),
    }


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump()

    print("=" * 120)
    print("  Adaptive STRONG: variable picks per slate based on absolute thresholds")
    print("  Model: WINNING variant (+last5 + top3c_obp + ump_both)")
    print("  Train 2024+2025 CLEAN ; test 2026 graded (348 games, 26 days)")
    print("=" * 120)
    p, y, meta = run_model("winning", [BT_2024, BT_2025], PICKS_2026,
                            park, ump_cache, ump_rates, league)

    # Threshold grid
    nrfi_thresholds = [0.52, 0.54, 0.55, 0.56, 0.58, 0.60]
    yrfi_thresholds = [0.48, 0.46, 0.45, 0.44, 0.42, 0.40]

    print(f"\n  {'T(NRFI)':>8} {'T(YRFI)':>8} | "
          f"{'NRFI':>15} {'YRFI':>15} {'TOTAL':>15} | "
          f"{'days w/pick':>11} {'no-pick':>8} | "
          f"{'avg/day':>7} {'med':>4} {'max':>4} | {'P&L':>7}")
    print("  " + "-" * 117)

    best_results = []
    for t_n in nrfi_thresholds:
        for t_y in yrfi_thresholds:
            out = evaluate(p, y, meta, t_n, t_y)
            print(f"  {t_n:>8.2f} {t_y:>8.2f} | "
                  f"{out['n_w']:>3}/{out['n_total']:<3} ({out['n_rate']*100:>4.1f}%) "
                  f"{out['y_w']:>3}/{out['y_total']:<3} ({out['y_rate']*100:>4.1f}%) "
                  f"{out['wins']:>3}/{out['total']:<3} ({out['rate']*100:>4.1f}%) | "
                  f"{out['days_with_pick']:>3}/{out['days']:<3} "
                  f"{out['days_no_pick']:>4} | "
                  f"{out['avg_per_day']:>6.1f} {out['median_picks']:>4} {out['max_picks_one_day']:>4} | "
                  f"{out['pnl']:>+6.1f}u")
            best_results.append((t_n, t_y, out))

    print("\n  Filter to candidates: NRFI rate >= 55% AND YRFI rate >= 60% AND days_with_pick >= 80%:")
    print("  " + "-" * 117)
    for t_n, t_y, out in best_results:
        if (out["n_rate"] >= 0.55 and out["y_rate"] >= 0.60 and
                out["days_with_pick"] / max(out["days"], 1) >= 0.80):
            print(f"    T(NRFI)>={t_n:.2f}, T(YRFI)<={t_y:.2f}: "
                  f"NRFI {out['n_w']}/{out['n_total']} ({out['n_rate']*100:.1f}%), "
                  f"YRFI {out['y_w']}/{out['y_total']} ({out['y_rate']*100:.1f}%), "
                  f"avg {out['avg_per_day']:.1f}/day, "
                  f"P&L {out['pnl']:+.1f}u")

    # Show some specific slates to understand variability
    print("\n  Per-slate distribution at recommended threshold (NRFI>=0.54, YRFI<=0.45):")
    print("  " + "-" * 117)
    by_day = defaultdict(lambda: {"nrfi": [], "yrfi": []})
    for i in range(len(p)):
        if p[i] >= 0.54:
            by_day[meta[i]["date"]]["nrfi"].append((p[i], y[i]))
        elif p[i] <= 0.45:
            by_day[meta[i]["date"]]["yrfi"].append((p[i], y[i]))

    n_only_nrfi = n_only_yrfi = n_both = n_neither = 0
    for date in sorted(set(m["date"] for m in meta)):
        n = len(by_day[date]["nrfi"])
        yc = len(by_day[date]["yrfi"])
        if n and yc:        n_both += 1
        elif n:             n_only_nrfi += 1
        elif yc:            n_only_yrfi += 1
        else:               n_neither += 1
    print(f"  Slates: {n_both} with both sides, {n_only_nrfi} only NRFI, "
          f"{n_only_yrfi} only YRFI, {n_neither} no picks")

    # Show 3 sample slates
    print("\n  Sample slate distributions:")
    for date in list(by_day.keys())[:6]:
        n = by_day[date]["nrfi"]; yc = by_day[date]["yrfi"]
        print(f"    {date}:  {len(n)} STRONG NRFI, {len(yc)} STRONG YRFI  |  "
              f"NRFI: " + ", ".join(f"{p:.3f}({'W' if w==1 else 'L'})" for p, w in n) +
              f"  YRFI: " + ", ".join(f"{p:.3f}({'W' if w==0 else 'L'})" for p, w in yc))


if __name__ == "__main__":
    main()

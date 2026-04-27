#!/usr/bin/env python3
"""
test_phase_e3.py -- test Statcast features against winning baseline.

Statcast features tested (against winning variant baseline):
  - xera (raw expected ERA from quality of contact)
  - est_woba (expected wOBA against)
  - k_pct_rank (K% percentile, 1-100 scale)
  - whiff_pct_rank (swinging strike rate percentile)
  - chase_pct_rank (chase rate percentile)

Each variant adds ONE pair of Statcast features (one per side).
Then combinations of the most promising single features.

Coverage: 81-90% of clean rows have BOTH pitchers in Statcast.
For ~10% of rows we default to neutral values (xera=4.20, percentiles=50).

Metrics: STRONG NRFI hit rate, STRONG YRFI hit rate, Brier, P&L.
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
WX_TEMP = 20.0; WX_WIND = 10.0; WX_HUMID = 60.0
FI_PARK = 0.50
LEAGUE_NRFI = 0.50
LEAGUE_AVG_XERA = 4.20
LEAGUE_AVG_EST_WOBA = 0.310
NEUTRAL_PCT_RANK = 50  # 50th percentile for missing pitchers

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"

PAYOUT = 0.91


def coerce(s, d):
    try: f = float(s); return f if math.isfinite(f) else d
    except: return d


def load_park():
    return json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))


def load_ump():
    cache = json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8"))
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    return cache, rates["umpires"], rates["league_nrfi_rate"]


def load_pid_cache():
    return json.load(open(ROOT / "data" / "pitcher_id_cache.json", encoding="utf-8"))


def load_statcast():
    return json.load(open(ROOT / "data" / "statcast_pitcher_cache.json", encoding="utf-8"))


def get_ump(pk, ump_cache, ump_rates, league):
    rec = ump_cache.get(str(pk))
    if not rec: return league
    u = ump_rates.get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


def get_statcast(pk, side, season, pid_cache, statcast):
    """Returns dict with xera/est_woba/k_pct_rank/whiff_pct_rank/chase_pct_rank for one side."""
    pids = pid_cache.get(str(pk))
    if not pids:
        return None
    pid = str(pids[0]) if side == "away" else str(pids[1])
    season_cache = statcast.get(str(season), {})
    return season_cache.get(pid)


def base_winning_t1(r, park, ump):
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


def statcast_features(rec, feat_set):
    """Return Statcast feature vector for one side."""
    if rec is None:
        rec = {}
    out = []
    for f in feat_set:
        if f == "xera":
            out.append(coerce(rec.get("xera"), LEAGUE_AVG_XERA))
        elif f == "est_woba":
            out.append(coerce(rec.get("est_woba"), LEAGUE_AVG_EST_WOBA))
        elif f == "k_pct_rank":
            out.append(coerce(rec.get("k_pct_rank"), NEUTRAL_PCT_RANK))
        elif f == "whiff_pct_rank":
            out.append(coerce(rec.get("whiff_pct_rank"), NEUTRAL_PCT_RANK))
        elif f == "chase_pct_rank":
            out.append(coerce(rec.get("chase_pct_rank"), NEUTRAL_PCT_RANK))
        elif f == "bb_pct_rank":
            out.append(coerce(rec.get("bb_pct_rank"), NEUTRAL_PCT_RANK))
    return out


def variant_features(r, park, ump, sc_home, sc_away, variant):
    """Build (t1, b1) feature vectors for a given variant."""
    t1 = base_winning_t1(r, park, ump)
    b1 = base_winning_b1(r, park, ump)
    if variant == "winning_baseline":
        return t1, b1

    # Determine which Statcast features to add
    featset_map = {
        "+xera":            ["xera"],
        "+est_woba":        ["est_woba"],
        "+k_pct":           ["k_pct_rank"],
        "+whiff_pct":       ["whiff_pct_rank"],
        "+chase_pct":       ["chase_pct_rank"],
        "+xera+k_pct":      ["xera", "k_pct_rank"],
        "+xera+whiff":      ["xera", "whiff_pct_rank"],
        "+xera+est_woba":   ["xera", "est_woba"],
        "+all_statcast":    ["xera", "est_woba", "k_pct_rank", "whiff_pct_rank", "chase_pct_rank"],
        "+statcast_lite":   ["xera", "k_pct_rank"],
        "+statcast_full_pitcher": ["xera", "k_pct_rank", "whiff_pct_rank", "bb_pct_rank"],
    }
    feats = featset_map.get(variant)
    if feats is None:
        raise ValueError(variant)
    # T1: home pitcher pitches (so home pitcher Statcast goes here)
    t1 = t1 + statcast_features(sc_home, feats)
    # B1: away pitcher pitches
    b1 = b1 + statcast_features(sc_away, feats)
    return t1, b1


def gather(csv_path, season, park, ump_cache, ump_rates, league,
           pid_cache, statcast, variant, clean):
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    meta = []
    n_no_statcast = 0
    n_partial_statcast = 0
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
        sc_home = get_statcast(r.get("game_pk", ""), "home", season, pid_cache, statcast)
        sc_away = get_statcast(r.get("game_pk", ""), "away", season, pid_cache, statcast)
        if sc_home is None and sc_away is None: n_no_statcast += 1
        elif sc_home is None or sc_away is None: n_partial_statcast += 1

        x_t, x_b = variant_features(r, park, ump, sc_home, sc_away, variant)
        Xt.append(x_t); Xb.append(x_b)
        yt.append(t1y); yb.append(b1y)
        ynrfi.append(1 if actual == "NRFI" else 0)
        meta.append({"date": r.get("date", ""), "actual": actual})
    return {
        "Xt": np.asarray(Xt, dtype=float), "yt": np.asarray(yt, dtype=int),
        "Xb": np.asarray(Xb, dtype=float), "yb": np.asarray(yb, dtype=int),
        "ynrfi": np.asarray(ynrfi, dtype=int),
        "meta": meta,
        "n_no_statcast": n_no_statcast,
        "n_partial_statcast": n_partial_statcast,
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


def run(variant, train_specs, test_spec, park, ump_cache, ump_rates, league,
         pid_cache, statcast):
    blocks = [gather(p, s, park, ump_cache, ump_rates, league, pid_cache, statcast, variant, clean=True)
              for s, p in train_specs]
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
    te_season, te_path = test_spec
    te = gather(te_path, te_season, park, ump_cache, ump_rates, league, pid_cache, statcast,
                variant, clean=False)
    p_raw = predict(m_t, m_b, te["Xt"], te["Xb"])
    p_cal = np.array([cal.predict(float(p)) for p in p_raw])
    return evaluate(p_cal, te["ynrfi"]), m_t, m_b


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump()
    pid_cache = load_pid_cache()
    statcast = load_statcast()

    variants = [
        "winning_baseline",
        "+xera",
        "+est_woba",
        "+k_pct",
        "+whiff_pct",
        "+chase_pct",
        "+xera+k_pct",
        "+xera+whiff",
        "+xera+est_woba",
        "+statcast_lite",
        "+all_statcast",
    ]

    print("=" * 130)
    print("  Phase E.3: Statcast features vs winning baseline")
    print("  Metrics: STRONG NRFI@>=0.55 hit rate, STRONG YRFI@<=0.44 hit rate, Brier, P&L")
    print("=" * 130)

    print("\n  TEST 1: train 2024+2025 CLEAN -> test 2026 graded (348 games, 26 days)")
    print(f"  {'variant':<26} {'Brier':>7} | {'STR NRFI':>15} {'STR YRFI':>15} {'TOTAL':>15} | {'P&L':>7}")
    print("  " + "-" * 100)
    for v in variants:
        out, _, _ = run(v, [(2024, BT_2024), (2025, BT_2025)],
                          (2026, PICKS_2026),
                          park, ump_cache, ump_rates, league, pid_cache, statcast)
        print(f"  {v:<26} {out['brier']:>7.4f} | "
              f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
              f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
              f"{out['total_w']:>3}/{out['total_n']:<3} ({out['total_rate']*100:>4.1f}%) | "
              f"{out['pnl']:>+6.1f}u")

    print("\n  TEST 2: train 2024 CLEAN -> test 2025 CLEAN (~1500 games, 172 days)")
    print(f"  {'variant':<26} {'Brier':>7} | {'STR NRFI':>15} {'STR YRFI':>15} {'TOTAL':>15} | {'P&L':>7}")
    print("  " + "-" * 100)
    for v in variants:
        out, _, _ = run(v, [(2024, BT_2024)], (2025, BT_2025),
                          park, ump_cache, ump_rates, league, pid_cache, statcast)
        print(f"  {v:<26} {out['brier']:>7.4f} | "
              f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
              f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
              f"{out['total_w']:>3}/{out['total_n']:<3} ({out['total_rate']*100:>4.1f}%) | "
              f"{out['pnl']:>+6.1f}u")

    print("\n  TEST 3: train 2025 CLEAN -> test 2024 CLEAN (~1400 games, 174 days)")
    print(f"  {'variant':<26} {'Brier':>7} | {'STR NRFI':>15} {'STR YRFI':>15} {'TOTAL':>15} | {'P&L':>7}")
    print("  " + "-" * 100)
    for v in variants:
        out, _, _ = run(v, [(2025, BT_2025)], (2024, BT_2024),
                          park, ump_cache, ump_rates, league, pid_cache, statcast)
        print(f"  {v:<26} {out['brier']:>7.4f} | "
              f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
              f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
              f"{out['total_w']:>3}/{out['total_n']:<3} ({out['total_rate']*100:>4.1f}%) | "
              f"{out['pnl']:>+6.1f}u")

    print("\n  Read: a feature ships if STRONG NRFI hit rate improves >= 3pp on at least 2 of 3 splits")
    print("  AND total P&L improves on average across the 3 splits.")


if __name__ == "__main__":
    main()

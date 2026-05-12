#!/usr/bin/env python3
"""
test_strong_redefinitions.py -- explore alternative definitions of STRONG.

Question: what definition of STRONG produces the most RELIABLE picks?

We're not optimizing for parlays.  We're asking: when the model says
STRONG, how often is it right?  And how many STRONG picks per slate do
we get?

Definitions tested:
  A. Absolute threshold (current default: 0.62 / 0.42)
     - Sweep: 0.60, 0.62, 0.65, 0.68 for NRFI; 0.42, 0.40, 0.38, 0.35 for YRFI
  B. Slate-relative ranking
     - Top-1 NRFI per slate, top-1 YRFI per slate
     - Top-2 NRFI per slate, top-2 YRFI per slate
  C. Multi-factor STRONG
     - Probability above threshold AND data quality (blended_inputs) >= 4
     - Probability above threshold AND park factor extreme (bottom/top tertile)
  D. Hybrid: top-K per slate, only if absolute prob also meets a minimum

Models compared:
  - slim_weather (current production)
  - +last5+top3c_obp+ump_both (winning variant from earlier test)

Each definition reports:
  - n_picks (total across season)
  - hit_rate (% of STRONG picks that won)
  - per_day_avg (volume per slate)
  - units_pnl (assuming 1u flat bet at -110, +0.91u win / -1u loss)
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

# Win pays +91 cents on $1 stake at -110 odds; loss costs $1
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


def features(r, variant, half, ump):
    """Build feature vector for one side (T1 or B1)."""
    base_t1 = slim_t1(r, load_park()) if half == "t1" else None
    base_b1 = slim_b1(r, load_park()) if half == "b1" else None
    base = base_t1 if half == "t1" else base_b1

    if variant == "slim_weather":
        return base
    if variant == "winning":
        # +last5+top3c_obp+ump_both
        last5 = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        obp   = "away_top3c_obp"            if half == "t1" else "home_top3c_obp"
        return base + [
            coerce(r.get(last5), LEAGUE_NRFI),
            coerce(r.get(obp),   LEAGUE_AVG_OBP),
            ump,  # ump goes in both halves
        ]
    raise ValueError(variant)


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
        h = r.get("home", "") or r.get("home_team", "")
        # Build features inline so we share base_*
        if variant == "slim_weather":
            x_t = slim_t1(r, park)
            x_b = slim_b1(r, park)
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
        # blended_inputs (data quality 0-4): how many of pitcher_q + batting_q are not 'avg'
        qual = sum(1 for q in [
            r.get("away_pitcher_q", ""), r.get("home_pitcher_q", ""),
            r.get("away_batting_q", ""), r.get("home_batting_q", ""),
        ] if (q or "avg").lower() != "avg")
        meta.append({
            "date": r.get("date", ""),
            "actual": actual,
            "park_factor": coerce(r.get("park_factor"), 1.0),
            "blended_inputs": qual,
            "home": h,
        })
    return {
        "Xt": np.asarray(Xt, dtype=float), "yt": np.asarray(yt, dtype=int),
        "Xb": np.asarray(Xb, dtype=float), "yb": np.asarray(yb, dtype=int),
        "ynrfi": np.asarray(ynrfi, dtype=int),
        "meta": meta,
    }


def predict(t1m, b1m, X_t, X_b):
    p_t = t1m.predict_proba(X_t); p_b = b1m.predict_proba(X_b)
    return (1 - p_t) * (1 - p_b)


def evaluate_picks(picks_indices, p_nrfi, ynrfi, side: str):
    """Given indices of selected picks and their direction, compute hit rate + P&L."""
    if not picks_indices:
        return {"n": 0, "wins": 0, "rate": 0.0, "pnl": 0.0, "roi": 0.0}
    wins = 0
    for i in picks_indices:
        # 'side' tells us what we're betting; check if actual matches
        won = (ynrfi[i] == 1 if side == "NRFI" else ynrfi[i] == 0)
        if won: wins += 1
    n = len(picks_indices)
    rate = wins / n
    pnl = wins * PAYOUT - (n - wins) * 1.0
    roi = pnl / n
    return {"n": n, "wins": wins, "rate": rate, "pnl": pnl, "roi": roi}


def select_threshold(p_nrfi, t_nrfi, t_yrfi):
    """Return (nrfi_indices, yrfi_indices) by absolute threshold."""
    n_idx = [i for i in range(len(p_nrfi)) if p_nrfi[i] >= t_nrfi]
    y_idx = [i for i in range(len(p_nrfi)) if p_nrfi[i] <= t_yrfi]
    return n_idx, y_idx


def select_top_k_per_slate(p_nrfi, meta, k_nrfi: int, k_yrfi: int):
    """Top-K NRFI and Top-K YRFI per day."""
    by_day = defaultdict(list)
    for i, m in enumerate(meta):
        by_day[m["date"]].append(i)
    n_idx, y_idx = [], []
    for date, idxs in by_day.items():
        if k_nrfi > 0:
            top_n = sorted(idxs, key=lambda i: -p_nrfi[i])[:k_nrfi]
            n_idx.extend(top_n)
        if k_yrfi > 0:
            top_y = sorted(idxs, key=lambda i: p_nrfi[i])[:k_yrfi]
            y_idx.extend(top_y)
    return n_idx, y_idx


def select_topk_with_threshold(p_nrfi, meta, k: int, t_nrfi: float, t_yrfi: float):
    """Top-K per slate but only count if prob also meets threshold."""
    by_day = defaultdict(list)
    for i, m in enumerate(meta):
        by_day[m["date"]].append(i)
    n_idx, y_idx = [], []
    for date, idxs in by_day.items():
        # Top-K NRFI but only those with P >= t_nrfi
        cands = [i for i in idxs if p_nrfi[i] >= t_nrfi]
        n_idx.extend(sorted(cands, key=lambda i: -p_nrfi[i])[:k])
        cands = [i for i in idxs if p_nrfi[i] <= t_yrfi]
        y_idx.extend(sorted(cands, key=lambda i: p_nrfi[i])[:k])
    return n_idx, y_idx


def select_threshold_plus_quality(p_nrfi, meta, t_nrfi: float, t_yrfi: float, min_quality: int):
    """Threshold + data quality filter."""
    n_idx = [i for i in range(len(p_nrfi))
             if p_nrfi[i] >= t_nrfi and meta[i]["blended_inputs"] >= min_quality]
    y_idx = [i for i in range(len(p_nrfi))
             if p_nrfi[i] <= t_yrfi and meta[i]["blended_inputs"] >= min_quality]
    return n_idx, y_idx


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


def report(label, n_idx, y_idx, p, y, days):
    n_out = evaluate_picks(n_idx, p, y, "NRFI")
    y_out = evaluate_picks(y_idx, p, y, "YRFI")
    total_n = n_out["n"] + y_out["n"]
    total_w = n_out["wins"] + y_out["wins"]
    total_pnl = n_out["pnl"] + y_out["pnl"]
    rate = total_w / total_n if total_n else 0
    per_day = total_n / days if days else 0
    print(f"  {label:<46}  "
          f"NRFI {n_out['wins']:>3}/{n_out['n']:<3} ({n_out['rate']*100:>4.1f}%)  "
          f"YRFI {y_out['wins']:>3}/{y_out['n']:<3} ({y_out['rate']*100:>4.1f}%)  "
          f"total {total_w}/{total_n} ({rate*100:>4.1f}%)  "
          f"per-day {per_day:>4.1f}  "
          f"P&L {total_pnl:>+7.1f}u")


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump()

    variants = [("slim_weather", "current production"),
                ("winning", "+last5+top3c_obp+ump_both")]

    for variant, label in variants:
        print("\n" + "=" * 110)
        print(f"  Model: {variant} ({label})")
        print(f"  Train 2024+2025 CLEAN  ;  test 2026 graded")
        print("=" * 110)
        p, y, meta = run_model(variant, [BT_2024, BT_2025], PICKS_2026,
                                park, ump_cache, ump_rates, league)
        days = len(set(m["date"] for m in meta))
        print(f"  N games: {len(p)}, days: {days}\n")

        # ---- A. Absolute thresholds ----
        print("  A. Absolute threshold sweep:")
        for t_n, t_y in [(0.55, 0.45), (0.58, 0.42), (0.60, 0.40), (0.62, 0.38),
                          (0.65, 0.35), (0.68, 0.32)]:
            n_idx, y_idx = select_threshold(p, t_n, t_y)
            report(f"NRFI>={t_n:.2f} / YRFI<={t_y:.2f}", n_idx, y_idx, p, y, days)

        # ---- B. Slate-relative top-K ----
        print("\n  B. Slate-relative top-K per day:")
        for k_n, k_y, lbl in [(1, 1, "top-1 each side"),
                                (2, 2, "top-2 each side"),
                                (3, 3, "top-3 each side"),
                                (1, 2, "top-1 NRFI / top-2 YRFI"),
                                (2, 1, "top-2 NRFI / top-1 YRFI")]:
            n_idx, y_idx = select_top_k_per_slate(p, meta, k_n, k_y)
            report(lbl, n_idx, y_idx, p, y, days)

        # ---- C. Multi-factor: threshold + data quality ----
        print("\n  C. Threshold + data quality (blended_inputs >= 4):")
        for t_n, t_y in [(0.58, 0.42), (0.60, 0.40), (0.62, 0.38)]:
            n_idx, y_idx = select_threshold_plus_quality(p, meta, t_n, t_y, 4)
            report(f"NRFI>={t_n:.2f} / YRFI<={t_y:.2f} & qual=4", n_idx, y_idx, p, y, days)

        # ---- D. Hybrid: top-K per day with min threshold ----
        print("\n  D. Hybrid: top-K per slate, only if prob meets minimum:")
        for k, t_n, t_y in [(1, 0.55, 0.45), (1, 0.60, 0.40),
                              (2, 0.55, 0.45), (2, 0.58, 0.42)]:
            n_idx, y_idx = select_topk_with_threshold(p, meta, k, t_n, t_y)
            report(f"top-{k}/day & NRFI>={t_n:.2f} / YRFI<={t_y:.2f}",
                   n_idx, y_idx, p, y, days)

    print("\n  Read: aim for high hit rate (>= 60%) AND meaningful per-day volume (>= 1.0).")
    print("  Below 1.0/day = some slates have no STRONG pick; you'd skip those days.")


if __name__ == "__main__":
    main()

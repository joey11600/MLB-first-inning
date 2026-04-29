#!/usr/bin/env python3
"""
test_era_gap.py -- test ERA / FIP gap as a feature against Phase E.3 baseline.

User's observation (4/26 + 4/27 sample): when a YRFI game has a meaningful
ERA gap between starters (>= 0.5 ERA), the worse pitcher gives up the
inning-1 run 100% of the time (6/6 in that sample).  Tiny window, but a
clear directional signal.

Hypothesis: a derived "pitcher gap" feature could push borderline YRFI
candidates (e.g. PHI@ATL with Nola 5.80 vs Sale 2.45 → model said only
53.3% YRFI, missed STRONG threshold) into the STRONG zone.

Variants tested (each adds ONE pair of derived features to Phase E.3 baseline):
  - +era_gap_signed   : (opposing - own) ERA per half
                        T1 sees (away_era - home_era) -- high gap means
                        home pitcher is much WORSE than away → P(T1 run)
                        should rise
                        B1 sees (home_era - away_era)
  - +era_gap_abs      : abs(away_era - home_era) per half (slate "spread")
  - +fip_gap_signed   : same idea but on FIP (probably more predictive
                        than raw ERA)
  - +era_gap+fip_gap  : both, see if they add or are collinear

Eval: STRONG NRFI hit rate at P>=0.58, STRONG YRFI hit rate at P<=0.42,
total P&L at -110.  Train 2024+2025 CLEAN, test 2026 graded.
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
LEAGUE_AVG_XERA = 4.20
NEUTRAL_PCT_RANK = 50.0
LEAGUE_NRFI = 0.50
WX_TEMP = 20.0; WX_WIND = 10.0; WX_HUMID = 60.0
FI_PARK = 0.50

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"

PAYOUT = 0.91


def coerce(s, d):
    try: f = float(s); return f if math.isfinite(f) else d
    except: return d


def load_park():
    return json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))


def load_ump_data():
    cache = json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8"))
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    return cache, rates["umpires"], rates["league_nrfi_rate"]


def get_ump(pk, ump_cache, ump_rates, league):
    rec = ump_cache.get(str(pk))
    if not rec: return league
    u = ump_rates.get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


def base_t1(r, park, ump):
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
        coerce(r.get("home_xera"),                 LEAGUE_AVG_XERA),
        coerce(r.get("home_whiff_pct_rank"),       NEUTRAL_PCT_RANK),
    ]


def base_b1(r, park, ump):
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
        coerce(r.get("away_xera"),                 LEAGUE_AVG_XERA),
        coerce(r.get("away_whiff_pct_rank"),       NEUTRAL_PCT_RANK),
    ]


def variant_extras(r, half, variant):
    """Return list of additional feature(s) for the given variant + half."""
    h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
    h_fip = coerce(r.get("home_fip"), LEAGUE_AVG_ERA)
    a_fip = coerce(r.get("away_fip"), LEAGUE_AVG_ERA)

    if variant == "baseline":
        return []
    if variant == "+era_gap_signed":
        # T1 = home pitcher pitches; positive value means home pitcher is
        # worse than away (away_era > home_era is NEGATIVE here -- careful).
        # Sign convention: gap > 0 means the pitcher in THIS half is worse
        # than the OTHER half's pitcher.
        if half == "t1":
            return [h_era - a_era]   # home_era - away_era; high means home is worse
        else:
            return [a_era - h_era]   # away_era - home_era; high means away is worse
    if variant == "+era_gap_abs":
        return [abs(h_era - a_era)]
    if variant == "+fip_gap_signed":
        if half == "t1":
            return [h_fip - a_fip]
        else:
            return [a_fip - h_fip]
    if variant == "+era_gap+fip_gap":
        if half == "t1":
            return [h_era - a_era, h_fip - a_fip]
        else:
            return [a_era - h_era, a_fip - h_fip]
    raise ValueError(variant)


def gather(csv_path, park, ump_cache, ump_rates, league, variant, clean):
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
        x_t = base_t1(r, park, ump) + variant_extras(r, "t1", variant)
        x_b = base_b1(r, park, ump) + variant_extras(r, "b1", variant)
        Xt.append(x_t); Xb.append(x_b)
        yt.append(t1y); yb.append(b1y)
        ynrfi.append(1 if actual == "NRFI" else 0)
        meta.append({"date": r.get("date", "")})
    return {
        "Xt": np.asarray(Xt, dtype=float), "yt": np.asarray(yt, dtype=int),
        "Xb": np.asarray(Xb, dtype=float), "yb": np.asarray(yb, dtype=int),
        "ynrfi": np.asarray(ynrfi, dtype=int), "meta": meta,
    }


def predict(t1m, b1m, X_t, X_b):
    return (1 - t1m.predict_proba(X_t)) * (1 - b1m.predict_proba(X_b))


def evaluate(p, y, t_nrfi=0.58, t_yrfi=0.42):
    n_idx = [i for i in range(len(p)) if p[i] >= t_nrfi]
    y_idx = [i for i in range(len(p)) if p[i] <= t_yrfi]
    n_w = sum(1 for i in n_idx if y[i] == 1)
    y_w = sum(1 for i in y_idx if y[i] == 0)
    n = len(n_idx); k = len(y_idx)
    tot = n + k
    wins = n_w + y_w
    pnl = wins * PAYOUT - (tot - wins) * 1.0
    brier = float(np.mean((p - y) ** 2))
    return {
        "brier": brier,
        "n_n": n, "n_w": n_w, "n_rate": n_w / max(n, 1),
        "y_n": k, "y_w": y_w, "y_rate": y_w / max(k, 1),
        "total": tot, "wins": wins,
        "rate": wins / max(tot, 1),
        "pnl": pnl,
    }


def run(variant, train_paths, test_path, park, ump_cache, ump_rates, league):
    blocks = [gather(p, park, ump_cache, ump_rates, league, variant, clean=True)
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
    te = gather(test_path, park, ump_cache, ump_rates, league, variant, clean=False)
    p_raw = predict(m_t, m_b, te["Xt"], te["Xb"])
    p_cal = np.array([cal.predict(float(p)) for p in p_raw])
    return evaluate(p_cal, te["ynrfi"]), m_t, m_b


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump_data()

    variants = [
        "baseline",
        "+era_gap_signed",
        "+era_gap_abs",
        "+fip_gap_signed",
        "+era_gap+fip_gap",
    ]

    print("=" * 110)
    print("  ERA / FIP GAP feature test vs Phase E.3 baseline")
    print("  Threshold: STRONG NRFI >= 0.58, STRONG YRFI <= 0.42")
    print("=" * 110)

    splits = [
        ("Train 2024+2025 -> Test 2026 (348 games, 26 days, holdout)",
         [BT_2024, BT_2025], PICKS_2026),
        ("Train 2024 -> Test 2025 (1500 games, 172 days)",
         [BT_2024], BT_2025),
        ("Train 2025 -> Test 2024 (1400 games, 174 days)",
         [BT_2025], BT_2024),
    ]

    summary = {v: {"pnls": [], "n_rates": [], "y_rates": [], "weights": []} for v in variants}

    for label, tr_paths, te_path in splits:
        print(f"\n  {label}")
        print(f"  {'variant':<22} {'Brier':>7} | {'STR NRFI':>15} {'STR YRFI':>15} {'TOTAL':>15} | {'P&L':>7} | {'T1_w':>7} {'B1_w':>7}")
        print("  " + "-" * 105)
        for v in variants:
            out, m_t, m_b = run(v, tr_paths, te_path, park, ump_cache, ump_rates, league)
            # Coefficient on the new gap feature(s) -- last weight in each model
            t1_w = m_t.w[-1] if v != "baseline" else 0.0
            b1_w = m_b.w[-1] if v != "baseline" else 0.0
            print(f"  {v:<22} {out['brier']:>7.4f} | "
                  f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
                  f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
                  f"{out['wins']:>3}/{out['total']:<3} ({out['rate']*100:>4.1f}%) | "
                  f"{out['pnl']:>+6.1f}u | "
                  f"{t1_w:>+7.4f} {b1_w:>+7.4f}")
            summary[v]["pnls"].append(out["pnl"])
            summary[v]["n_rates"].append(out["n_rate"])
            summary[v]["y_rates"].append(out["y_rate"])

    print()
    print("=" * 110)
    print("  3-SPLIT AGGREGATE (averages)")
    print("=" * 110)
    print(f"  {'variant':<22} {'avg P&L':>9} {'sum P&L':>9} {'NRFI rates':>30} {'YRFI rates':>30}")
    base_pnl = sum(summary["baseline"]["pnls"])
    for v in variants:
        s = summary[v]
        avg_pnl = sum(s["pnls"]) / len(s["pnls"])
        total_pnl = sum(s["pnls"])
        n_str = " / ".join(f"{r*100:.1f}%" for r in s["n_rates"])
        y_str = " / ".join(f"{r*100:.1f}%" for r in s["y_rates"])
        delta = total_pnl - base_pnl
        marker = " *" if delta > 5 else ("  " if v == "baseline" else "")
        print(f"  {v:<22} {avg_pnl:>+8.1f}u {total_pnl:>+8.1f}u  {n_str:>30}  {y_str:>30}{marker}")

    print()
    print("  Read: a variant ships only if total 3-split P&L beats baseline by >= 10u")
    print("  AND STRONG hit rates don't drop on the 2026 holdout (TEST 1).")


if __name__ == "__main__":
    main()

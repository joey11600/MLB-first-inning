#!/usr/bin/env python3
"""
test_top3_power.py -- test top-3 batter SLG / ISO as features.

User observation (4/28 NYY @ TEX): elite pitcher (deGrom 2.65 ERA) vs
heavy-hitting Yankees lineup (top3c SLG 0.551, ISO 0.311 -- both well
above league average).  Model said STRONG NRFI 66% based on pitcher
quality + (default) league-avg lineup data; actual was YRFI.  Power
signal was missing from the model.

Variants tested vs current production baseline (14 features per half):
  - +top3c_slg          : top-3 slugging (per half)
  - +top3c_iso          : top-3 isolated power (= SLG - AVG, per half)
  - +top3c_ops_proxy    : top3c_obp + top3c_slg added as one feature (OPS-like)
  - +top3c_slg_gap      : signed gap mirroring era_gap pattern
  - +top3c_iso_gap      : signed gap on ISO
  - +top3c_slg_iso      : both slg and iso (per half)

Train 2024+2025 CLEAN, test 2026 graded, 3-split cross-validation.
Threshold 0.56 / 0.44.  Target: STRONG zone hit-rate hold or improve,
+10u or better total 3-split P&L.
"""

import csv
import json
import math
import sys
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
LEAGUE_AVG_SLG = 0.414
LEAGUE_AVG_ISO = 0.169
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


def load_ump():
    cache = json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8"))
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    return cache, rates["umpires"], rates["league_nrfi_rate"]


def get_ump(pk, ump_cache, ump_rates, league):
    rec = ump_cache.get(str(pk))
    if not rec: return league
    u = ump_rates.get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


def base_t1(r, park, ump):
    """Current production baseline: 14 features."""
    h = r.get("home", "") or r.get("home_team", "")
    h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
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
        h_era - a_era,
        coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
    ]


def base_b1(r, park, ump):
    h = r.get("home", "") or r.get("home_team", "")
    h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
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
        a_era - h_era,
        coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
    ]


def variant_extras(r, half, variant):
    """Return additional feature(s) for the variant."""
    a_slg = coerce(r.get("away_top3c_slg"), LEAGUE_AVG_SLG)
    h_slg = coerce(r.get("home_top3c_slg"), LEAGUE_AVG_SLG)
    a_iso = coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO)
    h_iso = coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO)
    a_obp = coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP)
    h_obp = coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP)

    # In each half, the OPPOSING offense is the one that bats
    # T1 (home pitches): away offense bats -- use AWAY top3c stats
    # B1 (away pitches): home offense bats -- use HOME top3c stats
    if variant == "baseline":
        return []
    if variant == "+top3c_slg":
        return [a_slg] if half == "t1" else [h_slg]
    if variant == "+top3c_iso":
        return [a_iso] if half == "t1" else [h_iso]
    if variant == "+top3c_ops_proxy":
        # OPS-like: OBP + SLG added together (proxy)
        return [a_obp + a_slg] if half == "t1" else [h_obp + h_slg]
    if variant == "+top3c_slg_gap":
        # Gap of THIS half's batting team's SLG vs the other half's batting team's SLG
        # T1: away offense bats → away_slg vs home_slg, signed
        if half == "t1":
            return [a_slg - h_slg]
        else:
            return [h_slg - a_slg]
    if variant == "+top3c_iso_gap":
        return [a_iso - h_iso] if half == "t1" else [h_iso - a_iso]
    if variant == "+top3c_slg_iso":
        return [a_slg, a_iso] if half == "t1" else [h_slg, h_iso]
    raise ValueError(variant)


def gather(csv_path, park, ump_cache, ump_rates, league, variant, clean):
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
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
    return (np.asarray(Xt, dtype=float), np.asarray(yt, dtype=int),
            np.asarray(Xb, dtype=float), np.asarray(yb, dtype=int),
            np.asarray(ynrfi, dtype=int))


def predict(t1m, b1m, X_t, X_b):
    return (1 - t1m.predict_proba(X_t)) * (1 - b1m.predict_proba(X_b))


def evaluate(p, y, t_nrfi=0.56, t_yrfi=0.44):
    n_idx = [i for i in range(len(p)) if p[i] >= t_nrfi]
    y_idx = [i for i in range(len(p)) if p[i] <= t_yrfi]
    n_w = sum(1 for i in n_idx if y[i] == 1)
    y_w = sum(1 for i in y_idx if y[i] == 0)
    n = len(n_idx); k = len(y_idx)
    tot = n + k; wins = n_w + y_w
    pnl = wins * PAYOUT - (tot - wins) * 1.0
    return {"n_n": n, "n_w": n_w, "n_rate": n_w/max(n,1),
            "y_n": k, "y_w": y_w, "y_rate": y_w/max(k,1),
            "total": tot, "wins": wins, "rate": wins/max(tot,1), "pnl": pnl,
            "brier": float(np.mean((p - y) ** 2))}


def run(variant, train_paths, test_path, park, ump_cache, ump_rates, league):
    blocks = [gather(p, park, ump_cache, ump_rates, league, variant, clean=True)
              for p in train_paths]
    Xt = np.vstack([b[0] for b in blocks]); yt = np.concatenate([b[1] for b in blocks])
    Xb = np.vstack([b[2] for b in blocks]); yb = np.concatenate([b[3] for b in blocks])
    ynrfi_tr = np.concatenate([b[4] for b in blocks])
    m_t = LogReg.fit(Xt, yt, [f"t{i}" for i in range(Xt.shape[1])], l2=0.05)
    m_b = LogReg.fit(Xb, yb, [f"b{i}" for i in range(Xb.shape[1])], l2=0.05)
    p_tr = predict(m_t, m_b, Xt, Xb)
    cal = ProbCalibrator.fit([float(p) for p in p_tr], [int(y) for y in ynrfi_tr], n_bins=20)
    Xt_te, _, Xb_te, _, ynrfi_te = gather(test_path, park, ump_cache, ump_rates, league,
                                           variant, clean=False)
    p_raw = predict(m_t, m_b, Xt_te, Xb_te)
    p_cal = np.array([cal.predict(float(p)) for p in p_raw])
    return evaluate(p_cal, ynrfi_te), m_t, m_b


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump()

    variants = [
        "baseline",
        "+top3c_slg",
        "+top3c_iso",
        "+top3c_ops_proxy",
        "+top3c_slg_gap",
        "+top3c_iso_gap",
        "+top3c_slg_iso",
    ]

    splits = [
        ("Train 2024+2025 -> Test 2026 (holdout)", [BT_2024, BT_2025], PICKS_2026),
        ("Train 2024 -> Test 2025",                [BT_2024], BT_2025),
        ("Train 2025 -> Test 2024",                [BT_2025], BT_2024),
    ]

    print("=" * 110)
    print("  Top-3 power-signal feature test vs current production (Phase E.3 + era_gap + last10_nrfi)")
    print("  Threshold: STRONG NRFI >= 0.56, STRONG YRFI <= 0.44")
    print("=" * 110)

    summary = {v: {"pnls": []} for v in variants}

    for label, tr_paths, te_path in splits:
        print(f"\n  {label}")
        print(f"  {'variant':<22} {'Brier':>7} | {'STR NRFI':>15} {'STR YRFI':>15} {'TOTAL':>15} | {'P&L':>7}")
        print("  " + "-" * 100)
        for v in variants:
            out, m_t, m_b = run(v, tr_paths, te_path, park, ump_cache, ump_rates, league)
            print(f"  {v:<22} {out['brier']:>7.4f} | "
                  f"{out['n_w']:>3}/{out['n_n']:<3} ({out['n_rate']*100:>4.1f}%) "
                  f"{out['y_w']:>3}/{out['y_n']:<3} ({out['y_rate']*100:>4.1f}%) "
                  f"{out['wins']:>3}/{out['total']:<3} ({out['rate']*100:>4.1f}%) | "
                  f"{out['pnl']:>+6.1f}u")
            summary[v]["pnls"].append(out["pnl"])

    print()
    print("=" * 110)
    print("  3-SPLIT AGGREGATE")
    print("=" * 110)
    base = sum(summary["baseline"]["pnls"])
    print(f"  {'variant':<22} {'sum P&L':>9} {'delta vs base':>15}")
    for v in variants:
        s = summary[v]; total = sum(s["pnls"]); delta = total - base
        marker = " *" if delta > 10 else ("  " if v == "baseline" else "")
        print(f"  {v:<22} {total:>+8.1f}u {delta:>+14.1f}u{marker}")


if __name__ == "__main__":
    main()

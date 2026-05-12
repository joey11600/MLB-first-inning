#!/usr/bin/env python3
"""
test_strong_zone_features.py -- re-evaluate every feature variant we
tested in Phase A/B/C, but score them against the user's ACTUAL betting
strategy metrics (top-3 daily parlay hit rates) instead of global Brier.

A variant that improved Brier marginally but degraded the top-3 parlay
hit rate is worse for our use case.  A variant that made Brier slightly
worse but boosted parlay hit rate is better.

Variants tested:
  - slim_weather (current production baseline)
  - +last5_nrfi          (Phase A.2)
  - +top3c_obp           (Phase A.3)
  - +top3c_iso           (Phase A.4)
  - +ump_nrfi_b1         (Phase C, B1-only umpire rate)
  - +ump_split           (Phase C, T1/B1 split umpire rates)
  - all combinations

Reports per variant:
  - Top-K=3 mixed parlay hit rate + P&L
  - Top-K=3 YRFI-only parlay hit rate + P&L
  - Top-K=3 NRFI-only parlay hit rate + P&L
  - STRONG zone (>=0.62 / <=0.42) hit rates
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

PARLAY_PAYOUT_3 = 6.0  # rough 3-leg parlay payout at -110 each


def coerce(s, d):
    try: f = float(s); return f if math.isfinite(f) else d
    except: return d


def load_park():
    return json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))


def load_ump():
    cache = json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8"))
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    return cache, rates["umpires"], rates["league_nrfi_rate"]


# ---------- Feature builders ----------

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


def get_ump_rate(pk, ump_cache, ump_rates, league):
    rec = ump_cache.get(str(pk))
    if not rec: return league
    u = ump_rates.get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


def variant_extras(r, variant, half, ump_rate):
    """Return additional features for a given variant + half."""
    if variant == "slim_weather":
        return []
    if variant == "+last5":
        col = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        return [coerce(r.get(col), LEAGUE_NRFI)]
    if variant == "+top3c_obp":
        col = "away_top3c_obp" if half == "t1" else "home_top3c_obp"
        return [coerce(r.get(col), LEAGUE_AVG_OBP)]
    if variant == "+top3c_iso":
        col = "away_top3c_iso" if half == "t1" else "home_top3c_iso"
        return [coerce(r.get(col), LEAGUE_AVG_ISO)]
    if variant == "+ump_b1":
        return [ump_rate] if half == "b1" else []
    if variant == "+ump_both":
        return [ump_rate]
    if variant == "+last5+ump_b1":
        col = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        return [coerce(r.get(col), LEAGUE_NRFI)] + ([ump_rate] if half == "b1" else [])
    if variant == "+last5+top3c_obp":
        last5 = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        obp   = "away_top3c_obp"            if half == "t1" else "home_top3c_obp"
        return [coerce(r.get(last5), LEAGUE_NRFI),
                coerce(r.get(obp),   LEAGUE_AVG_OBP)]
    if variant == "+all":
        last5 = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        obp   = "away_top3c_obp"            if half == "t1" else "home_top3c_obp"
        iso   = "away_top3c_iso"            if half == "t1" else "home_top3c_iso"
        return [coerce(r.get(last5), LEAGUE_NRFI),
                coerce(r.get(obp),   LEAGUE_AVG_OBP),
                coerce(r.get(iso),   LEAGUE_AVG_ISO)] + ([ump_rate] if half == "b1" else [])
    raise ValueError(variant)


def gather(csv_path, park, variant, ump_cache, ump_rates, league_rate, clean_only):
    """Returns dict with arrays + metadata for parlay analysis."""
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    metadata = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"): continue
            if clean_only:
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
            ump = get_ump_rate(r.get("game_pk", ""), ump_cache, ump_rates, league_rate)
            x_t = slim_t1(r, park) + variant_extras(r, variant, "t1", ump)
            x_b = slim_b1(r, park) + variant_extras(r, variant, "b1", ump)
            Xt.append(x_t); Xb.append(x_b)
            yt.append(t1y); yb.append(b1y)
            ynrfi.append(1 if actual == "NRFI" else 0)
            metadata.append({
                "date": r.get("date", ""),
                "game_pk": r.get("game_pk", ""),
                "actual": actual,
            })
    return {
        "Xt": np.asarray(Xt, dtype=float), "yt": np.asarray(yt, dtype=int),
        "Xb": np.asarray(Xb, dtype=float), "yb": np.asarray(yb, dtype=int),
        "ynrfi": np.asarray(ynrfi, dtype=int),
        "metadata": metadata,
    }


def predict(t1_model, b1_model, X_t1, X_b1):
    p_t1 = t1_model.predict_proba(X_t1)
    p_b1 = b1_model.predict_proba(X_b1)
    return (1.0 - p_t1) * (1.0 - p_b1)


def evaluate(p_nrfi, ynrfi, metadata):
    """Compute zone hit rates and per-day top-3 parlay rates."""
    n = len(p_nrfi)

    # Strong zone hits
    nrfi_strong = [(i, p_nrfi[i]) for i in range(n) if p_nrfi[i] >= 0.62]
    yrfi_strong = [(i, p_nrfi[i]) for i in range(n) if p_nrfi[i] <= 0.42]
    nrfi_strong_w = sum(1 for i, _ in nrfi_strong if ynrfi[i] == 1)
    yrfi_strong_w = sum(1 for i, _ in yrfi_strong if ynrfi[i] == 0)

    # Per-day groupings
    by_day = defaultdict(list)
    for i, m in enumerate(metadata):
        by_day[m["date"]].append(i)

    # Top-3 mixed parlay (rank by edge = |p - 0.5|)
    mixed_p_w = mixed_p_n = 0
    nrfi_p_w = nrfi_p_n = 0
    yrfi_p_w = yrfi_p_n = 0
    for date, idxs in by_day.items():
        if len(idxs) < 3:
            continue
        # Top-3 mixed
        top_mixed = sorted(idxs, key=lambda i: -abs(p_nrfi[i] - 0.50))[:3]
        if all(
            (ynrfi[i] == 1 if p_nrfi[i] >= 0.50 else ynrfi[i] == 0)
            for i in top_mixed
        ):
            mixed_p_w += 1
        mixed_p_n += 1
        # Top-3 NRFI side
        top_nrfi = sorted(idxs, key=lambda i: -p_nrfi[i])[:3]
        if all(ynrfi[i] == 1 for i in top_nrfi):
            nrfi_p_w += 1
        nrfi_p_n += 1
        # Top-3 YRFI side
        top_yrfi = sorted(idxs, key=lambda i: p_nrfi[i])[:3]
        if all(ynrfi[i] == 0 for i in top_yrfi):
            yrfi_p_w += 1
        yrfi_p_n += 1

    # Brier for reference
    brier = float(np.mean((p_nrfi - ynrfi) ** 2))

    # P&L
    def pnl(w, n):
        if not n: return 0.0, 0.0
        return w * PARLAY_PAYOUT_3 - (n - w), (w * PARLAY_PAYOUT_3 - (n - w)) / n

    return {
        "brier":            brier,
        "n_strong_nrfi":    len(nrfi_strong),
        "w_strong_nrfi":    nrfi_strong_w,
        "rate_strong_nrfi": nrfi_strong_w / len(nrfi_strong) if nrfi_strong else 0,
        "n_strong_yrfi":    len(yrfi_strong),
        "w_strong_yrfi":    yrfi_strong_w,
        "rate_strong_yrfi": yrfi_strong_w / len(yrfi_strong) if yrfi_strong else 0,
        "mixed_p_w":        mixed_p_w, "mixed_p_n": mixed_p_n,
        "mixed_pnl":        pnl(mixed_p_w, mixed_p_n)[0],
        "nrfi_p_w":         nrfi_p_w, "nrfi_p_n": nrfi_p_n,
        "nrfi_pnl":         pnl(nrfi_p_w, nrfi_p_n)[0],
        "yrfi_p_w":         yrfi_p_w, "yrfi_p_n": yrfi_p_n,
        "yrfi_pnl":         pnl(yrfi_p_w, yrfi_p_n)[0],
    }


def main():
    park = load_park()
    ump_cache, ump_rates, league = load_ump()

    variants = [
        "slim_weather",
        "+last5",
        "+top3c_obp",
        "+top3c_iso",
        "+ump_both",
        "+ump_b1",
        "+last5+top3c_obp",
        "+last5+ump_b1",
        "+all",
    ]

    print("=" * 110)
    print("  Re-evaluation for PARLAY strategy  (train 2024+2025 CLEAN ; test 2026 graded ; 26 days)")
    print("=" * 110)
    print(f"  payout assumed: 3-leg parlay = +600 / 6x at -110 odds.  P&L = (parlays_won * 6) - (parlays_lost * 1)")
    print()
    print(f"  {'variant':<22} {'Brier':>7} | "
          f"{'STR NRFI':>10} {'STR YRFI':>10} | "
          f"{'mix-3':>9} {'mix$':>7} | "
          f"{'NRFI-3':>9} {'NRFI$':>7} | "
          f"{'YRFI-3':>9} {'YRFI$':>7}")
    print("  " + "-" * 108)

    for v in variants:
        # Train
        d24 = gather(BT_2024, park, v, ump_cache, ump_rates, league, clean_only=True)
        d25 = gather(BT_2025, park, v, ump_cache, ump_rates, league, clean_only=True)
        Xt = np.vstack([d24["Xt"], d25["Xt"]])
        yt = np.concatenate([d24["yt"], d25["yt"]])
        Xb = np.vstack([d24["Xb"], d25["Xb"]])
        yb = np.concatenate([d24["yb"], d25["yb"]])

        m_t = LogReg.fit(Xt, yt, [f"t{i}" for i in range(Xt.shape[1])], l2=0.05)
        m_b = LogReg.fit(Xb, yb, [f"b{i}" for i in range(Xb.shape[1])], l2=0.05)

        # Train predictions for calibration
        p_tr = predict(m_t, m_b, Xt, Xb)
        ynrfi_tr = np.concatenate([d24["ynrfi"], d25["ynrfi"]])
        cal = ProbCalibrator.fit([float(p) for p in p_tr],
                                  [int(y) for y in ynrfi_tr], n_bins=20)

        # Test (uncalibrated and calibrated)
        te = gather(PICKS_2026, park, v, ump_cache, ump_rates, league, clean_only=False)
        p_raw = predict(m_t, m_b, te["Xt"], te["Xb"])
        p_cal = np.array([cal.predict(float(p)) for p in p_raw])

        out = evaluate(p_cal, te["ynrfi"], te["metadata"])
        print(f"  {v:<22} {out['brier']:>7.4f} | "
              f"{out['w_strong_nrfi']:>2}/{out['n_strong_nrfi']:<2} ({out['rate_strong_nrfi']*100:>4.1f}%) "
              f"{out['w_strong_yrfi']:>2}/{out['n_strong_yrfi']:<2} ({out['rate_strong_yrfi']*100:>4.1f}%) | "
              f"{out['mixed_p_w']:>2}/{out['mixed_p_n']:<2} ({out['mixed_p_w']/max(out['mixed_p_n'],1)*100:>4.1f}%) "
              f"{out['mixed_pnl']:>+6.1f}u | "
              f"{out['nrfi_p_w']:>2}/{out['nrfi_p_n']:<2} ({out['nrfi_p_w']/max(out['nrfi_p_n'],1)*100:>4.1f}%) "
              f"{out['nrfi_pnl']:>+6.1f}u | "
              f"{out['yrfi_p_w']:>2}/{out['yrfi_p_n']:<2} ({out['yrfi_p_w']/max(out['yrfi_p_n'],1)*100:>4.1f}%) "
              f"{out['yrfi_pnl']:>+6.1f}u")

    print()
    print("  Read: 'mix-3' = top-3 by edge (mix of NRFI/YRFI per day).")
    print("        'NRFI-3' = top-3 highest-P(NRFI) parlayed.")
    print("        'YRFI-3' = top-3 lowest-P(NRFI) (= top-3 YRFI).")
    print("  $ columns are net P&L over 26 graded days assuming 1u stake per parlay.")


if __name__ == "__main__":
    main()

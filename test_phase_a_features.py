#!/usr/bin/env python3
"""
test_phase_a_features.py -- test the Phase A feature candidates that
ACTUALLY exist in our backtest CSVs at decent coverage:

  - pitcher_last5_pitcher_nrfi  (proxy for "first-inning ERA")
  - pitcher_last10_pitcher_nrfi (longer-window proxy)
  - top3c_obp                    (point-in-time top-3 OBP -- replaces team OBP)
  - top3c_iso                    (top-3 power)

Train on 2024+2025 CLEAN rows (the proven best subset), test on 2026
graded.  Compare against slim_weather baseline (current production).

Each variant ADDS features to the slim_weather baseline (7 features per
half).  Pitcher recent-form goes in the half where that pitcher pitches.
Batter splits go in the half where that team bats.
"""

import csv
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

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_ISO = 0.169
WX_TEMP_DEFAULT     = 20.0
WX_WIND_DEFAULT     = 10.0
WX_HUMIDITY_DEFAULT = 60.0
FI_PARK_DEFAULT = 0.50
LEAGUE_AVG_NRFI_RATE = 0.50  # neutral prior for missing pitcher_last5

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"


def coerce(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def base_t1(r, park_lookup):
    home = r.get("home", "") or r.get("home_team", "")
    fi_park = park_lookup.get(home, FI_PARK_DEFAULT)
    return [
        fi_park,
        coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
        coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
        coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
        coerce(r.get("wx_is_dome"),  0.0),
    ]


def base_b1(r, park_lookup):
    home = r.get("home", "") or r.get("home_team", "")
    fi_park = park_lookup.get(home, FI_PARK_DEFAULT)
    return [
        fi_park,
        coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
        coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
        coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
        coerce(r.get("wx_is_dome"),  0.0),
    ]


def variant_extras(r, variant: str, half: str) -> list[float]:
    """Return ADDITIONAL features (beyond slim_weather base) for a given
    variant + half ('t1' = home pitcher pitches; 'b1' = away pitcher pitches)."""
    if variant == "slim_weather":
        return []
    if variant == "+last5_nrfi":
        # Pitcher's last-5 NRFI rate -- the proxy for "first-inning ERA".
        # T1 = home pitcher, so home_p_last5_pitcher_nrfi
        col = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        return [coerce(r.get(col), LEAGUE_AVG_NRFI_RATE)]
    if variant == "+last10_nrfi":
        col = "home_p_last10_pitcher_nrfi" if half == "t1" else "away_p_last10_pitcher_nrfi"
        return [coerce(r.get(col), LEAGUE_AVG_NRFI_RATE)]
    if variant == "+top3c_obp":
        # Top-3 batters' point-in-time OBP -- replaces team OBP.  T1: away offense bats,
        # so away_top3c_obp.  B1: home offense bats.
        col = "away_top3c_obp" if half == "t1" else "home_top3c_obp"
        return [coerce(r.get(col), LEAGUE_AVG_OBP)]
    if variant == "+top3c_iso":
        col = "away_top3c_iso" if half == "t1" else "home_top3c_iso"
        return [coerce(r.get(col), LEAGUE_AVG_ISO)]
    if variant == "+top3c_obp+iso":
        obp = "away_top3c_obp" if half == "t1" else "home_top3c_obp"
        iso = "away_top3c_iso" if half == "t1" else "home_top3c_iso"
        return [coerce(r.get(obp), LEAGUE_AVG_OBP),
                coerce(r.get(iso), LEAGUE_AVG_ISO)]
    if variant == "+last5+top3c_obp":
        last5 = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        obp   = "away_top3c_obp"            if half == "t1" else "home_top3c_obp"
        return [coerce(r.get(last5), LEAGUE_AVG_NRFI_RATE),
                coerce(r.get(obp),   LEAGUE_AVG_OBP)]
    if variant == "+all_phase_a":
        last5 = "home_p_last5_pitcher_nrfi" if half == "t1" else "away_p_last5_pitcher_nrfi"
        obp   = "away_top3c_obp"            if half == "t1" else "home_top3c_obp"
        iso   = "away_top3c_iso"            if half == "t1" else "home_top3c_iso"
        return [coerce(r.get(last5), LEAGUE_AVG_NRFI_RATE),
                coerce(r.get(obp),   LEAGUE_AVG_OBP),
                coerce(r.get(iso),   LEAGUE_AVG_ISO)]
    raise ValueError(variant)


def gather(csv_path, park, variant, clean_only):
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"): continue
            if clean_only:
                ap_q = (r.get("away_pitcher_q") or "").lower()
                hp_q = (r.get("home_pitcher_q") or "").lower()
                if ap_q == "avg" or hp_q == "avg": continue
            t1_runs = r.get("fi_away_runs", "")
            b1_runs = r.get("fi_home_runs", "")
            if t1_runs == "" or b1_runs == "": continue
            try:
                t1y = 1 if int(float(t1_runs)) > 0 else 0
                b1y = 1 if int(float(b1_runs)) > 0 else 0
            except (TypeError, ValueError):
                continue
            t1_x = base_t1(r, park) + variant_extras(r, variant, "t1")
            b1_x = base_b1(r, park) + variant_extras(r, variant, "b1")
            Xt.append(t1_x); yt.append(t1y)
            Xb.append(b1_x); yb.append(b1y)
            ynrfi.append(1 if actual == "NRFI" else 0)
    return (np.asarray(Xt, dtype=float), np.asarray(yt, dtype=int),
            np.asarray(Xb, dtype=float), np.asarray(yb, dtype=int),
            np.asarray(ynrfi, dtype=int))


def brier(p, y): return float(np.mean((p - y) ** 2))


def q5_hit(p, y):
    order = np.argsort(p); n = len(p)
    q5 = order[-(n // 5):]
    return float(y[q5].mean()), int(y[q5].sum()), len(q5)


def q1_yrfi(p, y):
    order = np.argsort(p); n = len(p)
    q1 = order[:(n // 5)]
    wins = int((y[q1] == 0).sum())
    return wins / len(q1) if len(q1) else 0.0, wins, len(q1)


def main():
    import json
    with open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8") as f:
        park = json.load(f)

    variants = [
        "slim_weather",        # baseline (current production)
        "+last5_nrfi",         # + pitcher last-5 NRFI rate
        "+last10_nrfi",        # + pitcher last-10 NRFI rate
        "+top3c_obp",          # + top-3 point-in-time OBP
        "+top3c_iso",          # + top-3 ISO
        "+top3c_obp+iso",      # + top-3 OBP + ISO together
        "+last5+top3c_obp",    # + best of each (recent-form + top-3 OBP)
        "+all_phase_a",        # + last5 + top3c OBP + top3c ISO
    ]

    print("=" * 90)
    print("  Phase A feature test  (train: 2024+2025 CLEAN ; test: 2026 graded)")
    print("=" * 90)
    print(f"  {'variant':<22} {'train N':>7} {'2026 Brier':>11} "
          f"{'2026 mean':>10} {'2026 Q5 NRFI':>15} {'2026 Q1 YRFI':>15}")
    print("  " + "-" * 88)

    for v in variants:
        Xt_24, yt_24, Xb_24, yb_24, _ = gather(BT_2024, park, v, True)
        Xt_25, yt_25, Xb_25, yb_25, _ = gather(BT_2025, park, v, True)
        Xt_tr = np.vstack([Xt_24, Xt_25]); yt_tr = np.concatenate([yt_24, yt_25])
        Xb_tr = np.vstack([Xb_24, Xb_25]); yb_tr = np.concatenate([yb_24, yb_25])
        n_train = len(yt_tr)

        m_t1 = LogReg.fit(Xt_tr, yt_tr,
                           [f"t{i}" for i in range(Xt_tr.shape[1])], l2=0.05)
        m_b1 = LogReg.fit(Xb_tr, yb_tr,
                           [f"b{i}" for i in range(Xb_tr.shape[1])], l2=0.05)

        Xt_26, _, Xb_26, _, ynrfi_26 = gather(PICKS_2026, park, v, False)
        p_nrfi = (1 - m_t1.predict_proba(Xt_26)) * (1 - m_b1.predict_proba(Xb_26))

        b = brier(p_nrfi, ynrfi_26)
        m = float(p_nrfi.mean())
        q5_r, q5_w, q5_n = q5_hit(p_nrfi, ynrfi_26)
        q1_r, q1_w, q1_n = q1_yrfi(p_nrfi, ynrfi_26)

        print(f"  {v:<22} {n_train:>7} {b:>11.4f} {m*100:>9.2f}%  "
              f"{q5_w}-{q5_n - q5_w} ({q5_r*100:>4.1f}%)".ljust(45) +
              f" {q1_w}-{q1_n - q1_w} ({q1_r*100:>4.1f}%)")

    print()
    print("  Read: a variant ships only if it improves Brier (lower is better)")
    print("  AND improves at least one of Q5 NRFI / Q1 YRFI by >= 2pp.")
    print("  Sub-1pp moves on N=348 are sample noise.")


if __name__ == "__main__":
    main()

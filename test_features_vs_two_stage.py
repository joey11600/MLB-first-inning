#!/usr/bin/env python3
"""
test_features_vs_two_stage.py -- comprehensive feature test against the
current LR-v3 two-stage slim baseline.

Earlier tests in the project benchmarked features against the V1/V2 single
LR model.  Now that production is two-stage with separate T1 and B1 LRs,
those previous null results may not transfer cleanly.  Re-test the same
candidate features (handedness, arsenal, weather) against the actual
production architecture.

Each variant ADDS features to the SLIM baseline (3 features per half),
keeping the half-inning structure intact -- features assigned to the
relevant pitcher's half:

  T1 (home pitcher pitches): home_pitcher_*, away_offense_*
  B1 (away pitcher pitches): away_pitcher_*, home_offense_*
  weather: in both halves

For each variant, train on 2024+2025, test on 2026 graded games
(N=348).  Report Brier, mean predicted vs actual, Q5 NRFI hit rate,
Q1 YRFI hit rate.

Test only -- never modifies the production model files.
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
LEAGUE_AVG_HR9 = 1.20
LEAGUE_AVG_BB9 = 3.20
LEAGUE_AVG_K9  = 8.9
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_SLG = 0.414
FI_PARK_DEFAULT = 0.50

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"


def coerce(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ----- Feature builders per half -----

def build_t1_features(r, park_lookup, variant: str) -> list[float]:
    """Build T1 (home pitcher's half) feature vector for a given variant."""
    home = r.get("home", "") or r.get("home_team", "")
    fi_park = park_lookup.get(home, FI_PARK_DEFAULT)
    base = [
        fi_park,
        coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
    ]
    if variant == "slim":
        return base
    elif variant == "slim_hand":
        return base + [
            coerce(r.get("home_pitcher_throws_l"), 0.0),
            coerce(r.get("away_top3_lhb"),         1.0),
            coerce(r.get("away_top3_platoon"),     0.0),
            coerce(r.get("away_top3_switch"),      0.0),
        ]
    elif variant == "slim_arsenal":
        return base + [
            coerce(r.get("home_arsenal_fb_pct"),   0.55),
            coerce(r.get("home_arsenal_fb_velo"),  93.0),
            coerce(r.get("home_arsenal_velo_gap"), 9.0),
        ]
    elif variant == "slim_weather":
        return base + [
            coerce(r.get("wx_temp_c"),    20.0),
            coerce(r.get("wx_wind_kmh"),  10.0),
            coerce(r.get("wx_humidity"),  60.0),
            coerce(r.get("wx_is_dome"),   0.0),
        ]
    elif variant == "slim_all":
        return base + [
            # handedness
            coerce(r.get("home_pitcher_throws_l"), 0.0),
            coerce(r.get("away_top3_lhb"),         1.0),
            coerce(r.get("away_top3_platoon"),     0.0),
            # arsenal
            coerce(r.get("home_arsenal_fb_pct"),   0.55),
            coerce(r.get("home_arsenal_fb_velo"),  93.0),
            # weather
            coerce(r.get("wx_temp_c"),    20.0),
            coerce(r.get("wx_wind_kmh"),  10.0),
            coerce(r.get("wx_is_dome"),   0.0),
        ]
    else:
        raise ValueError(variant)


def build_b1_features(r, park_lookup, variant: str) -> list[float]:
    """Build B1 (away pitcher's half) feature vector."""
    home = r.get("home", "") or r.get("home_team", "")
    fi_park = park_lookup.get(home, FI_PARK_DEFAULT)
    base = [
        fi_park,
        coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
    ]
    if variant == "slim":
        return base
    elif variant == "slim_hand":
        return base + [
            coerce(r.get("away_pitcher_throws_l"), 0.0),
            coerce(r.get("home_top3_lhb"),         1.0),
            coerce(r.get("home_top3_platoon"),     0.0),
            coerce(r.get("home_top3_switch"),      0.0),
        ]
    elif variant == "slim_arsenal":
        return base + [
            coerce(r.get("away_arsenal_fb_pct"),   0.55),
            coerce(r.get("away_arsenal_fb_velo"),  93.0),
            coerce(r.get("away_arsenal_velo_gap"), 9.0),
        ]
    elif variant == "slim_weather":
        return base + [
            coerce(r.get("wx_temp_c"),    20.0),
            coerce(r.get("wx_wind_kmh"),  10.0),
            coerce(r.get("wx_humidity"),  60.0),
            coerce(r.get("wx_is_dome"),   0.0),
        ]
    elif variant == "slim_all":
        return base + [
            coerce(r.get("away_pitcher_throws_l"), 0.0),
            coerce(r.get("home_top3_lhb"),         1.0),
            coerce(r.get("home_top3_platoon"),     0.0),
            coerce(r.get("away_arsenal_fb_pct"),   0.55),
            coerce(r.get("away_arsenal_fb_velo"),  93.0),
            coerce(r.get("wx_temp_c"),    20.0),
            coerce(r.get("wx_wind_kmh"),  10.0),
            coerce(r.get("wx_is_dome"),   0.0),
        ]
    else:
        raise ValueError(variant)


def gather(csv_path, park_lookup, variant: str):
    """Returns (X_t1, y_t1, X_b1, y_b1, y_nrfi)."""
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            t1_runs = r.get("fi_away_runs", "")
            b1_runs = r.get("fi_home_runs", "")
            if t1_runs == "" or b1_runs == "":
                continue
            try:
                t1y = 1 if int(float(t1_runs)) > 0 else 0
                b1y = 1 if int(float(b1_runs)) > 0 else 0
            except (TypeError, ValueError):
                continue
            Xt.append(build_t1_features(r, park_lookup, variant))
            Xb.append(build_b1_features(r, park_lookup, variant))
            yt.append(t1y); yb.append(b1y)
            ynrfi.append(1 if actual == "NRFI" else 0)
    return (np.asarray(Xt, dtype=float), np.asarray(yt, dtype=int),
            np.asarray(Xb, dtype=float), np.asarray(yb, dtype=int),
            np.asarray(ynrfi, dtype=int))


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def q5_hit(p, y):
    order = np.argsort(p)
    n = len(p)
    q5 = order[-(n // 5):]
    return float(y[q5].mean()), int(y[q5].sum()), len(q5)


def q1_yrfi(p, y):
    order = np.argsort(p)
    n = len(p)
    q1 = order[:(n // 5)]
    wins = int((y[q1] == 0).sum())
    return wins / len(q1) if len(q1) else 0.0, wins, len(q1)


def load_park():
    import json
    with open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8") as f:
        return json.load(f)


def main():
    park = load_park()
    variants = ["slim", "slim_hand", "slim_arsenal", "slim_weather", "slim_all"]

    print("=" * 78)
    print("  Feature variants vs current LR-v3 two-stage SLIM baseline")
    print("=" * 78)
    print(f"\n  Train: 2024+2025 backtests (N=4802)")
    print(f"  Test:  2026 graded picks (N~348)")
    print()

    # Header
    print(f"  {'variant':<16} {'2025 Brier':>11} {'2026 Brier':>11} "
          f"{'2026 mean':>10} {'2026 Q5 NRFI':>14} {'2026 Q1 YRFI':>14}")
    print("  " + "-" * 78)

    for variant in variants:
        # Train: 2024+2025
        Xt_24, yt_24, Xb_24, yb_24, _ = gather(BT_2024, park, variant)
        Xt_25, yt_25, Xb_25, yb_25, _ = gather(BT_2025, park, variant)
        Xt_tr = np.vstack([Xt_24, Xt_25]); yt_tr = np.concatenate([yt_24, yt_25])
        Xb_tr = np.vstack([Xb_24, Xb_25]); yb_tr = np.concatenate([yb_24, yb_25])

        # Synthetic feature names (just for the LR class)
        n_t = Xt_tr.shape[1]
        n_b = Xb_tr.shape[1]
        t_names = [f"t{i}" for i in range(n_t)]
        b_names = [f"b{i}" for i in range(n_b)]

        m_t1 = LogReg.fit(Xt_tr, yt_tr, t_names, l2=0.05)
        m_b1 = LogReg.fit(Xb_tr, yb_tr, b_names, l2=0.05)

        # Score on 2025 holdout (train data overlaps -- still useful for sanity)
        # and 2026 graded
        Xt_25_only, _, Xb_25_only, _, ynrfi_25 = gather(BT_2025, park, variant)
        p_t1_25 = m_t1.predict_proba(Xt_25_only)
        p_b1_25 = m_b1.predict_proba(Xb_25_only)
        p_nrfi_25 = (1 - p_t1_25) * (1 - p_b1_25)

        Xt_26, _, Xb_26, _, ynrfi_26 = gather(PICKS_2026, park, variant)
        p_t1_26 = m_t1.predict_proba(Xt_26)
        p_b1_26 = m_b1.predict_proba(Xb_26)
        p_nrfi_26 = (1 - p_t1_26) * (1 - p_b1_26)

        b_25 = brier(p_nrfi_25, ynrfi_25)
        b_26 = brier(p_nrfi_26, ynrfi_26)
        mean_26 = float(p_nrfi_26.mean())
        q5_r, q5_w, q5_n = q5_hit(p_nrfi_26, ynrfi_26)
        q1_r, q1_w, q1_n = q1_yrfi(p_nrfi_26, ynrfi_26)

        print(f"  {variant:<16} {b_25:>11.4f} {b_26:>11.4f} {mean_26*100:>9.2f}% "
              f"{q5_w}-{q5_n - q5_w} ({q5_r*100:>4.1f}%)".ljust(72) +
              f" {q1_w}-{q1_n - q1_w} ({q1_r*100:>4.1f}%)")

    print()
    print("  Notes:")
    print("  - 2025 Brier is in-sample for the train set, only loosely meaningful.")
    print("  - 2026 Brier is the honest holdout.")
    print("  - Q5 NRFI = top 20% predicted NRFI -> what fraction were actually NRFI?")
    print("  - Q1 YRFI = bottom 20% predicted NRFI -> what fraction were actually YRFI?")
    print("  - SLIM is the current production baseline.  Beat it on Q5 + Q1 to ship.")


if __name__ == "__main__":
    main()

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


def gather(csv_path, park_lookup, variant: str, clean_only: bool = False):
    """Returns (X_t1, y_t1, X_b1, y_b1, y_nrfi).
    If clean_only=True, drops rows where either pitcher_q == 'avg' (synthetic
    data substitutions).  This is critical because ~22% of historical
    backtest rows were defaulted at backtest-generation time and trained
    the LR on synthetic features."""
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    n_dropped_avg = 0
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            if clean_only:
                ap_q = (r.get("away_pitcher_q") or "").lower()
                hp_q = (r.get("home_pitcher_q") or "").lower()
                if ap_q == "avg" or hp_q == "avg":
                    n_dropped_avg += 1
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
    if clean_only and n_dropped_avg:
        print(f"    [{Path(csv_path).name}] dropped {n_dropped_avg} rows with avg-quality pitchers")
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


def run_test(park, variants, clean_only: bool, label: str):
    print("=" * 80)
    print(f"  {label}")
    print("=" * 80)
    print(f"  {'variant':<16} {'train N':>7} {'2026 Brier':>11} "
          f"{'2026 mean':>10} {'2026 Q5 NRFI':>14} {'2026 Q1 YRFI':>14}")
    print("  " + "-" * 80)

    for variant in variants:
        Xt_24, yt_24, Xb_24, yb_24, _ = gather(BT_2024, park, variant, clean_only)
        Xt_25, yt_25, Xb_25, yb_25, _ = gather(BT_2025, park, variant, clean_only)
        Xt_tr = np.vstack([Xt_24, Xt_25]); yt_tr = np.concatenate([yt_24, yt_25])
        Xb_tr = np.vstack([Xb_24, Xb_25]); yb_tr = np.concatenate([yb_24, yb_25])
        n_train = len(yt_tr)

        n_t = Xt_tr.shape[1]
        n_b = Xb_tr.shape[1]
        m_t1 = LogReg.fit(Xt_tr, yt_tr, [f"t{i}" for i in range(n_t)], l2=0.05)
        m_b1 = LogReg.fit(Xb_tr, yb_tr, [f"b{i}" for i in range(n_b)], l2=0.05)

        # Test on 2026 (always full -- 2026 is already nearly all clean)
        Xt_26, _, Xb_26, _, ynrfi_26 = gather(PICKS_2026, park, variant, False)
        p_nrfi_26 = (1 - m_t1.predict_proba(Xt_26)) * (1 - m_b1.predict_proba(Xb_26))

        b_26 = brier(p_nrfi_26, ynrfi_26)
        mean_26 = float(p_nrfi_26.mean())
        q5_r, q5_w, q5_n = q5_hit(p_nrfi_26, ynrfi_26)
        q1_r, q1_w, q1_n = q1_yrfi(p_nrfi_26, ynrfi_26)

        print(f"  {variant:<16} {n_train:>7} {b_26:>11.4f} {mean_26*100:>9.2f}%  "
              f"{q5_w}-{q5_n - q5_w} ({q5_r*100:>4.1f}%)".ljust(35) +
              f" {q1_w}-{q1_n - q1_w} ({q1_r*100:>4.1f}%)")
    print()


def main():
    park = load_park()
    # All combinations: each individual feature group + pairs + the full kitchen sink
    variants = [
        "slim",                # baseline (current production)
        "slim_hand",           # + handedness only
        "slim_arsenal",        # + arsenal only
        "slim_weather",        # + weather only
        "slim_all",            # + all three
    ]

    run_test(park, variants, clean_only=True,
             label="CLEAN rows only (pitcher_q != 'avg' on both sides)\n"
                   "  Training data verified against MLB API: outcomes, names, parks all real.\n"
                   "  Q5 NRFI = top 20% predicted NRFI; Q1 YRFI = bottom 20% (= top YRFI).")

    print("  How to read:")
    print("  - SLIM is the current production baseline (3 features per half).")
    print("  - To beat SLIM and ship, a variant needs to improve EITHER Q5 NRFI")
    print("    OR Q1 YRFI by >= 3pp on the 2026 holdout, without hurting Brier.")
    print("  - 1-2pp improvements are likely sample noise on N=348 graded picks.")


if __name__ == "__main__":
    main()

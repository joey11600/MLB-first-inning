#!/usr/bin/env python3
"""
two_stage_model.py -- physics-correct NRFI prediction.

Instead of one regression mixing all 11 features (which produces
multicollinearity-driven wrong-sign weights and leaves the away pitcher
practically unused), train two separate logistic regressions matching
the actual structure of a first inning:

  T1 (top of 1st):  home pitcher vs away offense  -> P(T1 has run)
                    features = home_fip, home_hr9, home_bb9,
                               away_obp, away_slg, fi_park_nrfi_rate
  B1 (bot of 1st):  away pitcher vs home offense  -> P(B1 has run)
                    features = away_fip, away_hr9, away_bb9,
                               home_obp, home_slg, fi_park_nrfi_rate

Then combine assuming half-inning independence (the standard betting
model):
                    P(NRFI) = (1 - P(T1 run)) * (1 - P(B1 run))

This forces each pitcher into the half-inning they actually pitch, so
the away pitcher CANNOT be ignored -- it's the only pitcher in the B1
model.

Outputs:
  data/lr_t1.json  -- T1 model
  data/lr_b1.json  -- B1 model

Test command (after training):
  python two_stage_model.py --test data/backtests/backtest_2025-04-01_to_2025-09-30.csv
"""

import argparse
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
LEAGUE_AVG_HR9 = 1.20
LEAGUE_AVG_BB9 = 3.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_SLG = 0.414
LEAGUE_AVG_ISO = 0.169
FI_PARK_DEFAULT = 0.50

# Weather defaults used when wx_* cells are blank (which is the signal for
# domed parks AND for outdoor games where the open-meteo lookup failed).
# The model effectively learns "no weather signal" for any row at these
# values, with wx_is_dome=1 marking the indoor case explicitly.
WX_TEMP_DEFAULT     = 20.0
WX_WIND_DEFAULT     = 10.0
WX_HUMIDITY_DEFAULT = 60.0

T1_FEATURES = ["fi_park_nrfi_rate",
               "home_fip", "home_hr9", "home_bb9",
               "away_obp", "away_slg"]
B1_FEATURES = ["fi_park_nrfi_rate",
               "away_fip", "away_hr9", "away_bb9",
               "home_obp", "home_slg"]
# Slim variants: drop the multicollinear wrong-sign features. Keep one
# pitcher quality stat per side to avoid the FIP<->HR9<->BB9 correlation
# pile-up, and only the strongest offense indicator.
T1_SLIM_FEATURES = ["fi_park_nrfi_rate", "home_fip", "away_obp"]
B1_SLIM_FEATURES = ["fi_park_nrfi_rate", "away_fip", "home_obp"]

# Slim + K9 variants: add the pitcher's K/9, which the WIN/LOSS forensic
# showed has a Cohen's d = 0.80 effect within LEAN YRFI (highly significant)
# despite being absent from the slim feature set.
T1_SLIM_K9_FEATURES = ["fi_park_nrfi_rate", "home_fip", "home_k9", "away_obp"]
B1_SLIM_K9_FEATURES = ["fi_park_nrfi_rate", "away_fip", "away_k9", "home_obp"]

# Slim + Weather variants: park + pitcher quality + opposing OBP +
# day-of-game environment.  The handpicked test (clean rows only,
# train 2024+2025, test 2026) showed slim_weather as the only variant
# that beat slim baseline on Brier (0.2441 vs 0.2446) AND Q5 NRFI
# (43-26 vs 41-28).  See test_features_vs_two_stage.py.
#
# IMPORTANT: For domed parks (ARI, HOU, MIA, MIL, SEA, TB, TEX, TOR),
# the weather columns in training data are blank (None) -> coerce to
# league-mean defaults at fit/predict time, with wx_is_dome=1 acting
# as the "ignore weather" switch for the model.
T1_SLIM_WEATHER_FEATURES = ["fi_park_nrfi_rate", "home_fip", "away_obp",
                            "wx_temp_c", "wx_wind_kmh",
                            "wx_humidity", "wx_is_dome"]
B1_SLIM_WEATHER_FEATURES = ["fi_park_nrfi_rate", "away_fip", "home_obp",
                            "wx_temp_c", "wx_wind_kmh",
                            "wx_humidity", "wx_is_dome"]

# Phase E.3 variant -- combines slim_weather with:
#   Phase D additions: pitcher last-5 NRFI rate, top-3 batters' point-in-time
#                      OBP, home-plate umpire's career NRFI rate
#   Phase E.3 additions: pitcher xERA (Statcast quality-of-contact-based
#                        expected ERA), whiff_pct_rank (swinging-strike
#                        rate percentile, the most direct K predictor),
#                        and SIGNED ERA GAP (the user's "worse pitcher
#                        gives up the run" intuition encoded directly).
# 13 features per half.  Threshold 0.58 / 0.42 produces total hit rate
# 62.1% on 2026 holdout, +90.4u 3-split P&L (vs +68.1u baseline = +22u
# improvement).  See test_era_gap.py.
#
# era_gap_signed convention: positive value means the pitcher in THIS
# half is worse than the OTHER half's pitcher.
#   T1 (home pitcher pitches): home_era - away_era.  Higher = home is
#                              worse than away → P(T1 run) higher.
#   B1 (away pitcher pitches): away_era - home_era.
T1_PHASE_E3_FEATURES = [
    "fi_park_nrfi_rate", "home_fip", "away_obp",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "home_p_last5_pitcher_nrfi",
    "away_top3c_obp",
    "home_plate_ump_nrfi_rate",
    "home_xera",
    "home_whiff_pct_rank",
    "era_gap_t1",                  # = home_era - away_era
    "home_p_last10_pitcher_nrfi",  # 10-start recent-form window
    "away_top3c_slg",              # power signal: top-3 batters' SLG
    "away_top3c_iso",              # isolated power: SLG - AVG
    # Phase F: pitcher-vs-team familiarity + opener detection
    "home_pvt_nrfi_rate",          # career NRFI rate vs this team (Bayesian-shrunk)
    "home_avg_ip_per_start",       # last-5 avg IP; <3 IP suggests opener
]
B1_PHASE_E3_FEATURES = [
    "fi_park_nrfi_rate", "away_fip", "home_obp",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "away_p_last5_pitcher_nrfi",
    "home_top3c_obp",
    "home_plate_ump_nrfi_rate",
    "away_xera",
    "away_whiff_pct_rank",
    "era_gap_b1",                  # = away_era - home_era
    "away_p_last10_pitcher_nrfi",
    "home_top3c_slg",
    "home_top3c_iso",
    # Phase F: pitcher-vs-team familiarity + opener detection
    "away_pvt_nrfi_rate",
    "away_avg_ip_per_start",
]

# Defaults for Phase E.3 features when CSV cell is missing
LEAGUE_NRFI_RATE     = 0.50
LEAGUE_AVG_XERA      = 4.20
NEUTRAL_PCT_RANK     = 50


def coerce(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _ump_rate_for(r, ump_cache, ump_rates_data):
    """Look up home-plate umpire's shrunk NRFI rate for a row."""
    pk = (r.get("game_pk") or "").strip()
    league = ump_rates_data.get("league_nrfi_rate", LEAGUE_NRFI_RATE)
    rec = ump_cache.get(pk)
    if not rec: return league
    u = ump_rates_data.get("umpires", {}).get(str(rec["hp_id"]))
    return u["shrunk_nrfi"] if u else league


def gather(csv_path: Path, fi_park_map=None, slim: bool = False,
           slim_k9: bool = False, slim_weather: bool = False,
           phase_e3: bool = False, ump_cache=None, ump_rates_data=None,
           clean_only: bool = False) -> dict:
    """Returns dict of stacked numpy arrays for both halves' features and
    binary labels (1 if that half had a run).

    clean_only=True drops rows where pitcher_q == 'avg' on either side
    (synthetic league-average defaults; ~22% of historical backtests).
    Training on those rows trains the LR on noise."""
    rows = []
    n_dropped_avg = 0
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = r.get("actual_side") or r.get("actual_result") or ""
            if actual.upper() not in ("NRFI", "YRFI"):
                continue
            if clean_only:
                ap_q = (r.get("away_pitcher_q") or "").lower()
                hp_q = (r.get("home_pitcher_q") or "").lower()
                if ap_q == "avg" or hp_q == "avg":
                    n_dropped_avg += 1
                    continue
            t1_runs = r.get("fi_away_runs") or r.get("fi_away_runs", "")
            b1_runs = r.get("fi_home_runs") or r.get("fi_home_runs", "")
            if t1_runs == "" or b1_runs == "":
                continue
            try:
                t1_y = 1 if int(float(t1_runs)) > 0 else 0
                b1_y = 1 if int(float(b1_runs)) > 0 else 0
            except (TypeError, ValueError):
                continue
            home = r.get("home", "") or r.get("home_team", "")
            if fi_park_map is not None:
                fi_park = fi_park_map.get(home, FI_PARK_DEFAULT)
            else:
                fi_park = coerce(r.get("fi_park_nrfi_rate"), FI_PARK_DEFAULT)
            if phase_e3:
                wx = [
                    coerce(r.get("wx_temp_c"),    WX_TEMP_DEFAULT),
                    coerce(r.get("wx_wind_kmh"),  WX_WIND_DEFAULT),
                    coerce(r.get("wx_humidity"),  WX_HUMIDITY_DEFAULT),
                    coerce(r.get("wx_is_dome"),   0.0),
                ]
                # Look up umpire NRFI rate (preferred from CSV cell, fall back to cache)
                ump_rate_csv = (r.get("home_plate_ump_nrfi_rate") or "").strip()
                if ump_rate_csv:
                    ump_rate = float(ump_rate_csv)
                elif ump_cache is not None and ump_rates_data is not None:
                    ump_rate = _ump_rate_for(r, ump_cache, ump_rates_data)
                else:
                    ump_rate = LEAGUE_NRFI_RATE
                # Signed ERA gap: positive value means THIS half's pitcher
                # is worse than the other half's pitcher.  Encodes the "worse
                # pitcher gives up the run" intuition LR can't synthesize on
                # its own.
                h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
                a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
                era_gap_t1 = h_era - a_era
                era_gap_b1 = a_era - h_era
                t1_x = [
                    fi_park,
                    coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                ] + wx + [
                    coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                    coerce(r.get("away_top3c_obp"),            LEAGUE_AVG_OBP),
                    ump_rate,
                    coerce(r.get("home_xera"),                 LEAGUE_AVG_XERA),
                    coerce(r.get("home_whiff_pct_rank"),       NEUTRAL_PCT_RANK),
                    era_gap_t1,
                    coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                    coerce(r.get("away_top3c_slg"),            LEAGUE_AVG_SLG),
                    coerce(r.get("away_top3c_iso"),            LEAGUE_AVG_ISO),
                    # Phase F:
                    coerce(r.get("home_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
                    coerce(r.get("home_avg_ip_per_start"),     5.0),
                ]
                b1_x = [
                    fi_park,
                    coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                ] + wx + [
                    coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                    coerce(r.get("home_top3c_obp"),            LEAGUE_AVG_OBP),
                    ump_rate,
                    coerce(r.get("away_xera"),                 LEAGUE_AVG_XERA),
                    coerce(r.get("away_whiff_pct_rank"),       NEUTRAL_PCT_RANK),
                    era_gap_b1,
                    coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                    coerce(r.get("home_top3c_slg"),            LEAGUE_AVG_SLG),
                    coerce(r.get("home_top3c_iso"),            LEAGUE_AVG_ISO),
                    # Phase F:
                    coerce(r.get("away_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
                    coerce(r.get("away_avg_ip_per_start"),     5.0),
                ]
            elif slim_weather:
                wx = [
                    coerce(r.get("wx_temp_c"),    WX_TEMP_DEFAULT),
                    coerce(r.get("wx_wind_kmh"),  WX_WIND_DEFAULT),
                    coerce(r.get("wx_humidity"),  WX_HUMIDITY_DEFAULT),
                    coerce(r.get("wx_is_dome"),   0.0),
                ]
                t1_x = [
                    fi_park,
                    coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                ] + wx
                b1_x = [
                    fi_park,
                    coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                ] + wx
            elif slim_k9:
                t1_x = [
                    fi_park,
                    coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("home_k9"),  8.9),  # league avg
                    coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                ]
                b1_x = [
                    fi_park,
                    coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("away_k9"),  8.9),
                    coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                ]
            elif slim:
                t1_x = [
                    fi_park,
                    coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                ]
                b1_x = [
                    fi_park,
                    coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                ]
            else:
                t1_x = [
                    fi_park,
                    coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("home_hr9"), LEAGUE_AVG_HR9),
                    coerce(r.get("home_bb9"), LEAGUE_AVG_BB9),
                    coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                    coerce(r.get("away_slg"), LEAGUE_AVG_SLG),
                ]
                b1_x = [
                    fi_park,
                    coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("away_hr9"), LEAGUE_AVG_HR9),
                    coerce(r.get("away_bb9"), LEAGUE_AVG_BB9),
                    coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                    coerce(r.get("home_slg"), LEAGUE_AVG_SLG),
                ]
            rows.append((t1_x, t1_y, b1_x, b1_y, actual.upper()))
    if clean_only and n_dropped_avg:
        print(f"  [{Path(csv_path).name}] dropped {n_dropped_avg} 'avg'-quality rows")
    if not rows:
        return None
    return {
        "X_t1": np.asarray([r[0] for r in rows], dtype=float),
        "y_t1": np.asarray([r[1] for r in rows], dtype=int),
        "X_b1": np.asarray([r[2] for r in rows], dtype=float),
        "y_b1": np.asarray([r[3] for r in rows], dtype=int),
        "y_nrfi": np.asarray([1 if r[4] == "NRFI" else 0 for r in rows], dtype=int),
        "n": len(rows),
    }


def load_fi_park():
    with open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8") as f:
        return json.load(f)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True,
                    help="Training backtest CSVs")
    ap.add_argument("--test", required=False,
                    help="Held-out test CSV")
    ap.add_argument("--save-t1", default="data/lr_t1.json")
    ap.add_argument("--save-b1", default="data/lr_b1.json")
    ap.add_argument("--l2", type=float, default=0.05)
    ap.add_argument("--slim", action="store_true",
                    help="Use 3-feature slim variant (park + FIP + opp OBP) per half")
    ap.add_argument("--slim-k9", action="store_true",
                    help="Slim + pitcher K/9 (4 features per half)")
    ap.add_argument("--slim-weather", action="store_true",
                    help="Slim + weather (7 features per half: temp/wind/humidity/dome)")
    ap.add_argument("--phase-e3", action="store_true",
                    help="Phase E.3 model: slim_weather + last5 + top3c_obp + ump + xera + whiff_pct_rank "
                         "(12 features per half).  Production winner.")
    ap.add_argument("--clean-only", action="store_true",
                    help="Drop rows where pitcher_q == 'avg' on either side "
                         "(synthetic league-avg defaults; ~22%% of historical)")
    args = ap.parse_args()

    park = load_fi_park()
    # Load umpire data once for phase-e3 fallback lookups
    ump_cache = ump_rates_data = None
    if args.phase_e3:
        try:
            ump_cache_path = ROOT / "data" / "umpire_cache.json"
            ump_rates_path = ROOT / "data" / "umpire_rates.json"
            if ump_cache_path.exists():
                ump_cache = json.load(open(ump_cache_path, encoding="utf-8"))
            if ump_rates_path.exists():
                ump_rates_data = json.load(open(ump_rates_path, encoding="utf-8"))
            print(f"  Loaded umpire data: cache={len(ump_cache or {})} games, rates={len((ump_rates_data or {}).get('umpires', {}))} umps")
        except Exception as e:
            print(f"  [warn] umpire data not loaded: {e}")
    if args.phase_e3:
        t1_feats = T1_PHASE_E3_FEATURES
        b1_feats = B1_PHASE_E3_FEATURES
        variant = "PHASE_E3"
    elif args.slim_weather:
        t1_feats = T1_SLIM_WEATHER_FEATURES
        b1_feats = B1_SLIM_WEATHER_FEATURES
        variant = "SLIM+WEATHER"
    elif args.slim_k9:
        t1_feats = T1_SLIM_K9_FEATURES
        b1_feats = B1_SLIM_K9_FEATURES
        variant = "SLIM+K9"
    elif args.slim:
        t1_feats = T1_SLIM_FEATURES
        b1_feats = B1_SLIM_FEATURES
        variant = "SLIM"
    else:
        t1_feats = T1_FEATURES
        b1_feats = B1_FEATURES
        variant = "FULL"

    # ---------- Train ----------
    print("=" * 70)
    print(f"  Training two-stage T1 + B1 models  ({variant} variant)"
          f"{'  [CLEAN]' if args.clean_only else ''}")
    print("=" * 70)
    train_blocks = [gather(Path(p), park, slim=args.slim, slim_k9=args.slim_k9,
                           slim_weather=args.slim_weather,
                           phase_e3=args.phase_e3,
                           ump_cache=ump_cache, ump_rates_data=ump_rates_data,
                           clean_only=args.clean_only)
                    for p in args.train]
    train_blocks = [b for b in train_blocks if b is not None]
    if not train_blocks:
        sys.exit("No training data found.")
    Xt = np.vstack([b["X_t1"] for b in train_blocks])
    yt = np.concatenate([b["y_t1"] for b in train_blocks])
    Xb = np.vstack([b["X_b1"] for b in train_blocks])
    yb = np.concatenate([b["y_b1"] for b in train_blocks])
    print(f"  Train N      : {len(yt)}")
    print(f"  T1 base rate : {yt.mean()*100:.2f}% (away team scored in T1)")
    print(f"  B1 base rate : {yb.mean()*100:.2f}% (home team scored in B1)")

    m_t1 = LogReg.fit(Xt, yt, t1_feats, l2=args.l2)
    m_b1 = LogReg.fit(Xb, yb, b1_feats, l2=args.l2)
    m_t1.save(args.save_t1)
    m_b1.save(args.save_b1)

    print(f"\n  T1 weights:")
    for name, w in zip(t1_feats, m_t1.w):
        print(f"    {name:<22}  {w:+.4f}")
    print(f"\n  B1 weights:")
    for name, w in zip(b1_feats, m_b1.w):
        print(f"    {name:<22}  {w:+.4f}")

    # ---------- Test ----------
    if not args.test:
        print(f"\nSaved -> {args.save_t1} and {args.save_b1}")
        return

    print("\n" + "=" * 70)
    print(f"  Testing on {Path(args.test).name}")
    print("=" * 70)
    # Test set is always evaluated WITHOUT clean_only -- we want full coverage
    # of the holdout period to mirror what happens in production.
    te = gather(Path(args.test), park, slim=args.slim, slim_k9=args.slim_k9,
                slim_weather=args.slim_weather, phase_e3=args.phase_e3,
                ump_cache=ump_cache, ump_rates_data=ump_rates_data,
                clean_only=False)
    if not te:
        sys.exit("No test rows.")

    p_t1 = m_t1.predict_proba(te["X_t1"])
    p_b1 = m_b1.predict_proba(te["X_b1"])
    p_nrfi = (1 - p_t1) * (1 - p_b1)
    y_nrfi = te["y_nrfi"]

    print(f"\n  Test N           : {te['n']}")
    print(f"  Actual NRFI rate : {y_nrfi.mean()*100:.2f}%")
    print(f"  Mean P(T1 run)   : {p_t1.mean()*100:.2f}%   actual {te['y_t1'].mean()*100:.2f}%")
    print(f"  Mean P(B1 run)   : {p_b1.mean()*100:.2f}%   actual {te['y_b1'].mean()*100:.2f}%")
    print(f"  Mean P(NRFI)     : {p_nrfi.mean()*100:.2f}%   actual {y_nrfi.mean()*100:.2f}%")
    print(f"\n  Two-stage Brier  : {brier(p_nrfi, y_nrfi):.4f}")

    q5r, q5w, q5n = q5_hit(p_nrfi, y_nrfi)
    q1r, q1w, q1n = q1_yrfi(p_nrfi, y_nrfi)
    print(f"  Two-stage Q5 NRFI: {q5w}-{q5n - q5w} ({q5r*100:.1f}%)")
    print(f"  Two-stage Q1 YRFI: {q1w}-{q1n - q1w} ({q1r*100:.1f}%)")

    # ---------- Side-by-side: V2 baseline ----------
    print("\n  Loading current V2 production model + calibrator for comparison...")
    with open(ROOT / "data" / "lr_model.json", encoding="utf-8") as f:
        v2 = json.load(f)
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")

    # Build V2 features for the same test rows -- need the full 11
    rows = []
    with open(args.test, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = r.get("actual_side") or r.get("actual_result") or ""
            if actual.upper() not in ("NRFI", "YRFI"):
                continue
            home = r.get("home", "") or r.get("home_team", "")
            fi_park = park.get(home, FI_PARK_DEFAULT)
            x = [
                fi_park,
                coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                coerce(r.get("home_hr9"), LEAGUE_AVG_HR9),
                coerce(r.get("home_bb9"), LEAGUE_AVG_BB9),
                coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                coerce(r.get("away_slg"), LEAGUE_AVG_SLG),
                coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
                coerce(r.get("away_hr9"), LEAGUE_AVG_HR9),
                coerce(r.get("away_bb9"), LEAGUE_AVG_BB9),
                coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
                coerce(r.get("home_slg"), LEAGUE_AVG_SLG),
            ]
            rows.append(x)
    Xv = np.asarray(rows, dtype=float)
    Xn = (Xv - np.asarray(v2["mean"])) / np.asarray(v2["std"])
    z = Xn @ np.asarray(v2["weights"]) + v2["bias"]
    p_v2_raw = 1.0 / (1.0 + np.exp(-z))
    p_v2_cal = np.array([cal.predict(float(p)) for p in p_v2_raw])

    print(f"\n  V2 (current production):")
    print(f"    Mean P(NRFI)  : {p_v2_cal.mean()*100:.2f}%   actual {y_nrfi.mean()*100:.2f}%")
    print(f"    V2 Brier      : {brier(p_v2_cal, y_nrfi):.4f}")
    q5r, q5w, q5n = q5_hit(p_v2_cal, y_nrfi)
    q1r, q1w, q1n = q1_yrfi(p_v2_cal, y_nrfi)
    print(f"    V2 Q5 NRFI    : {q5w}-{q5n - q5w} ({q5r*100:.1f}%)")
    print(f"    V2 Q1 YRFI    : {q1w}-{q1n - q1w} ({q1r*100:.1f}%)")

    # ---------- Sensitivity: user's elite-home + trash-away scenario ----------
    print("\n" + "-" * 70)
    print("  User's scenario test: elite home pitcher, trash away pitcher")
    print("-" * 70)
    for label, away_fip, away_hr9, away_bb9 in [
        ("ELITE",   2.50, 0.50, 1.50),
        ("AVERAGE", 4.20, 1.20, 3.20),
        ("TRASH",   6.00, 2.50, 5.00),
    ]:
        if args.phase_e3:
            wx_neutral = [WX_TEMP_DEFAULT, WX_WIND_DEFAULT, WX_HUMIDITY_DEFAULT, 0.0]
            # T1: home pitcher elite (era 2.50), gap = 2.50 - matching scenario away ERA
            era_t1 = 2.50 - {"ELITE": 2.50, "AVERAGE": 4.20, "TRASH": 6.00}[label]
            era_b1 = -era_t1
            extras_t1 = [LEAGUE_NRFI_RATE, LEAGUE_AVG_OBP, LEAGUE_NRFI_RATE,
                         LEAGUE_AVG_XERA, NEUTRAL_PCT_RANK, era_t1]
            extras_b1 = [LEAGUE_NRFI_RATE, LEAGUE_AVG_OBP, LEAGUE_NRFI_RATE,
                         LEAGUE_AVG_XERA, NEUTRAL_PCT_RANK, era_b1]
            x_t1 = np.asarray([[0.50, 2.50, 0.318] + wx_neutral + extras_t1])
            x_b1 = np.asarray([[0.50, away_fip, 0.318] + wx_neutral + extras_b1])
        elif args.slim_weather:
            wx_neutral = [WX_TEMP_DEFAULT, WX_WIND_DEFAULT, WX_HUMIDITY_DEFAULT, 0.0]
            x_t1 = np.asarray([[0.50, 2.50, 0.318] + wx_neutral])
            x_b1 = np.asarray([[0.50, away_fip, 0.318] + wx_neutral])
        elif args.slim_k9:
            x_t1 = np.asarray([[0.50, 2.50, 8.9, 0.318]])
            x_b1 = np.asarray([[0.50, away_fip, 8.9, 0.318]])
        elif args.slim:
            x_t1 = np.asarray([[0.50, 2.50, 0.318]])
            x_b1 = np.asarray([[0.50, away_fip, 0.318]])
        else:
            x_t1 = np.asarray([[0.50, 2.50, 0.50, 1.50, 0.318, 0.414]])
            x_b1 = np.asarray([[0.50, away_fip, away_hr9, away_bb9, 0.318, 0.414]])
        p_t1_one = float(m_t1.predict_proba(x_t1)[0])
        p_b1_one = float(m_b1.predict_proba(x_b1)[0])
        p_nrfi_one = (1 - p_t1_one) * (1 - p_b1_one)
        print(f"  away pitcher = {label:<7}  "
              f"P(T1 run)={p_t1_one*100:5.1f}%  "
              f"P(B1 run)={p_b1_one*100:5.1f}%  "
              f"P(NRFI)={p_nrfi_one*100:5.1f}%")

    print()


if __name__ == "__main__":
    main()

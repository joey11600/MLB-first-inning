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

# Phase 2.2: single-source-of-truth FPS league prior.  Unlike
# LEAGUE_FI_AVG_ERA_FALLBACK which duplicates locally at 4.40 (intentional
# divergence from predict-time's year-indexed 4.38-4.41 priors), the FPS
# constant is identical at train and predict time -- so we import it.
# Updates to backtest.LEAGUE_FPS_1ST_AVG automatically propagate to
# training without needing a parallel constant in this file.
from backtest import LEAGUE_FPS_1ST_AVG

LEAGUE_AVG_ERA = 4.20

# Phase 2.1: FI-specific fallback for blank home/away_first_inning_era
# cells.  Distinct from LEAGUE_AVG_ERA (4.20, full-season) because the
# first-inning environment runs ~0.2 ERA hotter than overall MLB ERA.
# Set to 4.40 as the simple average across the year-indexed prior table
# LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON in backtest.py:
#   (2024: 4.3836 + 2025: 4.4100 + 2026: 4.4100) / 3 = 4.4012 ~= 4.40
#
# Why a single constant rather than passing target_season through to
# gather() coerce defaults: avoids touching gather()'s signature for an
# 89-cell (<1% of corpus) edge case.  Train/predict divergence shrinks
# from ~0.20 ERA (using LEAGUE_AVG_ERA=4.20 as fallback) to ~0.01 ERA
# (this constant vs the year-indexed predict-time prior 4.38-4.41).
# Audit row 2026-05-13-bug-fie-fallback-inconsistency remains filed
# as the V1.1 work item to thread season context through properly.
LEAGUE_FI_AVG_ERA_FALLBACK = 4.40
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

# Phase G (2026-05-12): adds top-3 batters' LAST-10-GAMES OBP/SLG/ISO
# on top of the Phase E.3 + F feature set.  Idea: season-to-date
# averages smear over hot/cold streaks; the last-10 window catches
# them.  See docs/PHASE_G_recent_form.md.
T1_PHASE_G_FEATURES = T1_PHASE_E3_FEATURES + [
    "away_top3c_last10_obp",
    "away_top3c_last10_slg",
    "away_top3c_last10_iso",
]
B1_PHASE_G_FEATURES = B1_PHASE_E3_FEATURES + [
    "home_top3c_last10_obp",
    "home_top3c_last10_slg",
    "home_top3c_last10_iso",
]

# Phase 2.1 FIE (2026-05-13): adds Bayesian-blended first-inning ERA for the
# pitcher pitching THIS half-inning, on top of whatever base variant is
# selected.  Designed to compose with --phase-e3 (and optionally --phase-g).
# Source: backtest.fetch_pitcher_first_inning_era; year-indexed prior table
# LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON.  See improvement_log.csv row
# 2026-05-12-phase2.1-fie for the empirical derivation.
T1_FIE_FEATURES = ["home_first_inning_era"]
B1_FIE_FEATURES = ["away_first_inning_era"]

# Phase 2.2 FPS (2026-05-14): adds Bayesian-blended first-pitch strike % in
# the 1st inning for the pitcher pitching THIS half-inning.  Composes with
# --phase-e3 (and optionally --phase-g, --fie).  Source:
# backtest.fetch_pitcher_first_pitch_strike_pct; league prior
# LEAGUE_FPS_1ST_AVG = 0.62 (empirically verified 0.6194 across 92,547
# events 2021-2026 during the 2026-05-13 backfill; rounded to 0.62).
# See improvement_log.csv row 2026-05-12-phase2.2-fps for the empirical
# derivation, Q5 redundancy diagnostic, and named-pitcher smell-test
# verification.
T1_FPS_FEATURES = ["home_first_pitch_strike_pct"]
B1_FPS_FEATURES = ["away_first_pitch_strike_pct"]

# Phase 2.3 LEADOFF (2026-05-14): SPLIT (not add) -- replaces
# away_top3c_obp / home_top3c_obp with two separate features per side:
# the leadoff hitter's OBP and the AB-weighted combined OBP of the
# 2nd and 3rd hitters.  Q5 pre-diagnostic showed mean |delta| = 6.08pp
# (5.66pp with leadoff AB >= 40 filter) and within-lineup corr = -0.16
# (strengthening to -0.27 under the AB filter) -- the aggregate top3c_obp
# averages two anti-correlated quantities, so the split exposes signal
# the aggregate destroys.  Composes with --phase-e3 (auto-enabled) and
# --phase-g (which keeps its top3c_last10_* features unchanged).
# Source for both new features: away_lineup_json / home_lineup_json
# (production schema; backfilled across 2024+2025+2026 by tools/
# backfill_lineup_json.py 2026-05-14).  Fallback for null cells: same
# LEAGUE_AVG_OBP = 0.318 the aggregate uses, so missing-data regime
# does NOT change between baseline and candidate -- Gate A cleanly
# measures "does the split help" rather than "split + fallback delta."

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


def _extract_leadoff_features(lineup_json: str) -> tuple[float, float]:
    """Parse a *_lineup_json CSV cell and return
    (leadoff_obp, two_three_combined_obp).

    Edge cases (each returns LEAGUE_AVG_OBP for the affected feature):

      a) lineup_json is empty/'[]'/missing/unparseable JSON
          -> json.loads either raises or returns []; both -> two-fallback.
      b) lineup has fewer than 3 entries
          -> use what's there for slots 0/1/2 that exist; fall back to
             LEAGUE_AVG_OBP for missing slots.
      c) lineup[0]["obp"] is None (early-season callup, < 10 AB)
          -> leadoff_obp = LEAGUE_AVG_OBP.
      d) Exactly one of lineup[1]/lineup[2] has obp non-null
          -> two_three_combined_obp = that batter's obp (degenerate
             single-element AB-weighted average).
      e) BOTH lineup[1] and lineup[2] have obp null
          -> two_three_combined_obp = LEAGUE_AVG_OBP.
      f) ab is None where obp isn't (defensive; shouldn't happen given
         producer guarantees ab/obp are set together)
          -> skip that batter entirely from the pair average.

    The producer (backtest.current_season_top3_per_batter) emits OBP
    using strict <-date cutoffs verified leakage-free 2026-05-14.
    """
    leadoff_obp = LEAGUE_AVG_OBP
    two_three_obp = LEAGUE_AVG_OBP

    # Case (a): empty / missing / unparseable
    try:
        lineup = json.loads(lineup_json) if lineup_json else []
    except (json.JSONDecodeError, TypeError):
        return (leadoff_obp, two_three_obp)
    if not isinstance(lineup, list) or not lineup:
        return (leadoff_obp, two_three_obp)

    # Case (c): leadoff obp null -> fallback.  Otherwise extract.
    if len(lineup) >= 1:
        v = lineup[0].get("obp")
        if v is not None:
            try:
                leadoff_obp = float(v)
            except (TypeError, ValueError):
                pass  # malformed -> leave at LEAGUE_AVG_OBP

    # Cases (b), (d), (e), (f): collect non-null (obp, ab) for slots 1+2
    pair_obps: list[tuple[float, float]] = []
    for i in (1, 2):
        if i >= len(lineup):
            continue  # case (b): slot missing
        b = lineup[i]
        obp_v = b.get("obp")
        ab_v  = b.get("ab")
        if obp_v is None or ab_v is None:
            continue  # cases (e), (f): skip this batter
        try:
            pair_obps.append((float(obp_v), float(ab_v)))
        except (TypeError, ValueError):
            pass

    if pair_obps:
        total_ab = sum(ab for _, ab in pair_obps)
        if total_ab > 0:
            # Case (d) reduces to single-element weighted average == that value
            two_three_obp = sum(obp * ab for obp, ab in pair_obps) / total_ab
        # else: total_ab == 0 (both batters have 0 AB); leave at LEAGUE_AVG_OBP
    # else: case (e) -- pair_obps empty; leave at LEAGUE_AVG_OBP

    return (leadoff_obp, two_three_obp)


def _swap_top3c_obp_for_leadoff(features: list[str], side: str) -> list[str]:
    """Return a copy of `features` with `<side>_top3c_obp` replaced by
    [`<side>_leadoff_obp`, `<side>_2_3_combined_obp`].  No-op (returns a
    copy unchanged) if the slot isn't present, so the helper is safe to
    call on any base feature list."""
    old = f"{side}_top3c_obp"
    if old not in features:
        return list(features)
    idx = features.index(old)
    return features[:idx] + [f"{side}_leadoff_obp",
                              f"{side}_2_3_combined_obp"] + features[idx+1:]


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
           phase_e3: bool = False, phase_g: bool = False,
           fie: bool = False,
           fps: bool = False,
           leadoff: bool = False,
           ump_cache=None, ump_rates_data=None,
           clean_only: bool = False,
           emit_meta: bool = False) -> dict:
    """Returns dict of stacked numpy arrays for both halves' features and
    binary labels (1 if that half had a run).

    clean_only=True drops rows where pitcher_q == 'avg' on either side
    (synthetic league-average defaults; ~22% of historical backtests).
    Training on those rows trains the LR on noise.

    emit_meta=True additionally captures per-surviving-row metadata
    (date, game_pk, home_top3c_iso, away_top3c_iso) into the returned
    dict under key 'meta'.  Used by --emit-rowwise to write a rowwise
    CSV that downstream Gate C analysis filters on iso >= 0.25.  Adds
    one dict per row to memory; no effect on training math."""
    rows = []
    meta = []  # only populated when emit_meta=True
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

                # Phase 2.3 LEADOFF: parse lineup_json once per row, extract
                # leadoff_obp + 2_3_combined_obp per side.  See
                # _extract_leadoff_features for full edge-case handling
                # (lineup_json missing/'[]'/unparseable; <3 entries;
                # individual null obps; both pair-batters null).
                if leadoff:
                    a_leadoff, a_pair = _extract_leadoff_features(
                        r.get("away_lineup_json") or "")
                    h_leadoff, h_pair = _extract_leadoff_features(
                        r.get("home_lineup_json") or "")

                t1_x = [
                    fi_park,
                    coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                    coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
                ] + wx + [
                    coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                ] + (
                    [a_leadoff, a_pair] if leadoff else
                    [coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP)]
                ) + [
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
                ] + (
                    [h_leadoff, h_pair] if leadoff else
                    [coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP)]
                ) + [
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
                # Phase G: append top-3 last-10 OBP/SLG/ISO to both halves.
                # Skipped rows (no Phase G data backfilled) get league averages
                # so the LR doesn't crash; expectation is the backfill ran
                # before this fit kicks off.
                if phase_g:
                    t1_x += [
                        coerce(r.get("away_top3c_last10_obp"), LEAGUE_AVG_OBP),
                        coerce(r.get("away_top3c_last10_slg"), LEAGUE_AVG_SLG),
                        coerce(r.get("away_top3c_last10_iso"), LEAGUE_AVG_ISO),
                    ]
                    b1_x += [
                        coerce(r.get("home_top3c_last10_obp"), LEAGUE_AVG_OBP),
                        coerce(r.get("home_top3c_last10_slg"), LEAGUE_AVG_SLG),
                        coerce(r.get("home_top3c_last10_iso"), LEAGUE_AVG_ISO),
                    ]
                # Phase 2.1 FIE: append home/away first-inning ERA on top of
                # whatever base e3 (+optional g) vector was assembled.  Blank
                # cells (47 from 2024 postponements, 35 from 2025, 7 from
                # 2026 missing-PID slots; ~89 / 10726 = 0.83% of corpus) fall
                # back to LEAGUE_FI_AVG_ERA_FALLBACK = 4.40 via coerce -- the
                # first-inning-environment-averaged league mean, NOT the
                # full-season LEAGUE_AVG_ERA (4.20) which would systematically
                # under-shrink the blank rows.  See audit row
                # 2026-05-13-bug-fie-fallback-inconsistency for the V1.1
                # target of true year-indexed fallbacks.
                if fie:
                    t1_x.append(coerce(r.get("home_first_inning_era"), LEAGUE_FI_AVG_ERA_FALLBACK))
                    b1_x.append(coerce(r.get("away_first_inning_era"), LEAGUE_FI_AVG_ERA_FALLBACK))
                # Phase 2.2 FPS: append home/away first-pitch strike % on
                # top of whatever base + optional fie vector was assembled.
                # Blank cells (89 from postponement-cascade unresolved PIDs
                # across 2024+2025+2026, same tie-out as FIE) fall back to
                # LEAGUE_FPS_1ST_AVG = 0.62 (the league prior, imported from
                # backtest.py so train and predict use identical fallback).
                # No train/predict divergence audit needed -- single SOT.
                if fps:
                    t1_x.append(coerce(r.get("home_first_pitch_strike_pct"), LEAGUE_FPS_1ST_AVG))
                    b1_x.append(coerce(r.get("away_first_pitch_strike_pct"), LEAGUE_FPS_1ST_AVG))
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
            if emit_meta:
                # Capture metadata AFTER all gather() filters so the meta
                # list aligns 1:1 with rows[] (and therefore with the
                # X_t1/X_b1/y_nrfi arrays).  ISO falls back to LEAGUE_AVG_ISO
                # on blank cells so the downstream elite-power filter at
                # >= 0.25 never spuriously fires on missing data.
                meta.append({
                    "date":           r.get("date") or "",
                    "game_pk":        r.get("game_pk") or "",
                    "home_top3c_iso": coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO),
                    "away_top3c_iso": coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO),
                })
    if clean_only and n_dropped_avg:
        print(f"  [{Path(csv_path).name}] dropped {n_dropped_avg} 'avg'-quality rows")
    if not rows:
        return None
    result = {
        "X_t1": np.asarray([r[0] for r in rows], dtype=float),
        "y_t1": np.asarray([r[1] for r in rows], dtype=int),
        "X_b1": np.asarray([r[2] for r in rows], dtype=float),
        "y_b1": np.asarray([r[3] for r in rows], dtype=int),
        "y_nrfi": np.asarray([1 if r[4] == "NRFI" else 0 for r in rows], dtype=int),
        "n": len(rows),
    }
    if emit_meta:
        result["meta"] = meta
    return result


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
    ap.add_argument("--phase-g", action="store_true",
                    help="Phase G: phase-e3 + top-3 batters' last-10-games OBP/SLG/ISO "
                         "(21 features per half).  Requires --phase-e3.  Test before deploy.")
    ap.add_argument("--fie", action="store_true",
                    help="Phase 2.1 -- append home/away_first_inning_era to "
                         "both half-inning vectors (Bayesian-blended; "
                         "see backtest.LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON).  "
                         "Requires --phase-e3 (auto-enabled if not given). "
                         "Composes with --phase-g.")
    ap.add_argument("--fps", action="store_true",
                    help="Phase 2.2 -- append home/away_first_pitch_strike_pct "
                         "to both half-inning vectors (Bayesian-blended; "
                         "see backtest.LEAGUE_FPS_1ST_AVG = 0.62).  "
                         "Requires --phase-e3 (auto-enabled if not given). "
                         "Composes with --phase-g and --fie.")
    ap.add_argument("--leadoff", action="store_true",
                    help="Phase 2.3 -- SPLIT (not add): replace home/away_top3c_obp "
                         "with home/away_leadoff_obp + home/away_2_3_combined_obp "
                         "(parsed from *_lineup_json columns).  T1/B1 each go "
                         "from 18 features -> 19.  Requires --phase-e3 (auto-enabled "
                         "if not given).  Composes with --phase-g, --fie, --fps.")
    ap.add_argument("--clean-only", action="store_true",
                    help="Drop rows where pitcher_q == 'avg' on either side "
                         "(synthetic league-avg defaults; ~22%% of historical)")
    ap.add_argument("--emit-rowwise", default=None, metavar="PATH",
                    help="Path to write per-row test predictions for Gate C "
                         "analysis.  CSV columns: date, game_pk, p_nrfi_pred, "
                         "y_actual, brier_row, home_top3c_iso, away_top3c_iso. "
                         "p_nrfi_pred is the RAW two-stage probability "
                         "(uncalibrated) -- matches the printed 'Two-stage "
                         "Brier' so Gate A and Gate C share an apples-to-apples "
                         "basis.  No-op when --test is absent.  Consumed by "
                         "tools/candidate_validation.py --gate-c-elite-power.")
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
    # Phase 2.1: --fie depends on phase-e3 (parallel to --phase-g's dependency).
    # Promote args.phase_e3 BEFORE the if/elif chain so the base block runs.
    if args.fie and not args.phase_e3:
        print("[note] --fie requires --phase-e3; enabling phase-e3 automatically")
        args.phase_e3 = True

    # Phase 2.2: same auto-enable for --fps.
    if args.fps and not args.phase_e3:
        print("[note] --fps requires --phase-e3; enabling phase-e3 automatically")
        args.phase_e3 = True

    # Phase 2.3: same auto-enable for --leadoff.
    if args.leadoff and not args.phase_e3:
        print("[note] --leadoff requires --phase-e3; enabling phase-e3 automatically")
        args.phase_e3 = True

    if args.phase_g:
        if not args.phase_e3:
            print("[note] --phase-g requires --phase-e3; enabling phase-e3 automatically")
            args.phase_e3 = True
        t1_feats = T1_PHASE_G_FEATURES
        b1_feats = B1_PHASE_G_FEATURES
        variant = "PHASE_G"
    elif args.phase_e3:
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

    # Phase 2.3 LEADOFF: applies AFTER base variant selection so it works
    # with both PHASE_E3 and PHASE_G.  Order matters relative to FIE/FPS:
    # LEADOFF swaps the top3c_obp slot in the BASE feature list, then FIE
    # and FPS append their features to the tail of whatever's been
    # assembled.  So with --leadoff --fie --fps the order is:
    #   <PHASE_E3 with top3c_obp swapped> + FIE + FPS  (= 21 features)
    if args.leadoff:
        t1_feats = _swap_top3c_obp_for_leadoff(t1_feats, "away")
        b1_feats = _swap_top3c_obp_for_leadoff(b1_feats, "home")
        variant = variant + "+LEADOFF"

    # Phase 2.1: layer FIE on top of whichever base was chosen, AFTER the
    # base block ran.  Composes with --phase-g (e3+g+fie = 23 features per half).
    if args.fie:
        t1_feats = t1_feats + T1_FIE_FEATURES
        b1_feats = b1_feats + B1_FIE_FEATURES
        variant = variant + "+FIE"

    # Phase 2.2: layer FPS on top of whatever's been assembled (e3 + optional
    # g + optional fie).  Order is intentional: FPS comes LAST so the variant
    # label reads "...+FPS" at the end and the feature-name list ends with
    # the FPS columns.  Saved JSON's feature_names array reflects the
    # composed order.
    if args.fps:
        t1_feats = t1_feats + T1_FPS_FEATURES
        b1_feats = b1_feats + B1_FPS_FEATURES
        variant = variant + "+FPS"

    # Explicit startup print so the operator sees the variant + feature count
    # before any training math runs.  Designed to make the "silent V2.2
    # retraining" failure mode (forgot --fie, got 18 features instead of 19)
    # visible at line 1 of stdout rather than 100 lines later in the Brier
    # comparison.
    print(f"  Active variant: {variant}  (T1={len(t1_feats)} features, B1={len(b1_feats)} features)")

    # ---------- T4.7: Holdout integrity check ----------
    # Refuse to train if the test file is also in the train list.
    # This is the canonical leakage failure mode -- catching it here
    # prevents accidentally publishing a model that "looks great" on
    # what is really its own training set.
    if args.test:
        train_paths = [str(Path(p).resolve()) for p in args.train]
        test_path   = str(Path(args.test).resolve())
        if test_path in train_paths:
            sys.exit(
                f"FATAL: --test file is also in --train list (path: {test_path}).  "
                f"This would silently leak the test set into training, biasing the "
                f"reported holdout metrics upward.  Use a separate season's data "
                f"as the holdout (e.g. train on 2024 + 2025, test on 2026)."
            )

    # ---------- Train ----------
    print("=" * 70)
    print(f"  Training two-stage T1 + B1 models  ({variant} variant)"
          f"{'  [CLEAN]' if args.clean_only else ''}")
    print("=" * 70)
    train_blocks = [gather(Path(p), park, slim=args.slim, slim_k9=args.slim_k9,
                           slim_weather=args.slim_weather,
                           phase_e3=args.phase_e3, phase_g=args.phase_g,
                           fie=args.fie,
                           fps=args.fps,
                           leadoff=args.leadoff,
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
                slim_weather=args.slim_weather, phase_e3=args.phase_e3, phase_g=args.phase_g,
                fie=args.fie,
                fps=args.fps,
                leadoff=args.leadoff,
                ump_cache=ump_cache, ump_rates_data=ump_rates_data,
                clean_only=False,
                emit_meta=bool(args.emit_rowwise))
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

    # Phase 2.3: rowwise emit for downstream Gate C elite-power filter.
    # Lands AFTER the all-rows Brier print so a downstream parser that
    # was relying on stdout sees the same line ordering it always saw.
    if args.emit_rowwise:
        out_path = Path(args.emit_rowwise)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "game_pk", "p_nrfi_pred", "y_actual",
                        "brier_row", "home_top3c_iso", "away_top3c_iso"])
            for i, m in enumerate(te["meta"]):
                p_i = float(p_nrfi[i])
                y_i = int(y_nrfi[i])
                w.writerow([m["date"], m["game_pk"],
                            f"{p_i:.6f}", y_i,
                            f"{(p_i - y_i) ** 2:.6f}",
                            f"{m['home_top3c_iso']:.4f}",
                            f"{m['away_top3c_iso']:.4f}"])
        print(f"  Rowwise CSV saved -> {out_path}  (N={len(te['meta'])})")

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


if __name__ == "__main__":
    main()

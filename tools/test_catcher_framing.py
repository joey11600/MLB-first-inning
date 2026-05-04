#!/usr/bin/env python3
"""
tools/test_catcher_framing.py — walk-forward test of catcher framing as
an LR feature (T4.1).

Compares phase_e3 (current production architecture) vs phase_e4
(phase_e3 + home_catcher_framing + away_catcher_framing) on the truepit
walk-forward fold:

  TRAIN: backtest_2024-..._truepit.csv  (16 vs 17 features per half)
  TEST:  backtest_2025-..._truepit.csv

Reports per-variant:
  - Brier on 2025 holdout
  - STRONG bet hit rate + P/L
  - Lift in NRFI vs YRFI side hit rates
  - Top-bucket NRFI accuracy

DECISION RULE (matches the project's "Test methodology" in docs/KB.md):
  Ship phase_e4 if total 3-split P&L beats baseline by >=10u AND no
  STRONG hit-rate regression on holdout.

USAGE
-----
  python tools/test_catcher_framing.py
  python tools/test_catcher_framing.py --season 2025  # restrict test season
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg
from calibration import ProbCalibrator
from two_stage_model import (
    gather, load_fi_park, brier, q5_hit, q1_yrfi, coerce,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
    FI_PARK_DEFAULT, LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG,
    LEAGUE_AVG_ISO, WX_TEMP_DEFAULT, WX_WIND_DEFAULT, WX_HUMIDITY_DEFAULT,
    LEAGUE_NRFI_RATE, LEAGUE_AVG_XERA, NEUTRAL_PCT_RANK,
)


STRONG_NRFI_THR = 0.58
STRONG_YRFI_THR = 0.42
WIN_PAYOUT      = 100.0 / 120.0   # -120 vig


# Phase E.4 = phase_e3 + 1 catcher framing feature per half.
T1_PHASE_E4_FEATURES = T1_PHASE_E3_FEATURES + ["home_catcher_framing"]
B1_PHASE_E4_FEATURES = B1_PHASE_E3_FEATURES + ["away_catcher_framing"]


def gather_phase_e4(csv_path: Path, fi_park_map: dict) -> dict | None:
    """Like two_stage_model.gather(phase_e3=True) but also extracts the
    catcher_framing column.  Replicates the gather() logic verbatim
    (no clean import path to extend it from outside)."""
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = r.get("actual_side") or r.get("actual_result") or ""
            if actual.upper() not in ("NRFI", "YRFI"):
                continue
            t1_runs = r.get("fi_away_runs") or ""
            b1_runs = r.get("fi_home_runs") or ""
            if t1_runs == "" or b1_runs == "":
                continue
            try:
                t1_y = 1 if int(float(t1_runs)) > 0 else 0
                b1_y = 1 if int(float(b1_runs)) > 0 else 0
            except (TypeError, ValueError):
                continue

            home = r.get("home", "") or r.get("home_team", "")
            fi_park = fi_park_map.get(home, FI_PARK_DEFAULT)

            wx = [
                coerce(r.get("wx_temp_c"),    WX_TEMP_DEFAULT),
                coerce(r.get("wx_wind_kmh"),  WX_WIND_DEFAULT),
                coerce(r.get("wx_humidity"),  WX_HUMIDITY_DEFAULT),
                coerce(r.get("wx_is_dome"),   0.0),
            ]
            ump_rate_csv = (r.get("home_plate_ump_nrfi_rate") or "").strip()
            ump_rate = float(ump_rate_csv) if ump_rate_csv else LEAGUE_NRFI_RATE

            h_era = coerce(r.get("home_era"), LEAGUE_AVG_ERA)
            a_era = coerce(r.get("away_era"), LEAGUE_AVG_ERA)
            era_gap_t1 = h_era - a_era
            era_gap_b1 = a_era - h_era

            # Catcher framing (NEW for phase_e4).  0.0 = neutral fallback.
            home_catcher_framing = coerce(r.get("home_catcher_framing"), 0.0)
            away_catcher_framing = coerce(r.get("away_catcher_framing"), 0.0)

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
                coerce(r.get("home_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
                coerce(r.get("home_avg_ip_per_start"),     5.0),
                home_catcher_framing,    # NEW
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
                coerce(r.get("away_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
                coerce(r.get("away_avg_ip_per_start"),     5.0),
                away_catcher_framing,    # NEW
            ]
            rows.append((t1_x, t1_y, b1_x, b1_y, actual.upper()))

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


def grade(p_nrfi: np.ndarray, y_nrfi: np.ndarray) -> dict:
    n_bets = n_wins = 0
    n_yrfi = n_yrfi_wins = 0
    n_nrfi = n_nrfi_wins = 0
    pl = 0.0
    for p, y in zip(p_nrfi, y_nrfi):
        if p >= STRONG_NRFI_THR:
            n_bets += 1; n_nrfi += 1
            if y == 1: n_wins += 1; n_nrfi_wins += 1; pl += WIN_PAYOUT
            else: pl -= 1.0
        elif p <= STRONG_YRFI_THR:
            n_bets += 1; n_yrfi += 1
            if y == 0: n_wins += 1; n_yrfi_wins += 1; pl += WIN_PAYOUT
            else: pl -= 1.0
    return {
        "n_bets":  n_bets, "n_wins": n_wins, "n_losses": n_bets - n_wins,
        "n_yrfi":  n_yrfi, "n_yrfi_wins": n_yrfi_wins,
        "n_nrfi":  n_nrfi, "n_nrfi_wins": n_nrfi_wins,
        "pl":      pl,
        "hit":     (n_wins / n_bets) if n_bets else 0.0,
        "roi":     (pl / n_bets) if n_bets else 0.0,
    }


def fit_predict_with_calibrator(
    train_block: dict, test_block: dict,
    t1_feats: list[str], b1_feats: list[str],
):
    """Train T1+B1 LR on train_block, fit calibrator on train predictions,
    apply calibrator to test predictions, return (p_nrfi_test_cal, brier_raw)."""
    m_t1 = LogReg.fit(train_block["X_t1"], train_block["y_t1"], t1_feats, l2=0.05)
    m_b1 = LogReg.fit(train_block["X_b1"], train_block["y_b1"], b1_feats, l2=0.05)
    # In-sample predictions on training set -> fit calibrator
    p_t1_train = m_t1.predict_proba(train_block["X_t1"])
    p_b1_train = m_b1.predict_proba(train_block["X_b1"])
    p_nrfi_train_raw = (1.0 - p_t1_train) * (1.0 - p_b1_train)
    cal = ProbCalibrator.fit(
        predictions=p_nrfi_train_raw.tolist(),
        actuals=train_block["y_nrfi"].tolist(),
        n_bins=20,
    )
    # Predict + calibrate on test
    p_t1_test = m_t1.predict_proba(test_block["X_t1"])
    p_b1_test = m_b1.predict_proba(test_block["X_b1"])
    p_nrfi_test_raw = (1.0 - p_t1_test) * (1.0 - p_b1_test)
    p_nrfi_test_cal = np.array([cal.predict(float(p)) for p in p_nrfi_test_raw])
    brier_raw = brier(p_nrfi_test_raw, test_block["y_nrfi"])
    brier_cal = brier(p_nrfi_test_cal, test_block["y_nrfi"])
    return p_nrfi_test_cal, brier_raw, brier_cal, cal, (m_t1, m_b1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    args = ap.parse_args()

    fi_park = load_fi_park()
    BTS = REPO_ROOT / "data" / "backtests"
    train_csv = BTS / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
    test_csv  = BTS / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
    if not train_csv.exists() or not test_csv.exists():
        sys.exit(f"Missing truepit CSVs.  Run tools/backfill_xera_pit_perpitch.py "
                 f"and tools/backfill_catcher_framing_to_csvs.py first.")

    print()
    print("=" * 100)
    print("  Catcher framing walk-forward test (T4.1)")
    print("  TRAIN: 2024 truepit  ->  TEST: 2025 truepit")
    print("=" * 100)

    # Phase E.3 baseline (16 features per half)
    train_e3 = gather(train_csv, fi_park, phase_e3=True)
    test_e3  = gather(test_csv,  fi_park, phase_e3=True)
    print(f"  Phase E.3 baseline: train n={train_e3['n']}, test n={test_e3['n']}")
    p_e3, brier_raw_e3, brier_cal_e3, cal_e3, _ = fit_predict_with_calibrator(
        train_e3, test_e3, T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES)
    print(f"    cal range: [{min(cal_e3.rates):.4f}, {max(cal_e3.rates):.4f}]")
    print(f"    Brier (raw): {brier_raw_e3:.4f}, Brier (cal): {brier_cal_e3:.4f}")
    bets_e3 = grade(p_e3, test_e3["y_nrfi"])

    # Phase E.4 with catcher framing (17 features per half)
    train_e4 = gather_phase_e4(train_csv, fi_park)
    test_e4  = gather_phase_e4(test_csv,  fi_park)
    print(f"\n  Phase E.4 (+catcher framing): train n={train_e4['n']}, test n={test_e4['n']}")
    p_e4, brier_raw_e4, brier_cal_e4, cal_e4, (m_t1_e4, m_b1_e4) = fit_predict_with_calibrator(
        train_e4, test_e4, T1_PHASE_E4_FEATURES, B1_PHASE_E4_FEATURES)
    print(f"    cal range: [{min(cal_e4.rates):.4f}, {max(cal_e4.rates):.4f}]")
    print(f"    Brier (raw): {brier_raw_e4:.4f}, Brier (cal): {brier_cal_e4:.4f}")
    bets_e4 = grade(p_e4, test_e4["y_nrfi"])

    # Show LR weight on the new feature (so we can see if it matters)
    print()
    print(f"  LR weight on catcher framing features (signed; in standardized space):")
    try:
        idx_t1 = T1_PHASE_E4_FEATURES.index("home_catcher_framing")
        idx_b1 = B1_PHASE_E4_FEATURES.index("away_catcher_framing")
        w_t1 = m_t1_e4.w[idx_t1]
        w_b1 = m_b1_e4.w[idx_b1]
        print(f"    T1 (home_catcher_framing -> P(T1 has run)): {w_t1:+.4f}")
        print(f"    B1 (away_catcher_framing -> P(B1 has run)): {w_b1:+.4f}")
        print(f"    (negative weight = better framer reduces probability of run, as expected)")
    except (ValueError, IndexError) as e:
        print(f"    [warn] couldn't extract weight: {e}")

    # P/L comparison
    print()
    print("=" * 100)
    print("  P/L comparison on 2025 holdout (-120 vig assumption):")
    print("=" * 100)
    def show(label, x, brier_v):
        print(f"  {label:<24}  bets={x['n_bets']:>3}  W-L={x['n_wins']}-{x['n_losses']:<3}  "
              f"hit={x['hit']*100:>4.1f}%  P/L={x['pl']:>+7.2f}u  ROI={x['roi']*100:>+5.1f}%   "
              f"(NRFI {x['n_nrfi_wins']}-{x['n_nrfi']-x['n_nrfi_wins']}, "
              f"YRFI {x['n_yrfi_wins']}-{x['n_yrfi']-x['n_yrfi_wins']})  Brier={brier_v:.4f}")
    show("Phase E.3 (no framing)", bets_e3, brier_cal_e3)
    show("Phase E.4 (+framing)",   bets_e4, brier_cal_e4)

    delta_pl = bets_e4["pl"] - bets_e3["pl"]
    delta_brier = brier_cal_e3 - brier_cal_e4   # positive = E4 lower (better)
    delta_hit = (bets_e4["hit"] - bets_e3["hit"]) * 100

    print()
    print(f"  Delta E.4 vs E.3:")
    print(f"    P/L:    {delta_pl:+.2f}u")
    print(f"    Brier:  {delta_brier:+.4f}  ({'+' if delta_brier > 0 else ''}better calibrated)")
    print(f"    Hit:    {delta_hit:+.2f}pp")

    # Verdict
    print()
    print("-" * 100)
    print("  Verdict (per docs/KB.md ship rule: +10u AND no STRONG hit regression)")
    print("-" * 100)
    if delta_pl >= 10 and delta_hit >= 0:
        print(f"  PASS -- ship phase_e4 with catcher framing.")
    elif delta_pl >= 0:
        print(f"  MARGINAL -- catcher framing helps modestly (+{delta_pl:.2f}u) but doesn't")
        print(f"              clear the +10u ship bar.  Hold for more evidence.")
    else:
        print(f"  FAIL -- catcher framing regresses P/L by {abs(delta_pl):.2f}u.  Reject.")
    print()


if __name__ == "__main__":
    main()

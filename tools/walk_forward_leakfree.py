#!/usr/bin/env python3
"""
tools/walk_forward_leakfree.py -- isolate the contribution of leaky features.

T3.11 walk-forward originally tested the production phase_e3 feature set on
2024 -> 2025 and reported +36.67u, 58.0% hit, +6.4% ROI.  Audit (2026-05-03)
found that two of the 16 features per half are LEAKY:

  - home_xera / away_xera          : Statcast cache keyed by (season, pid),
                                     so for an April 2024 game the model
                                     received the pitcher's END-OF-2024 xera.
  - home_whiff_pct_rank /
    away_whiff_pct_rank            : same Statcast cache, same problem.

The other 14 features per half are properly point-in-time:
  fi_park, fip, opp_obp, weather (4), p_last5, top3c_obp,
  ump_rate, era_gap, p_last10, top3c_slg, top3c_iso,
  pvt_nrfi_rate, avg_ip_per_start

This script runs walk-forward with the leaky 4 features removed (14 features
per half, "phase_e3_leakfree"), so we get a clean estimate of the true edge.

Anything that survives this is real; anything that disappears was leakage.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg
from two_stage_model import (
    coerce, load_fi_park, brier, q5_hit, q1_yrfi,
    FI_PARK_DEFAULT, LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG,
    LEAGUE_AVG_ISO, WX_TEMP_DEFAULT, WX_WIND_DEFAULT, WX_HUMIDITY_DEFAULT,
    LEAGUE_NRFI_RATE,
)


# -- LEAK-FREE feature lists (phase_e3 minus xera + whiff_pct_rank) --

T1_LEAKFREE_FEATURES = [
    "fi_park_nrfi_rate", "home_fip", "away_obp",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "home_p_last5_pitcher_nrfi",
    "away_top3c_obp",
    "home_plate_ump_nrfi_rate",
    # xera removed
    # whiff_pct_rank removed
    "era_gap_t1",
    "home_p_last10_pitcher_nrfi",
    "away_top3c_slg",
    "away_top3c_iso",
    "home_pvt_nrfi_rate",
    "home_avg_ip_per_start",
]
B1_LEAKFREE_FEATURES = [
    "fi_park_nrfi_rate", "away_fip", "home_obp",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "away_p_last5_pitcher_nrfi",
    "home_top3c_obp",
    "home_plate_ump_nrfi_rate",
    "era_gap_b1",
    "away_p_last10_pitcher_nrfi",
    "home_top3c_slg",
    "home_top3c_iso",
    "away_pvt_nrfi_rate",
    "away_avg_ip_per_start",
]


def gather_leakfree(csv_path: Path, fi_park_map: dict) -> dict | None:
    """Variant of two_stage_model.gather() with xera and whiff removed."""
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

            t1_x = [
                fi_park,
                coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
                coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
            ] + wx + [
                coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                coerce(r.get("away_top3c_obp"),            LEAGUE_AVG_OBP),
                ump_rate,
                # NO xera, NO whiff_pct_rank
                era_gap_t1,
                coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                coerce(r.get("away_top3c_slg"),            LEAGUE_AVG_SLG),
                coerce(r.get("away_top3c_iso"),            LEAGUE_AVG_ISO),
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
                era_gap_b1,
                coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
                coerce(r.get("home_top3c_slg"),            LEAGUE_AVG_SLG),
                coerce(r.get("home_top3c_iso"),            LEAGUE_AVG_ISO),
                coerce(r.get("away_pvt_nrfi_rate"),        LEAGUE_NRFI_RATE),
                coerce(r.get("away_avg_ip_per_start"),     5.0),
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


def simulated_pnl(p_nrfi, y_nrfi,
                  nrfi_thr=0.58, yrfi_thr=0.42, win_payout=100.0/120.0):
    n_nrfi_bets = n_nrfi_wins = 0
    n_yrfi_bets = n_yrfi_wins = 0
    pl = 0.0
    for p, y in zip(p_nrfi, y_nrfi):
        if p >= nrfi_thr:
            n_nrfi_bets += 1
            if y == 1:
                n_nrfi_wins += 1; pl += win_payout
            else:
                pl -= 1.0
        elif p <= yrfi_thr:
            n_yrfi_bets += 1
            if y == 0:
                n_yrfi_wins += 1; pl += win_payout
            else:
                pl -= 1.0
    n_bets = n_nrfi_bets + n_yrfi_bets
    n_wins = n_nrfi_wins + n_yrfi_wins
    return {
        "n_bets":  n_bets, "n_wins": n_wins, "n_losses": n_bets - n_wins,
        "n_nrfi_bets": n_nrfi_bets, "n_nrfi_wins": n_nrfi_wins,
        "n_yrfi_bets": n_yrfi_bets, "n_yrfi_wins": n_yrfi_wins,
        "pl": pl,
        "roi": (pl / n_bets) if n_bets else 0.0,
        "hit": (n_wins / n_bets) if n_bets else 0.0,
    }


def main():
    BACKTEST_DIR = REPO_ROOT / "data" / "backtests"
    SEASONS = {
        2024: BACKTEST_DIR / "backtest_2024-04-01_to_2024-09-30.csv",
        2025: BACKTEST_DIR / "backtest_2025-04-01_to_2025-09-30.csv",
    }
    fi_park = load_fi_park()

    print("=" * 92)
    print("  WALK-FORWARD LEAK-FREE -- phase_e3 minus xera + whiff_pct_rank")
    print("=" * 92)
    print("  Train 2024 -> Test 2025  (only fold available; advanced features only exist 2024+)")

    train = gather_leakfree(SEASONS[2024], fi_park)
    test  = gather_leakfree(SEASONS[2025], fi_park)
    if train is None or test is None:
        sys.exit("Could not load 2024 or 2025 backtest")

    m_t1 = LogReg.fit(train["X_t1"], train["y_t1"], T1_LEAKFREE_FEATURES, l2=0.05)
    m_b1 = LogReg.fit(train["X_b1"], train["y_b1"], B1_LEAKFREE_FEATURES, l2=0.05)

    p_t1 = m_t1.predict_proba(test["X_t1"])
    p_b1 = m_b1.predict_proba(test["X_b1"])
    p_nrfi = (1.0 - p_t1) * (1.0 - p_b1)
    y = test["y_nrfi"]

    base = float(y.mean())
    clim = base * (1 - base)
    b    = brier(p_nrfi, y)
    q5_r, q5_w, q5_n = q5_hit(p_nrfi, y)
    q1_r, q1_w, q1_n = q1_yrfi(p_nrfi, y)
    pnl = simulated_pnl(p_nrfi, y)

    print()
    print(f"  N train:       {train['n']}")
    print(f"  N test:        {test['n']}")
    print(f"  base rate:     {base*100:.2f}%   mean pred: {p_nrfi.mean()*100:.2f}%")
    print(f"  Brier:         {b:.4f}   (climatology {clim:.4f}, skill {(1-b/clim)*100:+.2f}%)")
    print(f"  Q5 NRFI hit:   {q5_w}-{q5_n-q5_w}  ({q5_r*100:.1f}%)")
    print(f"  Q1 YRFI hit:   {q1_w}-{q1_n-q1_w}  ({q1_r*100:.1f}%)")
    print()
    print(f"  Simulated P/L (STRONG @ 0.58/0.42, -120 vig):")
    print(f"    bets:        {pnl['n_bets']}  (NRFI {pnl['n_nrfi_bets']}, YRFI {pnl['n_yrfi_bets']})")
    print(f"    W-L:         {pnl['n_wins']}-{pnl['n_losses']}")
    print(f"    hit rate:    {pnl['hit']*100:.1f}%")
    print(f"    P/L:         {pnl['pl']:+.2f}u")
    print(f"    ROI:         {pnl['roi']*100:+.1f}%")
    print()

    # Compare to original phase_e3 (which had xera + whiff)
    print("-" * 92)
    print("  COMPARISON to original phase_e3 walk-forward (with leaky xera + whiff):")
    print("    Original:   572 bets,  332-240,  58.0% hit,  +36.67u,  +6.4% ROI,  Brier 0.2488 (skill +0.46%)")
    print(f"    Leak-free:  {pnl['n_bets']:>3} bets,  {pnl['n_wins']}-{pnl['n_losses']:<3},"
          f"  {pnl['hit']*100:>4.1f}% hit,  {pnl['pl']:+6.2f}u,  {pnl['roi']*100:+5.1f}% ROI,"
          f"  Brier {b:.4f} (skill {(1-b/clim)*100:+.2f}%)")
    print()

    # Verdict
    print("-" * 92)
    if (1 - b/clim) > 0 and pnl["pl"] > 0:
        verdict = "PASS -- model has true edge that survives leak removal"
    elif (1 - b/clim) > 0:
        verdict = "MARGINAL -- predictions still beat coin flip but betting policy unprofitable"
    else:
        verdict = "FAIL -- model loses its edge once leakage is removed; phase_e3 result was inflated"
    print(f"  Verdict: {verdict}")
    print()


if __name__ == "__main__":
    main()

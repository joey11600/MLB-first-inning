#!/usr/bin/env python3
"""
tools/walk_forward.py -- T3.11 walk-forward backtest framework.

!! WARNING: KNOWN LEAKAGE IN PHASE_E3 VARIANT (audit 2026-05-03) !!

When you run --include-e3, the phase_e3 variant uses two LEAKY features:

  - home_xera / away_xera          : Statcast cache is keyed by
                                     (season, pid).  For an April 2024 game
                                     the model gets the pitcher's
                                     END-OF-2024 xera, not what was actually
                                     known on the day of the pick.
  - home_whiff_pct_rank /
    away_whiff_pct_rank            : same Statcast cache, same problem.

Removing those two features collapses the original "+36.67u, 58.0% hit,
+6.4% ROI" result on 2024->2025 down to "-9.00u, 53.5% hit, -1.9% ROI"
(see tools/walk_forward_leakfree.py).  The phase_e3 numbers reported here
are INFLATED until the backfill in tools/backfill_xera_whiff_pit.py is run
and the 2024/2025 backtest CSVs are regenerated.

The slim and slim_weather variants do NOT use Statcast features and are
NOT subject to this leak -- their results are honest.

------------------------------------------------------------------------

The point: any model change you want to ship should generalize to seasons
the model hasn't seen.  Backtest-on-trained-data is a useless metric
because LR fits noise.  Walk-forward fixes that:

  Fold 1: train on 2022                 -> test on 2023
  Fold 2: train on 2022 + 2023          -> test on 2024
  Fold 3: train on 2022 + 2023 + 2024   -> test on 2025

For each fold and each variant, we report:

  - Brier score  (lower = better; climatology = base_rate * (1 - base_rate))
  - Top-quintile NRFI hit rate    (proxy for STRONG NRFI selection)
  - Bottom-quintile YRFI hit rate (proxy for STRONG YRFI selection)
  - Simulated betting P&L using current production thresholds
    (STRONG NRFI: P(NRFI) >= 0.58  /  STRONG YRFI: P(NRFI) <= 0.42)
    Bet pricing assumed at the average -120 vig (net 0.83u on a win,
    -1.00u on a loss) -- a conservative ML approximation.

Variants compared (apples-to-apples on the SAME folds):

  - slim          : 3 features per half (park + FIP + opp OBP)
                    available across all 4 historical seasons
  - slim_weather  : 7 features per half (slim + weather)
                    available across all 4 historical seasons

A separate single-fold check runs the phase_e3 production model
(2024 -> 2025) since the advanced features (xera, pvt_nrfi, whiff_pct,
avg_ip) only exist for 2024+ backtests.

Usage:
  python tools/walk_forward.py                   # run all folds, default variants
  python tools/walk_forward.py --variant slim    # just one variant
  python tools/walk_forward.py --include-e3      # also run phase_e3 single fold

This is the gatekeeper for model changes -- if a candidate variant
beats the current model on holdout Brier AND simulated P&L across
multiple folds, it's safe to ship.  If it only wins on one fold,
that's selection bias.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg
from two_stage_model import (
    gather, load_fi_park, brier, q5_hit, q1_yrfi,
    T1_SLIM_FEATURES, B1_SLIM_FEATURES,
    T1_SLIM_WEATHER_FEATURES, B1_SLIM_WEATHER_FEATURES,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
)


# Production thresholds for the simulated P&L column.
STRONG_NRFI_THRESHOLD = 0.58
STRONG_YRFI_THRESHOLD = 0.42

# Average vig assumption for the simulated P&L:
# -120 NRFI / -120 YRFI (conservative; real lines are sometimes worse).
# Net on a win = 100/120 = 0.8333.  Net on a loss = -1.0.
AVG_WIN_PAYOUT = 100.0 / 120.0


# Backtest CSV map -- year -> path.  Two flavors:
#   default:  the original CSVs with leaky xera/whiff (warning: phase_e3 only)
#   leakfree: the rewritten 2024/2025 CSVs from
#             tools/backfill_xera_whiff_pit.py (provably leak-free)
BACKTEST_DIR = REPO_ROOT / "data" / "backtests"
SEASON_CSV: dict[int, Path] = {
    2022: BACKTEST_DIR / "backtest_2022-04-01_to_2022-09-30.csv",
    2023: BACKTEST_DIR / "backtest_2023-04-01_to_2023-09-30.csv",
    2024: BACKTEST_DIR / "backtest_2024-04-01_to_2024-09-30.csv",
    2025: BACKTEST_DIR / "backtest_2025-04-01_to_2025-09-30.csv",
}
SEASON_CSV_LEAKFREE: dict[int, Path] = {
    2022: BACKTEST_DIR / "backtest_2022-04-01_to_2022-09-30.csv",  # no Statcast cols, untouched
    2023: BACKTEST_DIR / "backtest_2023-04-01_to_2023-09-30.csv",  # no Statcast cols, untouched
    2024: BACKTEST_DIR / "backtest_2024-04-01_to_2024-09-30_leakfree.csv",
    2025: BACKTEST_DIR / "backtest_2025-04-01_to_2025-09-30_leakfree.csv",
}

# Walk-forward folds: each (train_seasons, test_season).
WALK_FOLDS: list[tuple[list[int], int]] = [
    ([2022],                   2023),
    ([2022, 2023],             2024),
    ([2022, 2023, 2024],       2025),
]


# ---------------------------------------------------------------------------
# Variant configuration
# ---------------------------------------------------------------------------

VARIANT_CONFIGS: dict[str, dict[str, Any]] = {
    "slim": {
        "t1_features": T1_SLIM_FEATURES,
        "b1_features": B1_SLIM_FEATURES,
        "gather_kwargs": {"slim": True},
        "min_year": 2022,        # available in all years
    },
    "slim_weather": {
        "t1_features": T1_SLIM_WEATHER_FEATURES,
        "b1_features": B1_SLIM_WEATHER_FEATURES,
        "gather_kwargs": {"slim_weather": True},
        "min_year": 2022,        # weather columns present in all years
    },
    "phase_e3": {
        "t1_features": T1_PHASE_E3_FEATURES,
        "b1_features": B1_PHASE_E3_FEATURES,
        "gather_kwargs": {"phase_e3": True},
        "min_year": 2024,        # xera/pvt/whiff/avg_ip only in 2024+
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fit_two_stage(train_blocks: list[dict], t1_feats: list[str],
                  b1_feats: list[str], l2: float = 0.05
                  ) -> tuple[LogReg, LogReg]:
    """Train T1 and B1 models on stacked training blocks."""
    Xt = np.vstack([b["X_t1"] for b in train_blocks])
    yt = np.concatenate([b["y_t1"] for b in train_blocks])
    Xb = np.vstack([b["X_b1"] for b in train_blocks])
    yb = np.concatenate([b["y_b1"] for b in train_blocks])
    m_t1 = LogReg.fit(Xt, yt, t1_feats, l2=l2)
    m_b1 = LogReg.fit(Xb, yb, b1_feats, l2=l2)
    return m_t1, m_b1


def predict_nrfi(m_t1: LogReg, m_b1: LogReg, test_block: dict) -> np.ndarray:
    """Return P(NRFI) under independence: (1 - p_t1) * (1 - p_b1)."""
    p_t1 = m_t1.predict_proba(test_block["X_t1"])
    p_b1 = m_b1.predict_proba(test_block["X_b1"])
    return (1.0 - p_t1) * (1.0 - p_b1)


def simulated_pnl(p_nrfi: np.ndarray, y_nrfi: np.ndarray,
                  nrfi_thr: float = STRONG_NRFI_THRESHOLD,
                  yrfi_thr: float = STRONG_YRFI_THRESHOLD,
                  win_payout: float = AVG_WIN_PAYOUT) -> dict:
    """Apply production STRONG thresholds to the predictions and tabulate P&L."""
    n_nrfi_bets = n_nrfi_wins = 0
    n_yrfi_bets = n_yrfi_wins = 0
    pl_total = 0.0

    for p, y_is_nrfi in zip(p_nrfi, y_nrfi):
        if p >= nrfi_thr:
            # Bet NRFI: win if actual was NRFI (y == 1)
            n_nrfi_bets += 1
            if y_is_nrfi == 1:
                n_nrfi_wins += 1
                pl_total += win_payout
            else:
                pl_total -= 1.0
        elif p <= yrfi_thr:
            # Bet YRFI: win if actual was YRFI (y == 0)
            n_yrfi_bets += 1
            if y_is_nrfi == 0:
                n_yrfi_wins += 1
                pl_total += win_payout
            else:
                pl_total -= 1.0
        # else: PASS (no bet)

    n_bets = n_nrfi_bets + n_yrfi_bets
    n_wins = n_nrfi_wins + n_yrfi_wins
    units_risked = float(n_bets)  # 1u per bet (STRONG = 1u under T2.24 policy)
    return {
        "n_bets":       n_bets,
        "n_wins":       n_wins,
        "n_losses":     n_bets - n_wins,
        "n_nrfi_bets":  n_nrfi_bets,
        "n_nrfi_wins":  n_nrfi_wins,
        "n_yrfi_bets":  n_yrfi_bets,
        "n_yrfi_wins":  n_yrfi_wins,
        "pl_total":     pl_total,
        "roi":          (pl_total / units_risked) if units_risked else 0.0,
        "hit":          (n_wins / n_bets) if n_bets else 0.0,
    }


def evaluate_fold(variant: str, train_seasons: list[int], test_season: int,
                  fi_park: dict, csv_map: dict[int, Path] | None = None
                  ) -> dict | None:
    """Run a single fold for a single variant.  Returns metrics dict, or None
    if the variant isn't available for the requested seasons.

    csv_map controls which CSVs to load (default: SEASON_CSV; pass
    SEASON_CSV_LEAKFREE to use the leak-free 2024/2025 backfill)."""
    cfg = VARIANT_CONFIGS[variant]
    if test_season < cfg["min_year"] or any(s < cfg["min_year"] for s in train_seasons):
        return None
    csv_map = csv_map or SEASON_CSV

    train_blocks = []
    for s in train_seasons:
        block = gather(csv_map[s], fi_park, **cfg["gather_kwargs"])
        if block is None:
            return None
        train_blocks.append(block)
    test_block = gather(csv_map[test_season], fi_park, **cfg["gather_kwargs"])
    if test_block is None:
        return None

    m_t1, m_b1 = fit_two_stage(train_blocks, cfg["t1_features"], cfg["b1_features"])
    p_nrfi = predict_nrfi(m_t1, m_b1, test_block)
    y_nrfi = test_block["y_nrfi"]

    base_rate = float(y_nrfi.mean())
    climatology_brier = base_rate * (1 - base_rate)

    q5_rate, q5_w, q5_n = q5_hit(p_nrfi, y_nrfi)   # top quintile NRFI hit rate
    q1_rate, q1_w, q1_n = q1_yrfi(p_nrfi, y_nrfi)  # bottom quintile YRFI hit rate

    pnl = simulated_pnl(p_nrfi, y_nrfi)

    return {
        "variant":       variant,
        "train":         train_seasons,
        "test":          test_season,
        "n_train":       int(sum(b["n"] for b in train_blocks)),
        "n_test":        int(test_block["n"]),
        "base_rate":     base_rate,
        "brier":         brier(p_nrfi, y_nrfi),
        "brier_clim":    climatology_brier,
        "brier_skill":   1.0 - brier(p_nrfi, y_nrfi) / climatology_brier,
        "mean_pred":     float(p_nrfi.mean()),
        "q5_nrfi_rate":  q5_rate, "q5_w": q5_w, "q5_n": q5_n,
        "q1_yrfi_rate":  q1_rate, "q1_w": q1_w, "q1_n": q1_n,
        **pnl,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_fold_header(fold_idx: int, train: list[int], test: int) -> None:
    print()
    print("=" * 92)
    train_str = "+".join(str(s) for s in train)
    print(f"  Fold {fold_idx}: TRAIN {train_str}  ->  TEST {test}")
    print("=" * 92)


def print_variant_row(m: dict) -> None:
    """One pretty line per variant within a fold."""
    skill_pct = m["brier_skill"] * 100.0
    print(
        f"  {m['variant']:<14} "
        f"N(tr)={m['n_train']:>5}  N(te)={m['n_test']:>5}  "
        f"base={m['base_rate']*100:>4.1f}%  pred={m['mean_pred']*100:>4.1f}%  "
        f"Brier={m['brier']:.4f} (skill {skill_pct:+.2f}%)  "
        f"Q5={m['q5_w']}-{m['q5_n']-m['q5_w']} ({m['q5_nrfi_rate']*100:.1f}%)  "
        f"Q1={m['q1_w']}-{m['q1_n']-m['q1_w']} ({m['q1_yrfi_rate']*100:.1f}%)"
    )
    print(
        f"  {' ':<14} "
        f"sim P/L (STRONG @ {STRONG_NRFI_THRESHOLD:.2f}/{STRONG_YRFI_THRESHOLD:.2f}, -120 vig): "
        f"bets={m['n_bets']:>3}  W-L={m['n_wins']}-{m['n_losses']}  "
        f"hit={m['hit']*100:>4.1f}%  P/L={m['pl_total']:>+7.2f}u  "
        f"ROI={m['roi']*100:>+5.1f}%   "
        f"(NRFI {m['n_nrfi_wins']}-{m['n_nrfi_bets']-m['n_nrfi_wins']}, "
        f"YRFI {m['n_yrfi_wins']}-{m['n_yrfi_bets']-m['n_yrfi_wins']})"
    )


def print_summary(all_results: list[dict]) -> None:
    """Per-variant aggregate across all folds."""
    print()
    print("=" * 92)
    print("  WALK-FORWARD SUMMARY  (aggregate across all folds, per variant)")
    print("=" * 92)
    by_variant: dict[str, list[dict]] = {}
    for r in all_results:
        by_variant.setdefault(r["variant"], []).append(r)

    print(f"  {'variant':<14}  {'folds':>5}  {'N test':>7}  "
          f"{'mean Brier':>10}  {'mean skill':>10}  "
          f"{'bets':>5}  {'W-L':>10}  {'hit':>6}  {'P/L':>9}  {'ROI':>7}")
    print("  " + "-" * 88)
    for v, rows in by_variant.items():
        n_te    = sum(r["n_test"]   for r in rows)
        n_bets  = sum(r["n_bets"]   for r in rows)
        n_wins  = sum(r["n_wins"]   for r in rows)
        pl      = sum(r["pl_total"] for r in rows)
        mb      = float(np.mean([r["brier"]       for r in rows]))
        ms      = float(np.mean([r["brier_skill"] for r in rows])) * 100.0
        hit     = (n_wins / n_bets) if n_bets else 0.0
        roi     = (pl     / n_bets) if n_bets else 0.0
        print(f"  {v:<14}  {len(rows):>5}  {n_te:>7}  "
              f"{mb:>10.4f}  {ms:>9.2f}%  "
              f"{n_bets:>5}  {n_wins}-{n_bets-n_wins:<6}  "
              f"{hit*100:>5.1f}%  {pl:>+8.2f}u  {roi*100:>+5.1f}%")
    print()


def print_verdict(all_results: list[dict]) -> None:
    """Plain-English bottom line."""
    print("-" * 92)
    print("  Verdict")
    print("-" * 92)
    by_variant: dict[str, list[dict]] = {}
    for r in all_results:
        by_variant.setdefault(r["variant"], []).append(r)

    for v, rows in by_variant.items():
        n_folds  = len(rows)
        wins     = sum(1 for r in rows if r["pl_total"] > 0)
        positive = sum(1 for r in rows if r["brier_skill"] > 0)
        n_bets   = sum(r["n_bets"]   for r in rows)
        pl       = sum(r["pl_total"] for r in rows)
        if positive == n_folds and wins >= n_folds * 0.5:
            tag = "  PASS  -- model has predictive value AND profitable on majority of holdouts"
        elif positive == n_folds:
            tag = "  PASS-Brier-only -- model beats coin flip but betting policy isn't profitable"
        elif positive >= n_folds * 0.5:
            tag = "  MIXED -- generalizes to some seasons but not others"
        else:
            tag = "  FAIL  -- model does not beat climatology consistently"
        print(f"  {v:<14}  positive Brier-skill folds: {positive}/{n_folds},  "
              f"profitable folds: {wins}/{n_folds},  "
              f"total P/L over {n_bets} bets: {pl:+.2f}u")
        print(f"  {' ':<14}  {tag}")
    print()
    print("  Walk-forward is the gatekeeper for model changes:")
    print("  - any candidate variant must clear PASS on >= 2 folds before shipping;")
    print("  - a variant that wins one fold but loses others is selection bias.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--variant", action="append", default=None,
        choices=list(VARIANT_CONFIGS.keys()),
        help="Restrict to a single variant.  Omit to run slim + slim_weather.  "
             "Repeatable: --variant slim --variant slim_weather"
    )
    ap.add_argument(
        "--include-e3", action="store_true",
        help="Also run phase_e3 single-fold check (2024 -> 2025).  This requires "
             "the advanced features (xera/pvt/whiff/avg_ip), which only exist "
             "in 2024+ backtests, so it can only be a single fold."
    )
    ap.add_argument(
        "--save-json", metavar="PATH",
        help="Optional: dump all per-fold results as JSON for downstream comparison."
    )
    ap.add_argument(
        "--leakfree", action="store_true",
        help="Use the *_leakfree.csv versions of the 2024 + 2025 backtests "
             "(generated by tools/backfill_xera_whiff_pit.py).  This swaps "
             "xera + whiff_pct_rank for prior-season values, removing the "
             "future-data leak in those features.  Required for an honest "
             "phase_e3 verdict."
    )
    args = ap.parse_args()

    variants = args.variant or ["slim", "slim_weather"]
    csv_map  = SEASON_CSV_LEAKFREE if args.leakfree else SEASON_CSV

    # Make sure all required CSVs exist before doing any work
    missing = [str(p) for p in csv_map.values() if not p.exists()]
    if missing:
        sys.exit("Missing backtest CSVs (need 2022-2025 to walk forward):\n  "
                 + "\n  ".join(missing)
                 + ("\nDid you run tools/backfill_xera_whiff_pit.py first?"
                    if args.leakfree else ""))

    fi_park = load_fi_park()
    all_results: list[dict] = []

    print()
    print("=" * 92)
    print(f"  WALK-FORWARD BACKTEST  (T3.11{'  --LEAKFREE' if args.leakfree else ''})")
    print("=" * 92)
    print(f"  Variants : {', '.join(variants)}")
    print(f"  Folds    : {len(WALK_FOLDS)} multi-season + "
          f"{1 if args.include_e3 else 0} phase_e3 single-fold")
    print(f"  Vig      : -120 (win payout {AVG_WIN_PAYOUT:.4f} per 1u risked)")
    print(f"  STRONG   : NRFI >= {STRONG_NRFI_THRESHOLD:.2f},  "
          f"YRFI <= {STRONG_YRFI_THRESHOLD:.2f}")
    if args.leakfree:
        print(f"  CSVs     : leak-free 2024+2025 (xera/whiff = prior-season lookup)")

    for fold_idx, (train, test) in enumerate(WALK_FOLDS, start=1):
        print_fold_header(fold_idx, train, test)
        for v in variants:
            m = evaluate_fold(v, train, test, fi_park, csv_map=csv_map)
            if m is None:
                print(f"  {v:<14}  (skipped -- variant min_year={VARIANT_CONFIGS[v]['min_year']} "
                      f"and fold uses {min(train)})")
                continue
            print_variant_row(m)
            all_results.append(m)

    if args.include_e3:
        print()
        if args.leakfree:
            print("=" * 92)
            print("  Phase E3 fold: 2024 -> 2025  (LEAK-FREE -- xera/whiff = prior-season)")
            print("=" * 92)
        else:
            print("!" * 92)
            print("  WARNING: phase_e3 result below is INFLATED by xera + whiff_pct_rank leakage.")
            print("  Re-run with --leakfree (after tools/backfill_xera_whiff_pit.py) for the honest number.")
            print("!" * 92)
        print_fold_header("E3", [2024], 2025)
        m = evaluate_fold("phase_e3", [2024], 2025, fi_park, csv_map=csv_map)
        if m is None:
            print("  phase_e3      (skipped -- gather() returned no rows)")
        else:
            print_variant_row(m)
            all_results.append(m)

    if all_results:
        print_summary(all_results)
        print_verdict(all_results)

    if args.save_json:
        out_path = Path(args.save_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2)
        print(f"  Saved per-fold results -> {out_path}")


if __name__ == "__main__":
    main()

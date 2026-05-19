#!/usr/bin/env python3
"""tools/gate_a_tail.py -- Gate A with tail-metric evaluation.

The standard Gate A in this repo uses Brier as the deciding metric, but
Brier averages over ALL games while we only bet on the tails.  A feature
that helps the bottom-20% P(NRFI) bucket (where STRONG YRFI bets live)
or the top-20% bucket (STRONG NRFI) by 1-2pp can be worth real money
while leaving Brier nearly flat.

This script evaluates a candidate variant against baseline PHASE_E3 on
each Gate A split and reports:

  Brier (overall)                  -- the legacy gate metric
  Q5 NRFI hit rate (top-20% pred)  -- proxy for STRONG NRFI bet quality
  Q1 YRFI hit rate (bottom-20%)    -- proxy for STRONG YRFI bet quality
  STRONG-zone hit rate (p<0.44 or p>=0.56) + per-pick EV at -110

Splits:
  S1: train 2024 backtest, test 2025 backtest
  S2: train 2025 backtest, test 2024 backtest
  (S3 with 2026 picks_2026.csv is skipped when the candidate's
  feature is unavailable in that file.)

Pass criteria (tail-first):
  Both splits favor candidate on per-pick STRONG-zone EV AND
  total STRONG-zone EV is positive on majority of splits.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibration import ProbCalibrator
from lr_baseline import LogReg
from two_stage_model import (
    gather, load_fi_park, brier,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
    T1_PHASE_E3_VSHAND_FEATURES, B1_PHASE_E3_VSHAND_FEATURES,
    T1_PHASE_E3_VSHAND_DIFF_FEATURES, B1_PHASE_E3_VSHAND_DIFF_FEATURES,
)

BT24 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
BT25 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
CAL_PATH = ROOT / "data" / "calibration_v2.json"

STRONG_NRFI_P  = 0.56
PASS_LO_P      = 0.44
PAYOUT_110     = 100.0 / 110.0


def _ump_data():
    import json
    cache_p = ROOT / "data" / "umpire_cache.json"
    rates_p = ROOT / "data" / "umpire_rates.json"
    cache = json.load(open(cache_p, encoding="utf-8")) if cache_p.exists() else None
    rates = json.load(open(rates_p, encoding="utf-8")) if rates_p.exists() else None
    return cache, rates


def fit_and_predict(train_csv, test_csv, variant_flags, fi_park, ump_cache, ump_rates):
    """Returns (p_nrfi_cal, y_test_nrfi). variant_flags is a dict of phase_e3, etc."""
    train = gather(train_csv, fi_park, phase_e3=True,
                   ump_cache=ump_cache, ump_rates_data=ump_rates, **variant_flags)
    test  = gather(test_csv,  fi_park, phase_e3=True,
                   ump_cache=ump_cache, ump_rates_data=ump_rates, **variant_flags)

    # Build feature_names lists matching the gather() output
    if variant_flags.get("phase_e3_vshand"):
        t1_feats, b1_feats = T1_PHASE_E3_VSHAND_FEATURES, B1_PHASE_E3_VSHAND_FEATURES
    elif variant_flags.get("phase_e3_vshand_diff"):
        t1_feats, b1_feats = T1_PHASE_E3_VSHAND_DIFF_FEATURES, B1_PHASE_E3_VSHAND_DIFF_FEATURES
    else:
        t1_feats, b1_feats = T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES

    m_t1 = LogReg.fit(train["X_t1"], train["y_t1"], t1_feats, l2=0.05)
    m_b1 = LogReg.fit(train["X_b1"], train["y_b1"], b1_feats, l2=0.05)

    p_t1 = m_t1.predict_proba(test["X_t1"])
    p_b1 = m_b1.predict_proba(test["X_b1"])
    p_raw = (1 - p_t1) * (1 - p_b1)

    cal = ProbCalibrator.load(CAL_PATH)
    p_cal = np.array([cal.predict(float(p)) for p in p_raw])
    return p_cal, test["y_nrfi"]


def tail_metrics(p, y):
    """Returns dict of metrics."""
    n = len(p)
    k = n // 5

    # Brier
    b = brier(p, y)

    # Q5 NRFI = top 20% of predicted P(NRFI)
    idx_top = np.argsort(p)[-k:]
    q5_w = int(y[idx_top].sum())
    q5_n = len(idx_top)

    # Q1 YRFI = bottom 20% of predicted P(NRFI) = top 20% YRFI confidence
    idx_bot = np.argsort(p)[:k]
    q1_w = int(k - y[idx_bot].sum())  # YRFI wins = 1 - NRFI
    q1_n = len(idx_bot)

    # STRONG-zone: p >= 0.56 (NRFI) or p < 0.44 (YRFI)
    strong_nrfi_mask = p >= STRONG_NRFI_P
    strong_yrfi_mask = p < PASS_LO_P
    n_strong_nrfi = int(strong_nrfi_mask.sum())
    n_strong_yrfi = int(strong_yrfi_mask.sum())
    w_strong_nrfi = int(y[strong_nrfi_mask].sum()) if n_strong_nrfi else 0
    w_strong_yrfi = int((1 - y[strong_yrfi_mask]).sum()) if n_strong_yrfi else 0
    l_strong_nrfi = n_strong_nrfi - w_strong_nrfi
    l_strong_yrfi = n_strong_yrfi - w_strong_yrfi

    ev_strong = (w_strong_nrfi + w_strong_yrfi) * PAYOUT_110 - (l_strong_nrfi + l_strong_yrfi)
    n_strong = n_strong_nrfi + n_strong_yrfi
    per_pick_ev = ev_strong / n_strong if n_strong else 0.0

    return {
        "brier": b,
        "q5_w": q5_w, "q5_n": q5_n,
        "q5_rate": q5_w / q5_n if q5_n else 0.0,
        "q1_w": q1_w, "q1_n": q1_n,
        "q1_rate": q1_w / q1_n if q1_n else 0.0,
        "n_strong": n_strong,
        "strong_w": w_strong_nrfi + w_strong_yrfi,
        "strong_l": l_strong_nrfi + l_strong_yrfi,
        "strong_ev_total": ev_strong,
        "strong_ev_per_pick": per_pick_ev,
    }


def fmt(m):
    return (
        f"  Brier {m['brier']:.4f}  |  Q5 NRFI {m['q5_w']:>3}/{m['q5_n']} ({m['q5_rate']*100:>5.1f}%)"
        f"  Q1 YRFI {m['q1_w']:>3}/{m['q1_n']} ({m['q1_rate']*100:>5.1f}%)"
        f"  |  STRONG {m['strong_w']}-{m['strong_l']} (n={m['n_strong']})"
        f"  EV {m['strong_ev_total']:+.2f}u  per-pick {m['strong_ev_per_pick']:+.4f}u"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=["vshand", "vshand_diff"],
                    help="Candidate variant to test against baseline PHASE_E3.")
    args = ap.parse_args()

    flags_map = {
        "vshand":      {"phase_e3_vshand": True},
        "vshand_diff": {"phase_e3_vshand_diff": True},
    }
    cand_flags = flags_map[args.variant]
    fi_park = load_fi_park()
    ump_cache, ump_rates = _ump_data()

    print("=" * 90)
    print(f"  Gate A (tail) -- {args.variant.upper()} vs PHASE_E3 baseline")
    print("=" * 90)

    splits = [
        ("S1: train 2024, test 2025", BT24, BT25),
        ("S2: train 2025, test 2024", BT25, BT24),
    ]
    deltas_per_pick = []
    deltas_brier = []
    for label, tr, te in splits:
        p_base, y = fit_and_predict(tr, te, {}, fi_park, ump_cache, ump_rates)
        p_cand, _ = fit_and_predict(tr, te, cand_flags, fi_park, ump_cache, ump_rates)

        m_base = tail_metrics(p_base, y)
        m_cand = tail_metrics(p_cand, y)

        print(f"\n{label}")
        print(f"  BASELINE:")
        print(fmt(m_base))
        print(f"  CANDIDATE:")
        print(fmt(m_cand))

        d_brier = m_cand["brier"] - m_base["brier"]
        d_per_pick = m_cand["strong_ev_per_pick"] - m_base["strong_ev_per_pick"]
        d_strong_n = m_cand["n_strong"] - m_base["n_strong"]
        print(f"  DELTA: Brier {d_brier:+.4f}  per-pick EV {d_per_pick:+.4f}u  strong-vol {d_strong_n:+d}")

        deltas_per_pick.append(d_per_pick)
        deltas_brier.append(d_brier)

    print("\n" + "=" * 90)
    wins_pp = sum(1 for d in deltas_per_pick if d > 0)
    wins_br = sum(1 for d in deltas_brier  if d < 0)
    print(f"  Summary: per-pick EV wins {wins_pp}/{len(deltas_per_pick)}  "
          f"|  Brier wins {wins_br}/{len(deltas_brier)}")
    print(f"  Per-pick EV deltas: {['%+.4fu' % d for d in deltas_per_pick]}")
    print(f"  Brier deltas:       {['%+.4f' % d for d in deltas_brier]}")


if __name__ == "__main__":
    main()

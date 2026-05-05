#!/usr/bin/env python3
"""
tools/build_v5_model.py -- build the v5 candidate model and shadow-backtest it.

CONTEXT (2026-05-04)
--------------------
Production v2 model uses 18 features per half (Phase E.3 ship on 4/27).
Live results show:

  Pre-Statcast  (4/01 - 4/27): STRONG 103-59 = 63.6% win rate
  Post-Statcast (4/28 - 5/04): STRONG  19-16 = 54.3% win rate

The model lost 9.3pp of edge the day Statcast features were added.

Multi-variant 3-fold backtest confirms: the pre-Statcast 7-feature LR is
the best of every variant tested (+17.32u over 2892 bets at top/bot 20%,
beats GBM_shallow / L1_LR / recency_LR / prod_full_LR / last10_minimal).

THIS SCRIPT
-----------
Builds a v5 candidate (the pre-Statcast spec) WITHOUT touching production:

  - 7 features per half: fi_park, fip, opp_obp, 4 weather features
  - LR fit on 2024+2025 truepit (the leak-free corpus)
  - Save to data/lr_t1_v5.json + data/lr_b1_v5.json
  - Save calibrator data/calibration_v5.json

Then "shadow replays" the 4/28 -> 2026-05-04 production picks:
  - For each picks_2026 row in that window, compute what v5 would have
    predicted using the row's already-stored feature columns.
  - Compare v5 pick (label/strength) to production v2 pick.
  - For graded rows, compute hypothetical v5 P&L using the same DK odds
    that were captured for the production bet.
  - Side-by-side comparison: v2 actual P&L vs v5 shadow P&L per slate.

USAGE
-----
  python tools/build_v5_model.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg

# 2024 truepit + 2025 truepit make up the training corpus (leak-free,
# priors-pooled xera/whiff -- though v5 doesn't use those columns anyway).
TRAIN_CSVS = [
    REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv",
    REPO_ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv",
]

PICKS_2026  = REPO_ROOT / "data/picks_2026.csv"

OUT_T1     = REPO_ROOT / "data/lr_t1_v5.json"
OUT_B1     = REPO_ROOT / "data/lr_b1_v5.json"
OUT_CAL    = REPO_ROOT / "data/calibration_v5.json"
OUT_REPORT = REPO_ROOT / "data/v5_shadow_report.json"

LEAGUE_NRFI = 0.5246
LEAGUE_OBP  = 0.314
LEAGUE_FIP  = 4.10
WX_T, WX_W, WX_H = 22.0, 8.0, 50.0


def to_f(v, d):
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


def actual_label_truepit(r):
    try:
        f = int(r.get("fi_total_runs") or -1)
        return -1 if f < 0 else (1 if f == 0 else 0)
    except (ValueError, TypeError):
        return -1


def actual_label_picks2026(r):
    """picks_2026.csv has graded_result instead of fi_total_runs in actionable form."""
    g = (r.get("graded_result") or "").strip().upper()
    if g == "WIN":
        # The bet hit -- so actual side matches pick
        side = (r.get("pick_side") or "").upper()
        return 1 if side == "NRFI" else 0
    if g == "LOSS":
        side = (r.get("pick_side") or "").upper()
        return 0 if side == "NRFI" else 1
    return -1


# ---------------------------------------------------------------------
# v5 feature builders -- 7 features per half (pre-Statcast spec)
# ---------------------------------------------------------------------

def t1_v5(r):
    park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
    return [
        park,
        to_f(r.get("home_fip_blend") or r.get("home_fip"), LEAGUE_FIP),
        to_f(r.get("away_obp"), LEAGUE_OBP),
        to_f(r.get("wx_temp_c"),   WX_T),
        to_f(r.get("wx_wind_kmh"), WX_W),
        to_f(r.get("wx_humidity"), WX_H),
        to_f(r.get("wx_is_dome"),  0.0),
    ]


def b1_v5(r):
    park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
    return [
        park,
        to_f(r.get("away_fip_blend") or r.get("away_fip"), LEAGUE_FIP),
        to_f(r.get("home_obp"), LEAGUE_OBP),
        to_f(r.get("wx_temp_c"),   WX_T),
        to_f(r.get("wx_wind_kmh"), WX_W),
        to_f(r.get("wx_humidity"), WX_H),
        to_f(r.get("wx_is_dome"),  0.0),
    ]


T1_NAMES = ["fi_park_nrfi_rate", "home_fip", "away_obp",
            "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome"]
B1_NAMES = ["fi_park_nrfi_rate", "away_fip", "home_obp",
            "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome"]


# ---------------------------------------------------------------------
# Combined NRFI prob -- product of (1 - p_run) for T1 and B1
# ---------------------------------------------------------------------

def combined_nrfi_prob(p_t1_run, p_b1_run):
    """P(NRFI) = P(no run T1) * P(no run B1)."""
    return (1.0 - p_t1_run) * (1.0 - p_b1_run)


# ---------------------------------------------------------------------
# Simple isotonic / quantile calibrator (mirrors production
# ProbCalibrator format: centers + rates).  Built from holdout slice
# of training data.
# ---------------------------------------------------------------------

def fit_isotonic_calibrator(preds, labels, n_bins=20):
    """Equal-count quantile bins; PAV smoothing for monotonicity."""
    pairs = sorted(zip(preds, labels), key=lambda x: x[0])
    n = len(pairs)
    per = n // n_bins
    centers, rates = [], []
    for i in range(n_bins):
        lo = i * per
        hi = (i + 1) * per if i < n_bins - 1 else n
        chunk = pairs[lo:hi]
        if not chunk:
            continue
        c = sum(p for p, _ in chunk) / len(chunk)
        r = sum(y for _, y in chunk) / len(chunk)
        centers.append(c)
        rates.append(r)
    # PAV-smooth rates monotone-increasing
    smoothed = list(rates)
    i = 0
    while i < len(smoothed) - 1:
        if smoothed[i + 1] < smoothed[i]:
            new = (smoothed[i] + smoothed[i + 1]) / 2
            smoothed[i] = new
            smoothed[i + 1] = new
            i = max(0, i - 1)
        else:
            i += 1
    return centers, smoothed


def cal_predict(p, centers, rates):
    if not centers:
        return p
    if p <= centers[0]:
        return rates[0]
    if p >= centers[-1]:
        return rates[-1]
    # Linear interp between bracketing bin centers
    lo, hi = 0, len(centers) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if centers[mid] <= p:
            lo = mid
        else:
            hi = mid
    c0, c1 = centers[lo], centers[hi]
    r0, r1 = rates[lo], rates[hi]
    if c1 == c0:
        return (r0 + r1) / 2
    t = (p - c0) / (c1 - c0)
    return r0 + t * (r1 - r0)


# ---------------------------------------------------------------------
# Build the model
# ---------------------------------------------------------------------

def main():
    print("=" * 80)
    print("  V5 CANDIDATE MODEL BUILD + SHADOW BACKTEST")
    print("=" * 80)

    # 1. Train T1 + B1 LRs on combined 2024+2025 truepit
    print(f"\n[1/4] Training T1 + B1 LRs on {len(TRAIN_CSVS)} CSV(s)...")
    t1_X, t1_y = [], []
    b1_X, b1_y = [], []
    for path in TRAIN_CSVS:
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            lab = actual_label_truepit(r)
            if lab < 0:
                continue
            # T1 label: did away team score in T1?  fi_away_runs > 0 = run scored.
            try:
                fi_a = int(r.get("fi_away_runs") or -1)
                fi_h = int(r.get("fi_home_runs") or -1)
            except (ValueError, TypeError):
                continue
            if fi_a < 0 or fi_h < 0:
                continue
            t1_X.append(t1_v5(r))
            t1_y.append(1 if fi_a == 0 else 0)    # 1 = no run in T1 (good for NRFI)
            b1_X.append(b1_v5(r))
            b1_y.append(1 if fi_h == 0 else 0)

    print(f"      T1: {len(t1_X)} rows, base rate (no run) = {sum(t1_y)/len(t1_y):.4f}")
    print(f"      B1: {len(b1_X)} rows, base rate (no run) = {sum(b1_y)/len(b1_y):.4f}")

    # We want the LRs to predict P(run) so production can compose them.
    # Flip the labels.
    t1_y_run = [1 - y for y in t1_y]
    b1_y_run = [1 - y for y in b1_y]

    t1_model = LogReg.fit(np.asarray(t1_X), np.asarray(t1_y_run, dtype=float), T1_NAMES)
    b1_model = LogReg.fit(np.asarray(b1_X), np.asarray(b1_y_run, dtype=float), B1_NAMES)

    # 2. Save T1 + B1 model files (production format)
    print(f"\n[2/4] Saving model files...")
    t1_model.save(OUT_T1)
    b1_model.save(OUT_B1)
    print(f"      {OUT_T1}")
    print(f"      {OUT_B1}")

    # 3. Build the calibrator: predict P(NRFI) on training rows, fit isotonic
    print(f"\n[3/4] Fitting v5 calibrator on training NRFI predictions...")
    raw_probs, true_labels = [], []
    for path in TRAIN_CSVS:
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            lab = actual_label_truepit(r)
            if lab < 0:
                continue
            p_t1 = t1_model.predict_proba_one(t1_v5(r))
            p_b1 = b1_model.predict_proba_one(b1_v5(r))
            raw = combined_nrfi_prob(p_t1, p_b1)
            raw_probs.append(raw)
            true_labels.append(lab)
    centers, rates = fit_isotonic_calibrator(raw_probs, true_labels, n_bins=20)
    with open(OUT_CAL, "w", encoding="utf-8") as f:
        json.dump({
            "centers":       centers,
            "rates":         rates,
            "train_n":       len(raw_probs),
            "train_seasons": ["2024", "2025"],
            "model_version": "v5",
            "feature_set":   "v0_7feat (pre-Statcast spec)",
        }, f, indent=2)
    print(f"      {OUT_CAL}")
    print(f"      n_bins={len(centers)}  range=[{centers[0]:.3f}, {centers[-1]:.3f}]")

    # 4. Shadow replay on 2026 picks since 4/28
    print(f"\n[4/4] Shadow replay 4/28 -> latest on picks_2026.csv...")
    with open(PICKS_2026, encoding="utf-8") as f:
        rows_2026 = list(csv.DictReader(f))

    # Use thresholds matching what production currently uses.
    # Production: STRONG NRFI if calibrated P(NRFI) >= 0.56 (per recent commits),
    # STRONG YRFI if <= 0.44.  We replicate that for v5.
    STRONG_NRFI_TH = 0.56
    STRONG_YRFI_TH = 0.44

    summary = {
        "v2_pl_total": 0.0,  "v5_pl_total": 0.0,
        "v2_strong_W": 0,    "v2_strong_L": 0,
        "v5_strong_W": 0,    "v5_strong_L": 0,
    }
    per_day = {}
    flips = []

    for r in rows_2026:
        d = r.get("date") or ""
        if d < "2026-04-28":
            continue
        graded = (r.get("graded_result") or "").strip().upper()
        if graded not in ("WIN", "LOSS"):
            continue   # only score graded games

        # Compute v5 prediction on row's stored features
        p_t1 = t1_model.predict_proba_one(t1_v5(r))
        p_b1 = b1_model.predict_proba_one(b1_v5(r))
        v5_raw = combined_nrfi_prob(p_t1, p_b1)
        v5_cal = cal_predict(v5_raw, centers, rates)

        # Determine v5 pick
        if v5_cal >= STRONG_NRFI_TH:
            v5_side, v5_strength = "NRFI", "STRONG"
        elif v5_cal <= STRONG_YRFI_TH:
            v5_side, v5_strength = "YRFI", "STRONG"
        else:
            v5_side, v5_strength = None, "PASS"

        # Production v2's actual pick on the same row
        v2_side = (r.get("pick_side") or "").upper()
        v2_strength = (r.get("pick_strength") or "").upper()
        v2_pl = 0.0
        try:
            v2_pl = float(r.get("profit_loss_units") or 0)
        except (ValueError, TypeError):
            pass

        # Determine actual NRFI/YRFI outcome
        # actual_side comes from graded result + v2_side
        # If v2 graded WIN, actual matches v2_side; LOSS = opposite.
        v2_was_correct = (graded == "WIN")
        if v2_side in ("NRFI", "YRFI"):
            actual_side = v2_side if v2_was_correct else ("YRFI" if v2_side == "NRFI" else "NRFI")
        else:
            # v2 picked PASS -- can't deduce actual.  Get from fi_total_runs if present.
            actual_side = None

        # v5 hypothetical P&L: use the SAME DK odds the production bet used
        # if v5 picks the same side -- but if v5 picks differently, we'd need
        # both NRFI and YRFI odds.  For STRONG bets in our window, we have
        # market_nrfi_odds + market_yrfi_odds.
        v5_pl = 0.0
        v5_outcome = None
        if v5_strength == "STRONG" and actual_side is not None:
            won = (v5_side == actual_side)
            v5_outcome = "WIN" if won else "LOSS"
            if won:
                # Use v5_side's American odds
                am_str = r.get(f"market_{v5_side.lower()}_odds") or ""
                try:
                    am = int(am_str)
                    if am > 0:
                        v5_pl = am / 100.0
                    else:
                        v5_pl = 100.0 / -am
                except (ValueError, TypeError):
                    # Fallback to -110 even-ish
                    v5_pl = 0.909
            else:
                v5_pl = -1.0

        # Tally only when production actually placed a bet (so we compare apples
        # to apples on the bet-placed slate).  Otherwise v5 P&L is hypothetical
        # but useful as a sanity signal.
        bet_placed = (r.get("bet_placed") or "").strip().upper() == "Y"

        if d not in per_day:
            per_day[d] = {
                "n_games": 0, "v2_picks": 0, "v5_picks": 0,
                "v2_pl": 0.0, "v5_pl": 0.0, "v2_correct": 0,
                "v5_correct": 0, "agreed": 0, "flipped": 0,
            }
        per_day[d]["n_games"] += 1
        if v2_strength == "STRONG":
            per_day[d]["v2_picks"] += 1
            if v2_was_correct: per_day[d]["v2_correct"] += 1
            if bet_placed: per_day[d]["v2_pl"] += v2_pl
        if v5_strength == "STRONG":
            per_day[d]["v5_picks"] += 1
            if v5_outcome == "WIN":
                per_day[d]["v5_correct"] += 1
            per_day[d]["v5_pl"] += v5_pl
        if v2_side and v5_side and v2_side == v5_side and v2_strength == "STRONG" and v5_strength == "STRONG":
            per_day[d]["agreed"] += 1
        if v2_strength == "STRONG" and v5_strength == "STRONG" and v2_side != v5_side:
            per_day[d]["flipped"] += 1
            flips.append({
                "date": d,
                "matchup": f"{r.get('away_team')}@{r.get('home_team')}",
                "v2_pick": f"{v2_strength} {v2_side}",
                "v5_pick": f"{v5_strength} {v5_side}",
                "v2_p_nrfi":   round(to_f(r.get("nrfi_prob"), 0.5), 4),
                "v5_p_nrfi":   round(v5_cal, 4),
                "actual": actual_side or "?",
                "v2_pl": round(v2_pl if bet_placed else 0.0, 3),
                "v5_pl": round(v5_pl, 3),
            })

        # Accumulate totals
        summary["v2_pl_total"] += v2_pl if (v2_strength == "STRONG" and bet_placed) else 0.0
        summary["v5_pl_total"] += v5_pl
        if v2_strength == "STRONG":
            if v2_was_correct: summary["v2_strong_W"] += 1
            else: summary["v2_strong_L"] += 1
        if v5_strength == "STRONG":
            if v5_outcome == "WIN": summary["v5_strong_W"] += 1
            elif v5_outcome == "LOSS": summary["v5_strong_L"] += 1

    # --- Print report ---
    print()
    print(f"{'date':>10} | {'v2_picks':>8} {'v2_W-L':>8} {'v2_pl':>8} | {'v5_picks':>8} {'v5_W-L':>8} {'v5_pl':>8} | {'agree':>5} {'flip':>4}")
    print('-' * 90)
    for d in sorted(per_day.keys()):
        s = per_day[d]
        v2_wl = f"{s['v2_correct']}-{s['v2_picks']-s['v2_correct']}"
        v5_wl = f"{s['v5_correct']}-{s['v5_picks']-s['v5_correct']}"
        print(f"{d:>10} | {s['v2_picks']:>8} {v2_wl:>8} {s['v2_pl']:>+7.2f}u | {s['v5_picks']:>8} {v5_wl:>8} {s['v5_pl']:>+7.2f}u | {s['agreed']:>5} {s['flipped']:>4}")

    print('-' * 90)
    print(f"\nSUMMARY (post-Statcast era 4/28 -> latest):")
    print(f"  v2 actual STRONG record: {summary['v2_strong_W']}-{summary['v2_strong_L']}  P&L = {summary['v2_pl_total']:+.2f}u  (BET-PLACED only)")
    print(f"  v5 shadow STRONG record: {summary['v5_strong_W']}-{summary['v5_strong_L']}  P&L = {summary['v5_pl_total']:+.2f}u  (every STRONG zone)")
    print(f"  Picks where v2 and v5 disagree (STRONG side flips): {len(flips)}")

    # Save the report for later analysis
    out = {
        "summary": summary,
        "per_day": per_day,
        "flips":   flips,
    }
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Saved -> {OUT_REPORT}")

    if flips:
        print(f"\n  Notable flips:")
        for f in flips[:10]:
            print(f"    {f['date']} {f['matchup']:>9}  v2={f['v2_pick']:<14} v5={f['v5_pick']:<14}  actual={f['actual']:<4}  v5pl={f['v5_pl']:+.2f}u")


if __name__ == "__main__":
    main()

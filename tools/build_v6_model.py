#!/usr/bin/env python3
"""
tools/build_v6_model.py -- V6 candidate: drop multicollinear features.

DIAGNOSIS (2026-05-04)
----------------------
Production V2 LR has WRONG-SIGN weights:
  T1: home_fip (-), away_top3c_slg (-)
  B1: away_fip (-), home_obp (-), home_top3c_slg (-)

Cause: multicollinearity.  When you put both `home_xera` and `home_fip`
into the model, xera captures the pitcher-quality signal (+0.296 weight,
correct sign), and LR responds by flipping fip's sign because fip is now
fighting xera for the same signal space.  Same for top3c_slg vs
top3c_iso (power), and home_obp vs home_top3c_obp.

The 7-feature pre-Statcast model (V5) had CLEAN signs because no two
features measured the same thing -> 63.6% win rate over 27 days.
The 18-feature post-Statcast model has degenerate fits -> 54.3% win rate.

V6 FIX
------
Drop the redundant features.  Keep the BEST measure of each underlying
signal:
  pitcher quality       -> xera (drop fip)
  team offense          -> top3c_obp (drop full-team obp)
  team power            -> top3c_iso (drop top3c_slg)

V6 features per half (14 total):
  fi_park_nrfi_rate
  wx_temp_c, wx_wind_kmh, wx_humidity, wx_is_dome
  pitcher_last5_nrfi, pitcher_last10_nrfi
  top3c_obp, top3c_iso
  ump_nrfi_rate, era_gap
  xera, whiff_pct_rank
  pvt_nrfi_rate, avg_ip_per_start

That's 15. Round out at 14 by dropping last5 (last10 is more stable).

This script:
  1. Trains V6 LR on 2024+2025 truepit (leak-free corpus)
  2. Verifies all weights have correct signs (no multicollinear damage)
  3. Saves to data/lr_t1_v6.json + data/lr_b1_v6.json + calibration_v6.json
  4. Runs 3-fold leak-free walk-forward to verify robustness
  5. Replays V6 on 2026 picks for direct vs-V2 comparison
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg

TRAIN_CSVS = [
    REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv",
    REPO_ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv",
]
PICKS_2026 = REPO_ROOT / "data/picks_2026.csv"

OUT_T1     = REPO_ROOT / "data/lr_t1_v6.json"
OUT_B1     = REPO_ROOT / "data/lr_b1_v6.json"
OUT_CAL    = REPO_ROOT / "data/calibration_v6.json"

# V6 feature spec -- 14 features per half (no multicollinear redundancy)

T1_NAMES = [
    "fi_park_nrfi_rate",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "home_p_last10_pitcher_nrfi",
    "away_top3c_obp", "away_top3c_iso",
    "home_plate_ump_nrfi_rate",
    "era_gap_t1",
    "home_xera", "home_whiff_pct_rank",
    "home_pvt_nrfi_rate", "home_avg_ip_per_start",
]
B1_NAMES = [
    "fi_park_nrfi_rate",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "away_p_last10_pitcher_nrfi",
    "home_top3c_obp", "home_top3c_iso",
    "home_plate_ump_nrfi_rate",
    "era_gap_b1",
    "away_xera", "away_whiff_pct_rank",
    "away_pvt_nrfi_rate", "away_avg_ip_per_start",
]

# Expected signs per feature in a P(run) regression: positive means
# higher feature value -> more likely a run is scored in this half.
T1_EXPECTED_SIGNS = {
    "fi_park_nrfi_rate":           "neg",   # park rates NRFI -> less run
    "wx_temp_c":                    "pos",  # warm = more offense
    "wx_wind_kmh":                  "pos",  # wind = ball travels = more runs (rough)
    "wx_humidity":                  "pos",
    "wx_is_dome":                   "neutral",  # dome neutralizes weather; sign weak
    "home_p_last10_pitcher_nrfi":   "neg",   # pitcher's recent NRFI rate -> fewer runs
    "away_top3c_obp":               "pos",   # higher OBP -> more base runners -> runs
    "away_top3c_iso":               "pos",   # power -> XBHs -> runs
    "home_plate_ump_nrfi_rate":     "neg",   # pitcher-friendly ump -> fewer runs
    "era_gap_t1":                   "pos",   # home FIP - away FIP; positive = bad pitcher
    "home_xera":                    "pos",   # higher xera = bad pitcher = more runs
    "home_whiff_pct_rank":          "neg",   # higher whiff (good pitcher) -> fewer runs
    "home_pvt_nrfi_rate":           "neg",   # pitcher vs team NRFI -> fewer runs
    "home_avg_ip_per_start":        "neg",   # longer outings = better starter
}
B1_EXPECTED_SIGNS = {
    "fi_park_nrfi_rate":           "neg",
    "wx_temp_c":                    "pos",
    "wx_wind_kmh":                  "pos",
    "wx_humidity":                  "pos",
    "wx_is_dome":                   "neutral",
    "away_p_last10_pitcher_nrfi":   "neg",
    "home_top3c_obp":               "pos",
    "home_top3c_iso":               "pos",
    "home_plate_ump_nrfi_rate":     "neg",
    "era_gap_b1":                   "pos",
    "away_xera":                    "pos",
    "away_whiff_pct_rank":          "neg",
    "away_pvt_nrfi_rate":           "neg",
    "away_avg_ip_per_start":        "neg",
}

LEAGUE_NRFI = 0.5246
LEAGUE_OBP  = 0.314
LEAGUE_ISO  = 0.169
LEAGUE_FIP  = 4.10
LEAGUE_XERA = 4.20
WX_T, WX_W, WX_H = 22.0, 8.0, 50.0
NEUTRAL_WHIFF = 50.0


def to_f(v, d):
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


def t1_v6(r):
    park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
    h_era = to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_FIP)
    a_era = to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_FIP)
    return [
        park,
        to_f(r.get("wx_temp_c"),   WX_T),
        to_f(r.get("wx_wind_kmh"), WX_W),
        to_f(r.get("wx_humidity"), WX_H),
        to_f(r.get("wx_is_dome"),  0.0),
        to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
        to_f(r.get("away_top3c_iso") or r.get("away_top3_iso"), LEAGUE_ISO),
        LEAGUE_NRFI,    # ump_rate not in CSV
        h_era - a_era,
        to_f(r.get("home_xera"), LEAGUE_XERA),
        to_f(r.get("home_whiff_pct_rank"), NEUTRAL_WHIFF),
        to_f(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI),
        to_f(r.get("home_avg_ip_per_start"), 5.0),
    ]


def b1_v6(r):
    park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
    h_era = to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_FIP)
    a_era = to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_FIP)
    return [
        park,
        to_f(r.get("wx_temp_c"),   WX_T),
        to_f(r.get("wx_wind_kmh"), WX_W),
        to_f(r.get("wx_humidity"), WX_H),
        to_f(r.get("wx_is_dome"),  0.0),
        to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
        to_f(r.get("home_top3c_iso") or r.get("home_top3_iso"), LEAGUE_ISO),
        LEAGUE_NRFI,
        a_era - h_era,
        to_f(r.get("away_xera"), LEAGUE_XERA),
        to_f(r.get("away_whiff_pct_rank"), NEUTRAL_WHIFF),
        to_f(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI),
        to_f(r.get("away_avg_ip_per_start"), 5.0),
    ]


def fi_total(r):
    try:
        return int(r.get("fi_total_runs") or -1)
    except (ValueError, TypeError):
        return -1


def actual_label(r):
    f = fi_total(r)
    return -1 if f < 0 else (1 if f == 0 else 0)


# Calibrator: same isotonic + PAV approach as v5
def fit_isotonic(preds, labels, n_bins=20):
    pairs = sorted(zip(preds, labels), key=lambda x: x[0])
    n = len(pairs)
    per = n // n_bins
    centers, rates = [], []
    for i in range(n_bins):
        lo = i * per
        hi = (i + 1) * per if i < n_bins - 1 else n
        chunk = pairs[lo:hi]
        if not chunk: continue
        centers.append(sum(p for p, _ in chunk) / len(chunk))
        rates.append(sum(y for _, y in chunk) / len(chunk))
    smoothed = list(rates)
    i = 0
    while i < len(smoothed) - 1:
        if smoothed[i + 1] < smoothed[i]:
            new = (smoothed[i] + smoothed[i + 1]) / 2
            smoothed[i] = new; smoothed[i + 1] = new
            i = max(0, i - 1)
        else:
            i += 1
    return centers, smoothed


def cal_p(p, c, r):
    if p <= c[0]: return r[0]
    if p >= c[-1]: return r[-1]
    lo, hi = 0, len(c) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if c[mid] <= p: lo = mid
        else: hi = mid
    if c[hi] == c[lo]: return (r[lo] + r[hi]) / 2
    return r[lo] + (p - c[lo]) / (c[hi] - c[lo]) * (r[hi] - r[lo])


def main():
    print("=" * 80)
    print("  V6 CANDIDATE BUILD: drop multicollinear features (14/half, no fip/obp/slg)")
    print("=" * 80)

    # 1. Train T1 + B1 LRs on 2024+2025 truepit
    print(f"\n[1/5] Training V6 T1 + B1 on {len(TRAIN_CSVS)} CSV(s)...")
    t1_X, t1_y = [], []
    b1_X, b1_y = [], []
    for path in TRAIN_CSVS:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                lab = actual_label(r)
                if lab < 0: continue
                try:
                    fi_a = int(r.get("fi_away_runs") or -1)
                    fi_h = int(r.get("fi_home_runs") or -1)
                except (ValueError, TypeError):
                    continue
                if fi_a < 0 or fi_h < 0: continue
                t1_X.append(t1_v6(r)); t1_y.append(1 if fi_a > 0 else 0)  # 1 = run scored T1
                b1_X.append(b1_v6(r)); b1_y.append(1 if fi_h > 0 else 0)
    print(f"      n_train T1 = {len(t1_X)}, B1 = {len(b1_X)}")
    t1_model = LogReg.fit(np.asarray(t1_X), np.asarray(t1_y, dtype=float), T1_NAMES)
    b1_model = LogReg.fit(np.asarray(b1_X), np.asarray(b1_y, dtype=float), B1_NAMES)

    # 2. Print weights and verify signs
    print(f"\n[2/5] Verifying weight signs (was the multicollinearity fixed?)...")
    sign_errors = 0
    for label, model, names, exp in [
        ("T1", t1_model, T1_NAMES, T1_EXPECTED_SIGNS),
        ("B1", b1_model, B1_NAMES, B1_EXPECTED_SIGNS),
    ]:
        print(f"\n  {label} weights:")
        for n, w in zip(names, model.w):
            expected = exp.get(n, "neutral")
            actual_sign = "pos" if w > 0.005 else "neg" if w < -0.005 else "neutral"
            ok = expected == "neutral" or expected == actual_sign
            flag = "  OK" if ok else "  *** WRONG SIGN ***"
            print(f"    {n:<35} {w:+.4f}  expected={expected:<7}  got={actual_sign:<7}{flag}")
            if not ok and expected != "neutral":
                sign_errors += 1
    print(f"\n  Sign errors: {sign_errors}  (any > 0 = multicollinearity STILL present)")

    # 3. Save model files + calibrator
    print(f"\n[3/5] Saving model files...")
    t1_model.save(OUT_T1); b1_model.save(OUT_B1)

    # Build the calibrator on training data
    raw_probs, true_labels = [], []
    for path in TRAIN_CSVS:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                lab = actual_label(r)
                if lab < 0: continue
                p_t1 = t1_model.predict_proba_one(t1_v6(r))
                p_b1 = b1_model.predict_proba_one(b1_v6(r))
                raw_probs.append((1 - p_t1) * (1 - p_b1))
                true_labels.append(lab)
    centers, rates = fit_isotonic(raw_probs, true_labels, 20)
    with open(OUT_CAL, "w", encoding="utf-8") as f:
        json.dump({
            "centers": centers, "rates": rates,
            "train_n": len(raw_probs), "train_seasons": ["2024", "2025"],
            "model_version": "v6", "feature_set": "14_no_multicollinear",
        }, f, indent=2)
    print(f"      {OUT_T1}\n      {OUT_B1}\n      {OUT_CAL}")
    print(f"      calibration range: [{centers[0]:.3f}, {centers[-1]:.3f}]")

    # 4. 3-fold validation
    print(f"\n[4/5] 3-fold leak-free walk-forward on V6...")
    folds = [
        ("2022->2023",
         REPO_ROOT / "data/backtests/backtest_2022-04-01_to_2022-09-30.csv",
         REPO_ROOT / "data/backtests/backtest_2023-04-01_to_2023-09-30.csv"),
        ("2023->2024",
         REPO_ROOT / "data/backtests/backtest_2023-04-01_to_2023-09-30.csv",
         REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv"),
        ("2024->2025",
         REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv",
         REPO_ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv"),
    ]
    fold_pls = []
    print(f"  {'fold':<14} | {'n_test':>6} {'brier':>7} | {'top20%(NRFI)':>15} {'pl':>7} | {'bot20%(YRFI)':>15} {'pl':>7} | {'total':>8}")
    for label, train_p, test_p in folds:
        # Refit on this fold's train data only
        with open(train_p, encoding="utf-8") as f: train_rows = list(csv.DictReader(f))
        with open(test_p, encoding="utf-8")  as f: test_rows  = list(csv.DictReader(f))
        Xt1, yt1 = [], []; Xb1, yb1 = [], []
        for r in train_rows:
            lab = actual_label(r)
            if lab < 0: continue
            try:
                fi_a = int(r.get("fi_away_runs") or -1); fi_h = int(r.get("fi_home_runs") or -1)
            except (ValueError, TypeError): continue
            if fi_a < 0 or fi_h < 0: continue
            Xt1.append(t1_v6(r)); yt1.append(1 if fi_a > 0 else 0)
            Xb1.append(b1_v6(r)); yb1.append(1 if fi_h > 0 else 0)
        m_t1 = LogReg.fit(np.asarray(Xt1), np.asarray(yt1, dtype=float), T1_NAMES)
        m_b1 = LogReg.fit(np.asarray(Xb1), np.asarray(yb1, dtype=float), B1_NAMES)
        # Predict on test
        preds = []
        for r in test_rows:
            lab = actual_label(r)
            if lab < 0: continue
            p1 = m_t1.predict_proba_one(t1_v6(r))
            p2 = m_b1.predict_proba_one(b1_v6(r))
            preds.append(((1 - p1) * (1 - p2), lab))
        preds.sort(key=lambda x: x[0])
        n = len(preds); n_q = int(n * 0.20)
        yrfi_w = sum(1 for p, y in preds[:n_q] if y == 0)
        nrfi_w = sum(1 for p, y in preds[-n_q:] if y == 1)
        yrfi_pl = yrfi_w * 0.909 - (n_q - yrfi_w)
        nrfi_pl = nrfi_w * 0.909 - (n_q - nrfi_w)
        b = sum((p - y)**2 for p, y in preds) / n
        total = nrfi_pl + yrfi_pl
        fold_pls.append(total)
        print(f"  {label:<14} | {n:>6} {b:>7.4f} | {nrfi_w:>4}-{n_q-nrfi_w:<3} ({100*nrfi_w/n_q:.1f}%) {nrfi_pl:>+6.2f}u | {yrfi_w:>4}-{n_q-yrfi_w:<3} ({100*yrfi_w/n_q:.1f}%) {yrfi_pl:>+6.2f}u | {total:>+7.2f}u")
    total_3fold = sum(fold_pls)
    print(f"  {'AGGREGATE':<14} {'':>27} {'':<23} {'':<23} | {total_3fold:>+7.2f}u")

    # 5. Replay V6 on 2026 placed bets (the 26 bets from 4/29-5/04)
    print(f"\n[5/5] V6 shadow on 2026 placed bets (apples-to-apples with V2 actual):")
    with open(PICKS_2026, encoding="utf-8") as f:
        rows_2026 = list(csv.DictReader(f))

    # Use the V6 calibrator we just built (trained on 2024+2025)
    v6_results = []
    for r in rows_2026:
        if (r.get("bet_placed") or "").upper() != "Y": continue
        graded = (r.get("graded_result") or "").upper()
        if graded not in ("WIN", "LOSS"): continue
        v2_pl = to_f(r.get("profit_loss_units"), 0.0)
        v2_side = (r.get("pick_side") or "").upper()
        v2_correct = (graded == "WIN")
        actual = v2_side if v2_correct else ("YRFI" if v2_side == "NRFI" else "NRFI")
        # V6 prediction on this row
        p1 = t1_model.predict_proba_one(t1_v6(r))
        p2 = b1_model.predict_proba_one(b1_v6(r))
        raw = (1 - p1) * (1 - p2)
        v6_p = cal_p(raw, centers, rates)
        # V6 side: simple (no threshold yet, just lean direction)
        v6_lean = "NRFI" if v6_p >= 0.5 else "YRFI"
        v6_agrees_with_v2 = (v2_side == v6_lean)
        # V6 P&L if v6 had picked v6_lean at the same DK odds
        if v6_lean == actual:
            am_str = r.get(f"market_{v6_lean.lower()}_odds") or ""
            try:
                am = int(am_str)
                v6_pl = am / 100.0 if am > 0 else 100.0 / -am
            except (ValueError, TypeError):
                v6_pl = 0.909
        else:
            v6_pl = -1.0
        v6_results.append({
            "date": r["date"], "matchup": r["away_team"]+"@"+r["home_team"],
            "v2_side": v2_side, "v2_pl": v2_pl, "v2_correct": v2_correct,
            "v6_p_nrfi": v6_p, "v6_lean": v6_lean, "v6_pl": v6_pl,
            "v6_correct": v6_lean == actual, "agree": v6_agrees_with_v2,
        })

    # Print
    print(f"\n  {'date':>10} {'match':>9} | {'v2 pick':>5} {'v2_pl':>6} {'v2 W':>4} | {'v6 P(N)':>7} {'v6 lean':>7} {'v6_pl':>6} {'v6 W':>4} | {'agree':>5}")
    print('  ' + '-' * 90)
    v2_w = v2_l = v6_w = v6_l = 0
    v2_pl_sum = v6_pl_sum = 0
    for x in v6_results:
        v2_pl_sum += x["v2_pl"]; v6_pl_sum += x["v6_pl"]
        if x["v2_correct"]: v2_w += 1
        else: v2_l += 1
        if x["v6_correct"]: v6_w += 1
        else: v6_l += 1
        print(f"  {x['date']:>10} {x['matchup']:>9} | {x['v2_side']:>5} {x['v2_pl']:>+5.2f}u {('W' if x['v2_correct'] else 'L'):>4} | {x['v6_p_nrfi']:>7.3f} {x['v6_lean']:>7} {x['v6_pl']:>+5.2f}u {('W' if x['v6_correct'] else 'L'):>4} | {('Y' if x['agree'] else 'N'):>5}")
    print('  ' + '-' * 90)
    print(f"  V2 actual:  {v2_w}-{v2_l}  P&L = {v2_pl_sum:+.2f}u")
    print(f"  V6 shadow:  {v6_w}-{v6_l}  P&L = {v6_pl_sum:+.2f}u")
    print(f"  Delta (V6 - V2): {v6_pl_sum - v2_pl_sum:+.2f}u")
    print(f"  Disagreements: {sum(1 for x in v6_results if not x['agree'])} / {len(v6_results)}")


if __name__ == "__main__":
    main()

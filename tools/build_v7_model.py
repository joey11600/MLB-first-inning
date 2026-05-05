#!/usr/bin/env python3
"""
tools/build_v7_model.py -- V7 candidate model: clean rebuild.

WHY V7
------
V2 (production):  18 features per half, multicollinear, wrong-sign weights.
V5 (pre-Statcast spec): 7 features, clean signs but narrow prob distribution.
V6 (drop-redundant attempt): still had sign flips.

V7 strategy: pick ONE strong feature per signal axis, no redundancy.

V7 FEATURES PER HALF (10 features = 20 total)
---------------------------------------------
  fi_park_nrfi_rate            (venue)
  wx_temp_c, wx_wind_kmh, wx_humidity, wx_is_dome   (4 weather)
  xera (priors-pooled in production; truepit in train)  (pitcher quality)
  whiff_pct_rank (priors-pooled)                         (strikeout ability)
  pitcher_last10_nrfi                                    (recent form)
  top3c_obp                                              (offense base-reaching)
  top3c_iso                                              (offense power)

DROPPED (vs V2):
  home_fip / away_fip       -- xera covers pitcher quality
  home_obp / away_obp       -- top3c_obp is the better signal
  top3c_slg                 -- top3c_iso is the cleaner power measure
  pitcher_last5_nrfi        -- last10 is more stable
  era_gap                   -- redundant with xera + opp_xera (we use only the
                               half's pitcher xera, no opposing pitcher feature)
  ump_nrfi_rate             -- (not in CSV anyway, was league-avg fallback)
  pvt_nrfi_rate             -- weak signal, drops noise
  avg_ip_per_start          -- weak signal, drops noise

This script:
  1. Trains V7 LR on 2024+2025 truepit (priors-pooled inputs, leak-free)
  2. Verifies signs match expected directions
  3. Runs 3-fold cross-year backtest
  4. Runs shadow on V2's actual placed bets (last 3 days + full 4/28-5/04)
  5. Saves model files data/lr_t1_v7.json + data/lr_b1_v7.json + calibration_v7.json
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

import mlb_first_inning_predictor as mod
from lr_baseline import LogReg

TRAIN_CSVS = [
    REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv",
    REPO_ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv",
]
PICKS_2026 = REPO_ROOT / "data/picks_2026.csv"

OUT_T1     = REPO_ROOT / "data/lr_t1_v7.json"
OUT_B1     = REPO_ROOT / "data/lr_b1_v7.json"
OUT_CAL    = REPO_ROOT / "data/calibration_v7.json"

LEAGUE_NRFI = 0.5246
LEAGUE_OBP  = 0.314
LEAGUE_ISO  = 0.169
LEAGUE_XERA = 4.20
NEUTRAL_WHIFF = 50.0
WX_T, WX_W, WX_H = 22.0, 8.0, 50.0


def to_f(v, d):
    if v is None or v == "":
        return d
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


T1_NAMES = [
    "fi_park_nrfi_rate",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "home_xera",
    "home_whiff_pct_rank",
    "home_p_last10_pitcher_nrfi",
    "away_top3c_obp",
    "away_top3c_iso",
]
B1_NAMES = [
    "fi_park_nrfi_rate",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "away_xera",
    "away_whiff_pct_rank",
    "away_p_last10_pitcher_nrfi",
    "home_top3c_obp",
    "home_top3c_iso",
]

T1_EXPECTED_SIGNS = {
    "fi_park_nrfi_rate":           "neg",
    "wx_temp_c":                    "pos",
    "wx_wind_kmh":                  "pos",
    "wx_humidity":                  "pos",
    "wx_is_dome":                   "neutral",
    "home_xera":                    "pos",
    "home_whiff_pct_rank":          "neg",
    "home_p_last10_pitcher_nrfi":   "neg",
    "away_top3c_obp":               "pos",
    "away_top3c_iso":               "pos",
}
B1_EXPECTED_SIGNS = {
    "fi_park_nrfi_rate":           "neg",
    "wx_temp_c":                    "pos",
    "wx_wind_kmh":                  "pos",
    "wx_humidity":                  "pos",
    "wx_is_dome":                   "neutral",
    "away_xera":                    "pos",
    "away_whiff_pct_rank":          "neg",
    "away_p_last10_pitcher_nrfi":   "neg",
    "home_top3c_obp":               "pos",
    "home_top3c_iso":               "pos",
}


def t1_v7(r):
    return [
        to_f(r.get("fi_park_nrfi_rate") or r.get("park_factor"), LEAGUE_NRFI),
        to_f(r.get("wx_temp_c"),                  WX_T),
        to_f(r.get("wx_wind_kmh"),                WX_W),
        to_f(r.get("wx_humidity"),                WX_H),
        to_f(r.get("wx_is_dome"),                 0.0),
        to_f(r.get("home_xera"),                  LEAGUE_XERA),
        to_f(r.get("home_whiff_pct_rank"),        NEUTRAL_WHIFF),
        to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
        to_f(r.get("away_top3c_iso") or r.get("away_top3_iso"), LEAGUE_ISO),
    ]


def b1_v7(r):
    return [
        to_f(r.get("fi_park_nrfi_rate") or r.get("park_factor"), LEAGUE_NRFI),
        to_f(r.get("wx_temp_c"),                  WX_T),
        to_f(r.get("wx_wind_kmh"),                WX_W),
        to_f(r.get("wx_humidity"),                WX_H),
        to_f(r.get("wx_is_dome"),                 0.0),
        to_f(r.get("away_xera"),                  LEAGUE_XERA),
        to_f(r.get("away_whiff_pct_rank"),        NEUTRAL_WHIFF),
        to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
        to_f(r.get("home_top3c_iso") or r.get("home_top3_iso"), LEAGUE_ISO),
    ]


def t1_v7_with_priors(r, h_xera_pri, h_whiff_pri):
    """For 2026 shadow: use stored values for non-Statcast features but
    OVERRIDE xera/whiff with priors-pooled values from T4.2 lookup.
    This matches what the production predictor would produce given T4.2
    is enabled."""
    return [
        to_f(r.get("park_factor") or r.get("fi_park_nrfi_rate"), LEAGUE_NRFI),
        to_f(r.get("wx_temp_c"),                  WX_T),
        to_f(r.get("wx_wind_kmh"),                WX_W),
        to_f(r.get("wx_humidity"),                WX_H),
        to_f(r.get("wx_is_dome"),                 0.0),
        h_xera_pri,
        h_whiff_pri,
        to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
        to_f(r.get("away_top3c_iso") or r.get("away_top3_iso"), LEAGUE_ISO),
    ]


def b1_v7_with_priors(r, a_xera_pri, a_whiff_pri):
    return [
        to_f(r.get("park_factor") or r.get("fi_park_nrfi_rate"), LEAGUE_NRFI),
        to_f(r.get("wx_temp_c"),                  WX_T),
        to_f(r.get("wx_wind_kmh"),                WX_W),
        to_f(r.get("wx_humidity"),                WX_H),
        to_f(r.get("wx_is_dome"),                 0.0),
        a_xera_pri,
        a_whiff_pri,
        to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
        to_f(r.get("home_top3c_iso") or r.get("home_top3_iso"), LEAGUE_ISO),
    ]


def actual_label(r):
    try:
        f = int(r.get("fi_total_runs") or -1)
        return -1 if f < 0 else (1 if f == 0 else 0)
    except (ValueError, TypeError):
        return -1


def half_label(r, side):
    """Return 1 if a run scored in this half, 0 if not, -1 if missing."""
    try:
        if side == "T1":
            v = int(r.get("fi_away_runs") or -1)
        else:
            v = int(r.get("fi_home_runs") or -1)
    except (ValueError, TypeError):
        return -1
    if v < 0:
        return -1
    return 1 if v > 0 else 0


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
    if c[hi] == c[lo]:
        return (r[lo] + r[hi]) / 2
    return r[lo] + (p - c[lo]) / (c[hi] - c[lo]) * (r[hi] - r[lo])


def amer_payout(am_str):
    try:
        am = int(am_str)
        return am / 100.0 if am > 0 else 100.0 / -am
    except (ValueError, TypeError):
        return 0.909


def main():
    print("=" * 80)
    print("  V7 BUILD: 10 features per half, no multicollinearity, priors-aware")
    print("=" * 80)

    # 1. Load training data
    print(f"\n[1/6] Loading 2024+2025 truepit training data...")
    train_rows = []
    for path in TRAIN_CSVS:
        with open(path, encoding="utf-8") as f:
            train_rows.extend(list(csv.DictReader(f)))

    Xt1, yt1 = [], []
    Xb1, yb1 = [], []
    for r in train_rows:
        lt1 = half_label(r, "T1")
        lb1 = half_label(r, "B1")
        if lt1 < 0 or lb1 < 0: continue
        Xt1.append(t1_v7(r)); yt1.append(lt1)
        Xb1.append(b1_v7(r)); yb1.append(lb1)
    print(f"      Trained on {len(Xt1)} games")
    print(f"      T1 base rate (run scored): {sum(yt1)/len(yt1):.4f}")
    print(f"      B1 base rate (run scored): {sum(yb1)/len(yb1):.4f}")

    # 2. Fit T1 + B1 LRs
    t1m = LogReg.fit(np.asarray(Xt1, dtype=float), np.asarray(yt1, dtype=float), T1_NAMES)
    b1m = LogReg.fit(np.asarray(Xb1, dtype=float), np.asarray(yb1, dtype=float), B1_NAMES)

    # 3. Verify weight signs
    print(f"\n[2/6] V7 weight sign check (target: zero sign errors):")
    sign_errors = 0
    for label, model, names, exp in [
        ("T1", t1m, T1_NAMES, T1_EXPECTED_SIGNS),
        ("B1", b1m, B1_NAMES, B1_EXPECTED_SIGNS),
    ]:
        print(f"\n  {label}:")
        for n, w in zip(names, model.w):
            expected = exp.get(n, "neutral")
            actual_sign = "pos" if w > 0.005 else "neg" if w < -0.005 else "neutral"
            ok = expected == "neutral" or expected == actual_sign
            flag = "  OK" if ok else "  FLIP"
            print(f"    {n:<35} {w:+.4f}  expected={expected:<7}  got={actual_sign:<7}{flag}")
            if not ok and expected != "neutral":
                sign_errors += 1
    print(f"\n  Total sign errors: {sign_errors}")

    # 4. Build calibrator on training raw probs vs actual NRFI labels
    print(f"\n[3/6] Fitting V7 calibrator on training NRFI predictions...")
    raw_probs, true_labels = [], []
    for r in train_rows:
        lab = actual_label(r)
        if lab < 0: continue
        p_t1 = t1m.predict_proba_one(t1_v7(r))
        p_b1 = b1m.predict_proba_one(b1_v7(r))
        raw_probs.append((1 - p_t1) * (1 - p_b1))
        true_labels.append(lab)
    centers, rates = fit_isotonic(raw_probs, true_labels, 20)
    print(f"      Calibrator: {len(centers)} bins, range [{centers[0]:.3f}, {centers[-1]:.3f}]")

    t1m.save(OUT_T1)
    b1m.save(OUT_B1)
    with open(OUT_CAL, "w", encoding="utf-8") as f:
        json.dump({
            "centers": centers, "rates": rates,
            "train_n": len(raw_probs),
            "train_seasons": ["2024", "2025"],
            "model_version": "v7",
            "feature_set": "10_per_half_no_multicollinear_priors_aware",
        }, f, indent=2)
    print(f"      Saved {OUT_T1.name}, {OUT_B1.name}, {OUT_CAL.name}")

    # 5. 3-fold cross-year backtest
    print(f"\n[4/6] V7 3-fold backtest:")
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
    print(f"  {'fold':<14} | {'top20%(NRFI)':>15} {'pl':>7} | {'bot20%(YRFI)':>15} {'pl':>7} | {'total':>8}")
    fold_total = 0.0
    for label, train_p, test_p in folds:
        with open(train_p, encoding="utf-8") as f: tr = list(csv.DictReader(f))
        with open(test_p,  encoding="utf-8") as f: te = list(csv.DictReader(f))
        Xt1f, yt1f = [], []; Xb1f, yb1f = [], []
        for r in tr:
            lt = half_label(r, "T1"); lb = half_label(r, "B1")
            if lt < 0 or lb < 0: continue
            Xt1f.append(t1_v7(r)); yt1f.append(lt)
            Xb1f.append(b1_v7(r)); yb1f.append(lb)
        ft1 = LogReg.fit(np.asarray(Xt1f, dtype=float), np.asarray(yt1f, dtype=float), T1_NAMES)
        fb1 = LogReg.fit(np.asarray(Xb1f, dtype=float), np.asarray(yb1f, dtype=float), B1_NAMES)
        preds = []
        for r in te:
            lab = actual_label(r)
            if lab < 0: continue
            p1 = ft1.predict_proba_one(t1_v7(r))
            p2 = fb1.predict_proba_one(b1_v7(r))
            preds.append(((1 - p1) * (1 - p2), lab))
        preds.sort(key=lambda x: x[0])
        n = len(preds); n_q = int(n * 0.20)
        yrfi_w = sum(1 for p, y in preds[:n_q] if y == 0)
        nrfi_w = sum(1 for p, y in preds[-n_q:] if y == 1)
        yrfi_pl = yrfi_w * 0.909 - (n_q - yrfi_w)
        nrfi_pl = nrfi_w * 0.909 - (n_q - nrfi_w)
        total = nrfi_pl + yrfi_pl
        fold_total += total
        nrfi_str = f"{nrfi_w}-{n_q-nrfi_w} ({100*nrfi_w/n_q:.1f}%)"
        yrfi_str = f"{yrfi_w}-{n_q-yrfi_w} ({100*yrfi_w/n_q:.1f}%)"
        print(f"  {label:<14} | {nrfi_str:>15} {nrfi_pl:>+6.2f}u | {yrfi_str:>15} {yrfi_pl:>+6.2f}u | {total:>+7.2f}u")
    print(f"  {'AGGREGATE':<14} {'':<54} | {fold_total:>+7.2f}u")

    # 6. V7 shadow on 2026 placed bets, with priors-pooled overrides
    print(f"\n[5/6] V7 shadow on 2026 placed bets (T4.2 priors-pooled xera/whiff):")
    for window_label, since_date, until_date in [
        ("LAST 3 DAYS",  "2026-05-02", "2026-05-04"),
        ("FULL POST-STATCAST",  "2026-04-28", "2026-05-04"),
    ]:
        print(f"\n  --- {window_label} ({since_date} to {until_date}) ---")
        v2_w = v2_l = new_w = new_l = new_pass = 0
        v2_total = new_total = 0.0
        rows_out = []
        with open(PICKS_2026, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = r.get("date") or ""
                if d < since_date or d > until_date: continue
                if (r.get("bet_placed") or "").upper() != "Y": continue
                if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"): continue
                try:
                    h_pid = int(r.get("home_pitcher_id") or 0)
                    a_pid = int(r.get("away_pitcher_id") or 0)
                except (ValueError, TypeError):
                    continue
                # T4.2 priors-pooled values
                h_sc = mod.fetch_pitcher_statcast(h_pid, 2026, date_iso=d) if h_pid else {"xera": LEAGUE_XERA, "whiff_pct_rank": NEUTRAL_WHIFF}
                a_sc = mod.fetch_pitcher_statcast(a_pid, 2026, date_iso=d) if a_pid else {"xera": LEAGUE_XERA, "whiff_pct_rank": NEUTRAL_WHIFF}
                # V7 features with priors-pooled
                f_t1 = t1_v7_with_priors(r, h_sc["xera"], h_sc["whiff_pct_rank"])
                f_b1 = b1_v7_with_priors(r, a_sc["xera"], a_sc["whiff_pct_rank"])
                p_t1 = t1m.predict_proba_one(f_t1)
                p_b1 = b1m.predict_proba_one(f_b1)
                raw = (1 - p_t1) * (1 - p_b1)
                new_p = cal_p(raw, centers, rates)
                if new_p >= 0.56:
                    new_pick = "NRFI"
                elif new_p <= 0.44:
                    new_pick = "YRFI"
                else:
                    new_pick = "PASS"

                v2_side = (r.get("pick_side") or "").upper()
                graded = r.get("graded_result", "").upper()
                v2_correct = graded == "WIN"
                actual = v2_side if v2_correct else ("YRFI" if v2_side == "NRFI" else "NRFI")
                v2_pl = to_f(r.get("profit_loss_units"), 0.0)

                if new_pick == "PASS":
                    new_pl = 0.0; new_oc = "PASS"; new_pass += 1
                elif new_pick == actual:
                    new_pl = amer_payout(r.get(f"market_{new_pick.lower()}_odds") or "")
                    new_oc = "WIN"; new_w += 1
                else:
                    new_pl = -1.0; new_oc = "LOSS"; new_l += 1

                v2_total += v2_pl; new_total += new_pl
                if v2_correct: v2_w += 1
                else: v2_l += 1

                rows_out.append({
                    "d": d, "match": r["away_team"]+"@"+r["home_team"],
                    "v2": v2_side, "v2_p": to_f(r.get("nrfi_prob"), 0.5),
                    "v2_pl": v2_pl, "v2_oc": "W" if v2_correct else "L",
                    "v7": new_pick, "v7_p": new_p, "v7_pl": new_pl,
                    "v7_oc": new_oc[0],
                })

        # Print detailed breakdown
        print(f"    {'date':>10} {'match':>9} | {'v2':>5} {'v2_p':>5} {'pl':>6} {'oc':>2} | {'v7':>5} {'v7_p':>5} {'pl':>6} {'oc':>3}")
        for x in rows_out:
            print(f"    {x['d']:>10} {x['match']:>9} | {x['v2']:>5} {x['v2_p']:>5.3f} {x['v2_pl']:>+5.2f}u {x['v2_oc']:>2} | {x['v7']:>5} {x['v7_p']:>5.3f} {x['v7_pl']:>+5.2f}u {x['v7_oc']:>3}")
        print(f"\n    V2 ACTUAL: {v2_w}-{v2_l}  P&L = {v2_total:+.2f}u  ({v2_w + v2_l} bets)")
        print(f"    V7 SHADOW: {new_w}-{new_l} ({new_pass} PASS)  P&L = {new_total:+.2f}u")
        print(f"    Delta:     {new_total - v2_total:+.2f}u")


if __name__ == "__main__":
    main()

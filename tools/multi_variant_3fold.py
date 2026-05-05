#!/usr/bin/env python3
"""
tools/multi_variant_3fold.py -- 3-fold leak-free walk-forward.

T4.3 model rebuild diligence.  After the original 5-variant LR test on
2024->2025 showed `last10_minimal` returning +82u (overfit cherry-pick;
3-fold aggregate is -143u), we need to test a wider set of architectures
to know if ANY model family is consistently +EV across 2022->2023,
2023->2024, 2024->2025.

If no LR-family variant generalizes, that confirms cross-year LR
transfer is broken and we need a different architecture entirely
(market-edge, sliding-window, GBM with regularization).

VARIANTS
--------
LR family:
  prod_full          : 18 features per half, current production spec
  last10_minimal     : 5 features per half, user's last10 hypothesis
  L1_LR_prod         : prod features with L1 regularization (sparse)
  recency_weighted   : prod features, weight recent games 2x

Tree-based:
  GBM_default        : sklearn GBM, n_est=100, depth=3
  GBM_shallow        : sklearn GBM, n_est=50, depth=2 (less overfit)

Sanity / lower bound:
  base_rate_only     : predict the 14-day rolling NRFI rate.  No features.

Sliding window (only run if any of the above shows promise):
  sliding_60d_LR     : train on rolling 60 days of test year, refit daily.

For each variant on each fold, we report:
  - n test rows usable
  - Brier (lower better; climatology = base*(1-base))
  - Top-20% (NRFI zone) hit rate, P&L at -110, ROI
  - Bottom-20% (YRFI zone) hit rate, P&L at -110, ROI
  - Total P&L

Then we aggregate across the 3 folds to find any consistent winner.

USAGE
-----
  python tools/multi_variant_3fold.py
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lr_baseline import LogReg

# 3 folds.  truepit CSVs are leak-free for 2024 + 2025 (xera/whiff use
# prior-season aggregate).  2022 and 2023 are pre-leak-fix originals;
# we use them only with feature builders that don't read xera/whiff.
FOLDS = [
    ("2022 -> 2023",
     REPO_ROOT / "data/backtests/backtest_2022-04-01_to_2022-09-30.csv",
     REPO_ROOT / "data/backtests/backtest_2023-04-01_to_2023-09-30.csv"),
    ("2023 -> 2024_truepit",
     REPO_ROOT / "data/backtests/backtest_2023-04-01_to_2023-09-30.csv",
     REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv"),
    ("2024_truepit -> 2025_truepit",
     REPO_ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv",
     REPO_ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv"),
]

LEAGUE_NRFI = 0.5246
LEAGUE_OBP  = 0.314
LEAGUE_SLG  = 0.402
LEAGUE_ERA  = 4.10
LEAGUE_FIP  = 4.10
LEAGUE_XERA = 4.20
LEAGUE_ISO  = 0.169
WX_T, WX_W, WX_H = 22.0, 8.0, 50.0
NEUTRAL_WHIFF = 50.0


def to_f(v, d):
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


def actual_label(r):
    try:
        f = int(r.get("fi_total_runs") or -1)
        return -1 if f < 0 else (1 if f == 0 else 0)
    except (ValueError, TypeError):
        return -1


def read_rows(p):
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------
# Feature builders
# ---------------------------------------------------------------------

def build_prod_full(r):
    park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
    t1 = [
        park,
        to_f(r.get("home_fip_blend") or r.get("home_fip"), LEAGUE_FIP),
        to_f(r.get("away_obp"), LEAGUE_OBP),
        to_f(r.get("wx_temp_c"), WX_T), to_f(r.get("wx_wind_kmh"), WX_W),
        to_f(r.get("wx_humidity"), WX_H), to_f(r.get("wx_is_dome"), 0.0),
        to_f(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
        LEAGUE_NRFI,    # ump_rate not in CSV
        to_f(r.get("home_xera"), LEAGUE_XERA),
        to_f(r.get("home_whiff_pct_rank"), NEUTRAL_WHIFF),
        to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_ERA)
          - to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_ERA),
        to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("away_top3c_slg") or r.get("away_top3_slg"), LEAGUE_SLG),
        to_f(r.get("away_top3c_iso") or r.get("away_top3_iso"), LEAGUE_ISO),
        to_f(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI),
        to_f(r.get("home_avg_ip_per_start"), 5.0),
    ]
    b1 = [
        park,
        to_f(r.get("away_fip_blend") or r.get("away_fip"), LEAGUE_FIP),
        to_f(r.get("home_obp"), LEAGUE_OBP),
        to_f(r.get("wx_temp_c"), WX_T), to_f(r.get("wx_wind_kmh"), WX_W),
        to_f(r.get("wx_humidity"), WX_H), to_f(r.get("wx_is_dome"), 0.0),
        to_f(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
        LEAGUE_NRFI,
        to_f(r.get("away_xera"), LEAGUE_XERA),
        to_f(r.get("away_whiff_pct_rank"), NEUTRAL_WHIFF),
        to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_ERA)
          - to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_ERA),
        to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("home_top3c_slg") or r.get("home_top3_slg"), LEAGUE_SLG),
        to_f(r.get("home_top3c_iso") or r.get("home_top3_iso"), LEAGUE_ISO),
        to_f(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI),
        to_f(r.get("away_avg_ip_per_start"), 5.0),
    ]
    return t1 + b1


def build_last10_minimal(r):
    park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
    return [
        park,
        to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
        to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
    ]


def build_no_xera_whiff(r):
    """For 2022/2023 CSVs which lack xera/whiff -- prod_full minus those features.
    Symmetric for 2024_truepit/2025_truepit too so the SAME builder works on
    every fold (cross-year fair comparison)."""
    full = build_prod_full(r)
    # T1 indices 0-17, drop xera (10), whiff (11): keep 0..9 + 12..17
    t1 = full[0:10] + full[12:18]
    b1 = full[18+0:18+10] + full[18+12:18+18]
    return t1 + b1


# ---------------------------------------------------------------------
# Feature data prep
# ---------------------------------------------------------------------

def build_xy(rows, builder):
    X, y = [], []
    for r in rows:
        feats = builder(r)
        lab = actual_label(r)
        if feats is None or lab < 0:
            continue
        X.append(feats)
        y.append(lab)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


# ---------------------------------------------------------------------
# Variant: LR (production logreg with L2)
# ---------------------------------------------------------------------

def variant_lr(train_rows, test_rows, builder, name):
    Xtr, ytr = build_xy(train_rows, builder)
    Xte, yte = build_xy(test_rows,  builder)
    if Xtr.shape[0] == 0 or Xte.shape[0] == 0:
        return None
    feat_names = [f"{name}_f{i}" for i in range(Xtr.shape[1])]
    m = LogReg.fit(Xtr, ytr, feat_names)
    preds = [(m.predict_proba_one(list(Xte[i])), int(yte[i])) for i in range(Xte.shape[0])]
    return preds


# ---------------------------------------------------------------------
# Variant: L1-regularized LR via sklearn
# ---------------------------------------------------------------------

def variant_l1_lr(train_rows, test_rows, builder):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    Xtr, ytr = build_xy(train_rows, builder)
    Xte, yte = build_xy(test_rows,  builder)
    if Xtr.shape[0] == 0 or Xte.shape[0] == 0:
        return None
    sc = StandardScaler().fit(Xtr)
    Xtr_n = sc.transform(Xtr)
    Xte_n = sc.transform(Xte)
    # L1 with strong regularization to encourage sparsity.
    m = LogisticRegression(penalty="l1", solver="saga", C=0.5, max_iter=2000)
    m.fit(Xtr_n, ytr.astype(int))
    p = m.predict_proba(Xte_n)[:, 1]
    return [(float(p[i]), int(yte[i])) for i in range(len(p))]


# ---------------------------------------------------------------------
# Variant: recency-weighted LR (weight recent games more)
# ---------------------------------------------------------------------

def variant_recency_lr(train_rows, test_rows, builder):
    """Sort train rows by date, weight more recent games higher.
    Uses sklearn's sample_weight."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    rows = sorted(train_rows, key=lambda r: r.get("date", ""))
    X, y, w = [], [], []
    n = len(rows)
    for i, r in enumerate(rows):
        feats = builder(r)
        lab = actual_label(r)
        if feats is None or lab < 0:
            continue
        X.append(feats)
        y.append(lab)
        # Linear weight 0.5 -> 1.5: most-recent games weight 3x oldest.
        w.append(0.5 + 1.0 * (i / max(1, n - 1)))
    Xtr = np.asarray(X, dtype=float)
    ytr = np.asarray(y, dtype=float)
    wtr = np.asarray(w, dtype=float)
    Xte, yte = build_xy(test_rows, builder)
    if Xtr.shape[0] == 0 or Xte.shape[0] == 0:
        return None
    sc = StandardScaler().fit(Xtr)
    Xtr_n = sc.transform(Xtr)
    Xte_n = sc.transform(Xte)
    m = LogisticRegression(penalty="l2", C=1.0, max_iter=2000)
    m.fit(Xtr_n, ytr.astype(int), sample_weight=wtr)
    p = m.predict_proba(Xte_n)[:, 1]
    return [(float(p[i]), int(yte[i])) for i in range(len(p))]


# ---------------------------------------------------------------------
# Variant: GBM
# ---------------------------------------------------------------------

def variant_gbm(train_rows, test_rows, builder, n_est=100, depth=3):
    from sklearn.ensemble import GradientBoostingClassifier
    Xtr, ytr = build_xy(train_rows, builder)
    Xte, yte = build_xy(test_rows,  builder)
    if Xtr.shape[0] == 0 or Xte.shape[0] == 0:
        return None
    m = GradientBoostingClassifier(
        n_estimators=n_est, max_depth=depth, learning_rate=0.05,
        subsample=0.8, random_state=0,
    )
    m.fit(Xtr, ytr.astype(int))
    p = m.predict_proba(Xte)[:, 1]
    return [(float(p[i]), int(yte[i])) for i in range(len(p))]


# ---------------------------------------------------------------------
# Variant: base-rate only (sanity check)
# ---------------------------------------------------------------------

def variant_base_rate(train_rows, test_rows):
    """Predict the train base rate of NRFI for every test row.  Lower
    bound -- if anything beats this, we've found signal."""
    labels = [actual_label(r) for r in train_rows if actual_label(r) >= 0]
    if not labels:
        return None
    base = sum(labels) / len(labels)
    out = []
    for r in test_rows:
        lab = actual_label(r)
        if lab < 0:
            continue
        out.append((base, lab))
    return out


# ---------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------

def evaluate(preds, q=0.20, vig=-110):
    if not preds or len(preds) < 50:
        return None
    sorted_preds = sorted(preds, key=lambda x: x[0])
    n = len(sorted_preds)
    n_q = int(n * q)
    bottom = sorted_preds[:n_q]
    top    = sorted_preds[-n_q:]
    yrfi_w = sum(1 for _, y in bottom if y == 0)
    nrfi_w = sum(1 for _, y in top    if y == 1)
    yrfi_pl = yrfi_w * 0.909 - (n_q - yrfi_w) * 1.0
    nrfi_pl = nrfi_w * 0.909 - (n_q - nrfi_w) * 1.0
    base = sum(y for _, y in preds) / n
    b = sum((p - y) ** 2 for p, y in preds) / n
    skill = 100.0 * (1 - b / (base * (1 - base))) if base > 0 and base < 1 else 0.0
    return {
        "n":           n,
        "brier":       b,
        "skill":       skill,
        "n_q":         n_q,
        "yrfi_w":      yrfi_w,
        "yrfi_pct":    100.0 * yrfi_w / n_q if n_q else 0,
        "yrfi_pl":     yrfi_pl,
        "yrfi_roi":    100.0 * yrfi_pl / n_q if n_q else 0,
        "nrfi_w":      nrfi_w,
        "nrfi_pct":    100.0 * nrfi_w / n_q if n_q else 0,
        "nrfi_pl":     nrfi_pl,
        "nrfi_roi":    100.0 * nrfi_pl / n_q if n_q else 0,
        "total_pl":    nrfi_pl + yrfi_pl,
        "total_roi":   100.0 * (nrfi_pl + yrfi_pl) / (2 * n_q) if n_q else 0,
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print("=" * 100)
    print("  T4.3 MULTI-VARIANT 3-FOLD LEAK-FREE BACKTEST")
    print("=" * 100)

    # Variants we'll run on each fold.  Each entry: (name, run_fn).
    # run_fn takes (train_rows, test_rows) and returns list of (p, y) tuples.
    variants = [
        ("prod_full_LR",           lambda tr, te: variant_lr(tr, te, build_no_xera_whiff, "prod_no_e3")),
        ("last10_minimal_LR",      lambda tr, te: variant_lr(tr, te, build_last10_minimal, "l10")),
        ("L1_LR_prod",             lambda tr, te: variant_l1_lr(tr, te, build_no_xera_whiff)),
        ("recency_LR",             lambda tr, te: variant_recency_lr(tr, te, build_no_xera_whiff)),
        ("GBM_default",            lambda tr, te: variant_gbm(tr, te, build_no_xera_whiff, 100, 3)),
        ("GBM_shallow",            lambda tr, te: variant_gbm(tr, te, build_no_xera_whiff, 50, 2)),
        ("base_rate_only",         lambda tr, te: variant_base_rate(tr, te)),
    ]

    # Per-variant aggregator: total P&L across folds, total bets across folds
    aggr = {name: {"pl": 0.0, "n_q": 0, "fold_pls": []} for name, _ in variants}

    for fold_label, train_p, test_p in FOLDS:
        print(f"\n--- FOLD: {fold_label} ---")
        train_rows = read_rows(train_p)
        test_rows  = read_rows(test_p)
        print(f"  train={len(train_rows)} test={len(test_rows)}")

        rows_for_table = []
        for name, run in variants:
            try:
                preds = run(train_rows, test_rows)
            except Exception as exc:
                print(f"    [{name}] ERROR: {exc}")
                continue
            if preds is None:
                continue
            ev = evaluate(preds)
            if ev is None:
                continue
            rows_for_table.append((name, ev))
            aggr[name]["pl"]   += ev["total_pl"]
            aggr[name]["n_q"] += 2 * ev["n_q"]
            aggr[name]["fold_pls"].append(ev["total_pl"])

        # Per-fold table
        print(f"\n    {'variant':<20} {'n_test':>6} {'brier':>7} {'skill%':>7} | {'NRFI':>13} {'pl':>7} {'roi%':>6} | {'YRFI':>13} {'pl':>7} {'roi%':>6} | {'total':>8}")
        for name, ev in rows_for_table:
            nrfi_str = f"{ev['nrfi_w']}-{ev['n_q']-ev['nrfi_w']} ({ev['nrfi_pct']:.1f}%)"
            yrfi_str = f"{ev['yrfi_w']}-{ev['n_q']-ev['yrfi_w']} ({ev['yrfi_pct']:.1f}%)"
            print(
                f"    {name:<20} {ev['n']:>6} {ev['brier']:>7.4f} {ev['skill']:>+6.2f}% | "
                f"{nrfi_str:>13} {ev['nrfi_pl']:>+6.2f}u {ev['nrfi_roi']:>+5.1f}% | "
                f"{yrfi_str:>13} {ev['yrfi_pl']:>+6.2f}u {ev['yrfi_roi']:>+5.1f}% | "
                f"{ev['total_pl']:>+7.2f}u"
            )

    # Aggregate summary
    print()
    print("=" * 100)
    print("  3-FOLD AGGREGATE  (sum across all 3 folds; positive ROI = robust signal)")
    print("=" * 100)
    print(f"\n{'variant':<20} {'fold1':>9} {'fold2':>9} {'fold3':>9} | {'total_pl':>10} {'tot_roi%':>9} | {'verdict':<30}")
    print("-" * 100)
    # Sort by total P&L desc
    sorted_aggr = sorted(aggr.items(), key=lambda kv: -kv[1]["pl"])
    for name, agg in sorted_aggr:
        if not agg["fold_pls"]:
            continue
        f1 = agg["fold_pls"][0] if len(agg["fold_pls"]) > 0 else 0
        f2 = agg["fold_pls"][1] if len(agg["fold_pls"]) > 1 else 0
        f3 = agg["fold_pls"][2] if len(agg["fold_pls"]) > 2 else 0
        roi = 100.0 * agg["pl"] / agg["n_q"] if agg["n_q"] else 0
        # Verdict: how many folds positive?
        n_pos = sum(1 for p in agg["fold_pls"] if p > 0)
        if roi > 2.4 and n_pos >= 2:
            verdict = f"ROBUST (+EV in {n_pos}/3 folds)"
        elif roi > 0:
            verdict = f"weak +EV ({n_pos}/3 folds positive)"
        elif n_pos == 0:
            verdict = "broken (negative every fold)"
        else:
            verdict = f"variance / no signal ({n_pos}/3)"
        print(f"{name:<20} {f1:>+8.2f}u {f2:>+8.2f}u {f3:>+8.2f}u | {agg['pl']:>+9.2f}u {roi:>+8.2f}% | {verdict:<30}")


if __name__ == "__main__":
    main()

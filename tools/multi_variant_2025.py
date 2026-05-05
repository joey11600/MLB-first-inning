#!/usr/bin/env python3
"""
tools/multi_variant_2025.py -- compare model variants on 2024 -> 2025 walk-forward.

USER ASK (2026-05-04):  We're losing.  Production v2 LR on 5 weeks of 2026
shows -2u to +0.7u once leaky cache is removed.  Sample is too small to
know if the model has any signal.  Test multiple variants in parallel on
the leak-free truepit corpus (priors-pooled xera/whiff, prior-season fall
back) to see if ANY variant is genuinely +EV, or if we need to rebuild
from scratch.

USER HYPOTHESIS:  Pitcher's last-10 NRFI rate matters a lot.  Several
variants below center on that.

VARIANTS
--------
A.  prod_full          -- baseline: 18 features per half, the current
                          production model spec (truepit-aware xera/whiff,
                          fi_park, fip, opp_obp, weather, last5/last10_pitcher,
                          top3 obp/slg/iso, ump_rate, era_gap, pvt_nrfi,
                          avg_ip_per_start)
B.  last10_minimal     -- USER: just home/away last10_pitcher_nrfi + park +
                          opposing top3 obp.  Five features per half.
C.  last10_plus        -- USER+: last10_pitcher_nrfi-heavy: last10 + last5 +
                          fi_park + top3_obp + top3_slg + ump_rate.
D.  slim_weather       -- From T3.11-AUDIT: only the leak-immune feature set
                          (no Statcast).  Pre-existing pass on leak-free WF.
E.  no_statcast        -- prod_full minus xera/whiff.  Tests whether Statcast
                          adds signal once leakage is gone.
F.  naive_last10_rules -- not LR: pick NRFI iff avg(last10_home, last10_away)
                          >= 0.55, YRFI iff <= 0.45, else PASS.  No fit.

For each variant, on the test fold:
  - Brier score (lower = better; climatology Brier ~ p*(1-p))
  - top-20% hit rate (proxy for STRONG NRFI)
  - bottom-20% YRFI hit rate (proxy for STRONG YRFI)
  - implied ROI at -110 vig in the top/bottom quintiles

USAGE
-----
  python tools/multi_variant_2025.py
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from lr_baseline import LogReg

TRAIN_CSV = REPO_ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"
TEST_CSV  = REPO_ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"

LEAGUE_NRFI = 0.5246
LEAGUE_OBP  = 0.314
LEAGUE_SLG  = 0.402
LEAGUE_ISO  = 0.169
LEAGUE_ERA  = 4.10
LEAGUE_FIP  = 4.10
LEAGUE_XERA = 4.20
WX_TEMP_DEFAULT  = 22.0
WX_WIND_DEFAULT  = 8.0
WX_HUM_DEFAULT   = 50.0
NEUTRAL_WHIFF_RANK = 50.0


def to_f(v, d):
    try:
        f = float(v)
        if math.isnan(f):
            return d
        return f
    except (ValueError, TypeError):
        return d


def to_i(v, d):
    try:
        return int(v)
    except (ValueError, TypeError):
        return d


def actual_label(row) -> int:
    """Return 1 if NRFI (no first-inning runs), 0 if YRFI."""
    fi = to_i(row.get("fi_total_runs"), -1)
    if fi < 0:
        return -1   # missing -> drop
    return 1 if fi == 0 else 0


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"Missing {path}")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# -----------------------------------------------------------------------
# Feature builders.  Each returns a 1D feature vector (just the features,
# no label), or None if the row is missing required fields.
# -----------------------------------------------------------------------

def build_prod_full(r: dict) -> list[float] | None:
    """All 18 features per half = 36 features total (T1+B1 stacked)."""
    try:
        park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
        # T1: home pitcher, away offense
        t1 = [
            park,
            to_f(r.get("home_fip_blend") or r.get("home_fip"), LEAGUE_FIP),
            to_f(r.get("away_obp"), LEAGUE_OBP),
            to_f(r.get("wx_temp_c"), WX_TEMP_DEFAULT),
            to_f(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
            to_f(r.get("wx_humidity"), WX_HUM_DEFAULT),
            to_f(r.get("wx_is_dome"), 0.0),
            to_f(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
            LEAGUE_NRFI,  # ump_rate not in CSV
            to_f(r.get("home_xera"), LEAGUE_XERA),
            to_f(r.get("home_whiff_pct_rank"), NEUTRAL_WHIFF_RANK),
            to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_ERA)
              - to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_ERA),
            to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("away_top3c_slg") or r.get("away_top3_slg"), LEAGUE_SLG),
            to_f(r.get("away_top3c_iso") or r.get("away_top3_iso"), LEAGUE_ISO),
            to_f(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI),
            to_f(r.get("home_avg_ip_per_start"), 5.0),
        ]
        # B1: away pitcher, home offense
        b1 = [
            park,
            to_f(r.get("away_fip_blend") or r.get("away_fip"), LEAGUE_FIP),
            to_f(r.get("home_obp"), LEAGUE_OBP),
            to_f(r.get("wx_temp_c"), WX_TEMP_DEFAULT),
            to_f(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
            to_f(r.get("wx_humidity"), WX_HUM_DEFAULT),
            to_f(r.get("wx_is_dome"), 0.0),
            to_f(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
            LEAGUE_NRFI,
            to_f(r.get("away_xera"), LEAGUE_XERA),
            to_f(r.get("away_whiff_pct_rank"), NEUTRAL_WHIFF_RANK),
            to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_ERA)
              - to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_ERA),
            to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("home_top3c_slg") or r.get("home_top3_slg"), LEAGUE_SLG),
            to_f(r.get("home_top3c_iso") or r.get("home_top3_iso"), LEAGUE_ISO),
            to_f(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI),
            to_f(r.get("away_avg_ip_per_start"), 5.0),
        ]
        return t1 + b1
    except Exception:    # noqa: BLE001
        return None


def build_last10_minimal(r: dict) -> list[float] | None:
    """USER hypothesis: last10 + park + top3_obp.  5 features per half."""
    try:
        park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
        h_l10 = to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI)
        a_l10 = to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI)
        a_obp = to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP)
        h_obp = to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP)
        return [park, h_l10, a_obp, a_l10, h_obp]
    except Exception:    # noqa: BLE001
        return None


def build_last10_plus(r: dict) -> list[float] | None:
    """USER+: last10 + last5 + park + top3_obp/slg.  10 features."""
    try:
        park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
        h_l10 = to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI)
        h_l5  = to_f(r.get("home_p_last5_pitcher_nrfi"),  LEAGUE_NRFI)
        a_l10 = to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI)
        a_l5  = to_f(r.get("away_p_last5_pitcher_nrfi"),  LEAGUE_NRFI)
        a_obp = to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP)
        h_obp = to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP)
        a_slg = to_f(r.get("away_top3c_slg") or r.get("away_top3_slg"), LEAGUE_SLG)
        h_slg = to_f(r.get("home_top3c_slg") or r.get("home_top3_slg"), LEAGUE_SLG)
        return [park, h_l10, h_l5, a_obp, a_slg, a_l10, a_l5, h_obp, h_slg, park]
    except Exception:    # noqa: BLE001
        return None


def build_slim_weather(r: dict) -> list[float] | None:
    """T3.11-AUDIT slim_weather: no Statcast.  14 features per half."""
    try:
        park = to_f(r.get("fi_park_nrfi_rate"), LEAGUE_NRFI)
        t1 = [
            park,
            to_f(r.get("home_fip_blend") or r.get("home_fip"), LEAGUE_FIP),
            to_f(r.get("away_obp"), LEAGUE_OBP),
            to_f(r.get("wx_temp_c"), WX_TEMP_DEFAULT),
            to_f(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
            to_f(r.get("wx_humidity"), WX_HUM_DEFAULT),
            to_f(r.get("wx_is_dome"), 0.0),
            to_f(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("away_top3c_obp") or r.get("away_top3_obp"), LEAGUE_OBP),
            to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_ERA)
              - to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_ERA),
            to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("away_top3c_slg") or r.get("away_top3_slg"), LEAGUE_SLG),
            to_f(r.get("away_top3c_iso") or r.get("away_top3_iso"), LEAGUE_ISO),
            to_f(r.get("home_avg_ip_per_start"), 5.0),
        ]
        b1 = [
            park,
            to_f(r.get("away_fip_blend") or r.get("away_fip"), LEAGUE_FIP),
            to_f(r.get("home_obp"), LEAGUE_OBP),
            to_f(r.get("wx_temp_c"), WX_TEMP_DEFAULT),
            to_f(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
            to_f(r.get("wx_humidity"), WX_HUM_DEFAULT),
            to_f(r.get("wx_is_dome"), 0.0),
            to_f(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("home_top3c_obp") or r.get("home_top3_obp"), LEAGUE_OBP),
            to_f(r.get("away_era_blend") or r.get("away_era"), LEAGUE_ERA)
              - to_f(r.get("home_era_blend") or r.get("home_era"), LEAGUE_ERA),
            to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
            to_f(r.get("home_top3c_slg") or r.get("home_top3_slg"), LEAGUE_SLG),
            to_f(r.get("home_top3c_iso") or r.get("home_top3_iso"), LEAGUE_ISO),
            to_f(r.get("away_avg_ip_per_start"), 5.0),
        ]
        return t1 + b1
    except Exception:    # noqa: BLE001
        return None


def build_no_statcast(r: dict) -> list[float] | None:
    """Strip xera+whiff from prod_full.  Tests if Statcast adds signal at all."""
    full = build_prod_full(r)
    if full is None:
        return None
    # T1 features at indices 0..17, B1 at 18..35.  Drop xera (10), whiff (11)
    # within each half.
    t1 = full[0:10] + full[12:18]
    b1 = full[18+0:18+10] + full[18+12:18+18]
    return t1 + b1


def naive_last10_pred(r: dict) -> float | None:
    """Non-LR: predict P(NRFI) = avg(home_l10, away_l10).  Returns None if missing."""
    h_l10 = to_f(r.get("home_p_last10_pitcher_nrfi"), -1)
    a_l10 = to_f(r.get("away_p_last10_pitcher_nrfi"), -1)
    if h_l10 < 0 or a_l10 < 0:
        return None
    return (h_l10 + a_l10) / 2.0


# -----------------------------------------------------------------------
# Variant runner
# -----------------------------------------------------------------------

LR_VARIANTS = {
    "prod_full":         build_prod_full,
    "last10_minimal":    build_last10_minimal,
    "last10_plus":       build_last10_plus,
    "slim_weather":      build_slim_weather,
    "no_statcast":       build_no_statcast,
}


def fit_lr(train_rows, builder, name):
    X, y = [], []
    for r in train_rows:
        feats = builder(r)
        label = actual_label(r)
        if feats is None or label < 0:
            continue
        X.append(feats)
        y.append(label)
    if not X:
        return None
    Xn = np.asarray(X, dtype=float)
    yn = np.asarray(y, dtype=float)
    feat_names = [f"{name}_f{i}" for i in range(Xn.shape[1])]
    return LogReg.fit(Xn, yn, feat_names)


def predict_lr(test_rows, builder, model):
    """Return list of (probabilistic_p_nrfi, actual_label, row) for test rows."""
    out = []
    for r in test_rows:
        feats = builder(r)
        label = actual_label(r)
        if feats is None or label < 0:
            continue
        p = model.predict_proba_one(feats)
        out.append((p, label, r))
    return out


def predict_naive(test_rows):
    out = []
    for r in test_rows:
        p = naive_last10_pred(r)
        label = actual_label(r)
        if p is None or label < 0:
            continue
        out.append((p, label, r))
    return out


# -----------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------

def brier(preds):
    if not preds:
        return float("nan")
    return sum((p - y) ** 2 for p, y, _ in preds) / len(preds)


def quantile_metrics(preds, q_lo=0.20, q_hi=0.80, vig=-110):
    """For top-q_hi (predicted high P(NRFI) -> NRFI bets) and
    bottom-q_lo (predicted low P(NRFI) -> YRFI bets) quintiles, report
    bet count, hit rate, units P&L at -110 vig (1u to win 0.909u)."""
    if not preds:
        return {}
    sorted_preds = sorted(preds, key=lambda x: x[0])
    n = len(sorted_preds)
    n_lo = int(n * q_lo)
    n_hi = int(n * (1.0 - q_hi))
    bottom = sorted_preds[:n_lo]    # lowest P(NRFI) -> YRFI bets
    top    = sorted_preds[-n_hi:] if n_hi > 0 else []  # highest P(NRFI) -> NRFI bets

    def _zone_pl(picks, pick_side):
        # pick_side=1: NRFI (win when label=1).  pick_side=0: YRFI (win when label=0).
        if not picks:
            return (0, 0, 0, 0.0)
        wins = sum(1 for _, y, _ in picks if y == pick_side)
        losses = len(picks) - wins
        # Decimal odds at -110 = 1.909, profit per win = 0.909, loss = -1
        pl = wins * 0.909 - losses * 1.0
        return (len(picks), wins, losses, pl)

    n_yrfi, w_yrfi, l_yrfi, pl_yrfi = _zone_pl(bottom, 0)
    n_nrfi, w_nrfi, l_nrfi, pl_nrfi = _zone_pl(top,    1)

    return {
        "n_yrfi_zone":   n_yrfi,
        "yrfi_w":        w_yrfi,
        "yrfi_l":        l_yrfi,
        "yrfi_hit_pct":  100.0 * w_yrfi / n_yrfi if n_yrfi else 0,
        "yrfi_pl":       pl_yrfi,
        "yrfi_roi":      100.0 * pl_yrfi / n_yrfi if n_yrfi else 0,
        "n_nrfi_zone":   n_nrfi,
        "nrfi_w":        w_nrfi,
        "nrfi_l":        l_nrfi,
        "nrfi_hit_pct":  100.0 * w_nrfi / n_nrfi if n_nrfi else 0,
        "nrfi_pl":       pl_nrfi,
        "nrfi_roi":      100.0 * pl_nrfi / n_nrfi if n_nrfi else 0,
        "total_pl":      pl_yrfi + pl_nrfi,
        "total_n":       n_yrfi + n_nrfi,
    }


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main():
    print("=" * 90)
    print("  MULTI-VARIANT WALK-FORWARD: train 2024 -> test 2025  (truepit/leak-free)")
    print("=" * 90)

    train_rows = read_rows(TRAIN_CSV)
    test_rows  = read_rows(TEST_CSV)
    print(f"\n  Train: {len(train_rows)} rows from {TRAIN_CSV.name}")
    print(f"  Test:  {len(test_rows)} rows from {TEST_CSV.name}")

    # Climatology baseline
    test_labels = [actual_label(r) for r in test_rows if actual_label(r) >= 0]
    base_rate   = sum(test_labels) / len(test_labels)
    base_brier  = base_rate * (1 - base_rate)
    print(f"\n  Test 2025 NRFI rate: {base_rate:.4f}   Climatology Brier: {base_brier:.4f}")

    # Run each LR variant
    results = {}
    for name, builder in LR_VARIANTS.items():
        m = fit_lr(train_rows, builder, name)
        if m is None:
            print(f"\n  [{name}] failed to fit")
            continue
        preds = predict_lr(test_rows, builder, m)
        b = brier(preds)
        skill = 100.0 * (1 - b / base_brier)
        q = quantile_metrics(preds)
        results[name] = {"brier": b, "brier_skill": skill, "n": len(preds), **q}

    # Naive non-LR
    npreds = predict_naive(test_rows)
    nb = brier(npreds)
    nskill = 100.0 * (1 - nb / base_brier)
    nq = quantile_metrics(npreds)
    results["naive_last10_avg"] = {"brier": nb, "brier_skill": nskill, "n": len(npreds), **nq}

    # Print comparison
    print(f"\n{'variant':<22} {'n':>5} {'brier':>7} {'skill%':>7} | {'top20%(NRFI)':>15} {'pl@-110':>9} {'roi%':>6} | {'bot20%(YRFI)':>15} {'pl@-110':>9} {'roi%':>6} | {'tot_pl':>7}")
    print('-' * 130)
    for name, m in results.items():
        nrfi = f"{m['nrfi_w']}-{m['nrfi_l']} ({m['nrfi_hit_pct']:.1f}%)"
        yrfi = f"{m['yrfi_w']}-{m['yrfi_l']} ({m['yrfi_hit_pct']:.1f}%)"
        print(
            f"{name:<22} {m['n']:>5} {m['brier']:>7.4f} {m['brier_skill']:>+6.2f}% | "
            f"{nrfi:>15} {m['nrfi_pl']:>+8.2f}u {m['nrfi_roi']:>+5.1f}% | "
            f"{yrfi:>15} {m['yrfi_pl']:>+8.2f}u {m['yrfi_roi']:>+5.1f}% | "
            f"{m['total_pl']:>+6.2f}u"
        )
    print()
    # Highlight any clearly +EV variants (ROI > +2% in either zone)
    print("Verdict (true +EV requires ROI > +2.4% to beat -110 vig with confidence):")
    for name, m in results.items():
        flags = []
        if m['nrfi_roi'] > 2.4: flags.append(f"NRFI zone +{m['nrfi_roi']:.1f}% ROI")
        if m['yrfi_roi'] > 2.4: flags.append(f"YRFI zone +{m['yrfi_roi']:.1f}% ROI")
        if m['brier_skill'] > 0.5: flags.append(f"Brier skill +{m['brier_skill']:.2f}%")
        verdict = f"  {name:<22} -> "
        verdict += "+EV signal: " + ", ".join(flags) if flags else "no clear edge"
        print(verdict)


if __name__ == "__main__":
    main()

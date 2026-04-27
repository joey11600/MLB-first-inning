#!/usr/bin/env python3
"""
test_handedness_2026.py -- does the new top-3 OPS-vs-opposing-hand feature
predict NRFI/YRFI on 2026 data above and beyond what V2 already captures?

Two tests:
  1. Univariate correlation: does top-3 OPS-vs-hand correlate with the
     game's actual NRFI outcome on 2026 graded games?
  2. Augmented model: does adding the feature to V2 improve out-of-sample
     Brier or Q5 win rate on a 2026 chronological train/test split?

This is a TEST-ONLY script.  It does NOT change production weights or
calibration.
"""

import csv
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install: pip install numpy")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from lr_baseline import LogReg
from calibration import ProbCalibrator
from recalibrate_v2 import (
    load_lr_model, load_fi_park, lr_predict_raw,
    LEAGUE_AVG_ERA, LEAGUE_AVG_HR9, LEAGUE_AVG_BB9,
    LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, FI_PARK_DEFAULT,
    coerce_float, brier,
)

PICKS_PATH = ROOT / "data" / "picks_2026.csv"


def load_2026_graded(rows, fi_park_map):
    """Build (date, X11, x_hand_pair, y) for graded 2026 rows where the new
    handedness feature is populated."""
    samples = []
    for r in rows:
        actual = (r.get("actual_result", "") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue

        # Need the new feature populated
        a_oph = r.get("away_top3_ops_vs_oppHand", "")
        h_oph = r.get("home_top3_ops_vs_oppHand", "")
        if not a_oph or not h_oph:
            continue

        home_team = r.get("home_team", "")
        fi_park = fi_park_map.get(home_team, FI_PARK_DEFAULT)
        x11 = [
            fi_park,
            coerce_float(r.get("home_fip"), LEAGUE_AVG_ERA),
            coerce_float(r.get("home_hr9"), LEAGUE_AVG_HR9),
            coerce_float(r.get("home_bb9"), LEAGUE_AVG_BB9),
            coerce_float(r.get("away_obp"), LEAGUE_AVG_OBP),
            coerce_float(r.get("away_slg"), LEAGUE_AVG_SLG),
            coerce_float(r.get("away_fip"), LEAGUE_AVG_ERA),
            coerce_float(r.get("away_hr9"), LEAGUE_AVG_HR9),
            coerce_float(r.get("away_bb9"), LEAGUE_AVG_BB9),
            coerce_float(r.get("home_obp"), LEAGUE_AVG_OBP),
            coerce_float(r.get("home_slg"), LEAGUE_AVG_SLG),
        ]
        a_oph_f = float(a_oph)
        h_oph_f = float(h_oph)
        y = 1 if actual == "NRFI" else 0
        samples.append((r.get("date", ""), x11, [a_oph_f, h_oph_f], y))
    return samples


def pearson_r(x, y):
    x = np.asarray(x); y = np.asarray(y)
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def q5_hit(p, y):
    order = np.argsort(p)
    n = len(p)
    q5 = order[-(n // 5):] if n >= 5 else order
    return float(y[q5].mean()), int(y[q5].sum()), len(q5)


def q1_yrfi(p, y):
    order = np.argsort(p)
    n = len(p)
    q1 = order[:(n // 5)] if n >= 5 else order
    wins = int((y[q1] == 0).sum())
    return wins / len(q1) if len(q1) else 0.0, wins, len(q1)


def main():
    print("=" * 74)
    print("  Handedness feature test (2026 only) -- does top-3 OPS-vs-oppHand")
    print("  add signal beyond V2 on the current regime?")
    print("=" * 74)

    fi_park = load_fi_park()
    with open(PICKS_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    samples = load_2026_graded(rows, fi_park)
    print(f"\nLoaded {len(samples)} graded 2026 rows with the new feature populated")

    if len(samples) < 100:
        print("  [warn] sample size too low for stable inference")

    samples.sort(key=lambda s: s[0])  # chronological

    dates = [s[0] for s in samples]
    X11   = np.asarray([s[1] for s in samples], dtype=float)
    Xhand = np.asarray([s[2] for s in samples], dtype=float)
    y     = np.asarray([s[3] for s in samples], dtype=int)

    base = float(y.mean())
    print(f"  Date range : {dates[0]} -> {dates[-1]}")
    print(f"  NRFI rate  : {base*100:.2f}%")

    # ===== TEST 1: univariate correlation =====
    # Higher top-3 OPS vs opposing hand SHOULD predict more YRFI (i.e. y=0).
    # Combine away+home into one "total expected offense" feature for the game.
    total_oph = Xhand[:, 0] + Xhand[:, 1]
    r_corr = pearson_r(total_oph, y)
    print("\n" + "-" * 74)
    print("  TEST 1: Univariate correlation (combined top-3 OPS-vs-hand vs NRFI)")
    print("-" * 74)
    print(f"  Pearson r          : {r_corr:+.4f}")
    print(f"  (Negative = better offense -> more YRFI = correct intuition)")
    se = 1.0 / math.sqrt(max(2, len(samples) - 2))
    print(f"  approx 95% CI     : [{r_corr - 2*se:+.4f}, {r_corr + 2*se:+.4f}]")
    if abs(r_corr) > 2 * se:
        print(f"  -> correlation is {abs(r_corr)/se:.1f}-SE away from zero -- meaningful")
    else:
        print(f"  -> correlation is INSIDE noise floor ({2*se:.4f}) -- NOT meaningful")

    # ===== TEST 2: V2 vs V2+hand on chronological 60/40 split =====
    print("\n" + "-" * 74)
    print("  TEST 2: V2 vs V2+hand on 2026 chronological 60/40 split")
    print("-" * 74)
    n = len(samples)
    cut = int(n * 0.60)
    print(f"  Train rows (chronological first 60%) : {cut}  ({dates[0]} -> {dates[cut-1]})")
    print(f"  Test  rows (last 40%)                 : {n - cut}  ({dates[cut]} -> {dates[-1]})")

    # Use the saved V2 model + saved calibrator as baseline
    model = load_lr_model()
    cal_path = ROOT / "data" / "calibration_v2.json"
    cal = ProbCalibrator.load(cal_path)
    p_te = lr_predict_raw(model, X11[cut:])
    p_te_cal = np.array([cal.predict(float(p)) for p in p_te])
    y_te = y[cut:]

    print(f"\n  V2 baseline (current saved weights + calibrator):")
    print(f"    Brier      : {brier(p_te_cal, y_te):.4f}")
    q5r, q5w, q5n = q5_hit(p_te_cal, y_te)
    q1r, q1w, q1n = q1_yrfi(p_te_cal, y_te)
    print(f"    Q5 NRFI hit: {q5w}-{q5n - q5w} ({q5r*100:.1f}%)")
    print(f"    Q1 YRFI hit: {q1w}-{q1n - q1w} ({q1r*100:.1f}%)")

    # Augmented: train fresh LR-13 on train split (V2 + 2 handedness features)
    X_tr_13 = np.hstack([X11[:cut], Xhand[:cut]])
    X_te_13 = np.hstack([X11[cut:], Xhand[cut:]])
    y_tr = y[:cut]

    feat_names_13 = [
        "fi_park_nrfi_rate",
        "home_fip", "home_hr9", "home_bb9",
        "away_obp", "away_slg",
        "away_fip", "away_hr9", "away_bb9",
        "home_obp", "home_slg",
        "away_top3_ops_vs_oppHand", "home_top3_ops_vs_oppHand",
    ]
    m13 = LogReg.fit(X_tr_13, y_tr, feat_names_13, l2=0.05)
    p_te_13 = m13.predict_proba(X_te_13)
    print(f"\n  V2 + handedness (refit on chronological 60%):")
    print(f"    Brier      : {brier(p_te_13, y_te):.4f}")
    q5r, q5w, q5n = q5_hit(p_te_13, y_te)
    q1r, q1w, q1n = q1_yrfi(p_te_13, y_te)
    print(f"    Q5 NRFI hit: {q5w}-{q5n - q5w} ({q5r*100:.1f}%)")
    print(f"    Q1 YRFI hit: {q1w}-{q1n - q1w} ({q1r*100:.1f}%)")

    print(f"\n  V2 weights for handedness features:")
    for name, coef in zip(feat_names_13, m13.w):
        if "oppHand" in name:
            print(f"    {name:<28} = {coef:+.4f}")

    # Side-by-side: V2-baseline-trained-on-2026-only too (apples-to-apples)
    m11 = LogReg.fit(X11[:cut], y_tr, feat_names_13[:11], l2=0.05)
    p_te_11 = m11.predict_proba(X11[cut:])
    print(f"\n  Apples-to-apples (V2-only re-fit on 2026 60%):")
    print(f"    Brier      : {brier(p_te_11, y_te):.4f}")
    q5r, q5w, q5n = q5_hit(p_te_11, y_te)
    q1r, q1w, q1n = q1_yrfi(p_te_11, y_te)
    print(f"    Q5 NRFI hit: {q5w}-{q5n - q5w} ({q5r*100:.1f}%)")
    print(f"    Q1 YRFI hit: {q1w}-{q1n - q1w} ({q1r*100:.1f}%)")

    # Final delta
    print("\n" + "-" * 74)
    print(f"  Brier delta    (11-feat -> 13-feat): "
          f"{brier(p_te_11, y_te):.4f} -> {brier(p_te_13, y_te):.4f}  "
          f"({brier(p_te_13, y_te) - brier(p_te_11, y_te):+.4f})")
    print()


if __name__ == "__main__":
    main()

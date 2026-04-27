#!/usr/bin/env python3
"""
autopsy.py -- forensic on whether the LR-v2 model has any edge at all.

Hypotheses to test:
  H1. The model is barely better than always predicting the base rate.
      (Brier improvement over climatology < statistical noise floor.)
  H2. The 11 features are multicollinear -- most weights have wrong signs
      because correlated features split each other's effect.
  H3. A 1-feature model (fi_park_nrfi_rate alone) performs nearly as well
      as 11 features.  If true, the 10 pitcher/offense features are noise.
  H4. An intuitively-signed-only subset (no flipped-sign weights) generalizes
      better out-of-sample than the full 11-feature model.

If H1, H2, H3 are confirmed, the LR-v2 model is overfit to multicollinear
patterns in 2024+2025 and effectively useless.  The "edge" we've been
chasing is statistical noise.

Run: python autopsy.py
"""

import csv
import math
import sys
from pathlib import Path
from itertools import combinations

try:
    import numpy as np
except ImportError:
    sys.exit("Install: pip install numpy")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from lr_baseline import (
    LogReg, FEATURE_DEFAULTS, V2_FEATURES, load_dataset, brier, log_loss,
)


def autopsy(train_path: Path, test_path: Path):
    print("=" * 70)
    print("  MODEL AUTOPSY -- LR-v2 forensic on 2024 train / 2025 holdout")
    print("=" * 70)

    # Load data
    X_tr, y_tr, feats, _ = load_dataset(train_path, V2_FEATURES, False)
    X_te, y_te, _, _     = load_dataset(test_path,  V2_FEATURES, False)
    n_tr, n_te = len(y_tr), len(y_te)
    base_te = float(y_te.mean())
    climatology_brier = base_te * (1 - base_te)

    print(f"\nTrain N = {n_tr}, Test N = {n_te}")
    print(f"Test NRFI rate = {base_te*100:.2f}%")
    print(f"Climatology Brier (always predict base rate) = {climatology_brier:.4f}")

    # ----- H1: Brier improvement over climatology -----
    print("\n" + "-" * 70)
    print("  H1: Is the model meaningfully better than predicting base rate?")
    print("-" * 70)
    model = LogReg.fit(X_tr, y_tr, V2_FEATURES, l2=0.01)
    p_te  = model.predict_proba(X_te)
    bv2   = brier(p_te, y_te)
    delta = climatology_brier - bv2
    print(f"  Climatology Brier : {climatology_brier:.4f}")
    print(f"  V2 Brier          : {bv2:.4f}")
    print(f"  Improvement       : {delta:+.4f}  ({delta/climatology_brier*100:+.2f}%)")

    # SE of Brier on N test samples (rough, via bootstrap)
    rng = np.random.default_rng(seed=42)
    boot_briers = []
    for _ in range(500):
        idx = rng.integers(0, n_te, size=n_te)
        boot_briers.append(brier(p_te[idx], y_te[idx]))
    se_brier = float(np.std(boot_briers))
    print(f"  Bootstrap SE      : {se_brier:.4f}")
    print(f"  Improvement / SE  : {delta/se_brier:+.2f}")
    if abs(delta) < 2 * se_brier:
        print("  *** improvement is INSIDE 2-SE band -- NOT statistically meaningful ***")
    else:
        print(f"  improvement is {abs(delta)/se_brier:.1f}-SE away from zero -- significant")

    # ----- H2: weight pathology -----
    print("\n" + "-" * 70)
    print("  H2: Weight signs vs intuitive expectation")
    print("-" * 70)
    # In standardized space:
    #   positive coef -> higher feature value increases P(NRFI)
    # Intuitive direction for each feature (sign that SHOULD be there):
    expected_signs = {
        "fi_park_nrfi_rate":  +1,  # higher park NRFI rate -> NRFI
        "home_fip":           -1,  # higher FIP = worse pitcher -> YRFI
        "home_hr9":           -1,  # more HRs allowed -> YRFI
        "home_bb9":           -1,  # more walks -> YRFI
        "away_obp":           -1,  # better hitters -> YRFI
        "away_slg":           -1,  # better hitters -> YRFI
        "away_fip":           -1,  # higher FIP = worse pitcher -> YRFI
        "away_hr9":           -1,  # more HRs allowed -> YRFI
        "away_bb9":           -1,  # more walks -> YRFI
        "home_obp":           -1,  # better hitters -> YRFI
        "home_slg":           -1,  # better hitters -> YRFI
    }
    print(f"  {'feature':<22} {'weight':>8}  {'expected':>10}  verdict")
    flipped = 0
    for name, coef in zip(V2_FEATURES, model.w):
        exp = expected_signs.get(name, 0)
        actual_sign = +1 if coef > 0 else -1 if coef < 0 else 0
        if exp != 0 and actual_sign != exp:
            verdict = "WRONG SIGN"
            flipped += 1
        else:
            verdict = "ok" if exp != 0 else ""
        print(f"  {name:<22} {coef:>+8.3f}  {exp:>+10}  {verdict}")
    print(f"\n  -> {flipped} of {len(V2_FEATURES)} weights have COUNTERINTUITIVE signs")
    if flipped >= 3:
        print("  *** strong evidence of multicollinearity pathology ***")

    # ----- H3: minimal 1-feature model -----
    print("\n" + "-" * 70)
    print("  H3: How much of the model is just fi_park_nrfi_rate?")
    print("-" * 70)
    for feat_set in [
        ["fi_park_nrfi_rate"],
        ["fi_park_nrfi_rate", "home_fip"],
        ["fi_park_nrfi_rate", "home_fip", "away_fip"],
        ["fi_park_nrfi_rate", "home_fip", "away_hr9"],
        V2_FEATURES,  # full 11
    ]:
        X_tr_sub, y_tr_sub, _, _ = load_dataset(train_path, feat_set, False)
        X_te_sub, y_te_sub, _, _ = load_dataset(test_path,  feat_set, False)
        m = LogReg.fit(X_tr_sub, y_tr_sub, feat_set, l2=0.01)
        p = m.predict_proba(X_te_sub)
        b = brier(p, y_te_sub)
        # Q5 hit rate
        order = np.argsort(p)
        q5_idx = order[-(len(p)//5):]
        q5_hit = float(y_te_sub[q5_idx].mean())
        print(f"  {len(feat_set):>2}-feat: Brier={b:.4f}  Q5 NRFI={q5_hit*100:.1f}%   "
              f"feats=[{', '.join(feat_set[:3])}{'...' if len(feat_set) > 3 else ''}]")

    # ----- H4: intuitively-signed-only model -----
    print("\n" + "-" * 70)
    print("  H4: Model with only intuitively-signed features")
    print("-" * 70)
    # Pick features whose weights have the right sign in V2
    intuitive_feats = []
    for name, coef in zip(V2_FEATURES, model.w):
        exp = expected_signs.get(name, 0)
        actual = +1 if coef > 0 else -1
        if exp != 0 and actual == exp:
            intuitive_feats.append(name)
    print(f"  Intuitively signed: {intuitive_feats}")
    if intuitive_feats:
        X_tr_int, y_tr_int, _, _ = load_dataset(train_path, intuitive_feats, False)
        X_te_int, y_te_int, _, _ = load_dataset(test_path,  intuitive_feats, False)
        m_int = LogReg.fit(X_tr_int, y_tr_int, intuitive_feats, l2=0.01)
        p_int = m_int.predict_proba(X_te_int)
        b_int = brier(p_int, y_te_int)
        order = np.argsort(p_int)
        q5_idx = order[-(len(p_int)//5):]
        q5_hit = float(y_te_int[q5_idx].mean())
        print(f"  Brier   : {b_int:.4f}  (vs full V2 {bv2:.4f})")
        print(f"  Q5 NRFI : {q5_hit*100:.1f}%")
        if b_int <= bv2:
            print("  *** intuitively-signed-only matches or beats the full model -- ")
            print("      the 'wrong-sign' features add no signal ***")

    # ----- H5: Pairwise feature correlations -----
    print("\n" + "-" * 70)
    print("  H5: Feature correlation matrix (training data)")
    print("-" * 70)
    corr = np.corrcoef(X_tr.T)
    print(f"  Top correlations (|r| > 0.5):")
    pairs = []
    for i in range(len(V2_FEATURES)):
        for j in range(i + 1, len(V2_FEATURES)):
            if abs(corr[i, j]) > 0.5:
                pairs.append((V2_FEATURES[i], V2_FEATURES[j], corr[i, j]))
    pairs.sort(key=lambda p: -abs(p[2]))
    for a, b, r in pairs[:15]:
        print(f"    {a:<22} <-> {b:<22}  r = {r:+.3f}")
    if not pairs:
        print("    (none > 0.5)")

    print("\n" + "=" * 70)
    print("  Summary")
    print("=" * 70)
    print(f"  Climatology baseline  : {climatology_brier:.4f}")
    print(f"  V2 (11 features)      : {bv2:.4f}  (improvement: {delta:+.4f})")
    if abs(delta) < 2 * se_brier:
        print(f"  --> V2's improvement over coin-flip is WITHIN noise floor (2*SE = {2*se_brier:.4f}).")
    print(f"  Wrong-sign weights    : {flipped} of {len(V2_FEATURES)}")
    print()


if __name__ == "__main__":
    autopsy(
        Path(ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"),
        Path(ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"),
    )

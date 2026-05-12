#!/usr/bin/env python3
"""
test_away_pitcher_sensitivity.py -- does the model actually penalize a
trash away pitcher when paired with an elite home pitcher?

User's exact scenario: home pitcher is amazing, away pitcher gives up
HRs in the 1st inning.  Should be a YRFI lean (run scored in B1) even
though T1 is locked down.

We construct a synthetic game and dial the away pitcher from elite
(FIP 2.5, HR9 0.5, BB9 1.5) to terrible (FIP 6.0, HR9 2.5, BB9 5.0)
holding everything else constant.  How much does P(NRFI) shift?

If the model is correctly using the away pitcher, P(NRFI) should drop
substantially as the away pitcher gets worse.  If it barely moves, the
user's concern is right: the away pitcher's full-pitcher signal is
being ignored.
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def load_model():
    with open(ROOT / "data" / "lr_model.json", encoding="utf-8") as f:
        m = json.load(f)
    return m


def lr_predict(features, m):
    """Apply LR to a 11-feature vector. Returns raw P(NRFI)."""
    z = m["bias"]
    for x, mean, std, w in zip(features, m["mean"], m["std"], m["weights"]):
        if std <= 0: continue
        z += w * (x - mean) / std
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def main():
    m = load_model()
    print("=" * 72)
    print("  Away-pitcher sensitivity test")
    print("  (does the model penalize a trash away pitcher in the 1st inning?)")
    print("=" * 72)

    # Setup: elite home pitcher, average everything else, neutral park
    base = {
        "fi_park_nrfi_rate": 0.50,   # neutral park
        "home_fip":          3.00,   # elite home pitcher
        "home_hr9":          0.80,
        "home_bb9":          2.00,
        "away_obp":          0.318,  # league avg away offense
        "away_slg":          0.414,
        "away_fip":          4.20,   # baseline avg
        "away_hr9":          1.20,
        "away_bb9":          3.20,
        "home_obp":          0.318,
        "home_slg":          0.414,
    }
    feat_order = [
        "fi_park_nrfi_rate",
        "home_fip", "home_hr9", "home_bb9",
        "away_obp", "away_slg",
        "away_fip", "away_hr9", "away_bb9",
        "home_obp", "home_slg",
    ]
    def vec(d):
        return [d[k] for k in feat_order]

    # Sweep the away pitcher from elite to trash
    away_pitcher_levels = [
        ("ELITE",   {"away_fip": 2.50, "away_hr9": 0.50, "away_bb9": 1.50}),
        ("VERY GOOD",{"away_fip": 3.20, "away_hr9": 0.80, "away_bb9": 2.20}),
        ("AVERAGE", {"away_fip": 4.20, "away_hr9": 1.20, "away_bb9": 3.20}),
        ("BAD",     {"away_fip": 5.00, "away_hr9": 1.60, "away_bb9": 4.00}),
        ("TRASH",   {"away_fip": 6.00, "away_hr9": 2.50, "away_bb9": 5.00}),
    ]

    print(f"\n  Constants: elite home pitcher (FIP 3.0), neutral park (50% NRFI),")
    print(f"             league-avg offenses both sides")
    print()
    print(f"  {'away pitcher':<13}  {'fip':>5}  {'hr9':>5}  {'bb9':>5}  "
          f"{'P(NRFI)':>9}  {'pick':<14}  vs ELITE")

    elite_p = None
    for label, override in away_pitcher_levels:
        d = dict(base)
        d.update(override)
        p = lr_predict(vec(d), m)
        if elite_p is None:
            elite_p = p
            delta_str = ""
        else:
            delta_str = f"{(p - elite_p) * 100:+.2f}pp"

        # What pick does this raw P map to under the calibrator?
        from calibration import ProbCalibrator
        cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
        p_cal = cal.predict(p)
        if   p_cal >= 0.60: zone = "STRONG NRFI"
        elif p_cal >= 0.53: zone = "LEAN NRFI"
        elif p_cal >= 0.47: zone = "PASS"
        elif p_cal >= 0.40: zone = "LEAN YRFI"
        else:               zone = "STRONG YRFI"

        print(f"  {label:<13}  {d['away_fip']:>5.2f}  {d['away_hr9']:>5.2f}  "
              f"{d['away_bb9']:>5.2f}  {p*100:>8.2f}%  ({zone})  {delta_str}")

    print()
    print("  Interpretation:")
    print("  - If P(NRFI) drops significantly (15+pp) elite -> trash, away pitcher matters")
    print("  - If P(NRFI) barely moves (<5pp), the model is ignoring away pitcher quality")
    print()

    # Now test: what does pure SAM home pitcher excellence + trash away pitcher say?
    print("  User's exact scenario test:")
    print("  Home pitcher = ace (FIP 2.5), Away pitcher = trash (FIP 6.0, HR9 2.5)")

    user_case = dict(base)
    user_case["home_fip"] = 2.50
    user_case["home_hr9"] = 0.50
    user_case["home_bb9"] = 1.50
    user_case["away_fip"] = 6.00
    user_case["away_hr9"] = 2.50
    user_case["away_bb9"] = 5.00
    p_user = lr_predict(vec(user_case), m)
    p_user_cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json").predict(p_user)

    if   p_user_cal >= 0.60: zone = "STRONG NRFI"
    elif p_user_cal >= 0.53: zone = "LEAN NRFI"
    elif p_user_cal >= 0.47: zone = "PASS"
    elif p_user_cal >= 0.40: zone = "LEAN YRFI"
    else:                    zone = "STRONG YRFI"

    print(f"    Raw P(NRFI)        : {p_user*100:.2f}%")
    print(f"    Calibrated P(NRFI) : {p_user_cal*100:.2f}%")
    print(f"    Pick zone          : {zone}")
    print(f"    Intuition says     : LEAN YRFI or PASS (away pitcher likely gives up runs)")
    print()


if __name__ == "__main__":
    main()

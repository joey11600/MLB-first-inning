#!/usr/bin/env python3
"""
regrade_and_rebet.py -- retroactively regrade and re-bet the entire season
using the current production model and calibrator.

Why: the daily grader keeps actual_result fresh for each game, but past
picks were classified under whatever calibrator was loaded when the
prediction first ran. After we recalibrate (e.g. weekly), historical
graded_result rows still reflect the OLD pick zones. This script
re-derives picks from frozen point-in-time features so the ROI panel
shows what we'd have done with today's pipeline applied uniformly.

What it does:
  1. Regrade  : for every distinct date in picks_2026.csv, call
                tracker.grade_date() to refresh actual_result and any
                fields the MLB API now reports differently.
  2. Rebet    : for each row, rebuild the V2 11-feature vector from the
                already-stored away_*/home_* columns (these were captured
                at predict time, so no look-ahead), run the saved LR-v2
                model + isotonic calibrator, reclassify pick zone, and
                update nrfi_prob / yrfi_prob / pick_side / pick_strength
                / pick_label.
  3. Re-grade : with new pick_side in hand, derive graded_result from the
                already-known actual_result (POSTPONED stays POSTPONED;
                PASS stays PASS; otherwise WIN/LOSS).

Usage:
  python regrade_and_rebet.py             # do it
  python regrade_and_rebet.py --no-regrade # skip the MLB API regrade pass
  python regrade_and_rebet.py --dry-run   # report changes without writing
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Missing numpy. Run: pip install numpy")

ROOT = Path(__file__).parent
LR_T1_PATH    = ROOT / "data" / "lr_t1.json"
LR_B1_PATH    = ROOT / "data" / "lr_b1.json"
LR_CAL_PATH   = ROOT / "data" / "calibration_v2.json"
FI_PARK_PATH  = ROOT / "data" / "fi_park_factors.json"
PICKS_PATH    = ROOT / "data" / "picks_2026.csv"

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_HR9 = 1.20
LEAGUE_AVG_BB9 = 3.20
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_SLG = 0.414
FI_PARK_DEFAULT = 0.50

# Calibrated probability thresholds (mirror predictor's classify_pick_lr)
LR_STRONG_NRFI_P = 0.60
LR_LEAN_NRFI_P   = 0.53
LR_PASS_LO_P     = 0.47
LR_LEAN_YRFI_P   = 0.40

T1_FEATURES = ["fi_park_nrfi_rate", "home_fip", "away_obp"]
B1_FEATURES = ["fi_park_nrfi_rate", "away_fip", "home_obp"]


def _load_one(path: Path, expected: list[str]):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d["feature_names"] != expected:
        sys.exit(f"{path.name} feature order mismatch.")
    return {
        "weights": np.asarray(d["weights"], dtype=float),
        "bias":    float(d["bias"]),
        "mean":    np.asarray(d["mean"],    dtype=float),
        "std":     np.asarray(d["std"],     dtype=float),
    }


def load_lr_models():
    return _load_one(LR_T1_PATH, T1_FEATURES), _load_one(LR_B1_PATH, B1_FEATURES)


def load_calibrator():
    from calibration import ProbCalibrator
    return ProbCalibrator.load(LR_CAL_PATH)


def load_fi_park():
    if not FI_PARK_PATH.exists():
        return {}
    with open(FI_PARK_PATH, encoding="utf-8") as f:
        return json.load(f)


def coerce_float(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def lr_predict_one(model, x_vec):
    x = np.asarray(x_vec, dtype=float)
    xn = (x - model["mean"]) / model["std"]
    z = float(xn @ model["weights"] + model["bias"])
    return 1.0 / (1.0 + math.exp(-z))


def lr_predict_two_stage_one(t1_model, b1_model, t1_vec, b1_vec):
    p_t1 = lr_predict_one(t1_model, t1_vec)
    p_b1 = lr_predict_one(b1_model, b1_vec)
    return (1.0 - p_t1) * (1.0 - p_b1)


def classify_zone(p_nrfi: float, data_pts: int) -> tuple[str, str]:
    if data_pts == 0:
        return "PASS", "NO DATA"
    if p_nrfi >= LR_STRONG_NRFI_P: return "NRFI", "STRONG"
    if p_nrfi >= LR_LEAN_NRFI_P:   return "NRFI", "LEAN"
    if p_nrfi >= LR_PASS_LO_P:     return "PASS", "NO EDGE"
    if p_nrfi >= LR_LEAN_YRFI_P:   return "YRFI", "LEAN"
    return "YRFI", "STRONG"


def pick_label_text(side: str, strength: str) -> str:
    if side == "PASS":
        if strength == "NO DATA":         return "PASS - No data"
        if strength == "STARTER PENDING": return "PASS - Starter pending"
        return "PASS - No edge"
    return f"{strength} {side}"


def derive_graded_result(pick_side: str, actual: str) -> str:
    """Given the (new) pick_side and the stored actual_result, return
    the graded_result the bet would have produced."""
    actual_u = (actual or "").upper()
    if actual_u in ("POSTPONED", "SUSPENDED"):
        return actual_u
    if not actual_u:
        return ""  # game not yet played
    if pick_side == "PASS":
        return "PASS"
    return "WIN" if pick_side == actual_u else "LOSS"


def regrade_dates(dates: list[str], season: int = 2026) -> None:
    """Loop tracker.grade_date over all dates -- grader is idempotent."""
    from tracker import grade_date
    for d in sorted(dates):
        try:
            grade_date(d, season)
        except Exception as e:
            print(f"  [{d}] grade error: {e}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-regrade", action="store_true",
                        help="Skip the MLB-API regrade pass; only re-bet from frozen features")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print summary of changes without rewriting picks_2026.csv")
    args = parser.parse_args()

    if not LR_CAL_PATH.exists():
        sys.exit(f"Missing calibrator: {LR_CAL_PATH}. Run recalibrate_v2.py first.")

    print("=" * 64)
    print("  Regrade + Rebet pass for picks_2026.csv")
    print("=" * 64)

    # ------- Step 1: regrade via MLB API (refresh actual_result) -------
    with open(PICKS_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if not rows or not fieldnames:
        sys.exit("picks_2026.csv is empty or has no header.")

    dates = sorted({r["date"] for r in rows if r.get("date")})

    if not args.no_regrade:
        print(f"\n[1/2] Regrading {len(dates)} slate dates via MLB API "
              f"({dates[0]} -> {dates[-1]})...")
        regrade_dates(dates)
        # Re-read after grader updated the file
        with open(PICKS_PATH, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)
    else:
        print("\n[1/2] Skipping regrade (--no-regrade)")

    # ------- Step 2: rebet using current model + calibrator -------
    print(f"\n[2/2] Rebetting {len(rows)} rows using current LR-v2 + calibration...")

    t1_model, b1_model = load_lr_models()
    cal   = load_calibrator()
    fi_park_map = load_fi_park()

    changed_picks = 0
    changed_grades = 0
    zone_before: dict[str, int] = {}
    zone_after:  dict[str, int] = {}
    grade_before: dict[str, int] = {}
    grade_after:  dict[str, int] = {}

    for r in rows:
        old_zone  = (r.get("pick_label") or "").strip()
        old_grade = (r.get("graded_result") or "").strip()
        zone_before[old_zone]   = zone_before.get(old_zone, 0) + 1
        grade_before[old_grade] = grade_before.get(old_grade, 0) + 1

        # Build T1 + B1 feature vectors from frozen point-in-time stats
        home_team = r.get("home_team", "")
        fi_park = fi_park_map.get(home_team, FI_PARK_DEFAULT)
        t1_vec = [
            fi_park,
            coerce_float(r.get("home_fip"), LEAGUE_AVG_ERA),
            coerce_float(r.get("away_obp"), LEAGUE_AVG_OBP),
        ]
        b1_vec = [
            fi_park,
            coerce_float(r.get("away_fip"), LEAGUE_AVG_ERA),
            coerce_float(r.get("home_obp"), LEAGUE_AVG_OBP),
        ]
        raw_p = lr_predict_two_stage_one(t1_model, b1_model, t1_vec, b1_vec)
        cal_p = cal.predict(raw_p)

        # data_pts mirrors predictor: count of non-avg quality inputs out of 4
        qualities = [
            r.get("away_pitcher_q", "avg"),
            r.get("home_pitcher_q", "avg"),
            r.get("away_batting_q", "avg"),
            r.get("home_batting_q", "avg"),
        ]
        data_pts = sum(1 for q in qualities if (q or "avg") != "avg")

        side, strength = classify_zone(cal_p, data_pts)

        # Starter-pending guard (mirror predictor): if either pitcher quality
        # is "avg", force PASS / STARTER PENDING
        if r.get("away_pitcher_q") == "avg" or r.get("home_pitcher_q") == "avg":
            side, strength = "PASS", "STARTER PENDING"

        new_label = pick_label_text(side, strength)

        # Update prediction columns
        r["pick_side"]     = side
        r["pick_strength"] = strength
        r["pick_label"]    = new_label
        r["nrfi_prob"]     = f"{cal_p:.4f}"
        r["yrfi_prob"]     = f"{1.0 - cal_p:.4f}"

        # Re-derive graded_result from new pick + existing actual_result
        actual = (r.get("actual_result") or "").upper()
        new_grade = derive_graded_result(side, actual)
        r["graded_result"] = new_grade

        # Track diffs
        if new_label != old_zone:   changed_picks += 1
        if new_grade != old_grade:  changed_grades += 1

        zone_after[new_label]   = zone_after.get(new_label, 0) + 1
        grade_after[new_grade]  = grade_after.get(new_grade, 0) + 1

    # ------- Report diff -------
    print("\n" + "=" * 64)
    print(f"  Diff summary  (--dry-run={args.dry_run})")
    print("=" * 64)
    print(f"  Picks changed     : {changed_picks} / {len(rows)}")
    print(f"  Grades changed    : {changed_grades} / {len(rows)}")

    print("\n  Pick zone distribution:")
    print(f"    {'zone':<28}  {'before':>8}  {'after':>8}  {'delta':>7}")
    all_zones = sorted(set(zone_before) | set(zone_after))
    for z in all_zones:
        b = zone_before.get(z, 0); a = zone_after.get(z, 0)
        delta = a - b
        sign = "+" if delta > 0 else ""
        print(f"    {z:<28}  {b:>8}  {a:>8}  {sign}{delta:>6}")

    print("\n  Grade outcome distribution:")
    print(f"    {'grade':<28}  {'before':>8}  {'after':>8}  {'delta':>7}")
    all_grades = sorted(set(grade_before) | set(grade_after))
    for g in all_grades:
        b = grade_before.get(g, 0); a = grade_after.get(g, 0)
        delta = a - b
        sign = "+" if delta > 0 else ""
        label = g if g else "(ungraded)"
        print(f"    {label:<28}  {b:>8}  {a:>8}  {sign}{delta:>6}")

    # Bet-zone P&L delta at -110
    win_units = 100 / 110
    pl_before = (grade_before.get("WIN", 0) * win_units
                 - grade_before.get("LOSS", 0))
    pl_after  = (grade_after.get("WIN", 0) * win_units
                 - grade_after.get("LOSS", 0))
    print(f"\n  Net units P&L @ -110:")
    print(f"    before : {pl_before:+.2f}")
    print(f"    after  : {pl_after:+.2f}")
    print(f"    delta  : {pl_after - pl_before:+.2f}")

    # ------- Write back -------
    if args.dry_run:
        print(f"\n  [dry-run] {PICKS_PATH} NOT modified.")
    else:
        with open(PICKS_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  Wrote updated picks -> {PICKS_PATH}")

    print("\nDone.\n")


if __name__ == "__main__":
    main()

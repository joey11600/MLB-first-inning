#!/usr/bin/env python3
"""
audit_data.py -- data integrity audit.

For each feature in our backtest + 2026 picks CSVs, report:
  - % populated with real data (not empty, not the league-average default)
  - distribution (min/max/mean/median)
  - count of rows where we'd silently substitute a default at fit time

This catches the "we're testing a feature but 90% of rows have it missing
so it's effectively constant" failure mode.
"""

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent

# Default values that the load_dataset / backfill code substitutes when
# a cell is empty.  If a feature is mostly equal to its default, then
# "adding it to the model" effectively adds a constant column.
DEFAULTS = {
    "fi_park_nrfi_rate":  0.50,
    "home_fip":           4.20, "away_fip":           4.20,
    "home_hr9":           1.20, "away_hr9":           1.20,
    "home_bb9":           3.20, "away_bb9":           3.20,
    "home_k9":            8.9,  "away_k9":            8.9,
    "home_obp":           0.318, "away_obp":          0.318,
    "home_slg":           0.414, "away_slg":          0.414,
    "home_rpg":           4.50, "away_rpg":           4.50,
    # Handedness
    "home_pitcher_throws_l": 0.0, "away_pitcher_throws_l": 0.0,
    "home_top3_lhb":         1.0, "away_top3_lhb":         1.0,
    "home_top3_switch":      0.0, "away_top3_switch":      0.0,
    "home_top3_platoon":     0.0, "away_top3_platoon":     0.0,
    # Arsenal
    "home_arsenal_fb_pct":   0.55, "away_arsenal_fb_pct":   0.55,
    "home_arsenal_bb_pct":   0.30, "away_arsenal_bb_pct":   0.30,
    "home_arsenal_off_pct":  0.15, "away_arsenal_off_pct":  0.15,
    "home_arsenal_fb_velo":  93.0, "away_arsenal_fb_velo":  93.0,
    "home_arsenal_velo_gap": 9.0,  "away_arsenal_velo_gap": 9.0,
    # Weather
    "wx_temp_c":  20.0,
    "wx_wind_kmh": 10.0,
    "wx_humidity": 60.0,
    "wx_is_dome":  0.0,
    "wx_wind_deg": 180.0,
    # First-inning splits
    "home_fi_era": 4.20, "away_fi_era": 4.20,
    "home_fi_whip": 1.25, "away_fi_whip": 1.25,
    "home_fi_ip":  0.0,  "away_fi_ip":  0.0,
    # Top-3 current
    "home_top3c_obp": 0.318, "away_top3c_obp": 0.318,
    "home_top3c_slg": 0.414, "away_top3c_slg": 0.414,
    "home_top3c_iso": 0.169, "away_top3c_iso": 0.169,
    # Days rest
    "home_days_rest": 5.0, "away_days_rest": 5.0,
    # Day game
    "is_day_game": 0.0, "start_hour_et": 19.0,
}


def coerce(s):
    if s in (None, ""):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def near(a, b, tol=1e-3):
    return abs(a - b) < tol


def audit(csv_path: Path, label: str, feature_subset: list[str]):
    print(f"\n{'=' * 80}")
    print(f"  {label}: {csv_path.name}")
    print(f"{'=' * 80}")

    n_rows = 0
    populated: dict[str, int] = defaultdict(int)
    default_match: dict[str, int] = defaultdict(int)
    all_values: dict[str, list[float]] = defaultdict(list)

    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            n_rows += 1
            for feat in feature_subset:
                cell = r.get(feat, "")
                if cell == "" or cell is None:
                    continue
                v = coerce(cell)
                if v is None:
                    continue
                populated[feat] += 1
                if feat in DEFAULTS and near(v, DEFAULTS[feat]):
                    default_match[feat] += 1
                all_values[feat].append(v)

    print(f"\n  Total rows: {n_rows}\n")
    print(f"  {'feature':<28} {'populated':>10} {'pct':>6} "
          f"{'== default':>10} {'distinct':>9} {'mean':>8} {'min':>8} {'max':>8}")
    print("  " + "-" * 95)

    # Identify problem features (low coverage OR mostly default)
    problems = []
    for feat in feature_subset:
        n = populated[feat]
        pct = n / n_rows * 100 if n_rows else 0.0
        d = default_match[feat]
        d_pct = d / max(1, n) * 100
        vals = all_values[feat]
        distinct = len(set(round(v, 4) for v in vals))
        mean = sum(vals) / len(vals) if vals else 0.0
        mn = min(vals) if vals else 0.0
        mx = max(vals) if vals else 0.0

        flag = ""
        if pct < 30:
            flag = "  ## low coverage"
            problems.append((feat, "low_coverage", pct))
        elif d_pct > 90 and distinct < 10:
            flag = "  ## mostly default"
            problems.append((feat, "mostly_default", d_pct))
        elif distinct < 3:
            flag = "  ## near-constant"
            problems.append((feat, "near_constant", distinct))

        print(f"  {feat:<28} {n:>10} {pct:>5.1f}%  "
              f"{d:>4} ({d_pct:>4.0f}%) {distinct:>8}  "
              f"{mean:>8.3f} {mn:>8.3f} {mx:>8.3f}{flag}")

    if problems:
        print(f"\n  PROBLEM FEATURES (won't add real signal):")
        for feat, kind, val in problems:
            if kind == "low_coverage":
                print(f"    - {feat}: only {val:.1f}% of rows have data")
            elif kind == "mostly_default":
                print(f"    - {feat}: {val:.0f}% of populated rows == default value")
            elif kind == "near_constant":
                print(f"    - {feat}: only {val} distinct values across the whole dataset")
    else:
        print(f"\n  All features in this category have meaningful coverage.")


def main():
    # Group features by category
    base = ["fi_park_nrfi_rate",
            "home_fip", "away_fip",
            "home_hr9", "away_hr9",
            "home_bb9", "away_bb9",
            "home_k9",  "away_k9",
            "home_obp", "away_obp",
            "home_slg", "away_slg",
            "home_rpg", "away_rpg"]
    handedness = ["home_pitcher_throws_l", "away_pitcher_throws_l",
                  "home_top3_lhb", "away_top3_lhb",
                  "home_top3_switch", "away_top3_switch",
                  "home_top3_platoon", "away_top3_platoon"]
    arsenal = ["home_arsenal_fb_pct", "away_arsenal_fb_pct",
               "home_arsenal_bb_pct", "away_arsenal_bb_pct",
               "home_arsenal_off_pct", "away_arsenal_off_pct",
               "home_arsenal_fb_velo", "away_arsenal_fb_velo",
               "home_arsenal_velo_gap", "away_arsenal_velo_gap"]
    weather = ["wx_temp_c", "wx_wind_kmh", "wx_wind_deg",
               "wx_humidity", "wx_is_dome"]
    fi_splits = ["home_fi_era", "away_fi_era",
                 "home_fi_whip", "away_fi_whip",
                 "home_fi_ip",   "away_fi_ip"]
    misc = ["home_top3c_obp", "away_top3c_obp",
            "home_top3c_iso", "away_top3c_iso",
            "home_days_rest", "away_days_rest",
            "is_day_game", "start_hour_et"]
    outcomes = ["fi_away_runs", "fi_home_runs", "fi_total_runs",
                "actual_side", "actual_result", "graded_result"]

    full_set = base + handedness + arsenal + weather + fi_splits + misc + outcomes

    for path, label in [
        (ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv", "2024 backtest"),
        (ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv", "2025 backtest"),
        (ROOT / "data" / "picks_2026.csv",                                       "2026 picks"),
    ]:
        # Filter to only features present in the file's header
        with open(path, encoding="utf-8") as f:
            header = next(csv.reader(f))
        present = [f for f in full_set if f in header]
        audit(path, label, present)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
rebuild_park_factors.py -- regenerate data/fi_park_factors.json from
2025 backtest + 2026 partial graded data, dropping the 2024 anomaly.

The current factors were built from 2024+2025. 2024 had a 53.5% NRFI base
rate (vs 49.7% in 2025 and 49.1% in 2026 to date) so factors are biased
high. The dominant feature in V2 (fi_park_nrfi_rate, weight +0.21) ends
up flagging too many games as NRFI because every park's prior is too
NRFI-favorable.

Test_park_refresh.py confirmed: switching to 2025+2026 partial flips
2026 retro Q5 NRFI from 39% (-17u) to 56.5% (+5u) and Q1 YRFI from 53.6%
(+1.6u) to 65.2% (+16.9u). Combined ~+38u swing on the same model.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
PICKS_26 = ROOT / "data" / "picks_2026.csv"
OUT_PATH = ROOT / "data" / "fi_park_factors.json"

PRIOR_GAMES = 50  # Bayesian shrinkage prior


def main():
    park_n: dict[str, int]    = {}
    park_nrfi: dict[str, int] = {}

    # 2025 backtest -- columns: home, actual_side
    with open(BT_2025, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            park = r.get("home", "")
            actual = (r.get("actual_side") or "").upper()
            if not park or actual not in ("NRFI", "YRFI"):
                continue
            park_n[park]    = park_n.get(park, 0) + 1
            park_nrfi[park] = park_nrfi.get(park, 0) + (1 if actual == "NRFI" else 0)
    n_2025 = sum(park_n.values())

    # 2026 picks -- columns: home_team, actual_result
    n_2026 = 0
    with open(PICKS_26, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            park = r.get("home_team", "")
            actual = (r.get("actual_result") or "").upper()
            if not park or actual not in ("NRFI", "YRFI"):
                continue
            park_n[park]    = park_n.get(park, 0) + 1
            park_nrfi[park] = park_nrfi.get(park, 0) + (1 if actual == "NRFI" else 0)
            n_2026 += 1

    overall_n    = sum(park_n.values())
    overall_nrfi = sum(park_nrfi.values())
    overall_rate = overall_nrfi / overall_n if overall_n else 0.5

    print(f"Park-factor source mix:")
    print(f"  2025 backtest : {n_2025} graded games")
    print(f"  2026 picks    : {n_2026} graded games")
    print(f"  Total         : {overall_n}, base NRFI rate {overall_rate*100:.2f}%")
    print(f"  Bayesian prior: {PRIOR_GAMES} games per park toward overall mean\n")

    # Bayesian shrinkage
    rates: dict[str, float] = {}
    for park, n in park_n.items():
        rates[park] = (park_nrfi[park] + PRIOR_GAMES * overall_rate) / (n + PRIOR_GAMES)

    # Diff vs current
    cur = {}
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            cur = json.load(f)

    print(f"{'park':<5}  {'old':>6}  {'new':>6}  {'delta':>7}  n  nrfi/total")
    for park in sorted(rates.keys()):
        old_v = cur.get(park, 0.5)
        new_v = rates[park]
        delta = new_v - old_v
        sign = "+" if delta >= 0 else ""
        print(f"{park:<5}  {old_v*100:>5.1f}%  {new_v*100:>5.1f}%  "
              f"{sign}{delta*100:>5.1f}pp  {park_n[park]:>3}  "
              f"{park_nrfi[park]}/{park_n[park]} = {park_nrfi[park]/park_n[park]*100:.1f}%")

    # Write
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rates, f, indent=2, sort_keys=True)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()

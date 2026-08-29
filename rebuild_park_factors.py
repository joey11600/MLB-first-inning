#!/usr/bin/env python3
"""
rebuild_park_factors.py -- regenerate data/fi_park_factors.json from
2025 backtest + 2026 graded data, dropping the 2024 anomaly.

The 2024 season had a 53.5% NRFI base rate (vs 49.7% in 2025 and 49.3%
in 2026) so factors built on it were biased NRFI-high. fi_park_nrfi_rate
is the single largest weight in both half-inning models (T1 -0.0354,
B1 -0.0331 on standardized inputs), so a wrong park value does not nudge
a probability -- it can flip the side.

VENUE CHANGES (2026-08-29)
--------------------------
A park's history is only usable while the team plays in the SAME
BUILDING. Tampa Bay spent 2025 at George M. Steinbrenner Field (open
air) while Tropicana Field was repaired after Hurricane Milton, and
returned to the rebuilt Trop for 2026. Those are different buildings
and the first-inning rates differ by 13 points:

    TB 2025 @ Steinbrenner : 30/77 = 39.0% NRFI
    TB 2026 @ Tropicana    : 35/67 = 52.2% NRFI

Blending them produced 43.8% -- a value describing a stadium the Rays
no longer play in, and the largest single input behind a 58% YRFI read
on SD@TB on 2026-08-29 that a park-corrected estimate put at 56% NRFI.
Parks listed in VENUE_CHANGED_SINCE_2025 therefore use 2026 data only.

Oakland is NOT in that list on purpose: the A's played Sutter Health
Park in both 2025 and 2026, so their two seasons are the same building.

SHRINKAGE -- READ BEFORE CHANGING PRIOR_GAMES
---------------------------------------------
Park first-inning NRFI rate is mostly noise. Measured 2026-08-29:

  * year-over-year correlation 2025 vs 2026, 30 parks: r = +0.13
    (r^2 = 0.017 -- last year explains ~2% of this year)
  * observed 2026 spread sd 7.4pp vs 6.2pp expected from coin-flip
    noise alone at ~66 games/park => implied TRUE park sd only ~4.1pp

Two out-of-sample tests (build on one period, score the next):
  * 2025 -> 2026      : best prior ~250-500; prior 50 is WORSE than
                        assigning every park the league mean
  * 2026 H1 -> 2026 H2: best prior >=1000 (i.e. the flat league mean);
                        every finite prior below that loses to flat

PRIOR_GAMES stays at 50 here because the live LR weights were fit
against factors on this spread (sd 4.07pp). Raising the prior to 250
compresses the spread to 1.79pp, which silently shrinks the feature's
real contribution by more than half -- a de facto weight change the
model was never refit for. Doing it properly means refitting the LR
with the new factors under the 3-split OOS protocol in CLAUDE.md, and
that needs the operator's sign-off. Do not raise PRIOR_GAMES on its
own.
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

PRIOR_GAMES = 50  # Bayesian shrinkage prior -- see SHRINKAGE note above

# Parks whose 2025 games were played in a DIFFERENT BUILDING than 2026.
# These use 2026 data only; their 2025 rows are counted for the league
# base rate but never attributed to the park.
VENUE_CHANGED_SINCE_2025 = {"TB"}


def _tally(rows, park_key, result_key, n, nrfi):
    """Accumulate NRFI/total per park. Returns the number of graded rows."""
    seen = 0
    for r in rows:
        park = r.get(park_key, "")
        actual = (r.get(result_key) or "").upper()
        if not park or actual not in ("NRFI", "YRFI"):
            continue
        n[park] = n.get(park, 0) + 1
        nrfi[park] = nrfi.get(park, 0) + (1 if actual == "NRFI" else 0)
        seen += 1
    return seen


def main():
    n_25: dict[str, int] = {}
    k_25: dict[str, int] = {}
    n_26: dict[str, int] = {}
    k_26: dict[str, int] = {}

    with open(BT_2025, encoding="utf-8") as f:
        n_2025 = _tally(csv.DictReader(f), "home", "actual_side", n_25, k_25)
    with open(PICKS_26, encoding="utf-8") as f:
        n_2026 = _tally(csv.DictReader(f), "home_team", "actual_result", n_26, k_26)

    # League base rate uses EVERY graded game, including the seasons we
    # drop for a relocated park -- the base rate is a league fact, not a
    # park fact.
    overall_n = n_2025 + n_2026
    overall_nrfi = sum(k_25.values()) + sum(k_26.values())
    overall_rate = overall_nrfi / overall_n if overall_n else 0.5

    # Per-park counts, skipping 2025 for any relocated club.
    park_n: dict[str, int] = {}
    park_nrfi: dict[str, int] = {}
    for park in set(n_25) | set(n_26):
        use_25 = park not in VENUE_CHANGED_SINCE_2025
        park_n[park] = (n_25.get(park, 0) if use_25 else 0) + n_26.get(park, 0)
        park_nrfi[park] = (k_25.get(park, 0) if use_25 else 0) + k_26.get(park, 0)

    print("Park-factor source mix:")
    print(f"  2025 backtest : {n_2025} graded games")
    print(f"  2026 picks    : {n_2026} graded games")
    print(f"  Total         : {overall_n}, base NRFI rate {overall_rate*100:.2f}%")
    print(f"  Bayesian prior: {PRIOR_GAMES} games per park toward overall mean")
    if VENUE_CHANGED_SINCE_2025:
        moved = ", ".join(sorted(VENUE_CHANGED_SINCE_2025))
        print(f"  Venue changed : {moved} -- 2026 only (2025 was a different building)")
    print()

    rates = {
        park: (park_nrfi[park] + PRIOR_GAMES * overall_rate) / (n + PRIOR_GAMES)
        for park, n in park_n.items()
    }

    cur = {}
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            cur = json.load(f)

    print(f"{'park':<5}  {'old':>6}  {'new':>6}  {'delta':>7}    n  nrfi/total")
    for park in sorted(rates):
        old_v = cur.get(park, 0.5)
        new_v = rates[park]
        delta = new_v - old_v
        flag = "  <-- venue" if park in VENUE_CHANGED_SINCE_2025 else ""
        print(f"{park:<5}  {old_v*100:>5.1f}%  {new_v*100:>5.1f}%  "
              f"{delta*100:>+6.1f}pp  {park_n[park]:>3}  "
              f"{park_nrfi[park]}/{park_n[park]} = "
              f"{park_nrfi[park]/park_n[park]*100:.1f}%{flag}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rates, f, indent=2, sort_keys=True)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()

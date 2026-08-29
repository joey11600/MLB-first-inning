#!/usr/bin/env python3
"""
rebuild_umpire_rates.py -- regenerate data/umpire_rates.json from PERMITTED
seasons, with the shrinkage the evidence actually supports (total).

WHAT WAS WRONG (found 2026-08-29)
---------------------------------
The shipped file's own `training_corpus` field declared it was built from the
**2022 and 2023** backtests -- the exact seasons CLAUDE.md forbids ("pre-pitch-
clock distribution shift makes those seasons hurt the model"). The rule was
applied to the LR's training data and missed here. Measured against 2026:

  * its per-ump ranking was INVERTED -- the tercile it rated most NRFI-
    friendly produced 47.3% NRFI, its most YRFI-friendly tercile 50.5%
    (correlation -0.031 over 1,752 graded games);
  * its league level (0.5084) belongs to the 2022-23 era, not today's
    ~0.495;
  * tools/test_umpire_persistence.py (written 2026-07-27) had already
    reached "NO PERSISTENCE DETECTED ... ABLATE".

THE MEASUREMENT THAT DECIDES THE SHAPE OF THIS FILE
---------------------------------------------------
Cross-season covariance is an unbiased estimate of the TRUE umpire variance,
because sampling noise is independent across seasons.  Umps with >=15 games
in both seasons of each pair (2026-08-29, cache-joined outcomes):

    2024 vs 2025 : n=76  cov=-0.001392  r=-0.175
    2025 vs 2026 : n=78  cov=-0.000278  r=-0.027
    2024 vs 2026 : n=73  cov=-0.000694  r=-0.067

Every pair is NEGATIVE.  tau^2 <= 0: a home-plate umpire's first-inning
NRFI rate carries no information about his own next season.  The empirical-
Bayes shrinkage for tau^2 <= 0 is total -- **every umpire gets the league
rate**.  Publishing per-ump wiggle from any corpus, however recent, would be
knowingly serving noise into a live model.

So this file is deliberately FLAT.  Per-ump raw counts are kept for audit,
but `shrunk_nrfi` (the only per-ump field the predictor and trainer read) is
the league rate for everyone.  The league rate is computed from 2025+2026,
dropping 2024 for its 53.5%-NRFI anomaly -- the same precedent as
rebuild_park_factors.py.

WHY FLATTEN INSTEAD OF DROPPING THE FEATURE
-------------------------------------------
Dropping `home_plate_ump_nrfi_rate` means a 19-feature refit, a predictor
loader change, and a calibrator refit -- the full model-change path.  The
2026-08-29 park experiment (tools/refit2026/park_null.py) showed refit
"gains" on 2026 are dominated by luck, so that path buys risk for nothing.
Flattening the INPUT neutralizes the feature through the frozen weights
(|w| = 0.0054 / 0.0062, ranks 13 and 18 of 20) with no architecture change.
Measured impact with frozen weights + shipped CIR calibrator (excluding one
pre-v3 legacy row whose stored halves are not probabilities):

  * lambda_lr_total shift: mean -0.0020, sd 0.0047, max |d| 0.019
  * tonight's board (2026-08-29): ZERO verdict changes
  * graded 2026, old vs flat: Brier +0.00007 (placebo p=0.475),
    AUC +0.0045 (placebo p=0.705 -- shuffled nonsense averages +0.0034,
    because the old values ranked WORSE than noise).  A wash both ways;
    the justification is input correctness, not performance.

Ablation at the next approved refit remains the right end state -- see the
`umpire_rates_built_on_banned_seasons` memory.

Usage:  python rebuild_umpire_rates.py
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent

BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv"
PICKS_26 = ROOT / "data" / "picks_2026.csv"
UMP_CACHE = ROOT / "data" / "umpire_cache.json"
OUT_PATH = ROOT / "data" / "umpire_rates.json"

# Hard guard: the whole point of this rebuild. Refuse to ever read these.
BANNED_SEASON_MARKERS = ("2022-", "2023-")


def main():
    for p in (BT_2025, PICKS_26, UMP_CACHE):
        if not p.exists():
            sys.exit(f"missing input: {p}")
        if any(m in p.name for m in BANNED_SEASON_MARKERS):
            sys.exit(f"REFUSING banned-season input: {p.name} (CLAUDE.md: no 2022/2023)")

    with open(UMP_CACHE, encoding="utf-8") as f:
        cache = json.load(f)

    # Per-ump counts, 2025 (cache join) + 2026 (ids stored in the ledger).
    n: dict[str, int] = {}
    k: dict[str, int] = {}
    names: dict[str, str] = {}

    n_2025 = 0
    with open(BT_2025, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_side") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            rec = cache.get((r.get("game_pk") or "").strip())
            if not rec:
                continue
            u = str(rec["hp_id"])
            names.setdefault(u, rec.get("hp_name", ""))
            n[u] = n.get(u, 0) + 1
            k[u] = k.get(u, 0) + (1 if a == "NRFI" else 0)
            n_2025 += 1

    n_2026 = 0
    with open(PICKS_26, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            u = (r.get("home_plate_ump_id") or "").strip()
            if not u:
                continue
            rec = cache.get((r.get("game_pk") or "").strip()) or {}
            names.setdefault(u, rec.get("hp_name", ""))
            n[u] = n.get(u, 0) + 1
            k[u] = k.get(u, 0) + (1 if a == "NRFI" else 0)
            n_2026 += 1

    total = sum(n.values())
    league = sum(k.values()) / total

    cur = {}
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            cur = json.load(f)
    old_league = cur.get("league_nrfi_rate")
    old_umps = cur.get("umpires", {})

    print("Umpire-rate source mix:")
    print(f"  2025 backtest : {n_2025} graded games (cache-joined)")
    print(f"  2026 picks    : {n_2026} graded games")
    print(f"  Total         : {total} games, {len(n)} umpires")
    print(f"  League NRFI   : {league*100:.2f}%  (old file: "
          f"{old_league*100:.2f}% from its 2022-23 corpus)" if old_league else
          f"  League NRFI   : {league*100:.2f}%")
    print(f"  Shrinkage     : TOTAL (tau^2 <= 0 across all season pairs; "
          f"every shrunk_nrfi = league). See module docstring.\n")

    print(f"{'umpire':<24}{'n':>5}{'raw':>8}{'old shrunk':>12}{'new shrunk':>12}")
    shown = 0
    for u in sorted(n, key=lambda u: -n[u]):
        if shown < 10:
            old_v = old_umps.get(u, {}).get("shrunk_nrfi")
            print(f"{(names.get(u) or u):<24}{n[u]:>5}{k[u]/n[u]:>8.4f}"
                  f"{(f'{old_v:.4f}' if old_v is not None else '--'):>12}"
                  f"{league:>12.4f}")
            shown += 1
    print(f"  ... and {len(n) - shown} more, all shrunk_nrfi = {league:.4f}\n")

    out = {
        "league_nrfi_rate": round(league, 4),
        "n_games_global": total,
        "shrinkage_games": "total",
        "training_corpus": [BT_2025.name, PICKS_26.name],
        "note": ("FLAT ON PURPOSE. Cross-season covariance of per-ump "
                 "first-inning NRFI rate is negative for 2024v2025, "
                 "2025v2026 and 2024v2026 (tau^2 <= 0), so every "
                 "shrunk_nrfi equals the league rate. Raw per-ump counts "
                 "kept for audit only. Do NOT reintroduce per-ump spread "
                 "without new persistence evidence; see "
                 "rebuild_umpire_rates.py and the "
                 "umpire_rates_built_on_banned_seasons memory."),
        "umpires": {
            u: {
                "name": names.get(u, ""),
                "n_games": n[u],
                "raw_nrfi": round(k[u] / n[u], 4),
                "shrunk_nrfi": round(league, 4),
            }
            for u in sorted(n)
        },
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

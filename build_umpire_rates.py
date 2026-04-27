#!/usr/bin/env python3
"""
build_umpire_rates.py -- compute per-umpire first-inning NRFI rate from
2022-2023 backtest data, with Bayesian shrinkage to league average.

Output: data/umpire_rates.json

Schema:
  {
    "league_nrfi_rate":  0.50,         # observed in training corpus
    "n_games_global":    4500,
    "umpires": {
      "<ump_id>": {
        "name":     "Pat Hoberg",
        "n_games":  152,
        "raw_nrfi": 0.5263,
        "shrunk_nrfi": 0.5180          # blended toward league avg via shrinkage
      },
      ...
    }
  }

Why 2022-2023 only:  these years are upstream of our training window
(2024-2025) and our test window (2026), so umpire stats are computed from
data the model never trains on.  This is the cleanest no-leakage setup.

Why Bayesian shrinkage: umpires with very few games (rookies, fill-ins)
have unreliable observed rates.  Shrinking toward league average gives
realistic priors when sample is small.

Shrinkage formula:  shrunk = (n * raw + K * lg) / (n + K)
where K = SHRINK_GAMES (50 by default; same magnitude as our pitcher
prior IP / team prior games).
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CACHE_PATH = ROOT / "data" / "umpire_cache.json"
OUT_PATH = ROOT / "data" / "umpire_rates.json"

# Use these as the "umpire prior corpus".  They're upstream of our
# training window so umpire stats never leak into LR training.
TRAIN_CORPUS = [
    ROOT / "data" / "backtests" / "backtest_2022-04-01_to_2022-09-30.csv",
    ROOT / "data" / "backtests" / "backtest_2023-04-01_to_2023-09-30.csv",
]

# Bayesian shrinkage constant.  At n_games = 50 the umpire is 50% real /
# 50% league avg.  At n_games = 150 it's 75% real / 25% league avg.
SHRINK_GAMES = 50


def main():
    if not CACHE_PATH.exists():
        sys.exit("Run build_umpire_cache.py first.")
    with open(CACHE_PATH, encoding="utf-8") as f:
        ump_cache = json.load(f)
    print(f"  umpire cache: {len(ump_cache)} games")

    # Build {ump_id -> [(nrfi_outcome, name)]}
    per_ump: dict[str, list[int]] = {}
    name_for: dict[str, str] = {}
    n_games = 0
    n_nrfi  = 0
    n_skipped_no_outcome = 0
    n_skipped_no_ump     = 0

    for path in TRAIN_CORPUS:
        if not path.exists():
            continue
        print(f"  reading {path.name}...")
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pk = (r.get("game_pk") or "").strip()
                if not pk:
                    continue
                rec = ump_cache.get(pk)
                if not rec:
                    n_skipped_no_ump += 1
                    continue
                actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
                if actual not in ("NRFI", "YRFI"):
                    n_skipped_no_outcome += 1
                    continue
                ump_id = str(rec["hp_id"])
                outcome = 1 if actual == "NRFI" else 0
                per_ump.setdefault(ump_id, []).append(outcome)
                name_for[ump_id] = rec.get("hp_name", "")
                n_games += 1
                n_nrfi  += outcome

    league_rate = n_nrfi / n_games if n_games else 0.50
    print(f"\n  Training corpus: {n_games} games  ({n_nrfi} NRFI = {league_rate*100:.2f}%)")
    print(f"  Skipped (no ump): {n_skipped_no_ump}")
    print(f"  Skipped (no outcome): {n_skipped_no_outcome}")
    print(f"  Distinct umpires: {len(per_ump)}")

    out_umps = {}
    for ump_id, outcomes in per_ump.items():
        n = len(outcomes)
        raw = sum(outcomes) / n if n else league_rate
        shrunk = (n * raw + SHRINK_GAMES * league_rate) / (n + SHRINK_GAMES)
        out_umps[ump_id] = {
            "name":        name_for.get(ump_id, ""),
            "n_games":     n,
            "raw_nrfi":    round(raw, 4),
            "shrunk_nrfi": round(shrunk, 4),
        }

    out = {
        "league_nrfi_rate": round(league_rate, 4),
        "n_games_global":   n_games,
        "shrinkage_games":  SHRINK_GAMES,
        "training_corpus":  [p.name for p in TRAIN_CORPUS],
        "umpires":          out_umps,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\n  Wrote {OUT_PATH}")

    # Distribution diagnostics
    rates = sorted(u["shrunk_nrfi"] for u in out_umps.values() if u["n_games"] >= 30)
    if rates:
        print(f"\n  Shrunk NRFI rate distribution (umps with >=30 games, n={len(rates)}):")
        print(f"    min:    {rates[0]*100:.2f}%")
        print(f"    p10:    {rates[len(rates)//10]*100:.2f}%")
        print(f"    median: {rates[len(rates)//2]*100:.2f}%")
        print(f"    p90:    {rates[len(rates)*9//10]*100:.2f}%")
        print(f"    max:    {rates[-1]*100:.2f}%")
        print(f"    range:  {(rates[-1] - rates[0])*100:.2f}pp")

        print(f"\n  Top 5 NRFI-friendly umpires (>=80 games):")
        ranked = sorted([u for u in out_umps.values() if u["n_games"] >= 80],
                         key=lambda u: -u["shrunk_nrfi"])
        for u in ranked[:5]:
            print(f"    {u['name']:<22} n={u['n_games']:>3}  "
                  f"raw={u['raw_nrfi']*100:>5.1f}%  shrunk={u['shrunk_nrfi']*100:>5.1f}%")
        print(f"\n  Bottom 5 (most YRFI-friendly):")
        for u in ranked[-5:]:
            print(f"    {u['name']:<22} n={u['n_games']:>3}  "
                  f"raw={u['raw_nrfi']*100:>5.1f}%  shrunk={u['shrunk_nrfi']*100:>5.1f}%")


if __name__ == "__main__":
    main()

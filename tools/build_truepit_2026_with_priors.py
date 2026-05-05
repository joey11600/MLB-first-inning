#!/usr/bin/env python3
"""
tools/build_truepit_2026_with_priors.py -- priors-aware truepit features.

For each pitcher x date in 2026, computes a Bayesian-pooled xera +
whiff_pct_rank estimate that blends:
  - 2025 full-season per-pitch cumulative (the prior)
  - 2026 cumulative-through-yesterday per-pitch (the new data)

Pooling is sample-size weighted: each pitch from either season
contributes equally to the running average.  This is full Bayesian
pooling (alpha=1 -- treats 2025 and 2026 as same population).  Early-
2026 games where the pitcher has minimal 2026 data will be ~95%
prior-driven; end-of-season 2026 will approach 50/50.

For pitchers without 2025 data (rookies in 2026), the prior is empty
so the pooled values equal the 2026 cumulative.  These pitchers will
default to league-avg xera + neutral whiff rank early, same as the
priors-NAIVE truepit.

Reads:
  data/cache/perpitch_2026/perpitch_{pid}_2026.json   (built by
    tools/backfill_truepit_2026.py)
  data/cache/perpitch/perpitch_{pid}_2025.json        (built by
    tools/backfill_xera_pit_perpitch.py for 2025 truepit)

Writes:
  data/v2_perfect_2026/truepit_priors_per_pitcher_per_date.json

USAGE
-----
  python tools/build_truepit_2026_with_priors.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.backfill_xera_pit_perpitch import (
    xwoba_to_xera_proxy,
    NEUTRAL_PCT_RANK,
    SWING_DESCRIPTIONS,
    WHIFF_DESCRIPTIONS,
    LEAGUE_AVG_XERA,
)


def aggregate_2025_prior(pitches_2025: list) -> dict:
    """Compute end-of-2025 per-pitcher totals."""
    sum_xwoba = 0.0
    n_balls = 0
    n_swings = 0
    n_whiffs = 0
    for p in pitches_2025:
        x = p.get("estimated_woba_using_speedangle")
        if x is not None:
            sum_xwoba += x
            n_balls += 1
        desc = p.get("description") or ""
        if desc in SWING_DESCRIPTIONS:
            n_swings += 1
        if desc in WHIFF_DESCRIPTIONS:
            n_whiffs += 1
    return {"sum_xwoba": sum_xwoba, "n_balls": n_balls,
            "n_swings": n_swings, "n_whiffs": n_whiffs}


def build_pooled_per_date(pitches_2026: list, prior: dict) -> dict:
    """For each game date in 2026, build the cumulative-through-yesterday
    snapshot POOLED with the 2025 prior.  Returns dict mapping date_iso
    to dict with cum_xwoba_pooled, cum_whiff_pct_pooled, raw counts."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for p in pitches_2026:
        d = p.get("game_date")
        if d:
            by_date[d].append(p)
    sorted_dates = sorted(by_date.keys())

    out: dict[str, dict] = {}
    cum_woba_sum_2026 = 0.0
    cum_woba_n_2026 = 0
    cum_swings_2026 = 0
    cum_whiffs_2026 = 0

    for d in sorted_dates:
        # Pooled (prior + cumulative-through-yesterday) snapshot.
        pooled_n_balls = prior["n_balls"] + cum_woba_n_2026
        pooled_sum_xwoba = prior["sum_xwoba"] + cum_woba_sum_2026
        pooled_n_swings = prior["n_swings"] + cum_swings_2026
        pooled_n_whiffs = prior["n_whiffs"] + cum_whiffs_2026

        cum_xwoba = (pooled_sum_xwoba / pooled_n_balls) if pooled_n_balls else None
        whiff_pct = (pooled_n_whiffs / pooled_n_swings) if pooled_n_swings else None

        out[d] = {
            "cum_xwoba_pooled": cum_xwoba,
            "cum_whiff_pct_pooled": whiff_pct,
            "pooled_n_balls": pooled_n_balls,
            "pooled_n_swings": pooled_n_swings,
            "n_balls_2026": cum_woba_n_2026,
            "n_swings_2026": cum_swings_2026,
            "prior_n_balls": prior["n_balls"],
            "prior_n_swings": prior["n_swings"],
        }

        # Then add today's pitches to 2026 cumulative for tomorrow's snapshot.
        for p in by_date[d]:
            x = p.get("estimated_woba_using_speedangle")
            if x is not None:
                cum_woba_sum_2026 += x
                cum_woba_n_2026 += 1
            desc = p.get("description") or ""
            if desc in SWING_DESCRIPTIONS:
                cum_swings_2026 += 1
            if desc in WHIFF_DESCRIPTIONS:
                cum_whiffs_2026 += 1
    return out


def build_perdate_rank_pooled(by_pitcher: dict[int, dict],
                               min_swings: int = 200) -> dict[str, dict[int, int]]:
    """Cross-pitcher percentile rank per date for POOLED whiff_pct.
    Same threshold (>=200 swings) as the priors-naive version, but
    here pooled_n_swings includes prior swings -- so most established
    pitchers will clear the threshold from the FIRST 2026 game."""
    all_dates: set[str] = set()
    for perdate in by_pitcher.values():
        all_dates.update(perdate.keys())

    out: dict[str, dict[int, int]] = {}
    for d in sorted(all_dates):
        ratings: list[tuple[int, float]] = []
        for pid, perdate in by_pitcher.items():
            snap = perdate.get(d)
            if not snap:
                continue
            n_sw = snap.get("pooled_n_swings") or 0
            wp = snap.get("cum_whiff_pct_pooled")
            if n_sw < min_swings or wp is None:
                continue
            ratings.append((pid, wp))
        if not ratings:
            continue
        ratings.sort(key=lambda x: x[1])
        n = len(ratings)
        for i, (pid, _) in enumerate(ratings):
            pctile = int(round(((i + 1) / n) * 100))
            out.setdefault(d, {})[pid] = pctile
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out",
                    default="data/v2_perfect_2026/truepit_priors_per_pitcher_per_date.json",
                    help="Output JSON path.")
    args = ap.parse_args()

    cache_2026 = REPO_ROOT / "data" / "cache" / "perpitch_2026"
    cache_2025 = REPO_ROOT / "data" / "cache" / "perpitch"
    if not cache_2026.exists():
        sys.exit(f"Missing {cache_2026}.  Run tools/backfill_truepit_2026.py first.")

    print("=" * 80)
    print("  Priors-aware truepit for 2026  (2025 cumulative as Bayesian prior)")
    print("=" * 80)

    files_2026 = sorted(cache_2026.glob("perpitch_*_2026.json"))
    print(f"\n  {len(files_2026)} pitchers in 2026 cache")
    if cache_2025.exists():
        n_2025_files = sum(1 for _ in cache_2025.glob("perpitch_*_2025.json"))
        print(f"  {n_2025_files} pitchers in 2025 cache (potential priors)")
    else:
        print(f"  (no 2025 cache at {cache_2025} -- all priors will be empty)")

    n_with_prior = 0
    n_without_prior = 0
    n_skipped = 0
    pooled_by_pitcher: dict[int, dict] = {}

    for f26 in files_2026:
        try:
            stem = f26.stem
            pid = int(stem.replace("perpitch_", "").replace("_2026", ""))
        except ValueError:
            n_skipped += 1
            continue

        with open(f26, encoding="utf-8") as f:
            data_2026 = json.load(f)
        pitches_2026 = data_2026.get("pitches", [])
        if not pitches_2026:
            continue

        prior = {"sum_xwoba": 0.0, "n_balls": 0, "n_swings": 0, "n_whiffs": 0}
        f25 = cache_2025 / f"perpitch_{pid}_2025.json"
        if f25.exists():
            try:
                with open(f25, encoding="utf-8") as f:
                    data_2025 = json.load(f)
                pitches_2025 = data_2025.get("pitches", [])
                if pitches_2025:
                    prior = aggregate_2025_prior(pitches_2025)
                    n_with_prior += 1
                else:
                    n_without_prior += 1
            except Exception:    # noqa: BLE001
                n_without_prior += 1
        else:
            n_without_prior += 1

        per_date = build_pooled_per_date(pitches_2026, prior)
        pooled_by_pitcher[pid] = per_date

    print(f"\n  Pitchers with 2025 prior: {n_with_prior}")
    print(f"  Pitchers WITHOUT 2025 prior (rookies / new): {n_without_prior}")
    if n_with_prior:
        # Show prior-strength stats for rough calibration check
        sample_priors = []
        for pid, perdate in pooled_by_pitcher.items():
            for snap in perdate.values():
                p_n = snap.get("prior_n_balls", 0)
                if p_n > 0:
                    sample_priors.append(p_n)
                    break
        if sample_priors:
            print(f"  Prior-strength sample (n_balls in 2025): "
                  f"min={min(sample_priors)}, "
                  f"median={sorted(sample_priors)[len(sample_priors)//2]}, "
                  f"max={max(sample_priors)}")

    # Cross-pitcher percentile rank for whiff_pct (pooled)
    print(f"\n  Computing cross-pitcher percentile rank...")
    whiff_rank = build_perdate_rank_pooled(pooled_by_pitcher, min_swings=200)
    print(f"  Rank-able dates: {len(whiff_rank)}")

    # Write JSON in the same shape as the priors-naive truepit so the
    # backtest script can consume it via --truepit flag.
    output = {
        "season":     2026,
        "method":     "priors_aware_pooled (alpha=1, full pooling)",
        "fitted_at":  datetime.now(timezone.utc).replace(tzinfo=None)
                              .isoformat(timespec="seconds") + "Z",
        "n_pitchers": len(pooled_by_pitcher),
        "n_with_prior":    n_with_prior,
        "n_without_prior": n_without_prior,
        "per_pitcher": {
            str(pid): {
                d: {
                    "xera":           round(xwoba_to_xera_proxy(
                                          snap.get("cum_xwoba_pooled")), 4),
                    "whiff_pct_rank": whiff_rank.get(d, {}).get(pid,
                                                                  NEUTRAL_PCT_RANK),
                    "pooled_n_balls":  snap.get("pooled_n_balls", 0),
                    "pooled_n_swings": snap.get("pooled_n_swings", 0),
                    "n_balls_2026":   snap.get("n_balls_2026", 0),
                    "prior_n_balls":  snap.get("prior_n_balls", 0),
                }
                for d, snap in perdate.items()
            }
            for pid, perdate in pooled_by_pitcher.items()
        },
    }
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Saved -> {out_path}")
    print(f"  Pitchers={len(pooled_by_pitcher)}  rank-able dates={len(whiff_rank)}")


if __name__ == "__main__":
    main()

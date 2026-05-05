#!/usr/bin/env python3
"""
tools/build_2025_priors_aggregates.py -- one-off: extract 2025 per-pitcher aggregates
into a small JSON checked into git so daily-refresh runners don't need the
gitignored per-pitch 2025 cache.

PROBLEM
-------
data/cache/ is gitignored.  build_truepit_2026_with_priors.py loads each
pitcher's full 2025 per-pitch JSON (~25-50KB each, ~350 pitchers, ~9MB
total) just to compute four aggregates: sum_xwoba, n_balls, n_swings,
n_whiffs.  On a fresh GitHub Actions runner the per-pitch 2025 cache
isn't there, so n_with_prior would be zero -> NO shrinkage applied ->
truepit_priors JSON is bad.

THIS SCRIPT
-----------
Scans the local 2025 per-pitch cache once, computes the per-pitcher
aggregates (just the four numbers), and writes them to a small file at:

    data/v2_perfect_2026/2025_priors_aggregates.json

That file IS tracked in git (~50KB), and build_truepit_2026_with_priors.py
will read it on the daily-refresh runner instead of the per-pitch files.

Re-run this script whenever the 2025 cache is updated (which should be
never -- 2025 is over -- but just in case).

USAGE
-----
    python tools/build_2025_priors_aggregates.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.backfill_xera_pit_perpitch import (
    SWING_DESCRIPTIONS,
    WHIFF_DESCRIPTIONS,
)

CACHE_2025 = REPO_ROOT / "data" / "cache" / "perpitch"
OUT        = REPO_ROOT / "data" / "v2_perfect_2026" / "2025_priors_aggregates.json"


def aggregate(pitches: list) -> dict:
    sum_xwoba = 0.0
    n_balls = n_swings = n_whiffs = 0
    for p in pitches:
        x = p.get("estimated_woba_using_speedangle")
        if x is not None:
            sum_xwoba += x
            n_balls += 1
        desc = p.get("description") or ""
        if desc in SWING_DESCRIPTIONS:
            n_swings += 1
        if desc in WHIFF_DESCRIPTIONS:
            n_whiffs += 1
    return {
        "sum_xwoba": round(sum_xwoba, 6),
        "n_balls":   n_balls,
        "n_swings":  n_swings,
        "n_whiffs":  n_whiffs,
    }


def main():
    if not CACHE_2025.exists():
        sys.exit(f"Missing 2025 per-pitch cache at {CACHE_2025}.  Run "
                 f"tools/backfill_xera_pit_perpitch.py first.")

    files = sorted(CACHE_2025.glob("perpitch_*_2025.json"))
    if not files:
        sys.exit(f"No 2025 per-pitch files in {CACHE_2025}.  Aborting.")

    print("=" * 70)
    print(f"  Extracting 2025 priors aggregates from {len(files)} files")
    print("=" * 70)

    per_pitcher: dict[str, dict] = {}
    n_with_pitches = 0
    for f in files:
        try:
            stem = f.stem
            pid = stem.replace("perpitch_", "").replace("_2025", "")
            int(pid)   # validate
        except ValueError:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            print(f"  skip {f.name}: {exc}")
            continue
        pitches = data.get("pitches") or []
        if not pitches:
            continue
        per_pitcher[pid] = aggregate(pitches)
        n_with_pitches += 1

    print(f"\n  Aggregated {n_with_pitches} pitchers")
    if n_with_pitches > 0:
        # Quick sanity: max + median n_balls
        n_balls_list = sorted(p["n_balls"] for p in per_pitcher.values())
        median = n_balls_list[len(n_balls_list) // 2]
        print(f"  n_balls:  min={min(n_balls_list)}  median={median}  "
              f"max={max(n_balls_list)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "season":     2025,
        "fitted_at":  datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
        "n_pitchers": n_with_pitches,
        "schema":     "per_pitcher[pid_str] = {sum_xwoba, n_balls, n_swings, n_whiffs}",
        "note":       ("Pre-computed 2025 priors aggregates.  Used by "
                       "tools/build_truepit_2026_with_priors.py on the daily "
                       "refresh runner where the gitignored per-pitch 2025 "
                       "cache is not available."),
        "per_pitcher": per_pitcher,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    size_kb = OUT.stat().st_size / 1024
    print(f"\n  Saved -> {OUT}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()

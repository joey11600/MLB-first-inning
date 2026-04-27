#!/usr/bin/env python3
"""
build_statcast_cache.py -- fetch Baseball Savant per-pitcher per-season
xERA + K%/BB%/Whiff%/Chase% percentile ranks for 2022-2026.

Output: data/statcast_pitcher_cache.json

Schema:
  {
    "<season>": {
      "<player_id>": {
        "name":           "Logan Webb",
        "xera":           4.37,    # raw expected ERA from quality of contact
        "est_woba":       0.326,   # raw expected wOBA against
        "k_pct_rank":     81,      # percentile rank (1-100 scale)
        "bb_pct_rank":    72,
        "whiff_pct_rank": 75,
        "chase_pct_rank": 68,
      },
      ...
    },
    ...
  }

Two endpoints combined:
  - statcast_pitcher_expected_stats(year):  xERA + xwOBA + xBA (raw)
  - statcast_pitcher_percentile_ranks(year): K%/BB%/Whiff%/Chase% (percentile)
"""

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

try:
    import pybaseball as pb
except ImportError:
    sys.exit("pip install pybaseball")

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "data" / "statcast_pitcher_cache.json"
SEASONS = [2022, 2023, 2024, 2025, 2026]


def fetch_year(year: int) -> dict:
    print(f"  fetching {year}...")
    out: dict[str, dict] = {}

    # Expected stats: xERA, xwOBA (raw values)
    try:
        exp = pb.statcast_pitcher_expected_stats(year)
        for _, row in exp.iterrows():
            pid = str(int(row["player_id"]))
            out[pid] = {
                "name":     row.get("last_name, first_name", ""),
                "xera":     float(row["xera"]) if not _isnan(row.get("xera")) else None,
                "est_woba": float(row["est_woba"]) if not _isnan(row.get("est_woba")) else None,
                "est_ba":   float(row["est_ba"])   if not _isnan(row.get("est_ba"))   else None,
            }
        print(f"    expected_stats: {len(out)} pitchers")
    except Exception as e:
        print(f"    expected_stats ERROR: {e}")

    # Percentile ranks: K%, BB%, Whiff%, Chase% (1-100 scale)
    try:
        ranks = pb.statcast_pitcher_percentile_ranks(year)
        n_added = 0
        for _, row in ranks.iterrows():
            pid = str(int(row["player_id"]))
            kp = row.get("k_percent")
            if _isnan(kp):
                continue  # only keep pitchers with K% data (filters out very-low-IP)
            rec = out.setdefault(pid, {"name": row.get("player_name", "")})
            rec["k_pct_rank"]     = int(kp)
            rec["bb_pct_rank"]    = int(row["bb_percent"])    if not _isnan(row.get("bb_percent"))    else None
            rec["whiff_pct_rank"] = int(row["whiff_percent"]) if not _isnan(row.get("whiff_percent")) else None
            rec["chase_pct_rank"] = int(row["chase_percent"]) if not _isnan(row.get("chase_percent")) else None
            n_added += 1
        print(f"    percentile_ranks: {n_added} pitchers merged")
    except Exception as e:
        print(f"    percentile_ranks ERROR: {e}")

    return out


def _isnan(v):
    if v is None: return True
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def main():
    cache: dict = {}
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  loaded existing cache: {sum(len(v) for v in cache.values())} entries")

    for year in SEASONS:
        ystr = str(year)
        if ystr in cache and len(cache[ystr]) > 100:
            print(f"  {year} already cached ({len(cache[ystr])} pitchers); skipping")
            continue
        cache[ystr] = fetch_year(year)
        # Save after each year (resume-able)
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=0, separators=(",", ":"))

    # Summary
    print(f"\n  Cache saved -> {OUT_PATH}")
    for year in SEASONS:
        d = cache.get(str(year), {})
        n_xera = sum(1 for v in d.values() if v.get("xera") is not None)
        n_kpct = sum(1 for v in d.values() if v.get("k_pct_rank") is not None)
        print(f"    {year}: {len(d)} pitchers  ({n_xera} with xERA, {n_kpct} with K% rank)")


if __name__ == "__main__":
    main()

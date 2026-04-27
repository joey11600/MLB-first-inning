#!/usr/bin/env python3
"""
build_pitcher_id_cache.py -- map every game_pk in our 2022-2026 data to
(away_pitcher_id, home_pitcher_id) using the schedule API.

Output: data/pitcher_id_cache.json
Schema: { "<game_pk>": [<away_pid>, <home_pid>], ... }

Reuses existing helpers from backfill_top3_splits.py for daily-cache
fast path; falls back to season-wide schedule fetch when no cache exists.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from backfill_top3_splits import load_schedule_pid_map
from backfill_top3_splits_backtest import fetch_season_schedule_pid_map

OUT_PATH = ROOT / "data" / "pitcher_id_cache.json"

CSV_PATHS = [
    (2022, ROOT / "data" / "backtests" / "backtest_2022-04-01_to_2022-09-30.csv"),
    (2023, ROOT / "data" / "backtests" / "backtest_2023-04-01_to_2023-09-30.csv"),
    (2024, ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"),
    (2025, ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"),
    (2026, ROOT / "data" / "picks_2026.csv"),
]


def load_cache() -> dict:
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(c: dict):
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f, separators=(",", ":"))


def main():
    cache = load_cache()
    print(f"  cache start: {len(cache)} entries\n")

    # Build per-season pitcher_id maps
    season_maps: dict[int, dict[int, tuple[int, int]]] = {}
    for season, path in CSV_PATHS:
        if not path.exists():
            print(f"  {season}: missing {path}; skip")
            continue
        m = load_schedule_pid_map(season)
        if m:
            print(f"  {season}: loaded daily cache ({len(m)} games)")
        else:
            print(f"  {season}: no daily cache; fetching full season schedule...")
            m = fetch_season_schedule_pid_map(season)
            print(f"  {season}: fetched {len(m)} games")
        season_maps[season] = m

    # Iterate target CSVs and add to cache
    n_added = 0
    for season, path in CSV_PATHS:
        if not path.exists(): continue
        sm = season_maps.get(season, {})
        rows_in_csv = 0
        rows_with_pid = 0
        rows_added = 0
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pk = (r.get("game_pk") or "").strip()
                if not pk: continue
                rows_in_csv += 1
                pids = sm.get(int(pk))
                if pids:
                    if pk not in cache:
                        cache[pk] = list(pids)
                        rows_added += 1
                    rows_with_pid += 1
        n_added += rows_added
        print(f"  {season} ({path.name}): {rows_with_pid}/{rows_in_csv} games matched, {rows_added} added")

    save_cache(cache)
    print(f"\n  Cache saved -> {OUT_PATH}")
    print(f"  Total entries: {len(cache)}")


if __name__ == "__main__":
    main()

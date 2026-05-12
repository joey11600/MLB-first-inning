#!/usr/bin/env python3
"""
build_umpire_cache.py -- fetch home plate umpire id for every game in our
backtests + picks_2026.csv, cache to data/umpire_cache.json.

Uses statsapi schedule endpoint with officials hydration -- one call per
date covers all games that day (~15-20 games), so total API calls are
~900 (one per regular-season date over 5 years) instead of 10K+.

Output schema:
  {
    "<game_pk>": {
      "hp_id":   <int>,        # home plate umpire MLBAM player id
      "hp_name": "<str>",      # full name (for human inspection only)
      "date":    "YYYY-MM-DD",
    },
    ...
  }

Idempotent: rerun safely; only fetches dates not already covered.
"""

import csv
import json
import sys
import time
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("pip install mlb-statsapi")

ROOT = Path(__file__).parent
CACHE_PATH = ROOT / "data" / "umpire_cache.json"

CSV_PATHS = [
    ROOT / "data" / "backtests" / "backtest_2022-04-01_to_2022-09-30.csv",
    ROOT / "data" / "backtests" / "backtest_2023-04-01_to_2023-09-30.csv",
    ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv",
    ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv",
    ROOT / "data" / "picks_2026.csv",
]

# Schedule API often coughs on dates with no games (off-season); that's fine
SLEEP_BETWEEN_CALLS = 0.20


def load_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(c: dict):
    CACHE_PATH.parent.mkdir(exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=0, separators=(",", ":"))


def gather_target_dates() -> tuple[set[str], dict[str, set[str]]]:
    """Collect unique (date, game_pk) pairs across all input CSVs."""
    dates: set[str] = set()
    pk_by_date: dict[str, set[str]] = {}
    for path in CSV_PATHS:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = (r.get("date") or "").strip()
                pk = (r.get("game_pk") or "").strip()
                if d and pk:
                    dates.add(d)
                    pk_by_date.setdefault(d, set()).add(pk)
    return dates, pk_by_date


def fetch_date(date_iso: str) -> dict[str, dict]:
    """Returns {pk: {hp_id, hp_name, date}} for a given date.  Empty on error."""
    try:
        data = statsapi.get("schedule", {
            "sportId":  1,
            "date":     date_iso,
            "hydrate":  "officials",
            "fields":   "dates,games,gamePk,officials,officialType,official,fullName,id",
        })
    except Exception as e:
        print(f"  [{date_iso}] schedule error: {e}")
        return {}
    out: dict[str, dict] = {}
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = str(g.get("gamePk") or "")
            if not pk:
                continue
            hp_id = 0; hp_name = ""
            for o in g.get("officials", []):
                if o.get("officialType") == "Home Plate":
                    official = o.get("official", {}) or {}
                    hp_id = int(official.get("id") or 0)
                    hp_name = (official.get("fullName") or "").strip()
                    break
            if hp_id:
                out[pk] = {"hp_id": hp_id, "hp_name": hp_name, "date": date_iso}
    return out


def main():
    cache = load_cache()
    print(f"  cache start: {len(cache)} entries")

    dates, pk_by_date = gather_target_dates()
    target_pks = set()
    for s in pk_by_date.values():
        target_pks |= s
    missing_pks = target_pks - set(cache.keys())
    print(f"  total target games: {len(target_pks)}")
    print(f"  missing in cache:   {len(missing_pks)}")

    # Identify dates that still have ANY missing pk
    missing_dates = sorted({
        d for d in dates if (pk_by_date.get(d, set()) - set(cache.keys()))
    })
    print(f"  dates needing fetch: {len(missing_dates)}\n")

    n_added = 0
    for i, d in enumerate(missing_dates):
        if i and i % 50 == 0:
            save_cache(cache)
            print(f"  ... {i}/{len(missing_dates)}  cache size {len(cache)}")
        per_day = fetch_date(d)
        for pk, rec in per_day.items():
            if pk not in cache:
                cache[pk] = rec
                n_added += 1
        time.sleep(SLEEP_BETWEEN_CALLS)

    save_cache(cache)
    print(f"\n  Done.  Cache size {len(cache)} (added {n_added}).")
    print(f"  Saved -> {CACHE_PATH}")

    # Coverage report per CSV
    print(f"\n  Coverage by CSV:")
    for path in CSV_PATHS:
        if not path.exists():
            continue
        n = covered = 0
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pk = (r.get("game_pk") or "").strip()
                if not pk: continue
                n += 1
                if pk in cache: covered += 1
        pct = covered / n * 100 if n else 0
        print(f"    {path.name:<55}  {covered:>5}/{n:>5} ({pct:>5.1f}%)")


if __name__ == "__main__":
    main()

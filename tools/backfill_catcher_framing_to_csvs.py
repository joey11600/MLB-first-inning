#!/usr/bin/env python3
"""
tools/backfill_catcher_framing_to_csvs.py — write catcher framing columns
into 2024 + 2025 backtest CSVs (T4.1).

INPUTS
------
  data/catcher_framing_cache.json     # built by build_catcher_framing.py
  data/cache/catchers_per_game.json   # built by extract_catchers_per_game.py

For each row in each backtest CSV:
  - home_catcher_id = catchers_per_game[game_pk]["home_catcher_id"]
  - away_catcher_id = catchers_per_game[game_pk]["away_catcher_id"]
  - home_catcher_framing = framing_cache[season][home_catcher_id]["framing_score"]
  - away_catcher_framing = framing_cache[season][away_catcher_id]["framing_score"]
  - home_catcher_extra_strikes = framing_cache[season][home_catcher_id]["extra_strikes"]
  - away_catcher_extra_strikes = framing_cache[season][away_catcher_id]["extra_strikes"]

If catcher_id is missing or framing data isn't available for that catcher,
fall back to 0.0 (neutral framer).  Same fallback for backups / callups
who didn't make the framing min_pitches filter (50 shadow pitches).

OUTPUT
------
Writes new columns IN PLACE to:
  data/backtests/backtest_2024-..._truepit.csv
  data/backtests/backtest_2025-..._truepit.csv

(_truepit because catcher framing is itself a season-aggregate value;
no point joining it into the leaky CSVs.  In a future iteration we'd
make it point-in-time too.)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NEW_COLS = [
    "home_catcher_id", "away_catcher_id",
    "home_catcher_framing", "away_catcher_framing",
    "home_catcher_extra_strikes", "away_catcher_extra_strikes",
]


def load_caches():
    framing_path  = REPO_ROOT / "data" / "catcher_framing_cache.json"
    catchers_path = REPO_ROOT / "data" / "cache" / "catchers_per_game.json"
    if not framing_path.exists():
        sys.exit(f"Missing {framing_path}.  Run tools/build_catcher_framing.py first.")
    if not catchers_path.exists():
        sys.exit(f"Missing {catchers_path}.  Run tools/extract_catchers_per_game.py first.")
    with open(framing_path,  encoding="utf-8") as f: framing  = json.load(f)
    with open(catchers_path, encoding="utf-8") as f: catchers = json.load(f)
    return framing, catchers


def lookup_catcher_framing(framing: dict, season: int, catcher_id):
    """Return (framing_score, extra_strikes) for catcher in given season,
    or (0.0, 0.0) if missing.  0.0 = neutral framer fallback."""
    if catcher_id is None:
        return 0.0, 0.0
    season_data = framing.get(str(season), {})
    rec = season_data.get(str(catcher_id))
    if not rec:
        return 0.0, 0.0
    return float(rec.get("framing_score", 0.0)), float(rec.get("extra_strikes", 0.0))


def backfill_csv(season: int, framing: dict, catchers_per_game: dict) -> dict:
    """Returns stats summary."""
    src = REPO_ROOT / "data" / "backtests" / f"backtest_{season}-04-01_to_{season}-09-30_truepit.csv"
    if not src.exists():
        return {"season": season, "skipped": True, "reason": f"missing {src}"}

    with open(src, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    # Append new columns if not already present
    for c in NEW_COLS:
        if c not in header:
            header.append(c)

    # Stats
    n_rows = len(rows)
    n_with_both_catchers = 0
    n_with_framing       = 0
    n_no_catcher_id      = 0
    n_no_framing_data    = 0

    for r in rows:
        gpk = (r.get("game_pk") or "").strip()
        cgame = catchers_per_game.get(gpk, {})
        h_cid = cgame.get("home_catcher_id")
        a_cid = cgame.get("away_catcher_id")
        if h_cid and a_cid:
            n_with_both_catchers += 1
        if not h_cid and not a_cid:
            n_no_catcher_id += 1

        h_score, h_extra = lookup_catcher_framing(framing, season, h_cid)
        a_score, a_extra = lookup_catcher_framing(framing, season, a_cid)

        # If we had catcher_ids but no framing data, count as missing
        if h_cid and h_score == 0.0:
            # Could be neutral framer OR missing data; can't tell apart.
            # Count as "no framing" only when score is exactly 0 AND no catcher.
            pass
        if h_score != 0.0 or a_score != 0.0:
            n_with_framing += 1

        r["home_catcher_id"]              = str(h_cid) if h_cid else ""
        r["away_catcher_id"]              = str(a_cid) if a_cid else ""
        r["home_catcher_framing"]         = f"{h_score:.4f}"
        r["away_catcher_framing"]         = f"{a_score:.4f}"
        r["home_catcher_extra_strikes"]   = f"{h_extra:.1f}"
        r["away_catcher_extra_strikes"]   = f"{a_extra:.1f}"

    with open(src, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "season": season,
        "n_rows": n_rows,
        "n_with_both_catchers": n_with_both_catchers,
        "n_with_framing":       n_with_framing,
        "n_no_catcher_id":      n_no_catcher_id,
        "src": str(src),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, action="append", default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    seasons = [2024, 2025] if args.all else (args.season or [2024, 2025])
    framing, catchers_per_game = load_caches()

    print()
    print("=" * 80)
    print("  Backfilling catcher framing into _truepit CSVs (T4.1)")
    print("=" * 80)
    print(f"  framing cache:    {len(framing)} seasons, "
          f"{sum(len(v) for v in framing.values() if isinstance(v, dict))} total entries")
    print(f"  catchers cache:   {len(catchers_per_game)} games")
    print()

    for season in seasons:
        res = backfill_csv(season, framing, catchers_per_game)
        if res.get("skipped"):
            print(f"  {season}: SKIPPED -- {res['reason']}")
            continue
        n_rows = res["n_rows"]
        print(f"  {season}: {n_rows} rows")
        print(f"    games with both catchers identified: "
              f"{res['n_with_both_catchers']}/{n_rows} "
              f"({100*res['n_with_both_catchers']/max(n_rows,1):.1f}%)")
        print(f"    games with framing data:             "
              f"{res['n_with_framing']}/{n_rows} "
              f"({100*res['n_with_framing']/max(n_rows,1):.1f}%)")
        print(f"    games with NO catcher_id at all:     "
              f"{res['n_no_catcher_id']}/{n_rows}")
        print(f"    wrote {res['src']}")
        print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/extract_catchers_per_game.py — extract first-inning catcher_id per
game from per-pitch Statcast data (T4.1).

For each game we want the catcher who was on field during the first
inning, broken out by half:
  home_catcher_id = fielder_2 from a T1 pitch (top of 1st = away batting,
                                                home pitching, home catcher)
  away_catcher_id = fielder_2 from a B1 pitch (bot of 1st = home batting,
                                                away pitching, away catcher)

Why first-inning specifically: catchers occasionally swap mid-game.  Our
model predicts the FIRST inning, so we want the catcher actually on
field for that inning -- not necessarily the season's "primary" catcher.

OUTPUT
------
data/cache/catchers_per_game.json:
  {
    "<game_pk>": {
      "home_catcher_id": int,     # T1 catcher
      "away_catcher_id": int,     # B1 catcher
      "date":            "YYYY-MM-DD",
    },
    ...
  }

USAGE
-----
  python tools/extract_catchers_per_game.py --season 2024
  python tools/extract_catchers_per_game.py --all     # 2024 + 2025
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH  = REPO_ROOT / "data" / "cache" / "catchers_per_game.json"
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


def fetch_first_inning_catchers(season: int) -> dict:
    """For each game in the season, fetch first-inning pitches and extract
    the catcher (fielder_2) for each half."""
    try:
        import pybaseball as pb
    except ImportError:
        sys.exit("pip install pybaseball")

    games: dict[int, dict] = {}
    months = [
        (f"{season}-04-01", f"{season}-04-30"),
        (f"{season}-05-01", f"{season}-05-31"),
        (f"{season}-06-01", f"{season}-06-30"),
        (f"{season}-07-01", f"{season}-07-31"),
        (f"{season}-08-01", f"{season}-08-31"),
        (f"{season}-09-01", f"{season}-09-30"),
    ]
    for start, end in months:
        print(f"    fetching {start} to {end}...", flush=True)
        try:
            df = pb.statcast(start_dt=start, end_dt=end)
        except Exception as exc:    # noqa: BLE001
            print(f"      ERROR: {exc!r}", file=sys.stderr)
            continue
        if df is None or len(df) == 0:
            continue
        # Filter to inning 1 pitches only
        first_inning = df[df["inning"] == 1]
        # Group by game_pk
        for gpk, g in first_inning.groupby("game_pk"):
            try:
                gpk_int = int(gpk)
            except (TypeError, ValueError):
                continue
            rec = games.setdefault(gpk_int, {})
            # T1 pitches (away batting, home pitching, HOME catcher)
            t1 = g[g["inning_topbot"] == "Top"]
            if len(t1) > 0:
                cid = t1.iloc[0].get("fielder_2")
                if cid is not None and cid == cid:    # not NaN
                    try: rec["home_catcher_id"] = int(cid)
                    except: pass
            # B1 pitches (home batting, away pitching, AWAY catcher)
            b1 = g[g["inning_topbot"] == "Bot"]
            if len(b1) > 0:
                cid = b1.iloc[0].get("fielder_2")
                if cid is not None and cid == cid:
                    try: rec["away_catcher_id"] = int(cid)
                    except: pass
            # Game date
            if "game_date" in t1.columns and len(t1) > 0:
                d = t1.iloc[0]["game_date"]
                try:
                    rec["date"] = d.strftime("%Y-%m-%d")
                except AttributeError:
                    rec["date"] = str(d)[:10]
        del df
    return games


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, action="append", default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    seasons = [2024, 2025] if args.all else (args.season or [2024])

    out: dict = {}
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            out = json.load(f)
        print(f"Loaded existing: {len(out)} games already cached")

    for season in seasons:
        existing_for_season = sum(
            1 for v in out.values() if str(v.get("date", ""))[:4] == str(season)
        )
        if existing_for_season > 1500:
            print(f"  {season}: already have {existing_for_season} games; skip")
            continue
        print(f"\nProcessing season {season}...")
        season_games = fetch_first_inning_catchers(season)
        for gpk, rec in season_games.items():
            out[str(gpk)] = rec
        # Persist after each season
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=0, separators=(",", ":"))
        print(f"  {season}: extracted {len(season_games)} games  "
              f"(total cache: {len(out)})")

    # Summary
    print()
    print(f"Total games in cache: {len(out)}")
    n_both = sum(1 for v in out.values()
                 if v.get("home_catcher_id") and v.get("away_catcher_id"))
    print(f"Games with both home + away catchers: {n_both} "
          f"({100*n_both/max(len(out),1):.1f}%)")


if __name__ == "__main__":
    main()

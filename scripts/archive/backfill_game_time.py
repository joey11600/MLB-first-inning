#!/usr/bin/env python3
"""
backfill_game_time.py -- Add is_day_game and start_hour_et columns to backtest CSVs.

Hits MLB StatsAPI's schedule endpoint once per season (single call covering
the full date range), builds a {game_pk -> start_hour_et} map, then rewrites
each backtest CSV with two new trailing columns:

  start_hour_et : 0-23 hour of first pitch in America/New_York
  is_day_game   : 1 if start_hour_et < 17 (i.e. before 5pm ET) else 0

These let lr_baseline.py test whether time-of-day adds signal beyond V2.
"""

import argparse
import csv
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import statsapi
except ImportError:
    sys.exit("Install dependency: pip install mlb-statsapi")

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def fetch_season_schedule(start_date: str, end_date: str) -> dict[int, str]:
    """
    Fetch the full schedule for a date range and return
    {game_pk: gameDate_iso_utc}.
    """
    print(f"Fetching schedule {start_date} -> {end_date}...")
    data = statsapi.get(
        "schedule",
        {
            "sportId":   1,
            "startDate": start_date,
            "endDate":   end_date,
            "fields":    "dates,date,games,gamePk,gameDate,status,statusCode",
        },
    )
    out: dict[int, str] = {}
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            gd = g.get("gameDate")
            if pk and gd:
                out[int(pk)] = gd
    print(f"  fetched {len(out)} games")
    return out


def utc_iso_to_et_hour(iso_utc: str) -> int | None:
    """e.g. '2024-04-15T22:40:00Z' -> ET hour as int (18 for 6pm ET)."""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone(ET).hour
    except Exception:
        return None


def backfill_csv(csv_path: Path, pk_to_iso: dict[int, str]) -> int:
    """Returns count of rows updated."""
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(csv.reader([open(csv_path, encoding="utf-8").readline()]))[0]

    if "start_hour_et" not in fieldnames:
        fieldnames.append("start_hour_et")
    if "is_day_game" not in fieldnames:
        fieldnames.append("is_day_game")

    updated = 0
    missing = 0
    for r in rows:
        pk_str = r.get("game_pk", "")
        if not pk_str:
            missing += 1
            continue
        try:
            pk = int(pk_str)
        except ValueError:
            missing += 1
            continue
        iso = pk_to_iso.get(pk)
        if not iso:
            missing += 1
            continue
        hour = utc_iso_to_et_hour(iso)
        if hour is None:
            missing += 1
            continue
        r["start_hour_et"] = str(hour)
        r["is_day_game"]   = "1" if hour < 17 else "0"
        updated += 1

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  {csv_path.name}: updated={updated} missing={missing}")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", action="append", required=True,
                        help="Backtest CSV to backfill (repeatable)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    pk_to_iso = fetch_season_schedule(args.start, args.end)
    if not pk_to_iso:
        sys.exit("No games returned by API; cannot backfill.")

    for csv_path_str in args.csv:
        backfill_csv(Path(csv_path_str), pk_to_iso)


if __name__ == "__main__":
    main()

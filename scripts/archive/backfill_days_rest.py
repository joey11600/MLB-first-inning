#!/usr/bin/env python3
"""
backfill_days_rest.py -- add away_days_rest + home_days_rest columns to
the backtest CSVs and picks_2026.csv.

Days rest = number of calendar days between this game's date and the
pitcher's most recent prior start in the same season. First start of
the season has no prior, defaulted to 5 (typical MLB rotation).

Strategy:
  1. Build {game_pk -> (away_pid, home_pid)} lookup. Reuse cached
     schedule JSONs when present (2025+); fetch from statsapi for 2024.
  2. For each unique pitcher in scope, fetch their season game log
     (cached at data/cache/pitcher_gamelog_v2/{pid}_{season}.json), and
     build a sorted list of their start dates per season.
  3. For each row in each CSV:
       prev_start = last date in pitcher's gamelog strictly before today
       days_rest  = (today - prev_start).days  (default 5 if no prev)
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Install: pip install mlb-statsapi")

ROOT = Path(__file__).parent
SCHED_CACHE = ROOT / "data" / "cache" / "schedule"
GLOG_CACHE  = ROOT / "data" / "cache" / "pitcher_gamelog_v2"
DEFAULT_REST = 5


def parse_iso_date(s: str) -> date:
    return datetime.fromisoformat(s.split("T")[0]).date()


def load_or_fetch_schedule_for_season(season: int) -> dict[int, tuple[int, int]]:
    """Returns {game_pk -> (away_pid, home_pid)} for an entire season.
    Uses cached daily files if available; otherwise hits the schedule API
    once for the whole season range and saves a lite cache."""
    out: dict[int, tuple[int, int]] = {}
    # Try cached daily files first
    if SCHED_CACHE.exists():
        for path in sorted(SCHED_CACHE.glob(f"{season}-*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                for g in d.get("games", []):
                    pk = g.get("game_pk")
                    if pk is not None:
                        out[int(pk)] = (
                            g.get("away_pitcher_id") or 0,
                            g.get("home_pitcher_id") or 0,
                        )
            except Exception:
                continue
    if out:
        print(f"  schedule cache hit: {season} -> {len(out)} games")
        return out

    # Fetch from API as fallback (used for 2024)
    print(f"  fetching {season} schedule via API ({season}-04-01 -> {season}-09-30)...")
    data = statsapi.get(
        "schedule",
        {
            "sportId":   1,
            "startDate": f"{season}-04-01",
            "endDate":   f"{season}-09-30",
            "hydrate":   "probablePitcher",
            "fields":    "dates,games,gamePk,teams,away,home,probablePitcher,id",
        },
    )
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            if pk is None:
                continue
            ap = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
            hp = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
            out[int(pk)] = (
                int(ap.get("id") or 0),
                int(hp.get("id") or 0),
            )
    print(f"  fetched {len(out)} games for {season}")
    return out


def gamelog_path(pid: int, season: int) -> Path:
    return GLOG_CACHE / f"{pid}_{season}.json"


def load_or_fetch_gamelog(pid: int, season: int) -> list[date]:
    """Returns sorted list of start dates for a pitcher in a season."""
    p = gamelog_path(pid, season)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                rows = json.load(f)
            return sorted({parse_iso_date(r["date"]) for r in rows if r.get("date")})
        except Exception:
            pass
    # Fetch
    try:
        data = statsapi.get("person", {
            "personId": pid,
            "hydrate":  f"stats(group=[pitching],type=[gameLog],season={season})",
        })
    except Exception as e:
        print(f"  [warn] gamelog fetch failed for pid={pid} season={season}: {e}")
        return []
    rows: list[dict] = []
    for person in data.get("people", []):
        for stat_block in person.get("stats", []):
            for split in stat_block.get("splits", []):
                gd  = split.get("date")
                pk  = split.get("game", {}).get("gamePk")
                team = split.get("team", {}).get("id")
                stat = split.get("stat", {})
                if not gd:
                    continue
                rows.append({
                    "gamePk":  pk,
                    "date":    gd,
                    "team_id": team,
                    "ip":  float(stat.get("inningsPitched", 0) or 0),
                    "er":  float(stat.get("earnedRuns",     0) or 0),
                    "k":   float(stat.get("strikeOuts",     0) or 0),
                    "bb":  float(stat.get("baseOnBalls",    0) or 0),
                    "hr":  float(stat.get("homeRuns",       0) or 0),
                    "h":   float(stat.get("hits",           0) or 0),
                })
    GLOG_CACHE.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return sorted({parse_iso_date(r["date"]) for r in rows if r.get("date")})


def days_rest_lookup(starts: list[date], today: date) -> int:
    """Most recent start strictly before `today`."""
    prev = None
    for d in starts:
        if d < today:
            prev = d
        else:
            break
    if prev is None:
        return DEFAULT_REST
    return (today - prev).days


def backfill_csv(
    csv_path: Path,
    season: int,
    *,
    pk_to_pids: dict[int, tuple[int, int]],
    pid_to_starts: dict[int, list[date]],
    pid_col_in_csv: bool,
) -> tuple[int, int]:
    """Returns (rows_updated, rows_missing_data)."""
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        # We need fieldnames -- preserve original order, then append new ones.
        f.seek(0)
        header = next(csv.reader(f))

    if "away_days_rest" not in header: header.append("away_days_rest")
    if "home_days_rest" not in header: header.append("home_days_rest")

    updated = 0
    missing = 0

    for r in rows:
        try:
            game_date = parse_iso_date(r.get("date", ""))
        except Exception:
            missing += 1
            continue

        if pid_col_in_csv:
            try:
                away_pid = int(r.get("away_pitcher_id") or 0)
                home_pid = int(r.get("home_pitcher_id") or 0)
            except ValueError:
                away_pid = home_pid = 0
        else:
            try:
                pk = int(r.get("game_pk") or 0)
            except ValueError:
                pk = 0
            away_pid, home_pid = pk_to_pids.get(pk, (0, 0))

        if not away_pid or not home_pid:
            missing += 1
            r["away_days_rest"] = ""
            r["home_days_rest"] = ""
            continue

        away_starts = pid_to_starts.get(away_pid, [])
        home_starts = pid_to_starts.get(home_pid, [])
        r["away_days_rest"] = str(days_rest_lookup(away_starts, game_date))
        r["home_days_rest"] = str(days_rest_lookup(home_starts, game_date))
        updated += 1

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  {csv_path.name}: updated={updated} missing_data={missing}")
    return updated, missing


def main():
    print("=" * 64)
    print("  Backfill days_rest into backtest CSVs + picks_2026.csv")
    print("=" * 64)

    # Build per-season schedule lookups
    pk_2024 = load_or_fetch_schedule_for_season(2024)
    pk_2025 = load_or_fetch_schedule_for_season(2025)

    # Collect all pitcher ids per season
    pids_2024 = {pid for pids in pk_2024.values() for pid in pids if pid}
    pids_2025 = {pid for pids in pk_2025.values() for pid in pids if pid}

    # picks_2026.csv has pitcher_ids inline
    pids_2026: set[int] = set()
    picks_path = ROOT / "data" / "picks_2026.csv"
    with open(picks_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for col in ("away_pitcher_id", "home_pitcher_id"):
                v = r.get(col, "")
                try:
                    pid = int(v)
                    if pid:
                        pids_2026.add(pid)
                except ValueError:
                    continue

    print(f"\nUnique pitcher ids: 2024={len(pids_2024)} 2025={len(pids_2025)} 2026={len(pids_2026)}")

    # Fetch / cache gamelog per (pid, season)
    pid_to_starts: dict[tuple[int, int], list[date]] = {}
    print("\nFetching gamelogs (cached locally)...")
    fetched = 0
    for season, pid_set in [(2024, pids_2024), (2025, pids_2025), (2026, pids_2026)]:
        for i, pid in enumerate(sorted(pid_set)):
            if not gamelog_path(pid, season).exists():
                fetched += 1
            pid_to_starts[(pid, season)] = load_or_fetch_gamelog(pid, season)
        print(f"  season {season}: {len(pid_set)} pitchers ready (newly fetched this run: {fetched})")
    print(f"\nTotal new fetches this run: {fetched}")

    # Index by pid for the season-specific backfill
    starts_2024 = {pid: starts for (pid, s), starts in pid_to_starts.items() if s == 2024}
    starts_2025 = {pid: starts for (pid, s), starts in pid_to_starts.items() if s == 2025}
    starts_2026 = {pid: starts for (pid, s), starts in pid_to_starts.items() if s == 2026}

    # Backfill each CSV
    print("\nBackfilling CSVs...")
    bt_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
    bt_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"

    backfill_csv(bt_2024, 2024,
                 pk_to_pids=pk_2024,
                 pid_to_starts=starts_2024,
                 pid_col_in_csv=False)
    backfill_csv(bt_2025, 2025,
                 pk_to_pids=pk_2025,
                 pid_to_starts=starts_2025,
                 pid_col_in_csv=False)
    backfill_csv(picks_path, 2026,
                 pk_to_pids={},
                 pid_to_starts=starts_2026,
                 pid_col_in_csv=True)

    print("\nDone.\n")


if __name__ == "__main__":
    main()

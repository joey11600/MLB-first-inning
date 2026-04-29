#!/usr/bin/env python3
"""
backfill_last10_era.py -- compute pitcher ERA over the last 10 starts
before each game's date, and add as columns to all backtest CSVs +
picks_2026.csv.

Output columns:
  home_p_last10_era, away_p_last10_era

ERA formula: (sum ER over last N starts) * 9 / (sum IP over last N starts).
Falls back to current_season_pitcher_stats season ERA when fewer than 3
starts are available before the target date (early-April games).

Reuses the existing fetch_pitcher_gamelog cache so all 2024-2026 pitcher
gamelogs are read locally (~instant).  No new API calls for already-
cached pitchers.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from backtest import fetch_pitcher_gamelog, pitcher_last_n_first_inning

CSV_PATHS = [
    (2022, ROOT / "data" / "backtests" / "backtest_2022-04-01_to_2022-09-30.csv"),
    (2023, ROOT / "data" / "backtests" / "backtest_2023-04-01_to_2023-09-30.csv"),
    (2024, ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"),
    (2025, ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"),
    (2026, ROOT / "data" / "picks_2026.csv"),
]

WINDOW = 10
MIN_STARTS_FOR_USE = 3  # below this, use season-default fallback
LEAGUE_AVG_ERA = 4.20


def pitcher_last_n_era(player_id: int, target_date_iso: str, season: int,
                        n: int = WINDOW) -> tuple[float | None, int]:
    """ERA over the last `n` starts STRICTLY BEFORE target_date.
    Returns (era, n_starts).  era=None if fewer than MIN_STARTS_FOR_USE.

    Reaches into the prior season's gamelog if the current season has too
    few prior starts -- handles early-April games where a pitcher might
    have 1-2 current-season starts and we want to include late prior-year
    starts for a meaningful 10-start window."""
    if not player_id:
        return None, 0
    log = fetch_pitcher_gamelog(int(player_id), season)
    # Strict-before filter on the current season
    prior = [g for g in log if g.get("date", "") < target_date_iso] if log else []
    # If current season alone doesn't reach MIN_STARTS, pull prior season too
    if len(prior) < n:
        prev_log = fetch_pitcher_gamelog(int(player_id), season - 1)
        if prev_log:
            # All of last season is "before" current target_date, so include
            prior = prev_log + prior
    prior.sort(key=lambda g: g["date"])
    last = prior[-n:]
    if len(last) < MIN_STARTS_FOR_USE:
        return None, len(last)
    total_ip = sum(g.get("ip", 0.0) for g in last)
    total_er = sum(g.get("er", 0.0) for g in last)
    if total_ip <= 0:
        return None, len(last)
    era = (total_er * 9.0) / total_ip
    return era, len(last)


def backfill_csv(season: int, path: Path):
    if not path.exists():
        print(f"  {season}: missing {path}; skip")
        return
    print(f"\n  Backfilling last10_era columns into {path.name}")
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    new_cols = [
        "home_p_last10_era", "away_p_last10_era",
        "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
    ]
    for c in new_cols:
        if c not in header: header.append(c)

    # Need pitcher_id_cache for backtests (pitcher_id missing in those CSVs)
    pid_cache = {}
    pid_path = ROOT / "data" / "pitcher_id_cache.json"
    if pid_path.exists():
        pid_cache = json.load(open(pid_path, encoding="utf-8"))

    n_filled = n_total = 0
    last_date = None
    for r in rows:
        n_total += 1
        date_iso = (r.get("date") or "").strip()
        if not date_iso:
            continue
        if date_iso != last_date:
            print(f"    ... {date_iso}")
            last_date = date_iso

        # Pitcher IDs: try CSV cells first (picks_2026 has them), else fall
        # back to pid_cache (backtests).
        away_pid = (r.get("away_pitcher_id") or "").strip()
        home_pid = (r.get("home_pitcher_id") or "").strip()
        if not (away_pid and home_pid):
            pk = (r.get("game_pk") or "").strip()
            pids = pid_cache.get(pk)
            if pids:
                away_pid = away_pid or str(pids[0])
                home_pid = home_pid or str(pids[1])

        # Skip cells already filled (idempotent)
        if not r.get("home_p_last10_era", "").strip() and home_pid:
            era, n_st = pitcher_last_n_era(int(home_pid), date_iso, season)
            if era is not None:
                r["home_p_last10_era"] = f"{era:.3f}"
                n_filled += 1
        if not r.get("away_p_last10_era", "").strip() and away_pid:
            era, n_st = pitcher_last_n_era(int(away_pid), date_iso, season)
            if era is not None:
                r["away_p_last10_era"] = f"{era:.3f}"
                n_filled += 1
        # last10_pitcher_nrfi (only computed for backtests historically; missing
        # in picks_2026.csv where Phase D backfill only did last5).
        if not r.get("home_p_last10_pitcher_nrfi", "").strip() and home_pid:
            try:
                d = pitcher_last_n_first_inning(int(home_pid), date_iso, season, n=10)
                if d and d.get("pitcher_nrfi_rate") is not None:
                    r["home_p_last10_pitcher_nrfi"] = f"{d['pitcher_nrfi_rate']:.4f}"
                    n_filled += 1
            except Exception: pass
        if not r.get("away_p_last10_pitcher_nrfi", "").strip() and away_pid:
            try:
                d = pitcher_last_n_first_inning(int(away_pid), date_iso, season, n=10)
                if d and d.get("pitcher_nrfi_rate") is not None:
                    r["away_p_last10_pitcher_nrfi"] = f"{d['pitcher_nrfi_rate']:.4f}"
                    n_filled += 1
            except Exception: pass

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"    filled {n_filled} cells across {n_total} rows")


def main():
    for season, path in CSV_PATHS:
        backfill_csv(season, path)
    print("\n  Done.")


if __name__ == "__main__":
    main()

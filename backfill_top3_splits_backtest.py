#!/usr/bin/env python3
"""
backfill_top3_splits_backtest.py -- extend top-3-OPS-vs-opposing-hand
backfill to the 2024 and 2025 backtest CSVs.

The original backfill_top3_splits.py only handled picks_2026.csv (which
has different column names: home_team vs home, actual_result vs actual_side).
This variant handles the backtest format and uses the schedule cache to
recover pitcher_ids by gamePk (the backtest CSVs don't have pitcher_id
columns either).

Adds 4 columns to each backtest CSV:
  away_top3_ops_vs_oppHand  -- avg OPS of top-3 away batters vs home pitcher's hand
  home_top3_ops_vs_oppHand  -- avg OPS of top-3 home batters vs away pitcher's hand
  away_pitcher_throws_hand
  home_pitcher_throws_hand
"""

import csv
import json
import sys
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Install: pip install mlb-statsapi")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Reuse all the helpers from the original script
from backfill_top3_splits import (
    fetch_lineup, fetch_batter_splits, fetch_pitcher_hand,
    top3_ops_vs_hand, load_schedule_pid_map, LEAGUE_AVG_OPS,
)


def fetch_season_schedule_pid_map(season: int) -> dict[int, tuple[int, int]]:
    """Fetch the entire season's schedule from MLB API in one call and build
    a {gamePk: (away_pid, home_pid)} map.  Used for 2024 where we have no
    daily schedule cache."""
    print(f"  fetching {season} schedule via API (one call for the season)...")
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
    out: dict[int, tuple[int, int]] = {}
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
    return out


def backfill_csv(csv_path: Path, season: int):
    print(f"\n{'=' * 70}")
    print(f"  Backfilling {csv_path.name} (season {season})")
    print(f"{'=' * 70}")

    pk_to_pids = load_schedule_pid_map(season)
    if not pk_to_pids:
        pk_to_pids = fetch_season_schedule_pid_map(season)
    print(f"  schedule pid map: {len(pk_to_pids)} games")

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        f.seek(0)
        header = next(csv.reader(f))

    new_cols = [
        "away_top3_ops_vs_oppHand", "home_top3_ops_vs_oppHand",
        "away_pitcher_throws_hand", "home_pitcher_throws_hand",
    ]
    for c in new_cols:
        if c not in header:
            header.append(c)

    n_total = len(rows)
    n_filled = 0
    n_skipped = 0

    for i, r in enumerate(rows, 1):
        if i % 200 == 0:
            print(f"  ... {i}/{n_total} ({n_filled} filled, {n_skipped} skipped)")

        try:
            game_pk = int(r.get("game_pk") or 0)
        except ValueError:
            game_pk = 0
        if not game_pk:
            n_skipped += 1
            continue

        # Backtest CSVs don't have pitcher_id columns -- look up via schedule
        away_pid, home_pid = pk_to_pids.get(game_pk, (0, 0))
        if not away_pid or not home_pid:
            r["away_top3_ops_vs_oppHand"] = ""
            r["home_top3_ops_vs_oppHand"] = ""
            r["away_pitcher_throws_hand"] = ""
            r["home_pitcher_throws_hand"] = ""
            n_skipped += 1
            continue

        away_hand = fetch_pitcher_hand(away_pid, season)
        home_hand = fetch_pitcher_hand(home_pid, season)
        lineup = fetch_lineup(game_pk)

        # away batters face home pitcher's hand; home batters face away pitcher's hand
        away_score = top3_ops_vs_hand(lineup["away"], home_hand, season)
        home_score = top3_ops_vs_hand(lineup["home"], away_hand, season)

        r["away_top3_ops_vs_oppHand"] = f"{away_score:.4f}"
        r["home_top3_ops_vs_oppHand"] = f"{home_score:.4f}"
        r["away_pitcher_throws_hand"] = away_hand
        r["home_pitcher_throws_hand"] = home_hand
        n_filled += 1

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  Done.  Filled {n_filled} / {n_total}  (skipped {n_skipped})")


def main():
    backfill_csv(ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv", 2024)
    backfill_csv(ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv", 2025)


if __name__ == "__main__":
    main()

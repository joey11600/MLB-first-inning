#!/usr/bin/env python3
"""
backfill_phase_f.py -- add Phase F features (pitcher-vs-team familiarity
and opener detection) to picks_2026.csv and the 2024+2025 backtests.

New columns:
  home_pvt_nrfi_rate / away_pvt_nrfi_rate
    Pitcher's career NRFI rate vs the OPPOSING team, Bayesian-shrunk
    toward league average (K=20).  Strictly point-in-time -- only counts
    starts before the row's date.

  home_avg_ip_per_start / away_avg_ip_per_start
    Mean innings pitched over the pitcher's last 5 starts before this
    game.  Detects openers (avg < 3 IP) and bulk pitchers.

Idempotent: only fills cells that are blank.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from backtest import pitcher_vs_team_nrfi_rate, pitcher_role_features

# Team abbr -> MLB team_id (matches mlb_first_inning_predictor.TEAM_ID_TO_ABBR)
TEAM_ABBR_TO_ID = {
    "LAA": 108, "ARI": 109, "BAL": 110, "BOS": 111, "CHC": 112, "CIN": 113,
    "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC":  118, "LAD": 119,
    "WSH": 120, "NYM": 121, "OAK": 133, "PIT": 134, "SD":  135, "SEA": 136,
    "SF":  137, "STL": 138, "TB":  139, "TEX": 140, "TOR": 141, "MIN": 142,
    "PHI": 143, "ATL": 144, "CWS": 145, "MIA": 146, "NYY": 147, "MIL": 158,
}

NEW_COLS = [
    "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
    "home_avg_ip_per_start", "away_avg_ip_per_start",
]


def get_pitcher_ids(row: dict, pid_cache: dict) -> tuple[int, int]:
    """Return (away_pid, home_pid) using row CSV cols, fall back to pid_cache."""
    pk = (row.get("game_pk") or "").strip()
    a = (row.get("away_pitcher_id") or "").strip()
    h = (row.get("home_pitcher_id") or "").strip()
    a_pid = int(a) if a and a != "0" else 0
    h_pid = int(h) if h and h != "0" else 0
    if not (a_pid and h_pid) and pk in pid_cache:
        cached = pid_cache[pk]
        if isinstance(cached, list) and len(cached) >= 2:
            try:
                if not a_pid: a_pid = int(cached[0])
                if not h_pid: h_pid = int(cached[1])
            except (TypeError, ValueError):
                pass
    return a_pid, h_pid


def get_team_ids(row: dict) -> tuple[int, int]:
    """Return (away_team_id, home_team_id).  Backtest CSVs use 'away'/'home'
    cols; picks CSVs use 'away_team'/'home_team'."""
    a_abbr = (row.get("away_team") or row.get("away") or "").strip()
    h_abbr = (row.get("home_team") or row.get("home") or "").strip()
    return TEAM_ABBR_TO_ID.get(a_abbr, 0), TEAM_ABBR_TO_ID.get(h_abbr, 0)


def backfill_csv(path: Path, label: str, pid_cache: dict) -> None:
    print(f"\n  Backfilling Phase F columns into {label}")
    with open(path, encoding="utf-8") as f:
        r = csv.DictReader(f)
        header = list(r.fieldnames or [])
        rows = list(r)

    added = []
    for c in NEW_COLS:
        if c not in header:
            header.append(c)
            added.append(c)
    if added:
        print(f"    Added cols: {added}")

    n_pvt = n_role = 0
    for i, row in enumerate(rows):
        if i % 500 == 0:
            print(f"      ... {i}/{len(rows)}  pvt_filled={n_pvt}  role_filled={n_role}", flush=True)

        date_iso = (row.get("date") or "").strip()
        season_str = (row.get("season") or date_iso[:4] or "2026").strip()
        try: season = int(season_str)
        except: season = int(date_iso[:4]) if date_iso else 2026
        if not date_iso:
            continue

        a_pid, h_pid = get_pitcher_ids(row, pid_cache)
        a_tid, h_tid = get_team_ids(row)

        # PVT NRFI: home pitcher faces AWAY team, away pitcher faces HOME team
        if not (row.get("home_pvt_nrfi_rate") or "").strip() and h_pid and a_tid:
            v = pitcher_vs_team_nrfi_rate(h_pid, a_tid, date_iso)
            row["home_pvt_nrfi_rate"] = f"{v:.4f}"
            n_pvt += 1
        if not (row.get("away_pvt_nrfi_rate") or "").strip() and a_pid and h_tid:
            v = pitcher_vs_team_nrfi_rate(a_pid, h_tid, date_iso)
            row["away_pvt_nrfi_rate"] = f"{v:.4f}"
            n_pvt += 1

        # Opener detection: avg IP per start over last 5
        if not (row.get("home_avg_ip_per_start") or "").strip() and h_pid:
            d = pitcher_role_features(h_pid, date_iso, season)
            if d:
                row["home_avg_ip_per_start"] = f"{d['avg_ip_per_start']:.2f}"
                n_role += 1
        if not (row.get("away_avg_ip_per_start") or "").strip() and a_pid:
            d = pitcher_role_features(a_pid, date_iso, season)
            if d:
                row["away_avg_ip_per_start"] = f"{d['avg_ip_per_start']:.2f}"
                n_role += 1

    print(f"    PVT cells filled: {n_pvt}")
    print(f"    Role cells filled: {n_role}")

    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    import json
    pid_cache_path = ROOT / "data" / "pitcher_id_cache.json"
    pid_cache = json.load(open(pid_cache_path, encoding="utf-8")) if pid_cache_path.exists() else {}

    targets = [
        (ROOT / "data" / "picks_2026.csv", "picks_2026.csv"),
        (ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv", "backtest_2024"),
        (ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv", "backtest_2025"),
    ]
    for path, label in targets:
        if not path.exists():
            print(f"  {label}: not found, skipping")
            continue
        backfill_csv(path, label, pid_cache)
    print("\n  Done.")


if __name__ == "__main__":
    main()

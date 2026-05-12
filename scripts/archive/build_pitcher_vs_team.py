#!/usr/bin/env python3
"""
build_pitcher_vs_team.py -- per-(pitcher, opposing-team) first-inning
NRFI history.  Stored as individual start records so lookups can filter
by target_date (no leakage).

Why this matters: a pitcher's CAREER first-inning NRFI rate isn't the
same as their NRFI rate vs a SPECIFIC opponent.  deGrom rarely pitched
against the Yankees -- so when he did, the small sample didn't reflect
his career profile and he was vulnerable to their power lineup.  This
feature captures that "familiarity gap."

Output schema:
  data/pitcher_vs_team.json
  {
    "<pitcher_id>": {
      "<opp_team_id>": [
        {"date": "2024-05-15", "gpk": 12345, "gave_up_run": false},
        ...
      ]
    },
    "_meta": {
      "league_nrfi_rate": 0.5183,
      "shrink_K": 20,
      "n_pitchers": 234,
      "n_starts": 8901
    }
  }

Build idempotent: only fetches new (pitcher, season) pairs.  Reads from
existing pitcher_gamelog_v2 cache + fetch_first_inning_result cache so
no fresh API calls if data is already present.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from backtest import (
    fetch_pitcher_gamelog,
    fetch_schedule_iso,
    _cache_get,
    _cache_path,
)


def _cached_linescore_only(gpk: int) -> dict | None:
    """Read the linescore from cache only -- skip live API calls so the
    build doesn't fan out into 2000 sequential network requests.  We'll
    end up with slightly less coverage (only games we've seen before),
    but those are exactly the games whose first-inning result is fixed
    and reliable."""
    p = _cache_path("linescore", str(gpk))
    if not p.exists():
        return None
    try:
        import json as _json
        return _json.load(open(p, encoding="utf-8"))
    except Exception:
        return None

OUT_PATH    = ROOT / "data" / "pitcher_vs_team.json"
PID_CACHE   = ROOT / "data" / "pitcher_id_cache.json"

# Years to ingest -- cover everything we have backtest data for, including
# the in-progress current season (2026).  Lookups will filter by date so
# current-season starts don't leak future info into past predictions.
SEASONS = [2022, 2023, 2024, 2025, 2026]

# Bayesian shrinkage: with K=20, a pitcher with 5 starts vs a team only
# weights real data 5/(5+20) = 20%, so small samples regress hard to
# league average.  Tune later based on backtest impact.
SHRINK_K = 20

# Will be computed from the gathered data below
DEFAULT_LEAGUE_RATE = 0.5183


def collect_unique_pitchers() -> set[int]:
    """Pull every pitcher_id we know about from the pid_cache."""
    if not PID_CACHE.exists():
        sys.exit(f"Missing {PID_CACHE} -- can't enumerate pitchers")
    pid_cache = json.load(open(PID_CACHE, encoding="utf-8"))
    pitchers: set[int] = set()
    for pids in pid_cache.values():
        if isinstance(pids, list) and len(pids) >= 2:
            try:
                a, h = int(pids[0]), int(pids[1])
                if a: pitchers.add(a)
                if h: pitchers.add(h)
            except (TypeError, ValueError):
                continue
    return pitchers


def build_pvt_records(pitchers: set[int]) -> dict:
    """For each pitcher, walk their gamelog and record first-inning NRFI vs
    each opposing team.  Returns nested dict structure."""
    out: dict = {}
    n_starts_total = 0
    n_pitchers_with_data = 0

    for i, pid in enumerate(sorted(pitchers)):
        if i % 50 == 0:
            print(f"  ... pitcher {i}/{len(pitchers)}  starts={n_starts_total}", flush=True)

        # Combine all seasons of starts (cached, so fast on rerun)
        all_starts = []
        for season in SEASONS:
            try:
                log = fetch_pitcher_gamelog(pid, season)
                all_starts.extend(log)
            except Exception:
                pass

        if not all_starts:
            continue

        pid_records: dict[str, list] = {}

        for start in all_starts:
            gpk = start.get("gamePk")
            date = start.get("date")
            team_id = start.get("team_id")
            if not gpk or not date or not team_id:
                continue

            # Find opponent via schedule cache
            try:
                sched = fetch_schedule_iso(date)
            except Exception:
                continue
            game_info = next((g for g in sched if g.get("game_pk") == gpk), None)
            if not game_info:
                continue
            home_id = game_info.get("home_team_id")
            away_id = game_info.get("away_team_id")
            if not home_id or not away_id:
                continue
            is_home_pitcher = (team_id == home_id)
            opp_team_id = away_id if is_home_pitcher else home_id

            # Did THIS pitcher give up a 1st-inning run?  Cached-only
            # lookup; skip games whose linescore we haven't seen yet.
            ls = _cached_linescore_only(gpk)
            if ls is None:
                continue
            ar = ls.get("away_runs")
            hr = ls.get("home_runs")
            if ar is None or hr is None:
                continue
            # Home pitcher pitches top of 1st (away batting)
            opp_runs = ar if is_home_pitcher else hr
            gave_up_run = (opp_runs > 0)

            opp_key = str(opp_team_id)
            pid_records.setdefault(opp_key, []).append({
                "date": date,
                "gpk":  gpk,
                "gave_up_run": gave_up_run,
            })
            n_starts_total += 1

        if pid_records:
            out[str(pid)] = pid_records
            n_pitchers_with_data += 1

    print(f"\n  {n_pitchers_with_data} pitchers, {n_starts_total} starts collected")

    # Compute league NRFI rate from the collected data
    n_total_runs = 0
    n_total_starts = 0
    for pid_records in out.values():
        for opp_records in pid_records.values():
            for s in opp_records:
                n_total_starts += 1
                if not s["gave_up_run"]:
                    n_total_runs += 1
    league_rate = n_total_runs / n_total_starts if n_total_starts > 0 else DEFAULT_LEAGUE_RATE
    print(f"  League pitcher-NRFI rate: {league_rate:.4f}")

    out["_meta"] = {
        "league_nrfi_rate": round(league_rate, 4),
        "shrink_K": SHRINK_K,
        "n_pitchers": n_pitchers_with_data,
        "n_starts": n_total_starts,
    }
    return out


def main():
    print("Building pitcher-vs-team first-inning NRFI cache")
    pitchers = collect_unique_pitchers()
    print(f"  Enumerated {len(pitchers)} unique pitchers from pid_cache")

    out = build_pvt_records(pitchers)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, separators=(",", ":"))
    sz_mb = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"\n  Saved -> {OUT_PATH}  ({sz_mb:.1f}MB)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/backfill_xera_pit_perpitch.py — true point-in-time xera/whiff
backfill via per-pitch Baseball Savant data.

Replaces the prior-year proxy in tools/backfill_xera_whiff_pit.py with
strict per-game cumulative-through-yesterday values, computed from
per-pitch Statcast data via pybaseball.

ALGORITHM
---------
1. Enumerate every (pitcher_id, season) pair in the 2024 + 2025 backtest
   CSVs (via data/pitcher_id_cache.json).
2. For each pitcher × season, fetch full-season per-pitch Statcast
   data: pb.statcast_pitcher(start_dt, end_dt, player_id).
3. Persist raw per-pitch data to data/cache/perpitch_{pid}_{season}.json
   so the script is resumable on API failure / rerun.
4. Aggregate per (pitcher, date) cumulatively:
     - cum_xwoba         = mean(estimated_woba_using_speedangle) THROUGH date
     - cum_whiff_pct     = whiffs / swings cumulatively THROUGH date
   "Through date" means strictly: only pitches in games on dates <
   target_date (so the Aug 5 row uses Aug 4 cumulative — no
   look-ahead).
5. Map cum_xwoba -> cum_xera via the league-typical regression:
     xera_proxy = (cum_xwoba - league_mean_xwoba) * RUNS_PER_XWOBA + league_avg_era
   This is a simplification of MLB's official xERA formula (which uses
   per-batted-ball expected_woba_using_speedangle and adjusts for
   run-environment); for variant validation purposes the proxy is
   sufficient because it preserves rank-order across pitchers.
6. Map cum_whiff_pct -> whiff_pct_rank via per-date cross-pitcher
   percentile rank (1-100 scale).  This is expensive (sort all
   pitchers at every date) but only ~180 dates per season.
7. Write data/backtests/backtest_{season}-..._truepit.csv with
   home_xera/away_xera/home_whiff_pct_rank/away_whiff_pct_rank rewritten.

USAGE
-----
  python tools/backfill_xera_pit_perpitch.py             # both seasons
  python tools/backfill_xera_pit_perpitch.py --season 2024
  python tools/backfill_xera_pit_perpitch.py --pitcher 596001  # debug one pitcher

ENV
---
  pip install pybaseball  (already a dev dep)

ETA: ~50-80 min for ~700 unique (pitcher, season) calls at 3-5s each.
Cached & resumable.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache" / "perpitch"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# League constants for xwOBA -> xERA mapping.
# League-typical xwOBA ~= 0.310, league-typical ERA ~= 4.20.
# The slope (runs added per 0.001 xwOBA) is roughly +0.10 ERA per 0.010 xwOBA;
# i.e. 10 runs per .001 xwOBA per 600 PA = 1.0 ERA per .010 xwOBA.  Use that.
LEAGUE_XWOBA = 0.310
LEAGUE_ERA   = 4.20
ERA_PER_XWOBA = 100.0   # 1.0 ERA per 0.010 xwOBA = 100 ERA per 1.0 xwOBA

NEUTRAL_PCT_RANK = 50
LEAGUE_AVG_XERA  = 4.20

# Per-pitch description codes that count as a swing (vs take/ball/called).
SWING_DESCRIPTIONS = {
    "swinging_strike", "swinging_strike_blocked", "missed_bunt",
    "foul", "foul_tip", "foul_bunt",
    "hit_into_play",
}
WHIFF_DESCRIPTIONS = {
    "swinging_strike", "swinging_strike_blocked", "missed_bunt",
}


def _isnan(v):
    if v is None: return True
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def fetch_pitcher_perpitch(pid: int, season: int, force: bool = False) -> dict | None:
    """Return per-pitch list (dicts) for one pitcher × season.  Cached."""
    cache_path = CACHE_DIR / f"perpitch_{pid}_{season}.json"
    if cache_path.exists() and not force:
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass    # corrupt cache; re-fetch

    try:
        import pybaseball as pb
    except ImportError:
        sys.exit("pip install pybaseball")

    start = f"{season}-04-01"
    end   = f"{season}-09-30"
    try:
        df = pb.statcast_pitcher(start_dt=start, end_dt=end, player_id=pid)
    except Exception as exc:    # noqa: BLE001
        print(f"    [{pid}@{season}] ERROR: {exc!r}", file=sys.stderr)
        return None
    if df is None or len(df) == 0:
        # Empty cache -- pitcher had no MLB pitches that season
        out = {"n_pitches": 0, "pitches": []}
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(out, f)
        return out

    # Keep only the columns we need (way smaller).
    keep_cols = ["game_date", "description", "estimated_woba_using_speedangle"]
    available = [c for c in keep_cols if c in df.columns]
    pitches = []
    for _, row in df[available].iterrows():
        p = {}
        for c in available:
            v = row[c]
            if c == "game_date":
                # pandas Timestamp -> ISO date string
                try:
                    p["game_date"] = v.strftime("%Y-%m-%d")
                except AttributeError:
                    p["game_date"] = str(v)[:10]
            elif c == "description":
                p["description"] = str(v) if v is not None else ""
            else:
                if _isnan(v):
                    p[c] = None
                else:
                    p[c] = float(v)
        pitches.append(p)

    out = {"n_pitches": len(pitches), "pitches": pitches}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def cumulative_per_date(pitches: list[dict]) -> dict[str, dict]:
    """Build cumulative xwOBA + whiff/swing counts per (game_date - 1).

    Returns dict mapping date_iso -> {
        "cum_xwoba_through_yesterday": float | None,
        "cum_whiffs_through_yesterday": int,
        "cum_swings_through_yesterday": int,
    }
    The "yesterday" semantics: if a pitcher has a game on 2024-08-05,
    the value at "2024-08-05" reflects all of his pitches on or before
    2024-08-04 -- never including 08-05 itself.
    """
    # Sort all pitches by game_date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for p in pitches:
        d = p.get("game_date")
        if d:
            by_date[d].append(p)
    sorted_dates = sorted(by_date.keys())

    out: dict[str, dict] = {}
    cum_woba_sum = 0.0
    cum_woba_n   = 0
    cum_swings   = 0
    cum_whiffs   = 0
    prev_date    = None

    # The "as of yesterday" snapshot AT this date is the cumulative
    # state AFTER processing all pitches on dates STRICTLY BEFORE this date.
    for d in sorted_dates:
        # First, record the snapshot for today (which uses all data
        # accumulated UP TO this iteration -- i.e. through prev_date)
        out[d] = {
            "cum_xwoba_through_yesterday":  (cum_woba_sum / cum_woba_n) if cum_woba_n else None,
            "cum_whiffs_through_yesterday": cum_whiffs,
            "cum_swings_through_yesterday": cum_swings,
            "n_batted_balls_through_yesterday": cum_woba_n,
        }
        # Then, ADD today's pitches to the running totals (so tomorrow's
        # entry includes today).
        for p in by_date[d]:
            x = p.get("estimated_woba_using_speedangle")
            if x is not None:
                cum_woba_sum += x
                cum_woba_n   += 1
            desc = p.get("description") or ""
            if desc in SWING_DESCRIPTIONS:
                cum_swings += 1
            if desc in WHIFF_DESCRIPTIONS:
                cum_whiffs += 1
        prev_date = d
    return out


def xwoba_to_xera_proxy(cum_xwoba: float | None) -> float:
    """Convert cumulative xwOBA to a league-relative xera proxy."""
    if cum_xwoba is None:
        return LEAGUE_AVG_XERA
    return LEAGUE_ERA + (cum_xwoba - LEAGUE_XWOBA) * ERA_PER_XWOBA


def build_pitcher_id_set(seasons: list[int]) -> dict[int, set[int]]:
    """Return {season: set(pitcher_ids)} from the pid_cache + backtests."""
    pid_cache_path = REPO_ROOT / "data" / "pitcher_id_cache.json"
    with open(pid_cache_path, encoding="utf-8") as f:
        pid_cache = json.load(f)
    out: dict[int, set[int]] = {s: set() for s in seasons}
    for season in seasons:
        path = REPO_ROOT / "data" / "backtests" / f"backtest_{season}-04-01_to_{season}-09-30.csv"
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pk = (row.get("game_pk") or "").strip()
                if pk in pid_cache:
                    a, h = pid_cache[pk][0], pid_cache[pk][1]
                    try:    out[season].add(int(a))
                    except: pass
                    try:    out[season].add(int(h))
                    except: pass
    return out


def build_perdate_rank(season: int, by_pitcher: dict[int, dict]) -> dict[str, dict[int, int]]:
    """Build cross-pitcher percentile rank per (date, pitcher) for whiff_pct.

    Returns dict mapping date_iso -> {pitcher_id: percentile_rank}.

    Percentile rank is computed as (rank / total) * 100, where rank is
    the pitcher's position when all pitchers with >=200 swings to date
    are sorted by cum_whiff_pct.
    """
    # Collect all dates seen across any pitcher
    all_dates: set[str] = set()
    for pid, perdate in by_pitcher.items():
        all_dates.update(perdate.keys())

    out: dict[str, dict[int, int]] = {}
    MIN_SWINGS = 200      # filter low-volume pitchers (matches Savant's threshold)
    for d in sorted(all_dates):
        # For each pitcher who has data on this date, compute their cum_whiff_pct
        ratings = []
        for pid, perdate in by_pitcher.items():
            snap = perdate.get(d)
            if not snap: continue
            sw = snap.get("cum_swings_through_yesterday") or 0
            wh = snap.get("cum_whiffs_through_yesterday") or 0
            if sw < MIN_SWINGS:
                continue
            ratings.append((pid, wh / sw))
        if not ratings:
            continue
        ratings.sort(key=lambda x: x[1])    # ascending
        n = len(ratings)
        for i, (pid, _) in enumerate(ratings):
            pctile = int(round(((i + 1) / n) * 100))
            out.setdefault(d, {})[pid] = pctile
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--season", type=int, action="append", default=None,
                    help="Restrict to season(s).  Default: 2024 + 2025.")
    ap.add_argument("--limit",  type=int, default=None,
                    help="Stop after N pitchers (debug).")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Don't call API; only use existing cache files.")
    ap.add_argument("--rebuild-csvs-only", action="store_true",
                    help="Skip fetch + cumulative phase; just rebuild output CSVs from cached data.")
    args = ap.parse_args()

    seasons = args.season or [2024, 2025]
    pid_set = build_pitcher_id_set(seasons)

    # Step 1-3: fetch per-pitch data, persist to cache
    print("=" * 80)
    print("  Fetching per-pitch Statcast data (cached)")
    print("=" * 80)
    by_pitcher_per_season: dict[int, dict[int, dict]] = {s: {} for s in seasons}

    for season in seasons:
        pids = sorted(pid_set[season])
        if args.limit:
            pids = pids[:args.limit]
        print(f"\n  Season {season}: {len(pids)} unique pitchers")
        for i, pid in enumerate(pids, 1):
            cache_path = CACHE_DIR / f"perpitch_{pid}_{season}.json"
            if cache_path.exists():
                with open(cache_path, encoding="utf-8") as f:
                    by_pitcher_per_season[season][pid] = json.load(f)
                continue
            if args.no_fetch:
                continue
            t0 = time.time()
            data = fetch_pitcher_perpitch(pid, season)
            dt = time.time() - t0
            if data:
                by_pitcher_per_season[season][pid] = data
                np = data.get("n_pitches", 0)
                print(f"    {i:>3}/{len(pids)}  pid={pid}  {np:>5} pitches  ({dt:.1f}s)",
                      flush=True)
            else:
                print(f"    {i:>3}/{len(pids)}  pid={pid}  FETCH FAILED  ({dt:.1f}s)",
                      flush=True)

    # Step 4: aggregate per pitcher per date
    print("\n" + "=" * 80)
    print("  Computing cumulative-through-yesterday per (pitcher, date)")
    print("=" * 80)
    perdate_per_season: dict[int, dict[int, dict]] = {s: {} for s in seasons}
    for season in seasons:
        for pid, data in by_pitcher_per_season[season].items():
            pitches = data.get("pitches", [])
            if not pitches: continue
            perdate = cumulative_per_date(pitches)
            perdate_per_season[season][pid] = perdate
        print(f"  Season {season}: built per-date snapshots for "
              f"{len(perdate_per_season[season])} pitchers")

    # Step 6: cross-pitcher whiff percentile rank per date
    print("\n" + "=" * 80)
    print("  Computing per-date whiff percentile rank")
    print("=" * 80)
    whiff_rank: dict[int, dict[str, dict[int, int]]] = {s: {} for s in seasons}
    for season in seasons:
        whiff_rank[season] = build_perdate_rank(season, perdate_per_season[season])
        print(f"  Season {season}: rank table for {len(whiff_rank[season])} dates")

    # Step 7: rebuild backtest CSVs with new xera/whiff_pct_rank columns
    print("\n" + "=" * 80)
    print("  Rebuilding backtest CSVs with point-in-time xera/whiff_pct_rank")
    print("=" * 80)
    pid_cache_path = REPO_ROOT / "data" / "pitcher_id_cache.json"
    with open(pid_cache_path, encoding="utf-8") as f:
        pid_cache = json.load(f)

    for season in seasons:
        src = REPO_ROOT / "data" / "backtests" / f"backtest_{season}-04-01_to_{season}-09-30.csv"
        dst = src.with_name(src.stem + "_truepit.csv")
        if not src.exists():
            print(f"  Skipping {season}: source {src} not found")
            continue

        with open(src, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            rows   = list(reader)

        # Stats counters
        n_xera_filled = n_whiff_filled = n_no_pid = n_no_data = 0

        for r in rows:
            pk = (r.get("game_pk") or "").strip()
            game_date = (r.get("date") or "").strip()
            pids = pid_cache.get(pk)
            if not pids:
                n_no_pid += 1
                continue
            try:
                a_pid, h_pid = int(pids[0]), int(pids[1])
            except (TypeError, ValueError, IndexError):
                n_no_pid += 1
                continue

            for col_x, col_w, pid in [
                ("away_xera", "away_whiff_pct_rank", a_pid),
                ("home_xera", "home_whiff_pct_rank", h_pid),
            ]:
                snap = perdate_per_season[season].get(pid, {}).get(game_date)
                if snap and snap.get("cum_xwoba_through_yesterday") is not None:
                    r[col_x] = f"{xwoba_to_xera_proxy(snap['cum_xwoba_through_yesterday']):.3f}"
                    n_xera_filled += 1
                else:
                    r[col_x] = f"{LEAGUE_AVG_XERA:.3f}"
                    n_no_data += 1
                rank = whiff_rank[season].get(game_date, {}).get(pid)
                if rank is not None:
                    r[col_w] = str(rank)
                    n_whiff_filled += 1
                else:
                    r[col_w] = str(NEUTRAL_PCT_RANK)

        with open(dst, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        print(f"  {season}: wrote {dst.name}")
        print(f"    xera filled (true point-in-time):  {n_xera_filled}")
        print(f"    whiff filled (true cross-pitcher rank): {n_whiff_filled}")
        print(f"    fell back to league avg (no data): {n_no_data}")
        print(f"    rows missing pitcher_ids:          {n_no_pid}")

    print("\nDone.  Re-run tools/test_variant_g_2025.py against _truepit CSVs.")


if __name__ == "__main__":
    main()

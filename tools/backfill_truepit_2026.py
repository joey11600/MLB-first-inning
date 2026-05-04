#!/usr/bin/env python3
"""
tools/backfill_truepit_2026.py -- 2026 in-progress truepit Statcast.

Adapts the 2024/2025 truepit pipeline (tools/backfill_xera_pit_perpitch.py)
to the in-progress 2026 season.  Imports helpers from the existing tool;
adds 2026-specific:
  - end_dt = today UTC (not 2026-09-30 which is in the future)
  - pitcher_ids come from picks_2026.csv (no full-season backtest CSV
    exists for 2026 yet)
  - Output is a JSON map keyed by (pitcher_id, date) with point-in-time
    xera + whiff_pct_rank values.  Consumed by tools/backtest_v2_truepit_2026.py.

USAGE
-----
  python tools/backfill_truepit_2026.py
  python tools/backfill_truepit_2026.py --until 2026-05-04   # custom end
  python tools/backfill_truepit_2026.py --no-fetch           # cache only

Cached + resumable per pitcher in data/cache/perpitch_2026/.

Runtime: ~30-60 min for ~150-250 unique pitchers active in 2026.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.backfill_xera_pit_perpitch import (
    cumulative_per_date,
    xwoba_to_xera_proxy,
    build_perdate_rank,
    NEUTRAL_PCT_RANK,
    _isnan,
)

CACHE_DIR = REPO_ROOT / "data" / "cache" / "perpitch_2026"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_pitcher_perpitch_2026(pid: int, end_dt: str,
                                force: bool = False) -> dict | None:
    """Per-pitch Statcast for one pitcher, 2026-04-01 -> end_dt.  Cached."""
    cache_path = CACHE_DIR / f"perpitch_{pid}_2026.json"
    if cache_path.exists() and not force:
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:    # noqa: BLE001
            pass

    try:
        import pybaseball as pb
    except ImportError:
        sys.exit("pip install pybaseball")

    try:
        df = pb.statcast_pitcher(start_dt="2026-04-01",
                                  end_dt=end_dt,
                                  player_id=pid)
    except Exception as exc:    # noqa: BLE001
        print(f"    [{pid}] ERROR: {exc!r}", file=sys.stderr)
        return None
    if df is None or len(df) == 0:
        out = {"n_pitches": 0, "pitches": []}
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(out, f)
        return out

    keep_cols = ["game_date", "description", "estimated_woba_using_speedangle"]
    available = [c for c in keep_cols if c in df.columns]
    pitches = []
    for _, row in df[available].iterrows():
        p = {}
        for c in available:
            v = row[c]
            if c == "game_date":
                if v is not None:
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


def build_2026_pid_set() -> set[int]:
    """Pull unique pitcher_ids from picks_2026.csv."""
    csv_path = REPO_ROOT / "data" / "picks_2026.csv"
    if not csv_path.exists():
        sys.exit(f"Missing {csv_path}")
    pids: set[int] = set()
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for k in ("away_pitcher_id", "home_pitcher_id"):
                v = (row.get(k) or "").strip()
                if v:
                    try:
                        pids.add(int(v))
                    except ValueError:
                        pass
    return pids


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--until", default=None,
                    help="End date YYYY-MM-DD (default: today UTC).")
    ap.add_argument("--no-fetch", action="store_true",
                    help="Don't call API; only use cache.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after N pitchers (debug).")
    ap.add_argument("--out",
                    default="data/v2_perfect_2026/truepit_per_pitcher_per_date.json",
                    help="Output JSON path.")
    args = ap.parse_args()

    end_dt = args.until or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print("=" * 80)
    print(f"  2026 truepit Statcast backfill: 2026-04-01 -> {end_dt}")
    print("=" * 80)

    pid_set = build_2026_pid_set()
    pids = sorted(pid_set)
    print(f"\n  {len(pids)} unique pitcher IDs in picks_2026")
    if args.limit:
        pids = pids[:args.limit]
        print(f"  (--limit {args.limit}: processing first {len(pids)} only)")

    by_pitcher: dict[int, dict] = {}
    n_cached = n_fetched = n_failed = 0
    for i, pid in enumerate(pids, 1):
        cache_path = CACHE_DIR / f"perpitch_{pid}_2026.json"
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                by_pitcher[pid] = json.load(f)
            n_cached += 1
            continue
        if args.no_fetch:
            continue
        t0 = time.time()
        data = fetch_pitcher_perpitch_2026(pid, end_dt)
        dt = time.time() - t0
        if data:
            by_pitcher[pid] = data
            n_pitches = data.get("n_pitches", 0)
            n_fetched += 1
            print(f"    {i:>3}/{len(pids)}  pid={pid}  {n_pitches:>5} pitches  ({dt:.1f}s)",
                  flush=True)
        else:
            n_failed += 1
            print(f"    {i:>3}/{len(pids)}  pid={pid}  FETCH FAILED  ({dt:.1f}s)",
                  flush=True)

    print(f"\n  Done fetching.  cached={n_cached}  fetched={n_fetched}  failed={n_failed}")

    # Build per-pitcher per-date cumulative xera + whiff_pct_rank
    print("\n  Building per-date cumulative xera + whiff_pct_rank...")
    perdate: dict[int, dict] = {}
    for pid, data in by_pitcher.items():
        pitches = data.get("pitches", [])
        if not pitches:
            continue
        perdate[pid] = cumulative_per_date(pitches)

    whiff_rank = build_perdate_rank(2026, perdate)
    print(f"  Built per-date snapshots for {len(perdate)} pitchers, "
          f"{len(whiff_rank)} rank-able dates")

    # Serialize: {pitcher_id_str: {date: {xera, whiff_pct_rank}}}
    output = {
        "season":     2026,
        "end_date":   end_dt,
        "n_pitchers": len(by_pitcher),
        "fitted_at":  datetime.now(timezone.utc).replace(tzinfo=None)
                              .isoformat(timespec="seconds") + "Z",
        "per_pitcher": {
            str(pid): {
                date: {
                    "xera":           round(xwoba_to_xera_proxy(
                                          snap.get("cum_xwoba_through_yesterday")), 4),
                    "whiff_pct_rank": whiff_rank.get(date, {}).get(pid,
                                                                    NEUTRAL_PCT_RANK),
                    "n_balls_thru":   snap.get("n_batted_balls_through_yesterday", 0),
                    "n_swings_thru":  snap.get("cum_swings_through_yesterday", 0),
                }
                for date, snap in d.items()
            }
            for pid, d in perdate.items()
        },
    }
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Saved -> {out_path}")


if __name__ == "__main__":
    main()

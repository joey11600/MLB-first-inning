#!/usr/bin/env python3
"""
tools/build_catcher_framing.py — compute catcher framing (T4.1).

CONTEXT
-------
The published Baseball Savant catcher framing leaderboard is broken in
pybaseball (their CSV endpoint returns HTML now).  Their "framing runs
above average" metric is computed from a probabilistic strike model
that's not publicly published.

This script computes a SIMPLER PROXY: extra_called_strikes per catcher
per season.  For all "borderline" pitches (those near the edge of the
strike zone — described below), how many did each catcher convert from
ball to called-strike vs the league baseline?

DEFINITION
----------
A pitch is "borderline" if either:
  - It's just inside the strike zone but called a ball (lost strike), OR
  - It's just outside the strike zone but called a strike (stolen strike)

We define "just outside" / "just inside" via the standard Savant edge
definition: pitch location within the "shadow zone":
  - horizontally: |plate_x| in [10/12, 14/12] feet from center
  - vertically:   plate_z in [sz_bot - 4/12, sz_bot + 4/12] OR
                  plate_z in [sz_top - 4/12, sz_top + 4/12]
  - 4/12 feet = ~4 inches (one baseball width)

For each catcher, compute:
  shadow_strike_rate = called_strikes_in_shadow / total_called_in_shadow
where total_called_in_shadow = called_strikes + balls (excludes swings,
hbp, hits-into-play).

Catcher framing score = shadow_strike_rate - league_avg_shadow_strike_rate.
Positive means above-average framer; negative means below-average.

INPUTS
------
Per-pitch Statcast data via pb.statcast(start, end).  Returns ~700K
pitches per season; we chunk by month and keep only fields we need.

OUTPUTS
-------
data/catcher_framing_cache.json:
  {
    "<season>": {
      "_meta":  {"league_shadow_strike_rate": 0.46, "n_pitches": ...},
      "<catcher_player_id>": {
        "name":             "Patrick Bailey",
        "shadow_pitches":   3120,
        "shadow_strikes":   1612,
        "shadow_strike_rate": 0.5167,
        "framing_score":    +0.0567,    # vs league baseline
        "extra_strikes":    +176.9,     # estimated extra strikes captured
      },
      ...
    },
    ...
  }

USAGE
-----
  python tools/build_catcher_framing.py --season 2024
  python tools/build_catcher_framing.py --month 2024-07   # debug, 1 month only
  python tools/build_catcher_framing.py --all             # 2024 + 2025

ETA: ~5-10 min per season via pb.statcast (which chunks by month internally).
Resumable: writes after each season completes.
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
OUT_PATH  = REPO_ROOT / "data" / "catcher_framing_cache.json"

# Strike zone shadow definition (Statcast convention):
# - Plate is 17 inches wide -> half-width = 8.5 inches = 0.708 feet.
# - "Edge" zone: |plate_x| in [0.708 - 4/12, 0.708 + 4/12] = [0.375, 1.042].
# - Vertical edge: plate_z within +/- 4/12 of sz_bot or sz_top.
EDGE_INCHES = 4.0
EDGE_FEET   = EDGE_INCHES / 12.0
ZONE_HALF_X = 8.5 / 12.0   # 0.7083 ft

CALLED_STRIKE = "called_strike"
BALL          = "ball"
CALLED_DESCS  = {CALLED_STRIKE, BALL}     # only these count for framing


def _safe_float(v) -> float | None:
    """Coerce pandas NA/NaN/None safely to None; return float otherwise."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:    # NaN check
        return None
    return f


def is_in_shadow(plate_x, plate_z, sz_top, sz_bot) -> bool:
    """True if pitch is in the borderline shadow zone (just inside or
    outside the strike zone by <= 4 inches horizontally OR vertically).
    Robust to pandas NA values."""
    px = _safe_float(plate_x)
    pz = _safe_float(plate_z)
    st = _safe_float(sz_top)
    sb = _safe_float(sz_bot)
    if px is None or pz is None or st is None or sb is None:
        return False
    horiz_close = abs(abs(px) - ZONE_HALF_X) <= EDGE_FEET
    vert_close = (
        abs(pz - st) <= EDGE_FEET or
        abs(pz - sb) <= EDGE_FEET
    )
    return horiz_close or vert_close


def fetch_season_pitches(season: int):
    """Fetch all per-pitch data for a season via pybaseball.statcast.
    Yields per-month chunks to keep memory bounded."""
    try:
        import pybaseball as pb
    except ImportError:
        sys.exit("pip install pybaseball")

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
            print(f"      0 pitches", flush=True)
            continue
        yield df


def aggregate_framing(season: int) -> dict:
    """Walk per-pitch data for the season, accumulate per-catcher
    shadow-zone called-pitch counts."""
    # catcher_id -> {"shadow_strikes": int, "shadow_balls": int}
    cat_counts: dict[int, dict] = defaultdict(
        lambda: {"shadow_strikes": 0, "shadow_balls": 0, "name": ""}
    )
    league_strikes = 0
    league_balls   = 0
    n_pitches      = 0
    n_called       = 0

    for df in fetch_season_pitches(season):
        # Filter to called pitches (no swings, no contact)
        called = df[df["description"].isin(CALLED_DESCS)]
        n_pitches += len(df)
        n_called  += len(called)
        # Iterate -- vectorize would be faster but readability wins for now
        for _, row in called.iterrows():
            px = row.get("plate_x")
            pz = row.get("plate_z")
            sz_top = row.get("sz_top")
            sz_bot = row.get("sz_bot")
            if not is_in_shadow(px, pz, sz_top, sz_bot):
                continue
            cid = row.get("fielder_2")
            if cid is None or (isinstance(cid, float) and cid != cid):  # NaN
                continue
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                continue
            desc = row.get("description")
            rec = cat_counts[cid]
            if desc == CALLED_STRIKE:
                rec["shadow_strikes"] += 1
                league_strikes += 1
            else:  # BALL
                rec["shadow_balls"] += 1
                league_balls += 1
        del df    # free memory

    # League baseline
    league_total = league_strikes + league_balls
    league_rate  = (league_strikes / league_total) if league_total else 0.0
    print(f"    season {season}: {n_pitches:,} pitches, "
          f"{n_called:,} called, {league_total:,} in shadow, "
          f"league rate {league_rate*100:.2f}%")

    # Per-catcher: rate + framing score + extra strikes
    out: dict[str, dict] = {
        "_meta": {
            "league_shadow_strike_rate": round(league_rate, 6),
            "n_pitches_total":           n_pitches,
            "n_called_pitches":          n_called,
            "n_shadow_pitches":          league_total,
            "n_catchers":                len(cat_counts),
        }
    }
    for cid, rec in cat_counts.items():
        total = rec["shadow_strikes"] + rec["shadow_balls"]
        if total < 50:    # filter low-volume catchers (backups, callups)
            continue
        rate = rec["shadow_strikes"] / total
        framing_score = rate - league_rate
        # Estimated extra strikes vs league avg = framing_score * total_shadow
        extra = framing_score * total
        out[str(cid)] = {
            "shadow_pitches":      total,
            "shadow_strikes":      rec["shadow_strikes"],
            "shadow_strike_rate":  round(rate, 4),
            "framing_score":       round(framing_score, 4),
            "extra_strikes":       round(extra, 1),
        }
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, action="append", default=None,
                    help="Restrict to season(s).  Default: 2024.")
    ap.add_argument("--all", action="store_true",
                    help="Process 2024 + 2025.")
    ap.add_argument("--month", default=None,
                    help="Debug mode: process only one month (YYYY-MM).")
    args = ap.parse_args()

    if args.all:
        seasons = [2024, 2025]
    else:
        seasons = args.season or [2024]

    cache: dict = {}
    if OUT_PATH.exists():
        with open(OUT_PATH, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"Loaded existing cache: {sum(len(v) for v in cache.values() if isinstance(v, dict))} catcher entries")

    for season in seasons:
        sstr = str(season)
        if sstr in cache and len(cache[sstr]) > 50:
            print(f"  {season}: already cached ({len(cache[sstr])} entries); skipping")
            continue
        print(f"\nProcessing season {season}...")
        cache[sstr] = aggregate_framing(season)
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=0, separators=(",", ":"))
        print(f"  Saved -> {OUT_PATH}")

    # Summary
    print()
    print("=" * 84)
    print("  SUMMARY: top + bottom catchers by framing_score per season")
    print("=" * 84)
    for sstr, ydata in sorted(cache.items()):
        if not isinstance(ydata, dict):
            continue
        meta = ydata.get("_meta", {})
        print(f"\n  Season {sstr}:")
        print(f"    league shadow strike rate: {meta.get('league_shadow_strike_rate', 0)*100:.2f}%")
        print(f"    n_catchers: {meta.get('n_catchers', 0)}")
        catchers = [(cid, c) for cid, c in ydata.items() if cid != "_meta"]
        catchers.sort(key=lambda t: -t[1].get("framing_score", 0))
        print(f"    TOP 5 framers:")
        for cid, c in catchers[:5]:
            print(f"      pid {cid:>7}: rate {c['shadow_strike_rate']*100:5.2f}%  "
                  f"score {c['framing_score']:+.4f}  extra strikes {c['extra_strikes']:+6.1f}  "
                  f"({c['shadow_pitches']:>4} pitches)")
        print(f"    BOTTOM 5 framers:")
        for cid, c in catchers[-5:]:
            print(f"      pid {cid:>7}: rate {c['shadow_strike_rate']*100:5.2f}%  "
                  f"score {c['framing_score']:+.4f}  extra strikes {c['extra_strikes']:+6.1f}  "
                  f"({c['shadow_pitches']:>4} pitches)")


if __name__ == "__main__":
    main()

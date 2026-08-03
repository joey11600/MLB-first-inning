#!/usr/bin/env python3
"""
tools/scrape_statcast_zone.py -- re-scrape Savant KEEPING the zone columns.

WHY THIS EXISTS
---------------
`data/cache/perpitch/` looked like a Statcast cache and is not one. Audited
2026-08-03: 1,041,080 records across 933 files, and every record has exactly
THREE keys --

    game_date, description, estimated_woba_using_speedangle

-- so `plate_x`, `plate_z`, `sz_top`, `sz_bot` are absent. The note that
"119 columns are verified reachable" was true of the ENDPOINT and never of
the cache. That single gap blocked four of the remaining backlog factors at
once (#9 chase rate, #5 zone rate, #14 umpire called-strike-above-expected,
#15 zone geometry): all of them need to know where the pitch crossed the
plate relative to that batter's own strike zone, and none of that survived
the original scrape.

WHAT THIS KEEPS, AND WHY EACH ONE
---------------------------------
22 of the 119 columns. Everything needed to rebuild the blocked factors,
nothing else, because the full payload is ~3 MB/day and most of it is
tracking telemetry no factor here has ever asked for.

    game_date game_pk inning        join keys + first-inning filter
    pitcher batter stand p_throws   who, and the platoon split
    plate_x plate_z sz_top sz_bot   THE POINT OF THIS SCRAPE
    description type zone           called/swinging/in-play, Savant's own zone
    balls strikes                   count state, for first-pitch-strike work
    pitch_type release_speed        mix entropy, and velocity vs own mean
    events                          PA outcomes, for K rate
    estimated_woba_using_speedangle launch_speed delta_run_exp   quality

ALL INNINGS, NOT JUST THE FIRST. The original scrape kept inning 1 only,
which is why factor #6 (a pitcher's first-inning velocity RELATIVE to his
own season mean) could never be built -- the denominator was thrown away.
Keeping every inning costs disk but NOT time: the endpoint returns the whole
day in one request either way.

BEHAVIOUR
---------
  - one request per calendar date, gzipped CSV per day under
    data/cache/statcast_zone/<date>.csv.gz
  - RESUMABLE: an existing non-empty file for a date is skipped, so an
    interrupted run continues where it stopped
  - a date with no games writes a header-only file, so it is not retried
    forever
  - polite: --delay seconds between requests, default 1.0
  - retries with backoff; a date that fails all retries is logged and the
    run continues rather than aborting

USAGE
    python tools/scrape_statcast_zone.py                    # 2024+2025+2026
    python tools/scrape_statcast_zone.py --seasons 2026
    python tools/scrape_statcast_zone.py --delay 2.0
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cache" / "statcast_zone"

KEEP = [
    "game_date", "game_pk", "inning",
    "pitcher", "batter", "stand", "p_throws",
    "plate_x", "plate_z", "sz_top", "sz_bot",
    "description", "type", "zone",
    "balls", "strikes",
    "pitch_type", "release_speed",
    "events",
    "estimated_woba_using_speedangle", "launch_speed", "delta_run_exp",
]

URL = (
    "https://baseballsavant.mlb.com/statcast_search/csv?all=true"
    "&hfSea={season}%7C&hfGT=R%7C&player_type=pitcher"
    "&min_pitches=0&min_results=0&group_by=name&sort_col=pitches"
    "&player_event_sort=api_p_release_speed&sort_order=desc&type=details"
    "&game_date_gt={d}&game_date_lt={d}"
)

# Regular season only; the endpoint is filtered to hfGT=R anyway, these
# bounds just avoid ~200 pointless requests into the off-season.
WINDOWS = {
    2024: ("2024-03-20", "2024-10-01"),
    2025: ("2025-03-18", "2025-10-01"),
    2026: ("2026-03-25", "2026-08-03"),
}


def fetch(season: int, d: str, tries: int = 4, timeout: int = 180) -> list[dict] | None:
    url = URL.format(season=season, d=d)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            # utf-8-sig: the payload carries a BOM and a naive decode breaks
            # the first column name, which silently drops game_date.
            return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == tries - 1:
                print(f"  {d}  FAILED after {tries} tries: {e}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt * 3)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    tot_days = tot_rows = skipped = failed = 0
    t0 = time.time()

    for season in args.seasons:
        lo, hi = WINDOWS[season]
        d = dt.date.fromisoformat(lo)
        end = dt.date.fromisoformat(hi)
        while d <= end:
            iso = d.isoformat()
            path = OUT / f"{iso}.csv.gz"
            if path.exists() and path.stat().st_size > 0:
                skipped += 1
                d += dt.timedelta(days=1)
                continue

            rows = fetch(season, iso)
            if rows is None:
                failed += 1
                d += dt.timedelta(days=1)
                continue

            slim = [{k: r.get(k, "") for k in KEEP} for r in rows]
            with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=KEEP)
                w.writeheader()
                w.writerows(slim)

            tot_days += 1
            tot_rows += len(slim)
            if tot_days % 20 == 0 or len(slim) == 0:
                el = time.time() - t0
                print(f"  {iso}  {len(slim):5d} pitches   "
                      f"[{tot_days} fetched, {tot_rows:,} rows, {el/60:.1f} min]",
                      flush=True)
            time.sleep(args.delay)
            d += dt.timedelta(days=1)

    el = time.time() - t0
    print(f"\nDONE  fetched {tot_days} days ({tot_rows:,} pitches), "
          f"skipped {skipped} already cached, {failed} failed, {el/60:.1f} min")
    print(f"  -> {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""tools/backfill_top3_last10.py

Phase G backfill: compute top-3 batter LAST-10-GAMES OBP/SLG/ISO
features for every row in `data/picks_2026.csv` (or a backtest CSV)
and write the six new columns:

    away_top3c_last10_obp / slg / iso
    home_top3c_last10_obp / slg / iso

These are the team-mean of the top 3 batters' last-10-game rates,
falling back to prior-season tail and finally to league averages when
the batter has too few recent games (early-April, call-ups).

Implementation calls `backtest.top3_last10_stats()`, which goes through
the per-batter gameLog cache (`data/cache/batter_gamelog/`).  First
run on a player warms the cache; subsequent calls in the same backfill
hit the cache for free.  Expect ~1000 unique batters across a full
season × 2 seasons -- after warming the cache the backfill is mostly
local arithmetic.

Usage:
  python tools/backfill_top3_last10.py                        # all of picks_2026.csv
  python tools/backfill_top3_last10.py --since 2026-05-01     # date filter
  python tools/backfill_top3_last10.py --max-rows 20          # smoke-test
  python tools/backfill_top3_last10.py --file data/backtests/backtest_2025-...csv \\
                                       --lineup-source mlb-api  # backtest CSV w/o lineup JSON
  python tools/backfill_top3_last10.py --dry-run              # show counts; do not write

Two lineup sources:
  - `csv-json`  (default): read `away_lineup_json` / `home_lineup_json`
                from the row.  picks_2026.csv has these.
  - `mlb-api`:  fetch the actual top-3 batter IDs from the MLB Stats
                API via backtest.fetch_top3_batters(game_pk).  Used
                for backtest CSVs (2024 / 2025) which don't store the
                lineup JSON.  Slower (one API call per game).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Late imports so the --help path doesn't pay the import cost.
def _lazy_imports():
    global top3_last10_stats, fetch_top3_batters, FIELDS
    from backtest import top3_last10_stats, fetch_top3_batters
    from tracker import FIELDS
    return top3_last10_stats, fetch_top3_batters, FIELDS


NEW_COLS = [
    "away_top3c_last10_obp", "home_top3c_last10_obp",
    "away_top3c_last10_slg", "home_top3c_last10_slg",
    "away_top3c_last10_iso", "home_top3c_last10_iso",
]


def _parse_ids_from_lineup_json(s: str) -> list[int]:
    """Extract player IDs from an away/home_lineup_json CSV cell.

    Format (as produced by mlb_first_inning_predictor's --predict path):
      '[{"id": 592450, "name": "Aaron Judge", ...}, {...}, {...}]'
    """
    s = (s or "").strip()
    if not s or s == "[]":
        return []
    try:
        arr = json.loads(s)
    except json.JSONDecodeError:
        return []
    out: list[int] = []
    for item in arr[:3]:
        if isinstance(item, dict):
            pid = item.get("id")
            if isinstance(pid, int):
                out.append(pid)
            elif isinstance(pid, str) and pid.isdigit():
                out.append(int(pid))
        elif isinstance(item, int):
            out.append(item)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--file", default=str(ROOT / "data" / "picks_2026.csv"),
                   help="CSV to backfill (default: picks_2026.csv).")
    p.add_argument("--lineup-source", choices=["csv-json", "mlb-api"],
                   default="csv-json",
                   help="Where to get the top-3 batter IDs.  csv-json reads "
                        "away/home_lineup_json columns; mlb-api falls back to "
                        "fetch_top3_batters(game_pk).")
    p.add_argument("--since",
                   help="Only process rows with date >= this ISO date.")
    p.add_argument("--until",
                   help="Only process rows with date <= this ISO date.")
    p.add_argument("--max-rows", type=int, default=0,
                   help="Smoke-test mode: stop after N rows (0 = unlimited).")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute features but do not write the CSV.")
    p.add_argument("--rate-limit-ms", type=int, default=0,
                   help="Sleep this many ms between rows (MLB API rate limit).")
    args = p.parse_args()

    top3_last10_stats, fetch_top3_batters, FIELDS = _lazy_imports()

    fp = Path(args.file)
    if not fp.exists():
        print(f"ERROR: file not found: {fp}", file=sys.stderr)
        return 2

    print(f"backfill_top3_last10  file={fp.name}  lineup_source={args.lineup_source}  dry_run={args.dry_run}")
    with open(fp, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header_in = reader.fieldnames or []
    print(f"  rows in file: {len(rows)}")

    # Ensure new columns appended to the header for write-back.
    new_header = list(header_in)
    for col in NEW_COLS:
        if col not in new_header:
            new_header.append(col)
    print(f"  new columns added to header: "
          f"{[c for c in NEW_COLS if c not in header_in]}")

    processed = 0
    skipped_no_lineup = 0
    skipped_filter = 0
    cache_warmups = 0
    t0 = time.time()

    for r in rows:
        # Date filters
        d = r.get("date", "")
        if args.since and d < args.since:
            skipped_filter += 1
            continue
        if args.until and d > args.until:
            skipped_filter += 1
            continue
        if not d:
            skipped_filter += 1
            continue

        season = int(d[:4])

        # Resolve away + home top-3 player IDs
        if args.lineup_source == "csv-json":
            away_ids = _parse_ids_from_lineup_json(r.get("away_lineup_json", ""))
            home_ids = _parse_ids_from_lineup_json(r.get("home_lineup_json", ""))
        else:
            game_pk = r.get("game_pk", "")
            if not game_pk:
                skipped_no_lineup += 1
                continue
            try:
                top3 = fetch_top3_batters(int(game_pk))
                away_ids = [b.get("id") for b in (top3.get("away") or [])][:3]
                home_ids = [b.get("id") for b in (top3.get("home") or [])][:3]
                away_ids = [pid for pid in away_ids if isinstance(pid, int)]
                home_ids = [pid for pid in home_ids if isinstance(pid, int)]
                cache_warmups += 1
            except Exception as exc:    # noqa: BLE001
                skipped_no_lineup += 1
                continue

        if not away_ids and not home_ids:
            skipped_no_lineup += 1
            continue

        away_stats = top3_last10_stats(away_ids, d, season) if away_ids else None
        home_stats = top3_last10_stats(home_ids, d, season) if home_ids else None

        if away_stats:
            r["away_top3c_last10_obp"] = f"{away_stats['obp']:.4f}"
            r["away_top3c_last10_slg"] = f"{away_stats['slg']:.4f}"
            r["away_top3c_last10_iso"] = f"{away_stats['iso']:.4f}"
        if home_stats:
            r["home_top3c_last10_obp"] = f"{home_stats['obp']:.4f}"
            r["home_top3c_last10_slg"] = f"{home_stats['slg']:.4f}"
            r["home_top3c_last10_iso"] = f"{home_stats['iso']:.4f}"

        processed += 1
        if processed % 25 == 0:
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            print(f"  ...{processed} rows in {elapsed:.1f}s ({rate:.1f}/s)")
        if args.max_rows and processed >= args.max_rows:
            print(f"  --max-rows={args.max_rows} hit, stopping.")
            break
        if args.rate_limit_ms:
            time.sleep(args.rate_limit_ms / 1000.0)

    elapsed = time.time() - t0
    print()
    print(f"Summary:")
    print(f"  rows processed   : {processed}")
    print(f"  skipped (filter) : {skipped_filter}")
    print(f"  skipped (no lineup): {skipped_no_lineup}")
    print(f"  cache warmups (API): {cache_warmups}")
    print(f"  elapsed          : {elapsed:.1f}s")

    if args.dry_run:
        print("  [dry-run] not writing CSV.")
        return 0
    if processed == 0:
        print("  no rows processed; not rewriting CSV.")
        return 0

    # Write back to a temp file, then replace -- atomic write.
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=new_header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    import os
    os.replace(tmp, fp)
    print(f"  wrote {fp.name} with {len(new_header)} columns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

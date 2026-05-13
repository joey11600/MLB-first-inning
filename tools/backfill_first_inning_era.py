#!/usr/bin/env python3
"""tools/backfill_first_inning_era.py

Phase 2.1 backfill: compute `home_first_inning_era` /
`away_first_inning_era` features for every row in a picks/backtest CSV.

Each value is a point-in-time Bayesian-blended ERA produced by
`backtest.fetch_pitcher_first_inning_era(player_id, as_of_date)`, which
shrinks the pitcher's observed FI ERA across the two prior seasons
toward the year-indexed `LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON[target]`
prior using `N_PRIOR_FI=30` IP-equivalents.  See that function's
docstring for the shrinkage rationale.

Reads `away_pitcher_id`, `home_pitcher_id`, and `date` from each row.
First call per (player_id, date) hits the cache cold and makes 1-2
API calls (one per prior season); subsequent calls hit the disk cache
under `data/cache/pitcher_fi_era/`.

Atomic write: writes to file.tmp then `os.replace`, mirroring
`tools/backfill_top3_last10.py`.

Usage:
    # Live picks ledger (default):
    python tools/backfill_first_inning_era.py

    # A specific backtest CSV:
    python tools/backfill_first_inning_era.py \
        --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv

    # Smoke test -- 20 rows, do not write the CSV:
    python tools/backfill_first_inning_era.py --max-rows 20 --dry-run

    # Stratified sample across multiple files (step 5.5 validation):
    python tools/backfill_first_inning_era.py --dry-run --stats \
        --file <2024.csv> --file <2025.csv> --file <2026.csv> \
        --stratified-target 100

    # Date filter:
    python tools/backfill_first_inning_era.py --since 2026-04-01
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _lazy_imports():
    global fetch_pitcher_first_inning_era, LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON
    global _LEAGUE_FI_AVG_ERA_FALLBACK
    from backtest import (
        fetch_pitcher_first_inning_era,
        LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON,
        _LEAGUE_FI_AVG_ERA_FALLBACK,
    )
    return (fetch_pitcher_first_inning_era,
            LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON,
            _LEAGUE_FI_AVG_ERA_FALLBACK)


NEW_COLS = [
    "away_first_inning_era",
    "home_first_inning_era",
]


def _row_pid(r: dict, side: str) -> int | None:
    raw = (r.get(f"{side}_pitcher_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _stratified_indices(n_rows: int, target: int) -> list[int]:
    """Return indices spanning [0, n_rows) at roughly even spacing,
    so we cover the full date range without consecutive duplicates."""
    if target >= n_rows:
        return list(range(n_rows))
    if target <= 0:
        return []
    step = n_rows / target
    return [int(i * step) for i in range(target)]


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--file", action="append", default=[],
                   help="CSV to backfill.  Can repeat for stratified-sample mode. "
                        "Default: data/picks_2026.csv when none given.")
    p.add_argument("--since",
                   help="Only process rows with date >= this ISO date.")
    p.add_argument("--until",
                   help="Only process rows with date <= this ISO date.")
    p.add_argument("--max-rows", type=int, default=0,
                   help="Smoke-test mode: stop after N rows total (0 = unlimited).")
    p.add_argument("--stratified-target", type=int, default=0,
                   help="Sample this many rows total, evenly spaced across the "
                        "combined input.  Implies --dry-run.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute features but do not write the CSV.")
    p.add_argument("--stats", action="store_true",
                   help="Emit the 5-point validation report (quality counts, "
                        "mean/stddev, clamp hits, all-fallback rows, top/bottom 5).")
    p.add_argument("--rate-limit-ms", type=int, default=0,
                   help="Sleep this many ms between rows (MLB API rate limit).")
    args = p.parse_args()

    # Stratified mode implies dry-run and ignores per-file order.
    if args.stratified_target:
        args.dry_run = True

    (fetch_fn,
     prior_table,
     prior_fallback) = _lazy_imports()

    files = args.file or [str(ROOT / "data" / "picks_2026.csv")]
    files = [Path(f) for f in files]
    for fp in files:
        if not fp.exists():
            print(f"ERROR: file not found: {fp}", file=sys.stderr)
            return 2

    print(f"backfill_first_inning_era  files={[f.name for f in files]}  "
          f"dry_run={args.dry_run}  stats={args.stats}  "
          f"stratified_target={args.stratified_target}")
    print()

    # Load all rows up front.  Keep per-file headers so we can write each back.
    file_rows: dict[Path, tuple[list[str], list[dict]]] = {}
    combined: list[tuple[Path, int, dict]] = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            header_in = reader.fieldnames or []
        file_rows[fp] = (header_in, rows)
        for i, r in enumerate(rows):
            combined.append((fp, i, r))
        print(f"  loaded {fp.name}: {len(rows)} rows")

    # Date filter applies before stratified sampling.
    def keep(r: dict) -> bool:
        d = (r.get("date") or "")[:10]
        if not d:
            return False
        if args.since and d < args.since:
            return False
        if args.until and d > args.until:
            return False
        return True
    combined = [t for t in combined if keep(t[2])]
    print(f"  after date filter: {len(combined)} rows")

    # Stratify
    if args.stratified_target:
        idx = _stratified_indices(len(combined), args.stratified_target)
        combined = [combined[i] for i in idx]
        print(f"  after stratify (target={args.stratified_target}): {len(combined)} rows")

    # Pre-compute fallback bounds for clamp tracking.  Pre-blend clamps
    # fire when observed_era is outside [1.0, 12.0]; post-blend clamps
    # fire when blended is outside [1.0, 9.0].  Reproduce them here so
    # the report can flag any rows that hit them.
    EPS = 1e-9

    processed       = 0
    skipped_no_pid  = 0
    quality_counts  = {"live": 0, "ltd": 0, "sm": 0, "avg": 0}
    all_eras: list[tuple[str, str, str, str, float, str]] = []
    # entries: (date, file, away/home, pitcher_id, era, quality)
    fallback_hits = 0
    post_clamp_hi = 0   # blended at 9.0
    post_clamp_lo = 0   # blended at 1.0

    t0 = time.time()

    for fp, idx, r in combined:
        d = (r.get("date") or "")[:10]
        away_pid = _row_pid(r, "away")
        home_pid = _row_pid(r, "home")
        if not away_pid and not home_pid:
            skipped_no_pid += 1
            continue

        for side, pid in (("away", away_pid), ("home", home_pid)):
            if not pid:
                continue
            era, q = fetch_fn(pid, d)
            quality_counts[q] = quality_counts.get(q, 0) + 1
            r[f"{side}_first_inning_era"] = f"{era:.3f}"

            target_season = int(d[:4]) if d else 2026
            prior_mean = prior_table.get(target_season, prior_fallback)
            # Detect "all-fallback path" -- the fetcher returns
            # exactly the prior when there's < 3 FI IP or no data.
            if q == "avg" and abs(era - prior_mean) < EPS:
                fallback_hits += 1
            if abs(era - 9.0) < EPS: post_clamp_hi += 1
            if abs(era - 1.0) < EPS: post_clamp_lo += 1

            all_eras.append((d, fp.name, side, str(pid), era, q))

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
    print("Summary:")
    print(f"  rows processed     : {processed}")
    print(f"  skipped (no PID)   : {skipped_no_pid}")
    print(f"  pitcher-feature outputs (total): {sum(quality_counts.values())}")
    print(f"  quality counts: {quality_counts}")
    print(f"  elapsed            : {elapsed:.1f}s")

    if args.stats:
        total = sum(quality_counts.values())
        print()
        print("=" * 60)
        print("Step-5.5 5-point validation report")
        print("=" * 60)
        # 1) Quality distribution
        print("\n[1] Quality-tag distribution:")
        for q in ("live", "ltd", "sm", "avg"):
            n = quality_counts.get(q, 0)
            pct = n / total * 100 if total else 0
            print(f"      {q:>4}: {n:>4}  ({pct:>5.1f}%)")
        # 2) Mean / stddev of computed FI ERA
        eras = [t[4] for t in all_eras]
        if eras:
            print(f"\n[2] Computed FI ERA distribution (n={len(eras)}):")
            print(f"      mean   = {statistics.mean(eras):.4f}")
            print(f"      stddev = {statistics.stdev(eras) if len(eras)>1 else 0:.4f}")
            print(f"      min/max = {min(eras):.4f} / {max(eras):.4f}")
        # 3) Clamp hits at outer bounds
        print(f"\n[3] Post-blend clamp hits (1.0 or 9.0 bounds):")
        print(f"      lower bound (=1.0): {post_clamp_lo}")
        print(f"      upper bound (=9.0): {post_clamp_hi}")
        # 4) All-fallback rows
        print(f"\n[4] Rows that returned exactly the prior (all-fallback path):")
        print(f"      {fallback_hits} / {total}  ({fallback_hits/total*100 if total else 0:.1f}%)")
        # 5) Highest and lowest 5
        if all_eras:
            sorted_e = sorted(all_eras, key=lambda x: x[4])
            print("\n[5a] Lowest 5 FI ERA values:")
            for d, fn, side, pid, era, q in sorted_e[:5]:
                print(f"      {d}  {fn:<45} {side}_pid={pid:<8} era={era:.3f}  q={q}")
            print("\n[5b] Highest 5 FI ERA values:")
            for d, fn, side, pid, era, q in sorted_e[-5:]:
                print(f"      {d}  {fn:<45} {side}_pid={pid:<8} era={era:.3f}  q={q}")

    if args.dry_run:
        print("\n[dry-run] not writing CSV.")
        return 0
    if processed == 0:
        print("\nno rows processed; not rewriting CSV.")
        return 0

    # Write each input file back with the new columns.
    for fp, (header_in, rows) in file_rows.items():
        new_header = list(header_in)
        for col in NEW_COLS:
            if col not in new_header:
                new_header.append(col)
        added = [c for c in NEW_COLS if c not in header_in]
        if added:
            print(f"  {fp.name}: appending new header columns {added}")
        tmp = fp.with_suffix(fp.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=new_header, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        import os
        os.replace(tmp, fp)
        print(f"  wrote {fp.name} ({len(new_header)} columns)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

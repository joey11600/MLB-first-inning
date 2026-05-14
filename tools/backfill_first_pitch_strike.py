#!/usr/bin/env python3
"""tools/backfill_first_pitch_strike.py

Phase 2.2 backfill: compute `home_first_pitch_strike_pct` /
`away_first_pitch_strike_pct` features for picks/backtest CSVs.

Two-stage design:

  Stage 1 (heavy, per-pitcher per-season fetch):
    For each unique (pitcher_id, season) tuple needed by the input
    CSVs' date ranges, pull the pitcher's per-pitch Statcast data via
    pybaseball.statcast_pitcher.  Filter to inning=1, balls=0,
    strikes=0 (the first pitch of each first-inning plate appearance).
    Persist to data/cache/pitcher_fps_perpitch/perpitch_<pid>_<year>.json
    as {pid, year, events: [{game_date, is_strike}, ...]}.

    Cache freshness rules (per operator decision, 2026-05-14):
      - Closed-season caches (season < current calendar year) use 90-day
        TTL.  Statcast occasionally makes retroactive corrections but
        those are rare; 90d is a generous catchall without thrashing
        daily refreshes.  In practice = "never refetched in normal ops."
      - Current-season caches use 12-hour TTL so daily cron picks up
        new starts as they're played.
      - --refresh-cache bypasses TTL entirely for manual full-refresh.

  Stage 2 (fast, CSV writeback):
    Iterate input CSV rows.  For each row, call
    backtest.fetch_pitcher_first_pitch_strike_pct(pid, date) for both
    home and away pitchers.  Write columns atomically.  Blank for rows
    where pitcher_id is empty (the 47/35/7 unresolved-PID postponement
    rows from Phase 2.1's PID backfill -- expected tie-out).

Pooling rule: per backtest.fetch_pitcher_first_pitch_strike_pct, the
fetcher pools events across [_FPS_SEASON_FLOOR=2021, target_season]
with game_date < as_of_date.  So for 2024 backfill we need 2021-2023
per-pitcher caches; for 2025 we need 2021-2024; for 2026 we need
2021-2025 + 2026-YTD.  This script computes the precise union of
(pid, year) tuples we need and fetches only those.

Same atomic write + flag conventions as backfill_first_inning_era.py.

Usage:
    # Smoke test on picks_2026 (20 rows, no writes):
    python tools/backfill_first_pitch_strike.py --max-rows 20 --dry-run --stats

    # Stratified 100-row dry-run across all three CSVs:
    python tools/backfill_first_pitch_strike.py --dry-run --stats \\
        --stratified-target 100 \\
        --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv \\
        --file data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv \\
        --file data/picks_2026.csv

    # Full 2024 backfill (writes back to CSV):
    python tools/backfill_first_pitch_strike.py \\
        --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv

    # Force-refresh per-pitch cache (re-fetch all pybaseball data):
    python tools/backfill_first_pitch_strike.py --refresh-cache --file ...

    # Skip Stage 1 (use existing cache as-is, useful when iterating
    # on Stage 2 / stats reporting after a prior full Stage 1 run):
    python tools/backfill_first_pitch_strike.py --skip-stage1 --file ...
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _lazy_imports():
    global fetch_fps, LEAGUE_FPS_1ST_AVG, N_PRIOR_FPS, _FPS_STRIKE_DESCS, _FPS_SEASON_FLOOR
    global _CACHE_ROOT
    from backtest import (
        fetch_pitcher_first_pitch_strike_pct as fetch_fps,
        LEAGUE_FPS_1ST_AVG, N_PRIOR_FPS, _FPS_STRIKE_DESCS, _FPS_SEASON_FLOOR,
        CACHE_ROOT as _CACHE_ROOT,
    )
    return (fetch_fps, LEAGUE_FPS_1ST_AVG, N_PRIOR_FPS,
            _FPS_STRIKE_DESCS, _FPS_SEASON_FLOOR, _CACHE_ROOT)


NEW_COLS = [
    "away_first_pitch_strike_pct",
    "home_first_pitch_strike_pct",
]

# Cache freshness rules per Phase 2.2 design review (2026-05-14).
# Closed-season Statcast data is effectively immutable; 90 days is a
# generous catchall for the rare retroactive corrections without
# thrashing the cache on daily cron runs.  Current-season caches need
# legitimate refresh as new starts are played.
_CLOSED_SEASON_TTL_SEC  = 90 * 24 * 3600   # 90 days
_CURRENT_SEASON_TTL_SEC = 12 * 3600         # 12 hours


def _perpitch_cache_is_fresh(path: Path, season: int) -> bool:
    """True if the per-pitch cache file is fresh enough to skip refetch.
    Closed seasons (season < current calendar year) use 90-day TTL.
    Current season uses 12-hour TTL so daily cron picks up new starts."""
    if not path.exists():
        return False
    current_year = datetime.now(timezone.utc).year
    ttl = _CLOSED_SEASON_TTL_SEC if season < current_year else _CURRENT_SEASON_TTL_SEC
    try:
        age_sec = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        return age_sec < ttl
    except OSError:
        return False


def _row_pid(r: dict, side: str) -> int | None:
    raw = (r.get(f"{side}_pitcher_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _stratified_indices(n: int, target: int) -> list[int]:
    if target >= n: return list(range(n))
    if target <= 0: return []
    step = n / target
    return [int(i * step) for i in range(target)]


def _fetch_per_pitch_for_pid_season(pid: int, season: int,
                                     cache_dir: Path,
                                     refresh: bool = False) -> dict | None:
    """Stage 1: pull a pitcher's per-pitch data for one season via
    pybaseball, filter to inning=1 + balls=0 + strikes=0, persist as
    a JSON cache file.  Returns the cached payload (or None on hard
    failure)."""
    cache_path = cache_dir / f"perpitch_{pid}_{season}.json"
    if _perpitch_cache_is_fresh(cache_path, season) and not refresh:
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    try:
        import pybaseball as pb
    except ImportError:
        print("ERROR: pip install pybaseball", file=sys.stderr)
        return None

    # Use the standard MLB season window.  For 2026 in-progress, the API
    # naturally returns up-to-today's data with no special handling.
    start = f"{season}-04-01"
    end   = f"{season}-09-30"
    try:
        df = pb.statcast_pitcher(start_dt=start, end_dt=end, player_id=pid)
    except Exception as exc:
        print(f"    [pid={pid} season={season}] pybaseball error: {exc!r}",
              file=sys.stderr)
        return None

    if df is None or len(df) == 0:
        # No starts for this pitcher in this season; cache empty payload
        # so we don't retry on every backfill invocation.
        payload = {"pid": pid, "season": season, "events": []}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return payload

    # Filter to first pitch of each first-inning plate appearance.
    fp = df[(df["balls"] == 0) & (df["strikes"] == 0) & (df["inning"] == 1)]
    events = []
    for _, row in fp.iterrows():
        d = row.get("game_date")
        desc = row.get("description") or ""
        events.append({
            "game_date": str(d)[:10] if d is not None else "",
            "is_strike": bool(desc in _FPS_STRIKE_DESCS),
        })
    payload = {"pid": pid, "season": season, "events": events}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--file", action="append", default=[],
                   help="CSV(s) to backfill.  Default: picks_2026.csv.")
    p.add_argument("--since", help="Date filter: rows with date >= this.")
    p.add_argument("--until", help="Date filter: rows with date <= this.")
    p.add_argument("--max-rows", type=int, default=0,
                   help="Stop after N rows total (smoke-test).  0 = unlimited.")
    p.add_argument("--stratified-target", type=int, default=0,
                   help="Sample N rows evenly across combined inputs.  Implies --dry-run.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute features without writing CSVs.")
    p.add_argument("--stats", action="store_true",
                   help="Emit the 5-point validation report.")
    p.add_argument("--refresh-cache", action="store_true",
                   help="Force-refetch per-pitch cache files (bypass TTL).")
    p.add_argument("--rate-limit-ms", type=int, default=0,
                   help="Sleep between rows during Stage 2.")
    p.add_argument("--skip-stage1", action="store_true",
                   help="Skip pybaseball fetches; use existing cache as-is.  "
                        "Useful for stratified-sample dry-runs when the cache "
                        "is already warm from a prior full backfill.")
    args = p.parse_args()

    if args.stratified_target:
        args.dry_run = True

    (fetch_fps, league_fps_avg, n_prior_fps,
     _strike_descs, season_floor, cache_root) = _lazy_imports()

    cache_fps_dir = cache_root / "pitcher_fps_perpitch"

    files = args.file or [str(ROOT / "data" / "picks_2026.csv")]
    files = [Path(f) for f in files]
    for fp in files:
        if not fp.exists():
            print(f"ERROR: file not found: {fp}", file=sys.stderr)
            return 2

    print(f"backfill_first_pitch_strike  files={[f.name for f in files]}  "
          f"dry_run={args.dry_run}  stats={args.stats}  "
          f"stratified_target={args.stratified_target}  "
          f"refresh_cache={args.refresh_cache}  skip_stage1={args.skip_stage1}")
    print()

    # ---- Load all rows ----
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

    # Date filter
    def keep(r: dict) -> bool:
        d = (r.get("date") or "")[:10]
        if not d: return False
        if args.since and d < args.since: return False
        if args.until and d > args.until: return False
        return True
    combined = [t for t in combined if keep(t[2])]
    print(f"  after date filter: {len(combined)} rows")

    if args.stratified_target:
        idx = _stratified_indices(len(combined), args.stratified_target)
        combined = [combined[i] for i in idx]
        print(f"  after stratify (target={args.stratified_target}): {len(combined)} rows")

    # If --max-rows is set, limit the WORKING SET so Stage 1 only fetches
    # the (pid, season) tuples needed by the smoke-test rows.  Without
    # this, --max-rows 20 would still trigger Stage 1 for the full corpus.
    if args.max_rows:
        combined = combined[:args.max_rows]
        print(f"  after --max-rows={args.max_rows}: {len(combined)} rows")

    # ---- Stage 1: compute the union of (pid, year) we need ----
    needed: set[tuple[int, int]] = set()
    for fp, idx, r in combined:
        date = (r.get("date") or "")[:10]
        if not date: continue
        target_season = int(date[:4])
        for side in ("away", "home"):
            pid = _row_pid(r, side)
            if pid is None: continue
            for season in range(season_floor, target_season + 1):
                needed.add((pid, season))

    print(f"\nStage 1: per-pitcher per-season cache")
    print(f"  unique (pid, season) tuples needed: {len(needed)}")
    if args.skip_stage1:
        print(f"  --skip-stage1: skipping pybaseball fetches; using existing cache")
    else:
        t0 = time.time()
        fetched = cached_hit = empty = 0
        for i, (pid, season) in enumerate(sorted(needed), 1):
            path = cache_fps_dir / f"perpitch_{pid}_{season}.json"
            if _perpitch_cache_is_fresh(path, season) and not args.refresh_cache:
                cached_hit += 1
                continue
            payload = _fetch_per_pitch_for_pid_season(
                pid, season, cache_fps_dir, refresh=args.refresh_cache)
            if payload is None:
                continue
            fetched += 1
            if not payload.get("events"):
                empty += 1
            if i % 25 == 0:
                print(f"  ...{i}/{len(needed)} ({time.time()-t0:.1f}s, "
                      f"fetched={fetched}, cached={cached_hit}, empty={empty})")
        print(f"  Stage 1 done: fetched={fetched}, cached_hits={cached_hit}, "
              f"empty_seasons={empty}, elapsed={time.time()-t0:.1f}s")

    # ---- Stage 2: per-row CSV writeback ----
    print(f"\nStage 2: per-row fetch + CSV column write")
    processed = skipped_no_pid = 0
    quality_counts = {"live": 0, "ltd": 0, "sm": 0, "avg": 0}
    all_fps: list[tuple[str, str, str, str, float, str]] = []
    fallback_hits = 0
    clamp_lo = clamp_hi = 0

    EPS = 1e-9
    t0 = time.time()

    for fp, idx, r in combined:
        d = (r.get("date") or "")[:10]
        if not d:
            skipped_no_pid += 1; continue
        away_pid = _row_pid(r, "away")
        home_pid = _row_pid(r, "home")
        if not away_pid and not home_pid:
            skipped_no_pid += 1; continue

        for side, pid in (("away", away_pid), ("home", home_pid)):
            if not pid: continue
            fps, q = fetch_fps(pid, d)
            quality_counts[q] = quality_counts.get(q, 0) + 1
            r[f"{side}_first_pitch_strike_pct"] = f"{fps:.4f}"
            if q == "avg" and abs(fps - league_fps_avg) < EPS:
                fallback_hits += 1
            if abs(fps - 0.0) < EPS: clamp_lo += 1
            if abs(fps - 1.0) < EPS: clamp_hi += 1
            all_fps.append((d, fp.name, side, str(pid), fps, q))

        processed += 1
        if processed % 100 == 0:
            elapsed = time.time() - t0
            print(f"  ...{processed} rows in {elapsed:.1f}s ({processed/elapsed:.1f}/s)")
        if args.rate_limit_ms:
            time.sleep(args.rate_limit_ms / 1000.0)

    elapsed = time.time() - t0
    print()
    print(f"Summary:")
    print(f"  rows processed     : {processed}")
    print(f"  skipped (no PID)   : {skipped_no_pid}")
    print(f"  pitcher-feature outputs (total): {sum(quality_counts.values())}")
    print(f"  quality counts: {quality_counts}")
    print(f"  Stage 2 elapsed    : {elapsed:.1f}s")

    if args.stats:
        total = sum(quality_counts.values())
        print()
        print("=" * 60)
        print("Step-5.5 5-point validation report")
        print("=" * 60)
        print("\n[1] Quality-tag distribution:")
        for q in ("live", "ltd", "sm", "avg"):
            n = quality_counts.get(q, 0)
            pct = n / total * 100 if total else 0
            print(f"      {q:>4}: {n:>4}  ({pct:>5.1f}%)")
        fps_vals = [t[4] for t in all_fps]
        if fps_vals:
            print(f"\n[2] Computed FPS distribution (n={len(fps_vals)}):")
            print(f"      mean   = {statistics.mean(fps_vals):.4f}")
            print(f"      stddev = {statistics.stdev(fps_vals) if len(fps_vals)>1 else 0:.4f}")
            print(f"      min/max = {min(fps_vals):.4f} / {max(fps_vals):.4f}")
        print(f"\n[3] Clamp hits at outer bounds (0.0 / 1.0):  lo={clamp_lo}  hi={clamp_hi}")
        print(f"\n[4] Rows returning exactly the prior (all-fallback path):")
        print(f"      {fallback_hits} / {total}  ({fallback_hits/total*100 if total else 0:.1f}%)")
        if all_fps:
            sorted_v = sorted(all_fps, key=lambda x: x[4])
            print("\n[5a] Lowest 5 FPS values:")
            for d, fn, side, pid, fps, q in sorted_v[:5]:
                print(f"      {d}  {fn:<48} {side}_pid={pid:<8} fps={fps:.4f}  q={q}")
            print("\n[5b] Highest 5 FPS values:")
            for d, fn, side, pid, fps, q in sorted_v[-5:]:
                print(f"      {d}  {fn:<48} {side}_pid={pid:<8} fps={fps:.4f}  q={q}")

    if args.dry_run:
        print("\n[dry-run] not writing CSVs.")
        return 0
    if processed == 0:
        print("\nno rows processed; not rewriting CSVs.")
        return 0

    import os
    for fp, (header_in, rows) in file_rows.items():
        new_header = list(header_in)
        for col in NEW_COLS:
            if col not in new_header:
                new_header.append(col)
        added = [c for c in NEW_COLS if c not in header_in]
        if added:
            print(f"  {fp.name}: appending header columns {added}")
        tmp = fp.with_suffix(fp.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=new_header, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        os.replace(tmp, fp)
        print(f"  wrote {fp.name}  ({len(new_header)} columns)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""tools/backfill_lineup_json.py

Phase 2.3 backfill: compute home_lineup_json / away_lineup_json columns
for backtest CSVs (and optionally fill the 405 missing-lineup rows in
picks_2026.csv).  Mirrors the production schema BYTE-FOR-BYTE so the
downstream feature extractor in two_stage_model.gather() can read
either source indistinguishably.

Why this exists:

  Phase 2.3 splits the existing aggregate `top3c_obp` feature into
  `leadoff_obp` + `2_3_combined_obp`.  The per-batter data needed for
  the split is in the `*_lineup_json` columns that production writes
  to picks_2026.csv -- but the 2024 / 2025 backtest CSVs were built
  before that column existed, so they have ONLY the aggregate.  This
  script back-populates the per-batter JSON so V2.3 training has
  parallel sources for the split features across all three corpora.

Schema compatibility (load-bearing):

  Production writes lineup_json via:
      tracker.log_picks  ->  json.dumps(home_lineup)
      where home_lineup is built by
      backtest.current_season_top3_per_batter(player_ids, target_iso, season)

  This script calls the EXACT same producer function with the EXACT
  same arguments and uses bare json.dumps() with no kwargs.  Verified
  byte-identical output in the Phase 2.3 design-review side-by-side:
    PRODUCTION (one cell, 2026-04-30 ARI@MIL home):
      '[{"id": 669003, "name": "Garrett Mitchell", "bats": "L", ...}]'
    BACKFILL (same lineup constructed via per_batter + json.dumps):
      '[{"id": 669003, "name": "Garrett Mitchell", "bats": "L", ...}]'
  Spaces after commas/colons, ASCII-escaped Unicode (Jos\\u00e9),
  null for None, no trailing zeros on exact floats -- all default
  json.dumps behavior.

Leakage guarantee (verified Phase 2.3 design review 2026-05-14):

  current_season_top3_per_batter dispatches to
  current_season_to_date_batter which applies the strict-less-than
  date cutoff at backtest.py:1501:
      relevant = [g for g in log if g["date"] < target_date_iso]
  A batter's OBP/SLG/ISO/AB for a game on date d are computed ONLY
  from games strictly before d.  Same-day games (e.g. doubleheader
  G1 -> G2) are excluded.

Lineup composition asymmetry (audit row 2026-05-14-finding-
  phase23-lineup-composition-asymmetry): this backfill prefers
  post-game boxscore.battingOrder (actual lineup) while production
  at predict-time gets schedule.lineups (announced lineup).  ~5% of
  slots differ; NOT a stat-level leak; accepted deliberately for
  train/serve consistency.

Two-mode operation:

  Default mode (backtest fill): every input-CSV row gets its
  home_lineup_json + away_lineup_json column populated.  Used for
  2024 and 2025 backtests where the column is new.

  --fill-missing-picks-2026: ONLY fills rows where both lineup_json
  cells are empty or '[]'.  Used for picks_2026.csv where 156 of
  561 rows already have production-written values (lineup posted at
  predict-time) and 405 don't (lineup hadn't posted yet but is
  known retroactively).  Existing values stay byte-identical.

Architecture:

  Unlike Phase 2.1 (FIE) and Phase 2.2 (FPS), there's no separate
  Stage 1 pre-fetch.  The data path here (fetch_top3_batters for
  lineup discovery + current_season_to_date_batter per batter)
  already has caching in backtest._cache_get/_cache_put.  An
  explicit Stage 1 would just be "discover all PIDs via
  fetch_top3_batters, warm the cache" -- but discover requires the
  same fetch the per-row pass would make, so it's no savings.
  Single-pass.

Usage:
    # Smoke test on backtest 2024 (20 rows, no writes):
    python tools/backfill_lineup_json.py --max-rows 20 --dry-run --stats \\
        --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv

    # Stratified 100-row dry-run across both backtest years:
    python tools/backfill_lineup_json.py --dry-run --stats \\
        --stratified-target 100 \\
        --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv \\
        --file data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv

    # Full 2024 backfill (writes back to CSV):
    python tools/backfill_lineup_json.py \\
        --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv

    # Full 2025 backfill:
    python tools/backfill_lineup_json.py \\
        --file data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv

    # Deliberate separate step: fill the 405 missing picks_2026 rows:
    python tools/backfill_lineup_json.py --fill-missing-picks-2026 \\
        --file data/picks_2026.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _lazy_imports():
    """Deferred import so --help works without pulling the full
    backtest stack (statsapi, pandas, etc.)."""
    global fetch_top3_batters, current_season_top3_per_batter
    from backtest import (
        fetch_top3_batters,
        current_season_top3_per_batter,
    )
    return fetch_top3_batters, current_season_top3_per_batter


NEW_COLS = ["away_lineup_json", "home_lineup_json"]


def _stratified_indices(n: int, target: int) -> list[int]:
    if target >= n: return list(range(n))
    if target <= 0: return []
    step = n / target
    return [int(i * step) for i in range(target)]


def _row_gamepk(r: dict) -> int | None:
    raw = (r.get("game_pk") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _row_needs_fill(r: dict, fill_missing_mode: bool) -> bool:
    """In default mode every row is filled (backtest CSVs have no
    existing lineup_json column).  In --fill-missing-picks-2026 mode,
    only rows where BOTH lineup_json cells are empty or '[]' are
    filled; populated cells stay untouched (byte-identical)."""
    if not fill_missing_mode:
        return True
    h = (r.get("home_lineup_json") or "").strip()
    a = (r.get("away_lineup_json") or "").strip()
    h_empty = h in ("", "[]")
    a_empty = a in ("", "[]")
    return h_empty and a_empty


def _quality_tag(per_batter_list: list[dict],
                 thresh_live: int = 100,
                 thresh_ltd:  int = 40,
                 thresh_sm:   int = 10) -> str:
    """Combined-AB quality tag parallel to FIE/FPS quality-tag pattern.
    Thresholds are guesses; the stratified validation report uses these
    descriptively (no pass/fail gate) per Phase 2.3 design review.

      live: combined AB >= 100  (3 batters * ~3 weeks of regular play)
      ltd:  40 <= combined AB < 100
      sm:   10 <= combined AB < 40
      avg:  combined AB < 10 (per-batter OBP would be noise; feature
            extractor falls back to LEAGUE_AVG_OBP at training time)
    """
    total_ab = 0
    for b in per_batter_list:
        ab = b.get("ab")
        if ab is not None:
            try:
                total_ab += int(ab)
            except (TypeError, ValueError):
                pass
    if total_ab >= thresh_live: return "live"
    if total_ab >= thresh_ltd:  return "ltd"
    if total_ab >= thresh_sm:   return "sm"
    return "avg"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--file", action="append", default=[],
                   help="CSV(s) to backfill.  Default: backtest 2024 + 2025 "
                        "(NOT picks_2026 -- use --fill-missing-picks-2026 for that).")
    p.add_argument("--since", help="Date filter: rows with date >= this.")
    p.add_argument("--until", help="Date filter: rows with date <= this.")
    p.add_argument("--max-rows", type=int, default=0,
                   help="Stop after N rows total (smoke-test).  0 = unlimited.")
    p.add_argument("--stratified-target", type=int, default=0,
                   help="Sample N rows evenly across combined inputs.  "
                        "Implies --dry-run.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute lineup_json without writing CSVs.")
    p.add_argument("--stats", action="store_true",
                   help="Emit the 5-point validation report.")
    p.add_argument("--rate-limit-ms", type=int, default=0,
                   help="Sleep between rows during the per-row pass "
                        "(API politeness on bulk runs).")
    p.add_argument("--fill-missing-picks-2026", action="store_true",
                   help="Mode switch: only fill rows where BOTH lineup_json "
                        "cells are empty or '[]'.  Populated rows stay "
                        "byte-identical.  Use ONLY with picks_2026.csv.")
    args = p.parse_args()

    if args.stratified_target:
        args.dry_run = True

    fetch_top3, per_batter = _lazy_imports()

    if not args.file:
        args.file = [
            str(ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv"),
            str(ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"),
        ]
    files = [Path(f) for f in args.file]
    for fp in files:
        if not fp.exists():
            print(f"ERROR: file not found: {fp}", file=sys.stderr)
            return 2

    # Guardrail: --fill-missing-picks-2026 should only be used with
    # picks_2026.  Caught here so an accidental pairing doesn't silently
    # skip every row in a backtest CSV (none of them have lineup_json
    # populated -- every row would match the "missing" filter and proceed
    # identically to default, masking the operator's intent).
    if args.fill_missing_picks_2026:
        for fp in files:
            if "picks_2026" not in fp.name:
                print(f"ERROR: --fill-missing-picks-2026 used with non-picks file: {fp.name}",
                      file=sys.stderr)
                print("       Drop the flag for backtest CSVs (they need full fill).",
                      file=sys.stderr)
                return 2

    print(f"backfill_lineup_json  files={[f.name for f in files]}  "
          f"dry_run={args.dry_run}  stats={args.stats}  "
          f"stratified_target={args.stratified_target}  "
          f"fill_missing_picks_2026={args.fill_missing_picks_2026}")
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

    # Mode filter (fill-missing-picks-2026 reduces to rows where both
    # lineup_json cells are empty)
    if args.fill_missing_picks_2026:
        before = len(combined)
        combined = [t for t in combined if _row_needs_fill(t[2], True)]
        print(f"  after fill-missing filter: {len(combined)} rows  "
              f"(skipped {before - len(combined)} populated rows)")

    if args.stratified_target:
        idx = _stratified_indices(len(combined), args.stratified_target)
        combined = [combined[i] for i in idx]
        print(f"  after stratify (target={args.stratified_target}): "
              f"{len(combined)} rows")

    if args.max_rows:
        combined = combined[:args.max_rows]
        print(f"  after --max-rows={args.max_rows}: {len(combined)} rows")

    # ---- Per-row fetch + assemble + write into row dict ----
    print(f"\nPer-row pass: fetch_top3_batters + current_season_top3_per_batter")
    processed = skipped_no_pk = empty_lineup = 0
    quality_counts = {"live": 0, "ltd": 0, "sm": 0, "avg": 0}
    all_samples: list[tuple[str, str, str, int, int, str]] = []
    # (date, file_name, side, n_batters, total_ab, quality)
    sample_cells: list[tuple[str, str, str, str]] = []
    # (date, file_name, side, raw_json_string) -- captured for byte-spot-check
    t0 = time.time()

    for fp, idx, r in combined:
        d = (r.get("date") or "")[:10]
        game_pk = _row_gamepk(r)
        if not d or game_pk is None:
            skipped_no_pk += 1
            continue
        season = int(d[:4])
        try:
            top3 = fetch_top3(game_pk)
        except Exception as exc:
            print(f"    [game_pk={game_pk}] fetch_top3_batters error: {exc!r}",
                  file=sys.stderr)
            skipped_no_pk += 1
            continue

        for side, side_top3_key in (("away", "away_top3"), ("home", "home_top3")):
            pids = top3.get(side_top3_key) or []
            if not pids:
                # No lineup available even retroactively (rare; cancelled
                # games whose API endpoints never published a lineup).
                # Write literal '[]' so the column is non-empty and the
                # byte form matches production's "empty lineup" emission.
                empty_lineup += 1
                payload_list: list[dict] = []
            else:
                try:
                    payload_list = per_batter(pids, d, season)
                except Exception as exc:
                    print(f"    [game_pk={game_pk} side={side}] per_batter error: {exc!r}",
                          file=sys.stderr)
                    payload_list = []

            q = _quality_tag(payload_list)
            quality_counts[q] = quality_counts.get(q, 0) + 1
            total_ab = sum(int(b.get("ab") or 0) for b in payload_list)
            all_samples.append((d, fp.name, side, len(payload_list), total_ab, q))

            # Write into row dict via SAME json.dumps the producer uses
            # in production -- bare call, no kwargs.  Byte-compatibility
            # hinges on this line.
            json_str = json.dumps(payload_list)
            r[f"{side}_lineup_json"] = json_str

            # Capture the first few cells for the byte-spot-check report.
            if len(sample_cells) < 5 and payload_list:
                sample_cells.append((d, fp.name, side, json_str))

        processed += 1
        if processed % 100 == 0:
            elapsed = time.time() - t0
            print(f"  ...{processed} rows in {elapsed:.1f}s "
                  f"({processed/elapsed:.1f}/s)")
        if args.rate_limit_ms:
            time.sleep(args.rate_limit_ms / 1000.0)

    elapsed = time.time() - t0
    print()
    print(f"Summary:")
    print(f"  rows processed       : {processed}")
    print(f"  skipped (no game_pk) : {skipped_no_pk}")
    print(f"  side-lineups emitted : {sum(quality_counts.values())}")
    print(f"  empty-lineup writes  : {empty_lineup}  (literal '[]' cell)")
    print(f"  elapsed              : {elapsed:.1f}s")

    if args.stats:
        total = sum(quality_counts.values())
        print()
        print("=" * 60)
        print("Step-5.5 5-point validation report")
        print("=" * 60)
        print("\n[1] Quality-tag distribution (descriptive only, no pass/fail):")
        for q in ("live", "ltd", "sm", "avg"):
            n = quality_counts.get(q, 0)
            pct = n / total * 100 if total else 0
            print(f"      {q:>4}: {n:>4}  ({pct:>5.1f}%)")
        ab_vals = [s[4] for s in all_samples if s[3] > 0]
        if ab_vals:
            print(f"\n[2] Combined-AB distribution (n={len(ab_vals)} non-empty side-lineups):")
            print(f"      mean   = {statistics.mean(ab_vals):.1f}")
            print(f"      stddev = {statistics.stdev(ab_vals) if len(ab_vals)>1 else 0:.1f}")
            print(f"      min/max = {min(ab_vals)} / {max(ab_vals)}")
        n_zero = sum(1 for s in all_samples if s[3] == 0)
        print(f"\n[3] Side-lineups returning empty list (n_batters=0): {n_zero}")
        partial = sum(1 for s in all_samples if 0 < s[3] < 3)
        print(f"\n[4] Side-lineups with fewer than 3 batters: {partial}")
        if all_samples:
            sorted_v = sorted([s for s in all_samples if s[3] > 0], key=lambda x: x[4])
            print("\n[5a] Lowest 5 combined-AB lineups:")
            for d, fn, side, nb, ab, q in sorted_v[:5]:
                print(f"      {d}  {fn:<48} {side} n_batters={nb} total_ab={ab}  q={q}")
            print("\n[5b] Highest 5 combined-AB lineups:")
            for d, fn, side, nb, ab, q in sorted_v[-5:]:
                print(f"      {d}  {fn:<48} {side} n_batters={nb} total_ab={ab}  q={q}")

        # Byte-spot-check: print raw JSON for the first N captured cells
        # so the operator can verify production-schema compatibility by
        # eye (same key order, same separators, same Unicode escaping,
        # null for missing fields).
        if sample_cells:
            print("\n[6] Byte-spot-check (raw json.dumps output for "
                  f"first {len(sample_cells)} cells):")
            for d, fn, side, js in sample_cells:
                print(f"\n  {d}  {fn}  {side}_lineup_json:")
                print(f"    {js}")

    if args.dry_run:
        print("\n[dry-run] not writing CSVs.")
        return 0
    if processed == 0:
        print("\nno rows processed; not rewriting CSVs.")
        return 0

    # ---- Atomic per-CSV writeback ----
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

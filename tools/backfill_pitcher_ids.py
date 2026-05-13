#!/usr/bin/env python3
"""tools/backfill_pitcher_ids.py

One-shot schema upgrade: add `away_pitcher_id` / `home_pitcher_id`
columns to backtest CSVs that only store pitcher names.  Resolves
PIDs via MLB Stats API schedule (game_pk -> probable pitcher) and
verifies by name match.  Mismatches go to a diagnostics CSV; affected
rows are left without an ID so downstream features (Phase 2.1
`home_first_inning_era`, future Phase 2.2 / 2.5 / 2.6) fall back to
league-average behavior cleanly rather than ingesting wrong data.

Why this is a separate script:
  - The PID resolution is reusable across every Phase 2 feature that
    needs pitcher-level lookups (Phase 2.2 first-pitch strike, 2.5
    velocity trend, 2.6 stolen-base rate -- all need PIDs).  Doing
    the resolution once and persisting it as a schema upgrade beats
    re-resolving on every feature backfill.
  - Resolved PIDs are auditable in the CSV.  If something goes wrong
    we can spot-check that game_pk X correctly resolved to pitcher Y.
  - Permanent schema upgrade, not ephemeral memory state.

Method:
  1. Read input CSVs into memory.
  2. Collect the unique set of (date) values across all rows.
  3. For each unique date, call backtest.fetch_schedule_iso(date) once
     (cached).  Build a global  game_pk -> {away_pid, away_name,
     home_pid, home_name}  map.
  4. For each row in each input CSV:
       a. Look up the row's game_pk in the map.
       b. Normalize both API name and CSV name (lowercase, strip
          unicode accents, collapse whitespace).
       c. If they match -> write the IDs into the row.
       d. If they don't match (e.g. probable-pitcher starter swap,
          doubleheader g2 with different starter) -> emit a row to
          data/diagnostics/pid_resolution_mismatches.csv, leave the
          row's *_pitcher_id blank.
       e. If game_pk isn't in the map -> skip (warn).
  5. Write each input CSV back via atomic tmp+replace.

Atomic write follows tools/backfill_top3_last10.py pattern.

Usage:
  # Dry-run on 100 stratified rows across 2024 + 2025 backtests:
  python tools/backfill_pitcher_ids.py --dry-run --stratified-target 100 \
      --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv \
      --file data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv

  # Full backfill -- 2024 only, then 2025 separately:
  python tools/backfill_pitcher_ids.py \
      --file data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv
  python tools/backfill_pitcher_ids.py \
      --file data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv

  # Sample of first N resolved rows in the report:
  python tools/backfill_pitcher_ids.py --dry-run --sample 10 --file ...
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _lazy_imports():
    global fetch_schedule_iso
    from backtest import fetch_schedule_iso
    return fetch_schedule_iso


NEW_COLS = ["away_pitcher_id", "home_pitcher_id"]

MISMATCH_CSV = ROOT / "data" / "diagnostics" / "pid_resolution_mismatches.csv"
MISMATCH_COLS = [
    "date", "game_pk", "source_file",
    "side", "csv_name", "api_name", "api_pitcher_id",
    "reason",
]


def _normalize_name(s: str) -> str:
    """Lowercase, strip accents and punctuation noise, collapse whitespace.
    Used only for SAFETY comparison; never written back to a CSV."""
    if not s:
        return ""
    # Decompose unicode (è -> e + combining accent), then drop combining marks
    nfkd = unicodedata.normalize("NFKD", s)
    flat = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase + collapse whitespace; tolerate Jr./Sr./III dropping
    flat = flat.lower().strip()
    flat = " ".join(flat.split())
    # Strip trailing ", jr." / ", sr." / " jr" / " sr" / " ii"/"iii" suffixes
    for suf in (", jr.", ", sr.", ", jr", ", sr",
                " jr.", " sr.", " jr", " sr",
                " iii", " ii"):
        if flat.endswith(suf):
            flat = flat[: -len(suf)].strip()
    return flat


def _stratified_indices(n: int, target: int) -> list[int]:
    if target >= n: return list(range(n))
    if target <= 0: return []
    step = n / target
    return [int(i * step) for i in range(target)]


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--file", action="append", default=[], required=False,
                   help="Input CSV to backfill PIDs into.  Repeatable.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything but do not write CSVs back.")
    p.add_argument("--stratified-target", type=int, default=0,
                   help="If >0: sample this many rows total across the input "
                        "files (evenly spaced) instead of all rows.  Implies --dry-run.")
    p.add_argument("--sample", type=int, default=0,
                   help="Print the first N successfully resolved rows in the report.")
    p.add_argument("--rate-limit-ms", type=int, default=120,
                   help="Sleep this many ms between schedule API calls.")
    args = p.parse_args()
    if args.stratified_target:
        args.dry_run = True
    if not args.file:
        print("ERROR: --file required (at least one)", file=sys.stderr)
        return 2

    fetch_schedule_iso = _lazy_imports()

    files = [Path(f) for f in args.file]
    for fp in files:
        if not fp.exists():
            print(f"ERROR: file not found: {fp}", file=sys.stderr)
            return 2

    print(f"backfill_pitcher_ids  files={[f.name for f in files]}  "
          f"dry_run={args.dry_run}  stratified_target={args.stratified_target}")
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

    # Stratify if requested
    if args.stratified_target:
        idx = _stratified_indices(len(combined), args.stratified_target)
        combined = [combined[i] for i in idx]
        print(f"  stratified to {len(combined)} rows")

    # ---- Discover unique dates ----
    unique_dates = sorted({((r.get("date") or "")[:10]) for _, _, r in combined if (r.get("date") or "")[:10]})
    print(f"  unique dates to fetch: {len(unique_dates)}")
    print()

    # ---- Pre-warm the schedule cache: one fetch per unique date ----
    print(f"Fetching schedules (rate_limit_ms={args.rate_limit_ms})...")
    gp_map: dict[int, dict] = {}    # game_pk -> {away_pid, away_name, home_pid, home_name}
    t0 = time.time()
    for i, d in enumerate(unique_dates, 1):
        try:
            games = fetch_schedule_iso(d)
        except Exception as exc:
            print(f"  [date {d}] ERROR: {exc}")
            continue
        for g in games:
            gp = g.get("game_pk")
            if gp is None:
                continue
            gp_map[int(gp)] = {
                "away_pid":  g.get("away_pitcher_id"),
                "away_name": g.get("away_pitcher_name") or "",
                "home_pid":  g.get("home_pitcher_id"),
                "home_name": g.get("home_pitcher_name") or "",
            }
        if i % 25 == 0:
            print(f"  ...{i}/{len(unique_dates)} dates ({time.time()-t0:.1f}s)")
        if args.rate_limit_ms:
            time.sleep(args.rate_limit_ms / 1000.0)
    print(f"  schedule fetch complete: {len(gp_map)} games keyed by game_pk "
          f"in {time.time()-t0:.1f}s")
    print()

    # ---- Resolve each row ----
    resolved_rows = 0
    resolved_pids = 0
    no_gp_match   = 0
    skipped_no_pid_in_api = 0
    mismatches: list[dict] = []
    samples: list[str] = []

    for fp, idx, r in combined:
        gp_raw = (r.get("game_pk") or "").strip()
        if not gp_raw:
            no_gp_match += 1
            continue
        try:
            gp = int(gp_raw)
        except ValueError:
            no_gp_match += 1
            continue
        gp_entry = gp_map.get(gp)
        if gp_entry is None:
            no_gp_match += 1
            continue

        row_resolved_any = False
        for side in ("away", "home"):
            csv_name = (r.get(f"{side}_pitcher") or "").strip()
            api_pid  = gp_entry.get(f"{side}_pid")
            api_name = (gp_entry.get(f"{side}_name") or "").strip()
            if api_pid in (None, 0):
                skipped_no_pid_in_api += 1
                mismatches.append({
                    "date":            (r.get("date") or "")[:10],
                    "game_pk":         gp_raw,
                    "source_file":     fp.name,
                    "side":            side,
                    "csv_name":        csv_name,
                    "api_name":        api_name,
                    "api_pitcher_id":  "",
                    "reason":          "API returned no probable pitcher (TBD)",
                })
                continue
            if _normalize_name(csv_name) != _normalize_name(api_name):
                mismatches.append({
                    "date":            (r.get("date") or "")[:10],
                    "game_pk":         gp_raw,
                    "source_file":     fp.name,
                    "side":            side,
                    "csv_name":        csv_name,
                    "api_name":        api_name,
                    "api_pitcher_id":  str(api_pid),
                    "reason":          "name mismatch (likely starter swap / DH g2)",
                })
                continue
            # Match -- write the ID into the row (live or dry-run).
            r[f"{side}_pitcher_id"] = str(api_pid)
            resolved_pids += 1
            row_resolved_any = True
        if row_resolved_any:
            resolved_rows += 1
            if len(samples) < args.sample:
                samples.append(
                    f"  {(r.get('date') or '')[:10]}  gp={gp_raw}  "
                    f"away={(r.get('away_pitcher') or ''):<22} -> {r.get('away_pitcher_id','')}    "
                    f"home={(r.get('home_pitcher') or ''):<22} -> {r.get('home_pitcher_id','')}"
                )

    # ---- Report ----
    total_pitcher_slots = len(combined) * 2  # away + home per row
    print("Resolution report:")
    print(f"  rows considered            : {len(combined)}")
    print(f"  rows with >=1 PID resolved : {resolved_rows}  ({resolved_rows*100/max(len(combined),1):.1f}%)")
    print(f"  total PIDs resolved        : {resolved_pids} / {total_pitcher_slots}  ({resolved_pids*100/max(total_pitcher_slots,1):.1f}%)")
    print(f"  rows with no game_pk match : {no_gp_match}")
    print(f"  slots: API returned no probable: {skipped_no_pid_in_api}")
    print(f"  slots: name mismatches         : {sum(1 for m in mismatches if 'mismatch' in m['reason'])}")
    print(f"  total mismatch rows queued     : {len(mismatches)}")
    print()

    if args.sample and samples:
        print(f"First {len(samples)} successfully resolved rows:")
        for s in samples:
            print(s)
        print()

    # ---- Write the diagnostics CSV (always, dry-run or live) ----
    if mismatches:
        MISMATCH_CSV.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write
        import os, tempfile
        fd, tmp = tempfile.mkstemp(prefix=".pidmiss_", dir=str(MISMATCH_CSV.parent))
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=MISMATCH_COLS)
                w.writeheader()
                for m in mismatches:
                    w.writerow(m)
            os.replace(tmp, MISMATCH_CSV)
        except Exception:
            try: os.unlink(tmp)
            except OSError: pass
            raise
        print(f"  mismatch diagnostics -> {MISMATCH_CSV}  ({len(mismatches)} rows)")

    if args.dry_run:
        print("\n[dry-run] not writing input CSVs back.")
        return 0
    if resolved_rows == 0:
        print("\nno rows resolved; not rewriting input CSVs.")
        return 0

    # ---- Write each input CSV back, header extended ----
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

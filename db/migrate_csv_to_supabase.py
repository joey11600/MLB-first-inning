#!/usr/bin/env python3
"""
migrate_csv_to_supabase.py — One-off CSV → Supabase Postgres migration.

Reads the existing CSVs (picks_2026.csv, pick_changes.csv, system_errors.csv)
and bulk-upserts them into the corresponding Supabase tables.  Idempotent:
re-runs are safe (uses ON CONFLICT for picks_2026, append-only for journals).

This is Phase 1 of the dashboard real-time architecture migration.  After
this runs successfully, the dashboard's data layer can be flipped from
"read CSVs" to "read Supabase" with no behavior change.

Setup (one-time):
  1. Create Supabase project at https://supabase.com (free tier is fine)
  2. Run db/schema.sql in the Supabase SQL editor
  3. Get the project URL + service_role key from Settings → API
  4. Set env vars locally:
       SUPABASE_URL=https://<project>.supabase.co
       SUPABASE_SERVICE_KEY=<service-role key>
  5. pip install supabase python-dotenv
  6. python db/migrate_csv_to_supabase.py

Usage:
  python db/migrate_csv_to_supabase.py                 # migrate all 3 CSVs
  python db/migrate_csv_to_supabase.py --picks-only    # just picks_2026
  python db/migrate_csv_to_supabase.py --dry-run       # parse + report, no writes
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# We use the supabase-py client.  Service-role key is required because we're
# doing privileged INSERTs/UPSERTs that bypass RLS.  Don't ever check the
# service_role key into git or expose it client-side.
try:
    from supabase import create_client, Client
except ImportError:
    sys.exit("Missing dependency.  Run: pip install supabase python-dotenv")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # optional; env vars can be set directly


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR  = REPO_ROOT / "data"

# --- helpers --------------------------------------------------------------

def _to_float(s: str) -> float | None:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s: str) -> int | None:
    s = (s or "").strip()
    if s == "":
        return None
    try:
        # Some CSV ints are stored as floats ("1.0"); cast safely
        return int(float(s))
    except ValueError:
        return None


def _to_jsonb(s: str) -> list:
    """JSONB columns are stored as `[]` strings in CSV.  Parse to actual list
    so the supabase client serializes them as proper JSON, not a string."""
    s = (s or "").strip()
    if not s or s == "[]":
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return v
        return []
    except (json.JSONDecodeError, ValueError):
        return []


def _to_iso_date(s: str) -> str | None:
    """CSV date is YYYY-MM-DD; pass through if valid, else None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        return None


def _to_iso_ts(s: str) -> str | None:
    """CSV timestamps are ISO 8601 with optional Z suffix.  Postgres handles
    both, just pass through after trimming."""
    s = (s or "").strip()
    return s if s else None


# --- field mappers --------------------------------------------------------

# Map from CSV column name -> (target column name, converter function)
PICKS_FIELD_MAP = {
    # Identity
    "date":          ("date",          _to_iso_date),
    "season":        ("season",        _to_int),
    "game_pk":       ("game_pk",       lambda s: (s or "").strip() or None),
    "game_number":   ("game_number",   _to_int),
    "double_header": ("double_header", lambda s: (s or "N").strip() or "N"),
    "away_team":     ("away_team",     lambda s: (s or "").strip().upper()),
    "home_team":     ("home_team",     lambda s: (s or "").strip().upper()),
    "game_time_et":  ("game_time_et",  lambda s: (s or "").strip() or None),

    # Pitchers
    "away_pitcher":      ("away_pitcher",      lambda s: (s or "").strip() or None),
    "home_pitcher":      ("home_pitcher",      lambda s: (s or "").strip() or None),
    "away_pitcher_id":   ("away_pitcher_id",   _to_int),
    "home_pitcher_id":   ("home_pitcher_id",   _to_int),
    "away_pitcher_q":    ("away_pitcher_q",    lambda s: (s or "").strip() or None),
    "home_pitcher_q":    ("home_pitcher_q",    lambda s: (s or "").strip() or None),
    "away_batting_q":    ("away_batting_q",    lambda s: (s or "").strip() or None),
    "home_batting_q":    ("home_batting_q",    lambda s: (s or "").strip() or None),

    # Pitcher stats
    "away_era":  ("away_era",  _to_float),  "home_era":  ("home_era",  _to_float),
    "away_whip": ("away_whip", _to_float),  "home_whip": ("home_whip", _to_float),
    "away_fip":  ("away_fip",  _to_float),  "home_fip":  ("home_fip",  _to_float),
    "away_bb9":  ("away_bb9",  _to_float),  "home_bb9":  ("home_bb9",  _to_float),
    "away_hr9":  ("away_hr9",  _to_float),  "home_hr9":  ("home_hr9",  _to_float),
    "away_k9":   ("away_k9",   _to_float),  "home_k9":   ("home_k9",   _to_float),
    "away_xera": ("away_xera", _to_float),  "home_xera": ("home_xera", _to_float),
    "away_whiff_pct_rank":     ("away_whiff_pct_rank",     _to_float),
    "home_whiff_pct_rank":     ("home_whiff_pct_rank",     _to_float),

    # Recent form
    "home_p_last5_pitcher_nrfi":  ("home_p_last5_pitcher_nrfi",  _to_float),
    "away_p_last5_pitcher_nrfi":  ("away_p_last5_pitcher_nrfi",  _to_float),
    "home_p_last10_pitcher_nrfi": ("home_p_last10_pitcher_nrfi", _to_float),
    "away_p_last10_pitcher_nrfi": ("away_p_last10_pitcher_nrfi", _to_float),

    # Phase F
    "home_pvt_nrfi_rate":    ("home_pvt_nrfi_rate",    _to_float),
    "away_pvt_nrfi_rate":    ("away_pvt_nrfi_rate",    _to_float),
    "home_avg_ip_per_start": ("home_avg_ip_per_start", _to_float),
    "away_avg_ip_per_start": ("away_avg_ip_per_start", _to_float),

    # Batting
    "away_obp": ("away_obp", _to_float), "home_obp": ("home_obp", _to_float),
    "away_slg": ("away_slg", _to_float), "home_slg": ("home_slg", _to_float),
    "away_rpg": ("away_rpg", _to_float), "home_rpg": ("home_rpg", _to_float),

    # Top-3 batter aggregates
    "home_top3c_obp": ("home_top3c_obp", _to_float), "away_top3c_obp": ("away_top3c_obp", _to_float),
    "home_top3c_slg": ("home_top3c_slg", _to_float), "away_top3c_slg": ("away_top3c_slg", _to_float),
    "home_top3c_iso": ("home_top3c_iso", _to_float), "away_top3c_iso": ("away_top3c_iso", _to_float),
    "home_top3c_source": ("home_top3c_source", lambda s: (s or "").strip() or None),
    "away_top3c_source": ("away_top3c_source", lambda s: (s or "").strip() or None),
    "home_lineup_json":  ("home_lineup_json",  _to_jsonb),
    "away_lineup_json":  ("away_lineup_json",  _to_jsonb),

    # Environment
    "park_factor": ("park_factor", _to_float),
    "wx_temp_c":   ("wx_temp_c",   _to_float),
    "wx_wind_kmh": ("wx_wind_kmh", _to_float),
    "wx_humidity": ("wx_humidity", _to_float),
    "wx_is_dome":  ("wx_is_dome",  _to_int),

    # Umpire
    "home_plate_ump_id":        ("home_plate_ump_id",        _to_int),
    "home_plate_ump_nrfi_rate": ("home_plate_ump_nrfi_rate", _to_float),

    # Model output
    "away_proj_runs":  ("away_proj_runs",  _to_float),
    "home_proj_runs":  ("home_proj_runs",  _to_float),
    "combined_lambda": ("combined_lambda", _to_float),
    "lambda_lr_t1":    ("lambda_lr_t1",    _to_float),
    "lambda_lr_b1":    ("lambda_lr_b1",    _to_float),
    "lambda_lr_total": ("lambda_lr_total", _to_float),
    "nrfi_prob":       ("nrfi_prob",       _to_float),
    "yrfi_prob":       ("yrfi_prob",       _to_float),
    "over_1_5_prob":   ("over_1_5_prob",   _to_float),
    "under_1_5_prob":  ("under_1_5_prob",  _to_float),

    # Pick decision
    "pick_side":     ("pick_side",     lambda s: (s or "").strip() or None),
    "pick_strength": ("pick_strength", lambda s: (s or "").strip() or None),
    "pick_label":    ("pick_label",    lambda s: (s or "").strip() or None),
    "blended_inputs":("blended_inputs",_to_int),
    "top_factors_t1_json": ("top_factors_t1_json", _to_jsonb),
    "top_factors_b1_json": ("top_factors_b1_json", _to_jsonb),
    "created_at":    ("created_at",    _to_iso_ts),

    # Grade
    "actual_result": ("actual_result", lambda s: (s or "").strip() or None),
    "graded_result": ("graded_result", lambda s: (s or "").strip() or None),
    "fi_away_runs":  ("fi_away_runs",  _to_int),
    "fi_home_runs":  ("fi_home_runs",  _to_int),
    "fi_total_runs": ("fi_total_runs", _to_int),
    "graded_at":     ("graded_at",     _to_iso_ts),

    # Odds
    "sportsbook":         ("sportsbook",         lambda s: (s or "").strip() or None),
    "market_nrfi_odds":   ("market_nrfi_odds",   lambda s: (s or "").strip() or None),
    "market_yrfi_odds":   ("market_yrfi_odds",   lambda s: (s or "").strip() or None),
    "odds_captured_at":   ("odds_captured_at",   _to_iso_ts),
    "implied_nrfi_prob":  ("implied_nrfi_prob",  _to_float),
    "implied_yrfi_prob":  ("implied_yrfi_prob",  _to_float),
    "edge_nrfi":          ("edge_nrfi",          _to_float),
    "edge_yrfi":          ("edge_yrfi",          _to_float),
    "edge_on_pick":       ("edge_on_pick",       _to_float),

    # Opening line + CLV
    "opened_nrfi_odds":   ("opened_nrfi_odds",   lambda s: (s or "").strip() or None),
    "opened_yrfi_odds":   ("opened_yrfi_odds",   lambda s: (s or "").strip() or None),
    "opened_captured_at": ("opened_captured_at", _to_iso_ts),
    "clv_pct":            ("clv_pct",            _to_float),

    # Bet
    "bet_placed":        ("bet_placed",        lambda s: (s or "").strip() or None),
    "units_risked":      ("units_risked",      _to_float),
    "profit_loss_units": ("profit_loss_units", _to_float),
}


def transform_row(row: dict) -> dict:
    """Map a CSV row to a Supabase row, dropping unknown columns and
    converting types to JSON-serializable values."""
    out: dict = {}
    for csv_col, val in row.items():
        if csv_col in PICKS_FIELD_MAP:
            tgt_col, conv = PICKS_FIELD_MAP[csv_col]
            converted = conv(val)
            # Skip None for nullable columns? Postgres NULL is fine; just pass it.
            out[tgt_col] = converted
    return out


# --- migration steps -------------------------------------------------------

def migrate_picks(client: Client, csv_path: Path, dry_run: bool = False) -> tuple[int, int]:
    """Bulk-upsert picks_<year>.csv into picks_<year> table.  Returns (read, upserted)."""
    if not csv_path.exists():
        print(f"  [skip] {csv_path.name} does not exist")
        return (0, 0)

    table = csv_path.stem  # "picks_2026"
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(transform_row(row))

    print(f"  [{table}] read {len(rows)} rows from CSV")

    if dry_run:
        print(f"  [{table}] DRY RUN — would upsert {len(rows)} rows; first row keys: {list(rows[0].keys())[:8] if rows else []}...")
        return (len(rows), 0)

    # Upsert in batches of 500 to stay under request size limits + give us
    # progress feedback for long-running migrations
    BATCH = 500
    upserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        # ON CONFLICT (date, game_pk) DO UPDATE — handled by `upsert()` which
        # uses primary key automatically
        res = client.table(table).upsert(batch, on_conflict="date,game_pk").execute()
        if res.data is None and getattr(res, "error", None):
            print(f"  [{table}] batch {i//BATCH + 1} ERROR: {res.error}")
            continue
        upserted += len(batch)
        print(f"  [{table}] upserted {upserted}/{len(rows)}")

    return (len(rows), upserted)


def migrate_pick_changes(client: Client, csv_path: Path, dry_run: bool = False) -> tuple[int, int]:
    """Append pick_changes.csv → pick_changes table.  Idempotency by best-effort
    check on (captured_at_utc, date, game_pk) tuple before insert."""
    if not csv_path.exists():
        print(f"  [skip] {csv_path.name} does not exist")
        return (0, 0)

    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "captured_at_utc": _to_iso_ts(row.get("captured_at_utc", "")),
                "date":            _to_iso_date(row.get("date", "")),
                "game_pk":         (row.get("game_pk") or "").strip() or None,
                "away_team":       (row.get("away_team") or "").strip() or None,
                "home_team":       (row.get("home_team") or "").strip() or None,
                "game_time_et":    (row.get("game_time_et") or "").strip() or None,
                "old_pick_label":  (row.get("old_pick_label") or "").strip() or None,
                "new_pick_label":  (row.get("new_pick_label") or "").strip() or None,
            })

    print(f"  [pick_changes] read {len(rows)} rows from CSV")
    if dry_run:
        return (len(rows), 0)

    # Insert; duplicates on the journal are acceptable (we have a serial PK)
    BATCH = 500
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        client.table("pick_changes").insert(batch).execute()
        inserted += len(batch)
        print(f"  [pick_changes] inserted {inserted}/{len(rows)}")
    return (len(rows), inserted)


def migrate_system_errors(client: Client, csv_path: Path, dry_run: bool = False) -> tuple[int, int]:
    if not csv_path.exists():
        print(f"  [skip] {csv_path.name} does not exist")
        return (0, 0)
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "captured_at_utc": _to_iso_ts(row.get("captured_at_utc", "")),
                "date":            _to_iso_date(row.get("date", "")),
                "step":            (row.get("step") or "").strip() or None,
                "exit_code":       _to_int(row.get("exit_code", "")),
                "message":         (row.get("message") or "").strip() or None,
            })
    print(f"  [system_errors] read {len(rows)} rows from CSV")
    if dry_run:
        return (len(rows), 0)
    if rows:
        client.table("system_errors").insert(rows).execute()
    return (len(rows), len(rows))


# --- main ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--picks-only", action="store_true",
                        help="Migrate only picks_<year>.csv (skip pick_changes + system_errors)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read + transform but don't write to Supabase")
    parser.add_argument("--season", type=int, default=2026,
                        help="Which year's picks CSV (default: 2026)")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not args.dry_run and (not url or not key):
        sys.exit(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY env vars.\n"
            "See db/SUPABASE_SETUP.md for the setup walkthrough.\n"
            "Use --dry-run to test the CSV parsing without a Supabase connection."
        )

    client: Client | None = None
    if not args.dry_run:
        print(f"Connecting to {url}...")
        client = create_client(url, key)
        print("  connected.\n")
    else:
        print("DRY RUN — no Supabase writes will be made.\n")

    print(f"Migrating data from {DATA_DIR}...\n")

    picks_csv = DATA_DIR / f"picks_{args.season}.csv"
    print(f"--- {picks_csv.name} -> picks_{args.season} ---")
    p_read, p_upserted = migrate_picks(client, picks_csv, dry_run=args.dry_run)

    if not args.picks_only:
        print(f"\n--- pick_changes.csv -> pick_changes ---")
        c_read, c_inserted = migrate_pick_changes(
            client, DATA_DIR / "pick_changes.csv", dry_run=args.dry_run
        )

        print(f"\n--- system_errors.csv -> system_errors ---")
        e_read, e_inserted = migrate_system_errors(
            client, DATA_DIR / "system_errors.csv", dry_run=args.dry_run
        )

    print(f"\nDone.  picks_{args.season}: {p_read} read, {p_upserted} upserted")


if __name__ == "__main__":
    main()

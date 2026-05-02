"""
db/supabase_writer.py — Phase 1.5 dual-write helper for Supabase mirroring.

Sits alongside tracker.py's existing CSV-write flow.  After every
successful CSV write of picks_<season>.csv (in log_picks, grade_date,
import_odds), tracker.py also calls into this module to upsert the
same rows to the Supabase `picks_<season>` table.  Pick-change journal
entries are mirrored to `pick_changes` the same way.

Design contract:
  • CSV remains the source of truth.  Supabase is a mirror.  A Supabase
    outage MUST NOT raise into tracker.py / break the predictor cron.
  • Silent no-op when SUPABASE_URL or SUPABASE_SERVICE_KEY env vars
    are unset (so local dev / GHA before secrets land continue to work).
  • All exceptions are caught, logged to stderr, and swallowed.
  • Field types are converted to JSON-serializable values that match
    the Postgres schema in db/schema.sql.  Mirrors the converter logic
    in db/migrate_csv_to_supabase.py.

Once Phase 2 (dashboard reads from Supabase) ships, the CSV writes
become advisory and we can deprecate this module — until then it runs
in lock-step with every CSV write so we can validate parity daily.

Phase 1.5 acceptance criteria:
  - Both writes succeed for at least 1 week of cron runs without divergence
  - SELECT count(*) FROM picks_2026 matches `wc -l data/picks_2026.csv` - 1
  - SELECT count(*) FROM pick_changes matches the journal CSV
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Lazy client
# ---------------------------------------------------------------------------
#
# We don't import supabase at module top-level so that:
#   1. Importing this module is free even when supabase-py isn't installed
#      (e.g. older deploys, lightweight CI smoke tests).
#   2. The import cost (HTTP client init, auth handshake) is paid once
#      per process, lazily, on first use.
#
# `_CLIENT_TRIED` is set to True after the first attempt regardless of
# success/failure -- if we fail to connect once we don't keep retrying
# the import + handshake on every subsequent call.

_CLIENT: Any = None
_CLIENT_TRIED = False


def _get_client() -> Any:
    """Returns a supabase Client or None if disabled.  Cached."""
    global _CLIENT, _CLIENT_TRIED
    if _CLIENT_TRIED:
        return _CLIENT
    _CLIENT_TRIED = True

    # Best-effort: load .env so local dev (which puts the secrets in
    # .env at the repo root) automatically picks them up.  Production
    # GHA / Railway sets the env vars directly so dotenv is a no-op.
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        pass

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        # Silent no-op -- this is an environment without Supabase
        # configured (e.g. GHA before secrets land, local dev for
        # someone who hasn't set up .env yet).
        return None

    try:
        from supabase import create_client  # type: ignore
    except ImportError:
        print(
            "[supabase_writer] supabase-py not installed; dual-write "
            "disabled.  Run: pip install supabase python-dotenv",
            file=sys.stderr,
        )
        return None

    try:
        _CLIENT = create_client(url, key)
        return _CLIENT
    except Exception as exc:  # noqa: BLE001 -- swallow all client init errors
        print(f"[supabase_writer] failed to create client: {exc!r}",
              file=sys.stderr)
        _CLIENT = None
        return None


# ---------------------------------------------------------------------------
# Type converters (mirror db/migrate_csv_to_supabase.py PICKS_FIELD_MAP)
# ---------------------------------------------------------------------------

def _to_float(s: Any) -> float | None:
    if s is None:
        return None
    s = str(s).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s: Any) -> int | None:
    if s is None:
        return None
    s = str(s).strip()
    if s == "":
        return None
    try:
        return int(float(s))   # tolerate "1.0" stored as float-string
    except ValueError:
        return None


def _to_jsonb(s: Any) -> list:
    """JSONB columns are stored as JSON-encoded strings in CSV.  Parse to
    actual Python list so supabase-py serializes them as proper JSON."""
    if s is None:
        return []
    s = str(s).strip()
    if not s or s == "[]":
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _passthrough(s: Any) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return s if s else None


def _passthrough_upper(s: Any) -> str | None:
    if s is None:
        return None
    s = str(s).strip().upper()
    return s if s else None


def _to_iso_date(s: Any) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        return None


# Column → converter mapping.  Mirrors PICKS_FIELD_MAP in
# db/migrate_csv_to_supabase.py exactly so a row written by the
# tracker has the same Postgres-side shape as a row written by the
# one-off migration script.

PICKS_CONVERTERS: dict[str, Any] = {
    # Identity
    "date":          _to_iso_date,
    "season":        _to_int,
    "game_pk":       _passthrough,
    "game_number":   _to_int,
    "double_header": (lambda s: (str(s).strip() if s is not None else "") or "N"),
    "away_team":     _passthrough_upper,
    "home_team":     _passthrough_upper,
    "game_time_et":  _passthrough,

    # Pitchers
    "away_pitcher":      _passthrough,
    "home_pitcher":      _passthrough,
    "away_pitcher_id":   _to_int,
    "home_pitcher_id":   _to_int,
    "away_pitcher_q":    _passthrough,
    "home_pitcher_q":    _passthrough,
    "away_batting_q":    _passthrough,
    "home_batting_q":    _passthrough,

    # Pitcher stats
    "away_era":  _to_float, "home_era":  _to_float,
    "away_whip": _to_float, "home_whip": _to_float,
    "away_fip":  _to_float, "home_fip":  _to_float,
    "away_bb9":  _to_float, "home_bb9":  _to_float,
    "away_hr9":  _to_float, "home_hr9":  _to_float,
    "away_k9":   _to_float, "home_k9":   _to_float,
    "away_xera": _to_float, "home_xera": _to_float,
    "away_whiff_pct_rank": _to_float, "home_whiff_pct_rank": _to_float,

    # Recent form
    "home_p_last5_pitcher_nrfi":  _to_float, "away_p_last5_pitcher_nrfi":  _to_float,
    "home_p_last10_pitcher_nrfi": _to_float, "away_p_last10_pitcher_nrfi": _to_float,

    # Phase F
    "home_pvt_nrfi_rate":    _to_float, "away_pvt_nrfi_rate":    _to_float,
    "home_avg_ip_per_start": _to_float, "away_avg_ip_per_start": _to_float,

    # Batting
    "away_obp": _to_float, "home_obp": _to_float,
    "away_slg": _to_float, "home_slg": _to_float,
    "away_rpg": _to_float, "home_rpg": _to_float,

    # Top-3 batter aggregates
    "home_top3c_obp": _to_float, "away_top3c_obp": _to_float,
    "home_top3c_slg": _to_float, "away_top3c_slg": _to_float,
    "home_top3c_iso": _to_float, "away_top3c_iso": _to_float,
    "home_top3c_source": _passthrough, "away_top3c_source": _passthrough,
    "home_lineup_json":  _to_jsonb,    "away_lineup_json":  _to_jsonb,

    # Environment
    "park_factor": _to_float,
    "wx_temp_c":   _to_float,
    "wx_wind_kmh": _to_float,
    "wx_humidity": _to_float,
    "wx_is_dome":  _to_int,

    # Umpire
    "home_plate_ump_id":        _to_int,
    "home_plate_ump_nrfi_rate": _to_float,

    # Model output
    "away_proj_runs":  _to_float, "home_proj_runs":  _to_float,
    "combined_lambda": _to_float,
    "lambda_lr_t1":    _to_float, "lambda_lr_b1":    _to_float, "lambda_lr_total": _to_float,
    "nrfi_prob":       _to_float, "yrfi_prob":       _to_float,
    "over_1_5_prob":   _to_float, "under_1_5_prob":  _to_float,

    # Pick decision
    "pick_side":            _passthrough,
    "pick_strength":        _passthrough,
    "pick_label":           _passthrough,
    "blended_inputs":       _to_int,
    "top_factors_t1_json":  _to_jsonb,
    "top_factors_b1_json":  _to_jsonb,
    "created_at":           _passthrough,    # already ISO 8601

    # Grade
    "actual_result": _passthrough,
    "graded_result": _passthrough,
    "fi_away_runs":  _to_int,
    "fi_home_runs":  _to_int,
    "fi_total_runs": _to_int,
    "graded_at":     _passthrough,

    # Odds
    "sportsbook":         _passthrough,
    "market_nrfi_odds":   _passthrough,
    "market_yrfi_odds":   _passthrough,
    "odds_captured_at":   _passthrough,
    "implied_nrfi_prob":  _to_float,
    "implied_yrfi_prob":  _to_float,
    "edge_nrfi":          _to_float,
    "edge_yrfi":          _to_float,
    "edge_on_pick":       _to_float,

    # Opening line + CLV (T4.28)
    "opened_nrfi_odds":   _passthrough,
    "opened_yrfi_odds":   _passthrough,
    "opened_captured_at": _passthrough,
    "clv_pct":            _to_float,

    # Bet
    "bet_placed":        _passthrough,
    "units_risked":      _to_float,
    "profit_loss_units": _to_float,
}


def _transform_pick_row(row: dict) -> dict:
    """Tracker CSV-style row dict -> Supabase row dict with Postgres-
    appropriate types.  Unknown columns are dropped (forwards-compat:
    if FIELDS gains a column not yet in PICKS_CONVERTERS, that column
    just doesn't get mirrored until we add the converter)."""
    out: dict = {}
    for col, conv in PICKS_CONVERTERS.items():
        if col in row:
            out[col] = conv(row.get(col))
    # Default double_header to "N" if the column isn't on the row at
    # all -- some legacy rows pre-T2.21 may still be missing it.
    if "double_header" not in out:
        out["double_header"] = "N"
    return out


# ---------------------------------------------------------------------------
# Public mirror entry points
# ---------------------------------------------------------------------------

def mirror_picks(rows: Iterable[dict], season: int) -> int:
    """Upsert the given pick rows to Supabase `picks_<season>`.
    Returns the number successfully upserted, or 0 on any error / no-op.
    Never raises.

    Pass only the rows that ACTUALLY changed during the current operation
    -- mirroring 400+ rows on every cron run when only 12 changed wastes
    egress.  Callers in tracker.py track per-call diffs and pass just
    those rows here.  Idempotent thanks to ON CONFLICT (date, game_pk).
    """
    client = _get_client()
    if client is None:
        return 0

    rows = list(rows)
    if not rows:
        return 0

    payloads: list[dict] = []
    for r in rows:
        try:
            p = _transform_pick_row(r)
            # Skip rows missing the composite PK -- can't upsert without it.
            if not p.get("date") or not p.get("game_pk"):
                continue
            payloads.append(p)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[supabase_writer] transform failed for row "
                f"{r.get('date')!r}/{r.get('game_pk')!r}: {exc!r}",
                file=sys.stderr,
            )
            continue

    if not payloads:
        return 0

    table = f"picks_{season}"
    try:
        BATCH = 200    # supabase-py default request limit comfort margin
        total = 0
        for i in range(0, len(payloads), BATCH):
            batch = payloads[i:i + BATCH]
            client.table(table).upsert(batch, on_conflict="date,game_pk").execute()
            total += len(batch)
        return total
    except Exception as exc:  # noqa: BLE001
        print(f"[supabase_writer] picks upsert failed ({len(payloads)} rows): {exc!r}",
              file=sys.stderr)
        return 0


def mirror_pick_change(
    *,
    captured_at_utc: str,
    iso_date: str,
    game_pk: str,
    away_team: str,
    home_team: str,
    game_time_et: str,
    old_label: str,
    new_label: str,
) -> bool:
    """Insert one pick-change journal entry to Supabase `pick_changes`.
    Append-only; the journal is meant to record full intraday history
    so duplicates from a re-run aren't a problem (the SERIAL PK keeps
    each insert distinct)."""
    client = _get_client()
    if client is None:
        return False
    payload = {
        "captured_at_utc": captured_at_utc or None,
        "date":            iso_date or None,
        "game_pk":         (game_pk or "").strip() or None,
        "away_team":       (away_team or "").strip() or None,
        "home_team":       (home_team or "").strip() or None,
        "game_time_et":    (game_time_et or "").strip() or None,
        "old_pick_label":  old_label or None,
        "new_pick_label":  new_label or None,
    }
    try:
        client.table("pick_changes").insert(payload).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[supabase_writer] pick_changes insert failed: {exc!r}",
              file=sys.stderr)
        return False


def mirror_system_error(
    *,
    captured_at_utc: str,
    iso_date: str | None,
    step: str,
    exit_code: int | None,
    message: str,
) -> bool:
    """Insert one system_errors row.  Used by tracker / predictor when
    something blew up so the dashboard can show a "today's ops health"
    card without scraping cron logs."""
    client = _get_client()
    if client is None:
        return False
    payload = {
        "captured_at_utc": captured_at_utc or None,
        "date":            iso_date,
        "step":            step or None,
        "exit_code":       exit_code,
        # Cap at 2000 chars to avoid pathologically long traces blowing
        # up the row -- the message column is text but Supabase free
        # tier has a max-row-size limit we'd rather not flirt with.
        "message":         (message[:2000] if message else None),
    }
    try:
        client.table("system_errors").insert(payload).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[supabase_writer] system_errors insert failed: {exc!r}",
              file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Health check (run from the command line for parity verification)
# ---------------------------------------------------------------------------

def _row_count(table: str) -> int | None:
    """Return COUNT(*) for a table, or None on error / disabled."""
    client = _get_client()
    if client is None:
        return None
    try:
        # supabase-py exposes count via select.  Use head=True to avoid
        # actually fetching rows -- just the count header.
        res = client.table(table).select("*", count="exact", head=True).execute()
        return getattr(res, "count", None)
    except Exception as exc:  # noqa: BLE001
        print(f"[supabase_writer] count({table}) failed: {exc!r}", file=sys.stderr)
        return None


def main() -> int:
    """`python -m db.supabase_writer` prints row counts for the three
    tables we mirror to.  Useful for daily parity validation:

        python -m db.supabase_writer
        wc -l data/picks_2026.csv data/pick_changes.csv

    The numbers should match (CSV count = wc -l minus 1 header line).
    """
    if _get_client() is None:
        print("Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY unset).")
        return 1
    for table in ("picks_2026", "pick_changes", "system_errors"):
        c = _row_count(table)
        print(f"  {table:<16} {c if c is not None else '?':>8} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

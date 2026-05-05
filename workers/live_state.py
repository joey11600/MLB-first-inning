#!/usr/bin/env python3
"""
workers/live_state.py — Phase 4 live game-state worker.

Long-running Railway worker that polls the MLB Stats API every ~10
seconds for today's slate, computes per-game live state (status,
inning, score, 1st-inning runs), and upserts the changes to the
Supabase `live_game_state` table.

The dashboard subscribes to row-change events on `live_game_state`
via Supabase Realtime, so updates push to the browser within ~200ms
of the worker writing them.  This replaces the dashboard's previous
30-second polling of `/api/live-state` with sub-second push.

Architecture:
  MLB Stats API ──(10s poll)──> Worker ──(upsert on change)──> Supabase
                                                                 │
                                       Dashboard <──(Realtime push)─┘

Design choices:
  • Diff-and-skip: we cache the last-seen state per game_pk and only
    upsert when something the user would notice has changed (status,
    score, inning, 1st-inning runs).  Saves 80%+ of writes during
    quiet stretches between innings.
  • Adaptive cadence: when all games are Final, drop to a slow poll
    (5 min) so we're not pounding MLB Stats API or burning Railway
    egress overnight.  Same outside the active ET window.
  • Single-process: keeping it minimal — one synchronous loop that
    drives one Supabase client.  No async, no threads, no queues.
    A single MLB schedule call covers the whole slate (vs N-game
    parallel calls), so the per-tick cost is ~100ms regardless of
    slate size.

Run modes:
  python workers/live_state.py          # forever loop (Railway prod)
  python workers/live_state.py --once   # single cycle then exit (smoke test)
  python workers/live_state.py --debug  # verbose per-game logging

Env vars:
  SUPABASE_URL          (required)  — service URL
  SUPABASE_SERVICE_KEY  (required)  — service-role key (bypasses RLS)
  POLL_INTERVAL_S       (default 10)
  QUIET_INTERVAL_S      (default 300, used when no games or all final)
  ACTIVE_HOURS_ET       (default "10-26", i.e. 10am to 2am next day ET)
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Dependency import (fail loudly on Railway if reqs aren't installed)
# ---------------------------------------------------------------------------

try:
    import statsapi
except ImportError:
    sys.exit("Missing dep: pip install mlb-statsapi")

try:
    from supabase import create_client, Client
except ImportError:
    sys.exit("Missing dep: pip install supabase")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ET = ZoneInfo("America/New_York")

POLL_INTERVAL_S  = int(os.environ.get("POLL_INTERVAL_S",  "10"))
QUIET_INTERVAL_S = int(os.environ.get("QUIET_INTERVAL_S", "300"))
ACTIVE_HOURS     = os.environ.get("ACTIVE_HOURS_ET", "10-26")

# T4.13 — Railway worker grade extension.
# Tracks game_pks already graded by THIS worker process for the current
# slate date so grade_completed_picks() doesn't re-query Supabase for
# already-graded games on every 10-sec tick.  Cleared when the slate date
# rolls over (see main loop) so a worker that survives past midnight ET
# starts fresh on the new day.  Cross-process idempotency is provided by
# the SELECT-then-skip-on-terminal check inside grade_completed_picks
# itself, so this set is purely a query-savings optimization, not a
# correctness mechanism.
_graded_in_session: set[str] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_client() -> Client:
    """Boot the Supabase client once.  Uses service_role since the
    worker is a backend process — RLS is bypassed."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        sys.exit(
            "Missing SUPABASE_URL / SUPABASE_SERVICE_KEY env vars.\n"
            "Configure these in the Railway service's Variables tab."
        )
    return create_client(url, key)


def todays_iso() -> str:
    """Date in ET — the slate calendar the predictor uses."""
    return datetime.now(ET).strftime("%Y-%m-%d")


def is_active_hour() -> bool:
    """True when the current ET hour is inside the configured active
    window.  Window can wrap past midnight (e.g. 10-26 = 10am ET to
    2am ET next day, covering all west-coast late games)."""
    try:
        start_str, end_str = ACTIVE_HOURS.split("-")
        start, end = int(start_str), int(end_str)
    except ValueError:
        return True   # malformed config -> always active (fail-open)
    now_h = datetime.now(ET).hour
    if end <= 24:
        return start <= now_h < end
    # Wrap: e.g. start=10, end=26 -> active when hour >= 10 OR < 2
    return now_h >= start or now_h < (end - 24)


# T2.45 #3: record live-state worker failures to Supabase `system_errors`
# so the dashboard's `/api/health` (and the planned ops-health card) can
# surface a degraded worker without operators having to scrape Railway
# logs.  Uses the worker's existing Supabase client directly rather than
# pulling in db.supabase_writer -- saves an import + path mangle, and the
# insert payload is identical.  Best-effort: any failure to record falls
# through silently because the worker must keep polling regardless.
def _record_step_failure(client: "Client", step: str, message: str) -> None:
    try:
        client.table("system_errors").insert({
            "captured_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "date":            todays_iso(),
            "step":            step,
            "exit_code":       1,    # workers don't have RCs; 1 = "logical failure"
            "message":         (message or "")[:1500],
        }).execute()
    except Exception as exc:    # noqa: BLE001 — fail-open per worker contract
        print(f"[live_state] _record_step_failure({step!r}) failed: {exc!r}",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# T4.13 — grade computation helpers (mirrored from tracker._calc_pnl).
# Pure functions, no I/O.  Kept inline here instead of importing tracker so
# the worker container doesn't need the full predictor codebase available
# (tracker pulls in pandas, statsapi, etc. at import time -- way more than
# the worker needs to compute "did we win or lose this bet").
# ---------------------------------------------------------------------------

def _payout_per_unit(odds_str) -> Optional[float]:
    """American odds -> payout-per-unit-risked.
    -110 -> 0.909, +120 -> 1.20, -200 -> 0.50, etc.
    Returns None when the string is empty or unparseable."""
    if odds_str is None:
        return None
    s = str(odds_str).strip()
    if not s:
        return None
    try:
        odds = float(s)
    except (ValueError, TypeError):
        return None
    if odds == 0:
        return None
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def _compute_pnl(picks_row: dict, graded: str) -> Optional[float]:
    """Inline mirror of tracker._calc_pnl for the worker.

    Decision tree (kept identical to tracker.py:2407 so the worker and
    the GH-Actions grade cycle converge on the same number for any row):

      * Not WIN/LOSS -> None (PASS / POSTPONED / not-graded contribute nothing)
      * pick_side not in {NRFI, YRFI} -> None (PASS rows aren't bets)
      * bet_placed='N'  (sub-threshold edge) -> 0.0  (counted as a graded
        non-bet so dashboard ROI denominator stays right)
      * units = explicit units_risked, falling back to 1.0/STRONG, 0.5/LEAN
      * LOSS -> -units
      * WIN  -> units * payout_per_unit(market odds), with -110 fallback
        when the market odds column is empty.

    Returns a Python float (rounded to 3dp like tracker._fmt(x, 3)) or
    None when the row isn't gradable to a numeric P&L.  The Supabase
    converter (_to_float in supabase_writer) accepts None and writes
    SQL NULL -- consistent with how the dashboard already renders
    'no P&L yet' rows."""
    if graded not in ("WIN", "LOSS"):
        return None

    pick = (picks_row.get("pick_side") or "").strip().upper()
    if pick not in ("NRFI", "YRFI"):
        return None

    bet = (picks_row.get("bet_placed") or "").strip().upper()
    if bet == "N":
        return 0.0

    # Bet size: explicit units_risked wins; else strength default.
    raw_units = picks_row.get("units_risked")
    units: Optional[float] = None
    if raw_units not in (None, ""):
        try:
            units = float(raw_units)
        except (ValueError, TypeError):
            units = None
    if units is None:
        strength = (picks_row.get("pick_strength") or "").strip().upper()
        if strength == "STRONG":
            units = 1.0
        elif strength == "LEAN":
            units = 0.5
        else:
            units = 0.0
    if units <= 0:
        return None

    if graded == "LOSS":
        return round(-units, 3)

    # WIN -- prefer real market odds, otherwise -110 flat fallback.
    odds_col = "market_nrfi_odds" if pick == "NRFI" else "market_yrfi_odds"
    ppu = _payout_per_unit(picks_row.get(odds_col))
    if ppu is None:
        ppu = 100.0 / 110.0    # 0.9091 — matches tracker._calc_pnl fallback
    return round(units * ppu, 3)


def fetch_slate(date_iso: str) -> list[dict]:
    """One call to MLB Stats API hydrating linescore so we get inning
    state + 1st-inning runs in a single round trip.  Returns a list
    of normalized game-state dicts ready for upsert."""
    try:
        sched = statsapi.get("schedule", {
            "sportId":  1,
            "date":     date_iso,
            # `team` adds team.abbreviation (3-letter code).  Without it
            # the schedule endpoint only returns the full team name and
            # numeric ID -- and we'd be writing "?" placeholders to
            # Supabase, breaking the dashboard's row matching by team.
            # `probablePitcher` lets us detect starter scratches by
            # comparing live IDs vs what we recorded in picks_2026
            # (T2.40 scratch detection).
            "hydrate":  "linescore,team,probablePitcher",
        })
    except Exception as exc:    # noqa: BLE001 — keep loop resilient
        print(f"[live_state] schedule fetch failed: {exc!r}", file=sys.stderr)
        return []

    out: list[dict] = []
    for d in sched.get("dates", []):
        if d.get("date") != date_iso:
            continue
        for g in d.get("games", []):
            row = parse_game(g, date_iso)
            if row:
                out.append(row)
    return out


def parse_game(g: dict, date_iso: str) -> Optional[dict]:
    """Pull a single game's response into the live_game_state schema.
    Mirrors the field shape used by /api/live-state so the dashboard
    can swap data sources without behavior change."""
    gp = g.get("gamePk")
    if gp is None:
        return None

    status_obj  = g.get("status") or {}
    abstract    = status_obj.get("abstractGameState", "")
    detailed    = status_obj.get("detailedState", "")

    teams       = g.get("teams") or {}
    away        = teams.get("away") or {}
    home        = teams.get("home") or {}
    away_team   = ((away.get("team") or {}).get("abbreviation")) or "?"
    home_team   = ((home.get("team") or {}).get("abbreviation")) or "?"
    away_score  = (away.get("score") or 0)
    home_score  = (home.get("score") or 0)

    ls          = g.get("linescore") or {}
    cur_inning  = ls.get("currentInning")
    inn_state   = ls.get("inningState", "")

    inn1        = next((i for i in (ls.get("innings") or []) if i.get("num") == 1), {}) or {}
    fi_away     = (inn1.get("away") or {}).get("runs")
    fi_home     = (inn1.get("home") or {}).get("runs")
    fi_total    = (
        (fi_away + fi_home)
        if isinstance(fi_away, int) and isinstance(fi_home, int)
        else None
    )

    # The 1st inning is "complete" when:
    #   - game is Final (always), OR
    #   - we're in inning 2+ (top or otherwise), OR
    #   - we're in B1 / End of 1 / Middle of 1 (the half-inning beyond T1)
    fi_complete = (
        abstract == "Final"
        or (isinstance(cur_inning, int) and cur_inning >= 2)
        or (cur_inning == 1 and inn_state in ("End", "Middle", "Bottom"))
    )

    # T2.40: pull current probable_pitcher info per side so check_scratches
    # can compare live IDs vs the IDs we recorded at predict time.
    away_pp = (away.get("probablePitcher") or {})
    home_pp = (home.get("probablePitcher") or {})
    away_pp_id   = away_pp.get("id")
    home_pp_id   = home_pp.get("id")
    away_pp_name = away_pp.get("fullName") or ""
    home_pp_name = home_pp.get("fullName") or ""

    return {
        "game_pk":              str(gp),
        "date":                 date_iso,
        "away_team":            away_team,
        "home_team":            home_team,
        "status":               detailed,
        "abstract_game_state":  abstract,
        "current_inning":       cur_inning,
        "inning_state":         inn_state,
        "away_score":           int(away_score),
        "home_score":           int(home_score),
        "fi_away_runs":         int(fi_away)  if isinstance(fi_away,  int) else None,
        "fi_home_runs":         int(fi_home)  if isinstance(fi_home,  int) else None,
        "fi_total_runs":        int(fi_total) if isinstance(fi_total, int) else None,
        "fi_complete":          bool(fi_complete),
        # T2.40 scratch-detection fields.  Not written to Supabase
        # live_game_state — they're consumed only by check_scratches
        # in this same worker.  state_signature() ignores them so the
        # diff-skip logic isn't affected.
        "_probable_away_id":    int(away_pp_id) if isinstance(away_pp_id, int) else None,
        "_probable_home_id":    int(home_pp_id) if isinstance(home_pp_id, int) else None,
        "_probable_away_name":  str(away_pp_name),
        "_probable_home_name":  str(home_pp_name),
    }


def state_signature(row: dict) -> tuple:
    """A tuple of the user-visible fields we'd push for.  Two snapshots
    with the same signature need not generate a Supabase write — the
    dashboard would render identical pixels either way."""
    return (
        row.get("status"),
        row.get("abstract_game_state"),
        row.get("current_inning"),
        row.get("inning_state"),
        row.get("away_score"),
        row.get("home_score"),
        row.get("fi_away_runs"),
        row.get("fi_home_runs"),
        row.get("fi_total_runs"),
        row.get("fi_complete"),
    )


def short_state(row: dict) -> str:
    """One-line debug string for a single game's current state."""
    inn = row.get("current_inning") or "-"
    half = (row.get("inning_state") or "")[:3] or "--"
    return (
        f"{row['away_team']:>3}@{row['home_team']:<3}  "
        f"{(row['abstract_game_state'] or '?'):<7} "
        f"{half}{inn}  "
        f"{row['away_score']}-{row['home_score']}  "
        f"1st={row.get('fi_away_runs')}-{row.get('fi_home_runs')} "
        f"({'X' if row.get('fi_complete') else '.'})"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _strip_internal_fields(row: dict) -> dict:
    """Remove leading-underscore keys before sending to Supabase.  PostgREST
    rejects unknown columns; the underscore-prefixed fields (_probable_*)
    are consumed locally by check_scratches but not part of the
    live_game_state schema."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def run_cycle(client: Client, last_sigs: dict[str, tuple], debug: bool) -> tuple[int, int, bool, list[dict]]:
    """Run one polling iteration.  Returns
    (rows_seen, rows_upserted, all_final, full_rows).
    `full_rows` retains the _probable_* fields for downstream
    consumers like check_scratches."""
    date_iso = todays_iso()
    rows = fetch_slate(date_iso)
    if not rows:
        return (0, 0, True, [])

    to_upsert: list[dict] = []
    for r in rows:
        sig  = state_signature(r)
        prev = last_sigs.get(r["game_pk"])
        if sig != prev:
            to_upsert.append(_strip_internal_fields(r))
            last_sigs[r["game_pk"]] = sig
            if debug:
                print(f"[live_state] CHANGE  {short_state(r)}")
        elif debug:
            print(f"[live_state] same    {short_state(r)}")

    if to_upsert:
        try:
            client.table("live_game_state").upsert(
                to_upsert, on_conflict="game_pk"
            ).execute()
            ts = datetime.now(ET).strftime("%H:%M:%S")
            print(f"[live_state] {ts} ET  pushed {len(to_upsert):>2}/{len(rows):>2} games")
        except Exception as exc:    # noqa: BLE001
            print(f"[live_state] upsert failed: {exc!r}", file=sys.stderr)
            _record_step_failure(
                client, "live-state-upsert",
                f"upsert {len(to_upsert)} rows failed: {exc!r}",
            )

    all_final = all(r.get("abstract_game_state") == "Final" for r in rows)
    return (len(rows), len(to_upsert), all_final, rows)


# ---------------------------------------------------------------------------
# T4.13 — Railway worker grade extension.
#
# Why this exists: GH Actions cron reliability degraded to ~5 fires per 4
# hours (vs the 12/hour the schedule asks for) during prime-time slates,
# so picks_<season>.graded_result lagged 30-60+ minutes behind reality
# even though live_game_state was already showing fi_complete=true.
# Operators saw "winning bet, ungraded" and had to manual-grade.
#
# This worker already polls MLB every 10 sec for live_game_state, so it
# has fresh fi_total_runs in hand the moment a 1st inning lands.  We
# piggyback the grade decision onto that same tick: read picks_<season>
# from Supabase, compute WIN/LOSS/PASS deterministically, write it back.
# Result: dashboard sees graded picks ~10 sec after Final-of-1st instead
# of waiting for the next non-skipped GH Actions fire.
#
# Convergence with GH Actions: both writers share Supabase via
# ON CONFLICT(date, game_pk).  GH Actions also writes the canonical CSV
# and re-mirrors -- those writes are idempotent (same WIN/LOSS/PASS
# decision, identical profit_loss_units).  No conflict.
# ---------------------------------------------------------------------------

def grade_completed_picks(
    client: Client,
    full_rows: list[dict],
    date_iso: str,
) -> int:
    """Find newly-completed first innings and write graded results to
    picks_<season> in Supabase.  Returns the count of rows graded this
    tick.  Self-skips when nothing's new, so calling every cycle is cheap.

    Filtering pipeline:
      1. full_rows -> games where fi_complete=True AND we have all three
         fi_*_runs values (defensive: parse_game can return fi_complete
         for a Final game where the 1st-inning subobject is missing,
         which would yield None runs and a bogus grade).
      2. Drop game_pks already graded earlier in this worker session
         (`_graded_in_session` cache) to avoid re-querying Supabase
         for finished work.
      3. For each remaining game, SELECT the picks_<season> row(s) by
         (date, game_pk).  A single date+game_pk usually returns 1 row,
         but DH-paired predictions or backfilled rows could return >1
         -- we grade each independently rather than assuming uniqueness.
      4. Skip rows whose graded_result is already terminal (WIN/LOSS/PASS):
         those were graded by an earlier worker tick, GH Actions, or the
         operator.  Add to the session cache so we don't re-SELECT.
      5. Compute graded_result + profit_loss_units, UPDATE in place.

    Failure model: every Supabase call is wrapped in try/except.  Errors
    are logged + recorded to system_errors but never propagate -- the
    live_state polling loop must keep running even if grading fails (a
    grade outage is recoverable at the next GH Actions fire; a missed
    live_state upsert is not, because the dashboard's whole live UI
    depends on it).

    Telegram pings: lazy-imports tracker._notify_strong_graded_telegram
    so STRONG bets that grade here still trigger the operator ping.
    Notifications_log dedup means even if the same row is graded again
    by GH Actions later, the user gets exactly one ping per game per
    side.  Skipped silently if the import fails (worker doesn't need
    tracker to do its core job)."""
    if not full_rows:
        return 0

    # Step 1+2: filter to newly-grade-able games.
    candidates = [
        r for r in full_rows
        if r.get("fi_complete")
        and r.get("fi_total_runs") is not None
        and r.get("fi_away_runs") is not None
        and r.get("fi_home_runs") is not None
        and str(r.get("game_pk") or "") not in _graded_in_session
    ]
    if not candidates:
        return 0

    season = int(date_iso[:4])
    table  = f"picks_{season}"
    graded_count = 0
    # Re-fetched once on first successful grade so we can fire telegram
    # pings with today's running record.  None until we need it.
    today_record_cache: Optional[tuple] = None

    for state in candidates:
        gp = str(state.get("game_pk") or "")
        if not gp:
            continue

        # Step 3: SELECT picks_<season> for this (date, game_pk).
        try:
            res = (
                client.table(table)
                      .select(
                          "date, game_pk, away_team, home_team, "
                          "pick_side, pick_strength, pick_label, "
                          "graded_result, "
                          "market_nrfi_odds, market_yrfi_odds, "
                          "units_risked, bet_placed, game_time_et"
                      )
                      .eq("date", date_iso)
                      .eq("game_pk", gp)
                      .execute()
            )
        except Exception as exc:    # noqa: BLE001
            print(f"[live_state] grade-select {gp}: {exc!r}",
                  file=sys.stderr)
            _record_step_failure(
                client, "grade-select",
                f"SELECT picks_{season} game_pk={gp}: {exc!r}",
            )
            continue

        rows = res.data or []
        if not rows:
            # No picks_<season> entry for this game (e.g. predictor never
            # ran on this slate, or row got filtered out pre-write).
            # Cache to skip on subsequent ticks -- nothing to grade and
            # re-checking every 10s would waste queries.
            _graded_in_session.add(gp)
            continue

        all_terminal = True
        for picks_row in rows:
            existing = (picks_row.get("graded_result") or "").strip().upper()
            # Step 4: terminal-grade short-circuit.  POSTPONED/SUSPENDED
            # are NOT terminal -- tracker.grade_date re-checks them in
            # case of makeup/resume, and we follow the same rule so a
            # rain-delayed game whose 1st inning eventually completes
            # gets re-graded correctly.
            if existing in ("WIN", "LOSS", "PASS"):
                continue

            away_r  = state["fi_away_runs"]
            home_r  = state["fi_home_runs"]
            total_r = state["fi_total_runs"]
            actual  = "NRFI" if total_r == 0 else "YRFI"
            pick    = (picks_row.get("pick_side") or "").strip().upper()

            if pick == "PASS":
                graded_result = "PASS"
            elif pick in ("NRFI", "YRFI") and pick == actual:
                graded_result = "WIN"
            elif pick in ("NRFI", "YRFI"):
                graded_result = "LOSS"
            else:
                # Unknown pick_side (legacy row, manual edit, etc.) --
                # skip rather than guessing.  GH Actions grade will
                # surface it via the predictor's normal logging.
                all_terminal = False
                continue

            pnl     = _compute_pnl(picks_row, graded_result)
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

            update = {
                "actual_result":     actual,
                "graded_result":     graded_result,
                "fi_away_runs":      int(away_r),
                "fi_home_runs":      int(home_r),
                "fi_total_runs":     int(total_r),
                "graded_at":         now_iso,
                "profit_loss_units": pnl,
            }

            # Step 5: UPDATE in place.  Filter on date+game_pk to be a
            # no-op if a parallel writer raced us to it (idempotent).
            try:
                (client.table(table)
                       .update(update)
                       .eq("date", date_iso)
                       .eq("game_pk", gp)
                       .execute())
            except Exception as exc:    # noqa: BLE001
                print(f"[live_state] grade-update {gp}: {exc!r}",
                      file=sys.stderr)
                _record_step_failure(
                    client, "grade-update",
                    f"UPDATE picks_{season} game_pk={gp}: {exc!r}",
                )
                all_terminal = False
                continue

            graded_count += 1
            ts = datetime.now(ET).strftime("%H:%M:%S")
            pnl_tag = f"  pnl={pnl:+.3f}u" if isinstance(pnl, (int, float)) and pnl != 0 else ""
            print(
                f"[live_state] {ts} ET  graded {picks_row.get('away_team') or '???'}@"
                f"{picks_row.get('home_team') or '???'} (gp={gp})  "
                f"pick={pick} actual={actual} -> {graded_result}  "
                f"1st={away_r}-{home_r}{pnl_tag}",
                flush=True,
            )

            # Telegram graded ping for STRONG bets, mirrored from
            # tracker.grade_date.  Lazy-imported per-tick so import
            # errors degrade gracefully (worker must keep polling).
            try:
                if today_record_cache is None:
                    # One SELECT per tick to compute today's running
                    # W-L + P&L.  Refreshed AFTER the UPDATE above so
                    # the just-graded row is included in the running
                    # record we send to telegram.
                    todays_res = (
                        client.table(table)
                              .select(
                                  "graded_result, profit_loss_units, "
                                  "pick_side, pick_strength, "
                                  "bet_placed, units_risked"
                              )
                              .eq("date", date_iso)
                              .execute()
                    )
                    todays_rows = todays_res.data or []
                    import sys as _sys
                    from pathlib import Path as _P
                    repo_root = _P(__file__).resolve().parent.parent
                    if str(repo_root) not in _sys.path:
                        _sys.path.insert(0, str(repo_root))
                    from tracker import _aggregate_today_record
                    today_record_cache = _aggregate_today_record(
                        todays_rows, date_iso
                    )
                from tracker import _notify_strong_graded_telegram
                # Build a row dict shaped like what tracker passes:
                # the original picks_row plus the freshly-written
                # grade fields.
                notify_row = {**picks_row, **update}
                today_record, today_pl = today_record_cache
                _notify_strong_graded_telegram(
                    notify_row, today_record, today_pl,
                )
            except Exception as exc:    # noqa: BLE001
                # Non-fatal: telegram missing != grade missing.  GH
                # Actions will re-fire the ping after its next grade
                # cycle if the worker's path was unavailable.
                print(
                    f"[live_state] telegram graded ping skipped "
                    f"(gp={gp}): {exc!r}",
                    file=sys.stderr,
                )

        # If every row for this game_pk ended up terminal (or just got
        # graded), cache the game_pk so subsequent ticks skip it.  If
        # any row failed mid-loop (all_terminal=False), leave the
        # game_pk uncached so the next tick retries the failed UPDATE.
        if all_terminal:
            _graded_in_session.add(gp)

    return graded_count


def check_scratches(client, full_rows: list[dict]) -> int:
    """T2.40 starter-scratch detector.

    For each pre-game game in today's slate, compare the LIVE
    `probablePitcher.id` (just fetched from MLB Stats API in
    fetch_slate) to the pitcher_id we recorded in picks_<season>
    at predict time.  If they differ on a placed STRONG bet, fire
    a Telegram scratch alert via the T2.38 notifier framework.

    Throttled by the caller to ~once per minute.  Self-deduped via
    notifications_log (6h window per side per game) so a scratch
    that lingers across many cycles still pings exactly once.

    Returns the number of scratch alerts dispatched (0 if none)."""
    if not full_rows:
        return 0
    # Only check pre-game games; once a game is Live or Final the
    # pitcher matchup is locked in and any "probable" change is
    # cosmetic / late-season-roster-noise that doesn't affect the bet.
    pregame = [r for r in full_rows if (r.get("abstract_game_state") or "") == "Preview"]
    if not pregame:
        return 0
    today_iso = todays_iso()
    season = datetime.now(ET).year
    try:
        res = (
            client.table(f"picks_{season}")
                  .select(
                      "game_pk, away_team, home_team, "
                      "away_pitcher, home_pitcher, "
                      "away_pitcher_id, home_pitcher_id, "
                      "pick_side, pick_strength, "
                      "bet_placed, game_time_et, date"
                  )
                  .eq("date", today_iso)
                  .eq("pick_strength", "STRONG")
                  .eq("bet_placed", "Y")
                  .execute()
        )
    except Exception as exc:    # noqa: BLE001
        print(f"[live_state] scratch check supabase select failed: {exc!r}",
              file=sys.stderr)
        _record_step_failure(
            client, "scratch-check-select",
            f"picks_{season} select failed: {exc!r}",
        )
        return 0
    our_rows = res.data or []
    if not our_rows:
        return 0

    sched_by_pk = {(r.get("game_pk") or ""): r for r in pregame}

    # Lazy-import so the worker boots even if tracker / supabase_writer
    # have an init failure (e.g. supabase-py missing in a stripped image).
    try:
        import sys as _sys
        from pathlib import Path as _P
        repo_root = _P(__file__).resolve().parent.parent
        if str(repo_root) not in _sys.path:
            _sys.path.insert(0, str(repo_root))
        from tracker import _notify_strong_scratch_telegram
    except Exception as exc:    # noqa: BLE001
        print(f"[live_state] scratch notifier import failed: {exc!r}",
              file=sys.stderr)
        return 0

    fired = 0
    for our in our_rows:
        gp = str(our.get("game_pk") or "")
        sched = sched_by_pk.get(gp)
        if sched is None:
            continue
        cur_away_id   = sched.get("_probable_away_id")
        cur_home_id   = sched.get("_probable_home_id")
        cur_away_name = sched.get("_probable_away_name") or ""
        cur_home_name = sched.get("_probable_home_name") or ""
        try:
            our_away = int(our.get("away_pitcher_id") or 0)
            our_home = int(our.get("home_pitcher_id") or 0)
        except (TypeError, ValueError):
            continue

        # Skip rows where we never had a real pitcher recorded
        # (TBD / pitcher_q='avg' rows).  Those would false-positive
        # every time a probable_pitcher gets named.
        if our_away and cur_away_id and our_away != cur_away_id:
            _notify_strong_scratch_telegram(
                our, "away",
                our.get("away_pitcher") or f"id={our_away}",
                cur_away_name or f"id={cur_away_id}",
            )
            fired += 1
        if our_home and cur_home_id and our_home != cur_home_id:
            _notify_strong_scratch_telegram(
                our, "home",
                our.get("home_pitcher") or f"id={our_home}",
                cur_home_name or f"id={cur_home_id}",
            )
            fired += 1

    if fired:
        ts = datetime.now(ET).strftime("%H:%M:%S")
        print(f"[live_state] {ts} ET  scratch alerts fired: {fired}", flush=True)
    return fired


def check_ops_health(client) -> None:
    """T2.38 #7: detect stalled predictor and ping the user via
    Telegram.  Queries Supabase picks_<season> for the most recent
    `updated_at` timestamp.  If it's >30 min old during active hours,
    fire a one-shot stall alert (notifications_log dedup ensures at
    most one alert per hour).  Self-deduped + soft-fail."""
    try:
        # Lazy import so this worker boots even if tracker / supabase_writer
        # have an init failure.
        import sys as _sys
        from pathlib import Path as _P
        repo_root = _P(__file__).resolve().parent.parent
        if str(repo_root) not in _sys.path:
            _sys.path.insert(0, str(repo_root))
        from datetime import datetime as _dt, timedelta as _td
        from tracker import _notify_ops_health_telegram

        season = _dt.now(ET).year
        res = (
            client.table(f"picks_{season}")
                  .select("updated_at")
                  .order("updated_at", desc=True)
                  .limit(1)
                  .execute()
        )
        rows = res.data or []
        if not rows:
            return    # no picks ever -- can't gauge staleness
        latest_iso = (rows[0] or {}).get("updated_at") or ""
        if not latest_iso:
            return
        # Parse: "2026-05-02T16:38:00.735458+00:00" or with Z suffix
        latest = _dt.fromisoformat(latest_iso.replace("Z", "+00:00"))
        now    = _dt.now(latest.tzinfo)
        age_m  = int((now - latest).total_seconds() / 60)
        # T4.14: bumped from 30 min -> 45 min.  GitHub Actions cron
        # routinely fires 10-25 min late on free runners; the 30-min
        # threshold was firing on routine cron-skip events that resolved
        # themselves before the operator could act.  45 min is the point
        # where intervention is actually needed (real outage vs transient
        # cron lag).  Dedup window was bumped to 120 min in tracker.py
        # so a sustained outage produces 1 ping every 2h instead of
        # every hour.
        if age_m >= 45:
            _notify_ops_health_telegram(age_m)
    except Exception as exc:    # noqa: BLE001 — advisory only
        # Don't even log -- ops health checks shouldn't pollute logs
        # when supabase-py isn't installed in this image.
        if "[debug]" in str(exc):
            print(f"[live_state] ops_health check skipped: {exc!r}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one cycle and exit (for smoke testing)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Verbose per-game logging on every poll",
    )
    args = parser.parse_args()

    client = get_client()
    print(
        f"[live_state] connected, "
        f"poll={POLL_INTERVAL_S}s quiet={QUIET_INTERVAL_S}s "
        f"active_hours_ET={ACTIVE_HOURS}"
    )

    last_sigs: dict[str, tuple] = {}
    last_iso_seen = todays_iso()
    running = True
    # T2.38 #7: ops-health throttle.  Check at most every 10 cycles
    # (~100 sec at 10s poll, ~50min at 5min quiet) to avoid pounding
    # Supabase with health queries the user doesn't see anyway.
    cycle_count = 0
    HEALTH_EVERY_N_CYCLES = 10
    # T2.40: scratch-detection throttle.  Probable pitcher rarely
    # changes minute-to-minute, so polling MLB and querying Supabase
    # every 10s is wasteful.  Run every 6 cycles (~60s in fast mode,
    # ~30min in quiet mode).  Notifications_log dedup means even if
    # it fires more often we still only ping once per game per side.
    SCRATCH_EVERY_N_CYCLES = 6

    def handle_sig(*_a):
        nonlocal running
        running = False
        print("[live_state] received shutdown signal, exiting after current cycle")

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT,  handle_sig)

    while running:
        # Drop stale game_pk signatures when the slate date rolls over.
        # Otherwise last_sigs grows unbounded across multi-day runs.
        cur_iso = todays_iso()
        if cur_iso != last_iso_seen:
            print(f"[live_state] date rolled {last_iso_seen} -> {cur_iso}, clearing sig cache")
            last_sigs.clear()
            # T4.13: also clear the grade-session cache so games on the
            # new slate get fresh evaluation.  Without this a worker
            # that survives midnight would think every game on the new
            # slate was already graded just because game_pks happen to
            # collide (they don't, but the cache would still bloat).
            _graded_in_session.clear()
            last_iso_seen = cur_iso

        if not is_active_hour():
            now_et = datetime.now(ET).strftime("%H:%M %Z")
            print(f"[live_state] {now_et} outside active window; sleeping {QUIET_INTERVAL_S}s")
            if args.once:
                break
            time.sleep(QUIET_INTERVAL_S)
            continue

        seen, pushed, all_final, full_rows = run_cycle(client, last_sigs, args.debug)

        # T4.13: Railway worker grade extension.  Run after every cycle
        # (cheap when nothing's complete -- function self-skips when
        # full_rows has no fi_complete=True games not in the session
        # cache).  Wrapped in try/except so a grade outage NEVER takes
        # down the live_state polling loop -- worker contract is fail-
        # open: live UI must keep working even when grading is degraded.
        try:
            grade_completed_picks(client, full_rows, cur_iso)
        except Exception as exc:    # noqa: BLE001
            print(f"[live_state] grade_completed_picks crashed: {exc!r}",
                  file=sys.stderr)
            _record_step_failure(
                client, "grade-cycle",
                f"grade_completed_picks raised: {exc!r}",
            )

        # T2.38 #7: throttled ops-health check.
        cycle_count += 1
        if cycle_count % HEALTH_EVERY_N_CYCLES == 0:
            check_ops_health(client)

        # T2.40: throttled scratch detection on pre-game games.
        if cycle_count % SCRATCH_EVERY_N_CYCLES == 0:
            check_scratches(client, full_rows)

        if args.once:
            # In --once mode also run health + scratch checks so smoke
            # tests cover them.  Grade was already invoked above; no
            # need to call it twice.
            check_ops_health(client)
            check_scratches(client, full_rows)
            print(f"[live_state] --once mode: seen={seen} pushed={pushed} all_final={all_final}")
            break

        # Sleep adaptive: long when nothing's happening, short during games.
        sleep_s = QUIET_INTERVAL_S if all_final else POLL_INTERVAL_S
        time.sleep(sleep_s)

    print("[live_state] shutdown clean")


if __name__ == "__main__":
    main()

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
from datetime import datetime
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
        if age_m >= 30:
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
            last_iso_seen = cur_iso

        if not is_active_hour():
            now_et = datetime.now(ET).strftime("%H:%M %Z")
            print(f"[live_state] {now_et} outside active window; sleeping {QUIET_INTERVAL_S}s")
            if args.once:
                break
            time.sleep(QUIET_INTERVAL_S)
            continue

        seen, pushed, all_final, full_rows = run_cycle(client, last_sigs, args.debug)

        # T2.38 #7: throttled ops-health check.
        cycle_count += 1
        if cycle_count % HEALTH_EVERY_N_CYCLES == 0:
            check_ops_health(client)

        # T2.40: throttled scratch detection on pre-game games.
        if cycle_count % SCRATCH_EVERY_N_CYCLES == 0:
            check_scratches(client, full_rows)

        if args.once:
            # In --once mode also run health + scratch checks so smoke
            # tests cover them.
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

#!/usr/bin/env python3
"""
workers/predictor_loop.py — Phase 3 predictor loop for Railway.

Runs the full predict + grade + odds-import flow every 5 minutes
during active hours (9am-2am ET).  Replaces GHA's hourly cron as the
PRIMARY scheduler, dropping prediction freshness from 60-180min worst
case down to ~5min.  GHA stays running as a backup catch-up + the
daily git archival commit.

Each cycle, in order:
  1. Catch-up grade yesterday's slate    (idempotent; soft-fail)
  2. Live-grade today's completed 1sts   (soft-fail)
  3. Predict today's slate               (HARD fail; aborts cycle)
  4. Scrape DraftKings odds              (soft-fail)
  5. Import odds                          (soft-fail; computes edges)

Each step is idempotent thanks to tracker.py's lock-aware logic:
  - log_picks upserts by (date, game_pk) with bet-time pick lock
    so a refresh after lockout never rewrites a placed bet.
  - grade_date skips already-terminal grades (WIN/LOSS/PASS).
  - import_odds preserves bet-time odds with the same lock.

The Railway container's local CSV is ephemeral — that's fine.
tracker.log_picks / grade_date already dual-write to Supabase
(Phase 1.5), and Supabase is the source of truth for the dashboard.
The local CSV is just a working buffer between cycles within a
single container lifetime.

Run modes:
  python workers/predictor_loop.py          # forever loop (Railway prod)
  python workers/predictor_loop.py --once   # one cycle, exit clean (test)

Env vars:
  SUPABASE_URL                     (required)
  SUPABASE_SERVICE_KEY             (required)
  TELEGRAM_BOT_TOKEN               (optional — pick-flip notifications)
  TELEGRAM_CHAT_ID                 (optional)
  PREDICTOR_INTERVAL_S             (default 300, i.e. 5 min)
  PREDICTOR_QUIET_S                (default 1800, used outside active hours)
  PREDICTOR_ACTIVE_HOURS_ET        (default "9-26", i.e. 9am ET to 2am ET)
  PREDICTOR_MIN_EDGE               (default "0.02", i.e. 2% LEAN edge gate)
  PREDICTOR_SCRAPE_DK              (default "skip"; set to "enabled" to
                                    actually run the DK scrape on Railway.
                                    Default skip because DK's CDN blocks
                                    Railway egress -- see T2.56 in CHANGELOG.
                                    GHA's hourly cron handles scraping.)
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent

# Tunables (override via env)
INTERVAL_S         = int(os.environ.get("PREDICTOR_INTERVAL_S",   "300"))
QUIET_INTERVAL_S   = int(os.environ.get("PREDICTOR_QUIET_S",      "1800"))
ACTIVE_HOURS       = os.environ.get("PREDICTOR_ACTIVE_HOURS_ET",  "9-26")
MIN_EDGE           = os.environ.get("PREDICTOR_MIN_EDGE",         "0.02")
# Per-step subprocess timeouts (seconds).  Sized generously so a slow
# MLB API on a busy night doesn't kill an in-progress predict.
TIMEOUT_GRADE      = int(os.environ.get("PREDICTOR_GRADE_TIMEOUT_S",   "180"))
TIMEOUT_PREDICT    = int(os.environ.get("PREDICTOR_PREDICT_TIMEOUT_S", "300"))
TIMEOUT_SCRAPE     = int(os.environ.get("PREDICTOR_SCRAPE_TIMEOUT_S",  "120"))
TIMEOUT_IMPORT     = int(os.environ.get("PREDICTOR_IMPORT_TIMEOUT_S",  "120"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_active_hour() -> bool:
    """True when current ET hour is inside the active window.  Window
    can wrap past midnight (e.g. "9-26" = 9am ET to 2am ET next day)."""
    try:
        s, e = ACTIVE_HOURS.split("-")
        start, end = int(s), int(e)
    except ValueError:
        return True
    h = datetime.now(ET).hour
    if end <= 24:
        return start <= h < end
    return h >= start or h < (end - 24)


def today_iso() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def yesterday_iso() -> str:
    return (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")


# T2.47: capture last 600 chars of stderr per step so the system_errors
# row carries actual diagnostic context (not just "rc=1").  Cleared at
# the start of each cycle.  This is dual-streamed: stderr still flows
# to Railway's log capture in real time AND we keep a tail buffer for
# the system_errors message field.
_LAST_STDERR_TAIL: dict[str, str] = {}


def run(cmd: list[str], timeout_s: int, label: str) -> int:
    """Run a subprocess in REPO_ROOT, streaming output.  Returns exit
    code.  124 on timeout, 1 on unexpected exception.

    stdout streams directly to Railway's log capture so the operator
    can watch a live cycle in real time.  stderr is teed: streamed to
    the parent's stderr AND captured into _LAST_STDERR_TAIL[label] so
    `_record_step_failure` can surface the actual error in
    system_errors instead of an opaque "rc=1"."""
    print(f"[predictor] [{label}] $ {' '.join(cmd)}", flush=True)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=REPO_ROOT, timeout=timeout_s,
            stderr=subprocess.PIPE, text=True,
        )
        # Re-emit stderr so Railway log capture still sees it in
        # real time (subprocess.PIPE swallows it from the parent's
        # tty otherwise).
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr, flush=True)
            # Keep the LAST 600 chars -- usually the actual error msg.
            _LAST_STDERR_TAIL[label] = proc.stderr[-600:].strip()
        else:
            _LAST_STDERR_TAIL[label] = ""
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        print(f"[predictor] [{label}] TIMEOUT after {timeout_s}s", file=sys.stderr, flush=True)
        _LAST_STDERR_TAIL[label] = f"TIMEOUT after {timeout_s}s"
        return 124
    except Exception as exc:    # noqa: BLE001
        print(f"[predictor] [{label}] subprocess error: {exc!r}", file=sys.stderr, flush=True)
        _LAST_STDERR_TAIL[label] = f"subprocess error: {exc!r}"
        return 1
    dur = time.time() - started
    print(f"[predictor] [{label}] exit={rc} ({dur:.1f}s)", flush=True)
    return rc


# T2.45 #3: record Railway step failures to Supabase `system_errors` so
# the dashboard's `/api/health` (and the planned ops-health card) can
# surface a degraded predictor without operators having to scrape Railway
# logs.  Mirrors the `record_err` convention from .github/workflows/daily.yml.
#
# Best-effort: this helper itself fail-opens.  A Supabase outage cannot
# be allowed to escalate into a worker crash -- the worker must keep
# polling regardless.  If the recorder errors, it logs to stderr and
# returns silently so the cycle continues.
def _record_step_failure(step: str, exit_code: int, message: str = "") -> None:
    """Write one system_errors row for a failed predictor step.

    `step`        — short label (e.g. "predict", "grade-today", "scrape-dk")
    `exit_code`   — non-zero RC from `run()`; 124 means timeout
    `message`     — short human-readable hint (auto-derived from
                    _LAST_STDERR_TAIL if empty)
    """
    if exit_code == 0:
        return
    try:
        # Lazy import + path insert -- mirrors the pregame-alert step's
        # pattern -- so the worker boots even if supabase-py is missing.
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from db.supabase_writer import mirror_system_error
        # T2.47: if no explicit message, prefer the captured stderr tail
        # over the opaque "rc=N" placeholder -- much more useful for ops.
        if not message:
            stderr_tail = _LAST_STDERR_TAIL.get(step, "").strip()
            if stderr_tail:
                message = stderr_tail
        msg = message.strip() if message else (
            "timeout" if exit_code == 124 else f"rc={exit_code}"
        )
        mirror_system_error(
            captured_at_utc = datetime.utcnow().isoformat(timespec="seconds") + "Z",
            iso_date        = today_iso(),
            step            = step,
            exit_code       = exit_code,
            message         = msg[:1500],
        )
    except Exception as exc:    # noqa: BLE001 — fail-open per worker contract
        print(f"[predictor] _record_step_failure({step!r}, rc={exit_code}) "
              f"failed: {exc!r}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Cycle steps
# ---------------------------------------------------------------------------

def step_catch_up_grade_yesterday() -> int:
    """Soft-fail: if the nightly grade missed (cron drift), pick it up here."""
    return run(
        ["python", "mlb_first_inning_predictor.py", "--date", yesterday_iso(), "--grade"],
        TIMEOUT_GRADE, "grade-yesterday",
    )


def step_live_grade_today() -> int:
    """Soft-fail: live-grade today's completed 1sts so wins/losses
    surface within ~5 min of the inning ending."""
    return run(
        ["python", "mlb_first_inning_predictor.py", "--date", today_iso(), "--grade"],
        TIMEOUT_GRADE, "grade-today",
    )


def step_predict_today() -> int:
    """Hard-fail: if predict errors, abort the cycle (don't proceed to
    odds import with stale picks)."""
    return run(
        ["python", "mlb_first_inning_predictor.py"],
        TIMEOUT_PREDICT, "predict",
    )


def step_scrape_dk() -> int:
    """Soft-fail: if DK is unreachable, last-known odds stay in CSV.

    T2.56: scrape-dk is DISABLED BY DEFAULT on Railway because DK's
    sportsbook-nash CDN consistently times out non-residential IPs
    (Railway egress sees 100% read-timeout failure rate -- 60+ errors
    per day pre-fix).  T2.55 attempted a header-fingerprint upgrade
    + transport switch (urllib -> requests with HTTP/1.1); didn't
    help.  IP-level filtering is the actual cause.

    Net effect of skipping: odds refresh cadence drops from "every
    5 min when DK is reachable" to "hourly via GHA cron".  GHA's
    IPs aren't blocked, so its scrapes succeed; Railway picks up
    the fresh CSV on next deploy.  For 1st-inning markets, hourly
    cadence is fine -- DK rarely moves the line meaningfully in
    5-min windows, and the meaningful moves (lineup post, scratch)
    happen on hourly-ish boundaries anyway.

    Override: set PREDICTOR_SCRAPE_DK=enabled to re-enable (e.g. if
    DK ever stops blocking Railway IPs, or for a non-Railway deploy
    of this loop).
    """
    mode = os.environ.get("PREDICTOR_SCRAPE_DK", "skip").strip().lower()
    if mode != "enabled":
        # Silent no-op.  No system_errors row, no Telegram ops_health
        # ping, just a single log line per cycle so the operator can
        # verify the skip is intentional.
        print(
            "[predictor] [scrape-dk] skipped on Railway "
            "(set PREDICTOR_SCRAPE_DK=enabled to override)",
            flush=True,
        )
        return 0
    return run(
        ["python", "scrape_dk_odds.py"],
        TIMEOUT_SCRAPE, "scrape-dk",
    )


def step_import_odds() -> int:
    """Soft-fail.  If today's DK CSV is missing (scrape failed or no
    markets posted yet), skip silently."""
    odds_csv = REPO_ROOT / "data" / "odds" / f"dk_{today_iso()}.csv"
    if not odds_csv.exists() or odds_csv.stat().st_size == 0:
        print(f"[predictor] [import-odds] skipped (no {odds_csv.name})", flush=True)
        return 0
    return run(
        [
            "python", "mlb_first_inning_predictor.py",
            "--import-odds", str(odds_csv),
            "--min-edge",    MIN_EDGE,
        ],
        TIMEOUT_IMPORT, "import-odds",
    )


def step_pregame_alert_check() -> int:
    """T2.38 #2: scan today's STRONG bets for any whose first pitch is
    inside the pre-game alert window (25-35 min from now in ET).  Ping
    the user once per bet via notifications_log dedup.  Soft-fail."""
    try:
        # Import lazily so the worker boots even if tracker can't
        # (e.g. supabase-py missing in a stripped image).
        sys.path.insert(0, str(REPO_ROOT))
        import csv
        from datetime import datetime as _dt, timedelta as _td
        from zoneinfo import ZoneInfo as _ZI
        from tracker import _notify_strong_pregame_telegram

        et = _ZI("America/New_York")
        now_et = _dt.now(et)
        today_iso = now_et.strftime("%Y-%m-%d")
        # Sweep CSV for today's rows.  We could query Supabase too;
        # CSV is fine because predict step at the start of this cycle
        # just refreshed it.
        picks_csv = REPO_ROOT / "data" / f"picks_{today_iso[:4]}.csv"
        if not picks_csv.exists():
            return 0
        pinged = 0
        with open(picks_csv, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("date") or "") != today_iso:
                    continue
                if (r.get("pick_strength") or "").upper() != "STRONG":
                    continue
                if (r.get("bet_placed") or "").upper() != "Y":
                    continue
                # Parse "7:35 PM ET" -> ET datetime today.
                tstr = (r.get("game_time_et") or "").replace(" ET", "").strip()
                if ":" not in tstr:
                    continue   # placeholder like "After Game 1"
                try:
                    t = _dt.strptime(tstr, "%I:%M %p")
                    game_dt = now_et.replace(
                        hour=t.hour, minute=t.minute, second=0, microsecond=0,
                    )
                except ValueError:
                    continue
                # Window: 25-35 min from now.  Guarantees one cycle hits
                # it (predictor cron runs every 5 min, so window of 10
                # min always overlaps at least one tick).
                delta = game_dt - now_et
                if _td(minutes=25) <= delta <= _td(minutes=35):
                    minutes_to_first_pitch = int(delta.total_seconds() / 60)
                    _notify_strong_pregame_telegram(r, minutes_to_first_pitch)
                    pinged += 1
        if pinged:
            print(f"[predictor] [pregame] fired {pinged} pre-game alerts", flush=True)
        return 0
    except Exception as exc:    # noqa: BLE001 — advisory only
        print(f"[predictor] [pregame] failed: {exc!r}", file=sys.stderr, flush=True)
        return 1


def cycle() -> None:
    """Run one full cycle.  Each step's exit code is logged but only
    `predict` aborts on failure -- the others soft-fail so a transient
    error in one piece doesn't kill the whole run.

    T2.45 #3: every non-zero RC is also written to Supabase
    `system_errors` so the dashboard surfaces a degraded predictor."""
    started = time.time()
    print(f"[predictor] === cycle start {datetime.now(ET).strftime('%H:%M:%S %Z')} ===", flush=True)

    # 1+2: grade yesterday + today (both soft-fail)
    rc = step_catch_up_grade_yesterday()
    if rc != 0:
        _record_step_failure("grade-yesterday", rc)
    rc = step_live_grade_today()
    if rc != 0:
        _record_step_failure("grade-today", rc)

    # 3: predict today (hard-fail)
    rc = step_predict_today()
    if rc != 0:
        _record_step_failure("predict", rc)
        print(f"[predictor] predict failed (rc={rc}); skipping odds steps this cycle",
              file=sys.stderr, flush=True)
        return

    # 4: scrape DK (soft-fail)
    rc = step_scrape_dk()
    if rc != 0:
        _record_step_failure("scrape-dk", rc)

    # 5: import odds (soft-fail; skipped if no CSV)
    rc = step_import_odds()
    if rc != 0:
        _record_step_failure("import-odds", rc)

    # 6: T2.38 #2 pre-game alert sweep (soft-fail).  Run after odds
    # are imported so the message body has the real DK price/edge.
    rc = step_pregame_alert_check()
    if rc != 0:
        _record_step_failure("pregame-alert", rc)

    dur = time.time() - started
    print(f"[predictor] === cycle end ({dur:.1f}s) ===", flush=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--once", action="store_true",
        help="Run one cycle and exit (smoke test)",
    )
    args = parser.parse_args()

    print(
        f"[predictor] starting, interval={INTERVAL_S}s "
        f"quiet={QUIET_INTERVAL_S}s active_hours_ET={ACTIVE_HOURS} "
        f"min_edge={MIN_EDGE}",
        flush=True,
    )

    running = True
    def stop(*_a):
        nonlocal running
        running = False
        print("[predictor] shutdown requested; will exit after current cycle", flush=True)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT,  stop)

    while running:
        if not is_active_hour():
            now_et = datetime.now(ET).strftime("%H:%M %Z")
            print(f"[predictor] {now_et} outside active window; sleeping {QUIET_INTERVAL_S}s", flush=True)
            if args.once:
                break
            time.sleep(QUIET_INTERVAL_S)
            continue

        cycle()
        if args.once:
            break
        time.sleep(INTERVAL_S)

    print("[predictor] shutdown clean", flush=True)


if __name__ == "__main__":
    main()

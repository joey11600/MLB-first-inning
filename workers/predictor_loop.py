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
  6. Lock commit                          (soft-fail; T8.18, off by default)

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
  NRFI_LOCK_COMMIT                 (default "skip"; set to "enabled" to let
                                    tools/lock_commit.py commit STRONG picks
                                    at T-60 when no fresh DK price arrived.
                                    T8.18 PART 2 -- read by the subprocess,
                                    not by this loop.)
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


def step_sync_csv_from_supabase() -> int:
    """T-V21-2026-05-07d: pull non-predict-owned columns (odds, edges,
    grade results, bet placement) from Supabase into the container's
    local CSV before grade-today runs.  Closes the gap where a fresh
    container reads bet_placed='' from the git CSV and grade_picks's
    notifier short-circuits even though Supabase has the row's
    bet_placed='Y' from earlier import_odds + worker writes.  Soft-fail."""
    return run(
        [
            "python", "tools/sync_csv_from_supabase.py",
            "--days", "8",
        ],
        # 60s is plenty: the script does one Supabase SELECT + one CSV
        # rewrite, no network egress beyond that.
        60, "sync-csv-from-supabase",
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


def step_fetch_odds_api() -> int:
    """T8.31: buy DK's price from The Odds API instead of scraping DK.

    WHY THIS EXISTS.  2026-08-11 DraftKings began 403'ing Railway's egress
    IP -- the same block that killed the Contabo box -- and Railway was the
    ONLY working odds source, so the money path lost prices entirely.  It
    is not the code: curl_cffi's Chrome TLS impersonation IS running (its
    `raise_for_status` is the only thing that formats `HTTP Error 403: `),
    subcategory 20150 still returns a full payload, and the identical code
    from a residential IP returns 15 clean rows.  Four redeploys were all
    blocked, so unlike 2026-08-06 this is not one unlucky ephemeral IP.

    STILL DRAFTKINGS' NUMBER, DELIBERATELY.  `--book draftkings` is not
    optional here.  The published No.1 record is a DK-priced series and
    stakes move ~17% per 10 cents, with the win-loss line itself changing
    at +/-20c because nights the system refuses become bets.  Buying DK's
    price from an aggregator keeps the record continuous; buying anyone
    ELSE'S silently makes it a different product.  Without `--book` the
    tool emits every US book and `import_odds` applies them in file order,
    so the last book in the file would become the ledger's price.

    WRITES THE FILE `step_import_odds` ALREADY READS, so the import path
    is unchanged and this is one step, not a pipeline rewrite.

    THE WINDOW IS THE COST CONTROL.  Every event costs a credit and this
    loop runs every 5 minutes, so fetching the whole card each cycle would
    spend ~180 credits an hour on markets that mostly do not exist yet:
    DK posts a first-inning line a median 63 min before ITS OWN first
    pitch.  `--within-minutes` restricts each cycle to games actually
    approaching their lock, and `--skip-started` drops games whose first
    inning is already being played.  `--merge` then keeps the earlier
    games' captured prices in the file rather than overwriting them.

    OFF BY DEFAULT, exactly like `PREDICTOR_SCRAPE_DK`, so the money path
    does not change until an operator sets the variable -- and can be
    killed from Railway's dashboard without a deploy.
    """
    mode = os.environ.get("PREDICTOR_ODDS_API", "skip").strip().lower()
    if mode != "enabled":
        print("[predictor] [odds-api] skipped "
              "(set PREDICTOR_ODDS_API=enabled to override)", flush=True)
        return 0
    if not (os.environ.get("ODDS_API_KEY") or "").strip():
        # Distinct from the skip above: someone turned this ON and the key
        # is missing, which is a misconfiguration worth seeing every cycle
        # rather than a silent no-op that looks like "no markets yet".
        print("[predictor] [odds-api] ENABLED but ODDS_API_KEY is unset; "
              "no odds will be captured", file=sys.stderr, flush=True)
        return 0
    odds_csv = REPO_ROOT / "data" / "odds" / f"dk_{today_iso()}.csv"
    return run(
        [
            "python", "tools/fetch_odds_api.py",
            "--book",           os.environ.get("ODDS_API_BOOK", "draftkings"),
            "--within-minutes", os.environ.get("ODDS_API_WINDOW_MIN", "120"),
            "--min-credits",    os.environ.get("ODDS_API_MIN_CREDITS", "50"),
            "--skip-started",
            "--merge",
            "--output",         str(odds_csv),
        ],
        TIMEOUT_SCRAPE, "odds-api",
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


def step_lock_commit() -> int:
    """T8.18 PART 2: commit STRONG picks whose T-60 lock window has opened
    but for which no fresh DraftKings price arrived, so the sizing step
    inside `_apply_odds_to_row` never ran.  Soft-fail.

    RUNS AFTER import-odds, ON PURPOSE.  Predict runs BEFORE import-odds
    in `cycle()`, so committing any earlier (e.g. inside `log_picks`)
    would set bet_placed="Y" ahead of the price landing, and
    `_apply_odds_to_row`'s T2.23 early-return would then DISCARD the very
    price that just arrived -- on 64% of STRONG rows.

    Also runs BEFORE the pre-game alert sweep so a pick that commits on
    this tick can still get its T-30 ping on the same tick.

    OFF unless NRFI_LOCK_COMMIT=enabled is set in the Railway service
    environment (not in the repo -- same pattern as PREDICTOR_SCRAPE_DK).
    Unset, the tool prints one line and exits 0.  `tools/lock_commit.py`
    is soft-fail by contract and always exits 0 anyway; the RC check
    below exists for the timeout/crash case that `run()` synthesizes.
    """
    return run(
        ["python", "tools/lock_commit.py"],
        # One ledger read, at most a handful of Kelly evaluations, one
        # atomic CSV write and a few Supabase/Telegram calls.  60s is
        # the same headroom step_reconcile gets for comparable work.
        60, "lock-commit",
    )


def step_reconcile() -> int:
    """T-V21-2026-05-07e: continuous reconciliation sweep.  Asserts
    data + notification invariants over the recent window and
    auto-heals violations.  Runs every cycle so any drift
    introduced by a writer race / mid-cycle redeploy / etc. heals
    within the next 5 min.  Idempotent and dedup-protected --
    healthy slates are no-ops.  Soft-fail."""
    return run(
        ["python", "tools/reconcile.py", "--days", "3"],
        # Cheap query (~2 selects + a handful of patches).  60s is
        # huge headroom for the worst case.
        60, "reconcile",
    )



def step_discord_broadcasts() -> int:
    """SUBSCRIBER BROADCASTS (2026-08-06).  Evaluate the four nightly
    Discord posts and send whichever is due.  Soft-fail, in-process.

    WHY IN-PROCESS RATHER THAN A SUBPROCESS like every other step: the
    triggers need the slate and the No.1 rule, which are already loaded
    here, and a subprocess would pay full interpreter + tracker import
    on every 5-minute cycle for a job that is a no-op ~95% of the time.

    SAFETY, BECAUSE THIS SHARES THE MONEY PATH'S PROCESS:
      * discord_broadcasts.run_broadcasts() catches (Exception,
        SystemExit) internally and returns 0.  The extra try here is
        belt-and-braces, not the primary guard.
      * The import itself is inside the try: a syntax error or a missing
        module in the marketing code must not stop predict/grade/odds.
      * Dedupe lives in Supabase (notifications_log), so the ~20 Railway
        redeploys a day cannot re-post a board.
      * Default mode is `preview`: it renders to the log and sends
        NOTHING until DISCORD_BROADCASTS=live is set.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        import discord_broadcasts
        n = discord_broadcasts.run_broadcasts()
        if n:
            print(f"[predictor] [discord] {n} broadcast(s) handled", flush=True)
        return 0
    except (Exception, SystemExit) as exc:   # noqa: BLE001
        print(f"[predictor] [discord] step failed ({exc!r}); continuing",
              file=sys.stderr, flush=True)
        return 0    # NEVER surface as a cycle failure -- marketing is not money


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

    # T-V21-2026-05-07d: sync CSV from Supabase BEFORE grade-today.
    # The container's local CSV starts every redeploy from git, which
    # never has bet_placed='Y' for today's bets (only Railway's
    # import_odds writes that, and it lives only in this container's
    # filesystem).  Without the pull, grade_picks reads bet_placed=''
    # and _notify_strong_graded_telegram early-returns at its
    # bet_placed=Y guard -- so STRONG WIN/LOSS pings don't fire after
    # a mid-day redeploy.  Pull from Supabase first so the in-memory
    # CSV reflects the worker's + import_odds's authoritative state
    # for graded + bet-placed columns.
    rc = step_sync_csv_from_supabase()
    if rc != 0:
        _record_step_failure("sync-csv-from-supabase", rc)

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

    # 4: scrape DK (soft-fail).
    #
    # T3.11-AUDIT 2026-05-03: do NOT write scrape-dk failures to system_errors
    # from the Railway predictor.  Per T2.56, Railway egress IPs are blocked
    # by DK's CDN -- scrape attempts time out 100% of the time when
    # PREDICTOR_SCRAPE_DK is overridden to "enabled".  GHA's hourly cron
    # is the canonical source of odds; its scrape failures DO write to
    # system_errors (legitimate signal).  86 noise rows in 7 days were
    # cluttering the Ops Health card without surfacing actionable info.
    rc = step_scrape_dk()
    if rc != 0:
        # Log to stderr only -- visible in Railway logs, invisible in the
        # dashboard's Ops Health card.  Set PREDICTOR_SCRAPE_DK=skip (the
        # default) to suppress these too.
        print(f"[predictor] [scrape-dk] failed (rc={rc}); GHA hourly cron "
              f"is canonical, skipping system_errors write",
              file=sys.stderr, flush=True)

    # 4b: T8.31 -- buy DK's price from The Odds API (soft-fail).
    #
    # AFTER scrape-dk and BEFORE import-odds, and both halves matter.
    # After, so that on any host where the direct scrape still works its
    # file is written first and this run merges onto it rather than the
    # reverse. Before, because import-odds is what turns the file into
    # ledger rows -- a fetch landing after it would sit unimported until
    # the next cycle, five minutes closer to the lock.
    rc = step_fetch_odds_api()
    if rc != 0:
        # Soft-fail like scrape-dk: a missed fetch leaves last-known odds
        # in place and the next cycle retries five minutes later. It DOES
        # write system_errors, unlike scrape-dk, because this is the
        # canonical source now -- a persistent failure here means the
        # slate goes unpriced and the operator needs to see it.
        _record_step_failure("odds-api", rc)

    # 5: import odds (soft-fail; skipped if no CSV)
    rc = step_import_odds()
    if rc != 0:
        _record_step_failure("import-odds", rc)

    # 6: T8.18 lock commit (soft-fail).  BETWEEN import-odds and the
    # pre-game sweep, and that position is the whole design: after the
    # import so a fresh price is never discarded by the T2.23 freeze,
    # before the pre-game alert so a pick that commits on this tick can
    # still be picked up by the T-30 ping on the same tick.
    rc = step_lock_commit()
    if rc != 0:
        _record_step_failure("lock-commit", rc)

    # 7: T2.38 #2 pre-game alert sweep (soft-fail).  Run after odds
    # are imported so the message body has the real DK price/edge.
    rc = step_pregame_alert_check()
    if rc != 0:
        _record_step_failure("pregame-alert", rc)

    # 8: T-V21-2026-05-07e -- reconciliation sweep.  Continuously asserts
    # the system's data-integrity + notification invariants over the
    # recent window and auto-heals violations.  Catches anything the
    # rest of the pipeline missed (orphan bets, stale fallback pl,
    # missed strong_locked / strong_graded telegram sends, etc.).
    # Idempotent and dedup-protected -- a healthy slate is a no-op.
    rc = step_reconcile()
    if rc != 0:
        _record_step_failure("reconcile", rc)

    # 9: subscriber Discord broadcasts.  LAST on purpose -- everything
    # above it owns money or data, this owns marketing, and it must
    # never be able to delay them.  Always returns 0.
    step_discord_broadcasts()

    # 10: the watchdog.  Runs HERE, on Railway, because a monitor must not
    # share a failure domain with what it watches -- the GitHub-hosted
    # watchdog went down with GitHub Actions on 2026-08-06 and could not
    # report the very outage it existed for.  Watches GitHub, Vercel, the
    # odds pipeline and tonight's No.1; pings an external dead-man's
    # switch so Railway's OWN death is detectable.  Always returns 0.
    try:
        import watchdog as _wd
        _wd.run()
    except (Exception, SystemExit) as exc:   # noqa: BLE001
        print(f"[predictor] [watchdog] step failed ({exc!r}); continuing",
              file=sys.stderr, flush=True)

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

        # FIXED-PERIOD SCHEDULE, NOT FIXED-SLEEP (2026-08-06).
        #
        # This used to be `cycle(); time.sleep(INTERVAL_S)`, which makes
        # the real period `cycle_duration + INTERVAL_S`, not INTERVAL_S.
        # The cycle does predict + grade + scrape + import + reconcile
        # with subprocess timeouts up to 300s each, so on a slow night
        # the "5-minute loop" silently became a 10-minute loop -- and
        # every step that got slower pushed every downstream alert
        # later. Operator, 2026-08-06: "the telegram notifications for
        # some reason are all lagged."
        #
        # Sleeping the REMAINDER keeps the cadence honest: a cycle that
        # takes 90s sleeps 210s, and the next cycle still starts on the
        # 5-minute mark. A cycle that overruns the whole interval starts
        # the next one immediately rather than compounding the debt.
        started_at = time.monotonic()
        cycle()
        if args.once:
            break
        elapsed = time.monotonic() - started_at
        remaining = INTERVAL_S - elapsed
        if remaining > 0:
            time.sleep(remaining)
        else:
            print(f"[predictor] cycle took {elapsed:.0f}s (> {INTERVAL_S}s "
                  f"interval); starting next immediately", flush=True)

    print("[predictor] shutdown clean", flush=True)


if __name__ == "__main__":
    main()

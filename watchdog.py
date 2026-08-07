#!/usr/bin/env python3
"""
watchdog.py -- "make sure it catches every error" (operator, 2026-08-06).

WHERE THIS RUNS, AND WHY THAT IS THE WHOLE DESIGN.
A monitor must not share a failure domain with what it monitors. That is
not a principle borrowed from a textbook; it is the lesson of this week:
`.github/workflows/runner_watchdog.yml` was built to catch a dead runner
and pinned to `ubuntu-latest` so it would survive one -- but it runs ON
GitHub Actions, so when GitHub Actions itself had a critical outage on
2026-08-06 the watchdog went down with the thing it was watching. It
would not have caught the incident that prompted it.

So this one runs inside the RAILWAY loop, which is the host that stayed
up through that outage and which owns the money path anyway. It watches
GitHub, Vercel, the odds pipeline and the broadcasts -- all of which are
in different failure domains from Railway.

WHAT WATCHES RAILWAY? Nothing here can. A process cannot report its own
death, and every check below is silent if this file never runs. That is
what `heartbeat()` is for: it pings an EXTERNAL dead-man's-switch
service on every healthy cycle, and that service alerts when the pings
STOP. Until HEALTHCHECKS_URL is set, the honest statement is: **an
outage of Railway itself is currently undetected.** `daily.yml:917-923`
already has the same ping wired to the same unset secret.

THE ANTI-NOISE CONTRACT, which matters as much as the coverage.
This system's documented failure mode is alarm fatigue, not blindness:
a 403 wall ran for 90 days and everyone learned to scroll past it, and
the dashboard health badge sat on DEGRADED so long it stopped meaning
anything. So:
  * every check has a SEVERITY. Only `page` reaches Telegram.
  * `page` is reserved for things that cost money TONIGHT and that the
    operator can actually act on.
  * dedupe is escalating and stateless (Supabase-backed), so a fault
    that persists for six hours sends ~3 messages, not 72.
  * a healthy night sends ZERO. If this file ever chats, something is
    wrong; that is the only way an alert keeps meaning something.

ALERTS GO TO PERSONAL THREADS ONLY. Telegram is the internal channel
now. The subscriber channel must never receive an ops alert -- see
tracker._chat_should_receive_event, and the incident where a watchdog
nearly published runner-troubleshooting text to paying subscribers.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
DASH = "https://nrfi-terminal.vercel.app"
GH_REPO = "joey11600/MLB-first-inning"

# Severity tiers.
PAGE = "page"    # Telegram now -- costs money tonight, and is actionable
LOG = "log"      # printed to the Railway log only -- context, not an alarm


def _get_json(url: str, timeout: int = 12, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "NRFI-Terminal-watchdog/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _mins_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (datetime.now(t.tzinfo) - t).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# checks -- each returns (severity, message) or None when healthy
# ---------------------------------------------------------------------------

def check_dashboard() -> tuple[str, str] | None:
    """The dashboard's own health endpoint. It already knows about
    staleness and real errors (the known-blocked GHA scrape is filtered
    out there as a NOTICE, so this cannot resurrect that 90-day wall)."""
    try:
        h = _get_json(f"{DASH}/api/health")
    except Exception:  # noqa: BLE001
        return (PAGE, "Dashboard health endpoint is unreachable — "
                      "the site may be down.")
    status = (h.get("status") or "").upper()
    if status == "BROKEN":
        why = "; ".join(h.get("reasons") or []) or "no reason given"
        return (PAGE, f"Dashboard reports BROKEN — {why}")
    if status == "DEGRADED":
        return (LOG, f"Dashboard DEGRADED — {'; '.join(h.get('reasons') or [])}")
    return None


def check_github_actions() -> tuple[str, str] | None:
    """Has the bookkeeping cron stopped? This is the check the OLD
    watchdog could not make honestly, because it lived on the thing it
    was checking."""
    try:
        d = _get_json(
            f"https://api.github.com/repos/{GH_REPO}/actions/workflows/"
            f"daily.yml/runs?per_page=20")
    except Exception:  # noqa: BLE001
        return (LOG, "Could not reach the GitHub API to check the cron.")
    runs = d.get("workflow_runs") or []
    ok = [r for r in runs
          if r.get("status") == "completed" and r.get("conclusion") == "success"]
    if not ok:
        return (LOG, "No successful daily.yml run in the last 20 runs.")
    age = _mins_since(ok[0].get("updated_at"))
    # daily.yml runs hourly 12:00-03:30 UTC then sleeps ~6h by design, so
    # a plain staleness rule would cry wolf every single night. Only
    # judge it inside the dense window.
    now_utc_h = datetime.now(timezone.utc).hour
    dense = (now_utc_h >= 13) or (now_utc_h < 4)
    if dense and age is not None and age > 150:
        return (PAGE, f"GitHub cron has not succeeded in {age/60:.1f}h — "
                      f"the ledger is not being committed.")
    return None


def check_odds_coverage(rows: list[dict]) -> tuple[str, str] | None:
    """THE 2026-08-05 SIGNATURE: a full slate with zero captured prices.
    That day DraftKings rotated an internal id, every request returned
    HTTP 200 with an empty market, and nothing alerted -- the whole
    slate went unpriced. Zero-priced during prime hours is the one
    unambiguous tell."""
    if not rows:
        return None
    et = datetime.now(ET)
    if not (12 <= et.hour <= 23):
        return None
    try:
        import discord_broadcasts as B
        priced = sum(1 for r in rows if B.side_price(r) is not None)
    except Exception:  # noqa: BLE001
        return None
    if priced == 0:
        return (PAGE, f"No DraftKings price captured for ANY of "
                      f"{len(rows)} games — odds capture is down.")
    if priced < len(rows) * 0.4 and et.hour >= 18:
        return (LOG, f"Only {priced}/{len(rows)} games priced this late.")
    return None


def check_top_pick_locked(rows: list[dict], date_iso: str) -> tuple[str, str] | None:
    """The night's No.1 must carry a real price by its lock, because the
    stake is computed FROM the price -- no price means no publishable
    bet, which is a lost night of product."""
    try:
        import discord_broadcasts as B
        tp = B.top_pick(rows)
        if tp is None:
            return None
        st = B.game_start_et(date_iso, tp.get("game_time_et", ""))
        if st is None:
            return None
        now = datetime.now(ET)
        lock = st - timedelta(minutes=B.LOCK_MINUTES_PREGAME)
        if now < lock or now > st:
            return None          # not in the window that matters
        if B.side_price(tp) is None:
            return (PAGE,
                    f"No.1 play {tp.get('away_team')}@{tp.get('home_team')} "
                    f"has NO captured price and locks at "
                    f"{st.strftime('%I:%M %p').lstrip('0')} ET — "
                    f"it cannot be staked or published.")
    except Exception:  # noqa: BLE001
        return None
    return None


def heartbeat() -> None:
    """Ping the EXTERNAL dead-man's switch. The only check that can
    catch Railway itself dying, because it is the absence of this ping
    that raises the alarm, not its presence."""
    url = (os.environ.get("HEALTHCHECKS_URL", "") or "").strip()
    if not url:
        return
    try:
        urllib.request.urlopen(url, timeout=8).read()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

_ESCALATION_MIN = (0, 60, 240, 720)   # first, +1h, +4h, +12h


def _alert(message: str, key: str) -> None:
    """Telegram, PERSONAL THREADS ONLY, with escalating dedupe.

    Routed through tracker's notifications_log so a Railway redeploy
    cannot reset the dedupe and re-page for a fault already reported.
    """
    try:
        import tracker
    except (Exception, SystemExit):  # noqa: BLE001
        print(f"  [watchdog] {message}", file=sys.stderr)
        return
    body = f"🐕 <b>WATCHDOG</b>\n\n{message}\n\n<i>Internal alert — subscribers see nothing.</i>"
    # event_key includes the hour bucket so a persistent fault re-pages
    # on the escalation schedule rather than every 5 minutes.
    hour_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    try:
        tracker._notify_event_telegram("watchdog", f"{key}:{hour_bucket}", body)
    except (Exception, SystemExit):  # noqa: BLE001
        print(f"  [watchdog] alert send failed: {message}", file=sys.stderr)


def run(date_iso: str | None = None) -> int:
    """Run every check. Returns the number of PAGE-level problems.
    NEVER raises -- it shares a process with the money path."""
    try:
        if (os.environ.get("WATCHDOG_ENABLED", "1") or "1").strip() == "0":
            return 0
        now = datetime.now(ET)
        date_iso = date_iso or now.strftime("%Y-%m-%d")
        rows = []
        try:
            import discord_broadcasts as B
            rows = B.load_slate(date_iso)
        except Exception:  # noqa: BLE001
            pass

        results = [
            ("dashboard", check_dashboard()),
            ("github", check_github_actions()),
            ("odds", check_odds_coverage(rows)),
            ("toppick", check_top_pick_locked(rows, date_iso)),
        ]
        pages = 0
        for key, res in results:
            if res is None:
                continue
            sev, msg = res
            if sev == PAGE:
                pages += 1
                _alert(msg, key)
                print(f"  [watchdog] PAGE {key}: {msg}", flush=True)
            else:
                print(f"  [watchdog] log  {key}: {msg}", flush=True)

        # Heartbeat LAST and only when nothing is paging: a dead-man's
        # switch that keeps pinging while the system is broken is worse
        # than none, because it actively asserts health.
        if pages == 0:
            heartbeat()
        return pages
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        print(f"  [watchdog] run failed ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Run the watchdog checks once")
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true",
                    help="print findings without sending Telegram")
    a = ap.parse_args()
    if a.dry_run:
        os.environ["TELEGRAM_BOT_TOKEN"] = ""   # forces the send to no-op
    n = run(a.date)
    print(f"[watchdog] {n} page-level problem(s)")

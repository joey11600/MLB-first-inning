#!/usr/bin/env python3
"""tools/demotion_reeval_reminder.py

Cron-friendly reminder for active cluster demotions that are due for
re-evaluation.  Runs on the grade cron alongside the loss cluster
monitor.  Reads `data/cluster_demotions.json` and, for any active
entry whose `reevaluate_after` date is on or before today (ET),
fires a Telegram alert with the current shadow-P&L snapshot so the
operator can decide: keep, flip to inactive, or remove.

WHY THIS EXISTS
---------------
A demotion that should have been a 4-day experiment can quietly turn
into a permanent feature if nobody remembers to look at the shadow
data.  This closes the loop.  Each demotion entry gets a stamped
expiry; the cron pings the operator on/after that date with the
data they need to make the decision.

The reminder is idempotent: dedup is keyed on
`demotion_reeval:<id>:<date>` so a daily run won't spam.  Once the
operator flips `active: false` (or bumps the `reevaluate_after`
date), the reminder stops firing.

Usage:
  python tools/demotion_reeval_reminder.py            # today ET
  python tools/demotion_reeval_reminder.py --date 2026-05-14
  python tools/demotion_reeval_reminder.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")
DEMOTIONS_FILE = ROOT / "data" / "cluster_demotions.json"


def _capture_shadow_pnl(dem_id: str, since: str | None) -> str:
    """Run cluster_shadow_pnl.py for one demotion id and capture its
    stdout so we can paste it into the Telegram body."""
    from tools import cluster_shadow_pnl  # imports at the call site to avoid cycle
    argv = ["--id", dem_id, "--include-inactive"]
    if since:
        argv += ["--since", since]
    # Use argparse via sys.argv hack since the script's main() reads from argparse.
    old_argv = sys.argv[:]
    sys.argv = ["cluster_shadow_pnl.py", *argv]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            cluster_shadow_pnl.main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
    return buf.getvalue()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="ET reference date YYYY-MM-DD (default: today).")
    p.add_argument("--file", default=str(DEMOTIONS_FILE))
    p.add_argument("--dry-run", action="store_true",
                   help="Print reminders to stdout; do not fire Telegram.")
    args = p.parse_args()

    today_iso = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    fp = Path(args.file)
    if not fp.exists():
        print(f"[skip] demotions file not found: {fp}")
        return 0
    try:
        cfg = json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: cannot parse {fp}: {exc}", file=sys.stderr)
        return 2

    demotions = cfg.get("demotions") or []
    if not demotions:
        print("No demotions defined; nothing to remind.")
        return 0

    fired = 0
    skipped = 0
    for dem in demotions:
        dem_id = dem.get("id", "?")
        if not dem.get("active", True):
            skipped += 1
            continue
        due_iso = (dem.get("reevaluate_after") or "").strip()
        if not due_iso:
            skipped += 1
            continue
        if today_iso < due_iso:
            skipped += 1
            print(f"[wait] {dem_id} not due yet (re-eval after {due_iso}, today {today_iso})")
            continue

        # Build the body.  Capture the shadow-PnL output for context.
        shadow_text = _capture_shadow_pnl(dem_id, dem.get("activated_on"))
        body_lines = [
            "⏰ <b>Cluster demotion re-evaluation due</b>",
            f"Cluster: <code>{dem_id}</code>",
            f"Re-eval date: {due_iso}  (today: {today_iso})",
            f"Reason on record: <i>{dem.get('reason','-')}</i>",
            "",
            "<b>Current shadow-P&amp;L snapshot:</b>",
            "<pre>",
            shadow_text.strip() or "(no data yet)",
            "</pre>",
            "",
            "Decision tree:",
            "  • SHADOW &gt;= 5W-2L (clear winners skipped)  → flip <code>active: false</code> in cluster_demotions.json (we over-corrected)",
            "  • SHADOW &lt;= 2W-5L (clear losers skipped)   → keep on; bump <code>reevaluate_after</code> by ~7 days",
            "  • SHADOW ~ break-even / small sample        → bump <code>reevaluate_after</code> by ~4 days, wait for more data",
            "",
            tracker._dashboard_link(today_iso),
        ]
        body = "\n".join(body_lines)
        if args.dry_run:
            print(f"\n[dry-run] reminder for {dem_id} would fire:")
            print(body)
            fired += 1
            continue

        event_key = f"demotion_reeval:{dem_id}:{today_iso}"
        sent = tracker._notify_event_telegram("demotion_reeval", event_key, body)
        print(f"  {dem_id}: reminder {'sent' if sent else 'NOT sent (dedup or no creds)'}")
        if sent:
            fired += 1

    if fired == 0 and skipped == len(demotions):
        print(f"No re-evals due today.  ({skipped} demotions inspected, all current.)")
    elif fired > 0:
        print(f"\n{fired} reminder(s) fired, {skipped} not-yet-due.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

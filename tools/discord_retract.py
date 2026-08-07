#!/usr/bin/env python3
"""
discord_retract.py -- take a bad broadcast back out of the channel.

WHY THIS EXISTS. On 2026-08-06, the first live night of the Discord
product, two defects shipped to paying subscribers: a missing dedupe
window published THE BOARD and the No.1 play three times each, and the
operator's private dashboard URL appeared in four delivered posts. Every
one of them had to be deleted by hand, because the transport threw away
the message id that `?wait=true` hands back on every successful post.

A published mistake you cannot un-publish is a different class of problem
from a mistake you can. This tool closes that gap.

USAGE
    # what is live for tonight's board?
    python tools/discord_retract.py --list --date 2026-08-06

    # take it down
    python tools/discord_retract.py --event board --date 2026-08-06

    # take down the No.1 (its key carries the gamePk, so it is looked up)
    python tools/discord_retract.py --event toppick --date 2026-08-06

    # exact key, when you already know it
    python tools/discord_retract.py --key "board:2026-08-06" --type discord_board

RETRACTING DOES NOT LET IT RE-POST. The dedupe record in
notifications_log is deliberately left alone, so the scheduler will not
treat the broadcast as un-sent and publish it again on its next cycle.
Retract and then re-send is a two-step decision on purpose -- an
automatic replacement is how a formatting bug becomes a post loop. To
deliberately replace one, use:

    python -m discord_broadcasts --resend toppick --date 2026-08-06
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")

# event name -> (event_type, how to build the key for a date)
_EVENTS = {
    "board":   "discord_board",
    "toppick": "discord_toppick",
    "final":   "discord_final",
    "ledger":  "discord_ledger",
}


def _resolve_key(event: str, date_iso: str) -> str | None:
    """The No.1's key carries the gamePk, so it cannot be built from the
    date alone -- look up whichever key was actually recorded."""
    if event in ("board", "final", "ledger"):
        return f"{event}:{date_iso}"
    try:
        from db.supabase_writer import _get_client
        client = _get_client()
        if client is None:
            return None
        res = (client.table("discord_messages")
                     .select("event_key")
                     .eq("event_type", _EVENTS[event])
                     .like("event_key", f"%{date_iso}%")
                     .limit(1).execute())
        rows = res.data or []
        return rows[0]["event_key"] if rows else None
    except Exception as exc:  # noqa: BLE001
        print(f"lookup failed: {exc!r}", file=sys.stderr)
        return None


def _list_live(date_iso: str) -> int:
    try:
        from db.supabase_writer import _get_client
        client = _get_client()
        if client is None:
            print("Supabase unavailable.", file=sys.stderr)
            return 1
        res = (client.table("discord_messages")
                     .select("*")
                     .like("event_key", f"%{date_iso}%")
                     .order("posted_at_utc", desc=False).execute())
    except Exception as exc:  # noqa: BLE001
        print(f"lookup failed: {exc!r}", file=sys.stderr)
        return 1
    rows = res.data or []
    if not rows:
        print(f"No indexed Discord messages for {date_iso}.")
        print("(Anything posted before the id-capture shipped is not "
              "listed here and must be deleted by hand.)")
        return 0
    print(f"{len(rows)} indexed message(s) for {date_iso}:\n")
    for r in rows:
        t = r.get("posted_at_utc") or ""
        try:
            t = (datetime.fromisoformat(t.replace("Z", "+00:00"))
                 .astimezone(ET).strftime("%I:%M %p ET"))
        except Exception:  # noqa: BLE001
            pass
        state = "RETRACTED" if r.get("retracted_at_utc") else "live"
        print(f"  {t:14s} {r.get('event_type'):16s} "
              f"part {r.get('part_index')}/{r.get('part_total')}  "
              f"{state:9s}  id={r.get('message_id')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Delete a Discord broadcast from the subscriber channel")
    ap.add_argument("--date", default=datetime.now(ET).strftime("%Y-%m-%d"))
    ap.add_argument("--event", choices=sorted(_EVENTS),
                    help="Which broadcast to retract")
    ap.add_argument("--key", help="Exact event_key (with --type)")
    ap.add_argument("--type", help="Exact event_type (with --key)")
    ap.add_argument("--list", action="store_true",
                    help="Show what is live for the date and exit")
    ap.add_argument("--test-webhook", action="store_true",
                    help="Operate against DISCORD_TEST_WEBHOOK_URL")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the confirmation prompt")
    a = ap.parse_args()

    if a.list:
        return _list_live(a.date)

    if a.key and a.type:
        event_type, event_key = a.type, a.key
    elif a.event:
        event_type = _EVENTS[a.event]
        event_key = _resolve_key(a.event, a.date)
        if not event_key:
            print(f"No indexed messages for {a.event} on {a.date}. "
                  f"Try --list.", file=sys.stderr)
            return 1
    else:
        ap.error("give --event, or --key with --type, or --list")
        return 2

    import discord_notify
    if not a.yes:
        # Deleting from a channel subscribers are paying for is not a
        # thing to do on a typo.
        print(f"About to delete every live message for "
              f"{event_type} / {event_key}")
        print("Re-run with --yes to actually do it.")
        return 0

    gone = discord_notify.retract(event_type, event_key, test=a.test_webhook)
    print(f"[retract] {gone} message(s) removed from the channel.")
    return 0 if gone else 1


if __name__ == "__main__":
    raise SystemExit(main())

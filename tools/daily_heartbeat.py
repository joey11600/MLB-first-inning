#!/usr/bin/env python3
"""
tools/daily_heartbeat.py — once-a-day "system is alive + today's plan"
Telegram heartbeat.

Motivation: on 2026-07-13 (MLB All-Star break, 0 games) the pipeline
correctly produced 0 picks and sent 0 Telegram alerts.  From the
operator's chair that was indistinguishable from a dead system --
silence meant both "nothing to bet today" AND "the predictor crashed".
This heartbeat removes the ambiguity: every day it sends ONE message
stating how many games are on today's slate and how many STRONG / LEAN
picks the predictor produced.  A no-games day now says so out loud, so
the rest of that day's silence is confirmed-normal.

Delivery + dedup: sends through tracker._notify_event_telegram with the
key `daily_heartbeat:<date>`.  tracker._DEDUP_WINDOW_M["daily_heartbeat"]
is 18h, so even though the workflow calls this on every predict tick from
late morning on, exactly one heartbeat goes out per day (the first
qualifying tick sends; the rest are deduped no-ops).  "daily_heartbeat"
is not in _SUPERGROUP_ALLOWED_EVENTS, so it lands only in the operator's
DM, never the shared Backfist Bets group.

Run modes:
  python tools/daily_heartbeat.py               # send for today (ET)
  python tools/daily_heartbeat.py --date 2026-07-19
  python tools/daily_heartbeat.py --dry-run     # print body, do NOT send
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

ET = ZoneInfo("America/New_York")


def _count_regular_games(date_iso: str) -> int | None:
    """Count regular-season ('R') games on the MLB schedule for
    `date_iso`.  Returns None if the schedule fetch fails, so the caller
    can degrade gracefully instead of falsely reporting '0 games'."""
    try:
        import statsapi
    except Exception:      # noqa: BLE001 -- dep missing => degrade
        return None
    try:
        date_str = datetime.strptime(date_iso, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return None
    for attempt, sleep_s in enumerate([0, 2, 5], start=1):
        if sleep_s:
            _time.sleep(sleep_s)
        try:
            raw = statsapi.get("schedule", {"date": date_str, "sportId": 1})
            n = 0
            for date_obj in raw.get("dates", []):
                for game in date_obj.get("games", []):
                    if game.get("gameType") == "R":
                        n += 1
            return n
        except Exception:  # noqa: BLE001 -- retry then degrade
            continue
    return None


def _count_picks(date_iso: str, season: int) -> dict:
    """Tally today's logged picks by strength from the local CSV ledger
    (complete, unlike a capped Supabase read)."""
    from tracker import _csv_path, _read_rows
    out = {"STRONG": 0, "LEAN": 0, "PASS": 0, "total": 0}
    try:
        rows = _read_rows(_csv_path(season))
    except Exception:      # noqa: BLE001
        return out
    for r in rows:
        if (r.get("date") or "").strip() != date_iso:
            continue
        out["total"] += 1
        strength = (r.get("pick_strength") or "").strip().upper()
        if strength == "STRONG":
            out["STRONG"] += 1
        elif strength == "LEAN":
            out["LEAN"] += 1
        else:
            out["PASS"] += 1
    return out


def build_body(date_iso: str, season: int) -> str:
    from tracker import _dashboard_link
    games = _count_regular_games(date_iso)
    picks = _count_picks(date_iso, season)

    lines = [f"☀️ <b>{date_iso}</b> · morning check"]

    if games == 0:
        lines.append(
            "No MLB regular-season games today — expect no picks and "
            "no alerts. (Off-day / All-Star break / off-season.)"
        )
    else:
        slate = (f"{games} game(s) today" if games is not None
                 else f"{picks['total']} game(s) on today's board")
        lines.append(
            f"{slate} · picks: <b>{picks['STRONG']} STRONG</b>, "
            f"{picks['LEAN']} LEAN, {picks['PASS']} pass"
        )
        if games and picks["total"] == 0:
            lines.append(
                "⚠️ Games are scheduled but 0 picks logged yet. If "
                "this is still true by midday, the predictor may be stuck."
            )

    lines.append("System heartbeat ✓")
    lines.append("")
    lines.append(_dashboard_link(date_iso))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="ISO date YYYY-MM-DD (default: today ET)")
    ap.add_argument("--season", type=int, help="Season year (default: from date)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the message body and exit without sending")
    args = ap.parse_args()

    date_iso = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    season = args.season or int(date_iso[:4])
    body = build_body(date_iso, season)

    if args.dry_run:
        print(body)
        return 0

    from tracker import _notify_event_telegram
    sent = _notify_event_telegram(
        "daily_heartbeat", f"daily_heartbeat:{date_iso}", body
    )
    print(f"daily_heartbeat date={date_iso} sent={sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

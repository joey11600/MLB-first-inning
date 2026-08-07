#!/usr/bin/env python3
"""
discord_notify.py -- the SUBSCRIBER-FACING channel.

THE SPLIT (operator, 2026-08-06): "we're going to only use telegram for
internal use and errors and for the watchdog when problems occurr...
everything is going to be sent to our discord."

    TELEGRAM  = internal.  Errors, ops, the watchdog.  Never subscribers.
    DISCORD   = the product.  Four broadcasts a night, one channel.

WHY A SEPARATE MODULE RATHER THAN MORE OF tracker.py.  tracker.py is the
LEDGER: it grades bets, computes P&L and owns money invariants.  A
formatting bug in a marketing message must never be able to touch that
file.  What IS reused is the dedupe/audit layer
(`_notify_event_dedup_check` / `_notify_event_record`), because a
second, private idea of "did we already send this" is exactly how a
system ends up double-posting to paying subscribers.

DELIVERY GUARANTEES, AND THE ONES WE DELIBERATELY DON'T MAKE:
  • Never raises.  Every public function swallows everything.  A Discord
    outage must not delay predict / grade / odds / lock -- the money
    path shares a process with this code (workers/predictor_loop.py).
  • Silent no-op when DISCORD_WEBHOOK_URL is unset, same contract as the
    Telegram path, so a fresh clone or a local run posts nothing.
  • Dedupe is Supabase-backed and therefore survives a mid-day Railway
    redeploy -- which HAPPENS (one on 2026-08-06 mid-slate).  An
    in-process set would re-post the whole night's board after a
    restart.
  • Discord's hard limit is 2000 characters per message.  A 15-game
    board exceeds that, so `send()` splits on paragraph boundaries and
    posts parts in order, rate-limit aware.  Splitting is NOT a
    formatting nicety: an over-length payload is rejected wholesale.

PREVIEW MODE.  `preview=True` renders and returns the exact payload
without sending and without touching dedupe.  This repo has a history of
code that shipped and never fired (tools/fetch_odds_api.py, the
Healthchecks ping); a broadcast nobody has seen is not done.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# Discord's documented per-message content cap.
DISCORD_CONTENT_LIMIT = 2000
# Leave room for the "(1/3)" continuation marker appended to split parts.
_SPLIT_BUDGET = DISCORD_CONTENT_LIMIT - 40

_TIMEOUT_S = 15
_MAX_ATTEMPTS = 4


def _webhook_url(test: bool = False) -> str:
    """The destination. DISCORD_TEST_WEBHOOK_URL lets a full slate be
    rehearsed against a private channel before subscribers ever see a
    post; falls back to the live webhook only when explicitly asked for
    the live one."""
    if test:
        return (os.environ.get("DISCORD_TEST_WEBHOOK_URL", "") or "").strip()
    return (os.environ.get("DISCORD_WEBHOOK_URL", "") or "").strip()


def is_configured(test: bool = False) -> bool:
    return bool(_webhook_url(test))


def split_message(body: str, limit: int = _SPLIT_BUDGET) -> list[str]:
    """Split on paragraph boundaries, then lines, never mid-line.

    A board post is a table; breaking a row in half is worse than an
    extra message. Falls back to a hard slice only for a single line
    that is itself over the limit (shouldn't happen; defensive)."""
    body = body or ""
    if len(body) <= limit:
        return [body] if body else []

    parts: list[str] = []
    current = ""
    for block in body.split("\n\n"):
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(block) <= limit:
            current = block
            continue
        # Block alone is too long -- split it by lines.
        for line in block.split("\n"):
            cand2 = (current + "\n" + line) if current else line
            if len(cand2) <= limit:
                current = cand2
                continue
            if current:
                parts.append(current)
            while len(line) > limit:          # pathological single line
                parts.append(line[:limit])
                line = line[limit:]
            current = line
    if current:
        parts.append(current)
    return parts


def _post_once(url: str, payload: dict) -> tuple[bool, int, float, dict]:
    """One POST. Returns (ok, status, retry_after_seconds, created).

    `?wait=true` makes Discord return the created message object rather
    than an empty 204, so a 200 here means the message really exists in
    the channel -- not merely that the request was accepted. `created` is
    that object; its `id` is the ONLY handle that can ever delete the
    message again, and throwing it away is why four posts carrying the
    dashboard URL had to be deleted by hand on 2026-08-06.

    `created` is {} whenever the id could not be read -- a 204, an
    unparseable body, a network failure. Callers must treat a missing id
    as "delivered but not retractable", never as "not delivered": the
    message is in the channel either way and a retry would double-post.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url + ("&" if "?" in url else "?") + "wait=true",
        data=data,
        headers={
            "Content-Type": "application/json",
            # No dashboard URL, not even here. Discord requires a
            # User-Agent but nothing about this transport should carry
            # the operator console's address -- see the module docstring
            # in discord_broadcasts.py.
            "User-Agent": "NRFI-Terminal/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            ok = 200 <= resp.status < 300
            created: dict = {}
            try:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw.strip() else {}
                if isinstance(parsed, dict) and parsed.get("id"):
                    created = parsed
            except Exception:  # noqa: BLE001
                # A delivered message we cannot address. Deliberately NOT
                # an error: see the docstring -- retrying would post twice.
                created = {}
            return ok, resp.status, 0.0, created
    except urllib.error.HTTPError as exc:
        # 429 = rate limited. Discord tells us exactly how long to wait.
        retry_after = 0.0
        if exc.code == 429:
            try:
                retry_after = float(json.loads(exc.read().decode()).get("retry_after", 1.0))
            except Exception:  # noqa: BLE001
                retry_after = 2.0
        return False, exc.code, retry_after, {}
    except Exception:  # noqa: BLE001 -- network, DNS, timeout
        return False, 0, 0.0, {}


def _send_raw(body: str, *, test: bool = False) -> tuple[bool, list[dict]]:
    """Post `body`, split if needed. Returns (all_parts_delivered, posted).

    `posted` lists every message that REACHED THE CHANNEL, even when the
    overall result is False. That asymmetry is the point: a board that
    dies on part 3 of 4 has already published parts 1 and 2, and those
    are exactly the messages someone may need to retract. Returning only
    a bool is how the orphans became unaddressable before.

    A partially-delivered board is still a product defect, so the caller
    is told the truth and the dedupe record stores delivered=False --
    which lets the next cycle retry rather than marking the night done.
    """
    url = _webhook_url(test)
    if not url:
        return False, []

    parts = split_message(body)
    if not parts:
        return False, []
    total = len(parts)
    posted: list[dict] = []

    for idx, part in enumerate(parts, start=1):
        content = part if total == 1 else f"{part}\n\n*({idx}/{total})*"
        delivered = False
        for attempt in range(_MAX_ATTEMPTS):
            ok, status, retry_after, created = _post_once(url, {"content": content})
            if ok:
                delivered = True
                if created.get("id"):
                    posted.append({
                        "message_id": str(created["id"]),
                        "channel_id": str(created.get("channel_id") or "") or None,
                        "part_index": idx,
                        "part_total": total,
                    })
                else:
                    # Delivered but unaddressable. Say so loudly -- it is
                    # the only state where retraction silently cannot help.
                    print(f"  [discord] part {idx}/{total} delivered but "
                          f"returned no message id; NOT retractable",
                          file=sys.stderr)
                break
            # 4xx other than 429 is a permanent error (bad webhook, bad
            # payload). Retrying it just burns time and log noise.
            if 400 <= status < 500 and status != 429:
                print(f"  [discord] permanent error HTTP {status}; giving up",
                      file=sys.stderr)
                return False, posted
            wait = retry_after if retry_after else (2.0 ** attempt)
            if attempt < _MAX_ATTEMPTS - 1:
                print(f"  [discord] part {idx}/{total} HTTP {status}; "
                      f"retry in {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
        if not delivered:
            print(f"  [discord] part {idx}/{total} FAILED after "
                  f"{_MAX_ATTEMPTS} attempts", file=sys.stderr)
            return False, posted
        if idx < total:
            time.sleep(0.6)   # stay under the per-webhook burst limit
    return True, posted


# ---------------------------------------------------------------------------
# retraction index -- how a bad post gets taken down
# ---------------------------------------------------------------------------

def _record_message_ids(event_type: str, event_key: str,
                        posted: list[dict]) -> None:
    """Index delivered messages in `discord_messages` so they can be
    deleted later.

    FAIL-SILENT, AND IN ITS OWN TABLE ON PURPOSE. The dedupe record in
    notifications_log is what stops a broadcast repeating; on 2026-08-06
    a gap there published THE BOARD three times to a paying channel. If
    id-capture shared that write, a failure here could resurrect exactly
    that bug. Losing the index costs the ability to UN-send. Losing the
    dedupe row costs the ability to NOT DOUBLE-send. Those are not
    equally bad, so they do not share a failure.
    """
    if not posted:
        return
    try:
        from db.supabase_writer import _get_client
        client = _get_client()
        if client is None:
            return
        client.table("discord_messages").insert([
            {
                "event_type": event_type,
                "event_key": event_key,
                "message_id": p["message_id"],
                "part_index": p.get("part_index", 1),
                "part_total": p.get("part_total", 1),
                "channel_id": p.get("channel_id"),
            }
            for p in posted
        ]).execute()
    except Exception:  # noqa: BLE001 -- never break a send over bookkeeping
        print(f"  [discord] could not index {len(posted)} message id(s) for "
              f"{event_type}/{event_key}; they are live but not retractable",
              file=sys.stderr)


def _delete_message(url: str, message_id: str) -> tuple[bool, int]:
    """DELETE one webhook message. Returns (gone, status).

    404 counts as GONE, not as failure: the operator may already have
    deleted it by hand (they did, on 2026-08-06), and a retract tool that
    reports failure for an already-absent message trains people to
    ignore it."""
    req = urllib.request.Request(
        f"{url.split('?')[0].rstrip('/')}/messages/{message_id}",
        headers={"User-Agent": "NRFI-Terminal/1.0"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return (200 <= resp.status < 300), resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, 404
        if exc.code == 429:
            try:
                time.sleep(float(json.loads(exc.read().decode()).get("retry_after", 2.0)))
            except Exception:  # noqa: BLE001
                time.sleep(2.0)
            return _delete_message(url, message_id)
        return False, exc.code
    except Exception:  # noqa: BLE001
        return False, 0


def retract(event_type: str, event_key: str, *, test: bool = False) -> int:
    """Delete every message posted for one broadcast. Returns how many
    are now gone from the channel.

    Deletes in REVERSE part order so a split board never sits in the
    channel showing "(2/3)" with its opening part already removed.
    """
    url = _webhook_url(test)
    if not url:
        print("  [discord] no webhook configured; nothing to retract",
              file=sys.stderr)
        return 0
    try:
        from db.supabase_writer import _get_client
        client = _get_client()
        if client is None:
            print("  [discord] Supabase unavailable; cannot look up ids",
                  file=sys.stderr)
            return 0
        res = (client.table("discord_messages")
                     .select("*")
                     .eq("event_type", event_type)
                     .eq("event_key", event_key)
                     .is_("retracted_at_utc", "null")
                     .execute())
        rows = sorted(res.data or [],
                      key=lambda r: r.get("part_index") or 1, reverse=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  [discord] id lookup failed ({exc!r})", file=sys.stderr)
        return 0

    if not rows:
        print(f"  [discord] no live messages indexed for "
              f"{event_type}/{event_key}")
        return 0

    gone = 0
    for r in rows:
        ok, status = _delete_message(url, r["message_id"])
        if ok:
            gone += 1
            try:
                from datetime import datetime, timezone
                client.table("discord_messages").update({
                    "retracted_at_utc": datetime.now(timezone.utc).isoformat()
                }).eq("id", r["id"]).execute()
            except Exception:  # noqa: BLE001
                pass
            print(f"  [discord] retracted part {r.get('part_index')}"
                  f"/{r.get('part_total')}"
                  + (" (was already gone)" if status == 404 else ""))
        else:
            print(f"  [discord] FAILED to delete part {r.get('part_index')} "
                  f"(HTTP {status})", file=sys.stderr)
        time.sleep(0.4)
    return gone


def send(body: str, *, event_type: str, event_key: str,
         preview: bool = False, test: bool = False,
         force: bool = False) -> bool:
    """THE entry point for every subscriber broadcast.

    dedupe -> send -> record, mirroring tracker._notify_event_telegram so
    the two channels cannot disagree about what was already sent.

    `event_key` MUST be stable for the thing being announced and unique
    per slate (e.g. "board:2026-08-06"), because it is the only thing
    standing between a Railway redeploy and a duplicate post.

    Returns True only when a message was actually delivered.
    """
    try:
        if preview:
            parts = split_message(body)
            print(f"=== DISCORD PREVIEW · {event_type} · key={event_key} ===")
            print(f"=== {len(body)} chars -> {len(parts)} message(s) ===\n")
            for i, p in enumerate(parts, 1):
                if len(parts) > 1:
                    print(f"--- message {i}/{len(parts)} ({len(p)} chars) ---")
                print(p)
                print()
            return True

        if not is_configured(test):
            print(f"  [discord] not configured "
                  f"({'DISCORD_TEST_WEBHOOK_URL' if test else 'DISCORD_WEBHOOK_URL'} "
                  f"unset); silent no-op", file=sys.stderr)
            return False

        # Reuse the ledger's dedupe so both channels share one memory.
        # Test sends bypass it: a rehearsal must be repeatable.
        if not test and not force:
            # (Exception, SystemExit): tracker.py guards optional deps
            # with sys.exit(), which raises SystemExit -- a BaseException
            # that sails straight through `except Exception`. Observed
            # live 2026-08-06. This module shares a process with the
            # money path and must never be able to kill it.
            try:
                from tracker import (_notify_event_dedup_check,
                                     _notify_event_record)
            except (Exception, SystemExit):  # noqa: BLE001
                _notify_event_dedup_check = None       # type: ignore
                _notify_event_record = None            # type: ignore
            if _notify_event_dedup_check and _notify_event_dedup_check(event_type, event_key):
                return False   # already announced; another cycle beat us

        # `force` exists for ONE situation: a human decided a already-sent
        # broadcast was wrong, retracted it, and is deliberately replacing
        # it. It is never passed by workers/predictor_loop.py or by
        # discord_broadcasts.run_broadcasts -- only by a person at a CLI.
        # It bypasses the dedupe CHECK but still writes the dedupe RECORD
        # below, so the 24h window re-arms and the scheduler will not pick
        # the broadcast up again on its next 5-minute cycle.
        if force and not test:
            print(f"  [discord] FORCED re-send of {event_type}/{event_key} "
                  f"-- dedupe check bypassed by explicit request",
                  file=sys.stderr)

        ok, posted = _send_raw(body, test=test)

        if not test:
            # Dedupe record FIRST. If this process dies between the two
            # writes, losing the id index costs retractability; losing the
            # dedupe row costs a duplicate post to paying subscribers.
            # Only one of those is tonight's incident.
            try:
                from tracker import _notify_event_record as _rec
                _rec(event_type, event_key, body, ok)
            except (Exception, SystemExit):  # noqa: BLE001
                pass
            _record_message_ids(event_type, event_key, posted)
        return ok
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- NEVER raise into the money path
        print(f"  [discord] send failed unexpectedly ({exc!r})", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Discord transport smoke test")
    ap.add_argument("--test-webhook", action="store_true",
                    help="send to DISCORD_TEST_WEBHOOK_URL instead of live")
    ap.add_argument("--preview", action="store_true",
                    help="render without sending")
    ap.add_argument("--message", default="NRFI Terminal — transport check. "
                                         "If you can read this, the webhook works.")
    a = ap.parse_args()
    ok = send(a.message, event_type="transport_check",
              event_key="manual", preview=a.preview, test=a.test_webhook)
    print(f"delivered={ok}")
    sys.exit(0 if ok else 1)

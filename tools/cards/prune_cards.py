"""Delete published cards older than the retention window.

Operator, 2026-08-14: *"every day, the old pictures should be deleted since
theyre already posted. no need to waste space."* Two nights in the bucket was
already 6.5MB — a night runs ~3.3MB across the three plates, so the bucket
grows about 100MB a month if nothing ever prunes it.

    python tools/cards/prune_cards.py --require-date 2026-08-14
    python tools/cards/prune_cards.py --keep-days 3 --dry-run

WHY THIS IS SAFE TO AUTOMATE. A card is a DERIVED artefact, not a source: it
is drawn entirely from one ledger row, and that row stops changing once the
game grades. `make_card.py --date <d> --publish` redraws any past night
byte-for-byte identical. So this deletes something reproducible, never
something original — which is exactly the property that makes an unattended
delete acceptable at all.

TWO GUARDS, because "delete on a schedule" is the kind of job that is fine
999 times and expensive once:

  1. `--require-date` — refuses to delete ANYTHING unless the bucket already
     holds a card for that date. The cron passes today. So a night where the
     render failed, or the slate had no priced play, deletes nothing at all
     rather than clearing the bucket and leaving `/cards` blank. Yesterday's
     cards survive until today's exist to replace them.
  2. The filename pattern is an allowlist. Anything in the bucket that is not
     `backfist_<date>_<plate>.png` is counted and skipped, never removed —
     so a file put there by hand, or by some future tool, cannot be caught by
     a job that does not know what it is.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.parent.parent
BUCKET = "cards"
# `.txt` as well as `.png` since 2026-08-14: the night's X post
# (`backfist_<date>_post.txt`) lives in the same bucket and belongs to the
# same publication, so it ages out on the same schedule. Without this it
# matched nothing, was counted as "not a card", and quietly accumulated
# forever — the exact thing this job exists to stop.
NAME = re.compile(r"^backfist_(\d{4}-\d{2}-\d{2})_([a-z0-9-]+)\.(?:png|txt)$",
                  re.I)


def retention_cutoff(require_date: str | None, keep_days: int,
                     today: date) -> tuple[date, date]:
    """Return (anchor, cutoff) — nights strictly before `cutoff` are deleted.

    ANCHORED TO `require_date`, NOT THE WALL CLOCK, and that distinction is
    the whole reason this is a named function with a test. See the note in
    `main()`: computing the cutoff from `now()` while the guard checked a
    different date deleted a freshly-published set in testing.
    """
    anchor = date.fromisoformat(require_date) if require_date else today
    return anchor, anchor - timedelta(days=keep_days - 1)


def _client():
    """Service key, same credential path as `make_card.publish`."""
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(REPO / ".env")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot prune")
    return create_client(url, key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-days", type=int, default=1,
                    help="nights to keep, counting today (default 1 = today only)")
    ap.add_argument("--require-date", metavar="YYYY-MM-DD",
                    help="do nothing unless the bucket already holds a card "
                         "for this date")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would go, delete nothing")
    a = ap.parse_args()

    if a.keep_days < 1:
        raise SystemExit("--keep-days must be >= 1 — today's card is never deleted")

    # THE WINDOW IS ANCHORED TO --require-date, NOT THE WALL CLOCK.
    #
    # These were two different clocks and it cost the bucket a full set in
    # testing: the guard confirmed "a card for 2026-08-13 exists", then the
    # cutoff was computed from `now()` (already 2026-08-14) and deleted the
    # 08-13 cards the same run had just published. The guard passed and the
    # delete was still wrong, because the two were answering questions about
    # different days.
    #
    # In production they normally agree — the cron passes today — so this
    # only bites at the ET midnight rollover: a step that captures TODAY_ISO
    # at 11:59pm and reaches the prune at 12:00am would delete the night it
    # had just drawn. Anchoring to the date we actually verified makes the
    # window "keep N nights ending at the night we just published", which
    # holds regardless of when the clock ticks over mid-run.
    today = datetime.now(ZoneInfo("America/New_York")).date()
    anchor, cutoff = retention_cutoff(a.require_date, a.keep_days, today)

    sb = _client()
    objs = sb.storage.from_(BUCKET).list("", {"limit": 1000})

    doomed, kept, foreign, have_required = [], [], 0, False
    for o in objs:
        m = NAME.match(o["name"])
        if not m:
            foreign += 1                       # not ours — never touch it
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            foreign += 1
            continue
        if a.require_date and m.group(1) == a.require_date:
            have_required = True
        size = (o.get("metadata") or {}).get("size", 0) or 0
        (doomed if d < cutoff else kept).append((o["name"], size))

    print(f"bucket : {len(objs)} objects  ({foreign} not cards, left alone)")
    print(f"today  : {today} (ET)   anchor {anchor}   keeping nights >= {cutoff}")

    if a.require_date and not have_required:
        print(f"SKIP   : no card for {a.require_date} in the bucket yet — "
              f"refusing to delete anything.")
        return 0

    if not doomed:
        print("nothing to prune.")
        return 0

    freed = sum(s for _, s in doomed) / 1024
    for n, s in sorted(doomed):
        print(f"  {'would delete' if a.dry_run else 'delete'} {n:44} {s:>9}B")
    if a.dry_run:
        print(f"dry run: {len(doomed)} objects, {freed:.0f} KB would be freed.")
        return 0

    sb.storage.from_(BUCKET).remove([n for n, _ in doomed])
    print(f"pruned : {len(doomed)} objects, {freed:.0f} KB freed; "
          f"{len(kept)} kept.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())

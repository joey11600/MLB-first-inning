#!/usr/bin/env python3
"""
Load a multi-book odds capture (data/diagnostics/odds/raw_<date>.csv, written by
tools/fetch_odds_api.py --raw-output) into Supabase `odds_multibook`, so the
dashboard can show the best available price per pick beside the ledger's
one-book price.  Idempotent: upserts on the table's natural key.

    python tools/odds_multibook_to_supabase.py data/diagnostics/odds/raw_2026-08-23.csv

Uses the same service-key client as the ledger mirror (db.supabase_writer).
No Supabase creds -> prints a note and exits 0 (the CSV is still the record).
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")


def _et_date(commence_iso: str, captured_iso: str) -> str:
    """Slate date in Eastern time: the game's start date if known, else the
    capture date.  A 10pm ET game is 02:00 UTC next day, so UTC dates would
    file it under tomorrow's slate."""
    for s in (commence_iso, captured_iso):
        if not s:
            continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ET).date().isoformat()
        except ValueError:
            continue
    return ""


def rows_from_csv(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                price = int(float(r["price"]))
            except (TypeError, ValueError):
                continue
            cap = (r.get("captured_at_utc") or "").strip()
            d = _et_date((r.get("commence_time") or "").strip(), cap)
            if not d or not cap:
                continue
            point = r.get("point")
            try:
                point_v = float(point) if point not in (None, "") else None
            except ValueError:
                point_v = None
            out.append({
                "captured_at_utc": cap,
                "date_et": d,
                "away_team": (r.get("away_team") or "").strip(),
                "home_team": (r.get("home_team") or "").strip(),
                "commence_time": (r.get("commence_time") or "").strip() or None,
                "book_key": (r.get("book_key") or "").strip(),
                "book": (r.get("book") or "").strip(),
                "market": (r.get("market") or "").strip(),
                "point": point_v,
                "outcome": (r.get("outcome") or "").strip(),
                "price": price,
            })
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: odds_multibook_to_supabase.py raw_<date>.csv [...]"); return 2
    from db.supabase_writer import _get_client
    client = _get_client()
    if client is None:
        print("[odds_multibook] no Supabase creds; CSV is the record, nothing mirrored"); return 0
    total = 0
    for p in argv:
        rows = rows_from_csv(Path(p))
        if not rows:
            print(f"[odds_multibook] {p}: 0 usable rows"); continue
        for i in range(0, len(rows), 500):
            batch = rows[i:i + 500]
            client.table("odds_multibook").upsert(
                batch, on_conflict="captured_at_utc,away_team,home_team,book_key,market,point,outcome"
            ).execute()
            total += len(batch)
        print(f"[odds_multibook] {p}: upserted {len(rows)} rows")
    print(f"[odds_multibook] done, {total} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

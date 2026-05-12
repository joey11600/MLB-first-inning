#!/usr/bin/env python3
"""
backfill_actual_pitchers.py -- For graded games where the predictor couldn't
identify the starting pitcher (PASS - Starter Pending), fetch the ACTUAL
starter from the boxscore so we get real pitcher data for those games.

Without this, ~26 graded games in picks_2026.csv have no pitcher_id, which
forces last5/last10/xera/whiff to fall back to league averages.
"""

import csv
import json
import sys
from pathlib import Path

import statsapi

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def get_starting_pitchers(game_pk: int) -> tuple[int, int] | None:
    """From a finished game's boxscore, get the actual starting pitcher IDs.

    Returns (away_pid, home_pid) or None.
    """
    try:
        bx = statsapi.boxscore_data(game_pk)
    except Exception:
        return None
    away_pitchers = bx.get("awayPitchers") or []
    home_pitchers = bx.get("homePitchers") or []
    a_pid = None
    h_pid = None
    if len(away_pitchers) > 1:
        # First non-header row is the starter
        first = away_pitchers[1]
        a_pid = first.get("personId")
    if len(home_pitchers) > 1:
        first = home_pitchers[1]
        h_pid = first.get("personId")
    if a_pid and h_pid:
        return int(a_pid), int(h_pid)
    return None


def main():
    path = ROOT / "data" / "picks_2026.csv"
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    pid_cache_path = ROOT / "data" / "pitcher_id_cache.json"
    with open(pid_cache_path, encoding="utf-8") as f:
        pid_cache = json.load(f)

    n_filled = 0
    n_skipped = 0
    # T-V21-2026-05-06e: track touched rows so we can patch only the
    # pitcher_id columns to Supabase.  Without this, a backfill run
    # against a stale CSV would have its updates remain CSV-only --
    # the dashboard reads Supabase, so it'd show stale "0" pitcher
    # IDs until the next predictor cycle.
    touched_rows: list[dict] = []
    for r in rows:
        pk = (r.get("game_pk") or "").strip()
        a_pid_csv = (r.get("away_pitcher_id") or "").strip()
        h_pid_csv = (r.get("home_pitcher_id") or "").strip()
        graded = (r.get("graded_result") or "").strip().upper()

        # Only fill if both IDs missing AND game has been graded (postponed games skipped too)
        if a_pid_csv and a_pid_csv != "0" and h_pid_csv and h_pid_csv != "0":
            continue
        if graded not in ("WIN", "LOSS", "PASS"):
            n_skipped += 1
            continue

        try:
            result = get_starting_pitchers(int(pk))
        except Exception:
            result = None
        if not result:
            n_skipped += 1
            continue

        a_pid, h_pid = result
        if not a_pid_csv or a_pid_csv == "0":
            r["away_pitcher_id"] = str(a_pid)
        if not h_pid_csv or h_pid_csv == "0":
            r["home_pitcher_id"] = str(h_pid)
        # Update pid_cache too
        pid_cache[pk] = [a_pid, h_pid]
        n_filled += 1
        touched_rows.append(r)
        print(f"  {r['date']} {r['away_team']} @ {r['home_team']} pk={pk}: a={a_pid}, h={h_pid}")

    # Atomic write via tracker._write_rows -- prevents a torn-file race
    # where a concurrent reader (predictor cycle, dashboard CSV serve)
    # sees a half-written file mid-flush.
    from tracker import _write_rows, _patch_picks_to_supabase
    _write_rows(path, rows)

    with open(pid_cache_path, "w", encoding="utf-8") as f:
        json.dump(pid_cache, f, indent=2)

    # Patch Supabase with ONLY the two columns this script owns.
    # Per-row PATCH (not full mirror) means real odds / grade / bet
    # stay untouched even if the local CSV is mid-stale.
    if touched_rows:
        season = int((touched_rows[0].get("date") or "")[:4])
        _patch_picks_to_supabase(
            season, touched_rows,
            ["away_pitcher_id", "home_pitcher_id"],
        )
        print(f"  Patched {len(touched_rows)} rows to Supabase "
              f"(away_pitcher_id, home_pitcher_id only)")

    print(f"\n  Filled {n_filled} games with actual starting pitcher IDs")
    print(f"  Skipped {n_skipped} games (not graded or boxscore unavailable)")


if __name__ == "__main__":
    main()

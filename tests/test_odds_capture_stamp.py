"""The odds file carries each row's OWN capture time, and the importer uses it.

THE DEFECT (found 2026-08-23). The Railway odds file is --merge'd: a row for a
game fetched at 1 PM sits in the file all evening while later cycles add other
games. `import_odds` stamped EVERY matched row with the import time, so an
unlocked game's odds_captured_at moved every five minutes ("captured just
now", all night) although its price had not been re-fetched since 1 PM.
Locked rows were never affected (T2.23 freezes them before the stamp).

THE RULE. `captured_at_utc` in the odds file, when present and parseable, is
the stamp; otherwise the import time is (scrape_dk_odds.py writes no such
column). advance_capture_ts is a high-water mark, so re-importing a preserved
row with its original stamp is a no-op.

Also pinned here: the CLI contract the at-lock fetch relies on -- `--book`
and `--ledger-book` are mutually exclusive -- and the file schema's new
last column, which `merge_rows` must carry per row.
"""
from __future__ import annotations

import contextlib
import csv
import datetime
import io
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

pytestmark = pytest.mark.money


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """One unlocked LEAN game three hours out (no lock, no bet), so the
    capture stamp is the only thing an import changes."""
    et = datetime.datetime.now(ZoneInfo("America/New_York")) + datetime.timedelta(hours=3)
    iso = et.date().isoformat()
    gtime = et.strftime("%I:%M %p ET").lstrip("0")
    csv_path = tmp_path / "picks_2026.csv"
    monkeypatch.setattr(tracker, "_csv_path", lambda season: csv_path)
    r = {k: "" for k in tracker.FIELDS}
    r.update(date=iso, season="2026", game_pk="901", game_number="1",
             away_team="AAA", home_team="BBB", game_time_et=gtime,
             pick_side="NRFI", pick_strength="LEAN", pick_label="LEAN NRFI",
             nrfi_prob="0.5500", yrfi_prob="0.4500", bet_placed="", units_risked="")
    tracker._write_rows(csv_path, [r])

    def odds_file(tag, captured_at=None):
        p = tmp_path / f"odds_{tag}.csv"
        cols = ["date", "game_pk", "away_team", "home_team",
                "market_nrfi_odds", "market_yrfi_odds", "sportsbook"]
        if captured_at is not None:
            cols.append("captured_at_utc")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            row = [iso, "901", "AAA", "BBB", "-118", "-104", "FanDuel"]
            if captured_at is not None:
                row.append(captured_at)
            w.writerow(row)
        return p

    def run(path):
        with contextlib.redirect_stdout(io.StringIO()):
            tracker.import_odds(str(path), season=2026)
        return tracker._read_rows(csv_path)[0]

    return odds_file, run


def test_the_rows_own_capture_time_is_the_stamp(ledger):
    odds_file, run = ledger
    row = run(odds_file("a", "2026-08-23T17:01:02Z"))
    assert row["odds_captured_at"] == "2026-08-23T17:01:02Z"
    assert row["opened_captured_at"] == "2026-08-23T17:01:02Z"


def test_a_preserved_row_reimported_does_not_move_the_stamp(ledger):
    """The whole point: the same merged row, imported again five minutes
    later, leaves odds_captured_at where the price was actually fetched."""
    odds_file, run = ledger
    first = run(odds_file("a", "2026-08-23T17:01:02Z"))
    again = run(odds_file("b", "2026-08-23T17:01:02Z"))
    assert again["odds_captured_at"] == first["odds_captured_at"] == "2026-08-23T17:01:02Z"


def test_a_refetched_row_advances_the_stamp(ledger):
    odds_file, run = ledger
    run(odds_file("a", "2026-08-23T17:01:02Z"))
    row = run(odds_file("b", "2026-08-23T22:06:40Z"))
    assert row["odds_captured_at"] == "2026-08-23T22:06:40Z"
    assert row["opened_captured_at"] == "2026-08-23T17:01:02Z"   # first seen never moves


def test_a_file_without_the_column_still_stamps_the_import_time(ledger):
    """scrape_dk_odds.py and every pre-2026-08-23 file: unchanged behaviour."""
    odds_file, run = ledger
    before = tracker._now_utc()
    row = run(odds_file("a"))
    assert row["odds_captured_at"] >= before


def test_an_unparseable_capture_time_falls_back_to_the_import_time(ledger):
    odds_file, run = ledger
    before = tracker._now_utc()
    row = run(odds_file("a", "not a time"))
    assert row["odds_captured_at"] >= before


# ---------------------------------------------------------------------------
# the fetch tool's CLI contracts the at-lock step relies on
# ---------------------------------------------------------------------------

TOOL = ROOT / "tools" / "fetch_odds_api.py"


def test_book_and_ledger_book_are_mutually_exclusive():
    r = subprocess.run([sys.executable, str(TOOL), "--book", "fanduel",
                        "--ledger-book", "fanduel"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "mutually exclusive" in (r.stdout + r.stderr)


def test_the_odds_file_schema_carries_the_capture_time():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fetch_odds_api", TOOL)
    F = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(F)
    assert F.FIELDS[-1] == "captured_at_utc"
    # merge keeps a preserved row's own stamp and takes the fresh row's
    old = {"date": "2026-08-23", "away_team": "AAA", "home_team": "BBB",
           "sportsbook": "FanDuel", "captured_at_utc": "2026-08-23T17:01:02Z"}
    new = {"date": "2026-08-23", "away_team": "CCC", "home_team": "DDD",
           "sportsbook": "FanDuel", "captured_at_utc": "2026-08-23T22:06:40Z"}
    merged, _ = F.merge_rows([old], [new])
    stamps = {r["away_team"]: r["captured_at_utc"] for r in merged}
    assert stamps == {"AAA": "2026-08-23T17:01:02Z", "CCC": "2026-08-23T22:06:40Z"}

"""T8.19 -- the daily risk budget is allocated BEST BET FIRST.

Until 2026-08-09 `import_odds` handed out the 15u/day budget in whatever
order games happened to appear in DraftKings' file.  On a cap-bound slate
that meant a pick's PUBLISHED STAKE depended on its row position: the real
2026-07-31 four-game slate sized

    CWS@TB 8u / KC@COL 5u / MIL@LAA 2u / DET@OAK 0u      (file order)
    CWS@TB 4u / KC@COL 5u / MIL@LAA 5u / DET@OAK 0.5u    (reversed)

-- same games, same prices, same probabilities, and MIL@LAA either a 2u bet
or a 5u bet depending on nothing that has anything to do with the bet.  The
weakest play could take money the strongest one then could not have.

`kelly_stake_units`' own docstring had already named this as its known
limitation and named the fix ("rank the day's picks by edge before
allocating").  Both writers now sort by `_top_pick_rank_tuple` -- the same
ordering the No.1 rule and dashboard/lib/top-pick-rank.ts use -- so the
budget and the headline can never disagree about which play is best.

These tests drive the REAL `tracker.import_odds`, not a reimplementation of
its allocator, because the defect lived in the caller rather than in
`kelly_stake_units`: sizing one bet at a time was always correct, and the
bug was only visible across a slate.

NO HARDCODED SLATE DATES -- see the comment in tests/test_selection.py; a
pinned date aged past `_pick_is_locked`'s 24h defensive lock and turned the
suite red overnight.  Everything here is derived from `now`.
"""
import contextlib
import csv
import datetime
import io
from zoneinfo import ZoneInfo

import pytest

import tracker


# The real 2026-07-31 cap-bound slate: four STRONG YRFI picks whose uncapped
# quarter-Kelly stakes sum well past the 15u budget, ordered strongest first.
SLATE = [
    ("CWS", "TB",  0.7128, "-130"),
    ("KC",  "COL", 0.6903, "-155"),
    ("MIL", "LAA", 0.6288, "-115"),
    ("DET", "OAK", 0.5885, "-140"),
]


@pytest.fixture()
def slate(tmp_path, monkeypatch):
    """A ledger + odds file whose games are 30 minutes out, i.e. INSIDE the
    60-minute lock window, so every row commits and therefore every row
    competes for the budget.  Pre-lock rows would not allocate at all
    (T8.18: `game_date` reaches Kelly only when committing), so a pre-lock
    fixture would pass this test without testing anything."""
    et = datetime.datetime.now(ZoneInfo("America/New_York")) + datetime.timedelta(minutes=30)
    iso = et.date().isoformat()
    gtime = et.strftime("%I:%M %p ET").lstrip("0")
    csv_path = tmp_path / "picks_2026.csv"
    monkeypatch.setattr(tracker, "_csv_path", lambda season: csv_path)

    def write(order, tag):
        rows = []
        for n, (a, h, p, _o) in enumerate(order):
            r = {k: "" for k in tracker.FIELDS}
            r.update(date=iso, season="2026", game_pk=f"90{n}", game_number="1",
                     away_team=a, home_team=h, game_time_et=gtime,
                     pick_side="YRFI", pick_strength="STRONG",
                     pick_label="STRONG YRFI",
                     nrfi_prob=f"{1 - p:.4f}", yrfi_prob=f"{p:.4f}",
                     bet_placed="", units_risked="")
            rows.append(r)
        tracker._write_rows(csv_path, rows)
        odds = tmp_path / f"odds_{tag}.csv"
        with open(odds, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "game_pk", "away_team", "home_team",
                        "market_nrfi_odds", "market_yrfi_odds", "sportsbook"])
            for n, (a, h, _p, o) in enumerate(order):
                w.writerow([iso, f"90{n}", a, h, "", o, "DraftKings"])
        return odds

    def run(order, tag):
        odds = write(order, tag)
        with contextlib.redirect_stdout(io.StringIO()):
            tracker.import_odds(str(odds), season=2026)
        return {f"{r['away_team']}@{r['home_team']}":
                (r["bet_placed"], float(r["units_risked"] or 0.0))
                for r in tracker._read_rows(csv_path)}

    return run


def _total(alloc):
    return sum(v[1] for v in alloc.values())


def test_the_same_slate_allocates_identically_in_either_file_order(slate):
    """THE T8.19 REGRESSION GUARD. Reversing DraftKings' file must not move
    a single unit."""
    forward = slate(SLATE, "fwd")
    reverse = slate(list(reversed(SLATE)), "rev")
    assert forward == reverse, (
        f"allocation depends on file order:\n  fwd={forward}\n  rev={reverse}")


def test_the_budget_goes_to_the_strongest_play_first(slate):
    """Best-bet-first is not just deterministic, it is the RIGHT order: the
    most confident pick is funded before a weaker one can crowd it out."""
    alloc = slate(SLATE, "fwd")
    assert alloc["CWS@TB"][1] == 8.0      # strongest, funded in full
    assert alloc["KC@COL"][1] == 5.0
    assert alloc["MIL@LAA"][1] == 2.0     # trimmed to the remaining room
    assert alloc["DET@OAK"][1] == 0.0     # weakest, budget exhausted


def test_the_daily_cap_is_never_breached(slate):
    for tag, order in (("fwd", SLATE), ("rev", list(reversed(SLATE)))):
        alloc = slate(order, tag)
        assert _total(alloc) <= tracker.KELLY_MAX_DAILY_FRAC * 100.0 + 1e-9


def test_a_refused_row_is_not_committed(slate):
    """A row the budget could not fund is a refusal, not a zero-unit bet."""
    alloc = slate(SLATE, "fwd")
    assert alloc["DET@OAK"] == ("N", 0.0)


def test_three_consecutive_imports_produce_identical_stakes(slate):
    """The 2026-07-28 P0-1 oscillation guard, reached through the real
    caller rather than through a helper that resets the tally itself.

    Railway re-imports every five minutes, so a tally that accumulated
    across ticks used to walk the stakes full -> trimmed -> zero, and
    whatever value the lock window happened to catch froze forever."""
    first = slate(SLATE, "fwd")
    # The fixture rewrites the ledger from scratch each call, so each pass is
    # a genuinely fresh batch rather than a re-import onto committed rows --
    # which is the case that used to drift, because the tally survived the
    # process while the ledger did not.
    for _ in range(2):
        assert slate(SLATE, "fwd") == first

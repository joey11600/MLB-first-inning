"""
The Odds API path: what gets SPENT, and what reaches the importer.

T8.31, 2026-08-11. DraftKings began 403'ing Railway's egress IP -- the
only working odds source -- so the money path lost prices entirely. The
replacement buys DK's own number from an aggregator, which keeps the
published record continuous (it is still DraftKings' price), but moves
two new decisions into code:

  1. WHICH EVENTS COST A CREDIT.  Every event is one credit and the loop
     runs every five minutes. DK posts a first-inning line a median 63
     min before its own first pitch, so fetching the whole card each
     cycle spends the month's budget on markets that do not exist yet.
  2. WHAT SURVIVES INTO THE FILE.  A windowed fetch only ever holds part
     of the slate, and `import_odds` re-reads the whole file every cycle.
     Overwriting would delete the prices already captured for games that
     have locked.

Both are money-path logic, so both are pinned here.
"""

import csv
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fetch_odds_api", ROOT / "tools" / "fetch_odds_api.py")
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

pytestmark = pytest.mark.money

NOW = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)   # 12:00 ET


def _ev(minutes_from_now, name="A"):
    """An event starting `minutes_from_now` minutes after NOW."""
    return {"id": name,
            "commence_time": (NOW + timedelta(minutes=minutes_from_now))
            .isoformat().replace("+00:00", "Z")}


# ---------------------------------------------------------------------------
# what costs a credit
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_the_window_keeps_only_games_approaching_their_lock():
    events = [_ev(30, "soon"), _ev(119, "edge_in"),
              _ev(121, "edge_out"), _ev(400, "late")]
    kept = {e["id"] for e in
            F.select_events_in_window(events, within_minutes=120, now=NOW)}
    assert kept == {"soon", "edge_in"}


@pytest.mark.regression
def test_zero_means_no_upper_bound():
    """0 is 'the whole slate', not 'nothing' -- the CLI default."""
    events = [_ev(30), _ev(400), _ev(2000)]
    assert len(F.select_events_in_window(events, within_minutes=0, now=NOW)) == 3


@pytest.mark.regression
def test_started_games_are_dropped_only_when_asked():
    events = [_ev(-10, "started"), _ev(30, "upcoming")]
    with_flag = {e["id"] for e in
                 F.select_events_in_window(events, skip_started=True, now=NOW)}
    without = {e["id"] for e in
               F.select_events_in_window(events, skip_started=False, now=NOW)}
    assert with_flag == {"upcoming"}
    assert without == {"started", "upcoming"}


@pytest.mark.regression
def test_an_unparseable_start_time_is_kept_not_dropped():
    """Dropping it would lose a game's price to a date-format change.
    Keeping it costs one credit and is recoverable; dropping is silent."""
    events = [{"id": "weird", "commence_time": "not-a-date"},
              {"id": "missing"}]
    kept = {e["id"] for e in
            F.select_events_in_window(events, within_minutes=60,
                                      skip_started=True, now=NOW)}
    assert kept == {"weird", "missing"}


@pytest.mark.regression
def test_a_book_request_asks_for_that_book_not_the_whole_region():
    """THE COST MODEL, measured against the live API 2026-08-11.

    Cost is [markets RETURNED] x [regions], and empty responses are free.
    DK posts a first-inning line a median 63 min out; FanDuel/BetMGM post
    hours earlier. So `regions=us` RETURNS those books on every early
    fetch -- costing a credit for data the --book filter then discards --
    while `bookmakers=draftkings` comes back empty and costs nothing.

        CLE@DET at T-280:  bookmakers=draftkings -> 0 books, cost 0
                           regions=us            -> 4 books, cost 1

    If `regions` ever creeps back in alongside a --book request, every
    pre-posting fetch silently starts costing again."""
    p = F.odds_params("KEY", "draftkings", "us")
    assert p["bookmakers"] == "draftkings"
    assert "regions" not in p, "regions would re-introduce the paid path"
    assert p["markets"] == F.MARKET


@pytest.mark.regression
def test_a_display_name_is_normalised_to_the_api_key():
    """--book accepts 'DraftKings' for the local filter; the API wants
    'draftkings'. A mismatch here returns nothing, all evening, silently."""
    assert F.odds_params("K", "DraftKings", "us")["bookmakers"] == "draftkings"
    assert F.odds_params("K", "  DrAfTkInGs  ", "us")["bookmakers"] == "draftkings"


@pytest.mark.regression
def test_without_a_book_it_falls_back_to_regions():
    """The multi-book diagnostic pull is the one case where paying for
    other books is the point."""
    p = F.odds_params("KEY", None, "us")
    assert p["regions"] == "us"
    assert "bookmakers" not in p


@pytest.mark.regression
def test_parse_windows_reads_the_two_phase_spec():
    assert F.parse_windows("120:115,75:55") == [(120.0, 115.0), (75.0, 55.0)]
    assert F.parse_windows("75:55") == [(75.0, 55.0)]
    assert F.parse_windows("") == []


@pytest.mark.regression
def test_parse_windows_rejects_a_reversed_pair():
    """These are minutes BEFORE first pitch, so the earlier bound comes
    first. '55:75' would silently match nothing and the slate would go
    unpriced with no error -- fail loudly instead."""
    with pytest.raises(ValueError):
        F.parse_windows("55:75")
    with pytest.raises(ValueError):
        F.parse_windows("120")


@pytest.mark.regression
def test_two_phase_fetches_the_probe_and_the_lock_but_not_the_gap():
    """THE WHOLE POINT of design B: the stretch between the probe and the
    lock cluster is where a continuous window wasted most of its credits
    watching a market DK had not opened yet."""
    bands = F.parse_windows("120:115,75:55")

    def fetched(mins):
        return bool(F.select_events_in_window(
            [_ev(mins)], windows=bands, now=NOW))

    assert fetched(118), "inside the early probe"
    assert fetched(60), "inside the lock cluster"
    assert fetched(75) and fetched(55), "cluster bounds are inclusive"
    assert not fetched(100), "the dead gap must NOT be fetched"
    assert not fetched(90), "the dead gap must NOT be fetched"
    assert not fetched(300), "hours out, before any price exists"
    assert not fetched(30), "after the lock has committed"


@pytest.mark.regression
def test_the_lock_cluster_gives_several_attempts_not_one():
    """A single shot would miss ~45% of games, since the median first
    post is T-63. Count the 5-minute cycles that fall in the band."""
    bands = F.parse_windows("120:115,75:55")
    hits = sum(1 for m in range(0, 200, 5)
               if F.select_events_in_window([_ev(m)], windows=bands, now=NOW))
    # 75,70,65,60,55 = 5 in the cluster; 120,115 = 2 in the probe
    assert hits >= 6, f"too few attempts per game ({hits})"


@pytest.mark.regression
def test_two_phase_costs_far_less_than_a_continuous_window():
    """Pins the saving that justified the change. Simulates a real
    15-game evening slate at the loop's 5-minute cadence."""
    starts = [280, 280, 285, 305, 307, 315, 340, 340, 345,
              398, 400, 400, 400, 405, 430]      # minutes after NOW
    two_phase = F.parse_windows("120:115,75:55")

    def cost(windows, within=0):
        total = 0
        for step in range(0, 600, 5):
            now = NOW + timedelta(minutes=step)
            evs = [_ev(s, f"g{i}") for i, s in enumerate(starts)]
            total += len(F.select_events_in_window(
                evs, within_minutes=within, skip_started=True,
                now=now, windows=windows))
        return total

    continuous = cost(None, within=120)
    phased = cost(two_phase)
    assert phased < continuous / 3, (
        f"expected a >3x saving, got {phased} vs {continuous}")
    assert phased > 0, "must still fetch something"


@pytest.mark.regression
def test_the_window_is_what_makes_the_budget_work():
    """A full 15-game slate spanning an evening: at noon almost nothing
    is within two hours, so a cycle costs ~0 rather than 15."""
    slate = [_ev(m, f"g{m}") for m in
             (280, 300, 320, 340, 360, 380, 400, 420,
              440, 460, 480, 500, 520, 540, 560)]
    at_noon = F.select_events_in_window(slate, within_minutes=120, now=NOW)
    assert at_noon == []
    later = F.select_events_in_window(
        slate, within_minutes=120, now=NOW + timedelta(minutes=240))
    assert 0 < len(later) < len(slate)


# ---------------------------------------------------------------------------
# what reaches the importer
# ---------------------------------------------------------------------------

def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, F.FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _row(away, home, nrfi, yrfi, book="DraftKings", date="2026-08-11"):
    return {"date": date, "game_pk": "", "away_team": away,
            "home_team": home, "market_nrfi_odds": nrfi,
            "market_yrfi_odds": yrfi, "sportsbook": book,
            "start_time_utc": ""}


@pytest.mark.regression
def test_merge_preserves_earlier_games_and_refreshes_the_current_one():
    """THE FAILURE THIS PREVENTS: a windowed fetch at 9pm holds only the
    late games. Overwriting would delete the 6:40pm game's captured price
    from the file the importer re-reads every cycle.

    Calls the tool's own merge_rows -- an earlier version of this test
    reimplemented the merge inline, which would have passed even with the
    real function broken."""
    existing = [_row("PIT", "MIA", "-175", "+135"),
                _row("CLE", "DET", "-125", "-105")]
    fresh = [_row("CLE", "DET", "-140", "+115"),      # line moved
             _row("KC", "LAD", "-110", "-110")]       # newly quoted

    merged, replaced = F.merge_rows(existing, fresh)
    got = {(r["away_team"], r["home_team"]): r for r in merged}

    assert replaced == 1, "only CLE@DET was re-fetched"
    assert set(got) == {("PIT", "MIA"), ("CLE", "DET"), ("KC", "LAD")}
    assert got[("PIT", "MIA")]["market_nrfi_odds"] == "-175"   # preserved
    assert got[("CLE", "DET")]["market_nrfi_odds"] == "-140"   # refreshed
    assert len(merged) == 3, "a refreshed game must REPLACE, not duplicate"


@pytest.mark.regression
def test_a_refetched_game_never_leaves_a_stale_duplicate():
    """import_odds applies every matching row in FILE ORDER, so a
    duplicate would let the OLDER price win."""
    existing = [_row("CLE", "DET", "-125", "-105")]
    merged, _ = F.merge_rows(existing, [_row("CLE", "DET", "-140", "+115")])
    assert len(merged) == 1
    assert merged[0]["market_nrfi_odds"] == "-140"


@pytest.mark.regression
def test_merge_keeps_books_apart_in_a_diagnostic_file():
    """A multi-book file must not collapse to one row per game -- the key
    includes the sportsbook."""
    existing = [_row("CLE", "DET", "-125", "-105", book="FanDuel")]
    merged, replaced = F.merge_rows(
        existing, [_row("CLE", "DET", "-140", "+115", book="DraftKings")])
    assert replaced == 0
    assert len(merged) == 2


@pytest.mark.regression
def test_merge_round_trips_through_a_real_file(tmp_path):
    """End-to-end on disk, in the CSV shape the importer reads."""
    out = tmp_path / "dk_2026-08-11.csv"
    _write(out, [_row("PIT", "MIA", "-175", "+135")])
    with open(out, newline="", encoding="utf-8") as f:
        existing = list(csv.DictReader(f))
    merged, _ = F.merge_rows(existing, [_row("KC", "LAD", "-110", "-110")])
    _write(out, merged)
    with open(out, newline="", encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert len(got) == 2
    assert {r["away_team"] for r in got} == {"PIT", "KC"}


# ---------------------------------------------------------------------------
# the guard that keeps the record a DraftKings series
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_self_test_still_passes():
    """The tool's own --self-test covers team mapping, Over/Under -> YRFI/
    NRFI, the 1.5-line trap and the --book filter. Run it here so a CI
    failure names the file rather than waiting for someone to run it."""
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fetch_odds_api.py"), "--self-test"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL SELF-TESTS PASSED" in r.stdout


@pytest.mark.regression
def test_the_loop_step_is_off_unless_explicitly_enabled():
    """It must not start pricing the ledger from a new source because a
    deploy happened -- only because an operator set the variable."""
    sys.path.insert(0, str(ROOT / "workers"))
    import predictor_loop as P
    old = os.environ.pop("PREDICTOR_ODDS_API", None)
    try:
        assert P.step_fetch_odds_api() == 0      # skipped, no spend
    finally:
        if old is not None:
            os.environ["PREDICTOR_ODDS_API"] = old

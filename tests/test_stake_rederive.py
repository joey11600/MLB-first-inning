"""
T8.18 PART 1 -- the stake must follow the model until the pick locks.

WHAT WENT WRONG.  Sizing was a side effect of a PRICE ARRIVING, not a step in
the lock.  `units_risked` / `edge_on_pick` / `market_*_odds` all sit on
`log_picks`' always-preserve list, so a predict tick refreshed the probability
and left the stake alone, while the T2.25 probability freeze only engages once
`bet_placed=="Y"` -- which under T2.58 happens inside the 60-minute lock
window.  Between those two facts, a STRONG pick whose probability moved
pre-lock published a stake derived from a probability the model had already
thrown away.  Three confirmed victims, all git-history verified:

    2026-08-09 LAD@ARI   p 0.5831 -> 0.6288   published 2u, rule says 5u
    2026-08-02 DET@OAK   p 0.6994 -> 0.6012   published 7u, rule says 1u
    2026-08-04 SD@ARI    p 0.7128 -> 0.6873   published 9u, rule says 8u

On all three, `bet_placed` only became "Y" AFTER first pitch, via
`tools/end_of_day_check.py`'s orphan heal -- so the stale stake is what the
subscriber was sold AND what the record booked.

`tracker._rederive_pre_lock_stake` is the fix.  It re-derives the stake and
the edge triple from the row's CURRENT probability against its ALREADY
CAPTURED price.  Half of these tests pin that it MOVES; the other half pin the
five doors it must never open.  The second half matters more: every one of
them is a way to fabricate a bet, and this repo has fabricated bets before
(2026-07-28 P0-2).

EVERY EXPECTED STAKE HERE IS COMPUTED BY THE SHIPPED `kelly_stake_units`, not
written out by hand -- a hand-derived number pins the author's arithmetic
rather than the system's behaviour, which is how a suite goes green while the
money moves.  The two headline figures (2u and 5u on LAD@ARI) are additionally
asserted as literals, because those two numbers ARE the incident.

NO TEST HERE MAY READ data/picks_2026.csv, AND NO TEST HERE MAY HARDCODE A
SLATE DATE.  Both rules are load-bearing; see `no_real_ledger` and `_slate`.
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402

pytestmark = pytest.mark.money

ET = ZoneInfo("America/New_York")

# The columns the re-derive is FORBIDDEN to touch.  It re-sizes against a
# price; it does not re-price.  T2.23 capture semantics and the open->bet CLV
# both depend on this list staying frozen while units_risked moves.
PRICE_COLUMNS = (
    "market_nrfi_odds", "market_yrfi_odds", "sportsbook", "odds_captured_at",
    "opened_nrfi_odds", "opened_yrfi_odds", "opened_captured_at", "clv_pct",
)


# ---------------------------------------------------------------------------
# fixtures
#
# NO HARDCODED SLATE DATES.  tests/test_selection.py carries the incident:
# that file shipped 2026-08-07 pinned to "2026-08-06" and went red by itself
# overnight when the slate aged past `_pick_is_locked`'s 24h defensive lock,
# blaming a fix that had never touched tracker.py.  A test that fails because
# the calendar moved is not testing the code.  Everything below is derived
# from `now`, including the DATE -- a game "4 hours from now" run at 22:00 ET
# is tomorrow's slate, and the row's `date` has to say so or
# `_parse_game_time_et` reconstructs the wrong instant.
# ---------------------------------------------------------------------------

def _slate(offset_hours: float) -> tuple[str, str]:
    """(iso_date, game_time_et) for a game `offset_hours` from now in ET."""
    dt = datetime.now(ET) + timedelta(hours=offset_hours)
    # "%I" zero-pads ("09:40 PM"); the ledger writes "9:40 PM ET".  Both parse,
    # but match the real format so the fixture looks like a real row.
    return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p ET").lstrip("0")


def _row(p_yrfi, *, offset_hours=4.0, side="YRFI", strength="STRONG",
         yrfi_odds="-120", nrfi_odds="+100", bet_placed="N",
         units_risked="2.0", **kw) -> dict:
    """One pre-lock STRONG row with a captured price on both sides.

    Defaults describe the 2026-08-09 LAD@ARI victim mid-evening: STRONG YRFI,
    DK -120 already captured hours ago, stake recorded but NOT committed
    (T2.58 pre-lock = `bet_placed="N"` carrying a positive `units_risked`).
    """
    iso, gtet = _slate(offset_hours)
    r = {
        "date": iso, "season": "2026", "game_pk": "825050",
        "away_team": "LAD", "home_team": "ARI", "game_time_et": gtet,
        "nrfi_prob": str(round(1.0 - p_yrfi, 4)), "yrfi_prob": str(p_yrfi),
        "pick_side": side, "pick_strength": strength, "pick_label": "",
        "created_at": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
        "market_nrfi_odds": nrfi_odds, "market_yrfi_odds": yrfi_odds,
        "sportsbook": "DraftKings", "odds_captured_at": "2026-08-09T06:02:11Z",
        "opened_nrfi_odds": "+105", "opened_yrfi_odds": "-125",
        "opened_captured_at": "2026-08-09T06:02:11Z", "clv_pct": "0.0",
        "implied_nrfi_prob": "", "implied_yrfi_prob": "",
        "edge_nrfi": "", "edge_yrfi": "", "edge_on_pick": "",
        "bet_placed": bet_placed, "units_risked": units_risked,
        "profit_loss_units": "", "graded_result": "",
    }
    r.update(kw)
    return r


@pytest.fixture(autouse=True)
def no_real_ledger(monkeypatch):
    """No test in this file may read data/picks_2026.csv.

    The re-derive path passes `game_date=None` (rule R1) so it should never
    reach `_committed_on` or `current_bankroll_units` -- but "should" is not a
    guard.  Stubbing the reader means that if the projection ever starts
    allocating, it allocates against an EMPTY ledger here rather than silently
    picking up whatever tonight's real slate happens to hold, which would make
    these assertions depend on production data.
    """
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: [])
    monkeypatch.setattr(tracker, "_csv_path", lambda _s: "unused-by-tests.csv")


@pytest.fixture(autouse=True)
def kelly_state_restored():
    """Snapshot and restore the three Kelly module globals.

    `_daily_committed` is seeded directly by one test below.  A suite that
    leaks a seeded tally into tests/test_money.py's cap assertions would fail
    somewhere else entirely, and the bisect would land on the wrong file.
    """
    saved = (dict(tracker._daily_committed), tracker._bankroll_cache,
             tracker._kelly_batch_epoch)
    yield
    tracker._daily_committed.clear()
    tracker._daily_committed.update(saved[0])
    tracker._bankroll_cache = saved[1]
    tracker._kelly_batch_epoch = saved[2]


@pytest.fixture
def rederive_on(monkeypatch):
    """Turn PART 1 on.  It ships OFF (`NRFI_STAKE_REDERIVE` defaults to
    "skip") and belongs on the one host that captures the price -- Railway --
    the same way PREDICTOR_SCRAPE_DK does.  Two hosts re-deriving from two
    independent model fetches would flap the stake."""
    monkeypatch.setenv("NRFI_STAKE_REDERIVE", "enabled")


def _tick(row: dict) -> dict:
    """One predict tick against a pre-lock row.  Mutates and returns it."""
    tracker._rederive_pre_lock_stake(row, season=2026, locked=False)
    return row


def _load_tool(name: str):
    """Load a module out of tools/ WITHOUT putting tools/ on sys.path.

    tools/ holds ~40 files named test_*.py that are model experiments, not
    tests, plus modules with generic names (reconcile, pl_calc).  Adding that
    directory to the import path from inside a test would let any of them
    shadow a real import later in the session.  The path is derived from
    __file__, not the working directory -- pytest can be invoked from
    anywhere.
    """
    import importlib.util
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_t8_18_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _moves(row: dict, **repair) -> bool:
    """Re-derive a REPAIRED COPY of `row` and report whether the stake moved.

    Every negative test pairs its assertion with this control.  A fixture that
    fails the filter under test for some UNRELATED reason -- a missing price,
    an unparseable game time, a typo in a column name -- would pass the test
    while pinning absolutely nothing, and would keep passing after the filter
    it claims to cover was deleted.
    """
    probe = dict(row)
    probe.update(repair)
    tracker._rederive_pre_lock_stake(probe, season=2026, locked=False)
    return probe.get("units_risked") != row.get("units_risked")


# ---------------------------------------------------------------------------
# it moves -- the bug itself
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_stake_tracks_a_moving_probability_across_three_predict_ticks(rederive_on):
    """THE 2026-08-09 LAD@ARI VICTIM, replayed.

    One STRONG YRFI row, ~4h from first pitch, DK -120 captured once and never
    re-quoted.  The model walked 0.5831 -> 0.6060 -> 0.6288 across the
    afternoon's predict ticks.  Live, the stake sat at 2u the whole way and
    that is what locked; the rule says 5u.

    The second half of this test is worth as much as the first: the PRICE MUST
    NOT MOVE.  The re-derive re-sizes against the captured price, it does not
    re-price.  If `odds_captured_at` or `opened_*` drifted here, T2.23's
    bet-time odds lock and the open->bet CLV would both be quietly wrong, and
    a stake fix would have cost us the pricing history it was supposed to
    leave alone.
    """
    walk = [0.5831, 0.6060, 0.6288]
    expected = [tracker.kelly_stake_units(p, "-120") for p in walk]
    assert expected == [2.0, 3.0, 5.0], (
        "the shipped sizing rule no longer reproduces the incident's own "
        "figures -- fix the rule or the fixture, do not adjust this test")

    row = _row(walk[0])
    frozen = {c: row[c] for c in PRICE_COLUMNS}
    seen_stakes, seen_edges = [], []

    for p in walk:
        # A predict tick rewrites the probability columns and leaves the
        # always-preserve list (price, stake, edges) as it found them.
        row["yrfi_prob"] = str(p)
        row["nrfi_prob"] = str(round(1.0 - p, 4))
        _tick(row)
        seen_stakes.append(float(row["units_risked"]))
        seen_edges.append(row["edge_on_pick"])
        assert {c: row[c] for c in frozen} == frozen, (
            "the re-derive re-prices the row -- it must only re-SIZE it")

    assert seen_stakes == expected
    assert row["units_risked"] == "5.0", (
        "the pick locked on p=0.6288 and the rule says 5u; 2u is the bug")

    # The edge triple moves WITH the stake.  Live, the row stored
    # edge_on_pick=0.0376 (computed at 02:00 from p=0.5831) while the board
    # printed +8.3% from the row's own current probability -- the same number
    # disagreeing with itself on two surfaces.
    assert seen_edges == ["0.0376", "0.0605", "0.0833"]
    assert row["edge_yrfi"] == row["edge_on_pick"]   # YRFI pick -> same figure
    assert row["edge_nrfi"] == "-0.1288"             # all three move together
    # Implied probability is a function of the PRICE, so it must sit still
    # while the edge moves.  If it drifted, the edge change would be coming
    # from the wrong input.
    assert row["implied_yrfi_prob"] == "0.5455"

    # T2.58 is untouched: a pre-lock re-derive records the stake, it does not
    # commit the bet.  Flipping this to "Y" is what the lock window is for.
    assert row["bet_placed"] == "N"


@pytest.mark.regression
def test_three_ticks_at_a_static_probability_are_byte_identical(rederive_on):
    """THE 2026-07-28 P0-1 OSCILLATION GUARD, RESTATED AT THE CALLER.

    tests/test_money.py already proves `kelly_reset_daily_committed` works --
    but it calls the reset itself, inside its own helper.  That pins the reset
    function, not that any production caller calls it.  This pins the caller:
    three consecutive ticks over the same slate, nothing about the world
    changed, and the published stakes must be identical to the byte.  Live,
    the P0-1 symptom was a stake moving full -> trimmed -> zero every five
    minutes and then freezing at whatever the lock happened to catch.

    The slate is the real cap-bound 2026-07-31 one, and the vector deliberately
    SUMS PAST THE 15u DAILY CAP.  That is rule R1 doing its job, not a leak: a
    pre-lock figure is a PROJECTION, and a projection must be a pure function
    of (probability, price).  The moment it consumes the shared daily budget it
    becomes order-dependent, and two writers iterating different candidate sets
    make the same bet flip size every tick -- P0-1 wearing a new hat.  The
    budget is spent exactly once, at commit, by tools/lock_commit.py.
    """
    slate = [(0.7128, "-130"), (0.6903, "-155"),
             (0.6288, "-115"), (0.5885, "-140")]

    def tick(rows):
        return [float(_tick(r)["units_risked"]) for r in rows]

    # Each row carries a STALE projection from an earlier tick (1.0u), which
    # is the state the victims were in: sized once, never revisited.
    def fresh():
        return [_row(p, yrfi_odds=o, game_pk=f"pk{i}", units_risked="1.0")
                for i, (p, o) in enumerate(slate)]

    rows = fresh()
    first = tick(rows)
    assert tick(rows) == first
    assert tick(rows) == first

    # ...and the same answer from a cold process, which is the case a
    # long-lived Railway loop never gets to re-check for itself.
    tracker.kelly_reset_daily_committed()
    cold = tick(fresh())
    assert cold == first

    assert first == [tracker.kelly_stake_units(p, o) for p, o in slate]
    assert sum(first) > tracker.KELLY_MAX_DAILY_FRAC * 100.0, (
        "the pre-lock projection has started allocating against the daily "
        "budget -- see rule R1 in kelly_reset_daily_committed")


def test_rederive_does_not_touch_the_daily_tally(rederive_on):
    """RULE R1, pinned directly.

    `kelly_stake_units` mutates `_daily_committed` as a side effect of being
    CALLED with a game_date, not of its result being used.  So a projection
    that quietly started passing one would spend the night's 15u budget on
    stakes nobody ever published, and the real commits would come back trimmed
    or zero with nothing to explain it.  The batch epoch must not move either:
    a projection is not a batch.
    """
    iso, _ = _slate(4.0)
    tracker._daily_committed[iso] = 7.5      # tonight already has 7.5u locked
    before_tally = dict(tracker._daily_committed)
    before_epoch = tracker._kelly_batch_epoch

    sized = []
    for i, p in enumerate((0.7128, 0.6903, 0.6288, 0.5885, 0.6600)):
        row = _tick(_row(p, game_pk=f"pk{i}", units_risked="1.0"))
        sized.append(float(row["units_risked"]))

    # NOT VACUOUS: the slate really was re-sized, and to more than one night's
    # budget -- so an allocating projection would certainly have shown up.
    assert sum(sized) > tracker.KELLY_MAX_DAILY_FRAC * 100.0
    assert tracker._daily_committed == before_tally
    assert tracker._kelly_batch_epoch == before_epoch


# ---------------------------------------------------------------------------
# the doors it must never open
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_a_decided_no_bet_is_never_reopened(rederive_on):
    """THE 2026-07-28 P0-2 BET-FABRICATION DOOR.

    `bet_placed="N"` with no positive stake is a RECORDED REFUSAL -- Kelly's
    edge gate declined it, or the daily cap left no room.  Re-opening it would
    manufacture the pair `N + units>0`, and that pair is the ONLY discriminator
    `tools/end_of_day_check.py` has between a T2.58 pre-lock pending and a
    deliberate no-bet.  Manufacture it and the orphan heal retroactively stamps
    the row `bet_placed=Y` after the game has graded, booking P&L for a wager
    nobody made.

    So: four ticks of drift in both directions -- past the bet/no-bet boundary
    and back -- and the row must not move at all.  Then the game finishes and
    the orphan finder must still see nothing.
    """
    row = _row(0.5831, bet_placed="N", units_risked="")
    for p in (0.5831, 0.6288, 0.5000, 0.7200):
        row["yrfi_prob"] = str(p)
        row["nrfi_prob"] = str(round(1.0 - p, 4))
        _tick(row)
        assert row["bet_placed"] == "N"
        assert row["units_risked"] == ""

    # NOT VACUOUS: the identical row carrying a published stake DOES re-derive.
    # Only the refusal is frozen.
    assert _moves(row, units_risked="2.0")

    eodc = _load_tool("end_of_day_check")

    # Midnight: the game graded while the row was still at "N".
    row["graded_result"] = "LOSS"
    assert eodc.find_orphaned_strong_bets([row], row["date"]) == [], (
        "the heal has been re-armed on a deliberate no-bet -- this is the "
        "2026-07-28 P0-2 incident, and it books P&L for a bet nobody placed")


@pytest.mark.regression
def test_a_published_stake_is_never_blanked(rederive_on):
    """A PROJECTION MAY NEVER BLANK A PUBLISHED STAKE.

    The bet/no-bet cliff is ~0.18pp wide at -120 with a 0 -> 0.5u
    discontinuity, while measured pre-lock drift on the three victims was
    2.6 / 4.6 / 9.8pp.  Let a projection zero itself and a play blinks on and
    off a paying board all evening -- the "picks appear to disappear" pattern
    the operator has been burned by three times.  A blank could not propagate
    anyway: db/supabase_writer drops a blank write and the CSV-from-Supabase
    sync pulls the stale positive straight back over it.

    The honest zero is decided ONCE, at commit, where it is written as a
    literal 0 rather than a blank.
    """
    row = _row(0.5000, units_risked="5.00")   # p=0.50 at -120 is -EV
    assert tracker.kelly_stake_units(0.5000, "-120") == 0.0
    _tick(row)

    assert row["units_risked"] != ""
    assert row["units_risked"] == tracker._fmt(tracker.KELLY_ROUNDED_FLOOR, 2)
    assert float(row["units_risked"]) == 0.5
    assert row["bet_placed"] == "N"


def test_lean_is_never_kelly_sized(rederive_on):
    """LEAN is TRACK-ONLY (Phase 1.3, 2026-05-12) -- never auto-bet regardless
    of edge.  Its `units_risked` is a notional figure kept only so the
    counterfactual P&L can be computed, so re-deriving it would put a Kelly
    stake on a row the system has no intention of betting, and the board would
    print it next to the real ones."""
    row = _row(0.6288, strength="LEAN", units_risked="0.5")
    _tick(row)

    assert row["units_risked"] == "0.5"
    # THE EDGE COLUMN IS THE SENSITIVE HALF HERE.  `_size_row_stake` has its
    # own LEAN early-return, so deleting the re-derive's strength filter would
    # still leave units_risked at 0.5 -- but it would refresh edge_on_pick to
    # 0.0833, publishing a live edge next to a notional stake that is not a
    # bet.  Two guards, and only this assertion can tell them apart.
    assert row["edge_on_pick"] == ""
    # NOT VACUOUS: the same row at STRONG re-derives to a real Kelly stake.
    assert _moves(row, pick_strength="STRONG")


def test_cluster_demoted_row_is_never_resized(rederive_on):
    """A cluster demotion is an OPERATOR DECISION recorded in
    `data/cluster_demotions.json`, and `pick_label` is its durable marker --
    the canonical "is this row demoted?" test everywhere else in the system.

    The demotion writes `bet_placed='N' + units_risked=''`, so the
    decided-no-bet door usually catches these too.  This fixture deliberately
    carries a POSITIVE stake, because that combination is reachable: the
    predictor regenerates `pick_side`/`pick_strength` back to STRONG on the
    next tick, and a Supabase->CSV sync can restore a stale positive stake over
    the blanked one.  When that happens the label is the only thing left
    holding the door, and a re-derive would silently un-demote a bet the
    operator switched off.
    """
    row = _row(0.6288, units_risked="2.0",
               pick_label="PASS - Cluster demotion: STRONG YRFI (thin_pitcher_strong_v1)")
    _tick(row)

    assert row["units_risked"] == "2.0"
    assert row["bet_placed"] == "N"
    # NOT VACUOUS: clear the label and the identical row re-sizes to 5u.
    assert _moves(row, pick_label="")


def test_placeholder_game_time_is_never_sized(rederive_on):
    """Doubleheader Game 2 enters the slate as "After Game 1" / "TBD" 6-12
    hours before Game 1 even ends.  With no parseable start time we cannot
    know whether we are pre-lock, and `_is_inside_lock_window` is unbounded
    ABOVE -- so a row sized off a placeholder keeps being re-sized straight
    through first pitch and past it.  Refusing to size is the only safe
    answer; the real time arrives when DH-1 finishes and normal sizing
    resumes."""
    for placeholder in ("After Game 1", "TBD", ""):
        row = _row(0.6288, game_time_et=placeholder, units_risked="2.0")
        _tick(row)
        assert row["units_risked"] == "2.0"
        assert row["edge_on_pick"] == ""

    # NOT VACUOUS: the same row with a real time 4h out re-sizes.
    iso, gtet = _slate(4.0)
    assert _moves(_row(0.6288, game_time_et="After Game 1", units_risked="2.0"),
                  date=iso, game_time_et=gtet)


def test_started_game_is_never_resized(rederive_on):
    """First pitch has been thrown.  Whatever the row says now is what the
    subscriber acted on, and the board's "not locked" copy is long gone -- so
    the stake is history, not a projection.  Re-sizing here would also re-arm
    the end-of-day orphan heal against a figure that was never published.

    The game time is derived from `now`, including its DATE: two hours ago at
    00:30 ET is yesterday's slate, and a hardcoded date would reconstruct the
    wrong instant (and, a day later, trip the 24h defensive lock instead of
    the check this test is actually about).
    """
    iso, gtet = _slate(-2.0)
    row = _row(0.6288, units_risked="2.0", date=iso, game_time_et=gtet)
    assert tracker._game_has_started(gtet, iso) is True

    _tick(row)
    assert row["units_risked"] == "2.0"
    assert row["edge_on_pick"] == ""

    # NOT VACUOUS: move the same game 4h into the future and it re-sizes.
    future_iso, future_gtet = _slate(4.0)
    assert _moves(row, date=future_iso, game_time_et=future_gtet)


def test_a_placed_bet_is_frozen(rederive_on):
    """T2.23. Once `bet_placed="Y"` the operator is IN the bet at that price;
    the stake is a historical fact about money already at risk, not a figure
    the model may keep revising.  Not on the assignment's list, but it is the
    door between PART 1 and PART 2 -- the commit in tools/lock_commit.py flips
    exactly this flag, and if the re-derive did not respect it the two writers
    would fight over every locked row."""
    row = _row(0.6288, bet_placed="Y", units_risked="2.0")
    _tick(row)

    assert row["bet_placed"] == "Y"
    assert row["units_risked"] == "2.0"
    assert _moves(row, bet_placed="N")


def test_the_locked_flag_from_log_picks_freezes_the_row(rederive_on):
    """`locked` is not derived here -- `log_picks` passes it, computed by
    `_pick_is_locked`, which carries three defensive locks the re-derive's own
    filters do not reproduce (graded, slate >24h past, `created_at` >12h
    stale).  If this argument were ignored, a row the predictor had already
    decided to preserve would still have its stake rewritten underneath the
    preserve list, and the two halves of one merge would disagree.

    Also on the assignment's list only by implication, and worth its own case:
    it is the ONE filter that lives outside this function."""
    row = _row(0.6288, units_risked="2.0")
    tracker._rederive_pre_lock_stake(row, season=2026, locked=True)
    assert row["units_risked"] == "2.0"
    assert row["edge_on_pick"] == ""

    # NOT VACUOUS: the identical row with locked=False re-sizes to 5u.
    tracker._rederive_pre_lock_stake(row, season=2026, locked=False)
    assert row["units_risked"] == "5.0"


def test_terminal_grades_are_frozen(rederive_on):
    """The five terminal grades from `_pick_is_locked`.  POSTPONED and
    SUSPENDED count: those rows are re-graded if the game resumes and must
    keep the stake they carried, and CLAUDE.md's data rules keep them in the
    ledger rather than deleting them."""
    for grade in ("WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED"):
        row = _row(0.6288, units_risked="2.0", graded_result=grade)
        _tick(row)
        assert row["units_risked"] == "2.0", f"{grade} row was re-sized"

    assert _moves(_row(0.6288, units_risked="2.0", graded_result="LOSS"),
                  graded_result="")


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    """PART 1 SHIPS OFF.  It writes to the money path, so merging it must not
    switch it on -- the operator turns it on per host, exactly like
    PREDICTOR_SCRAPE_DK.  Two hosts re-deriving from two independent model
    fetches would flap the published stake.

    This test does NOT take the `rederive_on` fixture.  It deletes the
    variable outright rather than trusting the ambient environment, so it
    still means something on a machine where the operator has enabled the
    feature.
    """
    monkeypatch.delenv("NRFI_STAKE_REDERIVE", raising=False)
    row = _row(0.6288, units_risked="2.0")
    before = dict(row)
    _tick(row)
    assert row == before

    for off in ("", "skip", "disabled", "0", "Enable"):
        monkeypatch.setenv("NRFI_STAKE_REDERIVE", off)
        row = _row(0.6288, units_risked="2.0")
        _tick(row)
        assert row["units_risked"] == "2.0", f"{off!r} was read as ON"

    # NOT VACUOUS: the same row with the switch ON re-sizes to 5u.
    monkeypatch.setenv("NRFI_STAKE_REDERIVE", "enabled")
    _tick(row)
    assert row["units_risked"] == "5.0"


def test_the_switch_is_case_and_whitespace_tolerant(monkeypatch):
    """`NRFI_STAKE_REDERIVE` is typed into a Railway variables form by hand.
    "Enabled" with a trailing space must not read as OFF and silently leave
    the bug in place with no error anywhere."""
    for on in ("enabled", "ENABLED", "  Enabled  "):
        monkeypatch.setenv("NRFI_STAKE_REDERIVE", on)
        row = _row(0.6288, units_risked="2.0")
        _tick(row)
        assert row["units_risked"] == "5.0", f"{on!r} was read as OFF"

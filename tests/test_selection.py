"""
Which bet exists, and when it stops changing.

`_row_is_nights_top_pick` gates EVERY pick-facing subscriber alert under the
№1-only policy, and `pl_calc.select_top_picks` builds the published win-loss
record from the same idea. `_pick_is_locked` decides when a pick stops moving.
Between them they decide what gets sold and what gets counted.

Rivals are read from the ledger on disk, so every test here monkeypatches
`tracker._read_rows` via a fixture with teardown -- mutating the module global
in place (as an earlier draft of this suite did) leaks a stub into every later
test and can make the whole file pass while pinning nothing.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402

pytestmark = pytest.mark.money


def _row(away, home, p, side="YRFI", odds="-135", strength="STRONG",
         date="2026-08-06", **kw):
    r = {
        "date": date, "away_team": away, "home_team": home,
        "nrfi_prob": str(p), "pick_side": side, "pick_strength": strength,
        "market_yrfi_odds": odds if side == "YRFI" else "",
        "market_nrfi_odds": odds if side == "NRFI" else "",
        "bet_placed": "Y", "game_pk": f"{away}{home}",
    }
    r.update(kw)
    return r


@pytest.fixture
def ledger(monkeypatch):
    """Install a fake on-disk ledger, and REMOVE it afterwards."""
    def _install(rows):
        monkeypatch.setattr(tracker, "_read_rows", lambda _p: rows)
        monkeypatch.setattr(tracker, "_csv_path", lambda _s: "unused.csv")
    return _install


# ---------------------------------------------------------------------------
# the rank tuple -- mirrors dashboard/lib/top-pick-rank.ts
# ---------------------------------------------------------------------------

def test_yrfi_ranks_on_p_itself_and_nrfi_inverts_it():
    """Smaller tuple = stronger. A YRFI bet is confident when p(no run) is
    LOW; an NRFI bet is confident when it is HIGH. Getting this backwards
    would invert the entire №1 selection."""
    y = tracker._top_pick_rank_tuple("YRFI", 0.4064, "-135", "NYY@BOS")
    n = tracker._top_pick_rank_tuple("NRFI", 0.4064, "-135", "NYY@BOS")
    assert y[0] == 0.4064
    assert n[0] == pytest.approx(1 - 0.4064)


def test_missing_price_cannot_win_a_tiebreak():
    """implied defaults to 1.0 -- the worst possible -- so a row with no
    captured price never beats a priced row on the tiebreak it has no
    information for."""
    for missing in ("", None, "0"):
        assert tracker._top_pick_rank_tuple("YRFI", 0.4064, missing, "A@B")[1] == 1.0


def test_better_price_wins_an_exact_probability_tie():
    """NOT decorative. The retired calibrator emitted flat steps -- 115 games
    landed on p=0.4064 alone -- so ties are common, and without this rule the
    winner was whichever row the loader happened to return first."""
    cheap = tracker._top_pick_rank_tuple("YRFI", 0.4064, "-120", "A@B")
    dear = tracker._top_pick_rank_tuple("YRFI", 0.4064, "-135", "C@D")
    assert cheap < dear


def test_game_name_settles_a_full_tie():
    """Total determinism: same probability, same price. Without a final key
    the same season read 58-30 from Supabase and 56-32 from the CSV."""
    a = tracker._top_pick_rank_tuple("YRFI", 0.4064, "-135", "AAA@BBB")
    z = tracker._top_pick_rank_tuple("YRFI", 0.4064, "-135", "ZZZ@YYY")
    assert a < z


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def test_gate_picks_the_most_confident_play(ledger):
    rows = [_row("NYY", "BOS", 0.30), _row("LAD", "SFG", 0.45)]
    ledger(rows)
    assert tracker._row_is_nights_top_pick(rows[0]) is True
    assert tracker._row_is_nights_top_pick(rows[1]) is False


def test_a_lean_rival_is_not_a_number_one_candidate(ledger):
    """LEAN is tracked and never wagered, so a 'top bet' that is not a bet
    would point at money the system does not intend to risk."""
    strong = _row("LAD", "SFG", 0.45)
    rows = [_row("NYY", "BOS", 0.10, strength="LEAN"), strong]
    ledger(rows)
    assert tracker._row_is_nights_top_pick(strong) is True


def test_rows_from_other_dates_are_excluded(ledger):
    today = _row("LAD", "SFG", 0.45)
    rows = [_row("NYY", "BOS", 0.10, date="2026-08-05"), today]
    ledger(rows)
    assert tracker._row_is_nights_top_pick(today) is True


def test_a_non_side_row_short_circuits_true(ledger):
    """PASS / LINEUP PENDING rows are not ranked at all -- the gate fails open
    so a broken classification spams rather than silences."""
    ledger([])
    assert tracker._row_is_nights_top_pick(_row("NYY", "BOS", 0.3, side="PASS")) is True


def test_gate_fails_open_on_unparseable_input(ledger):
    """FAIL OPEN IS DELIBERATE (CLAUDE.md): a broken gate must spam, never
    silence. A silenced №1 is a night of product lost with no error."""
    ledger([])
    assert tracker._row_is_nights_top_pick(_row("NYY", "BOS", "not-a-number")) is True


# ---------------------------------------------------------------------------
# Both of these were DEFECTS pinned xfail on 2026-08-07 and fixed the same day
# (T8.13, T8.14). The strict marker did its job: fixing them turned the suite
# red and forced the markers off, which is why these now assert plainly.
# ---------------------------------------------------------------------------

def test_doubleheader_halves_do_not_both_become_number_one(ledger):
    a = _row("NYY", "BOS", 0.30, game_pk="777001")
    b = _row("NYY", "BOS", 0.35, game_pk="777002")
    rows = [a, b, _row("LAD", "SFG", 0.45)]
    ledger(rows)
    called = [tracker._row_is_nights_top_pick(r) for r in (a, b)]
    assert called.count(True) == 1


def test_a_demoted_no_bet_row_cannot_take_the_number_one_slot(ledger):
    demoted = _row("NYY", "BOS", 0.30, bet_placed="N")
    real = _row("LAD", "SFG", 0.35)
    ledger([demoted, real])
    assert tracker._row_is_nights_top_pick(real) is True


# ---------------------------------------------------------------------------
# the locks
#
# THESE TESTS MUST NOT HARDCODE A SLATE DATE.  `_pick_is_locked` has three
# defensive locks, and the second is "slate date more than 24 h in the past".
# A literal date therefore passes on the day it is written and starts failing
# by itself a day or two later, with no code change -- which is exactly what
# happened: this file shipped 2026-08-07 pinned to "2026-08-06" and went red
# overnight, blaming a fix that had not touched tracker.py.
#
# A test that fails because the calendar moved is not testing the code.  The
# NEGATIVE cases (which assert a row does NOT lock) must use a live slate
# date; the POSITIVE ones may use anything, since they lock on grade first.
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

TODAY_ET = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def test_terminal_grades_lock_a_pick(ledger):
    """A graded pick must never be rewritten by a later refresh -- that is the
    4/30 grade-reset incident.

    The set is EXACTLY these five. CANCELLED and VOID deliberately do NOT
    lock, because a cancelled game can be replayed and a void needs re-grading.

    ASYMMETRY WORTH KNOWING: `discord_broadcasts._terminal` uses a DIFFERENT,
    wider set -- it adds CANCELLED and VOID -- because it answers a different
    question ("is the slate finished, may I publish results?") rather than
    ("may this row still change?"). Two sets, two purposes; pinned here so a
    future reader does not "unify" them into one and quietly make a cancelled
    game unrewritable."""
    for g in ("WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED"):
        assert tracker._pick_is_locked(_row("A", "B", 0.4, graded_result=g), "2026-08-06") is True
    for g in ("CANCELLED", "VOID", "PENDING", ""):
        assert tracker._pick_is_locked({"graded_result": g, "date": TODAY_ET},
                                       TODAY_ET) is False


def test_grade_check_ignores_case_and_whitespace(ledger):
    assert tracker._pick_is_locked(_row("A", "B", 0.4, graded_result="  win "), "2026-08-06") is True


def test_an_empty_row_does_not_lock(ledger):
    assert tracker._pick_is_locked({}, TODAY_ET) is False


# ---------------------------------------------------------------------------
# the published record must count the night's actual No.1
# ---------------------------------------------------------------------------

def test_an_unsettled_top_play_excludes_the_night_rather_than_promoting_the_runner_up():
    """THE 2026-06-11 DEFECT. `select_top_picks` used to filter unsettled rows
    out BEFORE ranking, so a postponed №1 silently promoted the second-best
    game and counted ITS result as the №1's.

    Live: the top YRFI play was ATL@CWS (p_nrfi 0.3219) and it was POSTPONED;
    the record counted CHC@COL (0.3543), which LOST. A game the system would
    never have graded that night contributed a loss to the published record.

    A postponement is NO ACTION -- not a result, and not a licence to
    substitute a different game."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("plc", "tools/pl_calc.py")
    plc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plc)

    def row(away, home, p, graded, odds="-135"):
        return {
            "date": "2026-06-11", "away_team": away, "home_team": home,
            "nrfi_prob": str(p), "pick_side": "YRFI", "pick_strength": "STRONG",
            "market_yrfi_odds": odds, "bet_placed": "Y",
            "graded_result": graded,
            "units_risked": "" if graded == "POSTPONED" else "1",
            "profit_loss_units": "" if graded == "POSTPONED" else "-1",
        }

    # The most confident play is postponed; a weaker one lost.
    picks = plc.select_top_picks([
        row("ATL", "CWS", 0.3219, "POSTPONED"),
        row("CHC", "COL", 0.3543, "LOSS"),
    ])
    assert picks == [], (
        "the night's №1 did not settle, so the night must contribute NO "
        "result -- not the runner-up's loss")

    # Sanity: when the top play DOES settle, it is the one counted.
    picks = plc.select_top_picks([
        row("ATL", "CWS", 0.3219, "WIN"),
        row("CHC", "COL", 0.3543, "LOSS"),
    ])
    assert len(picks) == 1
    assert picks[0]["away_team"] == "ATL"

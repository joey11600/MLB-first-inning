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
# KNOWN DEFECTS -- xfail(strict) so fixing one turns this file RED and forces
# the marker to be removed. They are NOT pinned as correct behaviour.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: self-exclusion is by 'AWAY@HOME' name, not game_pk, so on a "
    "doubleheader each half excludes the OTHER as 'self' and both are called "
    "№1 -- two 'tonight's №1 play' alerts under a №1-only policy. Not yet "
    "triggered live (no real slate has two STRONG rows sharing a name) but "
    "18 slate+name keys already carry more than one row."))
def test_doubleheader_halves_do_not_both_become_number_one(ledger):
    a = _row("NYY", "BOS", 0.30, game_pk="777001")
    b = _row("NYY", "BOS", 0.35, game_pk="777002")
    rows = [a, b, _row("LAD", "SFG", 0.45)]
    ledger(rows)
    called = [tracker._row_is_nights_top_pick(r) for r in (a, b)]
    assert called.count(True) == 1


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: the rival scan filters on pick_strength only and never checks "
    "bet_placed, so a row demoted to no-bet by tools/apply_cluster_demotion.py "
    "still competes for №1 and wins -- silencing the alert for the game the "
    "money is actually on. Live on 1 of 123 slates (2026-04-29 TB@CLE)."))
def test_a_demoted_no_bet_row_cannot_take_the_number_one_slot(ledger):
    demoted = _row("NYY", "BOS", 0.30, bet_placed="N")
    real = _row("LAD", "SFG", 0.35)
    ledger([demoted, real])
    assert tracker._row_is_nights_top_pick(real) is True


# ---------------------------------------------------------------------------
# the locks
# ---------------------------------------------------------------------------

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
        assert tracker._pick_is_locked({"graded_result": g, "date": "2026-08-06"},
                                       "2026-08-06") is False


def test_grade_check_ignores_case_and_whitespace(ledger):
    assert tracker._pick_is_locked(_row("A", "B", 0.4, graded_result="  win "), "2026-08-06") is True


def test_an_empty_row_does_not_lock(ledger):
    assert tracker._pick_is_locked({}, "2026-08-06") is False

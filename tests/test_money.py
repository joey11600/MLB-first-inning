"""
The money path: staking, P&L, and the bankroll.

WHY THESE EXIST. Until 2026-08-07 nothing in this repo tested the functions
that decide how much real money goes on a game. The ~40 files named test_*.py
under tools/ and scripts/archive/ are model experiments that assert nothing.

EVERY EXPECTED VALUE HERE WAS EXECUTED AGAINST THE REAL CODE, not derived by
hand. A test asserting a hand-computed number would pin the author's arithmetic
rather than the system's behaviour, which is how a suite goes green while the
money moves.

Tests marked `regression` pin a bug this system has ALREADY SHIPPED once. Those
are worth more than the happy paths: each one is a night that cost money or
mis-stated the record.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402

pytestmark = pytest.mark.money


# ---------------------------------------------------------------------------
# the constants ARE the published policy
# ---------------------------------------------------------------------------

def test_kelly_constants_are_the_published_money_policy():
    """These nine numbers are what CLAUDE.md promises subscribers: quarter
    Kelly, 1u = 1% of a fixed 100u bank, 10u per bet, 15u per day. A silent
    drift in any one re-sizes every stake sold to every subscriber."""
    assert tracker.KELLY_ENABLED is True
    assert tracker.KELLY_FRACTION == 0.25
    assert tracker.KELLY_BANKROLL_UNITS == 100.0
    assert tracker.KELLY_MAX_STAKE_FRAC == 0.10      # 10u per bet
    assert tracker.KELLY_MAX_DAILY_FRAC == 0.15      # 15u per day
    assert tracker.KELLY_MIN_STAKE_UNITS == 0.10
    assert tracker.KELLY_STAKE_ROUNDING == 1.0
    assert tracker.KELLY_ROUNDED_FLOOR == 0.5
    assert tracker.KELLY_BANKROLL_EPOCH == "2026-07-28"


# ---------------------------------------------------------------------------
# the rounding rule -- the 2026-08-06 incident
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_double_rounding_lifts_3_4975_to_four_units():
    """THE SD@ARI BUG, 2026-08-06. Raw quarter-Kelly is 3.4975u. Python rounds
    TWICE -- round(x, 2) -> 3.5, then round(3.5) -> 4 -- while a single
    JS-style Math.round gives 3. Discord published "4 units" and the board
    printed "STAKE 3.00u" for the same bet.

    Python is the authority: it is what stakes the money. If this flips to 3,
    every bet whose raw stake lands in [x.495, x.5) changes by a whole unit."""
    assert tracker.kelly_stake_units(0.634, "-135") == 4.0


@pytest.mark.regression
def test_double_rounding_is_not_special_cased_to_one_boundary():
    """A fix that only handled 3.4975 would still be 100% wrong here."""
    assert tracker.kelly_stake_units(0.5999574468, "-135") == 2.0


@pytest.mark.regression
def test_python_rounds_half_to_even_not_half_up():
    """raw 2.4975 -> round(,2) = 2.5 -> round(2.5) = 2, NOT 3.

    Anyone 'fixing' the double-round with a half-up helper moves this bet from
    2u to 3u -- a 50% stake increase on a live play, with nothing to flag it."""
    assert tracker.kelly_stake_units(0.6169787234, "-135") == 2.0


@pytest.mark.regression
def test_rounded_floor_lifts_a_sub_half_unit_to_half_not_zero():
    """Plain whole-unit rounding turned every sub-0.5u stake into 0 -- a NO
    BET -- silently dropping 16 of 301 bets in the replay. KELLY_ROUNDED_FLOOR
    is what stops a display convenience acting as a hidden bet gate."""
    assert tracker.kelly_stake_units(0.5761702128, "-135") == 0.5
    assert tracker.kelly_stake_units(0.5829787234, "-135") == 0.5


def test_min_stake_gate_sits_above_the_rounding_block():
    """raw 0.09u is a deliberate refusal and must stay one; raw 0.10u is a
    real 0.5u bet. If the gate ever moves BELOW the rounding block, a refusal
    gets floored UP into money on a game the model declined."""
    assert tracker.kelly_stake_units(0.5760000000, "-135") == 0.0
    assert tracker.kelly_stake_units(0.5761702128, "-135") == 0.5


# ---------------------------------------------------------------------------
# 0.0 and None mean different things
# ---------------------------------------------------------------------------

def test_no_edge_returns_zero_meaning_do_not_bet():
    """0.0 = 'Kelly forbids this'. The caller sets bet_placed='N'."""
    assert tracker.kelly_stake_units(0.50, "-135") == 0.0
    assert tracker.kelly_fraction_of_bankroll(0.50, "-135") == 0.0


def test_unusable_inputs_return_none_meaning_no_opinion():
    """None = 'cannot size, fall back to flat'. If any of these returned 0.0,
    a missed DK scrape would silently CANCEL the bet instead of falling back --
    picks appearing to vanish, which is the failure the operator gets burned
    by most."""
    for bad_price in ("", "abc", "0"):
        assert tracker.kelly_stake_units(0.60, bad_price) is None
    for bad_p in (None, 0.0, 1.0):
        assert tracker.kelly_stake_units(bad_p, "-135") is None


# ---------------------------------------------------------------------------
# caps
# ---------------------------------------------------------------------------

def test_per_bet_cap_is_a_hard_ten_units():
    """A bad probability -- a calibrator bug, a 1-p sign flip -- would
    otherwise stake 20% of bank on one first inning."""
    assert tracker.kelly_stake_units(0.90, "+100") == 10.0
    assert tracker.kelly_stake_units(0.85, "-110") == 10.0
    assert tracker.kelly_stake_units(0.80, "-120") == 10.0


def test_daily_cap_holds_a_slate_to_fifteen_units():
    """WITHOUT game_date the same slate returns 31.0u -- more than double the
    published ceiling. If a caller ever stops passing game_date, a heavy slate
    risks 31% of bank in one night and nothing errors."""
    tracker.kelly_reset_daily_committed()
    slate = [(0.72, "-150"), (0.70, "-160"), (0.68, "-140"),
             (0.66, "-125"), (0.64, "-120")]
    capped = [tracker.kelly_stake_units(p, o, game_date="1999-01-01")
              for p, o in slate]
    assert capped == [8.0, 6.0, 1.0, 0.0, 0.0]
    assert sum(capped) == 15.0

    tracker.kelly_reset_daily_committed()
    uncapped = [tracker.kelly_stake_units(p, o) for p, o in slate]
    assert sum(uncapped) > 15.0


def test_exhausted_daily_budget_is_a_refusal_not_a_floored_half_unit():
    """0.05u of remaining room must produce 0.0, not get floored up to 0.5u.
    Otherwise the floor outranks a risk control and the cap leaks a bet every
    heavy night."""
    tracker.kelly_reset_daily_committed()
    tracker._daily_committed["1999-01-01"] = 14.95
    assert tracker.kelly_stake_units(0.85, "-110", game_date="1999-01-01") == 0.0


def test_rounding_never_rounds_up_through_a_cap():
    """With 7.5u of room, round(7.5) would be 8.0 and breach the ceiling, so
    the EXACT stake is kept. An awkward 7.5u beats quietly exceeding a limit
    the operator set."""
    tracker.kelly_reset_daily_committed()
    tracker._daily_committed["1999-01-01"] = 7.5
    assert tracker.kelly_stake_units(0.85, "-110", game_date="1999-01-01") == 7.5


def test_rounding_down_within_a_cap_is_allowed_to_stand():
    """Companion to the above: the cap check compares the ROUNDED figure, so a
    round-DOWN stands. A naive 'always keep exact when capped' fix would
    publish 4.5u here instead of 4u."""
    tracker.kelly_reset_daily_committed()
    tracker._daily_committed["1999-01-01"] = 10.5
    assert tracker.kelly_stake_units(0.85, "-110", game_date="1999-01-01") == 4.0


@pytest.mark.regression
def test_repeated_batches_produce_identical_stakes():
    """THE T7.x OSCILLATION. The daily cap was double-counted, so stakes moved
    every 5-minute import tick and then froze at whatever the lock caught. The
    reset between batches is what makes a tick idempotent."""
    slate = [(0.72, "-150"), (0.70, "-160"), (0.68, "-140")]

    def batch():
        tracker.kelly_reset_daily_committed()
        return [tracker.kelly_stake_units(p, o, game_date="1999-01-02")
                for p, o in slate]

    first = batch()
    assert batch() == first
    assert batch() == first


@pytest.mark.regression
def test_without_a_reset_the_double_count_collapses_the_slate():
    """Pins WHY the reset is required: run two ticks without it and the second
    slate is zeroed entirely, because the first tick's exposure is still on the
    books."""
    slate = [(0.72, "-150"), (0.70, "-160"), (0.68, "-140")]
    tracker.kelly_reset_daily_committed()
    tick1 = [tracker.kelly_stake_units(p, o, game_date="1999-01-03") for p, o in slate]
    tick2 = [tracker.kelly_stake_units(p, o, game_date="1999-01-03") for p, o in slate]
    assert sum(tick1) > 0
    assert tick2 == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# the unit model: 1u = 1% of a FIXED bank
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_stake_is_bankroll_independent():
    """THE THING THAT MAKES PICKS SELLABLE. The operator sells these picks, so
    the unit count must be identical for a $25k follower and a $1k one.
    Quarter-Kelly's fraction is bankroll-free; sizing must never read the
    current bank again (it did until 2026-07-30, producing 5.97u on one
    surface and 17.00u on another for the SAME bet)."""
    base = tracker.kelly_stake_units(0.6343, "-135")
    for fake_bank in (250.0, 12.0, 1000.0):
        tracker._bankroll_cache = fake_bank
        assert tracker.kelly_stake_units(0.6343, "-135") == base
    tracker._bankroll_cache = None
    tracker.kelly_reset_daily_committed()


# ---------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------

def _row(**kw):
    r = {
        "pick_side": "YRFI", "graded_result": "WIN", "units_risked": "1",
        "bet_placed": "Y", "market_yrfi_odds": "-110", "market_nrfi_odds": "",
    }
    r.update(kw)
    return r


def test_win_at_negative_price_pays_stake_times_100_over_abs_odds():
    assert tracker._calc_pnl(_row(units_risked="6.83", market_yrfi_odds="-135")) == "5.059"


def test_loss_returns_negative_stake_and_ignores_the_price():
    assert tracker._calc_pnl(_row(graded_result="LOSS", units_risked="6.83")) == "-6.83"


@pytest.mark.regression
def test_bet_placed_N_books_exactly_zero():
    """profit_loss_units only fills for real bets at real prices. A nightly
    heal once FABRICATED bets from deliberate no-bet rows."""
    assert tracker._calc_pnl(_row(bet_placed="N", units_risked="6.83")) == "0.0"


def test_unpriced_row_falls_back_to_flat_minus_110():
    """CLAUDE.md: never fabricate odds. The fallback is documented and
    deliberate -- but it IS an invented price, which is why the season figure
    below is worth watching."""
    assert tracker._calc_pnl(_row(market_yrfi_odds="")) == "0.909"


def test_wrong_side_price_is_never_used_to_pay_a_win():
    """A YRFI pick must not be paid from the NRFI column."""
    assert tracker._calc_pnl(
        _row(market_yrfi_odds="", market_nrfi_odds="+250")) == "0.909"


def test_non_terminal_grades_return_empty_not_zero():
    """Empty means 'not settled'. Zero would mean 'settled for nothing', which
    would drag an ungraded row into every sum."""
    for g in ("", "PENDING", "POSTPONED", "SUSPENDED", "VOID"):
        assert tracker._calc_pnl(_row(graded_result=g)) == ""

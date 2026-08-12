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


# ---------------------------------------------------------------------------
# T8.32 -- the No.1 keeps its stake when it locks last
# ---------------------------------------------------------------------------
#
# THE GAP THIS CLOSES. The 15u/day budget is handed out in LOCK order, and
# games lock at their own first-pitch-minus-60. T8.19 sorts each import batch
# best-bet-first, but two picks three hours apart are never in the same batch,
# so a weak 6:45 PM game takes its stake before a strong 9:40 PM game is even
# a candidate. On 2026-08-11 that left the 9:40 PM game 1u -- harmless only
# because the No.1 happened to be the 9:38 PM game.


def _slate_row(pk, away, home, prob_yrfi, yrfi_odds, date="2026-08-11",
               staked=None):
    """A STRONG YRFI pre-lock row, shaped like the real ledger.

    `units_risked` CARRIES THE PRE-LOCK PROJECTION, because that is what the
    real ledger holds and because `_is_declined_not_pending` reads a STRONG
    row at `N` with no stake as a DECLINE, not a pending play -- such a row is
    not a No.1 candidate on any surface. A fixture that left it blank would
    quietly test an empty slate."""
    proj = (tracker.kelly_stake_units(prob_yrfi, yrfi_odds)
            if staked is None else staked)
    return {
        "date": date, "game_pk": str(pk), "game_number": "1",
        "away_team": away, "home_team": home,
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "nrfi_prob": f"{1.0 - prob_yrfi:.4f}", "yrfi_prob": f"{prob_yrfi:.4f}",
        "market_nrfi_odds": "", "market_yrfi_odds": yrfi_odds,
        "bet_placed": "N",
        "units_risked": f"{proj:.2f}" if proj else "",
    }


def _commit(row, slate, monkeypatch, season=2026):
    """Size ONE row at its lock, in its own batch -- i.e. what actually
    happens in production, where each game locks hours from the next."""
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()
    tracker._size_row_stake(row, season=season, inside_lock=True,
                            units_lean=0.0, units_strong=1.0)
    return float(row["units_risked"])


# The 2026-08-11 slate with the two 9:40-ish games swapped in price, so the
# LATEST-locking game is the No.1. Same probabilities, same prices, same games.
_LATE_TOP_SLATE = [
    _slate_row(1, "CHC", "WSH", 0.6460, "-120"),   # 6:45 PM -- locks FIRST
    _slate_row(2, "TEX", "LAA", 0.7128, "-140"),   # 9:38 PM -- No.2
    _slate_row(3, "COL", "ARI", 0.7128, "-135"),   # 9:40 PM -- No.1, locks LAST
]


def test_number_one_is_identified_by_price_when_confidence_ties(monkeypatch):
    """Both 9:40-ish games are 71.28%, so the better price decides -- the same
    tie-break dashboard/lib/top-pick-rank.ts uses. If this flips, the whole
    reservation protects the wrong game."""
    monkeypatch.setattr(tracker, "_read_rows",
                        lambda _p: list(_LATE_TOP_SLATE))
    top = tracker._select_nights_top_pick("2026-08-11", 2026)
    assert top is not None and top["home_team"] == "ARI"


@pytest.mark.regression
def test_number_one_locking_last_still_gets_its_full_stake(monkeypatch):
    """THE 2026-08-11 NEAR MISS. Without the reservation the No.1 gets 1u,
    because the two weaker plays lock first and spend 14 of the 15u. The
    published No.1 is the play subscribers actually bet -- it must not be
    sized by an accident of the schedule."""
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    wsh, laa, ari = slate

    assert _commit(wsh, slate, monkeypatch) == 6.0    # unchanged: 7u of room
    assert _commit(laa, slate, monkeypatch) == 1.0    # No.2 takes the hit
    assert _commit(ari, slate, monkeypatch) == 8.0    # No.1 gets its full size

    assert sum(float(r["units_risked"]) for r in slate) == 15.0


@pytest.mark.regression
def test_without_the_reservation_the_number_one_is_the_one_trimmed(monkeypatch):
    """The counterfactual that makes the test above mean something: turn the
    reservation off and the SAME slate hands the No.1 a single unit."""
    monkeypatch.setattr(tracker, "_top_pick_reservation", lambda *a, **k: 0.0)
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    wsh, laa, ari = slate

    assert _commit(wsh, slate, monkeypatch) == 6.0
    assert _commit(laa, slate, monkeypatch) == 8.0
    assert _commit(ari, slate, monkeypatch) == 1.0    # the No.1, at 1u


def test_real_2026_08_11_slate_is_unchanged(monkeypatch):
    """A no-op on the night that prompted the change. The No.1 (TEX@LAA, the
    better price at -135) already locked before COL@ARI, so the reservation
    releases the moment it commits and nothing else moves. A fix that also
    re-sizes nights that were already correct is a bigger change than was
    asked for."""
    slate = [
        _slate_row(1, "CHC", "WSH", 0.6460, "-120"),   # 6:45 PM
        _slate_row(2, "TEX", "LAA", 0.7128, "-135"),   # 9:38 PM -- No.1
        _slate_row(3, "COL", "ARI", 0.7128, "-140"),   # 9:40 PM
    ]
    wsh, laa, ari = slate
    assert _commit(wsh, slate, monkeypatch) == 6.0
    assert _commit(laa, slate, monkeypatch) == 8.0
    assert _commit(ari, slate, monkeypatch) == 1.0


@pytest.mark.regression
def test_reservation_releases_once_the_number_one_is_committed(monkeypatch):
    """DOUBLE-COUNTING IS THE FAILURE MODE. `_select_nights_top_pick` reads
    DISK, where a game committed earlier in the same batch still says "N". If
    the reservation were held again on top of a stake already inside
    `_daily_committed`, every later pick would lose the No.1's stake twice."""
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    wsh, laa, ari = slate
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()

    # T8.19 order: the No.1 first, then the rest -- all inside ONE batch.
    for r in (ari, laa, wsh):
        tracker._size_row_stake(r, season=2026, inside_lock=True,
                                units_lean=0.0, units_strong=1.0)

    assert float(ari["units_risked"]) == 8.0
    assert sum(float(r["units_risked"]) for r in slate) == 15.0


def test_a_committed_number_one_is_not_reserved_for_twice(monkeypatch):
    """Across batches the No.1's frozen stake is already in `_committed_on`,
    so the reservation must return 0 -- otherwise a locked 8u No.1 would
    remove 16u from a 15u budget and zero the rest of the slate."""
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    wsh, laa, ari = slate
    ari["bet_placed"], ari["units_risked"] = "Y", "8.00"
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()

    assert tracker._top_pick_reservation(wsh, 2026) == 0.0
    assert _commit(wsh, slate, monkeypatch) == 6.0   # 15 - 8 committed = 7u room


def test_an_unstakeable_number_one_reserves_nothing(monkeypatch):
    """CLAUDE.md: never fabricate odds. A No.1 whose stake cannot be computed
    gets nothing held back for it rather than a made-up figure -- the cap then
    behaves exactly as it did before T8.32.

    The row still has to be a CANDIDATE to reach that branch, so it carries a
    projected stake but an unusable price. A No.1 with no price at all is
    excluded one level earlier, by `_is_declined_not_pending`, and a different
    game becomes No.1 -- which is the pre-existing rule on every surface, not
    something this reservation decides."""
    slate = [
        _slate_row(1, "CHC", "WSH", 0.6460, "-120"),
        _slate_row(2, "TEX", "LAA", 0.7128, "-140"),
        # Strictly the most confident, so it wins on rank alone -- an unusable
        # price scores implied=1.0 and could never win a tie-break.
        _slate_row(3, "COL", "ARI", 0.7300, "n/a", staked=8.0),
    ]
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()

    top = tracker._select_nights_top_pick("2026-08-11", 2026)
    assert top["home_team"] == "ARI"                  # still the No.1
    assert tracker.kelly_stake_units(0.7300, "n/a") is None
    assert tracker._top_pick_reservation(slate[0], 2026) == 0.0


def test_the_number_one_never_reserves_against_itself(monkeypatch):
    """Nothing outranks the No.1, so it always sees the full remaining
    budget."""
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()
    assert tracker._top_pick_reservation(slate[2], 2026) == 0.0


def test_reservation_is_a_projection_and_never_allocates(monkeypatch):
    """Rule R1: a projection must not consume the shared budget. If computing
    the reservation booked the No.1's stake into `_daily_committed`, the
    number would be spent twice and stakes would drift every 5-minute tick."""
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()
    before = dict(tracker._daily_committed)
    tracker._top_pick_reservation(slate[0], 2026)
    assert dict(tracker._daily_committed) == before


def test_pre_lock_projections_are_never_reserved_against(monkeypatch):
    """A pre-lock figure is a pure function of (probability, price). Letting
    the reservation touch it would make a published stake order-dependent --
    the P0-1 oscillation class, in a new hat."""
    slate = [dict(r) for r in _LATE_TOP_SLATE]
    laa = slate[1]
    monkeypatch.setattr(tracker, "_read_rows", lambda _p: list(slate))
    tracker.kelly_reset_daily_committed()
    tracker._size_row_stake(laa, season=2026, inside_lock=False,
                            units_lean=0.0, units_strong=1.0)
    assert float(laa["units_risked"]) == 8.0          # uncapped, unreserved
    assert laa["bet_placed"] == "N"


# ---------------------------------------------------------------------------
# the two No.1 rules must never disagree
# ---------------------------------------------------------------------------

def test_selector_agrees_with_the_notification_gate(monkeypatch):
    """`_select_nights_top_pick` (budget) and `_row_is_nights_top_pick`
    (Telegram) are separate functions on purpose -- the live notification gate
    was not worth destabilising. This is the guard that keeps them identical:
    a board that crowns one game while the budget protects another is the
    class of contradiction this repo keeps being cleared of."""
    probs  = [0.7128, 0.6460, 0.7128, 0.5900, 0.6822]
    prices = ["-120", "-135", "-140", "+100", "-165"]
    teams  = [("CHC", "WSH"), ("TEX", "LAA"), ("COL", "ARI"),
              ("KC", "LAD"), ("TB", "OAK")]

    for n in range(1, len(probs) + 1):
        for rot in range(len(probs)):
            slate = [
                _slate_row(i + 1, *teams[(i + rot) % len(teams)],
                           prob_yrfi=probs[(i + rot) % len(probs)],
                           yrfi_odds=prices[(i + rot) % len(prices)])
                for i in range(n)
            ]
            monkeypatch.setattr(tracker, "_read_rows",
                                lambda _p, s=slate: list(s))
            picked = tracker._select_nights_top_pick("2026-08-11", 2026)
            crowned = [r for r in slate if tracker._row_is_nights_top_pick(r)]
            assert len(crowned) == 1, f"{n}/{rot}: {len(crowned)} crowned"
            assert picked is not None
            assert tracker._game_ident(picked) == tracker._game_ident(crowned[0])

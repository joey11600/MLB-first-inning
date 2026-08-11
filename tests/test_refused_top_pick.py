"""
A REFUSED No.1 IS NOT A PLAY, AND NEVER WAS A RESULT.

2026-08-10, live to paying subscribers. TB@OAK locked with quarter-Kelly
having refused it -- `units_risked` 0, `edge_on_pick` -0.9% -- and the
channel got this:

    # 🔒 TONIGHT'S №1 PLAY
    **TB @ OAK** · 9:40 PM ET
    ## YRFI — a run scores in the 1st
    Model 58.3% · price -145 (needs 59.2%) · edge -0.9%
    Don't take worse than -130.          <-- at a price of -145
    _1 unit = 1% of your bankroll._

Two lines apart it publishes a price limit of -130 and a price of -145.
-145 IS worse than -130, so the message refutes itself -- but only for a
reader who does that arithmetic. Everything with visual weight (padlock,
"TONIGHT'S №1 PLAY", the side as a heading, the bankroll footer) says
BET, and the stake line is absent rather than zero because `if stake:`
treats 0.0 as missing.

THE SAME DEFECT WAS ALREADY FIXED ONCE. `build_board` grew the T8.18
"**This is not a bet.**" branch on 2026-08-06. `build_top_pick` and
`build_top_pick_settled` were never given it, so the fix covered one of
the three places that needed it.

THE SETTLE PING WAS THE WORSE HALF. It was ~40 minutes from publishing

    # ✅ THE №1 WON
    _Record: 47—21 (69.1%) · +88.89u at quarter-Kelly._

for a bet nobody placed, above a record that DOES NOT CONTAIN IT --
`select_top_picks` and dashboard/lib/top-pick.ts both drop a night whose
stake is zero. Claiming a win nobody staked is the oldest trick in paid
picks. On a loss it fails the other way: the ping reports a loss the
record never absorbs, so anyone reconciling the two finds the published
record understating losses.

These tests pin the boundary at the only honest place: what the SYSTEM
STAKED, not what it ranked.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord_broadcasts as B  # noqa: E402

pytestmark = pytest.mark.money
ET = ZoneInfo("America/New_York")
DATE = "2026-08-10"
AT_LOCK = datetime(2026, 8, 10, 20, 40, tzinfo=ET)   # T-60 before 9:40 PM


def _row(**kw):
    """The live TB@OAK row, refused by quarter-Kelly."""
    r = {
        "date": DATE, "away_team": "TB", "home_team": "OAK",
        "game_time_et": "9:40 PM ET", "game_pk": "824969",
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "pick_label": "STRONG YRFI",
        "nrfi_prob": "0.4172", "yrfi_prob": "0.5828",
        "market_yrfi_odds": "-145", "market_nrfi_odds": "+110",
        "edge_on_pick": "-0.009",
        "bet_placed": "N", "units_risked": "0",
        "graded_result": "", "profit_loss_units": "",
    }
    r.update(kw)
    return r


REFUSED = _row()
STAKED = _row(units_risked="4", bet_placed="Y",
              market_yrfi_odds="-120", edge_on_pick="0.0373")
# No price captured at all. NOT a refusal -- the ladder message is a real
# product path and must keep working.
UNPRICED = _row(market_yrfi_odds="", market_nrfi_odds="",
                edge_on_pick="", units_risked="", bet_placed="")


# ---------------------------------------------------------------------------
# the three states
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_the_three_stake_states_are_not_collapsed_into_two():
    """>0 staked, ==0 refused, None unpriced. The middle one is the bug."""
    assert B.stake_for(STAKED) == 4.0
    assert B.stake_for(REFUSED) == 0.0
    assert B.stake_for(UNPRICED) is None

    assert B.is_refused(REFUSED) is True
    assert B.is_refused(STAKED) is False
    # An unpriced row is NOT a refusal -- the system has no opinion yet.
    assert B.is_refused(UNPRICED) is False


# ---------------------------------------------------------------------------
# the lock-time message
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_a_refused_number_one_is_never_headlined_as_the_nights_play():
    body = B.build_top_pick(DATE, REFUSED)
    assert "TONIGHT'S №1 PLAY" not in body
    assert "🔒" not in body
    assert "NO PLAY" in body


@pytest.mark.regression
def test_a_refused_number_one_publishes_no_price_to_act_on():
    """THE LINE THAT SHIPPED. "Don't take worse than -130" is a betting
    instruction, and there is no bet -- it was published beside a price
    of -145, which already violates it."""
    body = B.build_top_pick(DATE, REFUSED)
    assert "Don't take worse than" not in body
    assert "-130" not in body
    assert "1 unit = 1% of your bankroll" not in body


@pytest.mark.regression
def test_a_refused_number_one_says_plainly_that_nothing_is_staked():
    body = B.build_top_pick(DATE, REFUSED)
    assert "staking nothing" in body
    assert "not a bet" in body.lower()
    assert "nothing is added to the record" in body


@pytest.mark.regression
def test_a_staked_number_one_still_reads_as_an_instruction():
    """The fix must not mute a real play."""
    body = B.build_top_pick(DATE, STAKED)
    assert "TONIGHT'S №1 PLAY" in body
    assert "Stake 4 units" in body
    assert "Don't take worse than" in body


@pytest.mark.regression
def test_an_unpriced_number_one_still_gets_the_ladder_message():
    """A missing price is not a refusal; this path must survive."""
    body = B.build_top_pick(DATE, UNPRICED)
    assert "TONIGHT'S №1 PLAY" in body
    assert "no price captured" in body
    assert "NO PLAY" not in body


# ---------------------------------------------------------------------------
# routing -- the refusal gets its OWN event, so neither shape can dedupe
# the other away
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_a_refused_number_one_routes_to_its_own_broadcast():
    due = dict((t, k) for t, k, _ in
               B.due_broadcasts(DATE, [REFUSED], AT_LOCK))
    assert "discord_noplay" in due
    assert "discord_toppick" not in due
    assert due["discord_noplay"].startswith("noplay:2026-08-10:")


@pytest.mark.regression
def test_a_staked_number_one_still_routes_to_the_play_broadcast():
    due = dict((t, k) for t, k, _ in
               B.due_broadcasts(DATE, [STAKED], AT_LOCK))
    assert "discord_toppick" in due
    assert "discord_noplay" not in due


@pytest.mark.regression
def test_the_no_play_event_is_registered_for_dedupe():
    """An unregistered discord_* type inherits the 5-MINUTE fallback and
    republishes ~12x an hour -- the 2026-08-06 board incident. Every new
    broadcast type must land in _DEDUP_WINDOW_M in the same commit."""
    tracker = B._tracker()
    if tracker is None:                       # optional dep missing
        pytest.skip("tracker unavailable")
    assert tracker._DEDUP_WINDOW_M.get("discord_noplay") == 24 * 60


@pytest.mark.regression
def test_a_refused_night_does_not_silence_a_card_that_has_other_plays():
    """T8.16 with the sign flipped: "NO PLAY TONIGHT" over a night that
    HAS a staked play is the same class of false claim."""
    other = _row(away_team="COL", home_team="ARI", game_pk="825048",
                 units_risked="3", bet_placed="Y", edge_on_pick="0.05")
    body = B.build_no_play(DATE, REFUSED, [REFUSED, other])
    assert "NO PLAY ON THE №1" in body
    assert "NO PLAY TONIGHT" not in body
    assert "still staked" in body


# ---------------------------------------------------------------------------
# the settle ping
# ---------------------------------------------------------------------------

@pytest.mark.regression
@pytest.mark.parametrize("graded", ["WIN", "LOSS"])
def test_a_refused_number_one_never_announces_a_result(graded):
    body = B.build_top_pick_settled(DATE, _row(graded_result=graded))
    assert "THE №1 WON" not in body
    assert "THE №1 LOST" not in body
    assert "NO ACTION" in body
    assert "No bet was placed" in body


@pytest.mark.regression
@pytest.mark.parametrize("graded", ["WIN", "LOSS"])
def test_a_refused_number_one_says_the_record_is_unchanged(graded):
    """The record printed below EXCLUDES this game. Saying so is what
    stops the message implying an inclusion that never happened."""
    body = B.build_top_pick_settled(DATE, _row(graded_result=graded))
    assert "unchanged" in body
    assert "not counted" in body


@pytest.mark.regression
def test_a_staked_number_one_still_announces_its_result():
    won = B.build_top_pick_settled(DATE, _row(
        units_risked="4", bet_placed="Y", graded_result="WIN",
        profit_loss_units="2.76"))
    assert "THE №1 WON" in won
    lost = B.build_top_pick_settled(DATE, _row(
        units_risked="4", bet_placed="Y", graded_result="LOSS",
        profit_loss_units="-4.00"))
    assert "THE №1 LOST" in lost


# ---------------------------------------------------------------------------
# unit formatting
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_a_half_unit_stake_never_prints_as_zero():
    """`f"{0.5:.0f}"` is "0". KELLY_ROUNDED_FLOOR is 0.5, so a floored
    pre-lock projection could print "Stake 0 units" -- which reads as a
    formatting bug and is indistinguishable from a refusal."""
    assert B._fmt_units(0.5) == "0.5"
    assert B._fmt_units(4.0) == "4"
    assert B._fmt_units(10.0) == "10"
    body = B.build_top_pick(DATE, _row(units_risked="0.5", bet_placed="N",
                                       market_yrfi_odds="-120",
                                       edge_on_pick="0.0373"))
    assert "Stake 0 units" not in body
    assert "Stake 0.5 units" in body

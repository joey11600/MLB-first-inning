"""
THE BOARD must not claim a verdict it has not reached.

2026-08-07, live to paying subscribers: the board went out at 5:42 PM saying

    ## NO PLAY TONIGHT
    The model looked at every game and declined them all.

    ## PASSING (8)
    `LAD @ ARI`  9:40 PM  57.4%  Lineup Pending

and at 8:41 PM published LAD@ARI as a 3-unit No.1. The board contradicted its
own body -- four of the eight "passing" games were Lineup Pending, not
declined -- and then contradicted itself across the evening.

THE MECHANISM IS STRUCTURAL, not a typo. The board fires at T-60 before the
FIRST game of the slate; a pick commits 60 minutes before ITS OWN first pitch.
On a card running 6:40 PM to 10:15 PM the board is written at 5:40 PM, hours
before the late games have lineups. It CANNOT have judged them.

A subscriber who read "no play tonight" and stopped watching missed the only
play of the night.
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
WHEN = datetime(2026, 8, 7, 17, 42, tzinfo=ET)


def _row(away, home, time_et, strength, label, side="YRFI"):
    return {
        "date": "2026-08-07", "away_team": away, "home_team": home,
        "game_time_et": time_et, "pick_side": side,
        "pick_strength": strength, "pick_label": label,
        "nrfi_prob": "0.42", "edge_on_pick": "0.047",
        "market_yrfi_odds": "-115", "game_pk": away + home,
        "bet_placed": "Y" if strength == "STRONG" else "N",
        "units_risked": "3" if strength == "STRONG" else "",
    }


PENDING = _row("LAD", "ARI", "9:40 PM ET", "LINEUP PENDING",
               "PASS - Lineup pending", "PASS")
JUDGED = _row("LAA", "MIA", "7:10 PM ET", "NO EDGE", "PASS - No edge", "PASS")
PLAY = _row("LAD", "ARI", "9:40 PM ET", "STRONG", "STRONG YRFI")


@pytest.mark.regression
def test_pending_games_are_not_reported_as_declined():
    """THE 2026-08-07 DEFECT. With no plays yet but lineups outstanding, the
    board must NOT say the model declined anything."""
    out = B.build_board("2026-08-07", [PENDING, JUDGED], WHEN)
    assert "declined them all" not in out
    assert "NO PLAY TONIGHT" not in out
    assert "NOT SET YET" in out
    assert "still waiting on lineups" in out
    # and it must tell the reader the No.1 can still arrive
    assert "THE №1 PLAY" in out


def test_a_genuinely_quiet_night_still_says_so():
    """The honest version of the same message. When every game HAS been
    judged, 'declined' is true and the board should say it plainly --
    otherwise the fix would just replace one false claim with another."""
    out = B.build_board("2026-08-07", [JUDGED], WHEN)
    assert "NO PLAY TONIGHT" in out
    assert "judged and declined" in out
    assert "NOT SET YET" not in out


@pytest.mark.regression
def test_a_board_with_a_play_still_warns_that_more_may_commit():
    """Even with a No.1 in hand, an unjudged game means the card is not
    settled. Silence here implies it is."""
    out = B.build_board("2026-08-07", [PLAY, _row("TB", "SEA", "9:45 PM ET",
                        "LINEUP PENDING", "PASS - Lineup pending", "PASS")], WHEN)
    assert "still waiting on lineups" in out
    assert "could still commit" in out


def test_is_undecided_reads_both_pending_shapes():
    """The predictor writes the state two ways; both mean 'no verdict yet'."""
    assert B.is_undecided({"pick_strength": "LINEUP PENDING"}) is True
    assert B.is_undecided({"pick_strength": "STARTER PENDING"}) is True
    assert B.is_undecided({"pick_label": "PASS - Lineup pending + Starter pending"}) is True
    assert B.is_undecided({"pick_strength": "NO EDGE", "pick_label": "PASS - No edge"}) is False
    assert B.is_undecided({"pick_strength": "STRONG", "pick_label": "STRONG YRFI"}) is False


# ---------------------------------------------------------------------------
# T8.17 -- a stake is an INSTRUCTION, and an unlocked pick is not one.
#
# 2026-08-08: the board fired at 2:07 PM (T-60 before a 3:05 PM opener) and
# published CLE@CWS, a 7:15 PM game, as "NO.1 PLAY / Stake 6 units". That pick
# did not lock until 6:15 PM, and the model reversed it at 4:02 PM --
# pick_changes: CLE@CWS 'STRONG YRFI' -> 'LEAN YRFI', and TOR@PHI the same
# at 3:37 PM -- leaving the slate with NO strong pick while the channel still
# held a live instruction to stake 6% of bankroll on it.
#
# This is T8.16 with the sign flipped: that one claimed a verdict it had not
# reached, this one claims a COMMITMENT it has not made.
# ---------------------------------------------------------------------------

def _unlocked_play():
    r = _row("CLE", "CWS", "7:15 PM ET", "STRONG", "STRONG YRFI")
    r["units_risked"] = "6"
    r["nrfi_prob"] = "0.3674"
    return [r]


def test_an_unlocked_play_is_never_printed_as_a_committed_stake():
    out = B.build_board("2026-08-07", _unlocked_play(),
                        now=datetime(2026, 8, 7, 14, 7, tzinfo=ET))
    assert "Stake 6 units" not in out,         "printed a committed stake four hours before the pick locks"
    assert "NOT LOCKED" in out
    assert "not a bet yet" in out.lower()
    assert "6:15 PM" in out, "must say when it actually locks"


def test_a_locked_play_still_prints_a_real_instruction():
    """The guard must not mute a genuine committed play."""
    out = B.build_board("2026-08-07", _unlocked_play(),
                        now=datetime(2026, 8, 7, 18, 30, tzinfo=ET))
    assert "Stake 6 units" in out
    assert "NOT LOCKED" not in out


def test_the_number_one_crown_waits_for_the_lock():
    out = B.build_board("2026-08-07", _unlocked_play(),
                        now=datetime(2026, 8, 7, 14, 7, tzinfo=ET))
    assert "OUT IN FRONT" in out
    # the crown itself must be withheld -- "OUT IN FRONT" is not "THE №1 PLAY"
    assert "# ⭐ THE №1 PLAY" not in out

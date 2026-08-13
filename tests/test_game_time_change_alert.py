"""
T8.35 follow-on -- the game-time-change alert.

THE COINCIDENCE THIS MAKES VISIBLE.  On 2026-08-13 MLB corrected
CIN@CWS from 2:10 PM to 1:10 PM ET at 11:58 -- which silently moved the
T-60 lock an hour earlier, to 12:10, leaving twelve minutes of runway.
The No.1 then committed inside a lineup outage nobody had time to see.
Every pre-lock protection in this system is denominated in minutes
before the lock; a time correction re-denominates them all, silently.

`_game_time_change_candidate` decides IF a move is alert-worthy; the
batched `_notify_game_time_change_telegram` sends ONE ping per detect
run.  Half these tests pin the fires; the other half pin the silences
(DH game-2 churn, placeholder resolution, jitter, locked/graded/started
rows) -- the silences are what keep the alert alive against this repo's
documented alarm-fatigue failure mode.

House rules: no real-ledger reads, no hardcoded slate dates -- fixtures
use fixed clock strings on tomorrow's (or yesterday's) date so deltas
stay exact and the started/lock guards behave the same on any day, at
any hour, the suite runs.
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")


def _rows(old_clock="3:10 PM", new_clock="2:10 PM", day_offset=1, **over):
    """(existing, new_row) whose game time moved old_clock -> new_clock.

    Fixed clock strings on TOMORROW's date (day_offset=1) keep every
    delta exact and every game un-started regardless of when the suite
    runs -- relative-to-now times straddle midnight during late-evening
    runs and skew the computed delta by a day.  day_offset=-1 puts the
    game firmly in the past for the started-guard test.
    """
    d = (datetime.now(ET) + timedelta(days=day_offset)).strftime("%Y-%m-%d")
    base = {
        "date": d,
        "game_pk": "824561",
        "away_team": "CIN", "home_team": "CWS",
        "double_header": "N", "game_number": "1",
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "pick_label": "STRONG YRFI",
        "bet_placed": "N", "graded_result": "",
        "units_risked": "7",
    }
    existing = dict(base); existing["game_time_et"] = f"{old_clock} ET"
    new_row  = dict(base); new_row["game_time_et"]  = f"{new_clock} ET"
    for k, v in over.items():
        if k.startswith("existing_"):
            existing[k[len("existing_"):]] = v
        elif k.startswith("new_"):
            new_row[k[len("new_"):]] = v
        else:
            existing[k] = v; new_row[k] = v
    return existing, new_row


@pytest.fixture
def sent(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        tracker, "_notify_event_telegram",
        lambda et, key, body: (calls.append((et, key, body)) or True),
    )
    return calls


# ---------------------------------------------------------------------------
# The candidate function: fires
# ---------------------------------------------------------------------------

def test_earlier_move_is_a_candidate():
    existing, new_row = _rows()          # 3:10 PM -> 2:10 PM
    c = tracker._game_time_change_candidate(existing, new_row, False)
    assert c is not None
    assert c["delta_m"] == -60
    # The lock the operator must now plan around: new game time - 60m.
    parsed = tracker._parse_game_time_et(new_row["game_time_et"],
                                         new_row["date"])
    assert c["lock_dt"] == parsed - timedelta(minutes=60)


def test_later_move_is_a_candidate_too():
    # A big later move is schedule upheaval worth seeing (and if the row
    # carries a projected stake, the operator's evening plan just moved).
    existing, new_row = _rows(old_clock="2:10 PM", new_clock="2:55 PM")
    c = tracker._game_time_change_candidate(existing, new_row, False)
    assert c is not None
    assert c["delta_m"] == 45


# ---------------------------------------------------------------------------
# The candidate function: silences
# ---------------------------------------------------------------------------

def test_sub_five_minute_jitter_is_silent():
    existing, new_row = _rows(old_clock="2:10 PM", new_clock="2:13 PM")
    assert tracker._game_time_change_candidate(existing, new_row, False) is None


def test_doubleheader_game_two_churn_is_silent():
    # MLB lists DH game 2 at game 1's time +5 min and corrects it later;
    # alerting on that routine cleanup is exactly the alarm-fatigue trap.
    existing, new_row = _rows(double_header="Y", game_number="2")
    assert tracker._game_time_change_candidate(existing, new_row, False) is None


def test_locked_rows_are_silent():
    # Post-lock the machinery is done with this row; a time change now is
    # a delay, not a compressed lock.
    existing, new_row = _rows()
    assert tracker._game_time_change_candidate(existing, new_row, True) is None


def test_placeholder_resolving_is_silent():
    # "After Game 1" -> a real time is a time APPEARING, not moving.
    existing, new_row = _rows(existing_game_time_et="After Game 1")
    assert tracker._game_time_change_candidate(existing, new_row, False) is None


def test_started_games_are_silent():
    existing, new_row = _rows(day_offset=-1)
    assert tracker._game_time_change_candidate(existing, new_row, False) is None


def test_graded_rows_are_silent():
    existing, new_row = _rows(new_graded_result="POSTPONED")
    assert tracker._game_time_change_candidate(existing, new_row, False) is None


def test_unchanged_time_is_silent():
    existing, new_row = _rows(old_clock="2:10 PM", new_clock="2:10 PM")
    assert tracker._game_time_change_candidate(existing, new_row, False) is None


# ---------------------------------------------------------------------------
# The batched notifier
# ---------------------------------------------------------------------------

def _candidate(existing, new_row):
    c = tracker._game_time_change_candidate(existing, new_row, False)
    assert c is not None
    return c


def test_two_moves_are_one_ping_with_loud_header(sent):
    e1, n1 = _rows()                                           # earlier
    e2, n2 = _rows(old_clock="2:10 PM", new_clock="2:40 PM",   # later
                   game_pk="900001", away_team="BOS", home_team="TOR")
    tracker._notify_game_time_change_telegram([_candidate(e1, n1),
                                               _candidate(e2, n2)])
    assert len(sent) == 1
    et, key, body = sent[0]
    assert et == "game_time_change"
    # Any earlier-move makes the header loud.
    assert "LOCK EARLIER" in body
    assert "CIN @ CWS" in body and "BOS @ TOR" in body
    # Signature carries each game's NEW time: a second move later today
    # is a new key, not a suppressed duplicate.
    assert "824561>" in key and "900001>" in key


def test_later_only_move_gets_calm_header(sent):
    e, n = _rows(old_clock="2:10 PM", new_clock="2:55 PM")
    tracker._notify_game_time_change_telegram([_candidate(e, n)])
    assert "LOCK EARLIER" not in sent[0][2]
    assert "GAME TIME CHANGED" in sent[0][2]
    assert "45m later" in sent[0][2]


def test_prelock_stake_is_worded_as_projection(sent):
    # T8.16/T8.17 lesson: an unlocked pick is not a bet.  A stake quoted
    # pre-lock must say NOT LOCKED, never read as a commitment.
    e, n = _rows()          # bet_placed=N, units 7
    tracker._notify_game_time_change_telegram([_candidate(e, n)])
    body = sent[0][2]
    assert "projected stake 7u — NOT LOCKED" in body
    assert "bet locked" not in body


def test_placed_bet_is_worded_as_locked(sent):
    e, n = _rows(new_bet_placed="Y", new_units_risked="7")
    tracker._notify_game_time_change_telegram([_candidate(e, n)])
    assert "bet locked at 7u" in sent[0][2]


def test_empty_candidate_list_sends_nothing(sent):
    tracker._notify_game_time_change_telegram([])
    assert sent == []

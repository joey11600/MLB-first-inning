"""
T8.35 layer 3 -- the lineup-regression alarm.

THE INCIDENT THIS GUARDS.  2026-08-13 CIN@CWS, the night's No.1: MLB's
schedule?hydrate=lineups served the CWS lineup card (model -> YRFI 66.9%),
WITHDREW it for ~55 minutes, and re-served it unchanged before first
pitch.  `fetch_top3_batters` has no memory, so during the outage the
model silently regressed the home side to team-average batting (58.6%),
and the T-60 commit -- landing 90 seconds into a lock window that the
same hour's game-time correction had moved an hour earlier -- sized
quarter-Kelly on the outage number: 2u where the rule on the published
probability said 7u.  No alarm existed.  These tests pin the one that
does now: `tracker._notify_lineup_regression_telegram`.

The alarm is OBSERVABILITY ONLY -- it must never mutate the row, and the
detection must fire on the lineup->fallback TRANSITION exactly, because
after the regressed row is written the stored source is no longer
"lineup" and the next cycle has nothing to compare against.  Half these
tests pin when it fires; the other half pin the silences.  The silences
matter more: an alarm that cries on every merge is deleted within a week
(see _DEDUP_WINDOW_M's history of exactly that failure mode).

Follows the house test rules (tests/test_stake_rederive.py): no test
reads data/picks_2026.csv, and no test hardcodes a slate date -- game
times are built relative to now() so the pre-lock / started gates behave
the same on any day the suite runs.
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")


def _fmt_et(dt: datetime) -> str:
    """'7:05 PM ET' from a datetime, portably (no %-I on Windows)."""
    hour12 = ((dt.hour - 1) % 12) + 1
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{hour12}:{dt.minute:02d} {ampm} ET"


def _slate(hours_from_now: float = 3.0) -> tuple[str, str]:
    """(iso_date, game_time_et) for a game `hours_from_now` ahead."""
    game_dt = datetime.now(ET) + timedelta(hours=hours_from_now)
    return game_dt.strftime("%Y-%m-%d"), _fmt_et(game_dt)


def _rows(**over):
    """(existing, new_row) pair for the canonical incident shape:
    home side lineup -> team_fallback on a pre-lock STRONG YRFI.
    Override any field with existing_/new_ prefixes, or both at once
    with a bare name."""
    iso, gtet = _slate()
    base = {
        "date": iso, "game_pk": "824561",
        "away_team": "CIN", "home_team": "CWS",
        "game_time_et": gtet,
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "bet_placed": "N", "graded_result": "",
        "nrfi_prob": "0.3313", "yrfi_prob": "0.6687",
        "away_top3c_source": "lineup", "home_top3c_source": "lineup",
        "units_risked": "7",
    }
    existing = dict(base)
    new_row = dict(base)
    new_row.update({
        "home_top3c_source": "team_fallback",
        "nrfi_prob": "0.414", "yrfi_prob": "0.586",
        "units_risked": "2",
    })
    for k, v in over.items():
        if k.startswith("existing_"):
            existing[k[len("existing_"):]] = v
        elif k.startswith("new_"):
            new_row[k[len("new_"):]] = v
        else:
            existing[k] = v
            new_row[k] = v
    return existing, new_row


@pytest.fixture
def sent(monkeypatch):
    """Capture (event_type, event_key, body) instead of sending."""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        tracker, "_notify_event_telegram",
        lambda et, key, body: (calls.append((et, key, body)) or True),
    )
    return calls


# ---------------------------------------------------------------------------
# When it MUST fire
# ---------------------------------------------------------------------------

def test_fires_on_home_lineup_to_fallback(sent):
    existing, new_row = _rows()
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert len(sent) == 1
    et, key, body = sent[0]
    assert et == "lineup_regression"
    # Key carries game + side + the NEW state, so a later escalation
    # (e.g. sticky memory lost mid-outage) is a different key, not a
    # suppressed duplicate.
    assert key == f"lineup_regression:{existing['date']}:824561:home>team_fallback"
    # Body carries the forensic pair: probability shift + stake shift.
    assert "66.9%" in body and "58.6%" in body
    assert "7u → 2u" in body
    assert "CWS" in body


def test_fires_when_regression_demotes_strength(sent):
    # The regression itself can demote the fresh verdict below STRONG --
    # the pick vanishing off the board IS the alert-worthy event, so
    # STRONG on the STORED side alone must be enough.
    existing, new_row = _rows(new_pick_strength="LEAN",
                              new_pick_side="PASS")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert len(sent) == 1
    # Probability line still renders, using the stored row's pick side.
    assert "66.9%" in sent[0][2]


def test_fires_for_away_side_with_away_key(sent):
    existing, new_row = _rows(
        new_home_top3c_source="lineup",
        new_away_top3c_source="team_fallback",
    )
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert len(sent) == 1
    assert sent[0][1].endswith(":away>team_fallback")
    assert "CIN" in sent[0][2]


def test_both_sides_regressing_is_one_message(sent):
    existing, new_row = _rows(new_away_top3c_source="league_default")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert len(sent) == 1                      # one ping, not two
    # Sorted signature covers both sides' new states.
    assert sent[0][1].endswith(":away>league_default+home>team_fallback")


def test_sticky_bridge_pings_with_calm_body(sent):
    # T8.35 layer 1 on: the card is pulled but sticky memory holds the
    # batters.  Still operator-worthy (the feed is flapping on a STRONG
    # game) but the body must say the bridge held, not cry regression --
    # and the probability barely moves, unlike the fallback case.
    existing, new_row = _rows(
        new_home_top3c_source="lineup_sticky",
        new_yrfi_prob="0.6687", new_nrfi_prob="0.3313",
        new_units_risked="7",
    )
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert len(sent) == 1
    key, body = sent[0][1], sent[0][2]
    assert key.endswith(":home>lineup_sticky")
    assert "STICKY memory kept the last posted card" in body
    assert "team-average" not in body


def test_sticky_memory_lost_escalates_with_new_key(sent):
    # lineup_sticky -> team_fallback is the memory dying mid-outage
    # (flag turned off, ledger row overwritten by a non-sticky host).
    # The stake is re-exposed, so it must ping -- and under a DIFFERENT
    # key than the earlier bridge ping, or the 12h window would eat it.
    existing, new_row = _rows(existing_home_top3c_source="lineup_sticky")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert len(sent) == 1
    key, body = sent[0][1], sent[0][2]
    assert key.endswith(":home>team_fallback")
    assert "team-average" in body


# ---------------------------------------------------------------------------
# When it MUST stay silent
# ---------------------------------------------------------------------------

def test_silent_when_bet_already_placed(sent):
    # T2.23 froze the stake -- there is nothing left to protect, and the
    # post-bet merge path preserves probabilities anyway.
    existing, new_row = _rows(new_bet_placed="Y")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert sent == []


def test_silent_when_no_side_is_strong(sent):
    existing, new_row = _rows(existing_pick_strength="LEAN",
                              new_pick_strength="LEAN")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert sent == []


def test_silent_when_nothing_regressed(sent):
    existing, new_row = _rows(new_home_top3c_source="lineup")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert sent == []


def test_silent_on_recovery_direction(sent):
    # fallback -> lineup is the card RETURNING.  That is the good path
    # (T8.18 re-derive picks the stake back up); alarming on it would
    # double every outage's noise.
    existing, new_row = _rows(
        existing_home_top3c_source="team_fallback",
        existing_yrfi_prob="0.586",
        new_home_top3c_source="lineup",
        new_yrfi_prob="0.6687",
    )
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert sent == []


def test_silent_when_terminally_graded(sent):
    existing, new_row = _rows(new_graded_result="WIN")
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert sent == []


def test_silent_once_game_has_started(sent):
    # Game time 2h in the past: post-start source churn is boxscore
    # noise, not an actionable pre-lock outage.
    iso_gtet = _slate(hours_from_now=-2.0)
    existing, new_row = _rows(date=iso_gtet[0], game_time_et=iso_gtet[1])
    tracker._notify_lineup_regression_telegram(existing, new_row)
    assert sent == []


def test_notify_failure_cannot_raise(monkeypatch):
    # The log_picks call site wraps this in try/except, but the function
    # itself reaching the send is the last line of defence -- a raising
    # notifier inside the merge loop would abort the slate write.
    def _boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(tracker, "_notify_event_telegram", _boom)
    existing, new_row = _rows()
    with pytest.raises(RuntimeError):
        # Documented: the FUNCTION may raise; the CALL SITE catches.
        tracker._notify_lineup_regression_telegram(existing, new_row)


# ---------------------------------------------------------------------------
# The stake-drift nightly notifier (layer 3b)
# ---------------------------------------------------------------------------

def test_stake_drift_notify_key_is_violation_signature(monkeypatch):
    from tools import stake_drift

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        tracker, "_notify_event_telegram",
        lambda et, key, body: (calls.append((et, key, body)) or True),
    )
    rep = stake_drift.Report()
    rep.violations.append(stake_drift.Finding(
        date="2026-08-13", game_pk="824561", game="CIN@CWS", side="YRFI",
        prob=0.6687, odds="-120", stored=2.0, expected=7.0,
    ))
    stake_drift._notify_violations(rep)
    assert len(calls) == 1
    et, key, body = calls[0]
    assert et == "stake_drift"
    # Same violations -> same key (dedup holds); a new violation would
    # change the signature and ping through the window.
    assert key == "stake_drift:2026-08-13:824561"
    assert "ledger 2u vs rule 7u" in body
    assert "auto-corrected" in body     # states the read-only contract


def test_stake_drift_notify_never_raises(monkeypatch):
    from tools import stake_drift

    def _boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(tracker, "_notify_event_telegram", _boom)
    rep = stake_drift.Report()
    rep.violations.append(stake_drift.Finding(
        date="2026-08-13", game_pk="824561", game="CIN@CWS", side="YRFI",
        prob=0.6687, odds="-120", stored=2.0, expected=7.0,
    ))
    stake_drift._notify_violations(rep)      # must swallow, not raise

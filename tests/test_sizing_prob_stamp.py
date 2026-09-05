"""
T8.35 -- the sizing_prob column: the ledger records WHAT SIZED THE BET.

On 2026-08-13 the No.1 published yrfi_prob=0.6687 beside a 2u stake that
quarter-Kelly had actually derived from 0.5864 -- and nothing in the
DATA said so; the number had to be dug out of Railway deploy logs.  This
column is the belt-and-suspenders on layers 1-2: `_size_row_stake`
stamps the exact probability it fed to `kelly_stake_units` whenever it
writes `units_risked`, so any future splice is visible in the row
itself, and a blank says honestly "this stake is not probability-sized"
(flat fallback, LEAN notional, orphan heal).

The invariant these tests pin: THE STAMP AND THE STAKE ARE THE SAME READ
OF THE SAME CELL.  A sizing_prob written from anywhere else would just
re-create the incoherence the column exists to expose.

House rules (tests/test_stake_rederive.py): expected stakes come from
the SHIPPED kelly_stake_units, never hand arithmetic; no real-ledger
reads; no hardcoded slate dates.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402

pytestmark = pytest.mark.money

ET = ZoneInfo("America/New_York")


def _tomorrow_iso() -> str:
    return (datetime.now(ET) + timedelta(days=1)).strftime("%Y-%m-%d")


def _row(**over):
    row = {
        "date": _tomorrow_iso(), "game_pk": "824561",
        "away_team": "CIN", "home_team": "CWS",
        "game_time_et": "1:10 PM ET",
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "pick_label": "STRONG YRFI",
        "nrfi_prob": "0.3313", "yrfi_prob": "0.6687",
        "market_nrfi_odds": "-110", "market_yrfi_odds": "-120",
        "bet_placed": "N", "graded_result": "",
        "units_risked": "", "sizing_prob": "",
        "double_header": "N", "game_number": "1",
    }
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def _kelly_sandbox(monkeypatch):
    """Fixed 100u bank, empty committed ledger, no reservation reads --
    and restore the module tallies afterwards (they are process-global)."""
    saved = (dict(tracker._daily_committed), tracker._bankroll_cache)
    monkeypatch.setattr(tracker, "_read_rows", lambda p: [])
    monkeypatch.setattr(tracker, "_csv_path", lambda s: Path("unused.csv"))
    monkeypatch.setattr(tracker, "_top_pick_reservation", lambda r, s: 0.0)
    tracker._bankroll_cache = 100.0
    tracker.kelly_reset_daily_committed()
    yield
    tracker._daily_committed.clear()
    tracker._daily_committed.update(saved[0])
    tracker._bankroll_cache = saved[1]


def _shipped_stake(p: float, odds: str) -> float:
    k = tracker.kelly_stake_units(p, odds, season=2026)
    assert k is not None and k > 0
    return k


# ---------------------------------------------------------------------------
# The stamp rides with every Kelly-written stake
# ---------------------------------------------------------------------------

def test_projection_stamps_the_probability_it_sized_from():
    row = _row()
    tracker._size_row_stake(row, season=2026, inside_lock=False,
                            units_lean=0.0, units_strong=None)
    assert row["units_risked"] == tracker._fmt(_shipped_stake(0.6687, "-120"), 2)
    assert row["sizing_prob"] == "0.6687"
    assert row["bet_placed"] == "N"          # projection, not a commit


def test_commit_stamps_and_places():
    row = _row()
    tracker._size_row_stake(row, season=2026, inside_lock=True,
                            units_lean=0.0, units_strong=1.0)
    assert row["bet_placed"] == "Y"
    assert row["units_risked"] == tracker._fmt(_shipped_stake(0.6687, "-120"), 2)
    assert row["sizing_prob"] == "0.6687"


def test_a_kelly_refusal_is_stamped_too():
    # p at the market's implied: Kelly says 0.  The refusal is
    # probability-DERIVED, so it is auditable exactly like a stake.
    row = _row(yrfi_prob="0.5", nrfi_prob="0.5")
    tracker._size_row_stake(row, season=2026, inside_lock=True,
                            units_lean=0.0, units_strong=1.0)
    assert row["bet_placed"] == "N"
    assert row["units_risked"] == tracker._fmt(0.0, 2)
    assert row["sizing_prob"] == "0.5"


def test_incident_shape_would_have_been_visible():
    # The 12:06 ET state: published-side prob has fallen to the outage
    # number.  The stamp records exactly what sized the 2u -- had the
    # published prob later been spliced back to 0.6687, the row itself
    # would show the contradiction.
    row = _row(yrfi_prob="0.586", nrfi_prob="0.414")
    tracker._size_row_stake(row, season=2026, inside_lock=True,
                            units_lean=0.0, units_strong=1.0)
    assert row["units_risked"] == tracker._fmt(_shipped_stake(0.586, "-120"), 2)
    assert row["sizing_prob"] == "0.586"


# ---------------------------------------------------------------------------
# Blank means "not probability-sized" -- and only then
# ---------------------------------------------------------------------------

def test_flat_fallback_stamps_nothing(monkeypatch):
    # Kill-switch world (NRFI_KELLY_ENABLED=0): flat units_strong.  A
    # flat stake claiming a sizing probability would be a lie.
    monkeypatch.setattr(tracker, "KELLY_ENABLED", False)
    row = _row()
    tracker._size_row_stake(row, season=2026, inside_lock=True,
                            units_lean=0.0, units_strong=1.0)
    assert row["units_risked"] == tracker._fmt(1.0, 2)
    assert row["sizing_prob"] == ""


def test_lean_notional_stamps_nothing():
    row = _row(pick_strength="LEAN", pick_label="LEAN YRFI")
    tracker._size_row_stake(row, season=2026, inside_lock=False,
                            units_lean=0.5, units_strong=1.0)
    assert row["units_risked"] == "0.5"
    assert row["sizing_prob"] == ""


def test_pass_clears_a_previous_stamp():
    row = _row(pick_side="PASS", units_risked="7", sizing_prob="0.6687")
    tracker._size_row_stake(row, season=2026, inside_lock=False,
                            units_lean=0.0, units_strong=1.0)
    assert row["units_risked"] == ""
    assert row["sizing_prob"] == ""


def test_keepalive_floor_leaves_the_last_honest_stamp():
    # Pre-lock projection where Kelly now refuses, on a row already
    # carrying a stake: T8.18 keeps a 0.5u placeholder so the play does
    # not blink off a paying board.  That floor is NOT probability-sized
    # -- the stamp from the last real sizing must survive untouched.
    row = _row(yrfi_prob="0.5", nrfi_prob="0.5",
               units_risked="3", sizing_prob="0.62")
    tracker._size_row_stake(row, season=2026, inside_lock=False,
                            units_lean=0.0, units_strong=None)
    assert row["units_risked"] == tracker._fmt(tracker.KELLY_ROUNDED_FLOOR, 2)
    assert row["sizing_prob"] == "0.62"


# ---------------------------------------------------------------------------
# The T8.18 re-derive keeps stamp and stake moving together
# ---------------------------------------------------------------------------

def test_rederive_restamps_when_the_probability_moves(monkeypatch):
    monkeypatch.setenv("NRFI_STAKE_REDERIVE", "enabled")
    row = _row(units_risked="2", sizing_prob="0.586",
               yrfi_prob="0.6687", nrfi_prob="0.3313")
    tracker._rederive_pre_lock_stake(row, season=2026, locked=False)
    assert row["units_risked"] == tracker._fmt(_shipped_stake(0.6687, "-120"), 2)
    assert row["sizing_prob"] == "0.6687"    # stamp tracked the stake


# ---------------------------------------------------------------------------
# Plumbing: the column must survive every hop or T8.23 eats it
# ---------------------------------------------------------------------------

def test_column_is_wired_end_to_end():
    from db import supabase_writer
    from tools import sync_csv_from_supabase as sync_mod
    assert "sizing_prob" in tracker.FIELDS
    # Appended, never reordered.  Columns added AFTER sizing_prob must be
    # appended behind it, and each one has to be named here on purpose --
    # so a reorder fails, and a silent insertion ahead of it fails.
    tail = tracker.FIELDS[tracker.FIELDS.index("sizing_prob"):]
    assert tail == ["sizing_prob",
                    "home_fi_xwoba", "away_fi_xwoba",   # 2026-08-22 model inputs
                    # 2026-09-04 shadow model (tests/test_shadow_model.py)
                    "shadow_model", "shadow_nrfi_prob", "shadow_nrfi_prob_raw",
                    "shadow_pick_label", "home_fi_form", "away_fi_form"]
    assert "sizing_prob" in supabase_writer.PICKS_CONVERTERS
    # Preserve-on-blank keeps a predict-path mirror from wiping the
    # sizer's stamp, and its membership auto-enrolls the column in the
    # CSV<-Supabase sync -- the stamp travels like units_risked does.
    assert "sizing_prob" in supabase_writer._PRESERVE_ON_BLANK_FIELDS
    assert "sizing_prob" in sync_mod._SYNC_COLUMNS

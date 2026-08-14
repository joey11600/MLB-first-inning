"""The card-retention window must never delete the night it just published.

REGRESSION. On 2026-08-14 the prune job computed its cutoff from `now()`
while its safety guard checked `--require-date`. The two were different
clocks, so a run that published 2026-08-13's cards and then pruned against a
wall clock already reading 2026-08-14 deleted the set it had just uploaded.
The guard passed; the delete was still wrong.

Production only hits this at the ET midnight rollover — a step capturing
TODAY_ISO at 11:59pm and reaching the prune at 12:00am — which is exactly the
kind of once-a-year failure that is cheap to prevent and expensive to
diagnose. These tests pin the anchoring rule.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    """Import prune_cards without importing supabase (it's lazy in _client)."""
    spec = importlib.util.spec_from_file_location(
        "_prune", REPO / "tools" / "cards" / "prune_cards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prune = _load()


def test_the_published_night_survives_a_midnight_rollover():
    """THE BUG. Anchor 08-13, clock already 08-14 -> 08-13 must be kept."""
    anchor, cutoff = prune.retention_cutoff(
        "2026-08-13", keep_days=1, today=date(2026, 8, 14))
    assert anchor == date(2026, 8, 13)
    assert cutoff == date(2026, 8, 13)
    # "delete nights strictly before cutoff" -> the published night is safe
    assert not date(2026, 8, 13) < cutoff
    # ...and the night before it still goes
    assert date(2026, 8, 12) < cutoff


def test_keep_one_day_keeps_only_the_anchor_night():
    _, cutoff = prune.retention_cutoff(
        "2026-08-14", keep_days=1, today=date(2026, 8, 14))
    assert date(2026, 8, 13) < cutoff          # yesterday goes
    assert not date(2026, 8, 14) < cutoff      # today stays


@pytest.mark.parametrize("keep,oldest_kept", [
    (1, date(2026, 8, 14)),
    (2, date(2026, 8, 13)),
    (7, date(2026, 8, 8)),
])
def test_keep_days_counts_the_anchor_night_itself(keep, oldest_kept):
    """keep_days=N keeps N nights INCLUDING the anchor, not N nights before."""
    _, cutoff = prune.retention_cutoff("2026-08-14", keep, date(2026, 8, 14))
    assert cutoff == oldest_kept
    assert not oldest_kept < cutoff


def test_without_require_date_it_falls_back_to_the_clock():
    anchor, cutoff = prune.retention_cutoff(None, 1, date(2026, 8, 14))
    assert anchor == date(2026, 8, 14) and cutoff == date(2026, 8, 14)


def test_the_filename_pattern_is_an_allowlist():
    """Anything that is not a Backfist card must not match, so it is skipped."""
    assert prune.NAME.match("backfist_2026-08-13_leather.png")
    assert prune.NAME.match("backfist_2026-08-13_green-wedge.png")
    for foreign in (
        "logo.png",                       # something put there by hand
        "backfist_2026-08-13.png",        # no plate
        "backfist_13-08-2026_leather.png",  # not ISO
        "backfist_2026-08-13_leather.jpg",  # not our format
        "old/backfist_2026-08-13_leather.png",
    ):
        assert not prune.NAME.match(foreign), foreign

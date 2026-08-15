"""A generated X post may not contain a number it was not given.

This is the control that makes an LLM acceptable on a public, paid surface
at all. The prompt also forbids invention, but a prompt is a request, not a
guarantee — `_unsourced_numbers` is the guarantee, and these tests pin its
two asymmetric halves:

  DECIMALS are matched leniently, because "a 3.7 ERA" for a supplied 3.67 is
  ordinary prose and rejecting it would push the generator into the dull
  fallback for no reason.

  INTEGERS are matched exactly, because the dangerous invention is a COUNT —
  "scored first in 8 of their last 10 games" is confident, checkable and
  entirely made up. Under decimal rounding rules that 8 would pass as a
  rounded 7.58 strikeouts-per-nine.

The seed allowlist is also pinned. It once held {1, 3, 5, 9} for "1st
inning" / "top 3" / "last 5" / "per nine", and seeding 3 alone was enough to
let "homered in 3 straight games" through.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "_mp", REPO / "tools" / "cards" / "make_post.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mp = _load()

# What a real night supplies, in the shape build_facts() produces.
FACTS = [
    "Our model puts the chance of a first-inning run at 66.9%.",
    "The market price implies 54.5%.",
    "That is an edge of +12.3 percentage points.",
    "Reds start left-handed Andrew Abbott, who has a 3.67 ERA, a 1.33 WHIP, "
    "7.22 strikeouts per nine.",
    "Andrew Abbott has kept the first inning scoreless in 4 of his last 5 starts.",
    "White Sox start right-handed Davis Martin, who has a 4.15 ERA, a 1.32 "
    "WHIP, 7.58 strikeouts per nine.",
]
NIGHT = {"stake": 7.0, "odds": -120.0}
ALLOWED = mp._allowed_numbers(FACTS, NIGHT)


@pytest.mark.parametrize("text", [
    "Abbott carries a 3.67 ERA and kept the first inning clean in 4 of his last 5.",
    "Abbott owns a 3.7 ERA and a 1.3 WHIP.",            # rounded, still true
    "A run in the 1st is all this needs.",              # the seeded 1
    "Both starters have been vulnerable early and the orders turn over fast.",
    "Our model makes it 66.9% against a market implying 54.5%.",
])
def test_sourced_prose_passes(text):
    assert mp._unsourced_numbers(text, ALLOWED) == []


@pytest.mark.parametrize("text,culprit", [
    ("Martin carries a 6.12 ERA into this one.", "6.12"),
    ("The Reds have scored first in 8 of their last 10 games.", "8"),
    ("Vargas has homered in 3 straight.", "3"),
    ("Our model makes it 69.4%.", "69.4"),
    ("Abbott has a 2.10 ERA over his last six.", "2.10"),
])
def test_invented_numbers_are_caught(text, culprit):
    assert culprit in mp._unsourced_numbers(text, ALLOWED)


def test_the_seed_allowlist_stays_minimal():
    """Every seeded integer is a free pass handed to a fabricated count.

    Only "1" is seeded (for "1st inning"); the rest of the set comes from the
    facts and from the night's own stake and price.
    """
    got = mp._allowed_numbers([], {"stake": 0.0, "odds": 0.0})
    assert got == {"1", "0", "0.0"}          # seed + the stake/odds of this stub
    assert mp._allowed_numbers([], {"stake": 7.0, "odds": -120.0}) == {
        "1", "7.0", "120"}


def test_the_header_is_never_generated():
    """The play and the units are built in Python and must not depend on the
    model — that is the T8.30 lesson (publish what the system STAKED)."""
    night = {
        # Full precision, as `load_night` supplies it — the edge is computed
        # from these and rounded ONCE, so 66.87 - 54.55 prints 12.3, not the
        # 12.4 you would get by subtracting the two displayed figures.
        "side": "YRFI", "stake": 7.0, "odds": -120.0,
        "model": 66.87, "implied": 54.55, "first_pitch": "1:10 PM ET",
        "clubs": {
            "away": {"club": "REDS", "pitcher": "Andrew Abbott", "bats": []},
            "home": {"club": "WHITE SOX", "pitcher": "Davis Martin", "bats": []},
        },
    }
    header = mp.build_post(night, verbose=False)[0].split("\n\n")[0]
    assert "7u" in header
    assert "Either team scores in the 1st" in header   # never "yes run"
    assert "−120" in header                       # real minus, as the card
    assert "Reds at White Sox" in header               # club names, not CIN/CWS


def test_the_template_fallback_states_only_real_figures():
    """With no key and no model, the post still goes out and is still true."""
    night = {
        # Full precision, as `load_night` supplies it — the edge is computed
        # from these and rounded ONCE, so 66.87 - 54.55 prints 12.3, not the
        # 12.4 you would get by subtracting the two displayed figures.
        "side": "YRFI", "stake": 7.0, "odds": -120.0,
        "model": 66.87, "implied": 54.55, "first_pitch": "1:10 PM ET",
        "clubs": {
            "away": {"club": "REDS", "pitcher": "Andrew Abbott", "bats": []},
            "home": {"club": "WHITE SOX", "pitcher": "Davis Martin", "bats": []},
        },
    }
    para = mp.template(night)
    assert "66.9%" in para and "54.5%" in para and "12.3" in para

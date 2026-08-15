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


@pytest.mark.parametrize("text,culprit", [
    ("Trout has homered in six straight games.", "6"),
    ("He has walked twelve in his last outing.", "12"),
])
def test_spelled_out_counts_are_checked_too(text, culprit):
    """A fabricated count is no less fabricated for being spelled.

    The guard read digits only until a real generation slipped "three of his
    last five" past it — derived by arithmetic, written in words, invisible
    to a digit scanner.
    """
    assert culprit in mp._unsourced_numbers(text, ALLOWED)


@pytest.mark.parametrize("text", [
    "He punches out 7.22 per nine.",        # "nine" appears in the facts
    "This one sets up well for the hitters.",   # pronoun, not a count
    "No one has solved him early this year.",
])
def test_word_check_does_not_eat_ordinary_prose(text):
    """"one" is excluded from the word map on purpose: it is an article and a
    pronoun far more often than a count. And a word that appears in the FACTS
    allows itself, so "per nine" survives."""
    assert mp._unsourced_numbers(text, ALLOWED) == []


def test_build_facts_runs_on_a_realistic_night():
    """REGRESSION: `build_facts` ended with an orphaned `del mc` after a
    refactor dropped the assignment, and nothing caught it — the no-key path
    returns BEFORE build_facts, so every test and every local run skipped the
    function entirely. It crashed on the first generation with a real key."""
    night = {
        "model": 69.9, "implied": 54.5, "first_pitch": "9:38 PM ET",
        "clubs": {
            "away": {"club": "ROYALS", "pitcher": "Seth Lugo",
                     "bats": [("Bobby Witt Jr.", ".355", 1)]},
            "home": {"club": "ANGELS", "pitcher": "Grayson Rodriguez",
                     "bats": [("Mike Trout", ".388", 2)]},
        },
        "row": {
            "away_era": "4.35", "away_whip": "1.35", "away_k9": "7.15",
            "away_pitcher_throws_hand": "R", "away_p_last5_pitcher_nrfi": "0.8",
            "home_era": "5.92", "home_whip": "1.47", "home_k9": "8.42",
            "home_pitcher_throws_hand": "R", "home_p_last5_pitcher_nrfi": "0.4",
            "park_factor": "0.99", "wx_is_dome": "0.0",
            "wx_temp_c": "27.2", "wx_wind_kmh": "12.9",
        },
    }
    joined = " ".join(mp.build_facts(night))
    assert "Seth Lugo" in joined and "Grayson Rodriguez" in joined
    # BOTH framings of the last-5 record, so the model never has to subtract
    assert "scoreless in 4 of his last 5" in joined
    assert "allowed a first-inning run in the other 1" in joined
    # and who bats against whom, so the causal link is stated, not inferred
    assert "against Grayson Rodriguez" in joined
    assert "against Seth Lugo" in joined


def test_build_facts_survives_a_night_with_no_row():
    """`row` is optional — a caller holding only the summary gets the handful
    of facts that do not need it, not a KeyError."""
    night = {
        "model": 69.9, "implied": 54.5, "first_pitch": "9:38 PM ET",
        "clubs": {
            "away": {"club": "ROYALS", "pitcher": "Seth Lugo", "bats": []},
            "home": {"club": "ANGELS", "pitcher": "Grayson Rodriguez", "bats": []},
        },
    }
    assert mp.build_facts(night)


def test_the_seed_allowlist_stays_minimal():
    """Every seeded integer is a free pass handed to a fabricated count.

    Only "1" is seeded (for "1st inning"); the rest of the set comes from the
    facts and from the night's own stake and price.
    """
    got = mp._allowed_numbers([], {"stake": 0.0, "odds": 0.0})
    assert got == {"1", "0", "0.0"}          # seed + the stake/odds of this stub
    assert mp._allowed_numbers([], {"stake": 7.0, "odds": -120.0}) == {
        "1", "7.0", "120"}


def _night(first_pitch="1:10 PM ET"):
    return {
        # Full precision, as `load_night` supplies it — the edge is computed
        # from these and rounded ONCE, so 66.87 - 54.55 prints 12.3, not the
        # 12.4 you would get by subtracting the two displayed figures.
        "side": "YRFI", "stake": 7.0, "odds": -120.0,
        "model": 66.87, "implied": 54.55, "first_pitch": first_pitch,
        "clubs": {
            "away": {"club": "REDS", "pitcher": "Andrew Abbott", "bats": []},
            "home": {"club": "WHITE SOX", "pitcher": "Davis Martin", "bats": []},
        },
    }


def test_every_line_but_the_paragraph_is_built_in_python():
    """The play, side, model number, price and stake are ledger facts on
    their own lines. None of them may depend on the model — that is the T8.30
    lesson: publish what the system STAKED."""
    post = mp.build_post(_night(), verbose=False)[0]
    assert "⚾ Reds @ White Sox" in post          # club names, not CIN/CWS
    assert "🎯 Either Team to Score in the 1st — YES" in post   # never "yes run"
    assert "📈 Model Probability: 66.9%" in post
    assert "🎰 Market odds: -120" in post          # plain ASCII for pasting
    assert "🔥 7 UNIT PLAY" in post                # trailing .0 stripped
    assert post.endswith("#MLB #MLBBets #SportsBetting #BackfistBets")


@pytest.mark.parametrize("first_pitch,expected", [
    ("9:38 PM ET", "TONIGHT’S"),
    ("1:10 PM ET", "TODAY’S"),
    ("4:05 PM ET", "TODAY’S"),
    ("7:05 PM ET", "TONIGHT’S"),
])
def test_the_daypart_matches_first_pitch(first_pitch, expected):
    """"Tonight's" over a 1:10 first pitch is the kind of wrong a reader
    notices before they notice anything else. Shares `make_card._daypart`
    rather than keeping a second copy of the rule."""
    assert mp.build_post(_night(first_pitch), verbose=False)[0].startswith(
        f"{expected} #1 PLAY")


def test_a_half_unit_stake_keeps_its_decimal():
    night = _night()
    night["stake"] = 7.5
    assert "🔥 7.5 UNIT PLAY" in mp.build_post(night, verbose=False)[0]


def test_a_plus_price_keeps_its_sign():
    night = _night()
    night["odds"] = 125.0
    assert "🎰 Market odds: +125" in mp.build_post(night, verbose=False)[0]


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

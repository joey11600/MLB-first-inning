"""An alert names the book the price ACTUALLY came from, never a constant.

THE INCIDENT. Until 2026-08-20 three subscriber-facing Telegram bodies and one
Discord line printed the literal string "DK"/"DraftKings" beside the captured
price. That was harmless only for as long as DraftKings really was the source.

It stopped being true the moment the price source moved to FanDuel — forced,
because DraftKings does not serve the first-inning market (`totals_1st_1_innings`)
through The Odds API at all. Verified live on the 2026-08-20 slate: DK quotes
moneyline on the same events, and returns nothing for the first-inning total,
while FanDuel / BetMGM / BetRivers / BetOnline each covered 9 of 9 games.

A subscriber who read "DK -122" and opened DraftKings would have found a
different number — against a stake quarter-Kelly had sized for FanDuel's price.
That is exactly T8.30's rule (publish what the system ACTUALLY did) applied to
the book name rather than the stake, and the spread that night was 19 cents
between the best and worst book, which their own notes price at roughly two
stake steps.

These tests pin the rule and its mirror.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import tracker  # noqa: E402


@pytest.mark.parametrize("stored,shown", [
    ("DraftKings",   "DK"),
    ("draftkings",   "DK"),
    ("FanDuel",      "FD"),
    ("fanduel",      "FD"),
    ("BetMGM",       "MGM"),
    ("Caesars",      "CAE"),
    ("Pinnacle",     "PIN"),
])
def test_the_label_follows_the_stored_book(stored, shown):
    assert tracker._book_label({"sportsbook": stored}) == shown


def test_an_unknown_book_is_named_in_full_rather_than_guessed():
    """Better a long, correct name than a clever, wrong abbreviation."""
    assert tracker._book_label({"sportsbook": "BetOnline.ag"}) == "BetOnline.ag"


@pytest.mark.parametrize("row", [{}, {"sportsbook": ""}, {"sportsbook": "   "}])
def test_no_book_means_no_book_name(row):
    """A row with no captured price names NO book. The old code printed a
    constant here, which is how a missing price came to be labelled DK."""
    assert tracker._book_label(row) == "Market"


def test_no_subscriber_alert_hardcodes_a_book_name():
    """THE REGRESSION GUARD. Catches the next person who types "DK " into an
    alert body instead of reading the column."""
    src = (REPO / "tracker.py").read_text(encoding="utf-8")
    # Message bodies are f-string list items; a literal book name inside one
    # is the bug. Comments and docstrings legitimately discuss DraftKings.
    offenders = [
        ln.strip() for ln in src.splitlines()
        if re.search(r'f"[^"]*\b(DK|DraftKings)\b\s*\{', ln)
    ]
    assert not offenders, f"alert body names a book literally: {offenders}"


def test_the_python_and_typescript_rules_agree():
    """`shortBook()` in dashboard/components/BoardRow.tsx is the same rule in
    another language. CLAUDE.md's standing convention: change one, change both."""
    ts = (REPO / "dashboard" / "components" / "BoardRow.tsx").read_text(encoding="utf-8")
    block = ts.split("function shortBook", 1)[1].split("\n}", 1)[0]
    pairs = re.findall(r'startsWith\("([a-z]+)"\)\s*\)?\s*return\s+"([A-Z]+)"', block)
    assert pairs, "could not parse shortBook() — did it move or change shape?"
    for prefix, expected in pairs:
        assert tracker._book_label({"sportsbook": prefix}) == expected, (
            f"TS maps {prefix!r} -> {expected!r}; Python disagrees")

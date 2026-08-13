"""
T8.35 layer 1 -- sticky lineups.

THE INCIDENT.  2026-08-13 CIN@CWS: MLB withdrew the CWS lineup card from
schedule?hydrate=lineups for ~55 minutes; `fetch_top3_batters` has no
memory, so the model regressed the home side to team-average batting and
the T-60 commit sized 2u where the rule on the published probability
said 7u.  The actual first-pitch top-3 was the withdrawn card.

THE FIX UNDER TEST.  `_apply_sticky_lineups(top3, prior_row)`: a card
once SEEN (recorded in the ledger row's `*_lineup_json` +
`*_top3c_source`) refills an EMPTY side of a fresh fetch; a NON-empty
fetch always wins, which is how a real scratch replaces the memory
instead of being masked by it.

The fixture rows use the REAL batter IDs from the incident ledger row
(CIN: De La Cruz / Stewart / Bleday; CWS: Meidroth / Grichuk / Vargas) --
the "would 2026-08-13 have been bridged?" question is asserted directly.

House rules honoured: no test reads data/picks_2026.csv (fixtures are
literal dicts) and none hardcodes a slate-relative date (sticky is
date-blind by design; the caller filters the ledger to today's rows).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mlb_first_inning_predictor as pred  # noqa: E402

# The real cards from the incident row (data/picks_2026.csv, 2026-08-13,
# game_pk 824561), copied as literals.
CIN_IDS = [682829, 701398, 668709]   # De La Cruz, Stewart, Bleday
CWS_IDS = [805367, 545341, 678246]   # Meidroth, Grichuk, Vargas


def _lineup_json(ids):
    return json.dumps([
        {"id": pid, "name": f"Batter {pid}", "bats": "R",
         "obp": 0.330, "slg": 0.450, "iso": 0.180, "ab": 300}
        for pid in ids
    ])


def _prior_row(away_source="lineup", home_source="lineup",
               away_ids=CIN_IDS, home_ids=CWS_IDS):
    return {
        "away_top3c_source": away_source,
        "home_top3c_source": home_source,
        "away_lineup_json": _lineup_json(away_ids),
        "home_lineup_json": _lineup_json(home_ids),
    }


# ---------------------------------------------------------------------------
# The incident, replayed
# ---------------------------------------------------------------------------

def test_the_2026_08_13_outage_is_bridged():
    # 12:06 ET state: fresh fetch has the away card, home side withdrawn.
    top3 = {"away_top3": list(CIN_IDS), "home_top3": []}
    sticky = pred._apply_sticky_lineups(top3, _prior_row())
    assert sticky == ["home"]
    assert top3["home_top3"] == CWS_IDS      # the withdrawn card, restored
    assert top3["away_top3"] == CIN_IDS      # live side untouched


def test_a_real_scratch_still_replaces_the_memory():
    # A revised card arrives as a NON-empty fetch -- it must win, or
    # sticky would mask genuine lineup changes behind yesterday's card.
    revised = [805367, 545341, 999999]
    top3 = {"away_top3": list(CIN_IDS), "home_top3": revised}
    sticky = pred._apply_sticky_lineups(top3, _prior_row())
    assert sticky == []
    assert top3["home_top3"] == revised


def test_both_sides_withdrawn_both_bridged():
    top3 = {"away_top3": [], "home_top3": []}
    sticky = pred._apply_sticky_lineups(top3, _prior_row())
    assert sticky == ["away", "home"]
    assert top3["away_top3"] == CIN_IDS
    assert top3["home_top3"] == CWS_IDS


# ---------------------------------------------------------------------------
# When memory must NOT engage
# ---------------------------------------------------------------------------

def test_no_memory_without_a_seen_card():
    # Row says team_fallback: no card was ever posted; lineup_json may
    # even hold stale names from a previous code path.  Nothing to stick.
    top3 = {"away_top3": [], "home_top3": []}
    sticky = pred._apply_sticky_lineups(
        top3, _prior_row(away_source="team_fallback",
                         home_source="team_fallback"))
    assert sticky == []
    assert top3 == {"away_top3": [], "home_top3": []}


def test_sticky_source_chains_across_cycles():
    # Cycle N bridged the outage and wrote source="lineup_sticky".
    # Cycle N+1's memory read must trust that row too, or the bridge
    # collapses after one cycle.
    top3 = {"away_top3": list(CIN_IDS), "home_top3": []}
    sticky = pred._apply_sticky_lineups(
        top3, _prior_row(home_source="lineup_sticky"))
    assert sticky == ["home"]
    assert top3["home_top3"] == CWS_IDS


def test_no_prior_row_is_inert():
    top3 = {"away_top3": [], "home_top3": []}
    assert pred._apply_sticky_lineups(top3, None) == []
    assert top3 == {"away_top3": [], "home_top3": []}


def test_partial_memory_is_not_a_card():
    # A 2-batter fragment would understate the top-of-order aggregate;
    # refusing it keeps the fallback path honest.
    row = _prior_row()
    row["home_lineup_json"] = _lineup_json(CWS_IDS[:2])
    top3 = {"away_top3": list(CIN_IDS), "home_top3": []}
    assert pred._apply_sticky_lineups(top3, row) == []
    assert top3["home_top3"] == []


def test_malformed_lineup_json_neither_raises_nor_engages():
    row = _prior_row()
    row["home_lineup_json"] = "{not json"
    top3 = {"away_top3": list(CIN_IDS), "home_top3": []}
    assert pred._apply_sticky_lineups(top3, row) == []
    assert top3["home_top3"] == []


def test_blank_lineup_json_is_inert():
    row = _prior_row()
    row["home_lineup_json"] = ""
    top3 = {"away_top3": list(CIN_IDS), "home_top3": []}
    assert pred._apply_sticky_lineups(top3, row) == []


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------

def test_flag_default_is_off(monkeypatch):
    monkeypatch.delenv("NRFI_STICKY_LINEUPS", raising=False)
    assert pred._sticky_lineups_enabled() is False


def test_flag_enabled_value(monkeypatch):
    monkeypatch.setenv("NRFI_STICKY_LINEUPS", "enabled")
    assert pred._sticky_lineups_enabled() is True
    # Anything that is not the literal "enabled" stays off -- same
    # contract as NRFI_STAKE_REDERIVE, so ops muscle memory transfers.
    monkeypatch.setenv("NRFI_STICKY_LINEUPS", "1")
    assert pred._sticky_lineups_enabled() is False


def test_memory_reader_requires_complete_ids():
    # _prior_top3_ids is the trust boundary between the ledger and the
    # model input; entries without ids must not half-fill a card.
    row = _prior_row()
    broken = json.loads(row["home_lineup_json"])
    del broken[1]["id"]
    row["home_lineup_json"] = json.dumps(broken)
    assert pred._prior_top3_ids(row, "home") == []
    assert pred._prior_top3_ids(row, "away") == CIN_IDS

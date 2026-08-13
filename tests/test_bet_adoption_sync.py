"""
T8.35 layer 2 -- the probability that sized a bet travels WITH the bet.

THE SPLICE THIS KILLS.  2026-08-13: Railway committed the No.1 as
(YRFI 58.6%, 2u, bet=Y) -- internally coherent.  GHA's CSV copy still
held the pre-outage 66.87%.  `sync_csv_from_supabase` pulled the MONEY
columns but not the probability (predict-owned, never synced), the next
log_picks run froze the local 66.87% beside Railway's 2u, and the
full-row mirror pushed that splice back over Supabase.  The published
record then claimed a 7u probability next to a 2u stake, and only a
human noticed.

The fix under test: at the ADOPTION MOMENT (this CSV learning
bet_placed=Y from Supabase for the first time) the committing host's
probability set and pick identity sync atomically with the money.
STRICTLY N->Y: frozen rows never re-adopt (that would let any future
Supabase writer silently edit history under a settled bet), and
unplaced rows keep their own fresh compute (pre-lock, local is the
honest number -- T8.18).

All I/O is faked: fetch_supabase_rows returns fixtures, _csv_path
points at tmp_path.  No test reads the real ledger or any network.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker  # noqa: E402
from tools import sync_csv_from_supabase as sync_mod  # noqa: E402


def _csv_row(**over):
    """A full-width CSV row (every tracker.FIELDS column present)."""
    row = {f: "" for f in tracker.FIELDS}
    row.update({
        "date": "2026-08-13", "season": "2026", "game_pk": "824561",
        "away_team": "CIN", "home_team": "CWS",
        "game_time_et": "1:10 PM ET",
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "pick_label": "STRONG YRFI",
        "nrfi_prob": "0.3313", "yrfi_prob": "0.6687",
        "nrfi_prob_raw": "0.349", "yrfi_prob_raw": "0.651",
        "bet_placed": "N",
    })
    row.update(over)
    return row


def _sb_row(**over):
    """The committing host's mirror: coherent (probability, stake) pair."""
    sb = {
        "date": "2026-08-13", "game_pk": "824561",
        "bet_placed": "Y", "units_risked": 2,
        "market_nrfi_odds": "-110", "market_yrfi_odds": "-120",
        "sportsbook": "DraftKings",
        "nrfi_prob": 0.414, "yrfi_prob": 0.586,
        "nrfi_prob_raw": 0.4271, "yrfi_prob_raw": 0.5729,
        "pick_side": "YRFI", "pick_strength": "STRONG",
        "pick_label": "STRONG YRFI",
    }
    sb.update(over)
    return sb


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Point the sync at a tmp CSV + fixture Supabase rows; return a
    driver that runs sync_csv and reads the row back."""
    csv_file = tmp_path / "picks_2026.csv"
    monkeypatch.setattr(sync_mod, "_csv_path", lambda season: csv_file)
    monkeypatch.setattr(sync_mod, "_get_client", lambda: object())

    def drive(local_row, sb_rows):
        tracker._write_rows(csv_file, [local_row])
        monkeypatch.setattr(sync_mod, "fetch_supabase_rows",
                            lambda client, season, dates: sb_rows)
        sync_mod.sync_csv(2026, ["2026-08-13"], dry_run=False)
        return tracker._read_rows(csv_file)[0]

    return drive


# ---------------------------------------------------------------------------
# The adoption moment (N -> Y): the incident, replayed and killed
# ---------------------------------------------------------------------------

def test_adoption_brings_the_sizing_probability(harness):
    row = harness(_csv_row(), [_sb_row()])
    # Money adopted (this part always worked)...
    assert row["bet_placed"] == "Y"
    assert row["units_risked"] == "2"
    assert row["market_yrfi_odds"] == "-120"
    # ...and now the probability that SIZED those 2u comes with it,
    # instead of the local pre-outage 0.6687 freezing beside them.
    assert row["yrfi_prob"] == "0.586"
    assert row["nrfi_prob"] == "0.414"
    assert row["yrfi_prob_raw"] == "0.5729"
    # The published pair is now coherent: rule(0.586 @ -120) is the 2u
    # that was staked.  stake_drift's invariant holds by construction.


def test_adoption_brings_the_pick_identity(harness):
    # This host's fresh compute demoted the row before syncing; the
    # committing host bet STRONG YRFI.  Adopting the stake without the
    # identity would manufacture a bet_placed=Y LEAN row -- forbidden
    # everywhere downstream (LEAN is track-only).
    local = _csv_row(pick_side="NRFI", pick_strength="LEAN",
                     pick_label="LEAN NRFI")
    row = harness(local, [_sb_row()])
    assert row["pick_side"] == "YRFI"
    assert row["pick_strength"] == "STRONG"
    assert row["pick_label"] == "STRONG YRFI"


def test_adoption_skips_blank_remote_values(harness):
    # A lagging mirror may carry the bet flag before the probability
    # lands.  Blank never overwrites: local values survive, money still
    # adopts, and the next sync (probability now present, but bet
    # already Y) must NOT half-adopt later -- frozen is frozen.
    row = harness(_csv_row(), [_sb_row(yrfi_prob=None, nrfi_prob=None)])
    assert row["bet_placed"] == "Y"
    assert row["yrfi_prob"] == "0.6687"     # local kept, not blanked


# ---------------------------------------------------------------------------
# The two directions that must NOT adopt
# ---------------------------------------------------------------------------

def test_frozen_rows_never_readopt(harness):
    # Row already bet_placed=Y: its (probability, stake) pair is the
    # settled record.  A later Supabase writer changing the probability
    # must not reach it -- that is a silent history edit under a placed
    # bet, the exact class T2.23/T2.25 exist to prevent.
    local = _csv_row(bet_placed="Y", units_risked="7",
                     nrfi_prob="0.3313", yrfi_prob="0.6687")
    row = harness(local, [_sb_row(yrfi_prob=0.51, nrfi_prob=0.49,
                                  units_risked=7,
                                  profit_loss_units=5.833,
                                  graded_result="WIN")])
    assert row["yrfi_prob"] == "0.6687"       # frozen record untouched
    assert row["nrfi_prob"] == "0.3313"
    # Money-set columns still flow on frozen rows (grades land later):
    assert row["graded_result"] == "WIN"
    assert row["profit_loss_units"] == "5.833"


def test_unplaced_rows_keep_their_own_compute(harness):
    # bet_placed=N on both sides: pre-lock, each host's fresh compute is
    # the honest probability (T8.18 re-derives from it).  Sync moves
    # odds, not beliefs.
    row = harness(_csv_row(), [_sb_row(bet_placed="N", units_risked=None,
                                       yrfi_prob=0.55, nrfi_prob=0.45)])
    assert row["bet_placed"] == "N"
    assert row["yrfi_prob"] == "0.6687"       # local belief stands
    assert row["market_yrfi_odds"] == "-120"  # odds still sync


def test_adoption_is_one_shot(harness, tmp_path, monkeypatch):
    # Run the sync twice: adoption fires on the first pass; the second
    # pass sees bet=Y locally and must leave the adopted values alone
    # even if Supabase has moved.
    csv_file = tmp_path / "picks_2026.csv"
    monkeypatch.setattr(sync_mod, "_csv_path", lambda season: csv_file)
    monkeypatch.setattr(sync_mod, "_get_client", lambda: object())
    tracker._write_rows(csv_file, [_csv_row()])

    monkeypatch.setattr(sync_mod, "fetch_supabase_rows",
                        lambda c, s, d: [_sb_row()])
    sync_mod.sync_csv(2026, ["2026-08-13"], dry_run=False)

    monkeypatch.setattr(sync_mod, "fetch_supabase_rows",
                        lambda c, s, d: [_sb_row(yrfi_prob=0.9)])
    sync_mod.sync_csv(2026, ["2026-08-13"], dry_run=False)

    row = tracker._read_rows(csv_file)[0]
    assert row["yrfi_prob"] == "0.586"        # first adoption, kept

"""
Does the published stake still match the rule that was supposed to produce it?

T8.18 PART 3. Parts 1 and 2 stop a stake freezing against a probability the
model has already replaced. This part is the system checking itself, so a
recurrence surfaces the same night instead of the next time somebody happens
to squint at a board -- which is how all three victims were actually found.

THE NAIVE INVARIANT DOES NOT WORK, IN EITHER DIRECTION, and half the tests
here exist to keep that from being "simplified" back in:

  * `units_risked == kelly_stake_units(p, odds, game_date=...)` per row seeds
    the day's exposure from a ledger that already contains the row being
    re-derived, so the budget is spent before the first comparison and every
    row recomputes to 0.
  * `units_risked == kelly_stake_units(p, odds)` has no daily cap, so every
    legitimately cap-trimmed row flags. That is not hypothetical: the
    cap-trimmed test below is a real slate shape where the stored 1.00u is
    CORRECT and the uncapped rule says 2.00u.

So the check is a per-day replay, and these tests are mostly about the
difference between a stake that is wrong and a stake that is merely small.

NO LITERAL SLATE DATES. Not for the 24 h-lock reason that bit
tests/test_selection.py -- this module never reads a clock -- but because the
check has an ERA FLOOR the operator is expected to move forward. Pin a literal
date below the floor and the day it is crossed these tests go quietly green
while asserting nothing. Every date here is derived from the floor itself.
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker                       # noqa: E402
from tools import stake_drift        # noqa: E402

pytestmark = pytest.mark.money

# The first slate the check covers, and the day before it.
ERA = stake_drift.STAKE_DRIFT_ERA_FLOOR
BEFORE_ERA = (date.fromisoformat(ERA) - timedelta(days=1)).isoformat()
NEXT_DAY = (date.fromisoformat(ERA) + timedelta(days=1)).isoformat()
SEASON = int(ERA[:4])


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    """THE REPLAY MUST NOT READ THE LIVE LEDGER, and this fixture proves it.

    `_check_one_day` seeds `tracker._daily_committed[date]` immediately after
    the reset. That seed is the whole trick: `_committed_on` memoises per date
    and only reads the CSV on a miss, so priming the key makes the replay start
    the night with an empty budget instead of one already spent on the very
    rows it is about to re-derive. If the seed ever stops happening, this stub
    fires, `_committed_on` turns it into KellyBudgetUnavailable, and the day
    lands in `rep.day_errors` -- which every test below asserts is empty.
    """
    def _boom(_path):
        raise AssertionError(
            "the replay read the ledger; the per-day seed must have been lost")
    monkeypatch.setattr(tracker, "_read_rows", _boom)
    monkeypatch.setattr(tracker, "_csv_path", lambda _s: "unused.csv")


@pytest.fixture(autouse=True)
def _isolate_kelly_state():
    """`check_rows` saves and restores these itself; this is the belt to that
    braces, so one failing test cannot leave 15u on the books and silently
    zero every stake in the next one."""
    saved = (dict(tracker._daily_committed), tracker._bankroll_cache,
             tracker._kelly_batch_epoch)
    yield
    tracker._daily_committed.clear()
    tracker._daily_committed.update(saved[0])
    tracker._bankroll_cache = saved[1]
    tracker._kelly_batch_epoch = saved[2]


def _row(away, home, *, p, odds, units, slate=ERA, side="YRFI", **extra):
    """One settled ledger row. `p` is the model's probability for the SIDE BET."""
    p_nrfi = p if side == "NRFI" else 1.0 - p
    row = {
        "date": slate, "game_pk": f"{away}{home}",
        "away_team": away, "home_team": home,
        "pick_side": side, "pick_strength": "STRONG",
        "pick_label": f"STRONG {side}", "bet_placed": "Y",
        "nrfi_prob": f"{p_nrfi:.4f}", "yrfi_prob": f"{1.0 - p_nrfi:.4f}",
        "market_nrfi_odds": "", "market_yrfi_odds": "",
        "units_risked": units, "graded_result": "",
        "sportsbook": "DraftKings",
    }
    row["market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"] = odds
    row.update(extra)
    return row


def _check(rows, **kw):
    rep = stake_drift.check_rows(rows, season=SEASON, **kw)
    # A day the replay could not run is not a pass. Assert it everywhere so a
    # check that goes blind can never read as a check that found nothing.
    assert rep.day_errors == []
    return rep


# ---------------------------------------------------------------------------
# the thing it is for
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_flags_a_locked_row_whose_stake_no_longer_matches():
    """THE 2026-08-02 DET@OAK ROW. The probability fell 0.6994 -> 0.6012
    pre-lock and the stake stayed at the 7u the higher figure had earned.
    At 0.6012 against -145 the rule says 1u, so the operator was published a
    bet SEVEN TIMES the size the model supported.

    Note the direction: T8.18 is not only about under-staking. Two of the
    three victims were over-staked."""
    rep = _check([_row("DET", "OAK", p=0.6012, odds="-145", units="7.00")])

    assert len(rep.violations) == 1
    f = rep.violations[0]
    assert (f.game, f.stored, f.expected) == ("DET@OAK", 7.00, 1.00)
    assert f.delta == pytest.approx(6.00)
    assert rep.ok is False
    assert "STAKE DRIFT" in "\n".join(stake_drift.render(rep))


def test_ignores_pre_lock_rows():
    """A pre-lock figure is a PROJECTION, not a published stake.

    It is allowed to move -- that is the entire point of T8.18 PART 1, which
    re-derives it on every tick so it tracks the model. Comparing a moving
    projection against a snapshot of the rule would flag the whole board all
    evening and train the operator to ignore the alert. Only `bet_placed="Y"`
    -- money actually committed at a captured price -- is in scope."""
    rep = _check([_row("DET", "OAK", p=0.6012, odds="-145", units="7.00",
                       bet_placed="N")])

    assert rep.violations == []
    assert rep.rows_compared == 0
    assert rep.days_checked == 0


def test_ignores_pre_era_flat_1u_history():
    """Before the 2026-07-30 unit re-basing the ledger was written under sizing
    rules that no longer exist, so re-deriving those rows proves nothing about
    today's code. Measured: `--all` reports 310 violations over 366 rows. A
    check that cries wolf on 85% of history is a check nobody reads.

    The second half is what stops this being a way to hide a real finding: the
    SAME row one day later is flagged, so the silence is the floor and not the
    row being uninteresting."""
    stale = dict(p=0.6012, odds="-145", units="7.00")

    quiet = _check([_row("DET", "OAK", slate=BEFORE_ERA, **stale)])
    assert quiet.violations == []
    assert quiet.days_checked == 0

    loud = _check([_row("DET", "OAK", slate=ERA, **stale)])
    assert len(loud.violations) == 1


# ---------------------------------------------------------------------------
# the cap -- the reason a naive per-row check is unimplementable
# ---------------------------------------------------------------------------

def test_ignores_a_cap_trimmed_row_because_the_replay_resolves_it():
    """THE TEST THAT DECIDES WHETHER THIS CHECK IS USABLE, and it passes
    row-for-row.

    A real slate shape: two strong plays take 7u each, and the third is
    legitimately trimmed to the 1u of room the 15u/day cap left it -- kept
    exact rather than rounded up by the never-round-up-through-a-cap guard in
    `kelly_stake_units`. The stored 1.00u is CORRECT.

    A per-row check without the daily cap says that row should be 2.00u and
    flags it. The per-day replay reproduces the trim and stays quiet, which is
    the whole justification for the extra machinery."""
    slate = [
        _row("KCA", "COL", p=0.7128, odds="-150", units="7.00"),
        _row("CWS", "TBA", p=0.6660, odds="-115", units="7.00"),
        _row("MIL", "LAA", p=0.5809, odds="-115", units="1.00"),
    ]
    rep = _check(slate)

    assert rep.violations == []
    assert rep.cap_order == []          # resolved outright, not suppressed
    assert rep.rows_compared == 3
    assert [round(f.expected, 2) for f, _ in rep.compared] == [7.00, 7.00, 1.00]

    # ...and this is what the naive uncapped invariant would have said about
    # the trimmed row. 1.00u stored, 2.00u "expected": a false alarm every
    # heavy night, on a row where the ledger is right.
    tracker.kelly_reset_daily_committed()
    assert tracker.kelly_stake_units(0.5809, "-115") == 2.0


def test_a_cap_bound_split_is_reported_as_cap_order_not_as_drift():
    """THE BLIND SPOT, PINNED SO IT STAYS THE SIZE IT IS.

    On a night that exhausts the budget, the split depends on the order it was
    handed out. This replay allocates best-bet-first (lock_commit's own sort
    key); the historical ledger was written first-come-first-served, which
    `kelly_stake_units`' docstring names as a known limitation and CHECK 7 of
    `tools/verify_kelly_wiring.py` now measures. Same 15u, different split.

    So a cap-bound day whose ledger and replay TOTALS agree is reported as a
    note, not a violation. That is a deliberate trade: on such a night a
    genuinely stale stake can hide behind a matching total. The alternative --
    one alarm every cap-bound night -- gets the check ignored inside a week.

    The second half is the floor under the trade. Change the money at risk and
    the totals stop matching, and it is a violation again."""
    def slate(mil_units, det_units):
        return [
            _row("CWS", "TBA", p=0.7128, odds="-130", units="8.00"),
            _row("KCA", "COL", p=0.6903, odds="-155", units="5.00"),
            _row("MIL", "LAA", p=0.6288, odds="-115", units=mil_units),
            _row("DET", "OAK", p=0.5885, odds="-140", units=det_units),
        ]

    # ledger 8 + 5 + 1.5 + 0.5 = 15.0; replay 8 + 5 + 2.0 + 0.0 = 15.0.
    # Both are correct allocations of one budget.
    same_total = _check(slate("1.50", "0.50"))
    assert same_total.violations == []
    assert len(same_total.cap_order) == 1
    assert "allocation order, not drift" in same_total.cap_order[0]

    # 8 + 5 + 0.5 + 0.5 = 14.0. One unit of real money differs, so the
    # suppression does not apply.
    different_total = _check(slate("0.50", "0.50"))
    assert len(different_total.violations) == 2
    assert different_total.cap_order == []


# ---------------------------------------------------------------------------
# rows that were never Kelly stakes in the first place
# ---------------------------------------------------------------------------

def test_ignores_manual_odds_and_cluster_demoted_rows():
    """None of these three had a Kelly stake to reproduce, so comparing them
    to the rule is guaranteed noise:

      * a manual odds override (data/manual_odds_overrides.csv) writes a flat
        1u when the column was blank -- a deliberate operator repair;
      * a cluster demotion is the operator taking a play off the board, and
        `tools/apply_cluster_demotion.py` deliberately leaves pick_strength
        alone so the row still reads STRONG;
      * a POSTPONED row is booked flat by end_of_day_check and can be
        re-graded later (T1.5), so it never stops being a row.

    Every stake here is wildly wrong on purpose. If any exemption stops
    matching -- a renamed sportsbook string, a reworded demotion label -- this
    goes red rather than filling the operator's alerts with known noise."""
    rows = [
        _row("DET", "OAK", p=0.6012, odds="-145", units="7.00",
             sportsbook=stake_drift.MANUAL_ODDS_BOOK),
        _row("SDP", "ARI", p=0.6873, odds="-120", units="9.00",
             pick_label=f"{stake_drift.CLUSTER_DEMOTION_PREFIX} -- thin pitcher"),
        _row("LAD", "SFG", p=0.6288, odds="-120", units="2.00",
             graded_result="POSTPONED"),
    ]
    rep = _check(rows)

    assert rep.violations == []
    assert rep.rows_compared == 0
    assert rep.exempt == {"manual odds override": 1, "cluster demotion": 1,
                          "postponed / suspended": 1}

    # The operator's own escape hatch, keyed (date, game_pk), reaches a row
    # nothing else would exempt.
    plain = _row("DET", "OAK", p=0.6012, odds="-145", units="7.00")
    assert len(_check([plain]).violations) == 1
    assert _check([plain], exempt_keys={(ERA, "DETOAK")}).exempt == {
        "operator exemption file": 1}


def test_an_exempt_row_still_consumes_the_nights_budget():
    """AN EXEMPT ROW IS NOT AN ABSENT ROW. A flat-1u orphan heal is 1u
    genuinely at risk, so the rows we DO compare have to be replayed against
    the room that was actually left, not a budget we know they never had.

    Here 10u is spoken for by a manual override. The remaining play is worth
    8u uncapped and stored 5u -- which is exactly the 5u of room left. Drop
    the seeding and the replay says 8u and flags a correct stake."""
    rep = _check([
        _row("AAA", "BBB", p=0.6288, odds="-120", units="10.00",
             sportsbook=stake_drift.MANUAL_ODDS_BOOK),
        _row("SDP", "ARI", p=0.6873, odds="-120", units="5.00"),
    ])

    assert rep.violations == []
    assert rep.rows_compared == 1
    assert rep.compared[0][0].expected == pytest.approx(5.00)

    tracker.kelly_reset_daily_committed()
    assert tracker.kelly_stake_units(0.6873, "-120") == 8.0


# ---------------------------------------------------------------------------
# the check must not corrupt what it is checking
# ---------------------------------------------------------------------------

def test_the_replay_restores_tracker_kelly_state():
    """`check_rows` runs inside `tools/pl_calc.py` and `tools/reconcile.py`,
    and reconcile is on the 5-minute money path. Rule R2 makes it reset the
    daily tally, the bankroll cache and the batch epoch before it can size
    anything -- and a reset mid-batch in a process that is allocating real
    stakes wipes the budget out from under them. A self-check that can corrupt
    the thing it checks is not a self-check."""
    tracker._daily_committed.clear()
    tracker._daily_committed["sentinel"] = 7.5
    tracker._bankroll_cache = 123.0
    tracker._kelly_batch_epoch = 41

    _check([
        _row("DET", "OAK", p=0.6012, odds="-145", units="7.00"),
        _row("KCA", "COL", p=0.7128, odds="-150", units="7.00", slate=NEXT_DAY),
    ])

    assert tracker._daily_committed == {"sentinel": 7.5}
    assert tracker._bankroll_cache == 123.0
    assert tracker._kelly_batch_epoch == 41

"""
The pick has to COMMIT when its window opens, even if DraftKings goes quiet.

T8.18 PART 2. Until `tools/lock_commit.py` existed, a STRONG pick's stake was
computed in exactly one place -- `tracker._apply_odds_to_row` -- which only
runs when a NEW DK price arrives. So a pick whose price never got re-quoted
inside its 60-minute lock window never flipped to `bet_placed="Y"` at all; it
was stamped hours later by `tools/end_of_day_check.py`'s orphan heal, AFTER
first pitch, at whatever stake a hours-old probability had produced. Three
git-history-verified victims:

    2026-08-09 LAD@ARI  p 0.5831 -> 0.6288 pre-lock; stake stayed 2u, rule 5u
    2026-08-02 DET@OAK  p 0.6994 -> 0.6012 pre-lock; stake stayed 7u, rule 1u
    2026-08-04 SD@ARI   p 0.7128 -> 0.6873 pre-lock; stake stayed 9u, rule 8u

Each was found BY EYE, on the dashboard, days later.

THE CLOCK IS FROZEN, AND THAT IS THE POINT OF THE `clock` FIXTURE. Every
predicate this tool depends on -- `_is_inside_lock_window`, `_game_has_started`
-- reads `datetime.now()`. tests/test_selection.py records what happens when a
suite pins a literal slate date instead: it shipped green on 2026-08-07 and
went red by itself overnight when the date aged past a 24 h defensive lock,
blaming a fix that had never touched tracker.py. So NOTHING here is a literal
date. Slate dates and game times are derived from a frozen "now", and the
frozen clock is also what lets a test say "and again five minutes later"
without sleeping.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker                       # noqa: E402
from tools import lock_commit        # noqa: E402

pytestmark = pytest.mark.money

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

class _Clock:
    """A frozen ET clock, installed over `tracker.datetime`.

    `advance()` moves it without sleeping, which is how the idempotency test
    below can honestly say "the next cron tick, five minutes later".
    """

    def __init__(self, monkeypatch):
        self.now = datetime.now(ET).replace(second=0, microsecond=0)
        holder = self

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return holder.now.astimezone(tz)
                return holder.now.astimezone().replace(tzinfo=None)

        monkeypatch.setattr(tracker, "datetime", _DT)

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)

    def at(self, minutes: int) -> tuple[str, str]:
        """(slate date, game_time_et) for a first pitch `minutes` from now.

        Negative means the game has already started. Both halves come from the
        same instant, so a game scheduled across midnight still lands on its
        own slate date instead of yesterday's.
        """
        t = self.now + timedelta(minutes=minutes)
        return t.strftime("%Y-%m-%d"), t.strftime("%I:%M %p ET")


@pytest.fixture
def clock(monkeypatch):
    return _Clock(monkeypatch)


class _Ledger:
    """The fake on-disk ledger, plus counters for everything the tool writes.

    `_read_rows` returns the SAME list the tests hold, which is deliberate:
    `tracker._committed_on` re-reads the ledger to seed the day's spent budget,
    so a row committed earlier in the sweep genuinely shows up as spoken-for
    exposure -- exactly as it does in production.
    """

    def __init__(self, monkeypatch, rows):
        self.rows = rows
        self.writes = 0
        self.mirrored: list[list[dict]] = []
        self.notified: list[dict] = []
        self.read_calls = 0
        self._read_error: Exception | None = None
        self._reads_before_error = 0

        def _read(_path):
            self.read_calls += 1
            if (self._read_error is not None
                    and self.read_calls > self._reads_before_error):
                raise self._read_error
            return self.rows

        def _write(_path, rows):
            self.writes += 1

        monkeypatch.setattr(tracker, "_read_rows", _read)
        monkeypatch.setattr(tracker, "_csv_path", lambda _s: Path("unused.csv"))
        monkeypatch.setattr(tracker, "_write_rows", _write)
        monkeypatch.setattr(tracker, "_mirror_picks_to_supabase",
                            lambda _season, rows: self.mirrored.append(list(rows)))
        monkeypatch.setattr(tracker, "_notify_strong_locked_telegram",
                            lambda row, rivals=None: self.notified.append(row))

    def break_reads_after(self, n: int, exc: Exception) -> None:
        """Let the first `n` ledger reads succeed, then fail every later one.

        The initial slate read has to work or there is nothing to iterate; the
        failure we care about is the one `_committed_on` hits mid-batch, which
        is what `KellyBudgetUnavailable` was created for.
        """
        self._reads_before_error = n
        self._read_error = exc


@pytest.fixture
def ledger(monkeypatch):
    def _install(rows):
        return _Ledger(monkeypatch, rows)
    return _install


@pytest.fixture(autouse=True)
def _isolate_kelly_state():
    """Kelly's daily tally, bankroll cache and batch epoch are module globals.

    A test that leaves 15u on the books hands the next test an exhausted
    budget and every stake silently becomes 0 -- a whole file of tests that
    pass while asserting nothing.
    """
    saved = (dict(tracker._daily_committed), tracker._bankroll_cache,
             tracker._kelly_batch_epoch)
    yield
    tracker._daily_committed.clear()
    tracker._daily_committed.update(saved[0])
    tracker._bankroll_cache = saved[1]
    tracker._kelly_batch_epoch = saved[2]


@pytest.fixture(autouse=True)
def _arm_the_gate(monkeypatch):
    """`run()` itself is ungated -- `main()` owns the env check -- but arm it
    anyway so nothing here depends on the operator's shell."""
    monkeypatch.setenv("NRFI_LOCK_COMMIT", "enabled")


def _row(clock, minutes, *, p, odds, away, home, side="YRFI",
         strength="STRONG", bet_placed="N", units="2.0", **extra):
    """One ledger row. `p` is the model's probability FOR THE SIDE WE BET.

    `units_risked` defaults to a non-zero stale figure because that is the
    shape of all three T8.18 victims: a real number, sized hours earlier
    against a probability the model has since replaced.
    """
    iso, gtet = clock.at(minutes)
    p_nrfi = p if side == "NRFI" else 1.0 - p
    price_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    row = {
        "date": iso, "season": iso[:4], "game_pk": f"{away}{home}",
        "game_number": "1", "away_team": away, "home_team": home,
        "game_time_et": gtet,
        "nrfi_prob": f"{p_nrfi:.4f}", "yrfi_prob": f"{1.0 - p_nrfi:.4f}",
        "pick_side": side, "pick_strength": strength,
        "pick_label": f"{strength} {side}",
        "market_nrfi_odds": "", "market_yrfi_odds": "",
        "sportsbook": "DraftKings",
        # Captured hours ago and never refreshed -- THE T8.18 SHAPE. No price
        # arrives inside the window, so nothing else would ever size this row.
        "odds_captured_at": (clock.now - timedelta(hours=13)).isoformat(),
        "bet_placed": bet_placed, "units_risked": units,
        "edge_on_pick": "0.0376", "graded_result": "",
        "profit_loss_units": "",
    }
    row[price_col] = odds
    row.update(extra)
    return row


def _state(row):
    return (row.get("bet_placed"), row.get("units_risked"))


# ---------------------------------------------------------------------------
# the fix itself
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_a_row_locks_at_T60_with_no_price_arriving_in_the_window(clock, ledger):
    """THE 2026-08-09 LAD@ARI ROW, reconstructed.

    Price captured at 02:00 ET, probability 0.5831 at the time, stake 2u
    written then and frozen. By 15:10 the model said 0.6288 and the rule said
    5u. DK never re-quoted, so nothing re-sized it and nothing committed it;
    `bet_placed` only became "Y" after first pitch, via the orphan heal.

    The sweep has to do three things here, and the third is the one that was
    missing: re-derive the stake from the CURRENT probability, move the edge
    with it, and actually flip `bet_placed` to Y.
    """
    row = _row(clock, 59, p=0.6288, odds="-120", away="LAD", home="ARI")
    iso = row["date"]
    lg = ledger([row])

    assert lock_commit._is_candidate(row, iso) is True
    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0

    assert row["bet_placed"] == "Y"
    assert float(row["units_risked"]) == 5.0

    # ...and 5u is not a number this test invented. It is what the shipped
    # sizing rule returns for that probability at that captured price.
    tracker.kelly_reset_daily_committed()
    assert tracker.kelly_stake_units(0.6288, "-120") == 5.0

    # THE EDGE HAS TO MOVE WITH THE STAKE. The live row stored edge_on_pick
    # 0.0376 -- computed at 02:00 from p=0.5831 -- while the board printed
    # +8.3% from the row's own current probability. Two numbers, one bet.
    implied = 120.0 / 220.0
    assert float(row["edge_on_pick"]) == pytest.approx(0.6288 - implied, abs=1e-4)
    assert float(row["edge_on_pick"]) != pytest.approx(0.0376, abs=1e-4)

    assert lg.writes == 1
    assert lg.mirrored and row in lg.mirrored[0]
    assert len(lg.notified) == 1

    # --- and again on the next tick, five minutes later ------------------
    # Railway cycles every five minutes and GitHub Actions hourly, so this
    # code runs many times inside one lock window. T2.23 says the price and
    # the stake freeze the moment the bet is placed; a second commit that
    # re-sized or re-pinged would be the 2026-07-28 oscillation with a new
    # entry point.
    frozen = _state(row)
    clock.advance(5)
    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0

    assert _state(row) == frozen
    assert lg.writes == 1            # nothing touched -> nothing written
    assert len(lg.notified) == 1     # and nobody pinged twice


# ---------------------------------------------------------------------------
# the two ways a finished game gets in
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_a_finished_game_is_never_committed(clock, ledger):
    """`_is_inside_lock_window` IS UNBOUNDED ABOVE -- its own docstring says
    it returns True from T-60 "or after start, including post-game".

    So the window predicate alone does not mean "the bet is still live". Grading
    lags a late west-coast finish by a good while, and any ungraded row sitting
    in that gap would be committed, SIZED against tonight's remaining budget,
    and Telegrammed to the operator as a bet to go place -- on a game that is
    over. `_game_has_started` is the predicate that stops it.
    """
    row = _row(clock, -10, p=0.6288, odds="-120", away="SEA", home="OAK")
    iso, gtet = row["date"], row["game_time_et"]
    lg = ledger([row])

    # The trap, spelled out: this row IS inside the lock window.
    assert tracker._is_inside_lock_window(gtet, iso) is True
    assert tracker._game_has_started(gtet, iso) is True
    assert lock_commit._is_candidate(row, iso) is False

    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0
    assert _state(row) == ("N", "2.0")
    assert lg.writes == 0
    assert lg.notified == []


def test_a_cleared_postponed_row_is_never_committed(clock, ledger):
    """A postponement is NO ACTION, and it stays no action.

    Two shapes, one rule. (1) Still marked POSTPONED and, because the game was
    called late, sitting inside its own lock window -- caught by the terminal-
    grade filter, which includes POSTPONED and SUSPENDED even though T1.5 lets
    those rows be re-graded if play resumes. (2) The grade cleared back to
    blank while the original first pitch is hours gone -- nothing about the
    row says "finished" any more, and only `_game_has_started` catches it.

    Both matter because a committed row is a Telegram telling the operator to
    put money on a game that is not being played.
    """
    postponed = _row(clock, 40, p=0.6288, odds="-120", away="WSH", home="PHI",
                     graded_result="POSTPONED")
    cleared = _row(clock, -240, p=0.6288, odds="-120", away="CHC", home="COL",
                   graded_result="")
    iso = postponed["date"]
    lg = ledger([postponed, cleared])

    assert lock_commit._is_candidate(postponed, iso) is False
    assert lock_commit._is_candidate(cleared, cleared["date"]) is False

    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0
    assert lock_commit.run(cleared["date"], int(iso[:4]), dry_run=False) == 0

    assert _state(postponed) == ("N", "2.0")
    assert _state(cleared) == ("N", "2.0")
    assert lg.writes == 0
    assert lg.notified == []


# ---------------------------------------------------------------------------
# a refusal is a decision, and it has to be written down
# ---------------------------------------------------------------------------

def test_a_zero_stake_does_not_commit(clock, ledger):
    """Kelly returns 0 when the model does not beat the market's own implied
    probability. That is a refusal, and a refusal must never become a bet.

    NOTE WHAT THIS TEST DOES *NOT* ASSERT, and see the xfail below it.
    `_size_row_stake`'s docstring promises that at COMMIT a refusal is written
    as a literal numeric zero rather than blank. It is not, today: the
    `projection_mode` branch is tested BEFORE the `inside_lock` branch and
    carries no `and not inside_lock` guard, and this tool is the first caller
    to pass `units_strong=None` together with `inside_lock=True`. So the row
    keeps the 0.5u rounding floor instead. Nothing is committed either way --
    which is why this is a missed cleanup rather than a live defect.
    """
    row = _row(clock, 45, p=0.5000, odds="-135", away="MIN", home="MIL")
    iso = row["date"]
    lg = ledger([row])

    assert tracker.kelly_stake_units(0.5000, "-135") == 0.0
    assert lock_commit._is_candidate(row, iso) is True   # it reached the sizer

    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0

    assert row["bet_placed"] == "N"
    assert lg.notified == []
    # The stake is not blanked -- see the xfail. What matters for the money is
    # that no positive stake survived a refusal.
    assert float(row["units_risked"]) < tracker.KELLY_MIN_STAKE_UNITS * 10


# The strict xfail that used to sit here did its job: it turned the suite red
# the moment the branch-ordering fix landed, which is how the fix got noticed.
# `_size_row_stake` now tests `inside_lock` BEFORE `projection_mode`, so a
# commit-time refusal writes the honest numeric zero instead of keeping
# KELLY_ROUNDED_FLOOR.  That mattered for real money: end_of_day_check's orphan
# finder skips only `staked <= 0`, so a refusal left at 0.5u survived the filter
# and was stamped bet_placed="Y" after the game graded -- a fabricated bet, on
# every cap-exhausted night.  Keep this test; it is now the regression guard.
def test_a_commit_time_refusal_writes_an_honest_zero_not_a_floor(clock, ledger):
    """Why blank-or-floored is not good enough.

    `tools/end_of_day_check.py` uses `bet_placed == "N" AND units_risked > 0`
    as its ONLY discriminator between a T2.58 pre-lock pending and a
    deliberate Kelly refusal. Leave 0.5u on a refused row and the orphan heal
    still sees a pending bet after the game grades, and retroactively stamps
    it placed -- the 2026-07-28 P0-2 bet-fabrication incident, which booked
    P&L for wagers nobody made. A blank is no better: db/supabase_writer drops
    a blank write and the CSV-from-Supabase sync pulls the stale positive back
    over it on the next tick.
    """
    row = _row(clock, 45, p=0.5000, odds="-135", away="MIN", home="MIL")
    iso = row["date"]
    ledger([row])

    lock_commit.run(iso, int(iso[:4]), dry_run=False)

    assert row["bet_placed"] == "N"
    assert row["units_risked"] != ""                 # blank is dropped by the mirror
    assert float(row["units_risked"]) == 0.0         # ...and zero means zero


# ---------------------------------------------------------------------------
# the budget
# ---------------------------------------------------------------------------

def test_the_daily_cap_binds_across_the_committed_slate(clock, ledger):
    """15u a night is a PUBLISHED number (CLAUDE.md money rules), so it has to
    hold across a sweep that commits several games in one pass.

    It holds only because `run()` calls `kelly_reset_daily_committed()` ONCE,
    before the first row -- rule R2. A reset per row re-reads the ledger and
    hands every row the full 15u, and the cap silently stops binding.
    """
    slate = [
        _row(clock, 45, p=0.72, odds="-150", away="KCA", home="COL"),
        _row(clock, 45, p=0.70, odds="-160", away="CWS", home="TBA"),
        _row(clock, 45, p=0.68, odds="-140", away="MIL", home="LAA"),
        _row(clock, 45, p=0.66, odds="-125", away="DET", home="OAK"),
        _row(clock, 45, p=0.64, odds="-120", away="SDP", home="ARI"),
    ]
    iso = slate[0]["date"]
    ledger(slate)

    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0

    committed = [float(r["units_risked"]) for r in slate if r["bet_placed"] == "Y"]
    assert sum(committed) <= tracker.KELLY_MAX_DAILY_FRAC * 100.0
    assert sum(committed) == 15.0

    # BEST BET FIRST. The budget is handed out in `_rank_key` order, not file
    # order, so the strongest play on the board gets the money and the two
    # weakest are refused rather than the last two rows in the CSV. Pinning
    # the vector is what makes that visible if the sort key ever changes.
    #
    # THE TWO REFUSALS ARE "0.0", NOT "0.5". A cap-exhausted row at commit is a
    # refusal, and `_size_row_stake` tests `inside_lock` before
    # `projection_mode` so it writes the honest numeric zero rather than the
    # pre-lock rounding floor. That distinction is real money:
    # end_of_day_check's orphan finder skips only `staked <= 0`, so a refusal
    # left at 0.5u survives the filter and gets stamped bet_placed="Y" once the
    # game grades -- a fabricated bet, on every cap-exhausted night. Zero, not
    # blank, because the Supabase mirror drops a blank and the sync restores the
    # stale positive over it.
    assert [_state(r) for r in slate] == [
        ("Y", "8.0"), ("Y", "6.0"), ("Y", "1.0"), ("N", "0.0"), ("N", "0.0"),
    ]

    # THE NEXT TICK MUST NOT RE-ALLOCATE. The three committed rows now seed
    # `_committed_on` from disk at 15u, so the two refusals stay refused
    # instead of finding a fresh budget. This is the T7.x oscillation guard
    # reached through a second writer.
    before = [_state(r) for r in slate]
    clock.advance(5)
    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0
    assert [_state(r) for r in slate] == before


@pytest.mark.regression
def test_budget_read_failure_refuses_to_size(clock, ledger):
    """SIZING AGAINST AN UNKNOWN BUDGET IS WORSE THAN NOT SIZING.

    `_committed_on` used to swallow a read failure as `total = 0.0` and CACHE
    it, handing the rest of the batch a confidently-empty budget: with 14u
    already locked, the slate would allocate another 28u against a published
    15u/day cap. `KellyBudgetUnavailable` makes that loud, and the tool's
    contract is to leave the row EXACTLY as found.
    """
    row = _row(clock, 50, p=0.6873, odds="-120", away="SDP", home="ARI")
    iso = row["date"]
    lg = ledger([row])
    before = _state(row)
    # One good read for the slate itself; every read after that fails, which
    # is the one `_committed_on` makes.
    lg.break_reads_after(1, OSError("ledger vanished mid-batch"))

    assert lock_commit.run(iso, int(iso[:4]), dry_run=False) == 0

    assert _state(row) == before
    assert row["bet_placed"] != "Y"
    # NOT the uncapped figure either. 8u is what this bet is worth with the
    # whole budget free; writing it would be the exact failure the exception
    # exists to prevent.
    assert row["units_risked"] != "8.0"
    # Nothing reached disk, so even the edge columns the tool refreshed before
    # the sizing call are discarded -- the row on disk is untouched.
    assert lg.writes == 0
    assert lg.mirrored == []
    assert lg.notified == []


# ---------------------------------------------------------------------------
# one bet, one ping -- across two writers
# ---------------------------------------------------------------------------

def test_bet_locked_telegram_fires_exactly_once_across_both_flip_sites(clock, ledger):
    """There are now TWO places a STRONG pick can flip to `bet_placed="Y"`:
    `_apply_odds_to_row` when a price lands inside the window, and this sweep
    when one never does. Both fire the BET LOCKED alert on the transition.

    Two alerts for one bet is not cosmetic. The №1-only policy (operator,
    2026-08-05) means a pick-facing Telegram is the operator's instruction to
    go place money; a duplicate reads as a second bet. The dedup inside
    `_notify_event_telegram` is a backstop -- this test pins the FLIP SITES
    themselves, with the notifier stubbed, so a dedup outage cannot hide a
    double-fire.
    """
    def _price_arrives(row, season):
        # import_odds resets the tally at the top of its batch (rule R2); the
        # capped sizing call inside `_apply_odds_to_row` refuses to run
        # without it, so the test has to be a faithful batch too.
        tracker.kelly_reset_daily_committed()
        tracker._apply_odds_to_row(
            row, "+100", "-120", "DraftKings", 0.02, 0.5, 1.0,
            clock.now.astimezone(ZoneInfo("UTC")).isoformat(), season=season)

    # --- price first, then the sweep -------------------------------------
    row = _row(clock, 58, p=0.6288, odds="-120", away="LAD", home="ARI",
               opened_nrfi_odds="+100", opened_yrfi_odds="-120")
    iso, season = row["date"], int(row["date"][:4])
    lg = ledger([row])

    _price_arrives(row, season)
    assert row["bet_placed"] == "Y"
    assert len(lg.notified) == 1

    assert lock_commit.run(iso, season, dry_run=False) == 0
    assert len(lg.notified) == 1

    # --- the sweep first, then a late price ------------------------------
    row2 = _row(clock, 58, p=0.6288, odds="-120", away="LAD", home="ARI",
                opened_nrfi_odds="+100", opened_yrfi_odds="-120")
    lg2 = ledger([row2])

    assert lock_commit.run(iso, season, dry_run=False) == 0
    assert row2["bet_placed"] == "Y"
    assert len(lg2.notified) == 1

    _price_arrives(row2, season)
    assert len(lg2.notified) == 1
    # T2.23: the operator is already in the bet at the captured price, so a
    # later quote must not move it -- and must not re-announce it.
    assert row2["market_yrfi_odds"] == "-120"


# ---------------------------------------------------------------------------
# the env gate
# ---------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    """A NEW WRITER ON THE MONEY PATH EARNS ITS WAY ON; it is not switched on
    by a deploy. Same pattern as PREDICTOR_SCRAPE_DK and NRFI_STAKE_REDERIVE,
    and it matters more here because both hosts run this tool: leave the gate
    unset in the repo and the GitHub Actions steps no-op while Railway commits,
    which is the single-writer discipline the operator asked for.

    Only the literal "enabled" arms it. "true" / "1" / "yes" -- the spellings
    every other boolean env var in this repo accepts -- deliberately do not.
    """
    calls = []
    monkeypatch.setattr(lock_commit, "run",
                        lambda *a, **k: calls.append(a) or 0)
    monkeypatch.setattr(sys, "argv", ["lock_commit.py"])

    monkeypatch.delenv("NRFI_LOCK_COMMIT", raising=False)
    assert lock_commit.main() == 0
    assert calls == []

    for off in ("skip", "", "true", "1", "yes", "on", "disabled"):
        monkeypatch.setenv("NRFI_LOCK_COMMIT", off)
        assert lock_commit.main() == 0
    assert calls == []

    for on in ("enabled", "ENABLED", "  Enabled  "):
        monkeypatch.setenv("NRFI_LOCK_COMMIT", on)
        assert lock_commit.main() == 0
    assert len(calls) == 3


def test_the_tool_never_reddens_a_cron(monkeypatch):
    """SOFT-FAIL BY CONTRACT. This runs as a step inside the predict and grade
    workflows, whose real job is producing the night's picks. A bad night here
    has to degrade to "no commit happened", never to a red run that hides
    whether the slate was predicted at all."""
    monkeypatch.setenv("NRFI_LOCK_COMMIT", "enabled")
    monkeypatch.setattr(sys, "argv", ["lock_commit.py"])
    monkeypatch.setattr(lock_commit, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert lock_commit.main() == 0

"""T8.41 -- a rescheduled game's ORIGINAL-date row must never carry the makeup's
result, and a late price must never commit a bet on a game nobody can bet.

The game_pk survives a postponement, so from the makeup onward the linescore
fetched by game_pk is the MAKEUP's first inning.  Before 2026-09-05 the
grader's "was POSTPONED, re-checking" branch wrote that inning onto the
original-date row: 2026-07-27 CLE@CIN was locked 3.5 h after a postponed
first pitch and booked +0.80u from the 07-28 makeup.  MLB's officialDate is
the tie-breaker everywhere below.

Every MLB call is faked through `tracker.statsapi.get`; nothing here touches
the network or the real ledger (conftest refuses writes under data/).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker                                       # noqa: E402
from tools import heal_phantom_reschedule_rows as heal   # noqa: E402
from tools import lock_commit                        # noqa: E402

pytestmark = pytest.mark.money

D0, D1 = "2026-07-27", "2026-07-28"


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _game(state, detail, official, *, innings=None, resumed=False,
          cur_inning=None, inning_state=""):
    """A `statsapi.get("game", ...)` payload with just the fields the grader reads."""
    dt = {"officialDate": official}
    if resumed:
        dt["resumeDate"] = "2026-06-17"
    ls = {}
    if innings is not None:
        a, h = innings
        inn = {"away": {}, "home": {}}
        if a is not None:
            inn["away"]["runs"] = a
        if h is not None:
            inn["home"]["runs"] = h
        ls["innings"] = [inn]
    if cur_inning is not None:
        ls["currentInning"] = cur_inning
    if inning_state:
        ls["inningState"] = inning_state
    return {"gameData": {"status": {"abstractGameState": state,
                                    "detailedState": detail},
                         "datetime": dt},
            "liveData": {"linescore": ls}}


class _MLB:
    """Fake statsapi: one canned `game` payload per gamePk, call counter."""

    def __init__(self, monkeypatch, games: dict):
        self.games = games
        self.calls = 0
        outer = self

        class _API:
            @staticmethod
            def get(endpoint, params=None, **kw):
                outer.calls += 1
                if endpoint == "game":
                    return outer.games[int(params["gamePk"])]
                if endpoint == "schedule":
                    return {"dates": []}
                raise AssertionError(endpoint)

        monkeypatch.setattr(tracker, "statsapi", _API)


def _row(date, pk, away, home, *, side="PASS", strength="PASS",
         label="PASS - No edge", **extra):
    row = {f: "" for f in tracker.FIELDS}
    row.update({
        "date": date, "season": date[:4], "game_pk": str(pk), "game_number": "1",
        "away_team": away, "home_team": home, "game_time_et": "7:10 PM ET",
        "nrfi_prob": "0.4000", "yrfi_prob": "0.6000",
        "pick_side": side, "pick_strength": strength, "pick_label": label,
        "created_at": f"{date}T17:00:00Z",
    })
    row.update(extra)
    return row


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real CSV under tmp_path, read and written through tracker's own I/O."""
    path = tmp_path / "picks_2026.csv"
    monkeypatch.setattr(tracker, "_csv_path", lambda season: path)
    mirrored: list[list[dict]] = []
    monkeypatch.setattr(tracker, "_mirror_picks_to_supabase",
                        lambda season, rows: mirrored.append(list(rows)))
    voided: list[tuple] = []
    monkeypatch.setattr(tracker, "_notify_strong_voided_telegram",
                        lambda row, detail: voided.append((row["date"], detail)))
    for name in ("_notify_strong_graded_telegram",
                 "_notify_lineup_pending_resolved_telegram"):
        monkeypatch.setattr(tracker, name, lambda *a, **k: None)

    class _L:
        pass
    l = _L()
    l.path, l.mirrored, l.voided = path, mirrored, voided
    l.write = lambda rows: tracker._write_rows(path, rows)
    l.read = lambda: tracker._read_rows(path)
    l.by_date = lambda d: [r for r in l.read() if r["date"] == d]
    return l


# ---------------------------------------------------------------------------
# the fetch exposes what the rule needs
# ---------------------------------------------------------------------------

def test_fetch_first_inning_exposes_official_date_and_resumed(monkeypatch):
    _MLB(monkeypatch, {1: _game("Final", "Final", D1, innings=(4, 0), resumed=True)})
    st = tracker._fetch_first_inning(1, slate_date=D0)
    assert st["official_date"] == D1
    assert st["resumed"] is True
    assert (st["away_runs"], st["home_runs"], st["complete"]) == (4, 0, True)


def test_fetch_error_still_carries_the_new_keys(monkeypatch):
    class _Boom:
        @staticmethod
        def get(*a, **k):
            raise RuntimeError("down")
    monkeypatch.setattr(tracker, "statsapi", _Boom)
    st = tracker._fetch_first_inning(1, slate_date=D0)
    assert st["state"] == "ERROR"
    assert st["official_date"] == "" and st["resumed"] is False


def test_phantom_rule():
    assert tracker._phantom_reschedule_grade(D0, {"official_date": D0}) == ""
    assert tracker._phantom_reschedule_grade(D0, {"official_date": ""}) == ""
    assert tracker._phantom_reschedule_grade(D0, {"official_date": D1}) == "POSTPONED"
    # resume-day listing of a suspended game
    assert tracker._phantom_reschedule_grade(D1, {"official_date": D0, "resumed": True}) == "SUSPENDED"
    # moved EARLIER, not suspended
    assert tracker._phantom_reschedule_grade(D1, {"official_date": D0, "resumed": False}) == "POSTPONED"


# ---------------------------------------------------------------------------
# grade_date
# ---------------------------------------------------------------------------

def test_original_row_stays_postponed_after_the_makeup_is_played(ledger, monkeypatch):
    """THE 07-27 CLE@CIN SHAPE.  The original-date row was POSTPONED on the
    night; the makeup played the next day with a 4-0 first inning.  The
    re-check must leave the original row POSTPONED with no runs and no P&L,
    keep the published bet columns untouched, and grade the makeup row."""
    orig = _row(D0, 824490, "CLE", "CIN", side="YRFI", strength="STRONG",
                label="STRONG YRFI", bet_placed="Y", units_risked="1.0",
                market_nrfi_odds="-105", market_yrfi_odds="-125",
                odds_captured_at="2026-07-28T02:39:18+00:00",
                actual_result="POSTPONED", graded_result="POSTPONED",
                graded_at="2026-07-28T03:50:00Z")
    makeup = _row(D1, 824490, "CLE", "CIN", side="YRFI", strength="STRONG",
                  label="STRONG YRFI", bet_placed="Y", units_risked="2.0",
                  market_nrfi_odds="+100", market_yrfi_odds="-130")
    ledger.write([orig, makeup])
    _MLB(monkeypatch, {824490: _game("Final", "Final", D1, innings=(4, 0))})

    tracker.grade_date(D0, 2026)
    o = ledger.by_date(D0)[0]
    assert o["graded_result"] == "POSTPONED" and o["actual_result"] == "POSTPONED"
    assert o["fi_away_runs"] == "" and o["fi_home_runs"] == "" and o["fi_total_runs"] == ""
    assert o["profit_loss_units"] == ""
    assert (o["bet_placed"], o["units_risked"], o["market_yrfi_odds"],
            o["odds_captured_at"]) == ("Y", "1.0", "-125", "2026-07-28T02:39:18+00:00")
    assert ledger.voided == []          # a re-check of a known PP is silent
    assert ledger.mirrored and ledger.mirrored[-1][0]["date"] == D0

    tracker.grade_date(D1, 2026)
    m = ledger.by_date(D1)[0]
    assert m["graded_result"] == "WIN" and m["actual_result"] == "YRFI"
    assert (m["fi_away_runs"], m["fi_home_runs"], m["fi_total_runs"]) == ("4", "0", "4")
    assert float(m["profit_loss_units"]) > 0


def test_first_grade_of_a_rescheduled_row_is_postponed_and_pings_once(ledger, monkeypatch):
    """MLB has already moved the game (officialDate = tomorrow) and the game
    endpoint shows the makeup as a Preview.  The row is POSTPONED on first
    sight -- no 6-hour stale-scheduled wait -- and the voided ping fires."""
    row = _row(D0, 7, "ARI", "PIT", side="YRFI", strength="STRONG",
               label="STRONG YRFI", bet_placed="Y", units_risked="3.0",
               market_yrfi_odds="-120")
    ledger.write([row])
    _MLB(monkeypatch, {7: _game("Preview", "Scheduled", D1)})
    tracker.grade_date(D0, 2026)
    r = ledger.by_date(D0)[0]
    assert r["graded_result"] == "POSTPONED"
    assert r["profit_loss_units"] == "" and r["bet_placed"] == "Y"
    assert ledger.voided == [(D0, "POSTPONED")]
    # and again tomorrow night: still POSTPONED, still silent
    tracker.grade_date(D0, 2026)
    assert ledger.by_date(D0)[0]["graded_result"] == "POSTPONED"
    assert len(ledger.voided) == 1


def test_resume_day_listing_of_a_suspended_game_is_suspended(ledger, monkeypatch):
    """2026-06-16/17 SF@ATL: suspended on the 16th, finished on the 17th.
    officialDate stays 06-16, so the 16th's row is the game and the 17th's
    row is a phantom -- SUSPENDED, no runs, no P&L."""
    real = _row("2026-06-16", 824912, "SF", "ATL")
    phantom = _row("2026-06-17", 824912, "SF", "ATL", side="NRFI",
                   strength="LEAN", label="LEAN NRFI", bet_placed="N")
    ledger.write([real, phantom])
    _MLB(monkeypatch, {824912: _game("Final", "Final", "2026-06-16",
                                     innings=(1, 2), resumed=True)})
    tracker.grade_date("2026-06-17", 2026)
    p = ledger.by_date("2026-06-17")[0]
    assert p["graded_result"] == "SUSPENDED" and p["fi_total_runs"] == ""
    assert p["profit_loss_units"] == ""
    tracker.grade_date("2026-06-16", 2026)
    r = ledger.by_date("2026-06-16")[0]
    assert r["graded_result"] == "PASS" and r["fi_total_runs"] == "3"


def test_suspended_original_row_keeps_the_old_behaviour(ledger, monkeypatch):
    """officialDate == the row's date, game Suspended before the first inning
    finished: the T2.7 branch marks it SUSPENDED (non-terminal) exactly as
    before this change."""
    row = _row(D0, 9, "BOS", "NYY", side="YRFI", strength="STRONG",
               label="STRONG YRFI", bet_placed="Y", units_risked="1.0",
               market_yrfi_odds="-110")
    ledger.write([row])
    _MLB(monkeypatch, {9: _game("Live", "Suspended", D0, innings=(0, None),
                                cur_inning=1, inning_state="Bottom")})
    tracker.grade_date(D0, 2026)
    r = ledger.by_date(D0)[0]
    assert r["graded_result"] == "SUSPENDED" and r["profit_loss_units"] == ""
    assert ledger.voided == [(D0, "Suspended")]


def test_grade_date_does_not_touch_a_terminally_graded_phantom(ledger, monkeypatch):
    """A row already graded WIN is skipped before any fetch -- that is the
    heal tool's job, pinned below -- so the live grader cannot silently
    rewrite history on its own."""
    wrong = _row(D0, 824490, "CLE", "CIN", side="YRFI", strength="STRONG",
                 label="STRONG YRFI", bet_placed="Y", units_risked="1.0",
                 market_yrfi_odds="-125", actual_result="YRFI",
                 graded_result="WIN", fi_away_runs="4", fi_home_runs="0",
                 fi_total_runs="4", profit_loss_units="0.800",
                 graded_at="2026-07-28T18:12:41+00:00")
    ledger.write([wrong])
    mlb = _MLB(monkeypatch, {824490: _game("Final", "Final", D1, innings=(4, 0))})
    tracker.grade_date(D0, 2026)
    assert ledger.by_date(D0)[0]["graded_result"] == "WIN"
    assert mlb.calls == 0


# ---------------------------------------------------------------------------
# part 2: a late price cannot commit a bet nobody can place
# ---------------------------------------------------------------------------

@pytest.fixture
def sizing(monkeypatch, tmp_path):
    """Everything `_apply_odds_to_row` touches besides the row itself."""
    monkeypatch.setattr(tracker, "_csv_path", lambda season: tmp_path / "picks_2026.csv")
    monkeypatch.setattr(tracker, "_write_rows", lambda *a, **k: None)
    monkeypatch.setattr(tracker, "_mirror_picks_to_supabase", lambda *a, **k: None)
    monkeypatch.setattr(tracker, "_notify_strong_locked_telegram", lambda *a, **k: None)
    monkeypatch.setattr(tracker, "_notify_strong_clv_telegram", lambda *a, **k: None)
    monkeypatch.setattr(tracker, "_is_inside_lock_window", lambda *a, **k: True)
    saved = (dict(tracker._daily_committed), tracker._bankroll_cache,
             tracker._kelly_batch_epoch)

    def _price(row, status):
        monkeypatch.setattr(tracker, "_read_rows", lambda _p: [row])
        if status is not None:
            monkeypatch.setattr(tracker, "_fetch_first_inning",
                                lambda pk, slate_date=None: status)
        tracker.kelly_reset_daily_committed()
        return tracker._apply_odds_to_row(
            row, "+100", "-120", "FanDuel", 0.02, 0.5, 1.0,
            "2026-07-28T02:39:18+00:00", season=2026)
    yield _price
    tracker._daily_committed.clear()
    tracker._daily_committed.update(saved[0])
    tracker._bankroll_cache = saved[1]
    tracker._kelly_batch_epoch = saved[2]


def _strong(**extra):
    return _row(D0, 824490, "CLE", "CIN", side="YRFI", strength="STRONG",
                label="STRONG YRFI", bet_placed="N", units_risked="2.0",
                nrfi_prob="0.3712", yrfi_prob="0.6288", **extra)


def test_late_price_after_first_pitch_refuses_a_postponed_game(sizing, monkeypatch):
    monkeypatch.setattr(tracker, "_game_has_started", lambda *a, **k: True)
    row = _strong()
    sizing(row, {"state": "Final", "detail": "Postponed", "official_date": D1,
                 "resumed": False, "away_runs": None, "home_runs": None,
                 "complete": False})
    assert row["bet_placed"] == "N"
    assert row["market_yrfi_odds"] == "-120"      # the price is still recorded


@pytest.mark.parametrize("status,why", [
    ({"state": "Live", "detail": "In Progress", "official_date": D0},
     "first pitch thrown"),
    ({"state": "Final", "detail": "Final", "official_date": D0},
     "game over"),
    ({"state": "Preview", "detail": "Scheduled", "official_date": D1},
     "belongs to another date"),
    ({"state": "ERROR", "detail": "timeout", "official_date": ""},
     "status unreadable -> fail closed"),
])
def test_late_price_refuses_when_the_market_is_closed(sizing, monkeypatch, status, why):
    monkeypatch.setattr(tracker, "_game_has_started", lambda *a, **k: True)
    row = _strong()
    sizing(row, status)
    assert row["bet_placed"] == "N", why


def test_late_price_on_a_delayed_game_still_commits(sizing, monkeypatch):
    """MLB still says Preview after the scheduled start (rain delay): the
    bet is placeable, so it commits exactly as before this change."""
    monkeypatch.setattr(tracker, "_game_has_started", lambda *a, **k: True)
    row = _strong()
    sizing(row, {"state": "Preview", "detail": "Delayed Start: Rain",
                 "official_date": D0, "resumed": False})
    assert row["bet_placed"] == "Y"


def test_price_before_first_pitch_never_asks_mlb(sizing, monkeypatch):
    monkeypatch.setattr(tracker, "_game_has_started", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(tracker, "_fetch_first_inning",
                        lambda pk, slate_date=None: calls.append(pk) or {})
    row = _strong()
    sizing(row, None)
    assert row["bet_placed"] == "Y" and calls == []


def test_graded_row_never_takes_a_new_bet_and_never_asks_mlb(sizing, monkeypatch):
    monkeypatch.setattr(tracker, "_game_has_started", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(tracker, "_fetch_first_inning",
                        lambda pk, slate_date=None: calls.append(pk) or {})
    row = _strong(graded_result="POSTPONED", actual_result="POSTPONED")
    sizing(row, None)
    assert row["bet_placed"] == "N" and calls == []


def test_lock_commit_candidate_refuses_a_postponed_game(monkeypatch):
    row = _strong(market_yrfi_odds="-120")
    monkeypatch.setattr(tracker, "_parse_game_time_et", lambda *a, **k: object())
    monkeypatch.setattr(tracker, "_is_inside_lock_window", lambda *a, **k: True)
    monkeypatch.setattr(tracker, "_game_has_started", lambda *a, **k: False)
    monkeypatch.setattr(tracker, "_fetch_first_inning", lambda pk, slate_date=None: {
        "state": "Final", "detail": "Postponed", "official_date": D0})
    assert lock_commit._is_candidate(row, D0) is False
    monkeypatch.setattr(tracker, "_fetch_first_inning", lambda pk, slate_date=None: {
        "state": "Preview", "detail": "Pre-Game", "official_date": D0})
    assert lock_commit._is_candidate(row, D0) is True
    # fails OPEN on an unreadable status -- the №1 is the product
    monkeypatch.setattr(tracker, "_fetch_first_inning", lambda pk, slate_date=None: {
        "state": "ERROR", "detail": "timeout", "official_date": ""})
    assert lock_commit._is_candidate(row, D0) is True


# ---------------------------------------------------------------------------
# the heal tool
# ---------------------------------------------------------------------------

def test_heal_rewrites_only_the_phantoms_and_is_idempotent(ledger, monkeypatch, tmp_path):
    wrong = _row(D0, 824490, "CLE", "CIN", side="YRFI", strength="STRONG",
                 label="STRONG YRFI", bet_placed="Y", units_risked="1.0",
                 market_nrfi_odds="-105", market_yrfi_odds="-125",
                 actual_result="YRFI", graded_result="WIN", fi_away_runs="4",
                 fi_home_runs="0", fi_total_runs="4", profit_loss_units="0.800",
                 graded_at="2026-07-28T18:12:41+00:00")
    makeup = _row(D1, 824490, "CLE", "CIN", actual_result="YRFI",
                  graded_result="PASS", fi_away_runs="4", fi_home_runs="0",
                  fi_total_runs="4")
    done = _row("2026-06-25", 823042, "ARI", "STL", side="YRFI", strength="STRONG",
                label="STRONG YRFI", bet_placed="Y", units_risked="1.0",
                actual_result="POSTPONED", graded_result="POSTPONED",
                graded_at="2026-06-27T03:54:00Z")
    done_makeup = _row("2026-07-23", 823042, "ARI", "STL", actual_result="YRFI",
                       graded_result="PASS", fi_away_runs="0", fi_home_runs="2",
                       fi_total_runs="2")
    single = _row(D0, 555, "HOU", "LAA", side="YRFI", strength="STRONG",
                  label="STRONG YRFI", bet_placed="Y", units_risked="1.0",
                  market_yrfi_odds="-115", actual_result="YRFI",
                  graded_result="WIN", fi_total_runs="1", profit_loss_units="0.870")
    ledger.write([wrong, makeup, done, done_makeup, single])
    statuses = {824490: {"state": "Final", "detail": "Final", "official_date": D1, "resumed": False},
                823042: {"state": "Final", "detail": "Final", "official_date": "2026-07-23", "resumed": False}}
    asked = []
    monkeypatch.setattr(tracker, "_fetch_first_inning",
                        lambda pk, slate_date=None: asked.append(pk) or statuses[pk])
    cleared = []
    monkeypatch.setattr(heal, "_clear_supabase",
                        lambda rows, season, fields: cleared.append((len(rows), tuple(fields))) or len(rows))
    monkeypatch.setattr(heal, "JOURNAL_DIR", tmp_path / "heals")

    # dry run: reads everything, writes nothing
    assert heal.run(2026, apply=False) == 0
    assert ledger.by_date(D0)[0]["graded_result"] == "WIN"
    assert sorted(asked) == [823042, 824490]        # one call per game, never the single
    assert not (tmp_path / "heals").exists()

    assert heal.run(2026, apply=True) == 0
    rows = {(r["date"], r["game_pk"]): r for r in ledger.read()}
    o = rows[(D0, "824490")]
    assert o["graded_result"] == "POSTPONED" and o["actual_result"] == "POSTPONED"
    assert (o["fi_away_runs"], o["fi_home_runs"], o["fi_total_runs"], o["profit_loss_units"]) == ("", "", "", "")
    assert (o["bet_placed"], o["units_risked"], o["market_yrfi_odds"]) == ("Y", "1.0", "-125")
    assert rows[(D1, "824490")]["graded_result"] == "PASS"          # the game's row
    assert rows[("2026-06-25", "823042")]["graded_at"] == "2026-06-27T03:54:00Z"  # already healed: untouched
    assert rows[(D0, "555")]["profit_loss_units"] == "0.870"       # a normal row
    assert ledger.mirrored[-1] == [o] or ledger.mirrored[-1][0]["date"] == D0
    assert cleared == [(1, ("fi_away_runs", "fi_home_runs", "fi_total_runs", "profit_loss_units"))]
    journals = list((tmp_path / "heals").glob("phantom_reschedule_*.csv"))
    assert len(journals) == 1
    body = journals[0].read_text(encoding="utf-8")
    assert "profit_loss_units,0.800,," in body and "graded_result,WIN,POSTPONED" in body

    # second apply: nothing left to do, no second journal
    n_mirrors = len(ledger.mirrored)
    assert heal.run(2026, apply=True) == 0
    assert len(ledger.mirrored) == n_mirrors
    assert len(list((tmp_path / "heals").glob("*.csv"))) == 1

"""T8.44 -- a game that has already started is never scored as a brand-new
ledger row, and a ledger that missed a day gets it back from the shared record.

The incident: during the T8.42 outage the git ledger was two days stale.
Railway rebuilds its ledger from git on every redeploy, every push
redeploys it, and the Supabase sync did not insert rows the CSV lacked --
so each fresh container scored the live slate mid/post-game as brand-new
rows and mirrored those numbers over its own pre-game record (2026-09-04
MIN@CWS: 3u sized on 62.24% at the lock, published 59.07% by midnight;
the whole 09-03 slate exists only in Supabase).

Everything below is faked: Supabase through `_adopt_supabase_pick_row` /
`fetch_full_pick_rows`, the clock through `_game_has_started`, the ledger
under tmp_path.  No network, no real ledger.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker                                              # noqa: E402
from tools import sync_csv_from_supabase as sync_mod        # noqa: E402
from tools import heal_2026_09_04_min_cws_prob as heal      # noqa: E402

pytestmark = pytest.mark.money

D = "2026-09-04"


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _result(pk, away, home, *, p_nrfi=0.4093, side="YRFI", conf="STRONG",
            time="7:40 PM ET"):
    """One predictor result dict with every key `log_picks` reads directly."""
    team = lambda abbr, name: {"abbr": abbr, "pitcher_name": name, "pitcher_id": "",
                               "pitcher_q": "ok", "batting_q": "ok", "lambda": 0.5}
    return {
        "game_pk": pk, "game_number": 1, "time": time,
        "away": team(away, "Zebby Matthews"), "home": team(home, "Erick Fedde"),
        "pick_side": side, "pick_conf": conf, "lambda_total": 1.0,
        "park_factor": 1.0, "nrfi_prob": p_nrfi, "yrfi_prob": 1 - p_nrfi,
        "over_1_5_prob": 0.4, "data_points": 10,
    }


def _sb_row(pk, away, home, **over):
    """The shared record's row: a coherent pre-game pick with its bet."""
    sb = {f: None for f in tracker.FIELDS}
    sb.update({
        "date": D, "season": 2026, "game_pk": str(pk), "game_number": 1,
        "away_team": away, "home_team": home, "game_time_et": "7:40 PM ET",
        "away_pitcher": "Zebby Matthews", "home_pitcher": "Erick Fedde",
        "nrfi_prob": 0.3776, "yrfi_prob": 0.6224, "nrfi_prob_raw": 0.4489,
        "pick_side": "YRFI", "pick_strength": "STRONG", "pick_label": "STRONG YRFI",
        "sizing_prob": 0.6224, "bet_placed": "Y", "units_risked": 3,
        "market_nrfi_odds": "+108", "market_yrfi_odds": "-138", "sportsbook": "FanDuel",
        "odds_captured_at": "2026-09-04T22:43:20+00:00",
        "created_at": "2026-09-04T13:03:15+00:00",
        "home_lineup_json": [{"id": 1, "name": "Someone"}],
    })
    sb.update(over)
    return sb


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "picks_2026.csv"
    monkeypatch.setattr(tracker, "_csv_path", lambda season: path)
    monkeypatch.setattr(sync_mod, "_csv_path", lambda season: path)
    monkeypatch.setattr(tracker, "DATA_DIR", tmp_path)       # pick_changes.csv lands here
    mirrored: list[list[dict]] = []
    monkeypatch.setattr(tracker, "_mirror_picks_to_supabase",
                        lambda season, rows: mirrored.append(list(rows)))
    monkeypatch.delenv("NRFI_STAKE_REDERIVE", raising=False)

    class _L:
        pass
    l = _L()
    l.path, l.mirrored = path, mirrored
    l.write = lambda rows: tracker._write_rows(path, rows)
    l.read = lambda: tracker._read_rows(path)
    return l


# ---------------------------------------------------------------------------
# log_picks: the started-game guard
# ---------------------------------------------------------------------------

def test_a_started_game_with_no_local_row_adopts_the_published_row(ledger, monkeypatch):
    """THE 09-04 SHAPE.  A host with no row for a game in progress must take
    the shared record's row verbatim -- probability, stamp, stake, odds --
    and must NOT mirror its own fresh score over it."""
    ledger.write([])
    monkeypatch.setattr(tracker, "_game_has_started", lambda gt, d: True)
    asked = []
    monkeypatch.setattr(tracker, "_adopt_supabase_pick_row",
                        lambda season, iso, pk: asked.append(pk) or
                        sync_mod.supabase_row_to_csv(_sb_row(824554, "MIN", "CWS")))
    n = tracker.log_picks(D, 2026, [_result(824554, "MIN", "CWS")])
    assert n == 1 and asked == ["824554"]
    rows = ledger.read()
    assert len(rows) == 1
    r = rows[0]
    assert (r["yrfi_prob"], r["nrfi_prob"], r["sizing_prob"]) == ("0.6224", "0.3776", "0.6224")
    assert (r["bet_placed"], r["units_risked"], r["market_yrfi_odds"]) == ("Y", "3", "-138")
    assert r["created_at"] == "2026-09-04T13:03:15+00:00"      # the ORIGINAL sighting
    assert json.loads(r["home_lineup_json"]) == [{"id": 1, "name": "Someone"}]
    assert ledger.mirrored == [[]]                                # nothing pushed back


def test_a_started_game_nobody_recorded_is_skipped_not_invented(ledger, monkeypatch):
    ledger.write([])
    monkeypatch.setattr(tracker, "_game_has_started", lambda gt, d: True)
    monkeypatch.setattr(tracker, "_adopt_supabase_pick_row", lambda season, iso, pk: None)
    n = tracker.log_picks(D, 2026, [_result(824554, "MIN", "CWS")])
    assert n == 0
    assert ledger.read() == []
    assert ledger.mirrored == [[]]


def test_a_game_not_yet_started_is_scored_and_mirrored_as_before(ledger, monkeypatch):
    ledger.write([])
    monkeypatch.setattr(tracker, "_game_has_started", lambda gt, d: False)
    monkeypatch.setattr(tracker, "_adopt_supabase_pick_row",
                        lambda *a: pytest.fail("pre-game rows never consult Supabase"))
    n = tracker.log_picks(D, 2026, [_result(824554, "MIN", "CWS")])
    assert n == 1
    r = ledger.read()[0]
    assert (r["nrfi_prob"], r["pick_label"], r["bet_placed"]) == ("0.4093", "STRONG YRFI", "")
    assert len(ledger.mirrored[-1]) == 1


def test_an_existing_row_is_refreshed_exactly_as_before(ledger, monkeypatch):
    """The guard is only on the brand-new branch: a row this host already
    has keeps going through the merge / freeze path."""
    existing = sync_mod.supabase_row_to_csv(_sb_row(824554, "MIN", "CWS"))
    ledger.write([existing])
    monkeypatch.setattr(tracker, "_game_has_started", lambda gt, d: True)
    monkeypatch.setattr(tracker, "_adopt_supabase_pick_row",
                        lambda *a: pytest.fail("existing rows never adopt"))
    n = tracker.log_picks(D, 2026, [_result(824554, "MIN", "CWS")])
    assert n == 1
    r = ledger.read()[0]
    # bet_placed=Y freezes the probability (T2.25); the fresh 0.4093 must not land
    assert (r["yrfi_prob"], r["units_risked"], r["bet_placed"]) == ("0.6224", "3", "Y")


def test_adopt_helper_matches_exactly_and_fails_open(monkeypatch):
    class _Client:
        pass
    monkeypatch.setattr("db.supabase_writer._get_client", lambda: _Client())
    monkeypatch.setattr(sync_mod, "fetch_full_pick_rows",
                        lambda client, season, dates, game_pk=None:
                        [_sb_row(824554, "MIN", "CWS")] if str(game_pk) == "824554" else [])
    got = tracker._adopt_supabase_pick_row(2026, D, "824554")
    assert got is not None and got["pick_label"] == "STRONG YRFI"
    assert tracker._adopt_supabase_pick_row(2026, D, "999") is None
    assert tracker._adopt_supabase_pick_row(2026, "2026-09-05", "824554") is None  # wrong date
    monkeypatch.setattr(sync_mod, "fetch_full_pick_rows",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert tracker._adopt_supabase_pick_row(2026, D, "824554") is None


# ---------------------------------------------------------------------------
# sync --insert-missing: a missed day comes back from the shared record
# ---------------------------------------------------------------------------

def test_insert_missing_backfills_in_date_order(ledger, monkeypatch):
    def _csv(date, pk, away, home):
        r = {f: "" for f in tracker.FIELDS}
        r.update({"date": date, "season": "2026", "game_pk": str(pk),
                  "away_team": away, "home_team": home, "bet_placed": "N"})
        return r
    ledger.write([_csv("2026-09-02", 1, "A", "B"), _csv("2026-09-04", 3, "E", "F")])
    monkeypatch.setattr(sync_mod, "_get_client", lambda: object())
    sb_missing = _sb_row(2, "C", "D", date="2026-09-03", bet_placed="N", units_risked=None,
                         market_yrfi_odds=None, sizing_prob=None, graded_result="PASS")
    sb_present = _sb_row(3, "E", "F", bet_placed="N", units_risked=None, sizing_prob=None)
    monkeypatch.setattr(sync_mod, "fetch_supabase_rows",
                        lambda client, season, dates: [sb_missing, sb_present])
    monkeypatch.setattr(sync_mod, "fetch_full_pick_rows",
                        lambda client, season, dates, game_pk=None: [sb_missing, sb_present])

    # without the flag: logged, not inserted (unchanged behaviour)
    sync_mod.sync_csv(2026, ["2026-09-03", "2026-09-04"], dry_run=False)
    assert [r["date"] for r in ledger.read()] == ["2026-09-02", "2026-09-04"]

    # dry run with the flag: reported, not written
    sync_mod.sync_csv(2026, ["2026-09-03", "2026-09-04"], dry_run=True, insert_missing=True)
    assert [r["date"] for r in ledger.read()] == ["2026-09-02", "2026-09-04"]

    n = sync_mod.sync_csv(2026, ["2026-09-03", "2026-09-04"], dry_run=False, insert_missing=True)
    rows = ledger.read()
    assert [r["date"] for r in rows] == ["2026-09-02", "2026-09-03", "2026-09-04"]
    ins = rows[1]
    assert (ins["game_pk"], ins["away_team"], ins["pick_label"], ins["graded_result"]) == ("2", "C", "STRONG YRFI", "PASS")
    assert ins["yrfi_prob"] == "0.6224" and ins["created_at"] == "2026-09-04T13:03:15+00:00"
    assert ins["away_top3_ops_vs_oppHand"] == ""          # a column the mirror never carried
    assert n >= 1

    # idempotent
    n2 = sync_mod.sync_csv(2026, ["2026-09-03", "2026-09-04"], dry_run=False, insert_missing=True)
    assert [r["date"] for r in ledger.read()] == ["2026-09-02", "2026-09-03", "2026-09-04"]
    assert n2 == 0


# ---------------------------------------------------------------------------
# the MIN@CWS heal
# ---------------------------------------------------------------------------

def _min_cws_row(**over):
    r = {f: "" for f in tracker.FIELDS}
    r.update({"date": D, "season": "2026", "game_pk": "824554", "away_team": "MIN",
              "home_team": "CWS", "pick_side": "YRFI", "pick_strength": "STRONG",
              "pick_label": "STRONG YRFI", "nrfi_prob": "0.4093", "yrfi_prob": "0.5907",
              "nrfi_prob_raw": "0.470789", "sizing_prob": "0.6224", "bet_placed": "Y",
              "units_risked": "3.0", "market_nrfi_odds": "108", "market_yrfi_odds": "-138",
              "edge_on_pick": "0.0426", "graded_result": "WIN", "profit_loss_units": "2.174"})
    r.update(over)
    return r


def test_heal_restores_the_lock_time_probability_and_nothing_else(ledger, monkeypatch, tmp_path):
    monkeypatch.setattr(heal, "JOURNAL_DIR", tmp_path / "heals")
    ledger.write([_min_cws_row()])
    assert heal.run(apply=False) == 0
    assert ledger.read()[0]["yrfi_prob"] == "0.5907"           # dry run wrote nothing
    assert heal.run(apply=True) == 0
    r = ledger.read()[0]
    assert (r["yrfi_prob"], r["nrfi_prob"]) == ("0.6224", "0.3776")
    assert (r["nrfi_prob_raw"], r["units_risked"], r["edge_on_pick"], r["profit_loss_units"]) == \
        ("0.470789", "3.0", "0.0426", "2.174")
    assert tracker.kelly_stake_units(float(r["yrfi_prob"]), r["market_yrfi_odds"], season=2026) == 3.0
    assert ledger.mirrored[-1][0]["yrfi_prob"] == "0.6224"
    body = next((tmp_path / "heals").glob("min_cws_prob_*.csv")).read_text(encoding="utf-8")
    assert "yrfi_prob,0.5907,0.6224" in body
    # a second run finds the published value already moved and refuses
    assert heal.run(apply=True) == 2


@pytest.mark.parametrize("over", [
    {"sizing_prob": "0.6000"}, {"units_risked": "2.0"}, {"bet_placed": "N"}, {"yrfi_prob": "0.6224"},
])
def test_heal_refuses_a_row_that_is_not_the_one_diagnosed(ledger, monkeypatch, tmp_path, over):
    monkeypatch.setattr(heal, "JOURNAL_DIR", tmp_path / "heals")
    ledger.write([_min_cws_row(**over)])
    assert heal.run(apply=True) == 2
    assert ledger.read()[0]["yrfi_prob"] == _min_cws_row(**over)["yrfi_prob"]

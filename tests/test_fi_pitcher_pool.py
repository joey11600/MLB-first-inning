"""fi_pitcher_pool -- the pooled first-inning pitcher xwOBA state the model reads.

Covers the state machine (ingest, season rollover, idempotence, shrinkage) on
synthetic rows, and -- when the research cache and the validated batch dump are
present on this machine -- that the incremental path reproduces the batch
builder's estimates (the thing that was validated in tools/refit2026/).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fi_pitcher_pool as P  # noqa: E402


def _row(pid, inning="1", events="", xw=""):
    return {"pitcher": str(pid), "inning": inning, "events": events,
            "estimated_woba_using_speedangle": xw}


def test_ingest_counts_only_first_inning_plate_appearances():
    st = P.new_state()
    rows = [_row(1, "1", "single", "0.9"), _row(1, "1", "strikeout"), _row(1, "1", ""),
            _row(1, "3", "home_run", "1.9"), _row(2, "1", "walk")]
    n = P.ingest_day(st, "2026-04-01", rows)
    assert n == 3                                   # two PAs for 1, one for 2; inning 3 ignored
    assert st["pitchers"]["1"]["cur"] == [2, 0.9]   # K counts as a PA at 0 weight
    assert st["pitchers"]["2"]["cur"] == [1, P.W_BB]
    assert st["league"]["pa"] == 3
    assert st["as_of"] == "2026-04-01"


def test_ingest_is_idempotent_by_date_and_refuses_out_of_order():
    st = P.new_state()
    P.ingest_day(st, "2026-04-02", [_row(1, "1", "single", "0.8")])
    assert P.ingest_day(st, "2026-04-02", [_row(1, "1", "single", "0.8")]) == 0
    assert P.ingest_day(st, "2026-04-01", [_row(1, "1", "single", "0.8")]) == 0
    assert st["pitchers"]["1"]["cur"] == [1, 0.8]


def test_estimate_shrinks_toward_league_mean_and_defaults_for_unknown():
    st = P.new_state()
    # league: 100 PAs at 0.30 from pitcher 9; pitcher 1: 10 PAs at 0.60
    P.ingest_day(st, "2026-04-01",
                 [_row(9, "1", "single", "0.30") for _ in range(100)]
                 + [_row(1, "1", "single", "0.60") for _ in range(10)])
    lm = P.league_mean(st)
    e1 = P.estimate(st, 1)
    assert lm < e1 < 0.60                            # pulled toward the league, not all the way
    assert abs(e1 - (10 * 0.60 + P.K_PA * lm) / (10 + P.K_PA)) < 1e-12
    assert P.estimate(st, 12345) is None
    assert P.value_or_default(st, 12345) == lm       # unknown starter = the shrinkage target
    assert P.value_or_default(st, None) == lm


def test_rollover_folds_the_closed_season_and_skips_pitchers_absent_for_a_whole_season():
    """Mirrors the validated batch builder: at a season boundary, every pitcher
    who appeared in the season being CLOSED has his sums folded into the prior
    pool at PRIOR_SEASON_W; a pitcher who then sits out an entire season is NOT
    decayed again at the next boundary (his pool waits as it was)."""
    st = P.new_state()
    P.ingest_day(st, "2025-06-01", [_row(1, "1", "single", "0.5"), _row(2, "1", "single", "0.5")])
    # 2026 opens: both appeared in 2025 -> both folded, even though only 1 pitches in 2026
    P.ingest_day(st, "2026-04-01", [_row(1, "5", "", "")])
    r1, r2 = st["pitchers"]["1"], st["pitchers"]["2"]
    assert r1["prior"] == [P.PRIOR_SEASON_W * 1, P.PRIOR_SEASON_W * 0.5] and r1["cur"] == [0.0, 0.0]
    assert r2["prior"] == [P.PRIOR_SEASON_W * 1, P.PRIOR_SEASON_W * 0.5] and r2["cur"] == [0.0, 0.0]
    # 2027 opens: pitcher 2 was absent all of 2026 -> NOT decayed again; pitcher 1 was present -> folded again
    P.ingest_day(st, "2027-04-01", [_row(2, "1", "strikeout")])
    assert abs(st["pitchers"]["2"]["prior"][0] - P.PRIOR_SEASON_W * 1) < 1e-12
    assert abs(st["pitchers"]["1"]["prior"][0] - P.PRIOR_SEASON_W * P.PRIOR_SEASON_W * 1) < 1e-12
    assert st["pitchers"]["2"]["cur"] == [1, 0.0]


def test_yesterday_et_is_a_date_string():
    y = P.yesterday_et()
    assert len(y) == 10 and y[4] == "-" and y[7] == "-"


def test_save_and_load_roundtrip(tmp_path):
    st = P.new_state()
    P.ingest_day(st, "2026-04-01", [_row(1, "1", "double", "1.2")])
    p = tmp_path / "pool.json"
    P.save_state(st, p)
    back = P.load_state(p)
    assert back["as_of"] == "2026-04-01" and P.estimate(back, 1) == P.estimate(st, 1)


@pytest.mark.skipif(not (ROOT / "data" / "candidates" / "fi_pitcher_pooled_current.json").exists()
                    or len(list((ROOT / "data" / "cache" / "statcast_zone").glob("*.csv.gz"))) < 500,
                    reason="research cache / validated batch dump not on this machine")
def test_incremental_rebuild_matches_validated_batch_builder():
    ref = json.loads((ROOT / "data" / "candidates" / "fi_pitcher_pooled_current.json").read_text())
    st = P.rebuild_from_cache()
    # the batch dump may be older or newer than the cache; compare at its as_of only
    if st["as_of"] != ref["as_of"]:
        pytest.skip(f"cache as_of {st['as_of']} != dump as_of {ref['as_of']}")
    diffs = [abs(P.estimate(st, pid) - rec["fi_xwoba"]) for pid, rec in ref["pitchers"].items()
             if P.estimate(st, pid) is not None]
    assert len(diffs) == len(ref["pitchers"])
    assert max(diffs) < 1e-3

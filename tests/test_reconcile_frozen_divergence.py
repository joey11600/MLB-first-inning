"""I6: the two writers must not silently disagree about a FROZEN row.

THE INCIDENT (2026-08-23).  Railway (every 5 min) and the GHA cron (hourly)
each compute every row from their own fetches, and `_pick_is_locked` freezes a
row at first pitch -- so each host can freeze a DIFFERENT number.  Supabase is
last-writer-wins and Railway writes 12x more often, so the dashboard served
CLE@COL at 60.0% for four hours while the committed ledger said 65.2%; it
converged only when an unrelated redeploy reset Railway's copy from git.  To
the operator that looked like a probability changing after the game ended.

I6 is REPORT-ONLY on purpose (same reasoning as I5): choosing a winner between
two frozen copies is arbitrary, and rewriting a frozen row is the write T2.23
refuses.  These tests pin what it notices and what it leaves alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import reconcile as R  # noqa: E402
import tracker  # noqa: E402


def _local_rows(monkeypatch, tmp_path, rows):
    csv_path = tmp_path / "picks_2026.csv"
    full = []
    for r in rows:
        base = {k: "" for k in tracker.FIELDS}
        base.update(r)
        full.append(base)
    tracker._write_rows(csv_path, full)
    monkeypatch.setattr(tracker, "_csv_path", lambda season: csv_path)


def _row(pk, nrfi, graded="PASS"):
    return {"date": "2026-08-23", "season": "2026", "game_pk": pk,
            "away_team": "CLE", "home_team": "COL", "game_number": "1",
            "pick_side": "PASS", "pick_strength": "LOW LAMBDA",
            "nrfi_prob": nrfi, "graded_result": graded}


def test_a_frozen_row_that_disagrees_is_reported(monkeypatch, tmp_path):
    """The exact 2026-08-23 shape: Supabase 0.4003, committed ledger 0.3483."""
    _local_rows(monkeypatch, tmp_path, [_row("824315", "0.3483")])
    remote = [dict(_row("824315", 0.4003))]
    found = R._check_i6_frozen_divergence(remote, 2026)
    assert len(found) == 1
    d = found[0]
    assert d["game"] == "CLE@COL"
    assert d["supabase_nrfi"] == 0.4003 and d["local_nrfi"] == 0.3483
    assert abs(d["delta"] - 0.052) < 1e-9


def test_agreement_is_silent(monkeypatch, tmp_path):
    _local_rows(monkeypatch, tmp_path, [_row("824315", "0.3483")])
    assert R._check_i6_frozen_divergence([dict(_row("824315", 0.3483))], 2026) == []


def test_rounding_noise_is_not_a_divergence(monkeypatch, tmp_path):
    """The CSV stores 4dp; a sub-0.001 gap is formatting, not disagreement."""
    _local_rows(monkeypatch, tmp_path, [_row("824315", "0.3483")])
    assert R._check_i6_frozen_divergence([dict(_row("824315", 0.34835))], 2026) == []


def test_an_unfrozen_row_is_ignored(monkeypatch, tmp_path):
    """Before the freeze the hosts are SUPPOSED to differ -- each is tracking
    fresh lineups and weather.  Flagging that would be pure noise."""
    _local_rows(monkeypatch, tmp_path, [_row("824315", "0.3483", graded="")])
    remote = [dict(_row("824315", 0.4003, graded=""))]
    assert R._check_i6_frozen_divergence(remote, 2026) == []


def test_a_row_missing_locally_is_ignored(monkeypatch, tmp_path):
    _local_rows(monkeypatch, tmp_path, [_row("824315", "0.3483")])
    assert R._check_i6_frozen_divergence([dict(_row("999999", 0.4003))], 2026) == []


def test_it_never_writes_to_the_ledger(monkeypatch, tmp_path):
    """REPORT-ONLY is the whole contract."""
    _local_rows(monkeypatch, tmp_path, [_row("824315", "0.3483")])
    before = tracker._read_rows(tracker._csv_path(2026))
    R._check_i6_frozen_divergence([dict(_row("824315", 0.4003))], 2026)
    assert tracker._read_rows(tracker._csv_path(2026)) == before


def test_an_unreadable_local_ledger_degrades_quietly(monkeypatch):
    def boom(season):
        raise OSError("no ledger here")
    monkeypatch.setattr(tracker, "_csv_path", boom)
    assert R._check_i6_frozen_divergence([dict(_row("824315", 0.4003))], 2026) == []

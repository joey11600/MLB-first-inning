"""reconcile's I3/I4 invariants honour the №1-only notification policy.

THE INCIDENT (2026-08-22/23).  Two STRONG bets locked on the 2026-08-21
slate.  The notifiers fire only for the night's №1 (policy of 2026-08-05)
and return early, with no dedup row, for the runner-up.  reconcile's I3/I4
still expected a notification for EVERY placed STRONG bet, so every 5-minute
cycle it "healed" the runner-up, the notifier declined again, and the next
cycle found the same anomaly: 326 heal notices in 36 hours for two bets that
were never meant to ping.  These tests pin the mirror: only the night's top
pick can be missing a notification.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import reconcile as R  # noqa: E402


def _row(pk, nrfi_prob, yrfi_odds, graded="WIN"):
    return {
        "date": "2026-08-21", "game_pk": pk, "game_time_et": "7:10 PM",
        "away_team": "AAA", "home_team": "BBB", "game_number": 1,
        "pick_side": "YRFI", "pick_strength": "STRONG", "bet_placed": "Y",
        "nrfi_prob": nrfi_prob, "market_nrfi_odds": "-118",
        "market_yrfi_odds": yrfi_odds, "units_risked": 4.0,
        "graded_result": graded, "profit_loss_units": 3.333,
    }


def _run(monkeypatch, rows):
    healed_i3, healed_i4 = [], []
    monkeypatch.setattr(R, "_get_client", lambda: object())
    monkeypatch.setattr(R, "_fetch_window", lambda client, dates, season: rows)
    monkeypatch.setattr(R, "_fetch_notifications", lambda client, dates: set())
    monkeypatch.setattr(R, "_within_lock_window", lambda *a, **k: True)
    monkeypatch.setattr(R, "_heal_i3_strong_locked",
                        lambda client, row, dry: healed_i3.append(row["game_pk"]) or True)
    monkeypatch.setattr(R, "_heal_i4_strong_graded",
                        lambda client, rows_, row, dry: healed_i4.append(row["game_pk"]) or True)
    monkeypatch.setattr(R, "_record_heal_notice", lambda stats: None)
    monkeypatch.setattr(R, "_check_i5_stake_drift", lambda *a, **k: None)
    stats = R.reconcile(["2026-08-21"], 2026, dry_run=False)
    return stats, healed_i3, healed_i4


def test_only_the_nights_top_pick_can_be_missing_a_notification(monkeypatch):
    # pk 1 is the stronger YRFI (lower P(no run)) -> the night's №1.
    top, runner_up = _row(1, 0.30, "+100"), _row(2, 0.38, "-120")
    stats, i3, i4 = _run(monkeypatch, [runner_up, top])
    assert i3 == [1] and i4 == [1], (i3, i4)
    assert stats.i3_strong_locked_fired == 1
    assert stats.i4_strong_graded_fired == 1


def test_a_lone_strong_bet_is_still_checked(monkeypatch):
    stats, i3, i4 = _run(monkeypatch, [_row(7, 0.35, "-105")])
    assert i3 == [7] and i4 == [7]


def test_the_gate_fails_open(monkeypatch):
    """A ranking error must over-report, never hide a missing alert."""
    import tracker
    def boom(*a, **k):
        raise RuntimeError("ranking broke")
    monkeypatch.setattr(tracker, "_row_is_nights_top_pick", boom)
    top, runner_up = _row(1, 0.30, "+100"), _row(2, 0.38, "-120")
    stats, i3, i4 = _run(monkeypatch, [runner_up, top])
    assert sorted(i3) == [1, 2] and sorted(i4) == [1, 2]

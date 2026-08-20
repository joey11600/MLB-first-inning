"""The Railway cycle publishes the card WHEN THE No.1 COMMITS, and only then.

WHY THIS STEP EXISTS AT ALL. The renderer used to be wired only into the
GitHub Actions predict step, so the published card lagged the lock by however
long the next GHA tick took. Measured live on 2026-08-19: LAD@COL committed at
20:30 ET for a 20:40 first pitch and the next tick was 21:00 — the "tonight's
play" post would have appeared twenty minutes after the game it advertised had
started. Railway is the host that commits the pick, so it is the only host
that can draw the card in the same cycle.

WHAT THESE TESTS PIN. The step runs every 5 minutes across a ~17-hour window,
so the two properties that matter are opposites of each other:

  * it MUST redraw when the No.1 changes (a stale card is the whole bug), and
  * it MUST NOT redraw when nothing changed — otherwise it is ~200 renders and
    ~200 OpenRouter calls a day to upsert three identical objects.

Plus the rule that outranks both: a card is only ever drawn for a COMMITTED
pick, because `pl_calc` counts only `bet_placed=Y` and a published bet the
tracked P&L does not contain is the T8.30 failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import workers.predictor_loop as pl  # noqa: E402


NIGHT = {
    "matchup": "DODGERS at ROCKIES",
    "side": "YRFI",
    "stake": 3.0,
    "odds": -160.0,
    "committed": True,
}


@pytest.fixture
def spy(monkeypatch):
    """Record which subprocesses the step would launch, and launch none."""
    calls: list[str] = []
    monkeypatch.setattr(pl, "run", lambda cmd, t, label: calls.append(label) or 0)
    monkeypatch.setattr(pl, "today_iso", lambda: "2026-08-19")
    monkeypatch.setattr(pl, "_LAST_CARD_SIG", None, raising=False)
    monkeypatch.setattr(pl, "_LAST_POST_SIG", None, raising=False)
    monkeypatch.delenv("PREDICTOR_PUBLISH_CARDS", raising=False)
    # Most tests assume this host CAN write the real paragraph; the
    # downgrade-guard tests below clear it explicitly.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return calls


def _night(monkeypatch, night):
    """Stand in for make_card.load_night, which the step imports by path."""
    import importlib.util

    def fake_spec(_name, _path):
        real = importlib.util.spec_from_loader("_fake", loader=None)

        class Mod:
            @staticmethod
            def load_night(_date):
                if night is None:
                    raise SystemExit("2026-08-19: no priced STRONG YRFI play "
                                     "on this slate")
                return dict(night)

        real.loader = type("L", (), {"exec_module": staticmethod(lambda m: None)})()
        return real, Mod

    spec, mod = fake_spec(None, None)
    monkeypatch.setattr(importlib.util, "spec_from_file_location",
                        lambda *a, **k: spec)
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda _s: mod)


def test_a_committed_number_one_draws_the_card_and_the_post(spy, monkeypatch):
    """THE POINT OF THE STEP. Card first, then the post — in that order."""
    _night(monkeypatch, NIGHT)
    assert pl.step_publish_cards() == 0
    assert spy == ["cards", "cards-post"]


def test_an_unchanged_number_one_is_not_redrawn(spy, monkeypatch):
    """THE COST GUARD. Second tick on the same slate must shell out to
    nothing — the cycle is 5 minutes and the card is a function of the
    No.1 alone."""
    _night(monkeypatch, NIGHT)
    pl.step_publish_cards()
    spy.clear()
    assert pl.step_publish_cards() == 0
    assert spy == []


@pytest.mark.parametrize("field,value", [
    ("matchup", "RANGERS at ANGELS"),   # the ledger crowned a different game
    ("stake", 7.0),                     # the stake re-derived
    ("odds", -145.0),                   # DK moved the price
])
def test_the_card_is_redrawn_when_the_number_one_changes(spy, monkeypatch,
                                                         field, value):
    """THE BUG THIS STEP FIXES. The No.1 genuinely moves during the day, and
    every one of these is a different card face."""
    _night(monkeypatch, NIGHT)
    pl.step_publish_cards()
    spy.clear()
    _night(monkeypatch, {**NIGHT, field: value})
    assert pl.step_publish_cards() == 0
    assert spy == ["cards", "cards-post"]


def test_no_committed_number_one_draws_nothing_and_stays_quiet(spy, monkeypatch):
    """Most of the day there is no committed No.1. That is the NORMAL state,
    not a fault: nothing is drawn and nothing is logged as a failure."""
    _night(monkeypatch, None)
    assert pl.step_publish_cards() == 0
    assert spy == []


def test_a_withdrawn_number_one_clears_the_cache(spy, monkeypatch):
    """If the No.1 vanishes and later returns IDENTICAL, the card must be
    redrawn rather than assumed still present — the object may have been
    pruned in between."""
    _night(monkeypatch, NIGHT)
    pl.step_publish_cards()
    _night(monkeypatch, None)
    pl.step_publish_cards()
    assert pl._LAST_CARD_SIG is None
    spy.clear()
    _night(monkeypatch, NIGHT)
    pl.step_publish_cards()
    assert spy == ["cards", "cards-post"]


def test_a_failed_render_does_not_publish_a_post(spy, monkeypatch):
    """A post with no card is a tweet about a bet the system did not
    publish — the T8.30 failure in a different costume."""
    _night(monkeypatch, NIGHT)
    monkeypatch.setattr(pl, "run",
                        lambda cmd, t, label: spy.append(label) or 1)
    assert pl.step_publish_cards() == 0
    assert spy == ["cards"]


def test_a_failed_render_is_retried_next_cycle(spy, monkeypatch):
    """The cache must NOT record a render that did not happen, or a failed
    night would never be retried."""
    _night(monkeypatch, NIGHT)
    monkeypatch.setattr(pl, "run", lambda cmd, t, label: 1)
    pl.step_publish_cards()
    assert pl._LAST_CARD_SIG is None


def test_a_failed_post_retries_without_redrawing_the_card(spy, monkeypatch):
    """THE REASON THE TWO MARKERS ARE SEPARATE. The `cards` bucket rejected
    `text/plain` with a 415 once already. If one marker covered both, a post
    stuck in that state would redraw three heavy Pillow plates every 5 minutes
    all night to retry one small text object."""
    _night(monkeypatch, NIGHT)

    def only_post_fails(cmd, t, label):
        spy.append(label)
        return 1 if label == "cards-post" else 0

    monkeypatch.setattr(pl, "run", only_post_fails)
    pl.step_publish_cards()
    assert spy == ["cards", "cards-post"]
    assert pl._LAST_CARD_SIG is not None      # the card DID publish
    assert pl._LAST_POST_SIG is None          # the post did not

    spy.clear()
    pl.step_publish_cards()
    assert spy == ["cards-post"], "the card must not be redrawn to retry a post"


def test_a_recovered_post_stops_retrying(spy, monkeypatch):
    """Once the post lands, the step goes quiet again."""
    _night(monkeypatch, NIGHT)
    monkeypatch.setattr(pl, "run",
                        lambda cmd, t, label: spy.append(label) or
                        (1 if label == "cards-post" else 0))
    pl.step_publish_cards()
    monkeypatch.setattr(pl, "run", lambda cmd, t, label: spy.append(label) or 0)
    spy.clear()
    pl.step_publish_cards()
    assert spy == ["cards-post"]
    spy.clear()
    pl.step_publish_cards()
    assert spy == []


def test_a_host_without_the_model_key_publishes_the_card_but_not_the_post(
        spy, monkeypatch):
    """THE REGRESSION THIS GUARDS. `make_post` falls back to a deterministic
    template with no OPENROUTER_API_KEY, which is correct for ONE renderer and
    a DOWNGRADE for two: GitHub Actions holds the key and writes the real
    paragraph, and this worker would upsert the template over it — measured
    live on 2026-08-19, four minutes after GHA wrote the good one.

    The card carries no generated text and cannot be degraded, so it still
    publishes every cycle."""
    _night(monkeypatch, NIGHT)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert pl.step_publish_cards() == 0
    assert spy == ["cards"], "the card must still publish; only the post waits"


def test_the_keyless_host_does_not_redraw_the_card_every_cycle(spy, monkeypatch):
    """The guard must not become an infinite redraw: an unchanged No.1 on a
    keyless host settles to doing nothing at all."""
    _night(monkeypatch, NIGHT)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    pl.step_publish_cards()
    spy.clear()
    pl.step_publish_cards()
    assert spy == []


def test_adding_the_key_lets_the_post_publish(spy, monkeypatch):
    """Setting OPENROUTER_API_KEY on the worker is all it takes to get the
    post same-cycle as well."""
    _night(monkeypatch, NIGHT)
    monkeypatch.setenv("OPENROUTER_API_KEY", "now-i-have-one")
    assert pl.step_publish_cards() == 0
    assert spy == ["cards", "cards-post"]


def test_the_kill_switch_stops_it_dead(spy, monkeypatch):
    """Marketing must be switchable off without a code change."""
    _night(monkeypatch, NIGHT)
    monkeypatch.setenv("PREDICTOR_PUBLISH_CARDS", "off")
    assert pl.step_publish_cards() == 0
    assert spy == []


def test_it_can_never_fail_the_cycle(spy, monkeypatch):
    """Soft-fail by contract: the step owns marketing and sits below steps
    that own money. Even a broken renderer returns 0."""
    import importlib.util
    monkeypatch.setattr(importlib.util, "spec_from_file_location",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pl.step_publish_cards() == 0


def test_the_step_runs_last_in_the_cycle():
    """ORDERING IS THE SAFETY PROPERTY. Everything above owns money, data or
    monitoring; this owns marketing and must not be able to delay them."""
    src = (REPO / "workers" / "predictor_loop.py").read_text(encoding="utf-8")
    body = src.split("def cycle()", 1)[1].split("\ndef ", 1)[0]
    assert "step_publish_cards()" in body
    for earlier in ("step_lock_commit()", "step_pregame_alert_check()",
                    "step_reconcile()", "step_discord_broadcasts()"):
        assert body.index(earlier) < body.index("step_publish_cards()"), (
            f"{earlier} must run before the card render")

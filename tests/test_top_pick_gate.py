"""T8.22 -- the №1-only alert gate must crown exactly one game a night.

THE DEFECT, as measured on the live ledger rather than reasoned about.
The №1-only policy shipped 2026-08-05. Both multi-pick slates since then
fired a "BET LOCKED" alert for EVERY pick:

    2026-08-05   TB@COL  +  WSH@PHI
    2026-08-06   WSH@PHI +  SD@ARI     <- and WSH@PHI, which pinged first,
                                          was 4 confidence points WORSE

Cause: `bet_placed="N"` is overloaded. It means DECLINED (edge gate, daily
cap, cluster demotion) and it also means T2.58's PENDING, "will commit at
its own lock". The rival scan discarded both. Games lock at their own
first-pitch-minus-60, so when one flipped to "Y" every other STRONG pick
was still "N" -- each game in turn saw an empty field and crowned itself.

The discriminator is the stake: a decline has none, a pending pick has a
positive one. That is the same test `tools/end_of_day_check.py` uses to
tell those two apart, so the two agree by construction.

The gate FAILS OPEN by design (a broken gate should spam, not silence), so
these tests check that it stays open where it should and closes where it
must -- a gate that suppressed too much would be the worse bug.
"""
import pytest

import tracker


def _pick(away, home, *, nrfi_prob, side="YRFI", odds="-120",
          bet_placed="N", units="5.0", game_pk=None, strength="STRONG",
          label=None, date="2026-08-06"):
    return {
        "date": date, "game_pk": game_pk or f"{away}{home}",
        "away_team": away, "home_team": home, "game_number": "1",
        "pick_side": side, "pick_strength": strength,
        "pick_label": label or f"{strength} {side}",
        "nrfi_prob": str(nrfi_prob),
        "market_nrfi_odds": odds if side == "NRFI" else "",
        "market_yrfi_odds": odds if side == "YRFI" else "",
        "bet_placed": bet_placed, "units_risked": units,
    }


# --- the discriminator itself -------------------------------------------

def test_a_pending_pick_is_not_a_decline():
    """T2.58 pre-lock state: "N" plus a real stake. A live play."""
    assert not tracker._is_declined_not_pending(
        _pick("SD", "ARI", nrfi_prob=0.3657, bet_placed="N", units="8.0"))


def test_an_edge_gate_refusal_is_a_decline():
    assert tracker._is_declined_not_pending(
        _pick("SD", "ARI", nrfi_prob=0.3657, bet_placed="N", units=""))


def test_a_cap_exhausted_refusal_is_a_decline():
    """T8.18 writes a numeric zero at commit rather than a blank."""
    assert tracker._is_declined_not_pending(
        _pick("SD", "ARI", nrfi_prob=0.3657, bet_placed="N", units="0.0"))


def test_a_committed_bet_is_never_a_decline():
    assert not tracker._is_declined_not_pending(
        _pick("SD", "ARI", nrfi_prob=0.3657, bet_placed="Y", units="8.0"))


def test_an_unpriced_row_is_never_a_decline():
    """Before the odds import every row is blank. Treating "unknown" as
    "no bet" would silence the whole slate."""
    assert not tracker._is_declined_not_pending(
        _pick("SD", "ARI", nrfi_prob=0.3657, bet_placed="", units=""))


def test_an_unparseable_stake_fails_open():
    """Losing a real №1 is worse than one extra ping."""
    assert not tracker._is_declined_not_pending(
        _pick("SD", "ARI", nrfi_prob=0.3657, bet_placed="N", units="oops"))


# --- the gate, over a whole slate ---------------------------------------

def _replay(night):
    """Lock the games in the given order and record which ones ping.

    Mirrors production: at each lock the earlier games are already "Y" and
    the later ones are still pending. A DECLINED row never commits and so
    never reaches the gate at all -- `_notify_strong_locked_telegram`
    requires bet_placed == "Y" before it even asks. Forcing one to "Y"
    here would test a state production cannot produce."""
    declined = [tracker._is_declined_not_pending(r) for r in night]
    for r, is_declined in zip(night, declined):
        if not is_declined:
            r["bet_placed"] = "N"        # T2.58 pending, pre-lock
    fired = []
    for r, is_declined in zip(night, declined):
        if is_declined:
            continue
        r["bet_placed"] = "Y"            # this game reaches its own lock
        if tracker._row_is_nights_top_pick(r, rivals=night):
            fired.append(f"{r['away_team']}@{r['home_team']}")
    return fired


def test_only_the_best_play_pings_when_it_locks_last():
    """THE 2026-08-06 SHAPE, which is the one that actually misfired.
    WSH@PHI locks at 6:05 and must stay quiet; SD@ARI locks at 9:40 and is
    4 points stronger, so it is the one that speaks."""
    night = [_pick("WSH", "PHI", nrfi_prob=0.4059, game_pk="823430"),
             _pick("SD", "ARI", nrfi_prob=0.3657, game_pk="825053")]
    assert _replay(night) == ["SD@ARI"]


def test_only_the_best_play_pings_when_it_locks_first():
    """THE 2026-08-05 SHAPE. TB@COL wins by 0.13 points and locks first,
    so the later, weaker WSH@PHI must be suppressed. Both orderings matter:
    a fix that only handled 'best locks last' would still misfire here."""
    night = [_pick("TB", "COL", nrfi_prob=0.2872, game_pk="824322"),
             _pick("WSH", "PHI", nrfi_prob=0.2885, game_pk="823429")]
    assert _replay(night) == ["TB@COL"]


def test_a_lone_strong_pick_still_pings():
    night = [_pick("LAD", "ARI", nrfi_prob=0.3712, game_pk="825050")]
    assert _replay(night) == ["LAD@ARI"]


def test_a_declined_rival_cannot_suppress_the_real_number_one():
    """A stood-down pick must not silence the night. The demoted row is
    the more confident of the two, so a gate that counted it as a rival
    would suppress the only real bet on the board."""
    night = [
        _pick("MIL", "LAA", nrfi_prob=0.2000, game_pk="1",
              bet_placed="N", units="",
              label="PASS - Cluster demotion: thin pitcher"),
        _pick("LAD", "ARI", nrfi_prob=0.3712, game_pk="2"),
    ]
    assert _replay(night) == ["LAD@ARI"]


def test_every_pick_pinging_is_what_this_prevents():
    """The regression itself, stated as an assertion: on a three-pick
    slate exactly one alert fires, not three."""
    night = [_pick("AAA", "BBB", nrfi_prob=0.30, game_pk="1"),
             _pick("CCC", "DDD", nrfi_prob=0.35, game_pk="2"),
             _pick("EEE", "FFF", nrfi_prob=0.40, game_pk="3")]
    fired = _replay(night)
    assert len(fired) == 1, f"expected one №1, got {fired}"
    assert fired == ["AAA@BBB"]          # lowest p(no run) = strongest YRFI


def test_the_price_breaks_a_confidence_tie():
    """Mirrors dashboard/lib/top-pick-rank.ts: equal confidence, better
    price wins. The two must not drift -- a board badge disagreeing with
    the alert about which game is №1 is the failure this shares a rule to
    avoid."""
    night = [_pick("AAA", "BBB", nrfi_prob=0.35, odds="-150", game_pk="1"),
             _pick("CCC", "DDD", nrfi_prob=0.35, odds="+110", game_pk="2")]
    assert _replay(night) == ["CCC@DDD"]

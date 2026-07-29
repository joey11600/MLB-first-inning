#!/usr/bin/env python3
"""
tools/edge_floor/wf_common.py -- shared machinery for the WALK-FORWARD
edge-floor decision test.

ANALYSIS ONLY.  Nothing here writes to the ledger, the predictor, or any
config.

THE POPULATION QUESTION -- READ THIS FIRST
------------------------------------------
The monotone edge table that prompted this study (495 bets at edge>=0)
is computed over EVERY graded 2026 game with a captured DK YRFI price --
no probability gate, no lambda floor.  That is not the population the
live rule bets.  The live rule is

    STRONG YRFI  <=>  p_nrfi < 0.40  AND  lambda >= weather-adjusted floor

which fires on 118 of those games under the shipped calibrator (96 under
a walk-forward one).  An edge floor is a filter applied ON TOP of that
rule, so every number in this study is computed on the live-rule
population.

STALE COLUMN.  edge_on_pick in the CSV is not trusted; edge is always
recomputed here as (1 - p_nrfi) - implied(market_yrfi_odds), against the
vig-inclusive implied probability -- the correct quantity for a betting
decision.
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import tracker                              # noqa: E402
import mlb_first_inning_predictor as P      # noqa: E402
from calibration import ProbCalibrator      # noqa: E402
from tools.season_replay import load_season, payout, implied   # noqa: E402
from tools.gate_validation import select, walk_forward_probs   # noqa: E402

START = 100.0
LIVE_GATE = P._LR_STRONG_YRFI_P             # 0.40


def universe(gate: float = LIVE_GATE):
    """Returns (rows, in_sample_probs, walk_forward_probs)."""
    rows, _ = load_season()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    ins = [cal.predict(r["raw"]) for r in rows]
    wf = walk_forward_probs(rows)
    return rows, ins, wf


def live_bets(rows, probs, gate: float = LIVE_GATE):
    """Bets the LIVE rule fires, real captured prices only, +recomputed edge."""
    bets = select(rows, probs, side="YRFI", gate=gate, fill=None)
    for b in bets:
        b["edge"] = b["p"] - implied(b["odds"])
    bets.sort(key=lambda b: b["date"])
    return bets


# ---------------------------------------------------------------------------
# staking -- the SHIPPED path
# ---------------------------------------------------------------------------

def kelly_day_pnl(day_bets, bank, frac=0.25, date="x"):
    """P/L in units for one slate at shipped staking, given a bankroll.

    Quarter Kelly, 10% per-bet cap, 15% same-day cap, 0.1u minimum,
    sized off the running bank.  Kelly itself already returns 0 on a
    non-positive-edge bet, which is exactly the behaviour an explicit
    floor is being compared against.
    """
    old_f, old_b = tracker.KELLY_FRACTION, tracker._bankroll_cache
    tracker.KELLY_FRACTION = frac
    tracker._bankroll_cache = bank
    tracker._daily_committed = {date: 0.0}
    pnl = 0.0
    n = w = 0
    staked = 0.0
    try:
        for b in day_bets:
            s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])),
                                          game_date=date) or 0.0
            if s <= 0:
                continue
            n += 1
            w += b["win"]
            staked += s
            pnl += s * payout(b["odds"]) if b["win"] else -s
    finally:
        tracker.KELLY_FRACTION = old_f
        tracker._bankroll_cache = old_b
    return pnl, n, w, staked


def kelly_run(day_seq, start=START, frac=0.25):
    """Compound a sequence of slates. day_seq: list[(date, [bets])]."""
    bank = peak = start
    mdd = 0.0
    n = w = 0
    staked = 0.0
    daily = []
    for i, (d, bets) in enumerate(day_seq):
        pnl, dn, dw, ds = kelly_day_pnl(bets, bank, frac, date=f"{d}#{i}")
        bank += pnl
        n += dn
        w += dw
        staked += ds
        daily.append(pnl)
        peak = max(peak, bank)
        if peak > 0:
            mdd = max(mdd, (peak - bank) / peak)
    return {"bets": n, "wins": w, "profit": bank - start, "final": bank,
            "maxdd": 100 * mdd, "staked": staked, "daily": daily,
            "days": len(day_seq)}


def flat_stats(bets):
    n = len(bets)
    if not n:
        return {"bets": 0, "wins": 0, "pl": 0.0, "roi": float("nan"),
                "hit": float("nan"), "need": float("nan")}
    w = sum(1 for b in bets if b["win"])
    pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)
    need = sum(implied(b["odds"]) for b in bets) / n
    return {"bets": n, "wins": w, "pl": pl, "roi": 100 * pl / n,
            "hit": 100 * w / n, "need": 100 * need}


def by_day(bets):
    d = defaultdict(list)
    for b in bets:
        d[b["date"]].append(b)
    return [(k, d[k]) for k in sorted(d)]


def paired_day_bootstrap(days_a, days_b, iters=2000, seed=11, frac=0.25):
    """Resample DAYS with replacement; re-run BOTH arms on the SAME
    resampled day sequence and take the delta of final profit.

    Pairing matters: the two arms share most of their bets, so an
    unpaired comparison would drown the difference in shared variance.
    Blocks are days because a slate settles together.
    """
    keys = sorted({d for d, _ in days_a} | {d for d, _ in days_b})
    A, B = dict(days_a), dict(days_b)
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        draw = [keys[rng.randrange(len(keys))] for _ in range(len(keys))]
        out.append(kelly_run([(k, B.get(k, [])) for k in draw], frac=frac)["profit"]
                   - kelly_run([(k, A.get(k, [])) for k in draw], frac=frac)["profit"])
    out.sort()
    return (out[int(0.05 * len(out))], out[int(0.50 * len(out))],
            out[int(0.95 * len(out))],
            sum(1 for v in out if v > 0) / len(out))


def flat_paired_bootstrap(days_a, days_b, iters=4000, seed=11):
    """Same pairing, flat 1u -- isolates edge from leverage."""
    keys = sorted({d for d, _ in days_a} | {d for d, _ in days_b})
    A, B = dict(days_a), dict(days_b)

    def pl(bets):
        return sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)

    dd = {k: pl(B.get(k, [])) - pl(A.get(k, [])) for k in keys}
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        out.append(sum(dd[keys[rng.randrange(len(keys))]] for _ in range(len(keys))))
    out.sort()
    return (out[int(0.05 * len(out))], out[int(0.50 * len(out))],
            out[int(0.95 * len(out))],
            sum(1 for v in out if v > 0) / len(out))

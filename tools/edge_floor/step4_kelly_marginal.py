#!/usr/bin/env python3
"""Step 4 -- THE CRUX.  Kelly already sizes by edge and already refuses a
negative-edge bet.  So the question is never "flat-bet with a floor vs
flat-bet without".  It is: how much does REFUSING the low-edge tail add
over merely STAKING IT SMALL, under the shipped staking rule?

Also: block-bootstrap over DAYS, and the walk-forward floor selection
(choose the floor from prior data only, apply blind to the next date).
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, walk_forward_probs, load_season,
    summary, implied, payout)

START = 100.0
FLOORS = (None, -0.10, -0.05, 0.00, 0.01, 0.02, 0.03, 0.04, 0.05,
          0.06, 0.08, 0.10, 0.12)


def kelly_sim(bets, frac=0.25, start=START):
    """Shipped quarter-Kelly with the live per-bet and same-day caps."""
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    staked = []
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(
                    b["p_yrfi"], str(int(b["odds"])), game_date=d) or 0.0
                if s <= 0:
                    continue
                staked.append(dict(b, stake=s))
                pnl += s * payout(b["odds"]) if b["win"] else -s
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
    finally:
        tracker.KELLY_FRACTION = o
    return bank, 100 * mdd, staked


def apply_floor(bets, f):
    return bets if f is None else [b for b in bets if b["edge"] >= f]


def block_bootstrap_roi(bets, iters=4000, seed=7):
    if not bets:
        return float("nan"), float("nan")
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        n = 0
        pl = 0.0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                n += 1
                pl += payout(b["odds"]) if b["win"] else -1.0
        if n:
            out.append(100 * pl / n)
    out.sort()
    return out[int(0.05 * len(out))], out[int(0.95 * len(out))]


def paired_bootstrap_delta(base, floored, iters=4000, seed=11):
    """Block bootstrap of (floored flat P&L - base flat P&L) on the SAME
    resampled days -- the paired difference, which is the quantity that
    matters for a ship/no-ship call."""
    byday_b = defaultdict(list)
    byday_f = defaultdict(list)
    for b in base:
        byday_b[b["date"]].append(b)
    for b in floored:
        byday_f[b["date"]].append(b)
    days = list(byday_b)
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        db = df = 0.0
        for _ in range(len(days)):
            d = rng.choice(days)
            db += sum(payout(x["odds"]) if x["win"] else -1.0 for x in byday_b[d])
            df += sum(payout(x["odds"]) if x["win"] else -1.0 for x in byday_f.get(d, []))
        out.append(df - db)
    out.sort()
    return (out[int(0.05 * len(out))], st.median(out), out[int(0.95 * len(out))],
            100.0 * sum(1 for v in out if v > 0) / len(out))


def sweep(bets, label):
    print(f"\n  {label}   base n={len(bets)}")
    print(f"  {'floor':>8}{'bets':>6}{'cut':>5}{'hit%':>7}{'flat u':>9}{'ROI':>8}"
          f"{'Kelly end':>11}{'d Kelly':>9}{'maxDD':>8}{'staked u':>10}")
    base_bank, base_dd, base_staked = kelly_sim(bets)
    base_stake_sum = sum(b["stake"] for b in base_staked)
    for f in FLOORS:
        g = apply_floor(bets, f)
        if not g:
            continue
        s = summary(g)
        bank, dd, sk = kelly_sim(g)
        tot = sum(b["stake"] for b in sk)
        lbl = "none" if f is None else f"{f:+.2f}"
        print(f"  {lbl:>8}{s['n']:>6}{len(bets)-s['n']:>5}{s['hit']:>7.1f}"
              f"{s['pl']:>+8.2f}u{s['roi']:>+7.1f}%{bank:>10.2f}u"
              f"{bank-base_bank:>+8.2f}u{dd:>7.1f}%{tot:>9.2f}u")
    return base_bank, base_staked


def kelly_zero_audit(bets):
    """How many bets does Kelly ALREADY refuse, and how small are the
    thin-edge ones it does take?  This is the effect the floor is
    competing against."""
    bank = START
    tracker._bankroll_cache = bank
    tracker._daily_committed = {}
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = 0.25
    rows = []
    try:
        for b in bets:
            tracker._bankroll_cache = START
            tracker._daily_committed = {}
            s = tracker.kelly_stake_units(b["p_yrfi"], str(int(b["odds"]))) or 0.0
            rows.append((b["edge"], s, b["win"], b["odds"]))
    finally:
        tracker.KELLY_FRACTION = o
    zero = [r for r in rows if r[1] <= 0]
    print(f"\n  KELLY'S OWN FILTER (100u bank, no daily cap, per-bet only)")
    print(f"    bets where Kelly stakes 0 (edge<=0 or below 0.10u min): "
          f"{len(zero)} / {len(rows)}")
    print(f"    {'edge band':<16}{'bets':>6}{'mean stake':>12}{'share of total':>16}")
    tot = sum(r[1] for r in rows)
    bands = [(-9, 0.0), (0.0, 0.02), (0.02, 0.04), (0.04, 0.06),
             (0.06, 0.08), (0.08, 0.12), (0.12, 9)]
    for a, bnd in bands:
        g = [r for r in rows if a <= r[0] < bnd]
        if not g:
            continue
        ms = sum(r[1] for r in g) / len(g)
        sh = 100 * sum(r[1] for r in g) / tot if tot else 0
        lo = "<0" if a < -1 else f"{a:.2f}"
        hi = "+" if bnd > 1 else f"{bnd:.2f}"
        print(f"    {lo+'..'+hi:<16}{len(g):>6}{ms:>11.2f}u{sh:>15.1f}%")


def walk_forward_floor(bets, grid, min_prior=25, objective="roi"):
    """At each date, pick the floor that maximised `objective` on bets
    settled STRICTLY BEFORE that date; apply it blind to that date."""
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    days = sorted(byday)
    kept, chosen = [], []
    for d in days:
        prior = [b for b in bets if b["date"] < d]
        if len(prior) < min_prior:
            kept.extend(byday[d])
            chosen.append((d, None))
            continue
        best, bestv = None, -1e9
        for f in grid:
            g = apply_floor(prior, f)
            if len(g) < 10:
                continue
            pl = sum(payout(x["odds"]) if x["win"] else -1.0 for x in g)
            v = pl / len(g) if objective == "roi" else pl
            if v > bestv:
                best, bestv = f, v
        chosen.append((d, best))
        kept.extend(apply_floor(byday[d], best))
    return kept, chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=4000)
    args = ap.parse_args()

    rows, _ = load_season()
    ins, _ = insample_probs(rows)
    wf = walk_forward_probs(rows)

    L_ins = build_bets(rows, ins)
    L_wf = build_bets(rows, wf)

    print("=" * 112)
    print("  THE MARGINAL EFFECT OF AN EDGE FLOOR **OVER KELLY SIZING**")
    print("  (live rule only: STRONG YRFI gate + weather lambda floor, REAL prices)")
    print("=" * 112)
    print(f"  staking: {0.25:.2f} Kelly, {tracker.KELLY_MAX_STAKE_FRAC:.0%}/bet, "
          f"{tracker.KELLY_MAX_DAILY_FRAC:.0%}/day, {START:.0f}u start")
    print(f"  floors searched: {len([f for f in FLOORS if f is not None])}")

    kelly_zero_audit(L_ins)
    sweep(L_ins, "A. IN-SAMPLE calibrator (optimistic; has seen these games)")
    sweep(L_wf, "B. WALK-FORWARD calibrator (no hindsight in the probability)")

    print("\n" + "=" * 112)
    print("  BLOCK BOOTSTRAP OVER DAYS -- flat 1u ROI, and the PAIRED delta")
    print("=" * 112)
    for label, bets in (("in-sample", L_ins), ("walk-forward", L_wf)):
        print(f"\n  {label}: base n={len(bets)} over "
              f"{len({b['date'] for b in bets})} days")
        lo, hi = block_bootstrap_roi(bets, args.iters)
        s = summary(bets)
        print(f"    {'no floor':<14} ROI {s['roi']:+6.1f}%   90% CI [{lo:+6.1f},{hi:+6.1f}]")
        for f in (0.02, 0.04, 0.06, 0.08):
            g = apply_floor(bets, f)
            if len(g) < 10:
                continue
            sg = summary(g)
            lo2, hi2 = block_bootstrap_roi(g, args.iters)
            dlo, dmed, dhi, pwin = paired_bootstrap_delta(bets, g, args.iters)
            print(f"    floor {f:+.2f}     ROI {sg['roi']:+6.1f}%   "
                  f"90% CI [{lo2:+6.1f},{hi2:+6.1f}]   "
                  f"delta flat u vs no-floor: med {dmed:+6.2f}u "
                  f"CI [{dlo:+6.2f},{dhi:+6.2f}]  P(delta>0)={pwin:.0f}%")

    print("\n" + "=" * 112)
    print("  WALK-FORWARD FLOOR SELECTION -- the deciding test")
    print("  (floor chosen at each date from settled bets BEFORE it, applied blind)")
    print("=" * 112)
    grid = [f for f in FLOORS if f is not None]
    for label, bets in (("in-sample probs", L_ins), ("walk-forward probs", L_wf)):
        for obj in ("roi", "profit"):
            kept, chosen = walk_forward_floor(bets, grid, objective=obj)
            s0, s1 = summary(bets), summary(kept)
            b0, _, _ = kelly_sim(bets)
            b1, _, _ = kelly_sim(kept)
            picks = [c for _, c in chosen if c is not None]
            from collections import Counter
            cc = Counter(picks)
            print(f"\n  {label}, objective={obj}")
            print(f"    no floor        : n={s0['n']:>4} hit {s0['hit']:.1f}% "
                  f"flat {s0['pl']:+.2f}u ROI {s0['roi']:+.1f}%  Kelly {b0:.2f}u")
            print(f"    WF-chosen floor : n={s1['n']:>4} hit {s1['hit']:.1f}% "
                  f"flat {s1['pl']:+.2f}u ROI {s1['roi']:+.1f}%  Kelly {b1:.2f}u")
            print(f"    delta           : flat {s1['pl']-s0['pl']:+.2f}u   "
                  f"Kelly {b1-b0:+.2f}u   ({s0['n']-s1['n']} bets skipped)")
            print(f"    floors it chose : "
                  f"{', '.join(f'{k:+.2f}x{v}' for k, v in sorted(cc.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
tools/edge_floor/crux.py -- ANALYSIS ONLY.  Does an explicit EDGE FLOOR add
anything OVER what quarter-Kelly staking already does on the live YRFI rule?

The proposal's in-sample table sweeps edge over ALL real-priced 2026 games.
The live rule does not bet all of them: it bets p_nrfi < 0.40 with a
weather-adjusted lambda floor, and then stakes by quarter Kelly, which
already (a) refuses any bet whose model probability does not beat the
price and (b) scales the stake WITH edge.  So the floor's only possible
contribution is removing the low-edge TAIL that Kelly is already staking
small.  Everything below is measured against that incumbent, never
against flat betting.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402

START = 100.0
FLOORS = [0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12]


def add_edge(bets):
    for b in bets:
        b["edge"] = b["p"] - implied(b["odds"])
    return bets


def apply_floor(bets, floor):
    return [b for b in bets if b["edge"] >= floor]


def simulate(bets, frac=0.25, start=START):
    """Day-by-day compounding with the SHIPPED Kelly helper + live caps."""
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    curve = []
    staked = []
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])), game_date=d) or 0.0
                if s <= 0:
                    continue
                rec = dict(b)
                rec["stake"] = s
                rec["pnl"] = s * payout(b["odds"]) if b["win"] else -s
                staked.append(rec)
                pnl += rec["pnl"]
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
            curve.append((d, bank))
    finally:
        tracker.KELLY_FRACTION = o
    return {"bank": bank, "profit": bank - start, "mdd": 100 * mdd,
            "curve": curve, "staked": staked}


def summary(res):
    s = res["staked"]
    n = len(s)
    w = sum(1 for b in s if b["win"])
    flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in s)
    tot_stake = sum(b["stake"] for b in s)
    return {"n": n, "w": w, "l": n - w, "hit": 100 * w / n if n else float("nan"),
            "flat": flat, "staked_u": tot_stake, "profit": res["profit"],
            "mdd": res["mdd"], "bank": res["bank"],
            "need": 100 * st.mean([implied(b["odds"]) for b in s]) if n else float("nan")}


def block_bootstrap_delta(base_bets, floor, iters=4000, seed=7):
    """Resample DAYS with replacement; report the flat-1u P&L DELTA
    (floor minus incumbent) per resample.  Flat, not Kelly: Kelly is
    path-dependent and cannot be resampled coherently."""
    byday = defaultdict(list)
    for b in base_bets:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        d0 = d1 = 0.0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                v = payout(b["odds"]) if b["win"] else -1.0
                d0 += v
                if b["edge"] >= floor:
                    d1 += v
        out.append(d1 - d0)
    out.sort()
    return out[int(0.05 * len(out))], out[int(0.5 * len(out))], out[int(0.95 * len(out))]


def main():
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    gate = P._LR_STRONG_YRFI_P

    # REAL captured prices only -- profit claims require real prices.
    bets = add_edge(select(rows, wf, side="YRFI", gate=gate, fill=None))
    bets.sort(key=lambda b: b["date"])

    print("=" * 104)
    print("  EDGE FLOOR vs KELLY -- what does the floor add over the sizing we already run?")
    print("=" * 104)
    print(f"  rule            STRONG YRFI, p_nrfi < {gate}, weather-adjusted lambda floor")
    print(f"  calibrator      WALK-FORWARD (refit from strictly prior games)")
    print(f"  staking         {0.25:.2f} Kelly, {tracker.KELLY_MAX_STAKE_FRAC:.0%}/bet, "
          f"{tracker.KELLY_MAX_DAILY_FRAC:.0%}/day, {START:.0f}u start, min stake "
          f"{tracker.KELLY_MIN_STAKE_UNITS:.2f}u")
    print(f"  prices          REAL captured DK YRFI only (no fills)")
    print(f"  graded 2026 games              {len(rows)}")
    print(f"  ... with a real DK YRFI price  {sum(1 for r in rows if r['yrfi_odds'] is not None)}")
    print(f"  ... the gate would BET         {len(bets)}   over "
          f"{len({b['date'] for b in bets})} days")

    # ---- how many does Kelly already refuse? -----------------------------
    neg = [b for b in bets if b["edge"] <= 0]
    print()
    print("  KELLY'S EXISTING EDGE FILTER")
    print(f"    of those {len(bets)} gated bets, {len(neg)} have edge <= 0 and are")
    print(f"    ALREADY skipped by kelly_stake_units (stake 0).  An edge floor at")
    print(f"    0.00 is therefore a NO-OP against the shipped system.")

    base = simulate(bets)
    b0 = summary(base)
    print()
    print("  INCUMBENT (no edge floor)")
    print(f"    bets staked   {b0['n']}   {b0['w']}W-{b0['l']}L  ({b0['hit']:.1f}%)  "
          f"need {b0['need']:.1f}%")
    print(f"    flat 1u       {b0['flat']:+.2f}u")
    print(f"    total staked  {b0['staked_u']:.2f}u")
    print(f"    Kelly bank    {START:.0f}u -> {b0['bank']:.2f}u  =  {b0['profit']:+.2f}u  "
          f"({100*(b0['bank']/START-1):+.1f}%)")
    print(f"    max drawdown  {b0['mdd']:.1f}%")

    # ---- 2. floors -------------------------------------------------------
    print()
    print("=" * 104)
    print("  2/3.  ADDING AN EDGE FLOOR -- delta vs the INCUMBENT (same staking, same caps)")
    print("=" * 104)
    print(f"  {'floor':>6}{'staked':>8}{'W':>4}{'L':>4}{'hit%':>7}{'flat':>9}"
          f"{'bank':>10}{'d bank':>9}{'d %bank':>9}{'maxDD':>7}{'staked u':>10}")
    results = {}
    for f in FLOORS:
        r = simulate(apply_floor(bets, f))
        s = summary(r)
        results[f] = (r, s)
        d = s["profit"] - b0["profit"]
        print(f"  {f:>6.2f}{s['n']:>8}{s['w']:>4}{s['l']:>4}{s['hit']:>7.1f}"
              f"{s['flat']:>+8.2f}u{s['bank']:>9.2f}u{d:>+8.2f}u"
              f"{100*d/START:>+8.1f}%{s['mdd']:>6.1f}%{s['staked_u']:>9.2f}u")
    print()
    print("  'd bank' is units of a 100u bank, so 'd %bank' is the same number as a")
    print("  percentage of the starting bankroll.")

    # ---- 4/5. decomposition ---------------------------------------------
    print()
    print("=" * 104)
    print("  4/5.  WHAT DOES EACH FLOOR ACTUALLY REMOVE?  (bets as the INCUMBENT staked them)")
    print("=" * 104)
    inc = base["staked"]
    inc_total_stake = sum(b["stake"] for b in inc)
    print(f"  {'floor':>6}{'removed':>9}{'W':>4}{'L':>4}{'stake u':>10}{'% of all':>10}"
          f"{'mean stk':>10}{'their P&L':>11}{'kept P&L':>10}")
    for f in FLOORS[1:]:
        rem = [b for b in inc if b["edge"] < f]
        kept = [b for b in inc if b["edge"] >= f]
        if not rem:
            continue
        rs = sum(b["stake"] for b in rem)
        rp = sum(b["pnl"] for b in rem)
        kp = sum(b["pnl"] for b in kept)
        rw = sum(1 for b in rem if b["win"])
        print(f"  {f:>6.2f}{len(rem):>9}{rw:>4}{len(rem)-rw:>4}{rs:>9.2f}u"
              f"{100*rs/inc_total_stake:>9.1f}%{rs/len(rem):>9.2f}u"
              f"{rp:>+10.2f}u{kp:>+9.2f}u")
    print()
    print("  'their P&L' is the money the incumbent actually made or lost on exactly")
    print("  the bets the floor deletes.  THAT is the value of the floor, before any")
    print("  compounding knock-on.  A floor whose removed bets sum near zero is")
    print("  cosmetic: it raises ROI by shrinking the denominator, not by earning.")

    # ---- stake distribution by edge band ---------------------------------
    print()
    print("  STAKE PROFILE BY EDGE BAND (incumbent) -- is Kelly already sizing these down?")
    print(f"  {'band':<14}{'n':>4}{'W':>4}{'L':>4}{'mean stake':>12}{'total stake':>13}"
          f"{'P&L':>10}{'% of stake':>12}")
    bands = [(-9, 0.02), (0.02, 0.04), (0.04, 0.06), (0.06, 0.08),
             (0.08, 0.12), (0.12, 9)]
    for lo, hi in bands:
        g = [b for b in inc if lo <= b["edge"] < hi]
        if not g:
            continue
        ts = sum(b["stake"] for b in g)
        lbl = f"{max(lo,0):.2f}-{min(hi,1):.2f}" if hi < 9 else f">={lo:.2f}"
        if lo < 0:
            lbl = f"<{hi:.2f}"
        gw = sum(1 for b in g if b["win"])
        print(f"  {lbl:<14}{len(g):>4}{gw:>4}{len(g)-gw:>4}{ts/len(g):>11.2f}u"
              f"{ts:>12.2f}u{sum(b['pnl'] for b in g):>+9.2f}u"
              f"{100*ts/inc_total_stake:>11.1f}%")

    # ---- bootstrap on the delta -----------------------------------------
    print()
    print("=" * 104)
    print("  BLOCK BOOTSTRAP OVER DAYS -- flat-1u P&L delta (floor minus incumbent), 4000 iters")
    print("=" * 104)
    print(f"  {'floor':>6}{'5th':>10}{'median':>10}{'95th':>10}   verdict")
    pos = [b for b in bets if b["edge"] > 0]      # what Kelly actually plays
    for f in FLOORS[1:]:
        lo, mid, hi = block_bootstrap_delta(pos, f)
        v = "delta CI excludes 0" if lo > 0 else "CI spans 0 -- indistinguishable from noise"
        print(f"  {f:>6.2f}{lo:>+9.2f}u{mid:>+9.2f}u{hi:>+9.2f}u   {v}")

    print()
    print(f"  thresholds searched in this sweep: {len(FLOORS)-1} "
          f"({', '.join(f'{f:.2f}' for f in FLOORS[1:])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

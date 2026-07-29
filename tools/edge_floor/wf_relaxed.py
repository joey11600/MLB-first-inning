#!/usr/bin/env python3
"""
tools/edge_floor/wf_relaxed.py -- the walk-forward floor test, run at a
RELAXED minimum history so it produces a number at all.

WHY THIS FILE EXISTS.  wf_decide.py asks for 100 prior settled live-rule
bets before the selector is allowed to choose a floor.  The live rule
only produced 96 priced bets in the whole 2026 season, so the selector
never becomes decidable and the test returns nothing.  That is a real
finding, but "no answer" is unsatisfying, so this file re-runs the same
procedure at 30 / 40 / 50 prior bets and reports the result at each --
explicitly noting that a selector allowed to act on 30 bets is choosing
between 9 candidates on a sample where the standard error of ROI is
roughly +/-18 percentage points.

It also separates two things the headline Kelly number conflates:
  * LEVERAGE     -- Kelly stakes more on higher edge (static bankroll)
  * COMPOUNDING  -- an early swing re-levers everything after it
A floor that "makes +22u of Kelly" while cutting 2% of staked volume is
almost entirely the second, and the second is path luck.

Usage:  python tools/edge_floor/wf_relaxed.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tracker  # noqa: E402
from tools.edge_floor.wf_common import (   # noqa: E402
    START, LIVE_GATE, universe, live_bets, kelly_run, flat_stats, by_day,
    paired_day_bootstrap, flat_paired_bootstrap, payout)

GRID = [0.00, 0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.10, 0.12]


def hdr(t):
    print("\n" + "=" * 100)
    print("  " + t)
    print("=" * 100)


def arm(bets, f):
    return [b for b in bets if b["edge"] >= f]


# --------------------------------------------------------------------------
# static-bankroll Kelly: leverage WITHOUT compounding
# --------------------------------------------------------------------------

def static_kelly(bets, frac=0.25, bank=START):
    old_f, old_b = tracker.KELLY_FRACTION, tracker._bankroll_cache
    tracker.KELLY_FRACTION = frac
    tracker._bankroll_cache = bank
    pnl = staked = 0.0
    try:
        for i, b in enumerate(bets):
            tracker._daily_committed = {f"s{i}": 0.0}
            s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])),
                                          game_date=f"s{i}") or 0.0
            if s <= 0:
                continue
            staked += s
            pnl += s * payout(b["odds"]) if b["win"] else -s
    finally:
        tracker.KELLY_FRACTION = old_f
        tracker._bankroll_cache = old_b
    return pnl, staked


def choose(prior, grid, min_sub, metric):
    best, bv = 0.00, None
    for f in grid:
        sub = [b for b in prior if b["edge"] >= f]
        if len(sub) < min_sub:
            continue
        st = flat_stats(sub)
        v = st["roi"] if metric == "roi" else st["pl"]
        if bv is None or v > bv + 1e-9:
            best, bv = f, v
    return best


def walk_forward(bets, min_hist, min_sub, metric, grid=GRID):
    days = by_day(bets)
    chosen, seen = {}, []
    for d, dd in days:
        chosen[d] = choose(seen, grid, min_sub, metric) if len(seen) >= min_hist else None
        seen.extend(dd)
    live = [d for d, _ in days if chosen[d] is not None]
    if not live:
        return None
    keep = set(live)
    a = [(d, dd) for d, dd in days if d in keep]
    b = [(d, arm(dd, chosen[d])) for d, dd in days if d in keep]
    return a, b, chosen, live


def report_wf(bets, min_hist, min_sub, metric):
    r = walk_forward(bets, min_hist, min_sub, metric)
    if r is None:
        print(f"  min_hist={min_hist:<3} min_sub={min_sub:<3} metric={metric:<6}"
              "  NEVER DECIDABLE")
        return None
    a, b, chosen, live = r
    A, B = kelly_run(a), kelly_run(b)
    fa = flat_stats([x for _, dd in a for x in dd])
    fb = flat_stats([x for _, dd in b for x in dd])
    vals = [chosen[d] for d in live]
    flips = sum(1 for i in range(1, len(vals)) if vals[i] != vals[i - 1])
    tally = defaultdict(int)
    for v in vals:
        tally[v] += 1
    modal = max(tally.items(), key=lambda kv: kv[1])
    print(f"\n  --- min_hist={min_hist}  min_sub={min_sub}  metric={metric} ---")
    print(f"  window: {live[0]} .. {live[-1]}   ({len(live)} betting days scored)")
    print(f"  {'arm':<24}{'bets':>6}{'hit%':>7}{'flat u':>9}{'ROI':>8}{'Kelly u':>10}{'maxDD':>8}")
    print(f"  {'incumbent (no floor)':<24}{fa['bets']:>6}{fa['hit']:>7.1f}"
          f"{fa['pl']:>+8.2f}u{fa['roi']:>+7.1f}%{A['profit']:>+9.2f}u{A['maxdd']:>7.1f}%")
    print(f"  {'walk-forward floor':<24}{fb['bets']:>6}{fb['hit']:>7.1f}"
          f"{fb['pl']:>+8.2f}u{fb['roi']:>+7.1f}%{B['profit']:>+9.2f}u{B['maxdd']:>7.1f}%")
    print(f"  {'DELTA':<24}{fb['bets']-fa['bets']:>6}{'':>7}{fb['pl']-fa['pl']:>+8.2f}u"
          f"{'':>8}{B['profit']-A['profit']:>+9.2f}u")
    print(f"  floor changed {flips}x over {len(vals)} days "
          f"({100*flips/max(len(vals)-1,1):.0f}% of transitions); "
          f"modal choice {modal[0]:.2f} on {100*modal[1]/len(vals):.0f}% of days")
    print("  distribution: " + "  ".join(f"{k:.2f}x{v}" for k, v in sorted(tally.items())))
    print("  trace: " + " ".join(f"{v:.2f}" for v in vals))
    lo, md, hi, pw = flat_paired_bootstrap(a, b)
    print(f"  bootstrap FLAT  delta median {md:+.2f}u  90% CI [{lo:+.2f}, {hi:+.2f}]"
          f"  P(better)={100*pw:.0f}%")
    lo, md, hi, pw = paired_day_bootstrap(a, b, iters=1500)
    print(f"  bootstrap KELLY delta median {md:+.2f}u  90% CI [{lo:+.2f}, {hi:+.2f}]"
          f"  P(better)={100*pw:.0f}%")
    return B["profit"] - A["profit"]


def main():
    rows, ins, wf = universe()
    bets = live_bets(rows, wf)
    ib = live_bets(rows, ins)

    hdr("A.  LEVERAGE vs COMPOUNDING -- what the Kelly headline is made of")
    print("  static bank = Kelly sized off a FIXED 100u (leverage only, no path).")
    print("  compounding = the shipped run (leverage + path).")
    print(f"  {'floor':>7}{'bets':>6}{'flat u':>9}{'static-bank u':>15}{'compounding u':>15}"
          f"{'d flat':>9}{'d static':>10}{'d compound':>12}")
    b0 = flat_stats(bets)
    s0, _ = static_kelly(bets)
    c0 = kelly_run(by_day(bets))["profit"]
    for f in GRID:
        k = arm(bets, f)
        st = flat_stats(k)
        s, _ = static_kelly(k)
        c = kelly_run(by_day(k))["profit"]
        print(f"  {f:>7.2f}{st['bets']:>6}{st['pl']:>+8.2f}u{s:>+14.2f}u{c:>+14.2f}u"
              f"{st['pl']-b0['pl']:>+8.2f}u{s-s0:>+9.2f}u{c-c0:>+11.2f}u")
    print("\n  READ: if 'd compound' is large while 'd flat' and 'd static' are small,")
    print("  the apparent gain is the bankroll path re-levering a couple of early")
    print("  results, not a better bet-selection rule.")

    hdr("B.  WALK-FORWARD FLOOR SELECTION at relaxed minimum history")
    print(f"  only {len(bets)} priced live-rule bets exist for the whole 2026 season,")
    print("  so the 100-bet minimum never triggers. Relaxing it below ~50 means the")
    print("  selector is choosing among 9 candidates on a sample whose ROI standard")
    print("  error is roughly +/-15-20 percentage points. Read accordingly.")
    for mh, ms in ((50, 20), (40, 15), (30, 12)):
        for metric in ("roi", "pl"):
            report_wf(bets, mh, ms, metric)

    hdr("C.  SAME TEST on the IN-SAMPLE calibrator (more bets, optimistic probs)")
    print(f"  n={len(ib)} bets. Shown only to check the walk-forward verdict is not")
    print("  an artefact of the smaller walk-forward sample.")
    for mh, ms in ((60, 25), (40, 15)):
        report_wf(ib, mh, ms, "roi")

    hdr("D.  SEARCH WIDTH -- how many thresholds were looked at")
    print(f"  candidate floors in the grid            : {len(GRID)}  {GRID}")
    print("  selector metrics tried                  : 2  (trailing flat ROI, trailing flat units)")
    print("  minimum-history settings tried          : 4  (100, 50, 40, 30)")
    print("  calibrators                             : 2  (walk-forward, in-sample)")
    print("  => 9 x 2 x 4 x 2 = 144 configurations were evaluated in this study.")
    print("  A winner surviving 1 of 144 is what a null result looks like.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

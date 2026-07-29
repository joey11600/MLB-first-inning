#!/usr/bin/env python3
"""
tools/edge_floor/wf_decide.py -- THE DECIDING TEST for the proposed
edge floor on the live STRONG YRFI rule.

QUESTION.  Today a bet fires on a probability gate (p_nrfi < 0.40) plus a
weather-adjusted lambda floor.  Should an EDGE floor be added on top?

WHY THE NAIVE SWEEP OVERSTATES THE CASE.  tracker.kelly_stake_units
already returns 0 when the model probability does not beat the price,
and stakes PROPORTIONALLY to edge.  A thin-edge bet is therefore already
taken at a small stake.  An explicit floor is not "start filtering by
edge" -- it is "stop taking the low-edge tail at all rather than taking
it small".  Every headline number here is measured OVER KELLY SIZING.
The flat-1u column is shown only to isolate edge from leverage.

SECTIONS
  0  population -- the 495-bet table is not the live rule's population
  1  what a floor actually cuts, flat vs Kelly
  2  WALK-FORWARD: floor chosen from prior settled bets only, applied blind
  3  fixed floor on H1 applied blind to H2
  4  paired day-block bootstrap on the walk-forward delta
  5  is "edge" doing anything a probability gate would not?

Usage:  python tools/edge_floor/wf_decide.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.edge_floor.wf_common import (   # noqa: E402
    ROOT, START, LIVE_GATE, universe, live_bets, kelly_run, flat_stats,
    by_day, paired_day_bootstrap, flat_paired_bootstrap, implied, payout, P)

# The candidate grid the walk-forward selector may choose from.
# 9 candidates INCLUDING "no floor" -- counted and reported as the
# search width, because a selector that picks the best of 9 on a short
# history is itself a source of overfitting.
GRID = [0.00, 0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.10, 0.12]

MIN_HIST = 100      # prior settled live-rule bets before the selector may act
MIN_SUB = 25        # prior bets that must survive a candidate for it to be eligible


def hdr(t):
    print("\n" + "=" * 100)
    print("  " + t)
    print("=" * 100)


def arm(bets, floor):
    return [b for b in bets if b["edge"] >= floor]


# ---------------------------------------------------------------------------

def section0(rows, ins, wf):
    hdr("0.  POPULATION -- the table that prompted this is not the live rule")
    priced = [(r, p) for r, p in zip(rows, ins) if r["yrfi_odds"] is not None]
    print(f"  graded 2026 games with a REAL captured DK YRFI price : {len(priced)}")
    print("\n  (a) EVERY priced game, no gate, no lambda floor -- the operator's table")
    print(f"  {'edge>=':>8}{'bets':>7}{'hit%':>8}{'need%':>8}{'ROI':>9}")
    for f in (0.00, 0.04, 0.08, 0.12, 0.16):
        s = [{"odds": r["yrfi_odds"], "win": r["yrfi_hit"]}
             for r, p in priced if (1 - p) - implied(r["yrfi_odds"]) >= f]
        st = flat_stats(s)
        print(f"  {f:>8.2f}{st['bets']:>7}{st['hit']:>8.1f}{st['need']:>8.1f}{st['roi']:>+8.1f}%")

    lb_ins = live_bets(rows, ins)
    lb_wf = live_bets(rows, wf)
    print(f"\n  (b) the LIVE RULE (p_nrfi < {LIVE_GATE} + lambda floor), real prices")
    print(f"      in-sample calibrator : {len(lb_ins):>4} bets"
          f"   walk-forward calibrator : {len(lb_wf):>4} bets")
    es = sorted(b["edge"] for b in lb_wf)
    q = lambda k: es[int(k * (len(es) - 1))]
    print(f"      edge distribution of live-rule bets (walk-forward): "
          f"min {es[0]:+.3f}  p10 {q(.1):+.3f}  median {q(.5):+.3f}  "
          f"p90 {q(.9):+.3f}  max {es[-1]:+.3f}")
    print(f"      share of live-rule bets already above edge 0.04 : "
          f"{100*sum(1 for e in es if e >= 0.04)/len(es):.0f}%")
    print("\n  READ: the probability gate ALREADY selects a high-edge population.")
    print("  A floor can only bite on the thin tail that survives the gate.")
    return lb_ins, lb_wf


def section1(bets, label):
    hdr(f"1.  WHAT A FLOOR ACTUALLY CUTS -- {label}  (n={len(bets)} live-rule bets)")
    base_days = by_day(bets)
    base_k = kelly_run(base_days)
    base_f = flat_stats(bets)
    print(f"  {'floor':>7}{'kept':>6}{'cut':>5}{'hit%':>7}{'flat u':>9}{'ROI':>8}"
          f"{'Kelly u':>10}{'d Kelly':>9}{'d flat':>8}   what the cut bets did")
    for f in GRID:
        keep = arm(bets, f)
        cut = [b for b in bets if b["edge"] < f]
        if not keep:
            continue
        k = kelly_run(by_day(keep))
        st = flat_stats(keep)
        cs = flat_stats(cut) if cut else None
        ctxt = ("--" if not cut else
                f"{cs['bets']:>3} bets {cs['hit']:>5.1f}% hit  {cs['pl']:+6.2f}u flat")
        print(f"  {f:>7.2f}{st['bets']:>6}{len(cut):>5}{st['hit']:>7.1f}"
              f"{st['pl']:>+8.2f}u{st['roi']:>+7.1f}%{k['profit']:>+9.2f}u"
              f"{k['profit']-base_k['profit']:>+8.2f}u{st['pl']-base_f['pl']:>+7.2f}u   {ctxt}")
    print(f"\n  incumbent (no floor): {base_f['bets']} bets, {base_f['hit']:.1f}% hit, "
          f"{base_f['pl']:+.2f}u flat, {base_k['profit']:+.2f}u Kelly "
          f"(bank {START:.0f} -> {base_k['final']:.2f})")
    # how much of the bankroll the cut tail even represents
    print("\n  Kelly stake actually placed on the tail a floor would remove:")
    for f in (0.02, 0.04, 0.08):
        cut = [b for b in bets if b["edge"] < f]
        if not cut:
            continue
        tot = kelly_run(by_day(bets))["staked"]
        # approximate: stake share of the cut bets in the incumbent run
        cutk = kelly_run(by_day(cut))["staked"]
        print(f"    floor {f:.2f}: {len(cut):>3} of {len(bets)} bets "
              f"({100*len(cut)/len(bets):>4.0f}% of tickets) but only ~"
              f"{100*cutk/tot:>4.0f}% of staked volume -- Kelly already shrinks them")
    return base_k


# ---------------------------------------------------------------------------

def choose_floor(prior, metric="roi"):
    """Pick the best trailing floor from GRID using ONLY `prior` bets.

    Ties and ineligible candidates fall back to 0.00 (no floor), which is
    the incumbent -- so the selector must earn its way off the default.
    """
    best, best_v = 0.00, None
    for f in GRID:
        sub = [b for b in prior if b["edge"] >= f]
        if len(sub) < MIN_SUB:
            continue
        st = flat_stats(sub)
        v = st["roi"] if metric == "roi" else st["pl"]
        if best_v is None or v > best_v + 1e-9:
            best, best_v = f, v
    return best


def section2(bets, metric="roi"):
    hdr(f"2.  WALK-FORWARD FLOOR SELECTION  (metric={metric}, "
        f"min history {MIN_HIST} bets, {len(GRID)} candidates searched)")
    days = by_day(bets)
    chosen = {}
    seen = []
    for d, dayb in days:
        prior = [b for b in seen]
        chosen[d] = choose_floor(prior, metric) if len(prior) >= MIN_HIST else None
        seen.extend(dayb)

    live_days = [d for d, _ in days if chosen[d] is not None]
    if not live_days:
        print(f"  NEVER DECIDABLE: only {len(bets)} live-rule bets exist all season,")
        print(f"  fewer than the {MIN_HIST}-bet minimum history the selector needs")
        print("  before it may choose a floor. There is no walk-forward test to run")
        print("  at this history requirement -- that is itself the answer.")
        return None, chosen, live_days

    first = live_days[0]
    print(f"  selector became decidable on {first} "
          f"({len(live_days)} of {len(days)} betting days scored)")
    seq = [(d, dd) for d, dd in days if d in set(live_days)]
    a_days = [(d, dd) for d, dd in seq]                       # incumbent
    b_days = [(d, arm(dd, chosen[d])) for d, dd in seq]       # floor arm
    A, B = kelly_run(a_days), kelly_run(b_days)
    fa, fb = flat_stats([b for _, dd in a_days for b in dd]), \
             flat_stats([b for _, dd in b_days for b in dd])
    print(f"\n  {'arm':<26}{'bets':>6}{'hit%':>7}{'flat u':>9}{'ROI':>8}{'Kelly u':>10}{'maxDD':>8}")
    print(f"  {'incumbent (no floor)':<26}{fa['bets']:>6}{fa['hit']:>7.1f}"
          f"{fa['pl']:>+8.2f}u{fa['roi']:>+7.1f}%{A['profit']:>+9.2f}u{A['maxdd']:>7.1f}%")
    print(f"  {'walk-forward floor':<26}{fb['bets']:>6}{fb['hit']:>7.1f}"
          f"{fb['pl']:>+8.2f}u{fb['roi']:>+7.1f}%{B['profit']:>+9.2f}u{B['maxdd']:>7.1f}%")
    print(f"  {'DELTA':<26}{fb['bets']-fa['bets']:>6}{'':>7}"
          f"{fb['pl']-fa['pl']:>+8.2f}u{'':>8}{B['profit']-A['profit']:>+9.2f}u")

    vals = [chosen[d] for d in live_days]
    flips = sum(1 for i in range(1, len(vals)) if vals[i] != vals[i - 1])
    tally = defaultdict(int)
    for v in vals:
        tally[v] += 1
    print(f"\n  floor chosen on {len(vals)} days; it CHANGED {flips} times "
          f"({100*flips/max(len(vals)-1,1):.0f}% of day-to-day transitions)")
    print("  distribution of the chosen floor: " +
          "  ".join(f"{k:.2f}x{v}" for k, v in sorted(tally.items())))
    print("  chronological trace: " + " ".join(f"{v:.2f}" for v in vals))
    return (a_days, b_days), chosen, live_days


def section3(bets):
    hdr("3.  SIMPLEST HONEST VERSION -- fix a floor on H1, apply blind to H2")
    days = [d for d, _ in by_day(bets)]
    mid = days[len(days) // 2]
    h1 = [b for b in bets if b["date"] < mid]
    h2 = [b for b in bets if b["date"] >= mid]
    print(f"  split at {mid}:  H1 {len(h1)} bets / {len({b['date'] for b in h1})} days"
          f"   H2 {len(h2)} bets / {len({b['date'] for b in h2})} days")
    print(f"\n  H1 (the fitting half) -- {len(GRID)} floors searched")
    print(f"  {'floor':>7}{'bets':>6}{'hit%':>7}{'flat u':>9}{'ROI':>8}{'Kelly u':>10}")
    best, bv = 0.0, None
    for f in GRID:
        k = arm(h1, f)
        if len(k) < MIN_SUB:
            continue
        st = flat_stats(k)
        kk = kelly_run(by_day(k))
        star = ""
        if bv is None or st["roi"] > bv:
            best, bv, star = f, st["roi"], ""
        print(f"  {f:>7.2f}{st['bets']:>6}{st['hit']:>7.1f}{st['pl']:>+8.2f}u"
              f"{st['roi']:>+7.1f}%{kk['profit']:>+9.2f}u{star}")
    print(f"\n  --> H1 picks floor {best:.2f} (best trailing ROI)")
    print(f"\n  H2 (blind) -- incumbent vs the H1-chosen floor")
    print(f"  {'arm':<26}{'bets':>6}{'hit%':>7}{'flat u':>9}{'ROI':>8}{'Kelly u':>10}")
    for lbl, f in (("incumbent (no floor)", 0.0), (f"H1 floor {best:.2f}", best)):
        k = arm(h2, f)
        st = flat_stats(k)
        kk = kelly_run(by_day(k))
        print(f"  {lbl:<26}{st['bets']:>6}{st['hit']:>7.1f}{st['pl']:>+8.2f}u"
              f"{st['roi']:>+7.1f}%{kk['profit']:>+9.2f}u")
    a = kelly_run(by_day(h2))["profit"]
    b = kelly_run(by_day(arm(h2, best)))["profit"]
    print(f"\n  H2 DELTA from the blind floor: {b-a:+.2f}u Kelly")
    return best, mid


def section4(pair):
    hdr("4.  PAIRED DAY-BLOCK BOOTSTRAP on the walk-forward delta")
    if pair is None:
        print("  not runnable -- no walk-forward window existed (see section 2)")
        return
    a_days, b_days = pair
    lo, md, hi, pw = flat_paired_bootstrap(a_days, b_days)
    print(f"  FLAT 1u delta   median {md:+.2f}u   90% CI [{lo:+.2f}, {hi:+.2f}]"
          f"   P(floor better) = {100*pw:.0f}%")
    lo, md, hi, pw = paired_day_bootstrap(a_days, b_days)
    print(f"  KELLY   delta   median {md:+.2f}u   90% CI [{lo:+.2f}, {hi:+.2f}]"
          f"   P(floor better) = {100*pw:.0f}%")
    print("\n  Days are the block because a slate settles together, and the two")
    print("  arms are resampled on the SAME day draw so the shared bets cancel.")
    print("  A CI spanning zero means the data cannot tell the two arms apart.")


def section5(bets):
    hdr("5.  IS 'EDGE' DOING ANYTHING A PROBABILITY GATE WOULD NOT?")
    print("  Edge = p_model - implied(price). Two ingredients. If the monotone")
    print("  curve is driven by p_model alone, the 'edge floor' is a relabelled")
    print("  probability gate -- and a probability gate is already what we have.")
    print("\n  Matched-count comparison: for each edge floor, take the SAME number")
    print("  of bets ranked by p_model alone, and compare.")
    print(f"\n  {'floor':>7}{'n':>5}   {'by EDGE  hit%/ROI':>22}   {'by p_model  hit%/ROI':>22}")
    byp = sorted(bets, key=lambda b: -b["p"])
    for f in GRID[1:]:
        keep = arm(bets, f)
        n = len(keep)
        if n < 10:
            continue
        alt = byp[:n]
        se, sp = flat_stats(keep), flat_stats(alt)
        print(f"  {f:>7.2f}{n:>5}   {se['hit']:>10.1f}% {se['roi']:>+9.1f}%   "
              f"{sp['hit']:>10.1f}% {sp['roi']:>+9.1f}%")
    # correlation between edge and p among the live-rule bets
    import statistics as stt
    xs = [b["edge"] for b in bets]
    ys = [b["p"] for b in bets]
    mx, my = stt.mean(xs), stt.mean(ys)
    num = sum((a - mx) * (c - my) for a, c in zip(xs, ys))
    den = (sum((a - mx) ** 2 for a in xs) * sum((c - my) ** 2 for c in ys)) ** 0.5
    print(f"\n  corr(edge, p_model) among live-rule bets = {num/den:+.2f}")
    prices = sorted({int(b["odds"]) for b in bets})
    print(f"  distinct DK prices in the sample: {len(prices)} "
          f"(range {prices[0]} .. {prices[-1]}); "
          f"implied spread {100*implied(prices[0]):.1f}% .. {100*implied(prices[-1]):.1f}%")


def main():
    rows, ins, wf = universe()
    lb_ins, lb_wf = section0(rows, ins, wf)
    base = section1(lb_wf, "walk-forward calibrator (honest probabilities)")
    section1(lb_ins, "in-sample calibrator (optimistic -- reference only)")
    pair, chosen, live_days = section2(lb_wf, "roi")
    section3(lb_wf)
    section4(pair)
    section5(lb_wf)
    return 0


if __name__ == "__main__":
    sys.exit(main())

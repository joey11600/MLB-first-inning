#!/usr/bin/env python3
"""
tools/edge_floor/what_is_it_filtering.py -- ANALYSIS ONLY.

What is an edge floor actually filtering ON, once the gate has already run?

Two candidate answers, both testable:
  (a) it is a stricter PROBABILITY gate in disguise (edge = p - implied,
      and p_yrfi carries most of the variance), or
  (b) it is a PRICE rule -- "skip when DraftKings prices YRFI short".
If either matched-count control reproduces the floor's result, the floor
is not a new idea, it is an existing knob at a different setting.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402
from tools.edge_floor.crux import add_edge, apply_floor, simulate, summary, FLOORS  # noqa: E402


def corr(a, b):
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else float("nan")


def main():
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    bets = add_edge(select(rows, wf, side="YRFI", gate=P._LR_STRONG_YRFI_P, fill=None))
    bets.sort(key=lambda b: b["date"])
    base = simulate(bets)
    inc = base["staked"]
    b0 = summary(base)

    print("=" * 92)
    print("  INSIDE THE BETTING POPULATION, WHAT IS 'EDGE' MADE OF?")
    print("=" * 92)
    ps = [b["p"] for b in inc]
    od = [b["odds"] for b in inc]
    eg = [b["edge"] for b in inc]
    im = [implied(b["odds"]) for b in inc]
    print(f"  n = {len(inc)} staked bets")
    print(f"  p_yrfi   min {min(ps):.3f}  median {st.median(ps):.3f}  max {max(ps):.3f}"
          f"   spread {max(ps)-min(ps):.3f}   sd {st.pstdev(ps):.3f}")
    print(f"  implied  min {min(im):.3f}  median {st.median(im):.3f}  max {max(im):.3f}"
          f"   spread {max(im)-min(im):.3f}   sd {st.pstdev(im):.3f}")
    print(f"  DK odds  min {min(od):.0f}  median {st.median(od):.0f}  max {max(od):.0f}")
    print()
    print(f"  corr(edge, p_yrfi)        {corr(eg, ps):+.3f}")
    print(f"  corr(edge, implied price) {corr(eg, im):+.3f}")
    print(f"  variance of edge explained by price   "
          f"{100*st.pvariance(im)/st.pvariance(eg):.0f}%")
    print(f"  variance of edge explained by p_yrfi  "
          f"{100*st.pvariance(ps)/st.pvariance(eg):.0f}%")
    print()
    print("  VERDICT (a): p_yrfi carries most of the variance, so an edge floor is")
    print("  mostly a stricter probability gate -- but not purely: the price term")
    print("  decides WHICH of the low-p bets get cut.")

    # matched-count p-gate, FLAT stakes (Kelly is path-amplified, see bottom)
    print()
    print("=" * 92)
    print("  CONTROL (a), ON FLAT STAKES -- edge floor vs a p_yrfi gate keeping the")
    print("  SAME number of bets.  Flat, because the Kelly delta is path-amplified.")
    print("=" * 92)
    print(f"  {'floor':>6}{'n':>5}{'edge-floor flat':>18}{'matched p_yrfi':>16}"
          f"{'p-gate flat':>14}{'difference':>13}")
    allp = sorted((b["p"] for b in bets), reverse=True)
    for f in FLOORS[1:]:
        keep = apply_floor(bets, f)
        if not keep:
            continue
        k = len(keep)
        thr = allp[k - 1]
        pg = [b for b in bets if b["p"] >= thr]
        fe = sum(payout(b["odds"]) if b["win"] else -1.0 for b in keep)
        fp = sum(payout(b["odds"]) if b["win"] else -1.0 for b in pg)
        print(f"  {f:>6.2f}{k:>5}{fe:>+17.2f}u{thr:>16.3f}{fp:>+13.2f}u{fe-fp:>+12.2f}u")
    print()
    print("  On flat stakes the floor's advantage over just tightening the gate is")
    print("  a couple of units on ~80 bets -- inside the noise band of either.")

    print()
    print("=" * 92)
    print("  SO TEST IT AS A PRICE RULE -- 'skip YRFI priced shorter than X'")
    print("=" * 92)
    print(f"  {'skip if odds <=':>17}{'bets':>6}{'W':>4}{'L':>4}{'hit%':>7}{'flat':>9}"
          f"{'bank':>10}{'d vs inc':>10}")
    for cut in (-200, -180, -170, -160, -150, -145, -140, -135, -130):
        keep = [b for b in bets if b["odds"] > cut]
        if not keep:
            continue
        s = summary(simulate(keep))
        if not s["n"]:
            continue
        print(f"  {cut:>17.0f}{s['n']:>6}{s['w']:>4}{s['l']:>4}{s['hit']:>7.1f}"
              f"{s['flat']:>+8.2f}u{s['bank']:>9.2f}u{s['profit']-b0['profit']:>+9.2f}u")
    print()
    print("  VERDICT (b): NO. Every price cut loses money against the incumbent, so")
    print("  the floor is not reducible to 'DK priced it short' either.  What is left")
    print("  is a specific 13-game intersection -- see the concentration table.")

    print()
    print("=" * 92)
    print("  HIT RATE BY DK PRICE BUCKET (incumbent staked bets)")
    print("=" * 92)
    print(f"  {'DK YRFI price':<18}{'n':>4}{'W':>4}{'L':>4}{'hit%':>8}{'need%':>8}"
          f"{'flat':>9}{'edge (mean)':>13}")
    buckets = [(-1000, -170), (-170, -150), (-150, -135), (-135, -120), (-120, 1000)]
    for lo, hi in buckets:
        g = [b for b in inc if lo < b["odds"] <= hi]
        if not g:
            continue
        w = sum(1 for b in g if b["win"])
        fl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in g)
        nd = st.mean([implied(b["odds"]) for b in g])
        lbl = (f"{lo:.0f} to {hi:.0f}" if lo > -1000 and hi < 1000
               else (f"<= {hi:.0f}" if lo <= -1000 else f"> {lo:.0f}"))
        print(f"  {lbl:<18}{len(g):>4}{w:>4}{len(g)-w:>4}{100*w/len(g):>7.1f}%"
              f"{100*nd:>7.1f}%{fl:>+8.2f}u{st.mean([b['edge'] for b in g]):>13.3f}")
    print()
    print("  n per bucket is tiny.  Any 'the model does badly at short prices' claim")
    print("  drawn from these cells is a claim about 10-25 baseball games.")

    print()
    print("=" * 92)
    print("  KELLY DELTA vs FLAT DELTA -- why the +22.77u headline is not 22.77u of edge")
    print("=" * 92)
    print(f"  {'floor':>6}{'flat delta':>13}{'Kelly delta':>14}{'amplification':>15}")
    fl0 = sum(payout(b["odds"]) if b["win"] else -1.0 for b in inc)
    for f in FLOORS[1:]:
        keep = apply_floor(bets, f)
        s = summary(simulate(keep))
        flk = sum(payout(b["odds"]) if b["win"] else -1.0 for b in s and simulate(keep)["staked"])
        df = flk - fl0
        dk = s["profit"] - b0["profit"]
        amp = dk / df if abs(df) > 1e-9 else float("nan")
        print(f"  {f:>6.2f}{df:>+12.2f}u{dk:>+13.2f}u{amp:>14.1f}x")
    print()
    print("  Kelly compounds, so removing an early loser inflates every later stake.")
    print("  The Kelly delta is the flat edge times a path-dependent multiplier that")
    print("  depends on WHEN the removed bets happened.  Flat is the edge; Kelly is")
    print("  leverage applied to it, and it unwinds at the same rate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Step 7 -- consolidated verdict numbers, with the search accounting.

The single most important correction: at a +0.04 floor, Kelly ALREADY
stakes zero on most of the bets the floor would remove.  The floor's
real marginal footprint is a handful of bets and ~2% of turnover.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, walk_forward_probs, load_season,
    summary, implied, payout)
from tools.edge_floor.step4_kelly_marginal import (  # noqa: E402
    kelly_sim, apply_floor, block_bootstrap_roi, paired_bootstrap_delta, START)


def footprint(bets, label):
    print(f"\n  {label}  (base n={len(bets)})")
    base_bank, _, sk = kelly_sim(bets)
    staked_keys = {(b["date"], b["rid"]) for b in sk}
    tot_stake = sum(b["stake"] for b in sk)
    print(f"    Kelly stakes >0 on {len(sk)}/{len(bets)} bets; "
          f"{len(bets)-len(sk)} already get ZERO from Kelly alone.")
    print(f"    total turnover {tot_stake:.2f}u, final bank {base_bank:.2f}u")
    print(f"\n  {'floor':>7}{'cut':>5}{'of which Kelly':>16}{'MARGINAL':>10}"
          f"{'their stake':>13}{'% turnover':>12}{'their W-L':>11}{'their Kelly P&L':>17}")
    print(f"  {'':>7}{'':>5}{'already skipped':>16}{'cut':>10}")
    for f in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12):
        cut = [b for b in bets if b["edge"] < f]
        if not cut:
            continue
        already = [b for b in cut if (b["date"], b["rid"]) not in staked_keys]
        marg = [b for b in sk if b["edge"] < f]
        st_ = sum(b["stake"] for b in marg)
        w = sum(1 for b in marg if b["win"])
        pl = sum(b["stake"] * payout(b["odds"]) if b["win"] else -b["stake"]
                 for b in marg)
        print(f"  {f:>+7.2f}{len(cut):>5}{len(already):>16}{len(marg):>10}"
              f"{st_:>12.2f}u{100*st_/tot_stake:>11.1f}%"
              f"{str(w)+'W-'+str(len(marg)-w)+'L':>11}{pl:>+16.2f}u")


def main():
    rows, _ = load_season()
    ins, _ = insample_probs(rows)
    wf = walk_forward_probs(rows)
    L_ins = build_bets(rows, ins)
    L_wf = build_bets(rows, wf)

    print("=" * 112)
    print("  THE FLOOR'S REAL MARGINAL FOOTPRINT OVER KELLY")
    print("=" * 112)
    footprint(L_ins, "in-sample calibrator")
    footprint(L_wf, "walk-forward calibrator")

    print("\n" + "=" * 112)
    print("  HEADLINE COMPARISON, FLAT 1u (the edge), floor +0.04")
    print("=" * 112)
    for label, bets in (("in-sample", L_ins), ("walk-forward", L_wf)):
        s0 = summary(bets)
        s1 = summary(apply_floor(bets, 0.04))
        dlo, dmed, dhi, pw = paired_bootstrap_delta(bets, apply_floor(bets, 0.04))
        print(f"  {label:<14} no floor  n={s0['n']:>4} hit {s0['hit']:.1f}% "
              f"flat {s0['pl']:+6.2f}u ROI {s0['roi']:+5.1f}%")
        print(f"  {'':<14} +0.04     n={s1['n']:>4} hit {s1['hit']:.1f}% "
              f"flat {s1['pl']:+6.2f}u ROI {s1['roi']:+5.1f}%")
        print(f"  {'':<14} DELTA     {s1['pl']-s0['pl']:+.2f}u flat  "
              f"[block-bootstrap 90% CI {dlo:+.2f}u .. {dhi:+.2f}u, "
              f"P(better)={pw:.0f}%]\n")

    print("=" * 112)
    print("  SEARCH ACCOUNTING")
    print("=" * 112)
    print("  edge floors evaluated          : 12  (-0.10,-0.05,0,0.01,0.02,0.03,")
    print("                                        0.04,0.05,0.06,0.08,0.10,0.12)")
    print("  price cutoffs evaluated        : 6   (implied <= 0.52..0.62)")
    print("  probability equivalents (2025) : 6 floors x 3 assumed prices = 18")
    print("  probability streams            : 2   (deployed / walk-forward)")
    print("  staking schemes                : 2   (flat 1u / quarter Kelly)")
    print("  selection objectives           : 2   (ROI / total profit)")
    print("  -> the in-sample table in the brief is the best of a search this")
    print("     wide over ONE season of 118 gate-qualifying priced bets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

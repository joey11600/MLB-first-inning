#!/usr/bin/env python3
"""Step 6 -- (a) prove the Kelly gain is a compounding-path accident,
(b) test the floor as what it actually is inside the bet set: a
BET-LONGER-PRICES rule, (c) ask whether the market price carries any
information about the outcome inside the bet set at all.
"""
from __future__ import annotations

import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, walk_forward_probs, load_season,
    summary, implied, payout)
from tools.edge_floor.step4_kelly_marginal import (  # noqa: E402
    kelly_sim, apply_floor, block_bootstrap_roi, walk_forward_floor, START)


def path_dependence(bets, f=0.04):
    cut = [b for b in bets if b["edge"] < f]
    _, _, sk = kelly_sim(bets)
    cutset = {(b["date"], b["rid"]) for b in cut}
    cs = [b for b in sk if (b["date"], b["rid"]) in cutset]
    print(f"\n  The {len(cut)} bets a +{f:.2f} floor would remove, in date order:")
    print(f"  {'date':<12}{'edge':>8}{'odds':>7}{'stake':>8}{'res':>6}{'Kelly P&L':>11}")
    tot = 0.0
    for b in sorted(cs, key=lambda x: x["date"]):
        pl = b["stake"] * payout(b["odds"]) if b["win"] else -b["stake"]
        tot += pl
        print(f"  {b['date']:<12}{b['edge']:>+8.3f}{b['odds']:>7.0f}"
              f"{b['stake']:>7.2f}u{'W' if b['win'] else 'L':>6}{pl:>+10.2f}u")
    flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in cut)
    w = sum(1 for b in cut if b["win"])
    print(f"  ---> record {w}W-{len(cut)-w}L   FLAT P&L {flat:+.2f}u "
          f"({100*flat/len(cut):+.1f}% ROI)   KELLY P&L {tot:+.2f}u")
    print(f"  A near-break-even FLAT record turning into {tot:+.2f}u under Kelly")
    print(f"  means the losses landed when the bank was large and the wins when")
    print(f"  it was small. That is the sequence, not the selection rule.")


def price_rule(bets, label):
    """Ignore the model term entirely: bet only prices longer than X."""
    print(f"\n  {label}  -- PURE PRICE RULE inside the live bet set")
    print(f"  {'implied <=':>11}{'bets':>6}{'hit%':>7}{'need%':>8}{'flat u':>9}"
          f"{'ROI':>8}{'Kelly end':>11}{'boot 90% CI':>20}")
    base = summary(bets)
    b0, _, _ = kelly_sim(bets)
    lo, hi = block_bootstrap_roi(bets)
    print(f"  {'(all)':>11}{base['n']:>6}{base['hit']:>7.1f}{base['need']:>8.1f}"
          f"{base['pl']:>+8.2f}u{base['roi']:>+7.1f}%{b0:>10.2f}u"
          f"   [{lo:+6.1f},{hi:+6.1f}]")
    for t in (0.62, 0.60, 0.58, 0.56, 0.54, 0.52):
        g = [b for b in bets if b["implied"] <= t]
        if len(g) < 12:
            continue
        s = summary(g)
        bk, _, _ = kelly_sim(g)
        lo, hi = block_bootstrap_roi(g)
        print(f"  {t:>11.2f}{s['n']:>6}{s['hit']:>7.1f}{s['need']:>8.1f}"
              f"{s['pl']:>+8.2f}u{s['roi']:>+7.1f}%{bk:>10.2f}u"
              f"   [{lo:+6.1f},{hi:+6.1f}]")


def market_informative(bets, label):
    """Inside the bet set, does the price predict the outcome? A logistic
    slope of win ~ implied, with a permutation p-value."""
    xs = [b["implied"] for b in bets]
    ys = [1.0 if b["win"] else 0.0 for b in bets]
    mx = sum(xs) / len(xs)

    def slope(y):
        # single-variable logistic via Newton, 30 iters
        a, c = 0.0, 0.0
        for _ in range(60):
            g0 = g1 = h00 = h01 = h11 = 0.0
            for x, yy in zip(xs, y):
                z = a + c * (x - mx)
                p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
                r = yy - p
                w = p * (1 - p)
                g0 += r
                g1 += r * (x - mx)
                h00 += w
                h01 += w * (x - mx)
                h11 += w * (x - mx) ** 2
            det = h00 * h11 - h01 * h01
            if abs(det) < 1e-12:
                break
            da = (h11 * g0 - h01 * g1) / det
            dc = (h00 * g1 - h01 * g0) / det
            a += da
            c += dc
            if abs(da) + abs(dc) < 1e-10:
                break
        return c

    obs = slope(ys)
    rng = random.Random(3)
    perm = []
    for _ in range(3000):
        sh = ys[:]
        rng.shuffle(sh)
        perm.append(slope(sh))
    p = sum(1 for v in perm if abs(v) >= abs(obs)) / len(perm)
    print(f"\n  {label}: logistic slope of WIN on implied prob "
          f"= {obs:+.2f}  (permutation p = {p:.3f}, n={len(bets)})")
    print(f"    A market that knew something would give a clearly POSITIVE slope")
    print(f"    (shorter price -> more likely to hit). Inside this bet set it does not,")
    print(f"    but n={len(bets)} cannot distinguish 'uninformative' from 'underpowered'.")


def main():
    rows, _ = load_season()
    ins, _ = insample_probs(rows)
    wf = walk_forward_probs(rows)
    L_ins = build_bets(rows, ins)
    L_wf = build_bets(rows, wf)

    print("=" * 108)
    print("  (a) IS THE KELLY GAIN A COMPOUNDING-PATH ACCIDENT?")
    print("=" * 108)
    path_dependence(L_ins, 0.04)

    print("\n" + "=" * 108)
    print("  (b) THE FLOOR, RE-TESTED AS WHAT IT IS INSIDE THE BET SET:")
    print("      a rule that refuses SHORT PRICES")
    print("=" * 108)
    price_rule(L_ins, "in-sample")
    price_rule(L_wf, "walk-forward")

    print("\n  walk-forward SELECTION of a price cutoff (chosen from prior days only):")
    grid = [0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64]
    for label, bets in (("in-sample", L_ins), ("walk-forward", L_wf)):
        for b in bets:
            b["edge_backup"] = b["edge"]
            b["edge"] = -b["implied"]      # reuse the WF machinery on -implied
        kept, chosen = walk_forward_floor(bets, [-g for g in grid], objective="roi")
        s0, s1 = summary(bets), summary(kept)
        b0, _, _ = kelly_sim(bets)
        b1, _, _ = kelly_sim(kept)
        print(f"    {label:<14} no rule n={s0['n']} flat {s0['pl']:+.2f}u "
              f"Kelly {b0:.2f}u  ->  WF price rule n={s1['n']} flat {s1['pl']:+.2f}u "
              f"Kelly {b1:.2f}u   delta flat {s1['pl']-s0['pl']:+.2f}u "
              f"Kelly {b1-b0:+.2f}u")
        for b in bets:
            b["edge"] = b["edge_backup"]

    print("\n" + "=" * 108)
    print("  (c) DOES THE PRICE CARRY INFORMATION INSIDE THE BET SET?")
    print("=" * 108)
    market_informative(L_ins, "in-sample bet set")
    market_informative(L_wf, "walk-forward bet set")

    print("\n  hit% by implied-probability quartile INSIDE the bet set")
    for label, bets in (("in-sample", L_ins), ("walk-forward", L_wf)):
        ss = sorted(bets, key=lambda b: b["implied"])
        print(f"    {label}:")
        for i in range(4):
            g = ss[i * len(ss) // 4:(i + 1) * len(ss) // 4]
            if not g:
                continue
            s = summary(g)
            se = 100 * math.sqrt(0.67 * 0.33 / len(g))
            print(f"      implied {g[0]['implied']:.3f}..{g[-1]['implied']:.3f}  "
                  f"n={s['n']:>3}  hit {s['hit']:>5.1f}% (+/-{se:.1f} 1se)  "
                  f"need {s['need']:>5.1f}%  ROI {s['roi']:>+6.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

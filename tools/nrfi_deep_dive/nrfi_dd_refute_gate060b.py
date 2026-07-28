#!/usr/bin/env python3
"""
tools/nrfi_dd_refute_gate060b.py -- follow-ups to the refutation.

  A. DEPLOYABILITY: how many bets would the rule have produced since
     the last one? (If zero, the rule is untestable going forward.)
  B. Is the lambda<=0.52 leg doing real work, or is it a calendar proxy?
  C. The 46 qualifying games with no captured price -- when are they?
  D. Sensitivity: does the win survive dropping ANY single slate?

Read-only.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied, fnum  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402
from tools.nrfi_dd_refute_gate060 import sel_nrfi, flat  # noqa: E402

CEIL, GATE = P._LR_LAMBDA_NRFI_CEILING, 0.60


def main():
    rows, _ = load_season()
    p_wf = walk_forward_probs(rows)
    bets = sel_nrfi(rows, p_wf, GATE, CEIL)
    allq = sel_nrfi(rows, p_wf, GATE, CEIL, real_only=False)

    print("=" * 92)
    print("  A. DEPLOYABILITY -- what has the rule fired on since its last bet?")
    print("=" * 92)
    last = max(b["date"] for b in allq)
    after = [r for r in rows if r["date"] > last]
    print(f"  last qualifying game of ANY kind: {last}")
    print(f"  graded games after that date: {len(after)} over "
          f"{len(set(r['date'] for r in after))} slates")
    print(f"  qualifying under the rule in that period: "
          f"{len([b for b in allq if b['date'] > last])}")
    print("  -> the rule is not merely unproven, it is INERT: adopting it today")
    print("     would place zero bets and generate zero new evidence.")

    print()
    print("=" * 92)
    print("  B. IS THE lambda<=0.52 LEG DOING WORK, OR IS IT A CALENDAR PROXY?")
    print("=" * 92)
    # gate only, no ceiling, real prices
    g_only = sel_nrfi(rows, p_wf, GATE, 9.9)
    f1, f2 = flat(g_only), flat(bets)
    print(f"  p>=0.60 alone           : n={f1['n']:>3} hit={100*f1['hit']:>5.1f}% "
          f"P&L={f1['pl']:+7.2f}u")
    print(f"  p>=0.60 AND lam<=0.52   : n={f2['n']:>3} hit={100*f2['hit']:>5.1f}% "
          f"P&L={f2['pl']:+7.2f}u")
    excl = [b for b in g_only if not any(
        x["date"] == b["date"] and x["game"] == b["game"] for x in bets)]
    fe = flat(excl)
    print(f"  the {fe['n']} games the ceiling THREW AWAY: hit={100*fe['hit']:.1f}% "
          f"P&L={fe['pl']:+.2f}u")
    print()
    lam_by_month = defaultdict(list)
    for r in rows:
        if r["lambda"] is not None:
            lam_by_month[r["date"][:7]].append(r["lambda"])
    print("  lambda_lr_total by month (is the ceiling seasonal?):")
    for m in sorted(lam_by_month):
        v = lam_by_month[m]
        print(f"    {m}  n={len(v):>4}  mean={st.mean(v):.3f}  "
              f"frac<=0.52 = {100*sum(1 for x in v if x <= 0.52)/len(v):>5.1f}%")
    miss = defaultdict(lambda: [0, 0])
    for r in rows:
        m = miss[r["date"][:7]]
        m[0] += 1
        m[1] += r["lambda"] is None
    print("\n  lambda_lr_total MISSINGNESS by month (rule treats missing as PASS):")
    for m in sorted(miss):
        v = miss[m]
        print(f"    {m}  games={v[0]:>4}  missing={v[1]:>4} ({100*v[1]/v[0]:.0f}%)")

    print()
    print("=" * 92)
    print("  C. THE UNPRICED QUALIFIERS")
    print("=" * 92)
    nop = [b for b in allq if b["odds"] is None]
    bym = defaultdict(lambda: [0, 0])
    for b in allq:
        m = bym[b["date"][:7]]
        m[0] += 1
        m[1] += b["odds"] is not None
    for m in sorted(bym):
        print(f"    {m}: {bym[m][0]} qualifying, {bym[m][1]} with a real DK price")
    w = sum(b["win"] for b in nop)
    print(f"  unpriced: {len(nop)} games, {w}-{len(nop)-w} = "
          f"{100*w/max(1,len(nop)):.1f}% NRFI")
    print(f"  priced  : {len(bets)} games, {sum(b['win'] for b in bets)}-"
          f"{len(bets)-sum(b['win'] for b in bets)} = "
          f"{100*flat(bets)['hit']:.1f}% NRFI")
    print("  NOTE: the 14 priced games are a 23% subsample of the rule's own")
    print("  selections, chosen by whether a scraper happened to capture a line.")

    print()
    print("=" * 92)
    print("  D. LEAVE-ONE-SLATE-OUT SENSITIVITY (10 slates carry the whole result)")
    print("=" * 92)
    days = sorted({b["date"] for b in bets})
    for d in days:
        rest = [b for b in bets if b["date"] != d]
        f3 = flat(rest)
        print(f"    drop {d}: n={f3['n']:>3} hit={100*f3['hit']:>5.1f}% "
              f"P&L={f3['pl']:+6.2f}u ROI={100*f3['roi']:+6.1f}%")
    # drop the two best slates
    order = sorted(days, key=lambda d: -sum(
        payout(b["odds"]) if b["win"] else -1.0 for b in bets if b["date"] == d))
    rest = [b for b in bets if b["date"] not in order[:2]]
    f4 = flat(rest)
    print(f"    drop the 2 best slates ({order[0]}, {order[1]}): n={f4['n']} "
          f"hit={100*f4['hit']:.1f}% P&L={f4['pl']:+.2f}u")

    print()
    print("=" * 92)
    print("  E. HOW MANY WINS OUT OF 14 ARE 'EXCESS'?")
    print("=" * 92)
    need = flat(bets)["need"]
    print(f"  break-even at these prices = {100*need:.1f}%  -> expected wins "
          f"= {14*need:.1f}")
    print(f"  observed wins = 11.  Excess = {11 - 14*need:.1f} games.")
    print("  Flipping TWO coin-flip outcomes (9/14 = 64.3%) drops it to "
          f"{flat([dict(b, win=(b['win'] and i > 1)) for i, b in enumerate(sorted(bets, key=lambda x: x['date']))])['pl']:+.2f}u.")


if __name__ == "__main__":
    main()

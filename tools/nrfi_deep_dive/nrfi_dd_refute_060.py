#!/usr/bin/env python3
"""
tools/nrfi_dd_refute_060.py -- adversarial re-derivation of the claimed
NRFI rule:  walk-forward p_nrfi >= 0.60 AND lambda_lr_total <= 0.52.

This script does NOT trust the claim. It rebuilds the selection from the
live CSV + walk-forward calibrator, then attacks it on three axes:
overfitting (search exposure), pricing (real vs shaded), sample size
(block bootstrap over days + exact binomial under the null).

Analysis only. Writes nothing.
"""
from __future__ import annotations

import csv
import math
import random
import statistics as st
import sys
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402

CEIL = P._LR_LAMBDA_NRFI_CEILING


def sel(rows, probs, gate, ceil=CEIL, real_only=True):
    out = []
    for r, p in zip(rows, probs):
        if p is None or p < gate:
            continue
        if r["lambda"] is not None and r["lambda"] > ceil:
            continue
        o = r["nrfi_odds"]
        if o is None:
            if real_only:
                continue
            o = -110.0
        out.append({"date": r["date"], "game": f"{r['away']}@{r['home']}",
                    "p": p, "odds": o, "win": not r["yrfi_hit"],
                    "lam": r["lambda"]})
    return out


def stats(bets):
    n = len(bets)
    if not n:
        return dict(n=0, w=0, hit=float("nan"), need=float("nan"), pl=0.0, roi=float("nan"))
    w = sum(b["win"] for b in bets)
    pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)
    need = st.mean([implied(b["odds"]) for b in bets])
    return dict(n=n, w=w, hit=w / n, need=need, pl=pl, roi=pl / n)


def binom_sf(k, n, p):
    """P(X >= k) for Binomial(n, p)."""
    return sum(math.comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k, n + 1))


def shade(bets, cents=10):
    """Make every price `cents` worse for the bettor (American odds)."""
    out = []
    for b in bets:
        o = b["odds"]
        no = o - cents if o < 0 else (o - cents if o - cents >= 100 else -(100 + (100 - (o - cents))))
        c = dict(b)
        c["odds"] = no
        out.append(c)
    return out


def block_boot(bets, iters=10000, seed=11):
    if not bets:
        return float("nan"), float("nan"), float("nan")
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = random.Random(seed)
    res = []
    for _ in range(iters):
        n = 0
        pl = 0.0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                n += 1
                pl += payout(b["odds"]) if b["win"] else -1.0
        if n:
            res.append(100 * pl / n)
    res.sort()
    return (res[int(0.05 * len(res))], res[int(0.95 * len(res))],
            sum(1 for x in res if x <= 0) / len(res))


def main():
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    ins = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    ins_p = [ins.predict(r["raw"]) for r in rows]

    print("=" * 100)
    print("  1. RE-DERIVE THE CLAIM (walk-forward calibrator, real captured DK prices)")
    print("=" * 100)
    bets = sel(rows, wf, 0.60)
    s = stats(bets)
    print(f"  rule: wf p_nrfi >= 0.60 AND lambda <= {CEIL}")
    print(f"  bets={s['n']}  wins={s['w']}  hit={100*s['hit']:.1f}%  "
          f"break-even(real prices)={100*s['need']:.1f}%  flat P&L={s['pl']:+.2f}u  "
          f"ROI={100*s['roi']:+.1f}%")
    print(f"  distinct slates: {len(set(b['date'] for b in bets))}")
    print("\n  every bet:")
    print(f"  {'date':<12}{'game':<12}{'p_nrfi':>8}{'lam':>7}{'odds':>7}{'result':>8}")
    for b in sorted(bets, key=lambda x: x["date"]):
        print(f"  {b['date']:<12}{b['game']:<12}{b['p']:>8.4f}{b['lam']:>7.3f}"
              f"{int(b['odds']):>7}{'WIN' if b['win'] else 'LOSS':>8}")

    # ---- 2. temporal concentration / calibrator reachability -------------
    print("\n" + "=" * 100)
    print("  2. IS THE RULE EVEN REACHABLE? max walk-forward p_nrfi by month")
    print("=" * 100)
    bym = defaultdict(list)
    for r, p in zip(rows, wf):
        if p is not None:
            bym[r["date"][:7]].append(p)
    for m in sorted(bym):
        v = bym[m]
        n60 = sum(1 for x in v if x >= 0.60)
        print(f"  {m}   games={len(v):>4}  max p_nrfi={max(v):.4f}  "
              f"p95={sorted(v)[int(.95*len(v))]:.4f}  n(p>=0.60)={n60}")
    allb = sel(rows, wf, 0.60, real_only=False)
    print(f"\n  games passing the rule ignoring price availability: {len(allb)}"
          f"   date span {min(b['date'] for b in allb)} .. {max(b['date'] for b in allb)}")
    dc = Counter(b["date"][:7] for b in allb)
    print(f"  by month: {dict(sorted(dc.items()))}")

    # ---- 3. significance under the null ---------------------------------
    print("\n" + "=" * 100)
    print("  3. SIGNIFICANCE UNDER THE NULL OF ZERO EDGE")
    print("=" * 100)
    n, w, need = s["n"], s["w"], s["need"]
    p1 = binom_sf(w, n, need)
    print(f"  exact binomial P(X >= {w} | n={n}, p={need:.4f}) = {p1:.4f}")
    lo, hi, pneg = block_boot(bets)
    print(f"  block bootstrap over DAYS (10k): 90% CI on ROI = [{lo:+.1f}%, {hi:+.1f}%]"
          f"   P(ROI<=0) = {pneg:.3f}")
    print(f"  CI excludes zero? {'YES' if lo > 0 else 'NO'}")

    # search exposure
    for k in (10, 40, 100):
        fam = 1 - (1 - p1) ** k
        print(f"  family-wise p if {k:>3} cells were searched (Sidak): {fam:.3f}")

    # ---- 4. pricing -------------------------------------------------------
    print("\n" + "=" * 100)
    print("  4. PRICING")
    print("=" * 100)
    print(f"  prices are REAL captured DK numbers on all {n} bets: "
          f"{[int(b['odds']) for b in sorted(bets, key=lambda x: x['date'])]}")
    for c in (5, 10, 15, 20):
        sh = stats(shade(bets, c))
        print(f"  shade {c:>2} cents worse -> need={100*sh['need']:.1f}%  "
              f"P&L={sh['pl']:+.2f}u  ROI={100*sh['roi']:+.1f}%")
    # win/loss by price
    wprice = [b["odds"] for b in bets if b["win"]]
    lprice = [b["odds"] for b in bets if not b["win"]]
    print(f"  mean price on WINS  : {st.mean(wprice):+.0f} (n={len(wprice)})")
    if lprice:
        print(f"  mean price on LOSSES: {st.mean(lprice):+.0f} (n={len(lprice)})")

    # ---- 5. gate sweep, all cells shown ----------------------------------
    print("\n" + "=" * 100)
    print("  5. THE SWEEP THAT PRODUCED IT -- every neighbouring cell")
    print("=" * 100)
    print(f"  {'cal':<5}{'gate':>6}{'ceil':>7}{'n':>5}{'w':>4}{'hit%':>7}{'need%':>7}"
          f"{'P&L':>9}{'ROI%':>8}{'binom p':>9}")
    cells = 0
    winners = []
    for cal_name, pv in (("wf", wf), ("ins", ins_p)):
        for gate in (0.55, 0.57, 0.58, 0.60, 0.62, 0.65):
            for ceil in (0.45, 0.52, 0.60, 9.99):
                b2 = sel(rows, pv, gate, ceil)
                s2 = stats(b2)
                cells += 1
                if s2["n"] == 0:
                    continue
                pb = binom_sf(s2["w"], s2["n"], s2["need"])
                if s2["roi"] > 0:
                    winners.append((cal_name, gate, ceil, s2, pb))
                print(f"  {cal_name:<5}{gate:>6.2f}{ceil:>7.2f}{s2['n']:>5}{s2['w']:>4}"
                      f"{100*s2['hit']:>7.1f}{100*s2['need']:>7.1f}{s2['pl']:>+9.2f}"
                      f"{100*s2['roi']:>+8.1f}{pb:>9.4f}")
    print(f"\n  cells enumerated here: {cells};  cells with positive ROI: {len(winners)}")
    print("  (this is ONE slice -- the reported find also swept seasons and raw/calibrated)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

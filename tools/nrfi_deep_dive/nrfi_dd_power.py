#!/usr/bin/env python3
"""
tools/nrfi_dd_power.py -- STATISTICAL POWER REALITY CHECK for NRFI.

Question: what could EVER be concluded about NRFI profitability from
the data that exists?  Not "does gate X win" -- "could we tell".

Sections
  1. Inventory + CI width at every candidate gate (real prices only)
  2. Power: n required to detect a real edge at 80% power; calendar time
  3. Why the deployed and walk-forward calibrators disagree by 32pp
  4. Bottom line

Analysis only -- touches no production file.
"""
from __future__ import annotations

import math
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402

GATES = (0.55, 0.58, 0.60, 0.62, 0.65)


# ---------------------------------------------------------------- stats
def wilson(k, n, z=1.645):
    """Wilson score interval -- better than normal approx at small n."""
    if n == 0:
        return float("nan"), float("nan")
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def binom_cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def block_boot_hit(bets, iters=8000, seed=11):
    """Block bootstrap over DAYS -> percentiles of the HIT RATE."""
    if not bets:
        return float("nan"), float("nan")
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        n = w = 0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                n += 1
                w += b["win"]
        if n:
            out.append(w / n)
    out.sort()
    return out[int(0.05 * len(out))], out[int(0.95 * len(out))]


def n_for_power(p0, p1, alpha=0.05, power=0.80):
    """One-sided one-sample proportion test sample size."""
    za, zb = 1.6449, 0.8416
    num = za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1))
    return math.ceil((num / (p1 - p0)) ** 2)


# ---------------------------------------------------------------- main
def main():
    rows, _ = load_season()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    dep = [cal.predict(r["raw"]) for r in rows]
    wf = walk_forward_probs(rows)

    ndays = len({r["date"] for r in rows})
    d0, d1 = min(r["date"] for r in rows), max(r["date"] for r in rows)
    span_days = ndays
    print("=" * 100)
    print(f"  SEASON POOL: {len(rows)} graded games, {ndays} slate-days, {d0} .. {d1}")
    npr = sum(1 for r in rows if r["nrfi_odds"] is not None)
    print(f"  with a REAL captured DK NRFI price: {npr} ({100*npr/len(rows):.0f}%)")
    print("=" * 100)

    # ---- 1. inventory + CI width -------------------------------------
    for label, probs in (("DEPLOYED calibrator", dep), ("WALK-FORWARD calibrator", wf)):
        print(f"\n### 1. GATE INVENTORY -- {label} -- REAL PRICES ONLY")
        print(f"  {'gate':>6}{'bets':>6}{'days':>6}{'W-L':>9}{'hit%':>8}{'need%':>8}"
              f"{'Wilson 90%':>18}{'blockboot 90%':>20}{'width':>8}{'gap/half':>10}"
              f"{'flat u':>9}")
        for g in GATES:
            bets = select(rows, probs, side="NRFI", gate=g, fill=None)
            n = len(bets)
            if n == 0:
                print(f"  {g:>6.2f}{0:>6}")
                continue
            w = sum(1 for b in bets if b["win"])
            days = len({b["date"] for b in bets})
            hit = w / n
            need = st.mean([implied(b["odds"]) for b in bets])
            pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)
            wl, wh = wilson(w, n)
            bl, bh = block_boot_hit(bets)
            half = (bh - bl) / 2 if not math.isnan(bl) else float("nan")
            gap = (hit - need) / half if half and half == half and half > 0 else float("nan")
            print(f"  {g:>6.2f}{n:>6}{days:>6}{f'{w}-{n-w}':>9}{100*hit:>7.1f}%"
                  f"{100*need:>7.1f}%  [{100*wl:>5.1f},{100*wh:>5.1f}]"
                  f"   [{100*bl:>5.1f},{100*bh:>5.1f}]"
                  f"{100*(wh-wl):>7.1f}{gap:>10.2f}{pl:>+9.2f}")

        # rate of qualifying games
        print(f"\n  qualifying-game RATE ({label}) -- all games, priced or not:")
        for g in GATES:
            allq = [r for r, p in zip(rows, probs)
                    if p is not None and p >= g
                    and not (r["lambda"] is not None and r["lambda"] > P._LR_LAMBDA_NRFI_CEILING)]
            pr = [r for r in allq if r["nrfi_odds"] is not None]
            print(f"    gate {g:.2f}: {len(allq):>4} qualify / {len(rows)} games "
                  f"({100*len(allq)/len(rows):.1f}%)  -> {len(pr)} priced "
                  f"({100*len(pr)/max(1,len(allq)):.0f}% capture)  "
                  f"= {7*len(allq)/span_days:.2f} qual/wk, "
                  f"{7*len(pr)/span_days:.2f} priced/wk")

    # ---- 2. power ----------------------------------------------------
    print("\n" + "=" * 100)
    print("### 2. HOW MANY BETS TO DETECT A REAL EDGE (80% power, one-sided a=0.05)")
    print("=" * 100)
    base = select(rows, wf, side="NRFI", gate=0.60, fill=None)
    p0 = st.mean([implied(b["odds"]) for b in base]) if base else 0.545
    print(f"  break-even p0 = {100*p0:.1f}% (mean vig-inclusive implied NRFI price at gate 0.60,"
          f" n={len(base)})")
    print(f"  {'true hit%':>10}{'edge pp':>9}{'ROI@-120':>10}{'n needed':>10}"
          f"{'weeks@2.0/wk':>14}{'weeks@1.0/wk':>14}{'seasons':>9}")
    for d in (0.02, 0.03, 0.04, 0.05, 0.08, 0.10):
        p1 = p0 + d
        n = n_for_power(p0, p1)
        roi = 100 * (p1 * payout(-120) - (1 - p1))
        print(f"  {100*p1:>9.1f}%{100*d:>9.1f}{roi:>9.1f}%{n:>10}"
              f"{n/2.0:>14.0f}{n/1.0:>14.0f}{n/(2.0*26):>9.1f}")
    print("\n  (a full MLB season is ~26 betting weeks; 'seasons' assumes 2.0 priced")
    print("   NRFI-qualifying bets per week, the observed 0.60-gate rate)")

    # ---- 3. calibrator disagreement ----------------------------------
    print("\n" + "=" * 100)
    print("### 3. WHY DEPLOYED (12-14) AND WALK-FORWARD (11-3) DISAGREE")
    print("=" * 100)
    G = 0.60
    A = select(rows, dep, side="NRFI", gate=G, fill=None)
    B = select(rows, wf, side="NRFI", gate=G, fill=None)
    ka = lambda b: (b["date"], b["game"])
    sa, sb = {ka(b) for b in A}, {ka(b) for b in B}
    inter = sa & sb
    onlyA = sa - sb
    onlyB = sb - sa
    mapA = {ka(b): b for b in A}
    mapB = {ka(b): b for b in B}

    def wl(keys, m):
        w = sum(1 for k in keys if m[k]["win"])
        return w, len(keys) - w

    print(f"  deployed  gate {G}: {len(A)} bets, {wl(sa, mapA)[0]}-{wl(sa, mapA)[1]}"
          f"  ({100*wl(sa,mapA)[0]/max(1,len(A)):.1f}%)")
    print(f"  walkfwd   gate {G}: {len(B)} bets, {wl(sb, mapB)[0]}-{wl(sb, mapB)[1]}"
          f"  ({100*wl(sb,mapB)[0]/max(1,len(B)):.1f}%)")
    print(f"\n  OVERLAP (same game bet by both): {len(inter)}  -> {wl(inter, mapA)}")
    print(f"  deployed ONLY: {len(onlyA)} -> {wl(onlyA, mapA)}")
    print(f"  walkfwd  ONLY: {len(onlyB)} -> {wl(onlyB, mapB)}")
    print("\n  dates covered:")
    if sa:
        print(f"    deployed {min(d for d,_ in sa)} .. {max(d for d,_ in sa)}")
    if sb:
        print(f"    walkfwd  {min(d for d,_ in sb)} .. {max(d for d,_ in sb)}")
    # walk-forward is undefined for the first min_train games
    nwf_none = sum(1 for p in wf if p is None)
    print(f"    walk-forward is UNDEFINED (None) for the first {nwf_none} graded games "
          f"-- those dates are structurally absent from the WF sample")
    if nwf_none:
        cut = rows[nwf_none - 1]["date"]
        depearly = [b for b in A if b["date"] <= cut]
        we = sum(1 for b in depearly if b["win"])
        print(f"    deployed bets on or before {cut} (the WF blind window): "
              f"{len(depearly)} -> {we}-{len(depearly)-we}")

    # Under the null, both are draws from the same underlying pool.
    print("\n  --- is a 12-14 vs 11-3 split consistent with pure noise? ---")
    nA, wA = len(A), sum(1 for b in A if b["win"])
    nB, wB = len(B), sum(1 for b in B if b["win"])
    # (a) two-proportion Fisher-style exact test on the DISJOINT parts only,
    #     because shared games cannot contribute to a difference.
    nOa, wOa = len(onlyA), wl(onlyA, mapA)[0]
    nOb, wOb = len(onlyB), wl(onlyB, mapB)[0]
    pool = (wOa + wOb) / max(1, (nOa + nOb))
    print(f"  disjoint sets: dep-only {wOa}/{nOa}, wf-only {wOb}/{nOb}, pooled p={pool:.3f}")
    # exact permutation p-value on the disjoint parts
    if nOa and nOb:
        obs = abs(wOb / nOb - wOa / nOa)
        rng = random.Random(7)
        labels = [1] * (wOa + wOb) + [0] * (nOa + nOb - wOa - wOb)
        cnt = 0
        IT = 200000
        for _ in range(IT):
            rng.shuffle(labels)
            a = sum(labels[:nOa]) / nOa
            b = sum(labels[nOa:]) / nOb
            if abs(b - a) >= obs - 1e-12:
                cnt += 1
        print(f"  observed |hit_wf-only - hit_dep-only| = {100*obs:.1f}pp")
        print(f"  two-sided permutation p = {cnt/IT:.4f}  ({cnt}/{IT})")
    # (b) full-sample naive comparison, ignoring overlap (upper bound on surprise)
    obs2 = abs(wB / nB - wA / nA)
    poolf = (wA + wB) / (nA + nB)
    rng = random.Random(9)
    labels = [1] * (wA + wB) + [0] * (nA + nB - wA - wB)
    cnt = 0
    IT = 200000
    for _ in range(IT):
        rng.shuffle(labels)
        a = sum(labels[:nA]) / nA
        b = sum(labels[nA:]) / nB
        if abs(b - a) >= obs2 - 1e-12:
            cnt += 1
    print(f"\n  naive full-sample: {100*obs2:.1f}pp gap, permutation p = {cnt/IT:.4f}"
          f"  (treats the {len(inter)} shared games as independent -- ANTI-conservative)")

    # (c) how likely is 11-3 or better if the truth is exactly break-even?
    for n_, w_ in ((nB, wB), (nA, wA)):
        pv = 1 - binom_cdf(w_ - 1, n_, p0)
        print(f"  P(>= {w_} wins in {n_} | true p = break-even {100*p0:.1f}%) = {pv:.4f}")

    # (d) what does the WF record imply about the NEXT n bets?
    print("\n  --- predictive check: pretend WF 11-3 is real, forecast next 14 ---")
    print("  posterior (Jeffreys Beta(0.5,0.5) prior) on true hit rate given 11-3:")
    a_, b_ = wB + 0.5, nB - wB + 0.5
    mean = a_ / (a_ + b_)
    # sample posterior quantiles numerically
    xs = [i / 20000 for i in range(1, 20000)]
    dens = [x ** (a_ - 1) * (1 - x) ** (b_ - 1) for x in xs]
    tot = sum(dens)
    cum = 0.0
    q = {}
    for x, d in zip(xs, dens):
        cum += d / tot
        for t in (0.05, 0.50, 0.95):
            if t not in q and cum >= t:
                q[t] = x
    print(f"    mean {100*mean:.1f}%, 90% credible [{100*q[0.05]:.1f}, {100*q[0.95]:.1f}]")
    print(f"    P(true rate > break-even {100*p0:.1f}%) = "
          f"{sum(d for x, d in zip(xs, dens) if x > p0)/tot:.3f}")

    # ---- 4. season-split stability of the WF result -------------------
    print("\n" + "=" * 100)
    print("### 4. MONTH-BY-MONTH -- is the WF 0.60 record one hot stretch?")
    print("=" * 100)
    for lbl, bets in (("deployed", A), ("walk-fwd", B)):
        bym = defaultdict(lambda: [0, 0, 0.0])
        for b in bets:
            m = bym[b["date"][:7]]
            m[0] += 1
            m[1] += b["win"]
            m[2] += payout(b["odds"]) if b["win"] else -1.0
        print(f"  {lbl}:")
        for m in sorted(bym):
            n_, w_, pl_ = bym[m]
            print(f"    {m}  n={n_:>3}  {w_}-{n_-w_}  {100*w_/n_:>5.1f}%  {pl_:>+7.2f}u")

    # ---- 5. how much of the pool is even priced, by month -------------
    print("\n### 5. PRICE CAPTURE BY MONTH (the binding constraint)")
    bym = defaultdict(lambda: [0, 0])
    for r in rows:
        m = bym[r["date"][:7]]
        m[0] += 1
        m[1] += r["nrfi_odds"] is not None
    for m in sorted(bym):
        t, p = bym[m]
        print(f"    {m}  {p:>4}/{t:<4} priced ({100*p/t:>5.1f}%)")


if __name__ == "__main__":
    main()

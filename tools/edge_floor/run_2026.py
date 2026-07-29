#!/usr/bin/env python3
"""
Step 1 -- reproduce the in-sample 2026 edge table, then decompose it and
measure the floor's MARGINAL effect OVER KELLY SIZING.

The operator's table was flat-stake.  Kelly already refuses negative-edge
bets and already stakes thin-edge bets small, so a flat-stake sweep
massively overstates what an explicit floor would add.  This script
reports both, side by side, so the gap is visible.
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.edge_floor.common import (load_2026, payout, implied,  # noqa: E402
                                     passes_lambda_floor)

ROOT = Path(__file__).resolve().parent.parent.parent
START = 100.0
FLOORS = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.16]


def hdr(s):
    print("\n" + "=" * 96)
    print("  " + s)
    print("=" * 96)


def build_bets(rows, probs, gate, lam_key, require_price=True):
    """Games the LIVE rule fires on, with edge recomputed from scratch."""
    out = []
    for r, p in zip(rows, probs):
        if p is None:
            continue
        if not passes_lambda_floor(r, lam_key):
            continue
        if p >= gate:
            continue
        odds = r["yrfi_odds"]
        if odds is None and require_price:
            continue
        p_y = 1.0 - p
        out.append({
            "date": r["date"], "game": r["game"], "rid": r["rid"],
            "p_y": p_y, "odds": odds,
            "imp": implied(odds) if odds is not None else None,
            "edge": (p_y - implied(odds)) if odds is not None else None,
            "win": r["yrfi_hit"], "edge_stored": r.get("edge_stored"),
        })
    return out


def flat(bets):
    n = len(bets)
    if not n:
        return dict(n=0, hit=float("nan"), need=float("nan"), roi=float("nan"), pl=0.0)
    w = sum(b["win"] for b in bets)
    pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)
    return dict(n=n, hit=100 * w / n, need=100 * st.mean([b["imp"] for b in bets]),
                roi=100 * pl / n, pl=pl)


def kelly_run(bets, frac=0.25, start=START):
    """Replay the SHIPPED staking: quarter Kelly, per-bet + per-day caps."""
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    n = w = 0
    staked = 0.0
    old = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    detail = []
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p_y"], str(int(b["odds"])), game_date=d) or 0.0
                if s <= 0:
                    detail.append((b, 0.0, 0.0))
                    continue
                n += 1
                w += b["win"]
                staked += s
                g = s * payout(b["odds"]) if b["win"] else -s
                pnl += g
                detail.append((b, s, g))
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
    finally:
        tracker.KELLY_FRACTION = old
    return dict(n=n, w=w, final=bank, profit=bank - start, maxdd=100 * mdd,
                staked=staked, detail=detail)


def boot_flat_roi(bets, iters=4000, seed=7):
    """Block bootstrap over DAYS -> ROI percentiles."""
    if not bets:
        return (float("nan"),) * 3
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
        out.append(100 * pl / n if n else 0.0)
    out.sort()
    return out[int(.025 * iters)], out[int(.5 * iters)], out[int(.975 * iters)]


def boot_diff(bets_all, floor, iters=4000, seed=11):
    """Block bootstrap of the DIFFERENCE in flat ROI (floored - unfloored).

    Resamples days once and evaluates both policies on the same resample,
    so the CI is on the marginal effect, not on two independent totals.
    """
    byday = defaultdict(list)
    for b in bets_all:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        na = pa = nb = pb = 0.0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                g = payout(b["odds"]) if b["win"] else -1.0
                na += 1
                pa += g
                if b["edge"] >= floor:
                    nb += 1
                    pb += g
        ra = 100 * pa / na if na else 0.0
        rb = 100 * pb / nb if nb else 0.0
        out.append(rb - ra)
    out.sort()
    return out[int(.025 * iters)], out[int(.5 * iters)], out[int(.975 * iters)]


def main():
    rows, skipped = load_2026()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    probs = [cal.predict(r["raw"]) for r in rows]
    gate = P._LR_STRONG_YRFI_P

    hdr("SETUP")
    print(f"  graded 2026 rows          : {len(rows)} (skipped {skipped})")
    print(f"  live STRONG YRFI gate     : p_nrfi < {gate}")
    print(f"  live lambda floor (base)  : {P._LR_LAMBDA_YRFI_FLOOR}, weather-adjusted")
    print(f"  calibrator                : {cal.train_seasons}, n={cal.train_n}  "
          "<-- IN-SAMPLE for 2026")
    print(f"  staking                   : {tracker.KELLY_FRACTION:.2f} Kelly, "
          f"{tracker.KELLY_MAX_STAKE_FRAC:.0%}/bet, {tracker.KELLY_MAX_DAILY_FRAC:.0%}/day")

    for lam_key, label in (("lam_csv", "stored lambda_lr_total"),
                           ("lam_recon", "reconstructed -ln(raw)")):
        b = build_bets(rows, probs, gate, lam_key)
        print(f"  bet population, {label:<24}: {len(b)} priced bets")

    LAM = "lam_csv"
    bets = build_bets(rows, probs, gate, LAM)

    hdr("STALE edge_on_pick CHECK  (recomputed vs stored)")
    bad = [b for b in bets if b["edge_stored"] is not None
           and abs(b["edge_stored"] - b["edge"]) > 0.005]
    print(f"  priced bets                       : {len(bets)}")
    print(f"  stored edge_on_pick disagrees >0.5pp: {len(bad)}")
    if bad:
        print(f"  worst disagreement                : "
              f"{max(abs(b['edge_stored']-b['edge']) for b in bad):.4f}")
    print("  -> all numbers below use the RECOMPUTED edge.")

    hdr("A. FLAT-STAKE EDGE SWEEP  (reproduces the operator's table)")
    print(f"  {'edge >=':>9}{'bets':>7}{'hit%':>8}{'need%':>8}{'ROI%':>8}"
          f"{'flat P&L':>11}{'ROI 95% CI (block boot over days)':>40}")
    for f in FLOORS:
        sel = [b for b in bets if b["edge"] >= f]
        s = flat(sel)
        lo, md, hi = boot_flat_roi(sel)
        print(f"  {f:>9.2f}{s['n']:>7}{s['hit']:>8.1f}{s['need']:>8.1f}"
              f"{s['roi']:>8.1f}{s['pl']:>+11.2f}u    [{lo:>+6.1f}%, {hi:>+6.1f}%]")

    hdr("B. DECOMPOSITION -- is the edge signal coming from the MODEL or the PRICE?")
    print("  edge = model_p - implied_p.  A monotone edge curve can be driven by")
    print("  either term.  Only the MODEL half can possibly replicate on 2025,")
    print("  which has no prices.  Split them.\n")
    qs = [0, .2, .4, .6, .8, 1.0]

    def band(key, name, fmt):
        vals = sorted(b[key] for b in bets)
        cuts = [vals[min(int(q * (len(vals) - 1)), len(vals) - 1)] for q in qs]
        print(f"  by {name} (quintiles):")
        print(f"    {'range':>18}{'bets':>7}{'hit%':>8}{'need%':>8}{'ROI%':>8}")
        for i in range(5):
            lo, hi = cuts[i], cuts[i + 1]
            sel = [b for b in bets if (lo <= b[key] <= hi if i == 4 else lo <= b[key] < hi)]
            s = flat(sel)
            print(f"    {fmt.format(lo, hi):>18}{s['n']:>7}{s['hit']:>8.1f}"
                  f"{s['need']:>8.1f}{s['roi']:>8.1f}")
        print()

    band("p_y", "MODEL prob of YRFI", "{:.3f}-{:.3f}")
    band("imp", "PRICE implied prob", "{:.3f}-{:.3f}")
    band("edge", "EDGE", "{:+.3f}-{:+.3f}")

    hdr("C. THE CRUX -- marginal effect of the floor OVER SHIPPED KELLY STAKING")
    base = kelly_run(bets)
    print(f"  {'edge floor':>11}{'bets':>7}{'wins':>6}{'staked':>10}{'final':>10}"
          f"{'profit':>10}{'maxDD':>8}{'vs no floor':>13}")
    print(f"  {'(none)':>11}{base['n']:>7}{base['w']:>6}{base['staked']:>10.1f}"
          f"{base['final']:>10.2f}{base['profit']:>+10.2f}{base['maxdd']:>8.1f}"
          f"{'--':>13}")
    for f in FLOORS[1:]:
        sel = [b for b in bets if b["edge"] >= f]
        k = kelly_run(sel)
        print(f"  {f:>11.2f}{k['n']:>7}{k['w']:>6}{k['staked']:>10.1f}"
              f"{k['final']:>10.2f}{k['profit']:>+10.2f}{k['maxdd']:>8.1f}"
              f"{k['profit']-base['profit']:>+13.2f}")

    hdr("D. WHAT DOES KELLY ALREADY DO TO THE LOW-EDGE TAIL?")
    print("  If Kelly already stakes the thin-edge bets near zero, removing them")
    print("  cannot matter much.  Actual stakes assigned, by edge band:\n")
    dstake = {}
    for b, s, g in base["detail"]:
        dstake[(b["rid"])] = (s, g)
    bands = [(-9, 0.0), (0.0, 0.02), (0.02, 0.04), (0.04, 0.08), (0.08, 9)]
    print(f"    {'edge band':>16}{'bets':>7}{'staked u':>11}{'% of all stake':>16}"
          f"{'P&L u':>10}{'mean stake':>12}")
    tot = base["staked"]
    for lo, hi in bands:
        sel = [b for b in bets if lo <= b["edge"] < hi]
        ss = [dstake.get(b["rid"], (0, 0)) for b in sel]
        stk = sum(x[0] for x in ss)
        pl = sum(x[1] for x in ss)
        nz = sum(1 for x in ss if x[0] > 0)
        lbl = f"{lo:+.2f}..{hi:+.2f}" if lo > -9 else f"< 0.00"
        if hi > 8:
            lbl = f">= {lo:+.2f}"
        print(f"    {lbl:>16}{len(sel):>7}{stk:>11.1f}{100*stk/tot if tot else 0:>15.1f}%"
              f"{pl:>+10.2f}{(stk/nz if nz else 0):>12.2f}")

    hdr("E. BOOTSTRAP ON THE DIFFERENCE  (flat ROI: floored - unfloored, same days)")
    print("  A floor is only real if the DIFFERENCE excludes zero, not if the")
    print("  floored subset's own CI excludes zero (that is a selection artefact).\n")
    print(f"  {'floor':>8}{'bets kept':>11}{'d-ROI median':>15}{'95% CI on difference':>28}")
    for f in FLOORS[1:]:
        sel = [b for b in bets if b["edge"] >= f]
        lo, md, hi = boot_diff(bets, f)
        print(f"  {f:>8.2f}{len(sel):>11}{md:>+15.1f}%      [{lo:>+6.1f}%, {hi:>+6.1f}%]")

    print(f"\n  THRESHOLDS SEARCHED IN THIS SCRIPT: {len(FLOORS)} floors, "
          "all on the same 2026 sample the hypothesis came from.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Step 3 -- THE DECIDING TEST: walk-forward edge floor.

At every date d:
    calibrator  = CIR fit on ALL 2025 games + every 2026 game BEFORE d.
                  Nothing from d or later.
    edge floor  = whichever candidate floor maximised realised P&L on
                  bets SETTLED BEFORE d.  Ties and thin samples fall back
                  to the incumbent (no floor).
    apply blind to d's bets, stake with the SHIPPED quarter-Kelly helper.

Then compare, on the IDENTICAL walk-forward bet stream:
    NO FLOOR (incumbent)   vs   WALK-FORWARD-CHOSEN FLOOR
    ... and, for reference only, each FIXED floor with hindsight.

A floor that only wins in the hindsight row is not shippable.
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
from calibration import CIRCalibrator  # noqa: E402
from tools.edge_floor.common import (load_2026, load_backtest, payout,  # noqa: E402
                                     implied, passes_lambda_floor)

START = 100.0
CANDIDATES = [0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12]
MIN_SETTLED = 30       # need this many settled bets before trusting a choice
LAM = "lam_csv"


def hdr(s):
    print("\n" + "=" * 92)
    print("  " + s)
    print("=" * 92)


def kelly_stream(bets, frac=0.25, start=START):
    """bets: chronological list of dicts with date/p_y/odds/win/edge/floor."""
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    n = w = 0
    old = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p_y"], str(int(b["odds"])),
                                              game_date=d) or 0.0
                if s <= 0:
                    continue
                n += 1
                w += b["win"]
                pnl += s * payout(b["odds"]) if b["win"] else -s
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
            if bank <= 0:
                break
    finally:
        tracker.KELLY_FRACTION = old
    hit = 100 * w / n if n else float("nan")
    return dict(n=n, w=w, hit=hit, final=bank, profit=bank - start, maxdd=100 * mdd)


def flat_pl(bets):
    return sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)


def main():
    r26, _ = load_2026()
    r25, _ = load_backtest(2025)
    gate = P._LR_STRONG_YRFI_P

    hdr("WALK-FORWARD CONSTRUCTION")
    dates = sorted({r["date"] for r in r26})
    idx_by_date = defaultdict(list)
    for i, r in enumerate(r26):
        idx_by_date[r["date"]].append(i)

    base_raw = [r["raw"] for r in r25]
    base_y = [r["y_nrfi"] for r in r25]

    wf = [None] * len(r26)
    for d in dates:
        prior = [i for i in range(len(r26)) if r26[i]["date"] < d]
        cal = CIRCalibrator.fit(base_raw + [r26[i]["raw"] for i in prior],
                                base_y + [r26[i]["y_nrfi"] for i in prior],
                                20, ["wf"])
        for i in idx_by_date[d]:
            wf[i] = cal.predict(r26[i]["raw"])
    print(f"  2026 dates walked : {len(dates)}")
    print(f"  calibrator base   : all {len(r25)} 2025 games + prior 2026 games")
    print("  (so even the first 2026 date has a real, leak-free calibrator)")

    # the walk-forward bet stream the LIVE rule produces
    stream = []
    for r, p in zip(r26, wf):
        if p is None or p >= gate:
            continue
        if not passes_lambda_floor(r, LAM):
            continue
        if r["yrfi_odds"] is None:
            continue
        stream.append({"date": r["date"], "p_y": 1 - p, "odds": r["yrfi_odds"],
                       "imp": implied(r["yrfi_odds"]),
                       "edge": (1 - p) - implied(r["yrfi_odds"]),
                       "win": r["yrfi_hit"]})
    stream.sort(key=lambda b: b["date"])
    print(f"  walk-forward bet stream (real prices only): {len(stream)} bets on "
          f"{len({b['date'] for b in stream})} days")
    if len(stream) < 20:
        print("  STREAM TOO SMALL -- nothing to decide on.")
        return 0

    hdr("A. WALK-FORWARD FLOOR SELECTION -- floor chosen from PRIOR bets only")
    chosen = []
    kept = []
    picks_log = defaultdict(int)
    for d in sorted({b["date"] for b in stream}):
        settled = [b for b in stream if b["date"] < d]
        f = 0.0
        if len(settled) >= MIN_SETTLED:
            best, bestv = 0.0, None
            for c in CANDIDATES:
                s = [b for b in settled if b["edge"] >= c]
                if len(s) < 15:
                    continue
                v = flat_pl(s) / len(s)          # realised ROI so far
                if bestv is None or v > bestv + 1e-12:
                    best, bestv = c, v
            f = best
        picks_log[f] += 1
        chosen.append((d, f))
        for b in stream:
            if b["date"] == d and b["edge"] >= f:
                kept.append(b)
    print("  floor the walk-forward selector chose, by number of days:")
    for f in sorted(picks_log):
        print(f"    {f:.2f} -> {picks_log[f]:>3} days")

    no_floor = kelly_stream(stream)
    wf_floor = kelly_stream(kept)
    print(f"\n  {'policy':<40}{'bets':>6}{'hit%':>8}{'final':>10}{'profit':>10}{'maxDD':>8}")
    print(f"  {'INCUMBENT (no edge floor)':<40}{no_floor['n']:>6}{no_floor['hit']:>8.1f}"
          f"{no_floor['final']:>10.2f}{no_floor['profit']:>+10.2f}{no_floor['maxdd']:>8.1f}")
    print(f"  {'WALK-FORWARD-CHOSEN FLOOR':<40}{wf_floor['n']:>6}{wf_floor['hit']:>8.1f}"
          f"{wf_floor['final']:>10.2f}{wf_floor['profit']:>+10.2f}{wf_floor['maxdd']:>8.1f}")
    print(f"  {'DIFFERENCE':<40}{wf_floor['n']-no_floor['n']:>6}"
          f"{wf_floor['hit']-no_floor['hit']:>+8.1f}{'':>10}"
          f"{wf_floor['profit']-no_floor['profit']:>+10.2f}")

    hdr("B. FIXED FLOORS ON THE SAME WALK-FORWARD STREAM (hindsight -- reference only)")
    print(f"  {'floor':>8}{'bets':>7}{'hit%':>8}{'flat P&L':>11}{'kelly final':>13}"
          f"{'kelly profit':>14}{'vs no floor':>13}")
    for c in CANDIDATES:
        s = [b for b in stream if b["edge"] >= c]
        k = kelly_stream(s)
        print(f"  {c:>8.2f}{len(s):>7}{k['hit']:>8.1f}{flat_pl(s):>+11.2f}"
              f"{k['final']:>13.2f}{k['profit']:>+14.2f}"
              f"{k['profit']-no_floor['profit']:>+13.2f}")
    print(f"\n  Floors searched here: {len(CANDIDATES)}.  Combined with run_2026's 11,")
    print("  the total threshold search across this investigation is 18.")

    hdr("C. BLOCK BOOTSTRAP over DAYS on the walk-forward DIFFERENCE (flat ROI)")
    byday = defaultdict(list)
    for b in stream:
        byday[b["date"]].append(b)
    days = list(byday)
    fl_of = dict(chosen)
    rng = random.Random(3)
    diffs = []
    for _ in range(4000):
        na = pa = nb = pb = 0.0
        for _ in range(len(days)):
            d = rng.choice(days)
            for b in byday[d]:
                g = payout(b["odds"]) if b["win"] else -1.0
                na += 1
                pa += g
                if b["edge"] >= fl_of.get(d, 0.0):
                    nb += 1
                    pb += g
        diffs.append((100 * pb / nb if nb else 0.0) - (100 * pa / na if na else 0.0))
    diffs.sort()
    lo, md, hi = diffs[100], diffs[2000], diffs[3900]
    frac_pos = sum(1 for x in diffs if x > 0) / len(diffs)
    print(f"  d-ROI (walk-forward floor minus no floor)")
    print(f"    median      : {md:+.2f} percentage points")
    print(f"    95% CI      : [{lo:+.2f}, {hi:+.2f}]")
    print(f"    P(floor > no floor) = {100*frac_pos:.1f}%")
    print("  A CI straddling zero means the walk-forward floor is indistinguishable")
    print("  from doing nothing.")

    hdr("D. MONTH SPLIT -- is any gain concentrated in one month?")
    print(f"  {'month':<10}{'bets':>6}{'no-floor flat':>15}{'wf-floor flat':>15}{'delta':>10}")
    bym_a = defaultdict(list)
    bym_b = defaultdict(list)
    for b in stream:
        bym_a[b["date"][:7]].append(b)
    for b in kept:
        bym_b[b["date"][:7]].append(b)
    for m in sorted(bym_a):
        a = flat_pl(bym_a[m])
        bb = flat_pl(bym_b.get(m, []))
        print(f"  {m:<10}{len(bym_a[m]):>6}{a:>+15.2f}{bb:>+15.2f}{bb-a:>+10.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

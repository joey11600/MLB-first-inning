#!/usr/bin/env python3
"""
tools/edge_floor/walkforward.py -- ANALYSIS ONLY.

The deciding test.  Choose the edge floor at each date using ONLY bets that
had already settled before that date, apply it blind to that date's slate,
accumulate.  A floor that only works when picked with hindsight is not
shippable.

Also runs three controls the in-sample sweep cannot distinguish:
  * REPRODUCE the operator's 495-bet table, to show which population it
    came from (it is NOT the population the live rule bets).
  * MATCHED-COUNT probability gate: with a roughly constant price, an edge
    floor is arithmetically just a stricter p threshold.  If a p-gate that
    keeps the same number of bets does as well, the floor is buying nothing
    that tightening the existing gate would not.
  * MONTH SPLIT + price-fill sensitivity.
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402
from tools.edge_floor.crux import add_edge, apply_floor, simulate, summary, START, FLOORS  # noqa: E402


def wf_simulate(bets, chooser, frac=0.25, start=START, min_prior=20):
    """Per-date floor chosen from strictly prior SETTLED bets only."""
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    settled = []
    staked = []
    picks = []
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            floor = chooser(settled) if len(settled) >= min_prior else 0.0
            picks.append((d, floor))
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                if b["edge"] < floor:
                    settled.append(b)          # still observed, just not bet
                    continue
                s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])), game_date=d) or 0.0
                settled.append(b)
                if s <= 0:
                    continue
                rec = dict(b)
                rec["stake"] = s
                rec["pnl"] = s * payout(b["odds"]) if b["win"] else -s
                staked.append(rec)
                pnl += rec["pnl"]
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
    finally:
        tracker.KELLY_FRACTION = o
    return {"bank": bank, "profit": bank - start, "mdd": 100 * mdd,
            "curve": [], "staked": staked, "picks": picks}


def make_chooser(kind, min_n=15):
    def choose(prior):
        best, bestv = 0.0, None
        for f in FLOORS:
            g = [b for b in prior if b["edge"] >= f]
            if len(g) < min_n:
                continue
            pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in g)
            v = pl / len(g) if kind == "roi" else pl
            if bestv is None or v > bestv:
                best, bestv = f, v
        return best
    return choose


def main():
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    gate = P._LR_STRONG_YRFI_P
    bets = add_edge(select(rows, wf, side="YRFI", gate=gate, fill=None))
    bets.sort(key=lambda b: b["date"])

    base = simulate(bets)
    b0 = summary(base)

    print("=" * 100)
    print("  WALK-FORWARD -- floor chosen from PRIOR settled bets only, applied blind")
    print("=" * 100)
    print(f"  incumbent (no floor):  {b0['n']} bets, {b0['w']}W-{b0['l']}L, "
          f"bank {b0['bank']:.2f}u ({b0['profit']:+.2f}u)")
    print()
    print(f"  {'selection rule':<34}{'bets':>6}{'W':>4}{'L':>4}{'hit%':>7}"
          f"{'flat':>9}{'bank':>10}{'d vs inc':>10}{'maxDD':>7}")
    for kind, lbl in (("roi", "max prior ROI (>=15 prior bets)"),
                      ("pl", "max prior total P&L")):
        r = wf_simulate(bets, make_chooser(kind))
        s = summary(r)
        print(f"  {lbl:<34}{s['n']:>6}{s['w']:>4}{s['l']:>4}{s['hit']:>7.1f}"
              f"{s['flat']:>+8.2f}u{s['bank']:>9.2f}u"
              f"{s['profit']-b0['profit']:>+9.2f}u{s['mdd']:>6.1f}%")
        cnt = defaultdict(int)
        for _d, f in r["picks"]:
            cnt[f] += 1
        print(f"       floors it chose, by day: "
              + ", ".join(f"{f:.2f}x{n}" for f, n in sorted(cnt.items())))

    # fixed floors, walk-forward is irrelevant (no choice made) -- but show
    # the second-half-only result, i.e. apply the floor to data that was NOT
    # available when the in-sample curve was drawn.
    print()
    print("=" * 100)
    print("  PSEUDO-OUT-OF-SAMPLE:  fit the floor on the FIRST half of betting days,")
    print("  evaluate on the SECOND half (fixed floor, no re-choosing)")
    print("=" * 100)
    days = sorted({b["date"] for b in bets})
    cut = days[len(days) // 2]
    first = [b for b in bets if b["date"] < cut]
    second = [b for b in bets if b["date"] >= cut]
    print(f"  split at {cut}:  {len(first)} gated bets before, {len(second)} after")
    print(f"  {'floor':>6}{'H1 flat':>10}{'H1 bank':>10}   |{'H2 bets':>9}{'H2 hit%':>9}"
          f"{'H2 flat':>10}{'H2 bank':>10}{'H2 d vs inc':>13}")
    inc2 = summary(simulate(second))
    for f in FLOORS:
        s1 = summary(simulate(apply_floor(first, f)))
        s2 = summary(simulate(apply_floor(second, f)))
        print(f"  {f:>6.2f}{s1['flat']:>+9.2f}u{s1['bank']:>9.2f}u   |{s2['n']:>9}"
              f"{s2['hit']:>8.1f}%{s2['flat']:>+9.2f}u{s2['bank']:>9.2f}u"
              f"{s2['profit']-inc2['profit']:>+12.2f}u")
    h1_best = max(FLOORS, key=lambda f: summary(simulate(apply_floor(first, f)))["flat"] /
                  max(len(apply_floor(first, f)), 1))
    s2b = summary(simulate(apply_floor(second, h1_best)))
    print(f"\n  H1's best-ROI floor was {h1_best:.2f}.  Applied blind to H2 it returns "
          f"{s2b['profit']-inc2['profit']:+.2f}u vs the incumbent.")

    # ---- control: matched-count probability gate -------------------------
    print()
    print("=" * 100)
    print("  CONTROL -- is the floor doing anything a STRICTER p GATE would not?")
    print("=" * 100)
    print("  With a roughly constant price, edge = p_yrfi - implied(price) is just a")
    print("  shifted p threshold.  So: for each floor, find the p_yrfi threshold that")
    print("  keeps the SAME number of bets, and compare.")
    print(f"  {'floor':>6}{'n':>5}{'edge-floor bank':>18}{'matched p_yrfi':>16}"
          f"{'p-gate bank':>14}{'floor advantage':>17}")
    for f in FLOORS[1:]:
        keep = apply_floor(bets, f)
        if not keep:
            continue
        k = len(keep)
        ps = sorted((b["p"] for b in bets), reverse=True)
        thr = ps[k - 1] if k <= len(ps) else 0.0
        pg = [b for b in bets if b["p"] >= thr]
        se = summary(simulate(keep))
        sp = summary(simulate(pg))
        print(f"  {f:>6.2f}{k:>5}{se['bank']:>17.2f}u{thr:>16.3f}"
              f"{sp['bank']:>13.2f}u{se['bank']-sp['bank']:>+16.2f}u")

    # ---- reproduce the operator's table ---------------------------------
    print()
    print("=" * 100)
    print("  WHERE THE 495-BET IN-SAMPLE TABLE CAME FROM")
    print("=" * 100)
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    ins = [cal.predict(r["raw"]) for r in rows]
    allp = []
    for r, p in zip(rows, ins):
        if r["yrfi_odds"] is None:
            continue
        e = (1.0 - p) - implied(r["yrfi_odds"])
        allp.append({"date": r["date"], "p": 1.0 - p, "odds": r["yrfi_odds"],
                     "win": r["yrfi_hit"], "edge": e})
    print("  NO GATE -- every real-priced 2026 game, in-sample calibrator, bet YRFI:")
    print(f"  {'edge >=':>9}{'bets':>7}{'hit%':>8}{'need%':>8}{'ROI':>8}")
    for f in (0.00, 0.04, 0.08, 0.12, 0.16):
        g = [b for b in allp if b["edge"] >= f]
        if not g:
            continue
        w = sum(1 for b in g if b["win"])
        pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in g)
        nd = st.mean([implied(b["odds"]) for b in g])
        print(f"  {f:>9.2f}{len(g):>7}{100*w/len(g):>7.1f}%{100*nd:>7.1f}%"
              f"{100*pl/len(g):>+7.1f}%")
    gated = set()
    for b in bets:
        gated.add((b["date"], b["game"]))
    print(f"\n  That population is {len(allp)} games.  The LIVE RULE bets {len(bets)} of")
    print(f"  them.  The sweep's low-edge rows are dominated by games the gate never")
    print(f"  touches, so its monotone slope is mostly the GATE being rediscovered,")
    print(f"  not a new filter.")

    # ---- month split of the one surviving candidate ---------------------
    print()
    print("=" * 100)
    print("  MONTH SPLIT -- floor 0.04 (the only floor that beat the incumbent)")
    print("=" * 100)
    print(f"  {'month':<9}{'inc n':>7}{'inc flat':>11}{'0.04 n':>8}{'0.04 flat':>12}"
          f"{'flat delta':>12}")
    bym = defaultdict(list)
    for b in bets:
        bym[b["date"][:7]].append(b)
    for m in sorted(bym):
        g = bym[m]
        k = apply_floor(g, 0.04)
        f0 = sum(payout(b["odds"]) if b["win"] else -1.0 for b in g)
        f1 = sum(payout(b["odds"]) if b["win"] else -1.0 for b in k)
        print(f"  {m:<9}{len(g):>7}{f0:>+10.2f}u{len(k):>8}{f1:>+11.2f}u"
              f"{f1-f0:>+11.2f}u")

    # ---- price-fill sensitivity -----------------------------------------
    print()
    print("=" * 100)
    print("  PRICE-FILL SENSITIVITY -- does the verdict depend on dropping unpriced games?")
    print("  (a filled price is an ASSUMPTION; edge computed against it is fictional,")
    print("   so this is a robustness check only, not a profit claim)")
    print("=" * 100)
    print(f"  {'fill':<20}{'floor':>7}{'bets':>6}{'bank':>10}{'d vs inc':>11}")
    for fill, lbl in ((-110, "-110"), (-125, "-125"), (-140, "-140")):
        bb = add_edge(select(rows, wf, side="YRFI", gate=gate, fill=fill))
        bb.sort(key=lambda b: b["date"])
        i0 = summary(simulate(bb))
        print(f"  {lbl:<20}{'none':>7}{i0['n']:>6}{i0['bank']:>9.2f}u{0.0:>+10.2f}u")
        for f in (0.02, 0.04, 0.06, 0.08):
            s = summary(simulate(apply_floor(bb, f)))
            print(f"  {'':<20}{f:>7.2f}{s['n']:>6}{s['bank']:>9.2f}u"
                  f"{s['profit']-i0['profit']:>+10.2f}u")
    return 0


if __name__ == "__main__":
    sys.exit(main())

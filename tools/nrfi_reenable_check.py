#!/usr/bin/env python3
"""
tools/nrfi_reenable_check.py -- should STRONG NRFI betting be turned back on?

BACKGROUND.  STRONG NRFI was disabled 2026-06-07 (_LR_STRONG_NRFI_P = 1.01)
after a full rework concluded the NRFI prediction is SOUND but loses for
structural reasons: the market is efficient and one inning is close to a
coin flip.  Last NRFI bet placed 2026-06-14.

WHY RE-ASK NOW.  That decision was made under FLAT 1u staking, with the
old loose STRONG gate and the old plateaued calibrator.  All three have
since changed:
  * quarter Kelly (2026-07-27) stakes zero on any bet that does not beat
    the market's implied price, so a side that bleeds at flat 1u can come
    out neutral -- this is exactly what rescued the 0.60-0.64 YRFI band;
  * the STRONG gate tightened to p_nrfi < 0.36 on the YRFI side;
  * the calibrator lost its flat step (CIR, 2026-07-28), so probabilities
    -- which are what Kelly sizes on -- are no longer degenerate.
So the economics underneath the 6/07 decision are different, and the
question deserves a fresh answer rather than an appeal to the old one.

SELECTION BUG THIS SCRIPT ONCE HAD (fixed 2026-07-28, operator caught it).
The first version filtered on pick_strength == "STRONG" and evaluated 49
picks ending 2026-06-14.  But disabling NRFI set _LR_STRONG_NRFI_P = 1.01,
which no probability can exceed, so from that day the classifier stopped
emitting "STRONG NRFI" at all and the same games came out "LEAN NRFI".
The filter therefore discarded SIX WEEKS of live predictions -- 158 graded
games, 157 with real captured prices -- and judged re-enabling on a stale
sample.  Now selects on side + probability, giving 309 picks including 184
that are genuinely out-of-sample (we bet none of them, so none of our
money moved those lines).

Everything below uses REAL captured DK prices only.

Usage:
    python tools/nrfi_reenable_check.py
"""

from __future__ import annotations

import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

DISABLE_DATE = "2026-06-07"


def num(s):
    try:
        s = (s or "").strip().replace("−", "-")
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def load():
    out = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS") \
               and (r.get("actual_result") or "").upper() not in ("NRFI", "YRFI"):
                continue
            # DO NOT filter on pick_strength.  When NRFI betting was
            # disabled on 2026-06-07 the threshold was set to
            # _LR_STRONG_NRFI_P = 1.01, which no probability can exceed --
            # so from that day on the classifier stopped emitting
            # "STRONG NRFI" entirely and those same games came out as
            # "LEAN NRFI" instead.  Filtering on strength therefore drops
            # every NRFI prediction after 2026-06-14 (158 graded games,
            # 157 of them with a real captured price) and silently
            # evaluates re-enabling on a 6-week-stale sample.
            #
            # The strength LABEL changed meaning; the model's probability
            # did not.  Select on side + probability and re-derive the
            # strength ourselves at whatever gate is being tested.
            side = (r.get("pick_side") or "").upper()
            if side != "NRFI":
                continue
            odds = num(r.get("market_nrfi_odds"))
            p = num(r.get("nrfi_prob"))
            actual = (r.get("actual_result") or "").upper()
            if odds is None or p is None or actual not in ("NRFI", "YRFI"):
                continue
            out.append({
                "date": r["date"], "p": p, "odds": odds,
                "win": actual == "NRFI",
                "placed": (r.get("bet_placed") or "").upper() == "Y",
            })
    out.sort(key=lambda x: x["date"])
    return out


def flat(bets):
    if not bets:
        return 0, 0, 0.0, 0.0
    n = len(bets)
    w = sum(1 for b in bets if b["win"])
    pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)
    need = st.mean([implied(b["odds"]) for b in bets])
    return n, w, pl, need


def kelly(bets, frac=0.25, start=100.0):
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    bank = peak = start
    mdd = 0.0
    n = w = 0
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])), game_date=d) or 0.0
                if s <= 0:
                    continue
                n += 1
                if b["win"]:
                    w += 1
                    pnl += s * payout(b["odds"])
                else:
                    pnl -= s
            bank += pnl
            peak = max(peak, bank)
            mdd = max(mdd, (peak - bank) / peak if peak > 0 else 0)
    finally:
        tracker.KELLY_FRACTION = o
    return n, w, bank - start, 100 * mdd


def row(label, bets):
    n, w, pl, need = flat(bets)
    if n == 0:
        print(f"  {label:<34}{'0':>5}")
        return
    kn, kw, kpl, kdd = kelly(bets)
    print(f"  {label:<34}{n:>5}{100*w/n:>7.1f}%{100*need:>7.1f}%"
          f"{pl:>+9.2f}u{kn:>6}{kpl:>+10.2f}u{kdd:>7.1f}%")


def main():
    bets = load()
    print(f"Graded NRFI-side predictions with a real captured DK price: {len(bets)}")
    print(f"  {bets[0]['date']} .. {bets[-1]['date']}\n")

    print(f"  {'segment':<34}{'n':>5}{'hit%':>7}{'need':>7}{'flat 1u':>10}"
          f"{'K bets':>6}{'K P&L':>10}{'K maxDD':>7}")
    print("  " + "-" * 87)
    row("ALL graded NRFI predictions", bets)
    row("  ...that we actually BET", [b for b in bets if b["placed"]])
    row("  ...predicted but never bet", [b for b in bets if not b["placed"]])
    print()
    row(f"before {DISABLE_DATE} (betting era)", [b for b in bets if b["date"] < DISABLE_DATE])
    row(f"since {DISABLE_DATE} (all unbet)", [b for b in bets if b["date"] >= DISABLE_DATE])
    print()
    print("  The 'since' row is the 6 weeks the model kept predicting NRFI")
    print("  while we sat out -- the honest out-of-sample window, because")
    print("  no bet of ours could have moved those lines.")

    print("\n" + "=" * 92)
    print("  DOES THE MODEL BEAT THE MARKET ON NRFI?  (the only question that matters)")
    print("=" * 92)
    for lo, hi in ((0.50, 0.60), (0.60, 0.62), (0.62, 0.65), (0.65, 1.01)):
        g = [b for b in bets if lo <= b["p"] < hi]
        if len(g) < 8:
            continue
        n, w, pl, need = flat(g)
        kn, kw, kpl, kdd = kelly(g)
        edge = 100 * (w / n - need)
        print(f"  model p {lo:.2f}-{hi:.2f}: n={n:<4} hit {100*w/n:>5.1f}%  "
              f"market needs {100*need:>5.1f}%  edge {edge:>+5.1f}pp  "
              f"flat {pl:>+7.2f}u  Kelly {kpl:>+7.2f}u")

    print("\n" + "=" * 92)
    print("  IF WE RE-ENABLED AT VARIOUS THRESHOLDS (quarter Kelly, live caps)")
    print("=" * 92)
    print(f"  {'gate p_nrfi >=':<18}{'n':>5}{'hit%':>8}{'need':>8}{'flat 1u':>11}"
          f"{'Kelly':>11}{'maxDD':>8}")
    for th in (0.55, 0.58, 0.60, 0.62, 0.64, 0.66):
        g = [b for b in bets if b["p"] >= th]
        if len(g) < 8:
            continue
        n, w, pl, need = flat(g)
        kn, kw, kpl, kdd = kelly(g)
        print(f"  {th:<18.2f}{n:>5}{100*w/n:>7.1f}%{100*need:>7.1f}%"
              f"{pl:>+10.2f}u{kpl:>+10.2f}u{kdd:>7.1f}%")

    print("\n  Reminder: the 2026-06-07 rework concluded NRFI is sound as a")
    print("  PREDICTION but unprofitable as a BET because the market prices")
    print("  the first inning efficiently. Re-enabling needs evidence that")
    print("  survives out-of-sample, not a good-looking in-sample row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

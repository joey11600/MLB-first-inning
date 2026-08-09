#!/usr/bin/env python3
"""
tools/verify_refit.py -- independent check on whatever weekly_refit.py
just shipped, before we accept it.

WHY THIS EXISTS
---------------
`tools/weekly_refit.py` ships a candidate when it clears two tolerances:
P&L >= prod - 1.0u AND Brier <= prod + 0.005.  Those are ASYMMETRIC and
LOOSE -- a candidate that is measurably WORSE on both still passes.  The
2026-07-28 run is exactly that case: delta P&L +0.00u, delta Brier
+0.0037 (candidate worse).  It shipped a model that lost on the only
clean holdout, and the holdout was 93 games.

That is the same weakness that got the weekly cron disabled on
2026-05-11: "the GHA runner has no way to KNOW whether a given week's
refit is net-positive before shipping it."  So the refit passing its own
gate is not evidence.  This script asks the questions that matter:

  1. Is the new model actually better on the clean holdout, or just
     inside a tolerance?  Includes a bootstrap so we can tell a real
     difference from 93 games of noise.
  2. Does it change the bets Kelly would place, and the money?
  3. How far do the probabilities move?  Kelly stakes are a function of
     `p`, so a model that reorders games materially changes stake sizes
     even where the pick is unchanged.

Run with the candidate already in the production paths and the previous
files present as *.bak-<date> (which is what weekly_refit leaves behind).

Usage:
    python tools/verify_refit.py
"""

from __future__ import annotations

import glob
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from lr_baseline import LogReg  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.walk_forward_eval import (  # noqa: E402
    load_parks, load_picks_2026,
)

GATE_NRFI = 0.36     # STRONG YRFI when calibrated p_nrfi < this


def newest_bak(stem: str) -> Path | None:
    hits = sorted(glob.glob(str(ROOT / "data" / f"{stem}.bak-*")))
    return Path(hits[-1]) if hits else None


def load_pair(t1p, b1p, calp):
    return LogReg.load(str(t1p)), LogReg.load(str(b1p)), ProbCalibrator.load(calp)


def predict_nrfi(t1, b1, cal, rows):
    Xt = np.asarray([r["t1"] for r in rows])
    Xb = np.asarray([r["b1"] for r in rows])
    raw = (1 - t1.predict_proba(Xt)) * (1 - b1.predict_proba(Xb))
    return np.array([cal.predict(float(x)) for x in raw])


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def kelly_money(p_nrfi, rows, frac=0.25):
    """Re-decide + re-stake with the shipped Kelly helper."""
    byday = defaultdict(list)
    for p, r in zip(p_nrfi, rows):
        if p >= GATE_NRFI:
            continue
        odds = r.get("yrfi_odds")
        if odds is None:
            continue
        byday[r["date"]].append((1.0 - p, odds, r["y_nrfi"] == 0))
    bank = peak = 100.0
    mdd = 0.0
    n = w = 0
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            # T8.18: bump the batch epoch.  Seeding _daily_committed directly
            # skips kelly_reset_daily_committed(), and kelly_stake_units now
            # refuses to allocate (game_date=...) on a process that never reset.
            tracker.kelly_reset_daily_committed()
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for p, odds, win in byday[d]:
                s = tracker.kelly_stake_units(p, str(int(odds)), game_date=d) or 0.0
                if s <= 0:
                    continue
                n += 1
                b = odds / 100.0 if odds > 0 else 100.0 / abs(odds)
                if win:
                    w += 1
                    pnl += s * b
                else:
                    pnl -= s
            bank += pnl
            peak = max(peak, bank)
            mdd = max(mdd, (peak - bank) / peak if peak > 0 else 0)
    finally:
        tracker.KELLY_FRACTION = o
    return {"bets": n, "wins": w, "final": bank, "profit": bank - 100.0,
            "maxdd": 100 * mdd}


def main():
    t1b, b1b, calb = (newest_bak("lr_t1.json"), newest_bak("lr_b1.json"),
                      newest_bak("calibration_v2.json"))
    if not all([t1b, b1b, calb]):
        sys.exit("no .bak-* files found -- run tools/weekly_refit.py first")
    print(f"PREVIOUS: {t1b.name} / {b1b.name} / {calb.name}")
    print("CURRENT : data/lr_t1.json / lr_b1.json / calibration_v2.json\n")

    old = load_pair(t1b, b1b, calb)
    new = load_pair(ROOT / "data" / "lr_t1.json",
                    ROOT / "data" / "lr_b1.json",
                    ROOT / "data" / "calibration_v2.json")

    parks = load_parks()
    rows = load_picks_2026(parks)
    rows.sort(key=lambda r: r["date"])

    # Attach real captured YRFI odds so the money test is honest.
    import csv
    odds_by = {}
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            v = (r.get("market_yrfi_odds") or "").strip()
            if v:
                try:
                    odds_by[(r["date"], r["away_team"], r["home_team"])] = float(v)
                except ValueError:
                    pass
    for r in rows:
        r["yrfi_odds"] = odds_by.get((r["date"], r.get("away"), r.get("home")))

    HOLD_START, HOLD_END = "2026-07-21", "2026-07-27"
    hold = [r for r in rows if HOLD_START <= r["date"] <= HOLD_END]
    y_h = np.array([r["y_nrfi"] for r in hold])

    po = predict_nrfi(*old, hold)
    pn = predict_nrfi(*new, hold)

    print("=" * 88)
    print(f"  1. CLEAN HOLDOUT {HOLD_START}..{HOLD_END} ({len(hold)} games)")
    print("     -- the only window the NEW model has genuinely not seen")
    print("=" * 88)
    bo, bn = brier(po, y_h), brier(pn, y_h)
    print(f"  Brier  previous {bo:.5f}   new {bn:.5f}   delta {bn-bo:+.5f}"
          f"   -> {'NEW WORSE' if bn > bo else 'new better'}")

    # Is that delta distinguishable from noise on 93 games?
    rng = np.random.default_rng(7)
    diffs = []
    idx = np.arange(len(hold))
    for _ in range(4000):
        s = rng.choice(idx, len(idx), replace=True)
        diffs.append(brier(pn[s], y_h[s]) - brier(po[s], y_h[s]))
    diffs = np.sort(diffs)
    lo, hi = diffs[200], diffs[3800]
    print(f"  bootstrap 90% CI on the Brier delta: [{lo:+.5f}, {hi:+.5f}]")
    print(f"  -> {'INDISTINGUISHABLE FROM NOISE (CI spans 0)' if lo < 0 < hi else 'a real difference'}")

    print("\n" + "=" * 88)
    print("  2. DOES IT CHANGE WHAT WE BET?")
    print("=" * 88)
    allr = [r for r in rows if r.get("yrfi_odds") is not None]
    pao = predict_nrfi(*old, allr)
    pan = predict_nrfi(*new, allr)
    so = set(i for i, p in enumerate(pao) if p < GATE_NRFI)
    sn = set(i for i, p in enumerate(pan) if p < GATE_NRFI)
    print(f"  STRONG YRFI picks over {len(allr)} priced 2026 games:")
    print(f"    previous {len(so)}   new {len(sn)}   "
          f"added {len(sn-so)}   dropped {len(so-sn)}")
    print(f"  mean |probability change| {np.abs(pan-pao).mean():.4f}   "
          f"max {np.abs(pan-pao).max():.4f}")

    print("\n" + "=" * 88)
    print("  3. KELLY MONEY (1/4 Kelly, live caps, 2026 priced games)")
    print("     NOTE: in-sample for the NEW model (trained thru 07-20), so it")
    print("     is FLATTERED here. Treat section 1 as the real evidence.")
    print("=" * 88)
    ro, rn = kelly_money(pao, allr), kelly_money(pan, allr)
    print(f"  {'model':<12}{'bets':>6}{'W':>5}{'hit%':>8}{'final':>10}{'profit':>11}{'maxDD':>9}")
    for lbl, r in (("previous", ro), ("new", rn)):
        hit = 100 * r["wins"] / r["bets"] if r["bets"] else 0
        print(f"  {lbl:<12}{r['bets']:>6}{r['wins']:>5}{hit:>7.1f}%"
              f"{r['final']:>9.2f}u{r['profit']:>+10.2f}u{r['maxdd']:>8.1f}%")

    print("\n" + "=" * 88)
    print("  VERDICT")
    print("=" * 88)
    clean_win = bn < bo
    noise = lo < 0 < hi
    if clean_win and not noise:
        print("  SHIP -- new model is better on the clean holdout, beyond noise.")
    elif noise:
        print("  NO EVIDENCE EITHER WAY. The holdout difference is inside the")
        print("  noise band on 93 games. Shipping would perturb every live")
        print("  prediction -- and every Kelly stake -- for an unmeasurable")
        print("  gain. Default to KEEPING PRODUCTION and re-running when the")
        print("  holdout is larger.")
    else:
        print("  DO NOT SHIP -- new model is worse on the clean holdout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

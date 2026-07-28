#!/usr/bin/env python3
"""
tools/season_scenarios.py -- which configuration actually produced the
most profit over the 2026 season?

Builds on tools/season_replay.py (same loader, same shipped Kelly helper,
same weather-adjusted lambda floor) and sweeps the knobs that are
genuinely ours to choose:

    gate            p_nrfi < X          how selective the STRONG call is
    kelly fraction  flat / 1/8 / 1/4 / 1/2 / full
    per-bet cap     5% / 10% / 25%
    daily cap       10% / 15% / 25% / none
    lambda floor    scaled around the live 0.838

READ THE WARNING BEFORE ACTING ON THE WINNER.  A sweep this wide over
~1100 graded games WILL produce an impressive top row by chance alone.
Every scenario is therefore reported with:
    * flat-1u P&L alongside the Kelly figure -- if flat is near zero the
      profit is compounding, not edge, and it will not survive a cold run
    * max drawdown and the largest single stake in units
    * month-by-month sign, so a one-month wonder is visible
The last section re-runs the leaders WALK-FORWARD, refitting the
calibrator from strictly prior games, which is the only version that
answers "what would someone actually have made".

Usage:
    python tools/season_scenarios.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402

START = 100.0


def run(rows, probs, *, gate, frac, bet_cap, daily_cap, floor_scale=1.0):
    """One scenario. frac=None means flat 1u."""
    byday = defaultdict(list)
    for r, p_nrfi in zip(rows, probs):
        if p_nrfi is None:
            continue
        lam = r["lambda"]
        fl = P._weather_adjusted_floor(
            P._LR_LAMBDA_YRFI_FLOOR * floor_scale,
            r["wx_temp"], r["wx_wind"], r["wx_dome"])
        if lam is not None and lam < fl:
            continue
        if p_nrfi >= gate:
            continue
        if r["yrfi_odds"] is None:
            continue
        byday[r["date"]].append((1.0 - p_nrfi, r["yrfi_odds"], r["yrfi_hit"]))

    o_f, o_b, o_d = (tracker.KELLY_FRACTION, tracker.KELLY_MAX_STAKE_FRAC,
                     tracker.KELLY_MAX_DAILY_FRAC)
    if frac is not None:
        tracker.KELLY_FRACTION = frac
    tracker.KELLY_MAX_STAKE_FRAC = bet_cap
    tracker.KELLY_MAX_DAILY_FRAC = daily_cap

    bank = peak = START
    mdd = 0.0
    n = w = 0
    flat = 0.0
    top = 0.0
    monthly = defaultdict(float)
    try:
        for d in sorted(byday):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for p_y, odds, win in byday[d]:
                if frac is None:
                    s = 1.0
                else:
                    s = tracker.kelly_stake_units(p_y, str(int(odds)), game_date=d) or 0.0
                    if s <= 0:
                        continue
                n += 1
                w += win
                top = max(top, s)
                flat += payout(odds) if win else -1.0
                pnl += s * payout(odds) if win else -s
            bank += pnl
            monthly[d[:7]] += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
            if bank <= 0:
                break
    finally:
        tracker.KELLY_FRACTION, tracker.KELLY_MAX_STAKE_FRAC, \
            tracker.KELLY_MAX_DAILY_FRAC = o_f, o_b, o_d

    return {"bets": n, "wins": w, "final": bank, "profit": bank - START,
            "flat": flat, "maxdd": 100 * mdd, "top": top,
            "months_pos": sum(1 for v in monthly.values() if v > 0),
            "months": len(monthly), "monthly": dict(monthly)}


def row(label, r):
    hit = 100 * r["wins"] / r["bets"] if r["bets"] else 0
    print(f"  {label:<34}{r['bets']:>5}{hit:>7.1f}%{r['flat']:>+9.2f}u"
          f"{r['profit']:>+11.2f}u{r['maxdd']:>8.1f}%{r['top']:>8.1f}u"
          f"{r['months_pos']}/{r['months']:>4}")


HDR = (f"  {'scenario':<34}{'bets':>5}{'hit%':>7}{'flat 1u':>10}"
       f"{'Kelly P&L':>12}{'maxDD':>8}{'top bet':>8}{'mo+':>6}")


def main():
    rows, _ = load_season()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    probs = [cal.predict(r["raw"]) for r in rows]
    live = dict(gate=P._LR_STRONG_YRFI_P, frac=0.25, bet_cap=0.10, daily_cap=0.15)

    print("=" * 104)
    print("  SCENARIO SWEEP -- 2026 season, real captured prices only")
    print("  IN-SAMPLE: calibrator has seen these games and the gate was chosen on them.")
    print("=" * 104)

    print("\n--- 1. GATE (staking held at the live 1/4 Kelly, 10%/15% caps) ---")
    print(HDR)
    for g in (0.30, 0.33, 0.36, 0.40, 0.44):
        tag = "  <-- LIVE" if abs(g - 0.36) < 1e-9 else ""
        row(f"gate p_nrfi < {g:.2f}{tag}", run(rows, probs, **{**live, "gate": g}))

    print("\n--- 2. STAKING (gate held at the live 0.36) ---")
    print(HDR)
    for frac, lbl in ((None, "flat 1u"), (0.125, "1/8 Kelly"),
                      (0.25, "1/4 Kelly  <-- LIVE"), (0.5, "1/2 Kelly"),
                      (1.0, "full Kelly")):
        row(lbl, run(rows, probs, **{**live, "frac": frac}))

    print("\n--- 3. CAPS (gate 0.36, 1/4 Kelly) ---")
    print(HDR)
    for bc in (0.05, 0.10, 0.25):
        for dc in (0.10, 0.15, 0.25, 1.0):
            tag = "  <-- LIVE" if (bc == 0.10 and dc == 0.15) else ""
            d = "none" if dc >= 1.0 else f"{dc:.0%}"
            row(f"bet {bc:.0%} / day {d}{tag}",
                run(rows, probs, **{**live, "bet_cap": bc, "daily_cap": dc}))

    print("\n--- 4. LAMBDA FLOOR (gate 0.36, 1/4 Kelly) ---")
    print(HDR)
    for sc in (0.90, 0.95, 1.00, 1.05, 1.10):
        tag = "  <-- LIVE" if sc == 1.0 else ""
        row(f"floor x{sc:.2f} = {P._LR_LAMBDA_YRFI_FLOOR*sc:.3f}{tag}",
            run(rows, probs, **{**live, "floor_scale": sc}))

    # ---------------- walk-forward on the leaders ----------------------
    print("\n" + "=" * 104)
    print("  WALK-FORWARD -- calibrator refit from strictly PRIOR games at each date")
    print("  This is the only version that answers 'what would we actually have made'.")
    print("=" * 104)
    dates = sorted({r["date"] for r in rows})
    idx = defaultdict(list)
    for i, r in enumerate(rows):
        idx[r["date"]].append(i)
    wf = [None] * len(rows)
    MIN_TRAIN = 200
    for d in dates:
        prior = [i for i in range(len(rows)) if rows[i]["date"] < d]
        if len(prior) < MIN_TRAIN:
            continue
        c = CIRCalibrator.fit([rows[i]["raw"] for i in prior],
                              [rows[i]["y_nrfi"] for i in prior], 20, ["wf"])
        for i in idx[d]:
            wf[i] = c.predict(rows[i]["raw"])

    print(HDR)
    for g in (0.30, 0.33, 0.36, 0.40):
        for frac, fl in ((0.125, "1/8K"), (0.25, "1/4K"), (0.5, "1/2K")):
            row(f"WF gate {g:.2f} {fl}",
                run(rows, wf, **{**live, "gate": g, "frac": frac}))
        row(f"WF gate {g:.2f} flat",
            run(rows, wf, **{**live, "gate": g, "frac": None}))

    print("\n" + "=" * 104)
    print("  HOW TO PICK A WINNER FROM THIS")
    print("=" * 104)
    print("  * Compare the flat-1u column to the Kelly column. Where flat is near")
    print("    zero, the profit is COMPOUNDING, not edge -- it evaporates on a cold")
    print("    run and cannot be relied on.")
    print("  * 'top bet' is the largest single stake in units. A configuration that")
    print("    earns more but stakes 40u on one first inning is not obviously better.")
    print("  * 'mo+' is months with positive P&L. A 1/4 that made everything in one")
    print("    month is a coin that landed well, not an edge.")
    print("  * Prefer a WALK-FORWARD row over any in-sample row, always.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

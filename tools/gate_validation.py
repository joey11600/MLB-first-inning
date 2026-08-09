#!/usr/bin/env python3
"""
tools/gate_validation.py -- is the STRONG gate at the right place, on
BOTH sides, and does the answer survive a price assumption?

WHY NOT A LITERAL 3-SPLIT.  CLAUDE.md's 3-split rule (2024->2025,
2025->2024, 2024+2025->2026) validates a TRAINING PROCEDURE: refit the
model on one season, score it on another.  A gate threshold is not
fitted from training data -- it is a policy choice applied on top of a
fitted model, and evaluating it needs BETTING PRICES, which only exist
for 2026.  Running a 3-split here would look rigorous and answer a
different question.  The tests that actually apply are:

    * WALK-FORWARD    calibrator refit from strictly prior games
    * BLOCK BOOTSTRAP resampled over DAYS, not bets, because a slate
                      settles together and per-bet resampling would
                      understate correlation
    * MONTH SPLIT     a gate that made everything in one month is a coin
                      that landed well

MISSING PRICES.  404 of 1531 graded games never had a DK price captured,
and they are disproportionately April.  Dropping them biases the sample
toward the second half of the season; filling them silently at -110 is
what inflated the season total in the first place.  So this script fills
them at an EXPLICIT, LABELLED assumption and reports -110 / -125 / -140
side by side, so the reader can see how much the assumption is doing.
The real-prices-only column is always shown alongside.

BOTH SIDES.  NRFI is disabled live (_LR_STRONG_NRFI_P = 1.01), but the
operator asked to re-examine it with the fill applied, since the missing
prices were one reason the earlier NRFI sample was thin.

Usage:
    python tools/gate_validation.py
"""

from __future__ import annotations

import random
import statistics as st
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


def select(rows, probs, *, side, gate, fill):
    """Games this (side, gate) would bet. fill=None -> real prices only."""
    out = []
    for r, p in zip(rows, probs):
        if p is None:
            continue
        if side == "YRFI":
            fl = P._weather_adjusted_floor(
                P._LR_LAMBDA_YRFI_FLOOR, r["wx_temp"], r["wx_wind"], r["wx_dome"])
            if r["lambda"] is not None and r["lambda"] < fl:
                continue
            if p >= gate:
                continue
            odds = r["yrfi_odds"]
            win = r["yrfi_hit"]
        else:                      # NRFI: fires when p_nrfi is HIGH
            if p < gate:
                continue
            ceil = P._LR_LAMBDA_NRFI_CEILING
            if r["lambda"] is not None and r["lambda"] > ceil:
                continue
            odds = r["nrfi_odds"]
            win = not r["yrfi_hit"]
        missing = odds is None
        if missing:
            if fill is None:
                continue
            odds = float(fill)
        # game/side/assumed added 2026-07-28 for the dashboard's day-by-day
        # drill-down (export_season_record.py). Additive keys only -- every
        # existing consumer reads date/p/odds/win and ignores the rest.
        out.append({"date": r["date"], "p": (1.0 - p) if side == "YRFI" else p,
                    "odds": odds, "win": win,
                    "game": f"{r['away']}@{r['home']}", "side": side,
                    "assumed": missing})
    return out


def kelly_sim(bets, frac=0.25, start=START):
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
            # T8.18: bump the batch epoch.  Seeding _daily_committed directly
            # skips kelly_reset_daily_committed(), and kelly_stake_units now
            # refuses to allocate (game_date=...) on a process that never reset.
            tracker.kelly_reset_daily_committed()
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for b in byday[d]:
                s = tracker.kelly_stake_units(b["p"], str(int(b["odds"])), game_date=d) or 0.0
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
        tracker.KELLY_FRACTION = o
    return {"bets": n, "wins": w, "profit": bank - start, "maxdd": 100 * mdd}


def flat_stats(bets):
    if not bets:
        return 0, 0, 0.0, float("nan")
    n = len(bets)
    w = sum(1 for b in bets if b["win"])
    pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)
    return n, w, pl, st.mean([implied(b["odds"]) for b in bets])


def block_bootstrap_flat(bets, iters=4000, seed=5):
    """Resample DAYS with replacement; returns ROI percentiles."""
    if not bets:
        return float("nan"), float("nan")
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
        if n:
            out.append(100 * pl / n)
    out.sort()
    return out[int(0.05 * len(out))], out[int(0.95 * len(out))]


def walk_forward_probs(rows, min_train=200):
    dates = sorted({r["date"] for r in rows})
    idx = defaultdict(list)
    for i, r in enumerate(rows):
        idx[r["date"]].append(i)
    wf = [None] * len(rows)
    for d in dates:
        prior = [i for i in range(len(rows)) if rows[i]["date"] < d]
        if len(prior) < min_train:
            continue
        c = CIRCalibrator.fit([rows[i]["raw"] for i in prior],
                              [rows[i]["y_nrfi"] for i in prior], 20, ["wf"])
        for i in idx[d]:
            wf[i] = c.predict(rows[i]["raw"])
    return wf


def report(label, bets):
    n, w, pl, need = flat_stats(bets)
    if n == 0:
        print(f"  {label:<30}{'0':>5}")
        return
    k = kelly_sim(bets)
    lo, hi = block_bootstrap_flat(bets)
    mark = "  CI>0" if lo > 0 else ""
    bym = defaultdict(float)
    for b in bets:
        bym[b["date"][:7]] += payout(b["odds"]) if b["win"] else -1.0
    pos = sum(1 for v in bym.values() if v > 0)
    print(f"  {label:<30}{n:>5}{100*w/n:>7.1f}%{100*need:>7.1f}%{pl:>+9.2f}u"
          f"{100*pl/n:>+7.1f}%  [{lo:>+6.1f},{hi:>+6.1f}]{mark:<6}"
          f"{k['profit']:>+9.2f}u{pos}/{len(bym)}")


HDR = (f"  {'scenario':<30}{'bets':>5}{'hit%':>7}{'need':>7}{'flat':>9}"
       f"{'ROI':>7}  {'bootstrap 90% CI':^17}{'Kelly':>9}{'mo+':>5}")


def main():
    rows, _ = load_season()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    ins = [cal.predict(r["raw"]) for r in rows]
    wf = walk_forward_probs(rows)

    print("=" * 116)
    print("  GATE VALIDATION -- both sides, with an explicit price fill for")
    print("  the 404 graded games that never had a captured DK number.")
    print("=" * 116)

    for fill, fl_label in ((None, "REAL PRICES ONLY"), (-125, "missing filled at -125"),
                           (-110, "missing filled at -110"), (-140, "missing filled at -140")):
        print(f"\n########  {fl_label}  ########")
        print("\n  --- YRFI (walk-forward calibrator) ---")
        print(HDR)
        for g in (0.44, 0.40, 0.36, 0.33):
            tag = "  <-- LIVE" if abs(g - 0.36) < 1e-9 else ""
            report(f"YRFI gate p_nrfi < {g:.2f}{tag}",
                   select(rows, wf, side="YRFI", gate=g, fill=fill))
        print("\n  --- NRFI (walk-forward calibrator; disabled live) ---")
        print(HDR)
        for g in (0.55, 0.58, 0.60, 0.62, 0.65):
            report(f"NRFI gate p_nrfi >= {g:.2f}",
                   select(rows, wf, side="NRFI", gate=g, fill=fill))
        if fill is None:
            continue
        break_after = True

    print("\n" + "=" * 116)
    print("  IN-SAMPLE cross-check (calibrator has seen these games) -- fill -125")
    print("=" * 116)
    print(HDR)
    for g in (0.44, 0.40, 0.36):
        report(f"YRFI gate {g:.2f} (in-sample)",
               select(rows, ins, side="YRFI", gate=g, fill=-125))

    print("\n" + "=" * 116)
    print("  READING IT")
    print("=" * 116)
    print("  * 'CI>0' marks a bootstrap whose whole 90% band is above zero --")
    print("    the only rows with statistically supported edge.")
    print("  * The bootstrap and ROI are on FLAT 1u, because that isolates the")
    print("    edge. The Kelly column is the same edge levered; it cannot create")
    print("    edge that the flat column does not show.")
    print("  * Compare a gate across the four fill assumptions. If its verdict")
    print("    flips between -110 and -140, the result is an artefact of the")
    print("    assumption, not a property of the gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

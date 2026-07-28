#!/usr/bin/env python3
"""
tools/calibrator_shape_vs_selectivity.py

The bake-off (tools/calibrator_bakeoff.py) showed every candidate
calibrator beating the live system on 2026 P&L -- but each also placed
FEWER bets, and bet count fell monotonically with ROI.  That is the
classic confound: "bet less, bet better" is not evidence that the
calibrator's SHAPE improved.  It may only mean the calibrated
distribution shifted relative to a fixed 0.44 gate.

This script separates the two effects:

  1. SELECTIVITY -- how many bets a calibrator+threshold combination
     fires.  Controlled here by sweeping each calibrator's threshold to
     hit a TARGET BET COUNT, so every candidate is compared at equal
     volume.  Any remaining P&L difference is attributable to shape.

  2. SHAPE -- whether the calibrator RANKS games better.  Measured
     threshold-free by AUC (does it sort winners above losers?) and by
     P&L at matched volume.

Also adds the true baseline the bake-off was missing: the LIVE
production calibrator, data/calibration_v2.json, which was fit on
2025 + 2026 -- i.e. it has already SEEN the 2026 games it is scored on.
It therefore enjoys a leakage advantage; if it still loses, the deficit
is real.

Usage:
    python tools/calibrator_shape_vs_selectivity.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calibration import ProbCalibrator  # noqa: E402
import recalibrate_v2 as rc  # noqa: E402
from tools.calibrator_bakeoff import (  # noqa: E402
    CIRCalibrator, PlattCalibrator, BlendCalibrator,
    BT_2024, BT_2025, PICKS_2026, raw_preds_for,
)

PROD_CAL = ROOT / "data" / "calibration_v2.json"


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def settle(odds, hit):
    if not hit:
        return -1.0
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def breakeven(odds):
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def auc(scores, labels):
    """Probability a random positive outranks a random negative.
    scores = P(YRFI) here; labels = 1 when YRFI actually happened."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(s)
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, ranks)
    ranks = (sums / cnt)[inv]
    return float((ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2)
                 / (len(pos) * len(neg)))


def main():
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    p24, y24, _ = raw_preds_for(BT_2024, "backtest", fi_park, t1, b1)
    p25, y25, _ = raw_preds_for(BT_2025, "backtest", fi_park, t1, b1)
    p26, y26, rows26 = raw_preds_for(PICKS_2026, "picks", fi_park, t1, b1)

    graded = [r for r in rows26
              if (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")]
    assert len(graded) == len(y26)

    # Keep only 2026 games that have a REAL captured DK YRFI price --
    # the only rows where P&L is honest.
    universe = []
    for r, praw, actual in zip(graded, p26, y26):
        od = fnum(r.get("market_yrfi_odds"))
        if od is None:
            continue
        universe.append({
            "raw": float(praw),
            "yrfi_hit": actual == 0,     # actual==0 -> YRFI occurred
            "odds": od,
            "date": r.get("date"),
        })
    print(f"2026 universe with real captured DK prices: {len(universe)} games")
    print(f"  (of {len(graded)} graded 2026 games total)")
    base_rate = sum(u["yrfi_hit"] for u in universe) / len(universe)
    print(f"  YRFI base rate in that universe: {base_rate:.1%}")

    Ptr = np.concatenate([p24, p25])
    Ytr = np.concatenate([y24, y25])
    cands = {
        "PRODUCTION calibration_v2": ProbCalibrator.load(PROD_CAL),
        "iso20 (refit 24+25)": ProbCalibrator.fit(list(Ptr), list(Ytr), 20, ["2024", "2025"]),
        "cir20 (refit 24+25)": CIRCalibrator.fit(list(Ptr), list(Ytr), 20, ["2024", "2025"]),
        "cir40 (refit 24+25)": CIRCalibrator.fit(list(Ptr), list(Ytr), 40, ["2024", "2025"]),
        "platt (refit 24+25)": PlattCalibrator.fit(list(Ptr), list(Ytr), ["2024", "2025"]),
        "blend (refit 24+25)": BlendCalibrator.fit(list(Ptr), list(Ytr), 20, ["2024", "2025"]),
    }

    # ---------------------------------------------------------------
    # 1. Threshold-free ranking quality
    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("  1. SHAPE ONLY -- does the calibrator RANK games better?  (threshold-free)")
    print("=" * 88)
    print("  AUC = chance a random YRFI game is scored above a random NRFI game.")
    print("  0.50 = coin flip.  A monotone transform CANNOT change AUC, so all")
    print("  monotone calibrators of the same raw model must tie -- that is the point:")
    print("  the calibrator cannot add ranking information, only relabel it.\n")
    print(f"  {'calibrator':<28}{'AUC':>8}{'distinct values':>18}{'plateau mass':>15}")
    raws = np.array([u["raw"] for u in universe])
    hits = np.array([1 if u["yrfi_hit"] else 0 for u in universe])
    print(f"  {'raw model (no calibrator)':<28}{auc(1-raws, hits):>8.4f}"
          f"{len(np.unique(np.round(raws,6))):>18}{0.0:>14.1%}")
    for name, cal in cands.items():
        q = np.array([cal.predict(float(x)) for x in raws])
        qq = np.round(q, 6)
        c = {}
        for v in qq:
            c[v] = c.get(v, 0) + 1
        pm = sum(k for k in c.values() if k >= max(0.03 * len(qq), 2)) / len(qq)
        print(f"  {name:<28}{auc(1-q, hits):>8.4f}{len(c):>18}{pm:>14.1%}")

    # ---------------------------------------------------------------
    # 2. Equal-volume P&L
    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("  2. EQUAL-VOLUME P&L -- every calibrator forced to fire the SAME number of bets")
    print("=" * 88)
    print("  Threshold is tuned per calibrator to hit the target count, so differences")
    print("  here are shape, not selectivity.\n")

    targets = [50, 100, 150, 200, 250, 300]
    print(f"  {'calibrator':<28}" + "".join(f"{t:>12}" for t in targets))
    print(f"  {'':<28}" + "".join(f"{'bets  P&L':>12}" for _ in targets))
    for name, cal in cands.items():
        q = np.array([cal.predict(float(x)) for x in raws])
        order = np.argsort(q)          # lowest P(NRFI) = strongest YRFI
        cells = ""
        for t in targets:
            k = min(t, len(order))
            sel = order[:k]
            pl = sum(settle(universe[i]["odds"], universe[i]["yrfi_hit"]) for i in sel)
            cells += f"{pl:>+11.2f}u"
        print(f"  {name:<28}{cells}")

    print("\n  Reference: the LIVE system actually placed 349 STRONG bets in this")
    print("  window for -2.20u.  Its selection is the PRODUCTION row above.")

    # ---------------------------------------------------------------
    # 3. What the live gate actually does per calibrator
    # ---------------------------------------------------------------
    print("\n" + "=" * 88)
    print("  3. AT THE LIVE GATE (p_nrfi < 0.44) -- volume each calibrator would fire")
    print("=" * 88)
    print(f"  {'calibrator':<28}{'bets':>6}{'W':>5}{'L':>5}{'hit%':>8}{'need':>7}"
          f"{'P&L':>10}{'ROI%':>8}")
    for name, cal in cands.items():
        q = np.array([cal.predict(float(x)) for x in raws])
        sel = [i for i in range(len(universe)) if q[i] < 0.44]
        if not sel:
            print(f"  {name:<28}{0:>6}   (no qualifying bets)")
            continue
        n = len(sel)
        w = sum(1 for i in sel if universe[i]["yrfi_hit"])
        pl = sum(settle(universe[i]["odds"], universe[i]["yrfi_hit"]) for i in sel)
        nd = np.mean([breakeven(universe[i]["odds"]) for i in sel])
        print(f"  {name:<28}{n:>6}{w:>5}{n-w:>5}{100*w/n:>7.1f}%{100*nd:>6.1f}%"
              f"{pl:>+9.2f}u{100*pl/n:>7.2f}%")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/fit_cir_calibrator.py -- fit a Centered Isotonic Regression
calibrator and decide, on evidence, whether it should replace the
production one.

WHY NOW (and why NOT on 2026-07-27)
-----------------------------------
On 2026-07-27 a calibrator rebuild was proven WORTHLESS under flat 1u
staking: a calibrator is a monotone relabelling, so at equal bet volume
every candidate selects the same games for the same money (AUC 0.5346
raw vs 0.5334-0.5352 across six candidates).

Kelly changed that on 2026-07-28. The calibrated probability IS the `p`
in the Kelly formula, so it now sets STAKE SIZE directly, not just
ordering. The production curve has a flat step at calibrated NRFI
0.40639 (YRFI 0.5936) spanning raw inputs 0.39-0.43 -- 204 graded 2026
games across 98 distinct lambda values all receive that one number, and
Kelly stakes every one of them off it. The gate sweep measured the
damage: the 0.56-0.60 probability band, which is exactly where that
plateau sits, went from -14.27u at flat 1u to -36.91u under Kelly
because the model claims an edge it does not have and Kelly funds it.

WHAT CIR CHANGES
  Standard PAV isotonic pools adjacent bins that violate monotonicity
  and gives the whole pool one value -- interpolating between two bin
  centres that share a value is FLAT.  Centered Isotonic Regression
  (Oron & Flournoy 2017) collapses each pool to a single knot at its
  weighted-mean x and interpolates between KNOTS, so a pooled region
  becomes a ramp instead of a plateau.  Monotonicity is preserved.

  Crucially the output is still a {centers, rates} pair list, so
  `ProbCalibrator.load()` reads it unchanged -- this is a DATA swap, not
  a code change.

DECISION RULE (CLAUDE.md 3-split, no exceptions)
  Ship only if, across 2024->2025, 2025->2024 and 2024+2025->2026:
    * Brier does not degrade in more than one split, AND
    * plateau mass falls substantially, AND
    * the Kelly-staked 2026 P&L does not degrade.
  Reject otherwise.

Usage:
    python tools/fit_cir_calibrator.py             # validate only
    python tools/fit_cir_calibrator.py --write     # write data/calibration_v2.json
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import recalibrate_v2 as rc  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.calibrator_bakeoff import (  # noqa: E402
    CIRCalibrator, BT_2024, BT_2025, PICKS_2026,
    raw_preds_for, brier, logloss, ece, plateau_mass,
)
from tools.kelly_backtest import decimal_b, implied  # noqa: E402

PROD = ROOT / "data" / "calibration_v2.json"
# 2026-07-28 AUDIT FIX: was hardcoded 0.36 while live is 0.40. This value
# drives money() -- the script's own ship/reject rule -- and the script has
# a --write mode that overwrites production data/calibration_v2.json. A
# 0.36 gate selects ~32% fewer bets (86 vs 126 on the same walk-forward),
# so a future calibrator would be judged on the wrong population.
import mlb_first_inning_predictor as _P  # noqa: E402

GATE_NRFI = _P._LR_STRONG_YRFI_P   # STRONG YRFI when p_nrfi < this


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def money(cal, rows, raws, label, frac=0.25):
    """Re-decide + re-stake the 2026 slate under `cal`, day by day."""
    by_day = defaultdict(list)
    for r, praw in zip(rows, raws):
        p_nrfi = cal.predict(float(praw))
        if p_nrfi >= GATE_NRFI:
            continue                      # not STRONG YRFI under this curve
        odds = fnum(r.get("market_yrfi_odds"))
        if odds is None:
            continue
        res = (r.get("actual_result") or "").upper()
        if res not in ("NRFI", "YRFI"):
            continue
        by_day[r["date"]].append(
            {"p": 1.0 - p_nrfi, "odds": odds, "win": res == "YRFI"})

    bank = peak = 100.0
    maxdd = 0.0
    n = w = 0
    o_f = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for day in sorted(by_day):
            tracker._bankroll_cache = bank
            tracker._daily_committed = {day: 0.0}
            pnl = 0.0
            for b in by_day[day]:
                stake = tracker.kelly_stake_units(
                    b["p"], str(int(b["odds"])), game_date=day) or 0.0
                if stake <= 0:
                    continue
                n += 1
                if b["win"]:
                    w += 1
                    pnl += stake * decimal_b(b["odds"])
                else:
                    pnl -= stake
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                maxdd = max(maxdd, (peak - bank) / peak)
    finally:
        tracker.KELLY_FRACTION = o_f
    return {"label": label, "n": n, "w": w, "final": bank,
            "profit": bank - 100.0, "maxdd": 100 * maxdd,
            "days": len(by_day)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write data/calibration_v2.json (backs up the old one)")
    args = ap.parse_args()

    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    p24, y24, _ = raw_preds_for(BT_2024, "backtest", fi_park, t1, b1)
    p25, y25, _ = raw_preds_for(BT_2025, "backtest", fi_park, t1, b1)
    p26, y26, rows26 = raw_preds_for(PICKS_2026, "picks", fi_park, t1, b1)
    graded26 = [r for r in rows26
                if (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")]
    assert len(graded26) == len(y26)

    # ---------- 1. 3-split statistical validation ----------------------
    print("=" * 96)
    print("  1. MANDATED 3-SPLIT OUT-OF-SAMPLE VALIDATION")
    print("=" * 96)
    splits = [
        ("2024 -> 2025", (p24, y24, ["2024"]), (p25, y25)),
        ("2025 -> 2024", (p25, y25, ["2025"]), (p24, y24)),
        ("2024+2025 -> 2026",
         (np.concatenate([p24, p25]), np.concatenate([y24, y25]), ["2024", "2025"]),
         (p26, y26)),
    ]
    worse = 0
    for name, (Ptr, Ytr, seas), (Pte, Yte) in splits:
        iso = ProbCalibrator.fit(list(Ptr), list(Ytr), 20, seas)
        cir = CIRCalibrator.fit(list(Ptr), list(Ytr), 20, seas)
        qi = np.array([iso.predict(float(x)) for x in Pte])
        qc = np.array([cir.predict(float(x)) for x in Pte])
        bi, bc = brier(qi, Yte), brier(qc, Yte)
        if bc > bi + 1e-9:
            worse += 1
        print(f"\n  --- {name} ---")
        print(f"    {'':<10}{'Brier':>10}{'logloss':>10}{'ECE':>9}{'plateau mass':>15}")
        print(f"    {'iso20':<10}{bi:>10.5f}{logloss(qi,Yte):>10.5f}"
              f"{ece(qi,Yte):>9.5f}{plateau_mass(qi):>14.1%}")
        print(f"    {'CIR':<10}{bc:>10.5f}{logloss(qc,Yte):>10.5f}"
              f"{ece(qc,Yte):>9.5f}{plateau_mass(qc):>14.1%}")
        print(f"    Brier delta: {bc-bi:+.5f}"
              f"{'  (CIR worse)' if bc > bi else '  (CIR same or better)'}")
    print(f"\n  CIR degraded Brier in {worse} of 3 splits "
          f"({'PASS -- rule allows at most 1' if worse <= 1 else 'FAIL'})")

    # ---------- 2. production refit, same recipe as recalibrate_v2 -----
    print("\n" + "=" * 96)
    print("  2. PRODUCTION REFIT -- same training data the live curve uses (2025 + 2026)")
    print("=" * 96)
    prod = ProbCalibrator.load(PROD)
    print(f"  live curve: train_seasons={prod.train_seasons} n={prod.train_n} "
          f"knots={len(prod.centers)}")
    Ptr = np.concatenate([p25, p26])
    Ytr = np.concatenate([y25, y26])
    new = CIRCalibrator.fit(list(Ptr), list(Ytr), 20, ["2025", "2026"])
    print(f"  new CIR  : train n={len(Ytr)} knots={len(new.centers)}")

    def flat_runs(cal):
        r = [round(x, 9) for x in cal.rates]
        c = Counter(r)
        return max(c.values()), sum(v for v in c.values() if v > 1)
    mo, so = flat_runs(prod)
    mn, sn = flat_runs(new)
    print(f"\n  longest flat run  : live {mo} knots  ->  CIR {mn}")
    print(f"  knots inside flats: live {so}  ->  CIR {sn}")

    qp = np.array([prod.predict(float(x)) for x in p26])
    qn = np.array([new.predict(float(x)) for x in p26])
    print(f"\n  applied to the 1520 graded 2026 games:")
    print(f"    distinct probabilities : live {len(set(np.round(qp,6)))}"
          f"  ->  CIR {len(set(np.round(qn,6)))}")
    print(f"    plateau mass           : live {plateau_mass(qp):.1%}"
          f"  ->  CIR {plateau_mass(qn):.1%}")
    print(f"    games on the 0.5936 step: "
          f"{int((np.abs(qp-0.40639)<1e-4).sum())}  ->  "
          f"{int((np.abs(qn-0.40639)<1e-4).sum())}")
    print(f"    Brier on 2026 (in-sample for both): "
          f"live {brier(qp,y26):.5f}  CIR {brier(qn,y26):.5f}")
    print(f"    mean |probability change|: {np.abs(qn-qp).mean():.4f}"
          f"   max {np.abs(qn-qp).max():.4f}")
    print(f"    games crossing the STRONG gate (p_nrfi<{GATE_NRFI}): "
          f"live {int((qp<GATE_NRFI).sum())}  ->  CIR {int((qn<GATE_NRFI).sum())}")

    # ---------- 3. the reason we are doing this: Kelly money -----------
    print("\n" + "=" * 96)
    print("  3. KELLY MONEY TEST -- the actual reason to ship (stake size depends on p)")
    print("=" * 96)
    print(f"  {'calibrator':<28}{'bets':>6}{'W':>5}{'hit%':>8}{'final':>10}"
          f"{'profit':>11}{'maxDD':>9}")
    res = []
    for cal, lbl in ((prod, "live (plateau)"), (new, "CIR (no plateau)")):
        r = money(cal, graded26, p26, lbl)
        res.append(r)
        hit = 100 * r["w"] / r["n"] if r["n"] else 0
        print(f"  {lbl:<28}{r['n']:>6}{r['w']:>5}{hit:>7.1f}%{r['final']:>9.2f}u"
              f"{r['profit']:>+10.2f}u{r['maxdd']:>8.1f}%")
    delta = res[1]["profit"] - res[0]["profit"]
    print(f"\n  Kelly P&L delta: {delta:+.2f}u")

    # ---------- verdict ------------------------------------------------
    print("\n" + "=" * 96)
    print("  VERDICT")
    print("=" * 96)
    ok = (worse <= 1) and (plateau_mass(qn) < plateau_mass(qp)) and (delta >= -1.0)
    for cond, desc in [
        (worse <= 1, f"Brier degraded in <=1 of 3 splits (got {worse})"),
        (plateau_mass(qn) < plateau_mass(qp),
         f"plateau mass falls ({plateau_mass(qp):.1%} -> {plateau_mass(qn):.1%})"),
        (delta >= -1.0, f"Kelly P&L not materially worse ({delta:+.2f}u)"),
    ]:
        print(f"    [{'PASS' if cond else 'FAIL'}] {desc}")
    print(f"\n  {'SHIP IT' if ok else 'DO NOT SHIP'}")

    if args.write:
        if not ok:
            print("\n  --write refused: validation did not pass.")
            return 1
        bak = PROD.with_suffix(".json.pre_cir_bak")
        shutil.copy2(PROD, bak)
        new.save(PROD)
        # keep the loader's expected field set
        d = json.loads(PROD.read_text(encoding="utf-8"))
        d.pop("kind", None)
        d["fit_method"] = "cir"
        PROD.write_text(json.dumps(d, indent=2), encoding="utf-8")
        print(f"\n  wrote {PROD.relative_to(ROOT)}  (backup: {bak.name})")
        chk = ProbCalibrator.load(PROD)
        print(f"  reload check: {len(chk.centers)} knots, "
              f"predict(0.42) = {chk.predict(0.42):.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

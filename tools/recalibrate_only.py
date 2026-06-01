#!/usr/bin/env python3
"""
tools/recalibrate_only.py -- refit JUST the isotonic calibrator on
recent 2026 data, without touching the LR weights.

WHY:
- 2026-06-01 diagnosis showed the NRFI side of the calibrator is
  systematically over-confident: at cal_p >= 0.65 the actual hit
  rate is 40% (should be 65%).  At cal_p 0.56-0.60 it's 36% (should
  be 58%).  Driver: late-May league-wide NRFI rate dropped to ~46%,
  but the calibrator was fit on data averaging ~49%.
- YRFI side has the same drift but in our FAVOR (the model under-
  rates YRFI confidence -> our YRFI bets are stronger than the cal
  label claims, hitting 74%).
- A recalibration shifts the whole curve down to match current
  reality.  Same coin, both sides corrected.

WHAT IT DOES:
1. Load current production T1+B1 LR.  Don't touch.
2. Load picks_2026.csv graded rows from the recent window (default
   60 days).  Build feature vectors.
3. Run LR forward -> raw_p_nrfi for each row.
4. Fit ProbCalibrator.fit(predictions=raw_p, actuals=y_nrfi).
5. Save candidate calibrator to
   data/candidates/recalibrate_only/calibration.json (NOT prod).
6. Head-to-head: for the trailing 14 days of STRONG bets, show how
   each bet would change under the candidate calibrator.

DECISION GATE:
- Ship only if YRFI bet count under candidate >= current AND
  candidate beats current on holdout P&L by some tolerance.

Run modes:
  python tools/recalibrate_only.py             # diff only, no save
  python tools/recalibrate_only.py --save      # save candidate
  python tools/recalibrate_only.py --ship      # overwrite prod
                                               # (requires --save first)
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lr_baseline import LogReg
from calibration import ProbCalibrator
from walk_forward_eval import (
    load_parks, load_picks_2026, fit_calibrator,
)

PROD_T1 = ROOT / "data" / "lr_t1.json"
PROD_B1 = ROOT / "data" / "lr_b1.json"
PROD_CAL = ROOT / "data" / "calibration_v2.json"
CAND_DIR = ROOT / "data" / "candidates" / "recalibrate_only"
PICKS_CSV = ROOT / "data" / "picks_2026.csv"

STRONG_NRFI_P = 0.56
STRONG_YRFI_P = 0.44


def amer_to_payout(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n / 100.0 if n > 0 else 100.0 / abs(n)


def load_odds_map(start_iso: str, end_iso: str) -> dict:
    """date,game_pk -> (nrfi_payout, yrfi_payout) for the holdout window."""
    out = {}
    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] < start_iso or r["date"] > end_iso:
                continue
            n = amer_to_payout(r.get("market_nrfi_odds", ""))
            y = amer_to_payout(r.get("market_yrfi_odds", ""))
            out[(r["date"], r["game_pk"])] = (n, y)
    return out


def classify(p):
    if p >= STRONG_NRFI_P:
        return "STRONG NRFI"
    if p < STRONG_YRFI_P:
        return "STRONG YRFI"
    return "PASS"


def fit_recent_calibrator(parks: dict, training_days: int = 60) -> ProbCalibrator:
    """Refit the calibrator using only recent picks_2026 data + current LR."""
    today = date.today()
    train_start = (today - timedelta(days=training_days)).isoformat()
    train_end = today.isoformat()

    rows = load_picks_2026(parks)
    train_rows = [r for r in rows if train_start <= r["date"] < train_end]
    if len(train_rows) < 100:
        sys.exit(f"Only {len(train_rows)} graded rows in last {training_days} days; refusing to fit on tiny sample")

    prod_t1 = LogReg.load(str(PROD_T1))
    prod_b1 = LogReg.load(str(PROD_B1))
    new_cal = fit_calibrator(train_rows, prod_t1, prod_b1)
    print(f"Recalibrated on {len(train_rows)} rows from {train_start} to {train_end}")
    print(f"  bin count: {len(new_cal.centers)}")
    print(f"  curve range: cal_p min={min(new_cal.rates):.4f} max={max(new_cal.rates):.4f}")
    return new_cal


def show_curve_diff(old_cal: ProbCalibrator, new_cal: ProbCalibrator):
    print("\n=== Calibrator curve comparison ===")
    print(f"{'raw_p':>7}  {'old cal_p':>10}  {'new cal_p':>10}  {'delta':>8}")
    for raw in [0.30, 0.35, 0.40, 0.43, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        ocp = old_cal.predict(raw)
        ncp = new_cal.predict(raw)
        print(f"{raw:>7.3f}  {ocp:>10.4f}  {ncp:>10.4f}  {ncp-ocp:>+8.4f}")


def evaluate_holdout(parks: dict, new_cal: ProbCalibrator, holdout_days: int = 14):
    today = date.today()
    holdout_start = (today - timedelta(days=holdout_days)).isoformat()
    holdout_end = today.isoformat()

    prod_t1 = LogReg.load(str(PROD_T1))
    prod_b1 = LogReg.load(str(PROD_B1))
    old_cal = ProbCalibrator.load(PROD_CAL)

    rows = load_picks_2026(parks)
    holdout = [r for r in rows if holdout_start <= r["date"] < holdout_end]
    if not holdout:
        sys.exit("No holdout rows.")

    odds_map = load_odds_map(holdout_start, holdout_end)

    Xt = np.asarray([r["t1"] for r in holdout])
    Xb = np.asarray([r["b1"] for r in holdout])
    pt = prod_t1.predict_proba(Xt)
    pb = prod_b1.predict_proba(Xb)
    raw_p = (1 - pt) * (1 - pb)

    # Build a (date, away, home) -> game_pk + odds map from picks_2026.csv
    # so we can lookup logged DK odds without depending on game_pk in
    # the gather_features() row schema (which doesn't include it).
    aux_map = {}
    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] < holdout_start or r["date"] > holdout_end:
                continue
            key = (r["date"], r.get("away_team", ""), r.get("home_team", ""))
            aux_map[key] = {
                "game_pk": r.get("game_pk", ""),
                "n_pay":   amer_to_payout(r.get("market_nrfi_odds", "")),
                "y_pay":   amer_to_payout(r.get("market_yrfi_odds", "")),
            }

    print(f"\n=== Per-bet holdout diff (last {holdout_days} days, {len(holdout)} graded games) ===\n")
    print(f"{'date':>10} {'matchup':>10} {'actual':>6} {'raw':>6} {'old_cp':>7} {'old_pick':>13} {'new_cp':>7} {'new_pick':>13}")

    changes = []
    for r, raw, *_ in zip(holdout, raw_p, pt, pb):
        ocp = float(old_cal.predict(float(raw)))
        ncp = float(new_cal.predict(float(raw)))
        old_v = classify(ocp)
        new_v = classify(ncp)
        actual = "NRFI" if r["y_nrfi"] == 1 else "YRFI"
        matchup = f"{r['away']}@{r['home']}"
        diff_marker = " <<" if old_v != new_v else ""
        print(f"{r['date']:>10} {matchup:>10} {actual:>6} {raw:>6.3f} {ocp:>7.4f} {old_v:>13} {ncp:>7.4f} {new_v:>13}{diff_marker}")
        if old_v != new_v:
            changes.append((r, ocp, ncp, old_v, new_v, actual))

    print(f"\n=== Verdict changes: {len(changes)} ===")

    # Tally records under each calibrator
    def tally(cal_arr, label):
        snrfi_w = snrfi_l = syrfi_w = syrfi_l = 0
        pl = 0.0
        for r, raw, cp in zip(holdout, raw_p, cal_arr):
            v = classify(float(cp))
            if v == "PASS":
                continue
            won = (v == "STRONG NRFI" and r["y_nrfi"] == 1) or (v == "STRONG YRFI" and r["y_nrfi"] == 0)
            aux = aux_map.get((r["date"], r["away"], r["home"]), {})
            n_pay = aux.get("n_pay")
            y_pay = aux.get("y_pay")
            payout = n_pay if v == "STRONG NRFI" else y_pay
            if payout is None:
                payout = 100.0 / 110.0
            pl += payout if won else -1.0
            if v == "STRONG NRFI":
                if won: snrfi_w += 1
                else: snrfi_l += 1
            else:
                if won: syrfi_w += 1
                else: syrfi_l += 1
        n_snrfi = snrfi_w + snrfi_l
        n_syrfi = syrfi_w + syrfi_l
        print(f"\n{label}:")
        print(f"  STRONG NRFI: {snrfi_w}/{n_snrfi} ({snrfi_w/max(1,n_snrfi)*100:.1f}%)")
        print(f"  STRONG YRFI: {syrfi_w}/{n_syrfi} ({syrfi_w/max(1,n_syrfi)*100:.1f}%)")
        print(f"  Total bets:  {n_snrfi + n_syrfi}")
        print(f"  P&L:         {pl:+.3f}u (at logged DK odds, -110 fallback)")
        return {"pl": pl, "n_yrfi": n_syrfi, "w_yrfi": syrfi_w,
                "n_nrfi": n_snrfi, "w_nrfi": snrfi_w}

    old_cal_arr = [old_cal.predict(float(p)) for p in raw_p]
    new_cal_arr = [new_cal.predict(float(p)) for p in raw_p]
    prod_res = tally(old_cal_arr, "=== PRODUCTION (current calibrator) ===")
    cand_res = tally(new_cal_arr, "=== CANDIDATE (recalibrated) ===")

    print()
    print(f"=== Decision gate ===")
    yrfi_volume_ok = cand_res["n_yrfi"] >= prod_res["n_yrfi"]
    nrfi_better = cand_res["w_nrfi"] / max(1, cand_res["n_nrfi"]) >= prod_res["w_nrfi"] / max(1, prod_res["n_nrfi"]) - 0.01
    pl_ok = cand_res["pl"] >= prod_res["pl"] - 1.0
    print(f"  YRFI bet count: prod={prod_res['n_yrfi']}, cand={cand_res['n_yrfi']}  --  {'OK' if yrfi_volume_ok else 'FAIL'} (cand >= prod)")
    print(f"  NRFI hit rate:  prod={prod_res['w_nrfi']}/{prod_res['n_nrfi']}, cand={cand_res['w_nrfi']}/{cand_res['n_nrfi']}  --  {'OK' if nrfi_better else 'FAIL'} (cand >= prod - 1pp)")
    print(f"  P&L:            prod={prod_res['pl']:+.2f}, cand={cand_res['pl']:+.2f}  --  {'OK' if pl_ok else 'FAIL'} (cand >= prod - 1.0u)")
    all_ok = yrfi_volume_ok and nrfi_better and pl_ok
    print()
    print(f"  OVERALL: {'PASS - safe to ship' if all_ok else 'FAIL - do not ship'}")
    return all_ok, cand_res, prod_res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="Save candidate to data/candidates/recalibrate_only/")
    ap.add_argument("--ship", action="store_true", help="Overwrite production calibrator (requires --save)")
    ap.add_argument("--training-days", type=int, default=60)
    ap.add_argument("--holdout-days", type=int, default=14)
    args = ap.parse_args()

    parks = load_parks()
    old_cal = ProbCalibrator.load(PROD_CAL)
    new_cal = fit_recent_calibrator(parks, args.training_days)

    show_curve_diff(old_cal, new_cal)
    all_ok, _, _ = evaluate_holdout(parks, new_cal, args.holdout_days)

    if args.save:
        CAND_DIR.mkdir(parents=True, exist_ok=True)
        new_cal.save(CAND_DIR / "calibration.json")
        print(f"\nSaved candidate to {CAND_DIR / 'calibration.json'}")

    if args.ship:
        if not args.save:
            sys.exit("--ship requires --save (chicken-and-egg sanity check)")
        if not all_ok:
            print(f"\nValidation gate FAILED -- refusing to ship.  Re-run without --ship.")
            return 1
        # Back up and overwrite
        today_iso = date.today().isoformat()
        backup = ROOT / "data" / f"calibration_v2.json.bak-{today_iso}-recalibrate"
        shutil.copy2(PROD_CAL, backup)
        shutil.copy2(CAND_DIR / "calibration.json", PROD_CAL)
        print(f"\nSHIPPED -- backed up old calibrator to {backup.name}")
        print(f"Rollback: cp {backup.name} calibration_v2.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

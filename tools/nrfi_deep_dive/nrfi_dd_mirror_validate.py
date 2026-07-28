#!/usr/bin/env python3
"""tools/nrfi_dd_mirror_validate.py -- does the MIRROR TRADE have a mechanism?

The 2026 real-priced data says: on games where the model leans NRFI
(p_nrfi >= 0.50), betting the YRFI side returned +6.0% ROI (n=324). For that
to be anything but noise, one of two things must be true:

  (A) PRICING story -- the book overprices NRFI *worst* exactly in the zone
      our model likes NRFI. Only testable on 2026 (no odds before that).
  (B) CALIBRATION story -- our model OVER-PREDICTS NRFI in its own upper tail:
      it says p_nrfi = 0.54 and the games come back 0.463. That is a pure
      hit-rate claim, so it IS testable on the big 2024/2025 backtests, and it
      is the mechanism that would make (A) durable rather than a 2026 accident.

This script runs (B) under the project's mandatory 3-split, with the
calibrator refit out-of-sample each time so the tail is never scored by a
calibrator that has already seen it:

      train 2024        -> test 2025
      train 2025        -> test 2024
      train 2024+2025   -> test 2026 (picks_2026.csv)

Reject the mechanism unless the over-prediction shows up in ALL THREE.

Read-only. Analysis only.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc                      # noqa: E402
from calibration import CIRCalibrator            # noqa: E402

BT = ROOT / "data" / "backtests"
SEED, B = 20260728, 20000

SOURCES = {
    "2024": (BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv", "backtest"),
    "2025": (BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv", "backtest"),
    "2026": (ROOT / "data" / "picks_2026.csv", "picks"),
}


def load_raw(name):
    path, kind = SOURCES[name]
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if kind == "picks":
        seen = {}
        for r in rows:
            seen[(r.get("game_pk", ""), r.get("game_number", ""))] = r
        rows = list(seen.values())
    return rows, kind


def featurize(name):
    """-> (raw_uncalibrated_p, y_nrfi, date) arrays for one season."""
    rows, kind = load_raw(name)
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    gather = rc.gather_from_picks if kind == "picks" else rc.gather_from_backtest
    if kind == "picks":
        Xt, Xb, y, skipped = gather(rows, fi_park)
    else:
        Xt, Xb, y, skipped = gather(rows, fi_park)
    raw = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    # re-walk rows in the same order the gather kept them, to recover dates
    dates, okcol = [], ("actual_result" if kind == "picks" else "actual_side")
    for r in rows:
        a = (r.get(okcol) or "").upper()
        if a not in ("NRFI", "YRFI"):
            continue
        try:
            home = r.get("home_team") if kind == "picks" else r.get("home")
            rc._build_t1_b1_phase_e3(r, fi_park.get(home or "", rc.FI_PARK_DEFAULT))
        except Exception:
            continue
        dates.append(r["date"])
    if len(dates) != len(y):
        dates = [""] * len(y)          # fall back; only used for day-bootstrap
    return np.asarray(raw, float), np.asarray(y, int), dates, skipped


def boot_ci_days(vals, dates, seed=SEED):
    """block bootstrap over days on a mean."""
    if len(vals) < 8:
        return float("nan"), float("nan")
    byday = defaultdict(list)
    for v, d in zip(vals, dates):
        byday[d].append(v)
    keys = list(byday)
    if len(keys) < 5:
        return float("nan"), float("nan")
    sums = np.array([np.sum(byday[k]) for k in keys], float)
    cnts = np.array([len(byday[k]) for k in keys], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    m = sums[idx].sum(axis=1) / np.maximum(cnts[idx].sum(axis=1), 1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def zone_report(p, y, dates, lo_gate, label):
    m = p >= lo_gate
    n = int(m.sum())
    if n < 10:
        print(f"    {label:<26} n={n:>5}   (too few)")
        return None
    pred = float(p[m].mean())
    act = float(y[m].mean())
    gap = act - pred                      # negative = model OVER-predicts NRFI
    dd = [d for d, keep in zip(dates, m) if keep]
    clo, chi = boot_ci_days((y[m] - p[m]).tolist(), dd)
    flag = "OVER-predicts NRFI" if gap < 0 else "under-predicts NRFI"
    sig = "CI excl 0" if (chi < 0 or clo > 0) else "CI spans 0"
    print(f"    {label:<26} n={n:>5}  pred {pred*100:>5.1f}%  actual {act*100:>5.1f}%  "
          f"gap {gap*100:>+5.1f}pp  dayCI[{clo*100:+.1f},{chi*100:+.1f}]  {flag}, {sig}")
    return {"n": n, "pred": pred, "act": act, "gap": gap, "lo": clo, "hi": chi}


def main():
    print("=" * 112)
    print("  MIRROR-TRADE MECHANISM TEST -- does the model OVER-predict NRFI in its own")
    print("  upper tail?  3-split, calibrator refit out-of-sample every time.")
    print("=" * 112)

    data = {}
    for s in ("2024", "2025", "2026"):
        raw, y, dates, sk = featurize(s)
        data[s] = (raw, y, dates)
        print(f"  {s}: {len(y):>5} graded games loaded   ({sk} unbuildable, skipped)"
              f"   base NRFI rate {y.mean()*100:.1f}%")

    splits = [
        (["2024"], "2025"),
        (["2025"], "2024"),
        (["2024", "2025"], "2026"),
    ]

    results = defaultdict(dict)
    for train, test in splits:
        Xr = np.concatenate([data[s][0] for s in train])
        Xy = np.concatenate([data[s][1] for s in train])
        cal = CIRCalibrator.fit(Xr.tolist(), Xy.tolist(), 20, train)
        raw, y, dates = data[test]
        p = np.asarray([cal.predict(float(v)) for v in raw], float)
        print(f"\n  --- train {'+'.join(train)}  ->  test {test} "
              f"(calibrator never saw {test}) ---")
        for gate, lbl in ((0.50, "p_nrfi >= 0.50"),
                          (0.54, "p_nrfi >= 0.54"),
                          (0.58, "p_nrfi >= 0.58"),
                          (0.62, "p_nrfi >= 0.62")):
            r = zone_report(p, y, dates, gate, lbl)
            if r:
                results[lbl][test] = r["gap"]

    print("\n" + "=" * 112)
    print("  VERDICT TABLE -- gap = actual NRFI rate MINUS predicted. Negative means the")
    print("  model over-predicts NRFI, which is what the mirror trade needs.")
    print("=" * 112)
    print(f"  {'zone':<20}{'test 2025':>12}{'test 2024':>12}{'test 2026':>12}"
          f"   all three negative?")
    for lbl in ("p_nrfi >= 0.50", "p_nrfi >= 0.54", "p_nrfi >= 0.58", "p_nrfi >= 0.62"):
        g = results.get(lbl, {})
        vals = [g.get("2025"), g.get("2024"), g.get("2026")]
        cells = "".join(f"{v*100:>+12.1f}" if v is not None else f"{'--':>12}" for v in vals)
        ok = all(v is not None and v < 0 for v in vals)
        print(f"  {lbl:<20}{cells}   {'YES' if ok else 'NO'}")
    print("\n  Project rule: reject anything that only works in one direction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

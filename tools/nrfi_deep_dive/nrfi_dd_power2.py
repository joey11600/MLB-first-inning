#!/usr/bin/env python3
"""
tools/nrfi_dd_power2.py -- part 2 of the NRFI power reality check.

  A. WHY the walk-forward and deployed calibrators disagree: are they
     even scoring the same GAMES / same CALENDAR WINDOW?
  B. The one high-power arm that DOES exist: hit rate at each NRFI gate
     on the 2024 + 2025 backtests (thousands of games, NO odds).  This
     bounds ACCURACY tightly; it says nothing about profit directly, but
     combined with the observed 2026 break-even it tells us whether the
     needed rate is even reachable.

Analysis only.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied, fnum  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402

GATES = (0.55, 0.58, 0.60, 0.62, 0.65)


def wilson(k, n, z=1.645):
    if n == 0:
        return float("nan"), float("nan")
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


# ---------------------------------------------------------------- A
def part_a():
    rows, _ = load_season()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    dep = [cal.predict(r["raw"]) for r in rows]
    wf = walk_forward_probs(rows)

    print("=" * 96)
    print("  A. ARE THE TWO CALIBRATORS EVEN LOOKING AT THE SAME GAMES?")
    print("=" * 96)

    # month x gate: how many games each calibrator pushes over 0.60
    print("\n  count of games with p_nrfi >= 0.60 (lambda ceiling applied), by month:")
    print(f"    {'month':<9}{'games':>7}{'dep>=.60':>10}{'wf>=.60':>9}{'both':>7}"
          f"{'dep priced':>12}{'wf priced':>11}")
    bym = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for r, pd_, pw in zip(rows, dep, wf):
        if r["lambda"] is not None and r["lambda"] > P._LR_LAMBDA_NRFI_CEILING:
            continue
        m = bym[r["date"][:7]]
        m[0] += 1
        a = pd_ is not None and pd_ >= 0.60
        b = pw is not None and pw >= 0.60
        m[1] += a
        m[2] += b
        m[3] += a and b
        m[4] += a and r["nrfi_odds"] is not None
        m[5] += b and r["nrfi_odds"] is not None
    for m in sorted(bym):
        v = bym[m]
        print(f"    {m:<9}{v[0]:>7}{v[1]:>10}{v[2]:>9}{v[3]:>7}{v[4]:>12}{v[5]:>11}")

    # distribution of the two prob series
    print("\n  calibrated p_nrfi distribution (all graded games with wf defined):")
    pairs = [(a, b) for a, b in zip(dep, wf) if b is not None]
    da = [a for a, b in pairs]
    db = [b for a, b in pairs]
    print(f"    n={len(pairs)}   mean dep={st.mean(da):.4f}  mean wf={st.mean(db):.4f}")
    for q in (0.50, 0.75, 0.90, 0.95, 0.99):
        print(f"    q{100*q:>4.0f}: dep={np.quantile(da,q):.4f}   wf={np.quantile(db,q):.4f}")
    print(f"    max:   dep={max(da):.4f}   wf={max(db):.4f}")

    # by month, the wf ceiling
    print("\n  MAX calibrated p_nrfi reachable in each month (the mechanism):")
    mx = defaultdict(lambda: [0.0, 0.0])
    for r, pd_, pw in zip(rows, dep, wf):
        m = mx[r["date"][:7]]
        m[0] = max(m[0], pd_ or 0)
        if pw is not None:
            m[1] = max(m[1], pw)
    for m in sorted(mx):
        print(f"    {m}: dep max {mx[m][0]:.4f}   wf max {mx[m][1]:.4f}"
              + ("   <-- wf can never reach 0.60" if mx[m][1] < 0.60 else ""))

    # how many distinct DAYS carry the wf 0.60 record
    B = select(rows, wf, side="NRFI", gate=0.60, fill=None)
    A = select(rows, dep, side="NRFI", gate=0.60, fill=None)
    for lbl, s in (("walk-fwd", B), ("deployed", A)):
        days = sorted({b["date"] for b in s})
        print(f"\n  {lbl} 0.60 bets: n={len(s)} on {len(days)} days, "
              f"{days[0]} .. {days[-1]} (span {len(days)} distinct slates)")
        print(f"    calendar span = "
              f"{(np.datetime64(days[-1]) - np.datetime64(days[0])).astype(int)} days")


# ---------------------------------------------------------------- B
BT = [
    ("2024", ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv"),
    ("2025", ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv"),
    ("2026a", ROOT / "data/backtests/backtest_2026-04-01_to_2026-05-11_truepit.csv"),
    ("2026b", ROOT / "data/backtests/backtest_2026-05-12_to_2026-05-26_truepit.csv"),
]


def load_bt(path):
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    with open(path, encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    out = []
    nfail = 0
    for r in raw:
        # the 2024/2025 backtest CSVs use actual_side + away/home;
        # picks_2026 uses actual_result + away_team/home_team.
        a = (r.get("actual_result") or r.get("actual_side") or "").upper()
        if a not in ("NRFI", "YRFI"):
            continue
        home = r.get("home_team") or r.get("home") or ""
        if not r.get("home_team"):
            r = dict(r, home_team=home, away_team=r.get("away", ""))
        fp = fi_park.get(home, rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            nfail += 1
            continue
        out.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                    "lambda": fnum(r.get("lambda_lr_total")),
                    "y": 1 if a == "NRFI" else 0})
    if nfail:
        print(f"    [note] {nfail} rows failed feature build")
    if not out:
        return out
    Xt = np.asarray([x["t1"] for x in out], float)
    Xb = np.asarray([x["b1"] for x in out], float)
    pr = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    for x, p in zip(out, pr):
        x["raw"] = float(p)
    return out


def part_b():
    print("\n" + "=" * 96)
    print("  B. HIT RATE AT EACH GATE ON THE BIG BACKTESTS (no odds -> accuracy only)")
    print("     Deployed calibrator was fit on 2025+2026, so 2024 is the only")
    print("     genuinely out-of-sample season here.  Break-even from 2026 real")
    print("     prices is ~58.5%.")
    print("=" * 96)
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    for lbl, path in BT:
        if not path.exists():
            print(f"  {lbl}: MISSING {path}")
            continue
        rows = load_bt(path)
        if not rows:
            print(f"  {lbl}: 0 usable rows")
            continue
        base = sum(x["y"] for x in rows) / len(rows)
        print(f"\n  --- {lbl}: {len(rows)} graded games, base NRFI rate "
              f"{100*base:.1f}% ---")
        print(f"    {'gate':>6}{'n':>7}{'NRFI hit%':>11}{'Wilson 90%':>18}"
              f"{'width':>8}{'vs 58.5% need':>15}")
        for g in GATES:
            sel = [x for x in rows
                   if cal.predict(x["raw"]) >= g
                   and not (x["lambda"] is not None and x["lambda"] > P._LR_LAMBDA_NRFI_CEILING)]
            n = len(sel)
            if n == 0:
                print(f"    {g:>6.2f}{0:>7}")
                continue
            k = sum(x["y"] for x in sel)
            lo, hi = wilson(k, n)
            v = 100 * (k / n) - 58.5
            print(f"    {g:>6.2f}{n:>7}{100*k/n:>10.1f}%  [{100*lo:>5.1f},{100*hi:>5.1f}]"
                  f"{100*(hi-lo):>7.1f}{v:>+14.1f}pp")


if __name__ == "__main__":
    part_a()
    part_b()

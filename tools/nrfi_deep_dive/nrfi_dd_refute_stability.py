#!/usr/bin/env python3
"""tools/nrfi_dd_refute_stability.py -- is the winning grid cell stable?

If (lam<=0.80, odds>=-105) is a real structural effect, then searching the
same 56-cell grid on any sub-period of 2026 should keep landing near it.
If it is noise, the winner will jump around at random.  Read-only.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


t1m, b1m = rc.load_lr_models()
fi_park = rc.load_fi_park()
rows = []
with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if (r.get("actual_result") or "").upper() not in ("NRFI", "YRFI"):
            continue
        o = fnum(r.get("market_nrfi_odds"))
        if o is None:
            continue
        fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        rows.append({"date": r["date"], "t1": tv, "b1": bv, "odds": o,
                     "y": 1 if r["actual_result"].upper() == "NRFI" else 0})
Xt = np.asarray([r["t1"] for r in rows], float)
Xb = np.asarray([r["b1"] for r in rows], float)
for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
    r["lam"] = -math.log(max(1e-12, float(p)))

CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PFS = [-160, -140, -125, -115, -105, +100, +120]


def roi(sub):
    return sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub) / len(sub)


print("=" * 92)
print("  WINNER INSTABILITY: re-run the SAME 56-cell search, leaving out one month")
print("=" * 92)
months = sorted(set(r["date"][:7] for r in rows))
print(f"  {'held out':<12}{'winning cell (lam<=, odds>=)':<34}{'n':>6}{'ROI%':>9}")
for m in months:
    sub_all = [r for r in rows if r["date"][:7] != m]
    best = None
    for c in CAPS:
        for pf in PFS:
            s = [r for r in sub_all if r["lam"] <= c and r["odds"] >= pf]
            if len(s) < 20:
                continue
            v = roi(s)
            if best is None or v > best[0]:
                best = (v, c, pf, len(s))
    print(f"  {m:<12}{f'lam<={best[1]}, odds>={best[2]:+d}':<34}{best[3]:>6}{100*best[0]:>+9.1f}")

print("\n" + "=" * 92)
print("  SAME SEARCH RUN SEPARATELY ON EACH HALF OF THE SEASON")
print("=" * 92)
dates = sorted(set(r["date"] for r in rows))
cut = dates[len(dates) // 2]
for nm, sel in (("first half", lambda r: r["date"] < cut),
                ("second half", lambda r: r["date"] >= cut)):
    pool = [r for r in rows if sel(r)]
    scored = []
    for c in CAPS:
        for pf in PFS:
            s = [r for r in pool if r["lam"] <= c and r["odds"] >= pf]
            if len(s) >= 20:
                scored.append((roi(s), c, pf, len(s)))
    scored.sort(reverse=True)
    print(f"\n  {nm} (n={len(pool)}) -- top 4 cells of {len(scored)} eligible:")
    for v, c, pf, n in scored[:4]:
        print(f"      lam<={c:<6} odds>={pf:+5d}   n={n:>4}   ROI={100*v:+.1f}%")
    print(f"    positive-ROI cells: {sum(1 for v,*_ in scored if v>0)}/{len(scored)}")

print("\n" + "=" * 92)
print("  RANK OF THE PROPOSED CELL IN EACH HALF (1 = best)")
print("=" * 92)
for nm, sel in (("first half", lambda r: r["date"] < cut),
                ("second half", lambda r: r["date"] >= cut)):
    pool = [r for r in rows if sel(r)]
    scored = []
    for c in CAPS:
        for pf in PFS:
            s = [r for r in pool if r["lam"] <= c and r["odds"] >= pf]
            if len(s) >= 20:
                scored.append((roi(s), c, pf))
    scored.sort(reverse=True)
    rk = [i for i, (_, c, pf) in enumerate(scored, 1) if c == 0.80 and pf == -105]
    print(f"  {nm:<14} rank {rk[0] if rk else 'n/a'} of {len(scored)}")

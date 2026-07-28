#!/usr/bin/env python3
"""Attack 2: is the DK baseline (the thing being multiplied) stable in time,
and how much of it is search exposure from the 0.36 gate chosen 2026-07-27?

Also: what does a REALISTIC line-shop (max of two noisy books) return, vs the
uniform +10c bump the proposal assumes?
"""
import csv, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", newline="", encoding="utf-8")))


def fnum(v):
    try:
        return float(str(v).strip().replace("−", "-")) if v not in ("", None, "None") else None
    except ValueError:
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def bump(o, c):
    if c == 0:
        return o
    if o < 0:
        v = o + c
        return v if v <= -100 else 100 + (v + 100)
    return o + c


def get(side, strong=True, placed=True):
    out = []
    for r in rows:
        if r["pick_side"] != side:
            continue
        if placed and r["bet_placed"] != "Y":
            continue
        if strong and r["pick_strength"] != "STRONG":
            continue
        if r["graded_result"] not in ("WIN", "LOSS"):
            continue
        o = fnum(r[f"market_{side.lower()}_odds"])
        p = fnum(r["nrfi_prob"])
        if o is None:
            continue
        out.append(dict(date=r["date"], win=r["graded_result"] == "WIN", odds=o, pnrfi=p))
    out.sort(key=lambda x: x["date"])
    return out


def roi(sub, c=0):
    if not sub:
        return 0.0, 0.0, 0
    t = sum((payout(bump(x["odds"], c)) if x["win"] else -1.0) for x in sub)
    return t, t / len(sub), len(sub)


def dayboot(sub, c=0, iters=20000, seed=11):
    days = defaultdict(list)
    for x in sub:
        days[x["date"]].append(payout(bump(x["odds"], c)) if x["win"] else -1.0)
    arrs = [np.array(v) for v in days.values()]
    rng = np.random.default_rng(seed)
    n = len(arrs)
    out = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, n, n)
        s = sum(arrs[j].sum() for j in idx)
        b = sum(len(arrs[j]) for j in idx)
        out[i] = s / b if b else 0.0
    return np.percentile(out, [2.5, 97.5])


Y = get("YRFI")
print(f"YRFI STRONG placed priced n={len(Y)}  {Y[0]['date']} .. {Y[-1]['date']}")

# ---- 1. time split ---------------------------------------------------
print("\n--- TIME SPLIT (chronological halves) ---")
h = len(Y) // 2
for lab, s in [("first half", Y[:h]), ("second half", Y[h:])]:
    t0, r0, n = roi(s, 0)
    t10, r10, _ = roi(s, 10)
    lo, hi = dayboot(s, 0)
    print(f"{lab:12s} n={n:3d} {s[0]['date']}..{s[-1]['date']}  "
          f"DK {t0:+7.2f}u {r0:+7.2%}  CI[{lo:+.2%},{hi:+.2%}]   "
          f"+10c {t10:+7.2f}u {r10:+7.2%}  shop-gain {t10-t0:+.2f}u")

print("\n--- BY MONTH ---")
bym = defaultdict(list)
for x in Y:
    bym[x["date"][:7]].append(x)
for m in sorted(bym):
    s = bym[m]
    t0, r0, n = roi(s, 0)
    t10, _, _ = roi(s, 10)
    w = sum(1 for x in s if x["win"])
    print(f"  {m}  n={n:3d}  W-L {w:3d}-{n-w:3d}  DK {t0:+7.2f}u ({r0:+7.2%})  "
          f"+10c {t10:+7.2f}u   gain {t10-t0:+6.2f}u")

# ---- 2. gate search exposure ----------------------------------------
# the live STRONG YRFI gate is p_nrfi < 0.36, chosen on 2026-07-27 by
# looking at THIS season. sweep it and see how special 0.36 is.
print("\n--- GATE SWEEP (search exposure). all graded YRFI-side rows with a price ---")
ALL = []
for r in rows:
    if r["graded_result"] not in ("WIN", "LOSS"):
        continue
    o = fnum(r["market_yrfi_odds"])
    p = fnum(r["nrfi_prob"])
    if o is None or p is None:
        continue
    ALL.append(dict(date=r["date"], win=(fnum(r["fi_total_runs"]) or 0) > 0, odds=o, pnrfi=p))
print(f"universe n={len(ALL)}")
cells = 0
for g in [0.28, 0.30, 0.32, 0.34, 0.36, 0.38, 0.40, 0.42, 0.44, 0.46, 0.50]:
    s = [x for x in ALL if x["pnrfi"] < g]
    cells += 1
    if len(s) < 20:
        print(f"  gate<{g:.2f}  n={len(s):3d}  (too few)")
        continue
    t0, r0, n = roi(s, 0)
    t10, _, _ = roi(s, 10)
    lo, hi = dayboot(s, 0)
    print(f"  gate<{g:.2f}  n={n:4d}  DK {t0:+7.2f}u ({r0:+7.2%}) CI[{lo:+7.2%},{hi:+7.2%}]  "
          f"+10c {t10:+7.2f}u")
print(f"  cells searched here: {cells}")

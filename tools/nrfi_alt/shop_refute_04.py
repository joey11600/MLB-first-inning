#!/usr/bin/env python3
"""Attack 4: does the shopped gain survive out-of-sample-in-time, and is the
297-bet population even a single deployable rule?"""
import csv
from collections import defaultdict, Counter
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


def to_c(o):
    return o if o > 0 else o + 200.0


def fr_c(c):
    return c if c >= 100 else c - 200.0


def bump(o, c):
    return fr_c(to_c(o) + c)


sub = []
for r in rows:
    if r["pick_side"] != "YRFI" or r["bet_placed"] != "Y":
        continue
    if r["graded_result"] not in ("WIN", "LOSS"):
        continue
    o = fnum(r["market_yrfi_odds"])
    if o is None:
        continue
    sub.append(dict(date=r["date"], win=r["graded_result"] == "WIN", o=o,
                    p=fnum(r["nrfi_prob"])))
sub.sort(key=lambda x: x["date"])

# --- is this one rule? distribution of the gate variable over time -----
print("--- p_nrfi of the 297 placed YRFI bets, by month (live gate is <0.36 today) ---")
bym = defaultdict(list)
for x in sub:
    bym[x["date"][:7]].append(x["p"])
for m in sorted(bym):
    v = np.array([p for p in bym[m] if p is not None])
    print(f"  {m}  n={len(v):3d}  p_nrfi min {v.min():.3f} med {np.median(v):.3f} "
          f"max {v.max():.3f}   share >=0.36: {(v>=0.36).mean():.1%}")
allp = np.array([x["p"] for x in sub if x["p"] is not None])
print(f"  ALL n={len(allp)}  share with p_nrfi >= 0.36 (would NOT be bet today): "
      f"{(allp>=0.36).mean():.1%}")


def dayboot(s, c=0, iters=20000, seed=5):
    d = defaultdict(list)
    for x in s:
        d[x["date"]].append(payout(bump(x["o"], c)) if x["win"] else -1.0)
    arrs = [np.array(v) for v in d.values()]
    rng = np.random.default_rng(seed)
    n = len(arrs)
    out = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, n, n)
        out[i] = sum(arrs[j].sum() for j in idx) / max(1, sum(len(arrs[j]) for j in idx))
    return np.percentile(out, [2.5, 97.5]), (np.array(out) > 0).mean()


def tot(s, c=0):
    return sum((payout(bump(x["o"], c)) if x["win"] else -1.0) for x in s)


print("\n--- OUT-OF-SAMPLE-IN-TIME: hold out the last N days, at each price level ---")
dates = sorted({x["date"] for x in sub})
for cut in ("2026-06-01", "2026-06-16", "2026-07-01"):
    tr = [x for x in sub if x["date"] < cut]
    te = [x for x in sub if x["date"] >= cut]
    print(f"\n  cut {cut}: train n={len(tr)} test n={len(te)}")
    for c in (0, 10):
        t_tr, t_te = tot(tr, c), tot(te, c)
        (lo, hi), pp = dayboot(te, c)
        print(f"    +{c:2d}c  train {t_tr:+7.2f}u ({t_tr/len(tr):+7.2%})   "
              f"TEST {t_te:+7.2f}u ({t_te/len(te):+7.2%})  "
              f"CI[{lo:+7.2%},{hi:+7.2%}]  P(>0)={pp:.2f}")

print("\n--- FULL-SAMPLE CIs at each price level ---")
for c in (-10, -5, 0, 5, 10, 15):
    t = tot(sub, c)
    (lo, hi), pp = dayboot(sub, c)
    print(f"  {c:+3d}c  {t:+7.2f}u  ROI {t/len(sub):+7.2%}  "
          f"day-block CI[{lo:+7.2%},{hi:+7.2%}]  P(ROI>0)={pp:.2f}")

print("\n--- 'the base is inside a 10-cent band of nothing' ---")
for c in (-12, -10, -8, -6, -4, -2, 0):
    t = tot(sub, c)
    print(f"  DK{c:+3d}c -> {t:+7.2f}u  ({t/len(sub):+7.2%})")

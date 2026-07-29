"""ANALYSIS ONLY -- compare the raw P(NRFI) implied by the STORED lambda
(the vintage the live system used) against the raw P(NRFI) that today's
LR weights produce from the stored features (what the replay tools use)."""
import sys, os, math
sys.path.insert(0, os.getcwd())
from tools.season_replay import load_season

rows, skipped = load_season()
print(f"graded rows loaded={len(rows)} skipped={skipped}")

d = []
for r in rows:
    lam_stored = r.get("lambda")
    if lam_stored is None or r["raw"] is None:
        continue
    lam_recomp = -math.log(max(1e-12, r["raw"]))
    d.append((r["date"], r["away"], r["home"], lam_stored, lam_recomp,
              math.exp(-lam_stored), r["raw"]))

gaps = [abs(a - b) for _, _, _, a, b, _, _ in d]
n = len(d)
print(f"n with both = {n}")
for thr in (0.01, 0.02, 0.05, 0.10, 0.20):
    c = sum(1 for g in gaps if g > thr)
    print(f"  |lambda_stored - (-ln raw_recomputed)| > {thr:4.2f}: {c:4d} ({100*c/n:5.1f}%)")
print(f"  max={max(gaps):.4f} mean={sum(gaps)/n:.4f}")

# same thing in probability units
pg = [abs(x[5] - x[6]) for x in d]
print(f"  raw prob gap: max={max(pg):.4f} mean={sum(pg)/n:.4f}")

# by month
from collections import defaultdict
b = defaultdict(list)
for x, g in zip(d, gaps):
    b[x[0][:7]].append(g)
print("\nby month (gap in lambda units):")
for m in sorted(b):
    v = b[m]
    print(f"  {m}: n={len(v):4d} mean={sum(v)/len(v):.4f} >0.05={sum(1 for z in v if z>0.05):4d}")

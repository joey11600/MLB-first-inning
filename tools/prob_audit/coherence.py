#!/usr/bin/env python3
"""ANALYSIS ONLY -- coherence checks across the stored probability columns."""
import csv, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))

def f(x):
    try:
        s = str(x).strip(); return float(s) if s else None
    except Exception: return None

# --- 1. P(over 1.5) must be <= P(yrfi) = P(>=1 run) ------------------------
viol = []
for r in rows:
    o, y = f(r.get("over_1_5_prob")), f(r.get("yrfi_prob"))
    if o is None or y is None: continue
    if o > y + 1e-9:
        viol.append((r["date"], f"{r['away_team']}@{r['home_team']}", o, y, o - y))
print(f"1. over_1_5_prob > yrfi_prob (P(>=2) > P(>=1)) : {len(viol)} of "
      f"{sum(1 for r in rows if f(r.get('over_1_5_prob')) is not None)} rows")
viol.sort(key=lambda v: -v[4])
for v in viol[:8]:
    print(f"     {v[0]} {v[1]}  over15={v[2]:.4f} yrfi={v[3]:.4f}  excess={v[4]:+.4f}")
if viol:
    import statistics
    print(f"     mean excess among violations: {statistics.mean(v[4] for v in viol):+.4f}")

# --- 2. under_1_5 + over_1_5 == 1 -----------------------------------------
d = [abs((f(r['over_1_5_prob']) + f(r['under_1_5_prob'])) - 1.0)
     for r in rows if f(r.get('over_1_5_prob')) is not None and f(r.get('under_1_5_prob')) is not None]
print(f"2. over+under - 1: n={len(d)} max={max(d):.2e}")

# --- 3. combined_lambda vs lambda_lr_total --------------------------------
pairs = [(f(r['combined_lambda']), f(r['lambda_lr_total']), f(r['yrfi_prob']), r)
         for r in rows if f(r.get('combined_lambda')) is not None and f(r.get('lambda_lr_total')) is not None]
diffs = [a - b for a, b, _, _ in pairs]
import statistics
print(f"3. combined_lambda vs lambda_lr_total: n={len(pairs)}")
print(f"   combined_lambda  mean={statistics.mean(p[0] for p in pairs):.4f} "
      f"min={min(p[0] for p in pairs):.4f} max={max(p[0] for p in pairs):.4f}")
print(f"   lambda_lr_total  mean={statistics.mean(p[1] for p in pairs):.4f} "
      f"min={min(p[1] for p in pairs):.4f} max={max(p[1] for p in pairs):.4f}")
print(f"   diff  mean={statistics.mean(diffs):+.4f} max|d|={max(abs(x) for x in diffs):.4f}")
# correlation
mx, my = statistics.mean(p[0] for p in pairs), statistics.mean(p[1] for p in pairs)
cov = sum((p[0]-mx)*(p[1]-my) for p in pairs)
sx = math.sqrt(sum((p[0]-mx)**2 for p in pairs)); sy = math.sqrt(sum((p[1]-my)**2 for p in pairs))
print(f"   pearson r(combined_lambda, lambda_lr_total) = {cov/(sx*sy):.4f}")
# rank-order disagreement: how often does sorting by combined_lambda invert
inv = 0; tot = 0
import random
random.seed(0)
sample = random.sample(pairs, min(600, len(pairs)))
for i in range(len(sample)):
    for j in range(i+1, len(sample)):
        a, b = sample[i], sample[j]
        if a[0] == b[0] or a[1] == b[1]: continue
        tot += 1
        if (a[0] > b[0]) != (a[1] > b[1]): inv += 1
print(f"   pairwise rank inversions between the two lambdas: {inv}/{tot} = {100*inv/tot:.1f}%")

# --- 4. Poisson-implied YRFI from combined_lambda vs model yrfi ------------
pd = [abs((1 - math.exp(-a)) - y) for a, b, y, _ in pairs]
print(f"4. |1-exp(-combined_lambda) - yrfi_prob| : mean={statistics.mean(pd):.4f} max={max(pd):.4f}")
pd2 = [abs((1 - math.exp(-b)) - y) for a, b, y, _ in pairs]
print(f"   |1-exp(-lambda_lr_total) - yrfi_prob| : mean={statistics.mean(pd2):.4f} max={max(pd2):.4f}"
      f"   (this is raw-vs-calibrated gap, expected non-zero)")

# --- 5. does calibration MOVE the pick side? ------------------------------
# raw = exp(-lambda_lr_total); compare which side raw vs calibrated implies at 0.50
flip = 0; n5 = 0
for a, b, y, r in pairs:
    raw = math.exp(-b); calp = f(r['nrfi_prob'])
    n5 += 1
    if (raw >= 0.5) != (calp >= 0.5): flip += 1
print(f"5. raw and calibrated disagree on which side of 0.50: {flip}/{n5} = {100*flip/n5:.1f}%")

# --- 6. rounding: stored values are 4dp; how much decision space is that? --
from collections import Counter
c = Counter(r['nrfi_prob'] for r in rows if r['nrfi_prob'])
print(f"6. distinct stored nrfi_prob values: {len(c)} over {sum(c.values())} rows")
print(f"   most common: {c.most_common(5)}")

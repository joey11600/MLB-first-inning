import csv, math, statistics as st
from pathlib import Path

rows = list(csv.DictReader(open("data/picks_2026.csv", encoding="utf-8")))
print("total rows:", len(rows))

def f(v):
    try:
        v = (v or "").strip()
        return float(v) if v else None
    except Exception:
        return None

cl_present = sum(1 for r in rows if f(r.get("combined_lambda")) is not None)
lr_present = sum(1 for r in rows if f(r.get("lambda_lr_total")) is not None)
print("combined_lambda populated:", cl_present)
print("lambda_lr_total populated:", lr_present)
# fallback would only fire when combined_lambda is missing
fallback = sum(1 for r in rows if f(r.get("combined_lambda")) is None and f(r.get("lambda_lr_total")) is not None)
print("rows where the ?? fallback actually fires:", fallback)

pairs = [(f(r["combined_lambda"]), f(r["lambda_lr_total"])) for r in rows
         if f(r.get("combined_lambda")) is not None and f(r.get("lambda_lr_total")) is not None]
print("paired rows:", len(pairs))
cl = [a for a,b in pairs]; lr = [b for a,b in pairs]
print("combined_lambda  mean %.4f  min %.4f  max %.4f" % (st.mean(cl), min(cl), max(cl)))
print("lambda_lr_total  mean %.4f  min %.4f  max %.4f" % (st.mean(lr), min(lr), max(lr)))
print("mean gap (cl - lr): %+.4f" % st.mean([a-b for a,b in pairs]))
# pearson
n=len(pairs); mx=st.mean(cl); my=st.mean(lr)
cov=sum((a-mx)*(b-my) for a,b in pairs)
r = cov/math.sqrt(sum((a-mx)**2 for a in cl)*sum((b-my)**2 for b in lr))
print("pearson r = %.4f" % r)

# pairwise rank inversion (sample capped for speed)
import random
random.seed(0)
samp = pairs if len(pairs)<=1200 else random.sample(pairs,1200)
inv=tot=ties=0
for i in range(len(samp)):
    for j in range(i+1,len(samp)):
        a1,b1=samp[i]; a2,b2=samp[j]
        if a1==a2 or b1==b2:
            ties+=1; continue
        tot+=1
        if (a1>a2) != (b1>b2): inv+=1
print("pairwise: %d comparable, %.2f%% inverted (ties %d)" % (tot, 100*inv/tot, ties))

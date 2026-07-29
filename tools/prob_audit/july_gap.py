"""ANALYSIS ONLY -- why do July rows show cal(exp(-lambda)) != stored nrfi_prob?"""
import csv, math, sys, os
sys.path.insert(0, os.getcwd())
from calibration import ProbCalibrator
cal = ProbCalibrator.load("data/calibration_v2.json")
rows=[r for r in csv.DictReader(open("data/picks_2026.csv",encoding="utf-8")) if r["date"]>="2026-07-01"]
def f(x):
    x=(x or "").strip()
    try: return float(x) if x else None
    except Exception: return None
d=[]
for r in rows:
    lam,p=f(r.get("lambda_lr_total")),f(r.get("nrfi_prob"))
    if lam is None or p is None: continue
    d.append((abs(cal.predict(math.exp(-lam))-p), r["date"], r["away_team"], r["home_team"],
              lam, cal.predict(math.exp(-lam)), p, r.get("bet_placed"), r.get("pick_label")))
d.sort(reverse=True)
print("worst 12 July rows:")
for e,dt,a,h,lam,c,p,bp,lbl in d[:12]:
    print(f"  {dt} {a}@{h} lam={lam:.4f} cal={c:.4f} stored={p:.4f} err={e:.4f} bet={bp!r} {lbl[:30]}")
print()
print("count err<1e-3:", sum(1 for x in d if x[0]<1e-3), "of", len(d))
from collections import Counter
print(Counter(x[1] for x in d if x[0]>=1e-3))

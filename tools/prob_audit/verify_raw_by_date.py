"""ANALYSIS ONLY -- same check, bucketed by date window."""
import csv, math, sys, os
sys.path.insert(0, os.getcwd())
from calibration import ProbCalibrator
cal = ProbCalibrator.load("data/calibration_v2.json")
rows = list(csv.DictReader(open("data/picks_2026.csv", encoding="utf-8")))
def f(x):
    x=(x or "").strip()
    try: return float(x) if x else None
    except Exception: return None
from collections import defaultdict
buck=defaultdict(list)
for r in rows:
    lam,p=f(r.get("lambda_lr_total")),f(r.get("nrfi_prob"))
    if lam is None or p is None: continue
    buck[r["date"][:7]].append(abs(cal.predict(math.exp(-lam))-p))
for m in sorted(buck):
    v=buck[m]; print(f"{m}: n={len(v):4d} max={max(v):.3e} mean={sum(v)/len(v):.3e}")
# today only, detail
print()
for r in rows:
    if r["date"]!="2026-07-28": continue
    lam,p=f(r.get("lambda_lr_total")),f(r.get("nrfi_prob"))
    if lam is None: continue
    raw=math.exp(-lam)
    print(f"  {r['away_team']}@{r['home_team']} lam={lam:.4f} raw_hat={raw:.6f} cal={cal.predict(raw):.6f} stored_nrfi={p:.4f} stored_yrfi={f(r.get('yrfi_prob'))}")

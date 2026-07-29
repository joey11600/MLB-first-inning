"""ANALYSIS ONLY -- verify whether nrfi_prob_raw is recoverable from
lambda_lr_total, i.e. whether the blank raw column is actually lossy."""
import csv, math, sys, os
sys.path.insert(0, os.getcwd())
from calibration import ProbCalibrator

cal = ProbCalibrator.load("data/calibration_v2.json")
rows = list(csv.DictReader(open("data/picks_2026.csv", encoding="utf-8")))

def f(x):
    try:
        x = (x or "").strip()
        return float(x) if x else None
    except Exception:
        return None

d = []
for r in rows:
    lam, p = f(r.get("lambda_lr_total")), f(r.get("nrfi_prob"))
    if lam is None or p is None:
        continue
    raw_hat = math.exp(-lam)
    d.append((r["date"], r["game_pk"], raw_hat, cal.predict(raw_hat), p))

errs = [abs(c - p) for _, _, _, c, p in d]
print(f"rows with both lambda_lr_total and nrfi_prob: {len(d)}")
print(f"|calibrate(exp(-lambda)) - stored nrfi_prob|: max={max(errs):.3e} mean={sum(errs)/len(errs):.3e}")
big = sorted(d, key=lambda t: -abs(t[3]-t[4]))[:8]
for dt, gp, rh, c, p in big:
    print(f"  {dt} {gp}: raw_hat={rh:.6f} cal={c:.6f} stored={p:.6f} d={c-p:+.2e}")

# precision of the reconstruction: lambda is stored to 4dp
print()
print("reconstruction precision from 4dp lambda: raw*5e-5 <= %.2e" % (5e-5))
raws = [t[2] for t in d]
print(f"raw_hat range: {min(raws):.4f} .. {max(raws):.4f}")

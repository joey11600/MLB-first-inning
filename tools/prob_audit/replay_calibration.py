#!/usr/bin/env python3
"""ANALYSIS ONLY. nrfi_prob_raw is blank in the CSV, but lambda_lr_total ==
-ln(raw) by construction, so raw = exp(-lambda_lr_total) recovers it to ~1e-4.
Replay the production calibrator over that and diff against stored nrfi_prob."""
import csv, math, sys, json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from calibration import ProbCalibrator

cals = {
    "v2 (prod)": ProbCalibrator.load(ROOT / "data" / "calibration_v2.json"),
    "v2 bak (pre-5/26)": ProbCalibrator.load(ROOT / "data" / "calibration_v2.json.bak-2026-05-26-prod-prephase"),
}

rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))

def f(x):
    try:
        s = str(x).strip()
        return float(s) if s else None
    except Exception:
        return None

# sensitivity of calibrator to the +-5e-5 quantization of lambda
def band(cal, raw):
    lo = cal.predict(raw * math.exp(-5e-5))
    hi = cal.predict(raw * math.exp(+5e-5))
    return min(lo, hi), max(lo, hi)

for name, cal in cals.items():
    print(f"\n===== calibrator: {name} =====")
    per_month = defaultdict(list)
    n = 0
    for r in rows:
        lt, cp = f(r.get("lambda_lr_total")), f(r.get("nrfi_prob"))
        if lt is None or cp is None:
            continue
        raw = math.exp(-lt)
        lo, hi = band(cal, raw)
        # distance outside the quantization band (0 if consistent)
        if cp < lo:
            d = cp - lo
        elif cp > hi:
            d = cp - hi
        else:
            d = 0.0
        # allow for the 4dp rounding of the stored value too
        if abs(d) <= 5e-5:
            d = 0.0
        per_month[r["date"][:7]].append(d)
        n += 1
    print(f"  rows compared: {n}")
    for m in sorted(per_month):
        ds = per_month[m]
        a = [abs(x) for x in ds]
        bad = sum(1 for x in a if x > 1e-4)
        print(f"   {m}: n={len(ds):4d}  max|d|={max(a):.4f}  mean|d|={sum(a)/len(a):.5f}  "
              f"n_mismatch={bad} ({100*bad/len(ds):.1f}%)")

# Which calibrator best explains each month?
print("\n===== per-row best-fit calibrator =====")
extra = {}
for p in sorted((ROOT / "data").glob("calibration*.json")):
    try:
        c = ProbCalibrator.load(p)
        if c.centers:
            extra[p.name] = c
    except Exception as e:
        pass
best = defaultdict(Counter)
for r in rows:
    lt, cp = f(r.get("lambda_lr_total")), f(r.get("nrfi_prob"))
    if lt is None or cp is None:
        continue
    raw = math.exp(-lt)
    scores = {nm: abs(c.predict(raw) - cp) for nm, c in extra.items()}
    nm = min(scores, key=scores.get)
    best[r["date"][:7]][nm if scores[nm] < 2e-3 else f"NONE(min={scores[nm]:.3f} via {nm})"] += 1
for m in sorted(best):
    print(f"  {m}: {best[m].most_common(4)}")

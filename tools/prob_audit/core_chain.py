#!/usr/bin/env python3
"""ANALYSIS ONLY -- audit of the core probability chain in picks_2026.csv."""
import csv, json, math, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from calibration import ProbCalibrator

cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")

def f(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(x)
    except Exception:
        return None

rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
print(f"total rows: {len(rows)}")

graded = [r for r in rows if (r.get("graded_result") or "").strip() not in ("", "PENDING", "POSTPONED")]
print(f"graded rows: {len(graded)}  (graded_result values: "
      f"{sorted(set((r.get('graded_result') or '').strip() for r in rows))})")

def stats(name, diffs, thresh=1e-6):
    if not diffs:
        print(f"  {name}: NO DATA")
        return
    a = [abs(d) for _, d in diffs]
    bad = [(k, d) for k, d in diffs if abs(d) > thresh]
    print(f"  {name}: n={len(diffs)} max|diff|={max(a):.3e} mean|diff|={sum(a)/len(a):.3e} "
          f"n>{thresh:g}={len(bad)}")
    for k, d in sorted(bad, key=lambda x: -abs(x[1]))[:8]:
        print(f"      worst: {k}  diff={d:+.6f}")
    return bad

def key(r):
    return f"{r['date']}|{r['away_team']}@{r['home_team']}|{r.get('game_pk')}"

for label, subset in (("ALL ROWS", rows), ("GRADED ONLY", graded)):
    print(f"\n=== {label} (n={len(subset)}) ===")

    # 1. nrfi_prob == calibrate(nrfi_prob_raw)
    d1 = []
    missing_raw = 0
    for r in subset:
        raw, cp = f(r.get("nrfi_prob_raw")), f(r.get("nrfi_prob"))
        if raw is None or cp is None:
            missing_raw += 1
            continue
        d1.append((key(r), cp - cal.predict(raw)))
    print(f"  rows missing raw or calibrated: {missing_raw}")
    bad1 = stats("1. nrfi_prob - calibrate(nrfi_prob_raw)", d1)

    # 2. yrfi_prob == 1 - nrfi_prob
    d2 = [(key(r), (f(r["nrfi_prob"]) + f(r["yrfi_prob"])) - 1.0)
          for r in subset if f(r.get("nrfi_prob")) is not None and f(r.get("yrfi_prob")) is not None]
    stats("2. nrfi_prob + yrfi_prob - 1", d2)

    # 2b. yrfi_prob_raw == 1 - nrfi_prob_raw
    d2b = [(key(r), (f(r["nrfi_prob_raw"]) + f(r["yrfi_prob_raw"])) - 1.0)
           for r in subset if f(r.get("nrfi_prob_raw")) is not None and f(r.get("yrfi_prob_raw")) is not None]
    stats("2b. nrfi_prob_raw + yrfi_prob_raw - 1", d2b)

    # 3. raw vs calibrated: are they ever equal (i.e. raw stored in calibrated col)?
    same = sum(1 for r in subset
               if f(r.get("nrfi_prob_raw")) is not None and f(r.get("nrfi_prob")) is not None
               and abs(f(r["nrfi_prob_raw"]) - f(r["nrfi_prob"])) < 1e-9)
    print(f"  3. rows where nrfi_prob == nrfi_prob_raw exactly: {same}")

    # 4. lambda_lr_total == -ln(raw)
    d4 = []
    for r in subset:
        raw, lt = f(r.get("nrfi_prob_raw")), f(r.get("lambda_lr_total"))
        if raw is None or lt is None or raw <= 0:
            continue
        d4.append((key(r), lt - (-math.log(raw))))
    stats("4. lambda_lr_total - (-ln(nrfi_prob_raw))", d4)

    # 4b. lambda_lr_total == lambda_lr_t1 + lambda_lr_b1
    d4b = []
    for r in subset:
        a, b, t = f(r.get("lambda_lr_t1")), f(r.get("lambda_lr_b1")), f(r.get("lambda_lr_total"))
        if None in (a, b, t):
            continue
        d4b.append((key(r), t - (a + b)))
    stats("4b. lambda_lr_total - (t1+b1)", d4b)

    # 4c. raw == (1-exp(-l_t1))... i.e. raw == exp(-l_t1)*exp(-l_b1)
    d4c = []
    for r in subset:
        a, b, raw = f(r.get("lambda_lr_t1")), f(r.get("lambda_lr_b1")), f(r.get("nrfi_prob_raw"))
        if None in (a, b, raw):
            continue
        d4c.append((key(r), raw - math.exp(-a) * math.exp(-b)))
    stats("4c. nrfi_prob_raw - exp(-l_t1)*exp(-l_b1)", d4c)

    # 5. combined_lambda vs lambda_lr_total
    d5 = []
    for r in subset:
        c, t = f(r.get("combined_lambda")), f(r.get("lambda_lr_total"))
        if None in (c, t):
            continue
        d5.append((key(r), c - t))
    if d5:
        a = [abs(x) for _, x in d5]
        print(f"  5. combined_lambda - lambda_lr_total: n={len(d5)} "
              f"max|d|={max(a):.4f} mean|d|={sum(a)/len(a):.4f} "
              f"n>0.01={sum(1 for x in a if x > 0.01)}")

    # 6. range checks
    esc = []
    for r in subset:
        for col in ("nrfi_prob", "yrfi_prob", "nrfi_prob_raw", "yrfi_prob_raw",
                    "over_1_5_prob", "under_1_5_prob", "implied_nrfi_prob", "implied_yrfi_prob"):
            v = f(r.get(col))
            if v is None:
                continue
            if not (0.0 <= v <= 1.0) or v != v:
                esc.append((key(r), col, v))
    print(f"  6. probabilities outside [0,1] or NaN: {len(esc)}")
    for e in esc[:10]:
        print("     ", e)

# distribution of calibrated nrfi_prob
vals = sorted(f(r["nrfi_prob"]) for r in rows if f(r.get("nrfi_prob")) is not None)
print(f"\ncalibrated nrfi_prob: min={vals[0]:.6f} max={vals[-1]:.6f} n={len(vals)}")
from collections import Counter
cnt = Counter(round(v, 4) for v in vals)
print("top-10 most frequent calibrated nrfi_prob values (plateau detector):")
for v, n in cnt.most_common(10):
    print(f"   nrfi={v:.4f}  yrfi={1-v:.4f}  n={n}")

rawvals = sorted(f(r["nrfi_prob_raw"]) for r in rows if f(r.get("nrfi_prob_raw")) is not None)
print(f"raw nrfi_prob_raw: min={rawvals[0]:.6f} max={rawvals[-1]:.6f} n={len(rawvals)}")

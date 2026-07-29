#!/usr/bin/env python3
"""ANALYSIS ONLY -- numeric consistency of the probability chain and of
the gate geometry (is the lambda floor binding, given the calibrator?)."""
from __future__ import annotations
import csv, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from calibration import ProbCalibrator  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402


def f(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))

print("=== live constants ===")
for k in ("_LR_STRONG_NRFI_P", "_LR_LEAN_NRFI_P", "_LR_PASS_LO_P", "_LR_LEAN_YRFI_P",
          "_LR_STRONG_YRFI_P", "_LR_LAMBDA_YRFI_FLOOR", "_LR_LAMBDA_NRFI_CEILING",
          "_FLAT_ZONE_DEMOTE_SIZE"):
    print(f"  {k:<26}{getattr(P, k)}")

# --- 1. calibrated == cal.predict(raw)? -----------------------------------
d = []
for r in rows:
    raw, p = f(r["nrfi_prob_raw"]), f(r["nrfi_prob"])
    if raw is None or p is None:
        continue
    d.append(abs(cal.predict(raw) - p))
print(f"\n=== stored nrfi_prob vs cal.predict(nrfi_prob_raw) on {len(d)} rows ===")
if d:
    print(f"  max |diff| = {max(d):.6f}   mean = {sum(d)/len(d):.6f}")
    print(f"  rows > 5e-5: {sum(1 for x in d if x > 5e-5)}")

# --- 2. nrfi_prob + yrfi_prob == 1 ---------------------------------------
s = [abs(f(r["nrfi_prob"]) + f(r["yrfi_prob"]) - 1.0)
     for r in rows if f(r["nrfi_prob"]) is not None and f(r["yrfi_prob"]) is not None]
print(f"\n=== nrfi_prob + yrfi_prob - 1 on {len(s)} rows ===")
print(f"  max |diff| = {max(s):.2e}")
oob = [r for r in rows if (f(r['nrfi_prob']) or 0.5) <= 0 or (f(r['nrfi_prob']) or 0.5) >= 1]
print(f"  rows with nrfi_prob outside (0,1): {len(oob)}")

# --- 3. lambda_lr_total == -ln(raw) --------------------------------------
d = []
for r in rows:
    raw, lam = f(r["nrfi_prob_raw"]), f(r["lambda_lr_total"])
    if raw is None or lam is None or raw <= 0:
        continue
    d.append(abs(-math.log(raw) - lam))
print(f"\n=== lambda_lr_total vs -ln(nrfi_prob_raw) on {len(d)} rows ===")
if d:
    print(f"  max |diff| = {max(d):.6f}   mean = {sum(d)/len(d):.6f}")

# --- 4. is the YRFI lambda floor binding given the calibrator? -----------
print("\n=== gate geometry: lambda floor vs calibrated gate ===")
for floor in (0.798, 0.838, 0.858):
    raw_max = math.exp(-floor)
    print(f"  floor {floor:.3f}  <=>  raw p_nrfi <= {raw_max:.4f}"
          f"  -> calibrated {cal.predict(raw_max):.4f}")
print(f"  STRONG YRFI needs calibrated p_nrfi < {P._LR_STRONG_YRFI_P}")
# find the raw value where calibrated crosses the STRONG gate
lo, hi = 0.0, 1.0
for _ in range(80):
    mid = (lo + hi) / 2
    if cal.predict(mid) < P._LR_STRONG_YRFI_P:
        lo = mid
    else:
        hi = mid
print(f"  calibrated crosses {P._LR_STRONG_YRFI_P} at raw p_nrfi ~= {lo:.4f}"
      f"  (= lambda {-math.log(lo):.4f})")
print(f"  calibrator range: predict(0)={cal.predict(0.0):.4f}  "
      f"predict(1)={cal.predict(1.0):.4f}")

# --- 5. how often did the floor actually demote a would-be STRONG YRFI? --
n_below_gate = n_demoted = 0
for r in rows:
    p, lam = f(r["nrfi_prob"]), f(r["lambda_lr_total"])
    if p is None or lam is None:
        continue
    if p >= P._LR_STRONG_YRFI_P:
        continue
    n_below_gate += 1
    fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, f(r["wx_temp_c"]),
                                   f(r["wx_wind_kmh"]), (f(r["wx_is_dome"]) or 0) >= .5)
    if lam < fl:
        n_demoted += 1
print(f"\n=== floor bite on the CURRENT gate (p_nrfi < {P._LR_STRONG_YRFI_P}) ===")
print(f"  rows below gate with a lambda: {n_below_gate}   demoted by floor: {n_demoted}")

# --- 6. NRFI ceiling bite (with NRFI re-enabled at 0.62) -----------------
n_hi = n_ceil = 0
for r in rows:
    p, lam = f(r["nrfi_prob"]), f(r["lambda_lr_total"])
    if p is None or lam is None or p < 0.62:
        continue
    n_hi += 1
    if lam > P._LR_LAMBDA_NRFI_CEILING:
        n_ceil += 1
print(f"\n=== NRFI ceiling bite if gate were 0.62: {n_hi} rows, {n_ceil} demoted ===")

# --- 7. calibrated max actually observed ---------------------------------
ps = [f(r["nrfi_prob"]) for r in rows if f(r["nrfi_prob"]) is not None]
print(f"\n=== observed calibrated p_nrfi: min {min(ps):.4f}  max {max(ps):.4f} "
      f"(n={len(ps)}) ===")
print(f"  rows >= 1.01 (STRONG NRFI gate): {sum(1 for p in ps if p >= 1.01)}")

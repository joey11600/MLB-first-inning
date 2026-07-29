#!/usr/bin/env python3
"""Independent verification of the claimed 'lambda-floor misattribution'
defect in tools/export_season_record.py::disposition.

Read-only. Recomputes everything from data/ + the shipped model files.
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P                       # noqa: E402
from recalibrate_v2 import ProbCalibrator                    # noqa: E402
from tools.season_replay import load_season                  # noqa: E402
from tools.export_season_record import disposition, FILL, NRFI_GATE  # noqa: E402

rows, skipped = load_season()
cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
dep = [cal.predict(r["raw"]) for r in rows]
GATE = P._LR_STRONG_YRFI_P

print(f"graded rows loaded : {len(rows)}  (skipped {skipped})")
print(f"live YRFI gate     : p_nrfi < {GATE}")
print(f"base lambda floor  : {P._LR_LAMBDA_YRFI_FLOOR}")
print(f"calibrator knots   : {len(cal.centers)}  top={max(cal.centers):.4f}")

# ---- 1. reproduce the reason histogram exactly as the tool computes it
d_y = disposition(rows, dep, side="YRFI", gate=GATE, fill=FILL)
hist = Counter(why for _r, _p, why, _x in d_y)
print("\n=== 1. disposition() YRFI reason histogram (as shipped) ===")
for k, v in hist.most_common():
    print(f"  {k:<14} {v}")

# ---- 2. of the lambda-floor rejects, how many ALSO fail the prob gate?
floor_rej = [(r, p) for r, p, why, _x in d_y if why == "lambda-floor"]
also_gate = [(r, p) for r, p in floor_rej if p >= GATE]
only_floor = [(r, p) for r, p in floor_rej if p < GATE]
print("\n=== 2. would the gate have rejected them anyway? ===")
print(f"  labelled lambda-floor        : {len(floor_rej)}")
print(f"  ... of which p >= gate too   : {len(also_gate)}   (label is a coin flip)")
print(f"  ... of which p <  gate       : {len(only_floor)}   (floor is the ONLY blocker)")

# ---- 3. is the floor ever *able* to be the binding constraint?
# lambda == -ln(raw_p_nrfi) by construction, so lambda < floor <=> raw > e^-floor.
# Walk the calibrator: what does it return at raw = e^-floor?
print("\n=== 3. can the floor bind at all, given lambda == -ln(raw)? ===")
for fl in sorted({round(P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, t, w, d), 3)
                  for t in (5.0, 20.0, 30.0) for w in (5.0, 30.0) for d in (False, True)}):
    raw_at = math.exp(-fl)
    print(f"  floor {fl:.3f}  ->  lambda<floor means raw p_nrfi > {raw_at:.4f}"
          f"  ->  calibrated = {cal.predict(raw_at):.4f}"
          f"   {'>= gate (gate would reject too)' if cal.predict(raw_at) >= GATE else '*** BELOW GATE -> floor CAN bind'}")

# ---- 4. how self-consistent is the STORED lambda vs -ln(raw)?
diffs = []
for r in rows:
    if r["lambda"] is None:
        continue
    diffs.append((r["lambda"] - (-math.log(r["raw"])), r))
if diffs:
    ad = [abs(d) for d, _ in diffs]
    ad.sort()
    print("\n=== 4. stored lambda_lr_total  vs  -ln(recomputed raw) ===")
    print(f"  rows with a stored lambda : {len(diffs)} / {len(rows)}")
    print(f"  mean |diff| : {sum(ad)/len(ad):.5f}")
    print(f"  median      : {ad[len(ad)//2]:.5f}")
    print(f"  p95 / max   : {ad[int(.95*len(ad))]:.5f} / {ad[-1]:.5f}")

# ---- 5. rerun disposition with a SELF-CONSISTENT lambda = -ln(raw)
rows2 = [dict(r, **{"lambda": -math.log(r["raw"])}) for r in rows]
d_y2 = disposition(rows2, dep, side="YRFI", gate=GATE, fill=FILL)
hist2 = Counter(why for _r, _p, why, _x in d_y2)
print("\n=== 5. same histogram with lambda := -ln(raw) (self-consistent) ===")
for k, v in hist2.most_common():
    print(f"  {k:<14} {v}")

# ---- 6. does the misattribution change any BET? (order swap test)
def disposition_gate_first(rows_, probs_):
    out = []
    for r, p in zip(rows_, probs_):
        if p is None:
            out.append((r, p, "warmup")); continue
        if p >= GATE:
            out.append((r, p, "gate")); continue
        fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, r["wx_temp"],
                                       r["wx_wind"], r["wx_dome"])
        if r["lambda"] is not None and r["lambda"] < fl:
            out.append((r, p, "lambda-floor")); continue
        out.append((r, p, "candidate"))
    return out

swap = disposition_gate_first(rows, dep)
print("\n=== 6. gate-first ordering: does the SET of candidates change? ===")
print("  ", Counter(why for _r, _p, why in swap).most_common())
a = {id(r) for r, _p, why, _x in d_y if why == "candidate"}
b = {id(r) for r, _p, why in swap if why == "candidate"}
print(f"  candidate set identical: {a == b}   (|shipped|={len(a)} |swapped|={len(b)})")

# ---- 7. what does PRODUCTION actually do with these games?
# classify_pick_lr only consults the floor when p_nrfi < 0.50.
print("\n=== 7. production reachability of the floor check ===")
never = sum(1 for r, p in floor_rej if p >= P._LR_LEAN_NRFI_P)
print(f"  _LR_LEAN_NRFI_P = {P._LR_LEAN_NRFI_P}; classify_pick_lr consults the")
print(f"  YRFI floor only when p_nrfi < that value.")
print(f"  lambda-floor-labelled games with p_nrfi >= {P._LR_LEAN_NRFI_P}: {never}")
print(f"  -> for those {never}, production never evaluated the floor at all;")
print(f"     they are NRFI-side classifications (LEAN NRFI / STRONG NRFI / PASS).")

# ---- 8. NRFI side: same ordering question
d_n = disposition(rows, dep, side="NRFI", gate=NRFI_GATE, fill=FILL)
print("\n=== 8. NRFI side reason histogram (gate tested FIRST there) ===")
print("  ", Counter(why for _r, _p, why, _x in d_n).most_common())

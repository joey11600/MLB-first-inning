#!/usr/bin/env python3
"""Independent verification of the 'stale gate literal' claim.

Checks:
  1. What is the live gate constant, and what does thresholds.json say?
  2. Does gate_validation.py's '<-- LIVE' marker land on the live gate?
  3. How many bets does 0.36 select vs 0.40 (the 86 vs 126 claim)?
  4. Does GATE = 0.64 on p_yrfi == the live gate on p_nrfi?
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.gate_validation import select, walk_forward_probs, flat_stats  # noqa: E402
from tools.season_replay import load_season  # noqa: E402

print("=== 1. live constants ===")
print(f"  P._LR_STRONG_YRFI_P            = {P._LR_STRONG_YRFI_P}")
th = json.loads((ROOT / "data" / "thresholds.json").read_text())
print(f"  data/thresholds.json strongYrfiP = {th['strongYrfiP']}")

print("\n=== 2. the '<-- LIVE' marker in gate_validation.py ===")
src = (ROOT / "tools" / "gate_validation.py").read_text().splitlines()
for i, ln in enumerate(src, 1):
    if "LIVE" in ln and "abs(" in ln:
        print(f"  line {i}: {ln.strip()}")
        lit = 0.36
        print(f"  marker fires on gate == {lit}; live gate is {P._LR_STRONG_YRFI_P}")
        print(f"  MISMATCH: {lit != P._LR_STRONG_YRFI_P}")

print("\n=== 3. bet-population size at each gate (walk-forward, fill -125) ===")
rows, _ = load_season()
wf = walk_forward_probs(rows)
for g in (0.44, 0.40, 0.36, 0.33):
    for fill, lab in ((-125, "fill -125"), (None, "real only")):
        b = select(rows, wf, side="YRFI", gate=g, fill=fill)
        n, w, pl, need = flat_stats(b)
        tag = "  <-- ACTUAL LIVE GATE" if abs(g - P._LR_STRONG_YRFI_P) < 1e-9 else ""
        tag += "  <-- marked LIVE by the report" if abs(g - 0.36) < 1e-9 else ""
        print(f"  gate {g:.2f} {lab:<10} bets={n:>4} hit={100*w/max(n,1):>5.1f}% "
              f"flat={pl:>+8.2f}u{tag}")

print("\n=== 4. p_yrfi >= 0.64 vs the live p_nrfi < 0.40 gate ===")
print(f"  GATE=0.64 on p_yrfi  <=>  p_nrfi <= 0.36")
print(f"  live gate            <=>  p_nrfi <  {P._LR_STRONG_YRFI_P} "
      f"<=> p_yrfi > {1 - P._LR_STRONG_YRFI_P:.2f}")
n36 = len(select(rows, wf, side="YRFI", gate=0.36, fill=-125))
n40 = len(select(rows, wf, side="YRFI", gate=0.40, fill=-125))
print(f"  population understated by {n40 - n36} bets "
      f"({100 * (n40 - n36) / max(n40, 1):.0f}% of the live population)")

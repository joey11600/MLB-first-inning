#!/usr/bin/env python3
"""Part 2: does the misattributed label actually REACH the dashboard, and
how many games does the operator actually see it on?  Read-only."""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P                        # noqa: E402
from recalibrate_v2 import ProbCalibrator                     # noqa: E402
from tools.season_replay import load_season                   # noqa: E402
from tools.export_season_record import disposition, FILL      # noqa: E402

GATE = P._LR_STRONG_YRFI_P
rows, _ = load_season()
cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
dep = [cal.predict(r["raw"]) for r in rows]
d_y = disposition(rows, dep, side="YRFI", gate=GATE, fill=FILL)

# --- A. the "16": are they purely a stored-vs-recomputed lambda artefact?
print("=== A. the 16 'floor is the only blocker' games ===")
only = [(r, p) for r, p, why, _x in d_y if why == "lambda-floor" and p < GATE]
print(f"  count = {len(only)}")
print(f"  {'date':<11}{'game':<12}{'p_cal':>7}{'stored_l':>10}{'-ln(raw)':>10}{'floor':>8}  self-consistent verdict")
for r, p in only:
    fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, r["wx_temp"],
                                   r["wx_wind"], r["wx_dome"])
    sc = -math.log(r["raw"])
    print(f"  {r['date']:<11}{r['away']+'@'+r['home']:<12}{p:>7.4f}"
          f"{r['lambda']:>10.4f}{sc:>10.4f}{fl:>8.3f}  "
          f"{'floor still bites' if sc < fl else 'floor does NOT bite'}")
n_art = sum(1 for r, p in only
            if -math.log(r["raw"]) >= P._weather_adjusted_floor(
                P._LR_LAMBDA_YRFI_FLOOR, r["wx_temp"], r["wx_wind"], r["wx_dome"]))
print(f"  purely a stored-vs-recomputed lambda artefact: {n_art}/{len(only)}")

# --- B. under self-consistent lambda, is the floor EVER the sole blocker?
rows2 = [dict(r, **{"lambda": -math.log(r["raw"])}) for r in rows]
d2 = disposition(rows2, dep, side="YRFI", gate=GATE, fill=FILL)
sole2 = [1 for r, p, why, _x in d2 if why == "lambda-floor" and p < GATE]
tot2 = [1 for r, p, why, _x in d2 if why == "lambda-floor"]
print("\n=== B. self-consistent lambda ===")
print(f"  total lambda-floor labels : {len(tot2)}   <-- claim said this drops to 0")
print(f"  floor is the SOLE blocker : {len(sole2)}  <-- this is what drops to 0")

# --- C. what the operator actually SEES: skip codes in the shipped JSON
rec = json.loads((ROOT / "data" / "season_record.json").read_text(encoding="utf-8"))
print("\n=== C. skip codes actually present in data/season_record.json days[] ===")
for which in ("projected", "real"):
    blk = rec.get(which)
    if not blk:
        continue
    codes, sides, acts = Counter(), Counter(), Counter()
    n_games = 0
    for d in blk["days"]:
        for g in d["games"]:
            n_games += 1
            acts[g["record"]["action"]] += 1
            if g["record"]["action"] == "SKIP":
                codes[g["record"].get("code")] += 1
                sides[(g["record"].get("code"), g["side"])] += 1
    print(f"  [{which}] days={len(blk['days'])} game-entries={n_games} {dict(acts)}")
    for k, v in codes.most_common():
        print(f"      code={k:<16} {v}")
    for k, v in sides.most_common():
        if k[0] == "lambda_floor":
            print(f"      lambda_floor on side {k[1]}: {v}")

# --- D. for the lambda_floor entries the operator sees, what was p?
print("\n=== D. the lambda_floor entries the operator can actually see ===")
for which in ("projected", "real"):
    blk = rec.get(which)
    if not blk:
        continue
    hit = [(d["date"], g) for d in blk["days"] for g in d["games"]
           if g["record"].get("code") == "lambda_floor"]
    print(f"  [{which}] {len(hit)} entries")
    over = sum(1 for _d, g in hit if (g["modelP"] or 0) >= GATE)
    print(f"      of which modelP >= {GATE} (gate would have rejected anyway): {over}")
    for dt, g in hit[:12]:
        print(f"      {dt}  {g['game']:<12} {g['side']:<5} p={g['modelP']}"
              f"  reason={g['record']['reason']!r}")

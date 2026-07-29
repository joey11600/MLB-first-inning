#!/usr/bin/env python3
"""Follow-ups: (a) lambda_lr_total == -ln(raw p_nrfi) identity,
(b) is combined_lambda the OPERATOR-FACING lambda (dashboard/discovery),
(c) blast radius of tracker._classify_tentative_lean stale constants."""
from __future__ import annotations
import csv, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
rows = list((ROOT / "data" / "picks_2026.csv").open(encoding="utf-8"))
import csv as _csv
rows = list(_csv.DictReader((ROOT / "data" / "picks_2026.csv").open(encoding="utf-8")))

def f(v):
    try:
        s = (v or "").strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None

print("=== (a) IDENTITY: lambda_lr_total == -ln(nrfi_prob_raw)? ===")
errs = []
n_raw = 0
for r in rows:
    raw = f(r.get("nrfi_prob_raw"))
    lt = f(r.get("lambda_lr_total"))
    if raw is None or lt is None or raw <= 0:
        continue
    n_raw += 1
    errs.append(abs(-math.log(raw) - lt))
if errs:
    print(f"  n={n_raw}  mean|err|={sum(errs)/len(errs):.6f}  max|err|={max(errs):.6f}")
else:
    print("  no rows carry BOTH nrfi_prob_raw and lambda_lr_total")

# how many rows have raw at all
have_raw = sum(1 for r in rows if f(r.get("nrfi_prob_raw")) is not None)
have_lt  = sum(1 for r in rows if f(r.get("lambda_lr_total")) is not None)
have_cl  = sum(1 for r in rows if f(r.get("combined_lambda")) is not None)
print(f"  coverage: nrfi_prob_raw={have_raw}  lambda_lr_total={have_lt}  combined_lambda={have_cl}  (of {len(rows)})")

# Reconstruct raw from the halves instead, where raw column is missing.
print("\n  fallback identity via halves: lambda_lr_total == lambda_lr_t1 + lambda_lr_b1 ?")
e2 = []
for r in rows:
    a, b, t = f(r.get("lambda_lr_t1")), f(r.get("lambda_lr_b1")), f(r.get("lambda_lr_total"))
    if None in (a, b, t):
        continue
    e2.append(abs(a + b - t))
print(f"  n={len(e2)}  max|err|={max(e2):.6f}" if e2 else "  none")

print("\n=== (b) which lambda does the OPERATOR-FACING tooling use? ===")
for path, needle in [
    ("tools/cluster_discovery.py", "combined_lambda"),
    ("dashboard/lib/board-supabase.ts", "combined_lambda"),
    ("dashboard/app/api/shadow-pnl/route.ts", "combined_lambda"),
]:
    txt = (ROOT / path).read_text(encoding="utf-8", errors="replace")
    print(f"  {path}: mentions combined_lambda = {needle in txt}  "
          f"| mentions lambda_lr_total = {'lambda_lr_total' in txt}")

print("\n=== (c) tracker._classify_tentative_lean vs production classify_pick_lr ===")
import tracker, mlb_first_inning_predictor as mp
print(f"  production _LR_STRONG_NRFI_P     = {mp._LR_STRONG_NRFI_P}   (>=1.0 == NRFI betting DISABLED)")
print(f"  tracker hardcodes NRFI threshold = 0.56")
print(f"  production _LR_LAMBDA_YRFI_FLOOR = {mp._LR_LAMBDA_YRFI_FLOOR}")
print(f"  tracker hardcodes lambda floor   = 0.78")

# Compare tracker's mirror against the real classifier on every row.
mismatch_side = 0
mismatch_any = 0
considered = 0
examples = []
for r in rows:
    p = f(r.get("nrfi_prob"))
    lt = f(r.get("lambda_lr_total"))
    cl = f(r.get("combined_lambda"))
    dp = r.get("blended_inputs")
    if p is None or lt is None or cl is None:
        continue
    try:
        dpi = int(float(dp)) if (dp or "").strip() else 4
    except ValueError:
        dpi = 4
    considered += 1
    t_side, t_str = tracker._classify_tentative_lean(p, cl)
    try:
        p_side, p_str = mp.classify_pick_lr(p, dpi, lt)
    except Exception as exc:
        print(f"  classify_pick_lr failed: {exc!r}")
        break
    if t_side != p_side:
        mismatch_side += 1
        if len(examples) < 5:
            examples.append((r.get("date"), r.get("away_team"), r.get("home_team"),
                             p, cl, lt, f"{t_side}/{t_str}", f"{p_side}/{p_str}"))
    if (t_side, t_str) != (p_side, p_str):
        mismatch_any += 1
print(f"  rows compared: {considered}")
print(f"  SIDE disagreement (tracker mirror vs production): {mismatch_side} "
      f"({100*mismatch_side/max(1,considered):.1f}%)")
print(f"  side+strength disagreement: {mismatch_any}")
for e in examples:
    print(f"    {e[0]} {e[1]}@{e[2]}  p={e[3]:.4f} cl={e[4]:.4f} lr={e[5]:.4f}  "
          f"tracker={e[6]}  production={e[7]}")

# Isolate: how much of the mismatch is the lambda column vs the constants?
only_lambda = 0
for r in rows:
    p = f(r.get("nrfi_prob")); cl = f(r.get("combined_lambda")); lt = f(r.get("lambda_lr_total"))
    if None in (p, cl, lt):
        continue
    if tracker._classify_tentative_lean(p, cl) != tracker._classify_tentative_lean(p, lt):
        only_lambda += 1
print(f"\n  attribution: swapping ONLY the lambda column (cl -> lr) inside tracker's "
      f"mirror flips {only_lambda} rows")
print(f"               the remaining {mismatch_side - 0} side-mismatches vs production are "
      f"driven by the stale 0.56 / 0.78 constants")

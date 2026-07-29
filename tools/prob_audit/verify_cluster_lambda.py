#!/usr/bin/env python3
"""Independent verification of the reported loss-cluster lambda defect.

Claim under test:
  tools/loss_cluster_monitor.py:120 (and apply_cluster_demotion.py:95)
  read `combined_lambda` (legacy V2 Poisson) instead of the production
  LR chain's `lambda_lr_total`.

ANALYSIS ONLY -- reads the ledger, writes nothing.
"""
from __future__ import annotations

import ast
import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "picks_2026.csv"


def f(v):
    try:
        s = (v or "").strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
print(f"ledger rows: {len(rows)}")

pairs = [
    (f(r["combined_lambda"]), f(r["lambda_lr_total"]), r)
    for r in rows
    if f(r.get("combined_lambda")) is not None
    and f(r.get("lambda_lr_total")) is not None
]
print(f"rows with BOTH lambda columns: {len(pairs)}")

cl = [p[0] for p in pairs]
lt = [p[1] for p in pairs]
n = len(cl)

mc, ml = sum(cl) / n, sum(lt) / n
sc = math.sqrt(sum((x - mc) ** 2 for x in cl) / n)
sl = math.sqrt(sum((x - ml) ** 2 for x in lt) / n)
cov = sum((a - mc) * (b - ml) for a, b in zip(cl, lt)) / n
r = cov / (sc * sl)

diffs = [a - b for a, b in zip(cl, lt)]
print("\n--- CHECK 1: are the two lambdas the same quantity? ---")
print(f"  mean combined_lambda  = {mc:.4f}   sd {sc:.4f}")
print(f"  mean lambda_lr_total  = {ml:.4f}   sd {sl:.4f}")
print(f"  pearson r             = {r:.4f}")
print(f"  mean(combined - lr)   = {sum(diffs)/n:+.4f}")
print(f"  max |combined - lr|   = {max(abs(d) for d in diffs):.4f}")

# pairwise rank inversions -- O(n^2) is fine at this size but subsample
# for speed if huge.
inv = 0
tot = 0
for i in range(n):
    ai, bi = cl[i], lt[i]
    for j in range(i + 1, n):
        aj, bj = cl[j], lt[j]
        if ai == aj or bi == bj:
            continue
        tot += 1
        if (ai < aj) != (bi < bj):
            inv += 1
print(f"  pairwise rank inversions = {inv}/{tot} = {100*inv/tot:.1f}%")

# --- CHECK 2: which lambda actually reconstructs the model's own YRFI? ---
print("\n--- CHECK 2: which lambda IS the model's expected 1st-inning runs? ---")
e_cl, e_lt, e_raw = [], [], []
for a, b, row in pairs:
    y = f(row.get("yrfi_prob"))
    yraw = f(row.get("yrfi_prob_raw"))
    if y is None:
        continue
    e_cl.append(abs(1 - math.exp(-a) - y))
    e_lt.append(abs(1 - math.exp(-b) - y))
    if yraw is not None:
        e_raw.append(abs(1 - math.exp(-b) - yraw))
print(f"  |1-exp(-combined_lambda) - yrfi_prob|   mean {sum(e_cl)/len(e_cl):.4f}  (n={len(e_cl)})")
print(f"  |1-exp(-lambda_lr_total) - yrfi_prob|   mean {sum(e_lt)/len(e_lt):.4f}")
if e_raw:
    print(f"  |1-exp(-lambda_lr_total) - yrfi_prob_RAW| mean {sum(e_raw)/len(e_raw):.6f}  <- identity check")
    print(f"      max {max(e_raw):.6f}")

# --- CHECK 3: is the monitor's line-120 lambda read actually USED? ---
print("\n--- CHECK 3: static check -- is `lam` at loss_cluster_monitor.py:120 used? ---")
src = (ROOT / "tools" / "loss_cluster_monitor.py").read_text(encoding="utf-8")
tree = ast.parse(src)
for fn in ast.walk(tree):
    if isinstance(fn, ast.FunctionDef) and fn.name in (
        "_match_strong_nrfi_marginal",
        "_match_yrfi_deep",
    ):
        names = [
            nd.id for nd in ast.walk(fn)
            if isinstance(nd, ast.Name) and isinstance(nd.ctx, ast.Load)
        ]
        reads_lam_col = "combined_lambda" in ast.dump(fn)
        print(f"  {fn.name}: mentions 'combined_lambda' column = {reads_lam_col}")
        print(f"      local `lam` LOADED (used) anywhere = {'lam' in names}")
        # show the return expression(s)
        for nd in ast.walk(fn):
            if isinstance(nd, ast.Return):
                print(f"      return: {ast.unparse(nd.value)}")

# --- CHECK 4: how many rows would a lambda-banded predicate mis-select? ---
print("\n--- CHECK 4: hypothetical lambda band 0.80-1.30 (the ORIGINAL 5/09 predicate) ---")
lo, hi = 0.80, 1.30
sel_c = {id(row) for a, b, row in pairs if lo <= a <= hi}
sel_l = {id(row) for a, b, row in pairs if lo <= b <= hi}
print(f"  rows selected using combined_lambda : {len(sel_c)}")
print(f"  rows selected using lambda_lr_total : {len(sel_l)}")
print(f"  overlap                             : {len(sel_c & sel_l)}")
print(f"  selected by combined but NOT by lr  : {len(sel_c - sel_l)}")
print(f"  selected by lr but NOT by combined  : {len(sel_l - sel_c)}")
jac = len(sel_c & sel_l) / max(1, len(sel_c | sel_l))
print(f"  Jaccard overlap                     : {jac:.3f}")

# --- CHECK 5: does the CURRENT config select anything at all? ---
print("\n--- CHECK 5: is the demotion path live? ---")
import json
cfg = json.loads((ROOT / "data" / "cluster_demotions.json").read_text(encoding="utf-8"))
for d in cfg.get("demotions", []):
    print(f"  id={d.get('id')}  active={d.get('active')}  "
          f"has_combined_lambda_band={'combined_lambda' in d}")

# --- CHECK 6: tracker._classify_tentative_lean lambda source ---
print("\n--- CHECK 6: tracker._classify_tentative_lean (0.78 floor on combined_lambda) ---")
sys.path.insert(0, str(ROOT))
import tracker  # noqa: E402
flips = 0
considered = 0
for row in rows:
    p = f(row.get("nrfi_prob"))
    a = f(row.get("combined_lambda"))
    b = f(row.get("lambda_lr_total"))
    if p is None or a is None or b is None:
        continue
    considered += 1
    cur = tracker._classify_tentative_lean(p, a)          # what code does
    alt = tracker._classify_tentative_lean(p, b)          # LR lambda instead
    if cur != alt:
        flips += 1
print(f"  rows considered: {considered}   verdict differs when fed lambda_lr_total: {flips}")

# and the floor-constant divergence
print(f"  tracker floor hardcoded in _classify_tentative_lean : 0.78")
import mlb_first_inning_predictor as mp  # noqa: E402
print(f"  predictor _LR_LAMBDA_YRFI_FLOOR                     : {mp._LR_LAMBDA_YRFI_FLOOR}")
print(f"  predictor _LR_STRONG_NRFI_P                         : {mp._LR_STRONG_NRFI_P}")
print(f"  predictor _LR_LEAN_YRFI_P                           : {getattr(mp,'_LR_LEAN_YRFI_P','?')}")

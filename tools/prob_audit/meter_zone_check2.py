#!/usr/bin/env python3
"""Meter zones vs the model's PROBABILITY band (lambda gates assumed to pass).

classify_pick_lr(p, dp, lambda_total=None) SKIPS the lambda gate, which for
the 0.44 < p_nrfi < 0.50 band means it falls through to PASS -- so passing
None does NOT give you the pure probability zone.  This script builds the
probability zone explicitly, then counts meter mismatches.
"""
import csv, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import mlb_first_inning_predictor as P

STRONG_NRFI_MAX, LEAN_NRFI_MAX, PASS_MAX, LEAN_YRFI_MAX = 0.40, 0.47, 0.53, 0.60


def meter_zone(y):
    if y < STRONG_NRFI_MAX: return "STRONG NRFI"
    if y < LEAN_NRFI_MAX:   return "LEAN NRFI"
    if y < PASS_MAX:        return "PASS"
    if y < LEAN_YRFI_MAX:   return "LEAN YRFI"
    return "STRONG YRFI"


def prob_band(p):
    """classify_pick_lr's probability zone, lambda gates assumed satisfied."""
    if p >= P._LR_STRONG_NRFI_P: return "STRONG NRFI"
    if p >= P._LR_LEAN_NRFI_P:   return "LEAN NRFI"
    if p >  P._LR_PASS_LO_P:     return "LEAN YRFI"
    if p >= P._LR_PASS_LO_P:     return "PASS"
    if p >= P._LR_STRONG_YRFI_P: return "LEAN YRFI"
    return "STRONG YRFI"


def f(x):
    try: return float(x)
    except Exception: return None


rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
mism = Counter(); ex = {}; tot = 0
bet_mism = Counter(); nbet = 0
for r in rows:
    p = f(r["nrfi_prob"]); y = f(r["yrfi_prob"])
    if p is None or y is None: continue
    tot += 1
    yd = (round(y * 1000) / 10) / 100.0
    mz, mb = meter_zone(yd), prob_band(p)
    if mz != mb:
        mism[(mz, mb)] += 1
        ex.setdefault((mz, mb), (r["date"], r["away_team"], r["home_team"], p, yd))
        if r.get("bet_placed") == "Y":
            bet_mism[(mz, mb)] += 1

print(f"rows: {tot}")
tm = sum(mism.values())
print(f"meter zone != model probability band: {tm} ({100*tm/tot:.1f}%)")
for k, c in mism.most_common():
    d = ex[k]
    print(f"  meter={k[0]:12s} model={k[1]:12s} n={c:4d}  (bet_placed=Y: {bet_mism[k]:3d})"
          f"  e.g. {d[0]} {d[1]}@{d[2]} p_nrfi={d[3]:.4f} yrfi_disp={d[4]:.3f}")

print("\n== boundary map, P(YRFI) space ==")
prev = None; x = 0.30
while x <= 0.7500001:
    b = prob_band(round(1.0 - x, 6))
    if b != prev:
        print(f"  model: P(YRFI) >= {x:.4f} -> {b}")
        prev = b
    x = round(x + 0.0001, 6)
print("  meter: 0.00-0.40 STRONG NRFI | 0.40-0.47 LEAN NRFI | 0.47-0.53 PASS "
      "| 0.53-0.60 LEAN YRFI | 0.60+ STRONG YRFI")

# does the meter ever paint a band that CONTRADICTS the row's stored pick?
print("\n== meter vs the row's own stored pick_side/pick_strength ==")
c2 = Counter(); t2 = 0
for r in rows:
    y = f(r["yrfi_prob"])
    if y is None: continue
    stored = (r["pick_side"] or "").strip()
    st = (r["pick_strength"] or "").strip()
    if not stored: continue
    lab = "PASS" if stored == "PASS" else f"{st} {stored}"
    mz = meter_zone((round(y*1000)/10)/100.0)
    t2 += 1
    if mz != lab: c2[(mz, lab)] += 1
print(f"  {sum(c2.values())} / {t2} disagree")
for k, c in c2.most_common(10):
    print(f"    meter={k[0]:12s} stored={k[1]:14s} n={c}")

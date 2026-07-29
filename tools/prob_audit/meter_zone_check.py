#!/usr/bin/env python3
"""Replay the dashboard LambdaMeter zone painting vs classify_pick_lr.

ANALYSIS ONLY.  Reads data/picks_2026.csv, recomputes:
  - meter zone  : the band LambdaMeter.tsx paints, from yrfi_prob
                  (exactly as GameDetails passes it: yrfiPct/100 where
                   yrfiPct = round(yrfi_prob*1000)/10)
  - model band  : classify_pick_lr() with the CURRENT live constants
and diffs them.
"""
import csv, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import mlb_first_inning_predictor as P

STRONG_NRFI_MAX = 0.40
LEAN_NRFI_MAX   = 0.47
PASS_MAX        = 0.53
LEAN_YRFI_MAX   = 0.60


def meter_zone(y: float) -> str:
    if y < STRONG_NRFI_MAX: return "STRONG NRFI"
    if y < LEAN_NRFI_MAX:   return "LEAN NRFI"
    if y < PASS_MAX:        return "PASS"
    if y < LEAN_YRFI_MAX:   return "LEAN YRFI"
    return "STRONG YRFI"


def band(side: str, strength: str) -> str:
    if side == "PASS":
        return "PASS"
    return f"{strength} {side}"


def f(x):
    try:
        return float(x)
    except Exception:
        return None


rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
print(f"ledger rows: {len(rows)}")

print("live constants:")
for k in ("_LR_STRONG_NRFI_P", "_LR_LEAN_NRFI_P", "_LR_PASS_LO_P",
          "_LR_LEAN_YRFI_P", "_LR_STRONG_YRFI_P", "_LR_LAMBDA_YRFI_FLOOR"):
    print(f"  {k:26s} = {getattr(P, k)}")

mismatch = Counter()
mismatch_nolam = Counter()
n = n_nolam = 0
tot = 0
examples = {}

for r in rows:
    p_nrfi = f(r["nrfi_prob"])
    y_raw  = f(r["yrfi_prob"])
    if p_nrfi is None or y_raw is None:
        continue
    tot += 1
    # dashboard rounding path
    y_disp = (round(y_raw * 1000) / 10) / 100.0
    mz = meter_zone(y_disp)

    lam = f(r["lambda_lr_total"])
    side, strength = P.classify_pick_lr(
        p_nrfi, 4, lam,
        f(r["wx_temp_c"]), f(r["wx_wind_kmh"]),
        str(r["wx_is_dome"]).strip().lower() in ("true", "1", "yes"),
    )
    b = band(side, strength)
    # lambda-free band (pure probability zone, what a meter *could* show)
    side2, strength2 = P.classify_pick_lr(p_nrfi, 4, None)
    b2 = band(side2, strength2)

    if mz != b:
        n += 1
        mismatch[(mz, b)] += 1
    if mz != b2:
        n_nolam += 1
        mismatch_nolam[(mz, b2)] += 1
        examples.setdefault((mz, b2), (r["date"], r["away_team"], r["home_team"],
                                       p_nrfi, y_disp, lam))

print(f"\nrows with probs: {tot}")
print(f"\n== meter zone vs classify_pick_lr (WITH lambda gates) ==")
print(f"mismatches: {n} ({100*n/tot:.1f}%)")
for (mz, b), c in mismatch.most_common():
    print(f"  meter={mz:12s} model={b:12s}  n={c}")

print(f"\n== meter zone vs classify_pick_lr (probability zone only, lambda=None) ==")
print(f"mismatches: {n_nolam} ({100*n_nolam/tot:.1f}%)")
for (mz, b), c in mismatch_nolam.most_common():
    d = examples[(mz, b)]
    print(f"  meter={mz:12s} model={b:12s}  n={c}   e.g. {d[0]} {d[1]}@{d[2]} "
          f"p_nrfi={d[3]:.4f} yrfi={d[4]:.3f} lam={d[5]}")

# boundary map: where does the model actually switch, in P(YRFI) space?
print("\n== model band as a function of P(YRFI) (lambda=None, sweep) ==")
prev = None
x = 0.300
while x <= 0.750001:
    pn = 1.0 - x
    s, st = P.classify_pick_lr(pn, 4, None)
    b = band(s, st)
    if b != prev:
        print(f"  P(YRFI) >= {x:.4f}  -> {b}")
        prev = b
    x += 0.0001

print("\n== distribution of yrfi_prob across meter zones ==")
zc = Counter(meter_zone((round(f(r['yrfi_prob'])*1000)/10)/100.0)
             for r in rows if f(r["yrfi_prob"]) is not None)
for k, v in zc.most_common():
    print(f"  {k:12s} {v}")

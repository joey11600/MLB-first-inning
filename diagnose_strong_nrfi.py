#!/usr/bin/env python3
"""
diagnose_strong_nrfi.py -- forensic on the STRONG NRFI underperformance.

Question: STRONG NRFI is 16-19 (-4.45u) on 2026 to date, while backtest
validation said top-quintile NRFI picks win 57.8%. Is this:
  (a) sample-size noise on N=35,
  (b) a regime shift (2026 different from 2024+2025),
  (c) calibration drift (the 60% threshold is no longer a true 60%),
  (d) a temporal cluster within 2026,
  (e) some combination?

Run: python diagnose_strong_nrfi.py
"""

import csv
import math
from collections import defaultdict
from pathlib import Path

PICKS = Path(__file__).parent / "data" / "picks_2026.csv"
BT_2024 = Path(__file__).parent / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = Path(__file__).parent / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def wilson_95(wins, n):
    """95% Wilson confidence interval for a binomial rate."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half, centre + half)


def two_proportion_z(w1, n1, w2, n2):
    """Two-proportion z-test. Returns z and approximate two-tailed p."""
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1, p2 = w1 / n1, w2 / n2
    p_pool = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # Two-tailed p via complementary error function
    p = math.erfc(abs(z) / math.sqrt(2))
    return z, p


def bin_hits(rows, side, strength, *, label):
    """Filter to a pick zone and return (wins, losses, total_graded)."""
    w = l = 0
    for r in rows:
        if r.get("pick_side") != side: continue
        if r.get("pick_strength") != strength: continue
        gr = (r.get("graded_result") or "").upper()
        if gr == "WIN":  w += 1
        elif gr == "LOSS": l += 1
    return w, l


# ---------------------------------------------------------------------------
print("=" * 64)
print("  STRONG NRFI Forensic")
print("=" * 64)

# --- 1. Current 2026 numbers
picks_2026 = read_csv(PICKS)
sn_2026 = [r for r in picks_2026
           if r.get("pick_side") == "NRFI"
           and r.get("pick_strength") == "STRONG"
           and r.get("graded_result", "").upper() in ("WIN", "LOSS")]

w26 = sum(1 for r in sn_2026 if r["graded_result"] == "WIN")
l26 = len(sn_2026) - w26
n26 = len(sn_2026)
hit26 = w26 / n26 if n26 else 0
lo26, hi26 = wilson_95(w26, n26)

print(f"\n2026 STRONG NRFI to date:")
print(f"  Record       : {w26}-{l26}  (N={n26})")
print(f"  Hit rate     : {hit26*100:.1f}%")
print(f"  95% CI       : [{lo26*100:.1f}%, {hi26*100:.1f}%]")
print(f"  Break-even   : 52.4% at -110")

# --- 2. Compare to historical baseline
# Backtests don't have pick_strength column directly; they have pick_strength
# from the lambda-based original predictor. The LR predictions in the CSV are
# in `pick_side_raw`/`pick_strength_raw` (lambda) plus the LR is computed at
# inference time, not stored.  So we use the actual pick_side/pick_strength
# columns that came from running the live predictor against the historical data.

bt_24 = read_csv(BT_2024)
bt_25 = read_csv(BT_2025)

w24, l24 = bin_hits(bt_24, "NRFI", "STRONG", label="2024")
w25, l25 = bin_hits(bt_25, "NRFI", "STRONG", label="2025")

n24 = w24 + l24
n25 = w25 + l25
hit24 = w24 / n24 if n24 else 0
hit25 = w25 / n25 if n25 else 0

print(f"\n2024 backtest STRONG NRFI:")
print(f"  Record       : {w24}-{l24}  (N={n24})  hit={hit24*100:.1f}%")
print(f"\n2025 backtest STRONG NRFI:")
print(f"  Record       : {w25}-{l25}  (N={n25})  hit={hit25*100:.1f}%")

# Pooled historical
nh = n24 + n25
wh = w24 + w25
hith = wh / nh if nh else 0
print(f"\n2024+2025 pooled:")
print(f"  Record       : {wh}-{nh-wh}  (N={nh})  hit={hith*100:.1f}%")

# --- 3. Statistical significance: is 2026 different from 2024+2025?
z, p = two_proportion_z(w26, n26, wh, nh)
print(f"\nIs 2026 STRONG NRFI different from 2024+2025?")
print(f"  z-stat       : {z:+.3f}")
print(f"  two-tailed p : {p:.3f}")
if p < 0.05:
    print(f"  -> YES, statistically significant at 5% (regime shift suspected)")
else:
    print(f"  -> NO, indistinguishable from 2024+2025 sample (probably noise)")

# Required N for 95% confidence given the observed gap
gap = hith - hit26
if gap > 0:
    # Approximate sample size needed so 95% CI half-width < gap/2
    p_avg = (hith + hit26) / 2
    var = p_avg * (1 - p_avg)
    needed_n = int(math.ceil((1.96 ** 2) * var / (gap / 2) ** 2))
    print(f"  Need ~{needed_n} graded STRONG NRFI picks to confirm a {gap*100:.1f}pp gap")

# --- 4. Temporal pattern within 2026
print("\nTemporal pattern in 2026 STRONG NRFI (chronological):")
sn_sorted = sorted(sn_2026, key=lambda r: r["date"])
running_w = running_n = 0
buckets = defaultdict(lambda: [0, 0])
for r in sn_sorted:
    running_n += 1
    if r["graded_result"] == "WIN": running_w += 1
    # Show running rate every ~7 picks
ten = max(1, n26 // 10)
print(f"  rolling rate every {ten} picks:")
running_w = running_n = 0
last_print = 0
for i, r in enumerate(sn_sorted, 1):
    running_n += 1
    if r["graded_result"] == "WIN": running_w += 1
    if running_n - last_print >= ten or i == n26:
        rate = running_w / running_n
        print(f"    after pick {running_n:>2}  ({r['date']})  : "
              f"{running_w}-{running_n-running_w}  ({rate*100:.1f}%)")
        last_print = running_n

# --- 5. Distribution of CALIBRATED probabilities for 2026 STRONG NRFI
# These are nrfi_prob from the calibrated LR-v2 model (>= 0.60 by definition
# of STRONG zone)
probs = sorted(float(r["nrfi_prob"]) for r in sn_2026)
print(f"\nCalibrated NRFI probabilities of 2026 STRONG NRFI picks:")
print(f"  N={len(probs)}  min={probs[0]*100:.1f}%  "
      f"median={probs[len(probs)//2]*100:.1f}%  max={probs[-1]*100:.1f}%")
print(f"  Avg predicted: {sum(probs)/len(probs)*100:.1f}%")
print(f"  Actual hit   : {hit26*100:.1f}%   "
      f"({(sum(probs)/len(probs)-hit26)*100:+.1f}pp model bias on this slice)")

# --- 6. Compare 2026 LEAN NRFI for context (the model's 53-60% bin)
ln_2026 = [r for r in picks_2026
           if r.get("pick_side") == "NRFI"
           and r.get("pick_strength") == "LEAN"
           and r.get("graded_result", "").upper() in ("WIN", "LOSS")]
wln = sum(1 for r in ln_2026 if r["graded_result"] == "WIN")
lln = len(ln_2026) - wln
nln = len(ln_2026)
hitln = wln / nln if nln else 0
print(f"\n2026 LEAN NRFI (for context):")
print(f"  Record       : {wln}-{lln}  (N={nln})  hit={hitln*100:.1f}%")

# Lean YRFI
ly_2026 = [r for r in picks_2026
           if r.get("pick_side") == "YRFI"
           and r.get("pick_strength") == "LEAN"
           and r.get("graded_result", "").upper() in ("WIN", "LOSS")]
wly = sum(1 for r in ly_2026 if r["graded_result"] == "WIN")
lly = len(ly_2026) - wly
nly = len(ly_2026)
hitly = wly / nly if nly else 0
print(f"\n2026 LEAN YRFI (the profitable zone):")
print(f"  Record       : {wly}-{lly}  (N={nly})  hit={hitly*100:.1f}%")

# Are NRFI hit rates structurally lower in 2026?
all_2026 = [r for r in picks_2026
            if r.get("graded_result", "").upper() in ("WIN", "LOSS")
            and r.get("pick_side") in ("NRFI", "YRFI")]
nrfi_actual = sum(1 for r in all_2026 if r.get("actual_result") == "NRFI")
yrfi_actual = sum(1 for r in all_2026 if r.get("actual_result") == "YRFI")
total_graded = nrfi_actual + yrfi_actual
nrfi_rate_2026 = nrfi_actual / total_graded if total_graded else 0
print(f"\n2026 base NRFI rate (across ALL graded games):")
print(f"  NRFI: {nrfi_actual}  YRFI: {yrfi_actual}  -> {nrfi_rate_2026*100:.1f}%")
print(f"  (Compare to 2024 base: {sum(1 for r in bt_24 if r.get('actual_side')=='NRFI'):d}/"
      f"{sum(1 for r in bt_24 if r.get('actual_side') in ('NRFI','YRFI')):d} = "
      f"{sum(1 for r in bt_24 if r.get('actual_side')=='NRFI') / max(1, sum(1 for r in bt_24 if r.get('actual_side') in ('NRFI','YRFI')))*100:.1f}%)")
print(f"  (Compare to 2025 base: {sum(1 for r in bt_25 if r.get('actual_side')=='NRFI'):d}/"
      f"{sum(1 for r in bt_25 if r.get('actual_side') in ('NRFI','YRFI')):d} = "
      f"{sum(1 for r in bt_25 if r.get('actual_side')=='NRFI') / max(1, sum(1 for r in bt_25 if r.get('actual_side') in ('NRFI','YRFI')))*100:.1f}%)")
print()

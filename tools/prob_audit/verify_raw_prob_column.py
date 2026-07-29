"""ANALYSIS ONLY -- verification of the claim that tracker.log_picks never
writes nrfi_prob_raw / yrfi_prob_raw.

Checks:
  1. Column population counts in data/picks_2026.csv.
  2. Whether raw is recoverable from lambda_lr_total via raw = exp(-lambda),
     by feeding exp(-lambda_lr_total) through the production calibrator and
     diffing against the stored nrfi_prob.
  3. How many rows have NO recovery path at all (blank lambda_lr_total).
Writes nothing.
"""
import csv, math, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
print(f"rows = {len(rows)}")


def f(v):
    try:
        s = (v or "").strip()
        return float(s) if s else None
    except Exception:
        return None


for c in ("nrfi_prob_raw", "yrfi_prob_raw", "nrfi_prob", "yrfi_prob",
          "lambda_lr_total", "lambda_lr_t1", "lambda_lr_b1", "combined_lambda"):
    print(f"  {c:18s} nonblank = {sum(1 for r in rows if (r.get(c) or '').strip())}")

# --- recovery path -------------------------------------------------------
from calibration import ProbCalibrator  # noqa: E402

cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")

diffs = []
no_path = 0
for r in rows:
    lam = f(r.get("lambda_lr_total"))
    p = f(r.get("nrfi_prob"))
    if p is None:
        continue
    if lam is None:
        no_path += 1
        continue
    raw_hat = math.exp(-lam)
    diffs.append((r["date"], r["game_pk"], raw_hat, cal.predict(raw_hat) - p))

ad = [abs(d) for _, _, _, d in diffs]
print(f"\nrecoverable rows        = {len(diffs)}")
print(f"rows with NO raw at all = {no_path}  (blank lambda_lr_total, blank raw)")
print(f"cal(exp(-lambda)) vs stored nrfi_prob:")
print(f"  max |diff|  = {max(ad):.3e}")
print(f"  mean |diff| = {sum(ad)/len(ad):.3e}")
worst = sorted(diffs, key=lambda t: -abs(t[3]))[:5]
for d_, gp, rh, dd in worst:
    print(f"    {d_} {gp}  raw_hat={rh:.6f}  diff={dd:+.3e}")

# rounding-induced precision loss on the recovery
lams = [f(r.get("lambda_lr_total")) for r in rows if f(r.get("lambda_lr_total")) is not None]
print(f"\nlambda_lr_total stored to 4dp -> raw recovery error bound "
      f"~ raw*5e-5 = {math.exp(-min(lams))*5e-5:.2e} (worst case)")

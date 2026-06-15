#!/usr/bin/env python3
"""tools/nrfi_prediction_diagnostic.py

Localise WHERE the NRFI prediction breaks.  NRFI is built as an
independence product:

    P(NRFI) = (1 - P(top-half run)) * (1 - P(bottom-half run))

with per-half run prob recoverable from the stored lambdas:
    P(half run) = 1 - exp(-lambda_lr_half)

Actuals: fi_away_runs>0 == top-half (T1) run, fi_home_runs>0 == bottom (B1).

We test three things on ALL graded games (max sample, not just bets):
  1. PER-HALF calibration -- does predicted P(run) match actual, for T1
     and B1 separately?  (If a half is off, the per-half model is the bug.)
  2. INDEPENDENCE -- does actual P(both scoreless) equal the product of
     the actual marginals?  (If not, the product formula is the bug.)
  3. RAW vs CALIBRATED vs ACTUAL NRFI, and vs the market's implied NRFI.

Read-only.  Usage:
  python tools/nrfi_prediction_diagnostic.py                 # 2026
  python tools/nrfi_prediction_diagnostic.py --glob "data/backtests/backtest_2025-*_truepit.csv"
"""
from __future__ import annotations
import argparse, csv, glob, math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def imp(a):
    s = (a or "").strip()
    try: n = int(float(s))
    except (ValueError, TypeError): return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def load(paths):
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    # live picks use lambda_lr_t1/b1; backtests use away_/home_lambda
                    lt1 = float(r.get("lambda_lr_t1") or r.get("away_lambda"))
                    lb1 = float(r.get("lambda_lr_b1") or r.get("home_lambda"))
                    fa = int(float(r["fi_away_runs"])); fh = int(float(r["fi_home_runs"]))
                except (ValueError, TypeError, KeyError):
                    continue
                pt1 = 1.0 - math.exp(-lt1)        # model P(top-half run)
                pb1 = 1.0 - math.exp(-lb1)        # model P(bottom-half run)
                row = {
                    "pt1": pt1, "pb1": pb1,
                    "at1": 1 if fa > 0 else 0,    # actual top-half run
                    "ab1": 1 if fh > 0 else 0,    # actual bottom-half run
                    "anrfi": 1 if (fa + fh) == 0 else 0,
                    "praw": (1 - pt1) * (1 - pb1),
                }
                try: row["pcal"] = float(r.get("nrfi_prob") or "nan")
                except ValueError: row["pcal"] = float("nan")
                row["mkt"] = imp(r.get("market_nrfi_odds", ""))
                rows.append(row)
    return rows


def mean(xs):
    xs = [x for x in xs if x == x]
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="data/picks_2026.csv")
    args = ap.parse_args()
    paths = sorted(glob.glob(str(ROOT / args.glob))) or [str(ROOT / args.glob)]
    rows = load(paths)
    n = len(rows)
    print(f"Graded games: {n}   source: {args.glob}\n")
    if not n:
        return

    # 1. Per-half calibration
    print("=== 1. PER-HALF run calibration (predicted vs actual) ===")
    print(f"  TOP half (T1, away bats):   model P(run)={mean([r['pt1'] for r in rows]):.3f}   "
          f"actual={mean([r['at1'] for r in rows]):.3f}   "
          f"gap={mean([r['pt1'] for r in rows])-mean([r['at1'] for r in rows]):+.3f}")
    print(f"  BOT half (B1, home bats):   model P(run)={mean([r['pb1'] for r in rows]):.3f}   "
          f"actual={mean([r['ab1'] for r in rows]):.3f}   "
          f"gap={mean([r['pb1'] for r in rows])-mean([r['ab1'] for r in rows]):+.3f}")
    print("  (negative gap = model UNDER-predicts runs in that half -> over-predicts NRFI)\n")

    # 2. Independence check
    pt1a = mean([r["at1"] for r in rows]); pb1a = mean([r["ab1"] for r in rows])
    actual_nrfi = mean([r["anrfi"] for r in rows])
    product_marginals = (1 - pt1a) * (1 - pb1a)
    # phi correlation between the two half-run indicators
    a, b = [r["at1"] for r in rows], [r["ab1"] for r in rows]
    ma, mb = mean(a), mean(b)
    cov = mean([(x-ma)*(y-mb) for x, y in zip(a, b)])
    sda = (mean([(x-ma)**2 for x in a]))**0.5; sdb = (mean([(y-mb)**2 for y in b]))**0.5
    phi = cov/(sda*sdb) if sda*sdb else float("nan")
    print("=== 2. INDEPENDENCE of the two halves (is the product formula valid?) ===")
    print(f"  actual P(NRFI)               = {actual_nrfi:.3f}")
    print(f"  product of actual marginals  = {product_marginals:.3f}   "
          f"(1-{pt1a:.3f})*(1-{pb1a:.3f})")
    print(f"  difference                   = {actual_nrfi-product_marginals:+.3f}")
    print(f"  phi correlation(T1 run, B1 run) = {phi:+.3f}  "
          f"(~0 => independent; the product assumption needs this)\n")

    # 3. Raw vs calibrated vs actual vs market
    print("=== 3. NRFI: model raw vs calibrated vs ACTUAL vs market ===")
    print(f"  model RAW   P(NRFI) (product) = {mean([r['praw'] for r in rows]):.3f}")
    print(f"  model CALIB P(NRFI)          = {mean([r['pcal'] for r in rows]):.3f}")
    print(f"  ACTUAL      NRFI rate        = {actual_nrfi:.3f}")
    mk = [r for r in rows if r["mkt"] is not None]
    if mk:
        print(f"  MARKET implied NRFI (de-vig'd by side not done; raw)= {mean([r['mkt'] for r in mk]):.3f}  "
              f"(n={len(mk)} with odds)")
    print()

    # 4. Calibration by model-confidence bucket (where does it break?)
    print("=== 4. Calibrated NRFI by confidence bucket (predicted vs actual) ===")
    edges = [(0.0,0.40),(0.40,0.44),(0.44,0.50),(0.50,0.56),(0.56,0.62),(0.62,0.66),(0.66,1.01)]
    print(f"  {'band':<12}{'n':>5}{'mean pred':>11}{'actual':>9}{'gap':>8}")
    for lo, hi in edges:
        sub = [r for r in rows if r["pcal"] == r["pcal"] and lo <= r["pcal"] < hi]
        if not sub: continue
        mp = mean([r["pcal"] for r in sub]); ma_ = mean([r["anrfi"] for r in sub])
        print(f"  {lo:.2f}-{hi:.2f} {len(sub):>5}{mp:>11.3f}{ma_:>9.3f}{mp-ma_:>+8.3f}")
    print("  (gap>0 = over-confident NRFI; this is where bets lose)")


if __name__ == "__main__":
    main()

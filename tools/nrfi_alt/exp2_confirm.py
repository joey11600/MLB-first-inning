#!/usr/bin/env python3
"""
exp2: the one cell in exp1 that looked like a gain -- SPECIALIST direct LR
in the "top 35% by p_prod" region -- gets the adversarial treatment.

  (a) reverse direction: train 2026 -> test 2025.  A real effect survives
      both directions; a searched one does not.
  (b) block bootstrap over DAYS on the AUC DIFFERENCE (specialist minus
      production) inside the region.
  (c) does it re-rank into money?  2026 real captured DK NRFI prices only.
  (d) restriction-of-range control: what AUC would a PERFECT oracle get
      inside a slice selected on its own score?  (this is why in-region
      AUC near 0.5 is not evidence of a broken model)
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

L2 = 10.0
QCUT = 0.35


def region(rows, X, y, q=QCUT):
    p = np.array([r["prod"] for r in rows])
    thr = np.quantile(p, 1 - q)
    m = p >= thr
    return [r for r, k in zip(rows, m) if k], X[m], y[m]


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())
    X25, names = C.design(r25)
    X26, _ = C.design(r26)
    y25 = np.array([r["y_nrfi"] for r in r25])
    y26 = np.array([r["y_nrfi"] for r in r26])

    g25, Xr25, yr25 = region(r25, X25, y25)
    g26, Xr26, yr26 = region(r26, X26, y26)

    print("=" * 96)
    print("  EXP2  adversarial check on the top-35%-region specialist LR")
    print("=" * 96)

    # ---- (a) both directions -------------------------------------------
    print("\n  (a) DIRECTION TEST  (specialist trained in-region, tested in-region)")
    for tag, Xtr, ytr, gte, Xte, yte in [
            ("train 2025 -> test 2026", Xr25, yr25, g26, Xr26, yr26),
            ("train 2026 -> test 2025", Xr26, yr26, g25, Xr25, yr25)]:
        sp = C.fit_lr(Xtr, ytr, L2)
        ps = C.predict_lr(sp, Xte)
        pp = np.array([r["prod"] for r in gte])
        print(f"     {tag:<26} n_te={len(yte):>4}  prod AUC={C.auc(yte,pp):.4f}   "
              f"specialist AUC={C.auc(yte,ps):.4f}   delta={C.auc(yte,ps)-C.auc(yte,pp):+.4f}")

    # ---- (b) bootstrap on the delta, forward direction ------------------
    sp = C.fit_lr(Xr25, yr25, L2)
    for r, s in zip(g26, C.predict_lr(sp, Xr26)):
        r["spec"] = float(s)

    def d_auc(rows):
        y = np.array([r["y_nrfi"] for r in rows])
        if y.min() == y.max():
            return float("nan")
        return C.auc(y, [r["spec"] for r in rows]) - C.auc(y, [r["prod"] for r in rows])

    m, lo, hi = C.block_bootstrap_days(g26, d_auc, B=2000, seed=7)
    print(f"\n  (b) BLOCK BOOTSTRAP over days, 2026 in-region, n={len(g26)}")
    print(f"      delta AUC (specialist - production) = {m:+.4f}   95% CI [{lo:+.4f}, {hi:+.4f}]")
    print("      -> CI spans 0" if lo < 0 < hi else "      -> CI excludes 0")

    # ---- (c) money -----------------------------------------------------
    priced = [r for r in g26 if r["nrfi_odds"] is not None]
    print(f"\n  (c) MONEY, 2026 in-region with REAL captured DK NRFI prices: n={len(priced)}"
          f" of {len(g26)}")
    if priced:
        need = np.mean([C.implied(r["nrfi_odds"]) for r in priced])
        hit = np.mean([r["y_nrfi"] for r in priced])
        print(f"      bet ALL in-region: hit={100*hit:.2f}%  break-even={100*need:.2f}%  "
              f"wall={100*(need-hit):+.2f}pp")
        for lab, key in (("production p_nrfi", "prod"), ("specialist p_nrfi", "spec")):
            print(f"      -- rank by {lab} --")
            s = sorted(priced, key=lambda r: -r[key])
            for frac in (0.1, 0.2, 0.3, 0.5, 1.0):
                k = max(10, int(len(s) * frac))
                sub = s[:k]
                h = np.mean([r["y_nrfi"] for r in sub])
                nd = np.mean([C.implied(r["nrfi_odds"]) for r in sub])
                roi = np.mean([C.payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0 for r in sub])
                u = sum(C.payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0 for r in sub)
                print(f"         top {int(frac*100):>3}%  n={k:>4}  hit={100*h:>5.2f}%  "
                      f"need={100*nd:>5.2f}%  gap={100*(h-nd):>+6.2f}pp  ROI={100*roi:>+6.2f}%  "
                      f"{u:>+7.2f}u flat")

    # ---- (d) restriction-of-range control ------------------------------
    print("\n  (d) RESTRICTION-OF-RANGE CONTROL -- simulated PERFECT oracle")
    rng = np.random.default_rng(0)
    for sd in (0.05, 0.075, 0.10, 0.15):
        n = 200000
        pt = np.clip(rng.normal(0.50, sd, n), 0.02, 0.98)
        yy = (rng.random(n) < pt).astype(int)
        full = C.auc(yy, pt)
        thr = np.quantile(pt, 1 - QCUT)
        k = pt >= thr
        print(f"      true-p spread sd={sd:.3f}:  oracle AUC on ALL games={full:.4f}   "
              f"oracle AUC inside its own top {int(QCUT*100)}%={C.auc(yy[k], pt[k]):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

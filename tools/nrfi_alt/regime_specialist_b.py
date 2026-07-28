#!/usr/bin/env python3
"""
Part B -- give the regime-specialist its BEST shot, then price it.

  B1. L2 sweep (the flat 31-d specialist may simply be overfit at l2=0.05).
  B2. Architecture-matched specialist: refit the TWO-STAGE t1/b1 pair on
      region rows only (this is what "refit the model on the region"
      most charitably means).
  B3. Blend specialist with incumbent (shrinkage toward the generalist).
  B4. MONEY: rank inside the 2026 high region with the 2025-trained
      specialist, bet the top-k NRFI at REAL captured DK prices.
  B5. Ceiling arithmetic: what AUC would even be needed?
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))

import recalibrate_v2 as rc                       # noqa: E402
from lr_baseline import LogReg                    # noqa: E402
from calibration import ProbCalibrator            # noqa: E402
from regime_specialist import (load, auc, day_block_boot, UNION_NAMES,  # noqa: E402
                               payout, RNG)

HI = 0.50
T1F = rc.T1_FEATURES
B1F = rc.B1_FEATURES


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def main():
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    d25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv", 2025)
    d26 = load("data/picks_2026.csv", 2026)
    for d in (d25, d26):
        for r in d:
            r["p"] = float(cal.predict(r["raw"]))
    hi25 = [r for r in d25 if r["p"] >= HI]
    hi26 = [r for r in d26 if r["p"] >= HI]

    cells = [("train 2025 -> test 2026", hi25, hi26),
             ("train 2026 -> test 2025", hi26, hi25)]

    # ---------------- B1: L2 sweep on the flat 31-d specialist -------------
    print("=" * 88)
    print("  B1. L2 SWEEP -- flat 31-feature specialist, HIGH regime only")
    print("=" * 88)
    l2s = [0.001, 0.01, 0.05, 0.25, 1.0, 5.0, 25.0, 100.0, 500.0]
    print(f"  {'l2':>8} " + "".join(f"{c[0][6:]:>22}" for c in cells))
    searched = 0
    for l2 in l2s:
        cellstr = ""
        for _, TR, TE in cells:
            m = LogReg.fit(np.asarray([r["u"] for r in TR], float),
                           np.asarray([r["y"] for r in TR], float),
                           UNION_NAMES, l2=l2)
            s = m.predict_proba(np.asarray([r["u"] for r in TE], float))
            a_s = auc([r["y"] for r in TE], s)
            a_i = auc([r["y"] for r in TE], [r["raw"] for r in TE])
            cellstr += f"   {a_s:.4f} ({a_s - a_i:+.4f})"
            searched += 1
        print(f"  {l2:>8} {cellstr}")
    print(f"  cells searched: {searched}")

    # ---------------- B2: architecture-matched two-stage refit ------------
    print("\n" + "=" * 88)
    print("  B2. ARCHITECTURE-MATCHED specialist: refit t1 & b1 on region rows")
    print("=" * 88)
    for lab, TR, TE in cells:
        Xt = np.asarray([r["t1"] for r in TR], float)
        Xb = np.asarray([r["b1"] for r in TR], float)
        # labels: the two-stage model predicts P(run in that half).  We only
        # have the game-level NRFI label in these CSVs for the 2026 file, and
        # fi_*_runs for the backtests; use the half-inning labels where
        # available, else fall back to the game label for both halves.
        yt = np.asarray([r["y"] for r in TR], float)   # NRFI=1
        mt = LogReg.fit(Xt, 1.0 - yt, T1F, l2=0.05)
        mb = LogReg.fit(Xb, 1.0 - yt, B1F, l2=0.05)
        pt = mt.predict_proba(np.asarray([r["t1"] for r in TE], float))
        pb = mb.predict_proba(np.asarray([r["b1"] for r in TE], float))
        s = (1 - pt) * (1 - pb)
        a_s = auc([r["y"] for r in TE], s)
        a_i = auc([r["y"] for r in TE], [r["raw"] for r in TE])
        print(f"  {lab:<26} n={len(TE):<6} inc {a_i:.4f}  spec {a_s:.4f}  "
              f"delta {a_s - a_i:+.4f}")

    # ---------------- B3: blend with incumbent ----------------------------
    print("\n" + "=" * 88)
    print("  B3. BLEND  score = (1-w)*z(incumbent) + w*z(specialist), l2=1.0")
    print("=" * 88)

    def z(v):
        v = np.asarray(v, float)
        return (v - v.mean()) / (v.std() + 1e-12)

    ws = [0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0]
    print(f"  {'w':>6} " + "".join(f"{c[0][6:]:>18}" for c in cells))
    for w in ws:
        s_out = ""
        for _, TR, TE in cells:
            m = LogReg.fit(np.asarray([r["u"] for r in TR], float),
                           np.asarray([r["y"] for r in TR], float),
                           UNION_NAMES, l2=1.0)
            sp = m.predict_proba(np.asarray([r["u"] for r in TE], float))
            sc = (1 - w) * z([r["raw"] for r in TE]) + w * z(sp)
            s_out += f"      {auc([r['y'] for r in TE], sc):.4f}"
        print(f"  {w:>6} {s_out}")
    print(f"  cells searched here: {len(ws) * 2}")

    # ---------------- B4: MONEY at real prices ----------------------------
    print("\n" + "=" * 88)
    print("  B4. MONEY -- 2025-trained specialist ranks 2026 HIGH region,")
    print("      bet top-k NRFI at REAL captured DK prices (flat 1u)")
    print("=" * 88)
    best_l2 = 1.0
    m = LogReg.fit(np.asarray([r["u"] for r in hi25], float),
                   np.asarray([r["y"] for r in hi25], float),
                   UNION_NAMES, l2=best_l2)
    sp = m.predict_proba(np.asarray([r["u"] for r in hi26], float))
    for r, s in zip(hi26, sp):
        r["spec"] = float(s)
    priced = [r for r in hi26 if r["nrfi_odds"] is not None]
    print(f"  2026 HIGH region: n={len(hi26)}, with a real DK NRFI price: {len(priced)}")
    print(f"  region base rate (priced): {np.mean([r['y'] for r in priced]):.4f}   "
          f"mean break-even: {np.mean([implied(r['nrfi_odds']) for r in priced]):.4f}")

    def roi(rows):
        if not rows:
            return float("nan"), 0, float("nan")
        pnl = sum(payout(r["nrfi_odds"]) if r["y"] else -1.0 for r in rows)
        return pnl, len(rows), 100 * pnl / len(rows)

    print(f"\n  {'selection':<34}{'n':>5}{'hit%':>8}{'need%':>8}{'units':>10}"
          f"{'ROI%':>8}   95% CI ROI%")
    for lab, sub in [
        ("ALL region (no ranking)", priced),
        ("specialist top 50%", sorted(priced, key=lambda r: -r["spec"])[:len(priced) // 2]),
        ("specialist top 25%", sorted(priced, key=lambda r: -r["spec"])[:len(priced) // 4]),
        ("specialist top 10%", sorted(priced, key=lambda r: -r["spec"])[:len(priced) // 10]),
        ("incumbent top 25% (control)",
         sorted(priced, key=lambda r: -r["raw"])[:len(priced) // 4]),
    ]:
        pnl, n, r_ = roi(sub)
        hit = 100 * np.mean([x["y"] for x in sub]) if sub else float("nan")
        need = 100 * np.mean([implied(x["nrfi_odds"]) for x in sub]) if sub else float("nan")
        ci = day_block_boot(sub, lambda rr: roi(rr)[2], B=1500)
        print(f"  {lab:<34}{n:>5}{hit:>8.1f}{need:>8.1f}{pnl:>+10.2f}{r_:>+8.2f}"
              f"   [{ci[0]:+.1f},{ci[1]:+.1f}]")

    # 10 cents worse pricing
    print("\n  Same rows, 10 cents WORSE pricing (odds - 10 on the NRFI side):")
    for lab, sub in [
        ("specialist top 25%", sorted(priced, key=lambda r: -r["spec"])[:len(priced) // 4]),
        ("specialist top 10%", sorted(priced, key=lambda r: -r["spec"])[:len(priced) // 10]),
    ]:
        def pay_worse(o):
            o2 = o - 10 if o > 0 else o - 10
            return payout(o2)
        pnl = sum(pay_worse(r["nrfi_odds"]) if r["y"] else -1.0 for r in sub)
        print(f"  {lab:<34}{len(sub):>5}{'':>16}{pnl:>+10.2f}"
              f"{100 * pnl / max(len(sub),1):>+8.2f}")

    # ---------------- B5: ceiling arithmetic ------------------------------
    print("\n" + "=" * 88)
    print("  B5. CEILING -- what AUC lift would be needed to clear the wall?")
    print("=" * 88)
    base = np.mean([r["y"] for r in priced])
    need = np.mean([implied(r["nrfi_odds"]) for r in priced])
    print(f"  region priced base rate  : {base:.4f}")
    print(f"  mean break-even          : {need:.4f}")
    print(f"  gap to close             : {100*(need-base):.2f} pp (before any margin)")
    # simulate: with a ranker of given AUC on a binary label, top-q selection
    # hit rate under a bi-normal model
    from math import erf, sqrt

    def phi(x):
        return 0.5 * (1 + erf(x / sqrt(2)))

    from scipy.stats import norm
    for a in (0.52, 0.55, 0.60, 0.65):
        d = norm.ppf(a) * sqrt(2.0)     # bi-normal separation
        for q in (0.25, 0.10):
            # threshold t on score; P(y=1 | score>t) via mixture
            lo, hi_ = -10.0, 10.0
            for _ in range(200):
                t = (lo + hi_) / 2
                frac = base * (1 - phi(t - d)) + (1 - base) * (1 - phi(t))
                if frac > q:
                    lo = t
                else:
                    hi_ = t
            t = (lo + hi_) / 2
            hit = base * (1 - phi(t - d)) / (base * (1 - phi(t - d))
                                             + (1 - base) * (1 - phi(t)))
            print(f"  AUC {a:.2f}, top {int(q*100):>2}%  ->  hit {hit:.4f}   "
                  f"vs need {need:.4f}   {'CLEARS' if hit > need else 'short by '
                  + format(100*(need-hit), '.2f') + 'pp'}")


if __name__ == "__main__":
    main()

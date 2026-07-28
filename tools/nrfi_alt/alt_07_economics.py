#!/usr/bin/env python3
"""Does the asymmetry MATTER?  Three questions, real captured DK prices only.

  A. High-regime calibration over time -- is the high side also drifting, not
     just undiscriminating?
  B. Price-vs-model slope.  Break-even % by model p_nrfi decile.  If the market's
     implied probability rises as fast as (or faster than) the model's, no amount
     of within-regime discrimination produces a bettable edge.
  C. REQUIRED-AUC SIMULATION.  Give the high regime a synthetic score with a
     chosen AUC against the TRUE outcomes, bet the top-k, and read the hit rate
     against that subset's real break-even.  Answers "if we fixed the asymmetry,
     would it clear the 5.65pp wall?" without hand-waving.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac

CUT = "2026-05-26"


def implied(american):
    a = float(american)
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def payout(american):
    a = float(american)
    return (100.0 / -a) if a < 0 else (a / 100.0)


def load_priced():
    d = ac.load("2026picks")
    rows = d["rows"]
    keep, on, oy = [], [], []
    for i, r in enumerate(rows):
        try:
            n = float(r.get("market_nrfi_odds") or "nan")
            y = float(r.get("market_yrfi_odds") or "nan")
        except ValueError:
            continue
        if not (np.isfinite(n) and np.isfinite(y)):
            continue
        keep.append(i); on.append(n); oy.append(y)
    keep = np.asarray(keep)
    return d, keep, np.asarray(on), np.asarray(oy)


def main():
    d, k, o_n, o_y = load_priced()
    p = d["cal"][k]; y = d["y"][k]; dates = d["dates"][k]
    be_n = np.asarray([implied(v) for v in o_n])   # break-even for the NRFI side
    pay_n = np.asarray([payout(v) for v in o_n])
    print(f"  real-priced 2026 rows: n={len(k)}   NRFI hit={y.mean():.4f}   "
          f"mean NRFI break-even={be_n.mean():.4f}   wall={be_n.mean()-y.mean():+.4f}")
    print()

    print("=" * 108)
    print("  A. HIGH-REGIME (p>=0.50) CALIBRATION BY MONTH -- all graded 2026 rows, not just priced")
    print("=" * 108)
    pa, ya, da = d["cal"], d["y"], d["dates"]
    months = sorted({x[:7] for x in da if x})
    print(f"  {'month':<10}{'regime':<10}{'n':>6}{'pred':>8}{'actual':>9}{'gap':>9}{'AUC':>9}")
    for m in months:
        mm = np.asarray([x[:7] == m for x in da])
        for lab, sel in (("p<0.50", pa < 0.50), ("p>=0.50", pa >= 0.50)):
            idx = np.where(mm & sel)[0]
            if len(idx) < 40:
                continue
            print(f"  {m:<10}{lab:<10}{len(idx):>6}{pa[idx].mean():>8.3f}"
                  f"{ya[idx].mean():>9.3f}{ya[idx].mean()-pa[idx].mean():>+9.3f}"
                  f"{ac.auc(pa[idx], ya[idx]):>9.4f}")
    print()

    print("=" * 108)
    print("  B. PRICE TRACKS THE MODEL.  Decile of model p_nrfi vs the market's implied")
    print("     NRFI probability (with vig) and the realised NRFI rate.  Real prices only.")
    print("=" * 108)
    q = np.percentile(p, np.arange(0, 101, 10))
    print(f"  {'dec':<5}{'n':>6}{'model p':>10}{'implied':>10}{'actual':>10}"
          f"{'act-implied':>13}{'ROI 1u NRFI':>14}")
    mids, imps = [], []
    for i in range(10):
        lo, hi = q[i], q[i + 1]
        sel = (p >= lo) & (p < hi) if i < 9 else (p >= lo)
        if sel.sum() < 15:
            continue
        roi = float(np.mean(np.where(y[sel] == 1, pay_n[sel], -1.0)))
        mids.append(p[sel].mean()); imps.append(be_n[sel].mean())
        print(f"  {i+1:<5}{sel.sum():>6}{p[sel].mean():>10.4f}{be_n[sel].mean():>10.4f}"
              f"{y[sel].mean():>10.4f}{y[sel].mean()-be_n[sel].mean():>+13.4f}{roi:>+14.4f}")
    sl = np.polyfit(mids, imps, 1)[0]
    print(f"\n  slope of market implied-NRFI vs model p_nrfi across deciles = {sl:.3f}")
    print("  (>= 1.0 means the price moves at least as fast as the model's belief --")
    print("   discrimination gets fully priced away, and better ranking buys nothing.)")
    print()

    print("=" * 108)
    print("  C. REQUIRED-AUC SIMULATION on the real-priced HIGH regime (p_nrfi>=0.50).")
    print("     Synthetic score with a target AUC vs the TRUE outcome; bet its top-k;")
    print("     compare hit rate to that subset's real break-even.  1000 draws each.")
    print("=" * 108)
    hi = p >= 0.50
    yh, beh, payh = y[hi], be_n[hi], pay_n[hi]
    print(f"  high-regime priced n={hi.sum()}  actual NRFI={yh.mean():.4f}  "
          f"mean break-even={beh.mean():.4f}  live AUC={ac.auc(p[hi], yh):.4f}")
    print()
    print(f"  {'target AUC':>11}{'top-k':>8}{'n bet':>7}{'hit rate':>11}{'break-even':>12}"
          f"{'edge':>9}{'ROI':>9}{'ROI 5-95%':>20}")
    rng = np.random.default_rng(23)
    for target in [0.52, 0.55, 0.58, 0.60, 0.65, 0.70]:
        # separation d such that AUC = Phi(d/sqrt2)
        from math import erf, sqrt
        def A(dd):
            return 0.5 * (1 + erf(dd / 2.0))
        dlo, dhi = 0.0, 5.0
        for _ in range(60):
            mid = (dlo + dhi) / 2
            if A(mid) < target:
                dlo = mid
            else:
                dhi = mid
        sep = (dlo + dhi) / 2
        for topk in (0.25, 0.50):
            hits, rois, nbets = [], [], []
            for _ in range(1000):
                sc = rng.standard_normal(len(yh)) + sep * yh
                thr = np.quantile(sc, 1 - topk)
                s = sc >= thr
                if s.sum() < 10:
                    continue
                hits.append(yh[s].mean())
                rois.append(float(np.mean(np.where(yh[s] == 1, payh[s], -1.0))))
                nbets.append(int(s.sum()))
            hm, rm = float(np.mean(hits)), float(np.mean(rois))
            bem = beh.mean()
            print(f"  {target:>11.2f}{topk:>8.0%}{int(np.mean(nbets)):>7}{hm:>11.4f}"
                  f"{bem:>12.4f}{hm-bem:>+9.4f}{rm:>+9.4f}"
                  f"   [{np.percentile(rois,5):+.3f},{np.percentile(rois,95):+.3f}]")
    print()
    print("  For reference the LOW regime (where the model already discriminates):")
    lo = ~hi
    be_y = np.asarray([implied(v) for v in o_y])[lo]
    pay_y = np.asarray([payout(v) for v in o_y])[lo]
    yy = 1 - y[lo]
    py = 1 - p[lo]
    print(f"    priced n={lo.sum()}  YRFI hit={yy.mean():.4f}  break-even={be_y.mean():.4f}  "
          f"edge={yy.mean()-be_y.mean():+.4f}  AUC={ac.auc(py, yy):.4f}  "
          f"ROI={float(np.mean(np.where(yy==1, pay_y, -1.0))):+.4f}")


if __name__ == "__main__":
    main()

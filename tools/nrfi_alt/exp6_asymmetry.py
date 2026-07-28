#!/usr/bin/env python3
"""
exp6: the operator's actual hypothesis, tested head-on.

  A. was the late-2026 stretch simply NRFI-rich?  (explains exp5's
     split table without any model effect)
  B. is the model's SKILL asymmetric -- genuinely better at the YRFI end
     than the NRFI end -- once you correct for restriction of range?
  C. same question in money terms on real prices: YRFI side vs NRFI side.
"""
from __future__ import annotations
import sys
from pathlib import Path
from collections import defaultdict
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def main():
    r26 = C.attach_production(C.load_2026())
    r25 = C.attach_production(C.load_2025())

    print("=" * 96)
    print("  EXP6  A. was the late-2026 window just NRFI-rich?")
    print("=" * 96)
    bym = defaultdict(list)
    for r in r26:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        v = bym[m]
        print(f"      {m}  n={len(v):>4}  NRFI rate={100*np.mean([x['y_nrfi'] for x in v]):>6.2f}%")
    dates = sorted({r["date"] for r in r26})
    cut = dates[int(len(dates) * 0.6)]
    late = [r for r in r26 if r["date"] >= cut]
    early = [r for r in r26 if r["date"] < cut]
    print(f"      before {cut}: n={len(early)} NRFI={100*np.mean([r['y_nrfi'] for r in early]):.2f}%")
    print(f"      from   {cut}: n={len(late)}  NRFI={100*np.mean([r['y_nrfi'] for r in late]):.2f}%")

    print("\n" + "=" * 96)
    print("  EXP6  B. is model SKILL asymmetric by end?  (corrected for restriction of range)")
    print("=" * 96)
    rng = np.random.default_rng(0)

    def oracle_tail_auc(sd, q, high=True, n=400000, base=0.485):
        pt = np.clip(rng.normal(base, sd, n), 0.05, 0.95)
        yy = (rng.random(n) < pt).astype(int)
        thr = np.quantile(pt, 1 - q if high else q)
        k = pt >= thr if high else pt <= thr
        return C.auc(yy[k], pt[k])

    for tag, rows in (("2025", r25), ("2026", r26)):
        p = np.array([r["prod"] for r in rows])
        y = np.array([r["y_nrfi"] for r in rows])
        sd = p.std()
        print(f"\n      {tag}: n={len(rows)}  sd(p_prod)={sd:.4f}  full-sample AUC={C.auc(y,p):.4f}"
              f"   (oracle at this sd = {C.auc((rng.random(400000) < np.clip(rng.normal(0.485,sd,400000),.05,.95)).astype(int), np.clip(rng.normal(0.485,sd,400000),.05,.95)):.4f} -- see note)")
        for q in (0.20, 0.35, 0.50):
            hi = p >= np.quantile(p, 1 - q)
            lo = p <= np.quantile(p, q)
            a_hi, a_lo = C.auc(y[hi], p[hi]), C.auc(y[lo], p[lo])
            o_hi, o_lo = oracle_tail_auc(sd, q, True), oracle_tail_auc(sd, q, False)
            print(f"        top {int(q*100):>3}% (NRFI end): AUC={a_hi:.4f}  oracle ceiling={o_hi:.4f}  "
                  f"shortfall={a_hi-o_hi:+.4f}")
            print(f"        bot {int(q*100):>3}% (YRFI end): AUC={a_lo:.4f}  oracle ceiling={o_lo:.4f}  "
                  f"shortfall={a_lo-o_lo:+.4f}")

    print("\n" + "=" * 96)
    print("  EXP6  C. money by side, 2026 real captured prices")
    print("=" * 96)
    priced_n = [r for r in r26 if r["nrfi_odds"] is not None]
    priced_y = [r for r in r26 if r["yrfi_odds"] is not None]
    print(f"      {'depth':>7}{'NRFI n':>8}{'hit%':>8}{'need%':>8}{'gap':>8}{'u':>9}"
          f"   |{'YRFI n':>8}{'hit%':>8}{'need%':>8}{'gap':>8}{'u':>9}")
    sn = sorted(priced_n, key=lambda r: -r["prod"])
    sy = sorted(priced_y, key=lambda r: r["prod"])
    for q in (0.05, 0.10, 0.20, 0.30, 0.50, 1.0):
        kn = max(15, int(len(sn) * q)); a = sn[:kn]
        ky = max(15, int(len(sy) * q)); b = sy[:ky]
        hn = np.mean([r["y_nrfi"] for r in a]); nn = np.mean([C.implied(r["nrfi_odds"]) for r in a])
        un = sum(C.payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0 for r in a)
        hy = np.mean([1 - r["y_nrfi"] for r in b]); ny = np.mean([C.implied(r["yrfi_odds"]) for r in b])
        uy = sum(C.payout(r["yrfi_odds"]) if not r["y_nrfi"] else -1.0 for r in b)
        print(f"      {int(q*100):>6}%{kn:>8}{100*hn:>8.2f}{100*nn:>8.2f}{100*(hn-nn):>+8.2f}{un:>+9.2f}"
              f"   |{ky:>8}{100*hy:>8.2f}{100*ny:>8.2f}{100*(hy-ny):>+8.2f}{uy:>+9.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

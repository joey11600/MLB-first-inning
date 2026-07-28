#!/usr/bin/env python3
"""Q1 + Q4: is discrimination asymmetric between the YRFI-leaning and
NRFI-leaning regimes?  Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac

SEASONS = ["2025bt", "2026bt", "2026picks"]


def main():
    print("=" * 100)
    print("  Q4 SANITY: AUC(NRFI label | score=p_nrfi) must EQUAL AUC(YRFI label | score=p_yrfi)")
    print("=" * 100)
    D = {}
    for s in SEASONS:
        d = ac.load(s)
        D[s] = d
        a_n = ac.auc(d["cal"], d["y"])
        a_y = ac.auc(1.0 - d["cal"], 1.0 - d["y"])
        a_n_raw = ac.auc(d["raw"], d["y"])
        print(f"  {s:<12} n={len(d['y']):>5}  base NRFI={d['y'].mean():.4f}  "
              f"AUC_nrfi(cal)={a_n:.6f}  AUC_yrfi(cal)={a_y:.6f}  "
              f"diff={a_n - a_y:+.2e}   AUC_nrfi(raw)={a_n_raw:.6f}")
    print()
    print("=" * 100)
    print("  Q1: WITHIN-REGIME DISCRIMINATION  (split on CALIBRATED p_nrfi)")
    print("     AUC 0.50 = model cannot rank games inside that regime.")
    print("     BSS = Brier skill vs the regime's OWN base rate (>0 beats a constant).")
    print("=" * 100)
    for pname in ("cal", "raw"):
        print(f"\n  --- split variable & score = {pname.upper()} p_nrfi ---")
        hdr = (f"  {'season':<12}{'regime':<14}{'n':>6}{'base':>8}{'AUC':>8}"
               f"{'AUC 95% CI':>20}{'BSS':>9}{'pred':>8}{'act':>8}")
        print(hdr)
        for s in SEASONS:
            d = D[s]
            p = d[pname]
            for label, mask in (("p<0.50 (YRFI)", p < 0.50), ("p>=0.50 (NRFI)", p >= 0.50)):
                idx = np.where(mask)[0]
                if len(idx) < 30:
                    print(f"  {s:<12}{label:<14}{len(idx):>6}  (too small)")
                    continue
                a = ac.auc(p[idx], d["y"][idx])
                bss = ac.brier_skill(p[idx], d["y"][idx])
                lo, hi = ac.block_boot(
                    d["dates"], lambda ii: ac.auc(p[ii], d["y"][ii]), idx, n_boot=1500)
                print(f"  {s:<12}{label:<14}{len(idx):>6}{d['y'][idx].mean():>8.3f}"
                      f"{a:>8.4f}   [{lo:.4f},{hi:.4f}]{bss:>9.4f}"
                      f"{p[idx].mean():>8.3f}{d['y'][idx].mean():>8.3f}")

    print()
    print("=" * 100)
    print("  Q1b: ALTERNATIVE SPLITS -- is the effect a 0.50 artifact or a real")
    print("       function of where you cut?  (calibrated p, terciles + fixed cuts)")
    print("=" * 100)
    cuts = [(0.0, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 1.0)]
    print(f"  {'season':<12}{'band':<16}{'n':>6}{'base':>8}{'AUC':>8}{'AUC 95% CI':>20}{'BSS':>9}")
    for s in SEASONS:
        d = D[s]; p = d["cal"]
        for lo_c, hi_c in cuts:
            idx = np.where((p >= lo_c) & (p < hi_c))[0]
            if len(idx) < 40:
                print(f"  {s:<12}{f'[{lo_c},{hi_c})':<16}{len(idx):>6}  (too small)")
                continue
            a = ac.auc(p[idx], d["y"][idx])
            bss = ac.brier_skill(p[idx], d["y"][idx])
            lo, hi = ac.block_boot(d["dates"], lambda ii: ac.auc(p[ii], d["y"][ii]),
                                   idx, n_boot=1200)
            print(f"  {s:<12}{f'[{lo_c},{hi_c})':<16}{len(idx):>6}{d['y'][idx].mean():>8.3f}"
                  f"{a:>8.4f}   [{lo:.4f},{hi:.4f}]{bss:>9.4f}")

    print()
    print("=" * 100)
    print("  Q1c: MATCHED-WIDTH CONTROL.  Within-regime AUC shrinks mechanically")
    print("       when the score has less spread inside the window.  Report the")
    print("       IQR of p inside each regime so the AUCs are comparable.")
    print("=" * 100)
    print(f"  {'season':<12}{'regime':<16}{'n':>6}{'sd(p)':>9}{'IQR(p)':>9}{'range':>18}")
    for s in SEASONS:
        d = D[s]; p = d["cal"]
        for label, mask in (("p<0.50", p < 0.50), ("p>=0.50", p >= 0.50)):
            idx = np.where(mask)[0]
            if len(idx) < 5:
                continue
            q = np.percentile(p[idx], [25, 75])
            print(f"  {s:<12}{label:<16}{len(idx):>6}{p[idx].std():>9.4f}"
                  f"{q[1]-q[0]:>9.4f}   [{p[idx].min():.3f},{p[idx].max():.3f}]")


if __name__ == "__main__":
    main()

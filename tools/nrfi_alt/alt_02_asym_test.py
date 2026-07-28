#!/usr/bin/env python3
"""Q1 formal: block-bootstrap CI on the DIFFERENCE in within-regime AUC,
plus an in-sample / out-of-sample split.

CRITICAL CONTEXT baked into this script:
  * lr_t1.json / lr_b1.json were retrained 2026-05-26 on 2024+2025+2026YTD.
  * calibration_v2.json train_seasons = ['2025','2026'], train_n=3913.
  So EVERYTHING on or before 2026-05-26 is IN-SAMPLE for the live model.
  The only honest out-of-sample window is 2026-05-27 onward.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac

CUT = "2026-05-26"


def regime_pair(p, y, dates, idx_all, tag, n_boot=3000, seed=11):
    """AUC low-regime, AUC high-regime, and bootstrap CI on (low - high)."""
    def stat(ii):
        pl, yl, ph, yh = _split(p, y, ii)
        return ac.auc(pl, yl), ac.auc(ph, yh)

    pl, yl, ph, yh = _split(p, y, idx_all)
    a_lo, a_hi = ac.auc(pl, yl), ac.auc(ph, yh)
    rng = np.random.default_rng(seed)
    d = dates[idx_all]
    uniq = np.unique(d)
    by_day = {u: idx_all[d == u] for u in uniq}
    diffs, los, his = [], [], []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by_day[u] for u in pick])
        a, b = stat(ii)
        if np.isfinite(a) and np.isfinite(b):
            diffs.append(a - b); los.append(a); his.append(b)
    diffs = np.asarray(diffs)
    ci = (np.percentile(diffs, 2.5), np.percentile(diffs, 97.5))
    p_gt0 = float((diffs <= 0).mean())   # one-sided bootstrap p for lo > hi
    print(f"  {tag:<34} n={len(idx_all):>5}  nLo={len(yl):>5} nHi={len(yh):>5}  "
          f"AUC_lo={a_lo:.4f}  AUC_hi={a_hi:.4f}  "
          f"diff={a_lo-a_hi:+.4f}  95%CI[{ci[0]:+.4f},{ci[1]:+.4f}]  "
          f"boot-p(diff<=0)={p_gt0:.3f}")
    return a_lo, a_hi


def _split(p, y, ii):
    pi, yi = p[ii], y[ii]
    m = pi < 0.50
    return pi[m], yi[m], pi[~m], yi[~m]


def main():
    print("=" * 118)
    print("  A. WITHIN-REGIME AUC DIFFERENCE (low regime minus high regime), block bootstrap over DAYS")
    print("     diff > 0  =>  model discriminates BETTER on the YRFI-leaning side  =>  operator's hypothesis")
    print("=" * 118)
    for s in ["2025bt", "2026bt", "2026picks"]:
        d = ac.load(s)
        idx = np.arange(len(d["y"]))
        regime_pair(d["cal"], d["y"], d["dates"], idx, s + " (calibrated p)")

    print()
    print("=" * 118)
    print("  B. IN-SAMPLE vs OUT-OF-SAMPLE.  Models retrained 2026-05-26 on 2024+2025+2026YTD;")
    print("     calibrator fit on 2025+2026.  Only dates AFTER 2026-05-26 are honestly out-of-sample.")
    print("=" * 118)
    d = ac.load("2026picks")
    dates = d["dates"]
    for tag, mask in (("2026 picks IN-SAMPLE  (<=05-26)", dates <= CUT),
                      ("2026 picks OUT-OF-SAMPLE (>05-26)", dates > CUT)):
        idx = np.where(mask)[0]
        regime_pair(d["cal"], d["y"], d["dates"], idx, tag)

    print()
    print("  detail on those two windows:")
    print(f"  {'window':<34}{'regime':<12}{'n':>6}{'base':>8}{'pred':>8}{'AUC':>8}{'BSS':>9}")
    for tag, mask in (("IN-SAMPLE (<=05-26)", dates <= CUT),
                      ("OOS (>05-26)", dates > CUT)):
        idx = np.where(mask)[0]
        p, y = d["cal"][idx], d["y"][idx]
        for lab, m in (("p<0.50", p < 0.50), ("p>=0.50", p >= 0.50)):
            print(f"  {tag:<34}{lab:<12}{m.sum():>6}{y[m].mean():>8.3f}"
                  f"{p[m].mean():>8.3f}{ac.auc(p[m], y[m]):>8.4f}"
                  f"{ac.brier_skill(p[m], y[m]):>9.4f}")

    print()
    print("=" * 118)
    print("  C. SPREAD-MATCHED CONTROL.  Within-regime AUC falls mechanically when p has")
    print("     less spread inside the window.  Compare EQUAL-WIDTH windows straddling 0.50")
    print("     and also equal-n windows (median split of |p-0.50| within each side).")
    print("=" * 118)
    print(f"  {'season':<12}{'window':<16}{'n':>6}{'sd':>8}{'AUC':>8}{'AUC 95% CI':>20}")
    for s in ["2025bt", "2026bt", "2026picks"]:
        dd = ac.load(s)
        p, y, dt = dd["cal"], dd["y"], dd["dates"]
        for lo, hi in [(0.44, 0.50), (0.50, 0.56)]:
            idx = np.where((p >= lo) & (p < hi))[0]
            if len(idx) < 40:
                continue
            a = ac.auc(p[idx], y[idx])
            cl, ch = ac.block_boot(dt, lambda ii: ac.auc(p[ii], y[ii]), idx, n_boot=1500)
            print(f"  {s:<12}{f'[{lo},{hi})':<16}{len(idx):>6}{p[idx].std():>8.4f}"
                  f"{a:>8.4f}   [{cl:.4f},{ch:.4f}]")


if __name__ == "__main__":
    main()

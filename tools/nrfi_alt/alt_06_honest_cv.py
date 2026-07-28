#!/usr/bin/env python3
"""Contamination control for the headline.

The live lr_t1/lr_b1 were retrained 2026-05-26 on 2024+2025+2026YTD, and
calibration_v2 was fit on 2025+2026.  So the 2025 numbers in alt_01/02 are
IN-SAMPLE.  If the 2025 'no asymmetry' result is just memorisation on the
high side, an honest refit should reproduce the 2026 asymmetry on 2025 too.

This script rebuilds the exact production architecture from scratch
(two half-inning L2 logistics, l2=0.05, standardized -> product -> isotonic)
under DAY-BLOCKED 5-fold cross-validation inside each season, so every
prediction is out-of-fold, and re-runs the within-regime AUC split.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac
import recalibrate_v2 as rc
from calibration import ProbCalibrator
from sklearn.linear_model import LogisticRegression

K = 5
C = 1.0 / 0.05     # matches two_stage_model.py --l2 0.05


def fit_half(X, y, Xte):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    m = LogisticRegression(C=C, max_iter=6000).fit((X - mu) / sd, y)
    return m.predict_proba((Xte - mu) / sd)[:, 1]


def oof(d, seed=0):
    dates = d["dates"]
    uniq = np.unique(dates)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(uniq))
    fold_of_day = {uniq[perm[i]]: i % K for i in range(len(uniq))}
    fold = np.asarray([fold_of_day[x] for x in dates])
    p_raw = np.full(len(dates), np.nan)
    p_cal = np.full(len(dates), np.nan)
    yt1 = np.where(d["y_t1_run"] == 1, 1, 0)
    yb1 = np.where(d["y_b1_run"] == 1, 1, 0)
    for k in range(K):
        tr, te = fold != k, fold == k
        pt = fit_half(d["X_t1"][tr], yt1[tr], d["X_t1"][te])
        pb = fit_half(d["X_b1"][tr], yb1[tr], d["X_b1"][te])
        p_raw[te] = (1 - pt) * (1 - pb)
        # calibrator fit on the TRAINING folds' own in-fold predictions
        pt_tr = fit_half(d["X_t1"][tr], yt1[tr], d["X_t1"][tr])
        pb_tr = fit_half(d["X_b1"][tr], yb1[tr], d["X_b1"][tr])
        raw_tr = (1 - pt_tr) * (1 - pb_tr)
        cal = ProbCalibrator.fit(list(map(float, raw_tr)),
                                 list(map(int, d["y"][tr])), n_bins=20)
        p_cal[te] = [cal.predict(float(v)) for v in p_raw[te]]
    return p_raw, p_cal


def report(tag, p, y, dates):
    a_all = ac.auc(p, y)
    out = [f"  {tag:<30} n={len(y):>5}  AUC(all)={a_all:.4f}"]
    aucs = {}
    for lab, mask in (("lo p<0.50", p < 0.50), ("hi p>=0.50", p >= 0.50)):
        idx = np.where(mask)[0]
        a = ac.auc(p[idx], y[idx])
        aucs[lab] = a
        lo, hi = ac.block_boot(dates, lambda ii: ac.auc(p[ii], y[ii]), idx, n_boot=1200)
        out.append(f"      {lab:<12} n={len(idx):>5}  base={y[idx].mean():.3f}  "
                   f"AUC={a:.4f}  95%CI[{lo:.4f},{hi:.4f}]  "
                   f"BSS={ac.brier_skill(p[idx], y[idx]):+.4f}")
    out.append(f"      -> lo minus hi = {aucs['lo p<0.50'] - aucs['hi p>=0.50']:+.4f}")
    print("\n".join(out))
    print()


def main():
    print("=" * 100)
    print("  HONEST OUT-OF-FOLD REFIT (day-blocked 5-fold, production architecture).")
    print("  Every prediction below comes from a model that never saw that game's day.")
    print("  Averaged over 5 random fold assignments to damp seed noise on the split.")
    print("=" * 100)
    for s in ["2025bt", "2026picks"]:
        d = ac.load(s)
        accum_lo, accum_hi = [], []
        last = None
        for seed in range(5):
            _, pc = oof(d, seed=seed)
            lo = ac.auc(pc[pc < .5], d["y"][pc < .5])
            hi = ac.auc(pc[pc >= .5], d["y"][pc >= .5])
            accum_lo.append(lo); accum_hi.append(hi)
            if seed == 0:
                last = pc
        report(f"{s}  OOF (seed 0)", last, d["y"], d["dates"])
        print(f"      seed-averaged over 5 splits:  AUC_lo={np.mean(accum_lo):.4f}"
              f"  AUC_hi={np.mean(accum_hi):.4f}"
              f"  diff={np.mean(accum_lo)-np.mean(accum_hi):+.4f}"
              f"   (per-seed diffs: {', '.join(f'{a-b:+.3f}' for a, b in zip(accum_lo, accum_hi))})")
        print()
        print(f"  --- same season, LIVE (in-sample) model for comparison ---")
        report(f"{s}  live model", d["cal"], d["y"], d["dates"])


if __name__ == "__main__":
    main()

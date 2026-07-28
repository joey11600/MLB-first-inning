#!/usr/bin/env python3
"""REFUTATION PASS 1: re-derive the min(1-pT1,1-pB1) vs product AUC claim.

Claim under test:
  "Replace the multiplicative combiner (1-pT1)(1-pB1) with min(1-pT1,1-pB1)
   at the NRFI end only.  2026 top-35% AUC 0.5421 vs 0.5166 (+0.0255)."

Notes on identity:
  product = exp(-(lamT1+lamB1));  min = exp(-max(lamT1,lamB1)).
  Both are monotone functions of a lambda aggregate, so AUC comparison is
  purely a ranking question: SUM vs MAX.
READ-ONLY.
"""
from __future__ import annotations
import numpy as np
import alt_common as ac

EPS = 1e-9


def scores(d):
    n_t1 = 1.0 - d["p_t1_run"]
    n_b1 = 1.0 - d["p_b1_run"]
    return {
        "product": n_t1 * n_b1,
        "min": np.minimum(n_t1, n_b1),
        "max": np.maximum(n_t1, n_b1),
        "mean": 0.5 * (n_t1 + n_b1),
    }


def subset_masks(d, frac):
    """Top-`frac` of games at the NRFI end and at the YRFI end, ranked by the
    INCUMBENT calibrated p.  Using one fixed selector for both combiners is the
    only apples-to-apples comparison: if each combiner picks its own subset the
    AUCs are computed on different games."""
    p = d["cal"]
    k = int(round(frac * len(p)))
    order = np.argsort(-p, kind="mergesort")
    nrfi_end = np.zeros(len(p), bool); nrfi_end[order[:k]] = True
    order2 = np.argsort(p, kind="mergesort")
    yrfi_end = np.zeros(len(p), bool); yrfi_end[order2[:k]] = True
    return nrfi_end, yrfi_end


def paired_boot(dates, idx, s_a, s_b, y, n_boot=3000, seed=11):
    """Day-block bootstrap of AUC(a) - AUC(b) on the SAME resampled days."""
    rng = np.random.default_rng(seed)
    d = dates[idx]
    uniq = np.unique(d)
    by = {u: idx[d == u] for u in uniq}
    out = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by[u] for u in pick])
        va = ac.auc(s_a[ii], y[ii]); vb = ac.auc(s_b[ii], y[ii])
        if np.isfinite(va) and np.isfinite(vb):
            out.append(va - vb)
    if not out:
        return np.nan, np.nan, np.nan
    out = np.array(out)
    return float(out.mean()), float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def main():
    D = {s: ac.load(s) for s in ["2025bt", "2026picks", "2026bt"]}
    S = {s: scores(D[s]) for s in D}

    print("=" * 104)
    print("  A. FULL-SEASON AUC (all games).  y = NRFI happened.")
    print("=" * 104)
    print(f"  {'season':<12}{'n':>6}{'product':>10}{'min':>10}{'max':>10}{'mean':>10}"
          f"{'min-product':>14}")
    for s in ["2025bt", "2026picks", "2026bt"]:
        d, sc = D[s], S[s]
        row = {k: ac.auc(v, d["y"]) for k, v in sc.items()}
        print(f"  {s:<12}{len(d['y']):>6}{row['product']:>10.4f}{row['min']:>10.4f}"
              f"{row['max']:>10.4f}{row['mean']:>10.4f}"
              f"{row['min']-row['product']:>+14.4f}")

    print()
    print("=" * 104)
    print("  B. TOP-FRACTION AUC, subset chosen by the INCUMBENT calibrated p.")
    print("     NRFI end = highest p games.  YRFI end = lowest p games.")
    print("     'y' is always NRFI-happened, so an AUC>0.5 means the score ranks correctly.")
    print("=" * 104)
    for frac in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
        print(f"\n  --- top {frac:.0%} ---")
        print(f"  {'season':<12}{'end':<7}{'n':>6}{'AUC prod':>11}{'AUC min':>10}"
              f"{'delta':>10}{'95% CI day-block':>26}")
        for s in ["2025bt", "2026picks"]:
            d, sc = D[s], S[s]
            ne, ye = subset_masks(d, frac)
            for lab, mask in (("NRFI", ne), ("YRFI", ye)):
                idx = np.where(mask)[0]
                a = ac.auc(sc["min"][idx], d["y"][idx])
                b = ac.auc(sc["product"][idx], d["y"][idx])
                _, lo, hi = paired_boot(d["dates"], idx, sc["min"], sc["product"], d["y"])
                print(f"  {s:<12}{lab:<7}{len(idx):>6}{b:>11.4f}{a:>10.4f}"
                      f"{a-b:>+10.4f}   [{lo:+.4f}, {hi:+.4f}]")

    print()
    print("=" * 104)
    print("  C. SELF-SELECTED SUBSETS (each combiner picks its own top 35%).")
    print("     This is the variant that most easily manufactures a spurious gain.")
    print("=" * 104)
    print(f"  {'season':<12}{'end':<7}{'AUC prod':>11}{'AUC min':>10}{'delta':>10}{'overlap':>10}")
    for s in ["2025bt", "2026picks"]:
        d, sc = D[s], S[s]
        k = int(round(0.35 * len(d["y"])))
        for lab, sgn in (("NRFI", -1.0), ("YRFI", +1.0)):
            ip = np.argsort(sgn * sc["product"], kind="mergesort")[:k]
            im = np.argsort(sgn * sc["min"], kind="mergesort")[:k]
            b = ac.auc(sc["product"][ip], d["y"][ip])
            a = ac.auc(sc["min"][im], d["y"][im])
            ov = len(set(ip.tolist()) & set(im.tolist())) / k
            print(f"  {s:<12}{lab:<7}{b:>11.4f}{a:>10.4f}{a-b:>+10.4f}{ov:>10.1%}")

    print()
    print("=" * 104)
    print("  D. HOW DIFFERENT ARE THE TWO RANKINGS AT ALL?  Spearman + how many of the")
    print("     top-35% NRFI picks actually change.")
    print("=" * 104)
    from scipy.stats import spearmanr
    for s in ["2025bt", "2026picks"]:
        d, sc = D[s], S[s]
        rho = spearmanr(sc["product"], sc["min"]).statistic
        k = int(round(0.35 * len(d["y"])))
        ip = set(np.argsort(-sc["product"], kind="mergesort")[:k].tolist())
        im = set(np.argsort(-sc["min"], kind="mergesort")[:k].tolist())
        print(f"  {s:<12} spearman={rho:.4f}   top35% NRFI membership overlap="
              f"{len(ip & im)/k:.1%}   ({k-len(ip & im)} of {k} games swapped)")


if __name__ == "__main__":
    main()

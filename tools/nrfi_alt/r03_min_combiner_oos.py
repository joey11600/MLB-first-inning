#!/usr/bin/env python3
"""REFUTATION PASS 3: out-of-sample survival of the min() combiner's
NRFI-end AUC gain.

The claim rests on ONE cell: 2026, top-35% NRFI end.  This asks whether that
cell is a stable property or a window artefact:
  (a) month-by-month within 2026,
  (b) time split: 2026 first half (search window) vs 2026 second half (held out),
  (c) 2025 month-by-month,
  (d) search exposure: how many (fraction x end x season) cells exist, and how
      often a coin-flip null produces a >= +0.0254 delta in at least one of them.

ANALYSIS ONLY.
"""
from __future__ import annotations
import numpy as np
import alt_common as ac


def scores(d):
    n_t1 = 1.0 - d["p_t1_run"]
    n_b1 = 1.0 - d["p_b1_run"]
    return {"product": n_t1 * n_b1, "min": np.minimum(n_t1, n_b1)}


def topmask(p, frac, nrfi_end=True):
    k = int(round(frac * len(p)))
    sgn = -1.0 if nrfi_end else 1.0
    order = np.argsort(sgn * p, kind="mergesort")
    m = np.zeros(len(p), bool)
    m[order[:k]] = True
    return m


def main():
    D = {s: ac.load(s) for s in ["2025bt", "2026picks"]}
    S = {s: scores(D[s]) for s in D}

    print("=" * 96)
    print("  A. MONTH-BY-MONTH, top-35% NRFI end (subset re-selected inside each month")
    print("     by the incumbent calibrated p, so each month is a clean mini-replication)")
    print("=" * 96)
    print(f"  {'season':<11}{'month':<9}{'n games':>9}{'n top35':>9}{'AUC prod':>11}"
          f"{'AUC min':>10}{'delta':>9}")
    for s in ["2025bt", "2026picks"]:
        d, sc = D[s], S[s]
        months = np.array([x[:7] for x in d["dates"]])
        for mo in sorted(set(months.tolist())):
            mm = months == mo
            idx = np.where(mm)[0]
            if len(idx) < 60:
                continue
            sub = topmask(d["cal"][idx], 0.35, True)
            ii = idx[sub]
            b = ac.auc(sc["product"][ii], d["y"][ii])
            a = ac.auc(sc["min"][ii], d["y"][ii])
            print(f"  {s:<11}{mo:<9}{len(idx):>9}{len(ii):>9}{b:>11.4f}{a:>10.4f}"
                  f"{a-b:>+9.4f}")
        print()

    print("=" * 96)
    print("  B. TIME SPLIT INSIDE 2026.  If the effect is real it should appear in both")
    print("     halves; if it is a window artefact it lives in one.")
    print("=" * 96)
    d, sc = D["2026picks"], S["2026picks"]
    order = np.argsort(d["dates"], kind="mergesort")
    half = len(order) // 2
    for lab, idx in (("2026 first half", order[:half]), ("2026 second half", order[half:])):
        dsub = sorted(d["dates"][idx].tolist())
        print(f"  {lab}  dates {dsub[0]} .. {dsub[-1]}  n={len(idx)}")
        for frac in (0.25, 0.35, 0.50):
            sub = topmask(d["cal"][idx], frac, True)
            ii = idx[sub]
            b = ac.auc(sc["product"][ii], d["y"][ii])
            a = ac.auc(sc["min"][ii], d["y"][ii])
            print(f"      top {frac:.0%}  n={len(ii):>4}  prod {b:.4f}  min {a:.4f}"
                  f"  delta {a-b:+.4f}")
        print()

    print("=" * 96)
    print("  C. SEARCH EXPOSURE.  Under a pure null (min ranking is no better or worse")
    print("     than product), how easily does a 24-cell grid throw off a +0.0254?")
    print("     Null built by permuting the OUTCOME within day blocks, keeping both")
    print("     scores and the subset selection fixed.  4000 draws.")
    print("=" * 96)
    rng = np.random.default_rng(23)
    d, sc = D["2026picks"], S["2026picks"]
    fracs = (0.20, 0.25, 0.30, 0.35, 0.40, 0.50)
    masks = {}
    for f in fracs:
        for end in (True, False):
            masks[(f, end)] = np.where(topmask(d["cal"], f, end))[0]
    obs_single = None
    hits_single = 0
    hits_any = 0
    B = 4000
    uniq = np.unique(d["dates"])
    day_idx = {u: np.where(d["dates"] == u)[0] for u in uniq}
    for bi in range(B):
        yp = d["y"].copy()
        for u in uniq:
            ii = day_idx[u]
            yp[ii] = rng.permutation(yp[ii])
        # global shuffle across days too (day-block permutation of labels)
        perm_days = rng.permutation(uniq)
        y2 = yp.copy()
        for u, v in zip(uniq, perm_days):
            a_i, b_i = day_idx[u], day_idx[v]
            n = min(len(a_i), len(b_i))
            y2[a_i[:n]] = yp[b_i[:n]]
        best = -9.0
        for key, ii in masks.items():
            dd = ac.auc(sc["min"][ii], y2[ii]) - ac.auc(sc["product"][ii], y2[ii])
            if np.isfinite(dd):
                best = max(best, dd)
                if key == (0.35, True) and dd >= 0.0254:
                    hits_single += 1
        if best >= 0.0254:
            hits_any += 1
    print(f"  P(delta >= +0.0254 in the SPECIFIC top-35% NRFI cell)      = {hits_single/B:.3f}")
    print(f"  P(delta >= +0.0254 in AT LEAST ONE of the 12 fraction/end cells,")
    print(f"     before even counting the 2 seasons or the other combiners) = {hits_any/B:.3f}")


if __name__ == "__main__":
    main()

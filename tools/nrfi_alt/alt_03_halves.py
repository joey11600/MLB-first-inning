#!/usr/bin/env python3
"""Q2: does the T1 half-inning model discriminate better than the B1 model?
P(NRFI) = (1-pT1)(1-pB1), so a dead half drags the NRFI end disproportionately.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac

CUT = "2026-05-26"


def line(tag, p, y, dates, idx):
    a = ac.auc(p[idx], y[idx])
    bss = ac.brier_skill(p[idx], y[idx])
    lo, hi = ac.block_boot(dates, lambda ii: ac.auc(p[ii], y[ii]), idx, n_boot=1500)
    print(f"  {tag:<40}{len(idx):>6}{y[idx].mean():>8.3f}{p[idx].mean():>8.3f}"
          f"{a:>8.4f}   [{lo:.4f},{hi:.4f}]{bss:>9.4f}")
    return a


def main():
    print("=" * 112)
    print("  Q2a: HALF-INNING DISCRIMINATION.  Target = did a run score in THAT half.")
    print("       T1 = top of 1st (away bats vs HOME starter).  B1 = bottom (home bats vs AWAY starter).")
    print("=" * 112)
    print(f"  {'model / season':<40}{'n':>6}{'base':>8}{'pred':>8}{'AUC':>8}{'AUC 95% CI':>20}{'BSS':>9}")
    store = {}
    for s in ["2025bt", "2026bt", "2026picks"]:
        d = ac.load(s)
        store[s] = d
        mt = np.where(np.isfinite(d["y_t1_run"]))[0]
        mb = np.where(np.isfinite(d["y_b1_run"]))[0]
        if len(mt) < 50:
            print(f"  {s}: no per-half run columns populated (n={len(mt)})")
            continue
        at = line(f"{s}  T1 model -> P(run in top 1st)", d["p_t1_run"], d["y_t1_run"], d["dates"], mt)
        ab = line(f"{s}  B1 model -> P(run in bot 1st)", d["p_b1_run"], d["y_b1_run"], d["dates"], mb)
        # paired bootstrap on the difference
        idx = np.intersect1d(mt, mb)
        rng = np.random.default_rng(3)
        dts = d["dates"][idx]
        uniq = np.unique(dts)
        by_day = {u: idx[dts == u] for u in uniq}
        diffs = []
        for _ in range(3000):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            ii = np.concatenate([by_day[u] for u in pick])
            x = ac.auc(d["p_t1_run"][ii], d["y_t1_run"][ii])
            z = ac.auc(d["p_b1_run"][ii], d["y_b1_run"][ii])
            if np.isfinite(x) and np.isfinite(z):
                diffs.append(x - z)
        diffs = np.asarray(diffs)
        print(f"  {'  -> T1 minus B1 AUC':<40}{'':>6}{'':>8}{'':>8}{at-ab:>+8.4f}   "
              f"[{np.percentile(diffs,2.5):+.4f},{np.percentile(diffs,97.5):+.4f}]"
              f"   boot-p(<=0)={float((diffs<=0).mean()):.3f}")
        print()

    print("=" * 112)
    print("  Q2b: INDEPENDENCE.  P(NRFI)=(1-pT1)(1-pB1) assumes halves are independent.")
    print("       Observed correlation of the two half outcomes (phi) and the")
    print("       observed vs product-implied joint-no-run rate.")
    print("=" * 112)
    for s, d in store.items():
        m = np.isfinite(d["y_t1_run"]) & np.isfinite(d["y_b1_run"])
        if m.sum() < 50:
            continue
        a, b = d["y_t1_run"][m], d["y_b1_run"][m]
        phi = float(np.corrcoef(a, b)[0, 1])
        obs_joint = float(((a == 0) & (b == 0)).mean())
        prod = float((1 - a.mean()) * (1 - b.mean()))
        print(f"  {s:<12} n={m.sum():>5}  P(run T1)={a.mean():.4f}  P(run B1)={b.mean():.4f}  "
              f"phi={phi:+.4f}  obs P(no run both)={obs_joint:.4f}  "
              f"independence-implied={prod:.4f}  gap={obs_joint-prod:+.4f}")

    print()
    print("=" * 112)
    print("  Q2c: WITHIN-REGIME half AUC.  Inside the NRFI-leaning region (p_nrfi>=0.50),")
    print("       does EITHER half still rank its own outcome?")
    print("=" * 112)
    print(f"  {'season / regime':<40}{'n':>6}{'base':>8}{'pred':>8}{'AUC':>8}{'AUC 95% CI':>20}{'BSS':>9}")
    for s, d in store.items():
        p = d["cal"]
        for lab, mask in (("p<0.50", p < 0.50), ("p>=0.50", p >= 0.50)):
            for half, ps, ys in (("T1", d["p_t1_run"], d["y_t1_run"]),
                                 ("B1", d["p_b1_run"], d["y_b1_run"])):
                idx = np.where(mask & np.isfinite(ys))[0]
                if len(idx) < 60:
                    continue
                line(f"{s}  {lab}  {half}", ps, ys, d["dates"], idx)
        print()

    print("=" * 112)
    print("  Q2d: OUT-OF-SAMPLE (2026 picks after 2026-05-26 only).")
    print("=" * 112)
    d = store["2026picks"]
    oos = d["dates"] > CUT
    print(f"  {'model / regime':<40}{'n':>6}{'base':>8}{'pred':>8}{'AUC':>8}{'AUC 95% CI':>20}{'BSS':>9}")
    for half, ps, ys in (("T1", d["p_t1_run"], d["y_t1_run"]),
                         ("B1", d["p_b1_run"], d["y_b1_run"])):
        idx = np.where(oos & np.isfinite(ys))[0]
        line(f"OOS all      {half}", ps, ys, d["dates"], idx)
    for lab, mask in (("p<0.50", d["cal"] < 0.50), ("p>=0.50", d["cal"] >= 0.50)):
        for half, ps, ys in (("T1", d["p_t1_run"], d["y_t1_run"]),
                             ("B1", d["p_b1_run"], d["y_b1_run"])):
            idx = np.where(oos & mask & np.isfinite(ys))[0]
            if len(idx) < 60:
                continue
            line(f"OOS {lab:<8} {half}", ps, ys, d["dates"], idx)


if __name__ == "__main__":
    main()

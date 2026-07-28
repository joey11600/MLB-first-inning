#!/usr/bin/env python3
"""The product P(NRFI)=(1-pT1)(1-pB1) treats a 0.75/0.73 half-pair the same as
a 0.90/0.61 pair.  Under independence that is CORRECT.  This script asks
whether it is empirically correct, i.e. whether any information about NRFI
survives after conditioning on the product.

Tests, all fit OUT OF SEASON (train 2025 -> test 2026, and train 2026 -> test 2025):
  M0  product                     (the incumbent)
  M1  logistic on [logit(1-pT1), logit(1-pB1)]        -- free weights per half
  M2  logistic on [logit(product), |logit(1-pT1) - logit(1-pB1)|]  -- balance term
  M3  logistic on [logit(product), min(1-pT1,1-pB1)]  -- weakest-link term
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac
from sklearn.linear_model import LogisticRegression

EPS = 1e-6
CUT = "2026-05-26"


def lg(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def feats(d):
    n_t1 = 1.0 - d["p_t1_run"]           # P(no run in top)
    n_b1 = 1.0 - d["p_b1_run"]           # P(no run in bottom)
    prod = n_t1 * n_b1
    L = lg(prod); a = lg(n_t1); b = lg(n_b1)
    return {
        "M0": prod.reshape(-1, 1),
        "M1": np.c_[a, b],
        "M2": np.c_[L, np.abs(a - b)],
        "M3": np.c_[L, np.minimum(n_t1, n_b1)],
        "_prod": prod, "_bal": np.abs(a - b), "_L": L,
    }


def zfit(Xtr, ytr, Xte):
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1.0
    m = LogisticRegression(C=1.0, max_iter=2000)
    m.fit((Xtr - mu) / sd, ytr)
    return m.predict_proba((Xte - mu) / sd)[:, 1], m.coef_[0] / sd


def main():
    D = {s: ac.load(s) for s in ["2025bt", "2026picks"]}
    F = {s: feats(D[s]) for s in D}

    print("=" * 112)
    print("  A. OUT-OF-SEASON COMBINER TEST.  Does anything beat the product at ranking NRFI?")
    print("     Reported: AUC on the FULL test season, and AUC inside the NRFI-leaning regime.")
    print("=" * 112)
    print(f"  {'train->test':<20}{'model':<6}{'AUC all':>10}{'AUC p>=.50':>12}"
          f"{'AUC p<.50':>11}{'n hi':>7}{'n lo':>7}")
    for tr, te in [("2025bt", "2026picks"), ("2026picks", "2025bt")]:
        dtr, dte = D[tr], D[te]
        ftr, fte = F[tr], F[te]
        hi = dte["cal"] >= 0.50
        lo = ~hi
        for m in ["M0", "M1", "M2", "M3"]:
            if m == "M0":
                s = fte["_prod"]
            else:
                s, _ = zfit(ftr[m], dtr["y"], fte[m])
            print(f"  {tr+'->'+te:<20}{m:<6}{ac.auc(s, dte['y']):>10.4f}"
                  f"{ac.auc(s[hi], dte['y'][hi]):>12.4f}"
                  f"{ac.auc(s[lo], dte['y'][lo]):>11.4f}{hi.sum():>7}{lo.sum():>7}")
        print()

    print("=" * 112)
    print("  B. DIRECT RESIDUAL TEST.  Inside each regime, regress the NRFI outcome on")
    print("     logit(product) PLUS the half-balance term.  If balance carries independent")
    print("     information the product is discarding, its coefficient is non-zero.")
    print("     (in-sample within the stated window -- this is a signal-detection screen,")
    print("      the out-of-season table above is the honest test)")
    print("=" * 112)
    print(f"  {'season':<12}{'regime':<12}{'n':>6}{'coef logit(prod)':>19}{'coef |bal|':>13}"
          f"{'bal 95% CI (day boot)':>28}")
    for s in ["2025bt", "2026picks"]:
        d, f = D[s], F[s]
        for lab, mask in (("p<0.50", d["cal"] < 0.50), ("p>=0.50", d["cal"] >= 0.50)):
            idx = np.where(mask)[0]
            X = np.c_[f["_L"], f["_bal"]][idx]
            y = d["y"][idx]
            mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1
            mdl = LogisticRegression(C=1e6, max_iter=5000).fit((X - mu) / sd, y)
            c = mdl.coef_[0]
            # day-block bootstrap on the balance coefficient
            rng = np.random.default_rng(5)
            dts = d["dates"][idx]; uniq = np.unique(dts)
            by = {u: idx[dts == u] for u in uniq}
            bs = []
            for _ in range(600):
                pick = rng.choice(uniq, size=len(uniq), replace=True)
                ii = np.concatenate([by[u] for u in pick])
                Xb = np.c_[f["_L"], f["_bal"]][ii]; yb = d["y"][ii]
                if yb.mean() in (0.0, 1.0):
                    continue
                mb, sb = Xb.mean(0), Xb.std(0); sb[sb == 0] = 1
                try:
                    bs.append(LogisticRegression(C=1e6, max_iter=3000)
                              .fit((Xb - mb) / sb, yb).coef_[0][1])
                except Exception:
                    pass
            ci = (np.percentile(bs, 2.5), np.percentile(bs, 97.5)) if bs else (np.nan, np.nan)
            print(f"  {s:<12}{lab:<12}{len(idx):>6}{c[0]:>19.4f}{c[1]:>13.4f}"
                  f"     [{ci[0]:+.4f},{ci[1]:+.4f}]")

    print()
    print("=" * 112)
    print("  C. WHY the product loses AUC in the high regime while the halves keep it:")
    print("     conditioning on the product forces pT1 and pB1 to trade off, so the")
    print("     product's own spread collapses while each half's spread does not.")
    print("=" * 112)
    print(f"  {'season':<12}{'regime':<10}{'n':>6}{'sd logit(prod)':>16}"
          f"{'sd logit nT1':>14}{'sd logit nB1':>14}{'corr(a,b)':>11}")
    for s in ["2025bt", "2026picks"]:
        d, f = D[s], F[s]
        a = lg(1 - d["p_t1_run"]); b = lg(1 - d["p_b1_run"])
        for lab, mask in (("all", np.ones(len(a), bool)),
                          ("p<0.50", d["cal"] < 0.50), ("p>=0.50", d["cal"] >= 0.50)):
            print(f"  {s:<12}{lab:<10}{mask.sum():>6}{f['_L'][mask].std():>16.4f}"
                  f"{a[mask].std():>14.4f}{b[mask].std():>14.4f}"
                  f"{np.corrcoef(a[mask], b[mask])[0,1]:>+11.4f}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Q3: which features carry the signal in each regime, and can a REGIME-SPECIFIC
model beat the global one inside the NRFI-leaning region?

Two parts:
  A. Out-of-season regime-restricted refit (train 2025 in-regime -> test 2026
     in-same-regime).  This is the operationally meaningful version of "predict
     NRFI differently": a dedicated high-regime model.
  B. Standardized coefficients per regime per season + sign-flip audit.
     CAVEAT stated in output: the regime is DEFINED by the model's own p, which
     is a function of these same features, so restricting on it is a collider.
     Coefficients are read as descriptive, not causal.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac
import recalibrate_v2 as rc
from sklearn.linear_model import LogisticRegression

# Combined game-level feature set: T1 vector + B1 vector minus the six columns
# that are identical in both (park, 4x weather, umpire) and era_gap_b1 (= -t1).
SHARED_IDX = [0, 3, 4, 5, 6, 9]
SHARED_NAMES = ["fi_park", "wx_temp", "wx_wind", "wx_humid", "wx_dome", "ump_nrfi"]
T1_ONLY = [i for i in range(len(rc.T1_FEATURES)) if i not in SHARED_IDX]
B1_ONLY = [i for i in range(len(rc.B1_FEATURES)) if i not in SHARED_IDX
           and rc.B1_FEATURES[i] != "era_gap_b1"]
NAMES = (SHARED_NAMES
         + ["T1:" + rc.T1_FEATURES[i] for i in T1_ONLY]
         + ["B1:" + rc.B1_FEATURES[i] for i in B1_ONLY])


def X_of(d):
    return np.c_[d["X_t1"][:, SHARED_IDX], d["X_t1"][:, T1_ONLY], d["X_b1"][:, B1_ONLY]]


def fit_pred(Xtr, ytr, Xte, C=0.3):
    mu, sd = Xtr.mean(0), Xtr.std(0); sd[sd == 0] = 1.0
    m = LogisticRegression(C=C, max_iter=4000).fit((Xtr - mu) / sd, ytr)
    return m.predict_proba((Xte - mu) / sd)[:, 1], m.coef_[0]


def main():
    D = {s: ac.load(s) for s in ["2025bt", "2026picks"]}
    for s in D:
        D[s]["X"] = X_of(D[s])
    print(f"  combined feature set: {len(NAMES)} columns")

    print()
    print("=" * 112)
    print("  A. DEDICATED HIGH-REGIME MODEL vs THE INCUMBENT, OUT OF SEASON.")
    print("     'regime-only' trains only on games the incumbent puts in that regime.")
    print("     'global'      trains on the whole season, then is scored inside the regime.")
    print("     Beating the incumbent requires AUC materially above the incumbent column.")
    print("=" * 112)
    print(f"  {'train->test':<22}{'regime':<10}{'n_tr':>6}{'n_te':>6}"
          f"{'incumbent':>11}{'global':>9}{'regime-only':>13}{'CI on regime-only - incumbent':>32}")
    for tr, te in [("2025bt", "2026picks"), ("2026picks", "2025bt")]:
        dtr, dte = D[tr], D[te]
        for lab, thr in (("p<0.50", False), ("p>=0.50", True)):
            mtr = (dtr["cal"] >= 0.50) if thr else (dtr["cal"] < 0.50)
            mte = (dte["cal"] >= 0.50) if thr else (dte["cal"] < 0.50)
            itr, ite = np.where(mtr)[0], np.where(mte)[0]
            s_glob, _ = fit_pred(dtr["X"], dtr["y"], dte["X"][ite])
            s_reg, _ = fit_pred(dtr["X"][itr], dtr["y"][itr], dte["X"][ite])
            inc = dte["cal"][ite]; y = dte["y"][ite]
            a_i, a_g, a_r = ac.auc(inc, y), ac.auc(s_glob, y), ac.auc(s_reg, y)
            # paired day-block bootstrap on (regime-only AUC - incumbent AUC)
            rng = np.random.default_rng(17)
            dts = dte["dates"][ite]; uniq = np.unique(dts)
            pos = {u: np.where(dts == u)[0] for u in uniq}
            diffs = []
            for _ in range(2000):
                pick = rng.choice(uniq, size=len(uniq), replace=True)
                jj = np.concatenate([pos[u] for u in pick])
                x, z = ac.auc(s_reg[jj], y[jj]), ac.auc(inc[jj], y[jj])
                if np.isfinite(x) and np.isfinite(z):
                    diffs.append(x - z)
            ci = (np.percentile(diffs, 2.5), np.percentile(diffs, 97.5))
            print(f"  {tr+'->'+te:<22}{lab:<10}{len(itr):>6}{len(ite):>6}"
                  f"{a_i:>11.4f}{a_g:>9.4f}{a_r:>13.4f}"
                  f"     {a_r-a_i:+.4f}  [{ci[0]:+.4f},{ci[1]:+.4f}]")
        print()

    print("=" * 112)
    print("  B. STANDARDIZED COEFFICIENTS BY REGIME (unpenalised, target = NRFI=1).")
    print("     CAVEAT: the regime is defined by the model's own p_nrfi, a function of")
    print("     these same columns -- restricting on it is a COLLIDER.  Signs here are")
    print("     descriptive of the conditional slice, NOT evidence of a causal flip.")
    print("     'flip' marks a feature whose sign differs across the two regimes in BOTH seasons.")
    print("=" * 112)
    coefs = {}
    for s in D:
        d = D[s]
        for lab, mask in (("lo", d["cal"] < 0.50), ("hi", d["cal"] >= 0.50)):
            idx = np.where(mask)[0]
            _, c = fit_pred(d["X"][idx], d["y"][idx], d["X"][idx], C=1e6)
            coefs[(s, lab)] = c
    hdr = f"  {'feature':<34}"
    for s in D:
        for lab in ("lo", "hi"):
            hdr += f"{s[:4]+'.'+lab:>11}"
    print(hdr + "   flip?")
    for i, nm in enumerate(NAMES):
        row = f"  {nm:<34}"
        vals = {}
        for s in D:
            for lab in ("lo", "hi"):
                v = coefs[(s, lab)][i]; vals[(s, lab)] = v
                row += f"{v:>+11.3f}"
        flip = all(np.sign(vals[(s, "lo")]) != np.sign(vals[(s, "hi")]) for s in D)
        print(row + ("   FLIP" if flip else ""))

    print()
    print("=" * 112)
    print("  C. PER-FEATURE UNIVARIATE AUC INSIDE EACH REGIME (2026 picks, |AUC-0.5| ranked).")
    print("     A feature with real within-regime signal shows |AUC-0.5| well above the")
    print("     noise floor.  Noise floor for n~560 is roughly 0.022 (1 s.e.).")
    print("=" * 112)
    d = D["2026picks"]
    rows = []
    for i, nm in enumerate(NAMES):
        lo = np.where(d["cal"] < 0.50)[0]; hi = np.where(d["cal"] >= 0.50)[0]
        rows.append((nm, ac.auc(d["X"][lo, i], d["y"][lo]),
                     ac.auc(d["X"][hi, i], d["y"][hi])))
    rows.sort(key=lambda r: -abs(r[2] - 0.5))
    print(f"  {'feature':<34}{'AUC lo (n=971)':>16}{'AUC hi (n=562)':>16}{'|hi-0.5|':>11}")
    for nm, a, b in rows:
        print(f"  {nm:<34}{a:>16.4f}{b:>16.4f}{abs(b-0.5):>11.4f}")


if __name__ == "__main__":
    main()

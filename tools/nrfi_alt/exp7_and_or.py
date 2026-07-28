#!/usr/bin/env python3
"""
exp7: WHY the NRFI end is the weak end.

Hypothesis: p_nrfi = (1 - p_t1)(1 - p_b1) is an AND at the NRFI end and
an OR at the YRFI end.  A high p_nrfi requires the model to be right about
BOTH halves; a low p_nrfi (= high YRFI) only requires it to be right about
ONE.  Errors compound multiplicatively going up and cancel going down.

Tests:
  1. per-half AUC on the half's own outcome (the raw ingredient quality)
  2. independence IN THE TAIL: is P(both quiet | high p_nrfi) actually the
     product of the two marginals there, or does dependence bite?
  3. does any NON-multiplicative combiner of the two half-probabilities
     rank better at the NRFI end?  (min, mean, logit-average, learned)
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import recalibrate_v2 as rc


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())
    t1m, b1m = rc.load_lr_models()
    for rows in (r25, r26):
        Xt = np.array([r["t1"] for r in rows], float)
        Xb = np.array([r["b1"] for r in rows], float)
        pt = rc.lr_predict_raw(t1m, Xt)
        pb = rc.lr_predict_raw(b1m, Xb)
        for r, a, b in zip(rows, pt, pb):
            r["p_t1"] = float(a)
            r["p_b1"] = float(b)

    print("=" * 96)
    print("  EXP7-1  per-half ingredient quality (AUC on that half's own run/no-run)")
    print("=" * 96)
    for tag, rows in (("2025", r25), ("2026", r26)):
        ok = [r for r in rows if r["y_t1_run"] is not None and r["y_b1_run"] is not None]
        yt = np.array([r["y_t1_run"] for r in ok])
        yb = np.array([r["y_b1_run"] for r in ok])
        print(f"      {tag}  n={len(ok)}   T1 run rate={yt.mean():.4f} AUC={C.auc(yt,[r['p_t1'] for r in ok]):.4f}"
              f"   B1 run rate={yb.mean():.4f} AUC={C.auc(yb,[r['p_b1'] for r in ok]):.4f}")
        # dependence
        both = np.mean((yt == 0) & (yb == 0))
        prod = (1 - yt.mean()) * (1 - yb.mean())
        phi = np.corrcoef(yt, yb)[0, 1]
        print(f"            P(both quiet) actual={both:.4f}  product of marginals={prod:.4f}  "
              f"diff={both-prod:+.4f}   phi={phi:+.4f}")

    print("\n" + "=" * 96)
    print("  EXP7-2  independence IN THE TAIL -- does the AND hold where it matters?")
    print("=" * 96)
    for tag, rows in (("2025", r25), ("2026", r26)):
        ok = [r for r in rows if r["y_t1_run"] is not None and r["y_b1_run"] is not None]
        p = np.array([r["prod"] for r in ok])
        yt = np.array([r["y_t1_run"] for r in ok])
        yb = np.array([r["y_b1_run"] for r in ok])
        print(f"      {tag}")
        for lab, m in (("bottom 25% (YRFI end)", p <= np.quantile(p, .25)),
                       ("middle 50%          ", (p > np.quantile(p, .25)) & (p < np.quantile(p, .75))),
                       ("top 25%    (NRFI end)", p >= np.quantile(p, .75))):
            a, b = yt[m], yb[m]
            act = np.mean((a == 0) & (b == 0))
            pr = (1 - a.mean()) * (1 - b.mean())
            phi = np.corrcoef(a, b)[0, 1] if a.std() > 0 and b.std() > 0 else float("nan")
            # what the model said
            pm = np.mean([r["prod"] for r, k in zip(ok, m) if k])
            print(f"        {lab} n={m.sum():>4}  model said {pm:.4f}  actual NRFI {act:.4f}  "
                  f"indep-implied {pr:.4f}  dependence {act-pr:+.4f}  phi={phi:+.4f}")

    print("\n" + "=" * 96)
    print("  EXP7-3  non-multiplicative combiners, ranked AT THE NRFI END")
    print("=" * 96)
    print("      train the combiner on 2025, evaluate on 2026 (and the reverse).")
    for tr, te, tag in ((r25, r26, "train 2025 -> test 2026"),
                        (r26, r25, "train 2026 -> test 2025")):
        def feats(rows):
            a = np.array([r["p_t1"] for r in rows])
            b = np.array([r["p_b1"] for r in rows])
            return a, b
        at, bt = feats(tr)
        ae, be = feats(te)
        ytr = np.array([r["y_nrfi"] for r in tr], float)
        yte = np.array([r["y_nrfi"] for r in te], float)
        lg = lambda x: np.log(np.clip(x, 1e-6, 1 - 1e-6) / (1 - np.clip(x, 1e-6, 1 - 1e-6)))
        learned = C.fit_lr(np.c_[lg(at), lg(bt), lg(at) * lg(bt)], ytr, 1.0)
        cands = {
            "production product (1-a)(1-b)": (1 - ae) * (1 - be),
            "min(1-a,1-b)  [weakest link]": np.minimum(1 - ae, 1 - be),
            "arithmetic mean of (1-p)": ((1 - ae) + (1 - be)) / 2,
            "logit-average": 1 / (1 + np.exp((lg(ae) + lg(be)) / 2)),
            "learned combiner (+interaction)": C.predict_lr(learned, np.c_[lg(ae), lg(be), lg(ae) * lg(be)]),
        }
        print(f"\n      {tag}   (n_te={len(te)})")
        base = (1 - ae) * (1 - be)
        top = base >= np.quantile(base, 0.65)
        bot = base <= np.quantile(base, 0.35)
        print(f"        {'combiner':<34}{'AUC all':>9}{'AUC top35%':>12}{'AUC bot35%':>12}")
        for lab, s in cands.items():
            print(f"        {lab:<34}{C.auc(yte, s):>9.4f}{C.auc(yte[top], s[top]):>12.4f}"
                  f"{C.auc(yte[bot], s[bot]):>12.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

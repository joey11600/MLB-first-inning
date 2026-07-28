#!/usr/bin/env python3
"""
exp1: does a model trained ONLY on the high-p_nrfi region discriminate
better THERE than a generalist trained on everything?

Fair fight: production is frozen and was fit on 2024+2025+2026YTD, so it
has SEEN the 2026 test rows. To avoid handing it that advantage, every
challenger AND a like-for-like generalist are fit on 2025 ONLY and tested
on 2026 ONLY. Production is reported alongside as a (leaky) reference.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def region_mask(rows, mode, cut):
    p = np.array([r["prod"] for r in rows])
    if mode == "abs":
        return p >= cut
    q = np.quantile(p, 1.0 - cut)
    return p >= q


def two_stage_fit(rows, l2=1.0):
    Xt = np.array([r["t1"] for r in rows], float)
    Xb = np.array([r["b1"] for r in rows], float)
    yt = np.array([r["y_t1_run"] for r in rows], float)
    yb = np.array([r["y_b1_run"] for r in rows], float)
    return C.fit_lr(Xt, yt, l2), C.fit_lr(Xb, yb, l2)


def two_stage_pred(models, rows):
    mt, mb = models
    Xt = np.array([r["t1"] for r in rows], float)
    Xb = np.array([r["b1"] for r in rows], float)
    return (1 - C.predict_lr(mt, Xt)) * (1 - C.predict_lr(mb, Xb))


def report(name, y, p, n_train):
    print(f"  {name:<44} n_tr={n_train:>5}  AUC={C.auc(y,p):.4f}  "
          f"LL={C.logloss(y,p):.4f}  Brier={C.brier(y,p):.4f}  mean_p={np.mean(p):.4f}")


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())
    X25, names = C.design(r25)
    X26, _ = C.design(r26)
    y25 = np.array([r["y_nrfi"] for r in r25])
    y26 = np.array([r["y_nrfi"] for r in r26])

    print("=" * 100)
    print("  EXP1  specialist-in-region vs generalist,  train 2025 -> test 2026")
    print("=" * 100)
    print(f"  2025 n={len(r25)}  NRFI base={y25.mean():.4f}")
    print(f"  2026 n={len(r26)}  NRFI base={y26.mean():.4f}")
    print(f"  production p_nrfi AUC  2025={C.auc(y25,[r['prod'] for r in r25]):.4f}  "
          f"2026={C.auc(y26,[r['prod'] for r in r26]):.4f}   (production SAW 2025+2026 in training)")

    # generalist challengers fit on ALL of 2025
    gen_direct = C.fit_lr(X25, y25, l2=10.0)
    gen_two = two_stage_fit(r25, l2=10.0)

    for mode, cut, label in [("abs", 0.55, "p_prod >= 0.55"),
                             ("abs", 0.52, "p_prod >= 0.52"),
                             ("q", 0.35, "top 35% by p_prod (per season)"),
                             ("q", 0.50, "top 50% by p_prod (per season)")]:
        m25 = region_mask(r25, mode, cut)
        m26 = region_mask(r26, mode, cut)
        tr = [r for r, k in zip(r25, m25) if k]
        te = [r for r, k in zip(r26, m26) if k]
        if len(tr) < 120 or len(te) < 80:
            print(f"\n  --- REGION {label}: too small (train {len(tr)}, test {len(te)}) skip")
            continue
        Xtr, ytr = X25[m25], y25[m25]
        Xte, yte = X26[m26], y26[m26]
        print(f"\n  --- REGION {label}:  train {len(tr)} (NRFI {ytr.mean():.3f})   "
              f"test {len(te)} (NRFI {yte.mean():.3f})")

        report("production p_nrfi (leaky ref)", yte, np.array([r["prod"] for r in te]), 0)
        report("generalist direct LR (all 2025)", yte, C.predict_lr(gen_direct, Xte), len(r25))
        report("generalist two-stage LR (all 2025)", yte, two_stage_pred(gen_two, te), len(r25))

        for l2 in (3.0, 10.0, 30.0, 100.0):
            sp = C.fit_lr(Xtr, ytr, l2=l2)
            report(f"SPECIALIST direct LR (region only) L2={l2:g}", yte, C.predict_lr(sp, Xte), len(tr))
        sp2 = two_stage_fit(tr, l2=10.0)
        report("SPECIALIST two-stage LR (region only)", yte, two_stage_pred(sp2, te), len(tr))

        # gradient boosting specialist
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier as H
            h = H(max_depth=3, max_iter=200, learning_rate=0.05,
                  l2_regularization=1.0, random_state=0).fit(Xtr, ytr)
            report("SPECIALIST HistGB (region only)", yte, h.predict_proba(Xte)[:, 1], len(tr))
            hg = H(max_depth=3, max_iter=200, learning_rate=0.05,
                   l2_regularization=1.0, random_state=0).fit(X25, y25)
            report("generalist HistGB (all 2025)", yte, hg.predict_proba(Xte)[:, 1], len(r25))
        except Exception as e:
            print("   HistGB failed:", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())

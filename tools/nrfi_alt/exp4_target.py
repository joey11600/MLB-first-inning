#!/usr/bin/env python3
"""
exp4:
  A. the wall is not a constant -- the DK break-even RISES as you select
     harder on any NRFI-ish score.  Measure it.
  B. a genuinely DIFFERENT TARGET: instead of predicting the NRFI event,
     train directly on "did a 1u NRFI bet at this price make money".
     Requires odds, so this is 2026-only, chronological split.
  C. how good would a model have to be, simulated against the REAL
     observed price distribution rather than a fixed break-even.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def main():
    r26 = C.attach_production(C.load_2026())
    priced = [r for r in r26 if r["nrfi_odds"] is not None and r["yrfi_odds"] is not None]
    for r in priced:
        a, b = C.implied(r["nrfi_odds"]), C.implied(r["yrfi_odds"])
        r["mkt"] = a / (a + b)
        r["be"] = a
        r["pnl"] = C.payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0
    X, names = C.design(priced)
    y = np.array([r["y_nrfi"] for r in priced], float)
    be = np.array([r["be"] for r in priced])
    pm = np.array([r["prod"] for r in priced])

    print("=" * 96)
    print("  EXP4  a moving wall, a different target, and the required model quality")
    print("=" * 96)
    print(f"  n={len(priced)} real-priced 2026 games   NRFI={100*y.mean():.2f}%  "
          f"break-even={100*be.mean():.2f}%")

    print("\n  --- A. THE WALL MOVES WITH YOU ---")
    print(f"      corr(production p_nrfi, DK NRFI break-even) = "
          f"{np.corrcoef(pm, be)[0,1]:+.4f}")
    order = np.argsort(-pm)
    print(f"      {'select':>10}{'n':>6}{'hit%':>8}{'break-even%':>13}{'gap pp':>9}{'flat u':>9}")
    for q in (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00):
        k = max(20, int(len(priced) * q))
        ix = order[:k]
        h, nd = y[ix].mean(), be[ix].mean()
        u = sum(priced[i]["pnl"] for i in ix)
        print(f"      {int(q*100):>9}%{k:>6}{100*h:>8.2f}{100*nd:>13.2f}"
              f"{100*(h-nd):>+9.2f}{u:>+9.2f}")

    print("\n  --- B. DIFFERENT TARGET: 'does the NRFI bet make money at this price' ---")
    dates = sorted({r["date"] for r in priced})
    cut = dates[int(len(dates) * 0.6)]
    tr = [i for i, r in enumerate(priced) if r["date"] < cut]
    te = [i for i, r in enumerate(priced) if r["date"] >= cut]
    print(f"      chronological split at {cut}: train n={len(tr)}  test n={len(te)}")
    Xtr, Xte = X[tr], X[te]
    yte = y[te]

    # target 1: plain NRFI event (control)
    m1 = C.fit_lr(Xtr, y[tr], 10.0)
    # target 2: "bet wins" == same event, but weighted by the payout so the
    #           fit chases money rather than accuracy
    wgt = np.array([C.payout(priced[i]["nrfi_odds"]) if priced[i]["y_nrfi"] else 1.0
                    for i in tr])
    Xw = np.vstack([Xtr, Xtr])
    yw = np.r_[y[tr], y[tr]]
    # crude importance weighting via replication of the winning rows
    rep = np.repeat(np.arange(len(tr)), np.maximum(1, np.round(wgt * 2).astype(int)))
    m2 = C.fit_lr(Xtr[rep], y[tr][rep], 10.0)
    # target 3: beat-the-price residual -- y minus the market's own estimate
    mktr = np.array([priced[i]["mkt"] for i in tr])
    m3 = C.fit_lr(np.hstack([Xtr, mktr[:, None]]), y[tr], 10.0)
    mkte = np.array([priced[i]["mkt"] for i in te])

    scores = {
        "production p_nrfi": pm[te],
        "DK de-vig market": mkte,
        "LR, target = NRFI event": C.predict_lr(m1, Xte),
        "LR, target = money-weighted": C.predict_lr(m2, Xte),
        "LR + market as a feature": C.predict_lr(m3, np.hstack([Xte, mkte[:, None]])),
    }
    print(f"      {'score':<32}{'AUC':>8}   then bet top 20% / 10%:")
    for lab, s in scores.items():
        o = np.argsort(-np.asarray(s))
        out = []
        for q in (0.20, 0.10):
            k = max(20, int(len(te) * q))
            ix = o[:k]
            h = yte[ix].mean()
            nd = np.mean([priced[te[i]]["be"] for i in ix])
            u = sum(priced[te[i]]["pnl"] for i in ix)
            out.append(f"n={k} hit={100*h:.1f}% need={100*nd:.1f}% "
                       f"gap={100*(h-nd):+.1f}pp {u:+.2f}u")
        print(f"      {lab:<32}{C.auc(yte, s):>8.4f}   " + "  |  ".join(out))

    print("\n  --- C. REQUIRED MODEL QUALITY, against the REAL price distribution ---")
    print("      Give a hypothetical model true-probability knowledge with correlation")
    print("      rho to the truth, keep DK's actual observed prices attached to each")
    print("      game, bet the top 20%, and see what rho is needed to break even.")
    rng = np.random.default_rng(0)
    # true p is unknown; use the market de-vig as the best available proxy and
    # add an orthogonal component of size s to represent information DK lacks.
    mk_all = np.array([r["mkt"] for r in priced])
    for s in (0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10):
        rois, hits = [], []
        for _ in range(300):
            extra = rng.normal(0, s, len(priced))
            ptrue = np.clip(mk_all + extra, 0.02, 0.98)
            yy = (rng.random(len(priced)) < ptrue).astype(float)
            sc = ptrue                      # a PERFECT model of this truth
            o = np.argsort(-sc)[:int(0.20 * len(priced))]
            pay = np.array([C.payout(priced[i]["nrfi_odds"]) for i in o])
            pnl = np.where(yy[o] > 0, pay, -1.0)
            rois.append(pnl.mean())
            hits.append(yy[o].mean())
        print(f"        orthogonal-info sd={s:.2f}  ->  top-20% hit={100*np.mean(hits):.2f}%  "
              f"ROI={100*np.mean(rois):+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

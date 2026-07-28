"""Part 2: (a) is the MECHANISM real?  (b) does any of it become money?

(a) MECHANISM: at matched product p_nrfi, does half-balance predict NRFI?
    - matched-product bins x balance tercile, per season
    - direct residual logistic: y ~ s + |x1-x2|, coefficient with day-block CI
    - half-independence check (does the product itself mis-state the joint?)

(b) MONEY: on picks_2026 rows with a REAL captured DK NRFI price, take the
    top-k games by each combiner and settle at the real price; repeat with the
    price 10 cents worse.  Combiners are fit on 2025 only (out of sample).

ANALYSIS ONLY.
"""
from __future__ import annotations
import math, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from combiner_refute2 import (load, feats, fit_lr, pred, auc, logit, KINDS, rng)


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def worsen(o, cents=10.0):
    """Move an American price `cents` against the bettor."""
    return o - cents if o > 0 else (o - cents if o < 0 else o)


def mechanism(rows, tag):
    s = np.array([logit(r["n1"]) + logit(r["n2"]) for r in rows])
    bal = np.array([abs(logit(r["n1"]) - logit(r["n2"])) for r in rows])
    y = np.array([r["y"] for r in rows], float)
    p = np.array([r["n1"] * r["n2"] for r in rows])
    print(f"\n### MECHANISM {tag}  n={len(rows)}")
    # matched-product quintiles x balance tercile
    qs = np.percentile(p, [20, 40, 60, 80])
    pq = np.digitize(p, qs)
    print("  product-quintile |  balanced (low |dx|)   |   lopsided (high |dx|) | diff")
    diffs = []
    for q in range(5):
        m = pq == q
        if m.sum() < 30: continue
        b = bal[m]; lo = b <= np.percentile(b, 33.3); hi = b >= np.percentile(b, 66.7)
        yb, yl = y[m][lo], y[m][hi]
        pb, pl = p[m][lo].mean(), p[m][hi].mean()
        d = yb.mean() - yl.mean()
        diffs.append(d)
        print(f"    q{q} p~{p[m].mean():.3f}   bal n={lo.sum():4d} pred {pb:.3f} act {yb.mean():.3f}"
              f"   |  lop n={hi.sum():4d} pred {pl:.3f} act {yl.mean():.3f}  | {d:+.3f}")
    print(f"    mean(balanced - lopsided) across quintiles: {np.mean(diffs):+.4f}")

    # residual logistic with day-block bootstrap on the balance coefficient
    X = np.column_stack([s, bal])
    m = fit_lr(X, y)
    days = {}
    for i, r in enumerate(rows): days.setdefault(r["date"], []).append(i)
    keys = list(days); co = []
    for _ in range(2000):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([days[keys[j]] for j in pick])
        if y[idx].sum() in (0, len(idx)): continue
        co.append(fit_lr(X[idx], y[idx])["w"][1])
    co = np.array(co)
    lo, med, hi = np.percentile(co, [2.5, 50, 97.5])
    print(f"    balance coef (standardized) point {m['w'][1]:+.4f}  "
          f"day-block 95% CI [{lo:+.4f},{hi:+.4f}]  P(coef>0)={ (co>0).mean():.2f}  days={len(keys)}")

    # half independence: does P(both scoreless) equal P(T1 scoreless)*P(B1 scoreless)?
    return


def money(train, test, tag, cents=0.0):
    ytr = np.array([r["y"] for r in train], float)
    priced = [r for r in test if r["nrfi_odds"] is not None]
    y = np.array([r["y"] for r in priced], float)
    odds = np.array([worsen(r["nrfi_odds"], cents) for r in priced], float)
    need = np.array([implied(o) for o in odds])
    print(f"\n### MONEY {tag} (price moved {cents:.0f}c against)  n_priced={len(priced)} "
          f"NRFI hit {y.mean():.4f}  mean break-even {need.mean():.4f}")
    res = {}
    for k in KINDS:
        m = fit_lr(feats(train, k), ytr)
        s = pred(m, feats(priced, k))
        res[k] = s
    for k in KINDS:
        s = res[k]
        line = f"    {k:13s}"
        for topn in (50, 100, 200, 400):
            idx = np.argsort(-s)[:topn]
            u = np.array([payout(odds[i]) if y[i] else -1.0 for i in idx])
            line += f"  top{topn}: {u.sum():+7.2f}u ROI {100*u.mean():+6.1f}% hit {y[idx].mean():.3f}"
        print(line)
    # day-block CI on the best-looking cell for the strongest alternative
    return res, priced, y, odds


if __name__ == "__main__":
    b25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv",
               "home", "fi_park_nrfi_rate", "fi_total_runs", "fi_away_runs", "fi_home_runs")
    p26 = load("data/picks_2026.csv", "home_team", None,
               "fi_total_runs", "fi_away_runs", "fi_home_runs")
    mechanism(b25, "2025")
    mechanism(p26, "2026")
    for c in (0.0, 10.0):
        money(b25, p26, "train2025 -> bet 2026", c)

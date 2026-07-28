#!/usr/bin/env python3
"""P02 -- does the BOOK'S OWN PRICE, used as a feature, improve outcome
prediction?

If DraftKings knows things the model does not, adding logit(book prob)
should lift out-of-sample AUC / log-loss on the NRFI outcome.  That number
is interesting either way: it measures the size of the information gap.

Splits (all strictly forward in time, priced 2026 universe only):
    A) train <= 2026-06-30 (n~807)  -> test 2026-07 (n~321)
    B) train <= 2026-05-31          -> test 2026-06+07
    C) expanding-window walk-forward by week
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

sys.path.insert(0, str(Path(__file__).resolve().parent))
import price_common as pc

d = pc.load()
d["l_model"] = pc.logit(d["p_model"])
d["l_book"] = pc.logit(d["book_nrfi"])

SPECS = {
    "model prob only":            ["l_model"],
    "book prob only":             ["l_book"],
    "model + book":               ["l_model", "l_book"],
    "raw feats":                  pc.FEATS,
    "raw feats + model":          pc.FEATS + ["l_model"],
    "raw feats + model + book":   pc.FEATS + ["l_model", "l_book"],
}


def fit_predict(tr, te, cols, C=0.05):
    m = make_pipeline(StandardScaler(),
                      LogisticRegression(C=C, max_iter=4000))
    m.fit(tr[cols].values, tr["y_nrfi"].values)
    return m.predict_proba(te[cols].values)[:, 1], m


def logloss(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


print(f"universe n={len(d)}   NRFI base rate={d.y_nrfi.mean():.4f}")
print()
print("=== unconditional discrimination on the whole priced universe ===")
print(f"  production p_nrfi        AUC={pc.auc(d.y_nrfi, d.p_model):.4f}")
print(f"  DK de-vigged book prob   AUC={pc.auc(d.y_nrfi, d.book_nrfi):.4f}")
print(f"  production log-loss={logloss(d.y_nrfi, d.p_model):.4f}   "
      f"book log-loss={logloss(d.y_nrfi, d.book_nrfi):.4f}   "
      f"base-rate log-loss={logloss(d.y_nrfi, np.full(len(d), d.y_nrfi.mean())):.4f}")
print()


def run_split(cut, label):
    tr = d[d.dt <= cut]
    te = d[d.dt > cut]
    print(f"=== SPLIT {label}: train n={len(tr)} (<= {cut.date()}), test n={len(te)} ===")
    print(f"    test NRFI rate={te.y_nrfi.mean():.4f}")
    print(f"{'feature set':>26} {'test AUC':>9} {'test LL':>9} {'train AUC':>10}")
    res = {}
    for name, cols in SPECS.items():
        p, m = fit_predict(tr, te, cols)
        ptr = m.predict_proba(tr[cols].values)[:, 1]
        a = pc.auc(te.y_nrfi, p)
        res[name] = (a, p)
        print(f"{name:>26} {a:>9.4f} {logloss(te.y_nrfi, p):>9.4f} "
              f"{pc.auc(tr.y_nrfi, ptr):>10.4f}")
    # bootstrap the AUC delta from adding the book
    te2 = te.copy()
    te2["_a"] = res["raw feats + model"][1]
    te2["_b"] = res["raw feats + model + book"][1]

    def dlt(x):
        return pc.auc(x.y_nrfi, x._b) - pc.auc(x.y_nrfi, x._a)

    lo, hi = pc.day_bootstrap(te2, dlt)
    print(f"    AUC delta from ADDING BOOK PRICE = {dlt(te2):+.4f}  "
          f"day-block 95% CI [{lo:+.4f}, {hi:+.4f}]")

    te2["_m"] = res["model prob only"][1]
    te2["_k"] = res["book prob only"][1]

    def dlt2(x):
        return pc.auc(x.y_nrfi, x._k) - pc.auc(x.y_nrfi, x._m)

    lo, hi = pc.day_bootstrap(te2, dlt2)
    print(f"    AUC(book alone) - AUC(model alone) = {dlt2(te2):+.4f}  "
          f"day-block 95% CI [{lo:+.4f}, {hi:+.4f}]")
    print()
    return res


run_split(pd.Timestamp("2026-06-30"), "A  Apr-Jun -> Jul")
run_split(pd.Timestamp("2026-05-31"), "B  Apr-May -> Jun+Jul")

# ---- C) expanding-window weekly walk-forward -------------------------
print("=== SPLIT C: expanding-window weekly walk-forward (min 250 train rows) ===")
weeks = sorted(d["dt"].dt.to_period("W").unique())
acc = {k: [] for k in SPECS}
rows_used = 0
for w in weeks:
    te = d[d["dt"].dt.to_period("W") == w]
    tr = d[d["dt"] < te["dt"].min()]
    if len(tr) < 250 or len(te) < 10:
        continue
    rows_used += len(te)
    for name, cols in SPECS.items():
        p, _ = fit_predict(tr, te, cols)
        acc[name].append(pd.DataFrame({"y": te.y_nrfi.values, "p": p,
                                       "date": te.date.values}))
print(f"    pooled walk-forward test rows n={rows_used}")
print(f"{'feature set':>26} {'pooled AUC':>11} {'pooled LL':>10}")
pool = {}
for name in SPECS:
    if not acc[name]:
        continue
    z = pd.concat(acc[name], ignore_index=True)
    pool[name] = z
    print(f"{name:>26} {pc.auc(z.y, z.p):>11.4f} {logloss(z.y, z.p):>10.4f}")

a = pool["raw feats + model"].rename(columns={"p": "_a"})
b = pool["raw feats + model + book"]["p"].values
a["_b"] = b


def dlt(x):
    return pc.auc(x.y, x._b) - pc.auc(x.y, x._a)


lo, hi = pc.day_bootstrap(a, dlt)
print(f"    AUC delta from ADDING BOOK PRICE = {dlt(a):+.4f}  "
      f"day-block 95% CI [{lo:+.4f}, {hi:+.4f}]")

m = pool["model prob only"].rename(columns={"p": "_a"})
m["_b"] = pool["book prob only"]["p"].values
lo, hi = pc.day_bootstrap(m, dlt)
print(f"    AUC(book alone) - AUC(model alone) = {dlt(m):+.4f}  "
      f"day-block 95% CI [{lo:+.4f}, {hi:+.4f}]")

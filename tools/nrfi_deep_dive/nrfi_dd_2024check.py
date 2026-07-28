#!/usr/bin/env python3
"""tools/nrfi_dd_2024check.py -- two integrity checks. Read-only.

A) Is 2024's zero AUC a BROKEN-DATA artifact or a genuine negative?
   Fit a fresh logistic regression on each season's own features with 5-fold
   CV. If 2024 can't predict 2024 even in-season, its features are dead and
   2024 is not a valid test bed. If it CAN, the current model simply does
   not transfer -- which is a real negative result.

B) 2026bt vs picks_2026 are the SAME GAMES over 2026-04-01..2026-05-26.
   Do their lambda values agree? Retrospective "truepit" features are not
   necessarily what the live predictor saw.
"""
from __future__ import annotations
import csv, math, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"


def auc(s, y):
    o = np.argsort(s)
    r = np.empty(len(s), float)
    ss = s[o]
    ranks = np.arange(1, len(s) + 1, dtype=float)
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        r[o[i:j + 1]] = ranks[i:j + 1].mean()
        i = j + 1
    n1, n0 = y.sum(), (1 - y).sum()
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def build(paths, outcol, homecol):
    fi_park = rc.load_fi_park()
    X, Y, meta = [], [], []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = (r.get(outcol) or "").upper()
                if a not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                X.append(tv + bv)
                Y.append(1 if a == "NRFI" else 0)
                meta.append((r.get("date", ""), r.get("away", r.get("away_team", "")),
                             r.get(homecol, ""), r.get("game_pk", "")))
    return np.asarray(X, float), np.asarray(Y, int), meta


def cv_auc(X, y, folds=5, seed=3):
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    parts = np.array_split(idx, folds)
    preds = np.zeros(len(y))
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Xs = (X - mu) / sd
    for k in range(folds):
        te = parts[k]
        tr = np.concatenate([parts[j] for j in range(folds) if j != k])
        w = np.zeros(Xs.shape[1])
        b = 0.0
        lr, lam = 0.5, 1e-3
        Xtr, ytr = Xs[tr], y[tr].astype(float)
        for _ in range(600):
            z = Xtr @ w + b
            p = 1 / (1 + np.exp(-z))
            g = Xtr.T @ (p - ytr) / len(ytr) + lam * w
            gb = float((p - ytr).mean())
            w -= lr * g
            b -= lr * gb
        preds[te] = Xs[te] @ w + b
    return auc(preds, y)


def main():
    sets = {
        "2024bt": ([BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"], "actual_side", "home"),
        "2025bt": ([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home"),
        "2026bt": ([BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
                    BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"], "actual_side", "home"),
        "2026picks": ([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team"),
    }
    print("=" * 88)
    print("  A) CAN A MODEL FIT ON SEASON X PREDICT SEASON X? (5-fold CV, fresh LR on the")
    print("     same 38 Phase-E.3 inputs).  AUC ~0.50 => that season's features carry no")
    print("     first-inning signal at all.")
    print("=" * 88)
    print(f"  {'season':<12}{'n':>7}{'base':>8}{'in-season CV AUC':>20}{'prod-model AUC':>18}")
    t1m, b1m = rc.load_lr_models()
    store = {}
    for name, (paths, oc, hc) in sets.items():
        X, y, meta = build(paths, oc, hc)
        cva = cv_auc(X, y)
        raw = np.asarray(rc.lr_predict_two_stage(t1m, b1m, X[:, :19], X[:, 19:]), float)
        pa = auc(raw, y)
        print(f"  {name:<12}{len(y):>7}{y.mean():>8.3f}{cva:>20.4f}{pa:>18.4f}")
        store[name] = (meta, raw, y)

    print("\n" + "=" * 88)
    print("  B) SAME GAMES, TWO FEATURE SNAPSHOTS: 2026bt vs picks_2026 (Apr 1 - May 26)")
    print("=" * 88)
    bt_meta, bt_raw, bt_y = store["2026bt"]
    pk_meta, pk_raw, pk_y = store["2026picks"]
    bt = {(m[0], m[2]): (r, yy) for m, r, yy in zip(bt_meta, bt_raw, bt_y)}
    pairs = []
    for m, r, yy in zip(pk_meta, pk_raw, pk_y):
        k = (m[0], m[2])
        if k in bt:
            pairs.append((bt[k][0], r, bt[k][1], yy))
    if not pairs:
        print("  no matching (date, home) keys")
        return 0
    a = np.array([p[0] for p in pairs])
    b = np.array([p[1] for p in pairs])
    la, lb = -np.log(a), -np.log(b)
    print(f"  matched games: {len(pairs)}")
    print(f"  outcome label agreement: {100*np.mean([p[2]==p[3] for p in pairs]):.1f}%")
    print(f"  raw_p  corr = {np.corrcoef(a,b)[0,1]:.3f}   mean |diff| = {np.abs(a-b).mean():.4f}")
    print(f"  lambda corr = {np.corrcoef(la,lb)[0,1]:.3f}   mean |diff| = {np.abs(la-lb).mean():.4f}")
    for c in (0.48, 0.52, 0.56, 0.60):
        sa, sb = la <= c, lb <= c
        both = (sa & sb).sum()
        print(f"  lambda<={c:.2f}: backtest picks {sa.sum():>4}, live picks {sb.sum():>4}, "
              f"overlap {both:>4}  (Jaccard {both/max(1,(sa|sb).sum()):.2f})")
        if sa.sum() >= 10:
            print(f"      NRFI rate on backtest-selected: "
                  f"{100*np.mean([p[2] for p,f in zip(pairs,sa) if f]):.1f}%")
        if sb.sum() >= 10:
            print(f"      NRFI rate on live-selected:     "
                  f"{100*np.mean([p[3] for p,f in zip(pairs,sb) if f]):.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

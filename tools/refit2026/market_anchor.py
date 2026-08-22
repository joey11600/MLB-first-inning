#!/usr/bin/env python3
"""
Is there an edge to find by ANCHORING on the market instead of competing
with it?

THE PREMISE.  Measured 2026-08-20/21 on 1435 priced games: the de-vigged
market ranks first-inning scoring at AUC 0.548; our model ranks it at 0.510
(90% CI 0.485-0.535, i.e. indistinguishable from a coin flip).  The market
knows more than the model.  A model that STARTS from the market price and
adds a little of its own information only has to find a small increment to
be profitable, whereas one that ignores the price has to rediscover
everything the market already knows before it can add anything.

The shipped pipeline never looks at the price except to compute an edge
AFTER the fact.  This asks whether that is leaving money on the table.

THREE QUESTIONS
  1. Does our model add ANY information on top of the de-vigged market?
     (out-of-sample logloss of  y ~ logit(mkt) + logit(model)  vs  y ~ logit(mkt))
  2. Our disagreement with the market has been INVERTED since June -- games
     we call likelier hit 40.7% in August, games we call less likely hit
     67.5%.  Is that sign stable enough to trade, or is it noise?
  3. What would any of it be worth after the vig?

CONSTRAINT, STATED UP FRONT.  First-inning odds exist only from 2026-04-29.
There is no 2024/2025 price data, so the three-season protocol is impossible
here.  Everything below is WALK-FORWARD WITHIN 2026: each test day is scored
by a model fit only on strictly earlier games.  That is a weaker guarantee
than three splits and the conclusions are held to a correspondingly higher
bar.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sig(z):
    return 1.0 / (1.0 + np.exp(-z))


def fit_lr(X, y, l2=1e-3, iters=200):
    X = np.c_[np.ones(len(X)), np.asarray(X, dtype=float)]
    w = np.zeros(X.shape[1])
    R = np.eye(X.shape[1]) * l2
    R[0, 0] = 0.0
    for _ in range(iters):
        p = sig(X @ w)
        g = X.T @ (y - p) / len(y) - R @ w
        H = (X * (p * (1 - p))[:, None]).T @ X / len(y) + R + 1e-9 * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        w += step
        if np.max(np.abs(step)) < 1e-10:
            break
    return w


def apply_lr(w, X):
    return sig(np.c_[np.ones(len(X)), np.asarray(X, dtype=float)] @ w)


def auc(y, p):
    y = np.asarray(y); p = np.asarray(p)
    n1 = int(y.sum()); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(p).rank().values
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def logloss(y, p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=3000)
    ap.add_argument("--min-train", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    d = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d.fi_total_runs.notna()].copy()
    d["y"] = (d.fi_total_runs > 0).astype(int)
    d = d.dropna(subset=["implied_yrfi_prob", "implied_nrfi_prob", "yrfi_prob"])
    d = d.sort_values("date").reset_index(drop=True)

    vig = d.implied_yrfi_prob + d.implied_nrfi_prob
    d["mkt"] = d.implied_yrfi_prob / vig            # de-vigged market P(YRFI)
    d["lg_mkt"] = logit(d.mkt)
    d["lg_mod"] = logit(d.yrfi_prob)
    d["resid"] = d.lg_mod - d.lg_mkt                # our disagreement, log-odds

    print(f"priced 2026 games: n={len(d)}  ({d.date.min().date()} .. {d.date.max().date()})")
    print(f"average book vig (overround): {(vig.mean()-1)*100:.2f}%  "
          f"-> you must beat the market by ~{(vig.mean()-1)*100/2:.2f}pp of probability "
          f"just to break even\n")

    print("=" * 92)
    print("BENCHMARK on the whole priced sample")
    for lab, col in [("market (de-vigged)", "mkt"), ("our model", "yrfi_prob")]:
        print(f"  {lab:22s} AUC={auc(d.y, d[col]):.4f}   logloss={logloss(d.y, d[col]):.5f}")

    # ---------- walk-forward ----------
    days = np.sort(d.date.unique())
    rows = []
    for D in days:
        tr = d[d.date < D]
        te = d[d.date == D]
        if len(tr) < args.min_train or not len(te):
            continue
        specs = {
            "market only":        ["lg_mkt"],
            "model only":         ["lg_mod"],
            "market + model":     ["lg_mkt", "lg_mod"],
            "market + residual":  ["lg_mkt", "resid"],
        }
        out = {"date": D, "n": len(te), "y": te.y.values, "mkt_raw": te.mkt.values}
        for name, cols in specs.items():
            w = fit_lr(tr[cols].values, tr.y.values)
            out[name] = apply_lr(w, te[cols].values)
            if name == "market + model":
                out["coef_mkt"], out["coef_mod"] = w[1], w[2]
            if name == "market + residual":
                out["coef_resid"] = w[2]
        rows.append(out)

    y = np.concatenate([r["y"] for r in rows])
    print(f"\n  walk-forward scored games: {len(y)}  "
          f"(first {args.min_train} used only for the initial fit)")
    print("\n" + "=" * 92)
    print("WALK-FORWARD: does our model add anything on top of the market?")
    print(f"  {'specification':<22} {'AUC':>8} {'logloss':>10} {'vs market-only':>26}")
    preds = {}
    for name in ["market only", "model only", "market + model", "market + residual"]:
        p = np.concatenate([r[name] for r in rows])
        preds[name] = p
        if name == "market only":
            cmp = "(baseline)"
        else:
            b = preds["market only"]
            dl = np.array([logloss(y[i], b[i]) - logloss(y[i], p[i])
                           for i in (rng.integers(0, len(y), len(y)) for _ in range(args.boot))])
            cmp = (f"{logloss(y, b)-logloss(y, p):+.5f} "
                   f"[{np.percentile(dl,5):+.5f},{np.percentile(dl,95):+.5f}] P={(dl>0).mean():.0%}")
        print(f"  {name:<22} {auc(y, p):>8.4f} {logloss(y, p):>10.5f} {cmp:>26}")

    print("\n" + "=" * 92)
    print("THE COEFFICIENT ON OUR MODEL, refit every day -- is it even positive?")
    cm = np.array([r["coef_mod"] for r in rows])
    cr = np.array([r["coef_resid"] for r in rows])
    print(f"  coef on logit(model) beside the market: mean {cm.mean():+.4f}  "
          f"median {np.median(cm):+.4f}  share of days > 0: {(cm>0).mean():.0%}")
    print(f"  coef on our residual vs the market:     mean {cr.mean():+.4f}  "
          f"median {np.median(cr):+.4f}  share of days > 0: {(cr>0).mean():.0%}")
    print("  (a NEGATIVE coefficient means our disagreement points the WRONG way and")
    print("   the fit is fading us; a coefficient near zero means we add nothing)")

    print("\n" + "=" * 92)
    print("IS THE INVERSION STABLE? residual bucket hit rate, by month")
    d2 = d.copy()
    d2["b"] = pd.cut(d2.resid, [-9, -0.10, -0.02, 0.02, 0.10, 9],
                     labels=["we MUCH lower", "we lower", "agree", "we higher", "we MUCH higher"])
    t = d2.pivot_table(index=d2.date.dt.to_period("M"), columns="b",
                       values="y", aggfunc=["size", "mean"], observed=True)
    print(t.round(3).to_string())
    print("\n  if the sign flips month to month it is noise, not a tradable inversion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

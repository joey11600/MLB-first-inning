#!/usr/bin/env python3
"""REFUTATION TEST: train on profitability instead of on the NRFI outcome.

Three price-aware targets, weekly walk-forward, real DK prices only.

  T_UNITS  Ridge regression on realised units of a 1u NRFI bet
           (y = +pay_n on NRFI, -1 on YRFI).  Payout-weighted by
           construction: a +130 win is worth more than a -150 win.
  T_OFFSET L2 logistic on the NRFI outcome with an OFFSET of
           logit(de-vigged market NRFI prob).  The fit can only learn
           a CORRECTION to the book -- coefficients are zero iff the
           market is already right.
  T_WEIGHT L2 logistic on the NRFI outcome with sample weights equal to
           the payout at stake (price-aware likelihood).

Every model consumes the market price as an input feature.

Baselines: bet-everything-NRFI, and the incumbent rule (EV computed from
the shipped calibrated nrfi_prob vs the same price).

Usage: python tools/nrfi_alt/profit_target_wf.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = Path(__file__).parent
RNG = np.random.default_rng(20260728)

# ------------------------------------------------------------------ helpers

def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X, y, w=None, offset=None, lam=1.0):
    """L2-penalised logistic regression with optional offset + weights.
    X already standardised, intercept added internally."""
    n, d = X.shape
    Xi = np.hstack([np.ones((n, 1)), X])
    w = np.ones(n) if w is None else np.asarray(w, float)
    off = np.zeros(n) if offset is None else np.asarray(offset, float)
    pen = np.ones(d + 1); pen[0] = 0.0

    def obj(b):
        z = Xi @ b + off
        p = sigmoid(z)
        ll = -np.sum(w * (y * np.log(np.clip(p, 1e-12, 1)) +
                          (1 - y) * np.log(np.clip(1 - p, 1e-12, 1))))
        g = Xi.T @ (w * (p - y))
        ll += 0.5 * lam * np.sum(pen * b * b)
        g += lam * pen * b
        return ll, g

    r = minimize(obj, np.zeros(d + 1), jac=True, method="L-BFGS-B",
                 options={"maxiter": 400})
    return r.x


def pred_logistic(b, X, offset=None):
    n = X.shape[0]
    Xi = np.hstack([np.ones((n, 1)), X])
    off = np.zeros(n) if offset is None else np.asarray(offset, float)
    return sigmoid(Xi @ b + off)


def fit_ridge(X, y, lam=1.0):
    n, d = X.shape
    Xi = np.hstack([np.ones((n, 1)), X])
    P = np.eye(d + 1) * lam; P[0, 0] = 0.0
    return np.linalg.solve(Xi.T @ Xi + P, Xi.T @ y)


def pred_ridge(b, X):
    return np.hstack([np.ones((X.shape[0], 1)), X]) @ b


# ------------------------------------------------------------------ data

def load():
    meta = pd.read_csv(HERE / "ds_2026_meta.csv", parse_dates=["date"])
    z = np.load(HERE / "ds_2026.npz")
    Xm = np.hstack([z["Xt"], z["Xb"]])
    # market + model block
    extra = np.column_stack([
        logit(meta.dev_n.values),        # de-vigged market NRFI logit
        logit(meta.p_raw.values),        # raw model NRFI logit
        logit(meta.p_raw.values) - logit(meta.dev_n.values),   # disagreement
        meta.pay_n.values,               # price level (payout on NRFI)
        meta.vig.values,
        meta.lam.values,
        meta.park.values,
    ])
    X = np.hstack([Xm, extra])
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return meta, X


# ------------------------------------------------------------------ walk-forward

def walk_forward(meta, X, lam_reg=5.0, burn_weeks=4, min_train=250):
    meta = meta.copy()
    meta["week"] = meta["date"].dt.to_period("W").astype(str)
    weeks = sorted(meta["week"].unique())
    units_n = np.where(meta.y_nrfi.values == 1, meta.pay_n.values, -1.0)

    out = {k: np.full(len(meta), np.nan) for k in ("T_UNITS", "T_OFFSET", "T_WEIGHT")}
    tested = np.zeros(len(meta), bool)

    for wi, wk in enumerate(weeks):
        te = (meta["week"] == wk).values
        tr = (meta["week"] < wk).values
        if wi < burn_weeks or tr.sum() < min_train or te.sum() == 0:
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0)
        sd[sd < 1e-9] = 1.0
        Xtr, Xte = (X[tr] - mu) / sd, (X[te] - mu) / sd
        y = meta.y_nrfi.values

        # --- T_UNITS : regress realised units, predict EV directly
        b = fit_ridge(Xtr, units_n[tr], lam=lam_reg)
        out["T_UNITS"][te] = pred_ridge(b, Xte)

        # --- T_OFFSET : logistic correction on top of the book
        off_tr = logit(meta.dev_n.values[tr])
        off_te = logit(meta.dev_n.values[te])
        b = fit_logistic(Xtr, y[tr], offset=off_tr, lam=lam_reg)
        p = pred_logistic(b, Xte, offset=off_te)
        out["T_OFFSET"][te] = p * meta.pay_n.values[te] - (1 - p)

        # --- T_WEIGHT : payout-weighted likelihood
        w = np.where(y[tr] == 1, meta.pay_n.values[tr], 1.0)
        b = fit_logistic(Xtr, y[tr], w=w, lam=lam_reg)
        p = pred_logistic(b, Xte)
        out["T_WEIGHT"][te] = p * meta.pay_n.values[te] - (1 - p)

        tested[te] = True

    meta["units_n"] = units_n
    for k, v in out.items():
        meta[k] = v
    # incumbent EV rule, same price
    meta["EV_LIVE"] = meta.p_live * meta.pay_n - (1 - meta.p_live)
    meta["tested"] = tested
    return meta[meta.tested].reset_index(drop=True)


# ------------------------------------------------------------------ scoring

def day_block_boot(df, mask, B=4000):
    """Block bootstrap over calendar days -> ROI CI (%) for the selected bets."""
    sel = df[mask]
    if len(sel) == 0:
        return (np.nan, np.nan)
    days = sel["date"].values
    uniq = np.unique(days)
    by = {d: sel.units_n.values[days == d] for d in uniq}
    rois = np.empty(B)
    for i in range(B):
        pick = RNG.choice(len(uniq), size=len(uniq), replace=True)
        u = np.concatenate([by[uniq[j]] for j in pick])
        rois[i] = 100 * u.mean() if len(u) else np.nan
    return tuple(np.nanpercentile(rois, [2.5, 97.5]))


def haircut_units(df, cents=10):
    """Worsen the NRFI price by `cents` of american odds, recompute units."""
    o = df.nrfi_odds.values.astype(float)
    o2 = np.where(o > 0, o - cents, o - cents)   # +130->+120, -150->-160
    # crossing zero: american odds have no [-100,100) region
    o2 = np.where((o2 < 100) & (o2 > 0), -100 - (100 - o2), o2)
    pay = np.where(o2 > 0, o2 / 100.0, 100.0 / np.abs(o2))
    return np.where(df.y_nrfi.values == 1, pay, -1.0)


def report(df, cols, depths, label=""):
    rows = []
    for c in cols:
        s = df[c].values
        ordr = np.argsort(-s)
        for d in depths:
            if isinstance(d, str) and d == "EV>0":
                mask = s > 0
                dl = "EV>0"
            else:
                k = max(1, int(round(d * len(df))))
                mask = np.zeros(len(df), bool); mask[ordr[:k]] = True
                dl = f"top{int(d*100)}%"
            n = int(mask.sum())
            if n < 5:
                continue
            u = df.units_n.values[mask]
            roi = 100 * u.mean()
            lo, hi = day_block_boot(df, mask)
            uh = haircut_units(df[mask])
            rows.append(dict(target=c, depth=dl, n=n,
                             hit=100 * df.y_nrfi.values[mask].mean(),
                             units=u.sum(), roi=roi, lo=lo, hi=hi,
                             roi_10c=100 * uh.mean()))
    r = pd.DataFrame(rows)
    print(f"\n=== {label}  (n_pool={len(df)}) ===")
    print(r.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))
    return r


def main():
    meta, X = load()
    df = walk_forward(meta, X)
    print("walk-forward OOS pool n =", len(df),
          df.date.min().date(), "->", df.date.max().date())
    u = df.units_n.values
    print("bet-EVERYTHING-NRFI on pool: n=%d units %.2f ROI %.2f%%  hit %.2f%%"
          % (len(u), u.sum(), 100 * u.mean(), 100 * df.y_nrfi.mean()))
    lo, hi = day_block_boot(df, np.ones(len(df), bool))
    print("   day-block CI [%.2f, %.2f]" % (lo, hi))

    depths = [0.05, 0.10, 0.20, 0.30, 0.50, "EV>0"]
    cols = ["T_UNITS", "T_OFFSET", "T_WEIGHT", "EV_LIVE"]
    full = report(df, cols, depths, "POOLED WALK-FORWARD, real DK prices")
    full.to_csv(HERE / "res_pooled.csv", index=False)
    print("\ncells searched (targets x depths):", len(cols) * len(depths))

    # ---- time split: searched on May-Jun, confirm on July
    cut = pd.Timestamp("2026-07-01")
    a, b = df[df.date < cut], df[df.date >= cut]
    ra = report(a.reset_index(drop=True), cols, depths, "SPLIT A  May-Jun (search)")
    rb = report(b.reset_index(drop=True), cols, depths, "SPLIT B  July (held out)")
    ra.to_csv(HERE / "res_mayjun.csv", index=False)
    rb.to_csv(HERE / "res_july.csv", index=False)

    # ---- discrimination check: is there ANY AUC gain over the market?
    from sklearn.metrics import roc_auc_score
    y = df.y_nrfi.values
    print("\n=== NRFI-outcome AUC on the same OOS pool ===")
    for nm, s in [("market de-vig", df.dev_n.values),
                  ("model p_live", df.p_live.values),
                  ("model p_raw", df.p_raw.values),
                  ("T_OFFSET p", df.T_OFFSET.values),
                  ("T_WEIGHT p", df.T_WEIGHT.values),
                  ("T_UNITS score", df.T_UNITS.values)]:
        print(f"  {nm:16s} AUC {roc_auc_score(y, s):.4f}")


if __name__ == "__main__":
    main()

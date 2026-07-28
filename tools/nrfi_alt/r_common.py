#!/usr/bin/env python3
"""Shared model zoo for the 'retarget to profit / price-as-feature' refutation.

ANALYSIS ONLY.  Nothing here is imported by production code.

Every model returns a SCORE on the test rows.  Higher score = "bet NRFI
here".  Selection is always: rank by score, take the top k%, place a flat
1u NRFI bet at the REAL captured DraftKings price, sum units.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from price_common import FEATS, logit  # noqa: E402

from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler


def _mat(df, use_book=False, use_pmodel=False):
    cols = list(FEATS)
    X = df[cols].to_numpy(float)
    extra = []
    if use_pmodel:
        extra.append(logit(df["p_model"].to_numpy(float)))
    if use_book:
        extra.append(logit(df["book_nrfi"].to_numpy(float)))
        extra.append(df["pay_nrfi"].to_numpy(float))
    if extra:
        X = np.column_stack([X] + extra)
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


def _fit_scale(Xtr, Xte):
    sc = StandardScaler().fit(Xtr)
    return sc.transform(Xtr), sc.transform(Xte)


# --------------------------------------------------------------------------
# Each builder: (train_df, test_df) -> score array on test rows
# --------------------------------------------------------------------------

def m_shipped_p(tr, te):
    """Baseline A: the production probability itself."""
    return te["p_model"].to_numpy(float)


def m_shipped_ev(tr, te):
    """Baseline B: EV computed from the production probability + real price."""
    return te["p_model"].to_numpy(float) * (1 + te["pay_nrfi"].to_numpy(float)) - 1


def m_book_ev(tr, te):
    """Baseline C: EV computed from the DE-VIGGED BOOK probability.
    If a 'price as a feature' model beats nothing else, it should at
    least beat this -- because this IS the price, used optimally."""
    return te["book_nrfi"].to_numpy(float) * (1 + te["pay_nrfi"].to_numpy(float)) - 1


def _logreg(tr, te, y, w=None, use_book=False, use_pmodel=False, C=0.1):
    Xtr = _mat(tr, use_book, use_pmodel)
    Xte = _mat(te, use_book, use_pmodel)
    Xtr, Xte = _fit_scale(Xtr, Xte)
    m = LogisticRegression(C=C, max_iter=4000)
    m.fit(Xtr, y, sample_weight=w)
    return m.predict_proba(Xte)[:, 1]


def m_lr_outcome(tr, te):
    """Refit the CURRENT target (did NRFI happen) on the same rows."""
    p = _logreg(tr, te, tr["y_nrfi"].to_numpy(int))
    return p * (1 + te["pay_nrfi"].to_numpy(float)) - 1


def m_lr_outcome_book(tr, te):
    """Outcome target + de-vigged market price as a feature."""
    p = _logreg(tr, te, tr["y_nrfi"].to_numpy(int), use_book=True)
    return p * (1 + te["pay_nrfi"].to_numpy(float)) - 1


def m_lr_weighted(tr, te):
    """Payout-WEIGHTED classification: same label, but a win at +130 counts
    more than a win at -140.  ('care about cheap NRFI games more.')"""
    w = np.where(tr["y_nrfi"].to_numpy(int) == 1,
                 tr["pay_nrfi"].to_numpy(float), 1.0)
    p = _logreg(tr, te, tr["y_nrfi"].to_numpy(int), w=w)
    return p * (1 + te["pay_nrfi"].to_numpy(float)) - 1


def m_ridge_profit(tr, te):
    """THE PROPOSAL, linear form: regress the realised 1u NRFI profit."""
    Xtr = _mat(tr); Xte = _mat(te)
    Xtr, Xte = _fit_scale(Xtr, Xte)
    m = Ridge(alpha=50.0).fit(Xtr, tr["u_nrfi"].to_numpy(float))
    return m.predict(Xte)


def m_ridge_profit_book(tr, te):
    """THE PROPOSAL, linear form + the price as a feature."""
    Xtr = _mat(tr, use_book=True); Xte = _mat(te, use_book=True)
    Xtr, Xte = _fit_scale(Xtr, Xte)
    m = Ridge(alpha=50.0).fit(Xtr, tr["u_nrfi"].to_numpy(float))
    return m.predict(Xte)


def m_gbm_profit(tr, te):
    """THE PROPOSAL, nonlinear form: gradient boosting on realised profit."""
    Xtr = _mat(tr); Xte = _mat(te)
    m = HistGradientBoostingRegressor(
        max_depth=3, max_iter=200, learning_rate=0.05,
        min_samples_leaf=40, l2_regularization=1.0, random_state=0)
    m.fit(Xtr, tr["u_nrfi"].to_numpy(float))
    return m.predict(Xte)


def m_gbm_profit_book(tr, te):
    Xtr = _mat(tr, use_book=True); Xte = _mat(te, use_book=True)
    m = HistGradientBoostingRegressor(
        max_depth=3, max_iter=200, learning_rate=0.05,
        min_samples_leaf=40, l2_regularization=1.0, random_state=0)
    m.fit(Xtr, tr["u_nrfi"].to_numpy(float))
    return m.predict(Xte)


def m_ridge_profit_pmodel(tr, te):
    """Profit target with the shipped probability handed to it as a feature
    (so it starts from what production already knows)."""
    Xtr = _mat(tr, use_book=True, use_pmodel=True)
    Xte = _mat(te, use_book=True, use_pmodel=True)
    Xtr, Xte = _fit_scale(Xtr, Xte)
    m = Ridge(alpha=50.0).fit(Xtr, tr["u_nrfi"].to_numpy(float))
    return m.predict(Xte)


MODELS = {
    "A_shipped_p":        m_shipped_p,
    "B_shipped_EV":       m_shipped_ev,
    "C_bookEV":           m_book_ev,
    "D_refit_outcome":    m_lr_outcome,
    "E_outcome+price":    m_lr_outcome_book,
    "F_payout_weighted":  m_lr_weighted,
    "G_ridge_PROFIT":     m_ridge_profit,
    "H_ridge_PROFIT+price": m_ridge_profit_book,
    "I_gbm_PROFIT":       m_gbm_profit,
    "J_gbm_PROFIT+price": m_gbm_profit_book,
    "K_PROFIT+price+pmodel": m_ridge_profit_pmodel,
}

DEPTHS = (0.05, 0.10, 0.20, 0.30, 0.50)


def topk_units(te, score, k):
    """Flat 1u NRFI on the top-k fraction by score.  Returns (n, units, roi,
    hitrate, sub-dataframe)."""
    n = max(1, int(round(k * len(te))))
    order = np.argsort(-np.asarray(score, float), kind="mergesort")[:n]
    sub = te.iloc[order]
    u = sub["u_nrfi"].to_numpy(float)
    return len(u), u.sum(), 100 * u.mean(), 100 * sub["y_nrfi"].mean(), sub


def day_boot_roi(sub, B=4000, seed=11):
    """Day-block bootstrap CI on ROI% for a set of 1u NRFI bets."""
    rng = np.random.default_rng(seed)
    g = sub.groupby("date")["u_nrfi"]
    sums = g.sum().to_numpy(float)
    cnts = g.count().to_numpy(float)
    n = len(sums)
    if n < 2:
        return float("nan"), float("nan")
    idx = rng.integers(0, n, size=(B, n))
    r = 100.0 * sums[idx].sum(1) / np.maximum(cnts[idx].sum(1), 1)
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def shade_payout(pay, d_imp=0.021):
    """Make every price WORSE by ~10 American cents.

    Implemented in implied-probability space because that is monotone and
    well-defined across the +100/-100 boundary:  -110 -> -120 raises the
    break-even from .5238 to .5455 (+.0217);  -120 -> -130 raises it from
    .5455 to .5652 (+.0197).  So 10 cents ~= +0.021 implied.  Returns the
    new net payout per 1u staked.
    """
    pay = np.asarray(pay, float)
    imp = 1.0 / (1.0 + pay)
    imp = np.clip(imp + d_imp, 1e-6, 0.999)
    return (1.0 - imp) / imp

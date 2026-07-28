#!/usr/bin/env python3
"""Shared loader for the "predict PROFITABILITY, not outcome" study.

ANALYSIS ONLY.  Nothing here is imported by production code.

Universe: 2026 picks rows that have BOTH a real captured DraftKings price
AND a graded first inning.  n ~= 1,128.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PICKS = ROOT / "data" / "picks_2026.csv"

# Model-visible features that are ~100% populated on the priced universe.
FEATS = [
    "lambda_lr_total", "park_factor",
    "away_era", "home_era", "away_whip", "home_whip",
    "away_fip", "home_fip", "away_bb9", "home_bb9",
    "away_hr9", "home_hr9", "away_k9", "home_k9",
    "away_obp", "home_obp", "away_slg", "home_slg",
    "away_rpg", "home_rpg", "wx_is_dome",
    "home_xera", "away_xera",
    "home_whiff_pct_rank", "away_whiff_pct_rank",
    "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
    "home_avg_ip_per_start", "away_avg_ip_per_start",
    "home_top3c_obp", "away_top3c_obp",
    "home_top3c_slg", "away_top3c_slg",
    "home_top3c_iso", "away_top3c_iso",
    "away_top3_ops_vs_oppHand", "home_top3_ops_vs_oppHand",
    "home_plate_ump_nrfi_rate",
    "home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi",
    "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
]


def payout(o: float) -> float:
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o: float) -> float:
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def logit(p, eps=1e-6):
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def _game_dt_utc(row):
    s = str(row["game_time_et"])
    m = re.match(r"(\d+):(\d+)\s*(AM|PM)", s)
    if not m:
        return pd.NaT
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    try:
        return pd.Timestamp(f"{row['date']} {h:02d}:{int(m.group(2)):02d}",
                            tz="US/Eastern").tz_convert("UTC")
    except Exception:
        return pd.NaT


def load(priced_only=True):
    d = pd.read_csv(PICKS, low_memory=False)
    d = d[d["fi_total_runs"].notna()].copy()
    if priced_only:
        d = d[d["market_nrfi_odds"].notna() & d["market_yrfi_odds"].notna()].copy()

    d["dt"] = pd.to_datetime(d["date"])
    d["y_nrfi"] = (d["fi_total_runs"].astype(float) == 0).astype(int)
    d["p_model"] = pd.to_numeric(d["nrfi_prob"], errors="coerce")

    if priced_only:
        d["o_nrfi"] = d["market_nrfi_odds"].astype(float)
        d["o_yrfi"] = d["market_yrfi_odds"].astype(float)
        d["pay_nrfi"] = d["o_nrfi"].map(payout)
        d["pay_yrfi"] = d["o_yrfi"].map(payout)
        d["imp_nrfi"] = d["o_nrfi"].map(implied)
        d["imp_yrfi"] = d["o_yrfi"].map(implied)
        tot = d["imp_nrfi"] + d["imp_yrfi"]
        d["vig"] = tot - 1.0
        d["book_nrfi"] = d["imp_nrfi"] / tot        # de-vigged book probability
        # 1u profit for a bet at the captured price
        d["u_nrfi"] = np.where(d["y_nrfi"] == 1, d["pay_nrfi"], -1.0)
        d["u_yrfi"] = np.where(d["y_nrfi"] == 0, d["pay_yrfi"], -1.0)
        d["cap_at"] = pd.to_datetime(d["odds_captured_at"], utc=True, errors="coerce")
        d["game_at"] = d.apply(_game_dt_utc, axis=1)
        d["lead_h"] = (d["game_at"] - d["cap_at"]).dt.total_seconds() / 3600.0

    for c in FEATS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d[FEATS] = d[FEATS].fillna(d[FEATS].median(numeric_only=True))
    return d.sort_values("dt").reset_index(drop=True)


def auc(y, s):
    y = np.asarray(y, float)
    s = np.asarray(s, float)
    m = np.isfinite(y) & np.isfinite(s)
    y, s = y[m], s[m]
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = pd.Series(s).rank().values
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def day_boot_mean(df, col, B=4000, seed=7, scale=1.0):
    """Fast day-block bootstrap of a column MEAN (e.g. ROI in units)."""
    rng = np.random.default_rng(seed)
    sums, cnts = [], []
    for _, g in df.groupby("date"):
        v = g[col].values.astype(float)
        sums.append(v.sum())
        cnts.append(len(v))
    sums = np.asarray(sums)
    cnts = np.asarray(cnts, float)
    n = len(sums)
    if n == 0:
        return float("nan"), float("nan")
    idx = rng.integers(0, n, size=(B, n))
    tot = sums[idx].sum(axis=1)
    cn = cnts[idx].sum(axis=1)
    r = scale * tot / np.maximum(cn, 1)
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def day_bootstrap(df, stat, B=3000, seed=7):
    """Block bootstrap over calendar DAYS.  stat: DataFrame -> float."""
    rng = np.random.default_rng(seed)
    groups = [g for _, g in df.groupby("date")]
    n = len(groups)
    out = []
    for _ in range(B):
        idx = rng.integers(0, n, size=n)
        s = stat(pd.concat([groups[i] for i in idx], ignore_index=True))
        if s == s:
            out.append(s)
    if not out:
        return float("nan"), float("nan")
    a = np.array(out)
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))

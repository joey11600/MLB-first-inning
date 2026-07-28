#!/usr/bin/env python3
"""R01 -- INDEPENDENT re-derivation: does adding the book's implied
probability as an ordinary model input help?

Written from scratch (own loader, own devig, own AUC, own bootstrap) so it
does not inherit any bug from tools/nrfi_alt/price_common.py.

ANALYSIS ONLY.
"""
from __future__ import annotations
import re, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

ROOT = Path(__file__).resolve().parents[2]

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

def payout(o): return o/100.0 if o > 0 else 100.0/abs(o)
def implied(o): return 100.0/(o+100.0) if o > 0 else abs(o)/(abs(o)+100.0)
def logit(p, eps=1e-6):
    p = np.clip(np.asarray(p, float), eps, 1-eps); return np.log(p/(1-p))

def worsen(o, cents):
    """Move an American price `cents` worse for the bettor."""
    o = float(o)
    if o > 0:
        n = o - cents
        if n >= 100: return n
        return -(10000.0/max(n, 1e-9)) if n > 0 else -(100 + (100-n))
    return o - cents

def game_dt(row):
    s = str(row["game_time_et"])
    m = re.match(r"(\d+):(\d+)\s*(AM|PM)", s)
    if not m: return pd.NaT
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    try:
        return pd.Timestamp(f"{row['date']} {h:02d}:{int(m.group(2)):02d}",
                            tz="US/Eastern").tz_convert("UTC")
    except Exception:
        return pd.NaT

def load():
    d = pd.read_csv(ROOT/"data"/"picks_2026.csv", low_memory=False)
    d = d[d.fi_total_runs.notna()].copy()
    d = d[d.market_nrfi_odds.notna() & d.market_yrfi_odds.notna()].copy()
    d["dt"] = pd.to_datetime(d["date"])
    d["y"] = (d.fi_total_runs.astype(float) == 0).astype(int)
    d["p_model"] = pd.to_numeric(d.nrfi_prob, errors="coerce")
    d["o_n"] = d.market_nrfi_odds.astype(float)
    d["o_y"] = d.market_yrfi_odds.astype(float)
    d["pay_n"] = d.o_n.map(payout); d["pay_y"] = d.o_y.map(payout)
    d["imp_n"] = d.o_n.map(implied); d["imp_y"] = d.o_y.map(implied)
    d["vig"] = d.imp_n + d.imp_y - 1.0
    d["book"] = d.imp_n/(d.imp_n + d.imp_y)
    d["u_n"] = np.where(d.y == 1, d.pay_n, -1.0)
    d["u_y"] = np.where(d.y == 0, d.pay_y, -1.0)
    d["cap_at"] = pd.to_datetime(d.odds_captured_at, utc=True, errors="coerce")
    d["game_at"] = d.apply(game_dt, axis=1)
    d["lead_h"] = (d.game_at - d.cap_at).dt.total_seconds()/3600.0
    for c in FEATS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d[FEATS] = d[FEATS].fillna(d[FEATS].median(numeric_only=True))
    d["l_model"] = logit(d.p_model); d["l_book"] = logit(d.book)
    return d.sort_values(["dt", "game_pk"]).reset_index(drop=True)

def auc(y, s):
    y = np.asarray(y, float); s = np.asarray(s, float)
    m = np.isfinite(y) & np.isfinite(s); y, s = y[m], s[m]
    n1, n0 = y.sum(), (1-y).sum()
    if n1 == 0 or n0 == 0: return float("nan")
    r = pd.Series(s).rank().values
    return float((r[y == 1].sum() - n1*(n1+1)/2)/(n1*n0))

def ll(y, p):
    p = np.clip(np.asarray(p, float), 1e-6, 1-1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y*np.log(p) + (1-y)*np.log(1-p)))

def dayboot(df, stat, B=1200, seed=11):
    rng = np.random.default_rng(seed)
    gs = [g for _, g in df.groupby("date")]
    n = len(gs); out = []
    for _ in range(B):
        idx = rng.integers(0, n, size=n)
        v = stat(pd.concat([gs[i] for i in idx], ignore_index=True))
        if v == v: out.append(v)
    a = np.array(out)
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))

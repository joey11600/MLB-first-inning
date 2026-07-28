#!/usr/bin/env python3
"""
tools/nrfi_alt/common.py -- shared loader for the "does NRFI need a
different model?" investigation.

Loads 2025 (backtest CSV) and 2026 (picks_2026.csv, has real DK prices)
through the EXACT production feature construction (recalibrate_v2's
_build_t1_b1_phase_e3) and the production two-stage LR, so the
"production p_nrfi" column here is what the live system actually emits.

ANALYSIS ONLY. Reads files, writes nothing.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402

BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"
CAL_PATH = ROOT / "data" / "calibration_v2.json"

# The union of T1 and B1 features, used when we want a single flat design
# matrix for a direct (one-stage) NRFI model.
T1F = rc.T1_FEATURES
B1F = rc.B1_FEATURES


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def _load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_2025():
    """2025 backtest. No odds. Half-inning outcomes from fi_home/away_runs."""
    fi_park = rc.load_fi_park()
    out = []
    for r in _load_csv(BT_2025):
        fh, fa = fnum(r.get("fi_home_runs")), fnum(r.get("fi_away_runs"))
        if fh is None or fa is None:
            continue
        home = r.get("home", "") or r.get("home_team", "")
        fp = fnum(r.get("fi_park_nrfi_rate"))
        if fp is None:
            fp = fi_park.get(home, rc.FI_PARK_DEFAULT)
        try:
            tvec, bvec = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        out.append({
            "season": 2025, "date": r["date"], "home": home,
            "away": r.get("away", "") or r.get("away_team", ""),
            "t1": tvec, "b1": bvec,
            # T1 = top of 1st = AWAY team bats. B1 = bottom = HOME bats.
            "y_t1_run": 1 if fa > 0 else 0,
            "y_b1_run": 1 if fh > 0 else 0,
            "y_nrfi": 1 if (fa + fh) == 0 else 0,
            "nrfi_odds": None, "yrfi_odds": None,
            "lambda": fnum(r.get("lambda_total")),
        })
    out.sort(key=lambda x: x["date"])
    return out


def load_2026():
    """picks_2026.csv graded rows.  HAS real captured DK prices."""
    fi_park = rc.load_fi_park()
    out = []
    for r in _load_csv(PICKS_2026):
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue
        home = r.get("home_team", "")
        fp = fi_park.get(home, rc.FI_PARK_DEFAULT)
        try:
            tvec, bvec = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        fh, fa = fnum(r.get("fi_home_runs")), fnum(r.get("fi_away_runs"))
        out.append({
            "season": 2026, "date": r["date"], "home": home,
            "away": r.get("away_team", ""),
            "t1": tvec, "b1": bvec,
            "y_t1_run": (1 if fa and fa > 0 else 0) if fa is not None else None,
            "y_b1_run": (1 if fh and fh > 0 else 0) if fh is not None else None,
            "y_nrfi": 1 if actual == "NRFI" else 0,
            "nrfi_odds": fnum(r.get("market_nrfi_odds")),
            "yrfi_odds": fnum(r.get("market_yrfi_odds")),
            "lambda": fnum(r.get("lambda_lr_total")),
        })
    out.sort(key=lambda x: x["date"])
    return out


def attach_production(rows):
    """Adds 'raw' (uncalibrated two-stage p_nrfi) and 'prod' (calibrated)."""
    t1m, b1m = rc.load_lr_models()
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    raw = rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)
    cal = ProbCalibrator.load(CAL_PATH)
    for r, p in zip(rows, raw):
        r["raw"] = float(p)
        r["prod"] = float(cal.predict(float(p)))
    return rows


def design(rows, kind="union"):
    """Flat design matrix.

    kind='union' -> concatenate T1 and B1 vectors, dropping the 7 shared
    columns (park + 4 weather + ump) from the B1 half so they appear once.
    """
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    # shared columns in both blocks (same value): indices 0..6 = park,
    # temp, wind, humidity, dome ... and index 9 = ump.
    shared = [0, 1, 2, 3, 4, 5, 6, 9]
    shared_idx = [0, 3, 4, 5, 6, 9]  # park, temp, wind, hum, dome, ump
    keep_b = [i for i in range(Xb.shape[1]) if i not in shared_idx]
    X = np.hstack([Xt, Xb[:, keep_b]])
    names = list(T1F) + [f"b1_{B1F[i]}" for i in keep_b]
    return X, names


# ---------------------------------------------------------------------------
# plain logistic regression w/ L2, no sklearn dependency assumptions
# ---------------------------------------------------------------------------

def fit_lr(X, y, l2=1.0, iters=400):
    """Standardize + IRLS-ish gradient fit. Returns dict model."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd < 1e-9] = 1.0
    Z = (X - mu) / sd
    n, d = Z.shape
    Z1 = np.hstack([Z, np.ones((n, 1))])
    w = np.zeros(d + 1)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Z1 @ w)))
        W = np.clip(p * (1 - p), 1e-6, None)
        g = Z1.T @ (y - p) - l2 * np.r_[w[:-1], 0.0]
        H = (Z1 * W[:, None]).T @ Z1 + l2 * np.diag(np.r_[np.ones(d), 0.0])
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        w = w + step
        if np.max(np.abs(step)) < 1e-8:
            break
    return {"w": w[:-1], "b": w[-1], "mu": mu, "sd": sd}


def predict_lr(m, X):
    Z = (np.asarray(X, float) - m["mu"]) / m["sd"]
    return 1.0 / (1.0 + np.exp(-(Z @ m["w"] + m["b"])))


def auc(y, s):
    y = np.asarray(y)
    s = np.asarray(s, float)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(s)
    ranks = np.empty(len(s), float)
    sr = s[order]
    r = np.arange(1, len(s) + 1, dtype=float)
    i = 0
    while i < len(sr):
        j = i
        while j + 1 < len(sr) and sr[j + 1] == sr[i]:
            j += 1
        ranks[order[i:j + 1]] = r[i:j + 1].mean()
        i = j + 1
    return (ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def logloss(y, p):
    p = np.clip(np.asarray(p, float), 1e-9, 1 - 1e-9)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def block_bootstrap_days(rows, stat_fn, B=2000, seed=0):
    """Resample DAYS with replacement. stat_fn(list_of_rows) -> float."""
    rng = np.random.default_rng(seed)
    byday = {}
    for r in rows:
        byday.setdefault(r["date"], []).append(r)
    days = list(byday)
    outs = []
    for _ in range(B):
        pick = rng.integers(0, len(days), len(days))
        samp = [x for k in pick for x in byday[days[k]]]
        v = stat_fn(samp)
        if v == v:
            outs.append(v)
    outs = np.sort(np.asarray(outs))
    if len(outs) == 0:
        return (float("nan"),) * 3
    return float(np.mean(outs)), float(outs[int(0.025 * len(outs))]), float(outs[int(0.975 * len(outs))])

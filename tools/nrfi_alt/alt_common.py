#!/usr/bin/env python3
"""Shared loaders/metrics for the NRFI-regime discrimination study.
READ-ONLY. Nothing here writes to repo data."""
from __future__ import annotations
import csv, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402

BT = ROOT / "data" / "backtests"

SOURCES = {
    "2025bt": dict(
        paths=[BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"],
        outcol="actual_side", homecol="home", datecol="date",
        away_runs="fi_away_runs", home_runs="fi_home_runs"),
    "2026bt": dict(
        paths=[BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
               BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"],
        outcol="actual_side", homecol="home", datecol="date",
        away_runs="fi_away_runs", home_runs="fi_home_runs"),
    "2026picks": dict(
        paths=[ROOT / "data" / "picks_2026.csv"],
        outcol="actual_result", homecol="home_team", datecol="date",
        away_runs="fi_away_runs", home_runs="fi_home_runs"),
}


def load(name):
    """Returns dict with rows, raw p_nrfi, calibrated p_nrfi, per-half raw
    P(run), y_nrfi, y_t1_run, y_b1_run, dates."""
    spec = SOURCES[name]
    rows = []
    for p in spec["paths"]:
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get(spec["outcol"]) or "").upper() in ("NRFI", "YRFI"):
                    rows.append(r)
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    Xt, Xb, y, dates = [], [], [], []
    yt1, yb1 = [], []
    for r in rows:
        fp = fi_park.get(r.get(spec["homecol"], ""), rc.FI_PARK_DEFAULT)
        tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        Xt.append(tv); Xb.append(bv)
        y.append(1 if (r.get(spec["outcol"]) or "").upper() == "NRFI" else 0)
        dates.append(r.get(spec["datecol"], ""))
        # T1 = top of 1st = AWAY team bats.  B1 = bottom = HOME bats.
        ar = _f(r.get(spec["away_runs"]))
        hr = _f(r.get(spec["home_runs"]))
        yt1.append(np.nan if ar is None else (1.0 if ar > 0 else 0.0))
        yb1.append(np.nan if hr is None else (1.0 if hr > 0 else 0.0))
    Xt = np.asarray(Xt, float); Xb = np.asarray(Xb, float)
    p_t1_run = np.asarray(rc.lr_predict_raw(t1m, Xt), float)
    p_b1_run = np.asarray(rc.lr_predict_raw(b1m, Xb), float)
    raw = (1.0 - p_t1_run) * (1.0 - p_b1_run)
    calp = np.asarray([cal.predict(float(v)) for v in raw], float)
    return dict(name=name, rows=rows, X_t1=Xt, X_b1=Xb,
                p_t1_run=p_t1_run, p_b1_run=p_b1_run,
                raw=raw, cal=calp, y=np.asarray(y, float),
                y_t1_run=np.asarray(yt1, float), y_b1_run=np.asarray(yb1, float),
                dates=np.asarray(dates))


def _f(s):
    try:
        v = float(s)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def auc(scores, y):
    scores = np.asarray(scores, float); y = np.asarray(y, float)
    m = np.isfinite(scores) & np.isfinite(y)
    scores, y = scores[m], y[m]
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0 or len(y) == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    s = scores[order]
    ranks = np.arange(1, len(s) + 1, dtype=float)
    r = np.empty(len(s), float)
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        r[order[i:j + 1]] = ranks[i:j + 1].mean()
        i = j + 1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def brier_skill(p, y):
    """1 - Brier(model)/Brier(base rate of THIS subset). >0 = beats the
    constant-prediction baseline inside the subset."""
    p = np.asarray(p, float); y = np.asarray(y, float)
    m = np.isfinite(p) & np.isfinite(y)
    p, y = p[m], y[m]
    if len(y) < 2:
        return float("nan")
    b = np.mean((p - y) ** 2)
    b0 = np.mean((y.mean() - y) ** 2)
    return float("nan") if b0 == 0 else 1.0 - b / b0


def block_boot(dates, stat_fn, idx, n_boot=2000, seed=7):
    """Block bootstrap over DAYS.  stat_fn takes an index array."""
    rng = np.random.default_rng(seed)
    d = dates[idx]
    uniq = np.unique(d)
    by_day = {u: idx[d == u] for u in uniq}
    out = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by_day[u] for u in pick])
        v = stat_fn(ii)
        if np.isfinite(v):
            out.append(v)
    if not out:
        return (float("nan"), float("nan"))
    return (float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)))

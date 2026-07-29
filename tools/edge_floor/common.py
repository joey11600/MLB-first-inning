#!/usr/bin/env python3
"""
tools/edge_floor/common.py -- shared loaders for the EDGE FLOOR study.

ANALYSIS ONLY.  Nothing here writes to data/ or touches production
config.  Everything is read-only.

Provides:
    load_2026()          picks_2026.csv graded rows -> raw LR prob + real DK price
    load_backtest(year)  backtest CSV -> raw LR prob, no prices
    passes_lambda_floor  the live weather-adjusted lambda gate
    payout / implied     price arithmetic
    auc                  rank AUC, for the "is this season usable" check

NOTE ON LAMBDA.  Production stores lambda_lr_total in picks_2026.csv.
The backtest CSVs predate that column (they carry a Poisson-model
lambda_total that is NOT the same quantity).  The predictor's own
comment at mlb_first_inning_predictor.py:1096 states the identity
    lambda_lr_total == -ln(raw p_nrfi)
so we reconstruct it from the raw two-stage output for every season.
Verified against the 2026 CSV column in run_diag.py.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc          # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402

BT = {
    2024: ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv",
    2025: ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv",
}


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


def _score(rows):
    """Attach raw two-stage p_nrfi and the reconstructed lambda."""
    t1, b1 = rc.load_lr_models()
    Xt = np.asarray([r["t1"] for r in rows], dtype=float)
    Xb = np.asarray([r["b1"] for r in rows], dtype=float)
    raw = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    for r, p in zip(rows, raw):
        r["raw"] = float(p)
        r["lam_recon"] = -math.log(max(1e-12, float(p)))
        del r["t1"], r["b1"]
    return rows


def load_2026():
    fi_park = rc.load_fi_park()
    out, skipped = [], 0
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for rid, r in enumerate(csv.DictReader(f)):
            actual = (r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                skipped += 1
                continue
            out.append({
                "rid": rid, "season": 2026, "date": r["date"],
                "game": f"{r.get('away_team','')}@{r.get('home_team','')}",
                "t1": tv, "b1": bv,
                "yrfi_odds": fnum(r.get("market_yrfi_odds")),
                "lam_csv": fnum(r.get("lambda_lr_total")),
                "edge_stored": fnum(r.get("edge_on_pick")),
                "wx_temp": fnum(r.get("wx_temp_c")),
                "wx_wind": fnum(r.get("wx_wind_kmh")),
                "wx_dome": bool(fnum(r.get("wx_is_dome")) or 0),
                "yrfi_hit": actual == "YRFI",
                "y_nrfi": 0 if actual == "YRFI" else 1,
            })
    out.sort(key=lambda x: x["date"])
    return _score(out), skipped


def load_backtest(year):
    """Backtest CSVs have NO odds and NO umpire column (defaults to 0.50)."""
    fi_park = rc.load_fi_park()
    out, skipped = [], 0
    with open(BT[year], encoding="utf-8") as f:
        for rid, r in enumerate(csv.DictReader(f)):
            actual = (r.get("actual_side") or r.get("graded_result") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            fp = fnum(r.get("fi_park_nrfi_rate"))
            if fp is None:
                fp = fi_park.get(r.get("home", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                skipped += 1
                continue
            out.append({
                "rid": rid, "season": year, "date": r["date"],
                "game": f"{r.get('away','')}@{r.get('home','')}",
                "t1": tv, "b1": bv,
                "yrfi_odds": None,
                "lam_csv": fnum(r.get("lambda_total")),
                "wx_temp": fnum(r.get("wx_temp_c")),
                "wx_wind": fnum(r.get("wx_wind_kmh")),
                "wx_dome": bool(fnum(r.get("wx_is_dome")) or 0),
                "yrfi_hit": actual == "YRFI",
                "y_nrfi": 0 if actual == "YRFI" else 1,
            })
    out.sort(key=lambda x: x["date"])
    return _score(out), skipped


def passes_lambda_floor(row, lam_key="lam_recon"):
    """The live weather-adjusted lambda gate for STRONG YRFI."""
    lam = row.get(lam_key)
    fl = P._weather_adjusted_floor(
        P._LR_LAMBDA_YRFI_FLOOR, row["wx_temp"], row["wx_wind"], row["wx_dome"])
    return not (lam is not None and lam < fl)


def auc(scores, labels):
    """Rank AUC of `scores` predicting label==1.  0.5 == coin flip."""
    pairs = sorted(zip(scores, labels))
    n = len(pairs)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    pos = sum(1 for _, y in pairs if y == 1)
    neg = n - pos
    if pos == 0 or neg == 0:
        return float("nan")
    s = sum(r for r, (_, y) in zip(ranks, pairs) if y == 1)
    return (s - pos * (pos + 1) / 2.0) / (pos * neg)

#!/usr/bin/env python3
"""Shared loader for the edge-floor investigation.

Builds the LIVE YRFI bet set (STRONG gate + weather-adjusted lambda
floor) over every graded 2026 game, attaches the recomputed edge, and
exposes both the in-sample (deployed calibrator) and walk-forward
probability streams.

ANALYSIS ONLY -- nothing here writes to the ledger or the config.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402

GATE = P._LR_STRONG_YRFI_P


def passes_gate(r, p_nrfi, gate=GATE):
    """Production STRONG-YRFI classifier: lambda floor then prob gate."""
    if p_nrfi is None:
        return False
    fl = P._weather_adjusted_floor(
        P._LR_LAMBDA_YRFI_FLOOR, r["wx_temp"], r["wx_wind"], r["wx_dome"])
    if r["lambda"] is not None and r["lambda"] < fl:
        return False
    return p_nrfi < gate


def build_bets(rows, probs, gate=GATE, real_price_only=True):
    """The bets the live rule fires, with edge recomputed from scratch."""
    out = []
    for r, p in zip(rows, probs):
        if not passes_gate(r, p, gate):
            continue
        odds = r["yrfi_odds"]
        if odds is None and real_price_only:
            continue
        p_y = 1.0 - p
        imp = implied(odds) if odds is not None else None
        out.append({
            "rid": r["rid"], "date": r["date"], "game": f"{r['away']}@{r['home']}",
            "p_nrfi": p, "p_yrfi": p_y, "odds": odds,
            "implied": imp,
            "edge": (p_y - imp) if imp is not None else None,
            "win": r["yrfi_hit"], "raw": r["raw"], "lambda": r["lambda"],
        })
    return out


def walk_forward_probs(rows, min_train=200, knots=20):
    """Calibrator refit at each date from strictly PRIOR games only."""
    dates = sorted({r["date"] for r in rows})
    idx = defaultdict(list)
    for i, r in enumerate(rows):
        idx[r["date"]].append(i)
    wf = [None] * len(rows)
    for d in dates:
        prior = [i for i in range(len(rows)) if rows[i]["date"] < d]
        if len(prior) < min_train:
            continue
        c = CIRCalibrator.fit([rows[i]["raw"] for i in prior],
                              [rows[i]["y_nrfi"] for i in prior], knots, ["wf"])
        for i in idx[d]:
            wf[i] = c.predict(rows[i]["raw"])
    return wf


def insample_probs(rows):
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    return [cal.predict(r["raw"]) for r in rows], cal


def flat_pl(bets):
    return sum(payout(b["odds"]) if b["win"] else -1.0 for b in bets)


def summary(bets):
    n = len(bets)
    if not n:
        return dict(n=0, w=0, hit=float("nan"), need=float("nan"),
                    pl=0.0, roi=float("nan"))
    w = sum(1 for b in bets if b["win"])
    pl = flat_pl(bets)
    need = sum(b["implied"] for b in bets) / n
    return dict(n=n, w=w, hit=100.0 * w / n, need=100.0 * need,
                pl=pl, roi=100.0 * pl / n)

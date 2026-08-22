#!/usr/bin/env python3
"""
Refit CANDIDATE model artifacts with the two validated changes:
    1. + pooled first-inning xwOBA allowed (home pitcher -> T1, away -> B1)
    2. L2 0.05 -> 0.50

Writes to data/candidates/refit2026_fixwoba/ -- NEVER to data/lr_t1.json,
data/lr_b1.json or data/calibration_v2.json.  Wiring into the predictor is a
production change and waits for the operator.

Recipe mirrors the shipped 2026-05-26 sliding-window fit: train on 2024 +
2025 + 2026-to-date, calibrator (CIR) fit on the same pool's out-of-fold
raw output -- here approximated with the in-sample raw output, exactly as
recalibrate_v2.py does for the shipped file.  JSON schema matches what
mlb_first_inning_predictor._load_one expects: feature_names, weights, bias,
mean, std (standardised weights, intercept separate).

Also prints a side-by-side of the candidate vs the shipped artifacts on the
last 30 days of the ledger so a human can see it behaves sensibly before it
goes anywhere near the money path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration import CIRCalibrator  # noqa: E402
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load, logloss, matrix, predict  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

FEAT = "fi_xwoba"
L2 = 0.50
OUTDIR = ROOT / "data" / "candidates" / "refit2026_fixwoba"


def save_lr(path: Path, names, w, mu, sd, meta: dict):
    path.write_text(json.dumps({
        "feature_names": list(names), "weights": [float(x) for x in w[1:]],
        "bias": float(w[0]), "mean": [float(x) for x in mu], "std": [float(x) for x in sd],
        **meta}, indent=1), encoding="utf-8")


def main() -> int:
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    d26 = attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fac)
    allg = pd.concat([d24, d25, d26], ignore_index=True)
    for c in (f"home_{FEAT}", f"away_{FEAT}"):
        allg[c] = allg[c].fillna(allg[c].mean())
    T1 = T1_SHIPPED + [f"home_{FEAT}"]; B1 = B1_SHIPPED + [f"away_{FEAT}"]
    print(f"training pool: {len(allg)} games  (2024 {len(d24)}, 2025 {len(d25)}, 2026 {len(d26)})")

    # NOTE: the park map here is rebuilt from the pool with the shipped recipe
    # (PRIOR_GAMES=50) for continuity with the live file; the candidate weight
    # on fi_park_nrfi_rate is therefore on the same scale the predictor feeds.
    pk, b0 = build_park(allg, 50)
    Xt, Xb = matrix(allg, T1, pk, b0), matrix(allg, B1, pk, b0)
    wt, mt, st = fit_lr(Xt, allg.y_t1.values, L2)
    wb, mb, sb = fit_lr(Xb, allg.y_b1.values, L2)
    raw_nrfi = (1 - predict(wt, mt, st, Xt)) * (1 - predict(wb, mb, sb, Xb))
    cal = CIRCalibrator.fit(list(raw_nrfi), list((allg.y == 0).astype(int)), n_bins=20)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    meta = {"candidate": "refit2026_fixwoba", "l2": L2, "train_n": int(len(allg)),
            "train_seasons": ["2024", "2025", "2026"],
            "note": "adds pooled first-inning xwOBA allowed; see CHANGELOG 2026-08-21"}
    save_lr(OUTDIR / "lr_t1.json", T1, wt, mt, st, meta)
    save_lr(OUTDIR / "lr_b1.json", B1, wb, mb, sb, meta)
    cal.save(OUTDIR / "calibration_v2.json")
    print(f"wrote {OUTDIR}/lr_t1.json, lr_b1.json, calibration_v2.json")
    print(f"  T1 weight on home_{FEAT}: {wt[1+T1.index(f'home_{FEAT}')]:+.4f}   "
          f"B1 weight on away_{FEAT}: {wb[1+B1.index(f'away_{FEAT}')]:+.4f}   "
          f"(standardised; both must be positive)")

    # sanity: shipped artifacts vs candidate on the last 30 ledger days (in-sample
    # for both, so this is a behaviour check, NOT a performance claim)
    ship_t1 = json.load(open(ROOT / "data" / "lr_t1.json")); ship_b1 = json.load(open(ROOT / "data" / "lr_b1.json"))
    def apply(m, X):
        z = (X - np.array(m["mean"])) / np.where(np.array(m["std"]) == 0, 1, np.array(m["std"]))
        return 1 / (1 + np.exp(-(z @ np.array(m["weights"]) + m["bias"])))
    d26["date"] = pd.to_datetime(d26["date"])
    last = allg[(allg.season == 2026)].copy(); last["date"] = pd.to_datetime(last["date"])
    last = last[last.date >= last.date.max() - pd.Timedelta(days=30)]
    Xs_t, Xs_b = matrix(last, T1_SHIPPED, pk, b0), matrix(last, B1_SHIPPED, pk, b0)
    p_ship = 1 - (1 - apply(ship_t1, Xs_t)) * (1 - apply(ship_b1, Xs_b))
    Xc_t, Xc_b = matrix(last, T1, pk, b0), matrix(last, B1, pk, b0)
    p_cand = 1 - (1 - predict(wt, mt, st, Xc_t)) * (1 - predict(wb, mb, sb, Xc_b))
    y = last.y.values
    print(f"\nlast 30 ledger days (n={len(last)}, in-sample for the candidate):")
    print(f"  shipped  raw p(YRFI): mean {p_ship.mean():.4f}  AUC {auc(y,p_ship):.4f}  logloss {logloss(y,p_ship):.5f}")
    print(f"  candidate raw p(YRFI): mean {p_cand.mean():.4f}  AUC {auc(y,p_cand):.4f}  logloss {logloss(y,p_cand):.5f}")
    print(f"  actual YRFI rate      : {y.mean():.4f}")
    print(f"  corr(shipped, candidate) = {np.corrcoef(p_ship, p_cand)[0,1]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

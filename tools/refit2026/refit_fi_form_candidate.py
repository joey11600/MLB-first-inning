#!/usr/bin/env python3
"""
Refit the SHADOW candidate: shipped v3 minus the flat umpire input, plus the
shrunk first-inning form rate (fi_form.py, K=65, no prior-season carry).

Writes ONLY to data/candidates/refit2026_fiform/ -- never to data/lr_t1.json,
data/lr_b1.json or data/calibration_v2.json.  The predictor loads this
directory as the shadow model (see _load_shadow_models) and records its
opinion in the ledger's shadow_* columns; the live pick never reads it.

Recipe mirrors refit_candidate.py (the v3 precedent): train on 2024 + 2025 +
2026-to-date, L2 0.5, park map rebuilt from the pool with PRIOR_GAMES=50, CIR
calibrator on the in-sample raw output.  The cal-gate ceiling is re-derived
for the candidate exactly as production must at any refit (87th percentile of
calibrated p_nrfi among train-corpus candidates below 0.42) and stored in
meta.json, because a different model shifts the calibrated scale under a
fixed cut.

Before writing anything it re-runs the three-split test on THIS exact feature
set (the validated `add / K65_pw0` cell had the umpire input still present;
dropping it is expected to be a no-op because the live file is flat, but that
is checked, not assumed).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration import CIRCalibrator                                           # noqa: E402
from harness import T1_SHIPPED, B1_SHIPPED, auc, brier, build_park, fit_lr, load, logloss, matrix, predict  # noqa: E402
from test_fi_pooled import attach                                               # noqa: E402

import argparse                                                                 # noqa: E402
from build_fi_form import GRID                                                  # noqa: E402

L2 = 0.50
UMP = "home_plate_ump_nrfi_rate"
RAW10 = ("home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi")
OUTDIR = ROOT / "data" / "candidates" / "refit2026_fiform"

_ap = argparse.ArgumentParser()
_ap.add_argument("--variant", choices=["add", "swap10"], default="add",
                 help="add: fi_form alongside both raw fractions; swap10: fi_form replaces last-10")
_ap.add_argument("--config", default="K65_pw0", help="a name from build_fi_form.GRID")
_ap.add_argument("--out", default=None,
                 help="output directory name under data/candidates/ (default refit2026_fiform, "
                      "which is the directory the predictor loads as the shadow)")
_args = _ap.parse_args()
VARIANT, FORM_CFG = _args.variant, _args.config
FORM_PARAMS = dict(GRID)[FORM_CFG]
if _args.out:
    OUTDIR = ROOT / "data" / "candidates" / _args.out

T1_LIVE = T1_SHIPPED + ["home_fi_xwoba"]
B1_LIVE = B1_SHIPPED + ["away_fi_xwoba"]
if VARIANT == "add":
    T1_CAND = [f for f in T1_LIVE if f != UMP] + ["home_fi_form"]
    B1_CAND = [f for f in B1_LIVE if f != UMP] + ["away_fi_form"]
else:
    T1_CAND = [("home_fi_form" if f == RAW10[0] else f) for f in T1_LIVE if f != UMP]
    B1_CAND = [("away_fi_form" if f == RAW10[1] else f) for f in B1_LIVE if f != UMP]


def load_all():
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    form = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_form.csv")
    form["game_pk"] = pd.to_numeric(form["game_pk"], errors="coerce")
    form = form[["game_pk", f"home_{FORM_CFG}", f"away_{FORM_CFG}"]].rename(
        columns={f"home_{FORM_CFG}": "home_fi_form", f"away_{FORM_CFG}": "away_fi_form"})
    bt = ROOT / "data" / "backtests"

    def ld(path, park_col, season):
        d = load(path, park_col, season)
        d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
        # keep the latest row per game_pk -- see test_fi_form.load_all for why
        has = d["game_pk"].notna()
        d = pd.concat([d[has].sort_values("date").drop_duplicates(subset=["game_pk"], keep="last"),
                       d[~has]], ignore_index=True)
        own = {c: pd.to_numeric(d[c], errors="coerce") for c in ("home_fi_xwoba", "away_fi_xwoba") if c in d.columns}
        d = attach(d.drop(columns=list(own)), fac)
        for c, v in own.items():
            d[c] = v.fillna(d[c]).values
        d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
        return d.merge(form, on="game_pk", how="left")

    return (ld(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024),
            ld(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025),
            ld(ROOT / "data" / "picks_2026.csv", "home_team", 2026))


def fill(tr, te, cols):
    tr, te = tr.copy(), te.copy()
    for c in cols:
        mu = pd.to_numeric(tr[c], errors="coerce").mean()
        tr[c] = pd.to_numeric(tr[c], errors="coerce").fillna(mu)
        te[c] = pd.to_numeric(te[c], errors="coerce").fillna(mu)
    return tr, te


def fit(tr, t1f, b1f):
    pk, b0 = build_park(tr, 50)
    wt = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, L2)
    wb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, L2)
    raw = lambda d: (1 - predict(*wt, matrix(d, t1f, pk, b0))) * (1 - predict(*wb, matrix(d, b1f, pk, b0)))
    rtr = raw(tr)
    cal = CIRCalibrator.fit(list(rtr), list((tr.y == 0).astype(int)), n_bins=20)
    return wt, wb, pk, b0, cal, raw, rtr


FILLS = ["home_fi_xwoba", "away_fi_xwoba", "home_fi_form", "away_fi_form"]


def three_split(d24, d25, d26) -> bool:
    print("=" * 96)
    print("  THREE-SPLIT CHECK on the exact candidate feature set (ump out, fi_form in)")
    print("=" * 96)
    ok_all = True
    for lab, tr, te in [("2024 (train 2025)", d25, d24), ("2025 (train 2024)", d24, d25),
                        ("2026 (train 24+25)", pd.concat([d24, d25], ignore_index=True), d26)]:
        te = te.reset_index(drop=True); y = (te.y.values == 0).astype(int)
        trf, tef = fill(tr, te, FILLS)
        out = {}
        for name, (t1f, b1f) in (("live", (T1_LIVE, B1_LIVE)), ("cand", (T1_CAND, B1_CAND))):
            *_, cal, raw, _ = fit(trf, t1f, b1f)
            p = np.array([cal.predict(float(v)) for v in raw(tef)])
            out[name] = (auc(y, p), brier(y, p), logloss(y, p))
        d = out["cand"][0] - out["live"][0]
        ok_all &= d > 0
        print(f"  {lab:<20} live AUC {out['live'][0]:.4f}  cand AUC {out['cand'][0]:.4f}  dAUC {d:+.4f}   "
              f"Brier {out['live'][1]:.5f} -> {out['cand'][1]:.5f}   {'ok' if d > 0 else 'WORSE'}")
    print(f"  candidate beats live on AUC in all three splits: {'YES' if ok_all else 'NO'}")
    return ok_all


def save_lr(path: Path, names, w, mu, sd, meta: dict):
    path.write_text(json.dumps({
        "feature_names": list(names), "weights": [float(x) for x in w[1:]],
        "bias": float(w[0]), "mean": [float(x) for x in mu], "std": [float(x) for x in sd],
        **meta}, indent=1), encoding="utf-8")


def main() -> int:
    d24, d25, d26 = load_all()
    for lab, d in (("2024", d24), ("2025", d25), ("2026", d26)):
        v = pd.to_numeric(d["home_fi_form"], errors="coerce")
        print(f"  coverage {lab}: home_fi_form {v.notna().mean() * 100:5.1f}%  sd {v.std():.4f}  distinct {v.round(6).nunique()}")
    passed = three_split(d24, d25, d26)

    allg = pd.concat([d24, d25, d26], ignore_index=True)
    allg, _ = fill(allg, allg.head(0), FILLS)
    wt, wb, pk, b0, cal, raw, rtr = fit(allg, T1_CAND, B1_CAND)
    ctr = np.array([cal.predict(float(v)) for v in rtr])
    cand = ctr[ctr < 0.42]
    ceiling = float(np.quantile(cand, 0.87)) if len(cand) >= 20 else 0.413

    OUTDIR.mkdir(parents=True, exist_ok=True)
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:
        sha = "unknown"
    meta = {
        "candidate": "refit2026_fiform",
        "shadow_model": f"v3-ump+fi_form[{VARIANT}/{FORM_CFG}]",
        "variant": VARIANT,
        "l2": L2, "train_n": int(len(allg)), "train_seasons": ["2024", "2025", "2026"],
        "fit_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fit_commit": sha,
        "features_t1": T1_CAND, "features_b1": B1_CAND,
        # the parameters fi_form.py must use at predict time so the live input
        # is on exactly the scale this fit was trained on
        "fi_form": {"K": float(FORM_PARAMS["K"]), "prior_w": float(FORM_PARAMS["prior_w"]),
                    "halflife": FORM_PARAMS.get("halflife"), "window": FORM_PARAMS.get("window"),
                    "research_config": FORM_CFG},
        "strong_yrfi_max_p": ceiling,
        "three_split_pass": bool(passed),
        "note": ("shadow candidate: shipped v3 minus the flat umpire input plus the shrunk "
                 "first-inning form rate; see CHANGELOG 2026-09-03b and 2026-09-04"),
    }
    save_lr(OUTDIR / "lr_t1.json", T1_CAND, wt[0], wt[1], wt[2], meta)
    save_lr(OUTDIR / "lr_b1.json", B1_CAND, wb[0], wb[1], wb[2], meta)
    cal.save(OUTDIR / "calibration_v2.json")
    (OUTDIR / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"\nwrote {OUTDIR}/lr_t1.json, lr_b1.json, calibration_v2.json, meta.json")
    print(f"  train n {len(allg)}   cal-gate ceiling for the candidate: {ceiling:.4f} (live 0.413)")
    print(f"  T1 weight on home_fi_form {wt[0][1 + T1_CAND.index('home_fi_form')]:+.4f}   "
          f"B1 weight on away_fi_form {wb[0][1 + B1_CAND.index('away_fi_form')]:+.4f}   (standardised; both must be NEGATIVE: cleaner history -> fewer runs)")
    print(f"  T1 weight on home_fi_xwoba {wt[0][1 + T1_CAND.index('home_fi_xwoba')]:+.4f}   "
          f"B1 on away_fi_xwoba {wb[0][1 + B1_CAND.index('away_fi_xwoba')]:+.4f}")

    # behaviour check vs the shipped artifacts on the last 30 ledger days (in-sample for both)
    ship_t1 = json.load(open(ROOT / "data" / "lr_t1.json")); ship_b1 = json.load(open(ROOT / "data" / "lr_b1.json"))
    def apply(m, X):
        z = (X - np.array(m["mean"])) / np.where(np.array(m["std"]) == 0, 1, np.array(m["std"]))
        return 1 / (1 + np.exp(-(z @ np.array(m["weights"]) + m["bias"])))
    last = allg[allg.season == 2026].copy(); last["date"] = pd.to_datetime(last["date"])
    last = last[last.date >= last.date.max() - pd.Timedelta(days=30)]
    p_ship = 1 - (1 - apply(ship_t1, matrix(last, T1_LIVE, pk, b0))) * (1 - apply(ship_b1, matrix(last, B1_LIVE, pk, b0)))
    p_cand = 1 - raw(last)
    y = last.y.values
    print(f"\nlast 30 ledger days (n={len(last)}, in-sample for the candidate):")
    print(f"  shipped   raw p(YRFI) mean {p_ship.mean():.4f}  AUC {auc(y, p_ship):.4f}")
    print(f"  candidate raw p(YRFI) mean {p_cand.mean():.4f}  AUC {auc(y, p_cand):.4f}")
    print(f"  corr(shipped, candidate) = {np.corrcoef(p_ship, p_cand)[0, 1]:.4f}   actual YRFI {y.mean():.4f}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

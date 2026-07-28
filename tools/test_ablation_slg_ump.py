#!/usr/bin/env python3
"""
tools/test_ablation_slg_ump.py -- 3-split out-of-sample test of two
feature ablations proposed by the 2026-07-27 investigation.

ABLATION 1 -- drop top3c_slg, keep top3c_obp + top3c_iso.
  Measured on this repo's data, `top3c_iso` and `top3c_slg` correlate
  +0.90 to +0.93 in every season (2024/2025/2026) and carry near-equal
  OPPOSITE-signed weights (T1: iso +0.3454, slg -0.3266; B1: iso +0.2289,
  slg -0.3980) -- by far the two largest coefficients in the model.
  Since ISO = SLG - AVG identically, that pair is a high-variance
  reconstruction of batting average.
  The deep-research sweep found the methodological literature is explicit
  that collinearity at r~0.93 damages coefficient STABILITY and SIGN, not
  predictive accuracy, and that the defensible remedy is an *a priori*
  drop (keep OBP + ISO on algebraic grounds) rather than data-driven
  selection such as Lasso.  EXPECTATION IS THEREFORE FLAT ACCURACY WITH
  MORE STABLE COEFFICIENTS -- not a P&L win.  This script is built to
  measure exactly that, so we do not fool ourselves into calling a null
  accuracy result a success or a failure.

ABLATION 2 -- drop home_plate_ump_nrfi_rate entirely.
  `tools/test_umpire_persistence.py` shows the precondition for the
  feature fails on this repo's own data: the stored 2022-23 shrunk rate
  correlates r = -0.138 (90% CI [-0.305, +0.042]) with the same umpire's
  actual 2026 first-inning results, and the 2026 umpire-to-umpire spread
  (sd 0.104) is SMALLER than pure binomial noise at those sample sizes
  (0.122) -- i.e. no detectable umpire signal exists in 2026 at all.
  The feature is additionally sourced from 2022-2023, the seasons
  CLAUDE.md bans from training for pitch-clock distribution shift, and
  is mis-scaled at serve time (train sd 0.0167 vs live sd 0.104).

METHOD
  Columns are MASKED out of the exact production feature matrices built
  by recalibrate_v2, rather than rebuilt from scratch, so feature
  construction is guaranteed identical to production.

  Mandated CLAUDE.md 3-split, no exceptions:
      2024        -> 2025
      2025        -> 2024
      2024 + 2025 -> 2026
  A variant that helps in only one direction is rejected.

  Reported per split: Brier, log loss, AUC on the combined NRFI
  probability.  Reported across splits: SIGN STABILITY -- the share of
  features whose fitted coefficient keeps the same sign in all three
  fits.  That is the quantity collinearity actually damages, so it is
  the primary endpoint for ablation 1.

Usage:
    python tools/test_ablation_slg_ump.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
from lr_baseline import LogReg  # noqa: E402
from tools.calibrator_bakeoff import BT_2024, BT_2025, PICKS_2026  # noqa: E402

L2 = 0.05  # matches two_stage_model.py's default


def load_split(path, kind, fi_park):
    """Build (X_t1, X_b1, y_t1, y_b1, y_nrfi) with PER-HALF labels.

    recalibrate_v2's gather_* helpers return only the combined NRFI
    label and do not report which rows they kept, so they cannot be used
    here -- the two-stage model must be trained on per-half targets
    (`y_t1` = did the TOP of the 1st score, `y_b1` = the BOTTOM), exactly
    as two_stage_model.py:225-231 does. Feature vectors still come from
    recalibrate_v2._build_t1_b1_phase_e3 so construction is byte-identical
    to production; only label extraction and row alignment are local.
    """
    import csv
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    home_key = "home" if kind == "backtest" else "home_team"
    res_key = "actual_side" if kind == "backtest" else "actual_result"

    Xt, Xb, yt, yb, yn = [], [], [], [], []
    for r in rows:
        actual = (r.get(res_key) or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue
        ta, ha = r.get("fi_away_runs"), r.get("fi_home_runs")
        if ta in (None, "") or ha in (None, ""):
            continue
        try:
            t1_y = 1 if int(float(ta)) > 0 else 0
            b1_y = 1 if int(float(ha)) > 0 else 0
        except (TypeError, ValueError):
            continue
        fp = fi_park.get(r.get(home_key, ""), rc.FI_PARK_DEFAULT)
        try:
            tvec, bvec = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        Xt.append(tvec)
        Xb.append(bvec)
        yt.append(t1_y)
        yb.append(b1_y)
        yn.append(1 if actual == "NRFI" else 0)

    return (np.asarray(Xt, float), np.asarray(Xb, float),
            np.asarray(yt, int), np.asarray(yb, int), np.asarray(yn, int))


def mask(names, drop):
    keep = [i for i, n in enumerate(names) if n not in drop]
    return keep, [names[i] for i in keep]


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def logloss(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(score, y):
    s, y = np.asarray(score, float), np.asarray(y, int)
    pos, neg = (y == 1).sum(), (y == 0).sum()
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(s)
    r = np.empty(len(s), float)
    r[order] = np.arange(1, len(s) + 1)
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    sm = np.zeros(len(cnt))
    np.add.at(sm, inv, r)
    r = (sm / cnt)[inv]
    return float((r[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def fit_two_stage(Xt, Xb, yt, yb, names_t, names_b):
    t1 = LogReg.fit(Xt, yt, names_t, l2=L2)
    b1 = LogReg.fit(Xb, yb, names_b, l2=L2)
    return t1, b1


def predict_nrfi(t1, b1, Xt, Xb):
    def p(m, X):
        Xn = (X - m.mean) / np.where(m.std == 0, 1.0, m.std)
        return 1.0 / (1.0 + np.exp(-(Xn @ m.w + m.b)))
    return (1.0 - p(t1, Xt)) * (1.0 - p(b1, Xb))


def main():
    fi_park = rc.load_fi_park()
    print("Loading splits...")
    d = {
        "2024": load_split(BT_2024, "backtest", fi_park),
        "2025": load_split(BT_2025, "backtest", fi_park),
        "2026": load_split(PICKS_2026, "picks", fi_park),
    }
    for k, (Xt, _, yt, yb, yn) in d.items():
        print(f"  {k}: N={len(yn)}  NRFI rate {yn.mean():.4f}  "
              f"P(T1 run) {yt.mean():.4f}  P(B1 run) {yb.mean():.4f}")

    NT, NB = rc.T1_FEATURES, rc.B1_FEATURES

    VARIANTS = {
        "A baseline (production)": set(),
        "B drop SLG":              {"away_top3c_slg", "home_top3c_slg"},
        "C drop umpire":           {"home_plate_ump_nrfi_rate"},
        "D drop SLG + umpire":     {"away_top3c_slg", "home_top3c_slg",
                                    "home_plate_ump_nrfi_rate"},
    }

    SPLITS = [
        ("2024 -> 2025", ["2024"], "2025"),
        ("2025 -> 2024", ["2025"], "2024"),
        ("2024+2025 -> 2026", ["2024", "2025"], "2026"),
    ]

    results = {}
    signs = {}
    for vname, drop in VARIANTS.items():
        kt, nt = mask(NT, drop)
        kb, nb = mask(NB, drop)
        results[vname] = {}
        signs[vname] = []
        for sname, tr, te in SPLITS:
            Xt = np.vstack([d[s][0] for s in tr])[:, kt]
            Xb = np.vstack([d[s][1] for s in tr])[:, kb]
            # PER-HALF targets: T1 model learns P(run in top of 1st),
            # B1 model learns P(run in bottom of 1st).
            y_t1 = np.concatenate([d[s][2] for s in tr])
            y_b1 = np.concatenate([d[s][3] for s in tr])
            t1, b1 = fit_two_stage(Xt, Xb, y_t1, y_b1, nt, nb)
            signs[vname].append(np.sign(np.concatenate([t1.w, b1.w])))

            XtT = d[te][0][:, kt]
            XbT = d[te][1][:, kb]
            yT = d[te][4]          # combined NRFI label for scoring
            p = predict_nrfi(t1, b1, XtT, XbT)
            results[vname][sname] = (brier(p, yT), logloss(p, yT), auc(p, yT))

    print("\n" + "=" * 96)
    print("  3-SPLIT OUT-OF-SAMPLE ACCURACY  (Brier / logloss lower better, AUC higher better)")
    print("=" * 96)
    for sname, _, _ in SPLITS:
        print(f"\n--- {sname} ---")
        print(f"  {'variant':<26}{'Brier':>10}{'d vs base':>12}{'logloss':>10}{'AUC':>9}")
        base = results["A baseline (production)"][sname]
        for vname in VARIANTS:
            b, ll, a = results[vname][sname]
            print(f"  {vname:<26}{b:>10.5f}{b-base[0]:>+12.5f}{ll:>10.5f}{a:>9.4f}")

    print("\n" + "=" * 96)
    print("  AVERAGE ACROSS 3 SPLITS + SIGN STABILITY (primary endpoint for ablation 1)")
    print("=" * 96)
    print(f"  {'variant':<26}{'Brier':>10}{'logloss':>10}{'AUC':>9}"
          f"{'splits better':>15}{'sign-stable':>13}")
    baseavg = np.mean([results["A baseline (production)"][s][0] for s, _, _ in SPLITS])
    for vname in VARIANTS:
        bs = [results[vname][s][0] for s, _, _ in SPLITS]
        lls = [results[vname][s][1] for s, _, _ in SPLITS]
        aus = [results[vname][s][2] for s, _, _ in SPLITS]
        better = sum(1 for s, _, _ in SPLITS
                     if results[vname][s][0] <= results["A baseline (production)"][s][0] + 1e-12)
        S = np.array(signs[vname])
        stable = float(np.mean(np.all(S == S[0], axis=0)))
        print(f"  {vname:<26}{np.mean(bs):>10.5f}{np.mean(lls):>10.5f}{np.mean(aus):>9.4f}"
              f"{better:>12}/3{stable:>12.1%}")
    print(f"\n  baseline average Brier = {baseavg:.5f}")

    print("\n" + "=" * 96)
    print("  INTERPRETATION GUIDE")
    print("=" * 96)
    print("  Ablation 1 (drop SLG): the literature predicts FLAT accuracy and HIGHER")
    print("    sign stability.  Ship it if Brier is unchanged (within ~0.0005) and")
    print("    sign stability improves -- that is the whole point.  A Brier *win*")
    print("    would be a bonus, not the criterion.")
    print("  Ablation 2 (drop umpire): the feature failed its persistence")
    print("    precondition, so the prediction is FLAT accuracy.  Flat means the")
    print("    feature was contributing nothing and should go, since it also")
    print("    carries a live train/serve scale mismatch.")
    print("  Reject any variant that degrades Brier in more than one split.")


if __name__ == "__main__":
    main()

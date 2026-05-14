#!/usr/bin/env python3
"""tools/candidate_walkforward.py -- Gate B walk-forward backtest.

Generalized from tools/v23_walkforward_backtest.py to accept any
phase combination via pass-through flags.

Walk-forward semantics (read this carefully -- the BASELINE and
CANDIDATE are intentionally asymmetric):

  BASELINE  = production V2.2 weights at data/lr_t1.json + data/lr_b1.json,
              used as a FIXED MODEL across every 2026 day.  This is a
              "fixed-model backtest" (option (a) in walk-forward
              taxonomy), NOT a retrain.  Rationale: V2.2 is the model
              we already ship; its 2026 performance is what we already
              observe live (~+35.5u per pl_calc.py).  Refitting V2.2
              daily would create a hypothetical V2.2-retrained that
              isn't the production model -- breaks the comparison.

  CANDIDATE = LR refit daily on the cumulative pool of 2026 graded
              picks, predicting the next day, using the candidate's
              feature set (--phase-e3 + optional --phase-g + optional
              --fie).  This IS true walk-forward (option (b)).  Falls
              back to BASELINE V2.2 weights when cumulative 2026 train
              size < 100 -- mirrors what real-time deploy would do at
              start of season before enough 2026 data accumulates.

Why this asymmetry: the gate is asking "would V2.3-fie deployed at
2026 season start have outperformed the V2.2 model we actually ran?"
That requires:
  - candidate to walk-forward learn from in-season data (option b), and
  - baseline to be the actual production model (option a, fixed weights)
NOT "two hypothetical models, both walk-forward."  We're comparing a
hypothetical deploy vs the real deploy, not two hypothetical deploys.

P&L math:
  Uses tracker.payout_per_unit (canonical American-odds-to-per-unit
  helper).  Fallback to flat -110 (= 100/110 = 0.909u win) when the
  captured market odds string is blank or unparseable.  Win/loss
  accounting is inline:
      if actual_result == picked_side:  pnl += payout_per_unit(odds)
      elif actual_result is graded:     pnl -= 1.0
  Thin-pitcher demotion (--phase-2.1 production policy) is applied to
  BOTH baseline and candidate so the gate compares apples-to-apples
  policy stacks (different models, identical demotion rule).

Decision gate (per playbook Phase 2.1):
  Candidate P&L matches or beats baseline P&L, OR stays within 5u.

Arg shape (option (d) pass-through, same as candidate_validation.py):
  --phase-e3                Required candidate anchor (auto-enabled).
  --phase-g                 Optional candidate add-on.
  --fie                     Optional candidate add-on (Phase 2.1 FIE).
  --candidate <dir>         Where to save the day-by-day walk-forward
                             log.  Trained weights are NOT persisted
                             (each day's retrained weights are
                             discarded after that day's predictions).
  --baseline-tmp <dir>      (reserved; baseline uses fixed production
                             weights from data/lr_*.json, no save path
                             needed.  Flag kept for arg-shape parity
                             with candidate_validation.py.)

Usage:
  python tools/candidate_walkforward.py --phase-e3 --fie \\
      --candidate data/candidates/v23_fie

  python tools/candidate_walkforward.py --phase-e3 --phase-g --fie \\
      --candidate data/candidates/v24_g_fie
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402  -- payout_per_unit
from calibration import ProbCalibrator  # noqa: E402
from lr_baseline import LogReg  # noqa: E402
from two_stage_model import (  # noqa: E402
    LEAGUE_FI_AVG_ERA_FALLBACK,
    T1_PHASE_E3_FEATURES, B1_PHASE_E3_FEATURES,
    T1_PHASE_G_FEATURES,  B1_PHASE_G_FEATURES,
    T1_FIE_FEATURES,      B1_FIE_FEATURES,
)
from recalibrate_v2 import load_fi_park  # noqa: E402

CAL_PATH = ROOT / "data" / "calibration_v2.json"
PICKS    = ROOT / "data" / "picks_2026.csv"
PROD_T1  = ROOT / "data" / "lr_t1.json"
PROD_B1  = ROOT / "data" / "lr_b1.json"
DEFAULT_BASELINE_TMP = ROOT / "data" / "candidates" / ".baseline_tmp"

STRONG_NRFI_P     = 0.56
PASS_LO_P         = 0.44
LAMBDA_YRFI_FLOOR = 0.78
MIN_TRAIN_N       = 100
THIN_PQ           = {"sm", "ltd"}

# Defaults shared with two_stage_model.gather()'s coerce() calls.
LEAGUE_AVG_ERA, LEAGUE_AVG_OBP, LEAGUE_AVG_SLG, LEAGUE_AVG_ISO = 4.20, 0.318, 0.414, 0.169
FI_PARK_DEFAULT = 0.50
WX_DEFS = (20.0, 10.0, 60.0)
LEAGUE_NRFI = 0.50
LEAGUE_AVG_XERA = 4.20
NEUTRAL_PCT = 50


def _coerce(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _pq_worst(aq: str, hq: str) -> str:
    order = {"sm": 0, "ltd": 1, "live": 2}
    a = order.get((aq or "").lower(), 9)
    h = order.get((hq or "").lower(), 9)
    rev = {0: "sm", 1: "ltd", 2: "live"}
    return rev.get(min(a, h), "avg")


def _payout(odds_str: str) -> float:
    """Wrapper: tracker.payout_per_unit returns None on parse failure;
    we fall back to flat -110 (~0.909u win) here so callers don't
    handle None.  This is the canonical math via tracker plus our
    documented fallback."""
    ppu = tracker.payout_per_unit(odds_str or "")
    return 100.0 / 110.0 if ppu is None else ppu


def _build_vec(r: dict, half: str, fipark: dict, *, phase_g: bool, fie: bool) -> list[float]:
    """Build the feature vector for one row + half-inning.  Phase-E3
    base + optional Phase G (3 features per half) + optional FIE
    (1 feature per half).  Order MUST match the matching
    T1_*/B1_* feature-name list compositions in two_stage_model.py."""
    home = r.get("home_team", "")
    fi_park = fipark.get(home, FI_PARK_DEFAULT)
    wx = [_coerce(r.get("wx_temp_c"),    WX_DEFS[0]),
          _coerce(r.get("wx_wind_kmh"),  WX_DEFS[1]),
          _coerce(r.get("wx_humidity"),  WX_DEFS[2]),
          _coerce(r.get("wx_is_dome"),   0.0)]
    ump = _coerce(r.get("home_plate_ump_nrfi_rate"), LEAGUE_NRFI)
    h_era = _coerce(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = _coerce(r.get("away_era"), LEAGUE_AVG_ERA)

    if half == "t1":
        vec = [
            fi_park,
            _coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
            _coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
            wx[0], wx[1], wx[2], wx[3],
            _coerce(r.get("home_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
            _coerce(r.get("away_top3c_obp"), LEAGUE_AVG_OBP),
            ump,
            _coerce(r.get("home_xera"), LEAGUE_AVG_XERA),
            _coerce(r.get("home_whiff_pct_rank"), NEUTRAL_PCT),
            h_era - a_era,
            _coerce(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
            _coerce(r.get("away_top3c_slg"), LEAGUE_AVG_SLG),
            _coerce(r.get("away_top3c_iso"), LEAGUE_AVG_ISO),
            _coerce(r.get("home_pvt_nrfi_rate"), LEAGUE_NRFI),
            _coerce(r.get("home_avg_ip_per_start"), 5.0),
        ]
        if phase_g:
            vec += [
                _coerce(r.get("away_top3c_last10_obp"), LEAGUE_AVG_OBP),
                _coerce(r.get("away_top3c_last10_slg"), LEAGUE_AVG_SLG),
                _coerce(r.get("away_top3c_last10_iso"), LEAGUE_AVG_ISO),
            ]
        if fie:
            vec.append(_coerce(r.get("home_first_inning_era"), LEAGUE_FI_AVG_ERA_FALLBACK))
        return vec

    # half == "b1"
    vec = [
        fi_park,
        _coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        _coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        wx[0], wx[1], wx[2], wx[3],
        _coerce(r.get("away_p_last5_pitcher_nrfi"), LEAGUE_NRFI),
        _coerce(r.get("home_top3c_obp"), LEAGUE_AVG_OBP),
        ump,
        _coerce(r.get("away_xera"), LEAGUE_AVG_XERA),
        _coerce(r.get("away_whiff_pct_rank"), NEUTRAL_PCT),
        a_era - h_era,
        _coerce(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI),
        _coerce(r.get("home_top3c_slg"), LEAGUE_AVG_SLG),
        _coerce(r.get("home_top3c_iso"), LEAGUE_AVG_ISO),
        _coerce(r.get("away_pvt_nrfi_rate"), LEAGUE_NRFI),
        _coerce(r.get("away_avg_ip_per_start"), 5.0),
    ]
    if phase_g:
        vec += [
            _coerce(r.get("home_top3c_last10_obp"), LEAGUE_AVG_OBP),
            _coerce(r.get("home_top3c_last10_slg"), LEAGUE_AVG_SLG),
            _coerce(r.get("home_top3c_last10_iso"), LEAGUE_AVG_ISO),
        ]
    if fie:
        vec.append(_coerce(r.get("away_first_inning_era"), LEAGUE_FI_AVG_ERA_FALLBACK))
    return vec


def fit_lr(train_rows: list[dict], features: list[str], half: str,
            fipark: dict, *, phase_g: bool, fie: bool) -> dict:
    """Fit one half-inning LR on the given training rows with the
    candidate's feature set.  Returns a dict in the same shape the
    rest of this script expects (weights / bias / mean / std)."""
    X, y = [], []
    for r in train_rows:
        vec = _build_vec(r, half, fipark, phase_g=phase_g, fie=fie)
        if half == "t1":
            label = 1 if _coerce(r.get("fi_away_runs"), 0) > 0 else 0
        else:
            label = 1 if _coerce(r.get("fi_home_runs"), 0) > 0 else 0
        X.append(vec)
        y.append(label)
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=int)
    model = LogReg.fit(X, y, features, l2=0.05)
    return {
        "weights": np.asarray(model.w, dtype=float),
        "bias":    float(model.b),
        "mean":    np.asarray(model.mean, dtype=float),
        "std":     np.asarray(model.std,  dtype=float),
    }


def _load_prod_weights(path: Path) -> dict:
    """Load a saved LogReg JSON.  Used for the BASELINE -- production
    V2.2 weights are read once and used as a fixed model."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {
        "weights": np.asarray(d["weights"], dtype=float),
        "bias":    float(d["bias"]),
        "mean":    np.asarray(d["mean"],    dtype=float),
        "std":     np.asarray(d["std"],     dtype=float),
    }


def predict_pair(t1m, b1m, t1v, b1v) -> float:
    """P(NRFI) = (1 - p_t1_run) * (1 - p_b1_run) under independence."""
    X1 = np.asarray([t1v], dtype=float)
    X2 = np.asarray([b1v], dtype=float)
    z1 = (X1 - t1m["mean"]) / t1m["std"] @ t1m["weights"] + t1m["bias"]
    z2 = (X2 - b1m["mean"]) / b1m["std"] @ b1m["weights"] + b1m["bias"]
    p_t1 = float(1.0 / (1.0 + np.exp(-z1))[0])
    p_b1 = float(1.0 / (1.0 + np.exp(-z2))[0])
    return (1.0 - p_t1) * (1.0 - p_b1)


def _classify(p_cal: float, lam_total: float) -> tuple[str, str]:
    """Production classifier: STRONG NRFI / STRONG YRFI / PASS."""
    if p_cal >= STRONG_NRFI_P:
        return "NRFI", "STRONG"
    if p_cal < PASS_LO_P:
        if lam_total >= LAMBDA_YRFI_FLOOR:
            return "YRFI", "STRONG"
        return "PASS", "NO EDGE"
    return "PASS", "NO EDGE"


def _resolve_flags(args) -> tuple[bool, bool, str]:
    """Return (phase_g, fie, candidate_label).  Same auto-enable
    behavior as candidate_validation.py."""
    if (args.phase_g or args.fie) and not args.phase_e3:
        print("[note] --phase-g/--fie require --phase-e3; enabling phase-e3 automatically")
        args.phase_e3 = True

    extras = []
    if args.phase_g: extras.append("PHASE_G")
    if args.fie:     extras.append("FIE")
    label = "PHASE_E3" + ("+" + "+".join(extras) if extras else "")
    return args.phase_g, args.fie, label


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--phase-e3", action="store_true",
                   help="Candidate anchor; auto-enabled if --phase-g or --fie given.")
    p.add_argument("--phase-g", action="store_true",
                   help="Candidate add-on: top-3 last-10-games batter features.")
    p.add_argument("--fie", action="store_true",
                   help="Candidate add-on: Phase 2.1 first-inning ERA Bayesian blend.")
    p.add_argument("--fps", action="store_true",
                   help="Candidate add-on: Phase 2.2 first-pitch strike % in 1st inning.")
    p.add_argument("--candidate", required=True, type=Path,
                   help="Directory for the walk-forward log CSV.  Trained "
                        "weights themselves are NOT persisted (each day's "
                        "retrained weights are discarded after use).")
    p.add_argument("--baseline-tmp", default=DEFAULT_BASELINE_TMP, type=Path,
                   help="(reserved for parity with candidate_validation.py; "
                        "baseline here uses fixed production weights from "
                        "data/lr_*.json and saves nothing.)")
    args = p.parse_args()

    phase_g, fie, candidate_label = _resolve_flags(args)

    if not (phase_g or fie):
        sys.exit("ERROR: candidate has no add-on flags (would equal baseline).  "
                 "Pass at least one of --phase-g, --fie, or future --phase-* flags.")

    args.candidate.mkdir(parents=True, exist_ok=True)
    log_path = args.candidate / "walkforward_log.csv"

    # Resolve feature-name lists for the candidate (used by LogReg.fit).
    if phase_g:
        cand_t1_feats = list(T1_PHASE_G_FEATURES)
        cand_b1_feats = list(B1_PHASE_G_FEATURES)
    else:
        cand_t1_feats = list(T1_PHASE_E3_FEATURES)
        cand_b1_feats = list(B1_PHASE_E3_FEATURES)
    if fie:
        cand_t1_feats += list(T1_FIE_FEATURES)
        cand_b1_feats += list(B1_FIE_FEATURES)

    fipark = load_fi_park()

    # Baseline = fixed production V2.2 weights.
    baseline_t1 = _load_prod_weights(PROD_T1)
    baseline_b1 = _load_prod_weights(PROD_B1)
    cal = ProbCalibrator.load(CAL_PATH)

    # Load 2026 picks ledger, filter to graded games, sort chronologically.
    with open(PICKS, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    graded = [
        r for r in all_rows
        if (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")
        and (r.get("fi_away_runs") or "") != ""
        and (r.get("fi_home_runs") or "") != ""
    ]
    graded.sort(key=lambda r: r.get("date", ""))

    by_date: dict[str, list[dict]] = {}
    for r in graded:
        by_date.setdefault(r["date"], []).append(r)
    dates = sorted(by_date.keys())

    print("=" * 78)
    print("  Gate B walk-forward backtest")
    print("=" * 78)
    print(f"  baseline      : V2.2 fixed model (data/lr_t1.json + lr_b1.json)")
    print(f"  baseline feats: {len(T1_PHASE_E3_FEATURES)} per half  (PHASE_E3)")
    print(f"  candidate     : walk-forward retrain daily on 2026 graded picks")
    print(f"  candidate flags: --phase-e3"
          f"{' --phase-g' if phase_g else ''}"
          f"{' --fie' if fie else ''}"
          f"  -> variant {candidate_label}")
    print(f"  candidate feats: {len(cand_t1_feats)} per half")
    print(f"  thin-pitcher demotion: applied to BOTH (production policy)")
    print(f"  walk-forward log -> {log_path}")
    print()
    print(f"  Days: {len(dates)}  ({dates[0]} -> {dates[-1]})  total graded picks: {len(graded)}")
    print()
    hdr = (f"  {'date':<11}  {'train_n':>7}  {'mode':<10}  "
           f"{'base bets':>9}  {'base P&L':>10}  "
           f"{'cand bets':>9}  {'cand P&L':>10}")
    print(hdr)
    print(f"  {'-'*11}  {'-'*7}  {'-'*10}  {'-'*9}  {'-'*10}  {'-'*9}  {'-'*10}")

    # Walk forward
    cum_base_pnl = 0.0
    cum_cand_pnl = 0.0
    cum_base_bets = 0
    cum_cand_bets = 0
    prior_train: list[dict] = []

    log_rows = []

    for d in dates:
        rows_today = by_date[d]
        n_prior = len(prior_train)
        if n_prior >= MIN_TRAIN_N:
            cand_t1 = fit_lr(prior_train, cand_t1_feats, "t1", fipark,
                             phase_g=phase_g, fie=fie)
            cand_b1 = fit_lr(prior_train, cand_b1_feats, "b1", fipark,
                             phase_g=phase_g, fie=fie)
            mode = candidate_label
        else:
            # Fallback: candidate uses baseline weights until 2026 train pool
            # has at least MIN_TRAIN_N samples.  IMPORTANT: feature vectors
            # for these prediction calls MUST be 18-feature (e3) since the
            # baseline weights are 18-feature.  We compute base_t1v / base_b1v
            # below as the e3 vector; we use those for candidate too here.
            cand_t1 = baseline_t1
            cand_b1 = baseline_b1
            mode = "fallback"

        base_pnl_today = cand_pnl_today = 0.0
        base_bets_today = cand_bets_today = 0

        for r in rows_today:
            base_t1v = _build_vec(r, "t1", fipark, phase_g=False, fie=False)
            base_b1v = _build_vec(r, "b1", fipark, phase_g=False, fie=False)
            cand_t1v = _build_vec(r, "t1", fipark, phase_g=phase_g, fie=fie) \
                       if mode != "fallback" else base_t1v
            cand_b1v = _build_vec(r, "b1", fipark, phase_g=phase_g, fie=fie) \
                       if mode != "fallback" else base_b1v

            actual = (r.get("actual_result") or "").upper()
            lam = _coerce(r.get("combined_lambda"), 1.0)
            worst_pq = _pq_worst(r.get("away_pitcher_q",""), r.get("home_pitcher_q",""))

            # Baseline: V2.2 fixed + thin-pitcher demotion
            p_base = cal.predict(predict_pair(baseline_t1, baseline_b1,
                                              base_t1v, base_b1v))
            base_side, base_str = _classify(p_base, lam)
            if base_str == "STRONG" and base_side in ("NRFI", "YRFI") \
               and worst_pq not in THIN_PQ:
                base_bets_today += 1
                odds_col = "market_nrfi_odds" if base_side == "NRFI" else "market_yrfi_odds"
                if actual == base_side:
                    base_pnl_today += _payout(r.get(odds_col, ""))
                else:
                    base_pnl_today -= 1.0

            # Candidate: walk-forward (or fallback) + thin-pitcher demotion
            p_cand = cal.predict(predict_pair(cand_t1, cand_b1,
                                              cand_t1v, cand_b1v))
            cand_side, cand_str = _classify(p_cand, lam)
            if cand_str == "STRONG" and cand_side in ("NRFI", "YRFI") \
               and worst_pq not in THIN_PQ:
                cand_bets_today += 1
                odds_col = "market_nrfi_odds" if cand_side == "NRFI" else "market_yrfi_odds"
                if actual == cand_side:
                    cand_pnl_today += _payout(r.get(odds_col, ""))
                else:
                    cand_pnl_today -= 1.0

        cum_base_pnl += base_pnl_today
        cum_cand_pnl += cand_pnl_today
        cum_base_bets += base_bets_today
        cum_cand_bets += cand_bets_today

        print(f"  {d:<11}  {n_prior:>7}  {mode:<10}  "
              f"{base_bets_today:>9}  {base_pnl_today:>+9.3f}u  "
              f"{cand_bets_today:>9}  {cand_pnl_today:>+9.3f}u")
        log_rows.append({
            "date":         d,
            "train_n":      n_prior,
            "mode":         mode,
            "base_bets":    base_bets_today,
            "base_pnl":     f"{base_pnl_today:+.4f}",
            "cand_bets":    cand_bets_today,
            "cand_pnl":     f"{cand_pnl_today:+.4f}",
            "cum_base_pnl": f"{cum_base_pnl:+.4f}",
            "cum_cand_pnl": f"{cum_cand_pnl:+.4f}",
        })

        # Append today to the cumulative training pool for tomorrow.
        prior_train.extend(rows_today)

    # Persist day-by-day log
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(log_rows[0].keys()) if log_rows else [])
        w.writeheader()
        for row in log_rows:
            w.writerow(row)

    delta = cum_cand_pnl - cum_base_pnl
    gate_pass = delta >= -5.0

    print()
    print("=" * 78)
    print("  GATE B verdict")
    print("=" * 78)
    print(f"  baseline  (V2.2 fixed-model walk-forward)        : {cum_base_pnl:+.3f}u  ({cum_base_bets} bets)")
    print(f"  candidate ({candidate_label} walk-forward retrain): {cum_cand_pnl:+.3f}u  ({cum_cand_bets} bets)")
    print(f"  delta (candidate - baseline)                     : {delta:+.3f}u")
    print(f"  gate threshold (delta >= -5.0u)                  : {'PASS' if gate_pass else 'FAIL'}")
    print(f"  for reference -- V2.2 live P&L (pl_calc.py season): see `python tools/pl_calc.py --window season`")
    print()
    print(f"  Day-by-day log written: {log_path}")
    return 0 if gate_pass else 2


if __name__ == "__main__":
    sys.exit(main())

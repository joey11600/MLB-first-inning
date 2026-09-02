#!/usr/bin/env python3
"""park_shrinkage_refit.py -- does re-shrinking the park factor help v3?

THE QUESTION (operator-approved 2026-08-29).  data/fi_park_factors.json uses
PRIOR_GAMES = 50.  Measured on 2026-08-29 the park first-inning NRFI rate is
mostly noise -- year-over-year r = +0.13 across 30 parks, and the 2026 spread
(sd 7.4pp) is barely above the 6.2pp expected from coin-flip noise alone at
~66 games/park.  Two out-of-sample checks on the RAW RATE preferred a prior of
250-1000, i.e. shrink almost to the league mean.  But raising the prior alone
would halve a trained feature's contribution with no refit, so the honest test
is to REFIT the model at each prior and score it out of sample.

WHY THIS IS NOT A REPEAT.  tools/refit2026/harness.py already carried a
`park309` candidate and the README records the answer: "re-shrinking the park
factor moves AUC <=0.0002".  That ran against the PRE-v3 model -- 19 features,
L2 0.05, no pooled first-inning xwOBA.  v3 shipped 2026-08-23 with L2 0.5,
which compresses the raw output, so the feature's role could have changed.
This re-runs it on the shipped 20-feature set at the shipped L2.

PROTOCOL (CLAUDE.md + the feature_test_methodology memory)
  1. COVERAGE IS PRINTED FIRST.  A feature that silently collapses to a
     constant fails identically to one that does not work.
  2. All THREE splits: 2024->2025, 2025->2024, 2024+2025->2026.  2026 is the
     only season recorded at predict time, so it is the split that decides.
  3. Park factors are rebuilt INSIDE each split FROM TRAINING SEASONS ONLY.
     Never read data/fi_park_factors.json here -- it was built from
     picks_2026.csv and scores +0.690 in-sample against -0.057 out.
  4. Paired bootstrap CI on every delta, resampling GAMES.

Writes nothing.  Read-only validation, like everything else in this directory.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import two_stage_model as TSM          # noqa: E402
from lr_baseline import LogReg          # noqa: E402

BT = ROOT / "data" / "backtests"
F2024 = BT / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv"
F2025 = BT / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv"
F2026 = ROOT / "data" / "picks_2026.csv"

L2_PER_SAMPLE = 0.5                    # the shipped, validated setting
PARK_FEAT = "fi_park_nrfi_rate"

# Candidate priors. 50 is what ships. "flat" assigns every park the training
# league mean, the logical endpoint of "the feature is noise".
PRIORS = [("shipped K=50", 50.0), ("K=150", 150.0), ("K=250", 250.0),
          ("K=500", 500.0), ("flat (league mean)", float("inf"))]


def park_map_from(paths: list[Path], K: float) -> tuple[dict, float]:
    """Per-park shrunk first-inning NRFI rate from TRAINING FILES ONLY."""
    n: dict[str, int] = {}
    k: dict[str, int] = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
                if actual not in ("NRFI", "YRFI"):
                    continue
                home = r.get("home", "") or r.get("home_team", "")
                if not home:
                    continue
                n[home] = n.get(home, 0) + 1
                k[home] = k.get(home, 0) + (1 if actual == "NRFI" else 0)
    base = sum(k.values()) / sum(n.values())
    if K == float("inf"):
        return {p: base for p in n}, base
    return {p: (k[p] + K * base) / (n[p] + K) for p in n}, base


def block(path: Path, pmap: dict):
    return _flatten_ump(TSM.gather(path, pmap, phase_e3=True, phase_e3_vshand=True,
                                   fi_xwoba=True, ump_cache=UMPC, ump_rates_data=UMPR))


def _flatten_ump(blk: dict) -> dict:
    """Hold the umpire feature at the league constant in EVERY block.

    data/umpire_rates.json was rebuilt FLAT on 2026-08-29 (tau^2 <= 0: every
    umpire = the league rate), so the cached lookup returns one constant for
    all 2024/2025 rows while 2026 ledger rows still carry the per-umpire values
    stored before that date.  lr_baseline.LogReg standardises by std + 1e-9,
    so a constant training column turns the 2026 values into z-scores of
    ~1e7 and the 2026 split scores WORSE THAN CHANCE (AUC 0.489, log-loss
    0.726 on 2026-09-02).  Production feeds the flat value to every game from
    now on, so the honest validation input is the same constant everywhere.
    """
    for key, feats in (("X_t1", TSM.T1_PHASE_E3_VSHAND_FI_FEATURES),
                       ("X_b1", TSM.B1_PHASE_E3_VSHAND_FI_FEATURES)):
        if "home_plate_ump_nrfi_rate" in feats:
            blk[key][:, feats.index("home_plate_ump_nrfi_rate")] = TSM.LEAGUE_NRFI_RATE
    return blk


def stack(blocks, key):
    return np.vstack([b[key] for b in blocks])


def fit_pair(tr_blocks):
    Xt = stack(tr_blocks, "X_t1"); yt = np.concatenate([b["y_t1"] for b in tr_blocks])
    Xb = stack(tr_blocks, "X_b1"); yb = np.concatenate([b["y_b1"] for b in tr_blocks])
    l2 = L2_PER_SAMPLE * len(yt)       # sum-loss units; see two_stage_model L2 note
    return (LogReg.fit(Xt, yt, TSM.T1_PHASE_E3_VSHAND_FI_FEATURES, l2=l2),
            LogReg.fit(Xb, yb, TSM.B1_PHASE_E3_VSHAND_FI_FEATURES, l2=l2), len(yt))


def nrfi_probs(m_t1, m_b1, te):
    pt = m_t1.predict_proba(te["X_t1"])
    pb = m_b1.predict_proba(te["X_b1"])
    p_nrfi = (1.0 - pt) * (1.0 - pb)
    y_nrfi = ((te["y_t1"] == 0) & (te["y_b1"] == 0)).astype(float)
    return p_nrfi, y_nrfi


def auc(p, y):
    o = np.argsort(p); yr = y[o]
    r = np.empty(len(p), float); r[o] = np.arange(1, len(p) + 1)
    n1 = yr.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def logloss(p, y):
    q = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def q1_yrfi(p, y):
    """Bottom quintile by p_nrfi = the YRFI side the gate actually bets."""
    o = np.argsort(p); q = o[:len(p) // 5]
    return float((y[q] == 0).mean()), len(q)


def boot_delta(pa, pb, y, n=2000, seed=11):
    """Paired bootstrap over GAMES of Brier(cand) - Brier(shipped)."""
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    idx = np.arange(len(y))
    for i in range(n):
        s = rng.choice(idx, len(idx), replace=True)
        out[i] = brier(pb[s], y[s]) - brier(pa[s], y[s])
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5)), float(out.mean())


UMPC, UMPR = None, None


def main():
    global UMPC, UMPR
    UMPC, UMPR = TSM.load_ump_data() if hasattr(TSM, "load_ump_data") else (None, None)
    if UMPC is None:
        try:
            import mlb_first_inning_predictor as P
            UMPC, UMPR = P._load_ump_data()
        except Exception:
            UMPC, UMPR = {}, {}

    for f in (F2024, F2025, F2026):
        if not f.exists():
            sys.exit(f"missing input: {f}")

    splits = [("2024 -> 2025", [F2024], F2025),
              ("2025 -> 2024", [F2025], F2024),
              ("2024+2025 -> 2026", [F2024, F2025], F2026)]

    # ---------------- 1. COVERAGE FIRST ----------------
    print("=" * 78)
    print("  COVERAGE  (no coverage line, no result)")
    print("=" * 78)
    for name, trs, te in splits:
        pmap, base = park_map_from(trs, 50.0)
        teb = block(te, pmap)
        i = TSM.T1_PHASE_E3_VSHAND_FI_FEATURES.index(PARK_FEAT)
        col = teb["X_t1"][:, i]
        j = TSM.T1_PHASE_E3_VSHAND_FI_FEATURES.index("home_fi_xwoba")
        fx = teb["X_t1"][:, j]
        print(f"  {name:<20} test n={len(teb['y_t1']):>5}  "
              f"{PARK_FEAT}: {len(np.unique(col)):>3} distinct, "
              f"sd={col.std():.4f}, {int((col == base).sum())} at league mean")
        print(f"  {'':<20} parks in train map: {len(pmap):>3}   "
              f"home_fi_xwoba: {len(np.unique(fx)):>4} distinct, sd={fx.std():.4f}")
    print()

    # ---------------- 2. THREE SPLITS x PRIORS ----------------
    results = {}
    for name, trs, te in splits:
        print("=" * 78)
        print(f"  SPLIT {name}")
        print("=" * 78)
        print(f"  {'park prior':<20}{'AUC':>9}{'Brier':>10}{'logloss':>10}"
              f"{'Q1 YRFI hit':>13}{'|w| park':>10}")
        print("  " + "-" * 72)
        base_p = None
        for label, K in PRIORS:
            pmap, _ = park_map_from(trs, K)
            trb = [block(p, pmap) for p in trs]
            teb = block(te, pmap)
            m1, m2, ntr = fit_pair(trb)
            p, y = nrfi_probs(m1, m2, teb)
            i = TSM.T1_PHASE_E3_VSHAND_FI_FEATURES.index(PARK_FEAT)
            wpark = abs(m1.w[i])
            hit, nq = q1_yrfi(p, y)
            print(f"  {label:<20}{auc(p, y):>9.4f}{brier(p, y):>10.5f}"
                  f"{logloss(p, y):>10.5f}{hit*100:>12.1f}%{wpark:>10.5f}")
            results[(name, label)] = (p, y)
            if K == 50.0:
                base_p = p
        # paired bootstrap vs shipped
        print(f"\n  paired bootstrap, Brier delta vs shipped K=50 (negative = better):")
        for label, K in PRIORS:
            if K == 50.0:
                continue
            p, y = results[(name, label)]
            lo, hi, mu = boot_delta(base_p, p, y)
            sig = "" if lo < 0 < hi else "   <-- CI excludes zero"
            print(f"    {label:<20} {mu:>+9.6f}   95% CI [{lo:>+.6f}, {hi:>+.6f}]{sig}")
        print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Candidate-factor sweep: every unused column already in the files, tested for
INCREMENTAL value on top of the full shipped feature set, three splits,
selection-aware permutation null.

Asked for by the operator 2026-08-21 ("look at other certain stats that could
possibly help predict better").  The columns below are present in the
2024/2025 `_ptfix` backtests AND the 2026 ledger but are NOT model features.

METHOD (per feature_test_methodology -- all five rules):
  1. COVERAGE printed per split before any result.
  2. All THREE splits (2024->2025, 2025->2024, 24+25->2026).
  3. Test = does adding z(candidate) to logit(p_model) improve TEST logloss,
     where p_model is the two-stage model REFIT on the train split (so the
     candidate must add to the full current feature set, out of sample).
  4. Survivor rule: improves in ALL THREE splits.
  5. SELECTION-AWARE NULL: permute every candidate column, rerun the entire
     sweep, record survivor count and best mean improvement.  300+ trials.
     A result only counts if it beats what the search finds in noise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import build_park, fit_lr, load, matrix, predict, T1_SHIPPED, B1_SHIPPED  # noqa: E402


def s(d, c):
    return pd.to_numeric(d.get(c), errors="coerce")


CANDS = {
    # strikeouts / walks / power allowed -- season rates the model skips
    "k9_sum":      lambda d: s(d, "home_k9") + s(d, "away_k9"),
    "bb9_sum":     lambda d: s(d, "home_bb9") + s(d, "away_bb9"),
    "hr9_sum":     lambda d: s(d, "home_hr9") + s(d, "away_hr9"),
    "whip_sum":    lambda d: s(d, "home_whip") + s(d, "away_whip"),
    "era_sum":     lambda d: s(d, "home_era") + s(d, "away_era"),
    # team-level scoring environment
    "rpg_sum":     lambda d: s(d, "home_rpg") + s(d, "away_rpg"),
    "slg_sum":     lambda d: s(d, "home_slg") + s(d, "away_slg"),
    # the repo's own composite quality scores, never fed to the LR
    "pitcher_q_sum": lambda d: s(d, "home_pitcher_q") + s(d, "away_pitcher_q"),
    "batting_q_sum": lambda d: s(d, "home_batting_q") + s(d, "away_batting_q"),
    # handedness structure
    "n_lefty_starters": lambda d: (d.home_pitcher_throws_hand.astype(str).str.upper()
                                   .eq("L").astype(float)
                                   + d.away_pitcher_throws_hand.astype(str).str.upper()
                                   .eq("L").astype(float)),
    # mismatch shapes the per-half model can under-express at game level
    "k9_min":      lambda d: np.minimum(s(d, "home_k9"), s(d, "away_k9")),
    "era_max":     lambda d: np.maximum(s(d, "home_era"), s(d, "away_era")),
}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sig(z):
    return 1 / (1 + np.exp(-z))


def fit2(X, y, l2=1e-4, iters=200):
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1]); R = np.eye(X.shape[1]) * l2; R[0, 0] = 0
    for _ in range(iters):
        p = sig(X @ w)
        g = X.T @ (y - p) / len(y) - R @ w
        H = (X * (p*(1-p))[:, None]).T @ X / len(y) + R + 1e-9*np.eye(X.shape[1])
        st = np.linalg.solve(H, g); w += st
        if np.max(np.abs(st)) < 1e-10:
            break
    return w


def logloss(y, p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y*np.log(p) + (1-y)*np.log(1-p)))


def base_probs(tr, te):
    """Two-stage shipped feature set refit on train; returns p_yrfi for both."""
    park, base = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, T1_SHIPPED, park, base), tr.y_t1.values, 0.05)
    wb, mb, sb = fit_lr(matrix(tr, B1_SHIPPED, park, base), tr.y_b1.values, 0.05)
    def p(d):
        pt = predict(wt, mt, st, matrix(d, T1_SHIPPED, park, base))
        pb = predict(wb, mb, sb, matrix(d, B1_SHIPPED, park, base))
        return 1 - (1-pt)*(1-pb)
    return p(tr), p(te)


def run_sweep(splits, cand_cols, rng=None):
    """cand_cols: {name: {split_label: (tr_vals, te_vals)}} -- possibly permuted."""
    res = {}
    for name in cand_cols:
        deltas = []
        for lab, (ytr, yte, btr, bte) in splits.items():
            xtr, xte = cand_cols[name][lab]
            m_tr = ~np.isnan(xtr); m_te = ~np.isnan(xte)
            if m_tr.mean() < 0.7 or m_te.mean() < 0.7:
                deltas = None; break
            mu, sd = xtr[m_tr].mean(), xtr[m_tr].std() or 1.0
            ztr = np.where(m_tr, (xtr-mu)/sd, 0.0)
            zte = np.where(m_te, (xte-mu)/sd, 0.0)
            w0 = fit2(btr.reshape(-1, 1), ytr)
            w1 = fit2(np.c_[btr, ztr], ytr)
            l0 = logloss(yte, sig(np.c_[np.ones(len(bte)), bte] @ w0))
            l1 = logloss(yte, sig(np.c_[np.ones(len(bte)), bte, zte] @ w1))
            deltas.append(l0 - l1)          # positive = candidate helped
        if deltas is not None:
            res[name] = deltas
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    bt = ROOT / "data" / "backtests"
    d24 = load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024)
    d25 = load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025)
    d26 = load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)

    print("=== COVERAGE (rule 1) -- % non-null per candidate per season ===")
    print(f"  {'candidate':<20} {'2024':>7} {'2025':>7} {'2026':>7}")
    keep = []
    for name, fn in CANDS.items():
        cov = [fn(d).notna().mean()*100 for d in (d24, d25, d26)]
        flag = "" if min(cov) >= 70 else "   <- EXCLUDED (coverage)"
        print(f"  {name:<20} {cov[0]:>6.1f}% {cov[1]:>6.1f}% {cov[2]:>6.1f}%{flag}")
        if min(cov) >= 70:
            keep.append(name)

    defs = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
            ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    splits, cand_cols = {}, {n: {} for n in keep}
    for lab, tr, te in defs:
        ptr, pte = base_probs(tr, te)
        splits[lab] = (tr.y.values, te.y.values, logit(ptr), logit(pte))
        for n in keep:
            cand_cols[n][lab] = (CANDS[n](tr).values.astype(float),
                                 CANDS[n](te).values.astype(float))

    obs = run_sweep(splits, cand_cols)
    print("\n=== OBSERVED (rules 2-4): test-logloss improvement x1000, per split ===")
    print(f"  {'candidate':<20} {'24->25':>9} {'25->24':>9} {'->2026':>9}   all 3?")
    survivors = []
    for n, dl in sorted(obs.items(), key=lambda kv: -sum(kv[1])):
        ok = all(v > 0 for v in dl)
        if ok:
            survivors.append(n)
        print(f"  {n:<20} " + " ".join(f"{v*1000:>+9.3f}" for v in dl)
              + f"   {'YES' if ok else 'no'}")
    print(f"\n  survivors (positive in all three): {survivors or 'NONE'}")

    print(f"\n=== SELECTION-AWARE NULL (rule 5): {args.trials} trials, full sweep on permuted columns ===")
    n_surv, best_mean = [], []
    for _ in range(args.trials):
        perm = {n: {} for n in keep}
        for n in keep:
            for lab in splits:
                xtr, xte = cand_cols[n][lab]
                perm[n][lab] = (xtr[rng.permutation(len(xtr))],
                                xte[rng.permutation(len(xte))])
        r = run_sweep(splits, perm)
        sv = [k for k, dl in r.items() if all(v > 0 for v in dl)]
        n_surv.append(len(sv))
        best_mean.append(max((np.mean(dl) for dl in r.values()), default=0.0))
    n_surv = np.array(n_surv); best_mean = np.array(best_mean)
    print(f"  survivors in NOISE: mean {n_surv.mean():.2f}  median {np.median(n_surv):.0f}  "
          f"P(>= {max(len(survivors),1)}) = {(n_surv >= max(len(survivors),1)).mean():.3f}")
    obs_best = max((np.mean(dl) for dl in obs.values()), default=0.0)
    print(f"  best mean improvement in NOISE: {np.mean(best_mean)*1000:.3f} x1000  "
          f"90th pct {np.percentile(best_mean, 90)*1000:.3f}")
    print(f"  observed best mean improvement: {obs_best*1000:.3f} x1000  "
          f"->  selection-aware p = {(best_mean >= obs_best).mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

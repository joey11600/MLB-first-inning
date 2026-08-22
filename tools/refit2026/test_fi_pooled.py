#!/usr/bin/env python3
"""
Test the pooled first-inning pitcher factors (build_fi_pitcher_pooled.py)
under the full protocol: coverage, three splits, incremental over the refit
shipped model, selection-aware permutation null -- and a per-half REFIT
(home pitcher's value -> T1, away pitcher's -> B1), because the rpg_sum case
showed a game-level stacking survivor can still fail as a real feature.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load,  # noqa: E402
                     logloss, matrix, predict)
from candidate_factors import base_probs, logit, run_sweep  # noqa: E402


def attach(d: pd.DataFrame, fac: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
    f = fac.copy(); f["game_pk"] = pd.to_numeric(f["game_pk"], errors="coerce")
    f = f.drop(columns=["date"]).drop_duplicates("game_pk")
    return d.merge(f, on="game_pk", how="left")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    d26 = attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fac)

    metrics = ["fi_xwoba", "fi_k", "fi_bb", "fi_csw"]
    CANDS = {}
    for m in metrics:
        CANDS[f"{m}_sum"] = (lambda mm: lambda d: d[f"home_{mm}"] + d[f"away_{mm}"])(m)
        CANDS[f"{m}_max"] = (lambda mm: lambda d: np.maximum(d[f"home_{mm}"], d[f"away_{mm}"]))(m)

    print("=== COVERAGE (rule 1) ===")
    keep = []
    for n, fn in CANDS.items():
        cov = [fn(d).notna().mean() * 100 for d in (d24, d25, d26)]
        print(f"  {n:<14} {cov[0]:6.1f}% {cov[1]:6.1f}% {cov[2]:6.1f}%")
        if min(cov) >= 70:
            keep.append(n)
    # how much real variation is there? (a near-constant feature is a fake null)
    for m in metrics:
        v = pd.concat([d24, d25, d26])[f"home_{m}"]
        print(f"  home_{m:<9} mean {v.mean():.4f}  sd {v.std():.4f}  distinct {v.nunique()}")

    defs = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
            ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    splits, cols = {}, {n: {} for n in keep}
    for lab, tr, te in defs:
        ptr, pte = base_probs(tr, te)
        splits[lab] = (tr.y.values, te.y.values, logit(ptr), logit(pte))
        for n in keep:
            cols[n][lab] = (CANDS[n](tr).values.astype(float), CANDS[n](te).values.astype(float))

    obs = run_sweep(splits, cols)
    print("\n=== STACKING TEST: test-logloss improvement x1000 per split ===")
    surv = []
    for n, dl in sorted(obs.items(), key=lambda kv: -sum(kv[1])):
        ok = all(v > 0 for v in dl); surv += [n] if ok else []
        print(f"  {n:<14} " + " ".join(f"{v*1000:>+8.3f}" for v in dl) + f"   {'YES' if ok else 'no'}")
    print(f"  survivors: {surv or 'NONE'}")
    n_surv, best = [], []
    for _ in range(args.trials):
        perm = {n: {lab: (x[rng.permutation(len(x))], z[rng.permutation(len(z))])
                    for lab, (x, z) in cols[n].items()} for n in keep}
        r = run_sweep(splits, perm)
        n_surv.append(sum(all(v > 0 for v in dl) for dl in r.values()))
        best.append(max((np.mean(dl) for dl in r.values()), default=0.0))
    n_surv, best = np.array(n_surv), np.array(best)
    ob = max((np.mean(dl) for dl in obs.values()), default=0.0)
    print(f"  NULL: survivors mean {n_surv.mean():.2f}, P(>= {max(len(surv),1)}) = "
          f"{(n_surv >= max(len(surv),1)).mean():.3f};  best-mean in noise {best.mean()*1000:.3f} "
          f"(90th {np.percentile(best,90)*1000:.3f});  observed best {ob*1000:.3f}  "
          f"-> p = {(best >= ob).mean():.3f}")

    print("\n=== PER-HALF REFIT: home pitcher's value -> T1, away's -> B1 ===")
    for m in metrics:
        T1 = T1_SHIPPED + [f"home_{m}"]; B1 = B1_SHIPPED + [f"away_{m}"]
        out = []
        for lab, tr, te in defs:
            tr2, te2 = tr.copy(), te.copy()
            for d in (tr2, te2):         # impute the few blanks at the train mean
                for c in (f"home_{m}", f"away_{m}"):
                    d[c] = d[c].fillna(tr[c].mean())
            y = te2.y.values
            def run(t1f, b1f):
                pk, b0 = build_park(tr2, 50)
                wt, mt, st = fit_lr(matrix(tr2, t1f, pk, b0), tr2.y_t1.values, 0.05)
                wb, mb, sb = fit_lr(matrix(tr2, b1f, pk, b0), tr2.y_b1.values, 0.05)
                return 1 - (1 - predict(wt, mt, st, matrix(te2, t1f, pk, b0))) * \
                           (1 - predict(wb, mb, sb, matrix(te2, b1f, pk, b0)))
            p0, p1 = run(T1_SHIPPED, B1_SHIPPED), run(T1, B1)
            d = np.array([logloss(y[i], p0[i]) - logloss(y[i], p1[i])
                          for i in (rng.integers(0, len(y), len(y)) for _ in range(600))])
            out.append(f"{lab}: dAUC {auc(y,p1)-auc(y,p0):+.4f} dll {np.mean(d)*1000:+.3f} "
                       f"[{np.percentile(d,5)*1000:+.3f},{np.percentile(d,95)*1000:+.3f}]")
        print(f"  {m}:"); [print("     " + s) for s in out]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

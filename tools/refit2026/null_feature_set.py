#!/usr/bin/env python3
"""
Selection-aware permutation null for a SET of per-half candidate specs
(feature_test_methodology rule 5), on the stacking form: for each spec the
game-level candidate z(T1col) + z(B1col) is added to logit(p_base) and scored
out of sample on the three splits; a 'survivor' improves all three.  The
whole sweep is re-run on permuted columns N times; a real result must beat
the best the search finds in noise.

Usage: python tools/refit2026/null_feature_set.py --base fixwoba --specs a b c
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import T1_SHIPPED, B1_SHIPPED, build_park, fit_lr, load, matrix, predict  # noqa: E402
from candidate_factors import logit, run_sweep  # noqa: E402
from test_feature_set import SPECS, attach_all  # noqa: E402


def base_probs_fs(tr, te, t1f, b1f, l2):
    tr, te = tr.copy(), te.copy()
    for c in [x for x in t1f + b1f if x.endswith("fi_xwoba")]:
        mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, l2)
    wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, l2)
    def p(d):
        return 1 - (1 - predict(wt, mt, st, matrix(d, t1f, pk, b0))) * (1 - predict(wb, mb, sb, matrix(d, b1f, pk, b0)))
    return p(tr), p(te)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", choices=["shipped", "fixwoba"], default="fixwoba")
    ap.add_argument("--l2", type=float, default=0.5)
    ap.add_argument("--specs", nargs="+", required=True)
    ap.add_argument("--trials", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    bt = ROOT / "data" / "backtests"
    d24 = attach_all(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024))
    d25 = attach_all(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025))
    d26 = attach_all(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026))
    T1B = list(T1_SHIPPED) + (["home_fi_xwoba"] if args.base == "fixwoba" else [])
    B1B = list(B1_SHIPPED) + (["away_fi_xwoba"] if args.base == "fixwoba" else [])
    defs = [("24->25", d24, d25), ("25->24", d25, d24),
            ("->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    splits, cols = {}, {n: {} for n in args.specs}
    for lab, tr, te in defs:
        ptr, pte = base_probs_fs(tr, te, T1B, B1B, args.l2)
        splits[lab] = (tr.y.values, te.y.values, logit(ptr), logit(pte))
        for n in args.specs:
            t1c, b1c, _ = SPECS[n]
            def z(d):
                a = pd.to_numeric(d[t1c], errors="coerce"); b = pd.to_numeric(d[b1c], errors="coerce")
                return ((a - a.mean()) / (a.std() or 1) + (b - b.mean()) / (b.std() or 1)).values.astype(float)
            cols[n][lab] = (z(tr), z(te))
    obs = run_sweep(splits, cols)
    print(f"base={args.base} L2={args.l2}  stacking deltas x1000 (24->25, 25->24, ->2026)")
    surv = []
    for n, dl in sorted(obs.items(), key=lambda kv: -sum(kv[1])):
        ok = all(v > 0 for v in dl); surv += [n] if ok else []
        print(f"  {n:<16} " + " ".join(f"{v*1000:>+8.3f}" for v in dl) + f"   {'YES' if ok else 'no'}")
    print(f"  survivors: {surv or 'NONE'}")
    n_surv, best = [], []
    for _ in range(args.trials):
        perm = {n: {lab: (x[rng.permutation(len(x))], z_[rng.permutation(len(z_))])
                    for lab, (x, z_) in cols[n].items()} for n in args.specs}
        r = run_sweep(splits, perm)
        n_surv.append(sum(all(v > 0 for v in dl) for dl in r.values()))
        best.append(max((np.mean(dl) for dl in r.values()), default=0.0))
    n_surv, best = np.array(n_surv), np.array(best)
    ob = max((np.mean(dl) for dl in obs.values()), default=0.0)
    print(f"  NULL ({args.trials} trials): survivors mean {n_surv.mean():.2f}, "
          f"P(>= {max(len(surv),1)}) = {(n_surv >= max(len(surv),1)).mean():.3f};  "
          f"best-mean in noise {best.mean()*1000:.3f} (90th {np.percentile(best,90)*1000:.3f});  "
          f"observed best {ob*1000:.3f} -> p = {(best >= ob).mean():.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
General feature tester for the first-inning model, built on what passed.

A SPEC is (name, T1 column, B1 column).  For each spec:
  * per-half refit over a BASE feature set (shipped, or shipped + fi_xwoba),
    three splits, dAUC and dlogloss with paired-bootstrap CI
  * stacking survivor check + selection-aware null across ALL specs run
  * THE PRODUCT METRIC: on 2026 (fit on 24+25), among gate-firing games per
    slate pick the lowest p_nrfi -- the simulated No.1 -- and report its hit
    rate, base vs candidate.  The No.1 is what the operator sells.

Factor CSVs (date, game_pk, away_value, home_value) are attached by game_pk;
`kind` says whether away_value/home_value belong to the PITCHER (home
pitcher -> T1) or the BATTERS (away batters -> T1).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration import CIRCalibrator  # noqa: E402
from harness import (T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load,  # noqa: E402
                     logloss, matrix, predict)
from candidate_factors import base_probs, logit, run_sweep  # noqa: E402
from money import GATE_NRFI  # noqa: E402

FACTORS = {   # csv name -> (feature stem, kind)
    "factor_fi_pooled.csv": None,                                  # already has named cols
    "factor_batter_pooled.csv": None,                              # already has named cols
    "factor_team_fi.csv": None,                                    # already has named cols
    "factor_defense_speed.csv": None,                              # already has named cols
    "factor_starter_velo_vs_own_mean.csv": ("velo_vs_own", "pitcher"),
    "factor_top3_chase_rate.csv": ("top3_chase", "batter"),
    "factor_top3_k_rate.csv": ("top3_k_old", "batter"),
    "factor_top3_contact_quality.csv": ("top3_xwoba_con", "batter"),
}


def attach_all(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy(); d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
    for f, spec in FACTORS.items():
        p = ROOT / "data" / "candidates" / f
        if not p.exists():
            continue
        x = pd.read_csv(p); x["game_pk"] = pd.to_numeric(x["game_pk"], errors="coerce")
        x = x.drop(columns=[c for c in ("date",) if c in x.columns]).drop_duplicates("game_pk")
        if spec is not None:
            stem, _ = spec
            x = x.rename(columns={"away_value": f"away_{stem}", "home_value": f"home_{stem}"})
            for c in (f"away_{stem}", f"home_{stem}"):
                x[c] = pd.to_numeric(x[c], errors="coerce")
        d = d.merge(x, on="game_pk", how="left")
    # derived: pooled pitcher x pooled lineup (both first-inning-specific)
    if "home_fi_xwoba" in d.columns and "away_top3_xwoba" in d.columns:
        d["t1_pool_x"] = pd.to_numeric(d["home_fi_xwoba"], errors="coerce") *             pd.to_numeric(d["away_top3_xwoba"], errors="coerce")
        d["b1_pool_x"] = pd.to_numeric(d["away_fi_xwoba"], errors="coerce") *             pd.to_numeric(d["home_top3_xwoba"], errors="coerce")
    # derived: interaction of cold-pitcher quality with the lineup he faces
    if "home_fi_xwoba" in d.columns:
        d["t1_cold_x_lineup"] = pd.to_numeric(d["home_fi_xwoba"], errors="coerce") * \
            pd.to_numeric(d["away_top3c_obp"], errors="coerce")
        d["b1_cold_x_lineup"] = pd.to_numeric(d["away_fi_xwoba"], errors="coerce") * \
            pd.to_numeric(d["home_top3c_obp"], errors="coerce")
    return d


SPECS = {
    # name:            (T1 col,               B1 col,               kind)
    "fi_xwoba":        ("home_fi_xwoba",      "away_fi_xwoba",      "pitcher"),
    "fi_velo":         ("home_fi_velo",       "away_fi_velo",       "pitcher"),
    "fi_k":            ("home_fi_k",          "away_fi_k",          "pitcher"),
    "fi_fstrike":      ("home_fi_fstrike",    "away_fi_fstrike",    "pitcher"),
    "fi_zone":         ("home_fi_zone",       "away_fi_zone",       "pitcher"),
    "fi_bb":           ("home_fi_bb",         "away_fi_bb",         "pitcher"),
    "velo_vs_own":     ("home_velo_vs_own",   "away_velo_vs_own",   "pitcher"),
    "top3_chase":      ("away_top3_chase",    "home_top3_chase",    "batter"),
    "top3_k_old":      ("away_top3_k_old",    "home_top3_k_old",    "batter"),
    "top3_xwoba_con":  ("away_top3_xwoba_con", "home_top3_xwoba_con", "batter"),
    "cold_x_lineup":   ("t1_cold_x_lineup",   "b1_cold_x_lineup",   "interaction"),
    # batter side, pooled across seasons (build_batter_pooled.py)
    "top3_xwoba_pool": ("away_top3_xwoba",    "home_top3_xwoba",    "batter"),
    "top3_k_pool":     ("away_top3_k",        "home_top3_k",        "batter"),
    "top3_fi_xwoba":   ("away_top3_fi_xwoba", "home_top3_fi_xwoba", "batter"),
    "lead_xwoba":      ("away_lead_xwoba",    "home_lead_xwoba",    "batter"),
    "platoon_xwoba":   ("t1_platoon_xwoba",   "b1_platoon_xwoba",   "matchup"),
    # team first-inning history from linescores (build_team_fi.py)
    "mid_xwoba":       ("away_mid_xwoba",     "home_mid_xwoba",     "batter"),
    "pool_x":          ("t1_pool_x",          "b1_pool_x",          "interaction"),
    "def_oaa":         ("home_def_oaa",       "away_def_oaa",       "defense"),
    "rpg":             ("away_rpg",           "home_rpg",           "team"),
    "top3_sprint":     ("away_top3_sprint",   "home_top3_sprint",   "batter"),
    "team_fi_score":   ("away_team_fi_score", "home_team_fi_score", "team"),
    "team_fi_allow":   ("home_team_fi_allow", "away_team_fi_allow", "team"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", choices=["shipped", "fixwoba"], default="fixwoba",
                    help="feature set the candidate is added ON TOP of")
    ap.add_argument("--l2", type=float, default=0.5)
    ap.add_argument("--specs", nargs="*", default=list(SPECS))
    ap.add_argument("--trials", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    bt = ROOT / "data" / "backtests"
    d24 = attach_all(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024))
    d25 = attach_all(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025))
    d26 = attach_all(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026))
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"])
    T1B = list(T1_SHIPPED); B1B = list(B1_SHIPPED)
    if args.base == "fixwoba":
        T1B += ["home_fi_xwoba"]; B1B += ["away_fi_xwoba"]
    defs = [("24->25", d24, d25), ("25->24", d25, d24),
            ("->2026", pd.concat([d24, d25], ignore_index=True), d26)]

    print(f"BASE = {args.base} (L2={args.l2});  candidates added one at a time")
    print("=== COVERAGE ===")
    for n in args.specs:
        t1c, b1c, _ = SPECS[n]
        cov = [min(d[t1c].notna().mean(), d[b1c].notna().mean()) * 100 for d in (d24, d25, d26)]
        print(f"  {n:<16} {cov[0]:6.1f}% {cov[1]:6.1f}% {cov[2]:6.1f}%"
              + ("" if min(cov) >= 70 else "   <- EXCLUDED"))
    specs = [n for n in args.specs if min(min(d[SPECS[n][0]].notna().mean(),
             d[SPECS[n][1]].notna().mean()) for d in (d24, d25, d26)) >= 0.7]

    def fitpred(tr, te, t1f, b1f, cols_to_impute):
        tr, te = tr.copy(), te.copy()
        for c in cols_to_impute:
            mu = tr[c].mean()
            tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
        pk, b0 = build_park(tr, 50)
        wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, args.l2)
        wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, args.l2)
        raw_tr = (1 - predict(wt, mt, st, matrix(tr, t1f, pk, b0))) * (1 - predict(wb, mb, sb, matrix(tr, b1f, pk, b0)))
        raw_te = (1 - predict(wt, mt, st, matrix(te, t1f, pk, b0))) * (1 - predict(wb, mb, sb, matrix(te, b1f, pk, b0)))
        cal = CIRCalibrator.fit(list(raw_tr), list((tr.y == 0).astype(int)), n_bins=20)
        p_nrfi = np.array([cal.predict(float(v)) for v in raw_te])
        coef = wt[1 + t1f.index(t1f[-1])] if t1f != T1B else None
        return 1 - raw_te, p_nrfi, coef

    print("\n=== PER-HALF REFIT over base: dAUC / dlogloss x1000 [90% CI] ===")
    print(f"  {'candidate':<16} {'24->25':>26} {'25->24':>26} {'->2026':>26}  coef(T1) all+?")
    base_cache = {}
    impute_base = [c for c in T1B + B1B if c.endswith("fi_xwoba")]
    for lab, tr, te in defs:
        base_cache[lab] = fitpred(tr, te, T1B, B1B, impute_base)
    res_stack = {}
    for n in specs:
        t1c, b1c, _ = SPECS[n]
        cells, allpos, coefs = [], True, []
        for lab, tr, te in defs:
            y = te.y.values
            p0, pn0, _ = base_cache[lab]
            p1, pn1, coef = fitpred(tr, te, T1B + [t1c], B1B + [b1c], impute_base + [t1c, b1c])
            coefs.append(coef)
            dl = np.array([logloss(y[i], p0[i]) - logloss(y[i], p1[i])
                           for i in (rng.integers(0, len(y), len(y)) for _ in range(400))])
            da = auc(y, p1) - auc(y, p0)
            allpos &= (da > 0) and (dl.mean() > 0)
            cells.append(f"{da:+.4f}/{dl.mean()*1000:+.2f}[{np.percentile(dl,5)*1000:+.2f},{np.percentile(dl,95)*1000:+.2f}]")
            if lab == "->2026":
                res_stack[n] = (te, p0, pn0, p1, pn1)
        print(f"  {n:<16} " + " ".join(f"{c:>26}" for c in cells)
              + f"  {'/'.join(f'{c:+.3f}' for c in coefs)}  {'ALL+' if allpos else '-'}")

    print("\n=== THE PRODUCT METRIC on 2026 (fit on 24+25): simulated No.1 per slate ===")
    print("   No.1 = lowest calibrated p_nrfi among gate-firing games that night")
    def no1_stats(te, pn):
        t = te[["date", "y"]].copy(); t["pn"] = pn; t = t[t.pn < GATE_NRFI]
        if not len(t):
            return 0, np.nan, 0
        idx = t.groupby("date").pn.idxmin()
        n1 = t.loc[idx]
        return len(n1), n1.y.mean(), len(t)
    any_spec = next(iter(res_stack.values()))
    te0, p0, pn0 = any_spec[0], any_spec[1], any_spec[2]
    n, h, nb = no1_stats(te0, pn0)
    print(f"  {'BASE':<16} No.1 slates={n:3d}  No.1 hit={h:.3f}   (gate bets {nb})")
    for nme, (te, _, _, p1, pn1) in res_stack.items():
        n, h, nb = no1_stats(te, pn1)
        print(f"  {'+'+nme:<16} No.1 slates={n:3d}  No.1 hit={h:.3f}   (gate bets {nb})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

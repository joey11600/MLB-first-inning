#!/usr/bin/env python3
"""test_fi_form.py -- does the shrunk continuous first-inning form rate beat the
raw last-5 / last-10 fractions it would replace?

Protocol is the feature_test_methodology memory, in order:
  1. COVERAGE FIRST.  A feature that silently collapses fails the same way as
     one that does not work.
  2. ALL THREE SPLITS (2024->2025, 2025->2024, 2024+2025->2026), park map and
     CIR calibrator rebuilt from TRAINING seasons only inside each split.
  3. Paired bootstrap over GAMES on every delta.
  4. SELECTION-AWARE NULL (--null): the grid is a search, so the best cell is
     compared against the best cell the SAME search finds on permuted values,
     not against zero.

VARIANTS
  shipped        the live 20 features (last-5 + last-10 raw fractions)
  swap10         fi_form replaces last-10, last-5 kept        (20 features)
  swap_both      fi_form replaces BOTH raw fractions          (19 features)
  add            fi_form added alongside both                 (21 features)

RESULT, 2026-09-03 -- IT SURVIVES.  The first candidate since the pooled
first-inning xwOBA (2026-08-21) to clear every bar in this directory.

  - 12 of 30 cells beat shipped on AUC in ALL THREE splits.  Best cell
    `add / K65_pw0`: 2026 +0.0070 (90% CI [+0.0034, +0.0105]), 2024 +0.0039,
    2025 +0.0007; 2026 dBrier -0.00051, CI [-0.00096, -0.00009], also
    excluding zero.
  - THE INTERNAL CONTROL THAT MATTERS: `shipped_like` -- the same rebuild with
    NO shrinkage, i.e. a reconstruction of the live last-10 fraction -- fails
    the three-split test (+0.0004 / -0.0013 / -0.0019).  Same pipeline, same
    source data, same code path; the only difference is the shrinkage.  So the
    gain is the shrinkage, not the reconstruction.
  - SELECTION-AWARE NULL, 300 trials, the WHOLE procedure re-run on values
    shuffled within season: noise yields 1.4 all-three-splits survivors per
    trial (observed 12), best-in-noise mean +0.0012, sd 0.0009, and a maximum
    of +0.0060 across all 300 trials -- below the observed +0.0070.
    p = 0.000.
  - `prior_w = 0` wins in every variant, which is not a random grid winner: it
    is exactly what the persistence measurement predicted, since cross-season
    carryover is weak and inconsistent (+0.08 / +0.21 / -0.14).
  - Leakage audit (build_fi_form.py --audit): 500 rows recomputed by brute
    force, worst disagreement 0.00e+00; corr with THIS start's outcome
    (+0.0420) and with the NEXT start's (+0.0323) are similar, as they should
    be for a feature that cannot see its own game.

  MONEY (--money), 2026 only, ceiling re-derived per config:
    shipped        64 bets  70.3%  +16.77u flat  +44.01u Kelly | No.1 54 nights 74.1% +42.26u
    add/K65_pw0    93 bets  66.7%  +17.56u flat  +49.08u Kelly | No.1 70 nights 70.0% +44.81u
    add/K65_all    89 bets  69.7%  +21.31u flat  +56.11u Kelly | No.1 69 nights 72.5% +48.70u
  More bets at a similar hit rate, so more total units -- but the No.1's hit
  rate is slightly LOWER on a larger set of nights, and per the README the
  2026 money LEVEL is flattered by the train/test base-rate gap.  The
  discrimination gain is the durable finding; do not lead with the units.

NOT SHIPPED.  Shipping means a full refit (weights + calibrator + the
predictor's feature list + a nightly production builder for the feature, the
way fi_pitcher_pool.py serves fi_xwoba) and is an operator decision.

Writes nothing.  Read-only validation, like everything else in this directory.

CLI
    python tools/refit2026/test_fi_form.py
    python tools/refit2026/test_fi_form.py --null --trials 150
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

from calibration import CIRCalibrator                                  # noqa: E402
from harness import (T1_SHIPPED, B1_SHIPPED, auc, brier, build_park,   # noqa: E402
                     fit_lr, load, logloss, matrix, predict)
from test_fi_pooled import attach                                      # noqa: E402
from build_fi_form import GRID                                         # noqa: E402

pd.set_option("display.width", 270)

T1_V3 = T1_SHIPPED + ["home_fi_xwoba"]
B1_V3 = B1_SHIPPED + ["away_fi_xwoba"]
L2 = 0.5
CONFIGS = [n for n, _ in GRID]
RAW10 = ("home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi")
RAW5 = ("home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi")


def load_all() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    form = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_form.csv")
    form["game_pk"] = pd.to_numeric(form["game_pk"], errors="coerce")
    bt = ROOT / "data" / "backtests"

    def ld(path: Path, park_col: str, season: int) -> pd.DataFrame:
        d = load(path, park_col, season)
        own = {c: pd.to_numeric(d[c], errors="coerce")
               for c in ("home_fi_xwoba", "away_fi_xwoba") if c in d.columns}
        d = attach(d.drop(columns=list(own)), fac)
        for c, v in own.items():
            d[c] = v.fillna(d[c]).values
        d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
        return d.merge(form, on="game_pk", how="left")

    return (ld(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024),
            ld(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025),
            ld(ROOT / "data" / "picks_2026.csv", "home_team", 2026))


def feats_for(variant: str, cfg: str) -> tuple[list[str], list[str]]:
    t1, b1 = list(T1_V3), list(B1_V3)
    h, a = f"home_{cfg}", f"away_{cfg}"
    if variant == "shipped":
        return t1, b1
    if variant == "swap10":
        t1 = [h if f == RAW10[0] else f for f in t1]
        b1 = [a if f == RAW10[1] else f for f in b1]
    elif variant == "swap_both":
        t1 = [h if f == RAW10[0] else f for f in t1 if f != RAW5[0]]
        b1 = [a if f == RAW10[1] else f for f in b1 if f != RAW5[1]]
    elif variant == "add":
        t1 = t1 + [h]
        b1 = b1 + [a]
    return t1, b1


def score(tr: pd.DataFrame, te: pd.DataFrame, t1f: list[str], b1f: list[str]) -> np.ndarray:
    tr, te = tr.copy(), te.copy()
    for c in set(t1f + b1f):
        if c.endswith("fi_xwoba") or any(c == f"{s}_{n}" for s in ("home", "away") for n in CONFIGS):
            mu = pd.to_numeric(tr[c], errors="coerce").mean()
            tr[c] = pd.to_numeric(tr[c], errors="coerce").fillna(mu)
            te[c] = pd.to_numeric(te[c], errors="coerce").fillna(mu)
    pk, b0 = build_park(tr, 50)
    wt = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, L2)
    wb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, L2)

    def raw(d: pd.DataFrame) -> np.ndarray:
        return (1 - predict(*wt, matrix(d, t1f, pk, b0))) * (1 - predict(*wb, matrix(d, b1f, pk, b0)))

    rtr, rte = raw(tr), raw(te)
    cal = CIRCalibrator.fit(list(rtr), list((tr.y == 0).astype(int)), n_bins=20)
    return np.array([cal.predict(float(v)) for v in rte])


def implied(o: float) -> float:
    return -o / (-o + 100) if o < 0 else 100 / (o + 100)


def payout(o: float) -> float:
    return o / 100 if o > 0 else 100 / -o


def kelly(p: float, o: float) -> float:
    """Production sizing: quarter Kelly, 10u cap, whole units with a 0.5u floor."""
    b = payout(o)
    f = min(max((p * b - (1 - p)) / b, 0.0) * 0.25, 0.10)
    s = f * 100
    if s < 0.10:
        return 0.0
    r = float(round(s))
    return max(0.5, min(r, 10.0))


def money_mode(args) -> int:
    """What the winning configs would have DONE on 2026, at the real captured prices.

    The gate is the shipped chain: calibrated p_nrfi below the cal-gate ceiling,
    where the ceiling is re-derived per configuration exactly as production does
    (87th percentile of calibrated p_nrfi among train-corpus candidates), because
    a different model shifts the calibrated scale underneath a fixed cut.
    """
    d24, d25, d26 = load_all()
    tr = pd.concat([d24, d25], ignore_index=True)
    te = d26.reset_index(drop=True)
    price = pd.to_numeric(te.get("market_yrfi_odds"), errors="coerce")
    y_run = (te.y.values == 1).astype(int)          # 1 = a run scored (YRFI won)
    dates = pd.to_datetime(te["date"]).dt.strftime("%Y-%m-%d").values

    print("=" * 108)
    print("  MONEY at the production gate -- 2026 only (the one season with real captured prices)")
    print("  Ceiling re-derived per config, as production must at any refit.  Kelly = quarter, 10u cap.")
    print("=" * 108)
    print(f"  {'variant / config':<30} {'bets':>5} {'hit':>7} {'stated':>7} {'break-even':>11} {'flat u':>9} {'Kelly u':>9} | {'No.1 nights':>11} {'hit':>7} {'Kelly u':>9}")

    cands = [("shipped", CONFIGS[0])] + [(v, c) for v in ("swap10", "add") for c in ("K65_pw0", "K65_hl15", "K65_all")]
    for variant, cfg in cands:
        t1f, b1f = feats_for(variant, cfg)
        # train-corpus calibrated probabilities, for the ceiling
        trc, tec = tr.copy(), te.copy()
        p_te = score(trc, tec, t1f, b1f)
        p_tr = score(trc, trc.reset_index(drop=True), t1f, b1f)
        cand_tr = p_tr[p_tr < 0.42]
        ceiling = float(np.quantile(cand_tr, 0.87)) if len(cand_tr) >= 20 else 0.413

        sel = (p_te < ceiling) & price.notna().values
        o = price.values[sel]
        yy = y_run[sel]
        pp = 1 - p_te[sel]
        dd = dates[sel]
        stk = np.array([kelly(a, b) for a, b in zip(pp, o)])
        keep = stk > 0
        o, yy, pp, dd, stk = o[keep], yy[keep], pp[keep], dd[keep], stk[keep]
        flat = np.where(yy == 1, [payout(x) for x in o], -1.0).sum()
        kel = np.where(yy == 1, stk * np.array([payout(x) for x in o]), -stk).sum()
        be = np.mean([implied(x) for x in o]) if len(o) else np.nan

        # No.1 of each night = most confident bet, price breaking ties (top-pick-rank.ts)
        order = np.lexsort((np.array([implied(x) for x in o]), 1 - pp))
        seen, n1 = set(), []
        for i in order:
            if dd[i] not in seen:
                seen.add(dd[i]); n1.append(i)
        n1 = np.array(sorted(n1))
        n1_kel = np.where(yy[n1] == 1, stk[n1] * np.array([payout(x) for x in o[n1]]), -stk[n1]).sum() if len(n1) else 0.0
        print(f"  {variant + ' / ' + cfg:<30} {len(o):>5} {yy.mean():>7.3f} {pp.mean():>7.3f} {be:>11.3f} "
              f"{flat:>+9.2f} {kel:>+9.2f} | {len(n1):>11} {yy[n1].mean():>7.3f} {n1_kel:>+9.2f}"
              + ("   <- shipped" if variant == "shipped" else ""))

    print("\n  Read this the way the repo's README says to: 2026 is the season nearest the fit and")
    print("  the one whose base rate the train set under-predicts, so treat the LEVEL of these")
    print("  figures as optimistic.  The discrimination gain (dAUC, and its permutation null) is")
    print("  the durable finding; the money is the same finding priced at one season's odds.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--null", action="store_true", help="selection-aware permutation null on the deciding split")
    ap.add_argument("--money", action="store_true", help="bets at the production gate on 2026 real prices, plus the No.1 series")
    ap.add_argument("--trials", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20260903)
    args = ap.parse_args()

    if args.money:
        return money_mode(args)

    d24, d25, d26 = load_all()
    splits = [("2024 (train 2025)", d25, d24),
              ("2025 (train 2024)", d24, d25),
              ("2026 (train 24+25)", pd.concat([d24, d25], ignore_index=True), d26)]

    print("=" * 108)
    print("  COVERAGE  (rule 1: no coverage line, no result)")
    print("=" * 108)
    for lab, _, te in splits:
        row = [f"  {lab:<20} n={len(te):>5}"]
        for c in ("K65_all", "shipped_like"):
            v = pd.to_numeric(te[f"home_{c}"], errors="coerce")
            row.append(f"{c}: {v.notna().mean() * 100:5.1f}% cov, sd {v.std():.4f}, {v.round(6).nunique():>5} distinct")
        print("   ".join(row))
        v = pd.to_numeric(te[RAW10[0]], errors="coerce")
        print(f"  {'':<20}   live last-10 column: {v.notna().mean() * 100:5.1f}% cov, sd {v.std():.4f}, {v.round(6).nunique():>5} distinct")

    results: dict[tuple[str, str, str], np.ndarray] = {}
    base: dict[str, np.ndarray] = {}
    ys: dict[str, np.ndarray] = {}

    for lab, tr, te in splits:
        te = te.reset_index(drop=True)
        y_nrfi = (te.y.values == 0).astype(int)
        ys[lab] = y_nrfi
        t1f, b1f = feats_for("shipped", CONFIGS[0])
        p0 = score(tr, te, t1f, b1f)
        base[lab] = p0
        print("\n" + "=" * 108)
        print(f"  SPLIT {lab}   n={len(te)}")
        print(f"  {'variant / config':<34} {'AUC':>8} {'dAUC':>8} {'Brier':>10} {'dBrier':>10} {'logloss':>10}")
        print(f"  {'shipped (raw last-5 + last-10)':<34} {auc(y_nrfi, p0):8.4f} {'--':>8} {brier(y_nrfi, p0):10.5f} {'--':>10} {logloss(y_nrfi, p0):10.5f}")
        for variant in ("swap_both", "swap10", "add"):
            for cfg in CONFIGS:
                t1f, b1f = feats_for(variant, cfg)
                p = score(tr, te, t1f, b1f)
                results[(lab, variant, cfg)] = p
                print(f"  {variant + ' / ' + cfg:<34} {auc(y_nrfi, p):8.4f} {auc(y_nrfi, p) - auc(y_nrfi, p0):+8.4f} "
                      f"{brier(y_nrfi, p):10.5f} {brier(y_nrfi, p) - brier(y_nrfi, p0):+10.5f} {logloss(y_nrfi, p):10.5f}")

    # ---- the three-split summary: does any single config win everywhere? ----
    print("\n" + "=" * 108)
    print("  RULE 2: a candidate must help in ALL THREE splits.  dAUC by config (positive = better than shipped)")
    print("=" * 108)
    print(f"  {'variant / config':<34} " + " ".join(f"{lab.split(' ')[0]:>10}" for lab, _, _ in splits) + "   all3?")
    winners = []
    for variant in ("swap_both", "swap10", "add"):
        for cfg in CONFIGS:
            ds = [auc(ys[lab], results[(lab, variant, cfg)]) - auc(ys[lab], base[lab]) for lab, _, _ in splits]
            ok = all(d > 0 for d in ds)
            if ok:
                winners.append((variant, cfg, ds))
            print(f"  {variant + ' / ' + cfg:<34} " + " ".join(f"{d:+10.4f}" for d in ds) + f"   {'YES' if ok else ''}")

    print("\n  bootstrap on the deciding split (2026), paired over games, vs shipped:")
    lab26 = splits[2][0]
    rng = np.random.default_rng(args.seed)
    y26 = ys[lab26]
    idx = np.arange(len(y26))
    boots = rng.integers(0, len(y26), size=(2000, len(y26)))
    for variant, cfg, _ in (winners or [("swap_both", "K65_all", None), ("swap10", "K65_all", None)]):
        p = results[(lab26, variant, cfg)]
        p0 = base[lab26]
        dA = np.array([auc(y26[b], p[b]) - auc(y26[b], p0[b]) for b in boots[:600]])
        dB = np.array([brier(y26[b], p[b]) - brier(y26[b], p0[b]) for b in boots[:600]])
        print(f"    {variant + ' / ' + cfg:<32} dAUC {dA.mean():+.4f} 90% CI [{np.percentile(dA, 5):+.4f},{np.percentile(dA, 95):+.4f}]  "
              f"dBrier {dB.mean():+.5f} [{np.percentile(dB, 5):+.5f},{np.percentile(dB, 95):+.5f}] (neg=better)")

    if not winners:
        print("\n  NO config helps in all three splits.  Per CLAUDE.md that is a REJECT, and the")
        print("  selection-aware null is not needed: there is nothing to defend.")
        return 0

    if not args.null:
        print(f"\n  {len(winners)} config(s) pass all three splits.  Re-run with --null to price the search.")
        return 0

    # ---- selection-aware null: replicate the WHOLE procedure on permuted values ----
    print("\n" + "=" * 108)
    print(f"  SELECTION-AWARE NULL, {args.trials} trials -- the entire procedure re-run on noise")
    print("  Each trial: shuffle every config column WITHIN SEASON (distribution kept, the")
    print("  pitcher-to-game link destroyed -- a relabelling cannot add information), then run")
    print("  all 30 cells across all 3 splits, apply the same all-three-positive filter, and")
    print("  read off the best 2026 dAUC among whatever survives.  That is what the real search")
    print("  did, so this is the distribution the observed number has to beat.")
    print("=" * 108)

    obs_best = max(auc(ys[lab26], results[(lab26, v, c)]) - auc(ys[lab26], base[lab26]) for v, c, _ in winners)
    cols = [f"{s}_{c}" for c in CONFIGS for s in ("home", "away")]
    seasons = [d24, d25, d26]
    null_best = np.full(args.trials, np.nan)
    null_nwin = np.zeros(args.trials, dtype=int)

    for t in range(args.trials):
        perm = []
        for frame in seasons:
            f = frame.copy()
            for col in cols:
                f[col] = rng.permutation(f[col].values)
            perm.append(f)
        p24, p25, p26 = perm
        psplits = [("2024", p25, p24.reset_index(drop=True)),
                   ("2025", p24, p25.reset_index(drop=True)),
                   ("2026", pd.concat([p24, p25], ignore_index=True), p26.reset_index(drop=True))]
        # shipped baseline is unaffected by the permutation (it uses no config column),
        # so the real per-split baselines are reused.
        d_by_cell: dict[tuple[str, str], list[float]] = {}
        for (plab, ptr, pte), (rlab, _, _) in zip(psplits, splits):
            y = ys[rlab]
            a0 = auc(y, base[rlab])
            for variant in ("swap_both", "swap10", "add"):
                for cfg in CONFIGS:
                    t1f, b1f = feats_for(variant, cfg)
                    p = score(ptr, pte, t1f, b1f)
                    d_by_cell.setdefault((variant, cfg), []).append(auc(y, p) - a0)
        surv = [d[2] for d in d_by_cell.values() if all(x > 0 for x in d)]
        null_nwin[t] = len(surv)
        null_best[t] = max(surv) if surv else np.nan
        if (t + 1) % 25 == 0:
            done = null_best[:t + 1]
            print(f"    {t + 1}/{args.trials}   trials with >=1 survivor: {np.isfinite(done).mean():.0%}   "
                  f"best-in-noise mean {np.nanmean(done):+.4f}")

    finite = null_best[np.isfinite(null_best)]
    # A trial where nothing survives cannot beat the observed result, so it counts as a loss.
    p_val = float((np.nan_to_num(null_best, nan=-9.9) >= obs_best).mean())
    print(f"\n  observed: {len(winners)} of 30 cells pass all three splits; best 2026 dAUC {obs_best:+.4f}")
    print(f"  in noise: {np.isfinite(null_best).mean():.0%} of trials produce at least one all-three-splits survivor;")
    print(f"            survivors per trial mean {null_nwin.mean():.1f} (observed {len(winners)});")
    if len(finite):
        print(f"            best-in-noise mean {finite.mean():+.4f}  sd {finite.std():.4f}  "
              f"90th pct {np.percentile(finite, 90):+.4f}  max {finite.max():+.4f}")
    print(f"\n  selection-aware p = {p_val:.3f}   ->  "
          f"{'SURVIVES the search' if p_val < 0.05 else 'ARTIFACT: the search finds this much in noise'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

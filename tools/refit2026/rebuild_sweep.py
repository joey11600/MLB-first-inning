#!/usr/bin/env python3
"""
PHASE 3 of the rebuild: candidate models judged on MONEY AT REAL PRICES.

Every earlier sweep in this directory judged candidates on AUC/Brier, or on
money at a -112 placeholder.  `real_price_backtest.py` showed what that
placeholder was worth: the shipped chain reads +4.68u pooled at -112 and
**-21.91u** at prices that were actually quoted.  So a candidate that "wins"
on placeholder money has not been shown to win on anything.  This script is
the sweep redone against `data/odds_history/` (1,146 priced games).

WHAT IT DOES, in the order the methodology memo requires:

  1. COVERAGE FIRST, per variant per split.  A gated bet with no captured
     price is DROPPED and counted, never defaulted.  A variant whose bets are
     only 60% priced cannot be read as a money result at all -- and variants
     differ here, because a rebuilt model bets different games.
  2. MONEY at real prices: flat and quarter-Kelly with the production caps,
     plus the break-even the PAID prices demand.
  3. DAY-LEVEL BOOTSTRAP (whole slates resampled -- bets on one night share
     weather, umpires and the schedule).
  4. ORACLE LEVEL CONTROL, the `baserate_control.py` trick: shift the
     calibrated probability in log-odds until its mean equals the test
     season's actual base rate.  Monotone, so ranking is untouched and only
     the LEVEL moves.  ROI that survives is skill; ROI that evaporates was
     the train/test base-rate gap, which has dominated every money number in
     this repo.
  5. SELECTION-AWARE NULL (`--null N`, default 0 = skip).  Reported deltas
     are the BEST of many variants, so they cannot be read against a fixed
     threshold -- pricing in the search moved a p of 0.003 to 0.227 once on
     this model.  Each trial builds placebo variants that CANNOT carry
     information (the park->rate pairing permuted, park_null style), runs the
     identical selection rule, and records the best placebo delta.  Only run
     this if something in stage 2-4 actually beats shipped; if nothing does,
     there is no winner to price.

THE 2026 SPLIT IS THE ONE THAT DECIDES -- it is the only season recorded at
predict time, so it is the closest proxy for live serving.  Two traps there,
both handled:
  * the ledger has carried its own home/away_fi_xwoba since 2026-08-23 while
    `factor_fi_pooled.csv` stops at 2026-08-22, so a naive merge either
    collides into _x/_y columns or silently mean-fills every later game.
    The ledger's own value wins, the factor file is the fallback
    (`no1_since_may26.py` pattern).
  * 2026 prices come from the ledger's `market_yrfi_odds`; rows without one
    are dropped, not defaulted.

Nothing here writes to data/, the ledger or any model artifact.

    python tools/refit2026/rebuild_sweep.py [--boot 2000] [--null 0]
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

from calibration import CIRCalibrator  # noqa: E402
from harness import (T1_SHIPPED, B1_SHIPPED, build_park, fit_lr, load,  # noqa: E402
                     matrix, predict)
from test_fi_pooled import attach  # noqa: E402
from real_price_backtest import (KELLY_FRAC, CAP_BET, CAP_DAY, breakeven,  # noqa: E402
                                 dec, load_prices)

GATE = 0.413
UMP = "home_plate_ump_nrfi_rate"
PARK = "fi_park_nrfi_rate"

V3_T1 = T1_SHIPPED + ["home_fi_xwoba"]
V3_B1 = B1_SHIPPED + ["away_fi_xwoba"]


def _drop(feats, *names):
    return [f for f in feats if f not in names]


# label -> (t1 feats, b1 feats, L2, park prior K).  K = None means "no park
# feature at all"; K = inf means "every park at the training league mean".
VARIANTS: dict[str, tuple] = {
    "shipped v3 (K=50, L2 0.50)": (V3_T1, V3_B1, 0.50, 50),
    "park K=150":                 (V3_T1, V3_B1, 0.50, 150),
    "park K=250":                 (V3_T1, V3_B1, 0.50, 250),
    "park K=500":                 (V3_T1, V3_B1, 0.50, 500),
    "park flat (league mean)":    (V3_T1, V3_B1, 0.50, float("inf")),
    "no park feature":            (_drop(V3_T1, PARK), _drop(V3_B1, PARK), 0.50, 50),
    "L2 0.25":                    (V3_T1, V3_B1, 0.25, 50),
    "L2 1.00":                    (V3_T1, V3_B1, 1.00, 50),
    "drop ump":                   (_drop(V3_T1, UMP), _drop(V3_B1, UMP), 0.50, 50),
    "park flat + drop ump":       (_drop(V3_T1, UMP), _drop(V3_B1, UMP), 0.50, float("inf")),
}
BASE = "shipped v3 (K=50, L2 0.50)"


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def oracle_shift(p, y):
    """baserate_control.oracle_shift: add a constant in log-odds so mean(p)
    == mean(y).  Monotone -> ranking untouched, only the level moves."""
    lo, hi = -5.0, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(logit(p) + mid)))).mean() < y.mean():
            lo = mid
        else:
            hi = mid
    return 1 / (1 + np.exp(-(logit(p) + (lo + hi) / 2)))


def fit_score(tr, te, t1f, b1f, l2, K, park_override=None):
    """Two-stage LR -> CIR, all fit on TRAIN ONLY.  Returns calibrated p_nrfi.

    `park_override` replaces the park map with a supplied dict (used by the
    null to permute which rate belongs to which park)."""
    tr, te = tr.copy(), te.copy()
    for c in [x for x in t1f + b1f if x.endswith("fi_xwoba")]:
        mu = tr[c].mean()
        tr[c] = tr[c].fillna(mu)
        te[c] = te[c].fillna(mu)

    if K is None or PARK not in set(t1f) | set(b1f):
        pk, b0 = {}, 0.5
    elif np.isinf(K):
        _, b0 = build_park(tr, 50)
        pk = {}                      # every park falls back to the base rate
    else:
        pk, b0 = build_park(tr, K)
    if park_override is not None:
        pk = park_override

    wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, l2)
    wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, l2)

    def raw(d):
        return ((1 - predict(wt, mt, st, matrix(d, t1f, pk, b0)))
                * (1 - predict(wb, mb, sb, matrix(d, b1f, pk, b0))))

    cal = CIRCalibrator.fit(list(raw(tr)), list((tr.y == 0).astype(int)), n_bins=20)
    return np.array([cal.predict(float(v)) for v in raw(te)])


def bet_table(te: pd.DataFrame, p_nrfi: np.ndarray, gate: float = GATE):
    """Gated bets that have a REAL price, staked at quarter-Kelly with caps."""
    t = te.assign(p_nrfi=p_nrfi, p_yrfi=1 - p_nrfi)
    t = t[t.p_nrfi < gate].copy()
    gated = len(t)
    t = t[t.price.notna()].copy()
    priced = len(t)          # counted BEFORE staking -- see the note at the end
    if not len(t):
        return t, gated, priced
    t["won"] = t.y == 1
    t["b"] = t.price.map(dec)
    f = (t.b * t.p_yrfi - (1 - t.p_yrfi)) / t.b
    t["stake"] = np.clip(f * KELLY_FRAC * 100.0, 0.0, CAP_BET)
    out = {}
    for _, day in t.sort_values(["date", "p_nrfi"]).groupby("date"):
        used = 0.0
        for idx, r in day.iterrows():
            s = min(r.stake, max(CAP_DAY - used, 0.0))
            used += s
            out[idx] = s
    t["stake"] = pd.Series(out)
    t = t[t.stake > 0].copy()
    t["flat"] = np.where(t.won, t.b, -1.0)
    t["kelly"] = np.where(t.won, t.stake * t.b, -t.stake)
    # THREE counts, and conflating them misreports coverage:
    #   gated (the model wants the bet) >= priced (a real price exists)
    #   >= staked (quarter-Kelly funded it).
    # A zero stake is Kelly declining a -EV price -- a legitimate no-bet, NOT
    # a data gap.  Reporting the staked count as "priced" understated coverage
    # badly on the first run (2025 park-flat read 49.4% when it is really
    # 80.7%), which nearly became a false "this result is unreadable" warning.
    return t, gated, priced


def build_frames():
    prices = load_prices()
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)

    # 2026: the ledger's own fi_xwoba wins over the stale factor dump.
    led = load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)
    own = {c: pd.to_numeric(led[c], errors="coerce")
           for c in ("home_fi_xwoba", "away_fi_xwoba") if c in led.columns}
    d26 = attach(led.drop(columns=list(own)), fac)
    for c, v in own.items():
        d26[c] = v.values if c not in d26.columns else v.fillna(d26[c]).values

    for d in (d24, d25, d26):
        d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce").astype("Int64")
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")

    for d in (d24, d25):
        d["price"] = [prices.get(int(g), {}).get("over") if pd.notna(g) else None
                      for g in d.game_pk]
    d26["price"] = pd.to_numeric(d26.get("market_yrfi_odds"), errors="coerce")

    print(f"prices: {len(prices)} historical games; 2026 ledger rows with a "
          f"captured YRFI price: {int(d26.price.notna().sum())} of {len(d26)}")
    return d24, d25, d26


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--null", type=int, default=0,
                    help="selection-aware null trials (0 = skip; only worth "
                         "running if something beat shipped)")
    ap.add_argument("--seed", type=int, default=20260912)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    d24, d25, d26 = build_frames()
    splits = [("2024 (tr 2025)", d25, d24),
              ("2025 (tr 2024)", d24, d25),
              ("2026 (tr 24+25)", pd.concat([d24, d25], ignore_index=True), d26)]

    res: dict[tuple, dict] = {}
    for lab, tr, te in splits:
        for vlab, (t1f, b1f, l2, K) in VARIANTS.items():
            p = fit_score(tr, te, t1f, b1f, l2, K)
            bets, gated, priced = bet_table(te, p)
            y = te["y"].values
            po = oracle_shift(1 - p, y)          # level-corrected p_yrfi
            ob, _, _ = bet_table(te.assign(_p=1 - po), 1 - po)
            res[(lab, vlab)] = dict(
                bets=bets, gated=gated, priced=priced,
                flat=float(bets.flat.sum()) if len(bets) else 0.0,
                kelly=float(bets.kelly.sum()) if len(bets) else 0.0,
                hit=float(bets.won.mean()) if len(bets) else float("nan"),
                be=float(bets.price.map(breakeven).mean()) if len(bets) else float("nan"),
                claimed=float(bets.p_yrfi.mean()) if len(bets) else float("nan"),
                lvl_flat=float(ob.flat.sum()) if len(ob) else 0.0,
                lvl_n=len(ob))

    print("\n" + "=" * 118)
    print(f"COVERAGE FIRST -- gate p_nrfi < {GATE}; unpriced bets DROPPED, never defaulted")
    print("  g = gated (model wants it) | p = priced (a real price exists) | "
          "s = staked (Kelly funded it); coverage = p/g")
    print(f"  {'variant':<28} " + " ".join(f"{l:>27}" for l, _, _ in splits))
    for vlab in VARIANTS:
        cells = []
        for lab, _, _ in splits:
            r = res[(lab, vlab)]
            cells.append(f"{r['gated']:>4}g {r['priced']:>4}p {len(r['bets']):>4}s "
                         f"{r['priced'] / max(r['gated'], 1):>6.1%}")
        print(f"  {vlab:<28} " + " ".join(f"{c:>27}" for c in cells))

    print("\n" + "=" * 118)
    print("MONEY AT REAL PRICES -- flat units (the repo judges on flat; Kelly shown for scale)")
    print(f"  {'variant':<28} " + " ".join(f"{l:>27}" for l, _, _ in splits))
    for vlab in VARIANTS:
        cells = []
        for lab, _, _ in splits:
            r = res[(lab, vlab)]
            cells.append(f"{r['hit']:>5.1%} {r['flat']:>+7.2f}u k{r['kelly']:>+8.2f}u")
        print(f"  {vlab:<28} " + " ".join(f"{c:>27}" for c in cells))

    print("\n" + "=" * 118)
    print("vs SHIPPED, flat units -- and the same delta after an ORACLE LEVEL correction")
    print("  (if 'lvl' collapses toward zero, the gain was the train/test base-rate gap)")
    print(f"  {'variant':<28} " + " ".join(f"{l:>27}" for l, _, _ in splits) + "   all 3?")
    for vlab in VARIANTS:
        if vlab == BASE:
            continue
        cells, deltas = [], []
        for lab, _, _ in splits:
            d = res[(lab, vlab)]["flat"] - res[(lab, BASE)]["flat"]
            dl = res[(lab, vlab)]["lvl_flat"] - res[(lab, BASE)]["lvl_flat"]
            deltas.append(d)
            cells.append(f"{d:>+8.2f}u  lvl {dl:>+8.2f}u")
        ok = all(x > 0 for x in deltas)
        print(f"  {vlab:<28} " + " ".join(f"{c:>27}" for c in cells)
              + f"   {'YES' if ok else 'no'}")

    print("\n" + "=" * 118)
    print("DAY-LEVEL BOOTSTRAP on the flat delta vs shipped (whole slates resampled)")
    for lab, _, _ in splits:
        base_days = res[(lab, BASE)]["bets"].groupby("date").flat.sum()
        for vlab in VARIANTS:
            if vlab == BASE:
                continue
            v_days = res[(lab, vlab)]["bets"].groupby("date").flat.sum()
            days = sorted(set(base_days.index) | set(v_days.index))
            if not days:
                continue
            bd = base_days.reindex(days).fillna(0.0).to_numpy()
            vd = v_days.reindex(days).fillna(0.0).to_numpy()
            diff = vd - bd
            draws = np.array([diff[rng.integers(0, len(diff), len(diff))].sum()
                              for _ in range(args.boot)])
            print(f"  {lab:<16} {vlab:<28} {diff.sum():>+8.2f}u  "
                  f"90% CI [{np.percentile(draws,5):+7.2f}, {np.percentile(draws,95):+7.2f}]  "
                  f"P(better) {(draws>0).mean():>4.0%}")

    best = max((v for v in VARIANTS if v != BASE),
               key=lambda v: sum(res[(l, v)]["flat"] - res[(l, BASE)]["flat"]
                                 for l, _, _ in splits))
    tot = sum(res[(l, best)]["flat"] - res[(l, BASE)]["flat"] for l, _, _ in splits)
    print(f"\nbest by summed flat delta: {best}  ({tot:+.2f}u over three splits)")
    print("A summed delta is NOT a result -- it is the best of "
          f"{len(VARIANTS)-1} searched variants. Price the search with --null "
          "before believing it, and only if it also wins all three splits.")

    if args.null > 0:
        print("\n" + "=" * 118)
        print(f"SELECTION-AWARE NULL -- {args.null} trials, park pairing permuted "
              "(a relabelling cannot add information)")
        lab, tr, te = splits[-1]                  # the deciding split
        pk_real, b0 = build_park(tr, 50)
        parks = sorted(pk_real)
        vals = np.array([pk_real[p] for p in parks])
        base_flat = res[(lab, BASE)]["flat"]
        obs = res[(lab, best)]["flat"] - base_flat
        wins = 0
        for i in range(args.null):
            perm = dict(zip(parks, rng.permutation(vals)))
            trial = []
            for vlab, (t1f, b1f, l2, K) in VARIANTS.items():
                if vlab == BASE:
                    continue
                p = fit_score(tr, te, t1f, b1f, l2, K, park_override=perm)
                b, _ = bet_table(te, p)
                trial.append((float(b.flat.sum()) if len(b) else 0.0) - base_flat)
            if max(trial) >= obs:
                wins += 1
            if (i + 1) % 25 == 0:
                print(f"    {i+1}/{args.null}  placebo-best >= observed in {wins}")
        print(f"\n  observed best delta on {lab}: {obs:+.2f}u")
        print(f"  p(placebo search does as well) = {wins/args.null:.3f}")
        print("  p >= 0.05 means the winner is the search, not a repair.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

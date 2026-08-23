#!/usr/bin/env python3
"""
THE SHIP BACKTEST: today's model vs the new one, same pipeline, same gate,
out of sample, in the three units the operator asked for -- record, flat
units, quarter-Kelly units -- plus the No.1 play on its own.

OLD  = 19 features, L2 0.05          (what runs today)
NEW  = 19 + pooled first-inning pitcher xwOBA, L2 0.50   (what ships)

Each config: two-stage LR fit on the TRAIN seasons only -> CIR calibrator fit
on the train seasons only -> gate p_nrfi < 0.42 (STRONG YRFI) -> quarter-Kelly
on the calibrated probability with the production caps (10u/bet, 15u/day,
strongest first).  Nothing from the test season leaks in.

PRICES.  2026: the real captured price when the ledger has one (market_yrfi_odds),
else -112.  2024/2025: NO first-inning odds exist (data/odds starts 2026-04-29),
so every 2024/2025 money figure assumes -112 and must be read as hit-rate
arithmetic, not money that could have been won.  The hit rates are real.

Kelly is path- and level-dependent; the repo's rule is to judge a change on the
FLAT column and report Kelly as what it does to the bank.  Both are printed.
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
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load, logloss, matrix, predict  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

GATE_NRFI, KELLY_FRAC, CAP_BET, CAP_DAY, DEFAULT_PRICE = 0.42, 0.25, 10.0, 15.0, -112.0


def dec(price):
    price = float(price)
    return (100.0 / abs(price)) if price < 0 else (price / 100.0)


def qkelly(p, b):
    f = (b * p - (1.0 - p)) / b
    return float(np.clip(f * KELLY_FRAC * 100.0, 0.0, CAP_BET))


def fit_score(tr, te, t1f, b1f, l2):
    tr, te = tr.copy(), te.copy()
    for c in [x for x in t1f + b1f if x.endswith("fi_xwoba")]:
        mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, l2)
    wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, l2)
    def raw(d):
        return (1 - predict(wt, mt, st, matrix(d, t1f, pk, b0))) * (1 - predict(wb, mb, sb, matrix(d, b1f, pk, b0)))
    cal = CIRCalibrator.fit(list(raw(tr)), list((tr.y == 0).astype(int)), n_bins=20)
    return np.array([cal.predict(float(v)) for v in raw(te)])


def simulate(te: pd.DataFrame, p_nrfi: np.ndarray) -> pd.DataFrame:
    """Gate -> stakes with caps -> per-bet P&L.  Returns the bet table."""
    t = te[["date", "game_pk", "y", "price"]].copy()
    t["p_nrfi"] = p_nrfi; t["p_yrfi"] = 1 - p_nrfi
    t = t[t.p_nrfi < GATE_NRFI].copy()
    if not len(t):
        return t
    t["b"] = t.price.map(dec)
    t["stake"] = [qkelly(p, b) for p, b in zip(t.p_yrfi, t.b)]
    # daily cap, strongest first (lowest p_nrfi)
    out = {}
    for _, day in t.sort_values(["date", "p_nrfi"]).groupby("date"):
        used = 0.0
        for idx, r in day.iterrows():
            s = min(r.stake, max(CAP_DAY - used, 0.0)); used += s; out[idx] = s
    t["stake"] = pd.Series(out)
    t = t[t.stake > 0].copy()
    t["won"] = t.y == 1
    t["flat"] = np.where(t.won, t.b, -1.0)
    t["kelly"] = np.where(t.won, t.stake * t.b, -t.stake)
    t["is_no1"] = False
    t.loc[t.groupby("date").p_nrfi.idxmin(), "is_no1"] = True
    return t


def summarize(bets: pd.DataFrame) -> dict:
    if not len(bets):
        return dict(bets=0, W=0, L=0, hit=np.nan, flat=0.0, kelly=0.0, staked=0.0,
                    n1=0, n1W=0, n1L=0, n1hit=np.nan, n1flat=0.0, n1kelly=0.0)
    n1 = bets[bets.is_no1]
    return dict(bets=len(bets), W=int(bets.won.sum()), L=int((~bets.won).sum()), hit=bets.won.mean(),
                flat=bets.flat.sum(), kelly=bets.kelly.sum(), staked=bets.stake.sum(),
                n1=len(n1), n1W=int(n1.won.sum()), n1L=int((~n1.won).sum()), n1hit=n1.won.mean(),
                n1flat=n1.flat.sum(), n1kelly=n1.kelly.sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    d26 = attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fac)
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d24["price"] = DEFAULT_PRICE; d25["price"] = DEFAULT_PRICE
    px = pd.to_numeric(d26.get("market_yrfi_odds"), errors="coerce")
    d26["price"] = px.fillna(DEFAULT_PRICE)
    print(f"2026 games with a real captured YRFI price: {px.notna().sum()} of {len(d26)} "
          f"(others at {DEFAULT_PRICE:.0f}); 2024/2025: all at {DEFAULT_PRICE:.0f} (no odds exist)")

    CFG = {"OLD (today)": (T1_SHIPPED, B1_SHIPPED, 0.05),
           "NEW (ships)": (T1_SHIPPED + ["home_fi_xwoba"], B1_SHIPPED + ["away_fi_xwoba"], 0.50)}
    splits = [("2024  (trained on 2025)", d25, d24),
              ("2025  (trained on 2024)", d24, d25),
              ("2026  (trained on 24+25)", pd.concat([d24, d25], ignore_index=True), d26)]

    res, bets_keep = {}, {}
    for lab, tr, te in splits:
        for name, (t1f, b1f, l2) in CFG.items():
            pn = fit_score(tr, te, t1f, b1f, l2)
            b = simulate(te, pn)
            res[(lab, name)] = (summarize(b), auc(te.y.values, 1 - pn), logloss(te.y.values, 1 - pn))
            bets_keep[(lab, name)] = b

    print("\n" + "=" * 112)
    print("ALL GATE BETS (STRONG YRFI, p_nrfi < 0.42), out of sample")
    print(f"  {'season':<26} {'model':<12} {'bets':>5} {'record':>9} {'hit':>6} {'flat P&L':>9} {'ROI':>7} "
          f"{'Kelly P&L':>10} {'Kelly stk':>9} {'K-ROI':>6} {'AUC':>7}")
    for lab, _, _ in splits:
        for name in CFG:
            s, a, ll = res[(lab, name)]
            roi = s['flat'] / s['bets'] * 100 if s['bets'] else float('nan')
            kroi = s['kelly'] / s['staked'] * 100 if s['staked'] else float('nan')
            print(f"  {lab:<26} {name:<12} {s['bets']:>5} {s['W']:>4}-{s['L']:<4} {s['hit']:>6.3f} "
                  f"{s['flat']:>+8.2f}u {roi:>+6.1f}% {s['kelly']:>+9.2f}u {s['staked']:>8.1f}u "
                  f"{kroi:>+5.1f}% {a:>7.4f}")
        print()

    print("=" * 112)
    print("THE No.1 PLAY ONLY (lowest p_nrfi among gate bets each night)")
    print(f"  {'season':<26} {'model':<12} {'slates':>6} {'record':>9} {'hit':>6} {'flat P&L':>9} {'Kelly P&L':>10}")
    for lab, _, _ in splits:
        for name in CFG:
            s, _, _ = res[(lab, name)]
            print(f"  {lab:<26} {name:<12} {s['n1']:>6} {s['n1W']:>4}-{s['n1L']:<4} {s['n1hit']:>6.3f} "
                  f"{s['n1flat']:>+8.2f}u {s['n1kelly']:>+9.2f}u")
        print()

    print("=" * 112)
    print("2026 BY MONTH, all gate bets (real prices where captured)")
    for name in CFG:
        b = bets_keep[("2026  (trained on 24+25)", name)]
        g = b.groupby(b.date.dt.to_period("M")).agg(n=("won", "size"), W=("won", "sum"),
                                                   flat=("flat", "sum"), kelly=("kelly", "sum"))
        cells = "  ".join(f"{str(m)[-2:]}: {int(r.W)}-{int(r.n-r.W)} {r.flat:+.1f}u/{r.kelly:+.1f}u" for m, r in g.iterrows())
        print(f"  {name:<12} {cells}")
    print("  (W-L  flat/Kelly)")

    print("\n" + "=" * 112)
    print("SLATE-DAY BOOTSTRAP on 2026: NEW minus OLD (resampling whole nights)")
    bo, bn = bets_keep[("2026  (trained on 24+25)", "OLD (today)")], bets_keep[("2026  (trained on 24+25)", "NEW (ships)")]
    days = np.array(sorted(set(bo.date) | set(bn.date)))
    go = {d: (x.flat.sum(), x.kelly.sum(), x[x.is_no1].won.sum(), len(x[x.is_no1])) for d, x in bo.groupby("date")}
    gn = {d: (x.flat.sum(), x.kelly.sum(), x[x.is_no1].won.sum(), len(x[x.is_no1])) for d, x in bn.groupby("date")}
    dflat, dkelly, dn1 = [], [], []
    for _ in range(args.boot):
        pick = days[rng.integers(0, len(days), len(days))]
        fo = sum(go.get(d, (0, 0, 0, 0))[0] for d in pick); fn = sum(gn.get(d, (0, 0, 0, 0))[0] for d in pick)
        ko = sum(go.get(d, (0, 0, 0, 0))[1] for d in pick); kn = sum(gn.get(d, (0, 0, 0, 0))[1] for d in pick)
        wo = sum(go.get(d, (0, 0, 0, 0))[2] for d in pick); no = sum(go.get(d, (0, 0, 0, 0))[3] for d in pick)
        wn = sum(gn.get(d, (0, 0, 0, 0))[2] for d in pick); nn = sum(gn.get(d, (0, 0, 0, 0))[3] for d in pick)
        dflat.append(fn - fo); dkelly.append(kn - ko); dn1.append(wn / max(nn, 1) - wo / max(no, 1))
    for lab, arr, unit in [("flat P&L", dflat, "u"), ("Kelly P&L", dkelly, "u"), ("No.1 hit rate", dn1, "")]:
        arr = np.array(arr)
        print(f"  d {lab:<14} {arr.mean():+8.2f}{unit}   90% CI [{np.percentile(arr,5):+.2f}, {np.percentile(arr,95):+.2f}]   "
              f"P(NEW better) = {(arr > 0).mean():.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

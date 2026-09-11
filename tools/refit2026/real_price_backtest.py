#!/usr/bin/env python3
"""
THE HONEST BASELINE: what the shipped chain does out of sample at prices that
actually existed -- not at the -112 placeholder every earlier backtest in this
repo assumed.

WHY THIS EXISTS.  `backtest_ship.py` prices 2024/2025 at a flat -112 because
when it was written no historical first-inning odds were available.  They are
now (`data/odds_history/`, bought 2026-09-09 and 2026-09-11).  The placeholder
was not a rounding detail: on the same 462 bets, same outcomes, only the price
changing, flat P&L moves from -0.14u to **-21.48u**.  The real market charged
an average 55.0% break-even; -112 charges 52.8%.

THE RULE THIS ENFORCES.  A gated bet with no captured price is **EXCLUDED and
counted**, never silently defaulted.  Defaulting is what turned a losing
population into a break-even-looking one.  The coverage line prints first, per
`feature_test_methodology` ("no coverage line, no result") -- a population that
is only 60% priced cannot be read as a money result at all.

WHAT IT REPORTS, per split:
  * coverage: gated bets, how many priced, how many dropped
  * hit rate vs the break-even the PAID prices demand (not a nominal -112)
  * flat and quarter-Kelly P&L at real prices, and the same at -112 beside it
    so the size of the flattery is visible rather than assumed
  * day-level bootstrap CI on flat P&L (resampling whole slates, because bets
    on one night share weather, umpires and the day's schedule)

Splits follow backtest_ship: a season is tested against a model trained on the
other season(s) only; park map rebuilt inside each split from TRAIN ONLY.

    python tools/refit2026/real_price_backtest.py [--boot 2000] [--gate 0.413]
"""
from __future__ import annotations

import argparse
import csv
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

PLACEHOLDER = -112.0
KELLY_FRAC, CAP_BET, CAP_DAY = 0.25, 10.0, 15.0

V3_T1 = T1_SHIPPED + ["home_fi_xwoba"]
V3_B1 = B1_SHIPPED + ["away_fi_xwoba"]


def dec(price: float) -> float:
    """American odds -> profit per 1 unit staked."""
    p = float(price)
    return (100.0 / abs(p)) if p < 0 else (p / 100.0)


def breakeven(price: float) -> float:
    p = float(price)
    return (-p) / (-p + 100.0) if p < 0 else 100.0 / (p + 100.0)


def load_prices() -> dict[int, dict]:
    """game_pk -> the YRFI (Over 0.5) price we could actually have taken.

    Reads every hist_fi_odds_*.csv in data/odds_history so the original
    purchase and later envelope runs combine.  A game priced twice keeps the
    FIRST row seen; the files are disjoint by construction (the envelope
    targets exclude anything already priced) and a duplicate would mean a
    re-fetch, in which case the earlier row is the one already used in
    published numbers.
    """
    out: dict[int, dict] = {}
    for path in sorted((ROOT / "data" / "odds_history").glob("hist_fi_odds_*.csv")):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    gp = int(float(r["game_pk"]))
                    over = float(r["over_price"])
                except (TypeError, ValueError, KeyError):
                    continue
                if gp in out:
                    continue
                try:
                    devig = float(r.get("cons_over_devig") or "nan")
                except ValueError:
                    devig = float("nan")
                out[gp] = {"over": over, "devig": devig,
                           "book": r.get("book", ""), "src": path.name}
    return out


def fit_score(tr: pd.DataFrame, te: pd.DataFrame):
    tr, te = tr.copy(), te.copy()
    for c in ("home_fi_xwoba", "away_fi_xwoba"):
        mu = tr[c].mean()
        tr[c] = tr[c].fillna(mu)
        te[c] = te[c].fillna(mu)
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, V3_T1, pk, b0), tr.y_t1.values, 0.50)
    wb, mb, sb = fit_lr(matrix(tr, V3_B1, pk, b0), tr.y_b1.values, 0.50)

    def raw(d):
        return ((1 - predict(wt, mt, st, matrix(d, V3_T1, pk, b0)))
                * (1 - predict(wb, mb, sb, matrix(d, V3_B1, pk, b0))))

    cal = CIRCalibrator.fit(list(raw(tr)), list((tr.y == 0).astype(int)), n_bins=20)
    return np.array([cal.predict(float(v)) for v in raw(te)])


def stake_table(bets: pd.DataFrame, price_col: str) -> pd.DataFrame:
    """Quarter-Kelly with the production caps, strongest first within a day."""
    b = bets.copy()
    b["b"] = b[price_col].map(dec)
    f = (b.b * b.p_yrfi - (1 - b.p_yrfi)) / b.b
    b["stake"] = np.clip(f * KELLY_FRAC * 100.0, 0.0, CAP_BET)
    out = {}
    for _, day in b.sort_values(["date", "p_nrfi"]).groupby("date"):
        used = 0.0
        for idx, r in day.iterrows():
            s = min(r.stake, max(CAP_DAY - used, 0.0))
            used += s
            out[idx] = s
    b["stake"] = pd.Series(out)
    b = b[b.stake > 0].copy()
    b["flat"] = np.where(b.won, b.b, -1.0)
    b["kelly"] = np.where(b.won, b.stake * b.b, -b.stake)
    return b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--gate", type=float, default=0.413,
                    help="calibrated p_nrfi ceiling for STRONG YRFI (live: 0.413)")
    ap.add_argument("--seed", type=int, default=20260911)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    prices = load_prices()
    srcs: dict[str, int] = {}
    for v in prices.values():
        srcs[v["src"]] = srcs.get(v["src"], 0) + 1
    print(f"prices loaded: {len(prices)} games  " +
          "  ".join(f"{k}={v}" for k, v in sorted(srcs.items())))

    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    for d in (d24, d25):
        d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce").astype("Int64")

    splits = [("2024 (trained on 2025)", d25, d24), ("2025 (trained on 2024)", d24, d25)]

    print("\n" + "=" * 104)
    print(f"COVERAGE FIRST -- gate p_nrfi < {args.gate}; an unpriced bet is DROPPED, never defaulted")
    print(f"  {'split':<24} {'gated':>6} {'priced':>7} {'dropped':>8} {'coverage':>9}")
    keep = {}
    for lab, tr, te in splits:
        p = fit_score(tr, te)
        t = te.assign(p_nrfi=p, p_yrfi=1 - p)
        t = t[t.p_nrfi < args.gate].copy()
        t["price"] = [prices.get(int(g), {}).get("over") if pd.notna(g) else None
                      for g in t.game_pk]
        t["devig"] = [prices.get(int(g), {}).get("devig") if pd.notna(g) else None
                      for g in t.game_pk]
        gated = len(t)
        t = t[t.price.notna()].copy()
        t["won"] = t.y == 1
        keep[lab] = t
        print(f"  {lab:<24} {gated:>6} {len(t):>7} {gated - len(t):>8} "
              f"{len(t) / max(gated, 1):>8.1%}")

    print("\n" + "=" * 104)
    print("MONEY AT REAL PRICES, and the same bets at the -112 placeholder")
    print(f"  {'split':<24} {'bets':>5} {'record':>9} {'hit':>6} {'stated':>7} {'b/e':>6} "
          f"{'flat':>8} {'ROI':>7} {'Kelly':>9} {'flat@-112':>10}")
    for lab, _, _ in splits:
        t = keep[lab]
        if not len(t):
            print(f"  {lab:<24} no priced bets")
            continue
        real = stake_table(t, "price")
        t112 = t.assign(ph=PLACEHOLDER)
        fake = stake_table(t112, "ph")
        w = int(real.won.sum())
        be = real.price.map(breakeven).mean()
        roi = real.flat.sum() / len(real) * 100
        print(f"  {lab:<24} {len(real):>5} {w:>4}-{len(real) - w:<4} {real.won.mean():>6.1%} "
              f"{real.p_yrfi.mean():>7.1%} {be:>6.1%} {real.flat.sum():>+7.2f}u {roi:>+6.1f}% "
              f"{real.kelly.sum():>+8.2f}u {fake.flat.sum():>+9.2f}u")

    print("\n" + "=" * 104)
    print("DAY-LEVEL BOOTSTRAP on flat P&L at real prices (whole slates resampled)")
    for lab, _, _ in splits:
        t = keep[lab]
        if not len(t):
            continue
        real = stake_table(t, "price")
        byday = real.groupby("date").flat.sum()
        days = byday.index.to_numpy()
        vals = byday.to_numpy()
        if not len(days):
            continue
        draws = np.array([vals[rng.integers(0, len(vals), len(vals))].sum()
                          for _ in range(args.boot)])
        print(f"  {lab:<24} {real.flat.sum():>+8.2f}u over {len(days)} slates   "
              f"90% CI [{np.percentile(draws, 5):+.2f}, {np.percentile(draws, 95):+.2f}]   "
              f"P(profitable) = {(draws > 0).mean():.0%}")

    print("\nPooled across both held-out seasons:")
    allb = pd.concat([stake_table(keep[l], "price") for l, _, _ in splits
                      if len(keep[l])], ignore_index=True)
    if len(allb):
        w = int(allb.won.sum())
        be = allb.price.map(breakeven).mean()
        print(f"  {len(allb)} bets  {w}-{len(allb) - w}  hit {allb.won.mean():.1%}  "
              f"model claimed {allb.p_yrfi.mean():.1%}  break-even {be:.1%}  "
              f"flat {allb.flat.sum():+.2f}u  Kelly {allb.kelly.sum():+.2f}u")
        print(f"  the gap that matters: hit {allb.won.mean():.1%} vs claimed "
              f"{allb.p_yrfi.mean():.1%} vs needed {be:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Backtest the WHOLE shipped pipeline, not a model variant.

    two-stage LR  ->  CIR calibrator  ->  gate p_nrfi < 0.42  ->  quarter-Kelly
                                                                  (10u/bet, 15u/day)

Model weights, calibrator and park map are all fit on the TRAINING season(s)
only and applied to the held-out season.  Nothing about the test season leaks
in.

WHAT THIS CAN AND CANNOT ANSWER
-------------------------------
It CANNOT give a real 2024/2025 ROI.  **There are no historical first-inning
odds before 2026-04-29** -- `data/odds/` starts there and the 2024/2025
backtest CSVs carry no price columns at all.  Any ROI quoted for those seasons
is an assumed price wearing a number's clothing.  So this reports the HIT RATE,
which is real, against the break-even a range of price assumptions demands.

That is the honest question: at what price would this system have had to bet
in 2024/2025 to make money -- and did it ever clear the price it actually
gets in 2026 (-112 average, 56.2% break-even)?

The 2026 row is the live ledger: 392 real bets, 55.6% hit, +0.43u.
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

GATE_NRFI = 0.42
KELLY_FRAC = 0.25
CAP_BET = 10.0
CAP_DAY = 15.0


def quarter_kelly(p_win: float, dec_odds: float) -> float:
    """Stake in units (= % of a 100u bank), quarter Kelly, capped per bet."""
    f = (dec_odds * p_win - (1.0 - p_win)) / dec_odds
    return float(np.clip(f * KELLY_FRAC * 100.0, 0.0, CAP_BET))


def run(train: pd.DataFrame, test: pd.DataFrame, price: float):
    park_map, base = build_park(train, 50)
    wt, mt, st = fit_lr(matrix(train, T1_SHIPPED, park_map, base), train["y_t1"].values, 0.05)
    wb, mb, sb = fit_lr(matrix(train, B1_SHIPPED, park_map, base), train["y_b1"].values, 0.05)

    def raw_nrfi(d):
        pt = predict(wt, mt, st, matrix(d, T1_SHIPPED, park_map, base))
        pb = predict(wb, mb, sb, matrix(d, B1_SHIPPED, park_map, base))
        return (1 - pt) * (1 - pb)

    cal = CIRCalibrator.fit(list(raw_nrfi(train)),
                            list((train["y"] == 0).astype(int)), n_bins=20)
    p_nrfi = np.array([cal.predict(float(v)) for v in raw_nrfi(test)])

    t = test.copy()
    t["p_nrfi"] = p_nrfi
    t["p_yrfi"] = 1 - p_nrfi
    t["fires"] = t.p_nrfi < GATE_NRFI          # STRONG YRFI; gate bets the YRFI side
    dec = (100.0 / abs(price)) if price < 0 else (price / 100.0)
    t["stake"] = [quarter_kelly(p, dec) if f else 0.0
                  for p, f in zip(t.p_yrfi, t.fires)]

    # daily cap, applied in the ledger's order (strongest first)
    out = []
    for _, day in t[t.fires].sort_values(["date", "p_nrfi"]).groupby("date"):
        used = 0.0
        for _, r in day.iterrows():
            s = min(r.stake, max(CAP_DAY - used, 0.0))
            used += s
            out.append((r.name, s))
    capped = dict(out)
    t["stake"] = [capped.get(i, 0.0) for i in t.index]

    f = t[t.stake > 0].copy()
    if not len(f):
        return None
    # gate bets YRFI, so win == the first inning scored
    f["won"] = f["y"] == 1
    f["pnl"] = np.where(f.won, f.stake * dec, -f.stake)
    f["flat"] = np.where(f.won, dec, -1.0)
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    bt = ROOT / "data" / "backtests"
    d24 = load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024)
    d25 = load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025)
    d26 = load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"])

    splits = [("2024 (trained on 2025)", d25, d24),
              ("2025 (trained on 2024)", d24, d25),
              ("2026 (trained on 24+25)", pd.concat([d24, d25], ignore_index=True), d26)]

    print("=" * 96)
    print("FULL SHIPPED PIPELINE, HELD-OUT SEASON -- HIT RATE (the part that is real)")
    print(f"  {'season':<24} {'bets':>5} {'bets/day':>9} {'hit rate':>10} {'90% CI':>18}")
    hits = {}
    for lab, tr, te in splits:
        f = run(tr, te, -112.0)     # price only shifts stake size, not which games fire much
        if f is None:
            print(f"  {lab:<24}   no bets"); continue
        n = len(f); h = f.won.mean()
        bs = [f.won.values[rng.integers(0, n, n)].mean() for _ in range(args.boot)]
        lo, hi = np.percentile(bs, [5, 95])
        days = f.date.dt.date.nunique()
        hits[lab] = (n, h, lo, hi, f)
        print(f"  {lab:<24} {n:>5} {n/max(days,1):>9.2f} {h:>10.4f}   [{lo:.4f}, {hi:.4f}]")

    print("\n" + "=" * 96)
    print("WHAT PRICE WOULD IT HAVE NEEDED?  (break-even hit rate by price)")
    prices = [-100, -105, -110, -115, -120, -125, -130, -140]
    print(f"  {'price':>7} {'break-even':>11} " +
          " ".join(f"{lab.split()[0]:>10}" for lab in hits))
    for pr in prices:
        be = -pr / (-pr + 100.0)
        cells = []
        for lab, (n, h, lo, hi, _) in hits.items():
            cells.append(f"{(h-be)*100:>+9.1f}p")
        print(f"  {pr:>7} {be*100:>10.1f}% " + " ".join(f"{c:>10}" for c in cells))
    print("\n  cells = hit rate minus break-even, in percentage points.  Positive = profitable")
    print("  AT THAT PRICE.  The price actually obtained in 2026 is -112 (56.2% break-even).")

    print("\n" + "=" * 96)
    print("AT THE PRICE ACTUALLY OBTAINED (-112), what each season would have returned")
    print(f"  {'season':<24} {'bets':>5} {'flat 1u':>10} {'quarter-Kelly':>15} {'worst 7d (K)':>14}")
    for lab, (n, h, lo, hi, f) in hits.items():
        days = f.groupby(f.date.dt.date).pnl.sum()
        print(f"  {lab:<24} {n:>5} {f.flat.sum():>+9.2f}u {f.pnl.sum():>+14.2f}u "
              f"{days.rolling(7).sum().min():>+13.2f}u")
    print("\n  NOTE: 2024/2025 columns assume a -112 price that was never recorded.")
    print("  No first-inning odds exist before 2026-04-29.  Treat them as hit-rate")
    print("  arithmetic, not as money that could have been won.")

    print("\n" + "=" * 96)
    print("THE ONLY REAL MONEY EVIDENCE: the 2026 live ledger")
    live = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    live = live[(live.pick_strength == "STRONG") & (live.bet_placed == "Y")
                & (live.fi_total_runs.notna())].copy()
    yrfi = (live.fi_total_runs > 0).astype(int)
    live["won"] = np.where(live.pick_side == "YRFI", yrfi == 1, yrfi == 0)
    o = pd.to_numeric(np.where(live.pick_side == "YRFI",
                               live.market_yrfi_odds, live.market_nrfi_odds), errors="coerce")
    live["be"] = np.where(o < 0, -o / (-o + 100), 100 / (o + 100))
    pl = pd.to_numeric(live.profit_loss_units, errors="coerce")
    print(f"  {len(live)} real bets   hit {live.won.mean():.4f}   "
          f"break-even at prices paid {live.be.mean():.4f}   "
          f"edge {(live.won.mean()-live.be.mean())*100:+.2f}pp   P&L {pl.sum():+.2f}u")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

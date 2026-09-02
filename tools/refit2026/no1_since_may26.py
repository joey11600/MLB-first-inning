#!/usr/bin/env python3
"""
"What would the No.1 strategy's units be with the new model?"

Reproduces the dashboard's No.1 figure (dashboard/lib/top-pick.ts +
kelly-sim.ts) from the ledger, then answers the counterfactual under the
IDENTICAL accounting:

  ACCOUNTING (mirrors top-pick.ts exactly)
    - nights from 2026-05-26 (the day the live weights were fit)
    - YRFI only; the night's No.1 = lowest p_nrfi, tiebreak better price
    - the No.1 must have a REAL captured price (market_yrfi_odds) and a result
    - stake = quarter-Kelly on p(YRFI) at that price, cap 10u, banker's
      rounding to whole units with a 0.5u floor (kelly-sim.stakeUnitsFor);
      no edge at the price -> the night is skipped
    - "at Kelly" = sum of +stake*payout / -stake ; "flat" = +payout / -1

  THREE SERIES
    REAL LEDGER   the picks production actually made (should print ~66u)
    OLD, refit    the 19-feature model, L2 0.05, FIT ON WHAT WAS KNOWN ON MAY 26
                  (2024 + 2025 + 2026 games before 05-26), scored 05-26 -> now
    NEW, refit    the shipped 20-feature model, L2 0.50, same fit window

  The OLD-refit vs NEW-refit pair is the apples-to-apples answer: same nights,
  same prices, same stake rule, same information cut-off.  The real ledger
  differs from OLD-refit for reasons unrelated to the weights (lock timing,
  demotions, lineup changes, the 07-28 calibrator swap, gate changes), which
  is why the counterfactual is NOT "66.2u + (NEW - OLD)" but is reported
  alongside it.
"""
from __future__ import annotations

import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration import CIRCalibrator  # noqa: E402
from harness import T1_SHIPPED, B1_SHIPPED, build_park, fit_lr, load, matrix, predict  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

FROM = "2026-05-26"
GATE_NRFI = 0.42


def payout(odds):  return odds / 100 if odds > 0 else 100 / -odds
def implied(odds): return -odds / (-odds + 100) if odds < 0 else 100 / (odds + 100)
def rhe(x, nd=0):  return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_EVEN))


def stake_units_for(p, odds):
    """kelly-sim.stakeUnitsFor, including the deliberate double rounding."""
    b = payout(odds)
    if not (b > 0) or not (0 < p < 1):
        return 0.0
    f = min(max((p * b - (1 - p)) / b, 0.0) * 0.25, 10 / 100)
    stake = f * 100
    if stake < 0.10:
        return 0.0
    stake = rhe(stake, 2)
    r = rhe(stake / 1.0) * 1.0
    if r < 0.5: r = 0.5
    if r > 10: r = stake
    return rhe(r, 2)


def series(df, p_nrfi_col, price_col, label):
    """No.1 per night with the dashboard's rule; returns (table, totals)."""
    d = df.dropna(subset=[p_nrfi_col]).copy()
    d = d[(d.date >= FROM) & (d.pick_yrfi)]
    d["impl"] = d[price_col].map(lambda o: implied(o) if pd.notna(o) and o != 0 else 1.0)
    d = d.sort_values(["date", p_nrfi_col, "impl", "gname"])
    top = d.groupby("date").head(1).copy()
    # the night's No.1 must have a real price and a result; else the night is excluded
    top = top[top[price_col].notna() & (top[price_col] != 0) & top.y.notna()]
    top["stake"] = [stake_units_for(1 - p, o) for p, o in zip(top[p_nrfi_col], top[price_col])]
    no_edge = int((top.stake <= 0).sum())
    top = top[top.stake > 0].copy()
    top["won"] = top.y == 1
    top["kelly"] = np.where(top.won, top.stake * top[price_col].map(payout), -top.stake)
    top["flat"] = np.where(top.won, top[price_col].map(payout), -1.0)
    t = dict(label=label, nights=len(top), W=int(top.won.sum()), L=int((~top.won).sum()),
             hit=top.won.mean() if len(top) else np.nan, kelly=top.kelly.sum(), flat=top.flat.sum(),
             staked=top.stake.sum(), no_edge=no_edge,
             be=top[price_col].map(implied).mean() if len(top) else np.nan)
    return top, t


def main() -> int:
    led = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    led["date"] = led.date.astype(str).str[:10]
    led["gname"] = led.away_team.astype(str) + "@" + led.home_team.astype(str)
    led["y"] = np.where(led.fi_total_runs.notna(), (led.fi_total_runs > 0).astype(float), np.nan)
    led["price"] = pd.to_numeric(led.market_yrfi_odds, errors="coerce")
    led["nrfi_prob"] = pd.to_numeric(led.nrfi_prob, errors="coerce")

    # ---------- REAL LEDGER: production's own picks ----------
    real = led[(led.bet_placed.astype(str).str.strip().str.upper() == "Y")].copy()
    real["pick_yrfi"] = real.pick_side.astype(str).str.strip() == "YRFI"
    _, t_real = series(real, "nrfi_prob", "price", "REAL LEDGER (what the dashboard shows)")

    # ---------- REFIT counterfactuals: what a MAY-26 fit would have known ----------
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    # The ledger has carried its own home/away_fi_xwoba since 2026-08-23 (the
    # production pool value, frozen with the bet).  attach() merges the factor
    # file on game_pk, so leaving those columns in place produces _x/_y
    # suffixes and a KeyError downstream; and the factor file is a one-off
    # dump (last row 2026-08-22), so games after it would silently mean-fill.
    # Prefer the ledger's own value, fall back to the factor file.
    led26 = load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)
    own = {c: pd.to_numeric(led26[c], errors="coerce") for c in ("home_fi_xwoba", "away_fi_xwoba") if c in led26.columns}
    d26 = attach(led26.drop(columns=list(own)), fac)
    for c, v in own.items():
        d26[c] = v.values if c not in d26.columns else v.fillna(d26[c]).values
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    tr = pd.concat([d24, d25, d26[d26.date < FROM]], ignore_index=True)
    te = d26[d26.date >= FROM].copy()
    te["gname"] = te.away_team.astype(str) + "@" + te.home_team.astype(str)
    te["price"] = pd.to_numeric(te.market_yrfi_odds, errors="coerce")
    te["pick_yrfi"] = True

    def fit_score(t1f, b1f, l2):
        trc, tec = tr.copy(), te.copy()
        for c in [x for x in t1f + b1f if x.endswith("fi_xwoba")]:
            mu = trc[c].mean(); trc[c] = trc[c].fillna(mu); tec[c] = tec[c].fillna(mu)
        pk, b0 = build_park(trc, 50)
        wt, mt, st = fit_lr(matrix(trc, t1f, pk, b0), trc.y_t1.values, l2)
        wb, mb, sb = fit_lr(matrix(trc, b1f, pk, b0), trc.y_b1.values, l2)
        raw = lambda d: (1 - predict(wt, mt, st, matrix(d, t1f, pk, b0))) * (1 - predict(wb, mb, sb, matrix(d, b1f, pk, b0)))
        cal = CIRCalibrator.fit(list(raw(trc)), list((trc.y == 0).astype(int)), n_bins=20)
        return np.array([cal.predict(float(v)) for v in raw(tec)])

    te["p_old"] = fit_score(T1_SHIPPED, B1_SHIPPED, 0.05)
    te["p_new"] = fit_score(T1_SHIPPED + ["home_fi_xwoba"], B1_SHIPPED + ["away_fi_xwoba"], 0.50)
    # the gate decides which games are bets; the No.1 is chosen among them
    old_bets = te[te.p_old < GATE_NRFI]; new_bets = te[te.p_new < GATE_NRFI]
    top_old, t_old = series(old_bets, "p_old", "price", "OLD model, refit on what was known 05-26")
    top_new, t_new = series(new_bets, "p_new", "price", "NEW model, refit on what was known 05-26")

    print(f"No.1 strategy, nights from {FROM}, YRFI only, real captured price required, "
          f"quarter-Kelly with production rounding (cap 10u, floor 0.5u)\n")
    print(f"  {'series':<44} {'nights':>6} {'record':>9} {'hit':>6} {'break-even':>10} {'AT KELLY':>10} {'FLAT 1u':>9} {'staked':>8}  skipped(no edge)")
    for t in (t_real, t_old, t_new):
        print(f"  {t['label']:<44} {t['nights']:>6} {t['W']:>4}-{t['L']:<4} {t['hit']:>6.3f} {t['be']*100:>9.1f}% "
              f"{t['kelly']:>+9.2f}u {t['flat']:>+8.2f}u {t['staked']:>7.1f}u  {t['no_edge']}")

    # month by month for the two refits and the ledger
    print("\n  by month (nights W-L, at Kelly):")
    def bym(top, lab):
        top = top.copy(); top["m"] = top.date.str[:7]
        g = top.groupby("m").agg(n=("won", "size"), W=("won", "sum"), k=("kelly", "sum"))
        print(f"    {lab:<14} " + "  ".join(f"{m[-2:]}: {int(r.W)}-{int(r.n - r.W)} {r.k:+.1f}u" for m, r in g.iterrows()))
    real_top, _ = series(real, "nrfi_prob", "price", "real")
    bym(real_top, "REAL LEDGER"); bym(top_old, "OLD refit"); bym(top_new, "NEW refit")

    # same-night comparison, OLD refit vs NEW refit
    both = top_old.set_index("date")[["kelly", "flat", "won"]].join(
        top_new.set_index("date")[["kelly", "flat", "won"]], lsuffix="_old", rsuffix="_new", how="inner")
    print(f"\n  nights where BOTH refits had a No.1: {len(both)}   "
          f"OLD {both.won_old.mean():.3f} / {both.kelly_old.sum():+.2f}u   NEW {both.won_new.mean():.3f} / {both.kelly_new.sum():+.2f}u")
    rng = np.random.default_rng(20260822)
    d = np.array([(both.kelly_new.values[i] - both.kelly_old.values[i]).sum()
                  for i in (rng.integers(0, len(both), len(both)) for _ in range(3000))])
    print(f"  slate-night bootstrap, NEW - OLD at Kelly: {d.mean():+.2f}u  90% CI [{np.percentile(d,5):+.2f}, {np.percentile(d,95):+.2f}]  P(NEW better)={(d>0).mean():.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

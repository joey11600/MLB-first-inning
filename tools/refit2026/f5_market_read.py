#!/usr/bin/env python3
"""f5_market_read.py -- do our inputs beat the F5 (first-five-innings) market?

WHY (2026-09-05).  target_horizon.py (2026-08-21) showed the same 19-20 inputs
rank runs-through-3 and runs-through-5 far better than the first inning alone
(AUC 0.57-0.58 vs 0.52 on 2026), and asked the question nobody could answer
then: does that translate into beating the F5 MARKET?  No F5 prices existed.
odds_diagnostic.yml has since captured eight books' F5 totals on 12 of the last
13 days, and fetch_linescores_full.py now covers every graded 2026 game.

WHAT THIS DOES
  1. Fits a Poisson regression for RUNS THROUGH FIVE on the v3 inputs (the
     union of both halves' feature lists), 2024+2025 only, and predicts a mean
     for each 2026 game.  L2 fixed at one value, chosen before looking.
  2. For every priced 2026 game (its own-date snapshot, last one per book),
     de-vigs each book's Over/Under at the modal line, takes the consensus as
     the market's P(over), and turns the model's mean into P(over) with the
     Poisson tail (pushes on whole-number lines excluded).
  3. Compares the two on the same games: AUC, log-loss, paired bootstrap of
     the AUC gap, a calibration line, and a pre-declared flat-1u bet
     simulation at the best available price when model minus market exceeds
     3 / 5 points.
  4. Runs the identical comparison on the FIRST-INNING market for the same
     games with the LIVE model's calibrated probability -- the control, where
     the answer is already known (the market ranks better).

HONESTY.  ~150 games is a first read, not a verdict.  Two thresholds and one
L2 are declared up front so there is no grid to price with a null; a bigger
sample is a matter of the capture running.  Writes nothing.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, load, matrix  # noqa: E402
from test_fi_pooled import attach                                          # noqa: E402

pd.set_option("display.width", 240)
LS = ROOT / "data" / "cache" / "linescore_full"
ODDS = sorted(glob.glob(str(ROOT / "data" / "diagnostics" / "odds" / "raw_*.csv")))
L2_ALPHA = 1.0                      # fixed before looking
EDGE_THRESHOLDS = (0.03, 0.05)      # pre-declared
ET = ZoneInfo("America/New_York")


def implied(o: float) -> float:
    return -o / (-o + 100) if o < 0 else 100 / (o + 100)


def payout(o: float) -> float:
    return o / 100 if o > 0 else 100 / -o


def runs_through(pk: int, n: int) -> float | None:
    f = LS / f"{pk}.json"
    if not f.exists():
        return None
    inn = json.loads(f.read_text(encoding="utf-8")).get("innings") or []
    if len(inn) < n:
        return None
    return float(sum((i.get("away") or 0) + (i.get("home") or 0) for i in inn[:n]))


def ld(path: Path, park_col: str, season: int) -> pd.DataFrame:
    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    d = load(path, park_col, season)
    own = {c: pd.to_numeric(d[c], errors="coerce") for c in ("home_fi_xwoba", "away_fi_xwoba") if c in d.columns}
    d = attach(d.drop(columns=list(own)), fac)
    for c, v in own.items():
        d[c] = v.fillna(d[c]).values
    d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
    d["r5"] = [runs_through(int(pk), 5) if pd.notna(pk) else None for pk in d["game_pk"]]
    return d


def main() -> int:
    bt = ROOT / "data" / "backtests"
    d24 = ld(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024)
    d25 = ld(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025)
    d26 = ld(ROOT / "data" / "picks_2026.csv", "home_team", 2026)
    for lab, d in (("2024", d24), ("2025", d25), ("2026", d26)):
        print(f"  {lab}: {len(d)} games, runs-through-5 known for {d.r5.notna().mean() * 100:.1f}%, mean R5 {d.r5.mean():.2f}")

    feats = list(dict.fromkeys(T1_SHIPPED + ["home_fi_xwoba"] + B1_SHIPPED + ["away_fi_xwoba"]))
    feats = [f for f in feats if f != "era_gap_b1"]          # = -era_gap_t1
    tr = pd.concat([d24, d25], ignore_index=True)
    tr = tr[tr.r5.notna()].copy()
    for c in ("home_fi_xwoba", "away_fi_xwoba"):
        mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); d26[c] = d26[c].fillna(mu)
    pk, b0 = build_park(tr, 50)
    Xtr = matrix(tr, feats, pk, b0); m, s = Xtr.mean(0), Xtr.std(0); s[s < 1e-12] = 1.0
    reg = PoissonRegressor(alpha=L2_ALPHA, max_iter=2000).fit((Xtr - m) / s, tr.r5.values)
    te = d26[d26.r5.notna()].copy()
    te["mu5"] = reg.predict((matrix(te, feats, pk, b0) - m) / s)
    print(f"\n  Poisson R5 model on {len(tr)} train games -> 2026: predicted mean {te.mu5.mean():.2f} vs actual {te.r5.mean():.2f}; "
          f"AUC of mu5 vs 'R5 > 2026 median ({te.r5.median():.0f})': {auc((te.r5 > te.r5.median()).astype(int).values, te.mu5.values):.4f}")

    # ---------------- the F5 market on its own-date snapshot ----------------
    od = pd.concat([pd.read_csv(f) for f in ODDS], ignore_index=True)
    od["cap"] = pd.to_datetime(od.captured_at_utc, utc=True)
    od["start"] = pd.to_datetime(od.commence_time, utc=True)
    od["date"] = od.start.dt.tz_convert(ET).dt.strftime("%Y-%m-%d")
    od["capday"] = od.cap.dt.tz_convert(ET).dt.strftime("%Y-%m-%d")
    od = od[od.capday == od.date]                                     # own-day snapshot only
    od = od.sort_values("cap").groupby(["date", "away_team", "home_team", "book", "market", "point", "outcome"]).tail(1)
    od["imp"] = od.price.apply(implied)
    te["date"] = pd.to_datetime(te["date"]).dt.strftime("%Y-%m-%d")
    key = ["date", "away_team", "home_team"]

    def market_frame(mkt: str) -> pd.DataFrame:
        f = od[od.market == mkt]
        w = f.pivot_table(index=key + ["book", "point"], columns="outcome", values=["imp", "price"]).dropna()
        w.columns = [f"{a}_{b}" for a, b in w.columns]
        w = w.reset_index()
        w["p_over"] = w.imp_Over / (w.imp_Over + w.imp_Under)           # de-vig
        # modal line per game, then consensus + best prices across books
        modal = w.groupby(key).point.agg(lambda x: x.mode().iloc[0]).rename("line").reset_index()
        w = w.merge(modal, on=key); w = w[w.point == w.line]
        g = w.groupby(key).agg(line=("line", "first"), mkt_over=("p_over", "mean"), books=("book", "nunique"),
                               best_over=("price_Over", "max"), best_under=("price_Under", "max"),
                               be_over=("imp_Over", "min"), be_under=("imp_Under", "min")).reset_index()
        return g

    f5 = market_frame("totals_1st_5_innings").merge(te[key + ["mu5", "r5", "game_pk"]], on=key, how="inner")
    f5 = f5.drop_duplicates(subset=key)
    lo = np.floor(f5.line)
    f5["p_gt"] = 1 - poisson.cdf(lo, f5.mu5)                         # P(R5 > line) for x.5 lines
    f5["p_lt"] = poisson.cdf(lo - 1, f5.mu5)
    whole = (f5.line % 1 == 0)
    f5.loc[whole, "p_lt"] = poisson.cdf(f5.line[whole] - 1, f5.mu5[whole])
    f5["model_over"] = np.where(whole, f5.p_gt / (f5.p_gt + f5.p_lt), f5.p_gt)
    f5["y"] = np.where(f5.r5 > f5.line, 1, np.where(f5.r5 < f5.line, 0, -1))
    f5 = f5[f5.y >= 0].copy()
    print(f"\n=== F5 TOTAL: {len(f5)} priced 2026 games matched to a result (own-day snapshot, modal line), lines {f5.line.value_counts().sort_index().to_dict()}")
    print(f"  actual over rate {f5.y.mean():.3f} | market consensus P(over) mean {f5.mkt_over.mean():.3f} | model P(over) mean {f5.model_over.mean():.3f}")
    y = f5.y.values
    def ll(p): p = np.clip(p, 1e-6, 1 - 1e-6); return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    a_m, a_k = auc(y, f5.model_over.values), auc(y, f5.mkt_over.values)
    print(f"  AUC  model {a_m:.4f}   market {a_k:.4f}   |   log-loss  model {ll(f5.model_over.values):.4f}   market {ll(f5.mkt_over.values):.4f}")
    rng = np.random.default_rng(5); idx = np.arange(len(y)); dif = []
    for _ in range(3000):
        b = rng.choice(idx, len(idx), replace=True)
        if y[b].min() == y[b].max(): continue
        dif.append(auc(y[b], f5.model_over.values[b]) - auc(y[b], f5.mkt_over.values[b]))
    dif = np.array(dif)
    print(f"  paired bootstrap, model minus market AUC: {dif.mean():+.4f}  90% CI [{np.percentile(dif, 5):+.4f}, {np.percentile(dif, 95):+.4f}]  P(model better) = {(dif > 0).mean():.0%}")
    f5["bin"] = pd.cut(f5.model_over, [0, .4, .5, .6, 1.0])
    print("  model calibration (P(over) band -> actual over rate):")
    print(f5.groupby("bin", observed=True).agg(n=("y", "size"), model=("model_over", "mean"), market=("mkt_over", "mean"), actual=("y", "mean")).round(3).to_string())
    for t in EDGE_THRESHOLDS:
        pnl = [];
        for _, r in f5.iterrows():
            e = r.model_over - r.mkt_over
            if e > t:
                pnl.append(payout(r.best_over) if r.y == 1 else -1.0)
            elif e < -t:
                pnl.append(payout(r.best_under) if r.y == 0 else -1.0)
        n = len(pnl); w = sum(1 for x in pnl if x > 0)
        print(f"  bet at best price when |model - market| > {t:.2f}: {n} bets, {w}-{n - w}, flat {sum(pnl):+.2f}u")

    # ---------------- CONTROL: the first-inning market vs the LIVE model ----------------
    f1 = market_frame("totals_1st_1_innings")
    f1 = f1[f1.line == 0.5].merge(te[key + ["yrfi_prob", "y", "game_pk"]].rename(columns={"y": "yrfi"}), on=key, how="inner").drop_duplicates(subset=key)
    f1["live"] = pd.to_numeric(f1.yrfi_prob, errors="coerce"); f1 = f1.dropna(subset=["live"])
    yy = f1.yrfi.values.astype(int)
    print(f"\n=== CONTROL, FIRST-INNING TOTAL on the same days: {len(f1)} games")
    print(f"  AUC  live model {auc(yy, f1.live.values):.4f}   market consensus {auc(yy, f1.mkt_over.values):.4f}   (known result: the market ranks better)")
    for t in EDGE_THRESHOLDS:
        pnl = []
        for _, r in f1.iterrows():
            e = r.live - r.mkt_over
            if e > t: pnl.append(payout(r.best_over) if r.yrfi == 1 else -1.0)
            elif e < -t: pnl.append(payout(r.best_under) if r.yrfi == 0 else -1.0)
        n = len(pnl); w = sum(1 for x in pnl if x > 0)
        print(f"  bet at best price when |live - market| > {t:.2f}: {n} bets, {w}-{n - w}, flat {sum(pnl):+.2f}u")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

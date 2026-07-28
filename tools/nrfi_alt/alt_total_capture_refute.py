#!/usr/bin/env python3
"""
tools/nrfi_alt/alt_total_capture_refute.py

REFUTATION TARGET
-----------------
Proposal: start capturing DK's posted GAME TOTAL (and F5 total) alongside
the 1st-inning NRFI/YRFI prices, on the theory that the market total would
be "the first genuinely independent market-consensus variable in the
dataset" and would let us finally answer "does the NRFI take vary by total
line".

This script attacks the proposal's PREMISE, not its plumbing:

  T1. Baseline. Re-derive the NRFI pricing wall on real captured DK prices.
  T2. The premise is false: the ledger ALREADY contains a market-consensus
      variable, and one that is strictly closer to the bet -- the de-vigged
      DK 1st-inning NRFI price itself. Partition NRFI P&L by it.
  T3. Best available stand-in for the market total (sum of the two clubs'
      season runs/game, park-scaled).  Partition NRFI P&L by it.
  T4. Does the total-proxy add anything the ledger does not already have?
      (correlation / incremental logistic fit vs first-inning outcome)
  T5. Out-of-sample: time split within 2026, plus 2025 backtest
      (prediction-only, no odds) for the same partition.
  T6. 10-cent worse-pricing stress on every cell.
  T7. Power: how much captured-total data would be needed for the test the
      proposal wants to enable to be able to detect anything.

ANALYSIS ONLY.  Reads CSVs; writes nothing.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

RNG = np.random.default_rng(20260728)
NBOOT = 4000


# ---------------------------------------------------------------- helpers
def payout(o: float) -> float:
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o: float) -> float:
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def worsen(o: float, cents: float = 10.0) -> float:
    """Move an American price `cents` worse for the bettor."""
    if o > 0:
        o2 = o - cents
        if o2 < 100:                 # cross through the +100/-100 seam
            o2 = -(100 + (100 - o2))
        return o2
    return o - cents


def fnum(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        s = str(v).strip().replace("−", "-")
        if s in ("", "None", "nan"):
            return None
        return float(s)
    except Exception:
        return None


def day_block_boot(df: pd.DataFrame, valcol: str, n: int = NBOOT):
    """Block bootstrap over DAYS.  Returns (mean, lo, hi) of mean(valcol)."""
    days = df["date"].unique()
    by = {d: df.loc[df["date"] == d, valcol].to_numpy() for d in days}
    k = len(days)
    if k < 3:
        return (float(df[valcol].mean()), float("nan"), float("nan"))
    out = np.empty(n)
    for i in range(n):
        pick = RNG.integers(0, k, k)
        cat = np.concatenate([by[days[j]] for j in pick])
        out[i] = cat.mean()
    return (float(df[valcol].mean()),
            float(np.percentile(out, 2.5)),
            float(np.percentile(out, 97.5)))


def cell_report(df, label, stress_cents=10.0):
    n = len(df)
    if n == 0:
        return None
    hit = df["nrfi_win"].mean()
    be = df["be_nrfi"].mean()
    roi, lo, hi = day_block_boot(df, "pnl_nrfi")
    roi_s = df["pnl_nrfi_stress"].mean()
    return dict(cell=label, n=n, days=df["date"].nunique(),
                hit=hit, be=be, gap_pp=(hit - be) * 100,
                roi=roi * 100, lo=lo * 100, hi=hi * 100,
                roi_10c=roi_s * 100)


def print_table(rows, title):
    print(f"\n{title}")
    print(f"{'cell':<28}{'n':>6}{'days':>6}{'NRFI%':>8}{'BE%':>8}"
          f"{'gap pp':>8}{'ROI%':>8}{'CI lo':>8}{'CI hi':>8}{'ROI-10c':>9}")
    for r in rows:
        if r is None:
            continue
        print(f"{r['cell']:<28}{r['n']:>6}{r['days']:>6}{r['hit']*100:>8.1f}"
              f"{r['be']*100:>8.1f}{r['gap_pp']:>8.2f}{r['roi']:>8.2f}"
              f"{r['lo']:>8.2f}{r['hi']:>8.2f}{r['roi_10c']:>9.2f}")


# ---------------------------------------------------------------- load
def load_priced():
    p = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    p["mo"] = p["market_nrfi_odds"].map(fnum)
    p["yo"] = p["market_yrfi_odds"].map(fnum)
    p["fi"] = p["fi_total_runs"].map(fnum)
    d = p[p["mo"].notna() & p["yo"].notna() & p["fi"].notna()].copy()
    d = d[d["graded_result"].isin(["WIN", "LOSS", "PASS"])]
    d["nrfi_win"] = (d["fi"] == 0).astype(float)

    d["be_nrfi"] = d["mo"].map(implied)              # vigged break-even
    d["pnl_nrfi"] = np.where(d["nrfi_win"] == 1,
                             d["mo"].map(payout), -1.0)
    ws = d["mo"].map(lambda o: worsen(o, 10.0))
    d["pnl_nrfi_stress"] = np.where(d["nrfi_win"] == 1,
                                    ws.map(payout), -1.0)

    # de-vigged market consensus on NRFI (multiplicative / proportional)
    ii = d["mo"].map(implied)
    jj = d["yo"].map(implied)
    d["mkt_p_nrfi"] = ii / (ii + jj)
    d["mkt_vig"] = ii + jj - 1.0

    # best available stand-in for a full-game market total:
    # the two clubs' season runs-per-game, park scaled.
    d["rpg_sum"] = d["away_rpg"].map(fnum) + d["home_rpg"].map(fnum)
    pf = d["park_factor"].map(fnum).fillna(1.0)
    d["proxy_total"] = d["rpg_sum"] * pf
    return d


def qcut_label(s, q, prefix):
    try:
        cats = pd.qcut(s, q, duplicates="drop")
    except ValueError:
        return None
    return cats.map(lambda c: f"{prefix} {c.left:.3f}-{c.right:.3f}"
                    if pd.notna(c) else "na")


# ---------------------------------------------------------------- main
def main():
    d = load_priced()
    print("=" * 96)
    print("T1  BASELINE -- bet NRFI on every real-priced graded 2026 game")
    print("=" * 96)
    base = cell_report(d, "ALL real-priced")
    print_table([base], "")
    print(f"\n  n={base['n']} over {base['days']} days.  "
          f"mean DK vig on the 1st-inning market = {d['mkt_vig'].mean()*100:.2f}%")
    print(f"  hit {base['hit']*100:.2f}%  vs break-even {base['be']*100:.2f}%  "
          f"=> wall = {base['be']*100 - base['hit']*100:.2f} pp")
    devig_gap = (d["mkt_p_nrfi"].mean() - d["nrfi_win"].mean()) * 100
    print(f"  NO-VIG check: market de-vigged NRFI prob mean = "
          f"{d['mkt_p_nrfi'].mean()*100:.2f}% vs actual {d['nrfi_win'].mean()*100:.2f}% "
          f"=> {devig_gap:.2f} pp still against us with ALL vig stripped")

    # ---------------------------------------------------------------- T2
    print("\n" + "=" * 96)
    print("T2  THE PREMISE.  The ledger ALREADY has a market-consensus variable:")
    print("    the de-vigged DK 1st-inning NRFI price.  Partition the take by it.")
    print("=" * 96)
    searched = 0
    for q in (3, 5):
        lab = qcut_label(d["mkt_p_nrfi"], q, "mktP")
        if lab is None:
            continue
        rows = []
        for name, g in d.assign(_c=lab).groupby("_c", observed=True):
            rows.append(cell_report(g, str(name)))
            searched += 1
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: r["cell"])
        print_table(rows, f"-- de-vigged market NRFI prob, {q} buckets")

    # ---------------------------------------------------------------- T3
    print("\n" + "=" * 96)
    print("T3  STAND-IN FOR THE PROPOSED MARKET TOTAL")
    print("    proxy_total = (away season R/G + home season R/G) * park_factor")
    print("=" * 96)
    dd = d[d["proxy_total"].notna()].copy()
    print(f"  coverage: {len(dd)}/{len(d)} priced games have both clubs' R/G")
    print(f"  proxy_total: mean {dd['proxy_total'].mean():.2f} "
          f"sd {dd['proxy_total'].std():.2f} "
          f"range {dd['proxy_total'].min():.2f}-{dd['proxy_total'].max():.2f}")
    for q in (2, 3, 4, 5):
        lab = qcut_label(dd["proxy_total"], q, "tot")
        if lab is None:
            continue
        rows = []
        for name, g in dd.assign(_c=lab).groupby("_c", observed=True):
            rows.append(cell_report(g, str(name)))
            searched += 1
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: r["cell"])
        print_table(rows, f"-- proxy market total, {q} buckets")

    print(f"\n  CELLS SEARCHED SO FAR: {searched}")

    # ---------------------------------------------------------------- T4
    print("\n" + "=" * 96)
    print("T4  DOES A TOTAL ADD INFORMATION THE LEDGER LACKS?")
    print("=" * 96)
    sub = dd[["mkt_p_nrfi", "proxy_total", "nrfi_win"]].dropna().copy()
    sub["model_p"] = pd.to_numeric(dd.loc[sub.index, "nrfi_prob"], errors="coerce")
    sub["lam"] = pd.to_numeric(dd.loc[sub.index, "lambda_lr_total"], errors="coerce")
    sub = sub.dropna()
    c = sub.corr(method="pearson")
    print("\n  Pearson correlations (n=%d):" % len(sub))
    print(c.round(3).to_string())

    # incremental logistic: outcome ~ market price, then + proxy_total
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score, log_loss

        y = sub["nrfi_win"].to_numpy()
        for name, cols in [("mkt price only", ["mkt_p_nrfi"]),
                           ("total proxy only", ["proxy_total"]),
                           ("mkt price + total", ["mkt_p_nrfi", "proxy_total"]),
                           ("model p only", ["model_p"]),
                           ("model p + total", ["model_p", "proxy_total"])]:
            X = sub[cols].to_numpy()
            X = (X - X.mean(0)) / X.std(0)
            m = LogisticRegression(max_iter=2000).fit(X, y)
            pr = m.predict_proba(X)[:, 1]
            print(f"   {name:<22} IN-SAMPLE auc={roc_auc_score(y, pr):.4f} "
                  f"logloss={log_loss(y, pr):.5f}  coef={np.round(m.coef_[0],4)}")
    except ImportError:
        print("   (sklearn unavailable -- skipping incremental fit)")

    # ---------------------------------------------------------------- T5
    print("\n" + "=" * 96)
    print("T5  OUT-OF-SAMPLE / STABILITY")
    print("=" * 96)
    dd = dd.sort_values("date")
    cut = dd["date"].quantile(0.5) if dd["date"].dtype != object else \
        sorted(dd["date"].unique())[len(dd["date"].unique()) // 2]
    early = dd[dd["date"] < cut]
    late = dd[dd["date"] >= cut]
    print(f"  time split at {cut}: early n={len(early)}  late n={len(late)}")
    for tag, part in (("EARLY", early), ("LATE", late)):
        lab = qcut_label(dd["proxy_total"], 3, "tot")   # bins fixed on full yr
        rows = []
        p2 = part.assign(_c=lab.loc[part.index])
        for name, g in p2.groupby("_c", observed=True):
            rows.append(cell_report(g, str(name)))
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: r["cell"])
        print_table(rows, f"-- proxy total terciles, {tag} half of 2026")

    # 2025 backtest: prediction-only heterogeneity (no odds exist)
    b = pd.read_csv(
        ROOT / "data" / "backtests" /
        "backtest_2025-04-01_to_2025-09-30_truepit.csv", low_memory=False)
    b["fi"] = pd.to_numeric(b["fi_total_runs"], errors="coerce")
    b = b[b["fi"].notna()].copy()
    b["nrfi_win"] = (b["fi"] == 0).astype(float)
    b["proxy_total"] = (pd.to_numeric(b["away_rpg"], errors="coerce") +
                        pd.to_numeric(b["home_rpg"], errors="coerce")) * \
        pd.to_numeric(b["park_factor"], errors="coerce").fillna(1.0)
    b["model_p"] = pd.to_numeric(b["nrfi_prob"], errors="coerce")
    b = b.dropna(subset=["proxy_total", "model_p"])
    print(f"\n-- 2025 backtest (n={len(b)}, NO ODDS: prediction only)")
    b["_c"] = qcut_label(b["proxy_total"], 5, "tot")
    print(f"{'cell':<28}{'n':>6}{'pred NRFI%':>12}{'actual NRFI%':>14}{'resid pp':>10}")
    for name, g in b.groupby("_c", observed=True):
        print(f"{str(name):<28}{len(g):>6}{g['model_p'].mean()*100:>12.2f}"
              f"{g['nrfi_win'].mean()*100:>14.2f}"
              f"{(g['nrfi_win'].mean()-g['model_p'].mean())*100:>10.2f}")

    print(f"\n-- same on 2026 priced set (n={len(dd)})")
    dd = dd.copy()
    dd["model_p"] = pd.to_numeric(dd["nrfi_prob"], errors="coerce")
    dd["_c"] = qcut_label(dd["proxy_total"], 5, "tot")
    print(f"{'cell':<28}{'n':>6}{'pred NRFI%':>12}{'actual NRFI%':>14}{'resid pp':>10}")
    for name, g in dd.groupby("_c", observed=True):
        print(f"{str(name):<28}{len(g):>6}{g['model_p'].mean()*100:>12.2f}"
              f"{g['nrfi_win'].mean()*100:>14.2f}"
              f"{(g['nrfi_win'].mean()-g['model_p'].mean())*100:>10.2f}")

    # ---------------------------------------------------------------- T7
    print("\n" + "=" * 96)
    print("T7  POWER -- what the proposal would buy, and when")
    print("=" * 96)
    per_season = len(d)
    print(f"  real-priced graded games captured in 2026 to date: {per_season}")
    p0 = float(d["nrfi_win"].mean())
    be = float(d["be_nrfi"].mean())
    need = be - p0
    print(f"  a total-defined subgroup must beat the base NRFI rate by "
          f"{need*100:.2f} pp just to break even.")
    for k in (2, 3, 4, 5):
        n_cell = per_season / k
        se = math.sqrt(0.25 / n_cell) * 100
        print(f"   {k} total buckets -> n/cell {n_cell:.0f}/season, "
              f"SE(hit rate) {se:.2f} pp; to prove a cell CLEARS break-even at "
              f"95% conf it must be observed at >= {be*100 + 1.96*se:.1f}% "
              f"(base is {p0*100:.1f}%)")
    # seasons needed for a 1-season-detectable true edge of +2pp over BE
    for eff in (2.0, 4.0):
        n_req = (1.96 + 0.84) ** 2 * 0.25 / ((eff / 100) ** 2)
        print(f"   to detect a TRUE +{eff:.0f} pp-over-break-even subgroup at "
              f"80% power: n={n_req:.0f} games IN THAT SUBGROUP "
              f"({n_req/(per_season/3):.1f} seasons at 1/3 of the slate)")

    print(f"\n  TOTAL CELLS SEARCHED IN THIS SCRIPT: {searched}")


if __name__ == "__main__":
    main()

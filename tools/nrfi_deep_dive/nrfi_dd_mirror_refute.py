#!/usr/bin/env python3
"""tools/nrfi_dd_mirror_refute.py -- adversarial test of the "MIRROR TRADE".

RULE UNDER TEST: bet YRFI at the captured DK YRFI price whenever p_nrfi >= 0.50.
CLAIM: 324 bets, 53.7% hit vs 50.6% break-even.

Read-only. Analysis only.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
BT = ROOT / "data" / "backtests"
SEED, B = 20260728, 20000


def imp(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


def shade(american, cents):
    """Move the price `cents` AGAINST the bettor (worse)."""
    n = float(american)
    if n > 0:
        n -= cents
        if n < 100:                      # crossed the +100/-100 boundary
            n = -(100 + (100 - n))
    else:
        n -= cents
    return n


def load_live():
    raw = list(csv.DictReader(open(PICKS, encoding="utf-8")))
    seen = {}
    for r in raw:
        seen[(r.get("game_pk", ""), r.get("game_number", ""))] = r
    rows = []
    for r in seen.values():
        try:
            fa = int(float(r["fi_away_runs"]))
            fh = int(float(r["fi_home_runs"]))
            m = float(r["nrfi_prob"])
        except (ValueError, TypeError, KeyError):
            continue
        iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
        pY = payout(r.get("market_yrfi_odds"))
        if not iN or not iY or pY is None:
            continue
        nrfi = 1 if (fa + fh) == 0 else 0
        rows.append({
            "date": r["date"], "model": m, "nrfi": nrfi,
            "yrfi_odds": float(r["market_yrfi_odds"]),
            "be_yrfi": iY, "pl": (-1.0 if nrfi else pY),
            "mkt": iN / (iN + iY),
            "strength": r.get("pick_strength", ""),
            "side": r.get("pick_side", ""),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def boot_days(rows, key="pl", seed=SEED, b=B):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r[key])
    days = list(byday.keys())
    if len(days) < 5:
        return float("nan"), float("nan")
    sums = np.array([sum(byday[d]) for d in days], float)
    cnts = np.array([len(byday[d]) for d in days], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(b, len(days)))
    m = sums[idx].sum(1) / np.maximum(cnts[idx].sum(1), 1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def line(label, rows, key="pl", ci=True):
    if not rows:
        print(f"  {label:<40} n=0")
        return
    n = len(rows)
    w = sum(1 - r["nrfi"] for r in rows)
    pl = sum(r[key] for r in rows)
    be = sum(r["be_yrfi"] for r in rows) / n
    nd = len({r["date"] for r in rows})
    s = (f"  {label:<40} n={n:>4} d={nd:>3}  hit {w/n*100:>5.2f}% "
         f"(need {be*100:5.2f}%)  ROI {pl/n*100:>+6.2f}%  P&L {pl:>+7.2f}u")
    if ci:
        lo, hi = boot_days(rows, key)
        s += f"  dayCI[{lo*100:+.1f}%,{hi*100:+.1f}%]"
    print(s)


def main():
    rows = load_live()
    print("=" * 128)
    print("  REFUTATION TEST -- 'MIRROR TRADE': bet YRFI at captured DK price when p_nrfi >= 0.50")
    print("=" * 128)
    days = sorted({r["date"] for r in rows})
    print(f"  priced+graded games: {len(rows)}   slates: {len(days)}   "
          f"{days[0]} .. {days[-1]}")

    sel = [r for r in rows if r["model"] >= 0.50]
    print("\n--- 1. RE-DERIVE THE CLAIM (real captured YRFI prices) ---")
    line("ALL priced games (bet YRFI)", rows)
    line("MIRROR: p_nrfi >= 0.50 (the rule)", sel)
    line("complement: p_nrfi < 0.50", [r for r in rows if r["model"] < 0.50])
    print(f"  mean model p_nrfi in the selection : {np.mean([r['mkt'] and r['model'] for r in sel]):.4f}")
    print(f"  ACTUAL NRFI rate in the selection  : {np.mean([r['nrfi'] for r in sel]):.4f}")
    print(f"  mean devigged MARKET p_nrfi        : {np.mean([r['mkt'] for r in sel]):.4f}")

    print("\n--- 2. PRICE ROBUSTNESS (shade the YRFI price against us) ---")
    for c in (0, 5, 10, 15, 20):
        pl = 0.0
        for r in sel:
            p = payout(str(shade(r["yrfi_odds"], c)))
            pl += (-1.0 if r["nrfi"] else p)
        print(f"  YRFI price {c:>2} cents worse : ROI {pl/len(sel)*100:>+6.2f}%  P&L {pl:>+7.2f}u")

    print("\n--- 3. TIME STABILITY (is it one hot stretch?) ---")
    bym = defaultdict(list)
    for r in sel:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        line(f"month {m}", bym[m], ci=False)
    # leave-one-month-out
    print("  leave-one-month-out ROI:")
    for m in sorted(bym):
        rest = [r for r in sel if r["date"][:7] != m]
        pl = sum(r["pl"] for r in rest)
        print(f"    drop {m}: n={len(rest):>4} ROI {pl/len(rest)*100:>+6.2f}%")

    print("\n--- 4. IS THE SELECTION EVEN THE ONE THE RULE DESCRIBES? "
          "(overlap with the live YRFI gate) ---")
    live_gate = [r for r in sel if r["model"] < 0.36]
    print(f"  rows with p_nrfi>=0.50 AND p_nrfi<0.36 (should be 0): {len(live_gate)}")
    print(f"  of the {len(sel)} mirror bets, pick_side breakdown: "
          f"{pd.Series([r['side'] for r in sel]).value_counts().to_dict()}")

    print("\n--- 5. OUT-OF-SAMPLE: does the model MIS-CALIBRATE the same way "
          "in 2024 / 2025? ---")
    print("    (no odds in the big backtests, so this bounds the HIT RATE, not profit.")
    print("     The mirror needs actual NRFI rate << model p_nrfi in the >=0.50 band.)")
    files = {
        "2024": "backtest_2024-04-01_to_2024-09-30_truepit.csv",
        "2025": "backtest_2025-04-01_to_2025-09-30_truepit.csv",
        "2026 bt (04-01..05-11)": "backtest_2026-04-01_to_2026-05-11_truepit.csv",
        "2026 bt (05-12..05-26)": "backtest_2026-05-12_to_2026-05-26_truepit.csv",
    }
    print(f"  {'season':<26}{'n>=0.50':>9}{'mean p_nrfi':>13}{'actual NRFI':>13}"
          f"{'YRFI hit':>10}{'calib gap':>11}")
    for k, f in files.items():
        d = pd.read_csv(BT / f)
        d = d.dropna(subset=["nrfi_prob", "fi_total_runs"])
        s = d[d["nrfi_prob"] >= 0.50]
        if not len(s):
            print(f"  {k:<26}{0:>9}")
            continue
        act = (s["fi_total_runs"] == 0).mean()
        print(f"  {k:<26}{len(s):>9}{s['nrfi_prob'].mean():>13.4f}{act:>13.4f}"
              f"{(1-act)*100:>9.2f}%{(s['nrfi_prob'].mean()-act)*100:>+10.2f}pp")
    # live 2026 for comparison, on ALL graded games not just priced ones
    dl = pd.read_csv(PICKS)
    dl = dl.drop_duplicates(subset=["game_pk", "game_number"], keep="last")
    dl = dl.dropna(subset=["nrfi_prob", "fi_total_runs"])
    s = dl[dl["nrfi_prob"] >= 0.50]
    act = (s["fi_total_runs"] == 0).mean()
    print(f"  {'2026 LIVE (all graded)':<26}{len(s):>9}{s['nrfi_prob'].mean():>13.4f}"
          f"{act:>13.4f}{(1-act)*100:>9.2f}%{(s['nrfi_prob'].mean()-act)*100:>+10.2f}pp")

    print("\n--- 6. SELECTION BIAS: priced vs un-priced rows in 2026 ---")
    dl["_priced"] = dl["market_yrfi_odds"].notna() & dl["market_nrfi_odds"].notna()
    s5 = dl[dl["nrfi_prob"] >= 0.50]
    for flag, lab in ((True, "priced"), (False, "un-priced")):
        g = s5[s5["_priced"] == flag]
        if len(g):
            print(f"  p_nrfi>=0.50, {lab:<10} n={len(g):>4}  actual NRFI "
                  f"{(g['fi_total_runs']==0).mean()*100:>5.2f}%  "
                  f"mean p {g['nrfi_prob'].mean():.4f}")

    print("\n--- 7. NULL-HYPOTHESIS PROBABILITY given the search exposure ---")
    n = len(sel)
    be = sum(r["be_yrfi"] for r in sel) / n
    w = sum(1 - r["nrfi"] for r in sel)
    # binomial one-sided p under H0: true win prob = break-even
    from math import comb
    p_ind = sum(comb(n, k) * be**k * (1-be)**(n-k) for k in range(w, n+1))
    print(f"  independent-bet one-sided binomial p (H0: p = {be:.4f}) : {p_ind:.4f}")
    # day-clustered version: bootstrap the ROI, get p from day-CI
    lo, hi = boot_days(sel)
    # permutation-ish: variance inflation
    pls = np.array([r["pl"] for r in sel])
    sd_iid = pls.std(ddof=1) / math.sqrt(n)
    sd_day = (hi - lo) / (2 * 1.96)
    deff = (sd_day / sd_iid) ** 2
    z_day = pls.mean() / sd_day
    from math import erfc
    p_day = 0.5 * erfc(z_day / math.sqrt(2))
    print(f"  day-clustered SE {sd_day:.4f} vs iid SE {sd_iid:.4f} "
          f"(design effect x{deff:.2f})")
    print(f"  day-clustered one-sided p                              : {p_day:.4f}")
    for k in (10, 20, 70):
        print(f"  family-wise p if {k:>2} cells were searched (Sidak)      : "
              f"{1-(1-p_day)**k:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

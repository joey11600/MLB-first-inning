#!/usr/bin/env python3
"""tools/nrfi_market_disagreement.py -- can NRFI be made PROFITABLE by only
betting when our model strongly disagrees with the book?

Not a prediction question -- a pricing question. For every graded game
that has captured DK NRFI+YRFI odds, compute:
    model_nrfi   = nrfi_prob (our calibrated number)
    market_nrfi  = de-vigged book NRFI prob = imp(N)/(imp(N)+imp(Y))
    disagree     = model_nrfi - market_nrfi   (+ = we're MORE bullish NRFI)
Then, among games our model leans NRFI (model_nrfi>=0.50), bucket by
disagreement and simulate betting NRFI at the captured price. If the
big-disagreement buckets clear the vig (ROI>0, bootstrap CI lower bound
> 0), the 'only bet NRFI when we disagree with the book' filter is real.

Uses ALL graded-with-odds games (~500), not just the ~70 we bet -> far
more power than the bet-only sample. Read-only.
"""
from __future__ import annotations
import csv, sys
from pathlib import Path
try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")
ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
SEED, B = 20260607, 10000


def imp(a):
    s = (a or "").strip()
    try: n = int(float(s))
    except (ValueError, TypeError): return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(a):
    s = (a or "").strip()
    try: n = int(float(s))
    except (ValueError, TypeError): return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


def boot_ci(pls):
    if len(pls) < 8: return (float("nan"), float("nan"))
    rng = np.random.default_rng(SEED)
    arr = np.asarray(pls)
    means = rng.choice(arr, size=(B, len(arr)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def load():
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                fa = int(float(r["fi_away_runs"])); fh = int(float(r["fi_home_runs"]))
                m_nrfi = float(r["nrfi_prob"])
            except (ValueError, TypeError, KeyError):
                continue
            iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
            pN = payout(r.get("market_nrfi_odds"))
            if not iN or not iY or pN is None:
                continue
            mkt_nrfi = iN / (iN + iY)             # de-vigged book NRFI prob
            actual_nrfi = 1 if (fa + fh) == 0 else 0
            rows.append({
                "disagree": m_nrfi - mkt_nrfi,
                "model": m_nrfi, "mkt": mkt_nrfi,
                "nrfi": actual_nrfi,
                "pl": (pN if actual_nrfi else -1.0),   # P&L of betting NRFI at captured price
                "breakeven": iN,                       # raw implied (incl vig) = hit rate needed
            })
    return rows


def show(rows, label, ci=False):
    if not rows:
        print(f"  {label:<26} (0)"); return
    n = len(rows); hit = sum(r["nrfi"] for r in rows); pl = sum(r["pl"] for r in rows)
    be = sum(r["breakeven"] for r in rows)/n
    s = (f"  {label:<26} n={n:>4}  NRFI {hit/n*100:>4.1f}% (need {be*100:.1f}%)  "
         f"ROI {pl/n*100:>+5.1f}%  P&L {pl:>+6.1f}u")
    if ci:
        lo, hi = boot_ci([r["pl"] for r in rows])
        verdict = "REAL (CI>0)" if lo > 0 else "unproven (CI spans 0)"
        s += f"  95%CI[{lo*100:+.0f}%,{hi*100:+.0f}%] {verdict}"
    print(s)


def main():
    rows = load()
    print(f"Graded games with captured NRFI+YRFI odds: {len(rows)}\n")

    print("=== BASELINE: bet every game NRFI (no filter) ===")
    show(rows, "all games")
    show([r for r in rows if r["model"] >= 0.50], "model leans NRFI (>=.50)")
    show([r for r in rows if r["model"] >= 0.62], "STRONG NRFI zone (>=.62)")

    print("\n=== THE TEST: among model-NRFI-leaning games, bucket by DISAGREEMENT ===")
    lean = [r for r in rows if r["model"] >= 0.50]
    show([r for r in lean if r["disagree"] < 0.00], "model<=market (agree/bear)")
    show([r for r in lean if 0.00 <= r["disagree"] < 0.03], "small +disagree 0-3pp")
    show([r for r in lean if 0.03 <= r["disagree"] < 0.06], "mid +disagree 3-6pp")
    show([r for r in lean if 0.06 <= r["disagree"] < 0.10], "big +disagree 6-10pp", ci=True)
    show([r for r in lean if r["disagree"] >= 0.10], "HUGE +disagree 10pp+", ci=True)
    print("\n  -> If the 6-10pp / 10pp+ buckets clear 'need%' with CI>0, the")
    print("     'bet NRFI only when we out-bull the book' filter is a real edge.")


if __name__ == "__main__":
    main()

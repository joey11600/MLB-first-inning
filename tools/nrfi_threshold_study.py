#!/usr/bin/env python3
"""
tools/nrfi_threshold_study.py -- should the STRONG NRFI threshold move
up from 0.56?

Calibration evidence (separate analysis): the model OVERPREDICTS NRFI in
the 0.50-0.62 calibrated band (says ~53-59%, actual ~36-43%, robust
across May AND June), while 0.62+ and the YRFI side (<0.44) are well
calibrated.  The STRONG-NRFI threshold (0.56) sits inside the broken
band.  This tests raising it.

A threshold T means "only fire STRONG NRFI when calibrated nrfi_prob >= T".
Higher T = skip the overconfident low band.

  1. Aggregate ROI sweep at each T (real captured odds only).
  2. True walk-forward: pick best T on PRIOR weeks, apply blind forward.
  3. Calibration cross-check on the FULL graded slate (n in the hundreds):
     realized NRFI rate at each T -- confirms the high band is real, not
     a small-bet artifact.

YRFI INVARIANCE: NRFI thresholding only touches picks with nrfi_prob>=0.56;
YRFI lives at nrfi_prob<0.44.  Disjoint -- cannot change a YRFI bet.
Read-only.
"""
from __future__ import annotations
import csv, datetime
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
CUR = 0.56
GRID = [0.56, 0.58, 0.60, 0.62, 0.64]


def payout(a):
    s = (a or "").strip()
    try: n = int(s)
    except ValueError: return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


def load_bets():
    """STRONG NRFI bets actually placed, with real captured odds."""
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("pick_side") != "NRFI" or r.get("pick_strength") != "STRONG" or r.get("bet_placed") != "Y":
                continue
            if r.get("graded_result") not in ("WIN", "LOSS"):
                continue
            pay = payout((r.get("market_nrfi_odds") or "").strip())
            if pay is None:
                continue
            try: cp = float(r.get("nrfi_prob", "") or "")
            except ValueError: continue
            won = r["graded_result"] == "WIN"
            rows.append({"date": r["date"], "cp": cp, "won": won, "pl": (pay if won else -1.0)})
    return rows


def load_all_graded():
    """Every graded game's (calibrated nrfi_prob, actual NRFI) -- for the
    calibration cross-check on a big sample, not just placed bets."""
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fa, fh = r.get("fi_away_runs", ""), r.get("fi_home_runs", "")
            if fa == "" or fh == "":
                continue
            try: nrfi = 1 if (int(float(fa)) + int(float(fh))) == 0 else 0
            except ValueError: continue
            try: cp = float(r.get("nrfi_prob", "") or "")
            except ValueError: continue
            rows.append((cp, nrfi))
    return rows


def roi(rows, t):
    sub = [r for r in rows if r["cp"] >= t]
    if not sub:
        return 0, 0, 0.0
    w = sum(1 for r in sub if r["won"])
    pl = sum(r["pl"] for r in sub)
    return len(sub), w, pl


def main():
    bets = load_bets()
    print(f"STRONG NRFI bets with real odds: {len(bets)}\n")

    print("=== Aggregate sweep: STRONG NRFI ROI at each threshold ===")
    print(f"  {'thresh':>7} {'bets':>5} {'W-L':>8} {'hit%':>6} {'ROI':>7} {'P&L':>8}   note")
    for t in GRID:
        n, w, pl = roi(bets, t)
        if n == 0:
            print(f"  {t:>7.2f} {0:>5}  (no bets at/above this threshold)")
            continue
        note = "  <-- CURRENT" if abs(t - CUR) < 1e-9 else ""
        print(f"  {t:>7.2f} {n:>5} {str(w)+'-'+str(n-w):>8} {w/n*100:>5.0f}% {pl/n*100:>+6.0f}% {pl:>+8.2f}{note}")

    print("\n=== TRUE walk-forward (threshold chosen on PRIOR weeks, applied blind) ===")
    wk = sorted({(datetime.date.fromisoformat(b["date"]) - datetime.timedelta(days=datetime.date.fromisoformat(b["date"]).weekday())).isoformat() for b in bets})
    bydate = lambda b: (datetime.date.fromisoformat(b["date"]) - datetime.timedelta(days=datetime.date.fromisoformat(b["date"]).weekday())).isoformat()
    print(f"   {'week':>12} {'chosen T':>9} {'that-wk P&L':>12} {'@0.56 P&L':>11}")
    wf = cur = 0.0
    for i, w in enumerate(wk):
        test = [b for b in bets if bydate(b) == w]
        if not test:
            continue
        if i == 0:
            chosen = CUR
        else:
            prior = [b for b in bets if bydate(b) < w]
            best, bestpl = CUR, None
            for t in GRID:
                _, _, pl = roi(prior, t)
                if bestpl is None or pl > bestpl:
                    bestpl, best = pl, t
            chosen = best
        _, _, wfpl = roi(test, chosen)
        _, _, curpl = roi(test, CUR)
        wf += wfpl; cur += curpl
        print(f"   {w:>12} {chosen:>9.2f} {wfpl:>+12.2f} {curpl:>+11.2f}")
    print("   " + "-" * 48)
    print(f"   {'TOTAL':>12} {'':>9} {wf:>+12.2f} {cur:>+11.2f}   walk-fwd vs 0.56: {wf-cur:+.2f}u")

    print("\n=== Calibration cross-check on ALL graded games (big sample) ===")
    allg = load_all_graded()
    print(f"   {'band':>12} {'n':>4} {'actual NRFI%':>12}  (we only WANT to bet bands that truly clear ~55%+)")
    for lo, hi in [(0.56, 0.60), (0.60, 0.62), (0.62, 0.66), (0.66, 1.01)]:
        b = [n for cp, n in allg if lo <= cp < hi]
        if not b:
            continue
        print(f"   {f'{lo:.2f}-{hi:.2f}':>12} {len(b):>4} {sum(b)/len(b)*100:>11.1f}%")

    print("\n  YRFI INVARIANCE: this only filters nrfi_prob>=0.56 picks; YRFI is the")
    print("  nrfi_prob<0.44 side -> mathematically untouched.")


if __name__ == "__main__":
    main()

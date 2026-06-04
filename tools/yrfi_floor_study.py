#!/usr/bin/env python3
"""
tools/yrfi_floor_study.py -- is the STRONG-YRFI lambda floor (0.838)
set at the right level?

Triggered by CWS@MIN 2026-06-03: a STRONG YRFI candidate (nrfi_prob
0.425) with lambda 0.826 was demoted to PASS "LOW LAMBDA" because
0.826 < the 0.838 floor; the first inning went 4-0 (YRFI would have
won).  The floor was 0.78 until 2026-05-19, when a league-constants
refresh scaled it up to 0.838.  This study asks: does the data support
0.838, or is a lower floor (0.80 / 0.82) a real, durable edge?

METHOD
------
Population = every GRADED game where the model wanted STRONG YRFI
(calibrated nrfi_prob < 0.44) and the floor was the deciding gate
(pick was either bet as STRONG YRFI, or demoted to LOW LAMBDA).  For
each we know lambda_lr_total and the true first-inning outcome
(YRFI hit = a YRFI bet wins; NRFI = it loses).

A floor of T means "bet YRFI only when lambda >= T".  Lower T bets
more (and deeper into the soft zone); higher T bets fewer.

Outputs:
  1. Threshold sweep -- realized P&L at each candidate floor (the basin).
  2. True walk-forward -- pick the best floor on PRIOR weeks only, apply
     blind to the next week (guards against fitting to one slate).
  3. Current-model-only subset (since the 2026-05-26 retrain) -- the
     finding must hold on the model that's actually live, not just the
     larger mixed-model history.

Read-only.  Changes nothing.
"""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"

PASS_LO_P = 0.44          # nrfi_prob < this = STRONG YRFI zone
CURRENT_FLOOR = 0.838     # production value being tested
RETRAIN_DATE = "2026-05-27"   # first full day on the current (sliding-window) model
BREAKEVEN = 0.524         # ~ -110 break-even hit rate

THRESHOLDS = [0.74, 0.76, 0.78, 0.80, 0.82, 0.838, 0.86, 0.88]


def amer_to_payout(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n / 100.0 if n > 0 else 100.0 / abs(n)


def load_population(since: str = "2026-04-01"):
    """All graded STRONG-YRFI candidates where the floor is the deciding gate."""
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["date"] < since:
                continue
            fa, fh = r.get("fi_away_runs", ""), r.get("fi_home_runs", "")
            if fa == "" or fh == "":
                continue
            try:
                fi_total = int(float(fa)) + int(float(fh))
            except ValueError:
                continue
            try:
                nrfi = float(r.get("nrfi_prob", ""))
            except ValueError:
                continue
            if nrfi >= PASS_LO_P:
                continue                      # not a STRONG YRFI candidate
            try:
                lam = float(r.get("lambda_lr_total", ""))
            except ValueError:
                continue
            strength = r.get("pick_strength", "")
            side = r.get("pick_side", "")
            is_bet_yrfi = (side == "YRFI" and strength == "STRONG")
            is_demoted  = (strength == "LOW LAMBDA")
            if not (is_bet_yrfi or is_demoted):
                continue                      # blocked by some other gate; floor not deciding
            payout = amer_to_payout(r.get("market_yrfi_odds", "")) or (100.0 / 110.0)
            rows.append({
                "date":   r["date"],
                "game":   f"{r['away_team']}@{r['home_team']}",
                "lam":    lam,
                "hit":    fi_total > 0,       # YRFI bet wins iff a run scored
                "payout": payout,
                "was_bet": is_bet_yrfi,
            })
    return rows


def pl_at_floor(rows, floor):
    bet = [r for r in rows if r["lam"] >= floor]
    w = sum(1 for r in bet if r["hit"])
    pl = sum((r["payout"] if r["hit"] else -1.0) for r in bet)
    return len(bet), w, pl


def sweep(rows, label):
    print(f"\n=== Threshold sweep: {label}  (n={len(rows)} YRFI candidates) ===")
    print(f"  {'floor':>7} {'bets':>5} {'W-L':>8} {'hit%':>6} {'P&L':>9}   note")
    for t in THRESHOLDS:
        n, w, pl = pl_at_floor(rows, t)
        if n == 0:
            continue
        note = "  <-- CURRENT" if abs(t - CURRENT_FLOOR) < 1e-9 else ""
        hit = w / n * 100
        print(f"  {t:>7.3f} {n:>5} {str(w)+'-'+str(n-w):>8} {hit:>5.0f}% {pl:>+9.2f}{note}")


def weeks(start, end):
    out = []
    d = date.fromisoformat(start)
    while d <= date.fromisoformat(end):
        we = min(d + timedelta(days=6), date.fromisoformat(end))
        out.append((d.isoformat(), we.isoformat()))
        d += timedelta(days=7)
    return out


def walk_forward(rows, start, end):
    print(f"\n=== TRUE walk-forward (floor chosen on PRIOR weeks only, applied blind) ===")
    print(f"   {'week':<16} {'chosen floor':>12} {'that-week P&L':>14} {'@0.838 P&L':>12}")
    wk = weeks(start, end)
    wf_total = cur_total = 0.0
    for i, (ws, we) in enumerate(wk):
        test = [r for r in rows if ws <= r["date"] <= we]
        if not test:
            continue
        if i == 0:
            chosen = CURRENT_FLOOR
        else:
            prior = [r for r in rows if r["date"] < ws]
            best, best_pl = CURRENT_FLOOR, None
            for t in THRESHOLDS:
                _, _, pl = pl_at_floor(prior, t)
                if best_pl is None or pl > best_pl:
                    best_pl, best = pl, t
            chosen = best
        _, _, wf_pl = pl_at_floor(test, chosen)
        _, _, cur_pl = pl_at_floor(test, CURRENT_FLOOR)
        wf_total += wf_pl
        cur_total += cur_pl
        print(f"   {ws[5:]+'..'+we[5:]:<16} {chosen:>12.3f} {wf_pl:>+14.2f} {cur_pl:>+12.2f}")
    print("   " + "-" * 56)
    print(f"   {'TOTAL':<16} {'':>12} {wf_total:>+14.2f} {cur_total:>+12.2f}")
    print(f"   Walk-forward vs status-quo (0.838): {wf_total - cur_total:+.2f}u")


def band_breakdown(rows):
    print(f"\n=== Where the action is: hit-rate by lambda band (the floor cuts at 0.838) ===")
    bands = [(0.74, 0.80), (0.80, 0.838), (0.838, 0.90)]
    print(f"  {'band':>16} {'n':>4} {'W-L':>8} {'hit%':>6} {'P&L':>9}   verdict")
    for lo, hi in bands:
        sub = [r for r in rows if lo <= r["lam"] < hi]
        if not sub:
            continue
        w = sum(1 for r in sub if r["hit"])
        pl = sum((r["payout"] if r["hit"] else -1.0) for r in sub)
        hit = w / len(sub) * 100
        verdict = "profitable -- floor may be cutting winners" if hit >= BREAKEVEN*100 else "below break-even -- correctly cut"
        tag = " (CURRENTLY BET)" if lo >= CURRENT_FLOOR else " (currently SKIPPED)"
        print(f"  {f'{lo:.3f}-{hi:.3f}':>16} {len(sub):>4} {str(w)+'-'+str(len(sub)-w):>8} {hit:>5.0f}% {pl:>+9.2f}  {verdict}{tag}")


def main():
    all_rows = load_population("2026-04-01")
    recent   = [r for r in all_rows if r["date"] >= RETRAIN_DATE]
    print(f"Loaded {len(all_rows)} STRONG-YRFI candidates (floor-decided), "
          f"{len(recent)} on the current model (since {RETRAIN_DATE}).")

    band_breakdown(all_rows)
    sweep(all_rows, "ALL 2026 (mixed model versions)")
    sweep(recent,   f"CURRENT MODEL ONLY (since {RETRAIN_DATE})")
    walk_forward(all_rows, "2026-04-27", date.today().isoformat())

    print("\n=== Caveats ===")
    print("  - The live floor is weather-adjusted (+/-~0.02/condition); this study")
    print("    tests the raw lambda vs fixed thresholds, a clean approximation.")
    print("  - Lowering the floor CHANGES the winning YRFI engine directly, so the")
    print("    bar for acting is high: want a consistent walk-forward edge AND the")
    print("    finding holding on the current-model subset, not just the mixed set.")


if __name__ == "__main__":
    main()

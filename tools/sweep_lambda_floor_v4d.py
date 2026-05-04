#!/usr/bin/env python3
"""
tools/sweep_lambda_floor_v4d.py -- T4-V4 candidate D: lambda-floor sweep.

Sweeps candidate YRFI lambda floors {0.70, 0.78 (current), 0.85, 1.00} on
the v4 design window (2026-04-01 -> 2026-04-15) using production picks.
Locks the highest-P/L floor as the value variant v4-floor will use on
the holdout.

THEORETICAL MOTIVATION (pre-registered)
----------------------------------------
Production demotes STRONG YRFI bets to PASS-LOW-LAMBDA when combined
lambda < 0.78.  Original 0.78 was set by an earlier backtest.  With more
data we can re-estimate the optimal floor.

  * 0.70 -- looser floor; allows MORE STRONG YRFI bets through.
            Theoretically risky if low-lambda YRFI bets are losers
            (which the original 0.78 floor was set to suppress).
  * 0.78 -- production floor (do-nothing baseline).  If this wins
            the sweep, variant v4-floor == production exactly.
  * 0.85 -- tighter floor; fewer STRONG YRFI bets.  Tries to
            suppress more borderline-low-lambda losers.
  * 1.00 -- variant D from db/variants.py (already evaluated and
            REJECTED -- prior eval was -6.4u over 32d, so we expect
            this to lose on the design window too).  Including it
            as a sanity check: if 1.00 wins on Apr 1-15 it's a hint
            that the prior rejection might have been window-specific
            noise, but we still won't trust it without the holdout.

This sweep PEEKS at design data freely (Apr 1-15 is design, peek
allowed).  The locked value is what variant v4-floor uses on the
HOLDOUT (Apr 16 -> today).  We do NOT iterate on the floor based on
holdout numbers -- that would be data dredging.

OUTPUT
------
Writes data/v4d_locked_floor.json:
  {
    "locked_floor":  <best of the four>,
    "design_window": "2026-04-01 -> 2026-04-15",
    "sweep_results": [{floor, pl_units, n_demoted, ...}, ...],
    "fitted_at":     ISO timestamp
  }

USAGE
-----
  python tools/sweep_lambda_floor_v4d.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pre-registered floor candidates.  Locked here BEFORE running.  Do not
# add or remove values based on what we see in design data.
SWEEP_FLOORS = [0.70, 0.78, 0.85, 1.00]

# Pre-registered design window.  Apr 1-15 of 2026.  These dates are
# locked here before sweep run.
DESIGN_START = "2026-04-01"
DESIGN_END   = "2026-04-15"


def parse_float(s: str | None, default: float = 0.0) -> float:
    if s is None or s == "" or s == "null":
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def load_design_rows(season: int = 2026) -> list[dict]:
    """Load picks_<season>.csv rows in the design window."""
    csv_path = REPO_ROOT / "data" / f"picks_{season}.csv"
    if not csv_path.exists():
        sys.exit(f"Missing {csv_path}")

    rows: list[dict] = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d = (r.get("date") or "").strip()[:10]
            if not d:
                continue
            if d < DESIGN_START or d > DESIGN_END:
                continue
            rows.append(r)
    return rows


def evaluate_floor(rows: Iterable[dict], floor: float) -> dict:
    """
    Apply the given YRFI lambda floor as a post-filter on production
    verdicts.  Compute aggregate P/L on graded bets.

    Rule: if production was STRONG YRFI AND combined_lambda < floor,
    demote to PASS (no bet).  Otherwise keep production verdict.
    All other production verdicts unchanged.

    Returns:
      {
        floor:       the input floor value,
        n_strong:    total production STRONG bets in window,
        n_yrfi:      production STRONG YRFI bets,
        n_demoted:   STRONG YRFI bets demoted by this floor,
        n_kept:      STRONG bets that still bet under this floor,
        kept_w-l:    W-L on kept STRONG bets,
        kept_pl:     P/L on kept STRONG bets (sum of profit_loss_units),
        demoted_w-l: W-L on demoted bets (counterfactual: what P/L we
                     would have gotten if we'd kept them; lets us see
                     whether the floor is throwing away money),
        demoted_pl:  P/L on demoted bets (counterfactual),
        net_change:  kept_pl - production_pl  (positive = floor helped)
      }
    """
    n_strong = n_yrfi = n_demoted = n_kept = 0
    kept_w = kept_l = 0
    kept_pl = 0.0
    demoted_w = demoted_l = 0
    demoted_pl = 0.0
    prod_pl = 0.0
    prod_w = prod_l = 0

    for r in rows:
        ps  = (r.get("pick_side")     or "").upper()
        pst = (r.get("pick_strength") or "").upper()
        if pst != "STRONG" or ps not in ("NRFI", "YRFI"):
            continue

        n_strong += 1
        graded = (r.get("graded_result") or "").upper()
        if graded not in ("WIN", "LOSS"):
            continue  # ungraded / postponed -- no P/L either way

        pl_str = (r.get("profit_loss_units") or "").strip()
        pl = parse_float(pl_str, default=(0.909 if graded == "WIN" else -1.0))

        # Production baseline tracking
        prod_pl += pl
        if graded == "WIN":
            prod_w += 1
        else:
            prod_l += 1

        # Apply this floor
        if ps == "YRFI":
            n_yrfi += 1
            lam = parse_float(r.get("lambda_lr_total") or r.get("combined_lambda"), 1.0)
            if lam < floor:
                n_demoted += 1
                demoted_pl += pl
                if graded == "WIN":
                    demoted_w += 1
                else:
                    demoted_l += 1
                continue  # demoted -- no bet under this floor

        # Bet was kept
        n_kept += 1
        kept_pl += pl
        if graded == "WIN":
            kept_w += 1
        else:
            kept_l += 1

    return {
        "floor":            floor,
        "n_strong":         n_strong,
        "n_yrfi":           n_yrfi,
        "n_demoted":        n_demoted,
        "n_kept":           n_kept,
        "kept_record":      f"{kept_w}-{kept_l}",
        "kept_pl":          round(kept_pl, 3),
        "demoted_record":   f"{demoted_w}-{demoted_l}",
        "demoted_pl":       round(demoted_pl, 3),
        "production_pl":    round(prod_pl, 3),
        "production_record": f"{prod_w}-{prod_l}",
        "net_change_vs_prod": round(kept_pl - prod_pl, 3),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", default="data/v4d_locked_floor.json",
                    help="Output JSON for locked floor (default: data/v4d_locked_floor.json)")
    args = ap.parse_args()

    print("=" * 92)
    print("  Variant v4-floor: lambda floor sweep on design window")
    print(f"  Design window: {DESIGN_START} -> {DESIGN_END} (Apr 1-15 2026)")
    print(f"  Candidate floors: {SWEEP_FLOORS}")
    print("=" * 92)

    rows = load_design_rows()
    print(f"\n  Loaded {len(rows)} pick rows from design window.")

    results = []
    for f in SWEEP_FLOORS:
        r = evaluate_floor(rows, f)
        results.append(r)

    print()
    print(f"  {'floor':>6}  {'n_yrfi':>7}  {'n_demoted':>10}  {'kept':>7}  "
          f"{'kept_pl':>8}  {'demo_pl':>8}  {'net_chg':>8}")
    for r in results:
        print(f"  {r['floor']:>6.2f}  {r['n_yrfi']:>7d}  {r['n_demoted']:>10d}  "
              f"{r['kept_record']:>7}  {r['kept_pl']:>+8.3f}  "
              f"{r['demoted_pl']:>+8.3f}  {r['net_change_vs_prod']:>+8.3f}")

    # Production baseline (no floor change) for reference
    print()
    print(f"  Production baseline (no floor change):")
    print(f"    P/L on STRONG bets = {results[0]['production_pl']:+.3f}u "
          f"({results[0]['production_record']})")

    # Lock the highest-kept-PL floor
    best = max(results, key=lambda r: r["kept_pl"])
    print()
    print(f"  WINNER: floor = {best['floor']:.2f} "
          f"(kept PL = {best['kept_pl']:+.3f}u, "
          f"net change vs production = {best['net_change_vs_prod']:+.3f}u)")

    payload = {
        "locked_floor":  best["floor"],
        "design_window": f"{DESIGN_START} -> {DESIGN_END}",
        "sweep_results": results,
        "fitted_at":     datetime.now(timezone.utc).replace(tzinfo=None)
                                  .isoformat(timespec="seconds") + "Z",
    }
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print()
    print(f"  Saved -> {out_path}")
    print()
    print("  variant v4-floor will use this locked floor on the HOLDOUT.")
    print("  No further sweeping based on holdout numbers.")


if __name__ == "__main__":
    main()

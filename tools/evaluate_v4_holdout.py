#!/usr/bin/env python3
"""
tools/evaluate_v4_holdout.py -- T4-V4 Phase 2 evaluator.

Reads each v4 candidate variant's verdicts from the pick_variants
Supabase table over the HOLDOUT WINDOW (2026-04-16 -> today) and
compares aggregate performance against the v2 production baseline
read from picks_<year>.csv.  Outputs a qualify/don't-qualify verdict
per variant against the pre-registered success bar locked at the
commit time of Phase 1.

PRE-REGISTERED SUCCESS BAR (locked, do not adjust based on output)
-------------------------------------------------------------------

A variant qualifies for v4 shadow promotion only if ALL FOUR hold on
the holdout:

  (a) Net P/L vs v2:    +6u or more  (single-test bar would be +3u;
                                       tightened to +6u to dilute the
                                       4-variant multiple-comparisons
                                       inflation)
  (b) Brier vs v2:      0.008 lower or more  (same inflation logic)
  (c) Max drawdown:     <= v2's max drawdown on the same window
  (d) Sample size:      40 or more graded bets

If 0 variants qualify -> answer is "v4 not ready, keep v2."  Valid
result.  If 1 variant qualifies -> ship as shadow (variant_name stays
in pick_variants), let it run forward 60+ days before considering
production promotion.  If 2+ qualify -> highly suspicious; means the
variants are correlated.  Investigate before promoting any.

THIS SCRIPT IS PHASE 2 ONLY.  DO NOT RUN UNTIL THE USER CONFIRMS
THE PHASE 1 SANITY CHECK ON THE DESIGN WINDOW LOOKS GOOD.

USAGE
-----
  python tools/evaluate_v4_holdout.py
  python tools/evaluate_v4_holdout.py --until 2026-05-15
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from db.supabase_writer import _get_client


# Pre-registered holdout window.
HOLDOUT_START = "2026-04-16"

# Pre-registered success bar.  All four must hold for qualify=True.
BAR_NET_PL_DELTA       = 6.0       # variant pl - v2 pl >= this
BAR_BRIER_DELTA        = 0.008     # v2_brier - variant_brier >= this
BAR_MAX_DD_RATIO       = 1.0       # variant_dd / v2_dd <= this (i.e. variant DD <= v2 DD)
BAR_MIN_GRADED_BETS    = 40

V4_VARIANTS = ["v4-platt", "v4-recency", "v4-asym", "v4-floor"]


def parse_float(s, default=0.0):
    if s is None or s == "" or s == "null":
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def load_v2_baseline_from_csv(holdout_start: str, holdout_end: str,
                              season: int = 2026) -> list[dict]:
    """Production v2 picks within the holdout window."""
    csv_path = REPO_ROOT / "data" / f"picks_{season}.csv"
    if not csv_path.exists():
        sys.exit(f"Missing {csv_path}")

    rows = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d = (r.get("date") or "").strip()[:10]
            if not d:
                continue
            if d < holdout_start or d > holdout_end:
                continue
            rows.append(r)
    return rows


def load_variant_from_supabase(client, variant_name: str,
                               holdout_start: str, holdout_end: str) -> list[dict]:
    """Variant verdicts from pick_variants for one variant_name."""
    res = (
        client.table("pick_variants")
        .select("date, game_pk, pick_side, pick_strength, nrfi_prob, "
                "graded_result, profit_loss_units, fi_total_runs")
        .eq("variant_name", variant_name)
        .gte("date", holdout_start)
        .lte("date", holdout_end)
        .limit(10000)
        .execute()
    )
    return res.data or []


def aggregate_metrics(rows: Iterable[dict],
                      pick_side_key: str = "pick_side",
                      pick_strength_key: str = "pick_strength",
                      graded_key: str = "graded_result",
                      pl_key: str = "profit_loss_units",
                      nrfi_prob_key: str = "nrfi_prob",
                      fi_runs_key: str = "fi_total_runs") -> dict:
    """
    Compute aggregate metrics on STRONG bets only.

    Returns:
      net_pl, n_bets, n_wins, n_losses, hit_rate, brier (across all
      graded bets where we have a probability), max_drawdown,
      cumulative_pl_by_day (for chart sanity).
    """
    n_bets = n_wins = n_losses = 0
    net_pl = 0.0
    daily_pl: dict[str, float] = {}
    brier_sum = 0.0
    brier_n = 0

    for r in rows:
        side = (r.get(pick_side_key) or "").upper()
        strength = (r.get(pick_strength_key) or "").upper()
        if strength != "STRONG" or side not in ("NRFI", "YRFI"):
            continue
        graded = (r.get(graded_key) or "").upper()
        if graded not in ("WIN", "LOSS"):
            continue

        n_bets += 1
        pl = parse_float(r.get(pl_key), default=(0.909 if graded == "WIN" else -1.0))
        net_pl += pl

        if graded == "WIN":
            n_wins += 1
        else:
            n_losses += 1

        d = (r.get("date") or "")[:10]
        daily_pl[d] = daily_pl.get(d, 0.0) + pl

        # Brier on calibrated nrfi_prob: actual = 1 if NRFI happened (graded
        # depends on the picked side).  Map graded outcome back to NRFI/YRFI:
        #   side=NRFI, WIN  -> NRFI happened  (actual=1)
        #   side=NRFI, LOSS -> YRFI happened  (actual=0)
        #   side=YRFI, WIN  -> YRFI happened  (actual=0)
        #   side=YRFI, LOSS -> NRFI happened  (actual=1)
        actual_nrfi = (
            1 if (side == "NRFI" and graded == "WIN")
                or (side == "YRFI" and graded == "LOSS")
            else 0
        )
        p = parse_float(r.get(nrfi_prob_key))
        if 0.0 < p < 1.0:
            brier_sum += (p - actual_nrfi) ** 2
            brier_n += 1

    # Cumulative + max drawdown
    sorted_days = sorted(daily_pl.keys())
    cumulative = []
    cum = 0.0
    peak = -float("inf")
    max_dd = 0.0
    for d in sorted_days:
        cum += daily_pl[d]
        cumulative.append((d, cum))
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return {
        "n_bets":     n_bets,
        "n_wins":     n_wins,
        "n_losses":   n_losses,
        "hit_rate":   (n_wins / n_bets) if n_bets else float("nan"),
        "net_pl":     net_pl,
        "max_dd":     max_dd,
        "brier":      (brier_sum / brier_n) if brier_n else float("nan"),
        "brier_n":    brier_n,
        "n_days":     len(daily_pl),
        "cumulative": cumulative,
    }


def evaluate_variant(name: str, m_var: dict, m_v2: dict) -> dict:
    """Apply pre-registered bar to a variant vs v2 baseline."""
    pl_delta    = m_var["net_pl"]   - m_v2["net_pl"]
    brier_delta = m_v2["brier"]     - m_var["brier"]    # v2 brier - variant brier; positive = variant better
    dd_ratio    = (m_var["max_dd"] / m_v2["max_dd"]) if m_v2["max_dd"] > 0 else 0.0

    qualifies = (
        pl_delta    >= BAR_NET_PL_DELTA
        and brier_delta >= BAR_BRIER_DELTA
        and dd_ratio    <= BAR_MAX_DD_RATIO
        and m_var["n_bets"] >= BAR_MIN_GRADED_BETS
    )

    crit = {
        "(a) net_pl_delta":      (pl_delta,
                                   pl_delta >= BAR_NET_PL_DELTA,
                                   f">= +{BAR_NET_PL_DELTA}u"),
        "(b) brier_delta":        (brier_delta,
                                   brier_delta >= BAR_BRIER_DELTA,
                                   f">= +{BAR_BRIER_DELTA:.3f}"),
        "(c) dd_ratio":           (dd_ratio,
                                   dd_ratio <= BAR_MAX_DD_RATIO,
                                   f"<= {BAR_MAX_DD_RATIO}"),
        "(d) n_graded_bets":     (m_var["n_bets"],
                                   m_var["n_bets"] >= BAR_MIN_GRADED_BETS,
                                   f">= {BAR_MIN_GRADED_BETS}"),
    }
    return {"name": name, "qualifies": qualifies, "criteria": crit, "metrics": m_var}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--until", default=None,
                    help="Holdout end date YYYY-MM-DD (default: today).")
    args = ap.parse_args()

    holdout_end = args.until or datetime.now(timezone.utc).date().isoformat()

    print("=" * 92)
    print("  T4-V4 Phase 2 holdout evaluation")
    print(f"  Holdout window: {HOLDOUT_START} -> {holdout_end}")
    print("=" * 92)
    print(f"\n  Pre-registered bar (ALL four must hold):")
    print(f"    (a) net_pl_delta_vs_v2  >= +{BAR_NET_PL_DELTA}u")
    print(f"    (b) brier_delta_vs_v2   >= +{BAR_BRIER_DELTA}")
    print(f"    (c) max_dd_ratio_vs_v2  <= {BAR_MAX_DD_RATIO}")
    print(f"    (d) n_graded_bets       >= {BAR_MIN_GRADED_BETS}")
    print()

    client = _get_client()
    if client is None:
        sys.exit("Supabase not configured.")

    # v2 baseline from picks_<year>.csv
    v2_rows = load_v2_baseline_from_csv(HOLDOUT_START, holdout_end)
    m_v2 = aggregate_metrics(v2_rows)
    print(f"  V2 baseline: {m_v2['n_bets']} bets ({m_v2['n_wins']}-{m_v2['n_losses']}), "
          f"P/L = {m_v2['net_pl']:+.3f}u, Brier = {m_v2['brier']:.4f}, "
          f"max DD = {m_v2['max_dd']:.3f}u, days = {m_v2['n_days']}")
    print()

    # Per-variant evaluation
    verdicts = []
    for vname in V4_VARIANTS:
        rows = load_variant_from_supabase(client, vname, HOLDOUT_START, holdout_end)
        m_var = aggregate_metrics(rows)
        verdict = evaluate_variant(vname, m_var, m_v2)
        verdicts.append(verdict)

        print(f"  {vname}")
        print(f"    Bets: {m_var['n_bets']} ({m_var['n_wins']}-{m_var['n_losses']}), "
              f"P/L = {m_var['net_pl']:+.3f}u, Brier = {m_var['brier']:.4f}, "
              f"max DD = {m_var['max_dd']:.3f}u")
        for crit_name, (val, ok, target) in verdict["criteria"].items():
            mark = "PASS" if ok else "FAIL"
            print(f"      {mark}  {crit_name:<22} = {val:>+8.3f}  (target {target})")
        decision = "QUALIFIES" if verdict["qualifies"] else "DOES NOT QUALIFY"
        print(f"    -> {decision}")
        print()

    # Summary
    qualifying = [v for v in verdicts if v["qualifies"]]
    print("=" * 92)
    if not qualifying:
        print("  RESULT: 0 variants qualify on holdout.")
        print("  Decision: v4 is not ready.  Keep v2 in production.")
    elif len(qualifying) == 1:
        v = qualifying[0]
        print(f"  RESULT: 1 variant qualifies -- {v['name']}")
        print(f"  Decision: ship {v['name']} as shadow.  Run forward 60+ days")
        print(f"           before considering production promotion.")
    else:
        names = ", ".join(v["name"] for v in qualifying)
        print(f"  RESULT: {len(qualifying)} variants qualify -- {names}")
        print(f"  Decision: SUSPICIOUS.  Multiple variants passing simultaneously")
        print(f"           usually means they're correlated (riding the same data")
        print(f"           quirk).  Investigate before promoting any.")
    print("=" * 92)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/abtest_report.py — T2.51 A/B harness side-by-side report.

Compares production picks vs variants A / C / AC on the same slate.
For each variant, computes:

  - Bets placed (would_be units summed)
  - W/L record on graded games
  - Hit rate on STRONG bets only, LEAN bets only, all bets
  - Net unit P/L
  - ROI per unit risked
  - Where variant disagreed with production (count, net P/L delta)

The point: see whether each variant would have produced more or less
P/L than production over historical data, and where the disagreements
cluster.

Usage:
  python tools/abtest_report.py                       # all 2026 graded
  python tools/abtest_report.py --since 2026-04-01
  python tools/abtest_report.py --season 2025

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY (loaded from .env).
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from db.supabase_writer import _get_client


def fetch_production(client: Any, season: int, since: str | None) -> list[dict]:
    """Pull production picks_<season> rows.  Only graded ones for the
    P/L comparison; ungraded count as 'pending' for both production and
    variants alike."""
    table = f"picks_{season}"
    cols = ("date,game_pk,away_team,home_team,"
            "pick_side,pick_strength,pick_label,bet_placed,units_risked,"
            "graded_result,profit_loss_units,actual_result,fi_total_runs")
    q = client.table(table).select(cols).not_.is_("graded_result", "null")
    if since:
        q = q.gte("date", since)
    return q.execute().data or []


def fetch_variants(client: Any, since: str | None) -> dict[tuple[str, str, str], dict]:
    """Pull all pick_variants rows.  Indexed by (date, game_pk, variant_name)."""
    q = client.table("pick_variants").select("*")
    if since:
        q = q.gte("date", since)
    rows = q.execute().data or []
    return {(r["date"], r["game_pk"], r["variant_name"]): r for r in rows}


def aggregate(picks: list[dict], variant_lookup: dict, variant_name: str | None) -> dict:
    """Compute aggregate metrics.  When variant_name is None, aggregates
    production picks; otherwise aggregates the variant's shadow picks for
    the same set of games."""
    units_risked = 0.0
    pl_total     = 0.0
    n_bets = n_wins = n_losses = n_pass = 0
    n_strong = n_strong_wins = 0
    n_lean   = n_lean_wins   = 0

    for prod in picks:
        if variant_name is None:
            # Production view -- apply production's auto-bet POLICY uniformly:
            # every STRONG pick = 1u bet, every LEAN with bet_placed=Y = 0.5u.
            # Historical rows pre-T2.24 have bet_placed=NULL on STRONG picks
            # even though profit_loss_units is populated; counting only
            # bet_placed=Y systematically undercounts the production
            # arm.  The policy comparison is what matters for A/B.
            side = (prod.get("pick_side") or "").upper()
            strength = (prod.get("pick_strength") or "").upper()
            graded = (prod.get("graded_result") or "").upper()
            bp = (prod.get("bet_placed") or "").upper()
            if strength == "STRONG":
                bet_placed = True
                units = 1.0
            elif strength == "LEAN" and bp == "Y":
                bet_placed = True
                units = 0.5
            else:
                bet_placed = False
                units = 0.0
            # profit_loss_units is the actual recorded P/L for the bet;
            # keep it as-is (it already accounts for stake + payout odds).
            pl_raw = prod.get("profit_loss_units")
            if graded == "WIN":
                pl = float(pl_raw) if pl_raw not in (None, "") else units
            elif graded == "LOSS":
                pl = float(pl_raw) if pl_raw not in (None, "") else -units
            else:
                pl = 0.0
        else:
            v = variant_lookup.get((prod["date"], str(prod["game_pk"]), variant_name))
            if v is None:
                continue
            side = (v.get("pick_side") or "").upper()
            strength = (v.get("pick_strength") or "").upper()
            graded = (v.get("graded_result") or "").upper()
            bet_placed = bool(v.get("would_bet"))
            units = float(v.get("would_be_units") or 0)
            pl    = float(v.get("profit_loss_units") or 0) if graded in ("WIN", "LOSS") else 0.0

        if not bet_placed:
            n_pass += 1
            continue
        n_bets += 1
        units_risked += units
        pl_total     += pl
        if graded == "WIN":
            n_wins += 1
            if strength == "STRONG": n_strong += 1; n_strong_wins += 1
            elif strength == "LEAN": n_lean   += 1; n_lean_wins   += 1
        elif graded == "LOSS":
            n_losses += 1
            if strength == "STRONG": n_strong += 1
            elif strength == "LEAN": n_lean   += 1

    hit  = (n_wins / n_bets) if n_bets else 0.0
    roi  = (pl_total / units_risked) if units_risked else 0.0
    sh   = (n_strong_wins / n_strong) if n_strong else 0.0
    lh   = (n_lean_wins   / n_lean)   if n_lean   else 0.0
    return {
        "n_bets":       n_bets,
        "n_pass":       n_pass,
        "n_wins":       n_wins,
        "n_losses":     n_losses,
        "units_risked": units_risked,
        "pl_total":     pl_total,
        "hit":          hit,
        "roi":          roi,
        "n_strong":     n_strong,
        "strong_hit":   sh,
        "n_lean":       n_lean,
        "lean_hit":     lh,
    }


def disagreements(prod_picks: list[dict], variant_lookup: dict,
                  variant_name: str) -> list[dict]:
    """Find games where the variant's would-bet decision differed from
    production's bet_placed=Y decision (either side or stake).
    Returns rows sorted by P/L delta, biggest first."""
    out = []
    for p in prod_picks:
        v = variant_lookup.get((p["date"], str(p["game_pk"]), variant_name))
        if v is None:
            continue
        prod_side = (p.get("pick_side") or "").upper()
        prod_strength = (p.get("pick_strength") or "").upper()
        # Apply production policy (same as aggregate(): STRONG = always bet)
        prod_bet = (prod_strength == "STRONG"
                    or (prod_strength == "LEAN"
                        and (p.get("bet_placed") or "").upper() == "Y"))
        var_side = (v.get("pick_side") or "").upper()
        var_strength = (v.get("pick_strength") or "").upper()
        var_bet = bool(v.get("would_bet"))
        if prod_side == var_side and prod_strength == var_strength and prod_bet == var_bet:
            continue   # same verdict, same stake -- not a disagreement

        prod_pl = float(p.get("profit_loss_units") or 0) if prod_bet else 0.0
        var_pl  = float(v.get("profit_loss_units")  or 0) if var_bet  else 0.0
        out.append({
            "date":         p["date"],
            "matchup":      f"{p['away_team']}@{p['home_team']}",
            "prod":         f"{prod_strength} {prod_side}".strip() if prod_bet else "PASS",
            "var":          f"{var_strength} {var_side}".strip() if var_bet  else "PASS",
            "prod_pl":      prod_pl,
            "var_pl":       var_pl,
            "delta":        var_pl - prod_pl,
            "fi_runs":      p.get("fi_total_runs"),
            "actual":       p.get("actual_result"),
        })
    out.sort(key=lambda r: r["delta"], reverse=True)
    return out


def print_summary(prod_agg: dict, variant_aggs: dict[str, dict]) -> None:
    print()
    print("=" * 92)
    print("  Production vs Variants -- aggregate over the requested window")
    print("=" * 92)
    print(f"  {'arm':10} {'bets':>6} {'pass':>5} {'W':>4} {'L':>4} "
          f"{'units':>9} {'P/L':>9} {'ROI':>7} {'hit':>7} "
          f"{'strong-hit':>11} {'lean-hit':>11}")
    print("  " + "-" * 88)
    rows = [("PROD", prod_agg)] + [(f"VAR-{k}", v) for k, v in variant_aggs.items()]
    for label, a in rows:
        srk = f"{a['n_strong']}({a['strong_hit']*100:.0f}%)"
        lrk = f"{a['n_lean']}({a['lean_hit']*100:.0f}%)" if a["n_lean"] else "0"
        print(f"  {label:10} {a['n_bets']:>6} {a['n_pass']:>5} "
              f"{a['n_wins']:>4} {a['n_losses']:>4} "
              f"{a['units_risked']:>8.2f}u {a['pl_total']:>+8.2f}u "
              f"{a['roi']*100:>+5.1f}% {a['hit']*100:>5.1f}% "
              f"{srk:>11} {lrk:>11}")
    print()
    # Variant deltas vs production
    print("  Variant deltas vs production:")
    for k, v in variant_aggs.items():
        d_pl = v["pl_total"] - prod_agg["pl_total"]
        d_bet = v["n_bets"]  - prod_agg["n_bets"]
        d_hit = (v["hit"] - prod_agg["hit"]) * 100.0
        sign  = "+" if d_pl >= 0 else ""
        print(f"    VAR-{k:3} P/L delta {sign}{d_pl:.2f}u   "
              f"bets {d_bet:+d}   hit {d_hit:+.1f}pp")
    print()


def print_disagreements(rows: list[dict], variant_name: str, top: int) -> None:
    if not rows:
        print(f"  No disagreements between PROD and VAR-{variant_name}.")
        return
    print(f"  Top {top} disagreements VAR-{variant_name} (sorted by P/L delta):")
    print(f"  {'date':10}  {'matchup':10}  {'prod':>14}  {'var':>14}  "
          f"{'prod-pl':>9}  {'var-pl':>9}  {'delta':>9}")
    print("  " + "-" * 86)
    for r in rows[:top]:
        d_sign = "+" if r["delta"] >= 0 else ""
        print(f"  {r['date']:10}  {r['matchup']:10}  "
              f"{r['prod']:>14}  {r['var']:>14}  "
              f"{r['prod_pl']:>+8.2f}u  {r['var_pl']:>+8.2f}u  "
              f"{d_sign}{r['delta']:>+8.2f}u")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since",  metavar="YYYY-MM-DD")
    parser.add_argument("--season", type=int, default=datetime.now().year)
    parser.add_argument("--top",    type=int, default=10,
                        help="Top N disagreements per variant to print (default 10).")
    args = parser.parse_args()

    client = _get_client()
    if client is None:
        sys.exit("Supabase not configured.")

    prod_picks = fetch_production(client, args.season, args.since)
    if not prod_picks:
        sys.exit(f"No graded picks_{args.season} rows found "
                 f"(since={args.since or 'any'}).")
    print(f"Comparing {len(prod_picks)} graded production picks "
          f"(since {args.since or 'season start'}) vs variants A / C / AC.")

    var_lookup = fetch_variants(client, args.since)

    prod_agg     = aggregate(prod_picks, var_lookup, None)
    variant_aggs = {
        v: aggregate(prod_picks, var_lookup, v) for v in ("A", "C", "AC")
    }

    print_summary(prod_agg, variant_aggs)

    for v in ("A", "C", "AC"):
        diffs = disagreements(prod_picks, var_lookup, v)
        print_disagreements(diffs, v, args.top)


if __name__ == "__main__":
    main()

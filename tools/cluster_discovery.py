#!/usr/bin/env python3
"""tools/cluster_discovery.py

Scan the season ledger for STRONG-bet feature combinations that have
underperformed expectations.  Output is a ranked list of candidate
clusters the operator should consider adding to
loss_cluster_monitor.py for ongoing watching.

Discovery proceeds in three nested resolutions, each filtered by
sample-size and hit-rate thresholds chosen to reduce false positives:

  1.  By (side, prob_band)                -- coarsest, large samples
  2.  By (side, prob_band, lambda_band)   -- mid-resolution
  3.  By (side, prob_band, lambda_band, pitcher_min_quality)
                                          -- finest, smallest samples

Why three resolutions: a true loss cluster usually shows up at the
COARSE level too -- if the bug is "all STRONG YRFI in nrfi_p band
0.4 lose," that's visible in resolution 1 already.  Spurious finer-
grained "clusters" with n=3 are filtered out.

Output: one ranked table per resolution.  Operator reads the tables,
picks clusters that look like real signal, and adds them to
tools/loss_cluster_monitor.py CLUSTERS list.

This tool is read-only.  It never mutates CSVs, never sends Telegram,
never demotes bets.  Discovery and action are separate concerns.

Pipeline:
  Discovery  -> Monitor                   -> (later) Demotion
  THIS TOOL  -> loss_cluster_monitor.py   -> apply_cluster_demotion.py

Usage:
  python tools/cluster_discovery.py                # scan whole season
  python tools/cluster_discovery.py --since 2026-04-15
  python tools/cluster_discovery.py --min-n 5      # lower the floor
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402


# --------------------------------------------------------------------------
# Bucket definitions
# --------------------------------------------------------------------------

def _prob_bucket(p: float) -> str:
    """nrfi_prob bucket.  Coarsened around the model's STRONG thresholds
    (0.44 / 0.56) so candidate clusters align with where bets actually
    commit, not arbitrary cutoffs."""
    if p < 0.40:  return "deep_yrfi (<0.40)"
    if p < 0.42:  return "near_yrfi (0.40-0.42)"
    if p < 0.44:  return "edge_yrfi (0.42-0.44)"
    if p < 0.50:  return "low_pass (0.44-0.50)"
    if p < 0.56:  return "high_pass (0.50-0.56)"
    if p < 0.59:  return "marginal_nrfi (0.56-0.59)"
    if p < 0.64:  return "mid_nrfi (0.59-0.64)"
    return            "deep_nrfi (>=0.64)"


def _lambda_bucket(lam: float) -> str:
    if lam < 0.75: return "low (<0.75)"
    if lam < 0.95: return "mid_low (0.75-0.95)"
    if lam < 1.15: return "mid_high (0.95-1.15)"
    return            "high (>=1.15)"


def _park_bucket(park: float) -> str:
    if park < 0.95: return "pitcher (<0.95)"
    if park < 1.05: return "neutral (0.95-1.05)"
    if park < 1.15: return "mild_hitter (1.05-1.15)"
    return            "hitter (>=1.15)"


def _pitcher_min_q(away_q: str, home_q: str) -> str:
    """Worst-quality side of the pitching pair.  sm < ltd < live.
    Empty / unknown maps to 'avg' (placeholder)."""
    order = {"sm": 0, "ltd": 1, "live": 2, "avg": -1}
    aq = (away_q or "").strip().lower()
    hq = (home_q or "").strip().lower()
    a = order.get(aq, -1)
    h = order.get(hq, -1)
    rev = {v: k for k, v in order.items()}
    return rev.get(min(a, h), "avg")


def _safe_float(v: str | None) -> float | None:
    if v is None:
        return None
    s = (v or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def _load_bets(rows: list[dict], since_iso: str | None) -> list[dict]:
    """Filter rows to STRONG NRFI/YRFI bets that resolved W/L."""
    bets = []
    for r in rows:
        if since_iso and (r.get("date") or "") < since_iso:
            continue
        if (r.get("pick_strength") or "").strip().upper() != "STRONG":
            continue
        side = (r.get("pick_side") or "").strip().upper()
        if side not in ("NRFI", "YRFI"):
            continue
        if (r.get("graded_result") or "").strip().upper() not in ("WIN", "LOSS"):
            continue
        if (r.get("bet_placed") or "").strip().upper() != "Y":
            continue
        p   = _safe_float(r.get("nrfi_prob"))
        lam = _safe_float(r.get("combined_lambda"))
        park = _safe_float(r.get("park_factor"))
        pl  = _safe_float(r.get("profit_loss_units")) or 0.0
        if p is None or lam is None or park is None:
            continue
        bets.append({
            "date": r.get("date", ""),
            "game": f"{(r.get('away_team') or '').upper()}@"
                    f"{(r.get('home_team') or '').upper()}",
            "side": side,
            "prob_b":  _prob_bucket(p),
            "lam_b":   _lambda_bucket(lam),
            "park_b":  _park_bucket(park),
            "pq":      _pitcher_min_q(r.get("away_pitcher_q"),
                                      r.get("home_pitcher_q")),
            "win":     (r.get("graded_result") or "").upper() == "WIN",
            "pl":      pl,
            "p":       p,
            "lam":     lam,
            "park":    park,
        })
    return bets


def _agg(group: list[dict]) -> dict:
    """Compute the metrics for a single feature-bucket group."""
    n = len(group)
    w = sum(1 for b in group if b["win"])
    return {
        "n":       n,
        "wins":    w,
        "losses":  n - w,
        "hit":     (w / n) if n else 0.0,
        "pl":      sum(b["pl"] for b in group),
    }


def _print_table(title: str,
                 keys: list[tuple],
                 groups: dict,
                 col_specs: list[tuple[str, int]],
                 *, min_n: int, max_hit: float, max_rows: int) -> None:
    """Pretty-print a candidate-cluster table.  `keys` is the field
    names that compose the bucket key; col_specs is [(label, width)]."""
    candidates = []
    for k in groups:
        m = _agg(groups[k])
        if m["n"] < min_n:
            continue
        if m["hit"] > max_hit:
            continue
        candidates.append((k, m))
    candidates.sort(key=lambda x: x[1]["pl"])    # worst P&L first

    print()
    print(f"== {title} ==")
    if not candidates:
        print(f"   (no clusters meet n>={min_n} AND hit<={max_hit*100:.0f}%)")
        return

    header = "".join(f"{label:<{w}}" for label, w in col_specs)
    header += f"{'n':>4}{'W':>4}{'L':>4}{'Hit %':>8}{'Net P&L':>12}"
    print("   " + header)
    print("   " + "-" * len(header))
    for k, m in candidates[:max_rows]:
        # k is a tuple; render each element to its column
        parts = "".join(f"{str(field):<{w}}"
                        for field, (_, w) in zip(k, col_specs))
        print(f"   {parts}{m['n']:>4}{m['wins']:>4}{m['losses']:>4}"
              f"{m['hit']*100:>7.0f}%{m['pl']:>+12.3f}u")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since",
                   help="ISO date (YYYY-MM-DD); only consider bets graded on or after.")
    p.add_argument("--season", type=int, default=None,
                   help="Season override (defaults to current ET year).")
    p.add_argument("--min-n", type=int, default=8,
                   help="Minimum sample size for resolution-1/2 clusters (default 8).")
    p.add_argument("--min-n-fine", type=int, default=4,
                   help="Minimum sample size for resolution-3 clusters (default 4).")
    p.add_argument("--max-hit", type=float, default=0.40,
                   help="Maximum hit rate to flag (default 0.40 = 40%%).")
    p.add_argument("--max-hit-fine", type=float, default=0.30,
                   help="Tighter ceiling for resolution-3 (default 0.30).")
    p.add_argument("--max-rows", type=int, default=10,
                   help="Top N rows to show per resolution (default 10).")
    args = p.parse_args()

    from datetime import datetime
    from zoneinfo import ZoneInfo
    season = args.season or datetime.now(ZoneInfo("America/New_York")).year
    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(csv_path)
    bets = _load_bets(rows, args.since)

    overall = _agg(bets)
    since_label = args.since or f"{season}-01-01"
    print(f"cluster_discovery  season={season}  since={since_label}  "
          f"strong_bets={overall['n']}  hit={overall['hit']*100:.1f}%  "
          f"net P&L={overall['pl']:+.3f}u")
    if overall["n"] == 0:
        return 0

    # --- Resolution 1: (side, prob_band) ---
    g1 = defaultdict(list)
    for b in bets:
        g1[(b["side"], b["prob_b"])].append(b)
    _print_table(
        "Resolution 1 -- (side, prob_band)",
        keys=["side", "prob"],
        groups=g1,
        col_specs=[("Side", 6), ("Prob band", 24)],
        min_n=args.min_n,
        max_hit=args.max_hit,
        max_rows=args.max_rows,
    )

    # --- Resolution 2: (side, prob_band, lambda_band) ---
    g2 = defaultdict(list)
    for b in bets:
        g2[(b["side"], b["prob_b"], b["lam_b"])].append(b)
    _print_table(
        "Resolution 2 -- (side, prob_band, lambda_band)",
        keys=["side", "prob", "lambda"],
        groups=g2,
        col_specs=[("Side", 6), ("Prob band", 24), ("Lambda", 22)],
        min_n=args.min_n,
        max_hit=args.max_hit,
        max_rows=args.max_rows,
    )

    # --- Resolution 3: (side, prob_band, lambda_band, pitcher_min_q) ---
    g3 = defaultdict(list)
    for b in bets:
        g3[(b["side"], b["prob_b"], b["lam_b"], b["pq"])].append(b)
    _print_table(
        "Resolution 3 -- (side, prob_band, lambda_band, pitcher_min_q)",
        keys=["side", "prob", "lambda", "pq"],
        groups=g3,
        col_specs=[("Side", 6), ("Prob band", 24),
                   ("Lambda", 22), ("PQ", 6)],
        min_n=args.min_n_fine,
        max_hit=args.max_hit_fine,
        max_rows=args.max_rows,
    )

    print()
    print("Next steps when you see a candidate cluster you want to track:")
    print("  1. Add a match predicate + threshold to the CLUSTERS list in")
    print("     tools/loss_cluster_monitor.py (use yrfi_040_band as a template).")
    print("  2. Wait until loss_cluster_streak Telegram fires for the cluster.")
    print("  3. If it fires AND backtest confirms the cluster is durable,")
    print("     add a row to data/cluster_demotions.json so")
    print("     tools/apply_cluster_demotion.py auto-demotes future bets.")
    print()
    print("DO NOT skip step 2: a cluster that LOOKS bad over n=10 may revert")
    print("to expectation over n=20.  Premature demotions overfit to noise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

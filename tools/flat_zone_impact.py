#!/usr/bin/env python3
"""
tools/flat_zone_impact.py -- empirical study of how a calibrator
flat-zone filter would have changed STRONG bet outcomes over the
trailing 14 days.

For each daily picks diagnostic JSON, look at every STRONG pick that
got graded.  Group by calibrator band/flat-zone properties.  Tally
W/L and P&L (computed from picks_2026.csv) by group, then simulate
"what if we demoted STRONG to PASS under rule X" and show the
hypothetical P&L delta.

Read-only: doesn't change any production data.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAG_DIR = ROOT / "tmp_diag"  # populated via git show
PICKS_CSV = ROOT / "data" / "picks_2026.csv"


def load_pick_outcomes() -> dict:
    """Map (date, game_pk) -> dict with pick_strength, pick_side, result, units, odds, pl."""
    out = {}
    if not PICKS_CSV.exists():
        return out
    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["date"], r["game_pk"])
            out[key] = {
                "pick_side": r.get("pick_side") or "",
                "pick_strength": r.get("pick_strength") or "",
                "graded_result": r.get("graded_result") or "",
                "bet_placed": r.get("bet_placed") or "",
                "market_nrfi_odds": r.get("market_nrfi_odds") or "",
                "market_yrfi_odds": r.get("market_yrfi_odds") or "",
                "profit_loss_units": r.get("profit_loss_units") or "",
            }
    return out


def american_to_payout(odds_str: str) -> float | None:
    """+115 -> 1.15 (profit per 1u win).  -130 -> 0.769.  Returns None if missing."""
    s = odds_str.strip() if odds_str else ""
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    if n > 0:
        return n / 100.0
    return 100.0 / abs(n)


def pl_for(rec: dict) -> float | None:
    """Recompute P&L for a STRONG bet using the row's stored odds."""
    side = rec.get("pick_side", "")
    result = rec.get("graded_result", "")
    if result not in ("WIN", "LOSS"):
        return None
    if side == "NRFI":
        odds = rec.get("market_nrfi_odds", "")
    elif side == "YRFI":
        odds = rec.get("market_yrfi_odds", "")
    else:
        return None
    payout = american_to_payout(odds)
    if payout is None:
        # fall back to flat -110
        payout = 100.0 / 110.0
    return payout if result == "WIN" else -1.0


def main() -> None:
    outcomes = load_pick_outcomes()
    print(f"Loaded {len(outcomes)} pick rows from picks_2026.csv\n")

    # Collect STRONG picks with band info
    rows = []
    for fp in sorted(DIAG_DIR.glob("2026-05-*.json")):
        date = fp.stem
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        for p in d.get("picks", []):
            if p.get("pick_strength") != "STRONG":
                continue
            key = (date, str(p.get("game_pk", "")))
            outc = outcomes.get(key)
            if not outc:
                continue
            result = outc.get("graded_result", "")
            if result not in ("WIN", "LOSS"):
                continue
            band = p.get("calibrator_band", {})
            cal_p = p.get("calibrated_p_nrfi", 0.5)
            side = p.get("pick_side", "")
            pl = pl_for(outc)
            rows.append({
                "date":       date,
                "matchup":    p.get("matchup", ""),
                "side":       side,
                "cal_p":      cal_p,
                "band":       band.get("band", ""),
                "is_flat":    bool(band.get("is_flat", False)),
                "flat_size":  int(band.get("flat_size") or 0),
                "flat_rate":  band.get("flat_rate"),
                "result":     result,
                "pl":         pl if pl is not None else 0.0,
            })

    if not rows:
        sys.exit("No STRONG graded picks found in the 14-day window.")

    n_total = len(rows)
    n_wins = sum(1 for r in rows if r["result"] == "WIN")
    pl_total = sum(r["pl"] for r in rows)
    print(f"All STRONG bets (graded), 14-day window:")
    print(f"  {n_wins}W / {n_total - n_wins}L  ({n_wins/n_total*100:.1f}% hit)  P&L = {pl_total:+.2f}u")
    print()

    # Group by band type
    by_band = {}
    for r in rows:
        b = r["band"]
        by_band.setdefault(b, []).append(r)
    print(f"By band ({len(by_band)} buckets):")
    print(f"  {'band':>12} {'n':>3} {'W':>3} {'L':>3} {'hit%':>5} {'P&L':>7}  flat_size_range")
    for b in sorted(by_band.keys()):
        bucket = by_band[b]
        w = sum(1 for x in bucket if x["result"] == "WIN")
        l = len(bucket) - w
        pl = sum(x["pl"] for x in bucket)
        fmin = min(x["flat_size"] for x in bucket)
        fmax = max(x["flat_size"] for x in bucket)
        hit = w / len(bucket) * 100
        print(f"  {b:>12} {len(bucket):>3} {w:>3} {l:>3} {hit:>4.0f}% {pl:>+7.2f}  {fmin}-{fmax}")
    print()

    # Group by flat_size
    by_flat = {}
    for r in rows:
        f = r["flat_size"]
        by_flat.setdefault(f, []).append(r)
    print(f"By flat_size:")
    print(f"  {'flat_size':>9} {'n':>3} {'W':>3} {'L':>3} {'hit%':>5} {'P&L':>7}")
    for f in sorted(by_flat.keys()):
        bucket = by_flat[f]
        w = sum(1 for x in bucket if x["result"] == "WIN")
        l = len(bucket) - w
        pl = sum(x["pl"] for x in bucket)
        hit = w / len(bucket) * 100
        print(f"  {f:>9} {len(bucket):>3} {w:>3} {l:>3} {hit:>4.0f}% {pl:>+7.2f}")
    print()

    # Hypothetical filter: demote any STRONG with band == "below_min" or "above_max"
    print(f"=== Hypothetical filters ===")
    for label, predicate in [
        ("Demote if band==below_min",   lambda r: r["band"] == "below_min"),
        ("Demote if band==above_max",   lambda r: r["band"] == "above_max"),
        ("Demote if is_flat AND fs>=3", lambda r: r["is_flat"] and r["flat_size"] >= 3),
        ("Demote if is_flat AND fs>=4", lambda r: r["is_flat"] and r["flat_size"] >= 4),
        ("Demote if is_flat AND fs>=5", lambda r: r["is_flat"] and r["flat_size"] >= 5),
        ("Demote if is_flat AND fs>=6", lambda r: r["is_flat"] and r["flat_size"] >= 6),
        ("Demote if flat_size>=3 OR extrapolated", lambda r: r["flat_size"] >= 3 or r["band"] in ("below_min", "above_max")),
        ("Demote if flat_size>=4 OR extrapolated", lambda r: r["flat_size"] >= 4 or r["band"] in ("below_min", "above_max")),
    ]:
        demoted = [r for r in rows if predicate(r)]
        kept = [r for r in rows if not predicate(r)]
        if not demoted:
            continue
        # Demoted picks become PASS (P&L = 0 for those bets)
        # Net delta vs. status quo = -sum(demoted P&L)
        demoted_pl = sum(r["pl"] for r in demoted)
        new_pl = pl_total - demoted_pl
        n_d = len(demoted)
        w_d = sum(1 for r in demoted if r["result"] == "WIN")
        n_k = len(kept)
        w_k = sum(1 for r in kept if r["result"] == "WIN")
        delta = -demoted_pl
        print(f"  {label}")
        print(f"     demoted: {w_d}W/{n_d-w_d}L = {demoted_pl:+.2f}u   (giving these up)")
        print(f"     kept:    {w_k}W/{n_k-w_k}L = {sum(r['pl'] for r in kept):+.2f}u")
        print(f"     net P&L: {pl_total:+.2f}  ->  {new_pl:+.2f}  (delta {delta:+.2f}u)")
        print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
tools/filter_impact_check.py -- count STRONG bets in last 14 days
that the new flat-zone filter would have demoted.

For each STRONG bet_placed=Y row in picks_2026.csv with a stored
calibrated probability, infer which calibrator band it came from
and compute flat_size.  Demoted iff flat_size >= _FLAT_ZONE_DEMOTE_SIZE.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from calibration import ProbCalibrator  # noqa: E402

THRESHOLD = 4  # match _FLAT_ZONE_DEMOTE_SIZE


def infer_band(cal_p: float, cal: ProbCalibrator) -> dict:
    """
    Given a stored calibrated P(NRFI), figure out which calibrator
    band/raw-p range produced it.  Returns the same dict shape that
    ProbCalibrator.predict_with_band returns.

    Two ambiguity cases:
    1. cal_p equals the first or last bin's rate -- could be from
       in-range bin or extrapolation.  We pick the *interior* band
       (more conservative for the filter: extrapolation has
       flat_size=0 so is never demoted; bin_0-1 has flat_size=2,
       also never demoted under threshold 4 -- so the answer is
       the same).
    2. cal_p equals a flat-zone rate exactly -- could be from
       multiple bins.  We use the widest such flat zone, which
       gives the most pessimistic (largest) flat_size.
    """
    c, r = cal.centers, cal.rates
    # Special case: cal_p exactly equals first or last rate.
    EPS = 1e-6
    if abs(cal_p - r[0]) < EPS:
        # bin_0-1.  flat_size could be 2 (just bin 0-1) or extended
        # if r[2] also matches.  Walk.
        right = 1
        while right < len(r) - 1 and abs(r[right + 1] - r[0]) < EPS:
            right += 1
        return {"band": "bin_0-1_or_below_min", "is_flat": (right + 1) >= 3,
                "flat_size": right + 1, "flat_rate": r[0] if (right + 1) >= 3 else None}
    if abs(cal_p - r[-1]) < EPS:
        left = len(r) - 2
        while left > 0 and abs(r[left - 1] - r[-1]) < EPS:
            left -= 1
        size = len(r) - left
        return {"band": f"bin_{len(r)-2}-{len(r)-1}_or_above_max",
                "is_flat": size >= 3, "flat_size": size,
                "flat_rate": r[-1] if size >= 3 else None}
    # Walk through interpolation segments to find the one whose
    # cal_p range covers our target.
    for i in range(len(r) - 1):
        r0, r1 = r[i], r[i + 1]
        lo_cp, hi_cp = (r0, r1) if r0 <= r1 else (r1, r0)
        if lo_cp - EPS <= cal_p <= hi_cp + EPS:
            # Either same-rate flat segment, or strict interpolation
            # (since the calibrator is monotonic increasing).
            # Compute the flat_size around (i, i+1).
            flat_left = i
            flat_right = i + 1
            while flat_left > 0 and abs(r[flat_left - 1] - r[i]) < EPS:
                flat_left -= 1
            while flat_right < len(r) - 1 and abs(r[flat_right + 1] - r[i + 1]) < EPS:
                flat_right += 1
            flat_size = flat_right - flat_left + 1
            return {"band": f"bin_{i}-{i+1}", "is_flat": flat_size >= 3,
                    "flat_size": flat_size,
                    "flat_rate": r[i] if (r[i] == r[i+1] and flat_size >= 3) else None}
    return {"band": "unknown", "is_flat": False, "flat_size": 0, "flat_rate": None}


def main():
    cal = ProbCalibrator.load("data/calibration_v2.json")
    print(f"Calibrator: {len(cal.centers)} bins, train_seasons={cal.train_seasons}")
    print(f"Filter threshold: flat_size >= {THRESHOLD}")
    print()

    # Categorize each STRONG bet
    bets = []
    with open("data/picks_2026.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] < "2026-05-12" or row["date"] > "2026-05-26":
                continue
            if row.get("pick_strength", "") != "STRONG":
                continue
            if row.get("bet_placed", "") != "Y":
                continue
            try:
                cal_p = float(row["nrfi_prob"])
            except (KeyError, ValueError, TypeError):
                continue
            info = infer_band(cal_p, cal)
            result = row.get("graded_result", "")
            try:
                pl = float(row.get("profit_loss_units", "") or 0)
            except ValueError:
                pl = 0.0
            bets.append({
                "date": row["date"],
                "matchup": f"{row.get('away_team','')}@{row.get('home_team','')}",
                "side": row["pick_side"],
                "cal_p": cal_p,
                "band": info["band"],
                "flat_size": info["flat_size"],
                "result": result,
                "pl": pl,
            })

    n_total = len(bets)
    pl_total = sum(b["pl"] for b in bets)
    w_total = sum(1 for b in bets if b["result"] == "WIN")
    print(f"All STRONG bets 5/12-5/26: {n_total} bets, {w_total}W/{n_total-w_total}L, P&L = {pl_total:+.2f}u\n")

    demoted = [b for b in bets if b["flat_size"] >= THRESHOLD]
    kept = [b for b in bets if b["flat_size"] < THRESHOLD]
    print(f"Demoted by filter (flat_size >= {THRESHOLD}): {len(demoted)} bets")
    if demoted:
        d_w = sum(1 for b in demoted if b["result"] == "WIN")
        d_pl = sum(b["pl"] for b in demoted)
        print(f"  Demoted record: {d_w}W/{len(demoted)-d_w}L, P&L = {d_pl:+.2f}u")
        print()
        print(f"  {'date':>10} {'matchup':>10} {'side':>4} {'cal_p':>7} {'band':>14} {'fs':>3} {'result':>5} {'pl':>7}")
        for b in demoted:
            print(f"  {b['date']:>10} {b['matchup']:>10} {b['side']:>4} {b['cal_p']:>7.4f} {b['band']:>14} {b['flat_size']:>3} {b['result']:>5} {b['pl']:>+7.2f}")
        print()
    n_kept = len(kept)
    k_w = sum(1 for b in kept if b["result"] == "WIN")
    k_pl = sum(b["pl"] for b in kept)
    print(f"Kept by filter: {n_kept} bets, {k_w}W/{n_kept-k_w}L, P&L = {k_pl:+.2f}u")
    print()
    new_pl = k_pl
    delta = new_pl - pl_total
    print(f"=== Net effect ===")
    print(f"  Before filter: {pl_total:+.2f}u")
    print(f"  After filter:  {new_pl:+.2f}u   (delta {delta:+.2f}u)")


if __name__ == "__main__":
    main()

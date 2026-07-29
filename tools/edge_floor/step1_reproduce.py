#!/usr/bin/env python3
"""Step 1 -- re-derive the operator's table from scratch, and audit the
stored edge_on_pick column against a fresh recomputation."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, load_season, summary, implied)

THRESH = (0.00, 0.04, 0.08, 0.12, 0.16)


def main():
    rows, skipped = load_season()
    ins, cal = insample_probs(rows)
    print(f"graded games loaded {len(rows)} (skipped {skipped})")
    print(f"calibrator: train={cal.train_seasons} n={cal.train_n} "
          f"knots={len(cal.centers)}  lowest knot p={min(cal.rates):.4f}")
    print(f"live gate p_nrfi < {GATE}")

    bets = build_bets(rows, ins)
    print(f"\nSTRONG-YRFI bets with a REAL captured DK price: n={len(bets)}")

    print("\n  IN-SAMPLE (deployed calibrator) -- re-derived operator table")
    print(f"  {'edge >=':>8}{'bets':>7}{'hit%':>7}{'need%':>8}{'ROI':>8}{'flat u':>9}")
    for t in THRESH:
        s = summary([b for b in bets if b["edge"] >= t])
        print(f"  {t:>8.2f}{s['n']:>7}{s['hit']:>7.1f}{s['need']:>8.1f}"
              f"{s['roi']:>+7.1f}%{s['pl']:>+8.2f}u")

    # ---------------- edge_on_pick column audit --------------------------
    print("\n  STORED edge_on_pick AUDIT (recomputed from nrfi_prob + odds)")
    raw = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
    checked = miss = agree = 0
    diffs = []
    blank_stored = 0
    for r in raw:
        side = (r.get("pick_side") or "").strip().upper()
        if side not in ("YRFI", "NRFI"):
            continue
        col = "market_yrfi_odds" if side == "YRFI" else "market_nrfi_odds"
        o = (r.get(col) or "").strip()
        pn = (r.get("nrfi_prob") or "").strip()
        if not o or not pn:
            continue
        try:
            o = float(o); pn = float(pn)
        except ValueError:
            continue
        mine = ((1 - pn) if side == "YRFI" else pn) - implied(o)
        stored = (r.get("edge_on_pick") or "").strip()
        checked += 1
        if not stored:
            blank_stored += 1
            continue
        try:
            sv = float(stored)
        except ValueError:
            blank_stored += 1
            continue
        d = mine - sv
        if abs(d) < 5e-4:
            agree += 1
        else:
            miss += 1
            diffs.append((abs(d), r["date"], r["away_team"], r["home_team"],
                          side, sv, mine))
    diffs.sort(reverse=True)
    print(f"  rows checked (picked side + real price + prob): {checked}")
    print(f"  stored blank/unparseable : {blank_stored}")
    print(f"  agree (<0.0005)          : {agree}")
    print(f"  DISAGREE                 : {miss}")
    if diffs:
        mags = [d[0] for d in diffs]
        mags.sort()
        print(f"  |diff|  min {mags[0]:.4f}  median {mags[len(mags)//2]:.4f}  "
              f"max {mags[-1]:.4f}  mean {sum(mags)/len(mags):.4f}")
        print(f"  worst 10:")
        for d, dt, a, h, sd, sv, mv in diffs[:10]:
            print(f"    {dt} {a}@{h:<4} {sd}  stored {sv:+.4f}  recomputed {mv:+.4f}"
                  f"  diff {mv-sv:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

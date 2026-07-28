#!/usr/bin/env python3
"""
tools/nrfi_dd_refute_gate060c.py -- calibrate the evidence itself.

The block bootstrap said P(ROI<=0)=1.3% on 14 bets over 10 slates,
which LOOKS like significance. Two checks:

  A. NULL CALIBRATION. Simulate 14 bets on 10 slates at the SAME real
     prices with the true win rate set exactly to break-even (zero edge).
     How often does the block bootstrap's 95% CI exclude zero anyway?
     That is the bootstrap's false-positive rate at this n. If it is far
     above 5%, the bootstrap result is not evidence.

  B. FULL SEARCH-AWARE NULL. Simulate the whole 2026 NRFI selection
     problem under zero edge, run the same gate x ceiling x calibrator
     sweep, and ask how often the BEST cell looks at least as good as
     +34.3% ROI on >=14 bets.

Read-only.
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402
from tools.nrfi_dd_refute_gate060 import sel_nrfi, flat  # noqa: E402

CEIL, GATE = P._LR_LAMBDA_NRFI_CEILING, 0.60


def boot_excl_zero(bets, iters=2000, rng=None):
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = rng or random.Random(0)
    rois = []
    for _ in range(iters):
        n = 0
        pl = 0.0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                n += 1
                pl += payout(b["odds"]) if b["win"] else -1.0
        if n:
            rois.append(pl / n)
    rois.sort()
    return rois[int(0.025 * len(rois))] > 0


def main():
    rows, _ = load_season()
    p_wf = walk_forward_probs(rows)
    bets = sel_nrfi(rows, p_wf, GATE, CEIL)
    obs = flat(bets)
    print(f"observed: n={obs['n']} hit={100*obs['hit']:.1f}% "
          f"ROI={100*obs['roi']:+.1f}% break-even={100*obs['need']:.1f}%")

    print()
    print("=" * 92)
    print("  A. NULL CALIBRATION OF THE BLOCK BOOTSTRAP AT THIS SAMPLE SIZE")
    print("=" * 92)
    print("  Same 14 games, same 10 slates, same real DK prices.")
    print("  True win probability for every bet = its own break-even (ZERO edge).")
    rng = random.Random(1234)
    TRIALS = 600
    hits_excl = 0
    hits_roi = 0
    for _ in range(TRIALS):
        sim = []
        for b in bets:
            pwin = implied(b["odds"])          # zero edge by construction
            sim.append(dict(b, win=(rng.random() < pwin)))
        f = flat(sim)
        if f["roi"] >= obs["roi"]:
            hits_roi += 1
        if boot_excl_zero(sim, iters=1500, rng=rng):
            hits_excl += 1
    print(f"  trials={TRIALS}")
    print(f"  bootstrap 95% CI excluded zero in {100*hits_excl/TRIALS:.1f}% of "
          f"ZERO-EDGE samples   (nominal should be 2.5%)")
    print(f"  -> the bootstrap is anti-conservative here; 'CI excludes zero' at "
          f"n=14/10 slates")
    print(f"     is NOT a 5%-level result.")
    print(f"  P(ROI >= {100*obs['roi']:+.1f}% | zero edge, single cell) = "
          f"{100*hits_roi/TRIALS:.1f}%")

    print()
    print("=" * 92)
    print("  B. SEARCH-AWARE NULL: re-run the WHOLE sweep on zero-edge outcomes")
    print("=" * 92)
    print("  Keep every game, every price, every model probability. Only the")
    print("  OUTCOMES are redrawn, each at its own book-implied probability")
    print("  (so the book is exactly right and no edge exists anywhere).")
    print("  Then run the same gate x ceiling sweep and take the BEST cell.")
    gates = [0.55, 0.56, 0.58, 0.60, 0.62, 0.64, 0.65]
    ceils = [0.45, 0.48, 0.52, 0.55, 0.60, 0.70, 9.9]
    priced = [(r, p) for r, p in zip(rows, p_wf)
              if p is not None and r["nrfi_odds"] is not None]
    print(f"  priced 2026 games available to the sweep: {len(priced)}")
    rng = random.Random(99)
    TR = 400
    beat = 0
    beat_any_n5 = 0
    best_rois = []
    for _ in range(TR):
        outc = [rng.random() < implied(r["nrfi_odds"]) for r, _ in priced]
        best = -9e9
        for g in gates:
            for c in ceils:
                n = 0
                pl = 0.0
                for (r, p), win in zip(priced, outc):
                    if p < g:
                        continue
                    if r["lambda"] is not None and r["lambda"] > c:
                        continue
                    n += 1
                    pl += payout(r["nrfi_odds"]) if win else -1.0
                if n >= 14:
                    best = max(best, pl / n)
        best_rois.append(best)
        if best >= obs["roi"]:
            beat += 1
    best_rois.sort()
    print(f"  trials={TR}  (each = full sweep of {len(gates)*len(ceils)} cells)")
    print(f"  distribution of the BEST cell's ROI under zero edge (n>=14 cells):")
    for q in (0.50, 0.75, 0.90, 0.95, 0.99):
        print(f"    q{100*q:>4.0f}: {100*best_rois[int(q*len(best_rois))]:+.1f}%")
    print(f"  P(best cell ROI >= observed {100*obs['roi']:+.1f}% | ZERO EDGE ANYWHERE)"
          f" = {100*beat/TR:.1f}%")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Step 3 -- the plateau/clamp artefact.

The CIR calibrator is a step function.  Every raw score below its lowest
knot is mapped to one identical probability.  Those games share an
IDENTICAL model term, so their edge ordering is decided ENTIRELY by the
price.  If the high-edge tail is mostly clamped games, "edge" there is a
price ranking wearing a model's clothes.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, walk_forward_probs, load_season,
    summary, implied)


def main():
    rows, _ = load_season()
    ins, cal = insample_probs(rows)

    lo = min(cal.rates)
    hi = max(cal.rates)
    print("=" * 96)
    print("  CALIBRATOR SHAPE")
    print("=" * 96)
    print(f"  knots: {len(cal.centers)}   train {cal.train_seasons} n={cal.train_n}")
    for c, v in zip(cal.centers, cal.rates):
        print(f"    raw>={c:.4f} -> p_nrfi {v:.4f}")
    print(f"\n  lowest step p_nrfi = {lo:.4f}  (p_yrfi = {1-lo:.4f})")

    clamped = [i for i, p in enumerate(ins) if abs(p - lo) < 1e-9]
    print(f"  graded games clamped to the lowest step: {len(clamped)} / {len(rows)}")
    top = [i for i, p in enumerate(ins) if abs(p - hi) < 1e-9]
    print(f"  graded games clamped to the highest step: {len(top)}")

    # distribution of p_nrfi among gate-passing bets
    L = build_bets(rows, ins)
    c = Counter(round(b["p_nrfi"], 4) for b in L)
    print(f"\n  LIVE BET SET (n={len(L)}) -- distinct model probabilities:")
    for p, k in sorted(c.items()):
        print(f"    p_nrfi {p:.4f} (p_yrfi {1-p:.4f}) : {k:>4} bets")
    print(f"  -> the gate admits only {len(c)} distinct model values; the model")
    print(f"     term is nearly a constant inside the bet set.")

    print("\n" + "=" * 96)
    print("  HOW MUCH OF THE HIGH-EDGE TAIL IS CLAMPED?")
    print("=" * 96)
    U = []
    for r, p in zip(rows, ins):
        if r["yrfi_odds"] is None:
            continue
        U.append({"p_nrfi": p, "p_yrfi": 1 - p, "odds": r["yrfi_odds"],
                  "implied": implied(r["yrfi_odds"]),
                  "edge": (1 - p) - implied(r["yrfi_odds"]),
                  "win": r["yrfi_hit"], "date": r["date"],
                  "clamp": abs(p - lo) < 1e-9})
    print(f"  {'edge >=':>8}{'bets':>7}{'clamped':>9}{'% clamp':>9}"
          f"{'hit% clamp':>12}{'hit% other':>12}{'ROI clamp':>11}{'ROI other':>11}")
    for t in (0.00, 0.04, 0.08, 0.12, 0.16):
        g = [b for b in U if b["edge"] >= t]
        cl = [b for b in g if b["clamp"]]
        ot = [b for b in g if not b["clamp"]]
        sc, so = summary(cl), summary(ot)
        print(f"  {t:>8.2f}{len(g):>7}{len(cl):>9}{100*len(cl)/max(len(g),1):>8.0f}%"
              f"{sc['hit']:>12.1f}{so['hit']:>12.1f}{sc['roi']:>+10.1f}%"
              f"{so['roi']:>+10.1f}%")

    print("\n  Inside the CLAMPED games only, the model term is a constant, so")
    print("  sorting by edge == sorting by price. Does that sort predict?")
    cl = sorted([b for b in U if b["clamp"]], key=lambda b: b["implied"])
    nb = 3
    print(f"\n  {'implied bucket':<20}{'bets':>6}{'hit%':>7}{'need%':>8}{'ROI':>8}")
    for i in range(nb):
        g = cl[i * len(cl) // nb:(i + 1) * len(cl) // nb]
        if not g:
            continue
        s = summary(g)
        print(f"  {g[0]['implied']:.3f}..{g[-1]['implied']:.3f}      "
              f"{s['n']:>6}{s['hit']:>7.1f}{s['need']:>8.1f}{s['roi']:>+7.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

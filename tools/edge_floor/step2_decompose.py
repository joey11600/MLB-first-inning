#!/usr/bin/env python3
"""Step 2 -- what population is the operator's table actually about, and
what is driving the monotone climb: the MODEL term or the PRICE term?

edge = (1 - p_nrfi) - implied(price).  Both terms vary.  If the lift is
carried by the price term the "floor" is really a bet-longer-prices
rule; if by the model term it is a restatement of the probability gate
the system already has.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, walk_forward_probs, load_season,
    summary, implied, payout, passes_gate)

THRESH = (0.00, 0.04, 0.08, 0.12, 0.16)


def univ(rows, probs):
    """Every graded game with a real DK YRFI price -- no gate, no floor."""
    out = []
    for r, p in zip(rows, probs):
        if p is None or r["yrfi_odds"] is None:
            continue
        imp = implied(r["yrfi_odds"])
        out.append({"rid": r["rid"], "date": r["date"], "p_nrfi": p,
                    "p_yrfi": 1.0 - p, "odds": r["yrfi_odds"], "implied": imp,
                    "edge": (1.0 - p) - imp, "win": r["yrfi_hit"],
                    "raw": r["raw"], "lambda": r["lambda"],
                    "gated": passes_gate(r, p)})
    return out


def table(bets, label, thresh=THRESH):
    print(f"\n  {label}   (n={len(bets)})")
    print(f"  {'edge >=':>8}{'bets':>7}{'hit%':>7}{'need%':>8}{'ROI':>8}{'flat u':>9}")
    for t in thresh:
        s = summary([b for b in bets if b["edge"] >= t])
        if not s["n"]:
            continue
        print(f"  {t:>8.2f}{s['n']:>7}{s['hit']:>7.1f}{s['need']:>8.1f}"
              f"{s['roi']:>+7.1f}%{s['pl']:>+8.2f}u")


def qbuckets(bets, key, nb=5):
    """Equal-count buckets by `key`."""
    s = sorted(bets, key=lambda b: b[key])
    n = len(s)
    return [s[i * n // nb:(i + 1) * n // nb] for i in range(nb)]


def show_buckets(bets, key, label, nb=5):
    print(f"\n  --- {label} ---")
    print(f"  {'bucket':<22}{'bets':>6}{'hit%':>7}{'need%':>8}{'ROI':>8}"
          f"{'mean p_y':>10}{'mean imp':>10}{'mean edge':>11}")
    for b in qbuckets(bets, key, nb):
        if not b:
            continue
        s = summary(b)
        rng = f"{b[0][key]:.3f}..{b[-1][key]:.3f}"
        mp = sum(x["p_yrfi"] for x in b) / len(b)
        mi = sum(x["implied"] for x in b) / len(b)
        me = sum(x["edge"] for x in b) / len(b)
        print(f"  {rng:<22}{s['n']:>6}{s['hit']:>7.1f}{s['need']:>8.1f}"
              f"{s['roi']:>+7.1f}%{mp:>10.3f}{mi:>10.3f}{me:>+11.3f}")


def joint(bets, nb=3):
    """2-D: model term (rows) x price term (cols), equal-count on each."""
    ps = sorted(bets, key=lambda b: b["p_yrfi"])
    cut_p = [ps[i * len(ps) // nb][ "p_yrfi"] for i in range(nb)] + [9]
    isort = sorted(bets, key=lambda b: b["implied"])
    cut_i = [isort[i * len(isort) // nb]["implied"] for i in range(nb)] + [9]

    def bi(v, cuts):
        for k in range(nb - 1, -1, -1):
            if v >= cuts[k]:
                return k
        return 0

    cell = defaultdict(list)
    for b in bets:
        cell[(bi(b["p_yrfi"], cut_p), bi(b["implied"], cut_i))].append(b)
    print(f"\n  --- JOINT: model p_yrfi (rows) x price implied (cols), "
          f"{nb}x{nb} equal-count marginals ---")
    print("      price tertile ->  " + "".join(
        f"{'LOW(long)' if c==0 else ('MID' if c==1 else 'HIGH(short)'):>22}"
        for c in range(nb)))
    for rr in range(nb - 1, -1, -1):
        lab = f"  p_yrfi t{rr+1} >={cut_p[rr]:.3f}"
        line = f"{lab:<22}"
        for cc in range(nb):
            g = cell[(rr, cc)]
            if not g:
                line += f"{'--':>22}"
                continue
            s = summary(g)
            line += f"{s['n']:>5}n {s['hit']:>5.1f}% {s['roi']:>+6.1f}%".rjust(22)
        print(line)


def main():
    rows, _ = load_season()
    ins, cal = insample_probs(rows)
    wf = walk_forward_probs(rows)

    U = univ(rows, ins)
    print("=" * 100)
    print("  WHAT POPULATION IS THE OPERATOR'S TABLE?")
    print("=" * 100)
    table(U, "ALL graded games with a real DK YRFI price -- NO gate, NO lambda floor")
    print("\n  ^ this reproduces the brief's table (495/217/89/35/11 -> 498/218/90/36/12).")
    print("    It is the WHOLE priced universe, not the set the live rule bets.")

    L = build_bets(rows, ins)
    table(L, f"THE LIVE RULE: STRONG YRFI (p_nrfi<{GATE}) + weather lambda floor, real prices")

    ng = [b for b in U if b["gated"]]
    print(f"\n  overlap: of the {len([b for b in U if b['edge']>=0.08])} games at "
          f"edge>=0.08, {len([b for b in ng if b['edge']>=0.08])} "
          f"already pass the live gate.")
    for t in THRESH:
        hi = [b for b in U if b["edge"] >= t]
        g = [b for b in hi if b["gated"]]
        print(f"    edge>={t:.2f}: {len(hi):>4} in universe, {len(g):>4} gated "
              f"({100*len(g)/max(len(hi),1):>5.1f}% already bet)")

    print("\n" + "=" * 100)
    print("  DECOMPOSITION -- universe (n={}), in-sample probs".format(len(U)))
    print("=" * 100)
    show_buckets(U, "p_yrfi", "MODEL TERM ONLY: quintiles of p_yrfi (ignore price)")
    show_buckets(U, "implied", "PRICE TERM ONLY: quintiles of implied prob "
                               "(low = longer price)")
    show_buckets(U, "edge", "EDGE: quintiles of edge (for comparison)")
    joint(U)

    print("\n" + "=" * 100)
    print("  DECOMPOSITION -- INSIDE THE LIVE BET SET (n={})".format(len(L)))
    print("=" * 100)
    show_buckets(L, "p_yrfi", "model term, within gate", nb=4)
    show_buckets(L, "implied", "price term, within gate", nb=4)
    show_buckets(L, "edge", "edge, within gate", nb=4)

    print("\n" + "=" * 100)
    print("  SAME, WALK-FORWARD CALIBRATOR")
    print("=" * 100)
    Uw = univ(rows, wf)
    Lw = build_bets(rows, wf)
    table(Uw, "universe, walk-forward")
    table(Lw, "live rule, walk-forward")
    show_buckets(Uw, "p_yrfi", "model term, universe, walk-forward")
    show_buckets(Uw, "implied", "price term, universe, walk-forward")
    return 0


if __name__ == "__main__":
    sys.exit(main())

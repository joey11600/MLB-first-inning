#!/usr/bin/env python3
"""Re-derive the second-book / price-shopping claim from picks_2026.csv.

Claim under test: on ~296 placed YRFI bets with real DK prices,
  +9.11u @ DK, +17.66u @ +5c, +26.21u @ +10c, +34.76u @ +15c
i.e. exactly +0.10u per bet per 10 cents.
"""
import csv, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "picks_2026.csv"


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def bump(o, cents):
    """Move an American price `cents` cents in the bettor's favour."""
    if cents == 0:
        return o
    # work in the continuous 'american line' space: negatives move toward 0
    # then cross to positive at +100/-100 boundary.
    if o < 0:
        v = o + cents          # -150 +10 -> -140 (better for bettor)
        if v > -100:           # crossed the boundary
            v = 100 + (v + 100)  # -100 == +100; -95 -> +105
        return v
    else:
        return o + cents       # +120 +10 -> +130


rows = list(csv.DictReader(open(CSV, newline="", encoding="utf-8")))
print(f"total rows {len(rows)}")


def subset(side, placed_only=True, strong_only=False):
    out = []
    for r in rows:
        if r.get("pick_side") != side:
            continue
        if placed_only and r.get("bet_placed") != "Y":
            continue
        if strong_only and r.get("pick_strength") != "STRONG":
            continue
        g = r.get("graded_result")
        if g not in ("WIN", "LOSS"):
            continue
        o = fnum(r.get(f"market_{side.lower()}_odds"))
        if o is None:
            continue
        out.append((r["date"], g == "WIN", o, r))
    return out


def pnl(sub, cents=0):
    tot = 0.0
    for _, win, o, _ in sub:
        oo = bump(o, cents)
        tot += payout(oo) if win else -1.0
    return tot


def day_block_boot(sub, cents=0, iters=20000, seed=7):
    days = defaultdict(list)
    for d, win, o, _ in sub:
        days[d].append(payout(bump(o, cents)) if win else -1.0)
    keys = list(days)
    arrs = [np.array(days[k]) for k in keys]
    rng = np.random.default_rng(seed)
    n = len(keys)
    tot_bets = sum(len(a) for a in arrs)
    outs = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, n, n)
        s = 0.0
        b = 0
        for j in idx:
            s += arrs[j].sum()
            b += len(arrs[j])
        outs[i] = s / b if b else 0.0   # ROI per bet
    return np.percentile(outs, [2.5, 50, 97.5]), tot_bets


for label, sub in [
    ("YRFI placed(Y) graded priced", subset("YRFI", True, False)),
    ("YRFI STRONG graded priced (any bet_placed)", subset("YRFI", False, True)),
    ("YRFI STRONG placed(Y)", subset("YRFI", True, True)),
    ("NRFI placed(Y) graded priced", subset("NRFI", True, False)),
]:
    n = len(sub)
    if not n:
        print(f"\n{label}: n=0")
        continue
    w = sum(1 for _, x, _, _ in sub if x)
    print(f"\n=== {label}  n={n}  W-L {w}-{n-w}  hit {w/n:.3%}")
    print("   median odds", st.median([o for _, _, o, _ in sub]))
    for c in (0, 5, 10, 15):
        p = pnl(sub, c)
        print(f"   +{c:>2}c : {p:+8.2f}u   ROI {p/n:+7.2%}")
    (lo, md, hi), nb = day_block_boot(sub, 0)
    print(f"   DK  day-block ROI 95% CI [{lo:+.2%}, {hi:+.2%}]  med {md:+.2%}  (n={nb})")
    (lo, md, hi), nb = day_block_boot(sub, 10)
    print(f"   +10c day-block ROI 95% CI [{lo:+.2%}, {hi:+.2%}]  med {md:+.2%}")
    (lo, md, hi), nb = day_block_boot(sub, -10)
    print(f"   -10c day-block ROI 95% CI [{lo:+.2%}, {hi:+.2%}]  med {md:+.2%}"
          f"   pnl {pnl(sub,-10):+.2f}u")

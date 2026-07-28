#!/usr/bin/env python3
"""
A5 -- what is a SECOND SPORTSBOOK actually worth?

Two models, both run on the real captured DK price distribution and the
real settled outcomes:

  (1) DETERMINISTIC: book #2 is always `c` cents better than DK.  This is
      the optimistic reading of "a second book is typically 10 cents better"
      and it is what a line-shopping pitch implicitly promises.

  (2) BEST-OF-TWO WITH NOISE: book #2's price is DK's price plus a normal
      draw with mean `mu` cents and sd `sd` cents; you always take the
      better of the two.  This is what line shopping really is -- two books
      that disagree game to game, and you skim the better half.  Even with
      mu = 0 you gain, because max(0, noise) > 0.

"Cents" are added on the PAYOUT ladder (a -110 at +10c becomes -100), which
is the conventional sportsbook meaning.

Read-only.
"""
from __future__ import annotations
import math
import random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
N = len(rows)


def be(pay):
    return 1.0 / (1.0 + pay)


def roi_at(sub, side, cents):
    tot = 0.0
    for r in sub:
        pay = r["pay_n" if side == "N" else "pay_y"] + cents / 100.0
        win = r["y"] if side == "N" else (1 - r["y"])
        tot += pay if win else -1.0
    return tot / len(sub)


def mean_be(sub, side, cents):
    return sum(be(r["pay_n" if side == "N" else "pay_y"] + cents / 100.0)
               for r in sub) / len(sub)


hit_n = sum(r["y"] for r in rows) / N
print(f"n={N} settled 2026 games with real DK prices on both sides")
print(f"realized NRFI hit rate {hit_n*100:.2f}%   realized YRFI hit rate {(1-hit_n)*100:.2f}%")
print()
print("=" * 112)
print("(1) DETERMINISTIC: book #2 is ALWAYS c cents better than DK")
print("=" * 112)
print(f"{'c':>5}{'NRFI need%':>12}{'wall pp':>10}{'wall closed':>13}"
      f"{'NRFI ROI%':>11}{'|':>3}{'YRFI need%':>12}{'YRFI ROI%':>11}")
base_wall = mean_be(rows, "N", 0) * 100 - hit_n * 100
for c in (0, 5, 10, 15, 20, 25, 30):
    need = mean_be(rows, "N", c) * 100
    wall = need - hit_n * 100
    closed = (base_wall - wall) / base_wall * 100
    print(f"{c:>5}{need:>12.2f}{wall:>10.2f}{closed:>12.1f}%"
          f"{roi_at(rows,'N',c)*100:>11.2f}{'|':>3}"
          f"{mean_be(rows,'Y',c)*100:>12.2f}{roi_at(rows,'Y',c)*100:>11.2f}")

# solve for the c that closes the NRFI wall
lo, hi = 0.0, 400.0
for _ in range(80):
    mid = (lo + hi) / 2
    if mean_be(rows, "N", mid) > hit_n:
        lo = mid
    else:
        hi = mid
print()
print(f"  --> cents needed to make NRFI break even at its realized 48.05% hit "
      f"rate: {lo:.0f} cents")
print(f"      (DK's mean NRFI price is {B.prob_to_am(sum(r['i_n'] for r in rows)/N):.0f}; "
      f"you would need roughly {B.prob_to_am(hit_n):+.0f})")

print()
print("=" * 112)
print("(2) BEST-OF-TWO WITH NOISE: book #2 = DK + Normal(mu, sd) cents, take the better")
print("=" * 112)
print("effective cents gained = E[max(0, X)] for X~N(mu,sd), simulated on the real slate")
print(f"{'mu':>5}{'sd':>5}{'eff cents':>12}{'NRFI ROI%':>11}{'wall closed':>13}"
       f"{'YRFI ROI%':>11}")
rnd = random.Random(11)
for mu, sd in [(0, 5), (0, 10), (0, 15), (5, 10), (10, 10), (10, 15), (15, 15)]:
    trials = 200
    eff = 0.0
    rn = 0.0
    ry = 0.0
    for _ in range(trials):
        for r in rows:
            x = rnd.gauss(mu, sd)
            g = max(0.0, x) / 100.0
            eff += g
            pn = r["pay_n"] + g
            py = r["pay_y"] + g
            rn += pn if r["y"] else -1.0
            ry += py if not r["y"] else -1.0
    tot = trials * N
    effc = eff / tot * 100
    # wall closed, using the deterministic curve at the effective cents
    wall = mean_be(rows, "N", effc) * 100 - hit_n * 100
    print(f"{mu:>5}{sd:>5}{effc:>12.2f}{rn/tot*100:>11.2f}"
          f"{(base_wall-wall)/base_wall*100:>12.1f}%{ry/tot*100:>11.2f}")

print()
print("=" * 112)
print("WHAT IT IS WORTH ON THE BETS YOU ACTUALLY PLACED")
print("=" * 112)
placed = [r for r in rows if r["bet"] == "Y"]
sy = [r for r in placed if r["side"] == "YRFI"]
sn = [r for r in placed if r["side"] == "NRFI"]
print(f"  bet_placed=Y with real both-side prices: n={len(placed)} "
      f"(YRFI {len(sy)}, NRFI {len(sn)})")
for label, sub, side in (("YRFI bets placed", sy, "Y"), ("NRFI bets placed", sn, "N")):
    if len(sub) < 10:
        print(f"  {label}: n={len(sub)} too few")
        continue
    print(f"  {label} (n={len(sub)}, flat 1u):")
    for c in (0, 5, 10, 15):
        u = sum((r["pay_n" if side == "N" else "pay_y"] + c / 100.0)
                if (r["y"] if side == "N" else 1 - r["y"]) else -1.0 for r in sub)
        print(f"      +{c:>2}c -> {u:+8.2f}u   (ROI {u/len(sub)*100:+6.2f}%)")
    d = len(sub) * 0.10
    print(f"      each 10 cents is worth exactly {d:+.2f}u over these "
          f"{len(sub)} bets (0.10u per bet, deterministic)")

print()
print("=" * 112)
print("STRONG-YRFI ONLY (the side that actually makes money)")
print("=" * 112)
st = [r for r in rows if r["strength"] == "STRONG" and r["side"] == "YRFI"]
if len(st) >= 20:
    print(f"  n={len(st)}  hit {sum(1-r['y'] for r in st)/len(st)*100:.2f}%")
    for c in (0, 5, 10, 15):
        u = sum((r["pay_y"] + c / 100.0) if not r["y"] else -1.0 for r in st)
        lo2, hi2 = B.day_boot(st, lambda rs, c=c: sum((x["pay_y"] + c/100.0) if not x["y"] else -1.0 for x in rs)/len(rs))
        print(f"      +{c:>2}c -> {u:+8.2f}u  ROI {u/len(st)*100:+6.2f}% "
              f"[{lo2*100:+6.2f},{hi2*100:+6.2f}]")

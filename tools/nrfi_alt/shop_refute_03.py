#!/usr/bin/env python3
"""Attack 3: how plausible is a ROUTINE +10c on the bet side?

(a) measure DK's two-way hold on the exact bets in question. A uniform +10c on
    the bet side means the 2nd book prices that side at/below no-vig fair on
    100% of bets.
(b) measure real price dispersion in this market using DK-open vs DK-at-bet
    (same book, two timestamps) -- the only dispersion data that exists.
(c) simulate an honest line shop: best-of-two books, second book drawn with
    that measured dispersion, gain = E[max(0, improvement)], not a flat +10c.
"""
import csv
from collections import Counter
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", newline="", encoding="utf-8")))


def fnum(v):
    try:
        return float(str(v).strip().replace("−", "-")) if v not in ("", None, "None") else None
    except ValueError:
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def to_cents(o):
    """map american odds onto a continuous 'cent' axis (no gap at +/-100)."""
    return o if o > 0 else o + 200.0 if o <= -100 else o   # -110 -> +90, +120 -> +120


def from_cents(c):
    return c if c >= 100 else c - 200.0


def bump(o, c):
    return from_cents(to_cents(o) + c)


sub = []
for r in rows:
    if r["pick_side"] != "YRFI" or r["bet_placed"] != "Y":
        continue
    if r["graded_result"] not in ("WIN", "LOSS"):
        continue
    y = fnum(r["market_yrfi_odds"]); n = fnum(r["market_nrfi_odds"])
    if y is None:
        continue
    sub.append(dict(date=r["date"], win=r["graded_result"] == "WIN", y=y, n=n,
                    oy=fnum(r["opened_yrfi_odds"]), on=fnum(r["opened_nrfi_odds"])))
print(f"n={len(sub)}")

# ---- (a) DK two-way hold on these games ------------------------------
holds = [implied(x["y"]) + implied(x["n"]) - 1.0 for x in sub if x["n"] is not None]
holds = np.array(holds)
print(f"\n(a) DK two-way hold, n={len(holds)}: "
      f"median {np.median(holds):.3%}  mean {holds.mean():.3%}  "
      f"p10 {np.percentile(holds,10):.3%}  p90 {np.percentile(holds,90):.3%}")
print(f"    -> no-vig fair YRFI price is worth about {np.median(holds)/2:.3%} "
      f"of probability better than DK's YRFI price")
# what is +10c worth in probability at these prices?
dp = [implied(x["y"]) - implied(bump(x["y"], 10)) for x in sub]
print(f"    a flat +10c is worth {np.median(dp):.3%} of implied probability (median)")
print(f"    ratio (+10c) / (half the DK hold) = {np.median(dp)/(np.median(holds)/2):.2f}x")
print("    i.e. a routine +10c requires the 2nd book to beat NO-VIG FAIR by "
      f"{np.median(dp)-np.median(holds)/2:+.3%} on every single bet.")

# ---- (b) real dispersion: DK open vs DK at bet ------------------------
mv = [to_cents(x["y"]) - to_cents(x["oy"]) for x in sub if x["oy"] is not None]
mv = np.array(mv)
same = (mv == 0).sum()
print(f"\n(b) DK open -> DK at-bet movement, n={len(mv)}: "
      f"{same}/{len(mv)} = {same/len(mv):.1%} identical")
print(f"    |move| median {np.median(np.abs(mv)):.1f}c  mean {np.abs(mv).mean():.1f}c  "
      f"p90 {np.percentile(np.abs(mv),90):.1f}c  max {np.abs(mv).max():.0f}c")
sigma = mv.std()
print(f"    sd of movement {sigma:.1f}c  -> a plausible cross-book sd")

# ---- (c) honest best-of-two shop --------------------------------------
def shop_gain(sigma_book, iters=4000, seed=3):
    rng = np.random.default_rng(seed)
    base = np.array([sum((payout(x["y"]) if x["win"] else -1.0) for x in sub)])
    tots = np.empty(iters)
    ys = np.array([to_cents(x["y"]) for x in sub])
    wins = np.array([x["win"] for x in sub])
    for i in range(iters):
        b2 = ys + rng.normal(0.0, sigma_book, len(ys))
        best = np.maximum(ys, b2)
        po = np.array([payout(from_cents(c)) for c in best])
        tots[i] = np.where(wins, po, -1.0).sum()
    return base[0], tots.mean(), np.percentile(tots, [2.5, 97.5])


print("\n(c) honest best-of-two shop (2nd book unbiased, sd = X cents):")
flat = sum((payout(x["y"]) if x["win"] else -1.0) for x in sub)
print(f"    DK only                       {flat:+7.2f}u")
for s in (5, 10, 15, 20, 30):
    b, m, ci = shop_gain(s)
    print(f"    best-of-2, book sd {s:2d}c      {m:+7.2f}u   gain {m-b:+6.2f}u "
          f"({(m-b)/len(sub):+.4f}u/bet)")
u10 = sum((payout(bump(x["y"], 10)) if x["win"] else -1.0) for x in sub)
print(f"    PROPOSAL's flat +10c on all   {u10:+7.2f}u   gain {u10-flat:+6.2f}u "
      f"({(u10-flat)/len(sub):+.4f}u/bet)")

# ---- (d) the same bump applied to the LOSING side ---------------------
nsub = [r for r in rows if r["pick_side"] == "NRFI" and r["bet_placed"] == "Y"
        and r["graded_result"] in ("WIN", "LOSS") and fnum(r["market_nrfi_odds"]) is not None]
f0 = sum((payout(fnum(r["market_nrfi_odds"])) if r["graded_result"] == "WIN" else -1.0) for r in nsub)
f10 = sum((payout(bump(fnum(r["market_nrfi_odds"]), 10)) if r["graded_result"] == "WIN" else -1.0) for r in nsub)
print(f"\n(d) SAME +10c on the NRFI bets (n={len(nsub)}): {f0:+.2f}u -> {f10:+.2f}u "
      f"gain {f10-f0:+.2f}u ({(f10-f0)/len(nsub):+.4f}u/bet)")
print("    the per-bet gain is a constant of the PRICE, not of the model side.")

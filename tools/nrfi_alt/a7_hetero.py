#!/usr/bin/env python3
"""
A7 -- (a) redo the timing question using opened_captured_at, which is the
only genuinely PRE-GAME timestamp in the ledger;
      (b) formally test whether the NRFI-side wall really varies by regime,
or whether every "soft regime" is just binomial noise.

The heterogeneity test: if the wall is truly constant at W across all
buckets, then bucket b's observed wall has variance p(1-p)/n_b.  Compare
the observed chi-square of the bucket walls against that null.

Read-only.
"""
from __future__ import annotations
import csv
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
byday = {}
for r in rows:
    byday.setdefault(r["date"], []).append(r)
for r in rows:
    r["slate"] = len(byday[r["date"]])

# ---- attach the opened-capture lead ---------------------------------------
opencap = {}
for r in csv.DictReader(open(B.PICKS, encoding="utf-8")):
    opencap[(r["date"], r["game_pk"], r["away_team"], r["home_team"])] = \
        r.get("opened_captured_at")
raw = list(csv.DictReader(open(B.PICKS, encoding="utf-8")))
idx = {}
for r in raw:
    idx[(r["date"], r["away_team"], r["home_team"], r.get("game_number", ""))] = r
for r in rows:
    src = None
    for k, v in idx.items():
        if k[0] == r["date"] and k[1] == r["away"] and k[2] == r["home"]:
            src = v
            break
    r["olead"] = None
    if src and src.get("opened_captured_at") and r["hour"] is not None:
        try:
            c = datetime.fromisoformat(src["opened_captured_at"].replace("Z", "+00:00"))
            d = datetime.strptime(r["date"], "%Y-%m-%d")
            g = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + \
                timedelta(hours=r["hour"] + 4)
            r["olead"] = (g - c).total_seconds() / 3600.0
        except Exception:
            pass

print("=" * 100)
print("(a) TIMING, done properly: opened_captured_at is the only pre-first-pitch "
      "timestamp we have")
print("=" * 100)
ol = sorted(r["olead"] for r in rows if r["olead"] is not None)
print(f"  n with an opened timestamp: {len(ol)}")
print(f"  earliest capture in the whole season: {ol[-1]:.2f} h before first pitch")
print(f"  median: {ol[len(ol)//2]:.2f} h    90th pct: {ol[int(.9*len(ol))]:.2f} h")
print("  --> the scraper NEVER sees an early market.  'Is DK soft when the")
print("      line first posts?' is UNTESTABLE with this ledger.")
print()
print(f"{'opened lead':<16}{'n':>6}{'take pp':>10}{'i_n%':>9}{'act%':>9}{'wall pp':>10}{'NRFI ROI%':>11}")
for lo, hi in [(0, .5), (.5, 1), (1, 1.1), (1.1, 2), (2, 99)]:
    sub = [r for r in rows if r["olead"] is not None and lo <= r["olead"] < hi]
    if len(sub) < 25:
        print(f"[{lo},{hi})".ljust(16) + f"{len(sub):>6}   (n<25)")
        continue
    n = len(sub)
    print(f"[{lo},{hi})".ljust(16) + f"{n:>6}"
          f"{sum(r['over'] for r in sub)/n*100:>10.3f}"
          f"{sum(r['i_n'] for r in sub)/n*100:>9.2f}"
          f"{sum(r['y'] for r in sub)/n*100:>9.2f}"
          f"{(sum(r['i_n'] for r in sub)/n - sum(r['y'] for r in sub)/n)*100:>10.2f}"
          f"{B.roi_nrfi(sub)*100:>11.2f}")

print()
print("=" * 100)
print("(b) IS THE WALL REALLY DIFFERENT ANYWHERE?  chi-square heterogeneity test")
print("=" * 100)
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SCHEMES = {
    "day of week": [(DOW[d], [r for r in rows if r["dow"] == d]) for d in range(7)],
    "first-pitch hour": B.buckets(rows, "hour", [14, 17, 19, 20, 22]),
    "park factor": B.buckets(rows, "park", [0.95, 1.00, 1.05]),
    "take quartile": B.buckets(rows, "over", [.0623, .0671, .0693]),
    "DK fair NRFI price": B.buckets(rows, "fair_n", [0.45, 0.48, 0.51, 0.54]),
    "slate size": B.buckets(rows, "slate", [8, 12, 15]),
    "month": [(f"m{m}", [r for r in rows if r["month"] == m]) for m in (5, 6, 7)],
}
print(f"{'partition':<24}{'k':>4}{'chi2':>9}{'df':>4}{'p':>9}   verdict")
for name, gs in SCHEMES.items():
    gs = [(l, s) for l, s in gs if len(s) >= 25]
    if len(gs) < 2:
        continue
    # null: wall is a constant; each bucket's actual rate ~ Binom(n, i_n_bar - W)
    tot_n = sum(len(s) for _, s in gs)
    W = (sum(sum(r["i_n"] for r in s) for _, s in gs)
         - sum(sum(r["y"] for r in s) for _, s in gs)) / tot_n
    chi = 0.0
    for _, s in gs:
        n = len(s)
        exp_p = sum(r["i_n"] for r in s) / n - W
        exp_p = min(max(exp_p, 1e-6), 1 - 1e-6)
        obs = sum(r["y"] for r in s)
        var = n * exp_p * (1 - exp_p)
        chi += (obs - n * exp_p) ** 2 / var
    df = len(gs) - 1
    # survival of chi2
    def chi2_sf(x, k):
        if k % 2 == 0:
            t = math.exp(-x / 2)
            s = t
            for i in range(1, k // 2):
                t *= x / (2 * i)
                s += t
            return min(1.0, s)
        z = math.sqrt(x)
        s = math.erfc(z / math.sqrt(2))
        t = math.sqrt(2 / math.pi) * z * math.exp(-x / 2)
        for i in range(1, (k - 1) // 2 + 1):
            s += t
            t *= x / (2 * i + 1)
        return min(1.0, s)
    p = chi2_sf(chi, df)
    v = "REAL heterogeneity" if p < 0.05 else "consistent with a CONSTANT wall"
    print(f"{name:<24}{len(gs):>4}{chi:>9.2f}{df:>4}{p:>9.3f}   {v}")

print()
print("  Note: 'day of week' etc are 7 pre-registered partitions, not a search.")
print("  A p<0.05 on one of seven would still be expected ~30% of the time.")

print()
print("=" * 100)
print("(c) ROBUSTNESS: headline numbers on GENUINELY PRE-GAME prices only")
print("=" * 100)
pre = [r for r in rows if r["lead_h"] is not None and r["lead_h"] > 0]
for label, sub in (("all captured prices", rows),
                   ("capture timestamp before first pitch", pre),
                   ("bet_placed=Y only (locked at bet time)",
                    [r for r in rows if r["bet"] == "Y"])):
    n = len(sub)
    a = sum(r["y"] for r in sub) / n
    print(f"  {label:<42} n={n:<5} take {sum(r['over'] for r in sub)/n*100:.2f}pp  "
          f"i_n {sum(r['i_n'] for r in sub)/n*100:.2f}%  act {a*100:.2f}%  "
          f"wall {(sum(r['i_n'] for r in sub)/n-a)*100:+.2f}pp")

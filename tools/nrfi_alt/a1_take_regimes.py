#!/usr/bin/env python3
"""
A1 -- where is DraftKings' TAKE smallest?

The take S-1 (overround) is what the book charges to make a two-way market.
If there are regimes where the take is materially smaller, those are the
cheapest places to bet -- for EITHER side.  Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
N = len(rows)
print(f"n={N} settled 2026 games, both DK sides captured, {len(set(r['date'] for r in rows))} days")
print(f"overall mean take: {sum(r['over'] for r in rows)/N*100:.3f}pp")
print()

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def show(title, groups, minn=25):
    print("=" * 104)
    print(title)
    print("-" * 104)
    print(f"{'bucket':<22}{'n':>6}{'take pp':>10}{'i_n%':>9}{'fair_n%':>9}"
          f"{'act NRFI%':>11}{'wall pp':>10}{'devig gap':>11}{'NRFI ROI%':>11}")
    for lab, sub in groups:
        n = len(sub)
        if n < minn:
            print(f"{lab:<22}{n:>6}   (skipped, n<{minn})")
            continue
        take = sum(r["over"] for r in sub) / n * 100
        i_n = sum(r["i_n"] for r in sub) / n * 100
        fair = sum(r["fair_n"] for r in sub) / n * 100
        act = sum(r["y"] for r in sub) / n * 100
        print(f"{lab:<22}{n:>6}{take:>10.3f}{i_n:>9.2f}{fair:>9.2f}"
              f"{act:>11.2f}{i_n-act:>10.2f}{fair-act:>11.2f}"
              f"{B.roi_nrfi(sub)*100:>11.2f}")
    print()


# --- 1. day of week
show("TAKE BY DAY OF WEEK",
     [(DOW[d], [r for r in rows if r["dow"] == d]) for d in range(7)])

# --- 2. month
show("TAKE BY MONTH",
     [(f"2026-{m:02d}", [r for r in rows if r["month"] == m]) for m in (4, 5, 6, 7)])

# --- 3. start hour (ET)
show("TAKE BY FIRST-PITCH HOUR (ET)",
     B.buckets(rows, "hour", [14, 17, 19, 20, 22]))

# --- 4. park factor
show("TAKE BY PARK FACTOR",
     B.buckets(rows, "park", [0.95, 1.00, 1.05]))

# --- 5. model lambda (proxy for the total; DK's actual game total is not
#        stored in the ledger, so this is the model's expected 1st-inning runs)
show("TAKE BY MODEL LAMBDA (total proxy)",
     B.buckets(rows, "lam", [0.85, 0.95, 1.05, 1.15]))

# --- 6. capture lead time
show("TAKE BY HOURS BEFORE FIRST PITCH AT CAPTURE",
     B.buckets(rows, "lead_h", [3, 5, 7, 10]))

# --- 7. price level: how big a favourite is NRFI
show("TAKE BY DK's OWN NRFI FAIR PRICE (favourite-ness)",
     B.buckets(rows, "fair_n", [0.45, 0.48, 0.51, 0.54]))

# --- 8. dome
show("TAKE BY DOME",
     [("dome", [r for r in rows if r["dome"]]),
      ("open air", [r for r in rows if not r["dome"]])])

# --- 9. slate size that day
byday = {}
for r in rows:
    byday.setdefault(r["date"], []).append(r)
for r in rows:
    r["slate"] = len(byday[r["date"]])
show("TAKE BY SLATE SIZE (games priced that day)",
     B.buckets(rows, "slate", [8, 12, 15]))

# --- 10. the cheapest-take games themselves, as their own bucket
srt = sorted(rows, key=lambda r: r["over"])
for frac in (0.10, 0.20, 0.33, 0.50):
    k = int(frac * N)
    sub = srt[:k]
    lo, hi = sub[0]["over"] * 100, sub[-1]["over"] * 100
    print(f"cheapest {frac:.0%} by take (n={k}, {lo:.2f}-{hi:.2f}pp): "
          f"NRFI act {sum(r['y'] for r in sub)/k*100:.2f}%  "
          f"i_n {sum(r['i_n'] for r in sub)/k*100:.2f}%  "
          f"NRFI ROI {B.roi_nrfi(sub)*100:+.2f}%  "
          f"YRFI ROI {B.roi_yrfi(sub)*100:+.2f}%")
sub = srt[int(0.5 * N):]
print(f"priciest 50% by take (n={len(sub)}): "
      f"NRFI ROI {B.roi_nrfi(sub)*100:+.2f}%  YRFI ROI {B.roi_yrfi(sub)*100:+.2f}%")

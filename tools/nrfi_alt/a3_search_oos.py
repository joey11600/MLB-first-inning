#!/usr/bin/env python3
"""
A3 -- search for a market-structure regime where DK's NRFI side is soft,
then confirm it OUT OF SAMPLE on a date range that was not searched.

The 2025/2024 backtests carry NO odds, so the ONLY out-of-sample split
available for any price-based claim is a temporal split inside 2026.
Search window : 2026-04-29 .. 2026-06-14
Holdout window: 2026-06-15 .. 2026-07-28

Every cell searched is counted and printed.  Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
CUT = "2026-06-15"
TRAIN = [r for r in rows if r["date"] < CUT]
TEST = [r for r in rows if r["date"] >= CUT]
print(f"search  n={len(TRAIN)}  days={len(set(r['date'] for r in TRAIN))}  "
      f"({min(r['date'] for r in TRAIN)}..{max(r['date'] for r in TRAIN)})")
print(f"holdout n={len(TEST)}  days={len(set(r['date'] for r in TEST))}  "
      f"({min(r['date'] for r in TEST)}..{max(r['date'] for r in TEST)})")
print(f"search  NRFI base {sum(r['y'] for r in TRAIN)/len(TRAIN)*100:.2f}%  "
      f"holdout NRFI base {sum(r['y'] for r in TEST)/len(TEST)*100:.2f}%")
print()

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---- build the candidate rule set (all market-structure only, no model vars
#      except the one lambda slice, flagged) --------------------------------
CANDS = []


def add(name, fn):
    CANDS.append((name, fn))


for d in range(7):
    add(f"dow={DOW[d]}", lambda r, d=d: r["dow"] == d)
for lo, hi in [(0, 14), (14, 17), (17, 19), (19, 20), (20, 22), (22, 99),
               (0, 17), (17, 99), (0, 19), (19, 99)]:
    add(f"hour[{lo},{hi})", lambda r, lo=lo, hi=hi: r["hour"] is not None and lo <= r["hour"] < hi)
for lo, hi in [(0, .95), (.95, 1), (1, 1.05), (1.05, 9), (0, 1), (1, 9)]:
    add(f"park[{lo},{hi})", lambda r, lo=lo, hi=hi: r["park"] is not None and lo <= r["park"] < hi)
for lo, hi in [(0, .058), (0, .062), (0, .065), (.065, .07), (.07, 9),
               (0, .0655), (.0655, 9)]:
    add(f"take[{lo},{hi})", lambda r, lo=lo, hi=hi: lo <= r["over"] < hi)
for lo, hi in [(0, .45), (.45, .48), (.48, .51), (.51, .54), (.54, 9),
               (0, .50), (.50, 9), (.45, .52)]:
    add(f"fair_n[{lo},{hi})", lambda r, lo=lo, hi=hi: lo <= r["fair_n"] < hi)
for lo, hi in [(0, .50), (.50, .53), (.53, .56), (.56, 9), (0, .53), (.53, 9)]:
    add(f"i_n[{lo},{hi})", lambda r, lo=lo, hi=hi: lo <= r["i_n"] < hi)
# price-pair shape: is DK's NRFI side the plus-money one?
add("nrfi is dog (odds>0)", lambda r: r["nrfi_odds"] > 0)
add("nrfi is fav (odds<0)", lambda r: r["nrfi_odds"] < 0)
add("both sides minus", lambda r: r["nrfi_odds"] < 0 and r["yrfi_odds"] < 0)
add("nrfi <= -130", lambda r: r["nrfi_odds"] <= -130)
add("nrfi >= -115", lambda r: r["nrfi_odds"] >= -115)
add("|i_n-i_y| < .02", lambda r: abs(r["i_n"] - r["i_y"]) < .02)
add("|i_n-i_y| >= .05", lambda r: abs(r["i_n"] - r["i_y"]) >= .05)
for s, e in [(4, 5), (5, 6), (6, 7), (7, 8)]:
    add(f"month={s}", lambda r, s=s: r["month"] == s)
for lo, hi in [(0, 12), (12, 15), (15, 99)]:
    add(f"slate[{lo},{hi})", lambda r, lo=lo, hi=hi: r.get("slate", 0) >= lo and r.get("slate", 0) < hi)
# one model-variable slice, flagged as retread territory
for lo, hi in [(0, .85), (.85, .95), (.95, 9)]:
    add(f"[MODEL]lam[{lo},{hi})", lambda r, lo=lo, hi=hi: r["lam"] is not None and lo <= r["lam"] < hi)

byday = {}
for r in rows:
    byday.setdefault(r["date"], []).append(r)
for r in rows:
    r["slate"] = len(byday[r["date"]])

# pairwise intersections of the singles, to be honest about the real
# size of the search space
PAIRS = []
for i in range(len(CANDS)):
    for j in range(i + 1, len(CANDS)):
        n1, f1 = CANDS[i]
        n2, f2 = CANDS[j]
        PAIRS.append((f"{n1} & {n2}", lambda r, f1=f1, f2=f2: f1(r) and f2(r)))

ALL = CANDS + PAIRS
print(f"CELLS SEARCHED: {len(CANDS)} single rules + {len(PAIRS)} pairwise "
      f"intersections = {len(ALL)} total")
print()

MINN = 60
res = []
for name, fn in ALL:
    sub = [r for r in TRAIN if fn(r)]
    if len(sub) < MINN:
        continue
    n = len(sub)
    wall = sum(r["i_n"] for r in sub) / n - sum(r["y"] for r in sub) / n
    res.append((wall, name, fn, n, B.roi_nrfi(sub) * 100))
res.sort()
print(f"survivors with n>={MINN} in the search window: {len(res)}")
print()
print("=" * 118)
print("TOP 15 SEARCH-WINDOW RULES BY SMALLEST NRFI WALL  ->  what they did on the HOLDOUT")
print("=" * 118)
print(f"{'rule':<52}{'trN':>5}{'trWall':>8}{'trROI':>8} | "
      f"{'teN':>5}{'teWall':>8}{'teROI':>8}{'teROI 95% CI':>22}")
for wall, name, fn, n, roi in res[:15]:
    sub = [r for r in TEST if fn(r)]
    if len(sub) < 15:
        print(f"{name:<52}{n:>5}{wall*100:>8.2f}{roi:>8.2f} | {len(sub):>5}   (too few)")
        continue
    tn = len(sub)
    tw = (sum(r["i_n"] for r in sub) / tn - sum(r["y"] for r in sub) / tn) * 100
    tr = B.roi_nrfi(sub) * 100
    lo, hi = B.day_boot(sub, B.roi_nrfi)
    print(f"{name:<52}{n:>5}{wall*100:>8.2f}{roi:>8.2f} | "
          f"{tn:>5}{tw:>8.2f}{tr:>8.2f}   [{lo*100:>7.2f},{hi*100:>7.2f}]")

print()
print("=" * 118)
print("SANITY: how many of the search-window rules would we EXPECT to look "
      "profitable by chance?")
print("=" * 118)
prof = [x for x in res if x[4] > 0]
print(f"  rules with positive NRFI ROI in the search window: {len(prof)} / {len(res)} "
      f"({len(prof)/len(res)*100:.1f}%)")
proft = 0
tot = 0
for wall, name, fn, n, roi in res:
    sub = [r for r in TEST if fn(r)]
    if len(sub) < 15:
        continue
    tot += 1
    if B.roi_nrfi(sub) > 0:
        proft += 1
print(f"  of ALL {tot} rules re-run on the holdout, {proft} were positive "
      f"({proft/tot*100:.1f}%) -- a coin flip would give ~50% if the rules "
      f"were break-even, and NRFI is not break-even")
carry = sum(1 for w, nm, fn, n, r in res if r > 0 and len([x for x in TEST if fn(x)]) >= 15
            and B.roi_nrfi([x for x in TEST if fn(x)]) > 0)
print(f"  rules positive in BOTH windows: {carry}")

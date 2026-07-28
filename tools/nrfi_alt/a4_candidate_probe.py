#!/usr/bin/env python3
"""
A4 -- probe the one rule family that carried across the split-half search:
DK's own de-vigged NRFI price sitting in [0.45, 0.48).

Also: what is the best take-reduction actually available by cherry-picking
the cheapest DK price pairs, and does that alone close any of the wall?

Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
byday = {}
for r in rows:
    byday.setdefault(r["date"], []).append(r)
for r in rows:
    r["slate"] = len(byday[r["date"]])


def rep(label, sub):
    if len(sub) < 10:
        print(f"{label:<44}n={len(sub):<5} (too few)")
        return
    n = len(sub)
    hit = sum(r["y"] for r in sub) / n
    be = sum(1 / (1 + r["pay_n"]) for r in sub) / n
    roi = B.roi_nrfi(sub)
    lo, hi = B.day_boot(sub, B.roi_nrfi)
    wlo, whi = B.wilson(sum(r["y"] for r in sub), n)
    print(f"{label:<44}n={n:<5} hit {hit*100:5.2f}% "
          f"[{wlo*100:5.2f},{whi*100:5.2f}]  need {be*100:5.2f}%  "
          f"ROI {roi*100:+7.2f}% [{lo*100:+7.2f},{hi*100:+7.2f}]  "
          f"units {sum(r['pay_n'] if r['y'] else -1.0 for r in sub):+7.2f}u")


print("=" * 124)
print("CANDIDATE: DK de-vigged NRFI price in [0.45, 0.48)  -- the only family "
      "that was positive in both halves")
print("=" * 124)
band = [r for r in rows if 0.45 <= r["fair_n"] < 0.48]
rep("full season, plain band", band)
rep("  search half (<06-15)", [r for r in band if r["date"] < "2026-06-15"])
rep("  holdout half (>=06-15)", [r for r in band if r["date"] >= "2026-06-15"])
print()
rep("band & hour>=17", [r for r in band if r["hour"] is not None and r["hour"] >= 17])
rep("  search half", [r for r in band if r["hour"] is not None and r["hour"] >= 17 and r["date"] < "2026-06-15"])
rep("  holdout half", [r for r in band if r["hour"] is not None and r["hour"] >= 17 and r["date"] >= "2026-06-15"])
print()
rep("band & slate>=15", [r for r in band if r["slate"] >= 15])
print()
print("month-by-month inside the plain band (stability check):")
for mth in (5, 6, 7):
    rep(f"  2026-{mth:02d}", [r for r in band if r["month"] == mth])
print()
print("neighbouring bands (is there a smooth signal or a lone lucky cell?):")
for lo, hi in [(.40, .43), (.43, .45), (.45, .48), (.48, .50), (.50, .52), (.52, .55), (.55, .60)]:
    rep(f"  fair_n[{lo},{hi})", [r for r in rows if lo <= r["fair_n"] < hi])

print()
print("=" * 124)
print("BEST AVAILABLE TAKE REDUCTION BY CHERRY-PICKING DK PRICE PAIRS")
print("=" * 124)
srt = sorted(rows, key=lambda r: r["over"])
N = len(rows)
base_vig = sum(r["vig_n"] for r in rows) / N * 100
print(f"  vig loaded on the NRFI side, all games: {base_vig:.2f}pp")
for frac in (.05, .10, .20, .33, .50):
    k = int(frac * N)
    sub = srt[:k]
    v = sum(r["vig_n"] for r in sub) / k * 100
    print(f"  cheapest {frac:>4.0%} of games (n={k:>4}): NRFI-side vig {v:.2f}pp "
          f"-> saves {base_vig - v:.2f}pp of the 5.64pp wall  "
          f"(realized NRFI ROI {B.roi_nrfi(sub)*100:+.2f}%)")
print()
print("  i.e. even betting ONLY the cheapest-priced 5% of the slate, the NRFI")
print("  side still carries ~2.8pp of vig and the wall is ~5.1pp.")

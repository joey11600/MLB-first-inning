#!/usr/bin/env python3
"""
A6 -- is DK softer at some point in the pricing cycle?

Checks (a) when we actually capture prices relative to first pitch, and
(b) whether the take at the OPENED capture differs from the take at the
final captured price.  Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
N = len(rows)

lead = sorted(r["lead_h"] for r in rows if r["lead_h"] is not None)
print(f"capture lead time before first pitch, n={len(lead)}")
for q in (0, 1, 5, 25, 50, 75, 95, 99, 100):
    print(f"  p{q:>3}: {lead[min(len(lead)-1,int(q/100*len(lead)))]:7.2f} h")
print(f"  captured AFTER first pitch: {sum(1 for x in lead if x<0)}")
print(f"  captured within 1h of first pitch: {sum(1 for x in lead if 0<=x<1)}")
print()

print("take by lead-time bucket (fine):")
print(f"{'bucket':<16}{'n':>6}{'take pp':>10}{'i_n%':>9}{'act%':>9}{'wall pp':>10}")
for lo, hi in [(-99, 0), (0, .5), (.5, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 99)]:
    sub = [r for r in rows if r["lead_h"] is not None and lo <= r["lead_h"] < hi]
    if len(sub) < 20:
        print(f"[{lo},{hi})".ljust(16) + f"{len(sub):>6}   (n<20)")
        continue
    n = len(sub)
    print(f"[{lo},{hi})".ljust(16) + f"{n:>6}"
          f"{sum(r['over'] for r in sub)/n*100:>10.3f}"
          f"{sum(r['i_n'] for r in sub)/n*100:>9.2f}"
          f"{sum(r['y'] for r in sub)/n*100:>9.2f}"
          f"{(sum(r['i_n'] for r in sub)/n - sum(r['y'] for r in sub)/n)*100:>10.2f}")
print()

# opened vs final
op = [r for r in rows if r["open_n"] is not None and r["open_y"] is not None]
print(f"rows with BOTH opened prices: {len(op)} / {N}")
same = sum(1 for r in op if r["open_n"] == r["nrfi_odds"] and r["open_y"] == r["yrfi_odds"])
print(f"  opened == final on both sides: {same} ({same/max(1,len(op))*100:.1f}%)")
mov = [r for r in op if not (r["open_n"] == r["nrfi_odds"] and r["open_y"] == r["yrfi_odds"])]
print(f"  actually moved: {len(mov)}")
if len(mov) >= 20:
    o_take = sum(B.implied(r["open_n"]) + B.implied(r["open_y"]) - 1 for r in mov) / len(mov)
    f_take = sum(r["over"] for r in mov) / len(mov)
    print(f"  take at OPEN  : {o_take*100:.3f}pp")
    print(f"  take at FINAL : {f_take*100:.3f}pp")
    o_in = sum(B.implied(r["open_n"]) for r in mov) / len(mov)
    f_in = sum(r["i_n"] for r in mov) / len(mov)
    a = sum(r["y"] for r in mov) / len(mov)
    print(f"  NRFI charged at OPEN {o_in*100:.2f}%  at FINAL {f_in*100:.2f}%  "
          f"actual {a*100:.2f}%")
    print(f"  NRFI wall betting the OPEN price : {(o_in-a)*100:+.2f}pp")
    print(f"  NRFI wall betting the FINAL price: {(f_in-a)*100:+.2f}pp")
    ro = sum((B.payout(r["open_n"]) if r["y"] else -1.0) for r in mov) / len(mov)
    rf = B.roi_nrfi(mov)
    print(f"  NRFI ROI at OPEN {ro*100:+.2f}%   at FINAL {rf*100:+.2f}%   "
          f"(n={len(mov)})")
    lo2, hi2 = B.day_boot(mov, lambda rs: sum((B.payout(x['open_n']) if x['y'] else -1.0) for x in rs)/len(rs) - B.roi_nrfi(rs))
    print(f"  open-minus-final NRFI ROI delta: "
          f"{(ro-rf)*100:+.2f}pp  [{lo2*100:+.2f},{hi2*100:+.2f}]")

print()
print("DATA GAP CHECK -- market variables NOT captured in the ledger:")
import csv
hdr = next(csv.reader(open(B.PICKS, encoding="utf-8")))
for want in ("total", "game_total", "run_line", "moneyline", "f5", "book_total"):
    hits = [h for h in hdr if want in h.lower()]
    print(f"  '{want}': {hits if hits else 'ABSENT'}")
print(f"  sportsbooks present: DraftKings only")

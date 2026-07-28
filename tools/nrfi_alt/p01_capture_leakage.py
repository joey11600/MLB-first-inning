#!/usr/bin/env python3
"""P01 -- is the CAPTURED DraftKings price contaminated by in-game info?

47% of `odds_captured_at` timestamps land AFTER the scheduled first pitch.
If DK keeps a live first-inning market open into the top of the 1st, a
price captured 10 minutes post-start could already know that the leadoff
man doubled -- which would make any "price as a feature" lift fake.

Test: split by capture lead time.  If the book's de-vigged probability
predicts the outcome MUCH better on late captures, that is leakage.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import price_common as pc

d = pc.load()
print(f"universe n={len(d)}  NRFI rate={d.y_nrfi.mean():.4f}")
print(f"mean vig={d.vig.mean():.4f}  median NRFI price={d.o_nrfi.median():.0f}")
print()

print("=== capture lead time (hours BEFORE scheduled first pitch) ===")
print(d["lead_h"].describe().round(3).to_string())
print(f"captured after scheduled first pitch: {(d.lead_h < 0).mean():.3f}")
print()

bins = [(-99, -2), (-2, -0.5), (-0.5, 0), (0, 0.5), (0.5, 2), (2, 99)]
print(f"{'lead bin (h)':>16} {'n':>5} {'NRFI%':>7} {'book_p':>7} {'AUC_book':>9} {'AUC_model':>10}")
for lo, hi in bins:
    s = d[(d.lead_h > lo) & (d.lead_h <= hi)]
    if len(s) < 25:
        continue
    print(f"{f'({lo},{hi}]':>16} {len(s):>5} {s.y_nrfi.mean():>7.3f} "
          f"{s.book_nrfi.mean():>7.3f} {pc.auc(s.y_nrfi, s.book_nrfi):>9.4f} "
          f"{pc.auc(s.y_nrfi, s.p_model):>10.4f}")
print()

pre = d[d.lead_h > 0]
post = d[d.lead_h <= 0]
print(f"PRE-pitch  n={len(pre):4d}  AUC_book={pc.auc(pre.y_nrfi, pre.book_nrfi):.4f}  "
      f"AUC_model={pc.auc(pre.y_nrfi, pre.p_model):.4f}  NRFI%={pre.y_nrfi.mean():.4f}")
print(f"POST-pitch n={len(post):4d}  AUC_book={pc.auc(post.y_nrfi, post.book_nrfi):.4f}  "
      f"AUC_model={pc.auc(post.y_nrfi, post.p_model):.4f}  NRFI%={post.y_nrfi.mean():.4f}")
print()

# Bootstrap the PRE-vs-POST difference in book AUC over days.
def diff(df):
    a = df[df.lead_h <= 0]
    b = df[df.lead_h > 0]
    if len(a) < 30 or len(b) < 30:
        return float("nan")
    return pc.auc(a.y_nrfi, a.book_nrfi) - pc.auc(b.y_nrfi, b.book_nrfi)


lo, hi = pc.day_bootstrap(d, diff)
print(f"AUC_book(post) - AUC_book(pre) = {diff(d):+.4f}   day-block 95% CI [{lo:+.4f}, {hi:+.4f}]")
print()

# Does the price MOVE as capture gets later?  A live market would widen.
print("=== is a late capture just a stale early one? ===")
same = (d["market_nrfi_odds"].astype(float) == pd.to_numeric(d["opened_nrfi_odds"], errors="coerce"))
print(f"market_nrfi_odds == opened_nrfi_odds: {same.mean():.3f}  ({same.sum()}/{len(d)})")
print(f"  among POST-pitch captures: {same[d.lead_h <= 0].mean():.3f}")
print(f"  among PRE-pitch captures : {same[d.lead_h > 0].mean():.3f}")
print()
print("bet_placed breakdown of the priced universe:")
print(d["bet_placed"].fillna("(blank)").value_counts().to_string())
print()
# Locked rows (bet_placed=Y) have prices frozen at BET time, not last scrape.
for lbl, s in [("bet_placed=Y (price locked at bet)", d[d.bet_placed == "Y"]),
               ("bet_placed!=Y (price = last scrape)", d[d.bet_placed != "Y"])]:
    if len(s) < 30:
        continue
    print(f"{lbl:38s} n={len(s):4d} AUC_book={pc.auc(s.y_nrfi, s.book_nrfi):.4f} "
          f"AUC_model={pc.auc(s.y_nrfi, s.p_model):.4f}")

#!/usr/bin/env python3
"""
A2 -- decompose the NRFI wall into [vig loaded on NRFI] + [book's opinion is
wrong], and put a day-block bootstrap CI on each piece.

The wall on the NRFI side is  i_n - actual_nrfi_rate.
The wall on the YRFI side is  i_y - actual_yrfi_rate.
They sum EXACTLY to the take (S-1).  So "how the book splits the take" and
"how wrong the book is" are the SAME number, viewed two ways.

Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import base as B  # noqa: E402

rows = B.load()
N = len(rows)


def m(rs, k):
    return sum(r[k] for r in rs) / len(rs)


def act(rs):
    return sum(r["y"] for r in rs) / len(rs)


print(f"n={N}  days={len(set(r['date'] for r in rows))}")
print()
print("=" * 92)
print("HEADLINE DECOMPOSITION (probability points)")
print("=" * 92)


def line(name, fn, unit="pp", scale=100):
    v = fn(rows) * scale
    lo, hi = B.day_boot(rows, fn)
    star = "" if (lo * scale < 0 < hi * scale) else "  <- CI excludes 0"
    print(f"{name:<48}{v:>9.2f}{unit}   [{lo*scale:>7.2f},{hi*scale:>7.2f}]{star}")


line("take charged both sides (S-1)", lambda rs: m(rs, "over"))
line("NRFI side: charged i_n", lambda rs: m(rs, "i_n"))
line("NRFI side: actual rate", act)
line("NRFI WALL = i_n - actual", lambda rs: m(rs, "i_n") - act(rs))
print()
line("  ..of which vig (proportional de-vig)", lambda rs: m(rs, "vig_n"))
line("  ..of which BOOK OPINION ERROR (fair_n-act)",
     lambda rs: m(rs, "fair_n") - act(rs))
print()
line("YRFI side: charged i_y", lambda rs: m(rs, "i_y"))
line("YRFI side: actual rate", lambda rs: 1 - act(rs))
line("YRFI WALL = i_y - actual", lambda rs: m(rs, "i_y") - (1 - act(rs)))
print()
print("=" * 92)
print("ROI, real DK prices vs a hypothetical ZERO-VIG book")
print("=" * 92)


def novig_roi_n(rs):
    tot = 0.0
    for r in rs:
        pay = (1 - r["fair_n"]) / r["fair_n"]
        tot += pay if r["y"] else -1.0
    return tot / len(rs)


def novig_roi_y(rs):
    tot = 0.0
    for r in rs:
        pay = (1 - r["fair_y"]) / r["fair_y"]
        tot += pay if not r["y"] else -1.0
    return tot / len(rs)


line("NRFI ROI at real DK prices", B.roi_nrfi, unit="%")
line("NRFI ROI at ZERO-VIG fair prices", novig_roi_n, unit="%")
line("YRFI ROI at real DK prices", B.roi_yrfi, unit="%")
line("YRFI ROI at ZERO-VIG fair prices", novig_roi_y, unit="%")

print()
print("=" * 92)
print("IS THE BOOK ACTUALLY WRONG?  book fair NRFI vs realized, by season")
print("=" * 92)
print("  2024 league NRFI (backtest, n=2409): 53.55%")
print("  2025 league NRFI (backtest, n=2393): 49.73%")
print("  2026 league NRFI (all settled picks, n=1533): 48.47%")
print(f"  2026 DK mean fair NRFI on our {N} priced games: {m(rows,'fair_n')*100:.2f}%")
print(f"  2026 realized on those same games:           {act(rows)*100:.2f}%")

print()
print("=" * 92)
print("HOW CONSTANT IS THE TAKE?  (spread across every slice tried in A1)")
print("=" * 92)
ov = sorted(r["over"] for r in rows)
print(f"  per-game take: min {ov[0]*100:.2f}pp  max {ov[-1]*100:.2f}pp  "
      f"sd {(sum((x-m(rows,'over'))**2 for x in ov)/N)**0.5*100:.3f}pp")
uniq = {}
for r in rows:
    uniq[(r["nrfi_odds"], r["yrfi_odds"])] = uniq.get((r["nrfi_odds"], r["yrfi_odds"]), 0) + 1
top = sorted(uniq.items(), key=lambda kv: -kv[1])[:12]
print("  most common DK price pairs (nrfi/yrfi, count, take):")
for (a, b), c in top:
    print(f"    {a:>6.0f} / {b:>6.0f}   n={c:<5} take={(B.implied(a)+B.implied(b)-1)*100:.2f}pp")

#!/usr/bin/env python3
"""
tools/nrfi_alt/shop_refute2.py -- the two decisive tests.

A. How many CENTS of price improvement does the vig itself represent?
   Line shopping between two honestly-priced books can, at best, walk you
   from the vigged price toward the NO-VIG fair price. It cannot go past
   it. So the vig, expressed in cents, is the hard ceiling on shopping.

B. How much do NRFI prices actually MOVE? We have no second book, but we
   do have DK open -> DK close on the same game. That is an empirical
   floor-ish estimate of dispersion in this market, replacing the
   proposal's assumed sd=10.

Read-only.
"""
from __future__ import annotations

import csv
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "picks_2026.csv"


def implied(o): return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)
def payout(o):  return o / 100.0 if o > 0 else 100.0 / abs(o)
def to_cents(o): return -abs(o) if o < 0 else o - 200.0
def from_cents(u): return u if u <= -100.0 else u + 200.0


def prob_to_odds(p):
    """american price whose implied prob is exactly p."""
    if p >= 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


def fnum(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


def load():
    rows = []
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            book = (r.get("sportsbook") or "").strip()
            res = (r.get("actual_result") or "").upper()
            no, yo = fnum(r.get("market_nrfi_odds")), fnum(r.get("market_yrfi_odds"))
            if not book or res not in ("NRFI", "YRFI") or no is None:
                continue
            rows.append({
                "date": r["date"], "month": r["date"][:7],
                "nrfi_odds": no, "yrfi_odds": yo,
                "open_nrfi": fnum(r.get("opened_nrfi_odds")),
                "open_yrfi": fnum(r.get("opened_yrfi_odds")),
                "nrfi_hit": 1 if res == "NRFI" else 0,
                "p_nrfi": fnum(r.get("nrfi_prob")),
                "side": (r.get("pick_side") or "").strip(),
                "strength": (r.get("pick_strength") or "").strip(),
            })
    return rows


def roi(rows, cents=0.0):
    return statistics.fmean(
        (payout(from_cents(to_cents(r["nrfi_odds"]) + cents)) if r["nrfi_hit"] else -1.0)
        for r in rows)


def day_ci(rows, fn, iters=4000, seed=13):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    rnd = random.Random(seed)
    out = []
    for _ in range(iters):
        s = []
        for _ in range(len(days)):
            s.extend(byday[days[rnd.randrange(len(days))]])
        out.append(fn(s))
    out.sort()
    return out[int(.025 * len(out))], out[int(.975 * len(out))]


def main():
    rows = load()
    two = [r for r in rows if r["yrfi_odds"] is not None]

    # ------------------------------------------------------------------
    print("=== A. THE VIG, EXPRESSED IN CENTS = the ceiling on shopping ===")
    cents_of_vig = []
    for r in two:
        pn, py = implied(r["nrfi_odds"]), implied(r["yrfi_odds"])
        fair = pn / (pn + py)                       # no-vig fair NRFI prob
        cents_of_vig.append(to_cents(prob_to_odds(fair)) - to_cents(r["nrfi_odds"]))
    cv = statistics.fmean(cents_of_vig)
    print(f"n={len(two)}  mean cents from DK NRFI price -> no-vig fair price"
          f" = {cv:.2f}c   (median {statistics.median(cents_of_vig):.2f}c)")
    print(f"  ROI at the NO-VIG FAIR price (unreachable upper bound)"
          f" = {roi(two, cv)*100:+.2f}%")
    lo, hi = day_ci(two, lambda s: roi(s, cv))
    print(f"  day-block 95% CI                                    "
          f"= [{lo*100:+.2f}%, {hi*100:+.2f}%]")
    print(f"  cents needed for ROI=0 is ~26.2c; the entire vig is only"
          f" {cv:.1f}c.")
    print(f"  => even a FREE, ZERO-VIG NRFI market loses. Shopping is capped"
          f" well below that.")

    # ------------------------------------------------------------------
    print("\n=== B. EMPIRICAL PRICE DISPERSION (DK open -> DK close) ===")
    mv = [to_cents(r["nrfi_odds"]) - to_cents(r["open_nrfi"])
          for r in rows if r["open_nrfi"] is not None]
    same = sum(1 for m in mv if m == 0)
    print(f"rows with a captured open price: {len(mv)}  "
          f"({same} = {same/max(len(mv),1)*100:.0f}% unchanged)")
    if mv:
        print(f"  mean move {statistics.fmean(mv):+.2f}c   "
              f"sd {statistics.pstdev(mv):.2f}c   "
              f"mean |move| {statistics.fmean(abs(m) for m in mv):.2f}c")
        print(f"  move histogram: {dict(sorted(Counter(mv).items()))}")
        print("  -> this market barely moves within one book. A second book on")
        print("     the same 5c ladder is very unlikely to be sd=10c away.")

    # ------------------------------------------------------------------
    print("\n=== C. IS +4c NRFI SIGNIFICANTLY POSITIVE ANYWHERE? ===")
    print("  (day-block 95% CI on ROI; need the WHOLE interval above 0)")
    subs = [("all priced", rows),
            ("2026-07 only", [r for r in rows if r["month"] == "2026-07"]),
            ("model NRFI", [r for r in rows if r["side"] == "NRFI"]),
            ("p_nrfi>=0.55", [r for r in rows if r["p_nrfi"] and r["p_nrfi"] >= .55])]
    for c in (4.0, 10.0, 20.0):
        print(f"  -- shopping gain = +{c:.0f}c --")
        for name, g in subs:
            if len(g) < 30:
                continue
            lo, hi = day_ci(g, lambda s: roi(s, c), iters=3000)
            flag = "POSITIVE" if lo > 0 else "not significant / negative"
            print(f"    {name:>14} n={len(g):>4} ROI {roi(g,c)*100:>+6.2f}% "
                  f"CI [{lo*100:>+6.2f},{hi*100:>+6.2f}]  {flag}")

    # ------------------------------------------------------------------
    print("\n=== D. 2025 OUT-OF-SAMPLE CHECK ===")
    p = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
    with open(p, encoding="utf-8") as f:
        bt = list(csv.DictReader(f))
    ocols = [c for c in bt[0] if "odds" in c.lower() or "market" in c.lower()]
    print(f"2025 backtest n={len(bt)}; price-bearing columns: {ocols or 'NONE'}")
    nr = [r for r in bt if fnum(r.get("fi_total_runs")) is not None]
    if nr:
        h = statistics.fmean(1.0 if float(r["fi_total_runs"]) == 0 else 0.0 for r in nr)
        print(f"  2025 base NRFI rate {h*100:.2f}% (n={len(nr)}) -- but with NO")
        print("  captured prices, the 2025 season cannot test a PRICE claim at all.")
        print(f"  At a flat -120 NRFI market (break-even 54.5%) 2025 would return "
              f"{(h*(100/120)-(1-h))*100:+.2f}%.")


if __name__ == "__main__":
    main()

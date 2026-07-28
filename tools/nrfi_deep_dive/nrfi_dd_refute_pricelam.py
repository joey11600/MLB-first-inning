#!/usr/bin/env python3
"""tools/nrfi_dd_refute_pricelam.py -- adversarial re-derivation of the
candidate rule:  bet NRFI iff lambda_lr_total <= 0.80 AND market_nrfi_odds >= -105.

Read-only. Touches no production config.

Sections
  1. Re-derive the headline numbers from the CSV columns directly.
  2. Marginal controls (price-only, lambda-only) + 2x2 interaction table.
  3. Day-block bootstrap CI on ROI.
  4. Multiple-comparisons: max-cell null distribution over the SAME 56-cell grid,
     by permuting outcomes within day (breaks any real edge, keeps day structure).
  5. Price robustness: shift every price 10 cents worse.
  6. Is the rule selecting games where DK is SHARP or SOFT? (price vs realised rate)
  7. Temporal split: first half of season -> second half.
"""
from __future__ import annotations
import csv, sys, math, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def worse(o, cents=10.0):
    """Shift an American price `cents` worse for the bettor."""
    if o > 0:
        return o - cents if o - cents > 100 else -(100 + (100 - (o - cents)))
    return o - cents


def load():
    out = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            o = fnum(r.get("market_nrfi_odds"))
            lam = fnum(r.get("lambda_lr_total"))
            if o is None or lam is None:
                continue
            out.append({"date": r["date"], "lam": lam, "odds": o,
                        "y": 1 if a == "NRFI" else 0,
                        "book": (r.get("sportsbook") or "").strip()})
    return out


def stats(sub):
    n = len(sub)
    if n == 0:
        return None
    hit = sum(r["y"] for r in sub) / n
    pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
    need = st.mean([implied(r["odds"]) for r in sub])
    return dict(n=n, hit=hit, pl=pl, roi=pl / n, need=need)


def day_boot(sub, iters=10000, seed=11, oddsfn=lambda o: o):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(payout(oddsfn(r["odds"])) if r["y"] else -1.0)
    days = list(byday.values())
    if len(days) < 3:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    k = len(days)
    out = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for j in idx for x in days[j]]
        out[i] = sum(v) / len(v)
    out.sort()
    return out[int(0.025 * iters)], out[int(0.975 * iters)], float((out <= 0).mean())


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, 100, 120]


def main():
    rows = load()
    print(f"priced+graded 2026 rows: n={len(rows)}   books={sorted({r['book'] for r in rows})}")
    print(f"overall NRFI rate {100*sum(r['y'] for r in rows)/len(rows):.1f}%  "
          f"mean implied {100*st.mean([implied(r['odds']) for r in rows]):.1f}%")

    # ---------------- 1. re-derive ----------------
    RULE = lambda r: r["lam"] <= 0.80 and r["odds"] >= -105
    sub = [r for r in rows if RULE(r)]
    s = stats(sub)
    print("\n== 1. THE RULE, re-derived from CSV columns ==")
    print(f"  n={s['n']}  hit={100*s['hit']:.1f}%  break-even={100*s['need']:.1f}%  "
          f"P/L={s['pl']:+.2f}u  ROI={100*s['roi']:+.1f}%")
    lo, hi, pneg = day_boot(sub)
    print(f"  day-block 95% CI on ROI: [{100*lo:+.1f}%, {100*hi:+.1f}%]   P(ROI<=0)={pneg:.3f}")
    print(f"  days spanned: {len({r['date'] for r in sub})}")

    # ---------------- 2. marginals + 2x2 ----------------
    print("\n== 2. MARGINAL CONTROLS + 2x2 interaction ==")
    for name, f in (("price only  (odds>=-105)", lambda r: r["odds"] >= -105),
                    ("lambda only (lam<=0.80)", lambda r: r["lam"] <= 0.80),
                    ("ALL priced games", lambda r: True)):
        t = stats([r for r in rows if f(r)])
        print(f"  {name:<26} n={t['n']:>4}  hit={100*t['hit']:.1f}%  "
              f"need={100*t['need']:.1f}%  ROI={100*t['roi']:+.1f}%")
    print("\n  2x2 cells (ROI% / n / hit% / need%):")
    for lg, lf in (("lam<=0.80", lambda r: r["lam"] <= 0.80),
                   ("lam >0.80", lambda r: r["lam"] > 0.80)):
        for pg, pf in (("odds>=-105", lambda r: r["odds"] >= -105),
                       ("odds <-105", lambda r: r["odds"] < -105)):
            t = stats([r for r in rows if lf(r) and pf(r)])
            print(f"    {lg} & {pg}: ROI={100*t['roi']:+6.1f}%  n={t['n']:>4}  "
                  f"hit={100*t['hit']:.1f}%  need={100*t['need']:.1f}%")

    # ---------------- 3. the grid, as searched ----------------
    print("\n== 3. THE 56-CELL GRID (cells with n>=20) ==")
    cells = []
    for c in CAPS:
        for pf in FLOORS:
            g = [r for r in rows if r["lam"] <= c and r["odds"] >= pf]
            if len(g) >= 20:
                cells.append((stats(g)["roi"], c, pf, len(g)))
    cells.sort(reverse=True)
    print(f"  evaluated cells with n>=20: {len(cells)} of {len(CAPS)*len(FLOORS)}")
    print(f"  positive-ROI cells: {sum(1 for c in cells if c[0] > 0)}")
    for roi, c, pf, n in cells[:6]:
        print(f"    lam<={c:<5} price>={pf:<+5} n={n:>4}  ROI={100*roi:+.1f}%")

    # ---------------- 4. max-cell null ----------------
    print("\n== 4. MULTIPLE COMPARISONS: null distribution of the BEST cell ==")
    print("  Permute NRFI/YRFI outcomes WITHIN each day (destroys any real edge,")
    print("  preserves day structure, price distribution and cell overlaps).")
    rng = np.random.default_rng(2026)
    byday = defaultdict(list)
    for i, r in enumerate(rows):
        byday[r["date"]].append(i)
    y = np.array([r["y"] for r in rows])
    lam = np.array([r["lam"] for r in rows])
    od = np.array([r["odds"] for r in rows])
    pay = np.array([payout(o) for o in od])
    masks = []
    for c in CAPS:
        for pf in FLOORS:
            m = (lam <= c) & (od >= pf)
            if m.sum() >= 20:
                masks.append(m)
    dayidx = [np.array(v) for v in byday.values()]

    def cellrois(yy):
        out = []
        for m in masks:
            v = np.where(yy[m] == 1, pay[m], -1.0)
            out.append(v.mean())
        return np.array(out)

    obs_best = cellrois(y).max()
    obs_rule = float(np.where(y[(lam <= 0.80) & (od >= -105)] == 1,
                              pay[(lam <= 0.80) & (od >= -105)], -1.0).mean())
    ITER = 3000
    best_null = np.empty(ITER)
    rule_null = np.empty(ITER)
    rm = (lam <= 0.80) & (od >= -105)
    for it in range(ITER):
        yy = y.copy()
        for idx in dayidx:
            yy[idx] = rng.permutation(y[idx])
        best_null[it] = cellrois(yy).max()
        rule_null[it] = np.where(yy[rm] == 1, pay[rm], -1.0).mean()
    print(f"  observed best-cell ROI          = {100*obs_best:+.1f}%")
    print(f"  null best-cell ROI  median      = {100*np.median(best_null):+.1f}%  "
          f"95th pct = {100*np.percentile(best_null,95):+.1f}%")
    print(f"  P(null best-cell >= observed)   = {(best_null >= obs_best).mean():.3f}   <-- "
          f"search-corrected p-value")
    print(f"  observed RULE ROI               = {100*obs_rule:+.1f}%")
    print(f"  P(null single-cell >= rule ROI) = {(rule_null >= obs_rule).mean():.3f}   "
          f"(uncorrected)")

    # ---------------- 5. price robustness ----------------
    print("\n== 5. PRICE ROBUSTNESS ==")
    for cents in (0, 5, 10, 15):
        t = sum(payout(worse(r["odds"], cents)) if r["y"] else -1.0 for r in sub)
        print(f"  prices {cents:>2} cents worse: P/L={t:+.2f}u  ROI={100*t/len(sub):+.1f}%")
    # how much of the edge is the +100/+105 tail?
    print("\n  by price bucket inside the rule:")
    for lo_, hi_ in ((-105, -105), (100, 105), (110, 9999)):
        g = [r for r in sub if lo_ <= r["odds"] <= hi_]
        if g:
            t = stats(g)
            print(f"    price {lo_:+d}..{hi_:+d}: n={t['n']:>3} hit={100*t['hit']:.1f}% "
                  f"need={100*t['need']:.1f}% ROI={100*t['roi']:+.1f}%")

    # ---------------- 6. sharp or soft? ----------------
    print("\n== 6. IS THE BOOK SHARP ON THESE GAMES? (calibration of DK price) ==")
    print("  For each NRFI price bucket over ALL priced games: implied vs realised.")
    buckets = [(-999, -140), (-140, -125), (-125, -115), (-115, -105),
               (-105, 100), (100, 110), (110, 999)]
    for a, b in buckets:
        g = [r for r in rows if a <= r["odds"] < b] if a != -999 else [r for r in rows if r["odds"] < b]
        if len(g) < 20:
            continue
        t = stats(g)
        print(f"    price [{a:+5},{b:+5}) n={t['n']:>4} implied={100*t['need']:.1f}% "
              f"realised={100*t['hit']:.1f}%  gap={100*(t['hit']-t['need']):+.1f}pp "
              f"ROI={100*t['roi']:+.1f}%")

    # ---------------- 7. temporal split ----------------
    print("\n== 7. TEMPORAL OUT-OF-SAMPLE (split the season in half by date) ==")
    dates = sorted({r["date"] for r in rows})
    mid = dates[len(dates) // 2]
    for name, f in (("first half  (<= " + mid + ")", lambda r: r["date"] <= mid),
                    ("second half (>  " + mid + ")", lambda r: r["date"] > mid)):
        g = [r for r in sub if f(r)]
        if not g:
            continue
        t = stats(g)
        print(f"  {name}: n={t['n']:>3} hit={100*t['hit']:.1f}% need={100*t['need']:.1f}% "
              f"ROI={100*t['roi']:+.1f}%")
    print("\n  month by month:")
    bym = defaultdict(list)
    for r in sub:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        t = stats(bym[m])
        print(f"    {m}: n={t['n']:>3} hit={100*t['hit']:.1f}% need={100*t['need']:.1f}% "
              f"ROI={100*t['roi']:+.1f}%  P/L={t['pl']:+.2f}u")
    return 0


if __name__ == "__main__":
    sys.exit(main())

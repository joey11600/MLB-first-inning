#!/usr/bin/env python3
"""tools/nrfi_dd_refute_pricelam2.py -- part 2 of the refutation.

  A. MARKET-CORRECT NULL. Null hypothesis = "the de-vigged DK price is the true
     probability" (i.e. exactly zero edge). Simulate outcomes from that, re-run
     the whole 56-cell search, and ask how often noise beats what we observed.
     This is the right null for a betting question -- unlike an outcome shuffle,
     it does NOT hand generous-price cells a free positive expectation.
  B. WALK-FORWARD ON THE PROCEDURE. At every date, pick the best cell of the
     56-grid using ONLY prior settled games, then bet it on that date. This
     scores the search procedure itself, which is what shipping the rule means.
  C. OPENED vs FINAL price -- was the selecting price available at post time?
  D. Sub-band robustness inside the rule (leave-one-price-out, leave-one-month-out).
"""
from __future__ import annotations
import csv, sys, statistics as st
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


def load():
    out = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            o = fnum(r.get("market_nrfi_odds"))
            yo = fnum(r.get("market_yrfi_odds"))
            lam = fnum(r.get("lambda_lr_total"))
            if o is None or lam is None:
                continue
            out.append({"date": r["date"], "lam": lam, "odds": o, "yodds": yo,
                        "opened": fnum(r.get("opened_nrfi_odds")),
                        "y": 1 if a == "NRFI" else 0})
    out.sort(key=lambda r: r["date"])
    return out


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, 100, 120]


def main():
    rows = load()
    n = len(rows)
    lam = np.array([r["lam"] for r in rows])
    od = np.array([r["odds"] for r in rows])
    y = np.array([r["y"] for r in rows])
    pay = np.array([payout(o) for o in od])

    # ---- de-vigged true prob under the null ----
    p_true = np.empty(n)
    nov = 0
    for i, r in enumerate(rows):
        pn = implied(r["odds"])
        if r["yodds"] is not None:
            py = implied(r["yodds"])
            p_true[i] = pn / (pn + py)
        else:
            nov += 1
            p_true[i] = pn / (pn + implied(-abs(r["odds"]) if r["odds"] > 0 else 100.0))
    print(f"n={n}   rows missing a YRFI price for de-vig: {nov}")
    print(f"mean de-vigged P(NRFI) = {p_true.mean():.4f}   actual NRFI rate = {y.mean():.4f}")
    print(f"mean vig-inclusive implied = {np.mean([implied(o) for o in od]):.4f}")

    masks = []
    labels = []
    for c in CAPS:
        for pf in FLOORS:
            m = (lam <= c) & (od >= pf)
            if m.sum() >= 20:
                masks.append(m)
                labels.append((c, pf))
    K = len(masks)
    rule_i = labels.index((0.80, -105))

    def rois(yy):
        return np.array([np.where(yy[m] == 1, pay[m], -1.0).mean() for m in masks])

    obs = rois(y)
    print(f"\n== A. MARKET-CORRECT NULL ({K} cells with n>=20) ==")
    print(f"  observed rule cell (lam<=0.80, price>=-105) ROI = {100*obs[rule_i]:+.1f}%")
    print(f"  observed best cell                          ROI = {100*obs.max():+.1f}% "
          f"(lam<={labels[int(obs.argmax())][0]}, price>={labels[int(obs.argmax())][1]:+d})")
    rng = np.random.default_rng(7)
    IT = 20000
    best_null = np.empty(IT)
    rule_null = np.empty(IT)
    npos = np.empty(IT)
    for it in range(IT):
        yy = (rng.random(n) < p_true).astype(int)
        v = rois(yy)
        best_null[it] = v.max()
        rule_null[it] = v[rule_i]
        npos[it] = (v > 0).sum()
    print(f"  NULL best-cell ROI: median {100*np.median(best_null):+.1f}%  "
          f"95th {100*np.percentile(best_null,95):+.1f}%")
    print(f"  P(null best-cell >= observed best-cell) = {(best_null >= obs.max()).mean():.4f}"
          f"   <-- search-corrected p")
    print(f"  P(null best-cell >= observed RULE ROI)  = {(best_null >= obs[rule_i]).mean():.4f}"
          f"   <-- p for the rule after correcting for the search that found it")
    print(f"  P(null RULE cell alone >= observed)     = {(rule_null >= obs[rule_i]).mean():.4f}"
          f"   <-- uncorrected p")
    print(f"  NULL count of positive-ROI cells: median {np.median(npos):.0f}  "
          f"observed {(obs>0).sum()}   (P(null>=obs)={ (npos >= (obs>0).sum()).mean():.3f})")

    # ---- B. walk-forward the PROCEDURE ----
    print("\n== B. WALK-FORWARD: pick the best grid cell from PRIOR data only, bet it today ==")
    dates = sorted({r["date"] for r in rows})
    for MIN_N in (60, 100, 200):
        placed = []
        for d in dates:
            hist = [r for r in rows if r["date"] < d]
            if len(hist) < MIN_N:
                continue
            best, bestroi = None, None
            for (c, pf) in labels:
                g = [r for r in hist if r["lam"] <= c and r["odds"] >= pf]
                if len(g) < 20:
                    continue
                roi = sum(payout(r["odds"]) if r["y"] else -1.0 for r in g) / len(g)
                if bestroi is None or roi > bestroi:
                    bestroi, best = roi, (c, pf)
            if best is None:
                continue
            c, pf = best
            for r in rows:
                if r["date"] == d and r["lam"] <= c and r["odds"] >= pf:
                    placed.append(r)
        if placed:
            pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in placed)
            hit = sum(r["y"] for r in placed) / len(placed)
            print(f"  burn-in {MIN_N:>3} games: n={len(placed):>4} hit={100*hit:.1f}% "
                  f"P/L={pl:+.2f}u ROI={100*pl/len(placed):+.1f}%")
        else:
            print(f"  burn-in {MIN_N}: no bets")

    # fixed-rule walk-forward: what if you had shipped THIS rule from day 1?
    sub = [r for r in rows if r["lam"] <= 0.80 and r["odds"] >= -105]
    print("\n  cumulative P/L of the FIXED rule, by month-end (is it a drift or a jump?):")
    cum = 0.0
    bym = defaultdict(float)
    for r in sub:
        cum += payout(r["odds"]) if r["y"] else -1.0
        bym[r["date"][:7]] = cum
    for m in sorted(bym):
        print(f"    through {m}: {bym[m]:+.2f}u")

    # ---- C. opened price ----
    print("\n== C. WOULD THE PRICE HAVE BEEN THERE AT POST TIME? (opened_nrfi_odds) ==")
    with_open = [r for r in rows if r["opened"] is not None]
    subo = [r for r in with_open if r["lam"] <= 0.80 and r["opened"] >= -105]
    pl = sum(payout(r["opened"]) if r["y"] else -1.0 for r in subo)
    print(f"  rule using OPENED price: n={len(subo)} hit={100*sum(r['y'] for r in subo)/len(subo):.1f}% "
          f"P/L={pl:+.2f}u ROI={100*pl/len(subo):+.1f}%")
    flip_in = [r for r in with_open if r["opened"] >= -105 and r["odds"] < -105 and r["lam"] <= 0.80]
    flip_out = [r for r in with_open if r["opened"] < -105 and r["odds"] >= -105 and r["lam"] <= 0.80]
    print(f"  games the final price lets in but the open did not: {len(flip_out)}   "
          f"and vice versa: {len(flip_in)}")

    # ---- D. leave-one-out robustness ----
    print("\n== D. LEAVE-ONE-OUT ROBUSTNESS OF THE RULE ==")
    print("  drop one month:")
    months = sorted({r["date"][:7] for r in sub})
    for m in months:
        g = [r for r in sub if r["date"][:7] != m]
        pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in g)
        print(f"    without {m}: n={len(g):>3} ROI={100*pl/len(g):+.1f}%")
    print("  drop one price point:")
    for p in sorted({r["odds"] for r in sub}):
        g = [r for r in sub if r["odds"] != p]
        pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in g)
        cnt = len(sub) - len(g)
        print(f"    without {p:+.0f} (n={cnt:>3}): n={len(g):>3} ROI={100*pl/len(g):+.1f}%")
    print("  drop one DAY (worst/best 5 days by contribution):")
    byday = defaultdict(float)
    for r in sub:
        byday[r["date"]] += payout(r["odds"]) if r["y"] else -1.0
    tot = sum(byday.values())
    srt = sorted(byday.items(), key=lambda kv: -kv[1])
    print(f"    total {tot:+.2f}u across {len(byday)} days")
    print(f"    top 3 days contribute {sum(v for _, v in srt[:3]):+.2f}u "
          f"({100*sum(v for _,v in srt[:3])/tot:.0f}% of total)")
    print(f"    top 5 days contribute {sum(v for _, v in srt[:5]):+.2f}u")
    return 0


if __name__ == "__main__":
    sys.exit(main())

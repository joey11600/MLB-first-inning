#!/usr/bin/env python3
"""tools/nrfi_dd_refute_lam060.py -- adversarial test of the candidate rule

    RULE: bet NRFI when  lambda_lr_total <= 0.60  AND  market_nrfi_odds >= -115

Claimed: 22 bets, 54.5% hit, break-even 51.3%, +6.1% ROI, best of a 56-cell grid.

This script tries to KILL it. Read-only; touches no production config.
"""
from __future__ import annotations
import csv, math, sys, random
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


def load_priced():
    rows = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            no = fnum(r.get("market_nrfi_odds"))
            yo = fnum(r.get("market_yrfi_odds"))
            lam = fnum(r.get("lambda_lr_total"))
            if no is None or lam is None:
                continue
            rows.append({
                "date": r["date"], "lam": lam, "odds": no, "yodds": yo,
                "hit": 1 if a == "NRFI" else 0,
                "p": fnum(r.get("nrfi_prob")),
                "opened": fnum(r.get("opened_nrfi_odds")),
                "park": fnum(r.get("park_factor")),
            })
    return rows


def cell_stats(sub, shift=0.0):
    """flat 1u. shift = cents of price degradation (positive = worse for us)."""
    if not sub:
        return None
    pl = 0.0
    imp = 0.0
    for r in sub:
        o = r["odds"]
        o2 = o - shift if o > 0 else o - shift  # more negative / less positive = worse
        # keep it on the right side of the -100/+100 discontinuity
        if -100 < o2 < 100:
            o2 = -100 - (100 - abs(o2)) if o2 >= 0 else o2
        pl += payout(o2) if r["hit"] else -1.0
        imp += implied(o2)
    n = len(sub)
    hits = sum(r["hit"] for r in sub)
    return {"n": n, "hits": hits, "hr": hits / n, "be": imp / n,
            "pl": pl, "roi": pl / n}


def day_boot_roi(sub, iters=20000, seed=7, shift=0.0):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(r)
    days = list(byday.values())
    if len(days) < 3:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    out = []
    k = len(days)
    for _ in range(iters):
        s = []
        for _ in range(k):
            s.extend(days[rng.randrange(k)])
        st = cell_stats(s, shift)
        out.append(st["roi"])
    out.sort()
    frac_pos = sum(1 for v in out if v > 0) / len(out)
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))], frac_pos


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PRICE_FLOORS = [-160, -140, -125, -115, -105, +100, +120]


def grid(rows, minn=20):
    cells = []
    for c in CAPS:
        for pf in PRICE_FLOORS:
            sub = [r for r in rows if r["lam"] <= c and r["odds"] >= pf]
            st = cell_stats(sub)
            if st is None:
                st = {"n": 0, "hits": 0, "hr": 0.0, "be": 0.0, "pl": 0.0, "roi": 0.0}
            cells.append((c, pf, st, sub))
    return cells


def main():
    rows = load_priced()
    print("=" * 92)
    print(f"UNIVERSE: 2026 graded games with a REAL captured DK NRFI price   n = {len(rows)}")
    print(f"          dates {min(r['date'] for r in rows)} .. {max(r['date'] for r in rows)}"
          f"   ({len(set(r['date'] for r in rows))} distinct slates)")
    base = cell_stats(rows)
    print(f"          ALL priced NRFI bets: {base['n']} bets, hit {100*base['hr']:.1f}%, "
          f"break-even {100*base['be']:.1f}%, ROI {100*base['roi']:+.1f}% ({base['pl']:+.2f}u)")
    print()

    # ---------- 1. re-derive the claimed cell ----------
    print("-" * 92)
    print("1. RE-DERIVING THE CLAIM  (lambda_lr_total <= 0.60 AND market_nrfi_odds >= -115)")
    sub = [r for r in rows if r["lam"] <= 0.60 and r["odds"] >= -115]
    st = cell_stats(sub)
    print(f"   n={st['n']}  hits={st['hits']}  hit%={100*st['hr']:.1f}  "
          f"break-even={100*st['be']:.1f}%  P&L={st['pl']:+.2f}u  ROI={100*st['roi']:+.1f}%")
    days = sorted(set(r["date"] for r in sub))
    print(f"   spread over {len(days)} distinct slates; "
          f"max bets on one slate = {max(sum(1 for r in sub if r['date']==d) for d in days)}")
    byday = defaultdict(float)
    for r in sub:
        byday[r["date"]] += payout(r["odds"]) if r["hit"] else -1.0
    top = sorted(byday.items(), key=lambda kv: -kv[1])[:3]
    print(f"   top-3 slates by P&L: {[(d, round(v,2)) for d,v in top]}  "
          f"= {sum(v for _,v in top):+.2f}u of {st['pl']:+.2f}u total")
    lo, hi, fp = day_boot_roi(sub)
    print(f"   day-block bootstrap 95% CI on ROI: [{100*lo:+.1f}%, {100*hi:+.1f}%]  "
          f"P(ROI>0) = {100*fp:.0f}%")
    print(f"   -> to detect a TRUE +6% ROI at 95%/80% power you need roughly "
          f"{int(round((1.96+0.84)**2 * 1.0 / 0.061**2))} bets. We have {st['n']}.")

    # ---------- 2. multiple comparisons ----------
    print()
    print("-" * 92)
    print("2. SEARCH EXPOSURE  --  how often does a 56-cell grid produce a winner this good")
    print("   under the NULL that the market price is exactly right (zero edge)?")
    cells = grid(rows)
    live = [(c, pf, st_, sub_) for c, pf, st_, sub_ in cells if st_["n"] >= 20]
    print(f"   grid = {len(CAPS)} lambda caps x {len(PRICE_FLOORS)} price floors = {len(cells)} cells; "
          f"{len(live)} have n>=20")
    obs_best = max(s["roi"] for _, _, s, _ in live)
    obs_rank = sorted(((s["roi"], c, pf, s["n"]) for c, pf, s, _ in live), reverse=True)
    print("   top cells actually measured:")
    for roi, c, pf, n in obs_rank[:6]:
        print(f"      lam<={c if c<9 else 'inf':<5}  price>={pf:+5d}   n={n:<5} ROI={100*roi:+.1f}%")

    # null sim: each game's NRFI outcome ~ Bernoulli(devigged implied prob)
    rng = np.random.default_rng(12345)
    pv = []
    for r in rows:
        if r["yodds"] is not None:
            a, b = implied(r["odds"]), implied(r["yodds"])
            pv.append(a / (a + b))
        else:
            pv.append(implied(r["odds"]) - 0.023)  # strip ~half a 4.6c hold
    pv = np.asarray(pv)
    lam = np.asarray([r["lam"] for r in rows])
    od = np.asarray([r["odds"] for r in rows])
    pay = np.asarray([payout(r["odds"]) for r in rows])
    masks = []
    for c in CAPS:
        for pf in PRICE_FLOORS:
            m = (lam <= c) & (od >= pf)
            if m.sum() >= 20:
                masks.append(m)
    ITERS = 20000
    maxes = np.empty(ITERS)
    cell_hits = 0
    target_mask = (lam <= 0.60) & (od >= -115)
    for i in range(ITERS):
        y = (rng.random(len(rows)) < pv).astype(float)
        unit = np.where(y > 0, pay, -1.0)
        best = -9.0
        for m in masks:
            v = unit[m].mean()
            if v > best:
                best = v
        maxes[i] = best
        if unit[target_mask].mean() >= st["roi"] - 1e-12:
            cell_hits += 1
    fam_p = float((maxes >= obs_best - 1e-12).mean())
    print(f"   NULL (market exactly right, 20k sims):")
    print(f"      P(best-of-{len(masks)} cells >= observed best {100*obs_best:+.1f}%) = {fam_p:.3f}"
          f"   <-- family-wise p-value")
    print(f"      median best-cell ROI under pure noise = {100*np.median(maxes):+.1f}%; "
          f"90th pct = {100*np.percentile(maxes,90):+.1f}%")
    print(f"      P(this ONE cell >= {100*st['roi']:+.1f}% by chance) = {cell_hits/ITERS:.3f}  "
          f"(uncorrected)")

    # ---------- 3. price robustness ----------
    print()
    print("-" * 92)
    print("3. PRICE ROBUSTNESS")
    for shift in (0, 5, 10, 15, 20):
        s2 = cell_stats(sub, shift)
        print(f"   prices {shift:>2}c worse:  ROI {100*s2['roi']:+6.1f}%  "
              f"P&L {s2['pl']:+6.2f}u  break-even {100*s2['be']:.1f}% vs hit {100*s2['hr']:.1f}%")
    # is the selection just "the cheapest prices"?
    print(f"   mean NRFI price in cell = {sum(r['odds'] for r in sub)/len(sub):+.1f}; "
          f"mean over all priced = {sum(r['odds'] for r in rows)/len(rows):+.1f}")
    # opened vs closed on the cell
    hasop = [r for r in sub if r["opened"] is not None]
    if hasop:
        moved = sum(1 for r in hasop if r["opened"] != r["odds"])
        print(f"   {moved}/{len(hasop)} of the cell's games have opened != captured price "
              f"(CLV is mostly unmeasurable here)")

    # ---------- 4. does the price leg do the work? ----------
    print()
    print("-" * 92)
    print("4. WHICH LEG IS LOAD-BEARING?")
    for label, s_ in (("lam<=0.60 only (any price)", [r for r in rows if r["lam"] <= 0.60]),
                      ("price>=-115 only (any lam)", [r for r in rows if r["odds"] >= -115]),
                      ("both (the rule)", sub),
                      ("lam<=0.60 AND price<-115", [r for r in rows if r["lam"] <= 0.60 and r["odds"] < -115])):
        s2 = cell_stats(s_)
        print(f"   {label:<30} n={s2['n']:<5} hit={100*s2['hr']:5.1f}%  "
              f"be={100*s2['be']:5.1f}%  ROI={100*s2['roi']:+6.1f}%")

    # ---------- 5. time holdout inside 2026 ----------
    print()
    print("-" * 92)
    print("5. TIME HOLDOUT INSIDE 2026 (the rule was found on the whole season)")
    for lo_d, hi_d, lab in (("0000", "2026-06-15", "Apr29-Jun14 (first half)"),
                            ("2026-06-15", "9999", "Jun15-Jul28 (second half)")):
        s_ = [r for r in sub if lo_d <= r["date"] < hi_d]
        if not s_:
            print(f"   {lab:<26} n=0")
            continue
        s2 = cell_stats(s_)
        print(f"   {lab:<26} n={s2['n']:<4} hit={100*s2['hr']:5.1f}%  "
              f"be={100*s2['be']:5.1f}%  ROI={100*s2['roi']:+6.1f}%  P&L={s2['pl']:+.2f}u")
    for mo in sorted(set(r["date"][:7] for r in sub)):
        s_ = [r for r in sub if r["date"][:7] == mo]
        s2 = cell_stats(s_)
        print(f"      {mo}: n={s2['n']:<4} hit={100*s2['hr']:5.1f}%  ROI={100*s2['roi']:+7.1f}%  "
              f"P&L={s2['pl']:+.2f}u")

    # ---------- 6. neighbourhood stability ----------
    print()
    print("-" * 92)
    print("6. NEIGHBOURHOOD STABILITY (a real effect should not vanish one notch away)")
    print(f"   {'lam cap':>8}" + "".join(f"{('>=' + str(p)):>13}" for p in PRICE_FLOORS))
    for c in CAPS:
        line = f"   {('inf' if c > 9 else f'{c:.2f}'):>8}"
        for pf in PRICE_FLOORS:
            s_ = [r for r in rows if r["lam"] <= c and r["odds"] >= pf]
            if len(s_) < 10:
                line += f"{'.':>13}"
            else:
                s2 = cell_stats(s_)
                line += f"{100*s2['roi']:>+8.1f}%/{s2['n']:<3}"
        print(line)


if __name__ == "__main__":
    main()

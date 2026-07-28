#!/usr/bin/env python3
"""Part B: reproduce the rule under the OTHER lambda definition (recomputed
from today's LR weights, as tools/nrfi_dd_pricegrid.py does), and re-run the
adversarial tests there.  Read-only."""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402


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
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            rows.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                         "y": 1 if a == "NRFI" else 0,
                         "o": fnum(r.get("market_nrfi_odds")),
                         "stored": fnum(r.get("lambda_lr_total"))})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def stats(sub):
    n = len(sub)
    if not n:
        return None
    h = sum(r["y"] for r in sub)
    pl = sum(payout(r["o"]) if r["y"] else -1.0 for r in sub)
    return {"n": n, "hits": h, "hit": h / n, "pl": pl, "roi": pl / n,
            "need": st.mean([implied(r["o"]) for r in sub])}


def day_boot(sub, iters=20000, seed=7):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(payout(r["o"]) if r["y"] else -1.0)
    days = list(byday.values())
    rng = np.random.default_rng(seed)
    k = len(days)
    out = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for j in idx for x in days[j]]
        out[i] = sum(v) / len(v)
    out.sort()
    return out[int(.025 * iters)], out[int(.975 * iters)], k, out


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, +100, +120]


def main():
    rows = load()
    priced = [r for r in rows if r["o"] is not None]
    both = [r for r in priced if r["stored"] is not None]
    a = np.asarray([r["lam"] for r in both])
    b = np.asarray([r["stored"] for r in both])
    print("=" * 96)
    print("  LAMBDA DEFINITION MATTERS")
    print("=" * 96)
    print(f"  n compared ................. {len(both)}")
    print(f"  corr(recomputed, stored) ... {np.corrcoef(a, b)[0,1]:.4f}")
    print(f"  mean abs difference ........ {np.abs(a-b).mean():.4f}")
    print(f"  rows with lam<=0.56 under recomputed: {(a<=0.56).sum()}")
    print(f"  rows with lam<=0.56 under stored ...: {(b<=0.56).sum()}")
    print(f"  rows in BOTH ........................ {((a<=0.56)&(b<=0.56)).sum()}")

    rule = [r for r in priced if r["lam"] <= 0.56 and r["o"] >= -125]
    geo = [r for r in priced if r["lam"] <= 0.56]
    print("\n" + "=" * 96)
    print("  RULE UNDER RECOMPUTED LAMBDA (this is what reproduces the claim)")
    print("=" * 96)
    print(f"  {'subset':<40}{'n':>5}{'hit%':>8}{'need%':>8}{'P/L u':>9}{'ROI%':>9}")
    for nm, s in (("RULE lam<=0.56 & price>=-125", rule),
                  ("geometry only lam<=0.56", geo),
                  ("all priced", priced)):
        x = stats(s)
        print(f"  {nm:<40}{x['n']:>5}{100*x['hit']:>8.1f}{100*x['need']:>8.1f}"
              f"{x['pl']:>+9.2f}{100*x['roi']:>+9.1f}")
    lo, hi, nd, dist = day_boot(rule)
    x = stats(rule)
    print(f"\n  record {x['hits']}W-{x['n']-x['hits']}L over {nd} distinct days")
    print(f"  ROI 95% day-block CI [{100*lo:+.1f}%, {100*hi:+.1f}%]")
    print(f"  P(ROI<=0) under resampling = {100*float((dist<=0).mean()):.1f}%")
    print(f"  edge over break-even = {100*(x['hit']-x['need']):+.1f}pp"
          f"  (needs {x['need']*x['n']:.1f} wins, got {x['hits']})")
    print(f"  ONE fewer win -> P/L {x['pl']-1-payout(-110):+.2f}u"
          f" ROI ~{100*(x['pl']-1-payout(-110))/x['n']:+.1f}%")

    # decomposition
    print("\n  Decomposition of the price filter (recomputed lambda):")
    print(f"  {'subset':<40}{'n':>5}{'hit%':>8}{'need%':>8}{'hit-need':>10}")
    for nm, s in (("lam<=0.56, price WORSE than -125", [r for r in geo if r["o"] < -125]),
                  ("lam<=0.56, price >= -125 (RULE)", rule),
                  ("lam>0.56,  price >= -125", [r for r in priced if r["lam"] > 0.56 and r["o"] >= -125])):
        x = stats(s)
        print(f"  {nm:<40}{x['n']:>5}{100*x['hit']:>8.1f}{100*x['need']:>8.1f}"
              f"{100*(x['hit']-x['need']):>+9.1f}pp")

    # null sim over the 56-cell grid
    lam = np.asarray([r["lam"] for r in priced])
    od = np.asarray([r["o"] for r in priced])
    q = np.asarray([implied(o) for o in od])
    pay = np.asarray([payout(o) for o in od])
    masks, meta = [], []
    for c in CAPS:
        for pf in FLOORS:
            m = (lam <= c) & (od >= pf)
            if m.sum() >= 20:
                masks.append(m)
                meta.append((c, pf, int(m.sum())))
    rulem = (lam <= 0.56) & (od >= -125)
    ITERS = 5000
    rng = np.random.default_rng(3)
    best = np.empty(ITERS)
    single = np.empty(ITERS)
    for i in range(ITERS):
        y = rng.random(len(q)) < q
        v = np.where(y, pay, -1.0)
        best[i] = max(v[m].mean() for m in masks)
        single[i] = v[rulem].mean()
    obs_rule = stats(rule)["roi"]
    real_cells = sorted(((np.where(np.asarray([r["y"] for r in priced], float) > 0, pay, -1.0)[m].mean(), *mt)
                         for m, mt in zip(masks, meta)), reverse=True)
    print("\n" + "=" * 96)
    print("  MULTIPLE-COMPARISONS / NULL SIMULATION (recomputed lambda)")
    print("=" * 96)
    print(f"  cells evaluated with n>=20: {len(masks)} of {len(CAPS)*len(FLOORS)}")
    print(f"  {'rank':>5}{'lam<=':>8}{'price>=':>10}{'n':>6}{'ROI%':>9}")
    for i, (roi, c, pf, n) in enumerate(real_cells[:6], 1):
        print(f"  {i:>5}{('inf' if c>9 else f'{c:.2f}'):>8}{pf:>+10d}{n:>6}{100*roi:>+9.1f}")
    print(f"\n  observed RULE cell ROI ......................... {100*obs_rule:+.1f}%")
    print(f"  observed BEST cell ROI in real data ............ {100*real_cells[0][0]:+.1f}%")
    print(f"  NULL (zero edge) best-of-{len(masks)}: median {100*np.median(best):+.1f}%,"
          f" 5th {100*np.percentile(best,5):+.1f}%, 95th {100*np.percentile(best,95):+.1f}%")
    print(f"  P(best-of-grid >= observed BEST | zero edge) .... {100*float((best>=real_cells[0][0]).mean()):.1f}%")
    print(f"  P(best-of-grid >= observed RULE | zero edge) .... {100*float((best>=obs_rule).mean()):.1f}%")
    print(f"  P(this ONE cell >= observed RULE | zero edge) ... {100*float((single>=obs_rule).mean()):.1f}%")

    # price degradation
    def worsen(o, c):
        return o - c
    print("\n" + "=" * 96)
    print("  PRICE ROBUSTNESS (recomputed lambda)")
    print("=" * 96)
    for c in (0, 5, 10):
        pl = sum(payout(worsen(r["o"], c)) if r["y"] else -1.0 for r in rule)
        sub2 = [r for r in priced if r["lam"] <= 0.56 and worsen(r["o"], c) >= -125]
        pl2 = sum(payout(worsen(r["o"], c)) if r["y"] else -1.0 for r in sub2)
        print(f"  -{c:>2}c: same bets ROI {100*pl/len(rule):+6.1f}%  |"
              f"  filter re-applied: n={len(sub2):>3} ROI {100*pl2/max(1,len(sub2)):+6.1f}%")

    # chronological split
    print("\n" + "=" * 96)
    print("  CHRONOLOGICAL SPLIT + MONTHLY (recomputed lambda)")
    print("=" * 96)
    bym = defaultdict(list)
    for r in rule:
        bym[r["date"][:7]].append(r)
    print(f"  {'month':<10}{'n':>5}{'W':>4}{'L':>4}{'hit%':>8}{'need%':>8}{'P/L u':>9}")
    for m in sorted(bym):
        x = stats(bym[m])
        print(f"  {m:<10}{x['n']:>5}{x['hits']:>4}{x['n']-x['hits']:>4}"
              f"{100*x['hit']:>8.1f}{100*x['need']:>8.1f}{x['pl']:>+9.2f}")

    # knob plateau
    print("\n  Knob sensitivity (ROI% (n)):")
    print(f"  {'lam<=':>8}" + "".join(f"{('>='+f'{p:+d}'):>13}" for p in (-140, -135, -130, -125, -120, -115)))
    for c in (0.50, 0.52, 0.54, 0.56, 0.58, 0.60):
        line = f"  {c:>8.2f}"
        for pf in (-140, -135, -130, -125, -120, -115):
            sub = [r for r in priced if r["lam"] <= c and r["o"] >= pf]
            line += (f"{'.':>13}" if len(sub) < 5
                     else f"{100*stats(sub)['roi']:+6.1f}%({len(sub)})".rjust(13))
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

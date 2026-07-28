#!/usr/bin/env python3
"""Part C: (i) show the rule is algebraically a MARKET-DISAGREEMENT filter,
which was already refuted on 505 games; (ii) honest walk-forward selection
of the best grid cell; (iii) capture-timing / availability checks.
Read-only."""
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
                         "oy": fnum(r.get("market_yrfi_odds")),
                         "op": fnum(r.get("opened_nrfi_odds")),
                         "cal": fnum(r.get("nrfi_prob"))})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return [r for r in rows if r["o"] is not None]


def stats(sub):
    n = len(sub)
    if not n:
        return None
    h = sum(r["y"] for r in sub)
    pl = sum(payout(r["o"]) if r["y"] else -1.0 for r in sub)
    return {"n": n, "hits": h, "hit": h/n, "pl": pl, "roi": pl/n,
            "need": st.mean([implied(r["o"]) for r in sub])}


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, +100, +120]


def main():
    priced = load()
    rule = [r for r in priced if r["lam"] <= 0.56 and r["o"] >= -125]

    print("=" * 96)
    print("  1. WHAT THE RULE ACTUALLY IS")
    print("=" * 96)
    print("  lambda = -ln(raw p_nrfi), so  lambda <= 0.56  <=>  raw p_nrfi >= exp(-0.56)")
    print(f"        exp(-0.56)     = {math.exp(-0.56):.4f}   (model says NRFI at least {100*math.exp(-0.56):.1f}%)")
    print(f"        implied(-125)  = {implied(-125):.4f}   (book says NRFI at most  {100*implied(-125):.1f}%)")
    print("  => EVERY bet the rule takes has model_p - book_p >= "
          f"{100*(math.exp(-0.56)-implied(-125)):+.1f}pp by construction.")
    print("  The rule is a MARKET-DISAGREEMENT filter written in other coordinates.")
    dis = [100*(r["raw"] - implied(r["o"])) for r in rule]
    print(f"  observed disagreement in the 21 bets: min {min(dis):+.1f}pp,"
          f" median {st.median(dis):+.1f}pp, max {max(dis):+.1f}pp")
    print("\n  Disagreement buckets over ALL priced NRFI games (the already-refuted test,")
    print("  re-run here on the current n so the equivalence is visible):")
    print(f"  {'model-book (pp)':<20}{'n':>6}{'hit%':>8}{'need%':>8}{'ROI%':>9}")
    for lo, hi in ((-99, 0), (0, 2), (2, 5), (5, 10), (10, 99)):
        sub = [r for r in priced if lo <= 100*(r["raw"]-implied(r["o"])) < hi]
        if len(sub) < 15:
            continue
        x = stats(sub)
        print(f"  [{lo:>+4},{hi:>+4})".ljust(20) + f"{x['n']:>6}{100*x['hit']:>8.1f}"
              f"{100*x['need']:>8.1f}{100*x['roi']:>+9.1f}")

    print("\n" + "=" * 96)
    print("  2. HONEST WALK-FORWARD: pick the best cell from PAST data only, bet it forward")
    print("=" * 96)
    ds = sorted({r["date"] for r in priced})
    results = []
    for burn in (30, 40, 50):
        if burn >= len(ds):
            continue
        bets = []
        picks = defaultdict(int)
        for i in range(burn, len(ds)):
            hist = [r for r in priced if r["date"] < ds[i]]
            best, bc = None, None
            for c in CAPS:
                for pf in FLOORS:
                    sub = [r for r in hist if r["lam"] <= c and r["o"] >= pf]
                    if len(sub) < 20:
                        continue
                    roi = stats(sub)["roi"]
                    if best is None or roi > best:
                        best, bc = roi, (c, pf)
            if bc is None:
                continue
            picks[bc] += 1
            for r in priced:
                if r["date"] == ds[i] and r["lam"] <= bc[0] and r["o"] >= bc[1]:
                    bets.append(r)
        if bets:
            x = stats(bets)
            top = sorted(picks.items(), key=lambda kv: -kv[1])[:3]
            print(f"  burn-in {burn} days -> {x['n']:>3} bets, {x['hits']}W-{x['n']-x['hits']}L,"
                  f" hit {100*x['hit']:.1f}% vs need {100*x['need']:.1f}%,"
                  f" P/L {x['pl']:+.2f}u, ROI {100*x['roi']:+.1f}%")
            print(f"      cell chosen most often: "
                  + ", ".join(f"lam<={c if c<9 else 'inf'}/{pf:+d} x{n}" for (c, pf), n in top))
            results.append(x)
    print("\n  Also: how often is lam<=0.56 & price>=-125 even the winner in-history?")

    print("\n" + "=" * 96)
    print("  3. WOULD YOU GET THAT PRICE?  capture timing / line movement")
    print("=" * 96)
    withopen = [r for r in rule if r["op"] is not None]
    same = sum(1 for r in withopen if abs(r["op"] - r["o"]) < 1e-9)
    print(f"  rule bets with an opened_nrfi_odds recorded: {len(withopen)}/{len(rule)}")
    print(f"  of those, opened == captured (no movement observed): {same}")
    moved = [r for r in withopen if abs(r["op"] - r["o"]) > 1e-9]
    if moved:
        print(f"  {'date':<12}{'open':>7}{'final':>7}{'move':>7}{'y':>3}")
        for r in moved:
            print(f"  {r['date']:<12}{r['op']:>+7.0f}{r['o']:>+7.0f}{r['o']-r['op']:>+7.0f}{r['y']:>3}")
    print("\n  Vig check on the rule's games (is DK sharp or generous here?):")
    v = [implied(r["o"]) + implied(r["oy"]) - 1 for r in rule if r["oy"] is not None]
    va = [implied(r["o"]) + implied(r["oy"]) - 1 for r in priced if r["oy"] is not None]
    if v:
        print(f"  mean 2-way overround, rule games : {100*st.mean(v):.2f}%  (n={len(v)})")
        print(f"  mean 2-way overround, all games  : {100*st.mean(va):.2f}%  (n={len(va)})")

    print("\n" + "=" * 96)
    print("  4. THE SAME RULE'S GEOMETRY, SCORED ON 2024 AND 2025 (accuracy only, no odds)")
    print("=" * 96)
    BT = ROOT / "data" / "backtests"
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    for label, path in (("2024", BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"),
                        ("2025", BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv")):
        rows = []
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = (r.get("actual_side") or "").upper()
                if a not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get("home", ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                rows.append({"t1": tv, "b1": bv, "y": 1 if a == "NRFI" else 0})
        Xt = np.asarray([r["t1"] for r in rows], float)
        Xb = np.asarray([r["b1"] for r in rows], float)
        for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
            r["lam"] = -math.log(max(1e-12, float(p)))
        base = sum(r["y"] for r in rows)/len(rows)
        sub = [r for r in rows if r["lam"] <= 0.56]
        h = sum(r["y"] for r in sub)/len(sub)
        print(f"  {label}: base {100*base:.1f}%   lam<=0.56 -> {len(sub):>4} games,"
              f" {100*h:.1f}% NRFI  (lift {100*(h-base):+.1f}pp)")
        print(f"        break-even at -125 is 55.6%; at the rule's mean price 53.6%."
              f"  {label} band {'CLEARS' if h>0.556 else 'MISSES'} the -125 line.")
    print("\n  NOTE: LR weights were fit on 2024+2025+2026YTD, so these lifts are")
    print("  IN-SAMPLE and optimistic.  They bound accuracy; they cannot prove profit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""tools/nrfi_dd_refute2.py -- second half of the audit of

    RULE: lambda <= 0.60 AND market_nrfi_odds >= -115

  A. Does the price filter raise HIT RATE, or does it only lower BREAK-EVEN?
  B. Honest within-2026 split: pick the best cell on games BEFORE a cutoff,
     bet it blind AFTER the cutoff.
  C. Disagreement-magnitude sort on the whole priced population (does the
     already-refuted filter show any trend at all in this data?).
Read-only.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

from importlib import import_module
_m = import_module("tools.nrfi_dd_refute") if False else None


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
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            side = (r.get("actual_result") or "").upper()
            if side not in ("NRFI", "YRFI"):
                continue
            o = fnum(r.get("market_nrfi_odds"))
            if o is None:
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            rows.append({"date": r.get("date", ""), "y": 1 if side == "NRFI" else 0,
                         "odds": o, "yodds": fnum(r.get("market_yrfi_odds")),
                         "t1": tv, "b1": bv})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    rows.sort(key=lambda r: r["date"])
    return rows


def roi(sub):
    if not sub:
        return float("nan"), 0.0
    pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
    return pl / len(sub), pl


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PRICE_FLOORS = [-160, -140, -125, -115, -105, +100, +120]


def main():
    priced = load_priced()
    band = [r for r in priced if r["lam"] <= 0.60]

    print("=" * 100)
    print("  A. IS IT HIT RATE, OR IS IT JUST THE BREAK-EVEN FALLING?")
    print("=" * 100)
    print("  Within the lam<=0.60 population, split by DK NRFI price band.")
    print("  If the price filter finds genuinely better games, hit% must RISE.")
    print("  If it only lowers the bar, hit% stays flat while need% falls.\n")
    edges = [(-9999, -160), (-160, -140), (-140, -125), (-125, -115), (-115, 9999)]
    hits, needs, ns = [], [], []
    print(f"  {'price band':<16}{'n':>5}{'hit%':>8}{'need%':>8}{'edge pp':>10}")
    for lo, hi in edges:
        sub = [r for r in band if lo < r["odds"] <= hi] if hi != 9999 else \
              [r for r in band if r["odds"] > lo]
        if not sub:
            continue
        h = sum(r["y"] for r in sub) / len(sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        hits.append(h); needs.append(nd); ns.append(len(sub))
        lbl = f"{lo:+d}..{hi:+d}" if hi != 9999 else f"> {lo:+d}"
        print(f"  {lbl:<16}{len(sub):>5}{100*h:>8.1f}{100*nd:>8.1f}{100*(h-nd):>+10.1f}")
    rk = lambda v: np.argsort(np.argsort(v))
    idx = list(range(len(hits)))
    r_hit = np.corrcoef(rk(idx), rk(hits))[0, 1]
    r_need = np.corrcoef(rk(idx), rk(needs))[0, 1]
    r_edge = np.corrcoef(rk(idx), rk([h - n for h, n in zip(hits, needs)]))[0, 1]
    print(f"\n  spearman(band, hit%)  = {r_hit:+.2f}   <- the only one that means anything")
    print(f"  spearman(band, need%) = {r_need:+.2f}   <- mechanical: price band IS need%")
    print(f"  spearman(band, edge)  = {r_edge:+.2f}   <- inherits the mechanical term")
    print(f"\n  hit% range across bands: {100*min(hits):.1f}% .. {100*max(hits):.1f}%  "
          f"(n per band: {ns})")
    # chi-square style: is hit rate independent of price band?
    tot_h = sum(h * n for h, n in zip(hits, ns))
    tot_n = sum(ns)
    p0 = tot_h / tot_n
    chi = sum((h * n - p0 * n) ** 2 / (p0 * (1 - p0) * n) for h, n in zip(hits, ns))
    print(f"  pooled hit% = {100*p0:.1f}%;  chi2({len(ns)-1}) for 'hit rate independent "
          f"of price band' = {chi:.2f}")
    print("  (critical value at 5% for 4 df = 9.49)")

    print("\n" + "=" * 100)
    print("  B. HONEST WITHIN-2026 SPLIT -- fit the grid BEFORE a cutoff, bet AFTER it")
    print("=" * 100)
    for cutoff in ("2026-06-15", "2026-06-30", "2026-07-01"):
        tr = [r for r in priced if r["date"] < cutoff]
        te = [r for r in priced if r["date"] >= cutoff]
        best, bcell = -9, None
        for c in CAPS:
            for pf in PRICE_FLOORS:
                s = [r for r in tr if r["lam"] <= c and r["odds"] >= pf]
                if len(s) < 20:
                    continue
                rr, _ = roi(s)
                if rr > best:
                    best, bcell = rr, (c, pf)
        if bcell is None:
            print(f"  cutoff {cutoff}: no cell with n>=20 in train")
            continue
        c, pf = bcell
        s = [r for r in te if r["lam"] <= c and r["odds"] >= pf]
        rr, pl = roi(s)
        hit = sum(r["y"] for r in s) / len(s) if s else float("nan")
        print(f"  cutoff {cutoff}: train n={len(tr):>4} picks lam<={c:.2f} & >={pf:+d} "
              f"(train ROI {100*best:+.1f}%)")
        print(f"      -> TEST  n={len(s):>4}  hit={100*hit:5.1f}%  P/L={pl:+6.2f}u  "
              f"ROI={100*rr:+6.1f}%")
        # and the proposed rule itself, on the test half only
        s2 = [r for r in te if r["lam"] <= 0.60 and r["odds"] >= -115]
        if s2:
            rr2, pl2 = roi(s2)
            print(f"      the PROPOSED rule on the same test half: n={len(s2)}  "
                  f"P/L={pl2:+.2f}u  ROI={100*rr2:+.1f}%")

    print("\n" + "=" * 100)
    print("  C. DISAGREEMENT MAGNITUDE ON THE WHOLE PRICED POPULATION (n=%d)" % len(priced))
    print("=" * 100)
    print("  The rule is 'model bullish NRFI + book bearish NRFI'. If that carries")
    print("  signal, ROI must improve as the disagreement gap widens.\n")
    for r in priced:
        a, b = implied(r["odds"]), implied(r["yodds"]) if r["yodds"] is not None else None
        r["dv"] = a / (a + b) if b else None
        r["gap"] = (r["raw"] - r["dv"]) if r["dv"] is not None else None
    g = [r for r in priced if r["gap"] is not None]
    print(f"  {'gap bucket':<16}{'n':>6}{'hit%':>8}{'need%':>8}{'ROI%':>9}")
    for lo, hi in [(-1, -0.05), (-0.05, 0.0), (0.0, 0.05), (0.05, 0.10),
                   (0.10, 0.15), (0.15, 1.0)]:
        s = [r for r in g if lo <= r["gap"] < hi]
        if len(s) < 15:
            continue
        hit = sum(r["y"] for r in s) / len(s)
        nd = st.mean([implied(r["odds"]) for r in s])
        rr, _ = roi(s)
        print(f"  {f'{100*lo:+.0f}..{100*hi:+.0f}pp':<16}{len(s):>6}{100*hit:>8.1f}"
              f"{100*nd:>8.1f}{100*rr:>+9.1f}")
    s = [r for r in g if r["gap"] >= 0.05]
    rr, pl = roi(s)
    hit = sum(r["y"] for r in s) / len(s)
    print(f"\n  the rule's own regime (gap >= +5pp, ANY lambda/price): n={len(s)}  "
          f"hit={100*hit:.1f}%  P/L={pl:+.2f}u  ROI={100*rr:+.1f}%")
    s = [r for r in g if r["gap"] >= 0.05 and r["odds"] >= -115]
    rr, pl = roi(s)
    hit = sum(r["y"] for r in s) / len(s)
    print(f"  gap >= +5pp AND price >= -115 (drop the lambda cap): n={len(s)}  "
          f"hit={100*hit:.1f}%  P/L={pl:+.2f}u  ROI={100*rr:+.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

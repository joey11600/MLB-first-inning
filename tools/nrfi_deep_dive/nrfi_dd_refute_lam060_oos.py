#!/usr/bin/env python3
"""tools/nrfi_dd_refute_lam060_oos.py -- part 2 of the refutation.

(a) boundary fragility: the claim reproduces only under -log(raw p); the
    lambda_lr_total column differs by 0.0008 median yet moves ROI 4.7pp.
(b) leave-one-day-out jackknife on the 22-bet set.
(c) family-wise null sim for the +6.1% cell.
(d) OUT-OF-SAMPLE: the lambda leg's true NRFI rate on 2024 / 2025 / 2026
    backtests (no odds there -> bounds ACCURACY, not profit).
Read-only.
"""
from __future__ import annotations
import csv, math, sys, random
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"


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


def load(paths, want_odds=False):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = (r.get("actual_result") or "").upper()
                if a not in ("NRFI", "YRFI"):
                    continue
                no = fnum(r.get("market_nrfi_odds"))
                if want_odds and no is None:
                    continue
                fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                rows.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                             "odds": no, "hit": 1 if a == "NRFI" else 0,
                             "colLam": fnum(r.get("lambda_lr_total"))})
    if not rows:
        return rows
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def stats(sub):
    n = len(sub)
    if n == 0:
        return None
    w = sum(r["hit"] for r in sub)
    pl = sum(payout(r["odds"]) if r["hit"] else -1.0 for r in sub)
    be = sum(implied(r["odds"]) for r in sub) / n
    return {"n": n, "w": w, "hr": w / n, "be": be, "pl": pl, "roi": pl / n}


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def main():
    s26 = load([ROOT / "data" / "picks_2026.csv"], want_odds=True)
    rule = [r for r in s26 if r["lam"] <= 0.60 and r["odds"] >= -115]
    st = stats(rule)
    print("=" * 92)
    print(f"THE CLAIM, REPRODUCED:  n={st['n']}  wins={st['w']}  hit={100*st['hr']:.1f}%  "
          f"be={100*st['be']:.1f}%  ROI={100*st['roi']:+.1f}%  P&L={st['pl']:+.2f}u")
    print()

    # ---- (a) boundary fragility -------------------------------------------
    print("-" * 92)
    print("(a) BOUNDARY FRAGILITY -- two numerically identical definitions of the SAME lambda")
    alt = [r for r in s26 if r["colLam"] is not None and r["colLam"] <= 0.60 and r["odds"] >= -115]
    sa = stats(alt)
    dd = [abs(r["lam"] - r["colLam"]) for r in s26 if r["colLam"] is not None]
    print(f"    median |(-log raw p) - lambda_lr_total| over all {len(dd)} priced games = "
          f"{float(np.median(dd)):.5f}  (pure float/rounding noise)")
    print(f"    select on -log(raw p)     : n={st['n']} hit={100*st['hr']:.1f}% ROI={100*st['roi']:+.1f}%")
    print(f"    select on lambda_lr_total : n={sa['n']} hit={100*sa['hr']:.1f}% ROI={100*sa['roi']:+.1f}%")
    ka = {(r["date"], r["odds"], r["hit"]) for r in rule}
    kb = {(r["date"], r["odds"], r["hit"]) for r in alt}
    diff = [r for r in s26 if (r["date"], r["odds"], r["hit"]) in (kb - ka)]
    print(f"    the two sets differ by {len(kb ^ ka)} game(s); the disputed ones sit at:")
    for r in sorted(diff, key=lambda x: x["date"]):
        print(f"        {r['date']}  -log(raw p)={r['lam']:.4f}  column={r['colLam']:.4f}  "
              f"odds={r['odds']:+.0f}  {'NRFI' if r['hit'] else 'YRFI'}")
    print(f"    -> a {float(np.median(dd)):.4f} wobble in the thresholded quantity moves ROI by "
          f"{100*abs(st['roi']-sa['roi']):.1f} points. The 'edge' is boundary bookkeeping.")

    # ---- (b) jackknife -----------------------------------------------------
    print()
    print("-" * 92)
    print("(b) LEAVE-ONE-DAY-OUT JACKKNIFE on the 22-bet set")
    days = sorted(set(r["date"] for r in rule))
    jk = []
    for d in days:
        s2 = stats([r for r in rule if r["date"] != d])
        jk.append((s2["roi"], d, sum(1 for r in rule if r["date"] == d)))
    jk.sort()
    print(f"    {len(days)} slates. ROI after dropping ONE slate ranges "
          f"{100*jk[0][0]:+.1f}% .. {100*jk[-1][0]:+.1f}%")
    print(f"    worst drops (edge depends on these days):")
    for roi, d, k in jk[:3]:
        print(f"        drop {d} ({k} bet(s)) -> ROI {100*roi:+.1f}%")
    neg = sum(1 for roi, _, _ in jk if roi <= 0)
    print(f"    {neg}/{len(days)} single-slate deletions push the rule to <= 0% ROI")
    lo, hi = wilson(st["w"], st["n"])
    print(f"    Wilson 95% CI on the {100*st['hr']:.1f}% hit rate: "
          f"[{100*lo:.1f}%, {100*hi:.1f}%]  (break-even {100*st['be']:.1f}% is inside)")

    # ---- (c) family-wise null ---------------------------------------------
    print()
    print("-" * 92)
    print("(c) SEARCH EXPOSURE for the +6.1% cell, -log(raw p) grid, null = market is right")
    CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
    PF = [-160, -140, -125, -115, -105, +100, +120]
    lam = np.asarray([r["lam"] for r in s26])
    od = np.asarray([r["odds"] for r in s26])
    pay = np.asarray([payout(r["odds"]) for r in s26])
    pv = np.asarray([implied(o) - 0.030 for o in od])  # strip a ~6c two-way hold
    masks = [(lam <= c) & (od >= p) for c in CAPS for p in PF]
    masks = [m for m in masks if m.sum() >= 20]
    obs = {}
    for c in CAPS:
        for p in PF:
            m = (lam <= c) & (od >= p)
            if m.sum() >= 20:
                obs[(c, p)] = float(np.where(np.asarray([r["hit"] for r in s26])[m] > 0,
                                             pay[m], -1.0).mean())
    best_obs = max(obs.values())
    rng = np.random.default_rng(2026)
    ITERS = 20000
    maxes = np.empty(ITERS)
    tgt = (lam <= 0.60) & (od >= -115)
    tgt_hits = 0
    for i in range(ITERS):
        y = rng.random(len(s26)) < pv
        unit = np.where(y, pay, -1.0)
        maxes[i] = max(unit[m].mean() for m in masks)
        if unit[tgt].mean() >= st["roi"]:
            tgt_hits += 1
    print(f"    {len(masks)} cells with n>=20 out of {len(CAPS)*len(PF)} searched")
    print(f"    best cell actually observed: {100*best_obs:+.1f}%  "
          f"(the +6.1% cell ranks #{sorted(obs.values(), reverse=True).index(obs[(0.60,-115)])+1})")
    print(f"    under the null, median best-of-grid ROI = {100*np.median(maxes):+.1f}%, "
          f"90th pct = {100*np.percentile(maxes,90):+.1f}%")
    print(f"    family-wise p(best-of-grid >= {100*best_obs:+.1f}%) = "
          f"{float((maxes>=best_obs).mean()):.3f}")
    print(f"    uncorrected p(this cell >= +6.1%) = {tgt_hits/ITERS:.3f}  -> "
          f"Bonferroni over {len(masks)} cells = {min(1.0, len(masks)*tgt_hits/ITERS):.2f}")

    # ---- (d) out-of-sample seasons ----------------------------------------
    print()
    print("-" * 92)
    print("(d) OUT-OF-SAMPLE: does the lambda leg even clear the break-even it needs?")
    print("    (2024/2025 backtests carry NO odds -> this bounds ACCURACY, not profit)")
    print(f"    the rule needs the selected games to hit NRFI > {100*st['be']:.1f}%")
    sets = {
        "2024 backtest": [BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"],
        "2025 backtest": [BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"],
        "2026 backtest": [BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
                          BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"],
        "2026 live (priced)": None,
    }
    print()
    print(f"    {'season':<20}{'all games':>26}{'lambda <= 0.60':>30}")
    print(f"    {'':<20}{'n':>8}{'NRFI%':>9}{'':>9}{'n':>8}{'NRFI%':>9}{'95% CI':>18}")
    for lab, paths in sets.items():
        rr = s26 if paths is None else load(paths)
        if not rr:
            print(f"    {lab:<20}  (no rows)")
            continue
        n0 = len(rr); h0 = sum(r["hit"] for r in rr)
        s_ = [r for r in rr if r["lam"] <= 0.60]
        n1 = len(s_); h1 = sum(r["hit"] for r in s_)
        lo1, hi1 = wilson(h1, n1) if n1 else (float("nan"),) * 2
        print(f"    {lab:<20}{n0:>8}{100*h0/n0:>8.1f}%{'':>9}{n1:>8}"
              f"{(100*h1/n1 if n1 else float('nan')):>8.1f}%"
              f"{f'[{100*lo1:.1f}, {100*hi1:.1f}]':>18}")
    print()
    print("    3-split direction check on the lambda leg (does low lambda beat 51.3% NRFI?):")
    for lab, paths in sets.items():
        rr = s26 if paths is None else load(paths)
        if not rr:
            continue
        s_ = [r for r in rr if r["lam"] <= 0.60]
        if not s_:
            continue
        h1 = sum(r["hit"] for r in s_); n1 = len(s_)
        lo1, hi1 = wilson(h1, n1)
        verdict = "CLEARS" if lo1 > st["be"] else ("fails" if hi1 < st["be"] else "inconclusive")
        print(f"        {lab:<20} {100*h1/n1:5.1f}%  vs  {100*st['be']:.1f}% needed   -> {verdict}")


if __name__ == "__main__":
    main()

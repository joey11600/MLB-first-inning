#!/usr/bin/env python3
"""
tools/nrfi_dd_refute_gate060.py -- adversarial re-derivation of the
candidate NRFI rule:

    BET NRFI when walk-forward-calibrated p_nrfi >= 0.60
                AND lambda_lr_total <= 0.52 (_LR_LAMBDA_NRFI_CEILING)

    claimed: 14 bets, 78.6% hit, 58.3% break-even, real captured prices.

This script does NOT trust the claim. It rebuilds it from the CSV, then
attacks it on three axes:

    1. OVERFITTING     -- how many cells were reachable; Sidak / Bonferroni
                          correction; does it replicate in another season
    2. PRICING         -- is break-even from real odds; survive -10c;
                          are the winners the games DK priced sharpest
    3. SAMPLE SIZE     -- block bootstrap over DAYS, exact binomial,
                          calendar concentration, effective n

Read-only. Touches no production file.
"""
from __future__ import annotations

import csv
import math
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied, fnum  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402

CEIL = P._LR_LAMBDA_NRFI_CEILING       # 0.52
GATE = 0.60


def hr(t=""):
    print("\n" + "=" * 92)
    if t:
        print("  " + t)
        print("=" * 92)


def binom_sf(k, n, p):
    """P(X >= k) for X~Bin(n,p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def shade(odds, cents=10.0):
    """Make a price `cents` worse for the bettor (American odds)."""
    if odds is None:
        return None
    # convert to a decimal-ish and move implied prob up by cents/100 of a
    # -100 unit: standard practice is to subtract from the payout side.
    if odds > 0:
        new = odds - cents
        if new <= 0:              # crossed the +100/-100 boundary
            new = -100.0 + (new + 100.0) * -1.0
            new = -(200.0 - odds + cents) if odds - cents <= 0 else new
            new = min(new, -100.0)
        return new
    return odds - cents


def sel_nrfi(rows, probs, gate, ceil, *, real_only=True):
    out = []
    for r, p in zip(rows, probs):
        if p is None or p < gate:
            continue
        lam = r["lambda"]
        if lam is not None and lam > ceil:
            continue
        o = r["nrfi_odds"]
        if o is None and real_only:
            continue
        out.append({"date": r["date"], "p": p, "odds": o,
                    "win": not r["yrfi_hit"], "lam": lam,
                    "game": f"{r['away']}@{r['home']}"})
    return out


def flat(bets, cents=0.0):
    n = len(bets)
    if not n:
        return dict(n=0)
    w = sum(b["win"] for b in bets)
    pl = 0.0
    needs = []
    for b in bets:
        o = shade(b["odds"], cents) if cents else b["odds"]
        needs.append(implied(o))
        pl += payout(o) if b["win"] else -1.0
    return dict(n=n, w=w, hit=w / n, pl=pl, roi=pl / n, need=st.mean(needs))


def boot_days(bets, iters=20000, seed=7, cents=0.0):
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    days = list(byday)
    rng = random.Random(seed)
    rois = []
    for _ in range(iters):
        n = 0
        pl = 0.0
        for _ in range(len(days)):
            for b in byday[rng.choice(days)]:
                o = shade(b["odds"], cents) if cents else b["odds"]
                n += 1
                pl += payout(o) if b["win"] else -1.0
        if n:
            rois.append(pl / n)
    rois.sort()
    return (rois[int(0.05 * len(rois))], rois[int(0.95 * len(rois))],
            rois[int(0.025 * len(rois))], rois[int(0.975 * len(rois))],
            sum(1 for v in rois if v <= 0) / len(rois))


def main():
    rows, skipped = load_season()
    print(f"loaded {len(rows)} graded 2026 games ({skipped} feature-build skips)")
    dep = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    p_dep = [dep.predict(r["raw"]) for r in rows]
    p_wf = walk_forward_probs(rows)

    # ---------------------------------------------------------- 0. rebuild
    hr("0. REBUILD THE CLAIM FROM THE CSV")
    bets = sel_nrfi(rows, p_wf, GATE, CEIL)
    s = flat(bets)
    print(f"  walk-forward p>={GATE} AND lambda<={CEIL}, REAL captured DK prices only")
    print(f"    n={s['n']}  wins={s['w']}  hit={100*s['hit']:.1f}%  "
          f"break-even(need)={100*s['need']:.1f}%  flat P&L={s['pl']:+.2f}u  "
          f"ROI={100*s['roi']:+.1f}%")
    print(f"  claimed: 14 bets / 78.6% hit / 58.3% break-even -> "
          f"{'REPRODUCED' if s['n'] == 14 else 'DOES NOT REPRODUCE'}")
    print("\n  the actual 14 bets:")
    for b in sorted(bets, key=lambda x: x["date"]):
        print(f"    {b['date']}  {b['game']:<9} p={b['p']:.4f} lam={b['lam']:.3f} "
              f"odds={int(b['odds']):>5}  {'WIN ' if b['win'] else 'LOSS'}")

    # ---------------------------------------------------------- 1. calendar
    hr("1. SAMPLE SIZE -- CALENDAR CONCENTRATION")
    days = sorted({b["date"] for b in bets})
    span = (np.datetime64(days[-1]) - np.datetime64(days[0])).astype(int)
    print(f"  {len(bets)} bets live on {len(days)} distinct slates, "
          f"{days[0]} .. {days[-1]}  (calendar span {span} days)")
    bym = defaultdict(lambda: [0, 0])
    for b in bets:
        m = bym[b["date"][:7]]
        m[0] += 1
        m[1] += b["win"]
    print("  by month:  " + "   ".join(f"{k}: {v[1]}-{v[0]-v[1]}" for k, v in sorted(bym.items())))
    print("\n  MAX walk-forward p_nrfi attainable per month (can the rule even fire?):")
    mx = defaultdict(float)
    cnt = defaultdict(int)
    for r, p in zip(rows, p_wf):
        if p is None:
            continue
        mx[r["date"][:7]] = max(mx[r["date"][:7]], p)
        cnt[r["date"][:7]] += 1
    for m in sorted(mx):
        flag = "   <-- rule can NEVER fire this month" if mx[m] < GATE else ""
        print(f"    {m}  games={cnt[m]:>4}  max wf p_nrfi={mx[m]:.4f}{flag}")

    # per-day effective sample
    print("\n  per-slate clustering (bets that settle together are not independent):")
    byday = defaultdict(list)
    for b in bets:
        byday[b["date"]].append(b)
    for d in sorted(byday):
        v = byday[d]
        print(f"    {d}: {len(v)} bets, {sum(x['win'] for x in v)} wins")

    # ---------------------------------------------------------- 2. stats
    hr("2. SAMPLE SIZE -- IS 11/14 EVEN SIGNIFICANT?")
    k, n, need = s["w"], s["n"], s["need"]
    pv = binom_sf(k, n, need)
    lo, hi = wilson(k, n)
    print(f"  exact binomial P(X>={k} | n={n}, p=break-even {need:.4f}) = {pv:.4f}")
    print(f"  Wilson 95% CI on the hit rate: [{100*lo:.1f}%, {100*hi:.1f}%]  "
          f"(contains break-even {100*need:.1f}%? "
          f"{'YES -> not significant' if lo <= need <= hi else 'no'})")
    b5, b95, b25, b975, pneg = boot_days(bets)
    print(f"  block bootstrap over DAYS (20k iters, resampling slates):")
    print(f"    ROI 90% CI [{100*b5:+.1f}%, {100*b95:+.1f}%]   "
          f"95% CI [{100*b25:+.1f}%, {100*b975:+.1f}%]")
    print(f"    P(ROI <= 0) under resampling = {100*pneg:.1f}%   "
          f"-> CI {'EXCLUDES' if b25 > 0 else 'INCLUDES'} zero at 95%")
    # how much of the win comes from the single best day
    tot = s["pl"]
    worst = None
    for d in sorted(byday):
        rest = [b for b in bets if b["date"] != d]
        r2 = flat(rest)
        if worst is None or r2["pl"] < worst[1]:
            worst = (d, r2["pl"], r2["n"])
    print(f"  leave-one-slate-out: dropping {worst[0]} leaves "
          f"{worst[2]} bets at {worst[1]:+.2f}u")

    # ---------------------------------------------------------- 3. pricing
    hr("3. PRICING")
    print(f"  break-even {100*need:.1f}% is computed from {n} REAL captured DK "
          f"prices (mean implied) -- confirmed, not assumed.")
    print(f"  price distribution: " +
          ", ".join(str(int(b['odds'])) for b in sorted(bets, key=lambda x: x['odds'])))
    for c in (0, 5, 10, 15, 20):
        f2 = flat(bets, cents=c)
        lo2, hi2, l25, h25, pn = boot_days(bets, cents=c)
        print(f"    price shaded {c:>2}c worse: need={100*f2['need']:.1f}%  "
              f"P&L={f2['pl']:+.2f}u  ROI={100*f2['roi']:+.1f}%  "
              f"boot90 [{100*lo2:+.1f}%,{100*hi2:+.1f}%]  P(ROI<=0)={100*pn:.0f}%")
    # are the winners the sharp games?
    print("\n  is the profit coming from games DK priced CHEAP or games it priced RICH?")
    srt = sorted(bets, key=lambda b: implied(b["odds"]))
    half = len(srt) // 2
    for lbl, grp in (("cheapest half (low implied)", srt[:half]),
                     ("richest half  (high implied)", srt[half:])):
        g = flat(grp)
        print(f"    {lbl}: n={g['n']} hit={100*g['hit']:.1f}% need={100*g['need']:.1f}% "
              f"P&L={g['pl']:+.2f}u")

    # coverage: how many 0.60 games had NO price at all
    allsel = sel_nrfi(rows, p_wf, GATE, CEIL, real_only=False)
    nopx = [b for b in allsel if b["odds"] is None]
    print(f"\n  price coverage at this gate: {len(bets)}/{len(allsel)} had a captured "
          f"DK price; {len(nopx)} did not.")
    if nopx:
        w = sum(b["win"] for b in nopx)
        print(f"    the {len(nopx)} unpriced ones went {w}-{len(nopx)-w}. "
              f"Including them at a nominal -130 -> "
              f"{flat([dict(b, odds=-130.0) for b in allsel])['pl']:+.2f}u over "
              f"{len(allsel)} bets")

    # ---------------------------------------------------------- 4. search
    hr("4. OVERFITTING -- SEARCH EXPOSURE")
    gates = [0.55, 0.56, 0.58, 0.60, 0.62, 0.64, 0.65]
    ceils = [0.45, 0.48, 0.52, 0.55, 0.60, 0.70, 9.9]
    cells = 0
    best = []
    print(f"  {'gate':>6}{'ceil':>7}  " + "  calibrator=WALK-FORWARD        calibrator=DEPLOYED")
    for g in gates:
        for c in ceils:
            line = f"  {g:>6.2f}{c:>7.2f}  "
            for lbl, pp in (("wf", p_wf), ("dep", p_dep)):
                bb = sel_nrfi(rows, pp, g, c)
                cells += 1
                f3 = flat(bb)
                if f3["n"] == 0:
                    line += f"{'n=0':>30}"
                    continue
                line += (f" n={f3['n']:<4d} hit={100*f3['hit']:>5.1f}% "
                         f"P&L={f3['pl']:>+7.2f}u ")
                if f3["pl"] > 0 and f3["n"] >= 5:
                    best.append((g, c, lbl, f3["n"], f3["hit"], f3["pl"]))
            print(line)
    print(f"\n  CELLS EVALUATED HERE: {cells} (gate x ceiling x 2 calibrators).")
    print(f"  The reporting note says ~40 cells were searched to find this rule.")
    for K in (14, 40, cells):
        a = 1 - (1 - pv) ** K
        print(f"    with K={K:>3} independent looks: family-wise P(some cell >= this "
              f"good under the null) = {100*a:.1f}%   "
              f"Bonferroni-adjusted p = {min(1.0, pv*K):.3f}")

    # ---------------------------------------------------------- 5. OOS
    hr("5. OVERFITTING -- DOES IT REPLICATE IN ANOTHER SEASON?")
    print("  The rule has TWO conditions. Check whether each is even measurable")
    print("  outside 2026 before asking whether it replicates.\n")
    for lbl, fn_ in (("2024", "backtest_2024-04-01_to_2024-09-30_truepit.csv"),
                     ("2025", "backtest_2025-04-01_to_2025-09-30_truepit.csv")):
        with open(ROOT / "data" / "backtests" / fn_, encoding="utf-8") as f:
            rr = list(csv.DictReader(f))
        h = rr[0].keys()
        has = "lambda_lr_total" in h
        lam = [fnum(r.get("lambda_total")) for r in rr]
        lam = [v for v in lam if v is not None]
        print(f"  {lbl}: rows={len(rr)}  has 'lambda_lr_total' column? {has}   "
              f"'lambda_total' min={min(lam):.3f} mean={st.mean(lam):.3f}")
        print(f"       games with lambda_total <= {CEIL}: "
              f"{sum(1 for v in lam if v <= CEIL)} / {len(lam)}")
    # 2026 own coverage of the lambda column
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        pr = list(csv.DictReader(f))
    have = sum(1 for r in pr if fnum(r.get("lambda_lr_total")) is not None)
    print(f"\n  even in 2026: lambda_lr_total present on {have}/{len(pr)} rows "
          f"({100*have/len(pr):.0f}%)")

    # the p_nrfi condition alone, 3-split on the calibrator
    print("\n  --- p_nrfi >= gate ALONE (lambda condition dropped, since it is")
    print("      not measurable pre-2026), proper 3-split on the calibrator ---")
    from tools.nrfi_dd_power2 import load_bt, BT  # noqa: E402
    data = {}
    for lbl, path in BT:
        data[lbl] = load_bt(path)
    keys = list(data)
    print(f"  loaded: " + ", ".join(f"{k}={len(data[k])}" for k in keys))

    def fitcal(train_rows):
        return CIRCalibrator.fit([x["raw"] for x in train_rows],
                                 [x["y"] for x in train_rows], 20, ["oos"])

    splits = []
    if "2024" in data and "2025" in data:
        splits.append(("fit 2024 -> score 2025", data["2024"], data["2025"]))
        splits.append(("fit 2025 -> score 2024", data["2025"], data["2024"]))
        tr = data["2024"] + data["2025"]
        sc = [{"raw": r["raw"], "y": r["y_nrfi"], "lambda": r["lambda"],
               "date": r["date"]} for r in rows]
        splits.append(("fit 2024+2025 -> score 2026", tr, sc))
    print(f"\n  break-even reference = {100*need:.1f}% (the mean vig-inclusive "
          f"implied NRFI price actually captured on the 14 bets)")
    print(f"  {'split':<30}{'gate':>6}{'n':>7}{'hit%':>8}{'vs BE':>8}{'95% CI':>18}")
    for lbl, tr, sc in splits:
        cal = fitcal(tr)
        pp = [cal.predict(x["raw"]) for x in sc]
        for g in (0.58, 0.60, 0.62):
            sel = [(x, p) for x, p in zip(sc, pp) if p >= g]
            if not sel:
                print(f"  {lbl:<30}{g:>6.2f}{0:>7}{'--':>8}{'--':>8}"
                      f"{'  gate unreachable':>18}")
                continue
            kk = sum(x["y"] for x, _ in sel)
            nn = len(sel)
            lo3, hi3 = wilson(kk, nn)
            print(f"  {lbl:<30}{g:>6.2f}{nn:>7}{100*kk/nn:>8.1f}"
                  f"{100*(kk/nn-need):>+8.1f}"
                  f"   [{100*lo3:>5.1f},{100*hi3:>5.1f}]")

    hr("")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""tools/nrfi_dd_refute_lam060_seasons.py -- part 3: cross-season + honest OOS.

Tests the two legs of  (lam <= 0.60 AND nrfi_odds >= -115)  where each CAN be
tested:
  * lambda leg -> 2024 / 2025 / 2026 backtests (no odds; bounds ACCURACY)
  * price leg  -> cannot be tested outside 2026 at all (no odds exist). Stated
                  as a finding, not skipped.
  * full rule  -> honest discover-on-A / confirm-on-B split inside 2026 prices.
Read-only.
"""
from __future__ import annotations
import csv, math, sys
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


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def load(paths, outcol, homecol, odds=False):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                v = (r.get(outcol) or "").upper()
                if v not in ("NRFI", "YRFI"):
                    continue
                no = fnum(r.get("market_nrfi_odds")) if odds else None
                if odds and no is None:
                    continue
                fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                rows.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                             "odds": no, "hit": 1 if v == "NRFI" else 0})
    if not rows:
        return rows
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PF = [-160, -140, -125, -115, -105, +100, +120]


def main():
    s24 = load([BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"], "actual_side", "home")
    s25 = load([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home")
    s26b = load([BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
                 BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"], "actual_result", "home_team")
    s26 = load([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team", odds=True)
    rule = [r for r in s26 if r["lam"] <= 0.60 and r["odds"] >= -115]
    NEED = sum(implied(r["odds"]) for r in rule) / len(rule)

    print("=" * 96)
    print("A. THE LAMBDA LEG ACROSS SEASONS -- does lam<=0.60 hit NRFI above the 51.3% the")
    print("   rule's captured prices demand?   (2024/2025 have NO odds: accuracy bound only)")
    print("=" * 96)
    print(f"   {'season':<22}{'all n':>8}{'all NRFI%':>11}{'lam<=.60 n':>13}"
          f"{'NRFI%':>9}{'95% CI':>18}{'vs 51.3%':>12}")
    for lab, rr in (("2024 backtest", s24), ("2025 backtest", s25),
                    ("2026 backtest", s26b), ("2026 live priced", s26)):
        if not rr:
            print(f"   {lab:<22}  (0 rows loaded)")
            continue
        n0, h0 = len(rr), sum(r["hit"] for r in rr)
        s_ = [r for r in rr if r["lam"] <= 0.60]
        n1, h1 = len(s_), sum(r["hit"] for r in s_)
        lo, hi = wilson(h1, n1)
        verd = "CLEARS" if lo > NEED else ("FAILS" if hi < NEED else "inconclusive")
        print(f"   {lab:<22}{n0:>8}{100*h0/n0:>10.1f}%{n1:>13}{100*h1/n1:>8.1f}%"
              f"{f'[{100*lo:.1f},{100*hi:.1f}]':>18}{verd:>12}")

    print()
    print("   Lambda-band NRFI rate by season (is low lambda even a stable NRFI signal?):")
    bands = [(0.0, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.75), (0.75, 9.0)]
    print(f"   {'band':<14}" + "".join(f"{s:>20}" for s in
                                       ("2024", "2025", "2026 backtest", "2026 live")))
    for lo_, hi_ in bands:
        line = f"   {f'{lo_:.2f}-{hi_ if hi_<9 else 9.99:.2f}':<14}"
        for rr in (s24, s25, s26b, s26):
            s_ = [r for r in rr if lo_ <= r["lam"] < hi_]
            if len(s_) < 15:
                line += f"{'.':>20}"
            else:
                line += f"{100*sum(r['hit'] for r in s_)/len(s_):>14.1f}% n={len(s_):<4}"
        print(line)

    print()
    print("=" * 96)
    print("B. THE PRICE LEG -- can it be validated out of sample?")
    print("=" * 96)
    for lab, rr in (("2024 backtest", s24), ("2025 backtest", s25), ("2026 backtest", s26b)):
        n_od = sum(1 for r in rr if r["odds"] is not None)
        print(f"   {lab:<22} rows={len(rr):<6} rows carrying a DK NRFI price: {n_od}")
    print("   -> the 'odds >= -115' half of the rule is UNTESTABLE outside the single 2026")
    print("      sample it was discovered in. There is no second season of prices anywhere in")
    print("      this repo. The project's mandatory 3-split cannot be run on this rule at all.")

    print()
    print("=" * 96)
    print("C. HONEST SPLIT INSIDE 2026 PRICES -- pick the best cell on part A, bet it on part B")
    print("=" * 96)
    dates = sorted(set(r["date"] for r in s26))
    cut = dates[len(dates) // 2]
    A = [r for r in s26 if r["date"] < cut]
    B = [r for r in s26 if r["date"] >= cut]
    print(f"   split at {cut}:  discover n={len(A)}   confirm n={len(B)}")
    for lab, dis, con in (("A->B (May/Jun -> Jul)", A, B), ("B->A (Jul -> May/Jun)", B, A)):
        best = None
        for c in CAPS:
            for p in PF:
                s_ = [r for r in dis if r["lam"] <= c and r["odds"] >= p]
                if len(s_) < 15:
                    continue
                roi = sum(payout(r["odds"]) if r["hit"] else -1.0 for r in s_) / len(s_)
                if best is None or roi > best[0]:
                    best = (roi, c, p, len(s_))
        if best is None:
            print(f"   {lab}: no cell reached n>=15 in the discovery half")
            continue
        roi, c, p, nA = best
        s_ = [r for r in con if r["lam"] <= c and r["odds"] >= p]
        nB = len(s_)
        roiB = (sum(payout(r["odds"]) if r["hit"] else -1.0 for r in s_) / nB) if nB else float("nan")
        plB = sum(payout(r["odds"]) if r["hit"] else -1.0 for r in s_) if nB else 0.0
        print(f"   {lab}:  best in-sample cell = lam<={c if c<9 else 'inf'} / price>={p:+d} "
              f"(n={nA}, ROI {100*roi:+.1f}%)")
        print(f"        {'':<24}applied blind to the other half: n={nB}, "
              f"ROI {100*roiB:+.1f}%, P&L {plB:+.2f}u")
        s2 = [r for r in con if r["lam"] <= 0.60 and r["odds"] >= -115]
        if s2:
            pl2 = sum(payout(r["odds"]) if r["hit"] else -1.0 for r in s2)
            print(f"        {'':<24}the CANDIDATE cell on that same half: n={len(s2)}, "
                  f"ROI {100*pl2/len(s2):+.1f}%, P&L {pl2:+.2f}u")

    print()
    print("=" * 96)
    print("D. IS THE RULE JUST THE ALREADY-REFUTED MARKET-DISAGREEMENT FILTER?")
    print("=" * 96)
    dis = []
    for r in s26:
        pm = implied(r["odds"])
        pmod = math.exp(-r["lam"])
        r["dis"] = pmod - pm
        dis.append(r)
    inr = [r for r in dis if r["lam"] <= 0.60 and r["odds"] >= -115]
    out = [r for r in dis if not (r["lam"] <= 0.60 and r["odds"] >= -115)]
    print(f"   mean (model P(NRFI) - book implied P(NRFI)):")
    print(f"      inside the rule : {100*np.mean([r['dis'] for r in inr]):+.1f} pp   (n={len(inr)})")
    print(f"      everywhere else : {100*np.mean([r['dis'] for r in out]):+.1f} pp   (n={len(out)})")
    print("   the rule is a re-parameterisation of 'model out-bulls the book on NRFI'.")
    print("   Ranked disagreement buckets on the SAME priced universe:")
    ds = sorted(dis, key=lambda r: r["dis"])
    k = len(ds) // 6
    for i in range(6):
        s_ = ds[i * k:(i + 1) * k] if i < 5 else ds[5 * k:]
        pl = sum(payout(r["odds"]) if r["hit"] else -1.0 for r in s_)
        print(f"      bucket {i+1}  disagreement {100*s_[0]['dis']:+6.1f}..{100*s_[-1]['dis']:+6.1f}pp"
              f"   n={len(s_):<5} hit={100*sum(r['hit'] for r in s_)/len(s_):5.1f}%"
              f"  ROI={100*pl/len(s_):+6.1f}%")


if __name__ == "__main__":
    main()

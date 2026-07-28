#!/usr/bin/env python3
"""tools/nrfi_dd_pricegrid.py -- the lever the brief says matters: PRICE.
Grid over (lambda ceiling) x (worst DK NRFI price we will accept), on the
2026 games with a REAL captured DraftKings NRFI line.  Read-only.

Also: apply the geometry that 2025 liked, blind, to 2026 real prices -- the
single honest out-of-sample confirmation available.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
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


def load(paths, outcol, homecol, odds=False):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get(outcol) or "").upper() not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                rows.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                             "y": 1 if (r.get(outcol) or "").upper() == "NRFI" else 0,
                             "odds": fnum(r.get("market_nrfi_odds")) if odds else None})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def day_boot(sub, key, iters=5000, seed=21):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(key(r))
    days = list(byday.values())
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    k, out = len(days), []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for i in idx for x in days[i]]
        out.append(sum(v) / len(v))
    out.sort()
    return out[int(0.025 * iters)], out[int(0.975 * iters)]


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
# "accept price no worse than X" -- more positive = more generous to us
PRICE_FLOORS = [-160, -140, -125, -115, -105, +100, +120]


def main():
    s26 = load([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team", True)
    priced = [r for r in s26 if r["odds"] is not None]
    print("=" * 100)
    print(f"  PRICE x LAMBDA GRID -- 2026, REAL captured DK NRFI prices only (n={len(priced)})")
    print("=" * 100)
    print("  Rule: bet NRFI when lambda <= cap AND the DK NRFI price is no worse than the")
    print("  column header (e.g. '>= -115' means we skip anything priced -116 or worse).")
    print("  Cell = flat-1u ROI%; '.' = fewer than 20 bets.\n")
    hdr = f"  {'lam<=':>8}" + "".join(f"{('>=' + f'{p:+d}'):>12}" for p in PRICE_FLOORS)
    print(hdr)
    cells = 0
    results = []
    for c in CAPS:
        line = f"  {('inf' if c > 9 else f'{c:.2f}'):>8}"
        for pf in PRICE_FLOORS:
            sub = [r for r in priced if r["lam"] <= c and r["odds"] >= pf]
            cells += 1
            if len(sub) < 20:
                line += f"{'.':>12}"
            else:
                pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
                roi = pl / len(sub)
                line += f"{100*roi:>+11.1f}%"
                results.append((roi, c, pf, len(sub), sub))
        print(line)
    print("\n  n per cell:")
    print(hdr)
    for c in CAPS:
        line = f"  {('inf' if c > 9 else f'{c:.2f}'):>8}"
        for pf in PRICE_FLOORS:
            sub = [r for r in priced if r["lam"] <= c and r["odds"] >= pf]
            line += f"{len(sub):>12}"
        print(line)
    print(f"\n  CELLS SEARCHED IN THIS GRID: {cells} ({len(CAPS)} caps x {len(PRICE_FLOORS)} price floors)")

    results.sort(reverse=True)
    print("\n  Top 5 cells by ROI, with a DAY-BLOCK bootstrap CI:")
    print(f"  {'lam<=':>8}{'price>=':>10}{'n':>6}{'hit%':>8}{'need%':>8}{'ROI%':>9}"
          f"{'ROI 95% CI':>26}")
    for roi, c, pf, n, sub in results[:5]:
        hit = sum(r["y"] for r in sub) / n
        need = st.mean([implied(r["odds"]) for r in sub])
        lo, hi = day_boot(sub, lambda r: (payout(r["odds"]) if r["y"] else -1.0))
        print(f"  {('inf' if c > 9 else f'{c:.2f}'):>8}{pf:>+10d}{n:>6}{100*hit:>8.1f}"
              f"{100*need:>8.1f}{100*roi:>+9.1f}   [{100*lo:>+6.1f}%,{100*hi:>+6.1f}%]")

    # ---- 2025-chosen geometry, applied blind to 2026 ---------------------
    print("\n" + "=" * 100)
    print("  OUT-OF-SAMPLE CONFIRMATION: take the ceiling 2025 liked best, bet it blind in 2026")
    print("=" * 100)
    s25 = load([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home")
    base25 = sum(r["y"] for r in s25) / len(s25)
    best = None
    for c in CAPS:
        sub = [r for r in s25 if r["lam"] <= c]
        if len(sub) < 100:
            continue
        lift = sum(r["y"] for r in sub) / len(sub) - base25
        if best is None or lift > best[0]:
            best = (lift, c)
    print(f"  2025 says the best ceiling is lambda <= {best[1]:.2f} (+{100*best[0]:.1f}pp over base)")
    print(f"  {'applied to':<26}{'n':>6}{'hit%':>8}{'need%':>8}{'P/L u':>9}{'ROI%':>8}"
          f"{'ROI 95% CI':>26}")
    for c in (best[1], 0.52, 0.56, 0.60):
        sub = [r for r in priced if r["lam"] <= c]
        if len(sub) < 10:
            continue
        n = len(sub)
        hit = sum(r["y"] for r in sub) / n
        need = st.mean([implied(r["odds"]) for r in sub])
        pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
        lo, hi = day_boot(sub, lambda r: (payout(r["odds"]) if r["y"] else -1.0))
        print(f"  {f'2026 real prices, lam<={c:.2f}':<26}{n:>6}{100*hit:>8.1f}{100*need:>8.1f}"
              f"{pl:>+9.2f}{100*pl/n:>+8.1f}   [{100*lo:>+6.1f}%,{100*hi:>+6.1f}%]")

    print("\n  What hit rate would each 2026 band have needed, vs what 2025 delivered there?")
    print(f"  {'lam<=':>8}{'2026 need%':>12}{'2025 hit%':>12}{'2026 hit%':>12}"
          f"{'2025 would be':>16}{'2026 actually is':>18}")
    for c in (0.48, 0.52, 0.56, 0.60, 0.65, 99.0):
        sub = [r for r in priced if r["lam"] <= c]
        s25s = [r for r in s25 if r["lam"] <= c]
        if len(sub) < 15 or len(s25s) < 30:
            continue
        need = st.mean([implied(r["odds"]) for r in sub])
        h25 = sum(r["y"] for r in s25s) / len(s25s)
        h26 = sum(r["y"] for r in sub) / len(sub)
        print(f"  {('inf' if c > 9 else f'{c:.2f}'):>8}{100*need:>12.1f}{100*h25:>12.1f}"
              f"{100*h26:>12.1f}{100*(h25-need):>+15.1f}pp{100*(h26-need):>+17.1f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())

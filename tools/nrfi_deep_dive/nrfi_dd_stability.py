#!/usr/bin/env python3
"""tools/nrfi_dd_stability.py -- does the tight-lambda NRFI edge PERSIST, or
did it live in a window that has since closed?  Read-only.

Tests, in order of how decisive they are:
  1. 2025 split-half (first half fits -> second half confirms).
  2. 2026 month by month at the candidate ceilings.
  3. Walk-forward on 2026 real prices: at each date, pick the best lambda
     ceiling using ONLY prior settled games, then bet the next day blind.
  4. Permutation / multiple-comparisons check: how good would the best of
     120 cells look if lambda carried no information at all?
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc                  # noqa: E402
from calibration import ProbCalibrator       # noqa: E402

BT = ROOT / "data" / "backtests"
CAPS = [0.44, 0.48, 0.50, 0.52, 0.56, 0.60, 0.65, 0.70, 99.0]


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
    rows.sort(key=lambda r: r["date"])
    return rows


def rate(sub):
    return sum(r["y"] for r in sub) / len(sub) if sub else float("nan")


def main():
    s25 = load([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home")
    s26 = load([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team", True)

    # ---- 1. 2025 split-half ---------------------------------------------
    print("=" * 96)
    print("  1. 2025 SPLIT-HALF -- the ONLY season where the effect is large.")
    print("     If it is a real property of the model it should show in both halves.")
    print("=" * 96)
    mid = s25[len(s25) // 2]["date"]
    h1 = [r for r in s25 if r["date"] < mid]
    h2 = [r for r in s25 if r["date"] >= mid]
    print(f"  H1 = {h1[0]['date']}..{h1[-1]['date']} (n={len(h1)}, base {rate(h1):.3f})")
    print(f"  H2 = {h2[0]['date']}..{h2[-1]['date']} (n={len(h2)}, base {rate(h2):.3f})")
    print(f"\n  {'lambda<=':>10}{'H1 n':>7}{'H1 NRFI':>10}{'H1 lift':>9}"
          f"{'H2 n':>7}{'H2 NRFI':>10}{'H2 lift':>9}")
    for c in CAPS:
        a = [r for r in h1 if r["lam"] <= c]
        b = [r for r in h2 if r["lam"] <= c]
        if len(a) < 15 or len(b) < 15:
            continue
        tag = "inf" if c > 9 else f"{c:.2f}"
        print(f"  {tag:>10}{len(a):>7}{rate(a):>10.3f}{100*(rate(a)-rate(h1)):>+9.1f}"
              f"{len(b):>7}{rate(b):>10.3f}{100*(rate(b)-rate(h2)):>+9.1f}")

    # ---- 2. 2026 month by month -----------------------------------------
    print("\n" + "=" * 96)
    print("  2. 2026 MONTH BY MONTH -- did the tight-lambda edge survive past the spring?")
    print("=" * 96)
    months = sorted({r["date"][:7] for r in s26})
    print(f"  {'month':>9}{'games':>7}{'base':>8}" +
          "".join(f"{('lam<=' + f'{c:.2f}'):>16}" for c in [0.52, 0.56, 0.60]))
    for m in months:
        sub = [r for r in s26 if r["date"][:7] == m]
        line = f"  {m:>9}{len(sub):>7}{rate(sub):>8.3f}"
        for c in [0.52, 0.56, 0.60]:
            s = [r for r in sub if r["lam"] <= c]
            line += (f"{rate(s):>10.3f}({len(s):>3})" if len(s) >= 5 else f"{'--':>16}")
        print(line)

    print(f"\n  Cumulative-to-date at lambda<=0.56, 2026:")
    print(f"  {'through':>12}{'n':>6}{'NRFI%':>8}{'base%':>8}{'lift pp':>9}")
    for m in months:
        sub = [r for r in s26 if r["date"][:7] <= m]
        s = [r for r in sub if r["lam"] <= 0.56]
        if len(s) < 10:
            continue
        print(f"  {m:>12}{len(s):>6}{100*rate(s):>8.1f}{100*rate(sub):>8.1f}"
              f"{100*(rate(s)-rate(sub)):>+9.1f}")

    # ---- 3. walk-forward on 2026 real prices ----------------------------
    print("\n" + "=" * 96)
    print("  3. WALK-FORWARD ON 2026 REAL PRICES -- ceiling chosen from PRIOR settled")
    print("     games only, applied blind to the next date. No hindsight.")
    print("=" * 96)
    priced = [r for r in s26 if r["odds"] is not None]
    dates = sorted({r["date"] for r in priced})
    bets, pl, wins, chosen = 0, 0.0, 0, []
    MIN_PRIOR = 150
    for d in dates:
        prior = [r for r in priced if r["date"] < d]
        if len(prior) < MIN_PRIOR:
            continue
        best_c, best_roi = None, -9e9
        for c in CAPS:
            s = [r for r in prior if r["lam"] <= c]
            if len(s) < 20:
                continue
            roi = sum(payout(r["odds"]) if r["y"] else -1.0 for r in s) / len(s)
            if roi > best_roi:
                best_roi, best_c = roi, c
        if best_c is None or best_roi <= 0:
            chosen.append((d, None))
            continue
        chosen.append((d, best_c))
        for r in [x for x in priced if x["date"] == d and x["lam"] <= best_c]:
            bets += 1
            wins += r["y"]
            pl += payout(r["odds"]) if r["y"] else -1.0
    nabst = sum(1 for _, c in chosen if c is None)
    print(f"  dates evaluated: {len(chosen)}   of which the rule said BET NOTHING: {nabst}")
    if bets:
        print(f"  bets={bets}  hit={100*wins/bets:.1f}%  P/L={pl:+.2f}u  ROI={100*pl/bets:+.1f}%")
    else:
        print("  bets=0 -- no prior window ever showed positive NRFI ROI, so a")
        print("  walk-forward operator would never have turned NRFI on at all.")

    # ---- 4. multiple comparisons ----------------------------------------
    print("\n" + "=" * 96)
    print("  4. HOW GOOD DOES 'BEST OF THE GRID' LOOK BY CHANCE?")
    print("     Shuffle the NRFI/YRFI outcomes within 2026 (destroying any real link to")
    print("     lambda), re-run the 120-cell grid, keep the best cell's lift. 2000 reps.")
    print("=" * 96)
    prod = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    for r in s26:
        r["p"] = float(prod.predict(r["raw"]))
    PG = [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72]
    masks = []
    for pg in PG:
        for c in CAPS + [0.40, 0.46, 0.54, 0.58, 0.62, 0.80]:
            m = np.array([(r["p"] >= pg and r["lam"] <= c) for r in s26])
            if m.sum() >= 25:
                masks.append(m)
    y = np.array([r["y"] for r in s26])
    base = y.mean()
    obs = max((y[m].mean() - base) for m in masks)
    rng = np.random.default_rng(5)
    null = []
    for _ in range(2000):
        ys = rng.permutation(y)
        null.append(max((ys[m].mean() - base) for m in masks))
    null.sort()
    p_emp = sum(1 for v in null if v >= obs) / len(null)
    print(f"  distinct cells with n>=25: {len(masks)}")
    print(f"  OBSERVED best-cell lift over base rate: {100*obs:+.1f}pp")
    print(f"  NULL best-cell lift: median {100*st.median(null):+.1f}pp, "
          f"95th pct {100*null[int(0.95*len(null))]:+.1f}pp, max {100*null[-1]:+.1f}pp")
    print(f"  empirical p-value for the BEST cell (grid-corrected): {p_emp:.3f}")
    print("  => a lift below the 95th-pct null line is what pure cherry-picking produces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""tools/nrfi_dd_geometry.py -- NRFI SELECTION GEOMETRY sweep (read-only).

Question: is there a (p_nrfi gate) x (lambda ceiling) cell where the ACTUAL
NRFI rate exceeds what the book charges for NRFI?

Structural fact established first (see --identity):
    lambda_lr_total = -ln(p_t1_no_run) - ln(p_b1_no_run) = -ln(raw_p_nrfi)
so the "lambda ceiling" is ALGEBRAICALLY a floor on the RAW NRFI probability:
    lambda <= C   <=>   raw_p >= exp(-C)
The 2-D grid is therefore (calibrated p) x (raw p).  Because the CIR
calibrator is monotone non-decreasing, these two carry the SAME ordering --
the lambda ceiling only adds independent information INSIDE a calibrator
plateau, where many raw values collapse to one calibrated value.

Calibration is OUT OF SAMPLE by season (project 3-split rule):
    score 2024  <- calibrator fit on 2025
    score 2025  <- calibrator fit on 2024
    score 2026  <- calibrator fit on 2024+2025
CAVEAT stated loudly: the LR WEIGHTS (data/lr_t1.json / lr_b1.json) were fit
on 2024+2025+2026YTD, so raw scores on 2024/2025 are in-sample for the LR.
That biases hit rates OPTIMISTICALLY. Treat 2024/2025 numbers as an upper
bound on accuracy, and 2026 (post-fit-window games) as the honest check.
"""
from __future__ import annotations
import argparse, csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc            # noqa: E402
from calibration import CIRCalibrator  # noqa: E402

BT = ROOT / "data" / "backtests"
SOURCES = {
    "2024": [BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"],
    "2025": [BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"],
    "2026bt": [BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
               BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"],
}


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


# --------------------------------------------------------------------------
def load_backtest(paths):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    out = []
    for p in paths:
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                actual = (r.get("actual_side") or "").upper()
                if actual not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get("home", ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                out.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                            "y": 1 if actual == "NRFI" else 0,
                            "nrfi_odds": None, "src": p.name})
    _score(out, t1m, b1m)
    return out


def load_picks():
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    out = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            out.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                        "y": 1 if actual == "NRFI" else 0,
                        "nrfi_odds": fnum(r.get("market_nrfi_odds")),
                        "bet_placed": (r.get("bet_placed") or "").strip(),
                        "src": "picks_2026"})
    _score(out, t1m, b1m)
    return out


def _score(rows, t1m, b1m):
    if not rows:
        return
    Xt = np.asarray([r["t1"] for r in rows], dtype=float)
    Xb = np.asarray([r["b1"] for r in rows], dtype=float)
    raw = rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)
    for r, p in zip(rows, raw):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))


def calibrate(target, trainsets, knots=20):
    tr = [x for s in trainsets for x in s]
    cal = CIRCalibrator.fit([x["raw"] for x in tr], [x["y"] for x in tr],
                            knots, ["dd"])
    for r in target:
        r["p"] = float(cal.predict(r["raw"]))
    return cal


# --------------------------------------------------------------------------
def cell(rows, pgate, lamcap):
    sub = [r for r in rows if r["p"] >= pgate and r["lam"] <= lamcap]
    if not sub:
        return None
    n = len(sub)
    hits = sum(r["y"] for r in sub)
    return {"n": n, "hits": hits, "rate": hits / n, "rows": sub}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def day_bootstrap(sub, iters=4000, seed=11):
    """Block bootstrap over DAYS. Returns (lo, hi) on the NRFI hit rate."""
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(r["y"])
    days = list(byday.values())
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    k = len(days)
    out = []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        ys = [v for i in idx for v in days[i]]
        out.append(sum(ys) / len(ys))
    out.sort()
    return (out[int(0.025 * iters)], out[int(0.975 * iters)])


# --------------------------------------------------------------------------
PGATES = [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72]
LAMCAPS = [0.40, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56, 0.60, 99.0]


def grid_table(name, rows, breakeven, min_n=25):
    print(f"\n{'='*104}\n  GRID -- {name}   (n={len(rows)} graded, base NRFI rate="
          f"{sum(r['y'] for r in rows)/len(rows):.3f})")
    print(f"  cell value = actual NRFI rate; '.' = n<{min_n}. "
          f"break-even at typical price = {breakeven:.3f}")
    print("=" * 104)
    hdr = "  p_gate |" + "".join(f"{('lam<=' + (f'{c:.2f}' if c < 9 else 'inf')):>10}" for c in LAMCAPS)
    print(hdr)
    best = []
    for pg in PGATES:
        line = f"  {pg:>6.2f} |"
        for lc in LAMCAPS:
            c = cell(rows, pg, lc)
            if c is None or c["n"] < min_n:
                line += f"{'.':>10}"
            else:
                mark = "*" if c["rate"] >= breakeven else " "
                line += f"{c['rate']:>9.3f}{mark}"
                best.append((c["rate"], pg, lc, c["n"]))
        print(line)
    print("  (n per cell shown below)")
    for pg in PGATES:
        line = f"  {pg:>6.2f} |"
        for lc in LAMCAPS:
            c = cell(rows, pg, lc)
            line += f"{(c['n'] if c else 0):>10}"
        print(line)
    return best


def lam_only(name, rows, breakeven):
    print(f"\n  --- lambda ceiling ALONE (no p gate) -- {name} ---")
    print(f"  {'lam<=':>8}{'n':>7}{'NRFI%':>9}{'95% CI (day-block)':>24}{'vs BE':>9}")
    for lc in [0.35, 0.40, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56, 0.60, 0.65, 0.70, 99.0]:
        sub = [r for r in rows if r["lam"] <= lc]
        if len(sub) < 20:
            continue
        rate = sum(r["y"] for r in sub) / len(sub)
        lo, hi = day_bootstrap(sub)
        tag = "inf" if lc > 9 else f"{lc:.2f}"
        print(f"  {tag:>8}{len(sub):>7}{100*rate:>8.1f}%   [{100*lo:>5.1f}%,{100*hi:>5.1f}%]"
              f"{100*(rate-breakeven):>+8.1f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=25)
    args = ap.parse_args()

    s24 = load_backtest(SOURCES["2024"])
    s25 = load_backtest(SOURCES["2025"])
    s26 = load_picks()

    print("=" * 104)
    print("  NRFI SELECTION GEOMETRY SWEEP")
    print("=" * 104)
    print(f"  2024 backtest : {len(s24)} graded games")
    print(f"  2025 backtest : {len(s25)} graded games")
    print(f"  2026 picks    : {len(s26)} graded games")

    # ---- what does the book actually charge for NRFI? --------------------
    prices = [r["nrfi_odds"] for r in s26 if r["nrfi_odds"] is not None]
    print(f"\n  REAL captured DK NRFI prices in 2026: n={len(prices)}")
    if prices:
        prices_sorted = sorted(prices)
        med = prices_sorted[len(prices) // 2]
        print(f"    median {med:+.0f}   mean {st.mean(prices):+.1f}   "
              f"min {min(prices):+.0f}   max {max(prices):+.0f}")
        imps = [implied(o) for o in prices]
        print(f"    implied NRFI prob: median {st.median(imps):.4f}  mean {st.mean(imps):.4f}")
        for lo, hi, nm in [(-1000, -160, "<= -160"), (-160, -140, "-160..-141"),
                           (-140, -120, "-140..-121"), (-120, -100, "-120..-101"),
                           (-100, 1000, ">= +100")]:
            c = sum(1 for o in prices if lo < o <= hi or (nm == ">= +100" and o > 0))
            print(f"    {nm:<12}{c:>5}")
        BE = st.mean(imps)
    else:
        BE = 0.55
    print(f"\n  BREAK-EVEN USED = {BE:.4f}  (mean implied NRFI prob at real captured DK prices)")

    # ---- out-of-sample calibration --------------------------------------
    calibrate(s24, [s25])
    calibrate(s25, [s24])
    calibrate(s26, [s24, s25])

    ncells = len(PGATES) * len(LAMCAPS)
    print(f"\n  CELLS SEARCHED PER SEASON: {ncells}  ({len(PGATES)} p-gates x {len(LAMCAPS)} lambda caps)")
    print(f"  TOTAL across 3 seasons: {3*ncells}")

    b24 = grid_table("2024 (cal fit on 2025)", s24, BE, args.min_n)
    b25 = grid_table("2025 (cal fit on 2024)", s25, BE, args.min_n)
    b26 = grid_table("2026 picks (cal fit on 2024+2025)", s26, BE, args.min_n)

    lam_only("2024", s24, BE)
    lam_only("2025", s25, BE)
    lam_only("2026", s26, BE)

    # ---- cells that clear break-even in BOTH big seasons -----------------
    print(f"\n{'='*104}\n  CELLS CLEARING BREAK-EVEN ({BE:.3f}) IN *BOTH* 2024 AND 2025 "
          f"(n>={args.min_n} both)\n{'='*104}")
    print(f"  {'p_gate':>7}{'lam<=':>8}{'n24':>6}{'r24':>8}{'n25':>6}{'r25':>8}"
          f"{'n26':>6}{'r26':>8}{'2026 95%CI':>22}")
    survivors = []
    for pg in PGATES:
        for lc in LAMCAPS:
            c24, c25, c26 = cell(s24, pg, lc), cell(s25, pg, lc), cell(s26, pg, lc)
            if not (c24 and c25 and c24["n"] >= args.min_n and c25["n"] >= args.min_n):
                continue
            if c24["rate"] < BE or c25["rate"] < BE:
                continue
            lo, hi = day_bootstrap(c26["rows"]) if c26 and c26["n"] >= 10 else (float("nan"),) * 2
            tag = "inf" if lc > 9 else f"{lc:.2f}"
            print(f"  {pg:>7.2f}{tag:>8}{c24['n']:>6}{c24['rate']:>8.3f}"
                  f"{c25['n']:>6}{c25['rate']:>8.3f}"
                  f"{(c26['n'] if c26 else 0):>6}{(c26['rate'] if c26 else float('nan')):>8.3f}"
                  f"   [{100*lo:>5.1f}%,{100*hi:>5.1f}%]")
            survivors.append((pg, lc))
    if not survivors:
        print("  NONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

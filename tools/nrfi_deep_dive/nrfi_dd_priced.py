#!/usr/bin/env python3
"""tools/nrfi_dd_priced.py -- the only part of the NRFI question that can
answer PROFIT rather than accuracy: 2026 games with a REAL captured
DraftKings NRFI price.  Read-only.

Also resolves the production-gate geometry exactly:
    lambda_lr_total == -ln(raw_nrfi_prob)   (algebraic identity)
so a calibrated-p gate implies a lambda ceiling through the calibrator.
Prints where the shipped 0.62 gate and the shipped 0.52 ceiling actually sit
relative to each other.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc                       # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402

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


def load(paths, outcol, homecol, want_odds=False):
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
                rows.append({
                    "date": r.get("date", ""), "t1": tv, "b1": bv,
                    "y": 1 if (r.get(outcol) or "").upper() == "NRFI" else 0,
                    "odds": fnum(r.get("market_nrfi_odds")) if want_odds else None,
                })
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    raw = rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)
    for r, p in zip(rows, raw):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def day_boot(sub, key, iters=5000, seed=7):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(key(r))
    days = list(byday.values())
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    k = len(days)
    out = []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for i in idx for x in days[i]]
        out.append(sum(v) / len(v))
    out.sort()
    return out[int(0.025 * iters)], out[int(0.975 * iters)]


def main():
    picks = load([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team", True)
    prod = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    for r in picks:
        r["p_prod"] = float(prod.predict(r["raw"]))

    # ---- 1. gate geometry ------------------------------------------------
    print("=" * 100)
    print("  1. THE TWO KNOBS ARE THE SAME KNOB")
    print("=" * 100)
    print("  IDENTITY:  lambda_lr_total = -ln(P_t1_norun) - ln(P_b1_norun) = -ln(raw_nrfi_p)")
    print("  => a lambda CEILING is exactly a RAW-probability FLOOR:  lam<=C  <=>  raw>=exp(-C)")
    for C in (0.44, 0.48, 0.50, 0.52, 0.56, 0.60):
        print(f"       lambda <= {C:.2f}   <=>   raw_p_nrfi >= {math.exp(-C):.4f}")
    lo, hi = 0.30, 0.95
    for _ in range(60):
        mid = (lo + hi) / 2
        if prod.predict(mid) >= 0.62:
            hi = mid
        else:
            lo = mid
    print(f"\n  Production calibrator: calibrated p >= 0.62 requires raw >= {hi:.4f}")
    print(f"                         which is lambda <= {-math.log(hi):.4f}")
    print(f"  Shipped ceiling _LR_LAMBDA_NRFI_CEILING = 0.52  (raw >= {math.exp(-0.52):.4f})")
    print("  => under the old 0.62 gate the 0.52 ceiling was NEVER BINDING: every game that")
    print("     cleared p>=0.62 already had lambda below the ceiling.")
    print(f"  Calibrator's TOP knot value = {max(prod.rates):.4f} (raw >= ~0.637 all map here)")

    n62 = sum(1 for r in picks if r["p_prod"] >= 0.62)
    n56 = sum(1 for r in picks if r["p_prod"] >= 0.56)
    print(f"\n  2026 graded games (n={len(picks)}) reaching the production calibrated p:")
    for g in (0.50, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66):
        s = [r for r in picks if r["p_prod"] >= g]
        pr = [r for r in s if r["odds"] is not None]
        rate = (sum(r["y"] for r in s) / len(s)) if s else float("nan")
        print(f"     p_prod >= {g:.2f}:  n={len(s):>4}  real-priced={len(pr):>4}  "
              f"actual NRFI={100*rate:>5.1f}%")

    # ---- 2. real-priced economics ---------------------------------------
    priced = [r for r in picks if r["odds"] is not None]
    print("\n" + "=" * 100)
    print(f"  2. REAL-PRICED 2026 NRFI ECONOMICS  (n={len(priced)} games with a captured DK NRFI line)")
    print("=" * 100)
    print("  flat 1u on NRFI in every cell. 'need' = mean implied prob of the prices actually paid.")
    print(f"  {'selection':<34}{'n':>5}{'days':>6}{'hit%':>7}{'need%':>7}"
          f"{'P/L u':>9}{'ROI%':>8}{'ROI 95% CI (day-block)':>26}")

    def report(label, sub):
        if len(sub) < 5:
            print(f"  {label:<34}{len(sub):>5}   -- too few to report")
            return
        n = len(sub)
        hit = sum(r["y"] for r in sub) / n
        need = st.mean([implied(r["odds"]) for r in sub])
        pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
        roi = pl / n
        lo_, hi_ = day_boot(sub, lambda r: (payout(r["odds"]) if r["y"] else -1.0))
        days = len({r["date"] for r in sub})
        print(f"  {label:<34}{n:>5}{days:>6}{100*hit:>7.1f}{100*need:>7.1f}"
              f"{pl:>+9.2f}{100*roi:>+8.1f}   [{100*lo_:>+6.1f}%,{100*hi_:>+6.1f}%]")

    report("ALL games (bet every NRFI)", priced)
    for g in (0.50, 0.54, 0.56, 0.58, 0.60, 0.62):
        report(f"p_prod >= {g:.2f}", [r for r in priced if r["p_prod"] >= g])
    for c in (0.44, 0.48, 0.50, 0.52, 0.56, 0.60, 0.65):
        report(f"lambda <= {c:.2f}  (any p)", [r for r in priced if r["lam"] <= c])
    report("p>=0.56 AND lambda<=0.52",
           [r for r in priced if r["p_prod"] >= 0.56 and r["lam"] <= 0.52])
    report("p>=0.58 AND lambda<=0.50",
           [r for r in priced if r["p_prod"] >= 0.58 and r["lam"] <= 0.50])

    # ---- 3. does the book price the model's NRFI picks WORSE? ------------
    print("\n" + "=" * 100)
    print("  3. WHAT THE BOOK CHARGES AS THE MODEL GETS MORE CONFIDENT")
    print("=" * 100)
    print(f"  {'lambda band':<18}{'n':>6}{'mean DK NRFI price':>22}{'implied%':>10}"
          f"{'actual NRFI%':>14}{'edge pp':>9}")
    bands = [(0, 0.48), (0.48, 0.55), (0.55, 0.62), (0.62, 0.70), (0.70, 0.80),
             (0.80, 0.95), (0.95, 9)]
    for lo_, hi_ in bands:
        sub = [r for r in priced if lo_ <= r["lam"] < hi_]
        if len(sub) < 10:
            continue
        mo = st.mean([r["odds"] for r in sub])
        mi = st.mean([implied(r["odds"]) for r in sub])
        ac = sum(r["y"] for r in sub) / len(sub)
        print(f"  {f'{lo_:.2f}-{hi_:.2f}':<18}{len(sub):>6}{mo:>+22.1f}{100*mi:>10.1f}"
              f"{100*ac:>14.1f}{100*(ac-mi):>+9.1f}")

    # ---- 4. same lambda sweep, all four data sources ---------------------
    print("\n" + "=" * 100)
    print("  4. LAMBDA CEILING ALONE -- DOES IT REPLICATE ACROSS SEASONS?")
    print("     (lambda needs NO calibrator, so this comparison is calibrator-free)")
    print("=" * 100)
    srcs = {
        "2024bt": load([BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"], "actual_side", "home"),
        "2025bt": load([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home"),
        "2026bt": load([BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
                        BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"], "actual_side", "home"),
        "2026picks": picks,
    }
    names = list(srcs)
    print(f"  {'lambda<=':<12}" + "".join(f"{n:>16}" for n in names))
    print(f"  {'(base rate)':<12}" +
          "".join(f"{100*sum(r['y'] for r in srcs[n])/len(srcs[n]):>13.1f}%   " for n in names))
    for c in (0.44, 0.48, 0.50, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80):
        line = f"  {c:<12.2f}"
        for n in names:
            sub = [r for r in srcs[n] if r["lam"] <= c]
            line += (f"{100*sum(r['y'] for r in sub)/len(sub):>8.1f}% ({len(sub):>4})"
                     if len(sub) >= 15 else f"{'--':>16}")
        print(line)
    print("\n  DELTA vs that season's own base rate (pp) -- this is the signal, sign matters:")
    print(f"  {'lambda<=':<12}" + "".join(f"{n:>16}" for n in names))
    for c in (0.44, 0.48, 0.50, 0.52, 0.56, 0.60, 0.65, 0.70):
        line = f"  {c:<12.2f}"
        for n in names:
            base = sum(r["y"] for r in srcs[n]) / len(srcs[n])
            sub = [r for r in srcs[n] if r["lam"] <= c]
            line += (f"{100*(sum(r['y'] for r in sub)/len(sub)-base):>+15.1f} "
                     if len(sub) >= 15 else f"{'--':>16}")
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

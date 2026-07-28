#!/usr/bin/env python3
"""
Part C -- remove every confound that could be unfairly favouring the incumbent.

The production two-stage LR was trained on 2024+2025+2026YTD, so it has
partly SEEN both test seasons.  Comparing it to a from-scratch specialist
is biased in its favour.  So here the comparison is GENERALIST vs
SPECIALIST, identical architecture (flat 31-d LogReg), identical features,
identical L2 -- the ONLY difference is the training set:

    generalist  = fit on ALL train-season rows
    specialist  = fit on the train-season rows inside the region only

If the proposal's mechanism ("different features matter inside the region")
is real, the specialist must beat the generalist on the test season's
region rows.  Four out-of-sample directions, plus a within-season time
split that no earlier search touched.
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))

import recalibrate_v2 as rc                       # noqa: E402
from lr_baseline import LogReg                    # noqa: E402
from calibration import ProbCalibrator            # noqa: E402
from regime_specialist import load, auc, day_block_boot, UNION_NAMES  # noqa: E402

HI = 0.50
L2 = 1.0


def fit(rows, l2=L2):
    return LogReg.fit(np.asarray([r["u"] for r in rows], float),
                      np.asarray([r["y"] for r in rows], float),
                      UNION_NAMES, l2=l2)


def score(m, rows):
    return m.predict_proba(np.asarray([r["u"] for r in rows], float))


def cell(lab, TRall, TE, region):
    """region: 'high' or 'low' -- both TRall and TE are full seasons."""
    sel = (lambda r: r["p"] >= HI) if region == "high" else (lambda r: r["p"] < HI)
    TRreg = [r for r in TRall if sel(r)]
    TEreg = [r for r in TE if sel(r)]
    g = fit(TRall)
    s = fit(TRreg)
    sg, ss = score(g, TEreg), score(s, TEreg)
    for r, a, b in zip(TEreg, sg, ss):
        r["_g"], r["_s"] = float(a), float(b)
    a_g = auc([r["y"] for r in TEreg], sg)
    a_s = auc([r["y"] for r in TEreg], ss)
    ci = day_block_boot(TEreg, lambda rr:
                        auc([r["y"] for r in rr], [r["_s"] for r in rr])
                        - auc([r["y"] for r in rr], [r["_g"] for r in rr]), B=1500)
    print(f"  {lab:<40}{len(TEreg):>6}{a_g:>11.4f}{a_s:>12.4f}{a_s - a_g:>+9.4f}"
          f"   [{ci[0]:+.4f},{ci[1]:+.4f}]")
    return a_s - a_g


def main():
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    d25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv", 2025)
    d26 = load("data/picks_2026.csv", 2026)
    for d in (d25, d26):
        for r in d:
            r["p"] = float(cal.predict(r["raw"]))

    print("=" * 96)
    print("  C. GENERALIST vs SPECIALIST -- identical architecture/features/L2,")
    print("     only the TRAINING SET differs.  No incumbent advantage possible.")
    print("=" * 96)
    print(f"  {'cell':<40}{'n':>6}{'gen AUC':>11}{'spec AUC':>12}{'delta':>9}"
          f"   95% CI on delta")
    deltas = []
    for region in ("high", "low"):
        deltas.append(cell(f"{region}: train 2025 -> test 2026", d25, d26, region))
        deltas.append(cell(f"{region}: train 2026 -> test 2025", d26, d25, region))

    # within-2025 time split -- a direction nothing has been searched on
    mid = sorted({r["date"] for r in d25})[len({r["date"] for r in d25}) // 2]
    h1 = [r for r in d25 if r["date"] < mid]
    h2 = [r for r in d25 if r["date"] >= mid]
    print(f"\n  within-2025 time split at {mid}: n1={len(h1)} n2={len(h2)}")
    for region in ("high", "low"):
        deltas.append(cell(f"{region}: 2025 H1 -> 2025 H2", h1, h2, region))
    mid6 = sorted({r["date"] for r in d26})[len({r["date"] for r in d26}) // 2]
    h1b = [r for r in d26 if r["date"] < mid6]
    h2b = [r for r in d26 if r["date"] >= mid6]
    print(f"\n  within-2026 time split at {mid6}: n1={len(h1b)} n2={len(h2b)}")
    for region in ("high", "low"):
        deltas.append(cell(f"{region}: 2026 H1 -> 2026 H2", h1b, h2b, region))

    print(f"\n  cells: {len(deltas)}   positive: {sum(1 for d in deltas if d > 0)}"
          f"   mean delta: {np.mean(deltas):+.4f}")

    # ---- does the specialist even LEARN anything? in-sample sanity --------
    print("\n" + "=" * 96)
    print("  C2. IN-SAMPLE sanity -- is the specialist fitting noise or signal?")
    print("=" * 96)
    for name, d in (("2025", d25), ("2026", d26)):
        reg = [r for r in d if r["p"] >= HI]
        s = fit(reg)
        print(f"  {name} high region n={len(reg)}: in-sample AUC "
              f"{auc([r['y'] for r in reg], score(s, reg)):.4f}   "
              f"(out-of-sample above: ~0.50)")

    # ---- region-boundary sensitivity: is 0.50 the wrong cut? -------------
    print("\n" + "=" * 96)
    print("  C3. REGION-BOUNDARY sweep -- maybe 0.50 is simply the wrong cut")
    print("=" * 96)
    print(f"  {'cut':>6}{'n(2026)':>9}{'gen':>9}{'spec':>9}{'delta':>9}"
          f"{'  |  n(2025)':>12}{'gen':>9}{'spec':>9}{'delta':>9}")
    searched = 0
    for cut in (0.48, 0.50, 0.52, 0.54, 0.56, 0.58):
        out = ""
        for TRall, TE in ((d25, d26), (d26, d25)):
            TR = [r for r in TRall if r["p"] >= cut]
            TEr = [r for r in TE if r["p"] >= cut]
            if len(TR) < 60 or len(TEr) < 60:
                out += f"{len(TEr):>9}{'--':>9}{'--':>9}{'--':>9}"
                continue
            g, s = fit(TRall), fit(TR)
            a_g = auc([r["y"] for r in TEr], score(g, TEr))
            a_s = auc([r["y"] for r in TEr], score(s, TEr))
            out += f"{len(TEr):>9}{a_g:>9.4f}{a_s:>9.4f}{a_s - a_g:>+9.4f}"
            searched += 1
        print(f"  {cut:>6}{out}")
    print(f"  cells searched: {searched}  (total search exposure across B+C "
          f"is reported in the summary)")


if __name__ == "__main__":
    main()

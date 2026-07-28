#!/usr/bin/env python3
"""
Part D -- price the SINGLE BEST-LOOKING cell in the whole search.

C3 found apparent positive AUC deltas for the specialist at cuts
0.54/0.56/0.58 on 2026 (+0.0707/+0.0761/+0.0895).  Those are the only
cells in ~50 searched that look like a win.  Two questions:

  D1. Do they replicate on 2025 (the direction not searched)?  [C3 says no]
  D2. Do they make MONEY at real captured DK prices, and do they survive
      10 cents of worse pricing?

Also: is the apparent lift the SPECIALIST rising, or the GENERALIST
collapsing on a tiny subset?
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))

from lr_baseline import LogReg                    # noqa: E402
from calibration import ProbCalibrator            # noqa: E402
from regime_specialist import (load, auc, day_block_boot,  # noqa: E402
                               UNION_NAMES, payout)

L2 = 1.0


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def fit(rows):
    return LogReg.fit(np.asarray([r["u"] for r in rows], float),
                      np.asarray([r["y"] for r in rows], float),
                      UNION_NAMES, l2=L2)


def sc(m, rows):
    return m.predict_proba(np.asarray([r["u"] for r in rows], float))


def main():
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    d25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv", 2025)
    d26 = load("data/picks_2026.csv", 2026)
    for d in (d25, d26):
        for r in d:
            r["p"] = float(cal.predict(r["raw"]))

    print("=" * 94)
    print("  D1. Is the C3 'lift' the specialist rising or the generalist collapsing?")
    print("=" * 94)
    print(f"  {'cut':>6}{'season':>8}{'n':>6}{'incumbent':>11}{'generalist':>12}"
          f"{'specialist':>12}")
    for cut in (0.50, 0.54, 0.56, 0.58):
        for name, TRall, TE in (("2026", d25, d26), ("2025", d26, d25)):
            TR = [r for r in TRall if r["p"] >= cut]
            TEr = [r for r in TE if r["p"] >= cut]
            if len(TR) < 60 or len(TEr) < 60:
                continue
            a_inc = auc([r["y"] for r in TEr], [r["raw"] for r in TEr])
            a_g = auc([r["y"] for r in TEr], sc(fit(TRall), TEr))
            a_s = auc([r["y"] for r in TEr], sc(fit(TR), TEr))
            print(f"  {cut:>6}{name:>8}{len(TEr):>6}{a_inc:>11.4f}{a_g:>12.4f}"
                  f"{a_s:>12.4f}")

    print("\n" + "=" * 94)
    print("  D2. MONEY on the best-looking cells -- 2025-trained specialist,")
    print("      2026 region rows with a REAL captured DK NRFI price, flat 1u")
    print("=" * 94)
    print(f"  {'cut / selection':<32}{'n':>5}{'hit%':>7}{'need%':>7}{'units':>9}"
          f"{'ROI%':>8}{'-10c ROI%':>11}   95% CI ROI%")
    for cut in (0.50, 0.54, 0.56, 0.58):
        TR = [r for r in d25 if r["p"] >= cut]
        TEr = [r for r in d26 if r["p"] >= cut]
        if len(TR) < 60 or len(TEr) < 60:
            continue
        m = fit(TR)
        for r, s in zip(TEr, sc(m, TEr)):
            r["_s"] = float(s)
        priced = [r for r in TEr if r["nrfi_odds"] is not None]
        for frac, lab in ((1.0, "all"), (0.5, "top50%"), (0.25, "top25%")):
            k = max(1, int(len(priced) * frac))
            sub = sorted(priced, key=lambda r: -r["_s"])[:k]
            pnl = sum(payout(r["nrfi_odds"]) if r["y"] else -1.0 for r in sub)
            pnl10 = sum(payout(r["nrfi_odds"] - 10) if r["y"] else -1.0 for r in sub)
            hit = 100 * np.mean([r["y"] for r in sub])
            need = 100 * np.mean([implied(r["nrfi_odds"]) for r in sub])
            ci = day_block_boot(
                sub, lambda rr: 100 * sum(payout(r["nrfi_odds"]) if r["y"] else -1.0
                                          for r in rr) / len(rr), B=1500)
            print(f"  {f'{cut} / {lab}':<32}{len(sub):>5}{hit:>7.1f}{need:>7.1f}"
                  f"{pnl:>+9.2f}{100*pnl/len(sub):>+8.2f}"
                  f"{100*pnl10/len(sub):>+11.2f}   [{ci[0]:+.1f},{ci[1]:+.1f}]")

    print("\n" + "=" * 94)
    print("  D3. Does ANY specialist selection beat break-even on real prices?")
    print("=" * 94)
    beat = 0
    tot = 0
    for cut in (0.50, 0.52, 0.54, 0.56, 0.58):
        TR = [r for r in d25 if r["p"] >= cut]
        TEr = [r for r in d26 if r["p"] >= cut]
        if len(TR) < 60 or len(TEr) < 60:
            continue
        m = fit(TR)
        for r, s in zip(TEr, sc(m, TEr)):
            r["_s"] = float(s)
        priced = [r for r in TEr if r["nrfi_odds"] is not None]
        for frac in (1.0, 0.75, 0.5, 0.33, 0.25, 0.15, 0.10):
            k = max(1, int(len(priced) * frac))
            sub = sorted(priced, key=lambda r: -r["_s"])[:k]
            pnl = sum(payout(r["nrfi_odds"]) if r["y"] else -1.0 for r in sub)
            tot += 1
            if pnl > 0:
                beat += 1
                print(f"    POSITIVE: cut {cut} top {int(frac*100)}%  n={len(sub)}"
                      f"  {pnl:+.2f}u  ROI {100*pnl/len(sub):+.1f}%")
    print(f"  {beat} of {tot} specialist selections were profitable at real prices.")


if __name__ == "__main__":
    main()

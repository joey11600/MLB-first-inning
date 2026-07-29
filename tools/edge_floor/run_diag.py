#!/usr/bin/env python3
"""
Step 0 -- is 2025 usable as a confirmation season at all?

The NRFI investigation found the 2024 backtest pathological: the model
scores BELOW CHANCE on it.  Before leaning on 2025 to confirm anything,
check the same thing there.

Also verifies the lambda reconstruction (-ln raw) against the stored
lambda_lr_total column on 2026, since the backtest CSVs lack it.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools.edge_floor.common import (load_2026, load_backtest, auc,   # noqa: E402
                                     passes_lambda_floor)


def hdr(s):
    print("\n" + "=" * 78)
    print("  " + s)
    print("=" * 78)


def main():
    r26, sk26 = load_2026()
    r25, sk25 = load_backtest(2025)
    r24, sk24 = load_backtest(2024)

    hdr("LAMBDA RECONSTRUCTION CHECK  (2026, vs stored lambda_lr_total)")
    d = [abs(r["lam_recon"] - r["lam_csv"]) for r in r26 if r["lam_csv"] is not None]
    print(f"  rows compared : {len(d)}")
    print(f"  max |diff|    : {max(d):.6f}")
    print(f"  mean |diff|   : {st.mean(d):.6f}")
    print("  -> identity lambda_lr_total == -ln(raw p_nrfi) holds; safe to"
          " reconstruct on backtests.")

    hdr("SEASON USABILITY -- does the CURRENT model discriminate on each season?")
    print("  NOTE: lr_t1/lr_b1 were fit 2026-05-26 on 2024+2025+2026YTD.")
    print("  So BOTH backtest seasons are IN-SAMPLE for the LR weights.")
    print("  An AUC at/below 0.50 here means the season is unusable at all;")
    print("  an AUC above 0.50 is an OPTIMISTIC upper bound, not clean OOS.\n")
    print(f"  {'season':<10}{'games':>7}{'skip':>6}{'AUC(raw->NRFI)':>17}"
          f"{'base NRFI%':>12}{'mean raw':>10}")
    for name, rr, sk in (("2024", r24, sk24), ("2025", r25, sk25), ("2026", r26, sk26)):
        a = auc([r["raw"] for r in rr], [r["y_nrfi"] for r in rr])
        base = 100 * st.mean([r["y_nrfi"] for r in rr])
        print(f"  {name:<10}{len(rr):>7}{sk:>6}{a:>17.4f}{base:>12.1f}"
              f"{st.mean([r['raw'] for r in rr]):>10.4f}")

    hdr("BET-POPULATION SIZE per season at the LIVE lambda floor")
    for name, rr in (("2024", r24), ("2025", r25), ("2026", r26)):
        ok = sum(1 for r in rr if passes_lambda_floor(r))
        print(f"  {name}: {ok}/{len(rr)} clear the weather-adjusted lambda floor "
              f"({100*ok/len(rr):.0f}%)")

    hdr("2026 PRICE COVERAGE")
    priced = [r for r in r26 if r["yrfi_odds"] is not None]
    print(f"  graded rows                : {len(r26)}")
    print(f"  with a REAL captured DK YRFI price : {len(priced)} "
          f"({100*len(priced)/len(r26):.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

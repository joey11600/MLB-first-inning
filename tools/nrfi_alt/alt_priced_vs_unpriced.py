#!/usr/bin/env python3
"""
tools/nrfi_alt/alt_priced_vs_unpriced.py

Final attack. In pass 2 the 2026 deep slice hit 56.86% over ALL graded games
but only 52.21% over the games with a REAL captured DK NRFI price. If the
deep-slice "strength" lives in the unpriced games, it is not bettable and the
whole ladder argument is an artefact of which games DraftKings quoted.

Also re-runs the fine sweep on RAW-p ranking to confirm the 0/80 result is not
a calibrator artefact, and reports what hit rate the deepest slice would need.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))

from alt_depth_ladder import load_rows, payout, implied, day_bootstrap  # noqa
from calibration import ProbCalibrator  # noqa: E402


def main():
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    rows26, _ = load_rows(ROOT / "data" / "picks_2026.csv", "2026")
    for r in rows26:
        r["p"] = cal.predict(r["raw"])

    print("=" * 100)
    print("  PRICED vs UNPRICED -- is the deep-slice edge bettable at all?")
    print("=" * 100)
    allrows = sorted(rows26, key=lambda r: -r["p"])
    print(f"    {'depth':>7}{'n all':>7}{'hit% all':>10}{'n priced':>10}"
          f"{'hit% priced':>13}{'n unpriced':>12}{'hit% unpriced':>15}")
    for d in (0.50, 0.30, 0.20, 0.15, 0.10, 0.05, 0.02):
        k = max(1, int(round(d * len(allrows))))
        sub = allrows[:k]
        pr = [r for r in sub if r["nrfi_odds"] is not None]
        un = [r for r in sub if r["nrfi_odds"] is None]
        f = lambda xs: 100 * np.mean([r["y_nrfi"] for r in xs]) if xs else float("nan")
        print(f"    {d:>7.0%}{k:>7}{f(sub):>10.2f}{len(pr):>10}{f(pr):>13.2f}"
              f"{len(un):>12}{f(un):>15.2f}")

    print("\n  Base rates:")
    pr_all = [r for r in rows26 if r["nrfi_odds"] is not None]
    un_all = [r for r in rows26 if r["nrfi_odds"] is None]
    print(f"    priced   n={len(pr_all):>5}  NRFI rate {100*np.mean([r['y_nrfi'] for r in pr_all]):.2f}%")
    print(f"    unpriced n={len(un_all):>5}  NRFI rate {100*np.mean([r['y_nrfi'] for r in un_all]):.2f}%")

    print("\n" + "=" * 100)
    print("  FINE SWEEP ON RAW-p RANKING (confirms 0/80 is not a calibrator artefact)")
    print("=" * 100)
    p26 = sorted(pr_all, key=lambda r: -r["raw"])
    pos = sig = 0
    best = (-9e9, None)
    for pct in range(1, 81):
        k = max(1, int(round(pct / 100.0 * len(p26))))
        sub = p26[:k]
        recs = [{"date": r["date"],
                 "pnl": payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0}
                for r in sub]
        roi = float(np.mean([x["pnl"] for x in recs]))
        lo, _ = day_bootstrap(recs, lambda x: x["pnl"], n_boot=1500)
        pos += roi > 0
        sig += lo > 0
        if roi > best[0]:
            best = (roi, pct, k)
    print(f"  cells searched: 80   positive: {pos}   CI-excludes-0: {sig}")
    print(f"  least-bad cell: depth {best[1]}%  n={best[2]}  ROI {100*best[0]:+.2f}%")

    print("\n" + "=" * 100)
    print("  WHAT WOULD IT TAKE?  required hit rate vs delivered, real prices")
    print("=" * 100)
    p26c = sorted(pr_all, key=lambda r: -r["p"])
    print(f"    {'depth':>7}{'n':>6}{'delivered%':>12}{'needed%':>10}{'shortfall pp':>14}")
    for d in (0.50, 0.30, 0.20, 0.15, 0.10, 0.05, 0.02):
        k = max(1, int(round(d * len(p26c))))
        sub = p26c[:k]
        hit = 100 * np.mean([r["y_nrfi"] for r in sub])
        need = 100 * np.mean([implied(r["nrfi_odds"]) for r in sub])
        print(f"    {d:>7.0%}{k:>6}{hit:>12.2f}{need:>10.2f}{hit-need:>+14.2f}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

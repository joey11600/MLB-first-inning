#!/usr/bin/env python3
"""
tools/edge_floor/wf_2025_rank.py -- the 2025 replication, done the only
way 2025 permits.

WHY THE OBVIOUS VERSION DOES NOT RUN.  Pushing the 2025 backtest through
the production feature stack yields a raw model output whose spread is
half the 2026 spread (sd 0.045 vs 0.077) and whose centre is higher
(0.508 vs 0.467).  Applying the live gate p_nrfi < 0.40 to it selects
TWO games out of 2393.  The 2025 file is also missing the umpire feature
entirely.  So the live rule cannot be replayed on 2025 at all; the two
seasons' score distributions are not on the same footing.

WHAT CAN STILL BE ASKED.  Whether the ORDERING carries signal: take the
same top slice of each season by model confidence, then ask whether
sorting further by edge (equivalently, under a constant assumed price,
by probability) lifts the hit rate in both.  This is a hit-rate test.
There are no 2025 prices, so it is NOT a profit test.

Usage:  python tools/edge_floor/wf_2025_rank.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import mlb_first_inning_predictor as P                       # noqa: E402
from calibration import CIRCalibrator                        # noqa: E402
from tools.edge_floor.wf_2025 import load_2025               # noqa: E402
from tools.edge_floor.wf_common import universe, implied     # noqa: E402


def lam_ok(x):
    fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, x["wx_temp"],
                                   x["wx_wind"], x["wx_dome"])
    return x["lambda"] is None or x["lambda"] >= fl


def main():
    r25, _ = load_2025()
    r26, _, _ = universe()
    cal26 = CIRCalibrator.fit([x["raw"] for x in r26], [x["y_nrfi"] for x in r26],
                              20, ["2026"])
    cal25 = CIRCalibrator.fit([x["raw"] for x in r25], [x["y_nrfi"] for x in r25],
                              20, ["2025"])

    print("=" * 96)
    print("  2025 vs 2026 -- RANK-MATCHED. Hit rate only; 2025 has no prices.")
    print("=" * 96)
    print("  The live gate cannot be replayed on 2025: only 2 of 2393 games clear")
    print("  p_nrfi < 0.40 once the 2025 file is pushed through the current feature")
    print("  stack (its raw spread is half of 2026's, and the umpire feature is")
    print("  absent from the file). So both seasons are sliced by RANK instead.")

    pools = {}
    for tag, rr, cal in (("2025", r25, cal25), ("2026", r26, cal26)):
        pool = [x for x in rr if lam_ok(x)]
        for x in pool:
            x["p_nrfi_c"] = cal.predict(x["raw"])
        pool.sort(key=lambda x: x["p_nrfi_c"])       # most YRFI-confident first
        pools[tag] = pool
        print(f"\n  {tag}: {len(pool)} games pass the lambda floor "
              f"(of {len(rr)} graded)")

    print("\n  Hit rate on the top-K% most YRFI-confident games in each season.")
    print("  Under a constant assumed price this IS the edge floor, exactly.")
    print(f"\n  {'top':>6}{'2025 n':>9}{'2025 YRFI%':>12}{'2026 n':>9}"
          f"{'2026 YRFI%':>12}{'both lift?':>12}")
    base = {t: 100 * sum(x["yrfi_hit"] for x in pools[t]) / len(pools[t])
            for t in pools}
    print(f"  {'all':>6}{len(pools['2025']):>9}{base['2025']:>11.1f}%"
          f"{len(pools['2026']):>9}{base['2026']:>11.1f}%")
    for frac in (0.30, 0.20, 0.15, 0.10, 0.06, 0.03):
        line = [f"  {frac:>6.0%}"]
        lifts = []
        for t in ("2025", "2026"):
            k = max(int(frac * len(pools[t])), 5)
            sub = pools[t][:k]
            h = 100 * sum(x["yrfi_hit"] for x in sub) / len(sub)
            lifts.append(h - base[t])
            line.append(f"{len(sub):>9}{h:>11.1f}%")
        both = "yes" if all(v > 0 for v in lifts) else "no"
        line.append(f"{both:>12}")
        print("".join(line))

    print("\n  READ: a monotone climb in BOTH columns means the model's ordering")
    print("  carries real signal -- which is a statement about the PROBABILITY")
    print("  gate, not about edge. Edge only adds information beyond that to the")
    print("  extent DK's price varies across bets, and on the 2026 live-rule")
    print("  sample the price term carries half the variance of the model term.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

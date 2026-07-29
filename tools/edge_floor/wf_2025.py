#!/usr/bin/env python3
"""
tools/edge_floor/wf_2025.py -- does the edge lift replicate on 2025?

THE STRUCTURAL POINT, WHICH MATTERS MORE THAN THE NUMBERS
---------------------------------------------------------
The 2025 backtest has NO odds.  Testing an "edge floor" there requires
assuming a price.  But with a CONSTANT assumed price c,

    edge = p_yrfi - implied(c)  >=  f     <=>     p_yrfi  >=  f + implied(c)

i.e. an edge floor collapses EXACTLY into a probability gate.  So 2025
cannot test the edge idea at all.  It can only test whether tightening
the PROBABILITY gate lifts the hit rate -- which is a different (and
already-shipped) knob.  This is not a profit test; there are no prices.

That collapse is itself the most useful finding: it shows the edge floor
only differs from a probability gate to the extent DK prices VARY across
the bets.  Section B measures how much variation there actually is in
the live 2026 sample.

Usage:  python tools/edge_floor/wf_2025.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc                 # noqa: E402
import mlb_first_inning_predictor as P      # noqa: E402
from calibration import CIRCalibrator       # noqa: E402
from tools.edge_floor.wf_common import (    # noqa: E402
    universe, live_bets, implied, flat_stats)

BT = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"


def load_2025():
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    skipped = 0
    with open(BT, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            g = (r.get("actual_side") or "").upper()
            if g not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                skipped += 1
                continue

            def fn(k):
                try:
                    return float(r.get(k) or "")
                except ValueError:
                    return None
            rows.append({"date": r["date"], "t1": tv, "b1": bv,
                         "yrfi_hit": g == "YRFI", "y_nrfi": int(g == "NRFI"),
                         "lambda": fn("lambda_total") or fn("lambda_lr_total"),
                         "wx_temp": fn("wx_temp_c"), "wx_wind": fn("wx_wind_kmh"),
                         "wx_dome": bool(fn("wx_is_dome") or 0)})
    Xt = np.asarray([x["t1"] for x in rows], dtype=float)
    Xb = np.asarray([x["b1"] for x in rows], dtype=float)
    for x, p in zip(rows, rc.lr_predict_two_stage(t1, b1, Xt, Xb)):
        x["raw"] = float(p)
    return rows, skipped


def main():
    print("=" * 100)
    print("  2025 REPLICATION -- HIT RATE ONLY. There are no prices in this file,")
    print("  so nothing below is a profit claim.")
    print("=" * 100)

    r25, skipped = load_2025()
    print(f"  2025 graded games rebuilt through the production feature stack: "
          f"{len(r25)} (skipped {skipped})")

    # Calibrator fit on 2026 ONLY, applied to 2025 -> genuinely out of sample
    r26, _, _ = universe()
    cal = CIRCalibrator.fit([x["raw"] for x in r26], [x["y_nrfi"] for x in r26],
                            20, ["2026-only"])
    for x in r25:
        x["p_nrfi"] = cal.predict(x["raw"])
    print("  calibrator: CIR fit on 2026 only, applied to 2025 (out of sample)")

    # live-rule population on 2025
    live = []
    for x in r25:
        fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, x["wx_temp"],
                                       x["wx_wind"], x["wx_dome"])
        if x["lambda"] is not None and x["lambda"] < fl:
            continue
        if x["p_nrfi"] >= P._LR_STRONG_YRFI_P:
            continue
        live.append(x)
    print(f"  games the LIVE rule would have bet in 2025: {len(live)}")

    print("\n  A.  under an assumed constant price, an edge floor IS a probability")
    print("      gate. Shown at three assumed prices so the equivalence is visible.")
    print(f"\n  {'assumed':>9}{'floor':>7}{'== p_yrfi >=':>14}{'bets':>7}{'hit%':>8}"
          f"{'need%':>8}{'lift vs no floor':>18}")
    for price in (-125, -110, -140):
        imp = implied(price)
        base = flat_stats([{"odds": price, "win": x["yrfi_hit"]} for x in live])
        for f in (0.00, 0.04, 0.08, 0.12):
            keep = [x for x in live if (1 - x["p_nrfi"]) - imp >= f]
            if len(keep) < 5:
                continue
            st = flat_stats([{"odds": price, "win": x["yrfi_hit"]} for x in keep])
            print(f"  {price:>9}{f:>7.2f}{imp+f:>14.3f}{st['bets']:>7}{st['hit']:>8.1f}"
                  f"{100*imp:>8.1f}{st['hit']-base['hit']:>+17.1f}pp")
        print()

    print("  B.  is the 2026 lift a PRICE effect or a PROBABILITY effect?")
    print("      If prices barely vary, the edge floor is a probability gate with")
    print("      extra steps. Measured on the live-rule 2026 bets:")
    rows26, ins, wf = universe()
    b26 = live_bets(rows26, wf)
    imps = sorted(implied(b["odds"]) for b in b26)
    import statistics as st
    print(f"        implied prob of the offered price: mean {st.mean(imps):.3f}, "
          f"sd {st.pstdev(imps):.3f}, range {imps[0]:.3f}..{imps[-1]:.3f}")
    ps = sorted(b["p"] for b in b26)
    print(f"        model prob p_yrfi                : mean {st.mean(ps):.3f}, "
          f"sd {st.pstdev(ps):.3f}, range {ps[0]:.3f}..{ps[-1]:.3f}")
    es = [b["edge"] for b in b26]
    print(f"        edge                             : sd {st.pstdev(es):.3f}")
    print(f"\n      variance decomposition of edge: var(p) {st.pvariance(ps):.5f}, "
          f"var(implied) {st.pvariance(imps):.5f}")
    print("      The larger term is what an 'edge floor' is really sorting on.")

    print("\n  C.  2025 hit-rate lift for the PROBABILITY gate alone (no price at all)")
    print(f"  {'p_nrfi <':>10}{'bets':>7}{'hit%':>8}{'lift':>9}")
    base = flat_stats([{"odds": -110, "win": x["yrfi_hit"]} for x in live])
    for g in (0.40, 0.36, 0.32, 0.28, 0.24):
        keep = [x for x in live if x["p_nrfi"] < g]
        if len(keep) < 5:
            continue
        st2 = flat_stats([{"odds": -110, "win": x["yrfi_hit"]} for x in keep])
        print(f"  {g:>10.2f}{st2['bets']:>7}{st2['hit']:>8.1f}{st2['hit']-base['hit']:>+8.1f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())

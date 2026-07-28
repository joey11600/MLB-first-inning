#!/usr/bin/env python3
"""
tools/nrfi_dd_ceiling.py -- the NRFI HIT-RATE CEILING on the big odds-free
backtests, using the CURRENT production model + CURRENT calibrator.

The backtest CSVs' own `nrfi_prob` column is stale (2024 file: mean 0.384
vs an actual NRFI rate of 0.536 -- a dead old calibration). So we recompute
with recalibrate_v2's current LR pair + data/calibration_v2.json.

THIS IS DELIBERATELY LEAKY AND THEREFORE OPTIMISTIC. The LR weights were
fit on 2024+2025+2026YTD and the calibrator on 2025+2026, so scoring 2024
and 2025 with them lets the model peek at the answers. That is fine for
the question being asked: if even a model that has SEEN these outcomes
cannot push the NRFI hit rate up to what DraftKings charges (~57-58% in
the high-p region), then no selection floor can.

Read-only.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402

FILES = {
    "2024": "backtest_2024-04-01_to_2024-09-30_truepit.csv",
    "2025": "backtest_2025-04-01_to_2025-09-30_truepit.csv",
    "2026(4/1-5/26)": ["backtest_2026-04-01_to_2026-05-11_truepit.csv",
                       "backtest_2026-05-12_to_2026-05-26_truepit.csv"],
}


def fnum(v, d=None):
    try:
        if v in (None, "", "None"):
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def build(paths):
    rows = []
    for p in paths:
        with open(ROOT / "data" / "backtests" / p, encoding="utf-8") as f:
            rows.extend(list(csv.DictReader(f)))
    return rows


def main():
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")

    print("model: data/lr_t1.json + lr_b1.json ; calibrator data/calibration_v2.json")
    print("NOTE: leaky/optimistic by construction -- see module docstring.\n")

    sets = {}
    for label, p in FILES.items():
        paths = p if isinstance(p, list) else [p]
        raw = build(paths)
        keep = []
        for r in raw:
            a = r.get("actual_side") or ""
            if a not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            keep.append((r, tv, bv, 1 if a == "NRFI" else 0))
        if not keep:
            print(f"{label}: no graded rows")
            continue
        Xt = np.asarray([k[1] for k in keep], dtype=float)
        Xb = np.asarray([k[2] for k in keep], dtype=float)
        rawp = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
        recs = []
        for (r, _, _, y), pr in zip(keep, rawp):
            recs.append({
                "date": r["date"], "y": y, "raw": float(pr),
                "p": cal.predict(float(pr)),
                "lam": fnum(r.get("lambda_total")),
                "park": fnum(r.get("park_factor")),
            })
        sets[label] = recs
        base = sum(x["y"] for x in recs) / len(recs)
        mp = sum(x["p"] for x in recs) / len(recs)
        print(f"{label:>16}: n={len(recs):>5}  actual NRFI {base:.4f}  "
              f"mean model p {mp:.4f}  (calibration err {mp - base:+.4f})")

    # ---- the ceiling table ----
    print()
    print("=" * 104)
    print("NRFI HIT RATE by p_nrfi FLOOR (no lambda cap).  BREAK-EVEN NEEDED at DK's")
    print("real prices in this region = 0.575-0.585 (measured on 2026, see pricewall).")
    print("=" * 104)
    print(f"{'p_nrfi floor':>14}" + "".join(f"{k:>22}" for k in sets))
    for p0 in [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70]:
        line = f"{p0:>14.2f}"
        for k, recs in sets.items():
            sel = [x for x in recs if x["p"] >= p0]
            if len(sel) < 20:
                line += f"{'n=' + str(len(sel)):>22}"
            else:
                h = sum(x["y"] for x in sel) / len(sel)
                mark = " *" if h >= 0.575 else "  "
                line += f"{h:.3f} n={len(sel):<6d}{mark}".rjust(22)
        print(line)
    print("  * = clears the 0.575 break-even")

    print()
    print("=" * 104)
    print("NRFI HIT RATE with a LAMBDA CEILING added (p_nrfi floor 0.56)")
    print("=" * 104)
    print(f"{'lambda cap':>14}" + "".join(f"{k:>22}" for k in sets))
    for c in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.85, 9.9]:
        line = f"{c:>14.2f}"
        for k, recs in sets.items():
            sel = [x for x in recs if x["p"] >= 0.56 and x["lam"] is not None and x["lam"] <= c]
            if len(sel) < 20:
                line += f"{'n=' + str(len(sel)):>22}"
            else:
                h = sum(x["y"] for x in sel) / len(sel)
                mark = " *" if h >= 0.575 else "  "
                line += f"{h:.3f} n={len(sel):<6d}{mark}".rjust(22)
        print(line)

    print()
    print("=" * 104)
    print("LAMBDA BAND ALONE (no p floor) -- checking the 2026 'lambda 0.85-0.95' anomaly")
    print("=" * 104)
    print(f"{'lambda band':>14}" + "".join(f"{k:>22}" for k in sets))
    bands = [(0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85),
             (0.85, 0.95), (0.95, 1.10), (1.10, 9.9)]
    for lo, hi in bands:
        line = f"{lo:.2f}-{hi:.2f}".rjust(14)
        for k, recs in sets.items():
            sel = [x for x in recs if x["lam"] is not None and lo <= x["lam"] < hi]
            if len(sel) < 20:
                line += f"{'n=' + str(len(sel)):>22}"
            else:
                h = sum(x["y"] for x in sel) / len(sel)
                line += f"{h:.3f} n={len(sel):<6d}  ".rjust(22)
        print(line)

    # top-decile ceiling: the single most favourable slice the model can name
    print()
    print("=" * 104)
    print("ABSOLUTE CEILING: sort by model p_nrfi, take the top X% of each season")
    print("=" * 104)
    print(f"{'top slice':>14}" + "".join(f"{k:>22}" for k in sets))
    for frac in [0.01, 0.02, 0.05, 0.10, 0.20, 0.30]:
        line = f"{frac:>13.0%}"
        for k, recs in sets.items():
            s = sorted(recs, key=lambda x: -x["p"])
            n = max(20, int(len(s) * frac))
            sel = s[:n]
            h = sum(x["y"] for x in sel) / len(sel)
            mark = " *" if h >= 0.575 else "  "
            line += f"{h:.3f} n={len(sel):<6d}{mark}".rjust(22)
        print(line)


if __name__ == "__main__":
    main()

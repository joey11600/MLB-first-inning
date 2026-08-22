#!/usr/bin/env python3
"""
Is ONE inning the wrong target for the information we have?

The structural hypothesis: our inputs (two starters, two lineups, park,
weather) carry real information, but a single first inning (~8 batters,
~1 run) is too noisy a target for it to surface -- AUC 0.52.  If the SAME
inputs rank 3-inning or 5-inning scoring much better, the model is pointed at
the wrong market: F5 totals are liquid and carry a standard ~4.5% vig versus
6.55% on the first-inning total.

Needs data/cache/linescore_full/ (tools/refit2026/fetch_linescores_full.py).
Prints, for each horizon H in {1, 3, 5, 9}: the refit two-stage model's AUC
for "total runs through inning H > median", three splits.  No odds required
-- this is a discrimination claim only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load, matrix, predict  # noqa: E402

LS = ROOT / "data" / "cache" / "linescore_full"


def runs_through(pk: int, H: int):
    f = LS / f"{pk}.json"
    if not f.exists():
        return (np.nan, np.nan)
    inns = json.loads(f.read_text(encoding="utf-8")).get("innings", [])
    a = h = 0.0; seen = 0
    for x in inns:
        if x.get("num") is None or int(x["num"]) > H:
            continue
        seen += 1
        a += float(x.get("away") or 0); h += float(x.get("home") or 0)
    if seen < min(H, 5):            # incomplete linescore (rainout etc.)
        return (np.nan, np.nan)
    return (a, h)


def main() -> int:
    bt = ROOT / "data" / "backtests"
    d = {2024: load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024),
         2025: load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025),
         2026: load(ROOT / "data" / "picks_2026.csv", "home_team", 2026)}
    print(f"linescore_full files: {len(list(LS.glob('*.json')))}")
    for H in (1, 3, 5, 9):
        for y, df in d.items():
            ah = [runs_through(int(pk), H) if pk == pk else (np.nan, np.nan) for pk in df.game_pk]
            df[f"a{H}"] = [x[0] for x in ah]; df[f"h{H}"] = [x[1] for x in ah]
            df[f"t{H}"] = df[f"a{H}"] + df[f"h{H}"]
    for y, df in d.items():
        cov = df["t5"].notna().mean() * 100
        chk = (df["t1"] == df["fi_total_runs"]).mean() * 100
        print(f"  {y}: F5 coverage {cov:.1f}%   (sanity: H=1 total == ledger fi_total_runs on {chk:.1f}% of rows)")

    defs = [("2024->2025", d[2024], d[2025]), ("2025->2024", d[2025], d[2024]),
            ("24+25->2026", pd.concat([d[2024], d[2025]], ignore_index=True), d[2026])]
    print("\nAUC of the refit two-stage model (same 19 features per half) at each horizon")
    print("  target = 'runs through inning H exceed the train-season median'")
    print(f"  {'H':>3} " + " ".join(f"{l:>14}" for l, _, _ in defs) + "   train med (24/25/24+25)")
    for H in (1, 3, 5, 9):
        cells, meds = [], []
        for lab, tr, te in defs:
            trc = tr.dropna(subset=[f"a{H}", f"h{H}"]).copy(); tec = te.dropna(subset=[f"a{H}", f"h{H}"]).copy()
            med = trc[f"t{H}"].median(); meds.append(med)
            for x in (trc, tec):
                x["yA"] = (x[f"a{H}"] > trc[f"a{H}"].median()).astype(int)
                x["yH"] = (x[f"h{H}"] > trc[f"h{H}"].median()).astype(int)
                x["yT"] = (x[f"t{H}"] > med).astype(int)
            pk, b0 = build_park(trc, 50)
            # T1 features describe the away offense vs home pitcher -> away runs; B1 the reverse
            wt, mt, st = fit_lr(matrix(trc, T1_SHIPPED, pk, b0), trc.yA.values, 0.05)
            wb, mb, sb = fit_lr(matrix(trc, B1_SHIPPED, pk, b0), trc.yH.values, 0.05)
            pa = predict(wt, mt, st, matrix(tec, T1_SHIPPED, pk, b0))
            ph = predict(wb, mb, sb, matrix(tec, B1_SHIPPED, pk, b0))
            cells.append(f"{auc(tec.yT.values, pa + ph):.4f} (n={len(tec)})")
        print(f"  {H:>3} " + " ".join(f"{c:>14}" for c in cells) + f"   {'/'.join(f'{m:.0f}' for m in meds)}")
    print("\n  If AUC climbs steeply with H, the inputs are informative and the 1st inning is")
    print("  the wrong market.  If it stays ~0.52 at H=5, the inputs themselves are weak.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

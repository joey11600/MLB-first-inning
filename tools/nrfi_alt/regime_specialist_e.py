#!/usr/bin/env python3
"""Part E -- split the ONE positive-looking money cell (cut 0.54, top 25/33%)
by 2026 half-season.  A real edge holds in both halves; a search artefact
lives in one."""
import sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))
from lr_baseline import LogReg
from calibration import ProbCalibrator
from regime_specialist import load, payout, day_block_boot, UNION_NAMES

def implied(o): return abs(o)/(abs(o)+100.) if o < 0 else 100./(o+100.)

cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
d25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv", 2025)
d26 = load("data/picks_2026.csv", 2026)
for d in (d25, d26):
    for r in d: r["p"] = float(cal.predict(r["raw"]))

CUT = 0.54
TR = [r for r in d25 if r["p"] >= CUT]
m = LogReg.fit(np.asarray([r["u"] for r in TR], float),
               np.asarray([r["y"] for r in TR], float), UNION_NAMES, l2=1.0)
TE = [r for r in d26 if r["p"] >= CUT and r["nrfi_odds"] is not None]
for r, s in zip(TE, m.predict_proba(np.asarray([r["u"] for r in TE], float))):
    r["_s"] = float(s)

dates = sorted({r["date"] for r in d26}); mid = dates[len(dates)//2]
print("=" * 88)
print(f"  E. The one positive cell (cut {CUT}) split by 2026 half-season")
print(f"     split date {mid}")
print("=" * 88)
print(f"  {'window / selection':<30}{'n':>5}{'hit%':>7}{'need%':>7}{'units':>9}{'ROI%':>8}   95% CI")
for wlab, rows in (("full 2026", TE), ("H1 (<%s)" % mid, [r for r in TE if r["date"] < mid]),
                   ("H2 (>=%s)" % mid, [r for r in TE if r["date"] >= mid])):
    for frac, flab in ((1.0, "all"), (0.33, "top33%"), (0.25, "top25%")):
        k = max(1, int(len(rows) * frac))
        sub = sorted(rows, key=lambda r: -r["_s"])[:k]
        if not sub: continue
        pnl = sum(payout(r["nrfi_odds"]) if r["y"] else -1. for r in sub)
        ci = day_block_boot(sub, lambda rr: 100*sum(payout(r["nrfi_odds"]) if r["y"] else -1. for r in rr)/len(rr), B=1500)
        print(f"  {wlab + ' / ' + flab:<30}{len(sub):>5}"
              f"{100*np.mean([r['y'] for r in sub]):>7.1f}"
              f"{100*np.mean([implied(r['nrfi_odds']) for r in sub]):>7.1f}"
              f"{pnl:>+9.2f}{100*pnl/len(sub):>+8.2f}   [{ci[0]:+.1f},{ci[1]:+.1f}]")

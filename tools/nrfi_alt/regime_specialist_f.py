#!/usr/bin/env python3
"""Part F -- ATTRIBUTION. At cut 0.54 the specialist's top-33% looks +6.9%.
How much of that is the SPECIALIST vs (a) the cut itself -- a probability
floor, already a closed null -- and (b) any ranker at all?"""
import sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nrfi_alt"))
from lr_baseline import LogReg
from calibration import ProbCalibrator
from regime_specialist import load, payout, day_block_boot, UNION_NAMES, RNG

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
def roi(sub):
    pnl = sum(payout(r["nrfi_odds"]) if r["y"] else -1. for r in sub)
    return pnl, 100*pnl/len(sub)
print("="*86); print(f"  F. ATTRIBUTION at cut {CUT}, top 33% of n={len(TE)} priced rows"); print("="*86)
k = int(len(TE)*0.33)
for lab, key in (("specialist score", "_s"), ("incumbent raw p_nrfi", "raw"),
                 ("calibrated p (= incumbent)", "p")):
    sub = sorted(TE, key=lambda r: -r[key])[:k]
    pnl, r_ = roi(sub)
    ci = day_block_boot(sub, lambda rr: roi(rr)[1], B=1500)
    print(f"  rank by {lab:<30}{len(sub):>4}{100*np.mean([r['y'] for r in sub]):>7.1f}%"
          f"{pnl:>+9.2f}u{r_:>+8.2f}%   [{ci[0]:+.1f},{ci[1]:+.1f}]")
# random rankers -- what does a coin-flip selection of the same size do?
rois = []
for _ in range(4000):
    idx = RNG.choice(len(TE), k, replace=False)
    rois.append(roi([TE[i] for i in idx])[1])
rois = np.array(rois)
spec_roi = roi(sorted(TE, key=lambda r: -r["_s"])[:k])[1]
print(f"\n  RANDOM ranker, same n={k}, 4000 draws: mean {rois.mean():+.2f}%  "
      f"5th/95th pct [{np.percentile(rois,5):+.1f},{np.percentile(rois,95):+.1f}]")
print(f"  specialist {spec_roi:+.2f}%  ->  percentile vs random: "
      f"{100*(rois < spec_roi).mean():.1f}%  (need >95 to be notable)")

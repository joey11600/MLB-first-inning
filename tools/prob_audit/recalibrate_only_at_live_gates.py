#!/usr/bin/env python3
"""Audit scratch: re-run recalibrate_only.py's DECISION GATE with the live
production constants substituted in memory.  No --save/--ship, so nothing is
written.  Read-only."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"tools"))
import mlb_first_inning_predictor as P
import recalibrate_only as R
from calibration import ProbCalibrator

parks = R.load_parks()
new_cal = R.fit_recent_calibrator(parks, 60)

for label, (snrfi, syrfi) in [
    ("AS-WRITTEN  (NRFI 0.56 / YRFI 0.44)", (R.STRONG_NRFI_P, R.STRONG_YRFI_P)),
    ("LIVE consts (NRFI %.2f / YRFI %.2f)" % (P._LR_STRONG_NRFI_P, P._LR_STRONG_YRFI_P),
     (P._LR_STRONG_NRFI_P, P._LR_STRONG_YRFI_P)),
]:
    R.STRONG_NRFI_P, R.STRONG_YRFI_P = snrfi, syrfi
    print("\n" + "="*70)
    print(label)
    print("="*70)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, cand, prod = R.evaluate_holdout(parks, new_cal, 14)
    out = buf.getvalue()
    print(out[out.index("=== Verdict changes"):])

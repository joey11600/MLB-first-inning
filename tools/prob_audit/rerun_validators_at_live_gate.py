#!/usr/bin/env python3
"""Audit scratch: re-run the two walk-forward validators with the LIVE gate
(_LR_STRONG_YRFI_P) substituted for their hardcoded 0.44, and see whether the
ship/no-ship verdict changes.  Read-only -- patches the module attribute in
memory only, never the file."""
import sys, importlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"tools"))
import mlb_first_inning_predictor as P
LIVE = P._LR_STRONG_YRFI_P
print(f"live _LR_STRONG_YRFI_P = {LIVE}\n")

for modname in ("full_retrain_validation", "recalibration_validation"):
    for gate in (0.44, LIVE, 0.36):
        m = importlib.import_module(modname)
        importlib.reload(m)
        m.STRONG_YRFI_P = gate
        print(f"===== {modname}  @ gate p_nrfi < {gate} =====")
        try:
            m.main()
        except SystemExit as e:
            print("exit:", e)
        print()

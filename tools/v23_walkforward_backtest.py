#!/usr/bin/env python3
"""tools/v23_walkforward_backtest.py -- MOVED.

This script was generalized to support arbitrary phase combinations
(Phase G, Phase 2.1 FIE, and future feature flags) and renamed.  The
old V2.3-only hardcoding is gone.

New path: tools/candidate_walkforward.py
New arg shape: pass-through phase flags + --candidate <dir>

  python tools/candidate_walkforward.py --phase-e3 --fie \\
      --candidate data/candidates/v23_fie

See tools/candidate_walkforward.py --help for the full interface,
including the documented baseline/candidate walk-forward asymmetry
(baseline = fixed V2.2 production weights; candidate = true daily
walk-forward retrain).
"""
import sys

print("ERROR: tools/v23_walkforward_backtest.py was MOVED to "
      "tools/candidate_walkforward.py (2026-05-13).",
      file=sys.stderr)
print("  Old arg shape:   python tools/v23_walkforward_backtest.py", file=sys.stderr)
print("  New arg shape:   python tools/candidate_walkforward.py \\", file=sys.stderr)
print("                       --phase-e3 [--phase-g] [--fie] \\", file=sys.stderr)
print("                       --candidate data/candidates/<name>", file=sys.stderr)
print("  See `tools/candidate_walkforward.py --help`.", file=sys.stderr)
sys.exit(1)

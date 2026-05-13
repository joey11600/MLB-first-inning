#!/usr/bin/env python3
"""tools/phase_g_validation.py -- MOVED.

This script was generalized to support arbitrary phase combinations
(Phase G, Phase 2.1 FIE, and future feature flags) and renamed.  The
old single-phase-G hardcoding is gone.

New path: tools/candidate_validation.py
New arg shape: pass-through phase flags + --candidate <dir>

  python tools/candidate_validation.py --phase-e3 --fie \\
      --candidate data/candidates/v23_fie

  python tools/candidate_validation.py --phase-e3 --phase-g \\
      --candidate data/candidates/phase_g

See tools/candidate_validation.py --help for the full interface.
"""
import sys

print("ERROR: tools/phase_g_validation.py was MOVED to "
      "tools/candidate_validation.py (2026-05-13).",
      file=sys.stderr)
print("  Old arg shape:   python tools/phase_g_validation.py [--quick]",
      file=sys.stderr)
print("  New arg shape:   python tools/candidate_validation.py \\",
      file=sys.stderr)
print("                       --phase-e3 [--phase-g] [--fie] \\",
      file=sys.stderr)
print("                       --candidate data/candidates/<name>",
      file=sys.stderr)
print("  See `tools/candidate_validation.py --help`.", file=sys.stderr)
sys.exit(1)

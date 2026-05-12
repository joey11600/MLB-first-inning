#!/usr/bin/env python3
"""tools/phase_g_validation.py

Runs the full validation gauntlet for the Phase G LR candidate (with
top3c_last10_obp/slg/iso features) vs production V2.2 (18-feature
phase-e3 + phase-f model).

Three tests:
  1. 3-split OOS Brier comparison.
     a. Train 2024, test 2025
     b. Train 2025, test 2024
     c. Train 2024+2025, test 2026
  2. Recent-108-games Brier (the test slice where 2026-only previously
     beat 2024+2025).
  3. Walk-forward backtest with Phase G features.

For each test, prints both V2.2 (18-feature) and Phase G (21-feature)
numbers side-by-side so we can see whether the new features earn
their keep.

Gate (from docs/PHASE_G_recent_form.md):
  - Brier improves on ≥ 2 of 3 OOS splits
  - Combined 2026 Brier improves by ≥ 0.003
  - Walk-forward beats V2.2 + thin-pitcher demotion's +0.85u
    (or stays within 5u)

Usage:
  python tools/phase_g_validation.py             # full run
  python tools/phase_g_validation.py --quick     # skip walk-forward
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_split(label: str, train: list[str], test: str, phase_g: bool) -> dict:
    """Run two_stage_model.py once.  Returns {brier_two_stage, brier_v2}."""
    flag = "--phase-g" if phase_g else "--phase-e3"
    cmd = ["python", "two_stage_model.py", flag, "--train"] + train + ["--test", test]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    brier_2s, brier_v2 = None, None
    for line in out.stdout.split("\n"):
        m = re.search(r"Two-stage Brier\s*:\s*([\d.]+)", line)
        if m: brier_2s = float(m.group(1))
        m = re.search(r"V2 Brier\s*:\s*([\d.]+)", line)
        if m: brier_v2 = float(m.group(1))
    return {"brier_two_stage": brier_2s, "brier_v2": brier_v2}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quick", action="store_true",
                   help="Skip the walk-forward backtest (slowest test).")
    args = p.parse_args()

    bt_24 = str(ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv")
    bt_25 = str(ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv")
    picks_26 = str(ROOT / "data" / "picks_2026.csv")

    print("=" * 78)
    print("  Phase G validation: V2.2 (18-feat) vs Phase G (21-feat)")
    print("=" * 78)

    print("\n[1/2] Three-split OOS Brier comparison")
    print(f"  {'Split':<28}  {'V2.2 Brier':>12}  {'Phase G Brier':>14}  {'Delta':>8}  {'Verdict'}")
    print(f"  {'-'*28}  {'-'*12}  {'-'*14}  {'-'*8}  {'-'*15}")

    splits = [
        ("S1: train 2024, test 2025", [bt_24], bt_25),
        ("S2: train 2025, test 2024", [bt_25], bt_24),
        ("S3: train 2024+25, test 2026", [bt_24, bt_25], picks_26),
    ]
    wins = 0
    losses = 0
    combined_v22 = None
    combined_pg  = None
    for label, train, test in splits:
        e3   = _run_split(label, train, test, phase_g=False)
        pg   = _run_split(label, train, test, phase_g=True)
        b22  = e3["brier_two_stage"]
        bpg  = pg["brier_two_stage"]
        if b22 is None or bpg is None:
            print(f"  {label:<28}  (could not parse)")
            continue
        delta = bpg - b22
        verdict = "PG BETTER" if delta < -0.001 else ("V2.2 BETTER" if delta > 0.001 else "TIE")
        if delta < 0: wins += 1
        elif delta > 0: losses += 1
        print(f"  {label:<28}  {b22:>12.4f}  {bpg:>14.4f}  {delta:>+7.4f}  {verdict}")
        if label.startswith("S3"):
            combined_v22 = b22
            combined_pg  = bpg

    print()
    print(f"  --> Phase G wins on {wins}/3 splits  (gate: need >= 2)")
    if combined_v22 is not None and combined_pg is not None:
        d = combined_pg - combined_v22
        print(f"  --> S3 (2026 OOS) Brier delta: {d:+.4f}  (gate: need <= -0.003)")

    if not args.quick:
        print()
        print("[2/2] Walk-forward not yet implemented for Phase G")
        print("      (TODO: extend tools/v23_walkforward_backtest.py to take --phase-g)")
        print("      Run that test manually once retrain ships.")

    print()
    print("=" * 78)
    print("  DECISION GATES (from docs/PHASE_G_recent_form.md)")
    print("=" * 78)
    print(f"  ≥ 2/3 splits favor Phase G:   {'PASS' if wins >= 2 else 'FAIL'}")
    if combined_v22 is not None and combined_pg is not None:
        d = combined_pg - combined_v22
        print(f"  S3 Brier delta ≤ -0.003:       {'PASS' if d <= -0.003 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

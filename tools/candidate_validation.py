#!/usr/bin/env python3
"""tools/candidate_validation.py -- Gate A validation harness.

Generalized from the prior tools/phase_g_validation.py (Phase-G specific)
to accept any phase combination via pass-through flags.  Runs the
3-split OOS Brier comparison between a BASELINE (--phase-e3 alone,
our production V2.2 setup) and a CANDIDATE (--phase-e3 + whatever
extra phase flags the operator passes).

Three splits per the playbook (docs/PHASE_G_recent_form.md spec):
  S1.  train 2024 backtest,           test 2025 backtest
  S2.  train 2025 backtest,           test 2024 backtest
  S3.  train 2024 + 2025 backtests,   test 2026 picks

Decision gate (per playbook):
  * Candidate Brier improves on >= 2 of 3 splits, AND
  * S3 Brier delta <= -0.003

Walk-forward (Gate B) lives in tools/candidate_walkforward.py.

Arg shape (option (d) -- pass-through flags, no translation layer):
  --phase-e3                Required baseline anchor (auto-enabled if
                             any candidate flag is given).
  --phase-g                 Optional candidate add-on: Phase G features.
  --fie                     Optional candidate add-on: Phase 2.1 FIE.
  (future) --phase-h        Layer in cleanly the same way.

Save path routing:
  --candidate <dir>         Writes the CANDIDATE's trained weights to
                             <dir>/lr_t1.json and <dir>/lr_b1.json.
                             Directory created if missing.
  --baseline-tmp <dir>      Writes the BASELINE's trained weights to
                             <dir>/lr_t1.json and <dir>/lr_b1.json.
                             Defaults to data/candidates/.baseline_tmp/
                             so production data/lr_t1.json is NEVER
                             clobbered by baseline runs.

Usage:
  # Gate A for Phase 2.1 FIE candidate:
  python tools/candidate_validation.py --phase-e3 --fie \\
      --candidate data/candidates/v23_fie

  # Gate A for legacy Phase G candidate (compatibility):
  python tools/candidate_validation.py --phase-e3 --phase-g \\
      --candidate data/candidates/phase_g

  # Compose: Phase G + FIE (V1.1 experiment):
  python tools/candidate_validation.py --phase-e3 --phase-g --fie \\
      --candidate data/candidates/v24_g_fie

  # Quick mode (placeholder; walk-forward TODO not implemented here --
  # use tools/candidate_walkforward.py for Gate B):
  python tools/candidate_validation.py --phase-e3 --fie \\
      --candidate data/candidates/v23_fie --quick
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_TMP = ROOT / "data" / "candidates" / ".baseline_tmp"


def _run_one_train(label: str,
                   phase_flags: list[str],
                   train: list[str],
                   test: str,
                   save_t1: Path,
                   save_b1: Path) -> dict:
    """Spawn two_stage_model.py with the given flags + save paths.
    Returns dict with brier_two_stage (the candidate's two-stage Brier),
    brier_v2 (the script's incidental V2-single-LR baseline), stdout,
    stderr, returncode, and the resolved active_variant string."""
    save_t1.parent.mkdir(parents=True, exist_ok=True)
    cmd = (["python", "two_stage_model.py"]
           + phase_flags
           + ["--train"] + train
           + ["--test", test]
           + ["--save-t1", str(save_t1), "--save-b1", str(save_b1)])
    print(f"    spawn: {' '.join(cmd)}")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(ROOT))

    brier_2s = brier_v2 = None
    active_variant = None
    for line in out.stdout.split("\n"):
        m = re.search(r"Two-stage Brier\s*:\s*([\d.]+)", line)
        if m: brier_2s = float(m.group(1))
        m = re.search(r"V2 Brier\s*:\s*([\d.]+)", line)
        if m: brier_v2 = float(m.group(1))
        m = re.search(r"Active variant:\s*(\S+)", line)
        if m: active_variant = m.group(1)

    return {"brier_two_stage": brier_2s,
            "brier_v2":         brier_v2,
            "active_variant":   active_variant,
            "stdout":           out.stdout,
            "stderr":           out.stderr,
            "returncode":       out.returncode}


def _resolve_flags(args) -> tuple[list[str], list[str], str, str]:
    """From parsed args, return (baseline_flags, candidate_flags,
    baseline_label, candidate_label).  Baseline is always --phase-e3
    alone; candidate is --phase-e3 + whatever phase add-ons the
    operator passed."""
    # Auto-enable --phase-e3 if a candidate-only flag was given.  Mirror
    # the two_stage_model.py behavior so the validation script doesn't
    # diverge from the underlying script's autoload rules.
    if (args.phase_g or args.fie) and not args.phase_e3:
        print("[note] --phase-g/--fie require --phase-e3; enabling phase-e3 automatically")
        args.phase_e3 = True

    baseline_flags = ["--phase-e3"]
    candidate_flags = ["--phase-e3"]
    candidate_extras = []
    if args.phase_g:
        candidate_flags.append("--phase-g")
        candidate_extras.append("PHASE_G")
    if args.fie:
        candidate_flags.append("--fie")
        candidate_extras.append("FIE")

    baseline_label = "PHASE_E3"
    candidate_label = "PHASE_E3" + ("+" + "+".join(candidate_extras) if candidate_extras else "")

    return baseline_flags, candidate_flags, baseline_label, candidate_label


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--phase-e3", action="store_true",
                   help="Baseline anchor; auto-enabled if a candidate flag is given.")
    p.add_argument("--phase-g", action="store_true",
                   help="Candidate add-on: top-3 last-10-games batter features.")
    p.add_argument("--fie", action="store_true",
                   help="Candidate add-on: Phase 2.1 first-inning ERA Bayesian blend.")
    p.add_argument("--candidate", required=True, type=Path,
                   help="Directory for the candidate's trained weights "
                        "(lr_t1.json + lr_b1.json).")
    p.add_argument("--baseline-tmp", default=DEFAULT_BASELINE_TMP, type=Path,
                   help="Directory for the BASELINE's trained weights.  Defaults "
                        "to data/candidates/.baseline_tmp/ -- never overlaps "
                        "production data/lr_t1.json.")
    p.add_argument("--quick", action="store_true",
                   help="(reserved) skip slowest test; currently no-op since "
                        "walk-forward lives in tools/candidate_walkforward.py.")
    args = p.parse_args()

    baseline_flags, candidate_flags, baseline_label, candidate_label = _resolve_flags(args)

    # Refuse meaningless candidate == baseline runs.
    if baseline_flags == candidate_flags:
        sys.exit("ERROR: candidate has no add-on flags (would equal baseline).  "
                 "Pass at least one of --phase-g, --fie, or future --phase-* flags.")

    bt_24 = str(ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30_truepit.csv")
    bt_25 = str(ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv")
    picks_26 = str(ROOT / "data" / "picks_2026.csv")

    print("=" * 78)
    print("  Gate A candidate validation")
    print("=" * 78)
    print(f"  baseline flags    : {' '.join(baseline_flags)}    -> variant {baseline_label}")
    print(f"  candidate flags   : {' '.join(candidate_flags)}    -> variant {candidate_label}")
    print(f"  candidate save dir: {args.candidate}")
    print(f"  baseline save dir : {args.baseline_tmp}  (production weights NOT touched)")
    print()
    print("  Three-split OOS Brier comparison:")
    fmt_hdr = f"  {'Split':<28}  {'Base Brier':>11}  {'Cand Brier':>11}  {'Delta':>8}  {'Verdict'}"
    print(fmt_hdr)
    print(f"  {'-'*28}  {'-'*11}  {'-'*11}  {'-'*8}  {'-'*16}")

    splits = [
        ("S1: train 2024, test 2025",      [bt_24],          bt_25),
        ("S2: train 2025, test 2024",      [bt_25],          bt_24),
        ("S3: train 2024+25, test 2026",   [bt_24, bt_25],   picks_26),
    ]
    wins = losses = 0
    s3_base = s3_cand = None
    any_failure = False

    for label, train, test in splits:
        base = _run_one_train(label, baseline_flags, train, test,
                              args.baseline_tmp / "lr_t1.json",
                              args.baseline_tmp / "lr_b1.json")
        cand = _run_one_train(label, candidate_flags, train, test,
                              args.candidate / "lr_t1.json",
                              args.candidate / "lr_b1.json")
        if base["returncode"] != 0 or cand["returncode"] != 0:
            any_failure = True
            print(f"  {label:<28}  SUBPROCESS FAILURE (rc base={base['returncode']} cand={cand['returncode']})")
            err_tail = (cand["stderr"] or base["stderr"] or "").splitlines()[-5:]
            for line in err_tail:
                print(f"      {line}")
            continue
        b22 = base["brier_two_stage"]
        bcd = cand["brier_two_stage"]
        if b22 is None or bcd is None:
            any_failure = True
            print(f"  {label:<28}  could not parse Brier from subprocess stdout")
            continue
        delta = bcd - b22
        verdict = ("CAND BETTER" if delta < -0.001
                   else "BASE BETTER" if delta > 0.001
                   else "TIE")
        if delta < 0: wins += 1
        elif delta > 0: losses += 1
        print(f"  {label:<28}  {b22:>11.4f}  {bcd:>11.4f}  {delta:>+7.4f}  {verdict}")
        if label.startswith("S3"):
            s3_base, s3_cand = b22, bcd

    print()
    print(f"  --> Candidate wins {wins}/3 splits  (gate: need >= 2)")
    if s3_base is not None and s3_cand is not None:
        d = s3_cand - s3_base
        print(f"  --> S3 Brier delta: {d:+.4f}  (gate: need <= -0.003)")

    print()
    print("=" * 78)
    print("  DECISION GATES")
    print("=" * 78)
    g1 = "PASS" if wins >= 2 else "FAIL"
    print(f"  >= 2/3 splits favor candidate:  {g1}")
    if s3_base is not None and s3_cand is not None:
        d = s3_cand - s3_base
        g2 = "PASS" if d <= -0.003 else "FAIL"
        print(f"  S3 Brier delta <= -0.003:       {g2}")
    else:
        print(f"  S3 Brier delta <= -0.003:       UNKNOWN (S3 did not produce Brier)")
    print()
    print("  Walk-forward (Gate B) is a separate step.  Run:")
    print(f"      python tools/candidate_walkforward.py {' '.join(candidate_flags)} "
          f"--candidate {args.candidate}")
    return 0 if not any_failure else 2


if __name__ == "__main__":
    sys.exit(main())

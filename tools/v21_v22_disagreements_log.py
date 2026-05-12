#!/usr/bin/env python3
"""tools/v21_v22_disagreements_log.py

Emit a CSV containing only the picks where V2.1 (shadow) and V2.2 (live)
disagreed.  Per playbook MLB_MODEL_IMPROVEMENT_PLAYBOOK.md Phase 1.2:
agreements tell us nothing; disagreements are the only informative
samples for evaluating model deltas.  This file is the slim companion
to `tools/v21_vs_v22_compare.py` (which does aggregate W/L/P&L).

A disagreement is defined as: the (side, strength) tuple differs between
V2.1 shadow and V2.2 live.  STRONG NRFI vs PASS counts as a disagreement;
so does STRONG NRFI vs STRONG YRFI.

Output: data/diagnostics/v21_v22_disagreements.csv
Columns:
    date, game_pk, v21_pick, v22_pick, v21_prob, v22_prob,
    actual_outcome, v21_correct, v22_correct

Where:
    *_pick           = "STRONG NRFI" | "STRONG YRFI" | "PASS"
    *_prob           = calibrated P(NRFI) under that version
    actual_outcome   = "NRFI" | "YRFI" | "POSTPONED" | ""
    *_correct        = "WIN" if the model picked the actual side,
                       "LOSS" if it picked the wrong side,
                       ""     if the model PASSed or game un-graded.

Regenerated end-to-end on every run (idempotent overwrite).

Usage:
    python tools/v21_v22_disagreements_log.py
    python tools/v21_v22_disagreements_log.py --since 2026-05-11
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

OUT_PATH = ROOT / "data" / "diagnostics" / "v21_v22_disagreements.csv"

COLS = [
    "date", "game_pk",
    "v21_pick", "v22_pick",
    "v21_prob", "v22_prob",
    "actual_outcome",
    "v21_correct", "v22_correct",
]


def _action(side: str, strength: str) -> str:
    """Render the model's effective action.

    'PASS' if no bet, else 'STRONG NRFI' / 'STRONG YRFI'.
    """
    side = (side or "").strip().upper()
    strength = (strength or "").strip().upper()
    if strength == "STRONG" and side in ("NRFI", "YRFI"):
        return f"STRONG {side}"
    return "PASS"


def _correct(action: str, actual: str) -> str:
    """Did the model's bet match the actual outcome?

    'WIN' / 'LOSS' for STRONG picks on graded games; blank otherwise.
    """
    if not action.startswith("STRONG "):
        return ""
    if actual not in ("NRFI", "YRFI"):
        return ""
    return "WIN" if action.endswith(actual) else "LOSS"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", help="ISO date YYYY-MM-DD; only rows on/after.")
    p.add_argument("--season", type=int, default=None)
    args = p.parse_args()

    from datetime import datetime
    from zoneinfo import ZoneInfo
    today_iso = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    season = args.season or int(today_iso[:4])

    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(csv_path)
    if args.since:
        rows = [r for r in rows if (r.get("date") or "") >= args.since]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    disagreements = []
    for r in rows:
        # Only rows with V2.1 shadow recorded
        v21_strength_raw = (r.get("v21_shadow_pick_strength") or "").strip()
        if v21_strength_raw == "":
            continue

        v22_action = _action(r.get("pick_side"), r.get("pick_strength"))
        v21_action = _action(r.get("v21_shadow_pick_side"),
                             r.get("v21_shadow_pick_strength"))
        if v22_action == v21_action:
            continue   # agreement -- skip

        actual = (r.get("actual_result") or "").strip().upper()
        disagreements.append({
            "date": r.get("date", ""),
            "game_pk": r.get("game_pk", ""),
            "v21_pick": v21_action,
            "v22_pick": v22_action,
            "v21_prob": r.get("v21_shadow_nrfi_prob", ""),
            "v22_prob": r.get("nrfi_prob", ""),
            "actual_outcome": actual if actual in ("NRFI", "YRFI") else (
                "POSTPONED" if actual == "POSTPONED" else ""
            ),
            "v21_correct": _correct(v21_action, actual),
            "v22_correct": _correct(v22_action, actual),
        })

    # Atomic write via tempfile + os.replace (mirrors tracker._write_rows).
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".disagree_", dir=str(OUT_PATH.parent))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS)
            w.writeheader()
            for row in disagreements:
                w.writerow(row)
        os.replace(tmp, OUT_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Counts for log output
    n_total = sum(1 for r in rows
                  if (r.get("v21_shadow_pick_strength") or "").strip() != "")
    n_dis = len(disagreements)
    n_graded_dis = sum(1 for d in disagreements
                       if d["actual_outcome"] in ("NRFI", "YRFI"))
    v21_wins = sum(1 for d in disagreements if d["v21_correct"] == "WIN")
    v22_wins = sum(1 for d in disagreements if d["v22_correct"] == "WIN")

    print(f"v21_v22_disagreements_log -> {OUT_PATH}")
    print(f"  shadow_rows={n_total}  disagreements={n_dis}  "
          f"graded_disagreements={n_graded_dis}")
    print(f"  Among graded disagreements: V2.1 right on {v21_wins}, "
          f"V2.2 right on {v22_wins}, neither (PASS vs STRONG losing) "
          f"= {n_graded_dis - v21_wins - v22_wins}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

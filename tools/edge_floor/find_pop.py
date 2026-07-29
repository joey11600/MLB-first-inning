#!/usr/bin/env python3
"""Which 2026 population produces the operator's 495-bet / 51.9% table?"""
from __future__ import annotations
import statistics as st, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import mlb_first_inning_predictor as P  # noqa
from calibration import ProbCalibrator  # noqa
from tools.edge_floor.common import load_2026, payout, implied, passes_lambda_floor  # noqa

ROOT = Path(__file__).resolve().parent.parent.parent
TARGET = [0.00, 0.04, 0.08, 0.12, 0.16]


def tbl(name, bets):
    print(f"\n  {name}")
    print(f"    {'edge>=':>8}{'bets':>7}{'hit%':>8}{'need%':>8}{'ROI%':>8}")
    for f in TARGET:
        s = [b for b in bets if b["edge"] >= f]
        if not s:
            continue
        w = sum(b["win"] for b in s)
        pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in s)
        print(f"    {f:>8.2f}{len(s):>7}{100*w/len(s):>8.1f}"
              f"{100*st.mean([b['imp'] for b in s]):>8.1f}{100*pl/len(s):>8.1f}")


def main():
    rows, _ = load_2026()
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    probs = [cal.predict(r["raw"]) for r in rows]
    priced = [(r, p) for r, p in zip(rows, probs) if r["yrfi_odds"] is not None]
    print(f"  priced 2026 graded rows: {len(priced)}")

    def mk(pairs):
        return [{"date": r["date"], "p_y": 1 - p, "odds": r["yrfi_odds"],
                 "imp": implied(r["yrfi_odds"]),
                 "edge": (1 - p) - implied(r["yrfi_odds"]), "win": r["yrfi_hit"]}
                for r, p in pairs]

    tbl("(1) ALL priced rows -- NO gate, NO lambda floor", mk(priced))
    tbl("(2) priced + lambda floor only",
        mk([(r, p) for r, p in priced if passes_lambda_floor(r, "lam_csv")]))
    tbl("(3) priced + STRONG gate only (p<0.40)",
        mk([(r, p) for r, p in priced if p < P._LR_STRONG_YRFI_P]))
    tbl("(4) priced + LEAN-or-better YRFI (p<0.50)",
        mk([(r, p) for r, p in priced if p < 0.50]))
    tbl("(5) priced + LEAN-or-better YRFI + lambda floor",
        mk([(r, p) for r, p in priced
            if p < 0.50 and passes_lambda_floor(r, "lam_csv")]))
    tbl("(6) LIVE RULE: STRONG gate + lambda floor  <-- what actually fires",
        mk([(r, p) for r, p in priced
            if p < P._LR_STRONG_YRFI_P and passes_lambda_floor(r, "lam_csv")]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

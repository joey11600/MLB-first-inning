#!/usr/bin/env python3
"""
tools/export_season_record.py -- write data/season_record.json so the
dashboard can display the REAL walk-forward season record.

Kept separate from tools/season_record.py on purpose: that script is the
human-readable report and is already load-bearing, and patching a JSON
writer into the middle of it once landed inside a string literal and
broke the file. A thin exporter that imports its pieces cannot do that.

The numbers here are the SAME walk-forward computation the report
prints -- same loader, same calibrator refit from strictly prior games,
same shipped Kelly helper, same -125 fill for the 404 graded games that
never had a captured DraftKings price.

Usage:
    python tools/export_season_record.py
"""

from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402
from tools.season_record import build, simulate, START, FILL  # noqa: E402


def main() -> int:
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    bank, mdd, curve, staked = simulate(build(rows, wf, FILL))
    if not staked:
        sys.exit("no staked bets -- nothing to export")

    n = len(staked)
    w = sum(1 for b in staked if b["win"])
    flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in staked)
    need = st.mean([implied(b["odds"]) for b in staked])

    bym = defaultdict(list)
    for b in staked:
        bym[b["date"][:7]].append(b)
    monthly = []
    for m in sorted(bym):
        g = bym[m]
        mw = sum(1 for b in g if b["win"])
        monthly.append({
            "month": m, "bets": len(g), "wins": mw, "losses": len(g) - mw,
            "flat": round(sum(payout(x["odds"]) if x["win"] else -1.0 for x in g), 2),
            "assumedBets": sum(1 for x in g if x["assumed"]),
        })

    out = {
        "generatedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": ("walk-forward -- the calibrator at each date is refit from "
                   "strictly earlier games, so no game is scored by a curve "
                   "that has already seen it"),
        "gate": P._LR_STRONG_YRFI_P,
        "kellyFraction": tracker.KELLY_FRACTION,
        "startBank": START,
        "priceFill": FILL,
        "gradedGames": len(rows),
        "slateDays": len(sorted({r["date"] for r in rows})),
        "betDays": len(sorted({b["date"] for b in staked})),
        "bets": n, "wins": w, "losses": n - w,
        "hitRate": round(w / n, 4),
        "breakEvenNeeded": round(need, 4),
        "edgePts": round(100 * (w / n - need), 2),
        "assumedBets": sum(1 for b in staked if b["assumed"]),
        "flatProfit": round(flat, 2),
        "finalBank": round(bank, 2),
        "kellyProfit": round(bank - START, 2),
        "maxDrawdownPct": round(mdd, 2),
        "largestStakeUnits": round(max(b["stake"] for b in staked), 2),
        "monthly": monthly,
        "curve": [{"date": d, "units": round(v, 2)} for d, v in curve],
    }
    dest = ROOT / "data" / "season_record.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}")
    print(f"  {w}W-{n-w}L ({100*w/n:.1f}%)  edge {out['edgePts']:+.1f}pts  "
          f"flat {flat:+.2f}u  bank {START:.0f}u -> {bank:.2f}u  maxDD {mdd:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

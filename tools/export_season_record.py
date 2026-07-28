#!/usr/bin/env python3
"""
tools/export_season_record.py -- writes data/season_record.json: the two
numbers the dashboard displays as THE system record.

THE TWO NUMBERS (operator spec, 2026-07-28)
-------------------------------------------
  PROJECTED  The whole season, April 1 to today. Every game the current
             system would bet. Where a real DraftKings price exists we
             use it; where none was ever captured we use an EXPLICIT
             -125, every time, no exceptions.

  REAL       2026-05-07 onward -- the first day DK price capture became
             reliable (>=80% sustained; 99.6% of games priced from that
             date). REAL captured prices only. Nothing filled, nothing
             assumed. This is the defensible number.

BOTH include YRFI (live gate, read from the predictor) and NRFI at
p_nrfi >= 0.60. Live NRFI *betting* stays disabled -- only 9 real-priced
NRFI bets exist since 5/07, which is not enough to fund -- but the
operator's decision is that the displayed record counts both sides.

METHOD: walk-forward. The calibrator at each date is refit from strictly
earlier games, so no game is scored by a curve that has seen its outcome.
Staking is the SHIPPED tracker.kelly_stake_units with the live caps.

Per-day bet detail is included so the dashboard can show, for any date,
which bets were taken, at what price and stake, and what each returned.

Run after nightly grading:
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
from tools.gate_validation import walk_forward_probs, select  # noqa: E402
from tools.season_record import simulate, START  # noqa: E402

FILL = -125.0
NRFI_GATE = 0.60          # paper gate; live NRFI betting remains disabled
REAL_START = "2026-05-07"  # first day DK capture is >=80% and sustained


def make_record(bets, label, price_fill, date_from, date_to):
    """Simulate one record and return the JSON-ready dict."""
    bets = sorted(bets, key=lambda b: b["date"])
    bank, mdd, curve, staked = simulate(bets)
    if not staked:
        return None
    # Audit 2026-07-28 (P1): Kelly zeroes some SELECTED bets (no edge at
    # the price, or the daily cap is exhausted), and the headline W-L was
    # silently computed over the staked subset only.  Disclose the gap.
    dropped = [b for b in bets if b.get("stake", 0.0) <= 0]
    dropped_flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in dropped)
    n = len(staked)
    w = sum(1 for b in staked if b["win"])
    flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in staked)
    need = st.mean([implied(b["odds"]) for b in staked])
    bank_after = dict(curve)

    bym = defaultdict(list)
    byd = defaultdict(list)
    for b in staked:
        bym[b["date"][:7]].append(b)
        byd[b["date"]].append(b)

    monthly = []
    for m in sorted(bym):
        g = bym[m]
        mw = sum(1 for b in g if b["win"])
        monthly.append({
            "month": m, "bets": len(g), "wins": mw, "losses": len(g) - mw,
            "flat": round(sum(payout(x["odds"]) if x["win"] else -1.0 for x in g), 2),
            "assumedBets": sum(1 for x in g if x.get("assumed")),
        })

    days = []
    for d in sorted(byd):
        g = byd[d]
        days.append({
            "date": d,
            "bankAfter": round(bank_after.get(d, float("nan")), 2),
            "bets": [{
                "game": b.get("game", ""),
                "side": b.get("side", ""),
                "odds": int(b["odds"]),
                "stake": round(b.get("stake", 0.0), 2),
                "win": bool(b["win"]),
                "pnl": round(b["stake"] * payout(b["odds"]) if b["win"] else -b["stake"], 2),
                "assumed": bool(b.get("assumed")),
            } for b in g],
        })

    return {
        "label": label,
        "priceFill": price_fill,
        "from": date_from, "to": date_to,
        "selectedBets": len(bets),
        "droppedZeroStake": len(dropped),
        "droppedFlatPnl": round(dropped_flat, 2),
        "bets": n, "wins": w, "losses": n - w,
        "hitRate": round(w / n, 4),
        "breakEvenNeeded": round(need, 4),
        "edgePts": round(100 * (w / n - need), 2),
        "assumedBets": sum(1 for b in staked if b.get("assumed")),
        "flatProfit": round(flat, 2),
        "startBank": START,
        "finalBank": round(bank, 2),
        "kellyProfit": round(bank - START, 2),
        "maxDrawdownPct": round(mdd, 2),
        "monthly": monthly,
        "days": days,
    }


def main() -> int:
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    yrfi_gate = P._LR_STRONG_YRFI_P
    last_date = max(r["date"] for r in rows)

    # PROJECTED: whole season, -125 fill on missing prices, both sides.
    proj_bets = (select(rows, wf, side="YRFI", gate=yrfi_gate, fill=FILL)
                 + select(rows, wf, side="NRFI", gate=NRFI_GATE, fill=FILL))
    projected = make_record(proj_bets, "Projected profit", FILL,
                            min(r["date"] for r in rows), last_date)

    # REAL: from REAL_START, real captured prices only, both sides.
    real_bets = [b for b in
                 (select(rows, wf, side="YRFI", gate=yrfi_gate, fill=None)
                  + select(rows, wf, side="NRFI", gate=NRFI_GATE, fill=None))
                 if b["date"] >= REAL_START]
    real = make_record(real_bets, "Real profit", None, REAL_START, last_date)

    out = {
        "generatedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Audit 2026-07-28 (P1): the old string claimed full walk-forward.
        # Only the CALIBRATOR is walk-forward; the LR weights are the fixed
        # 2026-05-26 refit, trained on 2024+2025+2026-through-05-11.  Bets
        # dated on or before 2026-05-11 are therefore partially in-sample
        # at the weight layer.  Say exactly that.
        "method": ("calibrator walk-forward (refit at each date from strictly "
                   "earlier games); LR weights fixed from the 2026-05-26 refit "
                   "(trained through 2026-05-11), so bets on or before "
                   "2026-05-11 are partially in-sample at the weight layer"),
        "gates": {"yrfi": yrfi_gate, "nrfi": NRFI_GATE},
        "kellyFraction": tracker.KELLY_FRACTION,
        "startBank": START,
        "realStart": REAL_START,
        "projected": projected,
        "real": real,
    }
    dest = ROOT / "data" / "season_record.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}")
    for r in (projected, real):
        if not r:
            continue
        print(f"  {r['label']:<18} {r['wins']}W-{r['losses']}L "
              f"({100*r['hitRate']:.1f}%)  edge {r['edgePts']:+.1f}pts  "
              f"flat {r['flatProfit']:+.2f}u  bank {r['startBank']:.0f} -> "
              f"{r['finalBank']:.2f}u  maxDD {r['maxDrawdownPct']:.1f}%  "
              f"assumed {r['assumedBets']}/{r['bets']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
tools/build_2026_truepit.py -- build a 2026 truepit-format training
CSV by augmenting picks_2026.csv with the derived columns required
by two_stage_model.gather() (mainly fi_park_nrfi_rate + actual_side).

picks_2026.csv already stores Phase E.3 + VSHAND feature inputs at
predict-time (home_xera, away_top3c_obp, etc.).  Since the priors-
pooled values are written *into the row at predict time* by T4.2,
the row is effectively "truepit-equivalent" for re-training -- no
need to re-derive Statcast features from the JSON snapshots.

Output: data/backtests/backtest_2026-04-01_to_<end_date>_truepit.csv

Usage:
  python tools/build_2026_truepit.py
  python tools/build_2026_truepit.py --end 2026-05-11    # for holdout training
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
PARKS = ROOT / "data" / "fi_park_factors.json"
OUT_DIR = ROOT / "data" / "backtests"


def load_park_map() -> dict:
    """Map home_team_abbrev -> empirical fi_park_nrfi_rate."""
    if not PARKS.exists():
        return {}
    with open(PARKS, encoding="utf-8") as f:
        d = json.load(f)
    # data/fi_park_factors.json structure:
    #   { "ARI": {"nrfi_rate": 0.512, ...}, ... }
    # or older flat form { "ARI": 0.512, ... }.  Handle both.
    out = {}
    for team, val in d.items():
        if isinstance(val, dict):
            out[team] = float(val.get("nrfi_rate") or val.get("rate") or 0.5)
        else:
            out[team] = float(val)
    return out


def to_truepit_row(r: dict, parks: dict) -> dict | None:
    """Convert one picks_2026.csv row to a truepit-format dict.
    Returns None if the row isn't graded (we can't train on it)."""
    fi_away = r.get("fi_away_runs", "")
    fi_home = r.get("fi_home_runs", "")
    if fi_away == "" or fi_home == "":
        return None
    try:
        away_runs = int(float(fi_away))
        home_runs = int(float(fi_home))
    except (TypeError, ValueError):
        return None
    total = away_runs + home_runs
    actual_side = "NRFI" if total == 0 else "YRFI"

    home_team = r.get("home_team", "")
    fi_park = parks.get(home_team, 0.50)

    # Build out a dict matching the truepit schema where it counts.
    # two_stage_model.gather() uses r.get(...) so only the columns it
    # actually reads need to exist; everything else is ignored.
    out = dict(r)  # start with everything picks_2026 already has
    out["actual_side"]        = actual_side
    out["fi_total_runs"]      = str(total)
    out["fi_park_nrfi_rate"]  = f"{fi_park:.4f}"
    # Aliases that two_stage_model.gather() may try
    out["home"] = home_team
    out["away"] = r.get("away_team", "")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-04-01")
    p.add_argument("--end",   default=date.today().isoformat())
    p.add_argument("--out",   default=None)
    args = p.parse_args()

    parks = load_park_map()
    print(f"Loaded park map: {len(parks)} home teams")

    if not PICKS.exists():
        sys.exit(f"picks_2026.csv not found at {PICKS}")

    rows_out = []
    rows_in = 0
    rows_dropped_ungraded = 0
    rows_dropped_window = 0
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows_in += 1
            d = r.get("date", "")
            if d < args.start or d > args.end:
                rows_dropped_window += 1
                continue
            tr = to_truepit_row(r, parks)
            if tr is None:
                rows_dropped_ungraded += 1
                continue
            rows_out.append(tr)

    if not rows_out:
        sys.exit("No graded rows in the requested window -- nothing to write.")

    # Output filename mirrors 2025's: backtest_<start>_to_<end>_truepit.csv
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = OUT_DIR / f"backtest_{args.start}_to_{args.end}_truepit.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Use the union of all columns seen so far.  Order: picks_2026 order
    # first, then any new derived columns at the end.
    base_fields = list(rows_out[0].keys())
    for r in rows_out[1:]:
        for k in r.keys():
            if k not in base_fields:
                base_fields.append(k)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=base_fields)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    print(f"Read {rows_in} picks_2026 rows")
    print(f"  Dropped {rows_dropped_window} outside [{args.start} .. {args.end}]")
    print(f"  Dropped {rows_dropped_ungraded} ungraded in window")
    print(f"  Wrote {len(rows_out)} truepit rows to {out_path}")

    # Print outcome distribution
    nrfi = sum(1 for r in rows_out if r["actual_side"] == "NRFI")
    yrfi = len(rows_out) - nrfi
    print(f"  NRFI rate: {nrfi}/{len(rows_out)} = {nrfi/len(rows_out)*100:.1f}%")


if __name__ == "__main__":
    main()

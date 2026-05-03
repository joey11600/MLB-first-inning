#!/usr/bin/env python3
"""
tools/backfill_xera_whiff_pit.py -- T3.11-AUDIT-FIX, point-in-time xera/whiff
backfill for the historical backtest CSVs.

CONTEXT
-------
The 2024 + 2025 backtest CSVs (data/backtests/) currently have leaky values
for these four columns:

  home_xera, away_xera
  home_whiff_pct_rank, away_whiff_pct_rank

They were filled from data/statcast_pitcher_cache.json which is keyed by
(season, pid).  So an April 2024 game gets the pitcher's END-OF-2024 xera --
classic future-data leakage that inflated the walk-forward backtest from
-1.9% ROI to +6.4% ROI.

THIS SCRIPT'S FIX (provably leak-free)
--------------------------------------
For each row in a backtest CSV, replace xera + whiff_pct_rank with the
PRIOR season's value for the same pitcher.  An April 2024 game uses the
pitcher's 2023 xera; a 2025 game uses 2024 xera.  The prior season is
fully complete before the test season begins, so this is mathematically
incapable of leaking forward data.

Tradeoff: a July 2024 game ideally would use 2024-season-to-date xera
(which captures current-form changes from the prior year), not 2023's
final xera.  But that requires per-game Statcast aggregation we don't
have cached -- it's a separate (longer) build.  For tonight's audit, prior
year is the right call: clean, fast, and a strict lower bound on the true
information content of the feature.

(For full per-game point-in-time, see the FUTURE_WORK section at the
bottom of this file.)

OUTPUTS
-------
For each input CSV `data/backtests/backtest_YYYY-...csv` writes a sibling
`backtest_YYYY-..._leakfree.csv` with the four columns rewritten.  Original
CSVs are NEVER modified.  Walk-forward then runs against the _leakfree
files.

USAGE
-----
  python tools/backfill_xera_whiff_pit.py
  python tools/backfill_xera_whiff_pit.py --season 2025
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# League-average defaults (mirror two_stage_model.py)
LEAGUE_AVG_XERA  = 4.20
NEUTRAL_PCT_RANK = 50

LEAKY_COLS = ("home_xera", "away_xera",
              "home_whiff_pct_rank", "away_whiff_pct_rank")


def load_caches():
    statcast_path = REPO_ROOT / "data" / "statcast_pitcher_cache.json"
    pid_path      = REPO_ROOT / "data" / "pitcher_id_cache.json"
    statcast = json.load(open(statcast_path, encoding="utf-8"))
    pid      = json.load(open(pid_path,      encoding="utf-8"))
    return statcast, pid


def prior_year_lookup(pid: str | None, season: int, statcast: dict
                      ) -> tuple[float | None, int | None]:
    """Return (xera, whiff_pct_rank) for `pid` in season-1, or (None, None)
    if the pitcher has no prior-season Statcast record."""
    if not pid:
        return None, None
    season_str = str(season - 1)
    rec = statcast.get(season_str, {}).get(str(pid))
    if not rec:
        return None, None
    xera = rec.get("xera")
    whiff = rec.get("whiff_pct_rank")
    return (
        float(xera) if xera is not None else None,
        int(whiff)  if whiff is not None else None,
    )


def backfill_csv(season: int, statcast: dict, pid_cache: dict,
                 dry_run: bool = False) -> dict:
    src = REPO_ROOT / "data" / "backtests" / f"backtest_{season}-04-01_to_{season}-09-30.csv"
    dst = src.with_name(src.stem + "_leakfree.csv")
    if not src.exists():
        return {"season": season, "skipped": True, "reason": f"source missing: {src}"}

    with open(src, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows   = list(reader)

    n_rows = len(rows)
    n_xera_filled  = n_xera_default  = 0
    n_whiff_filled = n_whiff_default = 0
    n_no_pid       = 0

    for r in rows:
        pk = (r.get("game_pk") or "").strip()
        pids = pid_cache.get(pk)
        if pids and len(pids) >= 2:
            a_pid, h_pid = str(pids[0]), str(pids[1])
        else:
            a_pid = h_pid = None
            n_no_pid += 1

        # Home pitcher xera + whiff (T1: home pitches top of 1st)
        h_x, h_w = prior_year_lookup(h_pid, season, statcast)
        if h_x is not None:
            r["home_xera"] = f"{h_x:.3f}"
            n_xera_filled += 1
        else:
            r["home_xera"] = f"{LEAGUE_AVG_XERA:.3f}"
            n_xera_default += 1
        if h_w is not None:
            r["home_whiff_pct_rank"] = str(h_w)
            n_whiff_filled += 1
        else:
            r["home_whiff_pct_rank"] = str(NEUTRAL_PCT_RANK)
            n_whiff_default += 1

        # Away pitcher xera + whiff (B1: away pitches bottom of 1st)
        a_x, a_w = prior_year_lookup(a_pid, season, statcast)
        if a_x is not None:
            r["away_xera"] = f"{a_x:.3f}"
            n_xera_filled += 1
        else:
            r["away_xera"] = f"{LEAGUE_AVG_XERA:.3f}"
            n_xera_default += 1
        if a_w is not None:
            r["away_whiff_pct_rank"] = str(a_w)
            n_whiff_filled += 1
        else:
            r["away_whiff_pct_rank"] = str(NEUTRAL_PCT_RANK)
            n_whiff_default += 1

    if dry_run:
        return {
            "season": season, "dry_run": True, "n_rows": n_rows,
            "n_xera_filled":   n_xera_filled,   "n_xera_default":  n_xera_default,
            "n_whiff_filled":  n_whiff_filled,  "n_whiff_default": n_whiff_default,
            "n_no_pid_rows":   n_no_pid,
        }

    # Write the new CSV (header is unchanged; just rewriting 4 columns)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "season": season, "src": str(src), "dst": str(dst), "n_rows": n_rows,
        "n_xera_filled":   n_xera_filled,   "n_xera_default":  n_xera_default,
        "n_whiff_filled":  n_whiff_filled,  "n_whiff_default": n_whiff_default,
        "n_no_pid_rows":   n_no_pid,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--season", type=int, action="append", default=None,
                    help="Restrict to one season (default: 2024 + 2025).  "
                         "Repeatable.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't write the output CSV, just report counts.")
    args = ap.parse_args()

    seasons = args.season or [2024, 2025]
    statcast, pid_cache = load_caches()

    print()
    print("=" * 84)
    print("  Point-in-time xera + whiff_pct_rank backfill (prior-year lookup)")
    print("=" * 84)
    print(f"  Statcast cache seasons : {sorted(statcast.keys())}")
    print(f"  PID cache games        : {len(pid_cache)}")
    print(f"  Target seasons         : {seasons}")
    if args.dry_run:
        print(f"  DRY RUN -- no files written")
    print()

    for s in seasons:
        res = backfill_csv(s, statcast, pid_cache, dry_run=args.dry_run)
        if res.get("skipped"):
            print(f"  {s}: SKIPPED -- {res['reason']}")
            continue
        n_xera_total  = res["n_xera_filled"]  + res["n_xera_default"]
        n_whiff_total = res["n_whiff_filled"] + res["n_whiff_default"]
        cov_xera  = res["n_xera_filled"]  / max(n_xera_total, 1)  * 100
        cov_whiff = res["n_whiff_filled"] / max(n_whiff_total, 1) * 100
        print(f"  {s} ({res['n_rows']} rows):")
        print(f"    xera  : prior-year hit  {res['n_xera_filled']:>5}  "
              f"/ default  {res['n_xera_default']:>4}  "
              f"({cov_xera:>4.1f}% coverage)")
        print(f"    whiff : prior-year hit  {res['n_whiff_filled']:>5}  "
              f"/ default  {res['n_whiff_default']:>4}  "
              f"({cov_whiff:>4.1f}% coverage)")
        print(f"    rows missing pitcher_ids: {res['n_no_pid_rows']}")
        if not args.dry_run:
            print(f"    wrote {res['dst']}")
        print()

    print()
    print("Next: tools/walk_forward.py --include-e3 (using the _leakfree CSVs)")
    print("(or the standalone tools/walk_forward_leakfree.py for the audit fold)")
    print()


# ---------------------------------------------------------------------------
# FUTURE WORK -- per-game point-in-time xera + whiff
# ---------------------------------------------------------------------------
# The strict-best version of this backfill would compute xera and whiff_pct_rank
# CUMULATIVELY through the day before each game, not just "use prior year".
# That captures within-season form changes (e.g. a pitcher who improved from
# a 4.5 xera in 2023 to a 2.8 xera by July 2024).  The implementation outline:
#
#   for each pitcher_id in pid_cache values:
#       df = pybaseball.statcast_pitcher(start="2024-04-01", end="2024-09-30",
#                                        player_id=pitcher_id)
#       # df is per-pitch.  Aggregate by date:
#       df["date"] = df["game_date"].dt.date
#       per_day = df.groupby("date").agg(
#                     pitches=("pitch_number", "count"),
#                     swings=("description", lambda s: (s.isin(SWING_DESCRIPTIONS)).sum()),
#                     whiffs=("description", lambda s: (s.isin(WHIFF_DESCRIPTIONS)).sum()),
#                     est_woba=("estimated_woba_using_speedangle", "mean"),
#                 )
#       # Cumulative xera = league formula on cumulative est_woba.
#       # Cumulative whiff_pct = swings/whiffs cumulative.
#       # Then: for each (pitcher, target_date) in backtest, look up the
#       # value on (target_date - 1) -- strict cutoff.
#
# Estimated runtime: ~30-60 min for 360 pitchers × 2 seasons via Baseball
# Savant API at the published 1-req-per-3-sec rate.  Worth doing if the
# prior-year backfill leaves significant unexplained gap between the leak-free
# walk-forward and live production hit rate -- but for tonight, prior-year
# is the honest, fast, defensible fix.

if __name__ == "__main__":
    main()

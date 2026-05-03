#!/usr/bin/env python3
"""
tools/inspect_mode.py — T2.49 deep-dive on a single loss-mode bucket.

Companion to tools/analyze_losses.py.  Once the classifier has tagged
losses with their failure modes, this script answers "what do the losses
in mode X have in common?" so we can decide which model lever to pull.

For each mode subset (e.g. all `quiet_inning` losses), it pulls the full
feature vector from picks_<season> and prints a side-by-side compare:

       feature              loss-mean      WIN-mean      slate-mean      delta
       --------------       ---------      --------      ----------      -----
       home_xera                 3.21          4.45            4.18      -0.97  *
       home_p_last5_pitcher_nrfi  0.84         0.61            0.66      +0.18  *
       wx_temp_c                14.2          21.3            19.1       -4.9   *
       ...

Features marked `*` differ from the slate mean by >= 0.5 stdevs --
candidates for "what's distinctive about this loss-mode."

Also dumps the raw loss rows so you can spot-check individual games.

Usage:
  python tools/inspect_mode.py --mode quiet_inning
  python tools/inspect_mode.py --mode pitcher_dominated --since 2026-04-01
  python tools/inspect_mode.py --mode quiet_inning --season 2026 --top 30

Env vars:
  SUPABASE_URL, SUPABASE_SERVICE_KEY  — required (loaded from .env)
"""

from __future__ import annotations

import argparse
import statistics as stats
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from db.supabase_writer import _get_client


# Numeric features we care about for cross-comparison.  Categorical /
# JSON / pure-id columns excluded so the side-by-side stays scannable.
COMPARE_FEATURES: list[str] = [
    # Pitcher season stats
    "home_era", "away_era",
    "home_whip", "away_whip",
    "home_fip", "away_fip",
    "home_k9", "away_k9",
    "home_bb9", "away_bb9",
    "home_hr9", "away_hr9",
    # Pitcher Statcast / advanced
    "home_xera", "away_xera",
    "home_whiff_pct_rank", "away_whiff_pct_rank",
    # Recent form
    "home_p_last5_pitcher_nrfi",  "away_p_last5_pitcher_nrfi",
    "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
    # Pitcher-vs-team familiarity
    "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
    "home_avg_ip_per_start", "away_avg_ip_per_start",
    # Top-3 batter aggregates (the model's only batter window)
    "home_top3c_obp", "away_top3c_obp",
    "home_top3c_slg", "away_top3c_slg",
    "home_top3c_iso", "away_top3c_iso",
    # Team aggregates
    "home_obp", "away_obp",
    "home_slg", "away_slg",
    # Environment
    "park_factor",
    "wx_temp_c", "wx_wind_kmh", "wx_humidity",
    "home_plate_ump_nrfi_rate",
    # Model output
    "nrfi_prob", "yrfi_prob",
    "combined_lambda", "lambda_lr_total",
    "edge_on_pick",
]


def _numerify(v: Any) -> float | None:
    """Coerce to float; return None for empty / non-numeric so the
    aggregator can `filter(None, ...)` cleanly."""
    if v in (None, "", "null"):
        return None
    try:
        f = float(v)
        # Filter out the league-default placeholders that pollute the mean.
        # Real ERA/WHIP/etc. are populated as floats; absent values come
        # in as 0.0 only when downstream coerce() fed in a default.
        # Keep zero values though -- some real metrics legitimately ARE 0
        # (e.g. away_top3c_iso for a 0-power lineup).  Caller dedupes.
        return f
    except (TypeError, ValueError):
        return None


def _mean_safe(vals: list[float]) -> float | None:
    """Mean ignoring None.  Returns None if empty."""
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return stats.mean(clean)


def _stdev_safe(vals: list[float]) -> float | None:
    clean = [v for v in vals if v is not None]
    if len(clean) < 2:
        return None
    return stats.stdev(clean)


def fetch_losses_in_mode(client: Any, mode: str, since: str | None) -> list[dict]:
    """Pull (date, game_pk) of losses tagged with this primary_mode."""
    q = client.table("loss_analysis").select(
        "date,game_pk,primary_mode,secondary_modes,notes,units_lost"
    ).eq("primary_mode", mode)
    if since:
        q = q.gte("date", since)
    return q.execute().data or []


def fetch_picks(client: Any, season: int, predicate: str,
                since: str | None) -> list[dict]:
    """Pull picks_<season> rows matching a predicate.  predicate is one of:
       'losses_in_mode'  - inner-joined later by (date, game_pk)
       'wins'            - graded_result='WIN'
       'all_graded'      - graded_result IN ('WIN','LOSS','PASS')
    Returns a flat list."""
    table = f"picks_{season}"
    cols  = ",".join(["date", "game_pk", "away_team", "home_team",
                       "pick_side", "pick_strength",
                       "graded_result", "fi_total_runs"] + COMPARE_FEATURES)
    q = client.table(table).select(cols)
    if predicate == "wins":
        q = q.eq("graded_result", "WIN")
    elif predicate == "all_graded":
        q = q.in_("graded_result", ["WIN", "LOSS", "PASS"])
    if since:
        q = q.gte("date", since)
    # Supabase PostgREST default page size is 1000; that covers any single
    # season.  Increase if/when we cross-season later.
    return q.execute().data or []


def aggregate(rows: list[dict]) -> dict[str, dict[str, float | None]]:
    """For each feature name, compute (mean, stdev, n) across rows."""
    out: dict[str, dict[str, float | None]] = {}
    for feat in COMPARE_FEATURES:
        vals = [_numerify(r.get(feat)) for r in rows]
        out[feat] = {
            "mean":  _mean_safe(vals),
            "stdev": _stdev_safe(vals),
            "n":     sum(1 for v in vals if v is not None),
        }
    return out


def print_compare(loss_agg: dict, win_agg: dict, slate_agg: dict, mode: str) -> None:
    """Side-by-side feature compare with `*` for >0.5σ deltas vs slate."""
    print()
    print("=" * 92)
    print(f"  Feature distribution -- mode='{mode}'")
    print("=" * 92)
    print(f"  {'feature':30} {'loss-mean':>10} {'WIN-mean':>10} "
          f"{'slate-mean':>10} {'delta':>10}  flag")
    print("  " + "-" * 86)
    for feat in COMPARE_FEATURES:
        lm = loss_agg[feat]["mean"]
        wm = win_agg[feat]["mean"]
        sm = slate_agg[feat]["mean"]
        ss = slate_agg[feat]["stdev"]
        if lm is None or sm is None:
            continue
        delta = lm - sm
        flag = ""
        if ss and ss > 0:
            z = abs(delta) / ss
            if z >= 0.75:
                flag = "**"
            elif z >= 0.5:
                flag = " *"
        wm_s = f"{wm:>10.3f}" if wm is not None else f"{'-':>10}"
        print(f"  {feat:30} {lm:>10.3f} {wm_s} {sm:>10.3f} "
              f"{delta:>+10.3f}  {flag}")
    print()
    print("  Legend:  **  >= 0.75 stdev from slate mean   "
          "*  >= 0.5 stdev   (worth investigating)")
    print()


def print_loss_rows(loss_picks: list[dict], top: int) -> None:
    """Quick dump of the actual loss rows for spot-checking."""
    if not loss_picks:
        return
    # Sort by date desc so the most recent are at the top
    loss_picks = sorted(loss_picks, key=lambda r: r.get("date", ""), reverse=True)
    print("=" * 92)
    print(f"  Individual losses in this mode (top {min(top, len(loss_picks))} most recent)")
    print("=" * 92)
    print(f"  {'date':10}  {'matchup':12}  {'pick':5}  "
          f"{'P(pick)':>8}  {'edge':>6}  {'runs':>4}")
    print("  " + "-" * 70)
    for r in loss_picks[:top]:
        side = (r.get("pick_side") or "").upper()
        nrfi = float(r.get("nrfi_prob") or 0)
        yrfi = float(r.get("yrfi_prob") or 0)
        p_pick = nrfi if side == "NRFI" else yrfi
        edge = float(r.get("edge_on_pick") or 0) if r.get("edge_on_pick") not in (None, "") else 0.0
        matchup = f"{r['away_team']}@{r['home_team']}"
        print(f"  {r['date']:10}  {matchup:12}  {side:5}  "
              f"{p_pick:>7.3f}   {edge:>+5.3f}  {r.get('fi_total_runs', '?'):>4}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode",   required=True,
                        help="Failure mode to inspect (e.g. quiet_inning, "
                             "pitcher_dominated, outside_top3_event, "
                             "sequencing, bunched_contact, lineup_changed_late, "
                             "data_quality, other)")
    parser.add_argument("--since",  metavar="YYYY-MM-DD",
                        help="Only consider losses from this date onward.")
    parser.add_argument("--season", type=int, default=datetime.now().year,
                        help="Season table to query (default: current year).")
    parser.add_argument("--top",    type=int, default=20,
                        help="Print this many recent loss rows (default 20).")
    args = parser.parse_args()

    client = _get_client()
    if client is None:
        sys.exit("Supabase not configured; set SUPABASE_URL + SUPABASE_SERVICE_KEY.")

    # 1. Pull losses in the requested mode
    mode_losses = fetch_losses_in_mode(client, args.mode, args.since)
    if not mode_losses:
        print(f"No losses found for mode='{args.mode}' "
              f"(since={args.since or 'any'}).")
        return
    print(f"Mode='{args.mode}': {len(mode_losses)} loss(es).")

    keys = {(r["date"], r["game_pk"]) for r in mode_losses}

    # 2. Pull all_graded picks once and partition.  One query >> N queries.
    all_graded = fetch_picks(client, args.season, "all_graded", args.since)

    loss_picks  = [r for r in all_graded if (r["date"], r["game_pk"]) in keys]
    win_picks   = [r for r in all_graded if r.get("graded_result") == "WIN"]
    slate_picks = all_graded   # everything graded -- "what a typical bet looks like"

    if not loss_picks:
        print(f"  WARNING: 0 picks_<season> rows matched the {len(mode_losses)} "
              f"loss_analysis rows.  Did the season filter exclude them?")
        return

    print(f"  Comparing against {len(win_picks)} WIN(s) and "
          f"{len(slate_picks)} graded total.\n")

    # 3. Aggregate + print
    loss_agg  = aggregate(loss_picks)
    win_agg   = aggregate(win_picks)
    slate_agg = aggregate(slate_picks)
    print_compare(loss_agg, win_agg, slate_agg, args.mode)

    # 4. Dump individual rows for spot-checking
    print_loss_rows(loss_picks, args.top)

    # 5. Bottom-line summary
    flagged = []
    for feat in COMPARE_FEATURES:
        lm, sm = loss_agg[feat]["mean"], slate_agg[feat]["mean"]
        ss = slate_agg[feat]["stdev"]
        if lm is None or sm is None or not ss:
            continue
        z = abs(lm - sm) / ss if ss > 0 else 0
        if z >= 0.5:
            flagged.append((feat, z, lm, sm))
    if flagged:
        flagged.sort(key=lambda t: -t[1])
        print("=" * 92)
        print(f"  Hypothesis seed: features distinctive of mode='{args.mode}'")
        print("=" * 92)
        for feat, z, lm, sm in flagged[:10]:
            arrow = "UP " if lm > sm else "DN "
            print(f"  {feat:30}  {arrow}  loss-mean={lm:>7.3f}  "
                  f"slate-mean={sm:>7.3f}  ({z:.2f} stdev)")
        print()
        print("  These are the candidates the model may be misweighting on this")
        print("  mode of loss.  Next step: backtest a feature-weight tweak that")
        print("  amplifies (or dampens) one of them on the historical slate.")
    else:
        print("No features distinctive of this mode (none reach 0.5σ from baseline).")
        print("These losses look like normal slate distribution -- pure variance.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""tools/fetch_league_fi_era.py

One-shot read-only fetcher for league-aggregate first-inning ERA per
season, used to seed `LEAGUE_FI_AVG_ERA_BY_TARGET_SEASON` in
`backtest.py` for the Phase 2.1 `fetch_pitcher_first_inning_era`
Bayesian shrinkage prior (MLB_MODEL_IMPROVEMENT_PLAYBOOK.md Phase 2.1
Q3 verification, 2026-05-12).

Method:
  - Discover all active MLB teams for the target season via /teams
  - For each team, fetch team_stats with sitCodes=[i01] (first-inning
    pitching aggregate for the season)
  - Sum IP and earned runs across all teams; compute league ERA

Why API rather than backtest CSV: we don't have 2022/2023 backtest CSVs
in this repo, but we need 2023 league FI ER to anchor the 2024
target-season prior (per the year-indexed table structure agreed with
the operator on 2026-05-12).  The 2024 and 2025 numbers can be derived
from the CSVs (total runs * 0.96 earned-only adjustment), but 2023
must come from the API.

Read-only.  Writes nothing to `data/`, the cache, `backtest.py`, or
any tracked file.  Pure print-output.

Usage:
    python tools/fetch_league_fi_era.py                    # default: 2023
    python tools/fetch_league_fi_era.py --season 2023
    python tools/fetch_league_fi_era.py --season 2023 --season 2024
    python tools/fetch_league_fi_era.py --rate-limit-ms 200
"""
from __future__ import annotations

import argparse
import sys
import time

import statsapi


def fetch_teams(season: int) -> list[tuple[int, str]]:
    """Active MLB teams for the season as (team_id, name) tuples."""
    r = statsapi.get("teams", {"sportIds": 1, "season": season})
    out: list[tuple[int, str]] = []
    for t in r.get("teams", []):
        if t.get("sport", {}).get("id") != 1:
            continue
        if not t.get("active", True):
            continue
        # Filter to top-level major-league entries; affiliate teams
        # share sportId in some payloads.  Use 'parentOrgId' present
        # AND matching itself, or simply rely on the 30-team result.
        out.append((int(t["id"]), str(t.get("name") or "")))
    return out


def fetch_team_fi_pitching(team_id: int, season: int) -> dict | None:
    """First-inning pitching aggregate for one team in one season."""
    r = statsapi.get("team_stats", {
        "teamId":   team_id,
        "season":   season,
        "group":    "pitching",
        "stats":    "statSplits",
        "sitCodes": "i01",
    })
    blocks = r.get("stats", [])
    if not blocks:
        return None
    splits = blocks[0].get("splits", [])
    if not splits:
        return None
    s = splits[0].get("stat", {})
    try:
        ip = float(s.get("inningsPitched", 0))
        er = int(s.get("earnedRuns", 0))
        # Also pull raw counts so the operator can see them
        return {
            "ip":          ip,
            "er":          er,
            "era":         float(s.get("era", 0)) if s.get("era") else None,
            "hits":        int(s.get("hits", 0)),
            "walks":       int(s.get("baseOnBalls", 0)),
            "strikeouts":  int(s.get("strikeOuts", 0)),
            "homeRuns":    int(s.get("homeRuns", 0)),
            "battersFaced":int(s.get("battersFaced", 0)),
        }
    except (TypeError, ValueError):
        return None


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--season", action="append", type=int, default=[],
                   help="Season(s) to fetch.  May repeat.  Default: 2023.")
    p.add_argument("--rate-limit-ms", type=int, default=150,
                   help="Sleep this many ms between API calls.")
    args = p.parse_args()

    seasons = args.season or [2023]
    print(f"fetch_league_fi_era  seasons={seasons}  rate_limit_ms={args.rate_limit_ms}")
    print()

    results: dict[int, dict] = {}
    for season in seasons:
        print(f"=== Season {season} ===")
        teams = fetch_teams(season)
        print(f"  discovered {len(teams)} active MLB teams")
        if args.rate_limit_ms:
            time.sleep(args.rate_limit_ms / 1000.0)

        total_ip = 0.0
        total_er = 0
        total_bf = 0
        total_h  = 0
        total_bb = 0
        total_k  = 0
        total_hr = 0
        skipped: list[str] = []
        t0 = time.time()

        for i, (team_id, name) in enumerate(teams, 1):
            data = fetch_team_fi_pitching(team_id, season)
            if data is None:
                skipped.append(f"{name} ({team_id})")
            else:
                total_ip += data["ip"]
                total_er += data["er"]
                total_bf += data["battersFaced"]
                total_h  += data["hits"]
                total_bb += data["walks"]
                total_k  += data["strikeouts"]
                total_hr += data["homeRuns"]
            if i % 5 == 0:
                print(f"    ...{i}/{len(teams)} teams ({time.time()-t0:.1f}s)")
            if args.rate_limit_ms:
                time.sleep(args.rate_limit_ms / 1000.0)

        ok = len(teams) - len(skipped)
        elapsed = time.time() - t0
        print(f"  teams_with_data: {ok}/{len(teams)}  elapsed={elapsed:.1f}s")
        if skipped:
            print(f"  skipped: {skipped}")

        if total_ip <= 0:
            print(f"  ERROR: no IP aggregated for {season}; skipping computation")
            print()
            continue

        league_era = total_er / total_ip * 9.0
        league_bb9 = total_bb / total_ip * 9.0
        league_k9  = total_k  / total_ip * 9.0
        league_hr9 = total_hr / total_ip * 9.0

        results[season] = {
            "ip":  total_ip, "er": total_er, "era": league_era,
            "bb9": league_bb9, "k9": league_k9, "hr9": league_hr9,
            "h":   total_h,  "bb": total_bb,  "k":  total_k,  "hr": total_hr,
            "bf":  total_bf, "teams_ok": ok,
        }
        print()
        print(f"  Total FI IP: {total_ip:.1f}")
        print(f"  Total FI ER: {total_er}")
        print(f"  League FI ERA: {league_era:.4f}")
        print(f"  League FI BB/9: {league_bb9:.2f}  K/9: {league_k9:.2f}  HR/9: {league_hr9:.2f}")
        print()

    # Summary table
    if results:
        print("=" * 60)
        print("Summary")
        print("=" * 60)
        print(f"  {'Season':<8} {'Teams':>6}  {'FI IP':>8}  {'FI ER':>6}  {'FI ERA':>8}")
        print(f"  {'-'*8} {'-'*6}  {'-'*8}  {'-'*6}  {'-'*8}")
        for s, d in sorted(results.items()):
            print(f"  {s:<8} {d['teams_ok']:>6}  {d['ip']:>8.1f}  {d['er']:>6}  {d['era']:>8.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

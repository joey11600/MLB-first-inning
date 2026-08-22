#!/usr/bin/env python3
"""
Team first-inning scoring propensity, pooled and shrunk, strictly pre-game.

The model has no team-level first-inning history as an input: the offense
is described by season OBP/SLG and the top-3's season line.  The dashboard
derives "team last-10 first-inning form" from the ledger; the model never
sees it.  From the per-inning linescores (data/cache/linescore_full, all
6,611 games) build, for each game and each team, strictly before the date:

  team_fi_score  = P(team scores in the 1st when BATTING), pooled across
                   seasons (prior x0.6), shrunk toward the league rate
                   with K_G games
  team_fi_allow  = P(team ALLOWS a 1st-inning run when fielding) -- mostly
                   the rotation, kept for completeness

T1 = away team bats -> away_team_fi_score ; B1 = home bats -> home_team_fi_score.
Output: data/candidates/factor_team_fi.csv.  BUILD ONLY.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LS = ROOT / "data" / "cache" / "linescore_full"
OUT = ROOT / "data" / "candidates" / "factor_team_fi.csv"
PRIOR_W, K_G = 0.6, 30.0


def main() -> int:
    frames = []
    for f, hc, ac in [("data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", "away"),
                      ("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", "away"),
                      ("data/picks_2026.csv", "home_team", "away_team")]:
        d = pd.read_csv(ROOT / f, low_memory=False, usecols=["date", "game_pk", hc, ac])
        d = d.rename(columns={hc: "home", ac: "away"}).dropna(subset=["game_pk"])
        frames.append(d)
    g = pd.concat(frames, ignore_index=True)
    g["game_pk"] = g.game_pk.astype(int).astype(str)
    g = g.drop_duplicates("game_pk").sort_values("date")

    def fi(pk):
        p = LS / f"{pk}.json"
        if not p.exists():
            return None
        inns = json.loads(p.read_text(encoding="utf-8")).get("innings", [])
        for x in inns:
            if x.get("num") == 1:
                a, h = x.get("away"), x.get("home")
                if a is None or h is None:
                    return None
                return int(a), int(h)
        return None

    cur_s, cur_a = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])   # [games, scored]
    pri_s, pri_a = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    lg = [0.0, 0.0]
    season = None; rows = []

    def est(cur, pri, team):
        n = cur[team][0] + pri[team][0]; s = cur[team][1] + pri[team][1]
        base = lg[1] / max(lg[0], 1.0)
        return (s + K_G * base) / (n + K_G) if n > 0 else None

    for date, day in g.groupby("date", sort=True):
        yr = date[:4]
        if yr != season:
            if season is not None:
                for cur, pri in ((cur_s, pri_s), (cur_a, pri_a)):
                    for t, (n, s) in cur.items():
                        pri[t][0] = PRIOR_W * (pri[t][0] + n); pri[t][1] = PRIOR_W * (pri[t][1] + s)
                cur_s.clear(); cur_a.clear()
            season = yr
        # emit (strictly before today)
        for _, r in day.iterrows():
            a_s, h_s = est(cur_s, pri_s, r.away), est(cur_s, pri_s, r.home)
            a_a, h_a = est(cur_a, pri_a, r.away), est(cur_a, pri_a, r.home)
            rows.append([date, r.game_pk,
                         "" if a_s is None else f"{a_s:.4f}", "" if h_s is None else f"{h_s:.4f}",
                         "" if a_a is None else f"{a_a:.4f}", "" if h_a is None else f"{h_a:.4f}"])
        # ingest today's results
        for _, r in day.iterrows():
            v = fi(r.game_pk)
            if v is None:
                continue
            ar, hr = v
            cur_s[r.away][0] += 1; cur_s[r.away][1] += (ar > 0)
            cur_s[r.home][0] += 1; cur_s[r.home][1] += (hr > 0)
            cur_a[r.home][0] += 1; cur_a[r.home][1] += (ar > 0)     # home fields the top of the 1st
            cur_a[r.away][0] += 1; cur_a[r.away][1] += (hr > 0)
            lg[0] += 2; lg[1] += (ar > 0) + (hr > 0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "game_pk", "away_team_fi_score", "home_team_fi_score",
                    "away_team_fi_allow", "home_team_fi_allow"])
        w.writerows(rows)
    print(f"[out] {OUT} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

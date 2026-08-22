#!/usr/bin/env python3
"""
Two untested dimensions, both pre-game by construction (PRIOR season's value):

  def_oaa       the FIELDING team's Outs Above Average last season.  xwOBA is
                defense-independent, so actual-minus-expected runs allowed is
                partly the gloves; nothing in the model sees defense.
                T1: home team fields (away bats) -> home_def_oaa
                B1: away team fields              -> away_def_oaa
  top3_sprint   the batting top-3's mean sprint speed last season (backlog
                #12: leadoff single + steal + groundout scores with no 2nd hit).
                T1: away top-3 -> away_top3_sprint ; B1: home top-3 -> home_top3_sprint

Sources: Savant leaderboards via pybaseball (outs_above_average team view,
sprint_speed).  Prior season for 2024 games = 2023, etc.  Top-3 ids from the
lineup-card cache (data/cache/batting_order).  Output:
data/candidates/factor_defense_speed.csv.  BUILD ONLY.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "candidates" / "factor_defense_speed.csv"
BO = ROOT / "data" / "cache" / "batting_order"


_TEAMS = None
def _teams():
    """statsapi team list: id -> abbreviation (with the dataset's aliases)."""
    global _TEAMS
    if _TEAMS is None:
        import requests
        j = requests.get("https://statsapi.mlb.com/api/v1/teams?sportId=1", timeout=30).json()["teams"]
        _TEAMS = {int(t["id"]): t["abbreviation"] for t in j}
    return _TEAMS


def oaa_by_team(year: int) -> dict:
    """team abbreviation -> season OAA, by summing the Savant FIELDER leaderboard
    one team at a time (the leaderboard's team filter defaults to a single club
    when left blank, so the all-teams pull silently returns 27 rows)."""
    import io as _io, time, requests
    out = {}
    for tid, ab in _teams().items():
        url = ("https://baseballsavant.mlb.com/leaderboard/outs_above_average"
               f"?type=Fielder&year={year}&team={tid}&range=year&min=0&pos=&roles=&viz=hide&csv=true")
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            t = pd.read_csv(_io.StringIO(r.text.lstrip("﻿"))); t.columns = [c.strip() for c in t.columns]
            if "outs_above_average" in t.columns and len(t):
                out[ab] = float(pd.to_numeric(t.outs_above_average, errors="coerce").sum())
        except Exception as e:  # noqa: BLE001
            print(f"  OAA {year} team {ab} failed: {e!r}")
        time.sleep(0.25)
    return out


def main() -> int:
    from pybaseball import statcast_sprint_speed
    oaa, sprint, id2abbr = {}, {}, {}
    for y in (2023, 2024, 2025):
        oaa[y] = oaa_by_team(y)
        s = statcast_sprint_speed(y, 10)
        sprint[y] = dict(zip(s.player_id.astype(int), pd.to_numeric(s.sprint_speed, errors="coerce")))
        for tid, ab in zip(s.team_id.astype(int), s.team.astype(str)):
            id2abbr[tid] = ab
        print(f"  {y}: OAA teams {len(oaa[y])}  sprint players {len(sprint[y])}")
    # the datasets use OAK/ARI/etc; statsapi uses ATH/AZ -- map the known differences
    alias = {"OAK": ["OAK", "ATH"], "ARI": ["ARI", "AZ"], "CWS": ["CWS", "CHW"],
             "WSH": ["WSH", "WSN"], "KC": ["KC", "KCR"], "SD": ["SD", "SDP"],
             "SF": ["SF", "SFG"], "TB": ["TB", "TBR"]}
    def team_oaa(abbr: str, year: int):
        for a in alias.get(abbr, [abbr]):
            if a in oaa.get(year, {}):
                return oaa[year][a]
        return None

    frames = []
    for f, hc, ac, yr in [("data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", "away", 2024),
                          ("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", "away", 2025),
                          ("data/picks_2026.csv", "home_team", "away_team", 2026)]:
        d = pd.read_csv(ROOT / f, low_memory=False, usecols=["date", "game_pk", hc, ac]).dropna(subset=["game_pk"])
        d = d.rename(columns={hc: "home", ac: "away"}); d["season"] = yr; frames.append(d)
    g = pd.concat(frames, ignore_index=True).drop_duplicates("game_pk")
    rows, miss = [], {"oaa": 0, "sprint": 0}
    for _, r in g.iterrows():
        pk = int(r.game_pk); py = r.season - 1
        ho, ao = team_oaa(str(r.home), py), team_oaa(str(r.away), py)
        if ho is None or ao is None: miss["oaa"] += 1
        asp = hsp = None
        p = BO / f"{pk}.json"
        if p.exists():
            j = json.loads(p.read_text(encoding="utf-8"))
            def m3(ids):
                v = [sprint[py].get(int(i)) for i in ids[:3]]
                v = [x for x in v if x is not None and x == x]
                return sum(v) / len(v) if v else None
            asp, hsp = m3(j.get("away", [])), m3(j.get("home", []))
        if asp is None or hsp is None: miss["sprint"] += 1
        rows.append([r.date, pk,
                     "" if ho is None else f"{ho:.1f}", "" if ao is None else f"{ao:.1f}",
                     "" if asp is None else f"{asp:.2f}", "" if hsp is None else f"{hsp:.2f}"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "game_pk", "home_def_oaa", "away_def_oaa", "away_top3_sprint", "home_top3_sprint"])
        w.writerows(rows)
    print(f"[out] {OUT} rows={len(rows)} missing: {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

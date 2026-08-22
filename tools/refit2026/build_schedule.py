#!/usr/bin/env python3
"""
Schedule / fatigue factors (backlog #17, #19), pre-game by construction.

From the three datasets' own (date, home, away) rows and the per-inning
linescores (data/cache/linescore_full):
  xi_yday     1 if the team played EXTRA INNINGS yesterday (the previous game
              was yesterday and ran past 9), else 0
  consec      consecutive calendar days with a game, counting back from
              yesterday (0 = off day yesterday)
  g_last7     games in the previous 7 days
Emitted for both teams; T1 = away team bats -> away_*; B1 = home bats -> home_*.
Output: data/candidates/factor_schedule.csv.  BUILD ONLY.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LS = ROOT / "data" / "cache" / "linescore_full"
OUT = ROOT / "data" / "candidates" / "factor_schedule.csv"


def main() -> int:
    frames = []
    for f, hc, ac in [("data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", "away"),
                      ("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", "away"),
                      ("data/picks_2026.csv", "home_team", "away_team")]:
        d = pd.read_csv(ROOT / f, low_memory=False, usecols=["date", "game_pk", hc, ac]).dropna(subset=["game_pk"])
        frames.append(d.rename(columns={hc: "home", ac: "away"}))
    g = pd.concat(frames, ignore_index=True)
    g["game_pk"] = g.game_pk.astype(int); g = g.drop_duplicates("game_pk")
    g["date"] = pd.to_datetime(g.date).dt.normalize()

    def n_innings(pk):
        p = LS / f"{pk}.json"
        if not p.exists():
            return None
        return len(json.loads(p.read_text(encoding="utf-8")).get("innings", []))

    # team -> sorted list of (date, n_innings)
    sched = defaultdict(list)
    for _, r in g.iterrows():
        ni = n_innings(r.game_pk)
        sched[r.home].append((r.date, ni)); sched[r.away].append((r.date, ni))
    for t in sched:
        sched[t].sort()

    def feats(team, date):
        prior = [(d, ni) for d, ni in sched[team] if d < date]
        if not prior:
            return 0, 0, 0
        last_d, last_ni = prior[-1]
        xi = int((date - last_d).days == 1 and (last_ni or 0) > 9)
        # consecutive calendar days with a game, back from yesterday
        days = sorted({d for d, _ in prior}, reverse=True)
        consec = 0; cur = date
        for d in days:
            if (cur - d).days == 1:
                consec += 1; cur = d
            else:
                break
        g7 = sum(1 for d, _ in prior if 0 < (date - d).days <= 7)
        return xi, consec, g7

    rows = []
    for _, r in g.sort_values("date").iterrows():
        axi, ac, a7 = feats(r.away, r.date); hxi, hc, h7 = feats(r.home, r.date)
        rows.append([r.date.strftime("%Y-%m-%d"), r.game_pk, axi, hxi, ac, hc, a7, h7])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "game_pk", "away_xi_yday", "home_xi_yday", "away_consec", "home_consec",
                    "away_g7", "home_g7"])
        w.writerows(rows)
    df = pd.DataFrame(rows, columns=["date", "game_pk", "away_xi_yday", "home_xi_yday", "away_consec",
                                     "home_consec", "away_g7", "home_g7"])
    print(f"[out] {OUT} rows={len(df)}  xi_yday rate {df.away_xi_yday.mean():.3f}  "
          f"consec mean {df.away_consec.mean():.2f}  g7 mean {df.away_g7.mean():.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

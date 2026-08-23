#!/usr/bin/env python3
"""
The pooled version of the two inputs the model ALREADY has for the first
inning -- home/away_p_last5_pitcher_nrfi and _last10 -- which are first-inning
specific but sample-starved (5-10 innings) and carry near-zero weight.

fi_ra = the starter's FIRST-INNING RUN-ALLOWED RATE (share of his starts in
which the first inning he pitched had >= 1 run), pooled across seasons (prior
x0.6), shrunk toward the league rate with K_START starts, strictly before the
game date.  Outcome-based (runs), unlike fi_xwoba (quality of contact), so it
is the same question asked of the result instead of the process.

Source: per-inning linescores (data/cache/linescore_full) + the game ->
starters map.  Home pitcher pitches the TOP of the 1st (allows away runs);
away pitcher the bottom.  Output: data/candidates/factor_fi_ra.csv.  BUILD ONLY.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_factor_starter_velo_vs_own_mean import load_games  # noqa: E402

LS = ROOT / "data" / "cache" / "linescore_full"
OUT = ROOT / "data" / "candidates" / "factor_fi_ra.csv"
PRIOR_W, K_START = 0.6, 25.0


def main() -> int:
    games = load_games()                    # game_pk -> (date, away_pid, home_pid, src)
    by_date = defaultdict(list)
    for gp, (date, apid, hpid, _) in games.items():
        by_date[date].append((gp, apid, hpid))
    cur, pri = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])   # [starts, scored-on]
    lg = [0.0, 0.0]; season = None; rows = []

    def est(pid):
        n = cur[pid][0] + pri[pid][0]; s = cur[pid][1] + pri[pid][1]
        base = lg[1] / max(lg[0], 1.0)
        return (s + K_START * base) / (n + K_START) if n > 0 else None

    for date in sorted(by_date):
        yr = date[:4]
        if yr != season:
            if season is not None:
                for p, (n, s) in cur.items():
                    pri[p][0] = PRIOR_W * (pri[p][0] + n); pri[p][1] = PRIOR_W * (pri[p][1] + s)
                cur.clear()
            season = yr
        todays = sorted(by_date[date])
        for gp, apid, hpid in todays:                       # emit first: strictly pre-game
            a = est(apid) if apid else None; h = est(hpid) if hpid else None
            rows.append([date, gp, "" if a is None else f"{a:.4f}", "" if h is None else f"{h:.4f}"])
        for gp, apid, hpid in todays:                       # then ingest today's results
            p = LS / f"{gp}.json"
            if not p.exists():
                continue
            inns = json.loads(p.read_text(encoding="utf-8")).get("innings", [])
            first = next((x for x in inns if x.get("num") == 1), None)
            if not first or first.get("away") is None or first.get("home") is None:
                continue
            ar, hr = int(first["away"]), int(first["home"])
            if hpid: cur[hpid][0] += 1; cur[hpid][1] += (ar > 0); lg[0] += 1; lg[1] += (ar > 0)
            if apid: cur[apid][0] += 1; cur[apid][1] += (hr > 0); lg[0] += 1; lg[1] += (hr > 0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["date", "game_pk", "away_fi_ra", "home_fi_ra"]); w.writerows(rows)
    print(f"[out] {OUT} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

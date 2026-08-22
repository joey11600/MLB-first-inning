#!/usr/bin/env python3
"""
Tier A done the way the backlog said it had to be done: a starter's
FIRST-INNING quality, POOLED ACROSS SEASONS, empirical-Bayes shrunk.

Batch 1 (2026-08-02) built first-inning xwOBA / CSW / zone rate season-to-date
and all failed.  Its own diagnosis: ~30 first innings a season is mostly noise,
and pooling 3 seasons of pitch-level data was the stated fix -- but the pooled
version was never built.  This builds it from the local Statcast cache
(data/cache/statcast_zone, 2024-03 .. 2026-08, all innings, 22 columns).

Per pitcher, accumulated over EVERY first-inning plate appearance strictly
BEFORE the game date, across seasons (prior seasons down-weighted):
    fi_xwoba  = xwOBA allowed in inning 1   (BB/HBP at wOBA weights, K = 0,
                in-play = estimated_woba_using_speedangle)
    fi_k      = strikeout rate, inning 1
    fi_bb     = walk+HBP rate, inning 1
    fi_csw    = called-strike + whiff rate per pitch, inning 1
each shrunk toward the league mean with prior strength K_PA plate appearances.

Output: data/candidates/factor_fi_pooled.csv
    date, game_pk, away_fi_xwoba, home_fi_xwoba, away_fi_k, home_fi_k,
    away_fi_bb, home_fi_bb, away_fi_csw, home_fi_csw, away_fi_pa, home_fi_pa
BUILD ONLY.  No evaluation here.
"""
from __future__ import annotations

import csv
import glob
import gzip
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_factor_starter_velo_vs_own_mean import load_games  # noqa: E402  (pid resolution)

SC_GLOB = str(ROOT / "data" / "cache" / "statcast_zone" / "*.csv.gz")
# Robustness knobs (argv): K_PA PRIOR_SEASON_W OUT_SUFFIX.  Defaults are the
# first-build settings; a result that only exists at one setting is a spike.
K_PA = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0        # shrinkage prior, PAs
PRIOR_SEASON_W = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6  # weight on earlier seasons
_SUF = sys.argv[3] if len(sys.argv) > 3 else ""
OUT = ROOT / "data" / "candidates" / f"factor_fi_pooled{_SUF}.csv"
W_BB, W_HBP = 0.69, 0.72      # wOBA linear weights (stable year to year)
CSW = {"called_strike", "swinging_strike", "swinging_strike_blocked", "foul_tip",
       "missed_bunt"}
K_EVENTS = {"strikeout", "strikeout_double_play"}
BB_EVENTS = {"walk", "hit_by_pitch", "intent_walk"}


FASTBALLS = {"FF", "SI", "FC"}
K_FB = 40.0                   # shrinkage prior for the velocity delta, in 1st-inning fastballs


class Acc:
    __slots__ = ("pa", "woba", "k", "bb", "pitches", "csw", "fb1_n", "fb1_s", "fb_n", "fb_s")
    def __init__(self):
        self.pa = self.woba = self.k = self.bb = self.pitches = self.csw = 0.0
        self.fb1_n = self.fb1_s = self.fb_n = self.fb_s = 0.0


def main() -> int:
    games = load_games()                       # game_pk -> (date, away_pid, home_pid, src)
    by_date = defaultdict(list)
    for gp, (date, apid, hpid, _) in games.items():
        by_date[date].append((gp, apid, hpid))
    files = {os.path.basename(p)[:10]: p for p in sorted(glob.glob(SC_GLOB))}
    print(f"[statcast] {len(files)} day files {min(files)} .. {max(files)}")
    dates = sorted(set(by_date) | set(files))

    cur = defaultdict(Acc)                     # this season
    prior = defaultdict(Acc)                   # all earlier seasons, already down-weighted
    lg = Acc()                                 # league running totals (for the shrink target)
    season = None
    rows, emitted, blank = [], 0, 0

    def est(pid):
        c, p = cur.get(pid), prior.get(pid)
        pa = (c.pa if c else 0) + (p.pa if p else 0)
        if pa <= 0:
            return None
        def comb(attr):
            return (getattr(c, attr) if c else 0) + (getattr(p, attr) if p else 0)
        lpa = max(lg.pa, 1.0); lpit = max(lg.pitches, 1.0)
        xw = (comb("woba") + K_PA * lg.woba / lpa) / (pa + K_PA)
        k = (comb("k") + K_PA * lg.k / lpa) / (pa + K_PA)
        bb = (comb("bb") + K_PA * lg.bb / lpa) / (pa + K_PA)
        pit = comb("pitches"); kp = K_PA * 3.8
        cs = (comb("csw") + kp * lg.csw / lpit) / (pit + kp) if pit + kp > 0 else None
        # first-inning fastball velocity MINUS the pitcher's own all-inning fastball
        # velocity, pooled; shrunk toward the league delta.  Negative = a cold starter.
        n1, n_all = comb("fb1_n"), comb("fb_n")
        velo = None
        if n1 >= 10 and n_all >= 50 and lg.fb1_n > 0 and lg.fb_n > 0:
            d_own = comb("fb1_s") / n1 - comb("fb_s") / n_all
            d_lg = lg.fb1_s / lg.fb1_n - lg.fb_s / lg.fb_n
            velo = (n1 * d_own + K_FB * d_lg) / (n1 + K_FB)
        return xw, k, bb, cs, pa, velo

    for date in dates:
        yr = date[:4]
        if yr != season:
            if season is not None:
                for pid, a in cur.items():
                    p = prior[pid]
                    for f in Acc.__slots__:
                        setattr(p, f, PRIOR_SEASON_W * getattr(p, f) + PRIOR_SEASON_W * getattr(a, f))
            cur = defaultdict(Acc)
            season = yr
        # 1) EMIT for today's games using only what was seen strictly before today
        for gp, apid, hpid in sorted(by_date.get(date, [])):
            vals = []
            for pid in (apid, hpid):
                e = est(pid) if pid is not None else None
                if e is None:
                    vals.append(["", "", "", "", "", ""]); blank += 1
                else:
                    vals.append([f"{e[0]:.4f}", f"{e[1]:.4f}", f"{e[2]:.4f}",
                                 "" if e[3] is None else f"{e[3]:.4f}", f"{e[4]:.0f}",
                                 "" if e[5] is None else f"{e[5]:.3f}"])
                    emitted += 1
            a, h = vals
            rows.append([date, gp, a[0], h[0], a[1], h[1], a[2], h[2], a[3], h[3], a[4], h[4], a[5], h[5]])
        # 2) INGEST today's first-inning pitches
        path = files.get(date)
        if not path:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            r = csv.reader(fh); hdr = next(r); ix = {h: i for i, h in enumerate(hdr)}
            ci, cp, cd, ce, cx = ix["inning"], ix["pitcher"], ix["description"], ix["events"], \
                ix["estimated_woba_using_speedangle"]
            ct, cv = ix["pitch_type"], ix["release_speed"]
            for row in r:
                try:
                    pid = int(row[cp])
                except ValueError:
                    continue
                # fastball velocity, ALL innings (the pitcher's own baseline) and
                # inning 1 separately -- the delta is the cold-start signal
                if row[ct] in FASTBALLS and row[cv]:
                    try:
                        v = float(row[cv])
                    except ValueError:
                        v = None
                    if v is not None and 50.0 <= v <= 110.0:
                        a0 = cur[pid]
                        a0.fb_n += 1; a0.fb_s += v; lg.fb_n += 1; lg.fb_s += v
                        if row[ci] == "1":
                            a0.fb1_n += 1; a0.fb1_s += v; lg.fb1_n += 1; lg.fb1_s += v
                if row[ci] != "1":
                    continue
                a = cur[pid]
                a.pitches += 1; lg.pitches += 1
                if row[cd] in CSW:
                    a.csw += 1; lg.csw += 1
                ev = row[ce]
                if not ev:
                    continue                 # not a PA-ending pitch
                a.pa += 1; lg.pa += 1
                if ev in K_EVENTS:
                    a.k += 1; lg.k += 1
                elif ev in BB_EVENTS:
                    w = W_HBP if ev == "hit_by_pitch" else W_BB
                    a.bb += 1; lg.bb += 1; a.woba += w; lg.woba += w
                else:
                    x = row[cx]
                    if x:
                        try:
                            v = float(x); a.woba += v; lg.woba += v
                        except ValueError:
                            pass
    # CURRENT STATE for predict-time: after every cached date is ingested,
    # dump each pitcher's pooled estimate as of the latest scrape.  The live
    # predictor reads this the way it reads fi_park_factors.json; a pitcher
    # missing from it takes the league mean (= the shrinkage target), which
    # is exactly what a zero-PA pitcher resolves to here.
    cur_state = {}
    for pid in set(cur) | set(prior):
        e = est(pid)
        if e is not None:
            cur_state[str(pid)] = {"fi_xwoba": round(e[0], 4), "fi_k": round(e[1], 4),
                                   "fi_bb": round(e[2], 4), "fi_pa": int(e[4]),
                                   "fi_velo": (None if e[5] is None else round(e[5], 3))}
    import json as _json
    state_path = ROOT / "data" / "candidates" / f"fi_pitcher_pooled_current{_SUF}.json"
    state_path.write_text(_json.dumps({
        "as_of": max(files) if files else None,
        "league_fi_xwoba": round(lg.woba / max(lg.pa, 1.0), 4),
        "k_pa": K_PA, "prior_season_w": PRIOR_SEASON_W,
        "pitchers": cur_state}, indent=0), encoding="utf-8")
    print(f"[state] {state_path}  pitchers={len(cur_state)}  as_of={max(files) if files else None}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "game_pk", "away_fi_xwoba", "home_fi_xwoba", "away_fi_k", "home_fi_k",
                    "away_fi_bb", "home_fi_bb", "away_fi_csw", "home_fi_csw", "away_fi_pa", "home_fi_pa",
                    "away_fi_velo", "home_fi_velo"])
        w.writerows(rows)
    print(f"[out] {OUT}  rows={len(rows)}  pitcher-slots emitted={emitted} blank={blank}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

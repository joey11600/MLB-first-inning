#!/usr/bin/env python3
"""
Batter-side and platoon features, built the way the pitcher feature passed:
pitch-level Statcast, POOLED ACROSS SEASONS, shrunk, strictly pre-game.

For each game's top-3 hitters (lineup card; sources in priority order:
data/cache/batting_order/<pk>.json from statsapi, the 2026 ledger's
lineup_json, data/cache/boxscore_top3/<pk>.json):

  top3_xwoba      mean pooled xwOBA of the three (all PAs, K=0, BB/HBP weights)
  top3_k          mean pooled strikeout rate
  top3_fi_xwoba   same, FIRST-INNING plate appearances only
  platoon_xwoba   the OPPOSING STARTER's pooled xwOBA allowed vs each hitter's
                  side (L/R, as he actually bats vs that pitcher's hand),
                  averaged over the three -- the platoon split the 2026-08-02
                  feature audit called "the one real gap"

T1 = home pitcher vs away top-3  ->  away_top3_* and t1_platoon_xwoba
B1 = away pitcher vs home top-3  ->  home_top3_* and b1_platoon_xwoba

Output: data/candidates/factor_batter_pooled.csv.  BUILD ONLY.
"""
from __future__ import annotations

import csv
import glob
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from build_factor_starter_velo_vs_own_mean import load_games  # noqa: E402

SC_GLOB = str(ROOT / "data" / "cache" / "statcast_zone" / "*.csv.gz")
OUT = ROOT / "data" / "candidates" / "factor_batter_pooled.csv"
PRIOR_W = 0.6
K_BAT, K_FI, K_PLAT = 100.0, 60.0, 150.0
W_BB, W_HBP = 0.69, 0.72
K_EV = {"strikeout", "strikeout_double_play"}
BB_EV = {"walk", "hit_by_pitch", "intent_walk"}


class A:
    __slots__ = ("pa", "w", "k", "fpa", "fw")          # batter: all PAs + first-inning PAs
    def __init__(self): self.pa = self.w = self.k = self.fpa = self.fw = 0.0


class P:
    __slots__ = ("paL", "wL", "paR", "wR")              # pitcher: by batter stand
    def __init__(self): self.paL = self.wL = self.paR = self.wR = 0.0


def top3_sources():
    bo = {}
    for f in glob.glob(str(ROOT / "data" / "cache" / "batting_order" / "*.json")):
        try:
            j = json.loads(open(f, encoding="utf-8").read())
            if len(j.get("away", [])) >= 3 and len(j.get("home", [])) >= 3:
                bo[os.path.basename(f)[:-5]] = (j["away"][:3], j["home"][:3])
        except Exception:  # noqa: BLE001
            pass
    lj = {}
    d = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False,
                    usecols=["game_pk", "away_lineup_json", "home_lineup_json"]).dropna()
    for _, r in d.iterrows():
        try:
            a = [int(x["id"]) for x in json.loads(r.away_lineup_json)[:3]]
            h = [int(x["id"]) for x in json.loads(r.home_lineup_json)[:3]]
            if len(a) == 3 and len(h) == 3:
                lj[str(int(r.game_pk))] = (a, h)
        except Exception:  # noqa: BLE001
            pass
    bx = {}
    for f in glob.glob(str(ROOT / "data" / "cache" / "boxscore_top3" / "*.json")):
        try:
            j = json.loads(open(f, encoding="utf-8").read())
            if len(j.get("away_top3", [])) == 3 and len(j.get("home_top3", [])) == 3:
                bx[os.path.basename(f)[:-5]] = (list(map(int, j["away_top3"])), list(map(int, j["home_top3"])))
        except Exception:  # noqa: BLE001
            pass
    print(f"[top3] batting_order {len(bo)}  lineup_json {len(lj)}  boxscore_top3 {len(bx)}")
    return bo, lj, bx


def main() -> int:
    games = load_games()
    bo, lj, bx = top3_sources()
    by_date = defaultdict(list)
    src = Counter()
    for gp, (date, apid, hpid, _) in games.items():
        t = bo.get(gp) or lj.get(gp) or bx.get(gp)
        src["batting_order" if gp in bo else "lineup_json" if gp in lj else "boxscore_top3" if gp in bx else "NONE"] += 1
        by_date[date].append((gp, apid, hpid, t))
    print(f"[games] top-3 source mix: {dict(src)}")
    files = {os.path.basename(p)[:10]: p for p in sorted(glob.glob(SC_GLOB))}
    dates = sorted(set(by_date) | set(files))

    bat_cur, bat_pri = defaultdict(A), defaultdict(A)
    pit_cur, pit_pri = defaultdict(P), defaultdict(P)
    stand_vsR, stand_vsL = defaultdict(Counter), defaultdict(Counter)   # batter -> Counter of stand
    throws = defaultdict(Counter)                                        # pitcher -> Counter of p_throws
    lg = A(); lgp = P()
    season = None; rows = []; diag = Counter()

    def comb(cur, pri, pid, attr):
        return (getattr(cur[pid], attr) if pid in cur else 0.0) + (getattr(pri[pid], attr) if pid in pri else 0.0)

    def bat_est(bid):
        pa = comb(bat_cur, bat_pri, bid, "pa")
        if pa <= 0: return None
        lw = lg.w / max(lg.pa, 1); lk = lg.k / max(lg.pa, 1); lfw = lg.fw / max(lg.fpa, 1)
        xw = (comb(bat_cur, bat_pri, bid, "w") + K_BAT * lw) / (pa + K_BAT)
        k = (comb(bat_cur, bat_pri, bid, "k") + K_BAT * lk) / (pa + K_BAT)
        fpa = comb(bat_cur, bat_pri, bid, "fpa")
        fxw = (comb(bat_cur, bat_pri, bid, "fw") + K_FI * lfw) / (fpa + K_FI)
        return xw, k, fxw

    def pit_hand(pid):
        c = throws.get(pid)
        return c.most_common(1)[0][0] if c else None

    def bat_side(bid, vs_hand):
        c = (stand_vsL if vs_hand == "L" else stand_vsR).get(bid)
        if not c:
            c = (stand_vsL[bid] + stand_vsR[bid]) if (bid in stand_vsL or bid in stand_vsR) else None
        return c.most_common(1)[0][0] if c else None

    def platoon(pid, bids):
        hand = pit_hand(pid)
        if hand is None: return None
        vals = []
        for b in bids:
            side = bat_side(b, hand)
            if side is None: continue
            pa = comb(pit_cur, pit_pri, pid, "paL" if side == "L" else "paR")
            w = comb(pit_cur, pit_pri, pid, "wL" if side == "L" else "wR")
            lpa = lgp.paL if side == "L" else lgp.paR; lw = lgp.wL if side == "L" else lgp.wR
            lmean = lw / max(lpa, 1)
            vals.append((w + K_PLAT * lmean) / (pa + K_PLAT))
        return sum(vals) / len(vals) if vals else None

    for date in dates:
        yr = date[:4]
        if yr != season:
            if season is not None:
                for dct_c, dct_p, cls in ((bat_cur, bat_pri, A), (pit_cur, pit_pri, P)):
                    for pid, a in dct_c.items():
                        p = dct_p[pid]
                        for f in cls.__slots__:
                            setattr(p, f, PRIOR_W * getattr(p, f) + PRIOR_W * getattr(a, f))
                bat_cur, pit_cur = defaultdict(A), defaultdict(P)
            season = yr
        for gp, apid, hpid, t in sorted(by_date.get(date, []), key=lambda x: x[0]):
            out = [date, gp] + [""] * 12
            if t is not None:
                a3, h3 = t
                ea = [bat_est(b) for b in a3]; eh = [bat_est(b) for b in h3]
                # leadoff hitter ALONE (backlog #11): he is guaranteed to bat
                la, lh = ea[0], eh[0]
                out[12] = "" if not la else f"{la[0]:.4f}"; out[13] = "" if not lh else f"{lh[0]:.4f}"
                ea = [e for e in ea if e]; eh = [e for e in eh if e]
                if ea:
                    out[2] = f"{sum(e[0] for e in ea)/len(ea):.4f}"; out[4] = f"{sum(e[1] for e in ea)/len(ea):.4f}"
                    out[6] = f"{sum(e[2] for e in ea)/len(ea):.4f}"; out[10] = str(len(ea))
                if eh:
                    out[3] = f"{sum(e[0] for e in eh)/len(eh):.4f}"; out[5] = f"{sum(e[1] for e in eh)/len(eh):.4f}"
                    out[7] = f"{sum(e[2] for e in eh)/len(eh):.4f}"; out[11] = str(len(eh))
                pt1 = platoon(hpid, a3) if hpid else None; pb1 = platoon(apid, h3) if apid else None
                out[8] = "" if pt1 is None else f"{pt1:.4f}"; out[9] = "" if pb1 is None else f"{pb1:.4f}"
                diag["emitted" if ea and eh else "partial"] += 1
            else:
                diag["no_top3"] += 1
            rows.append(out)
        path = files.get(date)
        if not path: continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            r = csv.reader(fh); h = next(r); ix = {c: i for i, c in enumerate(h)}
            ci, cp, cb, cs, cth, ce, cx = (ix["inning"], ix["pitcher"], ix["batter"], ix["stand"],
                                           ix["p_throws"], ix["events"], ix["estimated_woba_using_speedangle"])
            for row in r:
                ev = row[ce]
                if not ev: continue
                try:
                    pid, bid = int(row[cp]), int(row[cb])
                except ValueError:
                    continue
                st, th = row[cs], row[cth]
                if th: throws[pid][th] += 1
                if st and th:
                    (stand_vsL if th == "L" else stand_vsR)[bid][st] += 1
                if ev in K_EV: w = 0.0
                elif ev in BB_EV: w = W_HBP if ev == "hit_by_pitch" else W_BB
                else:
                    try: w = float(row[cx])
                    except ValueError: w = None
                wv = w if w is not None else 0.0
                b = bat_cur[bid]; b.pa += 1; b.w += wv; lg.pa += 1; lg.w += wv
                if ev in K_EV: b.k += 1; lg.k += 1
                if row[ci] == "1":
                    b.fpa += 1; b.fw += wv; lg.fpa += 1; lg.fw += wv
                p = pit_cur[pid]
                if st == "L": p.paL += 1; p.wL += wv; lgp.paL += 1; lgp.wL += wv
                elif st == "R": p.paR += 1; p.wR += wv; lgp.paR += 1; lgp.wR += wv
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "game_pk", "away_top3_xwoba", "home_top3_xwoba", "away_top3_k", "home_top3_k",
                    "away_top3_fi_xwoba", "home_top3_fi_xwoba", "t1_platoon_xwoba", "b1_platoon_xwoba",
                    "away_top3_n", "home_top3_n", "away_lead_xwoba", "home_lead_xwoba"])
        w.writerows(rows)
    print(f"[out] {OUT} rows={len(rows)} {dict(diag)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Weather at FIRST PITCH instead of the 7 PM proxy -- build + test.

THE OPERATOR'S POINT (2026-08-23): "only focus on the weather forecast for
the first 10 minutes from game time. we don't care about weather for the
rest of the day."  He is more right than he knows: the shipped model does
not even use the game hour.  Both the training cache
(backtest.fetch_weather_season) and the live fetch
(mlb_first_inning_predictor._fetch_open_meteo_forecast) take the 19:00
America/New_York slot for EVERY game -- a 1:35 PM game gets weather from
five hours after its first inning; a 9:40 PM ET start in Phoenix gets
4 PM local.  Train and serve agree (no skew), but the input is a proxy.

WHAT THIS DOES
  --build   MLB schedule API (gamePk -> real first-pitch UTC) + open-meteo
            archive (hourly temp/wind/dir/humidity per outdoor park,
            ET-indexed) -> data/candidates/factor_wx_gamehour.csv with the
            nearest-hour-to-first-pitch values per game.  Hourly is the
            practical limit: the 1st inning lasts ~20 min, and within-hour
            weather drift is far below forecast error, so the game-hour
            value IS "the first 10 minutes" for any honest purpose.
  --test    The repo's standard protocol on the LIVE 20-feature set
            (T1/B1_SHIPPED + fi_xwoba, L2 0.50): three splits, replacing
            wx_temp_c / wx_wind_kmh / wx_humidity with the game-hour values
            on outdoor rows, paired-bootstrap dAUC/dlogloss, and the No.1
            product metric on 2026.  Plus the wind-DIRECTION retest on the
            sharper input -- the one condition wind_direction_dead allows
            ("do not retest without ... gust-at-game-time data") -- with
            the same crosswind placebo bar.

RESEARCH ONLY.  Writes nothing under data/lr_* and changes no production
path; shipping a winner goes through the operator + model gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = ROOT / "data" / "candidates" / "factor_wx_gamehour.csv"
ET = ZoneInfo("America/New_York")


def _get_json(url: str, timeout: float = 60.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_first_pitch_utc(seasons: list[int]) -> dict[int, str]:
    """gamePk -> gameDate (the scheduled first-pitch instant, UTC ISO)."""
    out: dict[int, str] = {}
    today = date.today().isoformat()
    for season in seasons:
        start, end = f"{season}-03-15", min(f"{season}-10-05", today)
        # month chunks; the schedule endpoint dislikes very long ranges
        cur = datetime.fromisoformat(start).date()
        stop = datetime.fromisoformat(end).date()
        while cur <= stop:
            nxt = min(cur + timedelta(days=30), stop)
            j = _get_json("https://statsapi.mlb.com/api/v1/schedule?sportId=1"
                          f"&startDate={cur.isoformat()}&endDate={nxt.isoformat()}")
            for d in j.get("dates", []):
                for g in d.get("games", []):
                    try:
                        out[int(g["gamePk"])] = str(g["gameDate"])
                    except (KeyError, TypeError, ValueError):
                        continue
            cur = nxt + timedelta(days=1)
        print(f"  schedule {season}: {sum(1 for _ in out)} cumulative games")
    return out


def fetch_park_hourly(lat: float, lon: float, start: str, end: str) -> dict[str, tuple]:
    """ET-hour string "YYYY-MM-DDTHH:00" -> (temp_c, wind_kmh, wind_deg, humidity)."""
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
           "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m"
           "&timezone=America/New_York")
    j = _get_json(url, timeout=120)
    h = j.get("hourly", {})
    times = h.get("time", [])
    t, w, wd, hu = (h.get(k, []) for k in
                    ("temperature_2m", "wind_speed_10m", "wind_direction_10m",
                     "relative_humidity_2m"))
    return {times[i]: (t[i] if i < len(t) else None, w[i] if i < len(w) else None,
                       wd[i] if i < len(wd) else None, hu[i] if i < len(hu) else None)
            for i in range(len(times))}


def game_rows() -> pd.DataFrame:
    """(date, game_pk, park) for every game in the three evaluation frames."""
    bt = ROOT / "data" / "backtests"
    frames = []
    for f, hc in [(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home"),
                  (bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home"),
                  (ROOT / "data" / "picks_2026.csv", "home_team")]:
        d = pd.read_csv(f, low_memory=False, usecols=["date", "game_pk", hc])
        d = d.rename(columns={hc: "park"}).dropna(subset=["game_pk"])
        frames.append(d)
    g = pd.concat(frames, ignore_index=True).drop_duplicates("game_pk")
    g["game_pk"] = pd.to_numeric(g.game_pk, errors="coerce").astype("Int64")
    return g.dropna(subset=["game_pk"])


def build() -> int:
    from backtest import PARK_COORDS, DOMED_PARKS
    games = game_rows()
    outdoor = games[~games.park.isin(DOMED_PARKS) & games.park.isin(PARK_COORDS)]
    print(f"{len(games)} games, {len(outdoor)} at outdoor parks")

    pitch = fetch_first_pitch_utc([2024, 2025, 2026])
    print(f"  first-pitch times for {len(pitch)} scheduled games")

    end = (date.today() - timedelta(days=2)).isoformat()   # archive lags a little
    wx_by_park: dict[str, dict] = {}
    for pk_abbr in sorted(outdoor.park.unique()):
        lat, lon = PARK_COORDS[pk_abbr]
        try:
            wx_by_park[pk_abbr] = fetch_park_hourly(lat, lon, "2024-04-01", end)
            print(f"  {pk_abbr}: {len(wx_by_park[pk_abbr])} hourly records")
        except Exception as exc:  # noqa: BLE001
            print(f"  {pk_abbr}: FAILED {exc!r}")
        time.sleep(0.3)

    rows, miss_time, miss_wx = [], 0, 0
    for _, r in outdoor.iterrows():
        gp = int(r.game_pk)
        iso = pitch.get(gp)
        if not iso:
            miss_time += 1
            continue
        dt_et = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
        rounded = (dt_et + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
        key = rounded.strftime("%Y-%m-%dT%H:00")
        rec = wx_by_park.get(r.park, {}).get(key)
        if rec is None or rec[0] is None:
            miss_wx += 1
            continue
        rows.append([r.date, gp, r.park, rounded.strftime("%H:00"),
                     f"{float(rec[0]):.1f}", f"{float(rec[1]):.1f}" if rec[1] is not None else "",
                     f"{float(rec[2]):.0f}" if rec[2] is not None else "",
                     f"{float(rec[3]):.0f}" if rec[3] is not None else ""])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "game_pk", "park", "hour_et",
                    "gh_temp_c", "gh_wind_kmh", "gh_wind_deg", "gh_humidity"])
        w.writerows(rows)
    print(f"[out] {OUT}  rows={len(rows)}  no-first-pitch={miss_time}  no-archive-hour={miss_wx}")
    return 0


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

def _attach(d: pd.DataFrame, fac: pd.DataFrame) -> pd.DataFrame:
    d = d.copy(); d["game_pk"] = pd.to_numeric(d["game_pk"], errors="coerce")
    # picks_2026.csv records home/away_fi_xwoba itself since 2026-08-23 (the
    # ledger column ship) but only for rows written since then; drop from the
    # frame whatever the INCOMING factor also carries, so the factor CSV --
    # which covers every game -- is the single source and the merge never
    # suffixes the column names.  (Naming the columns here instead broke the
    # SECOND _attach call, which stripped what the first had just added.)
    overlap = [c for c in fac.columns
               if c in d.columns and c not in ("game_pk", "date", "park")]
    d = d.drop(columns=overlap)
    f = fac.copy(); f["game_pk"] = pd.to_numeric(f["game_pk"], errors="coerce")
    f = f.drop(columns=[c for c in ("date", "park") if c in f.columns]).drop_duplicates("game_pk")
    return d.merge(f, on="game_pk", how="left")


def test(l2: float, boots: int, seed: int) -> int:
    from calibration import CIRCalibrator
    from harness import (T1_SHIPPED, B1_SHIPPED, auc, build_park, fit_lr, load,
                         logloss, matrix, predict)
    from backtest import PARK_ORIENTATION_CF
    rng = np.random.default_rng(seed)

    fi = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    gh = pd.read_csv(ROOT / "data" / "candidates" / "factor_wx_gamehour.csv")
    bt = ROOT / "data" / "backtests"
    d24 = _attach(_attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fi), gh)
    d25 = _attach(_attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fi), gh)
    d26 = _attach(_attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fi), gh)

    T1 = T1_SHIPPED + ["home_fi_xwoba"]; B1 = B1_SHIPPED + ["away_fi_xwoba"]
    WXC = ["wx_temp_c", "wx_wind_kmh", "wx_humidity"]

    print("=== COVERAGE of game-hour weather (share of OUTDOOR rows) ===")
    for lab, d in (("2024", d24), ("2025", d25), ("2026", d26)):
        outdoor = d[pd.to_numeric(d.wx_is_dome, errors="coerce").fillna(0) == 0]
        cov = outdoor.gh_temp_c.notna().mean() * 100
        # how different is the input we are proposing?
        dt = (outdoor.gh_temp_c - pd.to_numeric(outdoor.wx_temp_c, errors="coerce")).abs()
        print(f"  {lab}: coverage {cov:5.1f}%  |game-hour minus 7PM| temp: "
              f"mean {dt.mean():.2f}C  p90 {dt.quantile(0.9):.2f}C  n={len(outdoor)}")

    def variant(d: pd.DataFrame) -> pd.DataFrame:
        v = d.copy()
        m = (pd.to_numeric(v.wx_is_dome, errors="coerce").fillna(0) == 0) & v.gh_temp_c.notna()
        for src, dst in (("gh_temp_c", "wx_temp_c"), ("gh_wind_kmh", "wx_wind_kmh"),
                         ("gh_humidity", "wx_humidity")):
            v.loc[m, dst] = pd.to_numeric(v.loc[m, src], errors="coerce")
        return v

    def fitpred(tr, te):
        tr, te = tr.copy(), te.copy()
        for c in ("home_fi_xwoba", "away_fi_xwoba"):
            mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
        pk, b0 = build_park(tr, 50)
        wt, mt, st = fit_lr(matrix(tr, T1, pk, b0), tr.y_t1.values, l2)
        wb, mb, sb = fit_lr(matrix(tr, B1, pk, b0), tr.y_b1.values, l2)
        raw_tr = (1 - predict(wt, mt, st, matrix(tr, T1, pk, b0))) * (1 - predict(wb, mb, sb, matrix(tr, B1, pk, b0)))
        raw_te = (1 - predict(wt, mt, st, matrix(te, T1, pk, b0))) * (1 - predict(wb, mb, sb, matrix(te, B1, pk, b0)))
        cal = CIRCalibrator.fit(list(raw_tr), list((tr.y == 0).astype(int)), n_bins=20)
        return np.array([cal.predict(float(v)) for v in raw_te])

    defs = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
            ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    GATE = 0.42
    print(f"\n=== SPLITS: base (7PM slot) vs game-hour, live 20-feature set, L2 {l2} ===")
    for lab, tr, te in defs:
        y = te.y.values
        pn0 = fitpred(tr, te)                      # p_nrfi, calibrated
        pn1 = fitpred(variant(tr), variant(te))
        p0, p1 = 1 - pn0, 1 - pn1                  # p(yrfi) for scoring vs y
        dl = np.array([logloss(y[i], p0[i]) - logloss(y[i], p1[i])
                       for i in (rng.integers(0, len(y), len(y)) for _ in range(boots))])
        print(f"  {lab:<12} dAUC {auc(y, p1) - auc(y, p0):+.4f}   "
              f"dlogloss x1000 {dl.mean()*1000:+.3f} [{np.percentile(dl,5)*1000:+.3f},{np.percentile(dl,95)*1000:+.3f}]")
        if lab == "24+25->2026":
            for nme, pn in (("BASE", pn0), ("GAME-HOUR", pn1)):
                t = te[["date", "y"]].copy(); t["pn"] = pn; t = t[t.pn < GATE]
                if len(t):
                    n1 = t.loc[t.groupby("date").pn.idxmin()]
                    print(f"    No.1 sim {nme:<9} slates={len(n1):3d}  hit={n1.y.mean():.3f}  gate bets={len(t)}")

    # ---- wind DIRECTION as a FEATURE: the decisive test ------------------
    # Raw correlations flatter every candidate (the 2026-08-20 test showed
    # the null mean is not even zero).  The standard that everything else
    # had to meet: add it to the live 20-feature set, three splits, and an
    # identical placebo arm (crosswind).  Signal = out helps in ALL splits
    # while cross does not.
    for d in (d24, d25, d26):
        m = (pd.to_numeric(d.wx_is_dome, errors="coerce").fillna(0) == 0) & d.gh_wind_deg.notna()             & d.park.isin(PARK_ORIENTATION_CF)
        ang = np.radians(pd.to_numeric(d.gh_wind_deg, errors="coerce")
                         - d.park.map(PARK_ORIENTATION_CF))
        spd = pd.to_numeric(d.gh_wind_kmh, errors="coerce")
        d["gh_wind_out"] = np.where(m, -np.cos(ang) * spd, np.nan)
        d["gh_wind_cross"] = np.where(m, np.abs(np.sin(ang)) * spd, np.nan)

    print("\n=== WIND AS A FEATURE on the live set (dome/missing -> 0 = neutral) ===")
    # defs' 24+25 concat was materialized before the wind columns existed;
    # rebuild so every frame carries them.
    defs2 = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
             ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
    def fitpred_feats(tr, te, extra):
        tr, te = tr.copy(), te.copy()
        for c in ("home_fi_xwoba", "away_fi_xwoba"):
            mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
        for c in extra:
            tr[c] = tr[c].fillna(0.0); te[c] = te[c].fillna(0.0)
        t1f, b1f = T1 + extra, B1 + extra
        pk, b0 = build_park(tr, 50)
        wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, l2)
        wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, l2)
        raw_te = (1 - predict(wt, mt, st, matrix(te, t1f, pk, b0))) *                  (1 - predict(wb, mb, sb, matrix(te, b1f, pk, b0)))
        return 1 - raw_te   # p(yrfi), uncalibrated is fine for a paired score
    for nme, extra in (("out", ["gh_wind_out"]), ("cross placebo", ["gh_wind_cross"])):
        cells, allpos = [], True
        for lab, tr, te in defs2:
            y = te.y.values
            p0 = fitpred_feats(tr, te, [])
            p1 = fitpred_feats(tr, te, extra)
            dl = np.array([logloss(y[i], p0[i]) - logloss(y[i], p1[i])
                           for i in (rng.integers(0, len(y), len(y)) for _ in range(400))])
            allpos &= dl.mean() > 0
            cells.append(f"{lab} {dl.mean()*1000:+.3f}[{np.percentile(dl,5)*1000:+.3f},{np.percentile(dl,95)*1000:+.3f}]")
        print(f"  +wind_{nme:<14} " + "  ".join(cells) + ("   ALL+" if allpos else "   -"))

    # ---- wind DIRECTION retest on the game-hour input --------------------
    print("\n=== WIND DIRECTION at game hour (placebo bar: cross must NOT match out) ===")
    for lab, d in (("2024", d24), ("2025", d25), ("2026", d26)):
        o = d[(pd.to_numeric(d.wx_is_dome, errors="coerce").fillna(0) == 0)
              & d.gh_wind_deg.notna() & d.park.isin(PARK_ORIENTATION_CF)].copy()
        if not len(o):
            print(f"  {lab}: no rows"); continue
        ang = np.radians(pd.to_numeric(o.gh_wind_deg) - o.park.map(PARK_ORIENTATION_CF))
        spd = pd.to_numeric(o.gh_wind_kmh)
        out_c = (-np.cos(ang) * spd).astype(float)
        cross = (np.abs(np.sin(ang)) * spd).astype(float)
        y = o.y.values.astype(float)
        co, cc = np.corrcoef(out_c, y)[0, 1], np.corrcoef(cross, y)[0, 1]
        print(f"  {lab}: corr(out,YRFI) {co:+.4f}   corr(cross placebo,YRFI) {cc:+.4f}   n={len(o)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--l2", type=float, default=0.50)
    ap.add_argument("--boots", type=int, default=600)
    ap.add_argument("--seed", type=int, default=20260823)
    a = ap.parse_args()
    if a.build:
        rc = build()
        if rc:
            return rc
    if a.test:
        return test(a.l2, a.boots, a.seed)
    if not (a.build or a.test):
        print("nothing to do: pass --build and/or --test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
backfill_arsenal.py -- pitch arsenal features per pitcher per season.

Adds 10 columns to each CSV (5 per pitcher, both away and home):

  away_arsenal_fb_pct    home_arsenal_fb_pct     -- fastball share (FF+SI+FT)
  away_arsenal_bb_pct    home_arsenal_bb_pct     -- breaking ball (SL+ST+CU+KC)
  away_arsenal_off_pct   home_arsenal_off_pct    -- off-speed (CH+FO+FS+SC+EP)
  away_arsenal_fb_velo   home_arsenal_fb_velo    -- avg fastball mph
  away_arsenal_velo_gap  home_arsenal_velo_gap   -- FB mph minus avg off-speed mph

Cutter (FC) is split half/half between FB and BB to keep three buckets clean.

Hits MLB statsapi pitchArsenal endpoint per (pitcher_id, season). Cached at
data/cache/pitch_arsenal/{pid}_{season}.json so re-runs are free.
"""

import csv
import json
import sys
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Install: pip install mlb-statsapi")

ROOT = Path(__file__).parent
ARS_CACHE = ROOT / "data" / "cache" / "pitch_arsenal"
SCHED_CACHE = ROOT / "data" / "cache" / "schedule"

FB_CODES  = {"FF", "FT", "SI"}                # 4-seam, 2-seam, sinker
CT_CODES  = {"FC"}                             # cutter -- split 50/50
BB_CODES  = {"SL", "ST", "CU", "KC", "SV"}    # slider, sweeper, curve, knuckle-curve, slurve
OFF_CODES = {"CH", "FO", "FS", "SC", "EP", "KN"}  # change, fork, split, screw, eephus, knuckle

DEFAULT_FB_PCT  = 0.55
DEFAULT_BB_PCT  = 0.30
DEFAULT_OFF_PCT = 0.15
DEFAULT_FB_VELO = 93.0
DEFAULT_VELO_GAP = 9.0


def fetch_arsenal(pid: int, season: int) -> dict:
    """Returns {fb_pct, bb_pct, off_pct, fb_velo, velo_gap} for pitcher."""
    cache_path = ARS_CACHE / f"{pid}_{season}.json"
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    try:
        data = statsapi.get("person", {
            "personId": pid,
            "hydrate":  f"stats(group=[pitching],type=[pitchArsenal],season={season})",
        })
    except Exception as e:
        print(f"  [warn] arsenal fetch failed pid={pid} season={season}: {e}")
        return _default_arsenal()

    splits = []
    for person in data.get("people", []):
        for sb in person.get("stats", []):
            splits.extend(sb.get("splits", []))

    if not splits:
        return _default_arsenal()

    fb_share = bb_share = off_share = ct_share = 0.0
    fb_speed_weighted = 0.0
    fb_share_weighted = 0.0
    off_speed_weighted = 0.0
    off_share_weighted = 0.0

    for s in splits:
        st = s.get("stat", {})
        code  = st.get("type", {}).get("code", "")
        pct   = float(st.get("percentage", 0) or 0)
        speed = float(st.get("averageSpeed", 0) or 0)
        if code in FB_CODES:
            fb_share += pct
            if speed > 0:
                fb_speed_weighted += pct * speed
                fb_share_weighted += pct
        elif code in CT_CODES:
            ct_share += pct
        elif code in BB_CODES:
            bb_share += pct
        elif code in OFF_CODES:
            off_share += pct
            if speed > 0:
                off_speed_weighted += pct * speed
                off_share_weighted += pct

    # Cutter splits 50/50 between FB and BB
    fb_share += ct_share * 0.5
    bb_share += ct_share * 0.5

    fb_velo = fb_speed_weighted / fb_share_weighted if fb_share_weighted > 0 else DEFAULT_FB_VELO
    off_velo = off_speed_weighted / off_share_weighted if off_share_weighted > 0 else (fb_velo - DEFAULT_VELO_GAP)
    velo_gap = fb_velo - off_velo

    out = {
        "fb_pct":   round(fb_share,  4),
        "bb_pct":   round(bb_share,  4),
        "off_pct":  round(off_share, 4),
        "fb_velo":  round(fb_velo,   2),
        "velo_gap": round(velo_gap,  2),
    }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def _default_arsenal() -> dict:
    return {
        "fb_pct":   DEFAULT_FB_PCT,
        "bb_pct":   DEFAULT_BB_PCT,
        "off_pct":  DEFAULT_OFF_PCT,
        "fb_velo":  DEFAULT_FB_VELO,
        "velo_gap": DEFAULT_VELO_GAP,
    }


def load_schedule_pids(season: int) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    if SCHED_CACHE.exists():
        for path in sorted(SCHED_CACHE.glob(f"{season}-*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                for g in d.get("games", []):
                    pk = g.get("game_pk")
                    if pk is not None:
                        out[int(pk)] = (
                            int(g.get("away_pitcher_id") or 0),
                            int(g.get("home_pitcher_id") or 0),
                        )
            except Exception:
                continue
    if out:
        return out

    # Fetch fallback (used for 2024)
    print(f"  fetching {season} schedule via API...")
    data = statsapi.get(
        "schedule",
        {
            "sportId":   1,
            "startDate": f"{season}-04-01",
            "endDate":   f"{season}-09-30",
            "hydrate":   "probablePitcher",
            "fields":    "dates,games,gamePk,teams,away,home,probablePitcher,id",
        },
    )
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            if pk is None:
                continue
            ap = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
            hp = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
            out[int(pk)] = (
                int(ap.get("id") or 0),
                int(hp.get("id") or 0),
            )
    return out


def backfill_csv(csv_path: Path, season: int, pk_to_pids: dict[int, tuple[int, int]],
                 pid_arsenal: dict[int, dict], pid_in_csv: bool):
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
        f.seek(0)
        header = next(csv.reader(f))

    new_cols = [
        "away_arsenal_fb_pct", "home_arsenal_fb_pct",
        "away_arsenal_bb_pct", "home_arsenal_bb_pct",
        "away_arsenal_off_pct", "home_arsenal_off_pct",
        "away_arsenal_fb_velo", "home_arsenal_fb_velo",
        "away_arsenal_velo_gap", "home_arsenal_velo_gap",
    ]
    for c in new_cols:
        if c not in header:
            header.append(c)

    updated = 0
    for r in rows:
        if pid_in_csv:
            try:
                away_pid = int(r.get("away_pitcher_id") or 0)
                home_pid = int(r.get("home_pitcher_id") or 0)
            except ValueError:
                away_pid = home_pid = 0
        else:
            try:
                pk = int(r.get("game_pk") or 0)
            except ValueError:
                pk = 0
            away_pid, home_pid = pk_to_pids.get(pk, (0, 0))

        away_arr = pid_arsenal.get(away_pid, _default_arsenal())
        home_arr = pid_arsenal.get(home_pid, _default_arsenal())

        r["away_arsenal_fb_pct"]   = str(away_arr["fb_pct"])
        r["home_arsenal_fb_pct"]   = str(home_arr["fb_pct"])
        r["away_arsenal_bb_pct"]   = str(away_arr["bb_pct"])
        r["home_arsenal_bb_pct"]   = str(home_arr["bb_pct"])
        r["away_arsenal_off_pct"]  = str(away_arr["off_pct"])
        r["home_arsenal_off_pct"]  = str(home_arr["off_pct"])
        r["away_arsenal_fb_velo"]  = str(away_arr["fb_velo"])
        r["home_arsenal_fb_velo"]  = str(home_arr["fb_velo"])
        r["away_arsenal_velo_gap"] = str(away_arr["velo_gap"])
        r["home_arsenal_velo_gap"] = str(home_arr["velo_gap"])
        updated += 1

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {csv_path.name}: updated={updated}")


def main():
    print("=" * 64)
    print("  Backfill pitcher arsenal features")
    print("=" * 64)

    pk_2024 = load_schedule_pids(2024)
    pk_2025 = load_schedule_pids(2025)

    # Collect unique pitcher_ids
    pids_2024 = {pid for pids in pk_2024.values() for pid in pids if pid}
    pids_2025 = {pid for pids in pk_2025.values() for pid in pids if pid}
    pids_2026: set[int] = set()
    picks_path = ROOT / "data" / "picks_2026.csv"
    with open(picks_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for col in ("away_pitcher_id", "home_pitcher_id"):
                v = r.get(col, "")
                try:
                    pid = int(v)
                    if pid:
                        pids_2026.add(pid)
                except ValueError:
                    continue
    print(f"\nUnique pitchers: 2024={len(pids_2024)} 2025={len(pids_2025)} 2026={len(pids_2026)}")

    # Fetch arsenals (with cache)
    print("\nFetching arsenals (cached)...")
    arsenal_24: dict[int, dict] = {}
    arsenal_25: dict[int, dict] = {}
    arsenal_26: dict[int, dict] = {}
    fetched = 0
    for pid in sorted(pids_2024):
        if not (ARS_CACHE / f"{pid}_2024.json").exists(): fetched += 1
        arsenal_24[pid] = fetch_arsenal(pid, 2024)
    for pid in sorted(pids_2025):
        if not (ARS_CACHE / f"{pid}_2025.json").exists(): fetched += 1
        arsenal_25[pid] = fetch_arsenal(pid, 2025)
    for pid in sorted(pids_2026):
        if not (ARS_CACHE / f"{pid}_2026.json").exists(): fetched += 1
        arsenal_26[pid] = fetch_arsenal(pid, 2026)
    print(f"  newly fetched this run: {fetched}")

    # Backfill CSVs
    print("\nBackfilling CSVs...")
    bt_24 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
    bt_25 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
    backfill_csv(bt_24, 2024, pk_2024, arsenal_24, pid_in_csv=False)
    backfill_csv(bt_25, 2025, pk_2025, arsenal_25, pid_in_csv=False)
    backfill_csv(picks_path, 2026, {}, arsenal_26, pid_in_csv=True)

    print("\nDone.\n")


if __name__ == "__main__":
    main()

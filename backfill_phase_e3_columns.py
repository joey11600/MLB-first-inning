#!/usr/bin/env python3
"""
backfill_phase_e3_columns.py -- add the 5 missing Phase D + E.3 feature
columns to picks_2026.csv (so rebet works) and add Statcast columns to
2024 + 2025 backtests (so training has them).

Columns added to picks_2026.csv:
  - home_p_last5_pitcher_nrfi  (already in 2024+2025 backtests)
  - away_p_last5_pitcher_nrfi
  - home_top3c_obp             (already in 2024+2025 backtests)
  - away_top3c_obp
  - home_plate_ump_id
  - home_plate_ump_nrfi_rate
  - home_xera                  (Statcast)
  - away_xera
  - home_whiff_pct_rank        (Statcast percentile)
  - away_whiff_pct_rank

Columns added to 2024/2025 backtests:
  - home_xera, away_xera
  - home_whiff_pct_rank, away_whiff_pct_rank

For last5_nrfi and top3c_obp on picks_2026 we need to fetch from the
backtest module's helpers (those data sources work for any date).

Idempotent: cells that already have a value are NOT overwritten.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def load_caches():
    return {
        "ump_cache": json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8")),
        "ump_rates": json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))["umpires"],
        "ump_league": json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))["league_nrfi_rate"],
        "pid_cache": json.load(open(ROOT / "data" / "pitcher_id_cache.json", encoding="utf-8")),
        "statcast":  json.load(open(ROOT / "data" / "statcast_pitcher_cache.json", encoding="utf-8")),
    }


def get_statcast(pid: str, season: str, statcast: dict) -> dict | None:
    return statcast.get(season, {}).get(pid)


def get_ump_rate(pk: str, caches: dict) -> tuple[str, float]:
    rec = caches["ump_cache"].get(str(pk))
    if not rec:
        return "", caches["ump_league"]
    ump_id = str(rec["hp_id"])
    u = caches["ump_rates"].get(ump_id)
    rate = u["shrunk_nrfi"] if u else caches["ump_league"]
    return ump_id, rate


# ---------- Statcast backfill for 2024 + 2025 backtests ----------

def backfill_backtest_statcast(season: int, caches: dict):
    path = ROOT / "data" / "backtests" / f"backtest_{season}-04-01_to_{season}-09-30.csv"
    if not path.exists():
        print(f"  {season}: missing {path}; skip")
        return
    print(f"\n  Backfilling Statcast columns into {path.name}")

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    new_cols = ["home_xera", "away_xera", "home_whiff_pct_rank", "away_whiff_pct_rank"]
    for c in new_cols:
        if c not in header: header.append(c)

    n_filled = 0
    season_str = str(season)
    for r in rows:
        pk = (r.get("game_pk") or "").strip()
        pids = caches["pid_cache"].get(pk)
        if not pids: continue
        a_pid, h_pid = str(pids[0]), str(pids[1])

        for col, pid in [("away_xera", a_pid), ("home_xera", h_pid)]:
            if r.get(col, "").strip(): continue
            sc = get_statcast(pid, season_str, caches["statcast"])
            if sc and sc.get("xera") is not None:
                r[col] = f"{sc['xera']:.3f}"
                n_filled += 1

        for col, pid in [("away_whiff_pct_rank", a_pid), ("home_whiff_pct_rank", h_pid)]:
            if r.get(col, "").strip(): continue
            sc = get_statcast(pid, season_str, caches["statcast"])
            if sc and sc.get("whiff_pct_rank") is not None:
                r[col] = str(int(sc["whiff_pct_rank"]))
                n_filled += 1

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"    Filled {n_filled} cells")


# ---------- Full backfill for picks_2026.csv (5 new columns) ----------

def backfill_picks_2026(caches: dict):
    path = ROOT / "data" / "picks_2026.csv"
    print(f"\n  Backfilling all Phase D + E.3 columns into {path.name}")

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    new_cols = [
        "home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi",
        "home_top3c_obp", "away_top3c_obp",
        "home_top3c_slg", "away_top3c_slg",   # new: power signal
        "home_top3c_iso", "away_top3c_iso",   # new: isolated power
        "home_plate_ump_id", "home_plate_ump_nrfi_rate",
        "home_xera", "away_xera",
        "home_whiff_pct_rank", "away_whiff_pct_rank",
    ]
    for c in new_cols:
        if c not in header: header.append(c)

    # Statcast + ump from caches (instant)
    n_statcast = n_ump = 0
    for r in rows:
        pk = (r.get("game_pk") or "").strip()
        season_str = (r.get("season") or "2026").strip()
        pids = caches["pid_cache"].get(pk)

        # Umpire
        if not r.get("home_plate_ump_id", "").strip():
            ump_id, rate = get_ump_rate(pk, caches)
            r["home_plate_ump_id"] = ump_id
            r["home_plate_ump_nrfi_rate"] = f"{rate:.4f}"
            if ump_id: n_ump += 1

        # Statcast
        if pids:
            a_pid, h_pid = str(pids[0]), str(pids[1])
            for col, pid in [("away_xera", a_pid), ("home_xera", h_pid)]:
                if r.get(col, "").strip(): continue
                sc = get_statcast(pid, season_str, caches["statcast"])
                if sc and sc.get("xera") is not None:
                    r[col] = f"{sc['xera']:.3f}"
                    n_statcast += 1
            for col, pid in [("away_whiff_pct_rank", a_pid), ("home_whiff_pct_rank", h_pid)]:
                if r.get(col, "").strip(): continue
                sc = get_statcast(pid, season_str, caches["statcast"])
                if sc and sc.get("whiff_pct_rank") is not None:
                    r[col] = str(int(sc["whiff_pct_rank"]))
                    n_statcast += 1

    print(f"    Statcast filled: {n_statcast} cells")
    print(f"    Umpire filled: {n_ump} games")

    # last5_pitcher_nrfi and top3c_obp -- requires API fetches via backtest module
    print(f"    Fetching pitcher last5 NRFI + top3c OBP via backtest helpers...")
    try:
        from backtest import pitcher_last_n_first_inning, current_season_top3_stats, fetch_top3_batters
    except ImportError as e:
        print(f"    (skipping last5/top3c fetch: {e})")
        save(path, header, rows); return

    n_last5 = n_top3c = 0
    for i, r in enumerate(rows):
        if i % 30 == 0:
            print(f"      ... {i}/{len(rows)}")
        season = int((r.get("season") or "2026").strip() or 2026)
        date_iso = (r.get("date") or "").strip()
        pk = (r.get("game_pk") or "").strip()
        if not date_iso or not pk: continue
        # Pitcher_id cache is only needed for last5_nrfi.  top3c columns
        # need only the game_pk + date (lineup is fetched directly), so
        # don't gate the whole row on pid availability.
        pids = caches["pid_cache"].get(pk)
        a_pid = pids[0] if pids else 0
        h_pid = pids[1] if pids else 0

        # Pitcher last-5 NRFI
        if not r.get("home_p_last5_pitcher_nrfi", "").strip() and h_pid:
            try:
                d = pitcher_last_n_first_inning(h_pid, date_iso, season, n=5)
                if d and d.get("pitcher_nrfi_rate") is not None:
                    r["home_p_last5_pitcher_nrfi"] = f"{d['pitcher_nrfi_rate']:.4f}"
                    n_last5 += 1
            except Exception:
                pass
        if not r.get("away_p_last5_pitcher_nrfi", "").strip() and a_pid:
            try:
                d = pitcher_last_n_first_inning(a_pid, date_iso, season, n=5)
                if d and d.get("pitcher_nrfi_rate") is not None:
                    r["away_p_last5_pitcher_nrfi"] = f"{d['pitcher_nrfi_rate']:.4f}"
                    n_last5 += 1
            except Exception:
                pass

        # Top-3 batters' current-season OBP/SLG/ISO -- fetch via game's top-3 lineup.
        # A game where we already have top3c_obp may still be missing SLG/ISO (the
        # earlier backfill only wrote OBP); detect that case and refill.
        need_h_obp = not r.get("home_top3c_obp", "").strip() or r.get("home_top3c_obp", "").startswith("0.318")
        need_a_obp = not r.get("away_top3c_obp", "").strip() or r.get("away_top3c_obp", "").startswith("0.318")
        need_h_slg = not r.get("home_top3c_slg", "").strip()
        need_a_slg = not r.get("away_top3c_slg", "").strip()
        need_h_iso = not r.get("home_top3c_iso", "").strip()
        need_a_iso = not r.get("away_top3c_iso", "").strip()
        if need_h_obp or need_a_obp or need_h_slg or need_a_slg or need_h_iso or need_a_iso:
            try:
                top3 = fetch_top3_batters(int(pk))
                home_top3 = top3.get("home_top3", [])
                away_top3 = top3.get("away_top3", [])
                if home_top3 and (need_h_obp or need_h_slg or need_h_iso):
                    h_agg = current_season_top3_stats(home_top3, date_iso, season)
                    if h_agg:
                        if need_h_obp and h_agg.get("obp") is not None:
                            r["home_top3c_obp"] = f"{h_agg['obp']:.4f}"; n_top3c += 1
                        if need_h_slg and h_agg.get("slg") is not None:
                            r["home_top3c_slg"] = f"{h_agg['slg']:.4f}"; n_top3c += 1
                        if need_h_iso and h_agg.get("iso") is not None:
                            r["home_top3c_iso"] = f"{h_agg['iso']:.4f}"; n_top3c += 1
                if away_top3 and (need_a_obp or need_a_slg or need_a_iso):
                    a_agg = current_season_top3_stats(away_top3, date_iso, season)
                    if a_agg:
                        if need_a_obp and a_agg.get("obp") is not None:
                            r["away_top3c_obp"] = f"{a_agg['obp']:.4f}"; n_top3c += 1
                        if need_a_slg and a_agg.get("slg") is not None:
                            r["away_top3c_slg"] = f"{a_agg['slg']:.4f}"; n_top3c += 1
                        if need_a_iso and a_agg.get("iso") is not None:
                            r["away_top3c_iso"] = f"{a_agg['iso']:.4f}"; n_top3c += 1
            except Exception:
                pass

    print(f"    last5_nrfi filled: {n_last5}")
    print(f"    top3c_obp filled: {n_top3c}")

    save(path, header, rows)


def save(path: Path, header: list, rows: list):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    caches = load_caches()
    print(f"  Caches loaded: ump={len(caches['ump_cache'])}, pid={len(caches['pid_cache'])}, statcast_seasons={list(caches['statcast'].keys())}")

    # Add Statcast columns to 2024/2025 backtests
    for season in [2024, 2025]:
        backfill_backtest_statcast(season, caches)

    # Full backfill for picks_2026.csv
    backfill_picks_2026(caches)

    print("\n  Done.")


if __name__ == "__main__":
    main()

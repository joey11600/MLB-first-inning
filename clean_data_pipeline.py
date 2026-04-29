#!/usr/bin/env python3
"""
clean_data_pipeline.py -- aggressive backfill that overwrites placeholder
defaults with real data wherever possible.

The previous backfill only filled cells that were EMPTY (idempotent).  This
script also overwrites cells that are sitting on suspicious LEAGUE_AVG
defaults so the model never trains/predicts on fake data.

Targets every CSV that feeds the model:
  - data/picks_2026.csv
  - data/backtests/backtest_2024-04-01_to_2024-09-30.csv
  - data/backtests/backtest_2025-04-01_to_2025-09-30.csv

Features cleaned (priority order):
  1. Pitcher ID (resolve via MLB schedule when missing)
  2. last5 / last10 pitcher NRFI (from gamelogs, current+prior season)
  3. top3c_obp / top3c_slg / top3c_iso (from lineup + current_season_top3_stats)
  4. xera / whiff_pct_rank (from Statcast cache, prior-season fallback)
  5. team OBP / SLG / RPG (from team_stats, prior-season fallback)
  6. Umpire NRFI rate (from umpire cache)

A cell is overwritten if it equals the LEAGUE_AVG default (within 1e-4)
AND we can fetch a real value.  If real data is unavailable, the existing
default is left alone but logged so we can quantify residual placeholder
rate.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# League-average defaults (must match predictor.py and recalibrate_v2.py)
LEAGUE_AVG_OBP = 0.318
LEAGUE_AVG_SLG = 0.414
LEAGUE_AVG_ISO = 0.169
LEAGUE_AVG_RPG = 4.45
LEAGUE_AVG_OPS = 0.728
LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_WHIP = 1.25
LEAGUE_AVG_BB9 = 3.0
LEAGUE_AVG_HR9 = 1.2
LEAGUE_AVG_K9 = 8.9
LEAGUE_NRFI_RATE = 0.5183

EPS = 1e-4


def is_default(val: str, default: float) -> bool:
    """True if cell value parses to the default (within EPS)."""
    if val is None or str(val).strip() == "":
        return True
    try:
        return abs(float(val) - default) < EPS
    except (TypeError, ValueError):
        return False


def is_blank(val: str) -> bool:
    return val is None or str(val).strip() == ""


def load_caches():
    return {
        "pid_cache": json.load(open(ROOT / "data" / "pitcher_id_cache.json", encoding="utf-8")),
        "ump_cache": json.load(open(ROOT / "data" / "umpire_cache.json", encoding="utf-8")),
        "ump_rates": json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))["umpires"],
        "ump_league": json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))["league_nrfi_rate"],
        "statcast": json.load(open(ROOT / "data" / "statcast_pitcher_cache.json", encoding="utf-8")),
    }


# ---------------------------------------------------------------------------
# Lazy imports of backtest helpers (heavy; only when needed)
# ---------------------------------------------------------------------------

_BT = None

def _get_backtest_helpers():
    global _BT
    if _BT is None:
        import backtest as bt
        _BT = bt
    return _BT


def get_pids_for_row(r: dict, pid_cache: dict) -> tuple[int, int]:
    """Return (away_pid, home_pid) using pid_cache, falling back to row's own
    pitcher_id columns, and finally an MLB schedule lookup."""
    pk = (r.get("game_pk") or "").strip()
    pids = pid_cache.get(pk)
    a_pid = pids[0] if pids else 0
    h_pid = pids[1] if pids else 0

    if not a_pid:
        v = (r.get("away_pitcher_id") or "").strip()
        if v and v != "0":
            try: a_pid = int(v)
            except (TypeError, ValueError): pass
    if not h_pid:
        v = (r.get("home_pitcher_id") or "").strip()
        if v and v != "0":
            try: h_pid = int(v)
            except (TypeError, ValueError): pass

    return a_pid, h_pid


def get_statcast_with_fallback(pid: str, season: str, statcast: dict,
                               historical: bool = False) -> dict | None:
    """Return statcast row for pid; fall back to prior season if current missing.

    When `historical=True` the current-season cache is SKIPPED entirely
    and only the prior-season value is returned.  This eliminates leakage
    when backfilling rows whose game date is older than the cache build
    timestamp (current-season Statcast leaderboards are a "today" snapshot
    and don't expose historical as-of-date values).

    Useful for early-season pitchers without enough IP for current-year
    qualifying (prior-season fallback) and for historical-game backfill
    (no future leakage)."""
    if not historical:
        rec = statcast.get(season, {}).get(str(pid))
        if rec and rec.get("xera") is not None:
            return rec
    # Prior-season fallback (always safe, never leaks)
    try:
        prior_season = str(int(season) - 1)
    except (TypeError, ValueError):
        return None
    rec = statcast.get(prior_season, {}).get(str(pid))
    if rec and rec.get("xera") is not None:
        return rec
    return None


# Team abbr -> MLB team_id (matches mlb_first_inning_predictor.TEAM_ID_TO_ABBR)
TEAM_ABBR_TO_ID = {
    "LAA": 108, "ARI": 109, "BAL": 110, "BOS": 111, "CHC": 112, "CIN": 113,
    "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC":  118, "LAD": 119,
    "WSH": 120, "NYM": 121, "OAK": 133, "PIT": 134, "SD":  135, "SEA": 136,
    "SF":  137, "STL": 138, "TB":  139, "TEX": 140, "TOR": 141, "MIN": 142,
    "PHI": 143, "ATL": 144, "CWS": 145, "MIA": 146, "NYY": 147, "MIL": 158,
}


def fetch_team_batting_with_prior(team_abbr: str, season: int) -> dict | None:
    """For a team abbreviation, get OBP/SLG/RPG using prior-season data
    (fully sampled, no leakage for current-season predictions).  Returns None
    if team_abbr is unknown.  Cached in backtest._cache_get."""
    tid = TEAM_ABBR_TO_ID.get(team_abbr)
    if not tid:
        return None
    bt = _get_backtest_helpers()
    try:
        stats, quality = bt.prior_season_team_batting(tid, season)
        return stats
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Row-level cleanup
# ---------------------------------------------------------------------------

def clean_picks_2026(caches: dict):
    path = ROOT / "data" / "picks_2026.csv"
    print(f"\n  Cleaning {path.name} (overwrite-defaults mode)")

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    # Schema columns we'll fill
    new_cols = [
        "home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi",
        "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
        "home_top3c_obp", "away_top3c_obp",
        "home_top3c_slg", "away_top3c_slg",
        "home_top3c_iso", "away_top3c_iso",
        "home_plate_ump_id", "home_plate_ump_nrfi_rate",
        "home_xera", "away_xera",
        "home_whiff_pct_rank", "away_whiff_pct_rank",
    ]
    for c in new_cols:
        if c not in header:
            header.append(c)

    bt = _get_backtest_helpers()

    n_pid_recovered = 0
    n_last_filled = 0
    n_top3c_filled = 0
    n_ump_filled = 0
    n_statcast_filled = 0
    n_obp_recovered = 0
    n_slg_recovered = 0

    # --- Pre-computation: fetch team batting once per (team_abbr, season) ---
    team_bat_cache: dict = {}
    def get_team_bat(abbr: str, season: int) -> dict | None:
        key = (abbr, season)
        if key in team_bat_cache:
            return team_bat_cache[key]
        b = fetch_team_batting_with_prior(abbr, season)
        team_bat_cache[key] = b
        return b

    for i, r in enumerate(rows):
        if i % 30 == 0:
            print(f"      ... {i}/{len(rows)}")

        date_iso = (r.get("date") or "").strip()
        season_str = (r.get("season") or "2026").strip() or "2026"
        try: season = int(season_str)
        except (TypeError, ValueError): season = 2026
        pk = (r.get("game_pk") or "").strip()
        if not date_iso or not pk: continue

        a_pid, h_pid = get_pids_for_row(r, caches["pid_cache"])

        # Persist resolved pitcher IDs back into the row for future runs
        if a_pid and not (r.get("away_pitcher_id") or "").strip():
            r["away_pitcher_id"] = str(a_pid); n_pid_recovered += 1
        if h_pid and not (r.get("home_pitcher_id") or "").strip():
            r["home_pitcher_id"] = str(h_pid); n_pid_recovered += 1

        # ---- last5 / last10 pitcher NRFI ----
        for col, pid, n in [
            ("home_p_last5_pitcher_nrfi",  h_pid, 5),
            ("away_p_last5_pitcher_nrfi",  a_pid, 5),
            ("home_p_last10_pitcher_nrfi", h_pid, 10),
            ("away_p_last10_pitcher_nrfi", a_pid, 10),
        ]:
            cur = (r.get(col) or "").strip()
            need = is_blank(cur) or is_default(cur, LEAGUE_NRFI_RATE)
            if need and pid:
                try:
                    d = bt.pitcher_last_n_first_inning(int(pid), date_iso, season, n=n)
                    if d and d.get("pitcher_nrfi_rate") is not None:
                        r[col] = f"{d['pitcher_nrfi_rate']:.4f}"
                        n_last_filled += 1
                except Exception:
                    pass

        # ---- top3c OBP / SLG / ISO ----
        need_top3c = (
            is_blank(r.get("home_top3c_obp")) or is_default(r.get("home_top3c_obp"), LEAGUE_AVG_OBP) or
            is_blank(r.get("away_top3c_obp")) or is_default(r.get("away_top3c_obp"), LEAGUE_AVG_OBP) or
            is_blank(r.get("home_top3c_slg")) or is_default(r.get("home_top3c_slg"), LEAGUE_AVG_SLG) or
            is_blank(r.get("away_top3c_slg")) or is_default(r.get("away_top3c_slg"), LEAGUE_AVG_SLG) or
            is_blank(r.get("home_top3c_iso")) or is_default(r.get("home_top3c_iso"), LEAGUE_AVG_ISO) or
            is_blank(r.get("away_top3c_iso")) or is_default(r.get("away_top3c_iso"), LEAGUE_AVG_ISO)
        )
        if need_top3c:
            try:
                top3 = bt.fetch_top3_batters(int(pk))
                if top3.get("home_top3"):
                    h_agg = bt.current_season_top3_stats(top3["home_top3"], date_iso, season)
                    if h_agg:
                        for k, default in [("obp", LEAGUE_AVG_OBP), ("slg", LEAGUE_AVG_SLG), ("iso", LEAGUE_AVG_ISO)]:
                            v = h_agg.get(k)
                            if v is not None:
                                col = f"home_top3c_{k}"
                                r[col] = f"{float(v):.4f}"
                                n_top3c_filled += 1
                if top3.get("away_top3"):
                    a_agg = bt.current_season_top3_stats(top3["away_top3"], date_iso, season)
                    if a_agg:
                        for k, default in [("obp", LEAGUE_AVG_OBP), ("slg", LEAGUE_AVG_SLG), ("iso", LEAGUE_AVG_ISO)]:
                            v = a_agg.get(k)
                            if v is not None:
                                col = f"away_top3c_{k}"
                                r[col] = f"{float(v):.4f}"
                                n_top3c_filled += 1
            except Exception:
                pass

        # ---- Umpire ----
        if is_blank(r.get("home_plate_ump_id")):
            ump_cache = caches["ump_cache"]
            rec = ump_cache.get(str(pk))
            if rec:
                ump_id = str(rec["hp_id"])
                u = caches["ump_rates"].get(ump_id)
                rate = u["shrunk_nrfi"] if u else caches["ump_league"]
                r["home_plate_ump_id"] = ump_id
                r["home_plate_ump_nrfi_rate"] = f"{rate:.4f}"
                n_ump_filled += 1

        # ---- Statcast (xera, whiff_pct_rank) ----
        # For graded games (historical), force prior-season Statcast only --
        # the current-season cache is a "today" snapshot and using it for
        # past games leaks future data into historical evaluation.  For
        # ungraded (today's slate, postponed), use current-season since
        # that matches what predict-time would have seen.
        is_graded = (r.get("graded_result") or "").strip().upper() in ("WIN", "LOSS", "PASS")
        for col_xera, col_whiff, pid in [
            ("home_xera", "home_whiff_pct_rank", h_pid),
            ("away_xera", "away_whiff_pct_rank", a_pid),
        ]:
            need_xera = is_blank(r.get(col_xera)) or is_default(r.get(col_xera), LEAGUE_AVG_ERA)
            need_whiff = is_blank(r.get(col_whiff)) or is_default(r.get(col_whiff), 50.0)
            if (need_xera or need_whiff) and pid:
                sc = get_statcast_with_fallback(
                    str(pid), season_str, caches["statcast"], historical=is_graded,
                )
                if sc:
                    if need_xera and sc.get("xera") is not None:
                        r[col_xera] = f"{sc['xera']:.3f}"
                        n_statcast_filled += 1
                    if need_whiff and sc.get("whiff_pct_rank") is not None:
                        r[col_whiff] = str(int(sc["whiff_pct_rank"]))
                        n_statcast_filled += 1

        # ---- Team OBP / SLG (overwrite default 0.318/0.414 with real data) ----
        away_abbr = (r.get("away_team") or "").strip()
        home_abbr = (r.get("home_team") or "").strip()
        for abbr, obp_col, slg_col in [
            (away_abbr, "away_obp", "away_slg"),
            (home_abbr, "home_obp", "home_slg"),
        ]:
            if not abbr: continue
            need_obp = is_default(r.get(obp_col), LEAGUE_AVG_OBP)
            need_slg = is_default(r.get(slg_col), LEAGUE_AVG_SLG)
            if need_obp or need_slg:
                tb = get_team_bat(abbr, season)
                if tb:
                    if need_obp and tb.get("obp") is not None:
                        r[obp_col] = f"{float(tb['obp']):.3f}"
                        n_obp_recovered += 1
                    if need_slg and tb.get("slg") is not None:
                        r[slg_col] = f"{float(tb['slg']):.3f}"
                        n_slg_recovered += 1

    print(f"\n  Pid recovered: {n_pid_recovered}")
    print(f"  last_n_nrfi filled: {n_last_filled}")
    print(f"  top3c filled: {n_top3c_filled}")
    print(f"  ump filled: {n_ump_filled}")
    print(f"  statcast filled: {n_statcast_filled}")
    print(f"  team obp recovered: {n_obp_recovered}")
    print(f"  team slg recovered: {n_slg_recovered}")

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean_backtest_statcast(season: int, caches: dict):
    """Backfill xera + whiff_pct_rank in backtest CSV using current+prior Statcast."""
    path = ROOT / "data" / "backtests" / f"backtest_{season}-04-01_to_{season}-09-30.csv"
    if not path.exists():
        return
    print(f"\n  Backfilling Statcast for {path.name}")
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    season_str = str(season)
    n = 0
    for r in rows:
        pk = (r.get("game_pk") or "").strip()
        a_pid, h_pid = get_pids_for_row(r, caches["pid_cache"])
        for col_xera, col_whiff, pid in [
            ("home_xera", "home_whiff_pct_rank", h_pid),
            ("away_xera", "away_whiff_pct_rank", a_pid),
        ]:
            if not pid: continue
            need_xera = is_blank(r.get(col_xera)) or is_default(r.get(col_xera), LEAGUE_AVG_ERA)
            need_whiff = is_blank(r.get(col_whiff)) or is_default(r.get(col_whiff), 50.0)
            if not (need_xera or need_whiff): continue
            sc = get_statcast_with_fallback(str(pid), season_str, caches["statcast"])
            if sc:
                if need_xera and sc.get("xera") is not None:
                    r[col_xera] = f"{sc['xera']:.3f}"; n += 1
                if need_whiff and sc.get("whiff_pct_rank") is not None:
                    r[col_whiff] = str(int(sc["whiff_pct_rank"])); n += 1
    print(f"    Filled {n} cells")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    caches = load_caches()
    print(f"  Caches loaded: pid={len(caches['pid_cache'])}, ump={len(caches['ump_cache'])}, statcast={list(caches['statcast'].keys())}")
    clean_picks_2026(caches)
    for season in [2024, 2025]:
        clean_backtest_statcast(season, caches)
    print("\n  Done.")


if __name__ == "__main__":
    main()

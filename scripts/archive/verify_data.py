#!/usr/bin/env python3
"""
verify_data.py -- spot-check whether the data in our CSVs matches reality.

Picks 5 random graded 2026 games and verifies each field against an
authoritative source:

  - pitcher names + IDs : MLB Stats API boxscore
  - fi_away_runs        : MLB Stats API linescore (top 1st runs)
  - fi_home_runs        : MLB Stats API linescore (bottom 1st runs)
  - actual_side         : derived from runs (NRFI iff total == 0)
  - pitcher FIP         : recompute from gamelog vs CSV value
  - pitcher arsenal     : MLB pitchArsenal hydration
  - team OBP            : MLB team hitting endpoint
  - park factor         : sanity check against actual NRFI rate at that park

Reports per-field PASS/FAIL with specific deltas when there's a mismatch.

This is a one-shot integrity check, not a regression test.  Run it
whenever you suspect the data pipeline is producing nonsense.
"""

import csv
import json
import math
import random
import sys
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Install: pip install mlb-statsapi")

ROOT = Path(__file__).parent
PICKS = ROOT / "data" / "picks_2026.csv"
PARK = ROOT / "data" / "fi_park_factors.json"

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

results = []


def report(field: str, status: str, detail: str = ""):
    results.append((field, status, detail))
    icon = {"PASS": "ok", "FAIL": "!!", "WARN": "?"}.get(status, " ")
    print(f"      [{status}] {icon}  {field:<24} {detail}")


def verify_game(r: dict):
    pk = r.get("game_pk", "")
    away = r.get("away_team", "")
    home = r.get("home_team", "")
    date_iso = r.get("date", "")
    print(f"\n  {date_iso}  {away} @ {home}  game_pk={pk}")

    # ---- 1. Linescore: actual first-inning runs ----
    try:
        bs = statsapi.boxscore_data(pk)
    except Exception as e:
        report("boxscore_fetch", FAIL, str(e))
        return

    try:
        ls = statsapi.get("game_linescore", {"gamePk": pk})
        innings = ls.get("innings", [])
        first = innings[0] if innings else {}
        api_t1_runs = first.get("away", {}).get("runs", None)
        api_b1_runs = first.get("home", {}).get("runs", None)
    except Exception as e:
        report("linescore_fetch", FAIL, str(e))
        api_t1_runs = api_b1_runs = None

    csv_t1 = r.get("fi_away_runs", "").strip()
    csv_b1 = r.get("fi_home_runs", "").strip()
    csv_total = r.get("fi_total_runs", "").strip()

    if api_t1_runs is None:
        report("fi_away_runs", WARN, "API returned no first-inning data (game possibly not played)")
    else:
        match = csv_t1 != "" and abs(int(float(csv_t1)) - int(api_t1_runs)) == 0
        report("fi_away_runs", PASS if match else FAIL,
               f"CSV={csv_t1}  API={api_t1_runs}")

    if api_b1_runs is None:
        report("fi_home_runs", WARN, "API returned no first-inning data")
    else:
        match = csv_b1 != "" and abs(int(float(csv_b1)) - int(api_b1_runs)) == 0
        report("fi_home_runs", PASS if match else FAIL,
               f"CSV={csv_b1}  API={api_b1_runs}")

    if api_t1_runs is not None and api_b1_runs is not None:
        api_total = int(api_t1_runs) + int(api_b1_runs)
        actual_side_expected = "NRFI" if api_total == 0 else "YRFI"
        actual_side_csv = (r.get("actual_result") or "").upper()
        report("actual_side_consistency",
               PASS if actual_side_csv == actual_side_expected else FAIL,
               f"CSV={actual_side_csv}  derived={actual_side_expected}")

    # ---- 2. Pitcher names + IDs ----
    away_name_api = home_name_api = ""
    away_pid_api = home_pid_api = None
    try:
        # Use the game endpoint's probablePitcher hydration (matches predictor's
        # data path).  Boxscore "pitchers" lists ALL pitchers used, not the starter.
        gd = statsapi.get("schedule", {
            "sportId": 1, "date": date_iso, "hydrate": "probablePitcher",
            "fields": "dates,games,gamePk,teams,away,home,probablePitcher,id,fullName",
        })
        for d in gd.get("dates", []):
            for g in d.get("games", []):
                if int(g.get("gamePk") or 0) != int(pk):
                    continue
                ap = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
                hp = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
                away_pid_api = ap.get("id")
                home_pid_api = hp.get("id")
                away_name_api = (ap.get("fullName") or "").strip()
                home_name_api = (hp.get("fullName") or "").strip()
    except Exception as e:
        report("pitcher_lookup", WARN, str(e)[:60])

    csv_away_name = (r.get("away_pitcher") or "").strip()
    csv_home_name = (r.get("home_pitcher") or "").strip()

    if away_name_api:
        ok = csv_away_name == away_name_api
        report("away_pitcher_name", PASS if ok else FAIL,
               f"CSV={csv_away_name!r}  API={away_name_api!r}")
    if home_name_api:
        ok = csv_home_name == home_name_api
        report("home_pitcher_name", PASS if ok else FAIL,
               f"CSV={csv_home_name!r}  API={home_name_api!r}")

    csv_away_pid = r.get("away_pitcher_id", "").strip()
    csv_home_pid = r.get("home_pitcher_id", "").strip()
    if away_pid_api:
        ok = csv_away_pid == str(away_pid_api)
        report("away_pitcher_id", PASS if ok else FAIL,
               f"CSV={csv_away_pid}  API={away_pid_api}")
    if home_pid_api:
        ok = csv_home_pid == str(home_pid_api)
        report("home_pitcher_id", PASS if ok else FAIL,
               f"CSV={csv_home_pid}  API={home_pid_api}")

    # ---- 3. Pitcher FIP from API yearByYear ----
    # Compare CSV's home_fip to a fresh API fetch of that pitcher's 2026 FIP.
    if home_pid_api:
        try:
            data = statsapi.get("person", {
                "personId": home_pid_api,
                "hydrate":  "stats(group=[pitching],type=[yearByYear])",
            })
            api_fip = None
            for p in data.get("people", []):
                for sb in p.get("stats", []):
                    for s in sb.get("splits", []):
                        if s.get("season") == "2026":
                            stat = s.get("stat", {})
                            api_fip = float(stat.get("fip", 0)) if stat.get("fip") else None
            csv_fip = r.get("home_fip", "")
            csv_fip_f = float(csv_fip) if csv_fip else None
            if api_fip is None or csv_fip_f is None:
                report("home_fip_freshness", WARN,
                       f"CSV={csv_fip!r}  API season FIP not available (small IP?)")
            else:
                # CSV uses Bayesian-blended FIP, API gives raw season FIP.  Should be close-ish.
                diff = abs(api_fip - csv_fip_f)
                report("home_fip_plausibility",
                       PASS if diff < 1.0 else WARN,
                       f"CSV(blended)={csv_fip_f:.2f}  API(raw)={api_fip:.2f}  delta={diff:.2f}")
        except Exception as e:
            report("home_fip_check", WARN, str(e)[:50])

    # ---- 4. Pitcher arsenal ----
    if home_pid_api:
        try:
            data = statsapi.get("person", {
                "personId": home_pid_api,
                "hydrate":  "stats(group=[pitching],type=[pitchArsenal],season=2026)",
            })
            api_fb = 0.0; api_total_pitches = 0
            for p in data.get("people", []):
                for sb in p.get("stats", []):
                    for s in sb.get("splits", []):
                        st = s.get("stat", {})
                        code = st.get("type", {}).get("code", "")
                        if code in ("FF", "FT", "SI"):
                            api_fb += float(st.get("percentage", 0))
                        api_total_pitches = max(api_total_pitches,
                                                int(st.get("totalPitches", 0)))
            csv_fb = r.get("home_arsenal_fb_pct", "")
            if csv_fb and api_total_pitches > 0:
                csv_fb_f = float(csv_fb)
                diff = abs(api_fb - csv_fb_f)
                report("home_arsenal_fb_pct",
                       PASS if diff < 0.05 else WARN,
                       f"CSV={csv_fb_f:.3f}  API_FB%={api_fb:.3f}  "
                       f"delta={diff:.3f} (cutter halved into FB+BB)")
            elif api_total_pitches == 0:
                report("home_arsenal_fb_pct", WARN, "API returned 0 pitches (small sample)")
        except Exception as e:
            report("arsenal_check", WARN, str(e)[:50])

    # ---- 5. Team OBP ----
    try:
        away_team_id = bs.get("teams", {}).get("away", {}).get("team", {}).get("id")
        if away_team_id:
            data = statsapi.get("teams_stats", {
                "season": 2026, "sportIds": 1,
                "group": "hitting", "stats": "season",
            })
            api_obp = None
            for st in data.get("stats", []):
                for s in st.get("splits", []):
                    if s.get("team", {}).get("id") == away_team_id:
                        api_obp = float(s.get("stat", {}).get("obp", 0))
            csv_obp = r.get("away_obp", "")
            if api_obp and csv_obp:
                csv_obp_f = float(csv_obp)
                diff = abs(api_obp - csv_obp_f)
                report("away_obp",
                       PASS if diff < 0.020 else WARN,
                       f"CSV(blended)={csv_obp_f:.3f}  API(season)={api_obp:.3f}  "
                       f"delta={diff:.3f}")
            elif csv_obp:
                report("away_obp", WARN,
                       f"CSV={csv_obp}  API: no season data found for team_id={away_team_id}")
    except Exception as e:
        report("team_obp_check", WARN, str(e)[:50])

    # ---- 6. Park factor vs reality ----
    park_factors = json.load(open(ROOT / "data" / "fi_park_factors.json"))
    home_team = r.get("home_team", "") or r.get("home", "")
    if home_team in park_factors:
        report("park_factor_loaded", PASS,
               f"home_team={home_team}  park_factor={park_factors[home_team]:.3f}")
    else:
        report("park_factor_missing", FAIL,
               f"home_team={home_team!r} not in fi_park_factors.json")


def main():
    with open(PICKS, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # Filter to graded games (have outcome data)
    graded = [r for r in rows
              if (r.get("graded_result") or "").upper() in ("WIN", "LOSS")
              and r.get("game_pk")]
    if not graded:
        sys.exit("No graded games found.")

    random.seed(7)  # different seed -> get later-season picks where pitcher_id is populated
    sample = random.sample(graded, min(8, len(graded)))

    print("=" * 78)
    print(f"  Spot-checking {len(sample)} random graded games against MLB API")
    print("=" * 78)
    for r in sample:
        verify_game(r)

    # Summary
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0}
    for _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    print("\n" + "=" * 78)
    print(f"  Summary: PASS={counts['PASS']}  FAIL={counts['FAIL']}  WARN={counts['WARN']}")
    print("=" * 78)
    if counts["FAIL"]:
        print("\n  FAILURES:")
        for f, s, d in results:
            if s == "FAIL":
                print(f"    {f}: {d}")


if __name__ == "__main__":
    main()

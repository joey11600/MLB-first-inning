#!/usr/bin/env python3
"""
backfill_top3_splits.py -- fetch top-3 lineup + per-batter vs-RHP/LHP splits
for every 2026 graded game and append matchup features to picks_2026.csv.

Adds two columns:
  away_top3_ops_vs_oppHand : avg OPS of away top-3 batters vs the home pitcher's hand
  home_top3_ops_vs_oppHand : avg OPS of home top-3 batters vs the away pitcher's hand

Caches:
  data/cache/lineup/{game_pk}.json       -- {away: [pid,...], home: [pid,...]}
  data/cache/batter_splits/{pid}_{season}.json
                                         -- {vs_R: {ops, ab}, vs_L: {ops, ab}}
  data/cache/pitcher_hand/{pid}_{season}.json
                                         -- {throws: 'L' | 'R'}
"""

import csv
import json
import sys
import time
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Install: pip install mlb-statsapi")

ROOT = Path(__file__).resolve().parents[2]  # repo root (script lives at scripts/archive/)
LINEUP_CACHE  = ROOT / "data" / "cache" / "lineup"
SPLITS_CACHE  = ROOT / "data" / "cache" / "batter_splits"
PHAND_CACHE   = ROOT / "data" / "cache" / "pitcher_hand"
SCHED_CACHE   = ROOT / "data" / "cache" / "schedule"
PICKS_PATH    = ROOT / "data" / "picks_2026.csv"


def load_schedule_pid_map(season: int) -> dict[int, tuple[int, int]]:
    """Build {gamePk -> (away_pid, home_pid)} from cached schedule files."""
    out: dict[int, tuple[int, int]] = {}
    if not SCHED_CACHE.exists():
        return out
    for path in sorted(SCHED_CACHE.glob(f"{season}-*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for g in d.get("games", []):
            pk = g.get("game_pk")
            if pk is None:
                continue
            out[int(pk)] = (
                int(g.get("away_pitcher_id") or 0),
                int(g.get("home_pitcher_id") or 0),
            )
    return out

LEAGUE_AVG_OPS = 0.720  # league-average OPS fallback


# ---------------------------------------------------------------------------
# Lineup fetch (top-3 batters per side per game)
# ---------------------------------------------------------------------------

def fetch_lineup(game_pk: int) -> dict:
    """Returns {away: [top3_pids], home: [top3_pids]}; empty list if unavailable."""
    cache = LINEUP_CACHE / f"{game_pk}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        data = statsapi.get("game", {"gamePk": game_pk})
    except Exception as e:
        print(f"  [warn] lineup fetch fail pk={game_pk}: {e}", file=sys.stderr)
        return {"away": [], "home": []}
    teams = data.get("liveData", {}).get("boxscore", {}).get("teams", {})
    out = {
        "away": list(teams.get("away", {}).get("battingOrder", []))[:3],
        "home": list(teams.get("home", {}).get("battingOrder", []))[:3],
    }
    LINEUP_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Per-batter splits (vs RHP / LHP)
# ---------------------------------------------------------------------------

def fetch_batter_splits(pid: int, season: int) -> dict:
    """Returns {vs_R: {ops, ab}, vs_L: {ops, ab}}.  Defaults to league avg
    when API has no entries (early-season call-ups, etc.)."""
    cache = SPLITS_CACHE / f"{pid}_{season}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        data = statsapi.get("person", {
            "personId": pid,
            "hydrate":  f"stats(group=[hitting],type=[statSplits],"
                        f"sitCodes=[vr,vl],season={season})",
        })
    except Exception as e:
        print(f"  [warn] split fetch fail pid={pid}: {e}", file=sys.stderr)
        return _empty_splits()

    out = _empty_splits()
    for person in data.get("people", []):
        for sb in person.get("stats", []):
            for split in sb.get("splits", []):
                code = split.get("split", {}).get("code", "")
                stat = split.get("stat", {})
                ops_str = stat.get("ops", "0")
                try:
                    ops = float(ops_str)
                except (TypeError, ValueError):
                    ops = 0.0
                ab = int(stat.get("atBats", 0) or 0)
                if code == "vr":
                    out["vs_R"] = {"ops": ops, "ab": ab}
                elif code == "vl":
                    out["vs_L"] = {"ops": ops, "ab": ab}
    SPLITS_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def _empty_splits() -> dict:
    return {
        "vs_R": {"ops": LEAGUE_AVG_OPS, "ab": 0},
        "vs_L": {"ops": LEAGUE_AVG_OPS, "ab": 0},
    }


# ---------------------------------------------------------------------------
# Pitcher handedness
# ---------------------------------------------------------------------------

def fetch_pitcher_hand(pid: int, season: int) -> str:
    """Returns 'L' or 'R' for the pitcher's throwing hand."""
    cache = PHAND_CACHE / f"{pid}_{season}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8")).get("throws", "R")
        except Exception:
            pass
    try:
        data = statsapi.get("person", {"personId": pid})
    except Exception:
        return "R"
    people = data.get("people", [])
    if not people:
        return "R"
    throws = people[0].get("pitchHand", {}).get("code", "R")
    PHAND_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"throws": throws}), encoding="utf-8")
    return throws


# ---------------------------------------------------------------------------
# Aggregator: top-3 OPS vs opposing pitcher's hand
# ---------------------------------------------------------------------------

def top3_ops_vs_hand(top3_pids: list[int], opp_hand: str, season: int,
                     min_ab: int = 5) -> float:
    """Average top-3 OPS vs the opposing pitcher's handedness.

    For each batter:
      - look up split vs that hand
      - if AB >= min_ab use real number, else fall back to LEAGUE_AVG_OPS
    Then average across the three batters present.  Empty top-3 returns
    LEAGUE_AVG_OPS as a sentinel.
    """
    if not top3_pids:
        return LEAGUE_AVG_OPS
    key = "vs_L" if opp_hand == "L" else "vs_R"
    obs = []
    for pid in top3_pids:
        try:
            sp = fetch_batter_splits(int(pid), season)[key]
        except Exception:
            obs.append(LEAGUE_AVG_OPS)
            continue
        if sp["ab"] >= min_ab:
            obs.append(sp["ops"])
        else:
            obs.append(LEAGUE_AVG_OPS)
    return sum(obs) / len(obs) if obs else LEAGUE_AVG_OPS


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("  Backfill top-3 batter OPS vs opposing pitcher's hand")
    print("=" * 70)

    # Read picks_2026.csv
    with open(PICKS_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        f.seek(0)
        header = next(csv.reader(f))

    new_cols = ["away_top3_ops_vs_oppHand", "home_top3_ops_vs_oppHand",
                "away_pitcher_throws_hand", "home_pitcher_throws_hand"]
    for c in new_cols:
        if c not in header:
            header.append(c)

    # Iterate
    n_total = len(rows)
    n_filled = 0
    n_skipped = 0
    n_lineups_fetched = 0
    n_splits_fetched = 0

    # Build schedule fallback for rows that don't have pitcher_id columns
    pk_to_pids = load_schedule_pid_map(2026)
    print(f"  schedule pid map: {len(pk_to_pids)} games")

    print(f"\nProcessing {n_total} rows...")
    for i, r in enumerate(rows, 1):
        if i % 50 == 0:
            print(f"  ... {i}/{n_total} rows ({n_filled} filled, {n_skipped} skipped)")

        try:
            game_pk = int(r.get("game_pk") or 0)
        except ValueError:
            game_pk = 0
        if not game_pk:
            n_skipped += 1
            continue

        # Pitcher handedness -- prefer column, fall back to schedule cache
        try:
            away_pid = int(r.get("away_pitcher_id") or 0)
            home_pid = int(r.get("home_pitcher_id") or 0)
        except ValueError:
            away_pid = home_pid = 0
        if (not away_pid or not home_pid) and game_pk in pk_to_pids:
            sp = pk_to_pids[game_pk]
            if not away_pid: away_pid = sp[0]
            if not home_pid: home_pid = sp[1]

        if not away_pid or not home_pid:
            # TBD pitcher case; cannot compute matchup
            r["away_top3_ops_vs_oppHand"] = ""
            r["home_top3_ops_vs_oppHand"] = ""
            r["away_pitcher_throws_hand"] = ""
            r["home_pitcher_throws_hand"] = ""
            n_skipped += 1
            continue

        away_hand = fetch_pitcher_hand(away_pid, 2026)
        home_hand = fetch_pitcher_hand(home_pid, 2026)
        if not (LINEUP_CACHE / f"{game_pk}.json").exists():
            n_lineups_fetched += 1
        lineup = fetch_lineup(game_pk)

        away_top3 = lineup["away"]
        home_top3 = lineup["home"]

        # Track new splits fetches
        for pid in away_top3 + home_top3:
            if not (SPLITS_CACHE / f"{pid}_2026.json").exists():
                n_splits_fetched += 1

        # away batters face home pitcher's hand; home batters face away pitcher's hand
        away_score = top3_ops_vs_hand(away_top3, home_hand, 2026)
        home_score = top3_ops_vs_hand(home_top3, away_hand, 2026)

        r["away_top3_ops_vs_oppHand"] = f"{away_score:.4f}"
        r["home_top3_ops_vs_oppHand"] = f"{home_score:.4f}"
        r["away_pitcher_throws_hand"] = away_hand
        r["home_pitcher_throws_hand"] = home_hand
        n_filled += 1

    # Write back
    with open(PICKS_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone.")
    print(f"  Rows filled       : {n_filled} / {n_total}")
    print(f"  Rows skipped      : {n_skipped} (no game_pk or TBD pitcher)")
    print(f"  Lineups fetched   : {n_lineups_fetched}")
    print(f"  Batter splits fetched: {n_splits_fetched}")


if __name__ == "__main__":
    main()

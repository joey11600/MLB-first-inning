#!/usr/bin/env python3
"""
tools/fetch_odds_api.py -- pull first-inning NRFI/YRFI prices from MANY
sportsbooks via The Odds API, in the CSV shape `--import-odds` expects.

WHY AN AGGREGATOR RATHER THAN MORE SCRAPERS
-------------------------------------------
`scrape_dk_odds.py` talks to DraftKings' undocumented internal JSON API.
Its own docstring notes DK has changed that URL roughly once a year, and
2026-05-03 showed their CDN fingerprinting our egress IP into read
timeouts. Writing one such scraper per book multiplies that fragility by
the number of books. The Odds API is a documented, keyed aggregator that
returns every book in one response, so a book being added or a schema
changing is their maintenance problem rather than ours.

THE MARKET.  NRFI/YRFI is not a named market anywhere -- it is the
first-inning total at a 0.5 line:
    market key : totals_1st_1_innings
    point      : 0.5
    Under 0.5  -> NRFI
    Over  0.5  -> YRFI

CALL BUDGET -- READ BEFORE SCHEDULING THIS.
`totals_1st_1_innings` is an "additional market", which The Odds API
serves only from the per-event endpoint. So one fetch costs:
    1 call to list the day's events  +  1 call per event
On a 15-game slate that is ~16 calls. The free tier is 500 credits per
month, i.e. roughly ONE fetch per day. The existing predict cron runs
~12x daily; wiring this into every tick would burn the quota in about
two days. Run it once, close to lock time, or buy a paid tier.
`--dry-run` prints the cost without spending anything.

UNTESTED PATHS.  Written against the documented schema but NOT executed
end-to-end -- this environment has no API key, and DraftKings' own
endpoint 403s from here, so no live comparison was possible. The parsing
and team-mapping are covered by `--self-test`. Treat the first live run
as a verification step: check the row count against the slate before
trusting the prices.

Usage:
    export ODDS_API_KEY=...
    python tools/fetch_odds_api.py --dry-run
    python tools/fetch_odds_api.py --output data/odds/api_2026-07-28.csv
    python tools/fetch_odds_api.py --self-test
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"
MARKET = "totals_1st_1_innings"
ET = ZoneInfo("America/New_York")

# The Odds API returns full team names; the ledger uses these abbrs.
TEAM_TO_ABBR = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Athletics": "OAK",                     # post-2025 rebrand
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "Seattle Mariners": "SEA",
    "San Francisco Giants": "SF", "St. Louis Cardinals": "STL",
    "St Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}

FIELDS = ["date", "game_pk", "away_team", "home_team",
          "market_nrfi_odds", "market_yrfi_odds", "sportsbook",
          "start_time_utc"]


def abbr(name: str) -> str | None:
    return TEAM_TO_ABBR.get((name or "").strip())


def fmt_american(v) -> str:
    try:
        i = int(round(float(v)))
    except (TypeError, ValueError):
        return ""
    return f"+{i}" if i > 0 else str(i)


def _get(url: str, params: dict, timeout: float = 20.0):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}",
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        remaining = r.headers.get("x-requests-remaining")
        used = r.headers.get("x-requests-used")
        return json.loads(r.read().decode("utf-8")), remaining, used


def utc_to_et_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(ET).date().isoformat()
    except Exception:
        return ""


def parse_event(ev: dict) -> list[dict]:
    """One event's per-book first-inning 0.5 totals -> one row per book."""
    away, home = abbr(ev.get("away_team")), abbr(ev.get("home_team"))
    if not away or not home:
        return []
    start = ev.get("commence_time") or ""
    out = []
    for bk in ev.get("bookmakers") or []:
        title = bk.get("title") or bk.get("key") or "unknown"
        for mk in bk.get("markets") or []:
            if mk.get("key") != MARKET:
                continue
            nrfi = yrfi = None
            for oc in mk.get("outcomes") or []:
                # Only the 0.5 line is NRFI/YRFI. Books sometimes also
                # quote 1.5; taking those would silently price a
                # different bet.
                try:
                    if abs(float(oc.get("point")) - 0.5) > 1e-9:
                        continue
                except (TypeError, ValueError):
                    continue
                nm = (oc.get("name") or "").strip().lower()
                if nm == "under":
                    nrfi = oc.get("price")
                elif nm == "over":
                    yrfi = oc.get("price")
            if nrfi is None and yrfi is None:
                continue
            out.append({
                "date": utc_to_et_date(start),
                "game_pk": "",          # aggregator has no MLB game_pk;
                                        # importer falls back to team+time
                "away_team": away,
                "home_team": home,
                "market_nrfi_odds": fmt_american(nrfi) if nrfi is not None else "",
                "market_yrfi_odds": fmt_american(yrfi) if yrfi is not None else "",
                "sportsbook": title,
                "start_time_utc": start,
            })
    return out


def self_test() -> int:
    print("=== team mapping ===")
    ok = len(set(TEAM_TO_ABBR.values())) == 30
    print(f"  [{'PASS' if ok else 'FAIL'}] maps to {len(set(TEAM_TO_ABBR.values()))} "
          f"distinct abbreviations (expect 30)")

    print("\n=== event parsing (synthetic payload matching the documented schema) ===")
    ev = {
        "away_team": "New York Yankees", "home_team": "Boston Red Sox",
        "commence_time": "2026-07-28T23:10:00Z",
        "bookmakers": [
            {"title": "DraftKings", "markets": [{"key": MARKET, "outcomes": [
                {"name": "Over", "price": -115, "point": 0.5},
                {"name": "Under", "price": -105, "point": 0.5},
                {"name": "Over", "price": 250, "point": 1.5},   # must be ignored
            ]}]},
            {"title": "FanDuel", "markets": [{"key": MARKET, "outcomes": [
                {"name": "Over", "price": 100, "point": 0.5},
                {"name": "Under", "price": -120, "point": 0.5},
            ]}]},
            {"title": "Irrelevant", "markets": [{"key": "h2h", "outcomes": [
                {"name": "New York Yankees", "price": -140},
            ]}]},
        ],
    }
    rows = parse_event(ev)
    checks = [
        (len(rows) == 2, f"2 book rows emitted (got {len(rows)})"),
        (all(r["away_team"] == "NYY" and r["home_team"] == "BOS" for r in rows),
         "teams mapped to NYY/BOS"),
        (rows[0]["market_yrfi_odds"] == "-115" and rows[0]["market_nrfi_odds"] == "-105",
         "DK: Over->YRFI -115, Under->NRFI -105"),
        (rows[1]["market_yrfi_odds"] == "+100" and rows[1]["market_nrfi_odds"] == "-120",
         "FD: Over->YRFI +100, Under->NRFI -120"),
        (all("250" not in (r["market_yrfi_odds"] or "") for r in rows),
         "the 1.5-line outcome was ignored"),
    ]
    for good, desc in checks:
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {desc}")

    print("\n=== unknown team is dropped rather than mis-mapped ===")
    bad = parse_event({"away_team": "Some Expansion Team", "home_team": "Boston Red Sox",
                       "bookmakers": []})
    ok &= bad == []
    print(f"  [{'PASS' if bad == [] else 'FAIL'}] dropped")

    print("\n" + ("ALL SELF-TESTS PASSED" if ok else "*** SELF-TEST FAILED ***"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path)
    ap.add_argument("--regions", default="us")
    ap.add_argument("--dry-run", action="store_true",
                    help="list events and report the credit cost, then stop")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    key = (os.getenv("ODDS_API_KEY") or "").strip()
    if not key:
        sys.exit("ODDS_API_KEY is not set. Get a key at https://the-odds-api.com "
                 "and export it, then re-run. Use --self-test to check the "
                 "parsing logic without a key.")

    try:
        events, rem, used = _get(f"{API_BASE}/sports/{SPORT}/events",
                                 {"apiKey": key})
    except urllib.error.HTTPError as e:
        sys.exit(f"event list failed: HTTP {e.code} {e.reason}")
    print(f"{len(events)} upcoming MLB events  (credits used {used}, remaining {rem})")
    print(f"Fetching '{MARKET}' costs 1 credit per event -> "
          f"{len(events)} more credits for this run.")
    if args.dry_run:
        print("--dry-run: stopping before spending them.")
        return 0
    if rem is not None:
        try:
            if int(rem) < len(events):
                sys.exit(f"refusing to start: {rem} credits left, "
                         f"{len(events)} needed. Top up or reduce scope.")
        except ValueError:
            pass

    rows, failed = [], 0
    for ev in events:
        try:
            detail, rem, used = _get(
                f"{API_BASE}/sports/{SPORT}/events/{ev['id']}/odds",
                {"apiKey": key, "regions": args.regions, "markets": MARKET,
                 "oddsFormat": "american"})
            rows.extend(parse_event(detail))
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}: "
                  f"HTTP {e.code}", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}: "
                  f"{type(e).__name__}", file=sys.stderr)

    if not rows:
        sys.exit("no first-inning 0.5 prices returned -- nothing written. "
                 "Check that your plan includes additional markets.")

    today = datetime.now(ET).date().isoformat()
    out = args.output or (ROOT / "data" / "odds" / f"api_{today}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    books = sorted({r["sportsbook"] for r in rows})
    print(f"wrote {len(rows)} rows across {len(books)} books -> {out}")
    print(f"  books: {', '.join(books)}")
    if failed:
        print(f"  {failed} event(s) failed and were skipped")
    print(f"  credits remaining: {rem}")
    print("\nNext: merge with any other source and import the best prices --")
    print(f"  python tools/merge_odds_books.py {out} data/odds/dk_{today}.csv \\")
    print(f"      --output data/odds/best_{today}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

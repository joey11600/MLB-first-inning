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

ONE BOOK ONLY, FOR ANYTHING THAT FEEDS THE LEDGER.  `--book draftkings`
is REQUIRED on the money path and the flag exists because omitting it is
silently wrong rather than loudly wrong: `tracker.import_odds` applies
every matching row in FILE ORDER against the same pick, so a multi-book
file leaves the ledger priced at whichever book the aggregator happened
to return last.  The published No.1 record is a DRAFTKINGS-priced series
-- stakes move ~17% per 10 cents and the win-loss line itself changes at
+/-20c, because nights the system refuses become bets.  Buying DK's
number from an aggregator only preserves the record while it is still
DK's number.  A multi-book file is a PRIVATE DIAGNOSTIC; the writer
prints a loud warning and refuses to suggest importing it.

Usage:
    export ODDS_API_KEY=...
    python tools/fetch_odds_api.py --dry-run
    # the money path -- one book, into the file the importer already reads:
    python tools/fetch_odds_api.py --book draftkings \
        --output data/odds/dk_2026-08-11.csv
    # diagnostic only -- every book, NEVER imported:
    python tools/fetch_odds_api.py --output data/odds/api_2026-08-11.csv
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


def parse_event(ev: dict, book: str | None = None) -> list[dict]:
    """One event's per-book first-inning 0.5 totals -> one row per book.

    `book` KEEPS ONLY THAT SPORTSBOOK, AND THE LEDGER REQUIRES IT.

    WHY, measured 2026-08-11. `tracker.import_odds` appends EVERY matching
    row to its `pending` list and applies them in file order against the
    same pick, so with several books in one file THE LAST ONE WINS -- and
    which book that is depends on the order the aggregator happened to
    return. Feeding a multi-book file to the importer therefore prices the
    ledger at an arbitrary book that can change between runs.

    That is not a cosmetic problem. The published No.1 record is a
    DRAFTKINGS-priced series; the memory `odds_source_strategy` measures
    what moving the basis does -- stakes shift ~17% per 10 cents and the
    win-loss line itself changes at +/-20c, because nights the system
    refuses become bets. A different basis is a different product wearing
    this one's label, so the whole point of buying DK's number from an
    aggregator is that it is STILL DK's number.

    Matches the aggregator's `title` ("DraftKings") or `key`
    ("draftkings"), case-insensitively, so either spelling works.
    """
    away, home = abbr(ev.get("away_team")), abbr(ev.get("home_team"))
    if not away or not home:
        return []
    start = ev.get("commence_time") or ""
    want = (book or "").strip().lower()
    out = []
    for bk in ev.get("bookmakers") or []:
        title = bk.get("title") or bk.get("key") or "unknown"
        if want and want not in (str(bk.get("title") or "").lower(),
                                 str(bk.get("key") or "").lower()):
            continue
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

    # THE BOOK FILTER IS A LEDGER GUARD, NOT A CONVENIENCE. Without it a
    # multi-book file re-prices the ledger at whichever book sorts last --
    # see parse_event's docstring.
    print("\n=== --book keeps exactly one sportsbook ===")
    ev_key = {
        "away_team": "New York Yankees", "home_team": "Boston Red Sox",
        "commence_time": "2026-07-28T23:10:00Z",
        "bookmakers": [
            {"key": "draftkings", "title": "DraftKings",
             "markets": [{"key": MARKET, "outcomes": [
                 {"name": "Over", "price": -115, "point": 0.5},
                 {"name": "Under", "price": -105, "point": 0.5}]}]},
            {"key": "fanduel", "title": "FanDuel",
             "markets": [{"key": MARKET, "outcomes": [
                 {"name": "Over", "price": 100, "point": 0.5},
                 {"name": "Under", "price": -120, "point": 0.5}]}]},
        ],
    }
    dk_title = parse_event(ev_key, "DraftKings")
    dk_key   = parse_event(ev_key, "draftkings")
    dk_case  = parse_event(ev_key, "DrAfTkInGs")
    none_hit = parse_event(ev_key, "Bovada")
    unfilt   = parse_event(ev_key)
    book_checks = [
        (len(dk_title) == 1 and dk_title[0]["sportsbook"] == "DraftKings",
         "matches on title ('DraftKings') -> 1 row"),
        (len(dk_key) == 1 and dk_key[0]["sportsbook"] == "DraftKings",
         "matches on key ('draftkings') -> 1 row"),
        (len(dk_case) == 1, "match is case-insensitive"),
        (dk_title[0]["market_yrfi_odds"] == "-115",
         "keeps DK's price (-115), not FanDuel's (+100)"),
        (none_hit == [],
         "a book that is not quoting yields NOTHING, never a fallback"),
        (len(unfilt) == 2, "omitting --book still returns every book"),
    ]
    for good, desc in book_checks:
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {desc}")

    print("\n" + ("ALL SELF-TESTS PASSED" if ok else "*** SELF-TEST FAILED ***"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path)
    ap.add_argument("--regions", default="us")
    ap.add_argument("--book",
                    help="Keep ONLY this sportsbook (case-insensitive match "
                         "on the book's title or key, e.g. --book draftkings). "
                         "REQUIRED for anything that feeds the ledger -- see "
                         "the note in the module docstring. Omit to keep "
                         "every book, which is a DIAGNOSTIC output only.")
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
            rows.extend(parse_event(detail, args.book))
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}: "
                  f"HTTP {e.code}", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}: "
                  f"{type(e).__name__}", file=sys.stderr)

    if not rows:
        if args.book:
            sys.exit(f"no first-inning 0.5 prices for --book {args.book!r} -- "
                     f"nothing written. Either that book was not quoting the "
                     f"market, or the name is wrong (try the aggregator's key, "
                     f"e.g. 'draftkings'). Nothing is written rather than "
                     f"silently falling back to a different book.")
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
    if len(books) > 1:
        # LOUD, because the importer will not complain. It applies every
        # matching row in file order against the same pick, so the LAST
        # book in the file silently becomes the ledger's price.
        print("\n  *** MULTI-BOOK FILE -- DO NOT --import-odds THIS ***",
              file=sys.stderr)
        print(f"  It holds {len(books)} books. tracker.import_odds applies "
              f"every row in file order, so the last book wins and your "
              f"pricing basis becomes arbitrary. Re-run with "
              f"--book draftkings for anything that feeds the ledger; keep "
              f"this file as a private diagnostic only.", file=sys.stderr)
    else:
        print(f"\nSingle-book file ({books[0]}). Safe to import:")
        print(f"  python tracker.py --import-odds {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

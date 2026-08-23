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
    # the money path since 2026-08-23 -- every US book in ONE call (same
    # cost), FanDuel to the ledger file, every book to the line-shopping file:
    python tools/fetch_odds_api.py --regions us --ledger-book fanduel \
        --windows 65:50 --skip-started --merge --output data/odds/dk_2026-08-23.csv \
        --raw-output data/diagnostics/odds/lock_2026-08-23.csv --raw-append
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
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"
MARKET = "totals_1st_1_innings"
ET = ZoneInfo("America/New_York")

RAW_FIELDS = ["captured_at_utc", "commence_time", "away_team", "home_team",
              "book_key", "book", "market", "point", "outcome", "price"]


def _host_label() -> str:
    """Which machine is spending: the two can hold DIFFERENT keys (2026-08-23:
    Railway had the exhausted free key while GitHub Actions had the 20K one),
    so the balance is recorded per host and the dashboard shows both."""
    if any(os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME",
                                       "RAILWAY_SERVICE_NAME", "RAILWAY_SERVICE_ID")):
        return "railway"
    if os.environ.get("GITHUB_ACTIONS"):
        return "gha"
    return "local"


def _record_credit_balance(used, remaining, quiet: bool = False) -> None:
    """Best-effort: write the balance the API just reported into Supabase
    `system_status` (key `odds_api_credits:<host>`) so the dashboard's Ops
    Health card shows it and can warn before the money path runs dry.  No
    Supabase credentials -> silently skipped; any error -> one stderr line,
    never a failure (this tool's exit code is money-path signal)."""
    if remaining is None:
        return
    try:
        rem_i = int(remaining)
    except (TypeError, ValueError):
        return
    try:
        from db.supabase_writer import _get_client   # lazy: keeps --self-test dependency-free
        client = _get_client()
        if client is None:
            return
        host = _host_label()
        try:
            used_i = int(used) if used not in (None, "") else None
        except (TypeError, ValueError):
            used_i = None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        client.table("system_status").upsert({
            "key": f"odds_api_credits:{host}",
            "value": {"remaining": rem_i, "used": used_i, "host": host, "checked_at": now},
            "updated_at": now,
        }, on_conflict="key").execute()
    except Exception as exc:    # noqa: BLE001
        if not quiet:
            print(f"  (credit balance not recorded: {exc!r})", file=sys.stderr)

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
          "start_time_utc", "captured_at_utc"]
# `captured_at_utc` (2026-08-23): when THIS row's price was actually fetched.
# A --merge file carries rows from earlier cycles, and the importer used to
# stamp every row with the import time -- so an unlocked game's "captured X
# ago" moved every five minutes although its price had not been re-fetched
# since the morning. The importer now uses this column when present and
# falls back to the import time for files that lack it (scrape_dk_odds.py).


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


def _parse_iso(iso: str | None) -> datetime | None:
    """The aggregator's `commence_time` as an aware UTC datetime, or None.

    Returns None rather than raising, and every caller treats None as
    "cannot judge this event" -- an event with an unreadable start time is
    KEPT by the window filter, never silently dropped. Dropping it would
    lose a game's price to a date-format change.
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def odds_params(key: str, book: str | None, regions: str, markets: str = MARKET) -> dict:
    """Query params for one event's odds -- and the whole cost model.

    2026-08-23 ADDENDUM -- the cost model flipped once the book was FanDuel.
    FanDuel posts the first-inning line by the morning (measured: the price
    was already up at T-120 for 100% of games, Aug 20-22), so the "ask for
    one book, pay nothing until it quotes" saving no longer exists, while
    the OTHER half of the rule still holds: cost = markets x regions, and
    `regions=us` is ONE region however many books it returns. So a call for
    every US book costs exactly what a FanDuel-only call costs. That is
    what `--ledger-book` exploits: one credit buys the ledger's FanDuel
    number AND every other book's quote for line shopping.

    ASK FOR THE ONE BOOK, NOT THE WHOLE REGION. Measured against the live
    API 2026-08-11: cost is [unique markets RETURNED] x [regions], up to
    10 bookmakers counts as one region, and "responses with empty data do
    not count towards the usage quota."

    DK posts a first-inning line a median 63 min before first pitch;
    FanDuel, BetMGM and BetRivers post hours earlier. So with
    `regions=us` every early fetch RETURNS those books, costs a credit,
    and `parse_event`'s --book filter then discards all of it. Asking for
    `bookmakers=draftkings` makes that same fetch come back empty and
    cost nothing until DK actually quotes.

        CLE@DET at T-280:  bookmakers=draftkings -> 0 books, cost 0
                           regions=us            -> 4 books, cost 1

    Paying only for prices we use is what lets the polling window be
    generous instead of surgical.

    `regions` is the fallback for a deliberate multi-book DIAGNOSTIC pull,
    which is the only case where paying for other books is the point.
    """
    params = {"apiKey": key, "markets": markets, "oddsFormat": "american"}
    if book:
        # The API wants the bookmaker KEY ("draftkings"), while --book
        # also accepts the display title ("DraftKings") for the local
        # filter -- so normalise here rather than making the caller care.
        params["bookmakers"] = book.strip().lower()
    else:
        params["regions"] = regions
    return params


def _merge_key(r: dict) -> tuple:
    """Identity of a priced game IN THE FILE.

    (date, away, home, book) and NOT game_pk, because the aggregator does
    not supply one -- `parse_event` writes an empty game_pk and the
    importer falls back to team+time matching. Including the book keeps a
    diagnostic multi-book file from collapsing to one row per game.
    """
    return (r.get("date", ""), r.get("away_team", ""),
            r.get("home_team", ""), r.get("sportsbook", ""))


def merge_rows(existing: list[dict], fresh: list[dict]) -> tuple[list[dict], int]:
    """(rows to write, how many were refreshed).

    A WINDOWED FETCH ONLY EVER HOLDS PART OF THE SLATE, and `import_odds`
    re-reads this whole file every cycle. Overwriting would delete the
    prices already captured for games that have locked -- so rows we did
    not fetch this run are PRESERVED, and a game we did fetch REPLACES its
    earlier row rather than appending beside it. Appending would leave two
    rows for one game, and the importer applies every matching row in file
    order, so the older price would win.
    """
    keyed = {_merge_key(r) for r in fresh}
    preserved = [r for r in existing if _merge_key(r) not in keyed]
    return preserved + list(fresh), len(existing) - len(preserved)


def parse_windows(spec: str) -> list[tuple[float, float]]:
    """"120:115,75:55" -> [(120.0, 115.0), (75.0, 55.0)].

    Each pair is MINUTES BEFORE FIRST PITCH, high (earlier) first. An
    event is fetched on a cycle if it sits inside ANY pair.
    """
    out: list[tuple[float, float]] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"window {chunk!r} must look like HI:LO, "
                             f"minutes before first pitch (e.g. 75:55)")
        hi_s, lo_s = chunk.split(":", 1)
        hi, lo = float(hi_s), float(lo_s)
        if hi < lo:
            raise ValueError(f"window {chunk!r}: HI ({hi}) must be >= LO "
                             f"({lo}); these are minutes BEFORE first pitch, "
                             f"so the earlier bound comes first")
        out.append((hi, lo))
    return out


def select_events_in_window(events: list[dict], within_minutes: int = 0,
                            skip_started: bool = False,
                            now: datetime | None = None,
                            windows: list[tuple[float, float]] | None = None
                            ) -> list[dict]:
    """The events worth spending a credit on right now.

    EXTRACTED SO IT CAN BE TESTED, because it decides what gets SPENT.
    Every event costs one credit and the loop calls this every five
    minutes; a filter that is off by an hour either burns the month's
    budget on markets that do not exist yet or misses the lock entirely.

    TWO PHASES, NOT ONE CONTINUOUS WINDOW (design B, 2026-08-11). Measured
    over 244 placed bets: DK first posts a price a median 63 min before
    first pitch (87% land in the 60-120 band, only 11% earlier), and the
    price a bet is actually PLACED at is captured a median 57 min out --
    the first 5-minute cycle after the T-60 lock opens. So the money lives
    in a narrow band around the lock, and everything between T-120 and
    T-75 is spent watching a market that does not exist yet.

        75:55   THE MONEY. Several attempts so a single miss cannot leave
                the slate unpriced at commit. One shot at T-62 would miss
                ~45% of games, because the median post is T-63.
        120:115 THE MOVEMENT PROBE. Only the 11% of games priced early can
                show any drift at all -- for the rest the price exists for
                ~6 minutes before we bet it, which is why 245 of 263 bets
                showed ZERO open-to-lock change. One credit per game keeps
                that finding measurable instead of assumed.

    `windows` wins when given. `within_minutes=N` is the older single-band
    form and is kept because it is the honest way to say "the whole slate"
    (N=0) for a one-off manual pull.
    An event whose start time will not parse is KEPT -- see `_parse_iso`.
    """
    now = now or datetime.now(timezone.utc)
    bands = list(windows) if windows else None
    out = []
    for ev in events:
        start = _parse_iso(ev.get("commence_time"))
        if start is None:
            out.append(ev)
            continue
        mins = (start - now).total_seconds() / 60.0
        if skip_started and mins < 0:
            continue
        if bands:
            if not any(lo <= mins <= hi for hi, lo in bands):
                continue
        elif within_minutes and mins > within_minutes:
            continue
        out.append(ev)
    return out


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
    # 2026-08-22 DIAGNOSTIC CAPTURE (rollout plan #6/#7).  --markets widens the
    # request (comma list; default = the first-inning total the ledger uses) and
    # --raw-output dumps EVERY book x market x outcome for the fetched events as
    # long-format rows.  Used by odds_diagnostic.yml to measure two things the
    # ledger never captured: cross-book dispersion on the first-inning total
    # (line shopping) and the first-5-innings total (target_horizon memory).
    # A raw file is NEVER importable into the ledger -- different shape.
    ap.add_argument("--markets", default=MARKET,
                    help="Comma-separated Odds API market keys to request "
                         "(default: the first-inning total).")
    ap.add_argument("--ledger-book", default=None, metavar="KEY",
                    help="Request EVERY book in --regions (same credit cost "
                         "as one book: cost is markets x regions) but write "
                         "only this book's rows to --output, the file the "
                         "ledger imports. Every book's rows go to "
                         "--raw-output for line shopping. Mutually exclusive "
                         "with --book. (2026-08-23)")
    ap.add_argument("--raw-append", action="store_true",
                    help="Append to --raw-output instead of overwriting it "
                         "(header written once). For the at-lock cycles, "
                         "which add a few rows each to one daily file.")
    ap.add_argument("--raw-output", type=Path, metavar="CSV",
                    help="Also write every book x market x outcome row here "
                         "(diagnostic; not importable).")
    ap.add_argument("--book",
                    help="Keep ONLY this sportsbook (case-insensitive match "
                         "on the book's title or key, e.g. --book draftkings). "
                         "REQUIRED for anything that feeds the ledger -- see "
                         "the note in the module docstring. Omit to keep "
                         "every book, which is a DIAGNOSTIC output only.")
    ap.add_argument("--windows", metavar="HI:LO,HI:LO",
                    help="Fetch an event when it sits in ANY of these bands, "
                         "in minutes before first pitch (earlier bound "
                         "first). The loop uses '120:115,75:55': a cluster "
                         "around the lock, where the money is, plus one "
                         "early probe for the ~11%% of games priced far out "
                         "-- the only ones whose line can move before we "
                         "bet it. Overrides --within-minutes.")
    ap.add_argument("--within-minutes", type=int, default=0, metavar="N",
                    help="Only fetch events whose first pitch is within N "
                         "minutes. 0 (default) = the whole slate. THE LOOP "
                         "SETS THIS: DraftKings posts first-inning lines a "
                         "median 63 min before first pitch, so fetching a "
                         "10pm game at noon spends a credit on a market that "
                         "does not exist yet (measured 2026-08-11).")
    ap.add_argument("--skip-started", action="store_true",
                    help="Drop events whose first pitch has passed. The pick "
                         "is decided in the 1st inning, so a started game can "
                         "never be priced usefully.")
    ap.add_argument("--min-credits", type=int, default=0, metavar="N",
                    help="Refuse to spend if fewer than N credits would "
                         "remain afterwards. A floor, so a runaway cadence "
                         "cannot take the account to zero mid-month and "
                         "silently unprice every remaining slate.")
    ap.add_argument("--merge", action="store_true",
                    help="Update --output in place instead of overwriting: "
                         "rows for games fetched now replace their earlier "
                         "entry, every other row is preserved. Required when "
                         "fetching a WINDOW, or each run would throw away the "
                         "prices captured for games that already locked.")
    ap.add_argument("--dry-run", action="store_true",
                    help="list events and report the credit cost, then stop")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.book and args.ledger_book:
        sys.exit("--book and --ledger-book are mutually exclusive: --book asks "
                 "the API for one book; --ledger-book asks for every book in "
                 "--regions and keeps one for the ledger file.")
    if args.ledger_book and not args.raw_output:
        print("note: --ledger-book without --raw-output keeps only the ledger "
              "book; the other books' quotes are fetched and discarded.",
              file=sys.stderr)
    # The LOCAL filter for the ledger file: --book (one-book request) or
    # --ledger-book (whole-region request, one book kept).
    ledger_filter = args.book or args.ledger_book

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
    _record_credit_balance(used, rem)

    # WINDOW THE SLATE BEFORE SPENDING ANYTHING. Every event costs a
    # credit, and DK does not post a first-inning line until roughly an
    # hour before its own first pitch -- so fetching the whole card at
    # noon buys mostly empty responses. Filtering here rather than after
    # the fetch is the entire saving.
    try:
        bands = parse_windows(args.windows) if args.windows else None
    except ValueError as exc:
        sys.exit(f"bad --windows: {exc}")

    if bands or args.within_minutes or args.skip_started:
        kept = select_events_in_window(events, args.within_minutes,
                                       args.skip_started, windows=bands)
        skipped = len(events) - len(kept)
        if skipped:
            desc = (args.windows if bands
                    else f"{args.within_minutes or 'any'} min")
            print(f"  window: {len(kept)} of {len(events)} events are in "
                  f"{desc}"
                  f"{' and not started' if args.skip_started else ''} "
                  f"-- {skipped} skipped, saving {skipped} credits")
        events = kept

    if not events:
        print("no events in the window -- nothing to fetch, 0 credits spent.")
        return 0

    print(f"Fetching '{MARKET}' costs 1 credit per event -> "
          f"{len(events)} more credits for this run.")
    if args.dry_run:
        print("--dry-run: stopping before spending them.")
        return 0
    if rem is not None:
        try:
            remaining = int(rem)
            if remaining < len(events):
                sys.exit(f"refusing to start: {rem} credits left, "
                         f"{len(events)} needed. Top up or reduce scope.")
            # THE FLOOR IS SEPARATE FROM "can I afford this run". Running
            # the balance to zero mid-month unprices every remaining slate
            # silently -- the importer just sees no file and skips.
            if args.min_credits and (remaining - len(events)) < args.min_credits:
                sys.exit(f"refusing to start: would leave "
                         f"{remaining - len(events)} credits, below the "
                         f"--min-credits floor of {args.min_credits}. "
                         f"Top up, or lower the floor deliberately.")
        except ValueError:
            pass

    rows, failed = [], 0
    raw_rows = []
    # "Z" form, the same shape tracker._now_utc() writes, so the ledger's
    # capture stamps stay uniform whichever path wrote them.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ev in events:
        try:
            detail, rem, used = _get(
                f"{API_BASE}/sports/{SPORT}/events/{ev['id']}/odds",
                odds_params(key, args.book, args.regions, args.markets))
            if args.raw_output:
                a_, h_ = abbr(detail.get("away_team")), abbr(detail.get("home_team"))
                for bk in detail.get("bookmakers") or []:
                    for mk in bk.get("markets") or []:
                        for oc in mk.get("outcomes") or []:
                            raw_rows.append({
                                "captured_at_utc": now_iso,
                                "commence_time": detail.get("commence_time") or "",
                                "away_team": a_ or detail.get("away_team"),
                                "home_team": h_ or detail.get("home_team"),
                                "book_key": bk.get("key"), "book": bk.get("title"),
                                "market": mk.get("key"), "point": oc.get("point"),
                                "outcome": oc.get("name"), "price": oc.get("price"),
                            })
            # The --book filter STAYS even though the API already narrowed
            # it. It is the guard that keeps the ledger a DraftKings-priced
            # series; a server-side parameter is not something to stake the
            # record on if the API ever widens what it returns.
            fresh = parse_event(detail, ledger_filter)
            for r_ in fresh:
                r_["captured_at_utc"] = now_iso
            rows.extend(fresh)
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}: "
                  f"HTTP {e.code}", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}: "
                  f"{type(e).__name__}", file=sys.stderr)

    # the per-event calls are the ones that cost; record the balance they left
    _record_credit_balance(used, rem, quiet=True)

    if args.raw_output:
        args.raw_output.parent.mkdir(parents=True, exist_ok=True)
        append = args.raw_append and args.raw_output.exists() and args.raw_output.stat().st_size > 0
        with open(args.raw_output, "a" if append else "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=RAW_FIELDS)
            if not append:
                w.writeheader()
            w.writerows(raw_rows)
        print(f"raw {'appended' if append else 'diagnostic'}: {len(raw_rows)} rows over "
              f"{len(events)} events -> {args.raw_output}")
        if not args.output:
            return 0

    if not rows:
        # A WINDOWED RUN LEGITIMATELY FINDS NOTHING, AND THAT IS NOT AN
        # ERROR. DK posts a first-inning line ~an hour before its own
        # first pitch, so a cycle that runs before the book has quoted
        # returns zero rows and must exit 0 -- the loop calls this every
        # five minutes and a non-zero exit would log a failure every time.
        # Without a window, zero rows still means something is wrong.
        if bands or args.within_minutes or args.skip_started:
            print("no prices yet for the events in this window "
                  "(the book has not posted them). Nothing written.")
            return 0
        if ledger_filter:
            sys.exit(f"no first-inning 0.5 prices for book {ledger_filter!r} -- "
                     f"nothing written. Either that book was not quoting the "
                     f"market, or the name is wrong (try the aggregator's key, "
                     f"e.g. 'draftkings'). Nothing is written rather than "
                     f"silently falling back to a different book.")
        sys.exit("no first-inning 0.5 prices returned -- nothing written. "
                 "Check that your plan includes additional markets.")

    today = datetime.now(ET).date().isoformat()
    out = args.output or (ROOT / "data" / "odds" / f"api_{today}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    # MERGE, BECAUSE A WINDOW ONLY EVER HOLDS PART OF THE SLATE.
    # Overwriting would delete the prices captured for games that already
    # locked -- and `import_odds` reads this whole file every cycle, so a
    # shrinking file means earlier games stop being re-confirmed. Keyed on
    # (date, away, home) because the aggregator gives us no game_pk;
    # a fresh row for the same game REPLACES the older one, which is what
    # makes repeated polling track the line rather than duplicate it.
    if args.merge and out.exists() and out.stat().st_size:
        fetched_n = len(rows)
        with open(out, newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
        rows, replaced = merge_rows(existing, rows)
        print(f"  merged into {out.name}: "
              f"{len(existing) - replaced} row(s) preserved, "
              f"{replaced} refreshed, {fetched_n} fetched this run")

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

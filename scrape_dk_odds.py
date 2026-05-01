#!/usr/bin/env python3
"""
scrape_dk_odds.py -- pull live NRFI/YRFI odds for today's MLB slate from
DraftKings' undocumented public JSON API.

Output: CSV in the format expected by mlb_first_inning_predictor.py
        --import-odds, ready to feed straight into the import flow.

Workflow:
  python scrape_dk_odds.py
    -> writes data/odds/dk_<date>.csv

  python mlb_first_inning_predictor.py --import-odds data/odds/dk_<date>.csv \
      --min-edge 0.02
    -> populates market_*_odds, computes edge, sets bet_placed=Y/N

DraftKings API notes (undocumented, public, no auth):
  Base URL : https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1
  League   : MLB = 84240
  Category : 1024 (1st Inning)
  Subcat   : 11024 (Runs - 1st Inning)  -- contains O/U 0.5 selections

  Schema is denormalized: events / markets / selections are sibling
  arrays joined by IDs.  Each event has its market_id; each market has
  its selection_ids; each selection has displayOdds.american.

  Stability: DK has changed this URL pattern roughly once per year.  If
  this breaks, look at the Network tab on sportsbook.draftkings.com when
  viewing the 1st Inning section -- the request structure usually still
  exists, just with different category/subcategory IDs.

This script handles:
  - DK's en-dash minus sign in odds (replaces with ASCII '-')
  - Team-abbr mismatches (a small mapping for KCR/TBR/SFG/etc.)
  - UTC -> ET date conversion (DK timestamps are UTC; our slate is ET)
  - Game lockout: if a game has already started, DK removes the market;
    we skip those and the importer ignores any rows we don't have odds for
"""

import csv
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# DraftKings MLB league + 1st Inning category IDs (current as of 2026-04-29)
DK_BASE        = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1"
MLB_LEAGUE_ID  = 84240
INNING_1_CAT   = 1024     # parent category "1st Inning"
RUNS_1ST_SUB   = 11024    # subcategory "Runs - 1st Inning" (Over/Under 0.5)

# Headers required to avoid 403 -- DK blocks the default Python user agent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":     "application/json",
}

# DK shortName -> our pick CSV abbr.  Most match exactly; this maps the
# divergent ones we've seen DK use.  Verified live from data/odds samples
# 2026-04-29: DK uses "A's" for OAK, "SFG" for SF, "WAS" for WSH.
DK_TO_OUR_ABBR = {
    "A'S":  "OAK",   # Athletics
    "OAK":  "OAK",
    "ATH":  "OAK",   # in case DK switches conventions
    "KCR":  "KC",    "KAN": "KC",
    "TBR":  "TB",    "TAM": "TB",
    "SFG":  "SF",    "SF":  "SF",
    "SDP":  "SD",    "SD":  "SD",
    "CHW":  "CWS",   "CWS": "CWS",
    "WAS":  "WSH",   "WSH": "WSH",
    # All others (NYY, BOS, LAA, ATL, ...) match exactly
}


def normalize_abbr(dk_abbr: str) -> str:
    """Map a DraftKings team shortName to our internal abbr."""
    a = (dk_abbr or "").strip().upper()
    return DK_TO_OUR_ABBR.get(a, a)


def parse_american_odds(s: str) -> str:
    """DK uses U+2212 (real minus sign) in displayOdds.american.  Convert to
    ASCII '-' so downstream tools (and our CSV import) parse correctly.
    Already-positive odds keep the leading '+'."""
    if not s:
        return ""
    return s.replace("−", "-").strip()


def fetch_dk_first_inning_runs() -> dict:
    """Hit the DK API for the entire MLB slate's 1st-inning-runs market."""
    url = f"{DK_BASE}/leagues/{MLB_LEAGUE_ID}/categories/{INNING_1_CAT}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def utc_iso_to_et_date(utc_iso: str) -> str:
    """Convert DK's start_event_date (UTC) to the ET-local calendar date.
    Critical for late-night West Coast games that start past midnight UTC
    but are listed on the previous ET date in our slate."""
    if not utc_iso:
        return ""
    # DK strings look like "2026-04-29T20:10:00.0000000Z"; trim subseconds
    s = utc_iso.replace("Z", "+00:00")
    if "." in s:
        head, _, tail = s.partition(".")
        # tail is like "0000000+00:00"; chop the fractional part
        plus = tail.find("+")
        s = head + (tail[plus:] if plus >= 0 else "")
    try:
        utc_dt = datetime.fromisoformat(s)
    except ValueError:
        return ""
    et_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return et_dt.strftime("%Y-%m-%d")


def extract_odds(data: dict) -> list[dict]:
    """Walk the DK response and emit one CSV row per game.

    Looks at every market under subcategoryId=RUNS_1ST_SUB (the Runs -
    1st Inning O/U).  For each, finds the Over 0.5 (= YRFI) and Under 0.5
    (= NRFI) selections."""
    events_by_id    = {e["id"]: e for e in data.get("events", [])}
    selections      = data.get("selections", [])
    markets         = data.get("markets", [])

    # Filter markets to only those in the Runs-1st-Inning subcategory
    runs_markets = [
        m for m in markets
        if m.get("subcategoryId") == RUNS_1ST_SUB and m.get("eventId")
    ]

    out: list[dict] = []
    for m in runs_markets:
        event = events_by_id.get(m["eventId"])
        if not event:
            continue

        # Pull team abbrs from the event's participants
        parts = event.get("participants", [])
        away_abbr = home_abbr = ""
        # DK marks the home team via metadata or participantSide -- but
        # the simplest reliable signal is the event name "Away @ Home".
        # Use participants[].metadata.shortName joined by venue order:
        # DK's participants list is [home, away] in some endpoints and
        # reversed in others.  Inspect the event name to disambiguate.
        name = event.get("name", "") or ""
        # Event names look like "CHI Cubs @ SD Padres" -- second team is home.
        if " @ " in name and len(parts) == 2:
            for p in parts:
                short = (p.get("metadata") or {}).get("shortName") or p.get("name") or ""
                # Match the participant name against the away/home halves
                # of the event name.
                away_str, home_str = name.split(" @ ", 1)
                p_name = p.get("name", "")
                if p_name == away_str.strip():
                    away_abbr = normalize_abbr(short)
                elif p_name == home_str.strip():
                    home_abbr = normalize_abbr(short)
        # Fallback: assume the first participant is home, second is away
        # (DK convention in some endpoints) -- only used when name parse
        # fails, which is rare.
        if (not away_abbr or not home_abbr) and len(parts) == 2:
            home_abbr = home_abbr or normalize_abbr((parts[0].get("metadata") or {}).get("shortName", ""))
            away_abbr = away_abbr or normalize_abbr((parts[1].get("metadata") or {}).get("shortName", ""))

        if not (away_abbr and home_abbr):
            continue

        date_iso = utc_iso_to_et_date(event.get("startEventDate", ""))

        # Find Over/Under 0.5 selections for this market
        nrfi_odds = ""    # = Under 0.5
        yrfi_odds = ""    # = Over 0.5
        for s in selections:
            if s.get("marketId") != m["id"]:
                continue
            try:
                pts = float(s.get("points", 0))
            except (TypeError, ValueError):
                continue
            if abs(pts - 0.5) > 0.01:
                continue   # skip the +1.5 alt lines
            outcome = (s.get("outcomeType") or s.get("label") or "").lower()
            american = parse_american_odds((s.get("displayOdds") or {}).get("american", ""))
            if outcome.startswith("under"):
                nrfi_odds = american
            elif outcome.startswith("over"):
                yrfi_odds = american

        if not nrfi_odds and not yrfi_odds:
            continue   # market exists but no O/U 0.5 selections (rare)

        out.append({
            "date":             date_iso,
            "game_pk":          "",            # DK has its own eventId; importer matches by date+teams
            "away_team":        away_abbr,
            "home_team":        home_abbr,
            "market_nrfi_odds": nrfi_odds,
            "market_yrfi_odds": yrfi_odds,
            "sportsbook":       "DraftKings",
        })

    return out


def write_csv(rows: list[dict], path: Path) -> None:
    fields = ["date", "game_pk", "away_team", "home_team",
              "market_nrfi_odds", "market_yrfi_odds", "sportsbook"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", metavar="FILE",
                        help="Output CSV path (default: data/odds/dk_<today_ET>.csv)")
    parser.add_argument("--print", action="store_true",
                        help="Also print the parsed odds to stdout")
    args = parser.parse_args()

    print("Fetching DraftKings 1st-inning runs market...", flush=True)
    try:
        data = fetch_dk_first_inning_runs()
    except Exception as exc:
        sys.exit(f"  Fetch failed: {exc}")

    rows = extract_odds(data)
    if not rows:
        # Distinguish between:
        #   - Late-night / off-day: legitimate empty slate (no MLB games today)
        #   - Mid-day during games: all markets correctly locked (games started)
        #   - DK API IDs went stale: WE have a problem and don't know it
        # The third case is silent and dangerous -- imports run with no odds,
        # bet_placed never gets populated, the user thinks "no good bets
        # today" when really our scraper is broken.
        #
        # Heuristic: if it's morning/afternoon ET (9am-5pm) and the MLB
        # schedule has games today, 0 odds is almost certainly a scraper
        # break.  Exit 2 (distinct from 0=success and 1=fetch failure) so
        # the workflow can alert.
        from zoneinfo import ZoneInfo as _ZI
        et_now = datetime.now(_ZI("America/New_York"))
        is_prime_window = 9 <= et_now.hour < 17
        likely_stale_ids = is_prime_window
        if likely_stale_ids:
            print(
                "  WARNING: 0 NRFI/YRFI markets returned during prime hours "
                f"({et_now.strftime('%I:%M %p ET')}).  DraftKings may have "
                "changed the API category/subcategory IDs (currently "
                f"{INNING_1_CAT}/{RUNS_1ST_SUB}).  Verify the IDs at "
                "https://sportsbook.draftkings.com/leagues/baseball/mlb "
                "(Network tab -> 1st Inning section).",
                file=sys.stderr,
            )
            sys.exit(2)
        print("  No NRFI/YRFI markets found (slate may be empty or all locked).")
        return

    # Default output path uses ET-local "today" so a 9am cron run lands
    # on the right slate even if it crosses UTC midnight.
    if args.output:
        out_path = Path(args.output)
    else:
        et_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        out_path = Path("data/odds") / f"dk_{et_today}.csv"

    write_csv(rows, out_path)
    print(f"  Wrote {len(rows)} games -> {out_path}")

    if args.print:
        print()
        print(f"  {'Date':10}  {'Matchup':12}  {'NRFI':>5}  {'YRFI':>5}")
        for r in rows:
            print(f"  {r['date']}  {r['away_team']:>3} @ {r['home_team']:<3}    "
                  f"{r['market_nrfi_odds']:>5}  {r['market_yrfi_odds']:>5}")

    print()
    print(f"  Next: python mlb_first_inning_predictor.py --import-odds {out_path} --min-edge 0.02")


if __name__ == "__main__":
    main()

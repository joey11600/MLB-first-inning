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


def fetch_dk_first_inning_runs(retries: int = 3, backoff: float = 2.0) -> dict:
    """Hit the DK API for the entire MLB slate's 1st-inning-runs market.

    Retries on transient errors (timeout, connection reset, 5xx) with
    exponential backoff.  We can't recover from a stale category-ID
    problem -- that returns a successful 200 with empty events -- but
    we CAN recover from network blips, which were the silent cause of
    most missed-coverage hours during the 04-29/04-30 partial captures.
    """
    import time
    url = f"{DK_BASE}/leagues/{MLB_LEAGUE_ID}/categories/{INNING_1_CAT}"
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001 -- want to retry on anything network-y
            last_exc = exc
            if attempt < retries - 1:
                wait = backoff ** attempt
                print(f"  DK fetch attempt {attempt+1}/{retries} failed ({exc!r}); "
                      f"retrying in {wait:.1f}s...", file=sys.stderr)
                time.sleep(wait)
    raise last_exc if last_exc else RuntimeError("DK fetch failed (no exception captured)")


# ---------------------------------------------------------------------------
# Schedule-aware coverage alerting (T2.20)
# ---------------------------------------------------------------------------
#
# DK's API only returns games with currently-OPEN markets.  Games already
# in progress are removed from the response.  Without comparing against
# the actual MLB schedule we can't tell the difference between:
#
#   "captured 4/15 because 11 games were locked" — fine
#   "captured 4/15 because something silently broke for 11 games" — NOT fine
#
# We hit the public MLB Stats API schedule endpoint (no auth, well-known
# URL) to get the count of games scheduled for the slate, then warn loudly
# if our capture is < 80% during the prime window when DK should have
# every game open.

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def fetch_mlb_schedule_count(date_iso: str, timeout: float = 10.0) -> int | None:
    """Return the number of regular-season MLB games scheduled on `date_iso`.

    Returns None if the schedule fetch fails -- caller should treat that
    as "can't compute coverage" and skip the alert (don't false-alarm
    just because StatsAPI is briefly down)."""
    url = f"{MLB_SCHEDULE_URL}?sportId=1&date={date_iso}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  MLB schedule fetch failed ({exc!r}); skipping coverage check.",
              file=sys.stderr)
        return None
    dates = data.get("dates", []) or []
    # `dates` is one entry per date; filter to ours and count games.
    for d in dates:
        if d.get("date") == date_iso:
            games = d.get("games", []) or []
            # Only count games that haven't been postponed/cancelled
            # at the schedule-fetch moment.  Status codes per MLB API:
            #   "Scheduled" / "Pre-Game" / "Warmup" / "In Progress" / "Final"
            #     -> count
            #   "Postponed" / "Cancelled" / "Suspended" (final) / "Delayed"
            #     -> exclude (they don't have markets either)
            countable = sum(
                1 for g in games
                if (g.get("status", {}).get("abstractGameState") or "")
                   not in ("Postponed", "Cancelled")
            )
            return countable
    return 0


def check_coverage(captured: int, scheduled: int | None,
                   et_now: datetime) -> tuple[bool, str]:
    """Decide whether to warn about partial coverage.

    Returns (should_warn, message).  Only fires during the prime window
    when DK should have every game open (9am-1pm ET); after 1pm, partial
    coverage is expected as games progressively start.

    Threshold is 80% -- captured at least 80% of scheduled games is OK,
    less is suspicious.  10/13 = 77% triggers; 11/13 = 85% does not.
    """
    if scheduled is None or scheduled == 0:
        return (False, "")  # can't compute, or off-day
    is_prime_window = 9 <= et_now.hour < 13
    if not is_prime_window:
        return (False, "")
    coverage = captured / scheduled
    if coverage >= 0.80:
        return (False, "")
    msg = (
        f"PARTIAL COVERAGE: captured {captured}/{scheduled} games "
        f"({100*coverage:.0f}%) during prime hours "
        f"({et_now.strftime('%I:%M %p ET')}).  Expected >=80%.  Possible causes: "
        f"DK API intermittent, abbrev mismatch (check DK_TO_OUR_ABBR), "
        f"or matchup parsing changed.  Investigate today's missed games "
        f"before tomorrow."
    )
    return (True, msg)


# ---------------------------------------------------------------------------
# Merge logic: preserve odds captured in earlier hourly runs
# ---------------------------------------------------------------------------
#
# The cron fires every hour 12-23 UTC.  Each hour, DK only returns games
# whose markets are currently OPEN -- games already in progress have their
# market removed.  Without merging, the noon run captures the morning slate
# (8 games), then the 5pm run captures only the late slate (1 game) and
# OVERWRITES the file -- losing the 8 morning games from the daily file.
#
# `picks_2026.csv` doesn't lose them (the importer's UPSERT preserves earlier
# updates), but the per-day odds file ends up lossy and useless as an audit
# trail.  Worse: any tooling that re-imports from the file later would only
# see the residue of the last run.
#
# Merge semantics:
#   - existing rows (from previous hourly runs today) start as the baseline
#   - new fetch overrides existing rows for the SAME (date, away, home)
#     combination -- fresher snapshot wins, so we converge on the closing
#     line as the day progresses
#   - new games not previously seen are added
#   - existing games not in the new fetch are KEPT (locked / market pulled)


def _row_key(r: dict) -> tuple[str, str, str, str]:
    """Identity key for an odds row, DH-aware (T2.21).

    Previously keyed by (date, away, home) only — DH-1 and DH-2 share
    teams + date and the second-listed game would clobber the first
    in our merge dict, leaving picks_2026.csv with odds on only one
    half of every doubleheader.  Confirmed via 2026-04-30 HOU@BAL: G1
    had no odds, G2 did.

    Adding `start_time_utc` makes DH halves distinct keys.  When the
    column is missing (older odds files written before this change),
    `start_time_utc` falls back to "" and behavior is identical to
    the old key — no schema-migration churn.
    """
    return (
        r.get("date", ""),
        r.get("away_team", ""),
        r.get("home_team", ""),
        r.get("start_time_utc", ""),
    )


def read_existing_csv(path: Path) -> list[dict]:
    """Read prior hourly capture, if any.  Returns [] when the file
    doesn't exist or has no rows.  Preserves whatever schema the file
    already has (we only need date / teams / odds / sportsbook columns
    for the merge)."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:  # noqa: BLE001
        # Don't let a corrupted file stop us from writing fresh data.
        # Log and proceed with empty baseline.
        print(f"  WARNING: existing odds file at {path} unreadable ({exc!r}); "
              f"starting from empty baseline.", file=sys.stderr)
        return []


def merge_rows(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Union existing + fresh, keyed by (date, away, home).  Fresh row
    wins on conflict so we end up with the latest captured odds per game.
    Empty-odds rows in `fresh` are skipped (treated as "didn't capture
    this game this run") so we never overwrite real odds with blanks."""
    by_key: dict[tuple[str, str, str], dict] = {_row_key(r): r for r in existing}
    for r in fresh:
        # Skip rows that have no actual odds -- they shouldn't ever be
        # emitted by extract_odds() (the early `continue` filters them
        # out), but defensive check in case the schema evolves.
        if not (r.get("market_nrfi_odds") or r.get("market_yrfi_odds")):
            continue
        by_key[_row_key(r)] = r
    # Sort by (date, away, home) so the file diff is human-readable
    # across runs.
    return sorted(by_key.values(), key=_row_key)


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
            "game_pk":          "",            # DK has its own eventId; importer matches by date+teams (+start_time for DH)
            "away_team":        away_abbr,
            "home_team":        home_abbr,
            "market_nrfi_odds": nrfi_odds,
            "market_yrfi_odds": yrfi_odds,
            "sportsbook":       "DraftKings",
            # T2.21 -- DK's start time, used by importer to disambiguate
            # doubleheader halves (same teams, different start times).
            # Stored verbatim from event.startEventDate (UTC ISO).
            "start_time_utc":   event.get("startEventDate", "") or "",
        })

    return out


def write_csv(rows: list[dict], path: Path) -> None:
    fields = ["date", "game_pk", "away_team", "home_team",
              "market_nrfi_odds", "market_yrfi_odds", "sportsbook",
              "start_time_utc"]   # T2.21 -- DH disambiguation
    path.parent.mkdir(parents=True, exist_ok=True)
    # extrasaction="ignore" is defensive: if a future schema adds columns
    # to extract_odds() but the existing file on disk still has the old
    # shape, the merge can produce rows with extra keys.  Drop them
    # rather than crash the run.
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
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

    fresh_rows = extract_odds(data)

    # Resolve output path BEFORE deciding what to do with an empty fetch --
    # we need to know whether a prior run already populated the file today.
    if args.output:
        out_path = Path(args.output)
    else:
        et_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        out_path = Path("data/odds") / f"dk_{et_today}.csv"

    existing_rows = read_existing_csv(out_path)

    if not fresh_rows:
        # Distinguish between:
        #   - Late-night / off-day: legitimate empty slate (no MLB games today)
        #   - Mid-day during games: all markets correctly locked (games started)
        #   - DK API IDs went stale: WE have a problem and don't know it
        # Mitigation now: if this hour returns 0 BUT a prior hourly run today
        # already populated the file, we still have data -- exit 0 (success)
        # and leave the existing file alone.  The "stale IDs" alarm only
        # fires when the file is ALSO empty (which means the IDs broke
        # before the FIRST run of the day captured anything).
        from zoneinfo import ZoneInfo as _ZI
        et_now = datetime.now(_ZI("America/New_York"))
        is_prime_window = 9 <= et_now.hour < 17
        if existing_rows:
            n = len(existing_rows)
            print(f"  No new markets returned this run; preserving {n} games "
                  f"from earlier today's captures at {out_path}.")
            return
        if is_prime_window:
            print(
                "  WARNING: 0 NRFI/YRFI markets returned during prime hours "
                f"({et_now.strftime('%I:%M %p ET')}) AND no prior captures "
                f"in {out_path}.  DraftKings may have changed the API "
                f"category/subcategory IDs (currently "
                f"{INNING_1_CAT}/{RUNS_1ST_SUB}).  Verify at "
                "https://sportsbook.draftkings.com/leagues/baseball/mlb "
                "(Network tab -> 1st Inning section).",
                file=sys.stderr,
            )
            sys.exit(2)
        print("  No NRFI/YRFI markets found (slate may be empty or all locked).")
        return

    # Merge with anything captured in earlier hourly runs today so the file
    # is a complete daily audit trail, not just the most recent snapshot.
    merged_rows = merge_rows(existing_rows, fresh_rows)
    new_count   = len(merged_rows) - len(existing_rows)  # may be negative if
                                                          # earlier file had
                                                          # different shape;
                                                          # we just report it
    write_csv(merged_rows, out_path)
    if existing_rows:
        print(f"  Fetched {len(fresh_rows)} games this run; merged with "
              f"{len(existing_rows)} from earlier captures -> "
              f"{len(merged_rows)} total in {out_path}"
              + (f" (+{new_count} new game{'s' if new_count != 1 else ''})"
                 if new_count > 0 else ""))
    else:
        print(f"  Wrote {len(merged_rows)} games -> {out_path}")

    if args.print:
        print()
        print(f"  {'Date':10}  {'Matchup':12}  {'NRFI':>5}  {'YRFI':>5}")
        for r in merged_rows:
            print(f"  {r['date']}  {r['away_team']:>3} @ {r['home_team']:<3}    "
                  f"{r['market_nrfi_odds']:>5}  {r['market_yrfi_odds']:>5}")

    # T2.20 -- check coverage against today's MLB schedule and warn loudly
    # on partial captures during prime hours.  Filters merged_rows to the
    # ET-today date so a stale leftover from yesterday's run doesn't inflate
    # the count.
    from zoneinfo import ZoneInfo as _ZI
    et_now = datetime.now(_ZI("America/New_York"))
    et_today_iso = et_now.strftime("%Y-%m-%d")
    captured_today = sum(1 for r in merged_rows if r.get("date", "") == et_today_iso)
    scheduled = fetch_mlb_schedule_count(et_today_iso)
    if scheduled is not None:
        print(f"  Coverage: {captured_today}/{scheduled} games captured for {et_today_iso}.")
    should_warn, warn_msg = check_coverage(captured_today, scheduled, et_now)
    if should_warn:
        print(f"  WARNING: {warn_msg}", file=sys.stderr)

    print()
    print(f"  Next: python mlb_first_inning_predictor.py --import-odds {out_path} --min-edge 0.02")


if __name__ == "__main__":
    main()

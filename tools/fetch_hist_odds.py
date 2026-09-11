#!/usr/bin/env python3
"""
tools/fetch_hist_odds.py -- buy HISTORICAL first-inning prices from The Odds
API for a list of past games, in the exact CSV shape `data/odds_history/
hist_fi_odds_{season}.csv` already uses.

WHY THIS EXISTS AS A COMMITTED TOOL.  The 2026-09-09 purchase (~7,400
credits, 629 games) was made with fetcher scripts written in a session
scratchpad that were never committed and are gone.  Re-deriving that data
would cost the money again.  This file is that capability, kept.

THE ENDPOINTS (documented; additional markets exist from 2023-05-03).

    events for a day   /v4/historical/sports/baseball_mlb/events
                       ?date={YYYY-MM-DD}T14:00:00Z            1 credit
    odds for one event /v4/historical/sports/baseball_mlb/events/{id}/odds
                       ?date={snapshot}&markets=totals_1st_1_innings
                       &regions=us&oddsFormat=american        10 credits

`totals_1st_1_innings` is an "additional market", so it is served only from
the per-event endpoint -- one call per game, which is what makes this
expensive.  Cost = markets x regions per event; `regions=us` is ONE region
however many books it returns, so asking for every US book costs what
asking for one costs.  Both responses are wrapped:
`{timestamp, previous_timestamp, next_timestamp, data: ...}` -- `data` is a
LIST for the events endpoint and a SINGLE EVENT for the odds endpoint.

THE SNAPSHOT IS FIRST PITCH MINUS 60 MINUTES, not an arbitrary time.  The
live money path captures at the lock (window 65:50, median capture 57 min
out) because that is when the bet is actually placed, so a historical price
on any other basis would not be comparable to the ledger.

That 60 is not a guess -- it is what the 2026-09-09 purchase used, recovered
by `--verify`.  The API returns the nearest snapshot AT OR BEFORE the
requested instant, on a ~5-minute grid, and reports it back; that reported
value is what gets stored.  Asking at T-65 for game 746409 returned the
23:00:39 snapshot while the stored row holds 23:05:39, i.e. the original
asked at T-60.  Prices were identical at both instants, but the two halves
of the bench should sit on ONE basis, so this matches the original.
Re-verified end to end on 2024 (betrivers, no FanDuel) and 2025 (FanDuel).

Over 0.5 = YRFI, Under 0.5 = NRFI.  Books also quote a 1.5 line -- taking
those would silently price a different bet, so `point == 0.5` is enforced.

USAGE

    # what would this cost?  spends nothing:
    python tools/fetch_hist_odds.py --targets targets.csv --dry-run

    # prove the parsing reproduces the existing purchase before a big run
    # (11 credits: one day index + one event):
    python tools/fetch_hist_odds.py --verify 746409

    # the real run -- resumable, appends as it goes.  ONE file for both
    # seasons (the rows carry a `season` column); it is written beside the
    # original purchase, never into it:
    python tools/fetch_hist_odds.py --targets targets.csv \
        --out data/odds_history/hist_fi_odds_envelope.csv \
        --min-credits 2000

RESUME IS NOT OPTIONAL.  A 577-game run is ~800 HTTP calls; it will be
interrupted.  Every completed game is appended immediately and re-running
skips any game_pk already in the output, so a crash costs nothing but the
in-flight call.  The output file is written SEPARATELY from the original
purchase -- that file is irreplaceable and is never appended to.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.fetch_odds_api import TEAM_TO_ABBR, abbr  # noqa: E402

API_BASE = "https://api.the-odds-api.com/v4/historical"
SPORT = "baseball_mlb"
MARKET = "totals_1st_1_innings"
ET = ZoneInfo("America/New_York")

SNAPSHOT_LEAD_MIN = 60          # first pitch minus this = the lock basis
EVENTS_INDEX_HOUR = "T14:00:00Z"
COST_EVENTS, COST_ODDS = 1, 10

FIELDS = ["season", "date", "game_pk", "away", "home", "event_id",
          "commence_time", "snapshot_ts", "book", "over_price", "under_price",
          "n_books_0_5", "cons_over_devig", "fanduel_over_devig",
          "nrfi_prob", "fi_total_runs"]

# Preference order for which book's raw prices land in over/under_price.
# FanDuel first because the live ledger's basis moved to FanDuel 2026-08-23
# and the 2025 half of the existing purchase is FanDuel-priced.
BOOK_PREFERENCE = ["fanduel", "draftkings", "betmgm", "betrivers", "superbook"]


def load_key() -> str:
    key = (os.environ.get("ODDS_API_KEY") or "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("ODDS_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("ODDS_API_KEY not set (environment or .env)")


class Credits:
    """Running balance as the API reports it, plus the floor guard."""

    def __init__(self, floor: int = 0):
        self.remaining: int | None = None
        self.used: int | None = None
        self.floor = floor
        self.spent_here = 0

    def update(self, headers) -> None:
        for attr, hdr in (("remaining", "x-requests-remaining"),
                          ("used", "x-requests-used")):
            try:
                setattr(self, attr, int(headers.get(hdr)))
            except (TypeError, ValueError):
                pass
        try:
            self.spent_here += int(headers.get("x-requests-last") or 0)
        except (TypeError, ValueError):
            pass

    def check(self, need: int) -> None:
        if self.remaining is None:
            return
        if self.remaining - need < self.floor:
            sys.exit(f"refusing to continue: {self.remaining} credits left, "
                     f"next call costs {need}, floor is {self.floor}.")


def get(url: str, params: dict, credits: Credits, cost: int,
        timeout: float = 30.0, retries: int = 3):
    credits.check(cost)
    q = urllib.parse.urlencode(params)
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{url}?{q}",
                                         headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                credits.update(r.headers)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 4xx other than 429 will not fix themselves -- fail loudly
            # rather than burning retries (and credits) on a bad request.
            if e.code != 429 and 400 <= e.code < 500:
                raise
            last_exc = e
        except Exception as e:      # noqa: BLE001 -- network flake
            last_exc = e
        time.sleep(2 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def implied(american: float) -> float:
    a = float(american)
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def devig_over(over, under) -> float | None:
    """Vig-stripped P(Over 0.5) = P(a run scores) from one book's two sides."""
    if over is None or under is None:
        return None
    io, iu = implied(over), implied(under)
    tot = io + iu
    return (io / tot) if tot > 0 else None


def fmt_american(v) -> str:
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return ""


def parse_books(event: dict) -> dict[str, tuple]:
    """{book_key: (over_price, under_price)} for the 0.5 line only."""
    out: dict[str, tuple] = {}
    for bk in event.get("bookmakers") or []:
        key = (bk.get("key") or "").lower()
        over = under = None
        for mk in bk.get("markets") or []:
            if mk.get("key") != MARKET:
                continue
            for oc in mk.get("outcomes") or []:
                try:
                    if abs(float(oc.get("point")) - 0.5) > 1e-9:
                        continue
                except (TypeError, ValueError):
                    continue
                nm = (oc.get("name") or "").strip().lower()
                if nm == "over":
                    over = oc.get("price")
                elif nm == "under":
                    under = oc.get("price")
        if over is not None or under is not None:
            out[key] = (over, under)
    return out


def build_row(target: dict, event: dict, snapshot_ts: str) -> dict | None:
    books = parse_books(event)
    if not books:
        return None
    both = {k: v for k, v in books.items() if v[0] is not None and v[1] is not None}
    if not both:
        return None

    chosen = next((b for b in BOOK_PREFERENCE if b in both), sorted(both)[0])
    over, under = both[chosen]

    devigs = [devig_over(o, u) for o, u in both.values()]
    devigs = [d for d in devigs if d is not None]
    cons = sum(devigs) / len(devigs) if devigs else None
    fd = devig_over(*both["fanduel"]) if "fanduel" in both else None

    return {
        "season": target["season"], "date": target["date"],
        "game_pk": target["game_pk"], "away": target["away"], "home": target["home"],
        "event_id": event.get("id") or "",
        "commence_time": event.get("commence_time") or "",
        "snapshot_ts": snapshot_ts,
        "book": chosen,
        "over_price": fmt_american(over), "under_price": fmt_american(under),
        "n_books_0_5": len(books),
        "cons_over_devig": f"{cons:.6f}" if cons is not None else "",
        "fanduel_over_devig": f"{fd:.6f}" if fd is not None else "",
        "nrfi_prob": target.get("nrfi_prob", "") or target.get("p_nrfi", ""),
        "fi_total_runs": target.get("fi_total_runs", ""),
    }


def et_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone(ET).date().isoformat()
    except Exception:      # noqa: BLE001
        return ""


def snapshot_for(commence_iso: str) -> str:
    dt = datetime.fromisoformat(str(commence_iso).replace("Z", "+00:00"))
    return (dt - timedelta(minutes=SNAPSHOT_LEAD_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_index(date_iso: str, key: str, credits: Credits) -> dict[tuple, dict]:
    """{(et_date, away_abbr, home_abbr): event} for one calendar day."""
    payload = get(f"{API_BASE}/sports/{SPORT}/events",
                  {"apiKey": key, "date": f"{date_iso}{EVENTS_INDEX_HOUR}"},
                  credits, COST_EVENTS)
    out: dict[tuple, dict] = {}
    for ev in payload.get("data") or []:
        a, h = abbr(ev.get("away_team")), abbr(ev.get("home_team"))
        if not a or not h:
            continue
        out[(et_date(ev.get("commence_time")), a, h)] = ev
    return out


def event_odds(event_id: str, snapshot: str, key: str, credits: Credits) -> tuple[dict, str]:
    payload = get(f"{API_BASE}/sports/{SPORT}/events/{event_id}/odds",
                  {"apiKey": key, "date": snapshot, "regions": "us",
                   "markets": MARKET, "oddsFormat": "american"},
                  credits, COST_ODDS)
    return (payload.get("data") or {}), (payload.get("timestamp") or snapshot)


def read_targets(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        if str(r.get("already_priced", "0")).strip() in ("1", "true", "True"):
            continue
        out.append(r)
    return out


def done_game_pks(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        return {str(r.get("game_pk", "")).strip() for r in csv.DictReader(fh)}


def verify(game_pk: str, key: str, credits: Credits) -> int:
    """Re-buy ONE game we already own and diff every column (11 credits).

    This is the guard on a four-figure run: if the parsing here does not
    reproduce the existing purchase, the two halves of the bench are not
    comparable and the run must not start.
    """
    stored = None
    for season in ("2024", "2025"):
        p = ROOT / "data" / "odds_history" / f"hist_fi_odds_{season}.csv"
        if not p.exists():
            continue
        with open(p, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if str(r["game_pk"]).strip() == str(game_pk).strip():
                    stored = r
                    break
        if stored:
            break
    if not stored:
        sys.exit(f"game_pk {game_pk} is not in the existing purchase -- "
                 f"pick one that is, so there is something to compare against.")

    print(f"verifying {stored['away']}@{stored['home']} {stored['date']} "
          f"(game_pk {game_pk})\n")
    idx = day_index(stored["date"], key, credits)
    hit = idx.get((stored["date"], stored["away"], stored["home"]))
    if not hit:
        print(f"  day index has {len(idx)} games, none matching "
              f"{stored['away']}@{stored['home']} on {stored['date']}")
        return 1
    if hit.get("id") != stored["event_id"]:
        print(f"  NOTE event_id differs: stored {stored['event_id']} "
              f"vs fetched {hit.get('id')}")

    snap = snapshot_for(hit.get("commence_time") or stored["commence_time"])
    ev, ts = event_odds(hit["id"], snap, key, credits)
    row = build_row({k: stored[k] for k in ("season", "date", "game_pk", "away", "home")}
                    | {"nrfi_prob": stored["nrfi_prob"],
                       "fi_total_runs": stored["fi_total_runs"]}, ev, ts)
    if row is None:
        print("  no 0.5 line in that snapshot -- cannot compare.")
        return 1

    print(f"  {'column':<20} {'stored':>22} {'refetched':>22}   match")
    ok = True
    for f in FIELDS:
        a, b = str(stored.get(f, "")).strip(), str(row.get(f, "")).strip()
        same = a == b
        if f in ("cons_over_devig", "fanduel_over_devig") and a and b:
            same = abs(float(a) - float(b)) < 5e-6
        if f not in ("snapshot_ts",):
            ok &= same
        print(f"  {f:<20} {a:>22} {b:>22}   {'ok' if same else 'DIFF'}")
    print(f"\n  verdict: {'MATCH -- safe to run' if ok else 'MISMATCH -- do not run'}")
    print(f"  credits spent here: {credits.spent_here}, remaining {credits.remaining}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", type=Path, help="CSV: season,date,game_pk,away,home[,p_nrfi,fi_total_runs,already_priced]")
    ap.add_argument("--out", type=Path, help="output CSV (appended, resumable)")
    ap.add_argument("--dry-run", action="store_true", help="print the cost, spend nothing")
    ap.add_argument("--limit", type=int, default=0, help="stop after N games (a paid smoke test)")
    ap.add_argument("--min-credits", type=int, default=1000,
                    help="refuse to spend below this balance (default 1000)")
    ap.add_argument("--verify", metavar="GAME_PK",
                    help="re-buy one already-purchased game and diff it (11 credits)")
    args = ap.parse_args()

    key = load_key()
    credits = Credits(floor=args.min_credits)

    if args.verify:
        credits.floor = 0        # an 11-credit check is always affordable
        return verify(args.verify, key, credits)

    if not args.targets or not args.out:
        ap.error("--targets and --out are required (or use --verify)")

    targets = read_targets(args.targets)
    already = done_game_pks(args.out)
    todo = [t for t in targets if str(t["game_pk"]).strip() not in already]
    if args.limit:
        todo = todo[:args.limit]
    days = sorted({t["date"] for t in todo})
    cost = len(days) * COST_EVENTS + len(todo) * COST_ODDS

    print(f"targets needing a price: {len(targets)}")
    print(f"already in {args.out.name}: {len(already)} -> {len(todo)} to fetch")
    print(f"distinct dates: {len(days)}")
    print(f"COST: {len(days)} x {COST_EVENTS} + {len(todo)} x {COST_ODDS} = {cost:,} credits")
    if args.dry_run:
        print("--dry-run: nothing spent.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.out.exists() or args.out.stat().st_size == 0
    fh = open(args.out, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    if new_file:
        w.writeheader()
        fh.flush()

    by_day: dict[str, list[dict]] = {}
    for t in todo:
        by_day.setdefault(t["date"], []).append(t)

    wrote = missing = failed = 0
    try:
        for i, day in enumerate(sorted(by_day), 1):
            try:
                idx = day_index(day, key, credits)
            except Exception as e:      # noqa: BLE001
                print(f"[{i}/{len(by_day)}] {day}: index failed ({type(e).__name__}) -- skipped",
                      file=sys.stderr)
                failed += len(by_day[day])
                continue
            for t in by_day[day]:
                ev = idx.get((t["date"], t["away"], t["home"]))
                if not ev:
                    missing += 1
                    continue
                try:
                    detail, ts = event_odds(ev["id"], snapshot_for(ev["commence_time"]),
                                            key, credits)
                    row = build_row(t, detail, ts)
                except SystemExit:
                    raise
                except Exception as e:      # noqa: BLE001
                    print(f"  {t['away']}@{t['home']} {t['date']}: "
                          f"{type(e).__name__}", file=sys.stderr)
                    failed += 1
                    continue
                if row is None:
                    missing += 1
                    continue
                w.writerow(row)
                fh.flush()          # resume point after every single game
                wrote += 1
            print(f"[{i}/{len(by_day)}] {day}: {wrote} priced, {missing} no-line, "
                  f"{failed} failed, {credits.remaining} credits left", flush=True)
    finally:
        fh.close()

    print(f"\nwrote {wrote} rows -> {args.out}")
    print(f"no 0.5 line / not in index: {missing}   failed: {failed}")
    print(f"credits spent this run: {credits.spent_here}, remaining: {credits.remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

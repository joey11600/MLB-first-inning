#!/usr/bin/env python3
"""
fi_pitcher_pool.py -- the pooled first-inning pitcher quality the model reads.

WHAT IT IS.  For every starter, his FIRST-INNING expected-wOBA allowed, pooled
across seasons (earlier seasons weighted PRIOR_SEASON_W), shrunk toward the
league mean with K_PA plate appearances of prior, using only plate
appearances that happened strictly BEFORE the day the state is "as of".
Validated 2026-08-21 (tools/refit2026/, CHANGELOG): the first candidate in
~95 tested here to clear every bar, and it lifts the nightly No.1 from .657
to .736 out of sample on 2026 together with L2 0.5.

WHY A STATE FILE.  The research build streamed 544 day-files of pitch-level
Statcast (79 MB, gitignored, on one machine).  Production cannot depend on
that.  So the STATE is the set of running accumulators (per pitcher: this
season's and prior seasons' PA / wOBA sums, plus league totals) -- ~50 KB,
committed at data/fi_pitcher_pool.json -- and it advances one day at a time
by fetching ONLY that day's pitches from Savant.  The predictor reads it, and
refreshes it if it is behind, failing OPEN to the last good state: a stale
estimate moves slowly and is far better than a crash or a league-average
default for everyone.

EQUIVALENCE.  `--rebuild` reproduces the state from the full local cache and
the test suite checks that the incremental path gives the same estimates as
the batch builder that was validated (tools/refit2026/build_fi_pitcher_pooled.py).

CLI
    python fi_pitcher_pool.py --rebuild          # from data/cache/statcast_zone (research machine)
    python fi_pitcher_pool.py --update           # advance through yesterday (cron / predictor)
    python fi_pitcher_pool.py --show 681517      # one pitcher's estimate
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "fi_pitcher_pool.json"
CACHE_GLOB = str(ROOT / "data" / "cache" / "statcast_zone" / "*.csv.gz")

K_PA = 60.0                   # shrinkage prior, plate appearances
PRIOR_SEASON_W = 0.6          # weight on earlier seasons' accumulators at rollover
W_BB, W_HBP = 0.69, 0.72      # wOBA linear weights; K = 0; in-play = xwOBA of the ball
K_EVENTS = {"strikeout", "strikeout_double_play"}
BB_EVENTS = {"walk", "hit_by_pitch", "intent_walk"}
LEAGUE_FALLBACK = 0.32        # only if the state has no league totals at all
MAX_CATCHUP_DAYS = 12         # bound on predict-time refresh; the cron closes bigger gaps

# Same endpoint and day-filter as tools/scrape_statcast_zone.py (duplicated
# here on purpose: this module is imported by the predictor at runtime and
# must not depend on tools/ being importable).
_URL = ("https://baseballsavant.mlb.com/statcast_search/csv?all=true"
        "&hfSea={season}%7C&hfGT=R%7C&player_type=pitcher"
        "&min_pitches=0&min_results=0&group_by=name&sort_col=pitches"
        "&player_event_sort=api_p_release_speed&sort_order=desc&type=details"
        "&game_date_gt={d}&game_date_lt={d}")


# ----------------------------------------------------------------- state
def new_state() -> dict:
    return {"version": 1, "as_of": None, "season": None,
            "k_pa": K_PA, "prior_season_w": PRIOR_SEASON_W,
            "league": {"pa": 0.0, "woba": 0.0},
            "pitchers": {}}          # pid -> {"cur": [pa, woba], "prior": [pa, woba]}


def load_state(path: Path = STATE_PATH) -> dict:
    if not Path(path).exists():
        return new_state()
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))
    os.replace(tmp, path)


def _rollover(state: dict, season: str) -> None:
    """New season: fold this season's sums into the prior pool at PRIOR_SEASON_W."""
    if state["season"] is not None and state["season"] != season:
        for rec in state["pitchers"].values():
            c, p = rec["cur"], rec["prior"]
            if not rec.get("seen"):
                # Mirrors the validated batch builder exactly: only a pitcher who
                # appeared AT ALL that season (any inning) gets his pool folded
                # and decayed; one who did not pitch keeps his prior as it was.
                continue
            rec["prior"] = [PRIOR_SEASON_W * (p[0] + c[0]), PRIOR_SEASON_W * (p[1] + c[1])]
            rec["cur"] = [0.0, 0.0]
            rec["seen"] = 0
    state["season"] = season


def _pa_weight(row: dict):
    """wOBA credit for a PA-ending pitch row, or None if the row is not a PA end."""
    ev = row.get("events") or ""
    if not ev:
        return None
    if ev in K_EVENTS:
        return 0.0
    if ev in BB_EVENTS:
        return W_HBP if ev == "hit_by_pitch" else W_BB
    x = row.get("estimated_woba_using_speedangle") or ""
    try:
        return float(x) if x else 0.0
    except ValueError:
        return 0.0


def ingest_day(state: dict, date_iso: str, rows) -> int:
    """Fold one day's pitch rows (any innings; only inning 1 PAs count) into the
    state.  Days must be ingested in order; a day at or before `as_of` is ignored
    (idempotent).  Returns the number of first-inning PAs ingested."""
    if state["as_of"] and date_iso <= state["as_of"]:
        return 0
    _rollover(state, date_iso[:4])
    n = 0
    pit = state["pitchers"]; lg = state["league"]
    for row in rows:
        pid = str(row.get("pitcher") or "").strip()
        if not pid:
            continue
        rec = pit.get(pid)
        if rec is None:
            rec = pit[pid] = {"cur": [0.0, 0.0], "prior": [0.0, 0.0], "seen": 0}
        rec["seen"] = 1                      # pitched this season (any inning)
        if (row.get("inning") or "") != "1":
            continue
        w = _pa_weight(row)
        if w is None:
            continue
        rec["cur"][0] += 1; rec["cur"][1] += w
        lg["pa"] += 1; lg["woba"] += w
        n += 1
    state["as_of"] = date_iso
    return n


def league_mean(state: dict) -> float:
    lg = state.get("league") or {}
    return (lg["woba"] / lg["pa"]) if lg.get("pa") else LEAGUE_FALLBACK


def estimate(state: dict, pitcher_id) -> float | None:
    """Shrunk pooled first-inning xwOBA allowed, or None if never seen."""
    rec = state.get("pitchers", {}).get(str(pitcher_id))
    if not rec:
        return None
    pa = rec["cur"][0] + rec["prior"][0]
    if pa <= 0:
        return None
    w = rec["cur"][1] + rec["prior"][1]
    return (w + K_PA * league_mean(state)) / (pa + K_PA)


def value_or_default(state: dict, pitcher_id) -> float:
    """What the predictor feeds the model: the estimate, else the league mean
    (= the shrinkage target, i.e. exactly what a zero-PA pitcher resolves to)."""
    e = estimate(state, pitcher_id) if pitcher_id else None
    return e if e is not None else league_mean(state)


# ----------------------------------------------------------------- data in
def fetch_day(date_iso: str, tries: int = 3, timeout: int = 120) -> list[dict] | None:
    url = _URL.format(season=date_iso[:4], d=date_iso)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == tries - 1:
                return None
            time.sleep(2 ** attempt * 2)
    return None


def yesterday_et() -> str:
    """The last COMPLETE slate day, in Eastern time.  Never use the machine's
    local date: GitHub runners are UTC, and at 9pm ET a UTC 'yesterday' is
    today's in-progress slate -- ingesting a partial day would freeze it as
    complete (ingest_day is idempotent by date)."""
    from zoneinfo import ZoneInfo
    return (dt.datetime.now(ZoneInfo("America/New_York")).date() - dt.timedelta(days=1)).isoformat()


def update(state: dict, through: str | None = None, max_days: int = MAX_CATCHUP_DAYS,
           log=print) -> int:
    """Advance the state day by day through `through` (default: yesterday, ET).
    Stops at the first failed fetch and keeps what it has (fail-open).
    Returns the number of days ingested."""
    through = min(through or yesterday_et(), yesterday_et())   # never a partial day
    if state["as_of"] is None:
        log("[fi_pool] empty state; run --rebuild first"); return 0
    d = dt.date.fromisoformat(state["as_of"]) + dt.timedelta(days=1)
    end = dt.date.fromisoformat(through)
    done = 0
    while d <= end and done < max_days:
        iso = d.isoformat()
        rows = fetch_day(iso)
        if rows is None:
            log(f"[fi_pool] fetch failed for {iso}; keeping state as of {state['as_of']}")
            break
        n = ingest_day(state, iso, rows)
        log(f"[fi_pool] {iso}: {n} first-inning PAs")
        done += 1; d += dt.timedelta(days=1)
    return done


def rebuild_from_cache(cache_glob: str = CACHE_GLOB) -> dict:
    state = new_state()
    for path in sorted(glob.glob(cache_glob)):
        day = os.path.basename(path)[:10]
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            ingest_day(state, day, csv.DictReader(fh))
    return state


# ----------------------------------------------------------------- cli
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--through", default=None)
    ap.add_argument("--show", default=None)
    ap.add_argument("--path", default=str(STATE_PATH))
    a = ap.parse_args(argv)
    path = Path(a.path)
    if a.rebuild:
        st = rebuild_from_cache()
        save_state(st, path)
        print(f"[fi_pool] rebuilt: as_of={st['as_of']} pitchers={len(st['pitchers'])} "
              f"league_mean={league_mean(st):.4f} -> {path}")
        return 0
    st = load_state(path)
    if a.update:
        n = update(st, a.through)
        if n:
            save_state(st, path)
        print(f"[fi_pool] as_of={st['as_of']} (+{n} days) pitchers={len(st['pitchers'])} "
              f"league_mean={league_mean(st):.4f}")
    if a.show:
        print(f"[fi_pool] {a.show}: {estimate(st, a.show)}  (league {league_mean(st):.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

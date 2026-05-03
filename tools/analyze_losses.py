#!/usr/bin/env python3
"""
tools/analyze_losses.py — T2.48 loss failure-mode classifier (Phase 1).

Pulls every graded LOSS row from picks_<season> in Supabase, fetches the
1st-inning play-by-play (cached on disk so MLB Stats API is hit at most
once per game ever), classifies each loss into a primary failure mode,
and upserts the result to the `loss_analysis` Supabase table.

The point: turn "we lost N bets this month" into "of those N losses,
X% are pure variance, Y% are a model blind spot worth fixing."  Without
that breakdown we can't tell which features to invest in next.

Failure modes (mutually exclusive primary):
  data_quality        — pick made on 'avg' / sm pitcher_q or batting_q;
                        the model never had real data to work with
  lineup_changed_late — actual top-3 batters in inning != recorded top-3;
                        we bet on the wrong matchup
  outside_top3_event  — NRFI loss; HR or RBI from batter not in top-3.
                        Real model blind spot — top-3 is the only batter
                        feature window in the model
  pitcher_dominated   — YRFI loss; K rate >=35% of plate appearances.
                        Model under-weighted whiff-rate features.
                        (League avg K% is ~22%; >=35% is well above norm.)
  sequencing          — YRFI loss; >=2 baserunners reached but DP / K-RISP
                        killed the inning.  Pure variance, not fixable
  quiet_inning        — YRFI loss; <=1 baserunner, K rate <35%.  Pitchers
                        worked clean innings; model overestimated runs.
                        Calibration issue (not pitcher dominance, not
                        bad luck -- just a quiet half-inning the model
                        shouldn't have rated >0.56)
  bunched_contact     — NRFI loss; legitimate hits clustered, no anomaly.
                        Model said "should be quiet"; contact happened
  other               — fallback when nothing matched cleanly (rare
                        after T2.48: should be <5% of losses)

Usage:
  python tools/analyze_losses.py                    # all unclassified losses
  python tools/analyze_losses.py --date 2026-05-02  # one slate's losses
  python tools/analyze_losses.py --since 2026-04-01 # losses from this date on
  python tools/analyze_losses.py --reclassify       # re-run on already-classified
                                                    # (for when classifier rules change)
  python tools/analyze_losses.py --season 2025      # historical season

Env vars:
  SUPABASE_URL, SUPABASE_SERVICE_KEY  — required (loaded from .env if present)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Repo path so we can import db.* + reuse the existing Supabase client ----
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

try:
    import statsapi
except ImportError:
    sys.exit("Missing dep: pip install MLB-StatsAPI")

from db.supabase_writer import _get_client


# ---------------------------------------------------------------------------
# Cache layer — one JSON file per game, never expires
# ---------------------------------------------------------------------------
#
# 1st-inning events are immutable once a game is final.  Cache aggressively
# so re-running the classifier costs zero MLB API calls for historical
# games.  Live re-runs (today's just-graded losses) hit the API once per
# game then are permanent.

CACHE_DIR = REPO_ROOT / "data" / "cache" / "play_by_play"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_first_inning_plays(game_pk: str | int) -> list[dict]:
    """Return a list of normalized 1st-inning play dicts:
        {half: 'top'|'bottom', batter_id, batter_name, pitcher_id,
         pitcher_name, event, description}

    Cached on disk after first fetch.  Returns [] on API error so the
    classifier can still emit a row tagged 'other' rather than crash."""
    cache_path = CACHE_DIR / f"{game_pk}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass    # fall through to refetch on corrupt cache

    try:
        data = statsapi.get("game", {
            "gamePk": int(game_pk),
            "fields": (
                "liveData,plays,allPlays,about,inning,halfInning,"
                "result,event,description,"
                "matchup,batter,pitcher,id,fullName"
            ),
        })
    except Exception as exc:    # noqa: BLE001 — network etc.
        print(f"  [cache] MLB fetch failed for game_pk={game_pk}: {exc!r}",
              file=sys.stderr)
        return []

    plays_raw = data.get("liveData", {}).get("plays", {}).get("allPlays", [])
    out: list[dict] = []
    for p in plays_raw:
        if p.get("about", {}).get("inning") != 1:
            continue
        half = p.get("about", {}).get("halfInning", "").lower()  # 'top'|'bottom'
        m    = p.get("matchup", {})
        b    = m.get("batter",  {}) or {}
        pi   = m.get("pitcher", {}) or {}
        r    = p.get("result",  {}) or {}
        out.append({
            "half":         half,
            "batter_id":    b.get("id"),
            "batter_name":  b.get("fullName") or "",
            "pitcher_id":   pi.get("id"),
            "pitcher_name": pi.get("fullName") or "",
            "event":        r.get("event") or "",
            "description":  r.get("description") or "",
        })

    try:
        cache_path.write_text(json.dumps(out), encoding="utf-8")
    except OSError as exc:
        print(f"  [cache] write failed for game_pk={game_pk}: {exc!r}",
              file=sys.stderr)

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Events that score or place a batter on base.  Used by the bunched-contact
# vs sequencing branch.
HIT_EVENTS  = {"Single", "Double", "Triple", "Home Run"}
WALK_EVENTS = {"Walk", "Hit By Pitch", "Intent Walk"}
K_EVENTS    = {"Strikeout"}
DP_KEYWORDS = ("Grounded Into DP", "GIDP", "Lined Into DP",
               "Triple Play", "Double Play")


def _is_dp(event_str: str) -> bool:
    return any(kw in (event_str or "") for kw in DP_KEYWORDS)


def _parse_top3_ids(loss_row: dict, side: str) -> list[int]:
    """Pull batter MLB IDs from the recorded lineup_json.  `side` is
    'home' or 'away'.  Returns [] when JSON missing / malformed."""
    raw = loss_row.get(f"{side}_lineup_json") or "[]"
    if isinstance(raw, list):
        lineup = raw
    else:
        try:
            lineup = json.loads(raw) if isinstance(raw, str) else []
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(lineup, list):
        return []
    ids = []
    for entry in lineup[:3]:
        if isinstance(entry, dict) and "id" in entry:
            try:
                ids.append(int(entry["id"]))
            except (TypeError, ValueError):
                pass
    return ids


def _first_3_unique_batters(plays: list[dict]) -> list[int]:
    """First 3 unique batter IDs in a half-inning -- the actual top-3
    that came up.  Mirrors what the model meant to predict."""
    out: list[int] = []
    for p in plays:
        bid = p.get("batter_id")
        if not bid:
            continue
        if bid not in out:
            out.append(bid)
        if len(out) == 3:
            break
    return out


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

def classify_loss(loss_row: dict, plays: list[dict]) -> dict:
    """Return {primary_mode, secondary_modes: list, notes: str} for one
    loss row.  Pure function -- no IO, easy to unit-test."""
    pick     = (loss_row.get("pick_side") or "").upper()
    fi_runs  = loss_row.get("fi_total_runs") or 0
    plays_top = [p for p in plays if p.get("half") == "top"]
    plays_bot = [p for p in plays if p.get("half") == "bottom"]

    primary: str | None = None
    secondary: list[str] = []
    notes: list[str] = []

    # ---- (1) data_quality ----------------------------------------------
    # Any 'avg' (no real data, league-default) flag means the pick was made
    # on placeholders.  Always classify these first; everything below
    # presupposes the model had something real to work with.
    qmap = {
        "home_pitcher_q": loss_row.get("home_pitcher_q"),
        "away_pitcher_q": loss_row.get("away_pitcher_q"),
        "home_batting_q": loss_row.get("home_batting_q"),
        "away_batting_q": loss_row.get("away_batting_q"),
    }
    weak = {k: v for k, v in qmap.items() if (v or "").lower() == "avg"}
    if weak:
        primary = "data_quality"
        notes.append(f"League-default placeholders used: {sorted(weak.keys())}")

    # ---- (2) lineup_changed_late ---------------------------------------
    # Compare recorded top-3 (from lineup_json at pick time) to the actual
    # first-3-unique batters who came up.  When the lineup posted late or
    # got revised, the picks_2026 row's lineup is stale and we essentially
    # bet a different game than the one that played.
    away_recorded_top3 = _parse_top3_ids(loss_row, "away")
    home_recorded_top3 = _parse_top3_ids(loss_row, "home")
    away_actual_top3   = _first_3_unique_batters(plays_top)
    home_actual_top3   = _first_3_unique_batters(plays_bot)

    away_match = (
        len(away_recorded_top3) >= 2 and len(away_actual_top3) >= 2
        and set(away_recorded_top3) != set(away_actual_top3)
    )
    home_match = (
        len(home_recorded_top3) >= 2 and len(home_actual_top3) >= 2
        and set(home_recorded_top3) != set(home_actual_top3)
    )
    if away_match or home_match:
        if primary is None:
            primary = "lineup_changed_late"
        else:
            secondary.append("lineup_changed_late")
        notes.append("Recorded top-3 != actual top-3")

    # ---- (3) NRFI losses: outside_top3_event vs bunched_contact --------
    if pick == "NRFI" and fi_runs > 0:
        # Did a HR happen in the inning?  HR by a batter outside top-3 is
        # the canonical model blind spot (model only sees top-3 power).
        all_top3 = set(away_recorded_top3) | set(home_recorded_top3) \
                 | set(away_actual_top3)   | set(home_actual_top3)
        hr_plays = [p for p in plays if p.get("event") == "Home Run"]
        outside_top3_hr = any(
            p.get("batter_id") and p["batter_id"] not in all_top3
            for p in hr_plays
        )
        if outside_top3_hr:
            if primary is None:
                primary = "outside_top3_event"
            else:
                secondary.append("outside_top3_event")
            hitter_names = [p.get("batter_name", "?") for p in hr_plays
                            if p.get("batter_id") not in all_top3]
            notes.append(f"HR by batter outside top-3: {', '.join(hitter_names)}")
        if primary is None:
            primary = "bunched_contact"
            n_hits = sum(1 for p in plays if p.get("event") in HIT_EVENTS)
            notes.append(f"{fi_runs} run(s) on {n_hits} hit(s); no top-3-blind anomaly")

    # ---- (4) YRFI losses: pitcher_dominated / sequencing / quiet_inning -
    if pick == "YRFI" and fi_runs == 0:
        n_pa     = len(plays)
        n_k      = sum(1 for p in plays if p.get("event") in K_EVENTS)
        n_walks  = sum(1 for p in plays if p.get("event") in WALK_EVENTS)
        n_hits   = sum(1 for p in plays if p.get("event") in HIT_EVENTS)
        n_dp     = sum(1 for p in plays if _is_dp(p.get("event", "")))
        runners  = n_hits + n_walks
        k_rate   = (n_k / n_pa) if n_pa else 0.0

        # Pitcher_dominated: K rate well above league avg (~22%)
        if k_rate >= 0.35 and n_pa >= 5:
            if primary is None:
                primary = "pitcher_dominated"
            else:
                secondary.append("pitcher_dominated")
            notes.append(f"K rate {n_k}/{n_pa}={k_rate:.0%} of plate appearances")
        # Sequencing: contact happened but inning ended via DP / RISP-K /
        # 9-pitch outs in scoring spots
        if runners >= 2 and n_dp >= 1:
            if primary is None:
                primary = "sequencing"
            else:
                secondary.append("sequencing")
            notes.append(f"{runners} runners reached, {n_dp} DP killed inning")
        elif runners >= 2 and primary is None:
            primary = "sequencing"
            notes.append(f"{runners} runners reached but no run scored (variance)")
        # Quiet inning: ≤1 baserunner, K rate not extreme.  This is the
        # single most common YRFI failure mode -- the inning just didn't
        # produce action.  Calibration signal: the model's YRFI threshold
        # may be too aggressive on these matchup profiles.
        elif primary is None:
            primary = "quiet_inning"
            notes.append(
                f"Quiet inning: {runners} runner(s), {n_k} K, "
                f"{n_pa} PA -- pitchers worked clean"
            )

    # ---- fallback -----------------------------------------------------
    if primary is None:
        primary = "other"
        notes.append("No classifier rule matched")

    return {
        "primary_mode":    primary,
        "secondary_modes": secondary,
        "notes":           " | ".join(notes) if notes else None,
    }


# ---------------------------------------------------------------------------
# Supabase IO
# ---------------------------------------------------------------------------

def fetch_losses(client: Any, date: str | None, since: str | None,
                 season: int) -> list[dict]:
    """Pull all picks_<season> rows where graded_result='LOSS', optionally
    filtered to a date or date range.  Returns a list of dicts."""
    table = f"picks_{season}"
    q = client.table(table).select(
        "date,game_pk,away_team,home_team,"
        "pick_side,pick_strength,pick_label,"
        "nrfi_prob,yrfi_prob,fi_total_runs,fi_away_runs,fi_home_runs,"
        "profit_loss_units,units_risked,"
        "home_pitcher_q,away_pitcher_q,home_batting_q,away_batting_q,"
        "home_lineup_json,away_lineup_json"
    ).eq("graded_result", "LOSS")
    if date:
        q = q.eq("date", date)
    elif since:
        q = q.gte("date", since)
    return q.execute().data or []


def already_classified_keys(client: Any) -> set[tuple[str, str]]:
    """Return {(date, game_pk)} of losses already in loss_analysis -- so
    the default invocation only processes new losses."""
    res = client.table("loss_analysis").select("date,game_pk").execute()
    return {(r["date"], r["game_pk"]) for r in (res.data or [])}


def upsert_classification(client: Any, loss_row: dict, cls: dict) -> None:
    """Upsert one classification row.  Uses ON CONFLICT (date, game_pk)
    so re-running with --reclassify silently overwrites."""
    pl    = loss_row.get("profit_loss_units")
    units_lost = abs(float(pl)) if pl not in (None, "") else None
    nrfi  = loss_row.get("nrfi_prob") or 0
    yrfi  = loss_row.get("yrfi_prob") or 0
    pick_prob = max(nrfi, yrfi)
    payload = {
        "date":            loss_row["date"],
        "game_pk":         str(loss_row["game_pk"]),
        "away_team":       loss_row.get("away_team"),
        "home_team":       loss_row.get("home_team"),
        "pick_side":       loss_row.get("pick_side"),
        "pick_strength":   loss_row.get("pick_strength"),
        "pick_prob":       pick_prob,
        "fi_total_runs":   loss_row.get("fi_total_runs"),
        "units_lost":      units_lost,
        "primary_mode":    cls["primary_mode"],
        "secondary_modes": cls["secondary_modes"],
        "notes":           cls["notes"],
        "classified_at":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
    }
    client.table("loss_analysis").upsert(
        payload, on_conflict="date,game_pk"
    ).execute()


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------

def print_summary(losses: list[dict], classified: list[dict]) -> None:
    """Pretty per-mode breakdown for the operator."""
    if not classified:
        print("\nNo losses processed in this run.")
        return

    by_mode  = Counter(c["cls"]["primary_mode"] for c in classified)
    units    = defaultdict(float)
    for c in classified:
        u = c["loss"].get("profit_loss_units")
        if u is not None:
            try:
                units[c["cls"]["primary_mode"]] += abs(float(u))
            except (TypeError, ValueError):
                pass

    total = sum(by_mode.values())
    total_units = sum(units.values())
    print()
    print("=" * 76)
    print(f"  Loss-mode breakdown  ({total} losses, {total_units:.1f}u lost)")
    print("=" * 76)
    print(f"  {'mode':22} {'count':>8} {'%':>6} {'units lost':>12}")
    print("  " + "-" * 70)
    # Order matters: actionable modes first so they jump out
    order = [
        "outside_top3_event",
        "pitcher_dominated",
        "lineup_changed_late",
        "data_quality",
        "quiet_inning",
        "bunched_contact",
        "sequencing",
        "other",
    ]
    seen = set()
    for mode in order + sorted(set(by_mode) - set(order)):
        if mode in seen or mode not in by_mode:
            continue
        seen.add(mode)
        n = by_mode[mode]
        pct = 100.0 * n / total if total else 0.0
        print(f"  {mode:22} {n:>8} {pct:>5.0f}% {units.get(mode, 0):>11.2f}u")
    print()

    # Surface the classifier's actionable verdict
    actionable = sum(by_mode.get(m, 0) for m in (
        "outside_top3_event", "pitcher_dominated", "lineup_changed_late",
        "quiet_inning",   # calibration -- a real model improvement target
    ))
    variance  = sum(by_mode.get(m, 0) for m in (
        "sequencing", "bunched_contact",
    ))
    data_q    = by_mode.get("data_quality", 0)
    print(f"  Verdict: {actionable}/{total} ({100*actionable/total:.0f}%) actionable, "
          f"{variance}/{total} ({100*variance/total:.0f}%) variance, "
          f"{data_q}/{total} ({100*data_q/total:.0f}%) data-quality")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date",       metavar="YYYY-MM-DD",
                        help="Only classify losses from this date.")
    parser.add_argument("--since",      metavar="YYYY-MM-DD",
                        help="Classify all losses from this date onward.")
    parser.add_argument("--season",     type=int, default=datetime.now(timezone.utc).replace(tzinfo=None).year,
                        help="Season table to query (default: current year).")
    parser.add_argument("--reclassify", action="store_true",
                        help="Re-run classifier on already-classified rows "
                             "(useful after classifier rule changes).")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Classify + print, but skip the Supabase upsert.")
    args = parser.parse_args()

    client = _get_client()
    if client is None:
        sys.exit(
            "Supabase not configured.  Set SUPABASE_URL + SUPABASE_SERVICE_KEY "
            "in .env or env vars."
        )

    losses = fetch_losses(client, args.date, args.since, args.season)
    if not losses:
        print(f"No LOSS rows found in picks_{args.season} "
              f"(date={args.date or 'any'}, since={args.since or 'any'}).")
        return

    skip_keys = set() if args.reclassify else already_classified_keys(client)
    print(f"Found {len(losses)} loss row(s); "
          f"{'re-classifying all' if args.reclassify else f'skipping {len(skip_keys)} already classified'}.\n")

    classified: list[dict] = []
    for loss in losses:
        key = (loss["date"], str(loss["game_pk"]))
        if key in skip_keys:
            continue
        plays = fetch_first_inning_plays(loss["game_pk"])
        cls = classify_loss(loss, plays)
        if not args.dry_run:
            try:
                upsert_classification(client, loss, cls)
            except Exception as exc:    # noqa: BLE001
                print(f"  [upsert] {key} failed: {exc!r}", file=sys.stderr)
                continue
        classified.append({"loss": loss, "cls": cls})
        # Per-loss line so the operator can spot-check
        print(f"  {loss['date']}  {loss['away_team']}@{loss['home_team']:<3}  "
              f"{loss.get('pick_side','?'):4} {loss.get('pick_strength','?'):6}  "
              f"runs={loss.get('fi_total_runs'):>2}  "
              f"-> {cls['primary_mode']}")
        if cls.get("notes"):
            print(f"      {cls['notes']}")

    print_summary(losses, classified)


if __name__ == "__main__":
    main()

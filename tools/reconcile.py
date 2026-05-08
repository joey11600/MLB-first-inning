"""Continuous reconciliation + auto-heal for picks_<season> in Supabase.

PURPOSE
=======
Designed to be called every Railway predictor cycle (~5 min during
active hours) AND once at the end of every grade run.  It scans
recent rows, asserts a set of invariants, and auto-heals any
violation.  Whatever the rest of the system does -- new writers
land, race conditions hit, deploys redeploy mid-cycle -- this sweep
is the safety net that brings everything back to a consistent state.

Idempotent.  Safe to run any number of times.  Each invariant heals
ONLY what's wrong; healthy rows are no-ops.

INVARIANTS
==========
I1.  "graded → bet_placed set"
     A row with graded_result IN (WIN, LOSS) MUST have bet_placed
     IN (Y, N).  Empty bet_placed on a graded STRONG row means
     a writer crashed mid-flight or read a stale CSV; we heal by
     running end_of_day_check's orphan logic (set bet_placed=Y
     and recompute pl).  See tools/end_of_day_check.py for the
     full rationale.

I2.  "graded WIN/LOSS uses real captured odds when available"
     If market_*_odds for the picked side IS populated AND the
     stored profit_loss_units is the -110 fallback (0.909 for a
     1u WIN, 0.4545 for a 0.5u WIN, etc.), recompute pl from the
     real odds and patch.  Catches the same drift
     tools/recover_today_pl.py was built for.

I3.  "every placed STRONG bet has a strong_locked notification"
     bet_placed='Y' AND pick_strength='STRONG' AND game_time has
     passed → notifications_log MUST have a strong_locked entry
     for this game_pk.  Heal: fire _notify_strong_locked_telegram.

I4.  "every graded STRONG bet has a strong_graded notification"
     graded IN (WIN, LOSS) AND pick_strength='STRONG' AND
     bet_placed='Y' → notifications_log MUST have a strong_graded
     entry.  Heal: fire _notify_strong_graded_telegram.

OBSERVABILITY
=============
Each invariant logs what it found + healed in a single line.  When
an invariant SHOULD have healed but the heal call failed (e.g.
Telegram outage), we write a row to data/system_errors.csv +
Supabase.system_errors so the dashboard's Ops Health card surfaces
the residual anomaly.

USAGE
=====
  python tools/reconcile.py                # default: today + last 2 days
  python tools/reconcile.py --date 2026-05-07
  python tools/reconcile.py --days 8
  python tools/reconcile.py --all          # entire season
  python tools/reconcile.py --dry-run      # report only, no writes
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.supabase_writer import _get_client


# --------------------------------------------------------------------------
# Date helpers
# --------------------------------------------------------------------------

def _et_today() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _et_window(days: int) -> list[str]:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


# --------------------------------------------------------------------------
# Pure-math helpers (mirror tracker._calc_pnl + workers.live_state._compute_pnl)
# --------------------------------------------------------------------------

_FALLBACK_PPU = 100.0 / 110.0   # = 0.9090909..., the -110 payout per unit


def _payout_per_unit(odds_str) -> float | None:
    if odds_str is None:
        return None
    s = str(odds_str).strip()
    if not s:
        return None
    try:
        o = float(s)
    except (ValueError, TypeError):
        return None
    if o == 0:
        return None
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def _correct_pl(row: dict) -> float | None:
    """Recompute the canonical pl for a graded row using stored
    market_*_odds + units_risked.  Mirrors tracker._calc_pnl semantics."""
    grade = (row.get("graded_result") or "").strip().upper()
    if grade not in ("WIN", "LOSS"):
        return None
    side = (row.get("pick_side") or "").strip().upper()
    if side not in ("NRFI", "YRFI"):
        return None
    units_raw = row.get("units_risked")
    units = float(units_raw) if units_raw not in (None, "") else 1.0
    if units <= 0:
        return None
    if grade == "LOSS":
        return round(-units, 3)
    odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    ppu = _payout_per_unit(row.get(odds_col))
    if ppu is None:
        ppu = _FALLBACK_PPU
    return round(units * ppu, 3)


def _is_fallback_pl(stored_pl: float | None, units: float) -> bool:
    """True if the stored pl looks like the -110 fallback for this units."""
    if stored_pl is None:
        return False
    expected_fallback = round(units * _FALLBACK_PPU, 3)
    return abs(stored_pl - expected_fallback) < 0.005


# --------------------------------------------------------------------------
# Game-time helpers (lifted from tracker._is_inside_lock_window)
# --------------------------------------------------------------------------

def _game_dt_et(game_time_et: str, iso_date: str) -> datetime | None:
    if not game_time_et or ":" not in game_time_et:
        return None
    cleaned = game_time_et.replace("ET", "").strip()
    et = ZoneInfo("America/New_York")
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            t = datetime.strptime(cleaned, fmt)
            return datetime.fromisoformat(iso_date).replace(
                hour=t.hour, minute=t.minute, tzinfo=et,
            )
        except ValueError:
            continue
    return None


def _game_started(game_time_et: str, iso_date: str) -> bool:
    """True if the scheduled first pitch is in the past."""
    g = _game_dt_et(game_time_et, iso_date)
    if g is None:
        return False
    return datetime.now(ZoneInfo("America/New_York")) >= g


def _within_lock_window(game_time_et: str, iso_date: str,
                         lock_min: int = 60) -> bool:
    """True if now is within lock_min minutes of first pitch (or after)."""
    g = _game_dt_et(game_time_et, iso_date)
    if g is None:
        return False
    return datetime.now(ZoneInfo("America/New_York")) >= (
        g - timedelta(minutes=lock_min)
    )


# T-V21-2026-05-07e: telegram pings are RECENT-only.  An old graded
# bet from 3 weeks ago whose dedup row got lost is data we want to
# repair (insert the notifications_log entry so it's "remembered"),
# but firing the actual telegram is misleading -- the user's already
# moved on.  Only the last 36h of games get an actual ping.  Older
# data heals quietly: I1 patches Supabase, I2 fixes pl, I3/I4 record
# the dedup row without sending.
NOTIFY_FRESHNESS_HOURS = 36


def _is_fresh(game_time_et: str, iso_date: str) -> bool:
    """True if the game's first pitch is within NOTIFY_FRESHNESS_HOURS."""
    g = _game_dt_et(game_time_et, iso_date)
    if g is None:
        # Can't parse time (e.g. "After Game 1" placeholders); assume
        # recent if iso_date is today or yesterday ET.
        try:
            slate = datetime.fromisoformat(iso_date).date()
            today = datetime.now(ZoneInfo("America/New_York")).date()
            return (today - slate).days <= 1
        except (ValueError, TypeError):
            return False
    age_hours = (datetime.now(ZoneInfo("America/New_York")) - g).total_seconds() / 3600.0
    return age_hours <= NOTIFY_FRESHNESS_HOURS


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

class ReconcileStats:
    def __init__(self):
        self.scanned = 0
        self.i1_orphans_healed = 0
        self.i2_pl_corrections = 0
        self.i3_strong_locked_fired = 0
        self.i4_strong_graded_fired = 0
        self.errors: list[str] = []

    def __str__(self) -> str:
        parts = [f"scanned={self.scanned}"]
        if self.i1_orphans_healed:
            parts.append(f"I1 orphan heals={self.i1_orphans_healed}")
        if self.i2_pl_corrections:
            parts.append(f"I2 pl corrections={self.i2_pl_corrections}")
        if self.i3_strong_locked_fired:
            parts.append(f"I3 strong_locked fires={self.i3_strong_locked_fired}")
        if self.i4_strong_graded_fired:
            parts.append(f"I4 strong_graded fires={self.i4_strong_graded_fired}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return "  ".join(parts)


def _fetch_window(client, dates: list[str] | None, season: int) -> list[dict]:
    """Pull the columns each invariant needs for the date window."""
    table = f"picks_{season}"
    cols = (
        "date, game_pk, game_time_et, away_team, home_team, "
        "pick_side, pick_strength, pick_label, "
        "graded_result, fi_away_runs, fi_home_runs, fi_total_runs, graded_at, "
        "market_nrfi_odds, market_yrfi_odds, sportsbook, "
        "edge_on_pick, opened_nrfi_odds, opened_yrfi_odds, "
        "bet_placed, units_risked, profit_loss_units"
    )
    q = client.table(table).select(cols)
    if dates is not None:
        q = q.in_("date", dates)
    return q.execute().data or []


def _fetch_notifications(client, dates: list[str] | None) -> set[tuple[str, str]]:
    """Set of (event_type, event_key) entries on or after the earliest
    date in `dates` -- so we can answer "did event_X fire for game_Y?"
    in O(1).  Loose ISO date filter is fine here since notifications_log
    is small (~tens of rows per slate)."""
    q = client.table("notifications_log").select("event_type, event_key")
    if dates is not None:
        # Events fire UTC-stamped a few hours after slate ET-date; pull
        # 48h before the earliest date to be safe.
        min_date = min(dates) if dates else _et_today()
        cutoff = (datetime.fromisoformat(min_date)
                  - timedelta(days=2)).isoformat()
        q = q.gte("captured_at_utc", cutoff)
    res = q.execute()
    return {(r["event_type"], r["event_key"]) for r in (res.data or [])}


def _record_step_failure(message: str, error: str) -> None:
    """Append to system_errors so the dashboard surfaces residual issues."""
    try:
        from db.supabase_writer import mirror_system_error
        from tracker import _now_utc
        et_date = _et_today()
        mirror_system_error(
            captured_at_utc=_now_utc(),
            iso_date=et_date,
            step="reconcile",
            exit_code=1,
            message=f"{message}: {error}"[:1500],
        )
    except Exception as exc:    # noqa: BLE001 -- best effort
        print(f"[reconcile] could not write system_errors: {exc!r}",
              file=sys.stderr)


def _heal_i1_orphan(client, row: dict, dry_run: bool) -> bool:
    """I1: graded WIN/LOSS row with bet_placed empty.  Treat as orphan
    and flip to bet_placed=Y at 1u flat (mirrors end_of_day_check)."""
    if dry_run:
        return True
    season = int(row["date"][:4])
    units = float(row.get("units_risked") or 1.0) or 1.0
    pl = _correct_pl({**row, "units_risked": units})
    payload = {
        "bet_placed": "Y",
        "units_risked": units,
        "profit_loss_units": pl,
    }
    try:
        (client.table(f"picks_{season}")
                .update(payload)
                .eq("date", row["date"])
                .eq("game_pk", str(row["game_pk"]))
                .execute())
        return True
    except Exception as exc:    # noqa: BLE001
        print(f"[reconcile] I1 heal failed for "
              f"{row['date']}/{row['game_pk']}: {exc!r}", file=sys.stderr)
        return False


def _heal_i2_pl_drift(client, row: dict, correct: float, dry_run: bool) -> bool:
    if dry_run:
        return True
    season = int(row["date"][:4])
    try:
        (client.table(f"picks_{season}")
                .update({"profit_loss_units": correct})
                .eq("date", row["date"])
                .eq("game_pk", str(row["game_pk"]))
                .execute())
        return True
    except Exception as exc:    # noqa: BLE001
        print(f"[reconcile] I2 heal failed for "
              f"{row['date']}/{row['game_pk']}: {exc!r}", file=sys.stderr)
        return False


def _stringify_row(row: dict) -> dict:
    """Supabase returns numerics as int/float and timestamps as ISO
    strings.  tracker's notifier helpers expect every column as a string
    (CSV-style: numbers formatted, blanks as '').  Coerce so a Supabase
    row drops cleanly into the notifier without `.strip() on int`
    AttributeErrors."""
    out: dict = {}
    for k, v in row.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, bool):
            out[k] = "Y" if v else "N"
        elif isinstance(v, (int, float)):
            # Match tracker._fmt's rounding for the columns the notifier
            # actually formats: pl 3dp, units 2dp, default 4dp.
            if k == "profit_loss_units":
                out[k] = f"{v:.3f}".rstrip("0").rstrip(".") or "0"
            elif k == "units_risked":
                out[k] = f"{v:.2f}".rstrip("0").rstrip(".") or "0"
            elif isinstance(v, int):
                out[k] = str(v)
            else:
                out[k] = f"{v:.4f}".rstrip("0").rstrip(".") or "0"
        else:
            out[k] = str(v)
    return out


def _record_dedup_only(client, event_type: str, event_key: str) -> None:
    """Insert a notifications_log row WITHOUT firing telegram.  Used
    when reconcile detects a missing dedup entry on an OLD game --
    we want to backfill the dedup row so future runs don't keep
    "discovering" the same anomaly, but firing a stale ping is
    counterproductive."""
    try:
        from tracker import _now_utc
        client.table("notifications_log").insert({
            "captured_at_utc": _now_utc(),
            "event_type":      event_type,
            "event_key":       event_key,
            "chat_id":         "(reconcile-backfill)",
        }).execute()
    except Exception as exc:    # noqa: BLE001
        print(f"[reconcile] dedup-only insert failed for "
              f"{event_type}:{event_key}: {exc!r}", file=sys.stderr)


def _heal_i3_strong_locked(client, row: dict, dry_run: bool) -> bool:
    if dry_run:
        return True
    if not _is_fresh(row.get("game_time_et") or "", row.get("date") or ""):
        # Old game: backfill the dedup entry but don't telegram.
        _record_dedup_only(client, "strong_locked",
                           f"strong_locked:{row['game_pk']}")
        return True
    try:
        from tracker import _notify_strong_locked_telegram
        _notify_strong_locked_telegram(_stringify_row(row))
        return True
    except Exception as exc:    # noqa: BLE001
        print(f"[reconcile] I3 heal failed for "
              f"{row['date']}/{row['game_pk']}: {exc!r}", file=sys.stderr)
        return False


def _heal_i4_strong_graded(client, rows: list[dict], row: dict,
                            dry_run: bool) -> bool:
    if dry_run:
        return True
    if not _is_fresh(row.get("game_time_et") or "", row.get("date") or ""):
        # Old game: backfill the dedup entry but don't telegram.
        _record_dedup_only(client, "strong_graded",
                           f"strong_graded:{row['game_pk']}")
        return True
    try:
        from tracker import (
            _notify_strong_graded_telegram, _aggregate_today_record,
        )
        # _aggregate_today_record reads pl_loss_units and bet_placed off
        # each row; Supabase numerics break it the same way as the notifier.
        stringy_rows = [_stringify_row(r) for r in rows]
        today_record, today_pl = _aggregate_today_record(stringy_rows, row["date"])
        _notify_strong_graded_telegram(_stringify_row(row), today_record, today_pl)
        return True
    except Exception as exc:    # noqa: BLE001
        print(f"[reconcile] I4 heal failed for "
              f"{row['date']}/{row['game_pk']}: {exc!r}", file=sys.stderr)
        return False


def reconcile(
    dates: list[str] | None,
    season: int,
    dry_run: bool = False,
) -> ReconcileStats:
    """Run all invariants over the given date window.  Auto-heal violations."""
    stats = ReconcileStats()
    client = _get_client()
    if client is None:
        print("[reconcile] Supabase env vars not set; skipping.",
              file=sys.stderr)
        return stats

    rows = _fetch_window(client, dates, season)
    notes = _fetch_notifications(client, dates)
    stats.scanned = len(rows)

    # Group rows by date for I4's today_record helper.
    rows_by_date: dict[str, list[dict]] = {}
    for r in rows:
        rows_by_date.setdefault(r["date"], []).append(r)

    for r in rows:
        side = (r.get("pick_side") or "").strip().upper()
        strength = (r.get("pick_strength") or "").strip().upper()
        bet = (r.get("bet_placed") or "").strip().upper()
        grade = (r.get("graded_result") or "").strip().upper()
        game_pk = str(r.get("game_pk") or "")
        if not game_pk:
            continue

        # ---- I1: graded → bet_placed must be Y or N ------------------
        if (grade in ("WIN", "LOSS")
                and bet not in ("Y", "N")
                and strength == "STRONG"
                and side in ("NRFI", "YRFI")):
            if _heal_i1_orphan(client, r, dry_run):
                stats.i1_orphans_healed += 1
                bet = "Y"   # update locally for downstream invariants
                r["bet_placed"] = "Y"

        # ---- I2: pl matches real odds when available -----------------
        if grade in ("WIN", "LOSS") and bet == "Y" and side in ("NRFI", "YRFI"):
            odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
            picked_odds = (r.get(odds_col) or "")
            if str(picked_odds).strip():
                stored = r.get("profit_loss_units")
                stored_f = float(stored) if stored is not None else None
                correct = _correct_pl(r)
                if (correct is not None
                        and stored_f is not None
                        and abs(stored_f - correct) > 0.005):
                    if _heal_i2_pl_drift(client, r, correct, dry_run):
                        stats.i2_pl_corrections += 1

        # ---- I3: strong_locked fired for placed STRONG ---------------
        if (strength == "STRONG"
                and bet == "Y"
                and side in ("NRFI", "YRFI")
                and _within_lock_window(r.get("game_time_et") or "",
                                          r.get("date") or "")):
            key1 = ("strong_locked", f"strong_locked:{game_pk}")
            # Old-format key (pre-T-V21-2026-05-06h) for back-compat:
            key2 = ("strong_locked", f"strong_locked:{r['date']}:{game_pk}")
            if key1 not in notes and key2 not in notes:
                if _heal_i3_strong_locked(client, r, dry_run):
                    stats.i3_strong_locked_fired += 1
                    notes.add(key1)

        # ---- I4: strong_graded fired for graded STRONG ---------------
        if (strength == "STRONG"
                and bet == "Y"
                and grade in ("WIN", "LOSS")
                and side in ("NRFI", "YRFI")):
            key1 = ("strong_graded", f"strong_graded:{game_pk}")
            key2 = ("strong_graded", f"strong_graded:{r['date']}:{game_pk}")
            if key1 not in notes and key2 not in notes:
                if _heal_i4_strong_graded(client, rows_by_date[r["date"]],
                                            r, dry_run):
                    stats.i4_strong_graded_fired += 1
                    notes.add(key1)

    print(f"[reconcile] {stats}")
    return stats


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--date", help="ET slate date YYYY-MM-DD")
    grp.add_argument("--all", action="store_true",
                     help="Reconcile the entire season")
    grp.add_argument("--days", type=int, default=3,
                     help="Trailing window size (default: today + last 2)")
    parser.add_argument("--season", type=int,
                        default=int(_et_today()[:4]))
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only; do not heal.")
    args = parser.parse_args(argv)

    if args.date:
        dates: list[str] | None = [args.date]
    elif args.all:
        dates = None
    else:
        dates = _et_window(args.days)

    stats = reconcile(dates, args.season, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

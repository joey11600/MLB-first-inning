"""
tracker.py  --  Pick logging, result grading, and performance summary.

This module is intentionally self-contained (no imports from the predictor)
so it can be called from the predictor without circular-import issues.
All heavy lifting lives here; the predictor just calls three functions:
  log_picks(date_str, season, results)
  grade_date(date_str, season)
  show_summary(season, last_n, date_from, date_to)
"""

import csv
import os
import sys
import urllib.request
from datetime import datetime, date as date_type, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    import statsapi
except ImportError:
    sys.exit("Missing dependency: pip install mlb-statsapi")

# ---------------------------------------------------------------------------
# Paths and schema
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"

# All CSV columns in order.  Result and odds columns are empty on first write;
# populated later by --grade and (future) odds ingestion.
FIELDS = [
    # --- core prediction ---
    "date", "season", "game_pk", "game_number",
    "away_team", "home_team", "game_time_et",
    "away_pitcher", "home_pitcher",
    "away_pitcher_id", "home_pitcher_id",
    "away_pitcher_q", "home_pitcher_q",
    "away_batting_q", "home_batting_q",
    "park_factor",
    "away_proj_runs", "home_proj_runs", "combined_lambda",
    # LR-v3 two-stage expected runs per half (for Slate Projections display)
    "lambda_lr_t1", "lambda_lr_b1", "lambda_lr_total",
    "nrfi_prob", "yrfi_prob", "over_1_5_prob", "under_1_5_prob",
    # T3.13: raw (uncalibrated) probs preserved alongside calibrated ones
    # so Variant K (db/variants.py) can apply alternate calibrators
    # (e.g. calibration_v3.json) post-hoc.  Old rows pre-T3.13 will have
    # blanks here; Variant K mirrors production for those.
    "nrfi_prob_raw", "yrfi_prob_raw",
    "pick_side", "pick_strength", "pick_label",
    "blended_inputs",
    "created_at",
    # --- pitcher model inputs ---
    "away_era", "home_era",
    "away_whip", "home_whip",
    "away_fip", "home_fip",
    "away_bb9", "home_bb9",
    "away_hr9", "home_hr9",
    "away_k9",  "home_k9",
    # --- offense model inputs ---
    "away_obp", "home_obp",
    "away_slg", "home_slg",
    "away_rpg", "home_rpg",
    # --- weather (open-meteo at park; blanks for archive misses, dome
    # parks have wx_is_dome=1 with weather cols left blank) ---
    "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    # --- Phase D features: pitcher recent-form, top-3 batter point-in-time
    # OBP, home plate umpire's career NRFI rate ---
    "home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi",
    "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
    "home_top3c_obp", "away_top3c_obp",
    # Power signal (added 2026-04-29): top-3 SLG + ISO
    "home_top3c_slg", "away_top3c_slg",
    "home_top3c_iso", "away_top3c_iso",
    # Data-source provenance (added 2026-04-29): "lineup" | "team_fallback"
    # | "league_default".  Drives the PASS - LINEUP PENDING guard so morning
    # picks built on imputed top-3 stats don't show false confidence.
    "home_top3c_source", "away_top3c_source",
    # Per-batter top-3 lineup, JSON-encoded list of dicts with
    # {id, name, bats, obp, slg, iso, ab}.  CSV writer auto-quotes the
    # commas inside.  Empty "[]" when lineup hasn't posted yet.
    "home_lineup_json", "away_lineup_json",
    # T4.15: top-5 contributing LR features per half-inning (signed
    # contribution = w * (x - mean) / std).  JSON-encoded list of
    # {name, value, contribution} dicts.  Drives the "Why this pick?"
    # panel in the dashboard's expanded GameDetails view.
    "top_factors_t1_json", "top_factors_b1_json",
    "home_plate_ump_id", "home_plate_ump_nrfi_rate",
    # --- Phase E.3 Statcast features: xERA + whiff_pct_rank per pitcher ---
    "home_xera", "away_xera",
    "home_whiff_pct_rank", "away_whiff_pct_rank",
    # --- Phase F: pitcher-vs-team familiarity + opener detection ---
    "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
    "home_avg_ip_per_start", "away_avg_ip_per_start",
    # --- result (filled by --grade) ---
    "actual_result",     # NRFI | YRFI | POSTPONED | SUSPENDED
    "graded_result",     # WIN | LOSS | PASS | POSTPONED | SUSPENDED
    "fi_away_runs",
    "fi_home_runs",
    "fi_total_runs",
    "graded_at",
    # --- odds & edge (reserved -- odds system temporarily disabled) ---
    # These columns are kept in FIELDS so existing CSV rows load cleanly.
    # They are never written or updated while the odds system is disabled.
    "market_nrfi_odds",
    "market_yrfi_odds",
    "sportsbook",
    "odds_captured_at",
    "implied_nrfi_prob",
    "implied_yrfi_prob",
    "edge_nrfi",
    "edge_yrfi",
    "edge_on_pick",
    "bet_placed",
    "units_risked",
    "profit_loss_units",
    # T4.28: Closing-line value tracking.  `opened_*` is the FIRST-EVER
    # odds capture for this row -- set once on initial import and never
    # overwritten, representing the "open" odds we actually bet.
    # `market_*` continues to track the LATEST scrape (overwritten every
    # cron) -- the final pre-game value before DK pulls the market
    # becomes the "close" line.  CLV in % = closing implied prob -
    # opened implied prob, on the picked side.  Positive CLV =
    # market moved toward our side = we beat the close = leading
    # indicator of EV.
    "opened_nrfi_odds",
    "opened_yrfi_odds",
    "opened_captured_at",
    "clv_pct",
]

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _to_iso(date_str: str) -> str:
    """Normalize MM/DD/YYYY or YYYY-MM-DD to YYYY-MM-DD."""
    date_str = date_str.strip()
    if "/" in date_str:
        parts = date_str.split("/")
        if len(parts) == 3:
            m, d, y = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return date_str[:10]  # already ISO or truncate datetime


def _now_utc() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _hours_since_slate_date(iso_date: str) -> float:
    """
    Hours elapsed since midnight ET *after* the slate date.  e.g. for
    iso_date='2026-04-25', returns hours past 2026-04-26 00:00 ET.
    Used to decide whether a still-'Scheduled' game is a stale rainout.
    """
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        d = datetime.fromisoformat(iso_date)
        # Anchor: midnight ET the day after the slate; games that haven't
        # started by then were almost certainly cancelled.
        from datetime import timedelta
        end_of_slate = (d.replace(tzinfo=et) + timedelta(days=1))
        now_utc = datetime.now(tz=ZoneInfo("UTC"))
        return (now_utc - end_of_slate).total_seconds() / 3600.0
    except Exception:
        return 0.0

# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def _csv_path(season: int) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"picks_{season}.csv"


def _read_rows(path: Path) -> list[dict]:
    """Read picks_<season>.csv into a list of dicts.

    T3.12: Validate the header against FIELDS at read time.  Missing
    columns are silently back-filled (forwards-compat with newer schema
    being written to an older CSV), but EXTRA columns NOT in FIELDS are
    flagged via a one-time stderr warning -- those would be silently
    dropped by DictWriter(extrasaction='ignore') on the next write.
    """
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    # Schema drift detection (one-time warning per process invocation)
    field_set = set(FIELDS)
    extras = [h for h in header if h and h not in field_set]
    if extras:
        import sys as _sys
        print(
            f"  WARNING: {path.name} has {len(extras)} column(s) not in FIELDS: "
            f"{extras[:5]}{'...' if len(extras) > 5 else ''}.  "
            f"These will be DROPPED on the next write.  Add to FIELDS to preserve.",
            file=_sys.stderr,
        )
    missing = [f for f in FIELDS if f not in header]
    if missing and header:
        import sys as _sys
        print(
            f"  Schema-evolution: {path.name} missing {len(missing)} column(s) -- "
            f"will back-fill on next write.",
            file=_sys.stderr,
        )
    # Back-fill any new columns added after the file was first created
    for row in rows:
        for field in FIELDS:
            row.setdefault(field, "")
    return rows


def _mirror_picks_to_supabase(season: int, rows: list[dict]) -> None:
    """Phase 1.5 dual-write: mirror just-written pick rows to Supabase.
    Silent no-op when SUPABASE_URL / SUPABASE_SERVICE_KEY env vars are
    unset.  Wrapped here in tracker.py to swallow ALL errors -- the
    CSV is the source of truth and a Supabase outage must never break
    the predictor cron.  Pass only the rows that actually changed in
    the current call (not the full picks_<season>.csv) so we don't
    re-mirror 400+ unchanged rows on every refresh."""
    if not rows:
        return
    try:
        from db.supabase_writer import mirror_picks
        mirror_picks(rows, season)
    except Exception:
        # mirror_picks already logs internally; this catches any
        # import-time error (e.g. db package missing in a stripped-down
        # deploy).  Predictor must keep running.
        pass


def _mirror_pick_change_to_supabase(*, captured_at: str, iso_date: str,
                                     game_pk: str, away_team: str, home_team: str,
                                     game_time: str, old_label: str,
                                     new_label: str) -> None:
    """Phase 1.5 dual-write: mirror a pick-change journal entry to
    Supabase.  Same swallow-all contract as _mirror_picks_to_supabase."""
    try:
        from db.supabase_writer import mirror_pick_change
        mirror_pick_change(
            captured_at_utc = captured_at,
            iso_date        = iso_date,
            game_pk         = game_pk,
            away_team       = away_team,
            home_team       = home_team,
            game_time_et    = game_time,
            old_label       = old_label,
            new_label       = new_label,
        )
    except Exception:
        pass


def _write_rows(path: Path, rows: list[dict]) -> None:
    """Atomic CSV write -- write to a temp file in the same directory, fsync,
    then os.replace() to swap.  Eliminates the "torn write" race where two
    cron firings both open(path, "w") and a third reader (e.g. Vercel build's
    copy-data.mjs running in parallel) sees a partially-written file.
    os.replace is atomic on the same filesystem on both POSIX and Windows.
    """
    import os, tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync may fail on some Windows configurations; the
                # replace is still the atomicity guarantee.
                pass
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the temp file if anything went wrong
        # before the rename.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

# ---------------------------------------------------------------------------
# 1. Log picks
# ---------------------------------------------------------------------------

def _fmt(v, decimals: int = 3) -> str:
    """Format a numeric value for the CSV; empty string for None."""
    if v is None:
        return ""
    try:
        return str(round(float(v), decimals))
    except (TypeError, ValueError):
        return str(v)


def _pick_lock_minutes() -> int:
    """T2.58: how many minutes pre-game before we commit a STRONG pick
    (set bet_placed=Y, freeze the verdict, fire the BET LOCKED Telegram).
    Default 60 -- gives lineups + weather forecasts their final form
    while leaving a buffer for late scratches.  Configurable via env."""
    try:
        return max(0, int(os.environ.get("PICK_LOCK_AT_MIN_PREGAME", "60")))
    except (TypeError, ValueError):
        return 60


def _parse_game_time_et(game_time_et: str, iso_date: str):
    """Parse a row's game_time_et like '7:05 PM ET' + iso_date 'YYYY-MM-DD'
    into an ET-localized datetime.  Returns None when unparseable
    (e.g. 'TBD', 'After Game 1', empty)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    s = (game_time_et or "").strip()
    if not s or ":" not in s:
        return None
    cleaned = s.replace("ET", "").strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            t = datetime.strptime(cleaned, fmt)
            return datetime.fromisoformat(iso_date).replace(
                hour=t.hour, minute=t.minute, tzinfo=et,
            )
        except ValueError:
            continue
    return None


def _is_inside_lock_window(game_time_et: str, iso_date: str,
                           lock_min: int | None = None) -> bool:
    """T2.58: True if `now` is within `lock_min` minutes of game start
    (or after start, including post-game).  Used to gate the auto-bet
    flag in `_apply_odds_to_row` so STRONG picks don't commit hours
    in advance of game-time data being final.

    Returns True when the row should be eligible for bet_placed=Y.
    Returns False pre-lock (STRONG verdict stays advisory; the model
    can still flip with fresh lineup / weather / pitcher data).
    Returns True (defensively) when game_time_et is unparseable so
    we don't accidentally lock a row out forever -- caller's other
    guards will handle that case."""
    if lock_min is None:
        lock_min = _pick_lock_minutes()
    game_dt = _parse_game_time_et(game_time_et, iso_date)
    if game_dt is None:
        # Fall back to "always lockable" to avoid blocking forever.
        # Other guards (graded_result, etc.) handle stale rows.
        return True
    from zoneinfo import ZoneInfo
    from datetime import timedelta
    now_et = datetime.now(ZoneInfo("America/New_York"))
    lock_cutoff = game_dt - timedelta(minutes=lock_min)
    return now_et >= lock_cutoff


def _pick_lock_at_iso(game_time_et: str, iso_date: str,
                       lock_min: int | None = None) -> str:
    """T2.58: Return the lock cutoff time as an ISO-8601 UTC string,
    so the dashboard can render 'PENDING -- locks at HH:MM ET'.
    Returns empty string when game_time_et is unparseable."""
    if lock_min is None:
        lock_min = _pick_lock_minutes()
    game_dt = _parse_game_time_et(game_time_et, iso_date)
    if game_dt is None:
        return ""
    from datetime import timedelta, timezone
    lock_dt = (game_dt - timedelta(minutes=lock_min)).astimezone(timezone.utc)
    return lock_dt.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _pick_is_locked(existing: dict, iso_date: str) -> bool:
    """Decide whether the existing pick should be preserved across an
    intraday refresh.

    A pick is "locked" once:
      - The game has been graded (any result), OR
      - The game's start time has passed (live in-progress, no W/L yet), OR
      - The slate date is more than 24 h in the past (defensive: protects
        against ANY parse failure of game_time_et leaking a refresh
        through to a row that's clearly already played)

    Picks that haven't started yet are NOT locked, so the predictor can
    refresh them throughout the day as new info (lineups, weather, last-
    minute starter changes) becomes available.
    """
    graded = (existing.get("graded_result") or "").strip().upper()
    if graded in ("WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED"):
        return True

    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now_et = datetime.now(et)

    # Defensive lock #1: any slate date that's more than 24 h in the
    # past must be locked, even if game_time_et is missing/malformed.
    # Otherwise a refresh that lands tomorrow could silently rewrite
    # yesterday's locked-but-ungraded pick.
    try:
        slate_end_et = datetime.fromisoformat(iso_date).replace(
            hour=23, minute=59, tzinfo=et,
        )
        from datetime import timedelta
        if now_et > slate_end_et + timedelta(hours=24):
            return True
    except Exception:
        pass

    # Defensive lock #2: any row whose created_at timestamp is older
    # than 12 h is treated as locked.  Predictor runs idempotently and
    # writes a fresh created_at on every refresh of a still-mutable row,
    # so a stale created_at is a strong signal that the row has been
    # frozen (game in progress / final / API gave up trying to refresh).
    try:
        created_at = (existing.get("created_at") or "").strip()
        if created_at:
            ca_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            from datetime import timedelta
            if (datetime.now(ZoneInfo("UTC")) - ca_dt) > timedelta(hours=12):
                return True
    except Exception:
        pass

    # Compare game start time to "now" in ET
    time_et = (existing.get("game_time_et") or "").strip()
    if not time_et:
        return False
    # Non-numeric placeholders ("After Game 1", "TBD") cannot be locked
    # by start time -- the predictor's DH-Y placeholder resolution will
    # have emitted these only for games whose real time isn't known yet.
    # Defensive lock #1 (slate-date > 24h) catches the case where this
    # row outlives its slate.
    if ":" not in time_et:
        return False
    try:
        # game_time_et looks like "7:05 PM ET"
        cleaned = time_et.replace("ET", "").strip()
        # Try a couple of common formats
        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
            try:
                t = datetime.strptime(cleaned, fmt)
                game_dt = datetime.fromisoformat(iso_date).replace(
                    hour=t.hour, minute=t.minute, tzinfo=et,
                )
                # 5-minute buffer so a refresh that lands right at first
                # pitch still locks the pick (typical workflow: bet a few
                # minutes before)
                from datetime import timedelta
                return now_et >= (game_dt - timedelta(minutes=5))
            except ValueError:
                continue
    except Exception:
        pass
    return False


def log_picks(date_str: str, season: int, results: list[dict]) -> int:
    """
    Write every game in `results` to data/picks_{season}.csv.

    If a row with the same date+game_pk already exists it is updated:
      - Prediction fields (stats, lambda, probabilities) are always refreshed.
      - Grading fields (graded_result, fi_runs, etc.) are always preserved.
      - If the row is already graded, pick_side / pick_strength / pick_label
        are also preserved so the original bet decision is never overwritten.

    Returns the number of rows written/updated.
    """
    iso_date = _to_iso(date_str)
    path     = _csv_path(season)
    rows     = _read_rows(path)

    # Build index: (iso_date, str(game_pk)) -> list-index.
    # Detect duplicate keys -- if MLB ever returns the same game_pk for
    # both halves of a doubleheader (rare but observed historically),
    # we'd silently overwrite Game 1's row with Game 2's data.  Better
    # to fail loudly so the operator can investigate than to corrupt
    # a locked bet record.
    index: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        key = (row["date"], str(row["game_pk"]))
        if key in index:
            prev = rows[index[key]]
            print(
                f"  WARNING: duplicate (date, game_pk)={key} in picks CSV -- "
                f"row {index[key]} ({prev.get('pick_label','?')}) and "
                f"row {i} ({row.get('pick_label','?')}).  Keeping the later "
                f"row; first row's data will be lost.  Investigate MLB API "
                f"for game_pk={key[1]}."
            )
        index[key] = i

    now = _now_utc()
    written = 0
    # T2.30: track the just-built rows so we can mirror exactly the
    # changed set to Supabase after the CSV write -- avoids re-pushing
    # 400+ unchanged rows on every refresh.
    mirrored_rows: list[dict] = []

    for g in results:
        side   = g["pick_side"]
        conf   = g["pick_conf"]
        lam    = g["lambda_total"]
        ap     = g["away"]
        hp     = g["home"]

        if side == "PASS":
            # T2.53: when the predictor surfaces multiple PASS reasons
            # (e.g. lineup pending AND a debut pitcher), compose a
            # compound label like "PASS - Lineup pending + No data" so
            # the operator sees every applicable guard.  Single-reason
            # rows look identical to the pre-T2.53 output.
            #
            # `pass_reasons` is a list passed through from
            # mlb_first_inning_predictor.predict_slate; falls back to
            # the single-token `conf` if absent (older callers, or
            # rows where classify_pick_lr produced PASS directly
            # without going through the guards).
            _LABELS = {
                "NO DATA":         "No data",
                "STARTER PENDING": "Starter pending",
                "LINEUP PENDING":  "Lineup pending",
                "LOW LAMBDA":      "Low lambda",
                "NO EDGE":         "No edge",
            }
            reasons = g.get("pass_reasons") or []
            if not reasons:
                # Single-reason path -- use conf directly.
                if conf == "NO DATA":
                    label = "PASS - No data"
                elif conf == "STARTER PENDING":
                    label = "PASS - Starter pending"
                elif conf == "LINEUP PENDING":
                    label = "PASS - Lineup pending"
                elif conf == "LOW LAMBDA":
                    label = "PASS - Low lambda"
                else:
                    label = "PASS - No edge"
            else:
                parts = [_LABELS.get(r, r.title()) for r in reasons]
                label = "PASS - " + " + ".join(parts)
        else:
            label = f"{conf} {side}"

        new_row = {
            "date":           iso_date,
            "season":         season,
            "game_pk":        g["game_pk"],
            "game_number":    g["game_number"],
            "away_team":      ap["abbr"],
            "home_team":      hp["abbr"],
            "game_time_et":   g["time"],
            "away_pitcher":    ap["pitcher_name"],
            "home_pitcher":    hp["pitcher_name"],
            "away_pitcher_id": ap.get("pitcher_id") or "",
            "home_pitcher_id": hp.get("pitcher_id") or "",
            "away_pitcher_q":  ap["pitcher_q"],
            "home_pitcher_q":  hp["pitcher_q"],
            "away_batting_q": ap["batting_q"],
            "home_batting_q": hp["batting_q"],
            "park_factor":    _fmt(g["park_factor"], 3),
            "away_proj_runs": _fmt(ap["lambda"], 4),
            "home_proj_runs": _fmt(hp["lambda"], 4),
            "combined_lambda":_fmt(lam, 4),
            "lambda_lr_t1":   _fmt(g.get("lambda_lr_t1"),    4),
            "lambda_lr_b1":   _fmt(g.get("lambda_lr_b1"),    4),
            "lambda_lr_total":_fmt(g.get("lambda_lr_total"), 4),
            "nrfi_prob":      _fmt(g["nrfi_prob"], 4),
            "yrfi_prob":      _fmt(g["yrfi_prob"], 4),
            "over_1_5_prob":  _fmt(g["over_1_5_prob"], 4),
            "under_1_5_prob": _fmt(1.0 - g["over_1_5_prob"], 4),
            "pick_side":      side,
            "pick_strength":  conf,
            "pick_label":     label,
            "blended_inputs": g["data_points"],
            "created_at":     now,
            # pitcher model inputs
            "away_era":  _fmt(ap.get("era"),  3),
            "home_era":  _fmt(hp.get("era"),  3),
            "away_whip": _fmt(ap.get("whip"), 3),
            "home_whip": _fmt(hp.get("whip"), 3),
            "away_fip":  _fmt(ap.get("fip"),  3),
            "home_fip":  _fmt(hp.get("fip"),  3),
            "away_bb9":  _fmt(ap.get("bb9"),  2),
            "home_bb9":  _fmt(hp.get("bb9"),  2),
            "away_hr9":  _fmt(ap.get("hr9"),  2),
            "home_hr9":  _fmt(hp.get("hr9"),  2),
            "away_k9":   _fmt(ap.get("k9"),   2),
            "home_k9":   _fmt(hp.get("k9"),   2),
            # offense model inputs
            "away_obp": _fmt(ap.get("obp"), 3),
            "home_obp": _fmt(hp.get("obp"), 3),
            "away_slg": _fmt(ap.get("slg"), 3),
            "home_slg": _fmt(hp.get("slg"), 3),
            "away_rpg": _fmt(ap.get("rpg"), 3),
            "home_rpg": _fmt(hp.get("rpg"), 3),
            # weather (blank for archive misses; the model coerces to defaults)
            "wx_temp_c":   _fmt(g.get("wx_temp_c"),   2),
            "wx_wind_kmh": _fmt(g.get("wx_wind_kmh"), 2),
            "wx_humidity": _fmt(g.get("wx_humidity"), 1),
            "wx_is_dome":  _fmt(g.get("wx_is_dome"),  0),
            # Phase D + E.3 features
            "home_p_last5_pitcher_nrfi":  _fmt(g.get("home_p_last5_pitcher_nrfi"), 4),
            "away_p_last5_pitcher_nrfi":  _fmt(g.get("away_p_last5_pitcher_nrfi"), 4),
            # Phase F (last10) + power-signal (top3c SLG/ISO) -- added 2026-04-29
            "home_p_last10_pitcher_nrfi": _fmt(g.get("home_p_last10_pitcher_nrfi"), 4),
            "away_p_last10_pitcher_nrfi": _fmt(g.get("away_p_last10_pitcher_nrfi"), 4),
            "home_top3c_obp":             _fmt(g.get("home_top3c_obp"), 4),
            "away_top3c_obp":             _fmt(g.get("away_top3c_obp"), 4),
            "home_top3c_slg":             _fmt(g.get("home_top3c_slg"), 4),
            "away_top3c_slg":             _fmt(g.get("away_top3c_slg"), 4),
            "home_top3c_iso":             _fmt(g.get("home_top3c_iso"), 4),
            "away_top3c_iso":             _fmt(g.get("away_top3c_iso"), 4),
            # Source provenance for the top3c aggregate (lineup-aware vs
            # team-fallback).  Predictor sets this; CSV reader/dashboard
            # use it to flag PASS - LINEUP PENDING rows.
            "home_top3c_source":          g.get("home_top3c_source", "team_fallback"),
            "away_top3c_source":          g.get("away_top3c_source", "team_fallback"),
            # Per-batter top-3 lineup as JSON.  Default "[]" so the column
            # always parses cleanly (empty array means lineup not posted).
            "home_lineup_json":           g.get("home_lineup_json", "[]"),
            "away_lineup_json":           g.get("away_lineup_json", "[]"),
            # T4.15: top contributing LR features (JSON arrays).  Default
            # "[]" so empty / pre-LR-v4 rows parse cleanly.
            "top_factors_t1_json":        g.get("top_factors_t1_json", "[]"),
            "top_factors_b1_json":        g.get("top_factors_b1_json", "[]"),
            "home_plate_ump_id":          g.get("home_plate_ump_id", ""),
            "home_plate_ump_nrfi_rate":   _fmt(g.get("home_plate_ump_nrfi_rate"), 4),
            "home_xera":                  _fmt(g.get("home_xera"),                3),
            "away_xera":                  _fmt(g.get("away_xera"),                3),
            "home_whiff_pct_rank":        _fmt(g.get("home_whiff_pct_rank"),      0),
            "away_whiff_pct_rank":        _fmt(g.get("away_whiff_pct_rank"),      0),
            # Phase F (added 2026-04-29): pitcher-vs-team familiarity + opener detection
            "home_pvt_nrfi_rate":         _fmt(g.get("home_pvt_nrfi_rate"),       4),
            "away_pvt_nrfi_rate":         _fmt(g.get("away_pvt_nrfi_rate"),       4),
            "home_avg_ip_per_start":      _fmt(g.get("home_avg_ip_per_start"),    2),
            "away_avg_ip_per_start":      _fmt(g.get("away_avg_ip_per_start"),    2),
            # result fields start empty (preserved if already set)
            "actual_result": "", "graded_result": "",
            "fi_away_runs":  "", "fi_home_runs":  "", "fi_total_runs": "",
            "graded_at":     "",
            # odds & edge (filled by --import-odds; preserved on re-run)
            "market_nrfi_odds": "", "market_yrfi_odds": "",
            "sportsbook": "", "odds_captured_at": "",
            "implied_nrfi_prob": "", "implied_yrfi_prob": "",
            "edge_nrfi": "", "edge_yrfi": "", "edge_on_pick": "",
            "bet_placed": "", "units_risked": "", "profit_loss_units": "",
        }

        key = (iso_date, str(g["game_pk"]))
        if key in index:
            existing = rows[index[key]]

            # Always preserve grading and odds fields (these are populated
            # by --grade and --import-odds, NOT by the predictor itself).
            preserve = [
                "actual_result", "graded_result",
                "fi_away_runs", "fi_home_runs", "fi_total_runs", "graded_at",
                "market_nrfi_odds", "market_yrfi_odds",
                "sportsbook", "odds_captured_at",
                "implied_nrfi_prob", "implied_yrfi_prob",
                "edge_nrfi", "edge_yrfi", "edge_on_pick",
                "bet_placed", "units_risked", "profit_loss_units",
            ]

            # T2.25 -- Bet-time pick lock.  Once a bet has been placed
            # (bet_placed=Y), the pick_side / pick_strength / probabilities
            # / lambdas should freeze at the moment-of-bet snapshot.
            # Without this, a post-bet weather refresh, lineup tweak, or
            # any other input shift could flip pick_side from STRONG YRFI
            # to PASS, leaving the row with `bet_placed=Y` but
            # `pick_side=PASS` -- an incoherent state.  Confirmed via
            # 2026-05-01 ATL@COL: bet placed at STRONG YRFI -150 with
            # P(YRFI)=0.587, then a fresh weather fetch (wind 11.9 km/h
            # -> 5.6 km/h at Coors Field) shifted P(YRFI) to 0.551 and
            # demoted the pick to PASS-NO-EDGE -- but the user was
            # already in the bet at the original STRONG.
            if (existing.get("bet_placed") or "").strip().upper() == "Y":
                preserve += [
                    "pick_side", "pick_strength", "pick_label",
                    "nrfi_prob", "yrfi_prob",
                    "lambda_lr_t1", "lambda_lr_b1", "lambda_lr_total",
                    "combined_lambda",
                    "over_1_5_prob", "under_1_5_prob",
                    "blended_inputs",
                ]

            # Detect pick change BEFORE we apply the lock-preserve logic.
            # We only care about pre-lockout flips (game not yet started)
            # since locked rows preserve the pick by design.
            old_label = (existing.get("pick_label") or "").strip()
            new_label = new_row["pick_label"]
            if not _pick_is_locked(existing, iso_date) and old_label and old_label != new_label:
                _record_pick_change(
                    iso_date    = iso_date,
                    game_pk     = str(g["game_pk"]),
                    away_team   = ap["abbr"],
                    home_team   = hp["abbr"],
                    game_time   = g["time"],
                    old_label   = old_label,
                    new_label   = new_label,
                    captured_at = now,
                )
                # T2.22: also push the flip to Telegram (advisory; no-op
                # without TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars).
                # The notifier filters internally so PASS-variant churn
                # (LINEUP PENDING ↔ STARTER PENDING ↔ NO EDGE) doesn't
                # spam the user's phone -- only commits / demotes /
                # side-flips ping.
                #
                # T2.36: pass game_pk + probability context so the notifier
                # can dedupe across runners (Supabase query) and include
                # P(NRFI)/P(YRFI) in the message body.
                _notify_pick_flip_telegram(
                    iso_date    = iso_date,
                    away_team   = ap["abbr"],
                    home_team   = hp["abbr"],
                    game_time   = g["time"],
                    old_label   = old_label,
                    new_label   = new_label,
                    game_pk     = str(g.get("game_pk", "")),
                    row_context = {
                        "nrfi_prob": g.get("nrfi_prob"),
                        "yrfi_prob": g.get("yrfi_prob"),
                    },
                )

            # T2.38 #8: weather-flip alert on placed STRONG bets.
            # Compare existing wx_* values to the new ones.  Fire if
            # any of:  wind shifted >5 km/h, temp shifted >5°C, or
            # humidity shifted >20pp.  Function self-dedups via
            # notifications_log so we don't spam if cron tracks the
            # underlying drift across multiple cycles.
            #
            # T2.49 fix: also gate on `not _pick_is_locked(...)` so the
            # alert ONLY fires while the bet is still pre-game.  Without
            # this gate, the wx values keep drifting across the day after
            # the game ends, the 6-hour dedup window expires, and a
            # post-game refresh fires a "weather shift on STRONG YRFI
            # BAL@NYY" ping for a game that finished hours ago.
            # Confirmed via 2026-05-02 BAL@NYY (1:35 PM ET, graded LOSS):
            # weather alerts continued firing past 7 PM ET.
            try:
                if (existing.get("bet_placed") or "").upper() == "Y" and \
                   (existing.get("pick_strength") or "").upper() == "STRONG" and \
                   not _pick_is_locked(existing, iso_date):
                    def _f(x):
                        try: return float(x or 0)
                        except (ValueError, TypeError): return 0.0
                    old_wind  = _f(existing.get("wx_wind_kmh"))
                    new_wind  = _f(g.get("wx_wind_kmh"))
                    old_temp  = _f(existing.get("wx_temp_c"))
                    new_temp  = _f(g.get("wx_temp_c"))
                    old_humid = _f(existing.get("wx_humidity"))
                    new_humid = _f(g.get("wx_humidity"))
                    deltas = []
                    if abs(new_wind - old_wind) >= 5.0:
                        deltas.append(f"wind {old_wind:.1f} → {new_wind:.1f} km/h")
                    if abs(new_temp - old_temp) >= 5.0:
                        deltas.append(f"temp {old_temp:.1f} → {new_temp:.1f}°C")
                    if abs(new_humid - old_humid) >= 20.0:
                        deltas.append(f"humidity {old_humid:.0f}% → {new_humid:.0f}%")
                    if deltas:
                        # Use the existing (locked) row for the notifier
                        # since the pick_side / pick_strength / bet are
                        # frozen there; the new_row's wx_* is what we're
                        # alerting about.
                        merged_row = dict(existing)
                        merged_row.update({
                            "wx_wind_kmh": g.get("wx_wind_kmh"),
                            "wx_temp_c":   g.get("wx_temp_c"),
                            "wx_humidity": g.get("wx_humidity"),
                        })
                        _notify_strong_weather_telegram(
                            merged_row,
                            "Significant change since bet: " + " · ".join(deltas),
                        )
            except Exception:    # noqa: BLE001 — advisory only
                pass

            # If the game has already started (live or final), preserve
            # EVERYTHING the predictor would normally overwrite -- the
            # snapshot the user actually bet against has to stay frozen
            # for accurate post-mortem analysis.  Without this, an
            # intraday refresh that runs after first pitch would update
            # pitcher_q / weather / xera / etc. while keeping the locked
            # pick, producing an inconsistent display ("STARTER PENDING"
            # next to fully-known pitcher data, etc).  The grading and
            # odds-import flows handle their own fields outside this path.
            if _pick_is_locked(existing, iso_date):
                # Allowed to refresh post-lockout:
                #   - lineup JSON (purely informational; the dashboard uses
                #     this to show WHO the pitcher faced.  Doesn't affect
                #     the pick or any model inputs the user bet against.)
                #   - game_time_et (DH-Y game-2 placeholder cleanup: MLB's
                #     API initially reports game-2 at game-1 time + 5 min,
                #     then updates after game 1 ends.  We re-fetch the time
                #     so the dashboard isn't stuck showing a wrong time.)
                # PASS-only label refresh: when both the locked AND the new
                # pick are side="PASS" (no real bet placed), the user has
                # no money at stake, so refreshing the strength label is
                # purely informational -- e.g. "PASS - No edge" can flip
                # to "PASS - Low lambda" once the classifier learns to
                # distinguish the demotion reason.  Real bets (NRFI/YRFI)
                # still freeze hard since money is on the line.
                #
                # NOTE: `created_at` is intentionally NOT in this set.
                # _pick_is_locked() uses created_at >12h as defensive
                # lock #2 -- if a row was locked solely by that rule (e.g.
                # unparseable game_time_et, no terminal grade), refreshing
                # created_at would self-unlock it on the next predictor
                # run.  Add a separate `updated_at` field if a "last
                # touched" timestamp is needed.
                allow_update = {
                    "home_lineup_json", "away_lineup_json",
                    "game_time_et",
                }
                # PASS-label refresh extra guards (T2.14):
                #   - Both old AND new pick_side must be "PASS" (no real
                #     bet at risk -- this is the original guard).
                #   - The row must NOT have a real graded result yet
                #     (W/L/PASS).  Once grade lands, the strength label
                #     belongs to the historical record and we shouldn't
                #     keep flipping it for cosmetic reasons.
                #   - bet_placed must NOT be "Y".  Belt-and-suspenders:
                #     if a future code path ever lets bet_placed=Y on a
                #     PASS row (data corruption), don't mutate it.
                existing_grade = (existing.get("graded_result") or "").upper()
                existing_bet   = (existing.get("bet_placed") or "").upper()
                pass_label_refresh = (
                    (existing.get("pick_side") or "").upper() == "PASS"
                    and new_row.get("pick_side") == "PASS"
                    and existing_grade not in ("WIN", "LOSS", "PASS")
                    and existing_bet != "Y"
                )
                if pass_label_refresh:
                    allow_update |= {"pick_side", "pick_strength", "pick_label"}
                for fld in FIELDS:
                    if fld not in allow_update:
                        new_row[fld] = existing.get(fld, "")
            else:
                # Pre-game: full refresh except for the always-preserve list
                # (grading + odds, which the predictor doesn't generate).
                for fld in preserve:
                    new_row[fld] = existing.get(fld, "")

            rows[index[key]] = new_row
        else:
            rows.append(new_row)
            index[key] = len(rows) - 1

        written += 1
        mirrored_rows.append(new_row)

    _write_rows(path, rows)
    # T2.30: dual-write to Supabase (Phase 1.5).  Mirrors only the
    # rows touched by this call, never the full slate.  No-op when
    # Supabase env vars are unset.  Errors are caught + logged --
    # CSV is source of truth, predictor must not break on Supabase
    # outages.
    _mirror_picks_to_supabase(season, mirrored_rows)
    # T3.5: prune the pick-change log to the last 90 days on every
    # predictor run (cheap, idempotent, bounded growth).
    _prune_change_log(_change_log_path(), keep_days=90)
    return written


# ---------------------------------------------------------------------------
# Pick-change journal -- appended to whenever an intraday refresh flips a
# pre-game pick (e.g. STARTER PENDING -> STRONG YRFI as lineups post,
# or STRONG YRFI -> PASS as the lambda floor demotes a borderline call).
#
# Schema:
#   captured_at_utc, date, game_pk, away_team, home_team, game_time_et,
#   old_pick_label, new_pick_label
#
# Stored as a CSV next to picks_<season>.csv so it shows up in the
# repo, gets deployed alongside, and can be eyeballed easily.  Pruned
# implicitly by the dashboard (only "today" entries surface).
# ---------------------------------------------------------------------------

CHANGE_LOG_FIELDS = [
    "captured_at_utc", "date", "game_pk",
    "away_team", "home_team", "game_time_et",
    "old_pick_label", "new_pick_label",
]


def _change_log_path() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / "pick_changes.csv"


# ---------------------------------------------------------------------------
# Telegram pick-flip notifier (T2.22)
# ---------------------------------------------------------------------------
#
# Posts to a Telegram bot when a pick flips to/from an actionable state
# (STRONG / LEAN NRFI/YRFI).  Configured via two env vars, both required:
#
#   TELEGRAM_BOT_TOKEN  - the bot token from @BotFather
#   TELEGRAM_CHAT_ID    - the user's chat ID (from @userinfobot)
#
# When either is unset, this is a silent no-op so local dev / testing
# doesn't try to ping an unconfigured Telegram.  Failures (network, bad
# token, etc.) are caught and logged to stderr but never bubble up --
# notifications are advisory, they must not break the predictor cron.
#
# Filter:  notify only when at least one side of the flip is actionable.
# This keeps quiet on intraday PASS-variant churn (LINEUP PENDING ↔
# STARTER PENDING ↔ NO EDGE) which is just data-quality noise, but DOES
# notify on:
#   PASS / pending  →  STRONG / LEAN NRFI/YRFI   (commit, the user wants this)
#   STRONG / LEAN   →  PASS / pending             (rare demote, also wants)
#   STRONG NRFI     →  STRONG YRFI                (side flip, definitely wants)


def _is_actionable_label(label: str) -> bool:
    s = (label or "").upper()
    if "NRFI" not in s and "YRFI" not in s:
        return False
    return ("STRONG" in s) or ("LEAN" in s)


def _is_strong_label(label: str) -> bool:
    """T2.37: tighter filter for Telegram pings.  User explicitly wants
    STRONG-only notifications -- no LEAN, no PASS-variant churn, no
    demotes, no side-flips that don't end in STRONG.

    Returns True when `label` is a STRONG NRFI or STRONG YRFI pick.
    Used by the Telegram notifier to decide whether the NEW state is
    one the user cares about (STRONG to come in on, NOT LEAN or PASS)."""
    s = (label or "").upper()
    if "NRFI" not in s and "YRFI" not in s:
        return False
    return "STRONG" in s


# T2.36: Dashboard URL for the "→ View on dashboard" link in pings.
# Override via DASHBOARD_URL env var (e.g. for staging / preview deploys).
_DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://nrfi-terminal.vercel.app").rstrip("/")


# ---------------------------------------------------------------------------
# Generalized Telegram notifier (T2.38)
# ---------------------------------------------------------------------------
#
# Single send path for ALL notification types (flip / graded / voided /
# pregame / CLV / weather / milestone / digest / ops).  Centralizes:
#   - HTML body delivery via the bot API
#   - Cross-runner dedup via the notifications_log Supabase table
#   - Audit trail (every ping is recorded with full body)
#
# Each event has a stable type + key.  The (type, key) pair is unique
# within the dedup window — so e.g. two Railway runs racing each other
# both compute "STRONG bet ARI@CHC graded WIN" but only the first inserts
# the row + sends the ping; the second sees a prior row and skips.

# Dedup windows per event type.  A 0 means "fire every time" (no dedup).
# These are minutes; the helper filters notifications_log by
# captured_at_utc > now() - INTERVAL '<window>' minutes.
_DEDUP_WINDOW_M: dict[str, int] = {
    # T2.41: was 5 min; observed duplicate flip pings 6 min apart on
    # 2026-05-02 (Railway + GHA cron each independently detecting the
    # same PASS→STRONG YRFI flip from their separate CSV states, with
    # the 2nd run firing just outside the 5-min window).  24h ensures
    # a STRONG commit on a given (game, side) pings exactly once per
    # day — even if the pick demotes and re-commits hours later, the
    # bet is already locked at the first commit so the re-ping adds
    # no value.
    "flip_to_strong":       24 * 60,
    "strong_locked":        24 * 60,  # T2.58 -- bet lock alert, once per game per slate
    "strong_graded":        24 * 60,  # at most one grade ping per day per game
    "strong_voided":        24 * 60,  # one void ping per game
    "strong_pregame":       6 * 60,   # one pregame ping per game per ~6h window
    "strong_clv":           24 * 60,  # one CLV alert per bet per day
    "strong_weather":       6 * 60,   # one weather alert per ~6h window
    "strong_scratch":       6 * 60,   # one scratch alert per game per side
    "bankroll_milestone":   90 * 24 * 60,  # near-permanent (3 months) per milestone
    "daily_digest":         18 * 60,  # one digest per day
    # T4.14: bumped from 60 -> 120 min.  During a sustained predictor
    # outage (e.g. GH Actions queue starvation, Railway redeploy) the
    # 30-min staleness threshold + 60-min dedup combined to fire a stall
    # ping every hour for the duration.  120 min cuts that to once
    # every 2 hours -- still enough signal to know something's wrong,
    # half the noise volume.
    "ops_health":           120,
    # T4.14: feature_drift_monitor.py was using its own urllib send path
    # that bypassed dedup entirely.  Now routed through _notify_event_telegram
    # like all other event types.  Once-per-day window matches the
    # monitor's daily run cadence -- a re-run shouldn't re-ping if the
    # drift signal hasn't changed.
    "feature_drift":        24 * 60,
}


# T3.15: per-chat event-type routing.  Some chats only want a SUBSET of
# alert types -- e.g. the Backfist Bets supergroup is for sharing
# confirmed bets + outcomes, not internal pick-flip noise.  Other chats
# (the operator's personal DM) want everything.
#
# Format: chat_id (str) -> {"events": frozenset, "thread_id": str|None}
# - events:    set of event_types allowed; chats not listed default
#              to "all events" (backwards-compat)
# - thread_id: optional Forum Topic id (for Telegram supergroups with
#              Topics enabled).  Pass-through to sendMessage's
#              message_thread_id parameter so messages land in a
#              specific topic instead of General.
#
# 2026-05-04: per user request, Backfist Bets gets ONLY the two
# "user-actionable, fully confirmed" events:
#   - strong_locked:  fires at the 60-min lock window when lineup is
#                     posted, pitcher quality is real, weather is final.
#                     This IS the "bet confirmed with 100% accurate data"
#                     moment.
#   - strong_graded:  fires when the W/L result is in.
# Everything else (flip_to_strong, pregame, weather changes, scratches,
# CLV, daily digest, ops health, bankroll milestones) is suppressed for
# the supergroup -- those are internal-monitoring signals not relevant
# for sharing.
#
# T3.16 (later 2026-05-04): supergroup messages route to the
# "1st Inning Model" forum topic (thread_id=2), not the supergroup's
# General channel.  Discovered via getUpdates -- the operator had posted
# in topic_id=2 which is the only custom topic in the group.
_SUPERGROUP_CHAT_ID         = "-1003953933618"
_SUPERGROUP_ALLOWED_EVENTS  = frozenset({"strong_locked", "strong_graded"})
_SUPERGROUP_THREAD_ID       = "2"   # "1st Inning Model" topic in Backfist Bets

# T4.14: known-stale chat_ids that should receive nothing.
# Operators occasionally leave old test groups, decommissioned bots, or
# accidentally-pasted chat_ids in the TELEGRAM_CHAT_ID env var.  Listing
# them here (rather than relying on the env var being clean) makes the
# dedup explicit at the code level -- env-var changes can happen later
# without a redeploy, and re-adding a known-stale id won't re-spam.
#
# Map shape mirrors _TELEGRAM_EVENT_ROUTES so the same _chat_should_receive
# logic handles both: events=frozenset() means "no event types pass."
_DENIED_CHAT_IDS: frozenset[str] = frozenset({
    "-5115372935",   # legacy group, decommissioned 2026-Q1; kept here so
                     # an env-var refresh isn't strictly required.
})

# Per-chat event routes.  Add new entries here when adding new chat_ids.
_TELEGRAM_EVENT_ROUTES: dict[str, dict] = {
    _SUPERGROUP_CHAT_ID: {
        "events":    _SUPERGROUP_ALLOWED_EVENTS,
        "thread_id": _SUPERGROUP_THREAD_ID,
    },
}


def _chat_should_receive_event(chat_id: str, event_type: str | None) -> bool:
    """Return True if this chat_id should receive the given event_type.
    Chats without a route entry default to "receive everything"
    (backwards-compatible).

    T4.14: chat_ids in `_DENIED_CHAT_IDS` always return False, regardless
    of event_type (including the legacy `event_type=None` callers that
    used to bypass routing entirely).  This is the code-level kill for
    stale chat_ids that lingered in TELEGRAM_CHAT_ID after groups were
    archived / bots removed."""
    if chat_id in _DENIED_CHAT_IDS:
        return False
    if event_type is None:
        return True    # legacy callers that don't pass event_type get everything
    route = _TELEGRAM_EVENT_ROUTES.get(chat_id)
    if route is None:
        return True    # unrouted chat == everything
    allowed = route.get("events")
    if allowed is None:
        return True
    return event_type in allowed


def _chat_thread_id(chat_id: str) -> str | None:
    """Return the Forum Topic thread_id for this chat_id, or None if
    the chat doesn't use topic routing.  Pass-through to sendMessage's
    `message_thread_id` parameter."""
    route = _TELEGRAM_EVENT_ROUTES.get(chat_id)
    if route is None:
        return None
    return route.get("thread_id")


def _send_telegram_html(text: str, *, event_type: str | None = None) -> bool:
    """Low-level send.  Fans out to ALL chat_ids in `TELEGRAM_CHAT_ID`
    (comma-separated CSV).  Each chat_id can be:
      • a positive int  — DM to a person      (e.g. 5285688562)
      • a negative int  — group / channel     (e.g. -1001234567890)

    `event_type` (T3.15): when provided, chat_ids configured in
    _TELEGRAM_EVENT_ROUTES are filtered to only those that allow the
    given event type.  Chats without a route entry receive everything
    (backwards-compat).

    Returns True if at least one delivery succeeded.  Each recipient
    is independently attempted; one bad chat_id (e.g. bot kicked from
    a group) does NOT prevent delivery to the others.

    T2.43: previously a single chat_id was supported.  Bumped to CSV
    so the same notifications can broadcast to a group ('Backfist Bets')
    while still hitting the operator's personal chat.

    Fail-OPEN per the original notifier contract: a Telegram outage
    must NEVER break the predictor."""
    # T4.14: master kill switch.  Setting TELEGRAM_DISABLE_ALL=1 in any
    # writer's env (Railway dashboard, GH Actions secrets, local .env)
    # silences ALL telegram sends from that process -- no matter which
    # event type, which chat, which dedup state.  Designed for the
    # "bot is spamming, kill it now" emergency flow: flip the env var,
    # the next process tick goes silent, no redeploy needed.
    if os.environ.get("TELEGRAM_DISABLE_ALL", "").strip() == "1":
        return False

    token        = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
    if not token or not chat_ids_raw:
        return False

    # Tolerate whitespace around commas; ignore empty entries.
    # T3.13: also dedupe -- if the operator misconfigures the env var with
    # the same chat_id twice (e.g. "12345,12345" or "12345, 12345"), we
    # would otherwise broadcast the message to that chat twice, causing
    # the user to see duplicates.  Preserve order via dict.fromkeys
    # so the first occurrence wins.
    seen: set[str] = set()
    raw_ids: list[str] = []
    for c in chat_ids_raw.split(","):
        c = c.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        raw_ids.append(c)
    if not raw_ids:
        return False

    # T2.45 #4: validate each chat_id against Telegram's accepted shape
    # before we send.  Telegram chat_ids are signed 64-bit integers --
    # positive for DMs, negative (with optional `-100` supergroup prefix)
    # for groups/channels.  A malformed entry like "5285688562 garbage"
    # or stray quotes survives the strip() above but produces an opaque
    # 400 from sendMessage.  Filter early and emit a structured stderr
    # warning so a misconfigured env var doesn't quietly degrade the
    # broadcast.  The warning is one line per cycle so log volume stays
    # sane even if the env var stays bad.
    import re
    _CID_RE = re.compile(r"^-?\d+$")
    chat_ids:    list[str] = []
    bad_entries: list[str] = []
    for cid in raw_ids:
        if _CID_RE.match(cid):
            chat_ids.append(cid)
        else:
            bad_entries.append(cid)
    if bad_entries:
        print(
            f"  [telegram] WARNING: ignoring malformed chat_id(s) in "
            f"TELEGRAM_CHAT_ID env var: {bad_entries!r}.  "
            f"Each entry must match /^-?\\d+$/ (e.g. '5285688562' for a "
            f"DM, '-5115372935' for a group).  "
            f"Continuing with {len(chat_ids)} valid recipient(s).",
            file=sys.stderr,
        )
    if not chat_ids:
        return False

    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    delivered    = 0
    failures: list[tuple[str, str]] = []   # (chat_id, error_str)
    for chat_id in chat_ids:
        # T3.11-AUDIT: circuit breaker -- silently skip recipients with N+
        # consecutive failures this process.  See comment at
        # _TELEGRAM_TRIPPED_BREAKERS for trip semantics.
        if _telegram_chat_is_disabled(chat_id):
            continue
        # T3.15: per-chat event-type routing.  Skip recipients that
        # don't subscribe to this event_type (e.g. Backfist Bets only
        # gets strong_locked + strong_graded).
        if not _chat_should_receive_event(chat_id, event_type):
            continue
        # T3.16: per-chat Forum Topic routing.  Supergroups with topics
        # enabled need message_thread_id to land in a specific topic
        # instead of General.
        thread_id = _chat_thread_id(chat_id)
        try:
            payload: dict[str, str] = {
                "chat_id":                  chat_id,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_web_page_preview": "true",
            }
            if thread_id:
                payload["message_thread_id"] = thread_id
            body = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            delivered += 1
            _telegram_record_send_outcome(chat_id, success=True)
        except Exception as exc:    # noqa: BLE001 — per-recipient soft fail
            err = str(exc)
            print(f"  [telegram] send to {chat_id!r} failed: {exc!r}",
                  file=sys.stderr)
            failures.append((chat_id, err))
            _telegram_record_send_outcome(chat_id, success=False)

    # T2.57: per-recipient failures hit system_errors so the Ops Health card
    # surfaces them within minutes.  notifications_log only tracks "at
    # least one delivery succeeded" -- pre-T2.57 a bot kicked from a group
    # was invisible to ops because the personal chat kept succeeding.
    # Confirmed via 2026-05-03 Backfist Bets: bot lost send-messages
    # permission silently; user only noticed hours later.
    #
    # Rate-limited to once per (chat_id, error-class) per hour via a
    # simple in-memory dedup so a persistent failure doesn't spam
    # system_errors with hundreds of rows per day.
    for cid, err_str in failures:
        _log_telegram_failure(cid, err_str)

    return delivered > 0


# T2.57: in-memory dedup so we don't spam system_errors with one row per
# Telegram failure per cycle.  Keys: (chat_id, error_class).  Value: ts of
# last write.  Cleared on process restart, which is fine -- on a clean
# restart we WANT a fresh failure record.
_TELEGRAM_FAILURE_LOG_TS: dict[tuple[str, str], float] = {}
_TELEGRAM_FAILURE_LOG_INTERVAL_S = 3600    # 1 row per (chat, error-class) per hr

# T3.11-AUDIT 2026-05-03: per-chat circuit breaker.  Once a chat_id has
# failed N consecutive times within the same Railway process lifetime,
# stop attempting AND stop writing system_errors rows.  Real-world
# trigger: chat_id=-5115372935 produced 7 HTTP-400 errors in one
# afternoon (bot kicked or lost messaging permission); the in-memory
# 1-hr dedup let one row through per hour and the predictor kept
# attempting every cycle.  Trip threshold = 3, which catches a
# permanently-bad chat_id within ~30 min while still letting transient
# Telegram outages (which fail then succeed) heal naturally.
_TELEGRAM_CONSECUTIVE_FAILS:    dict[str, int]  = {}
_TELEGRAM_TRIPPED_BREAKERS:     set[str]        = set()
_TELEGRAM_BREAKER_TRIP_AT      = 3


def _telegram_chat_is_disabled(chat_id: str) -> bool:
    """Returns True if the per-chat circuit breaker has tripped this
    process.  _send_telegram_html consults this before each attempt
    so we silently skip known-dead recipients."""
    return chat_id in _TELEGRAM_TRIPPED_BREAKERS


def _telegram_record_send_outcome(chat_id: str, success: bool) -> None:
    """Track consecutive failures per chat.  Trips the breaker after
    `_TELEGRAM_BREAKER_TRIP_AT` consecutive failures.  Resets the
    counter on any success."""
    if success:
        _TELEGRAM_CONSECUTIVE_FAILS.pop(chat_id, None)
        # If a previously-tripped breaker recovers (rare; usually requires
        # process restart), un-trip so future attempts resume.
        _TELEGRAM_TRIPPED_BREAKERS.discard(chat_id)
        return
    n = _TELEGRAM_CONSECUTIVE_FAILS.get(chat_id, 0) + 1
    _TELEGRAM_CONSECUTIVE_FAILS[chat_id] = n
    if n >= _TELEGRAM_BREAKER_TRIP_AT and chat_id not in _TELEGRAM_TRIPPED_BREAKERS:
        _TELEGRAM_TRIPPED_BREAKERS.add(chat_id)
        print(
            f"  [telegram] CIRCUIT BREAKER tripped for chat_id={chat_id!r} "
            f"({n} consecutive failures).  Suppressing further send attempts "
            f"AND system_errors writes for this chat until process restart.  "
            f"Likely cause: bot kicked from group, lost messaging permission, "
            f"or chat_id is wrong.  Check group membership / bot permissions.",
            file=sys.stderr,
        )


def _log_telegram_failure(chat_id: str, err_str: str) -> None:
    """Write a row to system_errors when a Telegram send fails for a
    specific recipient.  Rate-limited per (chat_id, error-class) AND
    suppressed entirely once the per-chat circuit breaker is tripped.
    Fail-silent: if the system_errors write itself errors, we already
    logged to stderr above."""
    # Once the breaker has tripped, do not log -- the FIRST trip-line
    # printed by _telegram_record_send_outcome is enough signal.
    if _telegram_chat_is_disabled(chat_id):
        return
    import time as _time
    # Error class = first 80 chars; covers Telegram's distinctive error
    # messages ("not enough rights", "chat not found", "bot was kicked
    # from", etc.) without storing per-attempt unique strings.
    err_class = (err_str or "")[:80]
    key = (chat_id, err_class)
    now_ts = _time.time()
    last = _TELEGRAM_FAILURE_LOG_TS.get(key, 0)
    if now_ts - last < _TELEGRAM_FAILURE_LOG_INTERVAL_S:
        return
    _TELEGRAM_FAILURE_LOG_TS[key] = now_ts
    try:
        from db.supabase_writer import mirror_system_error
        mirror_system_error(
            captured_at_utc = _now_utc(),
            iso_date        = (datetime.utcnow().date().isoformat()),
            step            = "telegram-send",
            exit_code       = 1,
            message         = (
                f"chat_id={chat_id} delivery failed: {err_str[:600]}"
            )[:1500],
        )
    except Exception as exc:    # noqa: BLE001 — fail-silent per contract
        print(f"  [telegram] failed to record failure to system_errors: "
              f"{exc!r}", file=sys.stderr)


def _notify_event_dedup_check(event_type: str, event_key: str) -> bool:
    """Returns True if this (event_type, event_key) was ALREADY pinged
    inside its dedup window.  Caller should skip if True.
    Fail-OPEN: if the dedup query errors, return False so we still ping
    rather than silently dropping a real signal."""
    window_m = _DEDUP_WINDOW_M.get(event_type, 5)
    if window_m <= 0:
        return False    # zero window = fire every time
    try:
        from db.supabase_writer import _get_client
        from datetime import timedelta as _td
        client = _get_client()
        if client is None:
            return False    # Supabase not configured; can't dedup, so ping
        cutoff = (datetime.utcnow() - _td(minutes=window_m)).isoformat() + "Z"
        res = (
            client.table("notifications_log")
                  .select("id", count="exact", head=True)
                  .eq("event_type", event_type)
                  .eq("event_key",  event_key)
                  .gte("captured_at_utc", cutoff)
                  .execute()
        )
        return (getattr(res, "count", 0) or 0) > 0
    except Exception:    # noqa: BLE001 — fail-open
        return False


def _notify_event_record(event_type: str, event_key: str, body: str, delivered: bool) -> None:
    """Append one row to notifications_log for audit + future dedup.
    Fail-silent: a missing log row is not a real failure (the user already
    got the ping)."""
    try:
        from db.supabase_writer import _get_client
        client = _get_client()
        if client is None:
            return
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
        client.table("notifications_log").insert({
            "captured_at_utc": _now_utc(),
            "event_type":      event_type,
            "event_key":       event_key,
            "chat_id":         chat_id,
            "body":            (body or "")[:2000],   # cap; Telegram caps too
            "delivered":       bool(delivered),
        }).execute()
    except Exception:    # noqa: BLE001
        pass


def _notify_event_telegram(event_type: str, event_key: str, body: str) -> bool:
    """Top-level entry point.  Dedup → send → log.  Returns True if a
    ping was actually delivered (False on env-var miss / dedup hit /
    network error)."""
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
    if not token or not chat_id:
        return False
    if _notify_event_dedup_check(event_type, event_key):
        return False   # silent skip — another runner already pinged
    ok = _send_telegram_html(body, event_type=event_type)
    _notify_event_record(event_type, event_key, body, ok)
    return ok


def _dashboard_link(date_iso: str = "") -> str:
    """Build the trailing 'View on dashboard →' anchor used in nearly
    every notification body."""
    qs = f"?date={date_iso}" if date_iso else ""
    return f'<a href="{_DASHBOARD_URL}/{qs}">View on dashboard →</a>'


def _flip_already_pinged_supabase(iso_date: str, game_pk: str, new_label: str) -> bool:
    """T2.36 cross-runner dedup.

    Both Railway (every 5 min) and the GHA cron (hourly) detect the
    same flip and each writes a pick_changes journal row plus a
    Telegram ping.  Without dedup, every actionable flip fires 2-4
    pings per cycle.

    Strategy: query Supabase for ALL pick_changes rows matching
    (date, game_pk, new_pick_label) within the last 5 minutes.  By
    the time this function is called, _record_pick_change has already
    inserted THIS runner's row -- so:
      • count == 1  → only us in the window, ping
      • count >= 2  → another runner already pinged, skip

    Race window: ~1 sec between two runners both seeing count==1
    simultaneously.  Acceptable; the alternative (locks / unique
    constraints) is more complex than a duplicate ping every few
    weeks justifies.

    Returns True when the ping should be SKIPPED (duplicate detected).
    Always returns False on any error so the notifier fails-OPEN
    rather than silently swallowing real flips."""
    if not game_pk:
        return False  # legacy rows without game_pk -- skip dedup, ping anyway
    try:
        from db.supabase_writer import _get_client
        from datetime import timedelta as _td
        client = _get_client()
        if client is None:
            return False    # Supabase not configured -- can't dedup, ping anyway
        cutoff = (datetime.utcnow() - _td(minutes=5)).isoformat() + "Z"
        res = (
            client.table("pick_changes")
                  .select("id", count="exact", head=True)
                  .eq("date",          iso_date)
                  .eq("game_pk",       str(game_pk))
                  .eq("new_pick_label", new_label)
                  .gte("captured_at_utc", cutoff)
                  .execute()
        )
        count = getattr(res, "count", None) or 0
        return count > 1
    except Exception:    # noqa: BLE001 -- fail-open, never swallow a real flip
        return False


def _flip_category(old_label: str, new_label: str) -> tuple[str, str]:
    """Classify a flip into a category + emoji for the Telegram ping.

    Returns (category, emoji).  Categories:
      'commit'  — PASS/pending → STRONG/LEAN  (the user wants in)
      'demote'  — STRONG/LEAN → PASS/pending  (the user is out)
      'side'    — STRONG NRFI ↔ STRONG YRFI   (rare; high-impact)
    """
    old_a = _is_actionable_label(old_label)
    new_a = _is_actionable_label(new_label)
    if old_a and new_a:
        # Both actionable.  If sides differ → side flip; else strength change.
        old_side = "NRFI" if "NRFI" in (old_label or "").upper() else "YRFI"
        new_side = "NRFI" if "NRFI" in (new_label or "").upper() else "YRFI"
        if old_side != new_side:
            return ("side", "🔄")
        # Same side, strength changed (LEAN → STRONG = commit; STRONG → LEAN = trim)
        if "STRONG" in (new_label or "").upper() and "LEAN" in (old_label or "").upper():
            return ("commit", "📈")
        if "LEAN" in (new_label or "").upper() and "STRONG" in (old_label or "").upper():
            return ("demote", "📉")
        return ("commit", "📈")
    if not old_a and new_a:
        # PASS / pending → actionable.  Pick icon based on side + strength.
        nu = (new_label or "").upper()
        if "STRONG" in nu and "NRFI" in nu: return ("commit", "🟢")
        if "STRONG" in nu and "YRFI" in nu: return ("commit", "🔴")
        if "LEAN"   in nu and "NRFI" in nu: return ("commit", "🟢")
        if "LEAN"   in nu and "YRFI" in nu: return ("commit", "🔴")
        return ("commit", "✨")
    if old_a and not new_a:
        return ("demote", "⬇️")
    return ("commit", "•")    # both non-actionable -- shouldn't reach here


def _format_flip_message(*, iso_date: str, away_team: str, home_team: str,
                          game_time: str, old_label: str, new_label: str,
                          row_context: dict | None = None) -> str:
    """Build the Telegram message body.  HTML-formatted (Telegram parse_mode=HTML)
    so we can hyperlink the dashboard URL.  Falls back to plain text if
    parse_mode fails server-side."""
    category, emoji = _flip_category(old_label, new_label)

    # Headline: emoji + category + new pick label
    if category == "commit":
        headline = f"{emoji} <b>{(new_label or '—').upper()}</b> committed"
    elif category == "demote":
        headline = f"{emoji} <b>{(old_label or '—').upper()}</b> demoted"
    elif category == "side":
        headline = f"{emoji} <b>SIDE FLIP</b>: {(old_label or '—')} → {(new_label or '—')}"
    else:
        headline = f"{emoji} {(old_label or '—')} → {(new_label or '—')}"

    # Subline: matchup + game time + slate date
    matchup_line = f"{away_team} @ {home_team} · {game_time or 'TBD'} · {iso_date}"

    # Optional probability context line
    extra_lines: list[str] = []
    if row_context:
        nrfi_p = row_context.get("nrfi_prob")
        yrfi_p = row_context.get("yrfi_prob")
        try:
            if nrfi_p is not None and yrfi_p is not None:
                extra_lines.append(
                    f"P(NRFI) {float(nrfi_p) * 100:.1f}% · "
                    f"P(YRFI) {float(yrfi_p) * 100:.1f}%"
                )
        except (ValueError, TypeError):
            pass

    # Footer: clickable dashboard link.  Telegram HTML supports <a href>.
    dashboard_link = (
        f'<a href="{_DASHBOARD_URL}/?date={iso_date}">View on dashboard →</a>'
    )

    parts = [headline, matchup_line]
    parts += extra_lines
    parts += [f"\n{old_label or '—'} → {new_label or '—'}", dashboard_link]
    return "\n".join(parts)


def _notify_pick_flip_telegram(*, iso_date: str, away_team: str, home_team: str,
                                game_time: str, old_label: str, new_label: str,
                                game_pk: str = "",
                                row_context: dict | None = None) -> None:
    """STRONG-only flip notifier (T2.37) using the unified
    notifications_log dedup path (T2.38).  Backward-compatible signature.

    T2.58: pre-lock STRONG flips are now SUPPRESSED.  Under the lock-
    window model, a STRONG verdict at 9 AM is just an advisory
    projection -- the pick is still PENDING and can still flip with
    fresh lineup/weather data.  Firing a Telegram alert at that point
    would tell the user "you have a STRONG bet" when actually nothing
    is bet yet.  The canonical 'BET LOCKED' alert fires from
    `_apply_odds_to_row` when bet_placed transitions to Y, which
    happens inside the lock window with fully-final data.

    Pre-lock flips still appear on the dashboard (the model's view of
    where the pick is heading), they just don't ping Telegram.
    """
    if not _is_strong_label(new_label):
        return  # T2.37 STRONG-only filter
    # T2.58: suppress when outside lock window
    if not _is_inside_lock_window(game_time, iso_date):
        # Quiet pre-lock STRONG transitions on the dashboard side only.
        # The canonical lock-time alert comes from _apply_odds_to_row.
        return
    body = _format_flip_message(
        iso_date    = iso_date,
        away_team   = away_team,
        home_team   = home_team,
        game_time   = game_time,
        old_label   = old_label,
        new_label   = new_label,
        row_context = row_context,
    )
    pk = game_pk or f"{away_team}@{home_team}"
    event_key = f"flip_to_strong:{pk}:{(new_label or '').upper()}"
    _notify_event_telegram("flip_to_strong", event_key, body)


def _notify_strong_locked_telegram(row: dict) -> None:
    """T2.58: 'BET LOCKED' alert -- fires once per game per slate at the
    moment bet_placed transitions to Y (inside the lock window with
    final data).  This replaces the pre-lock flip-to-strong alert as
    the user's canonical 'place this bet now' signal.

    Includes price + would-be units + edge + dashboard link so the
    operator can fire the bet on DK and confirm the matchup at a
    glance.  Dedup is per-game per-day via notifications_log, so a
    re-run of import_odds after lock doesn't re-ping."""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    side       = (row.get("pick_side") or "").upper()
    away       = (row.get("away_team") or "").upper()
    home       = (row.get("home_team") or "").upper()
    game_time  = (row.get("game_time_et") or "").strip()
    iso_date   = (row.get("date") or "").strip()
    game_pk    = (row.get("game_pk") or "").strip()

    odds_col   = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    price      = (row.get(odds_col) or "—").strip()
    units      = (row.get("units_risked") or "1.0").strip()

    edge_raw   = row.get("edge_on_pick")
    edge_str   = ""
    try:
        edge_pct = float(edge_raw) * 100.0 if edge_raw not in (None, "") else None
        if edge_pct is not None:
            edge_str = f" · edge {'+' if edge_pct >= 0 else ''}{edge_pct:.1f}%"
    except (TypeError, ValueError):
        pass

    body = "\n".join([
        f"🔒 <b>BET LOCKED · STRONG {side}</b>",
        f"{away} @ {home} · {game_time}",
        f"DK {price} · {units}u{edge_str}",
        "(Pick is committed -- model verdict frozen until grade.)",
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_locked:{game_pk or (away + '@' + home)}"
    _notify_event_telegram("strong_locked", event_key, body)


# ---------------------------------------------------------------------------
# T2.38 — Additional STRONG-only Telegram event notifiers
# ---------------------------------------------------------------------------
#
# All of these go through `_notify_event_telegram` which centralizes dedup
# (via the notifications_log Supabase table) + send + audit logging.
# Each function is a thin wrapper that builds the HTML body and picks the
# event_type + event_key.


def _notify_strong_graded_telegram(row: dict, today_record: tuple[int, int, int],
                                    today_pl_units: float) -> None:
    """STRONG bet graded WIN or LOSS.  Fires once per game.  Includes
    today-so-far record + P&L for context.  No-op for PASS-variant
    grades and for LEAN bets (those don't ping)."""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    grade = (row.get("graded_result") or "").strip().upper()
    if grade not in ("WIN", "LOSS"):
        return

    side       = (row.get("pick_side") or "").upper()
    away       = (row.get("away_team") or "").upper()
    home       = (row.get("home_team") or "").upper()
    fi_a       = row.get("fi_away_runs")
    fi_h       = row.get("fi_home_runs")
    odds_col   = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    price      = (row.get(odds_col) or "—").strip()
    units      = (row.get("units_risked") or "1.0").strip()
    pl         = (row.get("profit_loss_units") or "").strip()
    iso_date   = (row.get("date") or "").strip()
    game_pk    = (row.get("game_pk") or "").strip()

    icon       = "✅" if grade == "WIN" else "❌"
    score_line = (
        f"1st inning: {fi_a}-{fi_h} ({(int(fi_a) + int(fi_h))} runs)"
        if fi_a not in (None, "") and fi_h not in (None, "") else ""
    )
    pl_line = f"+{pl}u" if grade == "WIN" else f"{pl}u"

    w, l, p = today_record
    today_line = (
        f"Today: {w}-{l}"
        + (f"-{p}P" if p else "")
        + f" · {today_pl_units:+.2f}u"
    )

    body = "\n".join([
        f"{icon} <b>STRONG {side}</b> · {away} @ {home} · {grade}",
        f"DK {price} · {units}u risked → <b>{pl_line}</b>",
        score_line,
        today_line,
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_graded:{game_pk or (away + '@' + home)}"
    _notify_event_telegram("strong_graded", event_key, body)


def _notify_strong_voided_telegram(row: dict, reason: str) -> None:
    """STRONG bet's game POSTPONED / SUSPENDED / CANCELLED before the 1st
    inning completed.  Bet is voided; stake returned at DK.  Fires once
    per game."""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    side     = (row.get("pick_side") or "").upper()
    away     = (row.get("away_team") or "").upper()
    home     = (row.get("home_team") or "").upper()
    units    = (row.get("units_risked") or "1.0").strip()
    iso_date = (row.get("date") or "").strip()
    game_pk  = (row.get("game_pk") or "").strip()

    body = "\n".join([
        f"⚠️ <b>STRONG {side}</b> bet voided",
        f"{away} @ {home} · {reason.upper()}",
        f"{units}u returned · no grade recorded",
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_voided:{game_pk or (away + '@' + home)}"
    _notify_event_telegram("strong_voided", event_key, body)


def _notify_strong_pregame_telegram(row: dict, minutes_to_first_pitch: int) -> None:
    """30-min-before-first-pitch reminder for STRONG bets.  Fires exactly
    once per game per ~6h dedup window (covers a single pre-game
    countdown — re-deploys shouldn't re-fire).  Caller is responsible
    for filtering to bets in the right time window before invoking."""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    side       = (row.get("pick_side") or "").upper()
    away       = (row.get("away_team") or "").upper()
    home       = (row.get("home_team") or "").upper()
    game_time  = (row.get("game_time_et") or "TBD").strip()
    odds_col   = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    price      = (row.get(odds_col) or "—").strip()
    edge_str   = (row.get("edge_on_pick") or "").strip()
    edge_pct   = ""
    try:
        if edge_str:
            edge_pct = f"+{float(edge_str)*100:.1f}%" if float(edge_str) >= 0 else f"{float(edge_str)*100:.1f}%"
    except ValueError:
        pass
    units      = (row.get("units_risked") or "1.0").strip()
    iso_date   = (row.get("date") or "").strip()
    game_pk    = (row.get("game_pk") or "").strip()

    body = "\n".join([
        f"⏰ <b>{minutes_to_first_pitch} min</b> to first pitch",
        f"<b>STRONG {side}</b> · {away} @ {home} · {game_time}",
        f"DK {price}" + (f" · edge {edge_pct}" if edge_pct else "") + f" · {units}u",
        "Last call to lock in the bet.",
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_pregame:{game_pk or (away + '@' + home)}"
    _notify_event_telegram("strong_pregame", event_key, body)


def _notify_strong_clv_telegram(row: dict, opened_implied: float, closing_implied: float) -> None:
    """Significant CLV move (>=5pp implied prob shift toward our pick)
    on a placed STRONG bet.  Fires once per bet per day.  Positive CLV
    means we beat the close → leading indicator of long-run +EV."""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    side     = (row.get("pick_side") or "").upper()
    away     = (row.get("away_team") or "").upper()
    home     = (row.get("home_team") or "").upper()
    iso_date = (row.get("date") or "").strip()
    game_pk  = (row.get("game_pk") or "").strip()

    delta_pp = (closing_implied - opened_implied) * 100
    direction = "📈 toward us" if delta_pp >= 0 else "📉 away from us"

    body = "\n".join([
        f"💸 Sharp move on <b>STRONG {side}</b>",
        f"{away} @ {home}",
        f"Opened: {opened_implied*100:.1f}% · Now: {closing_implied*100:.1f}% ({delta_pp:+.1f}pp {direction})",
        ("You beat the close 👍" if delta_pp >= 5 else "Market drifted; you locked at the better price."),
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_clv:{game_pk or (away + '@' + home)}"
    _notify_event_telegram("strong_clv", event_key, body)


def _notify_strong_weather_telegram(row: dict, change_summary: str) -> None:
    """Weather conditions changed materially after a STRONG bet was placed.
    Informational — bet is locked at the original prediction, but the
    underlying environment shifted.  Fires once per game per ~6h window."""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    # T2.49: defense in depth -- never alert on a game that's already
    # been graded (terminal) or postponed.  The call site in log_picks
    # already gates on _pick_is_locked, but a future caller could forget
    # this guard, so block it here too.  Cheap check; saves the user
    # from "weather shift on a game that ended 5 hours ago" pings.
    grade = (row.get("graded_result") or "").strip().upper()
    if grade in ("WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED"):
        return
    side     = (row.get("pick_side") or "").upper()
    away     = (row.get("away_team") or "").upper()
    home     = (row.get("home_team") or "").upper()
    iso_date = (row.get("date") or "").strip()
    game_pk  = (row.get("game_pk") or "").strip()

    body = "\n".join([
        f"🌬 Weather shift on <b>STRONG {side}</b>",
        f"{away} @ {home}",
        change_summary,
        "(Bet locked; informational only — pick stays at the bet-time prediction.)",
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_weather:{game_pk or (away + '@' + home)}"
    _notify_event_telegram("strong_weather", event_key, body)


def _notify_strong_scratch_telegram(row: dict,
                                     scratched_side: str,
                                     original_name: str,
                                     replacement_name: str) -> None:
    """T2.40: A starter scratched on a placed STRONG bet.  The bet's
    pick_state stays locked at the original prediction (per T2.25
    bet-time pick lock), but the matchup the user actually has money
    on now has a different pitcher.  Alert the user so they know
    their STRONG bet's underlying inputs changed.

    Args:
        row: tracker CSV row (existing locked state)
        scratched_side: "away" or "home" -- which pitcher was scratched
        original_name: the pitcher name we predicted with
        replacement_name: the pitcher actually starting now"""
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        return
    side       = (row.get("pick_side") or "").upper()
    away       = (row.get("away_team") or "").upper()
    home       = (row.get("home_team") or "").upper()
    game_time  = (row.get("game_time_et") or "TBD").strip()
    iso_date   = (row.get("date") or "").strip()
    game_pk    = (row.get("game_pk") or "").strip()
    side_label = scratched_side.upper()    # "AWAY" or "HOME"

    body = "\n".join([
        f"⚠️ Starter scratched · <b>STRONG {side}</b>",
        f"{away} @ {home} · {game_time}",
        f"{side_label} starter: {original_name or '?'} → <b>{replacement_name or '?'}</b>",
        "Bet stays locked at the original prediction (T2.25); next predictor",
        "cycle will recompute with the new starter.",
        "",
        _dashboard_link(iso_date),
    ])

    event_key = f"strong_scratch:{game_pk or (away + '@' + home)}:{scratched_side}"
    _notify_event_telegram("strong_scratch", event_key, body)


def _notify_bankroll_milestone_telegram(milestone_units: int,
                                         season_record: tuple[int, int, int],
                                         season_pl_units: float,
                                         hit_rate_pct: float) -> None:
    """Season P&L crossed a unit milestone (±10u, ±25u, ±50u, ±100u).
    Pseudo-permanent dedup (90-day window) so a milestone only fires
    once even across long stretches."""
    w, l, _p = season_record
    icon = "🏆" if milestone_units >= 0 else "🥶"
    sign = "+" if milestone_units >= 0 else ""
    body = "\n".join([
        f"{icon} Bankroll milestone: <b>{sign}{milestone_units}u</b>",
        f"Season: {w}-{l} · {hit_rate_pct:.1f}% hit rate",
        f"Net P&L: {season_pl_units:+.2f}u (real-odds + -110 fallback)",
        "",
        _dashboard_link(),
    ])
    event_key = f"bankroll_milestone:{milestone_units:+d}u"
    _notify_event_telegram("bankroll_milestone", event_key, body)


def _notify_daily_digest_telegram(iso_date: str,
                                   today_record: tuple[int, int, int],
                                   today_pl_units: float,
                                   season_record: tuple[int, int, int],
                                   season_pl_units: float,
                                   tomorrow_games: int) -> None:
    """Once-per-day end-of-slate wrap.  Fires after the last game of
    `iso_date` is graded (or via a daily cron at ~1am ET)."""
    w, l, p = today_record
    sw, sl, _sp = season_record
    body = "\n".join([
        f"🌙 <b>{iso_date} wrap</b>",
        f"Today: {w}-{l}" + (f"-{p}P" if p else "") + f" · <b>{today_pl_units:+.2f}u</b>",
        f"Season: {sw}-{sl} · {(sw / max(sw + sl, 1)) * 100:.1f}% · {season_pl_units:+.2f}u",
        f"Tomorrow: {tomorrow_games} games on the slate.",
        "",
        _dashboard_link(iso_date),
    ])
    event_key = f"daily_digest:{iso_date}"
    _notify_event_telegram("daily_digest", event_key, body)


def _aggregate_today_record(rows: list[dict], iso_date: str) -> tuple[tuple[int, int, int], float]:
    """Sum (W, L, PASS) and P&L (units) for one slate date.  Used to
    enrich graded-pick notifications with running-day context.
    POSTPONED / SUSPENDED don't count.  Returns ((W, L, P), pl_units)."""
    w = l = p = 0
    pl = 0.0
    for r in rows:
        if (r.get("date") or "").strip() != iso_date:
            continue
        g = (r.get("graded_result") or "").strip().upper()
        if g == "WIN":  w += 1
        elif g == "LOSS": l += 1
        elif g == "PASS": p += 1
        try:
            v = float((r.get("profit_loss_units") or "0") or 0)
            pl += v
        except (ValueError, TypeError):
            pass
    return (w, l, p), pl


def _aggregate_season_record(rows: list[dict]) -> tuple[tuple[int, int, int], float, float]:
    """Sum across the whole CSV: ((W, L, P), pl_units, hit_rate_pct).
    Used by the bankroll-milestone notifier to enrich the message."""
    w = l = p = 0
    pl = 0.0
    for r in rows:
        g = (r.get("graded_result") or "").strip().upper()
        if g == "WIN":  w += 1
        elif g == "LOSS": l += 1
        elif g == "PASS": p += 1
        try:
            v = float((r.get("profit_loss_units") or "0") or 0)
            pl += v
        except (ValueError, TypeError):
            pass
    bets = w + l
    hit_rate = (w / bets * 100.0) if bets else 0.0
    return (w, l, p), pl, hit_rate


_BANKROLL_MILESTONES = [10, 25, 50, 75, 100, 150, 200, 300, 500]


def _check_bankroll_milestone_after_grade(rows: list[dict]) -> None:
    """Compute season P&L; fire a one-shot ping if it crossed any
    standard milestone (positive or negative).  Notifications_log
    dedup ensures each milestone fires at most once per 90-day window
    so we don't spam if P&L oscillates over a threshold.

    T4.17: gated behind FIRE_BANKROLL_MILESTONES=1 env var.  The
    SELECT-then-INSERT dedup path is racy under multi-writer load
    (Railway worker + GH Actions PREDICT cron + ODDS-ONLY cron + nightly
    GRADE cron all call grade_date() and each iterates the milestone
    list).  When two writers fire within the millisecond race window
    they both see "no row" and both ping -- producing N-x duplicates
    per crossing where N is the number of milestones below current P&L.

    The fix mirrors the daily-wrap gate (T4.14): only ONE writer is
    authorized to fire milestones.  Default is off everywhere -- if
    you want milestones, set FIRE_BANKROLL_MILESTONES=1 on the nightly
    grade cron in .github/workflows/daily.yml (mirroring how
    FIRE_DAILY_DIGEST is wired) so the burst of "hit +10, +25, +50"
    fires once per slate at 11:30pm ET instead of every 5 minutes."""
    if os.environ.get("FIRE_BANKROLL_MILESTONES", "").strip() != "1":
        return

    season_record, season_pl, hit_rate = _aggregate_season_record(rows)
    abs_pl = int(season_pl)   # truncate toward zero
    sign = 1 if season_pl >= 0 else -1
    abs_units = abs(abs_pl)
    for m in _BANKROLL_MILESTONES:
        if abs_units >= m:
            milestone_signed = m * sign
            _notify_bankroll_milestone_telegram(
                milestone_signed, season_record, season_pl, hit_rate
            )
            # Don't break — we want to fire each crossed milestone the
            # FIRST time it's crossed.  Dedup window prevents re-pings.
        else:
            break    # higher milestones not yet reached


def _notify_ops_health_telegram(minutes_since_last_predict: int) -> None:
    """Predictor / system health alert.  Fires when predictor hasn't
    written to picks_<season> in `minutes_since_last_predict` >= 30 min
    during active hours.  At-most-once-per-hour dedup so a sustained
    outage doesn't spam the user every cycle."""
    body = "\n".join([
        "🚨 <b>Predictor stalled</b>",
        f"Last successful Railway cycle was {minutes_since_last_predict} min ago.",
        "Expected ≤6 min during active hours.",
        "Check Railway logs / GHA workflow runs.",
        "",
        _dashboard_link(),
    ])
    # Use minute granularity in the key so the dedup window = 1h still
    # lets us re-ping if the outage persists across an hour boundary.
    bucket = (datetime.utcnow().minute // 30) * 30
    event_key = f"ops_health:stalled:{datetime.utcnow().strftime('%Y%m%d%H')}{bucket:02d}"
    _notify_event_telegram("ops_health", event_key, body)


def _record_pick_change(*, iso_date: str, game_pk: str,
                        away_team: str, home_team: str, game_time: str,
                        old_label: str, new_label: str,
                        captured_at: str) -> None:
    """Append a single change row to data/pick_changes.csv.  Idempotent
    only at the (date, game_pk, captured_at) tuple level -- multiple
    flips within the same minute would dedupe but separate refresh
    cycles each get their own entry, so the dashboard can show the full
    history of how a pick evolved through the day."""
    path = _change_log_path()
    # Build the row in a tiny in-memory buffer first so we can decide
    # header-or-not after re-reading the file.  Two cron runs that race
    # the append previously could both see is_new=True and both write
    # a header, breaking the parser.  Now we check the file's actual
    # first-line content rather than just existence.
    try:
        existing_has_header = False
        if path.exists() and path.stat().st_size > 0:
            with open(path, encoding="utf-8") as f:
                first = f.readline().strip()
                existing_has_header = first.startswith("captured_at_utc")
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CHANGE_LOG_FIELDS)
            if not existing_has_header:
                w.writeheader()
            w.writerow({
                "captured_at_utc": captured_at,
                "date":             iso_date,
                "game_pk":          game_pk,
                "away_team":        away_team,
                "home_team":        home_team,
                "game_time_et":     game_time or "",
                "old_pick_label":   old_label,
                "new_pick_label":   new_label,
            })
    except Exception:
        # Don't let a logging failure break the predictor run.
        pass

    # T2.30: dual-write to Supabase (Phase 1.5).  Best-effort mirror
    # of the journal entry; CSV is the source of truth so a Supabase
    # insert failure won't break anything.
    _mirror_pick_change_to_supabase(
        captured_at = captured_at,
        iso_date    = iso_date,
        game_pk     = game_pk,
        away_team   = away_team,
        home_team   = home_team,
        game_time   = game_time or "",
        old_label   = old_label,
        new_label   = new_label,
    )


def _prune_change_log(path: Path, keep_days: int = 90) -> None:
    """T3.5: Trim data/pick_changes.csv to last N days so it stays bounded.
    Cheap: a season of intraday flips is roughly a few thousand rows
    (~250KB).  Without this, the file would grow unbounded over years.
    Runs at the END of log_picks so we touch it at most once per
    predictor invocation."""
    if not path.exists():
        return
    try:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=keep_days)
        cutoff_iso = cutoff.isoformat() + "Z"
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = [r for r in reader
                    if (r.get("captured_at_utc") or "") >= cutoff_iso]
        # If we'd lose nothing, skip the rewrite entirely.
        original_count = sum(1 for _ in open(path, encoding="utf-8")) - 1
        if len(rows) >= original_count:
            return
        # Atomic rewrite via tempfile + os.replace (mirrors _write_rows).
        import os, tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CHANGE_LOG_FIELDS)
                w.writeheader()
                w.writerows(rows)
                f.flush()
                try: os.fsync(f.fileno())
                except OSError: pass
            os.replace(tmp_path, path)
        except Exception:
            try: os.unlink(tmp_path)
            except OSError: pass
            raise
    except Exception:
        pass  # non-fatal


# ---------------------------------------------------------------------------
# 2. Grade picks
# ---------------------------------------------------------------------------

def _fetch_first_inning(game_pk: int) -> dict:
    """
    Pull the first-inning run totals for a completed (or live) game.

    Returns a dict with keys:
      state        - "Final" | "Live" | "Preview" | other
      detail       - detailed state string (e.g. "Postponed")
      away_runs    - int or None
      home_runs    - int or None
      complete     - bool: True only when the entire 1st inning (both
                     halves) is finished -- the rule applied everywhere
                     that grades or surfaces "1st inning done".

    NOTE: MLB's `game` endpoint sometimes lags on postponements -- it can
    keep returning "Scheduled" for hours after MLB has officially called
    a rainout, while the `schedule` endpoint correctly shows "Postponed"
    immediately.  When we get an inconsistent "Scheduled / no innings
    played" response, we double-check via the schedule endpoint and
    promote the postponement signal if we find one there.

    First-inning completion rule (must match workers/live_state.py and
    dashboard/app/api/live-state/route.ts):
      * abstractGameState == "Final" -> complete
      * currentInning >= 2 -> complete
      * currentInning == 1 AND inningState == "End" -> complete
      * anything else (including B1 / Middle of 1) -> NOT complete.
    A 0-0 score during B1 is NOT a completed inning -- the home team is
    still batting and a YRFI run can still cross.  The previous rule
    treated B1 / Middle of 1 as complete, which let the grader lock in
    NRFI before the home half finished.
    """
    try:
        data = statsapi.get("game", {
            "gamePk": game_pk,
            "fields": (
                "gameData,status,abstractGameState,detailedState,"
                "datetime,officialDate,"
                "liveData,linescore,innings,num,home,away,runs,"
                "currentInning,inningState"
            ),
        })
    except Exception as exc:
        return {
            "state": "ERROR", "detail": str(exc),
            "away_runs": None, "home_runs": None,
            "complete": False,
        }

    status = data.get("gameData", {}).get("status", {})
    state  = status.get("abstractGameState", "")
    detail = status.get("detailedState", "")

    linescore = (
        data.get("liveData", {})
            .get("linescore", {})
    )
    innings    = linescore.get("innings", []) or []
    cur_inning = linescore.get("currentInning")
    inn_state  = linescore.get("inningState", "") or ""

    away_r = home_r = None
    if innings:
        inn1   = innings[0]
        away_r = inn1.get("away", {}).get("runs")
        home_r = inn1.get("home", {}).get("runs")

    # Postpone-detection fallback: when the game endpoint says "Scheduled"
    # / "Preview" with no innings played, ask the schedule endpoint --
    # which updates faster on postponements.  The game endpoint's
    # officialDate flips to the RESCHEDULED date once a makeup is set,
    # so we query by gamePk directly (returns the game on every date it
    # appears -- original + makeup) and look for ANY postpone signal.
    if detail == "Scheduled" and away_r is None and home_r is None:
        try:
            sched = statsapi.get("schedule", {
                "sportId": 1, "gamePk": game_pk,
                "fields": "dates,games,gamePk,status,detailedState,reason",
            })
            for sd in sched.get("dates", []):
                for sg in sd.get("games", []):
                    if int(sg.get("gamePk") or 0) != int(game_pk):
                        continue
                    sched_detail = sg.get("status", {}).get("detailedState", "")
                    if sched_detail in ("Postponed", "Suspended", "Cancelled"):
                        detail = sched_detail
                        state  = "Final"  # treat as terminal so the grader records it
                        break
                if detail != "Scheduled":
                    break
        except Exception:
            pass  # fallback failure is non-fatal; we just keep the original state

    # Authoritative 1st-inning completion check.  Strict by design:
    # treat B1 / Middle of 1 as IN PROGRESS, not complete, even if the
    # current score is 0-0 (the home team is still hitting).
    complete = (
        state == "Final"
        or (isinstance(cur_inning, int) and cur_inning >= 2)
        or (cur_inning == 1 and inn_state == "End")
    )

    return {
        "state": state,
        "detail": detail,
        "away_runs": away_r,
        "home_runs": home_r,
        "complete": bool(complete),
    }


def grade_date(date_str: str, season: int) -> None:
    """
    Grade all picks for a given date that haven't been graded yet.
    Prints a per-game summary, then writes results back to the CSV.

    Grades as soon as innings[0] has both away+home run data -- does NOT
    require the game to be in Final state.
    """
    iso_date = _to_iso(date_str)
    path     = _csv_path(season)
    rows     = _read_rows(path)

    targets = [(i, r) for i, r in enumerate(rows) if r["date"] == iso_date]

    if not targets:
        print(f"No picks found for {iso_date}. Run the predictor first.")
        return

    print(f"\nGrading picks for {iso_date}  ({len(targets)} games)\n")

    now        = _now_utc()
    graded_n   = skipped_n = already_n = 0
    # T2.30: track which row indices we mutated so we can mirror just
    # those rows to Supabase after the CSV write (Phase 1.5 dual-write).
    graded_indices: list[int] = []

    for idx, row in targets:
        tag = f"  {row['away_team']:>3} @ {row['home_team']:<3}"

        existing_grade = row.get("graded_result", "")
        # Allow regrading of POSTPONED / SUSPENDED rows: MLB sometimes
        # marks a game POSTPONED then later actually plays it as a
        # makeup with the same game_pk, or resumes a suspended game.
        # Without this, the original PP grade is permanent and the
        # actual W/L outcome never gets recorded.  Only WIN / LOSS /
        # PASS are truly terminal (real outcomes that won't re-grade
        # differently).
        terminal = {"WIN", "LOSS", "PASS"}
        if existing_grade and existing_grade.upper() in terminal:
            print(f"{tag}  already graded: {existing_grade}")
            already_n += 1
            continue
        if existing_grade and existing_grade.upper() in ("POSTPONED", "SUSPENDED"):
            print(f"{tag}  was {existing_grade}, re-checking for makeup/resume...")

        result = _fetch_first_inning(int(row["game_pk"]))
        state  = result["state"]
        detail = result["detail"]

        if state == "ERROR":
            print(f"{tag}  API error: {detail}")
            skipped_n += 1
            continue

        away_r = result["away_runs"]
        home_r = result["home_runs"]

        # T2.7: If a game is Suspended but the 1st inning was COMPLETE
        # before the suspension hit (e.g. rain delay during the 3rd
        # inning, suspended at 7-2 in the 5th, resumed next day), the
        # 1st-inning result is already determined and the bet should
        # be graded as a normal W/L -- not marked SUSPENDED-no-bet.
        # Only treat it as terminal-no-bet when the 1st inning never
        # completed.  We use the strict completion flag here too so a
        # game suspended in B1/M1 (where MLB might report home_r as 0
        # before the bottom half is over) is correctly marked SUSPENDED
        # instead of falling through to a wrong NRFI grade.
        if detail in ("Postponed", "Suspended", "Cancelled"):
            if (
                away_r is not None
                and home_r is not None
                and bool(result.get("complete"))
            ):
                # 1st inning was finished -- fall through to normal grading.
                # Don't return; we'll grade the W/L below.
                print(f"{tag}  {detail} but 1st inning complete ({away_r}-{home_r}) -- grading normally")
            else:
                rows[idx]["actual_result"] = detail.upper()
                rows[idx]["graded_result"] = detail.upper()
                rows[idx]["graded_at"]     = now
                graded_indices.append(idx)
                print(f"{tag}  {detail} -- marked, not counted as a bet")
                graded_n += 1
                # T2.38 #3: STRONG bet voided ping (POSTPONED/SUSPENDED/CANCELLED).
                _notify_strong_voided_telegram(rows[idx], detail)
                continue

        # First-inning completion gate.  We require BOTH the run totals to
        # exist AND the 1st inning itself to be over (Final / inning >= 2 /
        # End of 1).  Without this gate a live B1 with a 0-0 score reads
        # as away_r=0, home_r=0 and would be permanently graded NRFI before
        # the home half of the 1st actually ended.
        is_complete = bool(result.get("complete"))
        if away_r is None or home_r is None or not is_complete:
            # First inning not yet complete -- decide why
            if state in ("Preview", "Scheduled"):
                # If the slate's calendar date is more than ~18h in the past
                # AND the game still says "Scheduled", treat it as effectively
                # postponed.  MLB's API often lags on the official Postponed
                # status update -- this prevents the daily grader from leaving
                # rainouts ungraded forever.
                hours_past = _hours_since_slate_date(iso_date)
                # 6h past midnight ET (= 6am ET next day).  Genuine games
                # are over by 1-2am ET at the latest; if status is still
                # "Scheduled" by 6am the next day, MLB has just lagged on
                # marking it postponed.
                if hours_past >= 6:
                    rows[idx]["actual_result"] = "POSTPONED"
                    rows[idx]["graded_result"] = "POSTPONED"
                    rows[idx]["graded_at"]     = now
                    graded_indices.append(idx)
                    print(f"{tag}  stale-scheduled ({hours_past:.0f}h past slate) "
                          f"-- marking POSTPONED")
                    graded_n += 1
                    # T2.38 #3: STRONG bet voided ping for stale-scheduled rainouts.
                    _notify_strong_voided_telegram(rows[idx], "POSTPONED")
                    continue
                print(f"{tag}  game not started yet -- skipping")
            elif state == "Live":
                print(f"{tag}  Live but 1st inning not yet complete -- skipping")
            else:
                print(f"{tag}  1st inning data unavailable (state={state!r}) -- skipping")
            skipped_n += 1
            continue

        total_r = away_r + home_r
        actual  = "NRFI" if total_r == 0 else "YRFI"
        pick    = row["pick_side"]

        if pick == "PASS":
            graded_result = "PASS"
        elif pick == actual:
            graded_result = "WIN"
        else:
            graded_result = "LOSS"

        rows[idx]["actual_result"] = actual
        rows[idx]["graded_result"] = graded_result
        rows[idx]["fi_away_runs"]  = away_r
        rows[idx]["fi_home_runs"]  = home_r
        rows[idx]["fi_total_runs"] = total_r
        rows[idx]["graded_at"]     = now
        # T2.27: compute P&L now that the grade landed.  Previously this
        # was deferred to the next --import-odds run, which on the live
        # path could be 30-60 min late OR never if no fresh DK fetch
        # came in (e.g., late-evening grade post-market-pull).  Without
        # this, a graded WIN row would show profit_loss_units="" on the
        # dashboard until the next odds refresh -- looks like the win
        # didn't count.  _calc_pnl is pure (no I/O) so safe inside the
        # grade loop.
        rows[idx]["profit_loss_units"] = _calc_pnl(rows[idx])
        graded_indices.append(idx)

        outcome_tag = {"WIN": "W", "LOSS": "L", "PASS": "-"}.get(graded_result, "?")
        source_tag  = "" if state == "Final" else f" [from {state}]"
        print(
            f"{tag}  1st inn: {away_r}-{home_r} ({actual})  "
            f"pick={pick}  ->  {outcome_tag} {graded_result}{source_tag}"
        )
        graded_n += 1

        # T2.38 #1: STRONG bet graded W/L ping.  Today's running record
        # + P&L computed on the fly from `rows` (in-memory, no extra
        # query).  Function self-filters non-STRONG / non-bet-placed
        # rows so calling for every grade is safe.
        today_record, today_pl = _aggregate_today_record(rows, iso_date)
        _notify_strong_graded_telegram(rows[idx], today_record, today_pl)

    _write_rows(path, rows)
    # T2.30: dual-write to Supabase (Phase 1.5).  Mirrors only the
    # rows that were actually graded in this call.
    _mirror_picks_to_supabase(season, [rows[i] for i in graded_indices])

    # T2.38 #6: Bankroll milestone check.  Run AFTER all rows are
    # graded for this date so the season total reflects the latest
    # P&L.  Function self-dedups via notifications_log so it can be
    # called every grade run without spamming.
    if graded_n > 0:
        _check_bankroll_milestone_after_grade(rows)

    # T2.38 #4 / T4.14: Daily digest, gated to ONE writer per slate.
    #
    # Original (T2.38) design: fire whenever all of today's rows were
    # terminal at the end of any grade_date() run, with notifications_log
    # 18h dedup as the only defense.  In practice four independent
    # writers all run grade_date for "today":
    #   - GH Actions PREDICT cron (hourly during prime time)
    #   - GH Actions ODDS-ONLY cron (every 5 min, 1pm-4am ET)
    #   - GH Actions GRADE cron (nightly 11:30pm ET)
    #   - Railway predictor_loop worker (its own cadence)
    # Once the last game lands, every subsequent tick from ANY writer
    # tried to fire the digest.  The dedup is a SELECT-then-INSERT
    # pattern (no atomic claim), so two writers within milliseconds
    # would both see "no row in window" and both ping -- producing
    # 2-5 wrap pings per slate ("the random wrap messages").
    #
    # Fix: gate the trigger on FIRE_DAILY_DIGEST=1.  ONE cron sets it
    # (the nightly grade in .github/workflows/daily.yml).  Everything
    # else leaves it unset, so the digest path is a no-op for them.
    # The notifications_log dedup remains as a backup but is no longer
    # carrying the weight.
    if os.environ.get("FIRE_DAILY_DIGEST", "").strip() == "1":
        iso_today_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        if iso_date == iso_today_et:
            all_today = [r for r in rows if (r.get("date") or "").strip() == iso_date]
            terminal = {"WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED"}
            if all_today and all(
                (r.get("graded_result") or "").strip().upper() in terminal
                for r in all_today
            ):
                today_record, today_pl = _aggregate_today_record(rows, iso_date)
                season_record, season_pl, _hit = _aggregate_season_record(rows)
                # We don't know tomorrow's slate count without an MLB API call;
                # leave 0 — predictor cycle the next morning will fix the
                # board.  Could enrich in a future revision.
                _notify_daily_digest_telegram(
                    iso_date         = iso_date,
                    today_record     = today_record,
                    today_pl_units   = today_pl,
                    season_record    = season_record,
                    season_pl_units  = season_pl,
                    tomorrow_games   = 0,
                )

    print(f"\n  Graded {graded_n} | Skipped {skipped_n} | Already done {already_n}")
    print(f"  CSV: {path}\n")

# ---------------------------------------------------------------------------
# 3. Odds import & edge calculation
# ---------------------------------------------------------------------------

def american_to_prob(odds_str: str) -> float | None:
    """
    Convert an American odds string to implied probability (0-1).
    Handles "-110", "+105", "110" (treated as negative), etc.
    Returns None if the string cannot be parsed.
    """
    if not odds_str:
        return None
    s = str(odds_str).strip().replace(" ", "")
    try:
        odds = float(s)
    except ValueError:
        return None
    if odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:  # negative
        return abs(odds) / (abs(odds) + 100.0)


def payout_per_unit(odds_str: str) -> float | None:
    """
    How much profit you earn per 1 unit risked (not including stake return).
    E.g. -110 -> 0.909,  +105 -> 1.05,  +100 (even) -> 1.0
    Returns None if unparseable.
    """
    if not odds_str:
        return None
    s = str(odds_str).strip().replace(" ", "")
    try:
        odds = float(s)
    except ValueError:
        return None
    if odds == 0:
        return None
    if odds > 0:
        return odds / 100.0
    else:
        return 100.0 / abs(odds)


def _pick_dh_candidate(
    rows:        list[dict],
    candidates:  list[int],
    start_iso:   str,
) -> int | None:
    """T2.21 -- DH-aware odds-to-pick matching.

    `candidates` is a list of indices into `rows` that all share
    (date, away_team, home_team) -- typically 2 entries for a
    doubleheader.  `start_iso` is the odds row's `start_time_utc`
    (DK's `event.startEventDate`).  Returns the index whose
    `game_time_et` parses to a UTC time within 90 min of `start_iso`,
    breaking ties by smallest delta.  Returns None if nothing is in
    range -- caller should fall back to first candidate or skip.

    Why 90 min: a typical DH-1 starts ~3.5h before DH-2 (e.g. 4:10pm
    and 7:10pm ET).  90 min is well inside half-the-gap so they
    can't both match the same odds row.  Generous enough to absorb
    schedule shifts of an hour or so without false-failing.
    """
    try:
        odds_dt = datetime.strptime(
            start_iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z"
        )
    except (ValueError, AttributeError):
        # Some DK timestamps include subseconds -- try the trim path.
        try:
            cleaned = start_iso.split(".")[0] + "+0000"
            odds_dt = datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S%z")
        except (ValueError, AttributeError):
            return None

    et = ZoneInfo("America/New_York")
    best_idx     = None
    best_delta_s = None
    for i in candidates:
        time_et = (rows[i].get("game_time_et") or "").strip()
        if not time_et:
            continue
        # game_time_et looks like "7:05 PM ET" -- parse and combine
        # with the row's date in ET.
        try:
            t = datetime.strptime(time_et.replace(" ET", "").strip(), "%I:%M %p")
        except ValueError:
            continue   # placeholder like "After Game 1" -- skip
        date_iso = rows[i].get("date", "")
        try:
            d = datetime.strptime(date_iso, "%Y-%m-%d")
        except ValueError:
            continue
        pick_dt_et = d.replace(hour=t.hour, minute=t.minute, tzinfo=et)
        pick_dt_utc = pick_dt_et.astimezone(timezone.utc)
        delta_s = abs((pick_dt_utc - odds_dt).total_seconds())
        if delta_s > 90 * 60:
            continue
        if best_delta_s is None or delta_s < best_delta_s:
            best_delta_s = delta_s
            best_idx = i
    return best_idx


def _calc_pnl(row: dict) -> str:
    """
    Compute profit/loss in units for one already-graded row.

    If market odds are populated, computes the actual payout.  If they're
    not (no real odds imported yet), falls back to flat -110 with 1u
    on STRONG / 0.5u on LEAN.

    bet_placed="N" overrides everything: that means odds were imported
    but the edge was below threshold (or negative), so no bet was placed
    and the row contributes 0 to P&L.

    Returns a formatted string or "" if the row was a PASS / not graded.
    """
    graded = row.get("graded_result", "")
    if graded not in ("WIN", "LOSS"):
        return ""

    pick_side = (row.get("pick_side") or "").strip().upper()
    if pick_side not in ("NRFI", "YRFI"):
        return ""  # PASS rows aren't bets

    # Explicit "no bet" decision from the odds-import flow (negative edge
    # below min_edge threshold) -- contribute 0 P&L.
    if (row.get("bet_placed") or "").strip().upper() == "N":
        return _fmt(0.0, 3)

    # Determine bet size: prefer explicit units_risked, otherwise use
    # strength-based default (matches what the dashboard displays).
    units_str = (row.get("units_risked") or "").strip()
    if units_str:
        try: units = float(units_str)
        except ValueError: return ""
    else:
        strength = (row.get("pick_strength") or "").strip().upper()
        units = 1.0 if strength == "STRONG" else 0.5 if strength == "LEAN" else 0.0
        if units <= 0:
            return ""

    if graded == "LOSS":
        return _fmt(-units, 3)

    # WIN -- prefer the actual market odds, otherwise default to flat -110
    odds_col = "market_nrfi_odds" if pick_side == "NRFI" else "market_yrfi_odds"
    ppu = payout_per_unit(row.get(odds_col, ""))
    if ppu is None:
        ppu = 100.0 / 110.0   # flat -110 fallback (= 0.9091)
    return _fmt(units * ppu, 3)


def _apply_odds_to_row(
    row:         dict,
    nrfi_odds:   str,
    yrfi_odds:   str,
    sportsbook:  str,
    min_edge:    float,
    units_lean:  float,
    units_strong: float,
    captured_at: str,
) -> dict:
    """
    Apply market odds to a single row dict.
    Computes implied probs, edges, bet sizing, and P&L if already graded.
    Returns the mutated row.

    T2.23 -- Bet-time odds lock.  Once a bet has been placed
    (bet_placed=Y) at the recorded market_* price, subsequent imports
    do NOT overwrite market_* / edge / bet_placed / units_risked.  The
    rationale: the user is already in the bet at that price; further
    DK line movement is irrelevant to THEIR position, and a moving
    OddsChip on the dashboard makes them second-guess a closed
    decision.

    Trade-off: we forgo closing-line capture on bet-placed games
    (market_* would otherwise track the latest scrape and become the
    closing line).  opened_*_odds (T4.28) still records the FIRST
    price ever seen, so we can still compute "open -> bet" line
    movement -- which is the CLV that actually affects the user's
    bet (movement after our entry doesn't help us).

    The lock releases automatically if the existing row has
    bet_placed=Y but market_*_odds are blank (data corruption /
    legacy row): we treat that as "no real lock" and re-evaluate.
    """
    existing_bet_placed   = (row.get("bet_placed") or "").strip().upper()
    existing_market_nrfi  = (row.get("market_nrfi_odds") or "").strip()
    existing_market_yrfi  = (row.get("market_yrfi_odds") or "").strip()

    if (existing_bet_placed == "Y"
        and existing_market_nrfi
        and existing_market_yrfi):
        # T2.38 #5: BEFORE the early-return, compute current implied
        # probability vs `opened_*_odds` (locked at first scrape).  If
        # the market shifted >=5pp toward the picked side on a STRONG
        # bet, fire a CLV alert.  Notifications_log dedup ensures one
        # ping per bet per day.  Doesn't update market_*_odds — those
        # stay locked per T2.23 — purely informational.
        if (row.get("pick_strength") or "").upper() == "STRONG":
            picked_side = (row.get("pick_side") or "").upper()
            opened_col  = "opened_nrfi_odds" if picked_side == "NRFI" else "opened_yrfi_odds"
            fresh_odds  = nrfi_odds if picked_side == "NRFI" else yrfi_odds
            opened_imp  = american_to_prob(row.get(opened_col, ""))
            fresh_imp   = american_to_prob(fresh_odds)
            if (opened_imp is not None and fresh_imp is not None
                    and (fresh_imp - opened_imp) >= 0.05):
                _notify_strong_clv_telegram(row, opened_imp, fresh_imp)

        # Locked.  Refresh book name (in case the import is from a
        # different sportsbook -- unlikely but harmless), and recompute
        # profit_loss_units (so a grade landing AFTER lock still gets
        # a P&L).  Everything else stays frozen.
        if sportsbook:
            row["sportsbook"] = sportsbook
        row["profit_loss_units"] = _calc_pnl(row)
        return row

    row["market_nrfi_odds"] = nrfi_odds
    row["market_yrfi_odds"] = yrfi_odds
    row["sportsbook"]       = sportsbook
    row["odds_captured_at"] = captured_at

    # T4.28: Capture the FIRST seen odds as the "open" line; never
    # overwrite once set.  market_* keeps tracking the latest scrape;
    # the final pre-game value (before DK pulls the market) becomes
    # the "close" used for CLV computation.
    if not (row.get("opened_nrfi_odds") or "").strip():
        row["opened_nrfi_odds"]  = nrfi_odds
        row["opened_yrfi_odds"]  = yrfi_odds
        row["opened_captured_at"] = captured_at

    imp_nrfi = american_to_prob(nrfi_odds)
    imp_yrfi = american_to_prob(yrfi_odds)

    row["implied_nrfi_prob"] = _fmt(imp_nrfi, 4) if imp_nrfi is not None else ""
    row["implied_yrfi_prob"] = _fmt(imp_yrfi, 4) if imp_yrfi is not None else ""

    # Model probs (already logged at prediction time)
    try:
        model_nrfi = float(row.get("nrfi_prob", "") or 0)
        model_yrfi = float(row.get("yrfi_prob", "") or 0)
    except ValueError:
        row["edge_nrfi"] = row["edge_yrfi"] = row["edge_on_pick"] = ""
        row["bet_placed"] = "N"
        row["units_risked"] = ""
        return row

    edge_nrfi = (model_nrfi - imp_nrfi) if imp_nrfi is not None else None
    edge_yrfi = (model_yrfi - imp_yrfi) if imp_yrfi is not None else None

    row["edge_nrfi"] = _fmt(edge_nrfi, 4) if edge_nrfi is not None else ""
    row["edge_yrfi"] = _fmt(edge_yrfi, 4) if edge_yrfi is not None else ""

    pick      = row.get("pick_side", "")
    strength  = row.get("pick_strength", "")

    if pick == "NRFI":
        edge_pick = edge_nrfi
    elif pick == "YRFI":
        edge_pick = edge_yrfi
    else:
        edge_pick = None

    row["edge_on_pick"] = _fmt(edge_pick, 4) if edge_pick is not None else ""

    # Bet sizing.  T2.24 -- STRONG picks auto-bet regardless of edge:
    # user's stated policy is "if the model commits STRONG, we bet at
    # whatever odds DK has".  LEAN keeps the 2% edge gate (model is less
    # certain on LEAN, so we want a margin of safety on price).
    #
    # units_risked is always populated for NRFI/YRFI picks (per T2.3) so
    # post-mortem analysis can compute counterfactual P&L for any row
    # regardless of bet_placed.  PASS picks have no would-be stake.
    # T2.58: gate the auto-bet flag on the lock window.  Prior behavior
    # set bet_placed=Y as soon as a pick became STRONG -- which could be
    # hours pre-game, before lineups/weather/scratches were final.  Real-
    # world consequence (2026-05-03 ATL@COL): pick locked at STRONG YRFI
    # at 12:30 PM, lambda dropped 0.008 by 12:42 PM, demoted to PASS,
    # but user had already placed the bet.  Now: bet_placed=Y only fires
    # within the lock window (default 60 min pre-game), giving the model
    # one last cycle with fully-current data before committing.
    iso_date_for_lock = (row.get("date") or "").strip()
    game_time_for_lock = (row.get("game_time_et") or "").strip()
    inside_lock = _is_inside_lock_window(game_time_for_lock, iso_date_for_lock)
    # Capture pre-edit bet_placed so we can detect the N->Y transition
    # below and fire the BET LOCKED Telegram alert exactly once per game.
    pre_edit_bet_placed = (row.get("bet_placed") or "").strip().upper()

    if pick in ("NRFI", "YRFI"):
        would_be_units = units_strong if strength == "STRONG" else (
            units_lean if strength == "LEAN" else 0.0
        )
        if strength == "STRONG" and would_be_units > 0:
            # T2.58: STRONG pre-lock = stake recorded but not bet.
            #        STRONG inside lock window = commit.
            if inside_lock:
                row["bet_placed"]   = "Y"
            else:
                row["bet_placed"]   = "N"   # pending, will commit at lock
            row["units_risked"] = _fmt(would_be_units, 2)
        elif edge_pick is not None and edge_pick >= min_edge and would_be_units > 0:
            # LEAN with edge above threshold -> bet (lock-gated too).
            if inside_lock:
                row["bet_placed"]   = "Y"
            else:
                row["bet_placed"]   = "N"
            row["units_risked"] = _fmt(would_be_units, 2)
        else:
            # LEAN below edge threshold -> skip but record the would-be stake.
            row["bet_placed"]   = "N"
            row["units_risked"] = _fmt(would_be_units, 2) if would_be_units > 0 else ""
    else:
        row["bet_placed"]   = "N"
        row["units_risked"] = ""

    # T4.28: CLV — closing implied prob - opened implied prob, on the
    # picked side.  Positive = market moved toward our pick (we beat
    # the close).  Computable any time we have both opened_* (set on
    # first import) and the latest market_* (set on every import).
    try:
        opened_nrfi = american_to_prob(row.get("opened_nrfi_odds", ""))
        opened_yrfi = american_to_prob(row.get("opened_yrfi_odds", ""))
        closing_nrfi = imp_nrfi  # latest scrape we just stored
        closing_yrfi = imp_yrfi
        clv = None
        if pick == "NRFI" and opened_nrfi is not None and closing_nrfi is not None:
            clv = closing_nrfi - opened_nrfi
        elif pick == "YRFI" and opened_yrfi is not None and closing_yrfi is not None:
            clv = closing_yrfi - opened_yrfi
        row["clv_pct"] = _fmt(clv, 4) if clv is not None else ""
    except Exception:
        row["clv_pct"] = ""

    # Compute P&L if graded
    row["profit_loss_units"] = _calc_pnl(row)

    # T2.58: fire the BET LOCKED Telegram alert when bet_placed
    # transitions from "" / "N" to "Y" on a STRONG pick.  Dedup via
    # notifications_log strong_locked window means a re-run of
    # import_odds after lock won't re-ping.  Fires exactly once per
    # game per slate.
    new_bet_placed = (row.get("bet_placed") or "").strip().upper()
    if (pre_edit_bet_placed in ("", "N") and new_bet_placed == "Y"
            and (row.get("pick_strength") or "").strip().upper() == "STRONG"):
        try:
            _notify_strong_locked_telegram(row)
        except Exception:    # noqa: BLE001 — advisory only, never break import
            pass

    return row


def import_odds(
    odds_path:     str | Path,
    season:        int | None = None,
    min_edge:      float      = 0.00,
    units_lean:    float      = 0.5,
    units_strong:  float      = 1.0,
) -> None:
    """
    Import market odds from a CSV file and update picks_{season}.csv.

    Odds file format (header required):
      date, game_pk, away_team, home_team, market_nrfi_odds, market_yrfi_odds
      Optional columns: sportsbook, start_time_utc

    Matching priority (T2.21):
      1. date + game_pk                                    (exact)
      2. date + away + home + start_time_utc match         (DH-aware)
      3. date + away + home                                (legacy fallback)

    The DH-aware path is what kept doubleheaders from getting
    half-priced -- `by_team` collisions caused DH-2 to overwrite DH-1
    in the lookup, so DH-1 never matched.  Now if the odds row carries
    a `start_time_utc`, we compare against picks_2026's `game_time_et`
    converted to UTC and pick the closest match (within 90 min).

    After matching, computes:
      implied probs, model edges, bet_placed, units_risked, profit_loss_units

    Example:
      2026-04-05,745459,NYY,BOS,-115,+105,FanDuel
      2026-04-05,,LAD,SF,-110,-110,DraftKings,2026-04-05T23:10:00Z
    """
    odds_path = Path(odds_path)
    if not odds_path.exists():
        print(f"Odds file not found: {odds_path}")
        return

    # Infer season from odds file if not provided
    if season is None:
        season = date_type.today().year

    picks_path = _csv_path(season)
    rows       = _read_rows(picks_path)

    if not rows:
        print(f"No picks logged for season {season}. Run the predictor first.")
        return

    # Build lookup indexes.  T2.21: by_team is now a LIST of indices per
    # team key (not a single int) so DH games don't clobber each other.
    # Caller picks the right one by start-time proximity when DH detected.
    by_pk:   dict[tuple, int]              = {}   # (iso_date, game_pk) -> idx
    by_team: dict[tuple, list[int]]        = {}   # (iso_date, away, home) -> [idx,...]
    for i, r in enumerate(rows):
        d = r["date"]
        by_pk[(d, str(r["game_pk"]))] = i
        key = (d, r["away_team"].upper(), r["home_team"].upper())
        by_team.setdefault(key, []).append(i)

    now = _now_utc()
    matched_n = unmatched_n = pk_matched_n = team_matched_n = time_matched_n = 0
    dates_covered: set[str] = set()
    # T2.30: track which row indices got odds applied so we mirror
    # exactly that subset to Supabase after the CSV write.
    matched_indices: list[int] = []

    with open(odds_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Normalize column names
        for odds_row in reader:
            cols = {k.strip().lower(): v.strip() for k, v in odds_row.items()}
            iso_date = _to_iso(cols.get("date", ""))
            game_pk  = str(cols.get("game_pk", "")).strip()
            away     = cols.get("away_team", "").upper()
            home     = cols.get("home_team", "").upper()
            nrfi_o   = cols.get("market_nrfi_odds", "")
            yrfi_o   = cols.get("market_yrfi_odds", "")
            book     = cols.get("sportsbook", "")
            start_iso = cols.get("start_time_utc", "")  # T2.21

            # Attempt match
            idx         = None
            match_method = ""
            if game_pk:
                idx = by_pk.get((iso_date, game_pk))
                if idx is not None:
                    match_method = "pk"
            if idx is None:
                # T2.21 -- DH-aware match.  Look up all picks rows for this
                # (date, away, home) and pick the one whose game_time_et
                # is closest to the odds row's start_time_utc.  90-min
                # tolerance is generous enough to absorb minor schedule
                # shifts but tight enough to keep DH-1 (afternoon) and
                # DH-2 (evening) on opposite sides of the cutoff.
                candidates = by_team.get((iso_date, away, home), [])
                if len(candidates) == 1:
                    idx = candidates[0]
                    match_method = "teams"
                elif len(candidates) > 1:
                    if start_iso:
                        idx = _pick_dh_candidate(rows, candidates, start_iso)
                        if idx is not None:
                            match_method = "teams+time"
                    if idx is None:
                        # Fall back to the first DH candidate so we don't
                        # silently drop the row -- but log it so partial
                        # coverage on DH days surfaces.
                        idx = candidates[0]
                        match_method = "teams (DH-ambiguous; first)"

            if idx is None:
                print(f"  [no match] {iso_date}  {away} @ {home}  pk={game_pk or '?'}")
                unmatched_n += 1
                continue

            if match_method == "pk":
                pk_matched_n += 1
            elif match_method.startswith("teams+time"):
                time_matched_n += 1
            else:
                team_matched_n += 1

            dates_covered.add(iso_date)

            rows[idx] = _apply_odds_to_row(
                rows[idx], nrfi_o, yrfi_o, book, min_edge,
                units_lean, units_strong, now
            )
            matched_indices.append(idx)

            r = rows[idx]
            bet_tag   = f"bet={'Y' if r['bet_placed']=='Y' else 'N'}"
            edge_tag  = f"edge={r['edge_on_pick'] or 'N/A'}"
            units_tag = f"{r['units_risked']}u" if r.get("bet_placed") == "Y" else ""
            print(
                f"  {r['away_team']:>3} @ {r['home_team']:<3}  "
                f"NRFI:{nrfi_o:>5}  YRFI:{yrfi_o:>5}  "
                f"{bet_tag}  {edge_tag}  {units_tag}"
            )
            matched_n += 1

    _write_rows(picks_path, rows)
    # T2.30: dual-write to Supabase (Phase 1.5).  Mirrors only the
    # rows that just got odds applied -- typically the full slate
    # for a given date rather than the entire CSV.
    _mirror_picks_to_supabase(season, [rows[i] for i in matched_indices])

    # Re-read to count remaining odds gaps for dates covered
    if dates_covered:
        updated_rows = _read_rows(picks_path)
        missing_odds = [
            r for r in updated_rows
            if r["date"] in dates_covered and not r.get("market_nrfi_odds")
        ]
    else:
        missing_odds = []

    print(f"\n  Matched {matched_n} "
          f"({pk_matched_n} by game_pk, "
          f"{time_matched_n} by teams+time, "
          f"{team_matched_n} by teams)"
          f"  |  Unmatched {unmatched_n}")
    if missing_odds:
        print(f"  Still missing odds: {len(missing_odds)} pick(s) for {sorted(dates_covered)}")
    print(f"  CSV: {picks_path}")
    print(f"  -> Run --summary to view ROI metrics\n")

# ---------------------------------------------------------------------------
# 4. Odds template export
# ---------------------------------------------------------------------------

_TEMPLATE_COLS = [
    "date", "game_pk", "away_team", "home_team",
    "market_nrfi_odds", "market_yrfi_odds", "sportsbook",
]


def export_odds_template(
    date_str:    str,
    season:      int,
    output_path: str | Path | None = None,
) -> str:
    """
    Write a blank odds-import CSV template for all logged picks on date_str.
    Pre-fills date, game_pk, away_team, home_team; leaves odds columns blank.
    Returns the output file path (empty string on error).
    """
    iso_date = _to_iso(date_str)
    rows     = _read_rows(_csv_path(season))
    targets  = [r for r in rows if r["date"] == iso_date]

    if not targets:
        print(f"No logged picks found for {iso_date} (season {season}).")
        print("Run the predictor first, then export the template.")
        return ""

    if output_path is None:
        output_path = Path(f"odds_{iso_date.replace('-', '_')}.csv")
    output_path = Path(output_path)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_TEMPLATE_COLS, extrasaction="ignore")
        writer.writeheader()
        for r in targets:
            writer.writerow({
                "date":             r["date"],
                "game_pk":          r["game_pk"],
                "away_team":        r["away_team"],
                "home_team":        r["home_team"],
                "market_nrfi_odds": "",
                "market_yrfi_odds": "",
                "sportsbook":       "",
            })

    print(f"\n  Exported {len(targets)} game(s) for {iso_date} -> {output_path}")
    print(f"  Fill in market_nrfi_odds / market_yrfi_odds, then run:")
    print(f"  python mlb_first_inning_predictor.py --import-odds {output_path}\n")
    return str(output_path)


# ---------------------------------------------------------------------------
# 5. CSV audit & repair
# ---------------------------------------------------------------------------

# Map label prefixes -> (pick_side, pick_strength) for safe auto-recovery
_LABEL_TO_SIDE: dict[str, tuple[str, str]] = {
    "STRONG NRFI": ("NRFI", "STRONG"),
    "LEAN NRFI":   ("NRFI", "LEAN"),
    "STRONG YRFI": ("YRFI", "STRONG"),
    "LEAN YRFI":   ("YRFI", "LEAN"),
}


def audit_csv(season: int | None = None) -> int:
    """
    Audit picks CSV for data-integrity issues.
    Checks: corrupted pick_side/strength, missing keys, reconciliation.
    Returns number of issues found.
    """
    if season is None:
        season = date_type.today().year

    path = _csv_path(season)
    rows = _read_rows(path)

    if not rows:
        print(f"No picks found for season {season}.")
        return 0

    issues: list[tuple[str, str]] = []

    for i, r in enumerate(rows):
        tag      = f"row {i + 2}"   # +2 = 1-based + header
        grade    = r.get("graded_result", "").strip()
        side     = r.get("pick_side", "").strip()
        strength = r.get("pick_strength", "").strip()
        label    = r.get("pick_label", "").strip()

        if not r.get("date", "").strip():
            issues.append((tag, "missing date"))
        if not r.get("game_pk", "").strip():
            issues.append((tag, f"missing game_pk  ({r.get('away_team')} @ {r.get('home_team')})"))

        if grade in ("WIN", "LOSS"):
            if side not in ("NRFI", "YRFI"):
                repair_hint = ""
                for prefix, (ps, pst) in _LABEL_TO_SIDE.items():
                    if label.upper().startswith(prefix):
                        repair_hint = f"  (--repair-csv can fix from label: {label!r})"
                        break
                issues.append((
                    tag,
                    f"graded {grade} but pick_side={side!r} -- expected NRFI/YRFI{repair_hint}"
                ))
            elif strength not in ("LEAN", "STRONG"):
                issues.append((
                    tag,
                    f"graded {grade} but pick_strength={strength!r} -- expected LEAN/STRONG"
                ))

        if grade == "PASS" and side in ("NRFI", "YRFI"):
            issues.append((tag, f"graded_result=PASS but pick_side={side!r} (should be WIN/LOSS)"))

    # Totals reconciliation
    all_bets  = [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")]
    passes    = [r for r in rows if r.get("graded_result") == "PASS"]
    delayed   = [r for r in rows if r.get("graded_result") in ("POSTPONED", "SUSPENDED")]
    ungraded  = [r for r in rows if not r.get("graded_result")]
    accounted = len(all_bets) + len(passes) + len(delayed) + len(ungraded)

    print(f"\nAudit  --  {path}")
    print(f"  Total rows    : {len(rows)}")
    print(f"  Accounted for : {accounted}  "
          f"(bets={len(all_bets)} pass={len(passes)} delayed={len(delayed)} ungraded={len(ungraded)})")
    if accounted != len(rows):
        print(f"  [warn] Totals mismatch: {len(rows) - accounted} rows unaccounted for")
    print(f"  Issues found  : {len(issues)}")

    if issues:
        print()
        for tag, msg in issues:
            print(f"  [{tag}]  {msg}")
    else:
        print("  All rows look clean.")

    if any("--repair-csv" in msg for _, msg in issues):
        print(f"\n  Run --repair-csv to auto-fix recoverable issues.")

    print()
    return len(issues)


def repair_csv(season: int | None = None, dry_run: bool = False) -> None:
    """
    Safely repair corrupted rows in picks CSV.

    Recovers pick_side and pick_strength from pick_label where unambiguous:
      "LEAN NRFI"   -> NRFI / LEAN
      "STRONG NRFI" -> NRFI / STRONG
      "LEAN YRFI"   -> YRFI / LEAN
      "STRONG YRFI" -> YRFI / STRONG

    Only targets WIN/LOSS rows with wrong pick_side or pick_strength.
    Rows that cannot be confidently repaired are left unchanged and reported.
    """
    if season is None:
        season = date_type.today().year

    path = _csv_path(season)
    rows = _read_rows(path)

    if not rows:
        print(f"No picks found for season {season}.")
        return

    repaired_n = skipped_n = ok_n = 0

    for i, r in enumerate(rows):
        tag      = f"row {i + 2}"
        grade    = r.get("graded_result", "").strip()
        side     = r.get("pick_side", "").strip()
        strength = r.get("pick_strength", "").strip()
        label    = r.get("pick_label", "").strip()

        # Only target rows with a grading problem
        needs_repair = (
            grade in ("WIN", "LOSS")
            and (side not in ("NRFI", "YRFI") or strength not in ("LEAN", "STRONG"))
        )

        if not needs_repair:
            ok_n += 1
            continue

        # Attempt recovery from pick_label
        new_side = new_strength = None
        for prefix, (ps, pst) in _LABEL_TO_SIDE.items():
            if label.upper().startswith(prefix):
                new_side, new_strength = ps, pst
                break

        if new_side is None:
            print(f"  [{tag}]  Cannot repair -- label {label!r} is ambiguous; leaving unchanged")
            skipped_n += 1
            continue

        verb = "(dry-run) would set" if dry_run else "Repaired"
        print(
            f"  [{tag}]  {verb}:  pick_side {side!r}->{new_side!r}  "
            f"pick_strength {strength!r}->{new_strength!r}  "
            f"(label={label!r}  grade={grade})"
        )

        if not dry_run:
            r["pick_side"]     = new_side
            r["pick_strength"] = new_strength
        repaired_n += 1

    if not dry_run and repaired_n > 0:
        _write_rows(path, rows)

    mode = "(dry-run) " if dry_run else ""
    print(f"\n  {mode}Repaired {repaired_n} | Skipped (unrecoverable) {skipped_n} | Already OK {ok_n}")
    if repaired_n > 0 and not dry_run:
        print(f"  CSV updated: {path}")
    print()


# ---------------------------------------------------------------------------
# 6. Performance summary
# ---------------------------------------------------------------------------

def _record(bets: list, wins: list) -> str:
    if not bets:
        return "--"
    pct = len(wins) / len(bets) * 100
    return f"{len(wins)}-{len(bets)-len(wins)}  ({pct:.1f}%)"


def _side(r: dict) -> str:
    return r.get("pick_side", "").strip()


def _strength(r: dict) -> str:
    return r.get("pick_strength", "").strip()


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# odds system temporarily disabled -- _roi_line is a stub; use _record directly
def _roi_line(bets: list, wins: list) -> str:
    return _record(bets, wins)


def show_summary(
    season: int | None     = None,
    last_n: int | None     = None,
    date_from: str | None  = None,
    date_to:   str | None  = None,
) -> None:
    """
    Print a performance summary from the picks CSV.

    Filters (applied in order):
      date_from / date_to  - inclusive ISO date range
      last_n               - most recent N graded bets
    """
    if season is None:
        season = date_type.today().year

    path = _csv_path(season)
    rows = _read_rows(path)

    if not rows:
        print(f"No picks logged for season {season}.")
        return

    # Apply date range filters
    if date_from:
        iso_from = _to_iso(date_from)
        rows = [r for r in rows if r["date"] >= iso_from]
    if date_to:
        iso_to = _to_iso(date_to)
        rows = [r for r in rows if r["date"] <= iso_to]

    # Apply --last N filter: most recent N graded bets
    if last_n:
        graded_bets = sorted(
            [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")],
            key=lambda r: r["date"],
            reverse=True,
        )[:last_n]
        cutoff_keys = {(r["date"], r["game_pk"]) for r in graded_bets}
        rows = [r for r in rows if (r["date"], r["game_pk"]) in cutoff_keys]

    # -- Categorize rows ------------------------------------------------------
    all_bets  = [r for r in rows if r.get("graded_result") in ("WIN", "LOSS")]
    wins      = [r for r in all_bets if r["graded_result"] == "WIN"]
    losses    = [r for r in all_bets if r["graded_result"] == "LOSS"]
    passes    = [r for r in rows if r.get("graded_result") == "PASS"]
    postponed = [r for r in rows if r.get("graded_result") in ("POSTPONED", "SUSPENDED")]
    ungraded  = [r for r in rows if not r.get("graded_result")]

    # -- By side --------------------------------------------------------------
    nrfi_bets = [r for r in all_bets if _side(r) == "NRFI"]
    yrfi_bets = [r for r in all_bets if _side(r) == "YRFI"]
    nrfi_wins = [r for r in nrfi_bets if r["graded_result"] == "WIN"]
    yrfi_wins = [r for r in yrfi_bets if r["graded_result"] == "WIN"]

    # -- By strength ----------------------------------------------------------
    lean_bets   = [r for r in all_bets if _strength(r) == "LEAN"]
    strong_bets = [r for r in all_bets if _strength(r) == "STRONG"]
    lean_wins   = [r for r in lean_bets   if r["graded_result"] == "WIN"]
    strong_wins = [r for r in strong_bets if r["graded_result"] == "WIN"]

    # -- Side x Strength cross-tab -----------------------------------------
    ln_bets = [r for r in nrfi_bets if _strength(r) == "LEAN"]
    sn_bets = [r for r in nrfi_bets if _strength(r) == "STRONG"]
    ly_bets = [r for r in yrfi_bets if _strength(r) == "LEAN"]
    sy_bets = [r for r in yrfi_bets if _strength(r) == "STRONG"]
    ln_wins = [r for r in ln_bets if r["graded_result"] == "WIN"]
    sn_wins = [r for r in sn_bets if r["graded_result"] == "WIN"]
    ly_wins = [r for r in ly_bets if r["graded_result"] == "WIN"]
    sy_wins = [r for r in sy_bets if r["graded_result"] == "WIN"]

    # -- Quality breakdown ----------------------------------------------------
    hi_qual_bets = [
        r for r in all_bets
        if all(r.get(q, "avg") not in ("", "avg")
               for q in ["away_pitcher_q", "home_pitcher_q",
                         "away_batting_q", "home_batting_q"])
    ]
    hi_qual_wins = [r for r in hi_qual_bets if r["graded_result"] == "WIN"]

    # Reconciliation check
    unaccounted = len(all_bets) - len(nrfi_bets) - len(yrfi_bets)
    win_rate = len(wins) / len(all_bets) * 100 if all_bets else 0.0

    dates = sorted({r["date"] for r in rows})
    date_range = f"{dates[0]} -> {dates[-1]}" if dates else "no data"

    sep  = "=" * 60
    sep2 = "-" * 60
    print(f"\n{sep}")
    print(f"  PERFORMANCE SUMMARY  --  Season {season}")
    if date_from or date_to or last_n:
        print(f"  Filter : {date_range}")
    print(sep)
    print(f"  Logged games        : {len(rows)}")
    print(f"  Graded bets (W+L)   : {len(all_bets)}")
    print(f"  Wins / Losses       : {len(wins)} / {len(losses)}")
    print(f"  Win rate            : {win_rate:.1f}%")
    print(f"  PASS (no pick)      : {len(passes)}")
    print(f"  Postponed/susp.     : {len(postponed)}")
    print(f"  Ungraded            : {len(ungraded)}")

    # Integrity checks
    total_accounted = len(all_bets) + len(passes) + len(postponed) + len(ungraded)
    if total_accounted != len(rows):
        print(f"  [warn] Row count mismatch: {len(rows)} logged, {total_accounted} accounted for")
    if unaccounted > 0:
        print(f"  [warn] {unaccounted} graded bet(s) have unexpected pick_side -- run --audit-csv")

    print(f"\n{sep2}")
    print(f"  By side:")
    print(f"    NRFI  : {_record(nrfi_bets, nrfi_wins)}")
    print(f"    YRFI  : {_record(yrfi_bets, yrfi_wins)}")

    print(f"\n{sep2}")
    print(f"  By strength:")
    print(f"    LEAN   : {_record(lean_bets,   lean_wins)}")
    print(f"    STRONG : {_record(strong_bets, strong_wins)}")

    print(f"\n{sep2}")
    print(f"  By side + strength:")
    print(f"    LEAN  NRFI  : {_record(ln_bets, ln_wins)}")
    print(f"    STRONG NRFI : {_record(sn_bets, sn_wins)}")
    print(f"    LEAN  YRFI  : {_record(ly_bets, ly_wins)}")
    print(f"    STRONG YRFI : {_record(sy_bets, sy_wins)}")

    if hi_qual_bets:
        print(f"\n{sep2}")
        print(f"  All inputs non-avg (4/4):")
        print(f"    {_record(hi_qual_bets, hi_qual_wins)}")

    print(sep)
    print()

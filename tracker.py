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
import sys
from datetime import datetime, date as date_type
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

    for g in results:
        side   = g["pick_side"]
        conf   = g["pick_conf"]
        lam    = g["lambda_total"]
        ap     = g["away"]
        hp     = g["home"]

        if side == "PASS":
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
                #   - created_at timestamp (so the user can see "this row
                #     was last touched at..." even after lock)
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
                allow_update = {
                    "created_at",
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

    _write_rows(path, rows)
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

    NOTE: MLB's `game` endpoint sometimes lags on postponements -- it can
    keep returning "Scheduled" for hours after MLB has officially called
    a rainout, while the `schedule` endpoint correctly shows "Postponed"
    immediately.  When we get an inconsistent "Scheduled / no innings
    played" response, we double-check via the schedule endpoint and
    promote the postponement signal if we find one there.
    """
    try:
        data = statsapi.get("game", {
            "gamePk": game_pk,
            "fields": (
                "gameData,status,abstractGameState,detailedState,"
                "datetime,officialDate,"
                "liveData,linescore,innings,num,home,away,runs"
            ),
        })
    except Exception as exc:
        return {"state": "ERROR", "detail": str(exc), "away_runs": None, "home_runs": None}

    status = data.get("gameData", {}).get("status", {})
    state  = status.get("abstractGameState", "")
    detail = status.get("detailedState", "")

    innings = (
        data.get("liveData", {})
            .get("linescore", {})
            .get("innings", [])
    )

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

    return {"state": state, "detail": detail, "away_runs": away_r, "home_runs": home_r}


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
        # completed.
        if detail in ("Postponed", "Suspended", "Cancelled"):
            if away_r is not None and home_r is not None:
                # 1st inning was finished -- fall through to normal grading.
                # Don't return; we'll grade the W/L below.
                print(f"{tag}  {detail} but 1st inning complete ({away_r}-{home_r}) -- grading normally")
            else:
                rows[idx]["actual_result"] = detail.upper()
                rows[idx]["graded_result"] = detail.upper()
                rows[idx]["graded_at"]     = now
                print(f"{tag}  {detail} -- marked, not counted as a bet")
                graded_n += 1
                continue

        if away_r is None or home_r is None:
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
                    print(f"{tag}  stale-scheduled ({hours_past:.0f}h past slate) "
                          f"-- marking POSTPONED")
                    graded_n += 1
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

        outcome_tag = {"WIN": "W", "LOSS": "L", "PASS": "-"}.get(graded_result, "?")
        source_tag  = "" if state == "Final" else f" [from {state}]"
        print(
            f"{tag}  1st inn: {away_r}-{home_r} ({actual})  "
            f"pick={pick}  ->  {outcome_tag} {graded_result}{source_tag}"
        )
        graded_n += 1

    _write_rows(path, rows)
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
    """
    row["market_nrfi_odds"] = nrfi_odds
    row["market_yrfi_odds"] = yrfi_odds
    row["sportsbook"]       = sportsbook
    row["odds_captured_at"] = captured_at

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

    # Bet sizing: only bet if pick is NRFI/YRFI and edge meets threshold.
    # units_risked is the WOULD-BE stake -- always populated for any
    # NRFI/YRFI pick (regardless of bet_placed) so the audit trail can
    # compute counterfactual P&L for skipped bets ("how would the
    # min_edge=0.02 strategy have performed if we'd dropped the gate?").
    # PASS picks have no would-be stake; units_risked stays blank.
    if pick in ("NRFI", "YRFI"):
        would_be_units = units_strong if strength == "STRONG" else (
            units_lean if strength == "LEAN" else 0.0
        )
        if edge_pick is not None and edge_pick >= min_edge and would_be_units > 0:
            row["bet_placed"]   = "Y"
            row["units_risked"] = _fmt(would_be_units, 2)
        else:
            row["bet_placed"]   = "N"
            # Preserve the would-be stake so post-mortem analysis can
            # distinguish "would have bet 1u but skipped due to edge"
            # from "PASS pick, never a candidate".
            row["units_risked"] = _fmt(would_be_units, 2) if would_be_units > 0 else ""
    else:
        row["bet_placed"]   = "N"
        row["units_risked"] = ""

    # Compute P&L if graded
    row["profit_loss_units"] = _calc_pnl(row)
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
      Optional columns: sportsbook

    Matching priority:
      1. date + game_pk        (exact)
      2. date + away_team + home_team  (fuzzy fallback)

    After matching, computes:
      implied probs, model edges, bet_placed, units_risked, profit_loss_units

    Example:
      2026-04-05,745459,NYY,BOS,-115,+105,FanDuel
      2026-04-05,,LAD,SF,-110,-110,DraftKings
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

    # Build lookup indexes
    by_pk:   dict[tuple, int] = {}   # (iso_date, str(game_pk)) -> idx
    by_team: dict[tuple, int] = {}   # (iso_date, away, home) -> idx
    for i, r in enumerate(rows):
        d = r["date"]
        by_pk[(d, str(r["game_pk"]))] = i
        by_team[(d, r["away_team"].upper(), r["home_team"].upper())] = i

    now = _now_utc()
    matched_n = unmatched_n = pk_matched_n = team_matched_n = 0
    dates_covered: set[str] = set()

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

            # Attempt match
            idx         = None
            match_method = ""
            if game_pk:
                idx = by_pk.get((iso_date, game_pk))
                if idx is not None:
                    match_method = "pk"
            if idx is None:
                idx = by_team.get((iso_date, away, home))
                if idx is not None:
                    match_method = "teams"

            if idx is None:
                print(f"  [no match] {iso_date}  {away} @ {home}  pk={game_pk or '?'}")
                unmatched_n += 1
                continue

            if match_method == "pk":
                pk_matched_n += 1
            else:
                team_matched_n += 1

            dates_covered.add(iso_date)

            rows[idx] = _apply_odds_to_row(
                rows[idx], nrfi_o, yrfi_o, book, min_edge,
                units_lean, units_strong, now
            )

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

    # Re-read to count remaining odds gaps for dates covered
    if dates_covered:
        updated_rows = _read_rows(picks_path)
        missing_odds = [
            r for r in updated_rows
            if r["date"] in dates_covered and not r.get("market_nrfi_odds")
        ]
    else:
        missing_odds = []

    print(f"\n  Matched {matched_n} ({pk_matched_n} by game_pk, {team_matched_n} by teams)"
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

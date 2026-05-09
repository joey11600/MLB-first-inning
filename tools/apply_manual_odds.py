#!/usr/bin/env python3
"""tools/apply_manual_odds.py

Apply user-supplied DK odds from data/manual_odds_overrides.csv to the
season ledger (data/picks_<season>.csv) AND the matching Supabase row,
recomputing profit_loss_units from the supplied price.

Why this exists.  The system's odds-capture pipeline (Railway worker
running scrape_dk_odds.py + import_odds) periodically goes down or
misses individual games.  When that happens the row gets graded with
empty market_*_odds; tools/end_of_day_check.py later "heals" the
orphan by stamping bet_placed=Y and computing profit_loss_units
against the -110 fallback (=+0.909u for a win) -- as if -110 were
the real price.  Audit on 2026-05-09 found 112 of 220 graded STRONG
bets had used the -110 fallback, including 3 from the trailing 7
days.  This tool gives the operator a way to tell the system the
ACTUAL DK prices they got, retroactively or going forward.

Mechanics.

1. Reads `data/manual_odds_overrides.csv` (skipping header + comment
   lines that begin with `#`).
2. For each override row, finds the matching CSV row in
   picks_<season>.csv by (date, game_pk) when game_pk is supplied,
   otherwise by (date, away_team, home_team).
3. Updates `market_nrfi_odds`, `market_yrfi_odds`, `sportsbook`,
   `odds_captured_at`.  Sportsbook defaults to "DraftKings (manual)"
   when blank in the override -- dashboard shows that label so the
   operator can tell at a glance which rows were hand-priced.
4. Sets `bet_placed=Y` and `units_risked=1` if the row is a STRONG
   NRFI/YRFI pick (and isn't already).  Does NOT touch PASS rows --
   if you want to retroactively flip a PASS to a real bet, use the
   T-V21-2026-05-08c heal pattern instead; this tool only fixes
   prices on bets the model already made.
5. Recomputes `profit_loss_units` via tracker._calc_pnl using the
   freshly-set market_*_odds.
6. Writes a `pick_changes.csv` journal entry for each mutation.
7. Mirrors the changed rows to Supabase via
   tracker._mirror_picks_to_supabase.

Idempotent.  Re-running with the same overrides is a no-op (the row
already has the supplied odds, so neither CSV nor profit_loss_units
changes).  Safe to wire into the predict cron.

Usage.

  # Apply every override in the file (typical):
  python tools/apply_manual_odds.py

  # Just check what would change:
  python tools/apply_manual_odds.py --dry-run

  # Override file lives elsewhere (e.g. you keep a per-season ledger):
  python tools/apply_manual_odds.py --file data/manual_odds_2025.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

DEFAULT_OVERRIDE_FILE = ROOT / "data" / "manual_odds_overrides.csv"
DEFAULT_SPORTSBOOK    = "DraftKings (manual)"


def _norm_odds(s: str) -> str:
    """Normalize an American-odds string.  Empty -> ''.  +110 stays +110.
    Bare '110' becomes '+110'.  '-130' stays '-130'.  Junk stays junk
    (we let _calc_pnl reject it via payout_per_unit returning None)."""
    s = (s or "").strip()
    if not s:
        return ""
    if s[0] in ("+", "-"):
        return s
    try:
        n = int(s)
        return f"+{n}" if n > 0 else str(n)
    except ValueError:
        return s


def _load_overrides(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  [skip] override file not found: {path}", file=sys.stderr)
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = None
        for raw in reader:
            if not raw:
                continue
            first = raw[0].strip()
            if first.startswith("#"):
                continue
            if header is None:
                header = [h.strip() for h in raw]
                continue
            if len(raw) < len(header):
                # Pad short rows
                raw = raw + [""] * (len(header) - len(raw))
            row = dict(zip(header, [c.strip() for c in raw]))
            # Skip empty placeholder rows
            if not row.get("date") or not row.get("away_team") or not row.get("home_team"):
                continue
            out.append(row)
    return out


def _find_pick_row(rows: list[dict], override: dict) -> int | None:
    d = (override.get("date") or "").strip()
    pk = (override.get("game_pk") or "").strip()
    a  = (override.get("away_team") or "").strip().upper()
    h  = (override.get("home_team") or "").strip().upper()

    # Prefer game_pk match (disambiguates doubleheaders)
    if pk:
        for i, r in enumerate(rows):
            if r.get("date") == d and str(r.get("game_pk", "")).strip() == pk:
                return i
    # Fall back to (date, away, home)
    matches = [
        i for i, r in enumerate(rows)
        if r.get("date") == d
        and (r.get("away_team", "") or "").strip().upper() == a
        and (r.get("home_team", "") or "").strip().upper() == h
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Doubleheader case: caller must supply game_pk to disambiguate.
        print(
            f"  [warn] override {d} {a}@{h} matched {len(matches)} rows "
            f"(doubleheader?).  Add `game_pk` to the override to disambiguate.",
            file=sys.stderr,
        )
    return None


def _row_already_at_override(row: dict, override: dict) -> bool:
    """True when the picks row already has the supplied odds (so the
    override is a no-op).  Lets the cron run this every tick safely."""
    o_n = _norm_odds(override.get("market_nrfi_odds", ""))
    o_y = _norm_odds(override.get("market_yrfi_odds", ""))
    cur_n = _norm_odds(row.get("market_nrfi_odds", ""))
    cur_y = _norm_odds(row.get("market_yrfi_odds", ""))
    # Treat blank-in-override as "leave existing alone".
    n_match = (not o_n) or (o_n == cur_n)
    y_match = (not o_y) or (o_y == cur_y)
    return n_match and y_match


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", default=str(DEFAULT_OVERRIDE_FILE),
                   help=f"Path to the overrides CSV (default: {DEFAULT_OVERRIDE_FILE.relative_to(ROOT)})")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change; don't write CSV / Supabase / journal.")
    p.add_argument("--season", type=int, default=None,
                   help="Season override (defaults to year of each override row).")
    args = p.parse_args()

    overrides = _load_overrides(Path(args.file))
    if not overrides:
        print(f"No overrides to apply.  ({args.file})")
        return 0

    print(f"Found {len(overrides)} override row(s) in {args.file}")

    # Group overrides by season -- one CSV per season
    by_season: dict[int, list[dict]] = {}
    for o in overrides:
        d = o.get("date", "")
        try:
            yr = args.season or int(d[:4])
        except ValueError:
            print(f"  [skip] override has unparseable date: {o!r}", file=sys.stderr)
            continue
        by_season.setdefault(yr, []).append(o)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_changed_indices = 0
    grand_no_op = 0

    for season, season_overrides in by_season.items():
        csv_path = tracker._csv_path(season)
        if not csv_path.exists():
            print(f"  [skip] season {season}: ledger not found at {csv_path}",
                  file=sys.stderr)
            continue
        rows = tracker._read_rows(csv_path)
        changed_indices: list[int] = []
        no_op = 0

        for o in season_overrides:
            d = o.get("date", "")
            a = o.get("away_team", "").upper()
            h = o.get("home_team", "").upper()
            tag = f"  {d} {a}@{h}"

            idx = _find_pick_row(rows, o)
            if idx is None:
                print(f"{tag}: NO MATCH in ledger -- skipping")
                continue
            row = rows[idx]

            if _row_already_at_override(row, o):
                no_op += 1
                continue  # idempotent: nothing to do

            old_label = (row.get("pick_label") or "").strip()
            old_pl    = (row.get("profit_loss_units") or "").strip()
            o_n = _norm_odds(o.get("market_nrfi_odds", ""))
            o_y = _norm_odds(o.get("market_yrfi_odds", ""))
            book = (o.get("sportsbook") or "").strip() or DEFAULT_SPORTSBOOK
            note = (o.get("note") or "").strip()

            # Apply odds (only the supplied side(s); blanks leave existing alone)
            if o_n:
                row["market_nrfi_odds"] = o_n
            if o_y:
                row["market_yrfi_odds"] = o_y
            row["sportsbook"]       = book
            row["odds_captured_at"] = now

            # Heal bet_placed for STRONG NRFI/YRFI rows.  Only flip
            # to Y when it wasn't already a real bet -- keeps PASS
            # rows untouched (those need the dedicated heal path).
            ps   = (row.get("pick_strength") or "").strip().upper()
            side = (row.get("pick_side")     or "").strip().upper()
            if ps == "STRONG" and side in ("NRFI", "YRFI"):
                if (row.get("bet_placed") or "").strip().upper() != "Y":
                    row["bet_placed"]   = "Y"
                if not (row.get("units_risked") or "").strip():
                    row["units_risked"] = "1"

            # Recompute P&L from the new odds when the row is graded.
            if (row.get("graded_result") or "").strip().upper() in ("WIN", "LOSS"):
                row["profit_loss_units"] = tracker._calc_pnl(row)

            new_pl = (row.get("profit_loss_units") or "").strip()
            print(
                f"{tag}: {row.get('pick_label')!r}  "
                f"odds {o_n or '-'}/{o_y or '-'}  "
                f"P&L {old_pl or '-'} -> {new_pl or '-'}"
                + (f"  ({note})" if note else "")
            )

            if not args.dry_run:
                # Journal the override
                try:
                    tracker._record_pick_change(
                        iso_date    = d,
                        game_pk     = str(row.get("game_pk") or ""),
                        away_team   = a,
                        home_team   = h,
                        game_time   = (row.get("game_time_et") or ""),
                        old_label   = old_label,
                        new_label   = (
                            f"{old_label} · MANUAL ODDS OVERRIDE "
                            f"({o_n or 'n/a'}/{o_y or 'n/a'})"
                        ),
                        captured_at = now,
                    )
                except Exception as exc:    # noqa: BLE001 -- journal advisory
                    print(f"{tag}: journal write failed: {exc!r}", file=sys.stderr)

            changed_indices.append(idx)

        if not changed_indices:
            print(f"  Season {season}: {no_op} override(s) already applied (no-op)")
            grand_no_op += no_op
            continue

        if args.dry_run:
            print(f"  [dry-run] season {season}: would heal {len(changed_indices)} row(s)")
            continue

        tracker._write_rows(csv_path, rows)
        try:
            tracker._mirror_picks_to_supabase(season, [rows[i] for i in changed_indices])
        except Exception as exc:    # noqa: BLE001
            print(f"  [warn] season {season}: Supabase mirror failed: {exc!r}",
                  file=sys.stderr)

        print(f"  Season {season}: healed {len(changed_indices)} row(s)  "
              f"({no_op} already-applied no-ops)")
        total_changed_indices += len(changed_indices)
        grand_no_op += no_op

    print()
    print(f"TOTAL: healed {total_changed_indices} row(s)  ({grand_no_op} no-ops)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

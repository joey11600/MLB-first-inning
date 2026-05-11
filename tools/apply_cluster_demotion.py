#!/usr/bin/env python3
"""tools/apply_cluster_demotion.py

Skip bet placement on STRONG rows that match a confirmed loss
cluster.  Reads `data/cluster_demotions.json` (operator-maintained)
and sets `bet_placed='N' + units_risked=''` on every matching
ungraded row.  Does NOT change `pick_side / pick_strength /
pick_label` -- the model's verdict stays visible on the dashboard
for transparency; only the money commit is suppressed.

Pipeline (3-stage):
  1. cluster_discovery.py    -- surface candidate clusters from data
  2. loss_cluster_monitor.py -- watch a candidate; fire Telegram if
                                its recent-N record crosses threshold
  3. THIS TOOL               -- apply demotion for confirmed clusters

Operator workflow:
  - Review cluster_discovery.py output.  Cluster looks bad?  Add a
    match predicate to loss_cluster_monitor.py CLUSTERS list.
  - Wait until the monitor's recent-N alert fires (i.e., the cluster
    has actually crossed the bad-performance threshold in real time).
  - If you want to act on the alert, add a demotion entry to
    `data/cluster_demotions.json`.  Future cron ticks will skip bet
    placement on rows in that cluster automatically.
  - Reversible: remove the entry from the JSON and bets resume.

Idempotent.  A row already at bet_placed='N' stays at bet_placed='N';
a row that was matched + demoted on a previous tick won't re-journal.

Safety:
  - PASS rows are never touched (no bet to demote).
  - Already-graded rows (W/L/PASS terminal) are never touched;
    you cannot retroactively un-bet a settled play.
  - bet_placed='Y' rows ARE touched (set to 'N') ONLY if the row
    doesn't yet have a profit_loss_units value (i.e., the bet was
    auto-committed but hasn't graded).  Defensive guard: a placed
    + graded bet stays placed + graded.

Usage:
  python tools/apply_cluster_demotion.py             # apply all active
  python tools/apply_cluster_demotion.py --dry-run   # report only
  python tools/apply_cluster_demotion.py --date 2026-05-10  # specific slate
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")
DEMOTIONS_FILE = ROOT / "data" / "cluster_demotions.json"


# --------------------------------------------------------------------------
# Predicate evaluation
# --------------------------------------------------------------------------

def _safe_float(v) -> float | None:
    if v is None:
        return None
    s = (v or "").strip() if isinstance(v, str) else v
    if s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _pitcher_min_q(away_q: str, home_q: str) -> str:
    order = {"sm": 0, "ltd": 1, "live": 2, "avg": -1}
    a = order.get((away_q or "").lower(), -1)
    h = order.get((home_q or "").lower(), -1)
    rev = {v: k for k, v in order.items()}
    return rev.get(min(a, h), "avg")


def _matches(row: dict, dem: dict) -> bool:
    """Apply the demotion's predicate to a row.  Returns True iff every
    declared condition is satisfied (None / missing = no constraint)."""
    side_req = dem.get("side")
    if side_req:
        if (row.get("pick_side") or "").upper() != side_req.upper():
            return False

    p   = _safe_float(row.get("nrfi_prob"))
    lam = _safe_float(row.get("combined_lambda"))
    park = _safe_float(row.get("park_factor"))

    nrfi_band = dem.get("nrfi_prob") or {}
    if nrfi_band.get("min") is not None and (p is None or p < nrfi_band["min"]):
        return False
    if nrfi_band.get("max") is not None and (p is None or p > nrfi_band["max"]):
        return False

    lam_band = dem.get("combined_lambda") or {}
    if lam_band.get("min") is not None and (lam is None or lam < lam_band["min"]):
        return False
    if lam_band.get("max") is not None and (lam is None or lam > lam_band["max"]):
        return False

    park_band = dem.get("park_factor") or {}
    if park_band.get("min") is not None and (park is None or park < park_band["min"]):
        return False
    if park_band.get("max") is not None and (park is None or park > park_band["max"]):
        return False

    pq_req = dem.get("pitcher_quality_min")
    if pq_req:
        pq = _pitcher_min_q(row.get("away_pitcher_q"), row.get("home_pitcher_q"))
        if pq not in pq_req:
            return False

    return True


# Magic prefix on pick_label that marks a row as already cluster-demoted.
# Used as the canonical "is this row demoted?" signal so that re-running
# the demotion on a row whose `bet_placed=N` was preserved by the
# predictor's preserve list (but whose `pick_side` got regenerated back
# to STRONG NRFI/YRFI) still recognises it as demoted -- avoiding a fresh
# journal entry every cron tick.
DEMOTION_LABEL_PREFIX = "PASS - Cluster demotion"


def _row_already_demoted(row: dict) -> bool:
    """True when the row is already in the demoted state.

    A row is considered "already demoted" if either:
      (a) Its pick_label still carries the DEMOTION_LABEL_PREFIX
          (predictor hasn't regenerated since last demotion), OR
      (b) bet_placed='N' + no units + no P&L (the historical signature
          from the v1 demotion logic that only flipped bet_placed -- kept
          for backward compatibility with rows demoted before the
          pick-side rewrite landed).
    """
    label = (row.get("pick_label") or "").strip()
    if label.startswith(DEMOTION_LABEL_PREFIX):
        return True
    bp = (row.get("bet_placed") or "").strip().upper()
    units = (row.get("units_risked") or "").strip()
    pl = (row.get("profit_loss_units") or "").strip()
    return bp == "N" and not units and not pl


def _row_demotable(row: dict) -> bool:
    """True if a row is in a state that can be safely demoted: a STRONG
    NRFI/YRFI verdict that hasn't graded yet AND has no real P&L
    recorded.  Per the docstring, we explicitly leave WIN/LOSS/PASS rows
    alone -- you can't un-bet a settled play.

    Also returns True when the predictor has regenerated a previously
    demoted row's pick_side back to STRONG NRFI/YRFI (because pick_side
    isn't in the preserve list).  In that case bet_placed='N' is still
    preserved, and we re-apply the demoted display state but skip the
    journal write (see `_row_already_demoted`).
    """
    if (row.get("pick_strength") or "").upper() != "STRONG":
        return False
    if (row.get("pick_side") or "").upper() not in ("NRFI", "YRFI"):
        return False
    grade = (row.get("graded_result") or "").upper()
    if grade in ("WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED"):
        return False
    pl = (row.get("profit_loss_units") or "").strip()
    if pl:
        return False
    return True


def _parse_demoted_label(label: str) -> tuple[str, str] | None:
    """Inverse of `_demoted_pick_label`.  Returns (orig_strength, orig_side)
    from a label like
        "PASS - Cluster demotion: STRONG YRFI (thin_pitcher_strong_v1)"
    Returns None when the label doesn't match the expected shape.
    """
    if not label.startswith(DEMOTION_LABEL_PREFIX):
        return None
    after = label[len(DEMOTION_LABEL_PREFIX):]
    # Expected: ": STRONG YRFI (<id>)"
    if not after.startswith(": "):
        return None
    rest = after[2:]
    # Strip trailing "(<id>)" if present
    paren = rest.rfind(" (")
    if paren >= 0:
        rest = rest[:paren]
    parts = rest.strip().split()
    if len(parts) != 2:
        return None
    return parts[0].upper(), parts[1].upper()


def _demoted_pick_label(dem_id: str, orig_strength: str, orig_side: str) -> str:
    """Encode the original verdict + demotion id in pick_label so the
    dashboard tooltip and `cluster_shadow_pnl.py` can recover both
    without needing extra CSV columns.  Format:

      "PASS - Cluster demotion: STRONG YRFI (thin_pitcher_strong_v1)"

    The DEMOTION_LABEL_PREFIX-startswith check is the canonical
    "is this row demoted?" test elsewhere in the system.
    """
    return f"{DEMOTION_LABEL_PREFIX}: {orig_strength} {orig_side} ({dem_id})"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", default=str(DEMOTIONS_FILE),
                   help=f"Demotion JSON path (default: {DEMOTIONS_FILE.relative_to(ROOT)})")
    p.add_argument("--date", help="ISO ET slate date; default = today.")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change; don't write CSV / Supabase / journal.")
    p.add_argument("--season", type=int, default=None,
                   help="Season override (defaults to year of --date).")
    args = p.parse_args()

    fpath = Path(args.file)
    if not fpath.exists():
        print(f"  [skip] demotions file not found: {fpath}")
        return 0
    try:
        cfg = json.loads(fpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: cannot parse {fpath}: {exc}", file=sys.stderr)
        return 2
    demotions = [d for d in (cfg.get("demotions") or []) if d.get("active") is not False]
    if not demotions:
        print(f"No active demotions in {fpath.name}.  Nothing to apply.")
        return 0

    target_date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    season = args.season or int(target_date[:4])
    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(csv_path)
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    target_rows = [(i, r) for i, r in enumerate(rows) if r.get("date") == target_date]
    print(f"apply_cluster_demotion  date={target_date}  active_demotions={len(demotions)}  "
          f"slate_rows={len(target_rows)}  dry_run={args.dry_run}")

    demoted_indices: list[int] = []
    would_demote_count = 0    # dry-run uses this since indices stay empty
    no_op_count = 0

    for idx, row in target_rows:
        if not _row_demotable(row):
            continue
        # Find the first matching demotion (rows match by ANY active rule)
        match = None
        for dem in demotions:
            if _matches(row, dem):
                match = dem
                break
        if match is None:
            continue

        away = (row.get("away_team") or "").upper()
        home = (row.get("home_team") or "").upper()
        old_label     = (row.get("pick_label") or "").strip()
        old_strength  = (row.get("pick_strength") or "").strip().upper()
        old_side      = (row.get("pick_side") or "").strip().upper()
        old_bp        = (row.get("bet_placed") or "").strip().upper()
        old_units     = (row.get("units_risked") or "").strip()

        already_demoted = _row_already_demoted(row)
        if already_demoted:
            # Predictor likely just regenerated pick_side/strength/label
            # back to STRONG NRFI/YRFI on its pre-lock refresh (those
            # fields aren't in the preserve list).  bet_placed='N' was
            # preserved though.  Re-apply the demoted display state so
            # the dashboard never shows STRONG between cron ticks, but
            # SKIP the journal write -- one journal entry per row total,
            # not 24 per day.
            no_op_count += 1

        verb = "WOULD DEMOTE" if args.dry_run else ("RE-APPLY" if already_demoted else "DEMOTE")
        # Recover the "true" original verdict when this is a re-apply.
        # If the label already encodes "STRONG YRFI" from a previous
        # demotion, use that; otherwise read the live pick_side/strength.
        if already_demoted and old_label.startswith(DEMOTION_LABEL_PREFIX):
            orig_strength, orig_side = _parse_demoted_label(old_label) or (old_strength, old_side)
        else:
            orig_strength, orig_side = old_strength, old_side

        new_pick_label = _demoted_pick_label(match.get("id", "unknown"),
                                             orig_strength, orig_side)
        print(f"  {verb}  {target_date} {away}@{home}  {old_label!r}  "
              f"->  {new_pick_label!r}  "
              f"reason: [{match.get('id','unknown')}] {match.get('reason','-')}")

        would_demote_count += 1
        if args.dry_run:
            continue

        rows[idx]["pick_side"]     = "PASS"
        rows[idx]["pick_strength"] = "NO EDGE"
        rows[idx]["pick_label"]    = new_pick_label
        rows[idx]["bet_placed"]    = "N"
        rows[idx]["units_risked"]  = ""
        # Don't touch profit_loss_units; _calc_pnl already returns 0 for
        # bet_placed=N (so the official record stays clean).
        demoted_indices.append(idx)

        # Journal only on TRANSITION (first demote of this row).  When
        # the predictor regenerates STRONG every tick we'd otherwise
        # write 24 identical entries per day.
        if already_demoted:
            continue
        try:
            tracker._record_pick_change(
                iso_date    = target_date,
                game_pk     = str(row.get("game_pk") or ""),
                away_team   = away,
                home_team   = home,
                game_time   = (row.get("game_time_et") or ""),
                old_label   = old_label,
                new_label   = new_pick_label,
                captured_at = now,
            )
        except Exception as exc:    # noqa: BLE001 -- journal advisory
            print(f"  [warn] journal write failed: {exc!r}", file=sys.stderr)

    if args.dry_run:
        if would_demote_count:
            print(f"\n[dry-run] Would demote {would_demote_count} row(s).")
        elif no_op_count:
            print(f"\n[dry-run] No new demotions ({no_op_count} already demoted).")
        else:
            print("\n[dry-run] No matching rows on this slate.")
        return 0

    if not demoted_indices:
        if no_op_count:
            print(f"  No new demotions ({no_op_count} already demoted).")
        else:
            print("  No matching rows on this slate.")
        return 0

    tracker._write_rows(csv_path, rows)
    try:
        tracker._mirror_picks_to_supabase(season, [rows[i] for i in demoted_indices])
    except Exception as exc:    # noqa: BLE001
        print(f"  [warn] Supabase mirror failed: {exc!r}", file=sys.stderr)

    print(f"\nDemoted {len(demoted_indices)} row(s) on {target_date}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

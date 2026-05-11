#!/usr/bin/env python3
"""tools/cluster_shadow_pnl.py

Evaluate the impact of each entry in `data/cluster_demotions.json` by
showing:

  REAL    -- STRONG bets the system DID place that match the cluster
             predicate (only happens for rows where bet_placed=Y was
             stamped before the demotion went live).
  SHADOW  -- STRONG rows the demotion SKIPPED (bet_placed='N').
             Computes the hypothetical P&L: 1u flat per row, paid at
             the captured market odds if available, otherwise -110.
  TOTAL   -- Real + Shadow.  This is "what we would have done if the
             demotion didn't exist" -- the counterfactual.

Use this when deciding whether to keep a demotion `active=true` or
flip it to `false`:

  Shadow trending positive over many graded skips (e.g. 5W-2L+):
    we overcorrected -- the cluster reverted, turn the demotion off.
  Shadow trending negative (e.g. 2W-5L+):
    the cluster is real -- keep the demotion on.
  Shadow ~ break-even:
    inconclusive, wait for more data.

Pipeline (3-stage):
  1. cluster_discovery.py    -- surface candidates from data
  2. loss_cluster_monitor.py -- alert when recent-N crosses threshold
  3. apply_cluster_demotion.py + THIS TOOL -- act + evaluate

Usage:
  python tools/cluster_shadow_pnl.py                # all demotions
  python tools/cluster_shadow_pnl.py --since 2026-05-10
  python tools/cluster_shadow_pnl.py --id thin_pitcher_strong_v1
  python tools/cluster_shadow_pnl.py --include-inactive
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from tools.apply_cluster_demotion import (  # noqa: E402
    DEMOTION_LABEL_PREFIX,
    _matches,
    _parse_demoted_label,
)

DEMOTIONS_FILE = ROOT / "data" / "cluster_demotions.json"


def _safe_float(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _effective_side(row: dict) -> str:
    """Return the side that should be used for shadow-P&L bookkeeping.

    For real bets (bet_placed=Y, pick_side in NRFI/YRFI) this is just
    pick_side.  For cluster-demoted rows (pick_side=PASS, label encodes
    "PASS - Cluster demotion: STRONG YRFI (...)") we recover the
    original side from the label so we can compute "would have won"
    even though graded_result is now PASS.
    """
    side = (row.get("pick_side") or "").upper()
    if side in ("NRFI", "YRFI"):
        return side
    parsed = _parse_demoted_label((row.get("pick_label") or "").strip())
    if parsed:
        return parsed[1]
    return ""


def _shadow_grade(row: dict, eff_side: str) -> str:
    """Derive WIN/LOSS for shadow rows by comparing the row's
    `actual_result` (NRFI/YRFI/POSTPONED) to the effective side.

    Real-bet rows still come in here -- in those cases graded_result is
    already WIN/LOSS so we just return that.  For demoted-PASS rows
    graded_result is PASS (because pick_side=PASS); we fall back to
    actual_result.
    """
    grade = (row.get("graded_result") or "").upper()
    if grade in ("WIN", "LOSS"):
        return grade
    actual = (row.get("actual_result") or "").upper()
    if actual in ("NRFI", "YRFI") and eff_side in ("NRFI", "YRFI"):
        return "WIN" if actual == eff_side else "LOSS"
    return ""    # POSTPONED / SUSPENDED / not graded


def _shadow_pnl_for_row(row: dict, eff_side: str, grade: str) -> float:
    """Hypothetical 1u-flat P&L assuming we had taken this bet.

    Prefers opened_*_odds (captured at first scrape -- closest to the
    price we'd have bet at when the model first said STRONG) over
    market_*_odds (latest scrape, closer to the close).  Falls back to
    flat -110 when neither is available.
    """
    if grade == "LOSS":
        return -1.0
    if grade != "WIN":
        return 0.0
    odds_col_opened = "opened_nrfi_odds" if eff_side == "NRFI" else "opened_yrfi_odds"
    odds_col_market = "market_nrfi_odds" if eff_side == "NRFI" else "market_yrfi_odds"
    ppu = (tracker.payout_per_unit(row.get(odds_col_opened, ""))
           or tracker.payout_per_unit(row.get(odds_col_market, "")))
    if ppu is None:
        ppu = 100.0 / 110.0
    return ppu


def _is_demoted_row(row: dict) -> bool:
    return (row.get("pick_label") or "").strip().startswith(DEMOTION_LABEL_PREFIX)


def _eval_demotion(dem: dict, rows: list[dict], since_iso: str | None) -> None:
    cid = dem.get("id", "?")
    print(f"\n=== {cid} ===  active={dem.get('active', True)}")
    if dem.get("reason"):
        print(f"    reason: {dem['reason']}")

    real_bets   = []   # bet_placed=Y rows where the picked side matches predicate
    shadow_bets = []   # cluster-demoted rows whose ORIGINAL verdict matches predicate
    for r in rows:
        if since_iso and (r.get("date") or "") < since_iso:
            continue

        # Determine the effective verdict for predicate matching.  For a
        # demoted row we run the predicate against a synthetic copy that
        # has pick_side restored to the original (otherwise the predicate
        # would never match -- pick_side is now "PASS").
        eff_side = _effective_side(r)
        if eff_side not in ("NRFI", "YRFI"):
            continue

        demoted = _is_demoted_row(r)
        # For predicate matching: substitute pick_side back to original
        # so demotions that filter by side or use pitcher quality still match.
        candidate = dict(r)
        if demoted:
            candidate["pick_side"] = eff_side
            parsed = _parse_demoted_label(r.get("pick_label", "")) or ("STRONG", eff_side)
            candidate["pick_strength"] = parsed[0]
        else:
            if (r.get("pick_strength") or "").upper() != "STRONG":
                continue

        if not _matches(candidate, dem):
            continue

        grade = _shadow_grade(r, eff_side)
        if grade not in ("WIN", "LOSS"):
            continue   # POSTPONED / not graded -- not yet a data point

        row_with_grade = dict(r)
        row_with_grade["_eff_side"] = eff_side
        row_with_grade["_shadow_grade"] = grade

        if demoted:
            shadow_bets.append(row_with_grade)
        else:
            # Real bet that pre-dates the demotion (already settled with
            # bet_placed=Y at real odds).
            real_bets.append(row_with_grade)

    def _summarize(bucket: list[dict], label: str, *, hypothetical: bool) -> tuple[int, int, float]:
        wins = sum(1 for r in bucket if r["_shadow_grade"] == "WIN")
        losses = len(bucket) - wins
        if hypothetical:
            pnl = sum(_shadow_pnl_for_row(r, r["_eff_side"], r["_shadow_grade"])
                      for r in bucket)
        else:
            pnl = sum(_safe_float(r.get("profit_loss_units")) for r in bucket)
        hit = (wins / len(bucket) * 100.0) if bucket else 0.0
        print(f"    {label:<8}  n={len(bucket):>3}   {wins}W-{losses}L   "
              f"hit={hit:>4.0f}%   P&L={pnl:+.3f}u")
        return wins, losses, pnl

    print(f"    --- counts (graded only) ---")
    rw, rl, rpnl = _summarize(real_bets,   "REAL",    hypothetical=False)
    sw, sl, spnl = _summarize(shadow_bets, "SHADOW",  hypothetical=True)
    tw, tl, tpnl = rw + sw, rl + sl, rpnl + spnl
    n_total = len(real_bets) + len(shadow_bets)
    hit_total = (tw / n_total * 100.0) if n_total else 0.0
    print(f"    {'TOTAL':<8}  n={n_total:>3}   {tw}W-{tl}L   "
          f"hit={hit_total:>4.0f}%   P&L={tpnl:+.3f}u   "
          f"(this is what we'd have done WITHOUT the demotion)")

    if shadow_bets:
        print(f"    --- shadow trail (most recent first) ---")
        for r in sorted(shadow_bets, key=lambda x: x.get("date", ""), reverse=True)[:10]:
            side = r["_eff_side"]
            grade = r["_shadow_grade"]
            sp = _shadow_pnl_for_row(r, side, grade)
            odds_col = "opened_nrfi_odds" if side == "NRFI" else "opened_yrfi_odds"
            odds_val = (r.get(odds_col) or "").strip()
            if not odds_val:
                odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
                odds_val = (r.get(odds_col) or "-").strip() or "-"
            paw = (r.get("away_pitcher_q") or "?")[:4]
            phw = (r.get("home_pitcher_q") or "?")[:4]
            print(f"      {r.get('date','?'):<11} "
                  f"{(r.get('away_team') or '').upper():>3}@{(r.get('home_team') or '').upper():<3}  "
                  f"{side:<4}  q=({paw}/{phw})  odds={odds_val:>5}  "
                  f"{grade:<4}  shadow={sp:+.3f}u")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", default=str(DEMOTIONS_FILE))
    p.add_argument("--since", help="ISO date YYYY-MM-DD; skip earlier rows.")
    p.add_argument("--id", help="Only evaluate the demotion with this id.")
    p.add_argument("--include-inactive", action="store_true",
                   help="Also evaluate demotions where active=false.")
    p.add_argument("--season", type=int, default=None,
                   help="Season override (defaults to current ET year).")
    args = p.parse_args()

    fp = Path(args.file)
    if not fp.exists():
        print(f"ERROR: demotions file not found: {fp}", file=sys.stderr)
        return 2
    cfg = json.loads(fp.read_text(encoding="utf-8"))
    demotions = cfg.get("demotions") or []
    if args.id:
        demotions = [d for d in demotions if d.get("id") == args.id]
    if not args.include_inactive:
        demotions = [d for d in demotions if d.get("active") is not False]
    if not demotions:
        print("No matching demotions to evaluate.")
        return 0

    from datetime import datetime
    from zoneinfo import ZoneInfo
    season = args.season or datetime.now(ZoneInfo("America/New_York")).year
    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(csv_path)

    since = args.since or "(season start)"
    print(f"cluster_shadow_pnl  season={season}  since={since}  "
          f"demotions={len(demotions)}  ledger_rows={len(rows)}")
    for dem in demotions:
        _eval_demotion(dem, rows, args.since)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
from tools.apply_cluster_demotion import _matches  # noqa: E402

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


def _shadow_pnl_for_row(row: dict) -> float:
    """Hypothetical 1u-flat P&L assuming we had taken this bet.

    WIN  → +ppu (where ppu is profit-per-unit at captured odds, else
           0.9091 for flat -110)
    LOSS → -1.0
    """
    grade = (row.get("graded_result") or "").upper()
    if grade == "LOSS":
        return -1.0
    if grade != "WIN":
        return 0.0
    side = (row.get("pick_side") or "").upper()
    odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    ppu = tracker.payout_per_unit(row.get(odds_col, ""))
    if ppu is None:
        ppu = 100.0 / 110.0
    return ppu


def _eval_demotion(dem: dict, rows: list[dict], since_iso: str | None) -> None:
    cid = dem.get("id", "?")
    print(f"\n=== {cid} ===  active={dem.get('active', True)}")
    if dem.get("reason"):
        print(f"    reason: {dem['reason']}")

    real_bets   = []   # bet_placed=Y, matches predicate (cluster slipped past gate)
    shadow_bets = []   # bet_placed=N, matches predicate (cluster caught it)
    for r in rows:
        if since_iso and (r.get("date") or "") < since_iso:
            continue
        if (r.get("pick_strength") or "").upper() != "STRONG":
            continue
        if (r.get("pick_side") or "").upper() not in ("NRFI", "YRFI"):
            continue
        if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"):
            continue
        if not _matches(r, dem):
            continue
        bp = (r.get("bet_placed") or "").upper()
        if bp == "Y":
            real_bets.append(r)
        elif bp == "N":
            shadow_bets.append(r)

    def _summarize(bucket: list[dict], label: str, *, hypothetical: bool) -> tuple[int, int, float]:
        wins = sum(1 for r in bucket if (r.get("graded_result") or "").upper() == "WIN")
        losses = len(bucket) - wins
        if hypothetical:
            pnl = sum(_shadow_pnl_for_row(r) for r in bucket)
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
            sp = _shadow_pnl_for_row(r)
            grade = (r.get("graded_result") or "").upper()
            side = (r.get("pick_side") or "").upper()
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

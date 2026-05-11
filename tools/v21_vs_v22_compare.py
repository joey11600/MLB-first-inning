#!/usr/bin/env python3
"""tools/v21_vs_v22_compare.py

Compare V2.1 shadow predictions to V2.2 (live) results.  Runs on the
grade cron; reports per-day W-L + P&L for both versions and fires a
Telegram alert if V2.2 underperforms V2.1 by 3u+ over the last 30
graded STRONG picks.

The shadow column scheme (filled by `tools/v21_shadow_predict.py`):
    v21_shadow_nrfi_prob       calibrated P(NRFI) under V2.1
    v21_shadow_pick_side       NRFI | YRFI | PASS
    v21_shadow_pick_strength   STRONG | NO EDGE

For each graded row we infer:
- V2.2 actual outcome from `graded_result` + `profit_loss_units`.
- V2.1 hypothetical outcome: same actual_result (the game played out
  the same way regardless of which model bet on it); +0.91u flat at
  -110 for hypothetical wins (or use captured market odds if
  available); -1.0u flat for losses; 0u for PASS.

Why -110 fallback in shadow: we don't know what odds V2.1 *would* have
bet at (its bet timing might have differed if it ever fired STRONG).
Flat -110 is the conservative, consistent baseline.

Usage:
  python tools/v21_vs_v22_compare.py             # since shadow start
  python tools/v21_vs_v22_compare.py --since 2026-05-12
  python tools/v21_vs_v22_compare.py --dry-run   # no Telegram
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")

# Telegram alert threshold: V2.2 underperforming V2.1 by this much over
# the last N graded STRONG picks fires a Telegram.
ALERT_DELTA_U = -3.0
ALERT_MIN_N   = 30


def _safe_float(s) -> float:
    try:
        return float((s or "").strip())
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _v22_pl(row: dict) -> float:
    """V2.2's real P&L on this row.  Demoted rows count as 0
    (we didn't actually place the bet)."""
    grade = (row.get("graded_result") or "").upper()
    if grade not in ("WIN", "LOSS"):
        return 0.0
    return _safe_float(row.get("profit_loss_units"))


def _v21_pl(row: dict) -> tuple[float, str]:
    """V2.1 hypothetical P&L assuming flat 1u at -110 on STRONG bets.

    Returns (pl, verdict_label) where verdict_label is one of:
      'WIN', 'LOSS', 'PASS', 'POSTPONED'.  pl is 0.0 for PASS/POSTPONED.
    """
    side = (row.get("v21_shadow_pick_side") or "").upper()
    strength = (row.get("v21_shadow_pick_strength") or "").upper()
    actual = (row.get("actual_result") or "").upper()
    if strength != "STRONG" or side not in ("NRFI", "YRFI"):
        return 0.0, "PASS"
    if actual not in ("NRFI", "YRFI"):
        return 0.0, "POSTPONED"
    if actual == side:
        # Prefer captured opened odds (matches real-bet semantics); fall
        # back to flat -110.
        odds_col = "opened_nrfi_odds" if side == "NRFI" else "opened_yrfi_odds"
        ppu = tracker.payout_per_unit(row.get(odds_col, ""))
        if ppu is None:
            ppu = 100.0 / 110.0
        return ppu, "WIN"
    return -1.0, "LOSS"


def _aggregate(rows: list[dict]) -> dict:
    """Counts + P&L for both V2.1 (shadow) and V2.2 (real).  Operates
    only on rows where v2.1 shadow has been stamped AND actual_result
    is settled."""
    v22_w = v22_l = 0
    v22_pl = 0.0
    v21_w = v21_l = 0
    v21_pl = 0.0
    for r in rows:
        if (r.get("v21_shadow_pick_strength") or "").strip() == "":
            continue   # no shadow data
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue   # POSTPONED / not graded

        # V2.2 side  (use the row's actual pick OR if cluster-demoted,
        # the original verdict before demotion -- we want to compare
        # what V2.2 INTENDED, since the demotion is a policy layer).
        v22_side = (r.get("pick_side") or "").upper()
        v22_strength = (r.get("pick_strength") or "").upper()
        if v22_strength != "STRONG":
            # Demoted from STRONG?
            label = r.get("pick_label", "")
            if "CLUSTER DEMOTED" in label:
                import re
                m = re.search(r"STRONG (NRFI|YRFI)", label)
                if m:
                    v22_side = m.group(1)
                    v22_strength = "STRONG"
        if v22_strength == "STRONG" and v22_side in ("NRFI", "YRFI"):
            # Real V2.2 P&L (already includes demotion->0 if applicable)
            pl = _v22_pl(r)
            v22_pl += pl
            if pl > 0:
                v22_w += 1
            elif pl < 0:
                v22_l += 1
            # Note: pl == 0 with bet_placed=N (demoted) counts as
            # neither W nor L for the policy-effective record but still
            # tells us V2.2 intended STRONG.  We treat demoted as PASS
            # for accounting because we didn't bet.

        # V2.1 shadow side
        v21_side = (r.get("v21_shadow_pick_side") or "").upper()
        v21_strength = (r.get("v21_shadow_pick_strength") or "").upper()
        if v21_strength == "STRONG" and v21_side in ("NRFI", "YRFI"):
            pl, verdict = _v21_pl(r)
            v21_pl += pl
            if verdict == "WIN":
                v21_w += 1
            elif verdict == "LOSS":
                v21_l += 1

    return {
        "v22_w": v22_w, "v22_l": v22_l, "v22_pl": v22_pl,
        "v21_w": v21_w, "v21_l": v21_l, "v21_pl": v21_pl,
    }


def _format_summary(label: str, agg: dict) -> str:
    v22_n = agg["v22_w"] + agg["v22_l"]
    v21_n = agg["v21_w"] + agg["v21_l"]
    return (
        f"{label}\n"
        f"  V2.2 (live):    {agg['v22_w']}W-{agg['v22_l']}L  "
        f"({agg['v22_w']*100/v22_n if v22_n else 0:.0f}%)  "
        f"P&L = {agg['v22_pl']:+.3f}u\n"
        f"  V2.1 (shadow):  {agg['v21_w']}W-{agg['v21_l']}L  "
        f"({agg['v21_w']*100/v21_n if v21_n else 0:.0f}%)  "
        f"P&L = {agg['v21_pl']:+.3f}u\n"
        f"  Delta (V2.2 - V2.1): {agg['v22_pl']-agg['v21_pl']:+.3f}u  "
        f"(N_compare = max({v22_n},{v21_n}))"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", help="ISO date YYYY-MM-DD; only consider rows on/after.")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="Print only; do not fire Telegram alert.")
    args = p.parse_args()

    today_iso = datetime.now(ET).strftime("%Y-%m-%d")
    season = args.season or int(today_iso[:4])
    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(csv_path)
    if args.since:
        rows = [r for r in rows if (r.get("date") or "") >= args.since]

    print(f"v21_vs_v22_compare  rows_considered={len(rows)}  since={args.since or '(all)'}")

    # Trailing 30 by graded date
    graded = sorted(
        [r for r in rows
         if (r.get("v21_shadow_pick_strength") or "").strip() != ""
         and (r.get("actual_result") or "").upper() in ("NRFI", "YRFI")],
        key=lambda r: r.get("graded_at") or r.get("date") or "",
    )
    last30 = graded[-30:]

    daily = defaultdict(list)
    for r in graded:
        daily[r.get("date") or "?"].append(r)
    overall = _aggregate(graded)
    last30_agg = _aggregate(last30)

    print()
    print(_format_summary("=== Since shadow start (all graded) ===", overall))
    print()
    print(_format_summary("=== Trailing 30 graded ===", last30_agg))
    print()
    print("Per-day breakdown (last 7 days with shadow data):")
    for d in sorted(daily.keys())[-7:]:
        a = _aggregate(daily[d])
        print(f"  {d}  V2.2 {a['v22_w']}-{a['v22_l']} ({a['v22_pl']:+.2f}u)  "
              f"V2.1 {a['v21_w']}-{a['v21_l']} ({a['v21_pl']:+.2f}u)  "
              f"delta {a['v22_pl']-a['v21_pl']:+.2f}u")

    # Alert check
    delta = last30_agg["v22_pl"] - last30_agg["v21_pl"]
    n_compare = max(last30_agg["v22_w"] + last30_agg["v22_l"],
                    last30_agg["v21_w"] + last30_agg["v21_l"])
    print()
    if n_compare < ALERT_MIN_N:
        print(f"  (no alert: trailing 30 has only {n_compare} comparison-eligible bets; need >={ALERT_MIN_N})")
        return 0
    if delta >= ALERT_DELTA_U:
        print(f"  V2.2 not underperforming V2.1 by alert threshold ({delta:+.3f}u, need <={ALERT_DELTA_U}u)")
        return 0

    body = (
        f"⚠️ <b>V2.2 underperforming V2.1 in shadow</b>\n"
        f"Trailing 30 graded STRONG picks:\n"
        f"  V2.2 (live):    {last30_agg['v22_w']}W-{last30_agg['v22_l']}L  "
        f"P&L = {last30_agg['v22_pl']:+.3f}u\n"
        f"  V2.1 (shadow):  {last30_agg['v21_w']}W-{last30_agg['v21_l']}L  "
        f"P&L = {last30_agg['v21_pl']:+.3f}u\n"
        f"  <b>Delta: {delta:+.3f}u</b>\n"
        f"\n"
        f"Threshold for alert: V2.2 - V2.1 &lt;= {ALERT_DELTA_U}u over &gt;= {ALERT_MIN_N} bets.\n"
        f"\n"
        f"Consider rolling back to V2.1.  Procedure in CHANGELOG entry "
        f"'2026-05-11 V2.2 deployed'.\n"
        f"\n"
        + tracker._dashboard_link(today_iso)
    )
    if args.dry_run:
        print("\n[dry-run] would fire alert:")
        print(body)
        return 0
    event_key = f"v22_underperform:{today_iso}"
    sent = tracker._notify_event_telegram("v22_shadow_underperform", event_key, body)
    print(f"\n  Alert {'sent' if sent else 'NOT sent (dedup or no creds)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

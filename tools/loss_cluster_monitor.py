#!/usr/bin/env python3
"""tools/loss_cluster_monitor.py

Watch for repeating loss patterns on STRONG bets and fire a Telegram
alert when a known cluster keeps losing.

Why this exists.  On 2026-05-09 the operator noticed that STRONG YRFI
bets where nrfi_p sits near 0.40 + combined_lambda near 1.0 + park
factor in the neutral 0.95-1.10 range had lost 5 in a row since v2.1
deployed (all ended NRFI 0-0).  They asked: "if that loses again then
we will have to adjust."  This script is the automation -- it runs
after every grade sweep, classifies each STRONG W/L into a cluster,
and fires `loss_cluster_streak` Telegram if a cluster's trailing
window record exceeds the alert threshold.

What it does NOT do: change picks, demote bets, or modify the model.
It is purely an observability tool that gives the operator a
heads-up so they can decide whether to skip future bets in the
cluster (manual judgment) or trigger a calibrator refit.

Cluster definitions live in this file (see CLUSTERS below).  Each
cluster is a name + a predicate over a CSV row.  Adding a new
cluster: append a dict to CLUSTERS with `name`, `description`,
`match` (callable taking a row dict, returns bool), and threshold
overrides if you want non-default behavior.

Threshold defaults:
  - Trailing window: 14 days (broad sample for context)
  - Recent-N window: last 6 graded matches in the cluster
  - Min losses in recent-N window: 5 of 6
  - Recent-N hit rate to alert: <= 0.20 (20%)

The "recent-N" check is what makes this responsive to a streak that
just started -- a cluster that was 15-6 over 14 days but lost the last
5 of 6 will alert because the recency signal dominates.  Without it
the broader sample drowns out new failure modes.

Usage:
  python tools/loss_cluster_monitor.py            # run for ET today
  python tools/loss_cluster_monitor.py --date 2026-05-10
  python tools/loss_cluster_monitor.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------
# Cluster definitions
# --------------------------------------------------------------------------

def _row_floats(row: dict, *cols: str) -> dict[str, float | None]:
    """Read several CSV columns as floats, returning None for any blank
    or unparseable value.  Used by cluster match predicates so they
    don't have to repeat the same try/except."""
    out: dict[str, float | None] = {}
    for c in cols:
        raw = (row.get(c) or "").strip()
        if not raw:
            out[c] = None
            continue
        try:
            out[c] = float(raw)
        except ValueError:
            out[c] = None
    return out


def _is_strong_bet(row: dict) -> bool:
    return (
        (row.get("pick_strength") or "").strip().upper() == "STRONG"
        and (row.get("pick_side")     or "").strip().upper() in ("NRFI", "YRFI")
        and (row.get("bet_placed")    or "").strip().upper() == "Y"
    )


def _match_yrfi_040_band(row: dict) -> bool:
    """STRONG YRFI cluster centered on nrfi_p ~ 0.40 + lambda ~ 1.0.
    Defined from the 2026-05-06 → 2026-05-09 loss streak that triggered
    this monitor.  Band widened slightly from the original
    [0.376, 0.420] to [0.370, 0.420] so the 2026-05-07 NYM@COL loss
    (nrfi_p=0.3759) lands inside.  Park gate is intentionally loose
    (0.90-1.30) -- the failing pattern hits across mild parks AND
    Coors-style hitter parks; restricting park excluded too many
    real cluster instances on test."""
    if (row.get("pick_side") or "").strip().upper() != "YRFI":
        return False
    f = _row_floats(row, "nrfi_prob", "combined_lambda", "park_factor")
    p, lam, park = f["nrfi_prob"], f["combined_lambda"], f["park_factor"]
    if p is None or lam is None or park is None:
        return False
    return 0.370 <= p <= 0.420 and 0.80 <= lam <= 1.30 and 0.90 <= park <= 1.30


def _match_strong_nrfi_marginal(row: dict) -> bool:
    """STRONG NRFI cluster centered on barely-above-threshold nrfi_p
    (0.56-0.59).  These tend to flip when one side's top of order
    catches a hit early; 5/08 NYY@MIL-style losses live here."""
    if (row.get("pick_side") or "").strip().upper() != "NRFI":
        return False
    f = _row_floats(row, "nrfi_prob", "combined_lambda")
    p, lam = f["nrfi_prob"], f["combined_lambda"]
    if p is None:
        return False
    return 0.560 <= p <= 0.590


# Each cluster: name (event_key suffix), human description, match predicate,
# optional threshold overrides.  Add new clusters by appending a dict here.
CLUSTERS: list[dict] = [
    {
        "id":           "yrfi_040_band",
        "label":        "STRONG YRFI · nrfi_p ~0.40 / λ ~1.0",
        "description":  "Bets where the model expects ~1 first-inning run "
                        "but pitchers shut both halves down to 0.  Active "
                        "since v2.1 deploy on 2026-05-06.",
        "match":        _match_yrfi_040_band,
        # 14-day overall numbers reported but don't gate the alert.
        "min_losses":      5,
        "max_hit_rate":    0.30,
        # Recent-N is what actually triggers.  Tuned so the alert fires
        # when 4-of-5 most-recent matches in the cluster have lost --
        # roughly "you've lost the last week of bets in this shape."
        "recent_n":        5,
        "recent_min_losses": 4,
        "recent_max_hit":  0.20,
    },
    {
        "id":           "nrfi_marginal_strong",
        "label":        "STRONG NRFI · barely-above-threshold (0.56-0.59)",
        "description":  "Marginal-edge STRONG NRFI commits where nrfi_p "
                        "is only 0-3pp above the STRONG threshold.  These "
                        "have low variance margin and flip often.",
        "match":        _match_strong_nrfi_marginal,
        "min_losses":      5,
        "max_hit_rate":    0.30,
        "recent_n":        5,
        "recent_min_losses": 4,
        "recent_max_hit":  0.20,
    },
]


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def _et_today() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def _trailing_window_rows(rows: list[dict], end_iso: str, days: int) -> list[dict]:
    """Return the subset of rows whose ET slate date is within `days`
    of end_iso (inclusive on both endpoints)."""
    end_dt = datetime.fromisoformat(end_iso)
    start_dt = end_dt - timedelta(days=days - 1)
    start_iso = start_dt.strftime("%Y-%m-%d")
    return [r for r in rows if start_iso <= (r.get("date") or "") <= end_iso]


def _cluster_record(rows: list[dict], cluster: dict) -> dict:
    """Walk the rows, count W/L for the cluster (full window AND
    recent-N subset), and return the recent trail for the alert body."""
    matched = [
        r for r in rows
        if _is_strong_bet(r) and cluster["match"](r)
    ]
    # Sort chronologically by graded_at (or date as fallback) so
    # "recent" reflects most-recently-resolved.
    matched.sort(key=lambda r: r.get("graded_at") or r.get("date") or "")
    graded = [r for r in matched if (r.get("graded_result") or "").upper() in ("WIN", "LOSS")]
    wins   = [r for r in graded if (r.get("graded_result") or "").upper() == "WIN"]
    losses = [r for r in graded if (r.get("graded_result") or "").upper() == "LOSS"]
    bets   = len(graded)
    hit    = (len(wins) / bets) if bets else float("nan")

    # Recent-N subset (last N graded bets in the cluster).
    n = int(cluster.get("recent_n", 6))
    recent_graded = graded[-n:]
    recent_wins   = sum(1 for r in recent_graded if (r.get("graded_result") or "").upper() == "WIN")
    recent_losses = sum(1 for r in recent_graded if (r.get("graded_result") or "").upper() == "LOSS")
    recent_bets   = recent_wins + recent_losses
    recent_hit    = (recent_wins / recent_bets) if recent_bets else float("nan")

    # Recent trail for the alert body.
    recent: list[tuple[str, str, str]] = []
    for r in recent_graded:
        gr = (r.get("graded_result") or "").upper()
        recent.append((
            r.get("date") or "?",
            f"{r.get('away_team','?')}@{r.get('home_team','?')}",
            gr,
        ))

    return {
        "matched_total": len(matched),
        "bets":          bets,
        "wins":          len(wins),
        "losses":        len(losses),
        "hit":           hit,
        "recent":        recent,
        "recent_bets":   recent_bets,
        "recent_wins":   recent_wins,
        "recent_losses": recent_losses,
        "recent_hit":    recent_hit,
    }


# --------------------------------------------------------------------------
# Alerting
# --------------------------------------------------------------------------

def _fire_alert(end_iso: str, cluster: dict, rec: dict, *, dry_run: bool) -> bool:
    """Build + send the Telegram alert.  Returns True if (would have)
    fired."""
    icon = "🚨" if rec["recent_losses"] >= (cluster["recent_min_losses"] + 1) else "⚠️"
    recent_pct  = (f"{rec['recent_hit'] * 100:.0f}%" if rec["recent_bets"] else "n/a")
    overall_pct = (f"{rec['hit'] * 100:.0f}%" if rec["bets"] else "n/a")
    body_lines = [
        f"{icon} <b>Loss-cluster streak detected</b>",
        f"Cluster: {cluster['label']}",
        f"Last {cluster['recent_n']} graded: "
        f"<b>{rec['recent_wins']}W-{rec['recent_losses']}L</b> "
        f"(hit rate {recent_pct})",
        f"Trailing 14d:  {rec['wins']}W-{rec['losses']}L  "
        f"(hit rate {overall_pct})",
        f"<i>{cluster['description']}</i>",
        "",
        f"Last {cluster['recent_n']} games in this cluster:",
    ]
    for date, game, gr in rec["recent"]:
        mark = "✅" if gr == "WIN" else "❌"
        body_lines.append(f"  {mark} {date} {game} {gr}")
    body_lines += [
        "",
        f"If the model bet this shape today and lost, the operator's "
        f"plan (per memory: loss_cluster_yrfi_040_band.md) is to "
        f"recalibrate the relevant side via "
        f"<code>recalibrate_v2.py</code> on the trailing 30-60 days.",
        "",
        tracker._dashboard_link(end_iso),
    ]
    body = "\n".join(body_lines)
    if dry_run:
        print(f"  [dry-run] would fire:\n{body}\n")
        return True
    event_key = f"loss_cluster_streak:{cluster['id']}:{end_iso}"
    return tracker._notify_event_telegram("loss_cluster_streak", event_key, body)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date",
                   help="ET slate date YYYY-MM-DD to evaluate as the window end "
                        "(defaults to today ET).")
    p.add_argument("--days", type=int, default=14,
                   help="Trailing window length in days (default: 14).")
    p.add_argument("--season", type=int, default=None,
                   help="Season override (defaults to year of --date).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print alerts to stdout instead of firing Telegram.")
    args = p.parse_args()

    end_iso = args.date or _et_today()
    season  = args.season or int(end_iso[:4])
    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2

    all_rows = tracker._read_rows(csv_path)
    window_rows = _trailing_window_rows(all_rows, end_iso, args.days)

    print(f"loss_cluster_monitor  end={end_iso}  window={args.days}d  "
          f"rows_in_window={len(window_rows)}  dry_run={args.dry_run}")

    fired_any = False
    for cluster in CLUSTERS:
        rec = _cluster_record(window_rows, cluster)
        # Recent-N is the actual trigger.  Need: enough recent bets,
        # enough recent losses, AND recent hit rate at-or-below the
        # ceiling.  The 14-day overall numbers are reported but don't
        # gate the alert.
        meets = (
            rec["recent_bets"] >= cluster["recent_n"]
            and rec["recent_losses"] >= cluster["recent_min_losses"]
            and (
                rec["recent_bets"] == 0
                or rec["recent_hit"] <= cluster["recent_max_hit"]
            )
        )
        verdict = "ALERT" if meets else "ok"
        recent_pct = (f"{rec['recent_hit'] * 100:.0f}%"
                      if rec["recent_bets"] else "n/a")
        overall_pct = (f"{rec['hit'] * 100:.0f}%" if rec["bets"] else "n/a")
        print(
            f"  [{verdict}] {cluster['id']:<24}  "
            f"recent {rec['recent_wins']}W-{rec['recent_losses']}L "
            f"(hit {recent_pct})  | 14d {rec['wins']}W-{rec['losses']}L "
            f"(hit {overall_pct})  matched={rec['matched_total']}"
        )
        if meets:
            sent = _fire_alert(end_iso, cluster, rec, dry_run=args.dry_run)
            fired_any = fired_any or sent

    if not fired_any:
        print("  No alerts fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

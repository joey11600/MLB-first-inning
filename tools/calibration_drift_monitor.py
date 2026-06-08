#!/usr/bin/env python3
"""tools/calibration_drift_monitor.py

Runs on the grade cron.  Computes trailing-30-day Brier score per
nrfi_prob bucket on STRONG bets and fires a Telegram alert if any
bucket degrades by >= 0.01 Brier vs the prior 30-day window.

WHY THIS EXISTS
---------------
2026-05-11: operator disabled the weekly auto-recalibrate cron after
the 5/11 audit revealed it was refitting the production calibrator
with no out-of-sample validation -- a direct violation of CLAUDE.md
"OOS validation is non-negotiable for any model change".

The trade-off: with auto-recalibrate off, the calibrator cannot
adapt to a real distribution shift on its own.  This monitor closes
that loop without auto-deploying anything -- it tells the operator
when a refit is warranted, but never ships one.

ALERT THRESHOLDS
----------------
- Per-bucket Brier delta >= +0.01 vs prior window  → Telegram alert
  (a 0.01 Brier degradation on a 100-row bucket is roughly equivalent
  to 1.5pp worse expected hit rate -- material if persistent)
- Cluster size < 30 in either window → skip (raised from 8 on 2026-06-07:
  a 0.04 Brier swing on ~15 bets is noise, and the old gate fired daily
  on exactly that)
- 30-day total Brier delta >= +0.005 → Telegram alert (smaller threshold
  because the aggregate has higher N and lower noise)
- Dedup is by ISO-week + drifting-bucket signature (not by date), so a
  slow-moving 30d condition pings at most ~once/week, not every day.

WHAT IT DOES NOT DO
-------------------
- Auto-refit the calibrator (intentional -- requires manual OOS review).
- Modify any pick / bet / odds / verdict.
- Re-train the LR weights.

Pure observability tool.  Operator action on alert:
  1. Run `python recalibrate_v2.py` locally + capture output.
  2. Diff candidate calibration_v2.json bins against production.
  3. If Brier improves on a held-out 30-day slice without regressing
     the trailing 7-day slice, ship the candidate via workflow_dispatch
     `recalibrate` action.

Usage:
  python tools/calibration_drift_monitor.py             # today ET
  python tools/calibration_drift_monitor.py --date 2026-05-10
  python tools/calibration_drift_monitor.py --dry-run   # report only
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")

# Bucket definitions mirror the 2026-05-11 audit's nrfi_p partitioning.
def _bucket(p: float) -> str:
    # Boundaries track the LIVE STRONG thresholds (NRFI raised to 0.62 on
    # 2026-06-04; STRONG YRFI is p_nrfi < 0.44).  The 0.44-0.62 zone is
    # LEAN/PASS only -- no STRONG bet lands there post-fix, so the old
    # 0.56-0.62 dead-band bets now sort into pass_zone instead of
    # contaminating the marg/deep NRFI Brier.
    if p < 0.40: return "deep_yrfi"
    if p < 0.44: return "marg_yrfi"
    if p < 0.62: return "pass_zone"     # LEAN/PASS only (+ discontinued dead-band); guard
    if p < 0.66: return "marg_nrfi"     # 0.62-0.66 STRONG NRFI, just over the line
    return "deep_nrfi"                  # >=0.66 high-confidence STRONG NRFI


def _safe_float(s) -> float | None:
    try:
        return float((s or "").strip())
    except (ValueError, AttributeError):
        return None


def _trailing_window(rows: list[dict], end_iso: str, days: int) -> list[dict]:
    end_dt = datetime.fromisoformat(end_iso)
    start_iso = (end_dt - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    out: list[dict] = []
    for r in rows:
        d = r.get("date") or ""
        if not (start_iso <= d <= end_iso):
            continue
        # Only graded STRONG bets count toward Brier.
        if (r.get("pick_strength") or "").upper() != "STRONG":
            continue
        if (r.get("pick_side") or "").upper() not in ("NRFI", "YRFI"):
            continue
        if (r.get("bet_placed") or "").upper() != "Y":
            continue
        if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"):
            continue
        if _safe_float(r.get("nrfi_prob")) is None:
            continue
        out.append(r)
    return out


def _brier_by_bucket(rows: list[dict]) -> dict[str, dict]:
    """Returns {bucket_id: {n, brier, hit, pl}}.

    Brier is computed against the calibrated nrfi_prob and the actual
    NRFI outcome (1 if actual_result == NRFI, 0 otherwise).  This
    captures calibration quality independent of bet sizing.
    """
    by_bucket: dict[str, list[tuple[float, int]]] = defaultdict(list)
    by_bucket_pnl: dict[str, float] = defaultdict(float)
    by_bucket_wins: dict[str, int] = defaultdict(int)

    for r in rows:
        p = _safe_float(r.get("nrfi_prob"))
        if p is None:
            continue
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue
        y = 1.0 if actual == "NRFI" else 0.0
        bucket = _bucket(p)
        by_bucket[bucket].append((p, y))
        try:
            by_bucket_pnl[bucket] += float(r.get("profit_loss_units") or 0)
        except ValueError:
            pass
        # Wins from the bettor's perspective (matches what dashboard shows).
        if (r.get("graded_result") or "").upper() == "WIN":
            by_bucket_wins[bucket] += 1

    summary: dict[str, dict] = {}
    for bucket, pairs in by_bucket.items():
        n = len(pairs)
        brier = sum((p - y) ** 2 for p, y in pairs) / n if n else 0.0
        summary[bucket] = {
            "n":     n,
            "brier": brier,
            "wins":  by_bucket_wins[bucket],
            "pl":    by_bucket_pnl[bucket],
        }
    return summary


def _format_summary(label: str, sm: dict[str, dict]) -> str:
    lines = [f"  {label}:"]
    total_n = sum(b["n"] for b in sm.values())
    total_brier_num = sum(b["brier"] * b["n"] for b in sm.values())
    total_brier = (total_brier_num / total_n) if total_n else 0.0
    for bucket in ["deep_yrfi", "marg_yrfi", "pass_zone", "marg_nrfi", "deep_nrfi"]:
        b = sm.get(bucket)
        if not b or b["n"] == 0:
            continue
        hit = (b["wins"] / b["n"] * 100) if b["n"] else 0
        lines.append(f"    {bucket:<12} n={b['n']:>3}  Brier={b['brier']:.4f}  "
                     f"hit={hit:>4.0f}%  P&L={b['pl']:+.3f}u")
    lines.append(f"    OVERALL      n={total_n:>3}  Brier={total_brier:.4f}")
    return "\n".join(lines)


# Minimum bets in BOTH windows before a per-bucket Brier delta is
# trustworthy.  Was 8 -- far too small: a 0.04 Brier swing on ~15 bets is
# noise, and the monitor was firing daily on exactly that.  30 matches the
# aggregate gate and tools/calibration_monitor.py's standard.
_MIN_CLUSTER = 30


def _build_alerts(curr: dict[str, dict], prev: dict[str, dict],
                  *, bucket_threshold: float, overall_threshold: float
                  ) -> tuple[list[str], list[str]]:
    """Compare per-bucket Brier between curr (recent 30d) and prev
    (prior 30d).  Returns (alert_lines, drifting_bucket_names) for any
    bucket that degraded by >= threshold with enough sample in BOTH
    windows.  The bucket-name list is used to dedup repeat alerts."""
    lines: list[str] = []
    drifting: list[str] = []
    for bucket, c in curr.items():
        if c["n"] < _MIN_CLUSTER:
            continue
        p = prev.get(bucket)
        if not p or p["n"] < _MIN_CLUSTER:
            continue
        delta = c["brier"] - p["brier"]
        if delta >= bucket_threshold:
            drifting.append(bucket)
            lines.append(
                f"⚠️ <b>{bucket}</b>: Brier {p['brier']:.4f} → {c['brier']:.4f} "
                f"(<b>+{delta:.4f}</b> over prior 30d).  "
                f"n_curr={c['n']}, n_prev={p['n']}."
            )

    # Aggregate check (high N, lower threshold -- catches model-wide drift
    # even when no single bucket clears the per-bucket sample gate).
    curr_total_n = sum(b["n"] for b in curr.values())
    curr_total_brier = (sum(b["brier"] * b["n"] for b in curr.values()) / curr_total_n) if curr_total_n else 0.0
    prev_total_n = sum(b["n"] for b in prev.values())
    prev_total_brier = (sum(b["brier"] * b["n"] for b in prev.values()) / prev_total_n) if prev_total_n else 0.0
    if curr_total_n >= 30 and prev_total_n >= 30:
        delta = curr_total_brier - prev_total_brier
        if delta >= overall_threshold:
            drifting.append("OVERALL")
            lines.append(
                f"⚠️ <b>OVERALL</b>: Brier {prev_total_brier:.4f} → {curr_total_brier:.4f} "
                f"(<b>+{delta:.4f}</b> over prior 30d)."
            )
    return lines, drifting


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="ET window-end date YYYY-MM-DD (default: today).")
    p.add_argument("--days", type=int, default=30,
                   help="Trailing window length in days (default: 30).")
    p.add_argument("--bucket-threshold", type=float, default=0.01,
                   help="Per-bucket Brier degradation to alert on (default: 0.01).")
    p.add_argument("--overall-threshold", type=float, default=0.005,
                   help="Aggregate Brier degradation to alert on (default: 0.005).")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="Print summary; do not fire Telegram.")
    args = p.parse_args()

    # Console may be cp1252 (Windows); the alert body has emoji.  The body
    # SENT to Telegram is UTF-8 regardless -- this only keeps local prints
    # from crashing.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    end_iso = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    season  = args.season or int(end_iso[:4])
    csv_path = tracker._csv_path(season)
    if not csv_path.exists():
        print(f"ERROR: ledger not found at {csv_path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(csv_path)

    # Current 30-day window
    curr_rows = _trailing_window(rows, end_iso, args.days)
    # Prior 30-day window (ending day before curr starts)
    prev_end = (datetime.fromisoformat(end_iso) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    prev_rows = _trailing_window(rows, prev_end, args.days)

    curr_sm = _brier_by_bucket(curr_rows)
    prev_sm = _brier_by_bucket(prev_rows)

    print(f"calibration_drift_monitor  end={end_iso}  window={args.days}d  "
          f"curr_n={len(curr_rows)}  prev_n={len(prev_rows)}  dry_run={args.dry_run}")
    print(_format_summary(f"Current ({end_iso} - {args.days}d)", curr_sm))
    if prev_rows:
        print(_format_summary(f"Prior ({prev_end} - {args.days}d, for comparison)", prev_sm))

    alerts, drifting = _build_alerts(curr_sm, prev_sm,
                            bucket_threshold=args.bucket_threshold,
                            overall_threshold=args.overall_threshold)
    if not alerts:
        print("\n  No drift alerts fired.")
        return 0

    body_lines = [
        "🚨 <b>Calibration drift detected</b>",
        f"Window: trailing {args.days}d ending {end_iso}",
        "",
    ]
    body_lines.extend(alerts)
    body_lines += [
        "",
        f"Investigation playbook:",
        f"  1. Re-read docs/2026-05-11_system_audit.md.",
        f"  2. Run <code>python recalibrate_v2.py</code> locally; capture output.",
        f"  3. Diff candidate calibration_v2.json bins against production.",
        f"  4. If Brier improves on the trailing-30d slice AND does NOT "
        f"     regress the trailing-7d slice, ship via workflow_dispatch "
        f"     <code>recalibrate</code> action.",
        f"  5. Do NOT auto-deploy without the OOS check.",
        "",
        tracker._dashboard_link(end_iso),
    ]
    body = "\n".join(body_lines)

    if args.dry_run:
        print("\n[dry-run] would fire alert:")
        print(body)
        return 0

    # Dedup by ISO week + which buckets drifted, NOT by date.  A 30-day
    # window moves slowly, so a date-keyed alert re-fired EVERY day a
    # persistent condition held -- that was the "I keep getting these"
    # spam.  Week+signature => at most one ping per week per distinct
    # drift pattern; a NEW bucket joining changes the signature and pings.
    iso_week = datetime.fromisoformat(end_iso).strftime("%G-W%V")
    sig = ",".join(sorted(set(drifting))) or "none"
    event_key = f"calibration_drift:{iso_week}:{sig}"
    sent = tracker._notify_event_telegram("calibration_drift", event_key, body)
    print(f"\n  Alert {'sent' if sent else 'NOT sent (dedup or no creds)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

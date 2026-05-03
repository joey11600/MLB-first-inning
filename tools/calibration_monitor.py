#!/usr/bin/env python3
"""
tools/calibration_monitor.py — T2.59 calibration drift detector.

Watches whether the model's stated P(pick) is matching reality across
recent windows.  Buckets STRONG bets by stated probability, computes
actual hit rate per bucket per window (7d / 14d / 30d / 60d), flags
deviations >= the configured threshold.

Run modes:
  python tools/calibration_monitor.py             # human-readable report
  python tools/calibration_monitor.py --alert     # also fires Telegram
                                                  # alert if drift detected

Designed to run from GHA daily cron (similar to analyze_losses).  Writes
a structured row to system_errors when significant drift is detected so
the Ops Health card surfaces it automatically.

Drift is "significant" when:
  - Bucket has >= 30 bets in the window
  - Actual hit rate differs from stated by >= drift_threshold (default 7pp)
  - Two-sided test (over OR under)

Two consecutive flagged windows = upgrade to system_errors entry +
Telegram alert.  Single flag = wait for next run to confirm (avoids
single-day noise).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY (loaded from .env).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from db.supabase_writer import _get_client


# Buckets calibrated to the model's actual STRONG-pick probability range.
# 0.55-0.60 / 0.60-0.65 / 0.65-0.70 covers the bulk; 0.70+ is rarely hit.
BUCKETS: list[tuple[float, float]] = [
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 1.01),
]

WINDOWS: list[tuple[int, str]] = [
    (7,  "Last 7d"),
    (14, "Last 14d"),
    (30, "Last 30d"),
    (60, "Last 60d"),
]

# Bucket needs at least this many bets to count as "stable enough" to
# distinguish drift from variance.  At n=30 with stated p=0.62, the
# 95% CI on observed hit rate spans roughly +-17pp.  At n=60, +-12pp.
# So we want at least 30 to call drift; the alert threshold accounts
# for what's distinguishable from binomial variance.
MIN_BUCKET_N = 30

# Drift threshold (pp).  7pp is roughly 2 standard errors at n=60 with
# a 0.6 base rate.  Below this, we can't distinguish from variance.
DRIFT_THRESHOLD_PP = 7.0


def fetch_strong_bets(client: Any, season: int, since_date: str) -> list[dict]:
    """Pull all STRONG picks_<season> rows graded WIN/LOSS since `since_date`."""
    table = f"picks_{season}"
    res = client.table(table).select(
        "date,pick_side,pick_strength,nrfi_prob,yrfi_prob,graded_result"
    ).gte("date", since_date).eq(
        "pick_strength", "STRONG"
    ).in_("graded_result", ["WIN", "LOSS"]).execute()
    return res.data or []


def p_of_pick(row: dict) -> float:
    """Return stated P(pick) for the row's actual pick_side."""
    side = (row.get("pick_side") or "").upper()
    if side == "NRFI":
        return float(row.get("nrfi_prob") or 0)
    if side == "YRFI":
        return float(row.get("yrfi_prob") or 0)
    return 0.0


def bucketize(rows: list[dict]) -> dict[tuple[float, float], list[bool]]:
    """Return {bucket: [True/False per bet]}."""
    out: dict[tuple[float, float], list[bool]] = {b: [] for b in BUCKETS}
    for r in rows:
        p = p_of_pick(r)
        won = r.get("graded_result") == "WIN"
        for (lo, hi) in BUCKETS:
            if lo <= p < hi:
                out[(lo, hi)].append(won)
                break
    return out


def compute_drift(rows_in_window: list[dict]) -> dict[tuple[float, float], dict]:
    """Per-bucket stats for one window."""
    buckets = bucketize(rows_in_window)
    out: dict[tuple[float, float], dict] = {}
    for (lo, hi), bets in buckets.items():
        n = len(bets)
        wins = sum(bets)
        actual_pct = (wins / n * 100) if n else 0.0
        stated_pct = (lo + hi) / 2 * 100
        drift_pp   = actual_pct - stated_pct
        out[(lo, hi)] = {
            "n":          n,
            "wins":       wins,
            "stated_pct": stated_pct,
            "actual_pct": actual_pct,
            "drift_pp":   drift_pp,
            # "Significant" = enough sample size AND drift past threshold
            "flagged":    n >= MIN_BUCKET_N and abs(drift_pp) >= DRIFT_THRESHOLD_PP,
        }
    return out


def is_drift_persistent(
    by_window: dict[str, dict[tuple[float, float], dict]]
) -> list[tuple[tuple[float, float], dict]]:
    """A bucket has 'persistent drift' if it's flagged in BOTH the 14d AND
    30d windows -- which means the drift survives sample-size scrutiny.
    Returns list of (bucket, 30d-stats) for buckets meeting that bar."""
    persistent = []
    for bucket in BUCKETS:
        s14 = by_window.get("Last 14d", {}).get(bucket, {})
        s30 = by_window.get("Last 30d", {}).get(bucket, {})
        # Both windows must be flagged AND drift in same direction
        if (s14.get("flagged") and s30.get("flagged")
                and (s14.get("drift_pp", 0) > 0) == (s30.get("drift_pp", 0) > 0)):
            persistent.append((bucket, s30))
    return persistent


def print_report(by_window: dict[str, dict[tuple[float, float], dict]]) -> None:
    print()
    print("=" * 80)
    print("  Calibration drift monitor")
    print("=" * 80)
    for _days, label in WINDOWS:
        stats = by_window.get(label, {})
        total = sum(s["n"] for s in stats.values())
        if total == 0: continue
        print(f"\n{label} ({total} STRONG bets):")
        print(f"  {'P(pick)':12} {'n':>4} {'stated':>8} {'actual':>8} {'drift':>8}  flag")
        for (lo, hi) in BUCKETS:
            s = stats.get((lo, hi), {})
            if s.get("n", 0) == 0: continue
            flag = " ***" if s["flagged"] else ""
            print(f"  {lo:.2f}-{hi:.2f}    {s['n']:>4} "
                  f"{s['stated_pct']:>7.1f}% {s['actual_pct']:>7.1f}% "
                  f"{s['drift_pp']:>+7.1f}pp{flag}")
    print()
    print(f"  *** = drift >= {DRIFT_THRESHOLD_PP}pp AND n >= {MIN_BUCKET_N} "
          f"(distinguishable from variance)")


def emit_to_system_errors(client: Any, persistent: list[tuple]) -> None:
    """When drift is persistent (flagged in both 14d AND 30d), write a
    system_errors row so the dashboard ops health card surfaces it."""
    if not persistent:
        return
    parts = []
    for (lo, hi), s in persistent:
        sign = "+" if s["drift_pp"] >= 0 else ""
        parts.append(
            f"P={lo:.2f}-{hi:.2f}: "
            f"stated {s['stated_pct']:.0f}%, actual {s['actual_pct']:.0f}% "
            f"({sign}{s['drift_pp']:.1f}pp, n={s['n']})"
        )
    msg = "Calibration drift persistent (flagged in 14d AND 30d windows): " \
          + " | ".join(parts)
    captured_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) \
                              .isoformat(timespec="seconds") + "Z"
    try:
        client.table("system_errors").insert({
            "captured_at_utc": captured_at,
            "date":            _dt.date.today().isoformat(),
            "step":            "calibration-drift",
            "exit_code":       1,
            "message":         msg[:1500],
        }).execute()
        print(f"  -> wrote drift alert to system_errors")
    except Exception as exc:    # noqa: BLE001
        print(f"  WARN: could not write to system_errors: {exc!r}",
              file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--season", type=int, default=_dt.date.today().year)
    parser.add_argument("--alert", action="store_true",
                        help="Write a system_errors row when drift is "
                             "persistent (flagged in 14d AND 30d).")
    args = parser.parse_args()

    client = _get_client()
    if client is None:
        sys.exit("Supabase not configured.")

    by_window: dict[str, dict[tuple[float, float], dict]] = {}
    today = _dt.date.today()
    # Pull the longest window once, slice client-side for the others
    longest = max(d for d, _ in WINDOWS)
    rows_60d = fetch_strong_bets(
        client, args.season,
        (today - _dt.timedelta(days=longest)).isoformat(),
    )

    for days, label in WINDOWS:
        cutoff = (today - _dt.timedelta(days=days)).isoformat()
        rows_in_window = [r for r in rows_60d if r["date"] >= cutoff]
        by_window[label] = compute_drift(rows_in_window)

    print_report(by_window)

    persistent = is_drift_persistent(by_window)
    if persistent:
        print()
        print(f"  ALERT: {len(persistent)} bucket(s) showing PERSISTENT drift "
              f"(flagged in both 14d AND 30d windows):")
        for (lo, hi), s in persistent:
            print(f"    P={lo:.2f}-{hi:.2f}  "
                  f"actual {s['actual_pct']:.1f}% vs stated "
                  f"{s['stated_pct']:.0f}%  ({s['drift_pp']:+.1f}pp, "
                  f"n={s['n']} bets)")
        if args.alert:
            emit_to_system_errors(client, persistent)
    else:
        print()
        print("  No persistent drift detected.  All buckets within "
              "variance bounds of stated probability.")


if __name__ == "__main__":
    main()

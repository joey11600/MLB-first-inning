#!/usr/bin/env python3
"""
tools/feature_drift_monitor.py -- T4.5 daily feature drift detection.

WHY THIS EXISTS
---------------
T2.53 (committed 2026-05-03 11:16 ET) silently changed pitcher_q tag
classification from 'ltd' to 'live' for ~24 of 30 pitchers on that day's
slate.  The tag change disabled the conservative ERA-blend that had been
shrinking small-sample 2026 stats toward 2025 priors.  Result: extreme
xera values like 14.71 reached the LR uncalibrated, drove confident-but-
wrong STRONG YRFI bets, lost -4.56u in a single day.

If this monitor had been running, it would have detected the tag shift
on the FIRST predict cron after T2.53 landed -- before any bets locked.

WHAT IT MONITORS
----------------
For each slate (default: yesterday ET), computes:

  Categorical drift:
    - pitcher_q distribution (away + home tags)
    - top3c_source distribution

  Numeric drift (mean, stdev, max, fraction-extreme):
    - home_xera, away_xera (extreme = > 7.0 in raw cache)
    - home_whiff_pct_rank, away_whiff_pct_rank
    - home_top3c_obp, away_top3c_obp
    - home_top3c_iso, away_top3c_iso
    - home/away_p_last5_pitcher_nrfi
    - home/away_p_last10_pitcher_nrfi

  Pick clustering (bin collapse detection):
    - Number of picks with calibrated nrfi_prob within 0.005 of each other
    - Multiple picks at exact same prob = calibrator flat zone consuming
      multiple distinct raw inputs.

Compares to a trailing 7-day baseline.  Flags:

  LOW       informational only
  MEDIUM    >= 2σ deviation OR tag-distribution shift >= 20pp
            -> writes data/system_errors.csv
  HIGH      >= 3σ deviation OR tag-shift >= 30pp OR pick-cluster >= 4
            -> writes data/system_errors.csv AND sends Telegram alert

OUTPUTS
-------
  data/diagnostics/drift_<date>.csv     -- full per-metric report
  data/diagnostics/drift_alerts.csv     -- running log of red flags only
  data/system_errors.csv                -- on MEDIUM+ severity
  Telegram message                      -- on HIGH severity

USAGE
-----
  python tools/feature_drift_monitor.py             # yesterday ET
  python tools/feature_drift_monitor.py 2026-05-03  # specific date (T2.53 demo)
  python tools/feature_drift_monitor.py --no-alerts # diagnostic only, no side effects
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PICKS_CSV = REPO_ROOT / "data" / "picks_2026.csv"
DIAG_DIR  = REPO_ROOT / "data" / "diagnostics"
ALERTS    = DIAG_DIR / "drift_alerts.csv"
ERRORS    = REPO_ROOT / "data" / "system_errors.csv"

# Numeric features to monitor.  (column_name, extreme_lo, extreme_hi)
# extreme bounds = "any individual pick outside this range is suspicious".
NUMERIC_FEATURES = [
    ("home_xera",                      2.0, 7.0),
    ("away_xera",                      2.0, 7.0),
    ("home_whiff_pct_rank",            5.0, 95.0),
    ("away_whiff_pct_rank",            5.0, 95.0),
    ("home_top3c_obp",                 0.250, 0.400),
    ("away_top3c_obp",                 0.250, 0.400),
    ("home_top3c_iso",                 0.080, 0.300),
    ("away_top3c_iso",                 0.080, 0.300),
    ("home_p_last5_pitcher_nrfi",      0.0, 1.0),
    ("away_p_last5_pitcher_nrfi",      0.0, 1.0),
    ("home_p_last10_pitcher_nrfi",     0.0, 1.0),
    ("away_p_last10_pitcher_nrfi",     0.0, 1.0),
]

CATEGORICAL_FEATURES = [
    "home_pitcher_q",
    "away_pitcher_q",
    "home_top3c_source",
    "away_top3c_source",
]

BASELINE_DAYS = 7


def to_f(v, d=None):
    if v is None or v == "":
        return d
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


def yesterday_et_iso() -> str:
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:    # noqa: BLE001
        now = datetime.utcnow() - timedelta(hours=4)
    return (now - timedelta(days=1)).strftime("%Y-%m-%d")


def read_picks_by_date() -> dict[str, list[dict]]:
    """Return {date_iso: [row, row, ...]} from picks_2026.csv."""
    rows_by_date: dict[str, list[dict]] = defaultdict(list)
    if not PICKS_CSV.exists():
        return rows_by_date
    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = r.get("date") or ""
            if d:
                rows_by_date[d].append(r)
    return rows_by_date


def numeric_stats(rows: list[dict], col: str, extreme_lo: float, extreme_hi: float) -> dict:
    vals = [to_f(r.get(col)) for r in rows]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "mean": 0.0, "stdev": 0.0, "max": 0.0, "min": 0.0, "n_extreme": 0}
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / max(1, n - 1)
    stdev = math.sqrt(var)
    n_extreme = sum(1 for v in vals if v < extreme_lo or v > extreme_hi)
    return {
        "n":         n,
        "mean":      mean,
        "stdev":     stdev,
        "max":       max(vals),
        "min":       min(vals),
        "n_extreme": n_extreme,
    }


def categorical_dist(rows: list[dict], col: str) -> dict[str, float]:
    """Return tag -> fraction (sums to 1.0)."""
    vals = [(r.get(col) or "").strip() or "_empty_" for r in rows]
    if not vals:
        return {}
    cnt = Counter(vals)
    total = len(vals)
    return {tag: count / total for tag, count in cnt.items()}


def pick_cluster_count(rows: list[dict], window: float = 0.005) -> int:
    """Largest cluster of picks with calibrated NRFI prob within `window` of
    each other -- a sign of calibrator bin collapse."""
    probs = [to_f(r.get("nrfi_prob")) for r in rows]
    probs = sorted(p for p in probs if p is not None)
    if len(probs) < 2:
        return 0
    best = 1
    for i in range(len(probs)):
        for j in range(i + 1, len(probs)):
            if probs[j] - probs[i] <= window:
                best = max(best, j - i + 1)
            else:
                break
    return best


def baseline_window(rows_by_date: dict[str, list[dict]],
                    target_date: str,
                    n_days: int) -> list[dict]:
    """Return rows from the n_days BEFORE target_date (exclusive)."""
    sorted_dates = sorted(rows_by_date.keys())
    out: list[dict] = []
    target_ts = datetime.strptime(target_date, "%Y-%m-%d")
    for d in sorted_dates:
        if d >= target_date:
            continue
        d_ts = datetime.strptime(d, "%Y-%m-%d")
        if (target_ts - d_ts).days <= n_days:
            out.extend(rows_by_date[d])
    return out


def severity_for_z(z: float) -> str:
    a = abs(z)
    if a >= 3.0:
        return "HIGH"
    if a >= 2.0:
        return "MEDIUM"
    if a >= 1.0:
        return "LOW"
    return "OK"


def severity_for_tag_shift(pp: float, downgrade: bool = False) -> str:
    """pp = absolute percentage-point shift (e.g. 0.30 = 30pp).
    If downgrade=True, severity is reduced by one level (used for
    lineup-timing-driven categories like top3c_source where shifts
    are mostly cosmetic, not feature-pipeline regressions)."""
    if pp >= 0.30:
        sev = "HIGH"
    elif pp >= 0.20:
        sev = "MEDIUM"
    elif pp >= 0.10:
        sev = "LOW"
    else:
        sev = "OK"
    if downgrade:
        sev = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "OK"}.get(sev, sev)
    return sev


# Categorical features whose distribution shifts are mostly noise
# (lineup-posting timing, etc.) -- alerts get downgraded one level.
NOISY_CATEGORICAL = {"home_top3c_source", "away_top3c_source"}


def severity_for_pick_cluster(n: int) -> str:
    if n >= 4:
        return "HIGH"
    if n >= 3:
        return "MEDIUM"
    return "OK"


def severity_for_extreme_count(n: int, total: int) -> str:
    if total == 0:
        return "OK"
    frac = n / total
    if frac >= 0.30:
        return "HIGH"
    if frac >= 0.15:
        return "MEDIUM"
    if frac >= 0.05:
        return "LOW"
    return "OK"


def send_telegram(msg: str) -> None:
    """Best-effort Telegram send; silent on missing creds or transient errors."""
    bot   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat  = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()
    if not bot or not chat:
        return
    try:
        import urllib.request
        import urllib.parse
        url = f"https://api.telegram.org/bot{bot}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(url, data=data, timeout=8)
    except Exception:    # noqa: BLE001
        pass


def append_system_error(date_iso: str, step: str, message: str) -> None:
    ERRORS.parent.mkdir(parents=True, exist_ok=True)
    new_file = not ERRORS.exists()
    with open(ERRORS, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["captured_at_utc", "date", "step", "exit_code", "message"])
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Trim message commas/quotes the same way daily.yml does
        msg = message.replace(",", ";").replace('"', "'")[:400]
        w.writerow([now_utc, date_iso, step, 0, msg])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("date", nargs="?", default=None,
                    help="Date YYYY-MM-DD to monitor (default: yesterday ET).")
    ap.add_argument("--no-alerts", action="store_true",
                    help="Diagnostic only -- skip Telegram + system_errors writes.")
    ap.add_argument("--baseline-days", type=int, default=BASELINE_DAYS,
                    help=f"Trailing days for baseline (default: {BASELINE_DAYS}).")
    args = ap.parse_args()

    target_date = args.date or yesterday_et_iso()
    DIAG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"==> Feature drift monitor for {target_date}  "
          f"(baseline: trailing {args.baseline_days} days)")

    rows_by_date = read_picks_by_date()
    today_rows = rows_by_date.get(target_date, [])
    if not today_rows:
        print(f"  No rows for {target_date}.  Aborting.")
        return
    baseline_rows = baseline_window(rows_by_date, target_date, args.baseline_days)
    if len(baseline_rows) < 20:
        print(f"  Baseline has only {len(baseline_rows)} rows; results may be noisy.")

    report_rows: list[dict] = []
    alerts: list[tuple[str, str, str]] = []   # (severity, metric, msg)

    # --- categorical drift ---
    for col in CATEGORICAL_FEATURES:
        today_dist = categorical_dist(today_rows, col)
        baseline_dist = categorical_dist(baseline_rows, col)
        all_tags = set(today_dist.keys()) | set(baseline_dist.keys())
        downgrade = col in NOISY_CATEGORICAL
        for tag in sorted(all_tags):
            tf = today_dist.get(tag, 0.0)
            bf = baseline_dist.get(tag, 0.0)
            shift_pp = tf - bf
            sev = severity_for_tag_shift(abs(shift_pp), downgrade=downgrade)
            note = f"{col}={tag}: today={tf:.1%} baseline={bf:.1%} shift={shift_pp:+.1%}"
            report_rows.append({
                "category":  "categorical",
                "metric":    f"{col}={tag}",
                "today":     f"{tf:.4f}",
                "baseline":  f"{bf:.4f}",
                "delta":     f"{shift_pp:+.4f}",
                "severity":  sev,
                "note":      note,
            })
            if sev in ("MEDIUM", "HIGH"):
                alerts.append((sev, f"{col}={tag}", note))

    # --- numeric drift ---
    for col, lo, hi in NUMERIC_FEATURES:
        ts = numeric_stats(today_rows,   col, lo, hi)
        bs = numeric_stats(baseline_rows, col, lo, hi)
        if ts["n"] == 0 or bs["n"] == 0:
            continue
        # z-score on the mean shift (using baseline stdev, with floor)
        b_stdev = max(bs["stdev"], 0.01)
        z_mean  = (ts["mean"] - bs["mean"]) / b_stdev
        sev_mean = severity_for_z(z_mean)
        report_rows.append({
            "category":  "numeric_mean",
            "metric":    f"{col}_mean",
            "today":     f"{ts['mean']:.4f}",
            "baseline":  f"{bs['mean']:.4f}",
            "delta":     f"z={z_mean:+.2f}",
            "severity":  sev_mean,
            "note":      f"{col} mean {ts['mean']:.3f} vs baseline {bs['mean']:.3f} (z={z_mean:+.2f})",
        })
        if sev_mean in ("MEDIUM", "HIGH"):
            alerts.append((sev_mean, f"{col}_mean", report_rows[-1]["note"]))
        # Stdev shift (high stdev = small-sample noise leaking through)
        z_stdev = (ts["stdev"] - bs["stdev"]) / b_stdev
        sev_std = severity_for_z(z_stdev)
        if sev_std in ("MEDIUM", "HIGH"):
            note = f"{col} stdev {ts['stdev']:.3f} vs baseline {bs['stdev']:.3f} (z={z_stdev:+.2f})"
            report_rows.append({
                "category":  "numeric_stdev",
                "metric":    f"{col}_stdev",
                "today":     f"{ts['stdev']:.4f}",
                "baseline":  f"{bs['stdev']:.4f}",
                "delta":     f"z={z_stdev:+.2f}",
                "severity":  sev_std,
                "note":      note,
            })
            alerts.append((sev_std, f"{col}_stdev", note))
        # Extreme outlier count (e.g. xera > 7.0)
        sev_ex = severity_for_extreme_count(ts["n_extreme"], ts["n"])
        if sev_ex in ("MEDIUM", "HIGH"):
            note = (f"{col} has {ts['n_extreme']}/{ts['n']} extreme values "
                    f"(outside [{lo}, {hi}]); max={ts['max']:.2f}")
            report_rows.append({
                "category":  "numeric_extreme",
                "metric":    f"{col}_extreme",
                "today":     str(ts["n_extreme"]),
                "baseline":  str(bs["n_extreme"]),
                "delta":     f"{ts['n_extreme'] - bs['n_extreme']:+d}",
                "severity":  sev_ex,
                "note":      note,
            })
            alerts.append((sev_ex, f"{col}_extreme", note))

    # --- pick clustering (bin collapse) ---
    cluster = pick_cluster_count(today_rows, window=0.005)
    sev_cluster = severity_for_pick_cluster(cluster)
    note = (f"largest cluster of picks within 0.005 calibrated P(NRFI): "
            f"{cluster} (>=4 = HIGH; >=3 = MEDIUM)")
    report_rows.append({
        "category":  "pick_cluster",
        "metric":    "max_cluster_size",
        "today":     str(cluster),
        "baseline":  "-",
        "delta":     "-",
        "severity":  sev_cluster,
        "note":      note,
    })
    if sev_cluster in ("MEDIUM", "HIGH"):
        alerts.append((sev_cluster, "pick_cluster", note))

    # --- write report ---
    out_path = DIAG_DIR / f"drift_{target_date}.csv"
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        fieldnames = ["category", "metric", "today", "baseline", "delta", "severity", "note"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in report_rows:
            w.writerow(row)
    print(f"  Wrote {out_path}  ({len(report_rows)} metrics)")

    # --- summarize ---
    by_sev: dict[str, int] = Counter(r["severity"] for r in report_rows)
    print(f"  Severity counts: HIGH={by_sev.get('HIGH', 0)}  "
          f"MEDIUM={by_sev.get('MEDIUM', 0)}  LOW={by_sev.get('LOW', 0)}  "
          f"OK={by_sev.get('OK', 0)}")

    high_alerts = [a for a in alerts if a[0] == "HIGH"]
    medium_alerts = [a for a in alerts if a[0] == "MEDIUM"]

    # Print all alerts
    if alerts:
        print()
        print("  ALERTS:")
        for sev, metric, note in sorted(alerts, key=lambda x: ("HIGH","MEDIUM","LOW").index(x[0])):
            print(f"    [{sev:<6}] {note}")

    # --- side effects ---
    if not args.no_alerts and (high_alerts or medium_alerts):
        # Append to drift_alerts.csv
        new_alerts_file = not ALERTS.exists()
        with open(ALERTS, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_alerts_file:
                w.writerow(["date", "severity", "metric", "note"])
            for sev, metric, note in alerts:
                w.writerow([target_date, sev, metric, note])
        print(f"  Appended {len(alerts)} alerts -> {ALERTS}")

        # System errors entry
        n_high = len(high_alerts); n_med = len(medium_alerts)
        msg = (f"feature drift {target_date}: {n_high} HIGH + {n_med} MEDIUM alerts; "
               f"see {out_path.name}")
        append_system_error(target_date, "feature-drift", msg)

        # Telegram on HIGH only
        if high_alerts:
            tg_lines = [f"FEATURE DRIFT [{target_date}]:"] + \
                       [f"- {note}" for sev, metric, note in high_alerts[:5]]
            if len(high_alerts) > 5:
                tg_lines.append(f"... and {len(high_alerts) - 5} more HIGH alerts")
            send_telegram("\n".join(tg_lines))
            print(f"  Sent Telegram alert ({len(high_alerts)} HIGH).")


if __name__ == "__main__":
    main()

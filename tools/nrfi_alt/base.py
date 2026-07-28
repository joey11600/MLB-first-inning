#!/usr/bin/env python3
"""
tools/nrfi_alt/base.py -- shared loader for the "where is DraftKings soft"
investigation.  Read-only.

Builds one row per SETTLED 2026 game that has BOTH DK sides captured, with
the market-structure fields we want to slice on:

    S       = implied_nrfi + implied_yrfi          (the book's total take)
    over    = S - 1                                (overround, in prob pts)
    fair_n  = i_n / S                              (proportional de-vig)
    vig_n   = i_n - fair_n                         (vig loaded on NRFI side)
    lead_h  = hours between odds capture and first pitch

No model refit, no writes.
"""
from __future__ import annotations

import csv
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def prob_to_am(p):
    """inverse of implied(): probability -> american odds"""
    if p <= 0 or p >= 1:
        return float("nan")
    return -100.0 * p / (1 - p) if p >= 0.5 else 100.0 * (1 - p) / p


def am_shift(o, cents):
    """Move an american price `cents` in the bettor's favour.

    American odds are not linear across the -100/+100 boundary, so shift on
    the payout ladder: convert to decimal-ish payout, add cents/100, convert
    back.  At -110 +10c -> -100(=+100 payout 1.0)... which is the standard
    'dime' meaning on the plus side and the usual approximation on the minus
    side.  This is the conventional sportsbook 'X cents better' reading.
    """
    return payout(o) + cents / 100.0  # returns a PAYOUT, not american odds


def parse_hour_et(s):
    """'6:40 PM ET' -> 18.666"""
    if not s:
        return None
    s = s.replace(" ET", "").strip()
    try:
        t = datetime.strptime(s, "%I:%M %p")
    except ValueError:
        return None
    return t.hour + t.minute / 60.0


def load(require_both=True):
    with open(PICKS, encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    out = []
    for r in raw:
        a = (r.get("actual_result") or "").upper()
        if a not in ("NRFI", "YRFI"):
            continue
        no, yo = fnum(r.get("market_nrfi_odds")), fnum(r.get("market_yrfi_odds"))
        if require_both and (no is None or yo is None):
            continue
        if not (r.get("sportsbook") or "").strip():
            continue
        i_n, i_y = implied(no), implied(yo)
        S = i_n + i_y
        d = r["date"]
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        hr = parse_hour_et(r.get("game_time_et"))
        cap = r.get("odds_captured_at") or ""
        lead = None
        if cap and hr is not None:
            try:
                cdt = datetime.fromisoformat(cap.replace("Z", "+00:00"))
                # game time ET -> UTC (ET is UTC-4 in season)
                gdt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc) + \
                    timedelta(hours=hr + 4)
                lead = (gdt - cdt).total_seconds() / 3600.0
            except Exception:
                lead = None
        ono, oyo = fnum(r.get("opened_nrfi_odds")), fnum(r.get("opened_yrfi_odds"))
        out.append({
            "date": d,
            "dow": dt.weekday(),          # 0=Mon
            "month": dt.month,
            "home": r.get("home_team", ""),
            "away": r.get("away_team", ""),
            "y": 1 if a == "NRFI" else 0,
            "nrfi_odds": no, "yrfi_odds": yo,
            "i_n": i_n, "i_y": i_y, "S": S, "over": S - 1.0,
            "fair_n": i_n / S, "fair_y": i_y / S,
            "vig_n": i_n - i_n / S, "vig_y": i_y - i_y / S,
            "pay_n": payout(no), "pay_y": payout(yo),
            "p_nrfi": fnum(r.get("nrfi_prob")),
            "p_raw": fnum(r.get("nrfi_prob_raw")),
            "lam": fnum(r.get("lambda_lr_total")),
            "clam": fnum(r.get("combined_lambda")),
            "park": fnum(r.get("park_factor")),
            "hour": hr,
            "lead_h": lead,
            "dome": (r.get("wx_is_dome") or "").strip().lower() in ("true", "1", "yes"),
            "temp": fnum(r.get("wx_temp_c")),
            "open_n": ono, "open_y": oyo,
            "strength": (r.get("pick_strength") or "").upper(),
            "side": (r.get("pick_side") or "").upper(),
            "bet": (r.get("bet_placed") or "").upper(),
        })
    return out


# ---------- stats helpers ----------

def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def day_boot(rows, fn, iters=4000, seed=17):
    """block bootstrap over calendar days"""
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    vals = []
    for _ in range(iters):
        s = []
        for _ in range(len(days)):
            s.extend(byday[days[rnd.randrange(len(days))]])
        v = fn(s)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            vals.append(v)
    if len(vals) < 100:
        return (float("nan"), float("nan"))
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1])


def roi_nrfi(rows):
    if not rows:
        return float("nan")
    tot = sum(r["pay_n"] if r["y"] else -1.0 for r in rows)
    return tot / len(rows)


def roi_yrfi(rows):
    if not rows:
        return float("nan")
    tot = sum(r["pay_y"] if not r["y"] else -1.0 for r in rows)
    return tot / len(rows)


def buckets(rows, key, edges):
    """edges = list of cut points; returns list of (label, subset)"""
    out = []
    vals = [(r, r[key]) for r in rows if r.get(key) is not None]
    prev = -1e18
    for e in list(edges) + [1e18]:
        sub = [r for r, v in vals if prev <= v < e]
        lab = f"[{prev if prev > -1e17 else float('-inf'):.3g},{e if e < 1e17 else float('inf'):.3g})"
        out.append((lab, sub))
        prev = e
    return out


if __name__ == "__main__":
    rows = load()
    print(f"rows: {len(rows)}  days: {len(set(r['date'] for r in rows))}")
    S = sorted(r["over"] for r in rows)
    n = len(S)
    print("overround (S-1) percentiles, in probability points:")
    for q in (0, 5, 10, 25, 50, 75, 90, 95, 100):
        print(f"  p{q:>3}: {S[min(n-1, int(q/100*n))]*100:6.2f}pp")
    print(f"  mean: {sum(S)/n*100:.2f}pp")
    miss = sum(1 for r in rows if r["lead_h"] is None)
    print(f"lead_h missing: {miss}")
    print(f"NRFI hit rate: {sum(r['y'] for r in rows)/n*100:.2f}%")
    print(f"mean charged i_n: {sum(r['i_n'] for r in rows)/n*100:.2f}%")
    print(f"mean fair_n:      {sum(r['fair_n'] for r in rows)/n*100:.2f}%")
    print(f"NRFI flat ROI:    {roi_nrfi(rows)*100:+.2f}%")
    print(f"YRFI flat ROI:    {roi_yrfi(rows)*100:+.2f}%")

#!/usr/bin/env python3
"""ANALYSIS ONLY -- independent re-implementation of classify_pick_lr,
replayed over every 2026 row with ERA-AWARE threshold constants taken
from git history, then diffed against stored pick_side / pick_strength.
"""
from __future__ import annotations
import csv, math, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- eras
# (effective_from_datetime_ET_approx, constants).  Commit timestamps are
# local-clock in git; picks are logged with `created_at`.  We key on the
# row's `created_at` when present, else the slate date.
ERAS = [
    ("2026-01-01T00:00", dict(SN=0.60, LN=0.53, PL=0.47, LY=0.40, SY=None, FL=None, CE=None)),
    ("2026-04-27T11:50", dict(SN=0.62, LN=0.53, PL=0.47, LY=0.42, SY=None, FL=None, CE=None)),
    ("2026-04-27T17:40", dict(SN=0.58, LN=0.58, PL=0.42, LY=0.42, SY=None, FL=None, CE=None)),
    ("2026-04-29T12:05", dict(SN=0.56, LN=0.56, PL=0.44, LY=0.44, SY=None, FL=None, CE=None)),
    ("2026-04-29T16:21", dict(SN=0.56, LN=0.56, PL=0.44, LY=0.44, SY=None, FL=0.78, CE=None)),
    ("2026-05-12T12:37", dict(SN=0.56, LN=0.50, PL=0.44, LY=0.50, SY=None, FL=0.78, CE=None)),
    ("2026-05-19T10:59", dict(SN=0.56, LN=0.50, PL=0.44, LY=0.50, SY=None, FL=0.838, CE=None)),
    ("2026-06-03T22:42", dict(SN=0.62, LN=0.50, PL=0.44, LY=0.50, SY=None, FL=0.838, CE=0.52)),
    ("2026-06-15T16:57", dict(SN=1.01, LN=0.50, PL=0.44, LY=0.50, SY=None, FL=0.838, CE=0.52)),
    ("2026-07-27T22:41", dict(SN=1.01, LN=0.50, PL=0.44, LY=0.50, SY=0.36, FL=0.838, CE=0.52)),
    ("2026-07-28T14:18", dict(SN=1.01, LN=0.50, PL=0.44, LY=0.50, SY=0.40, FL=0.838, CE=0.52)),
]
WX_FLOOR_FROM = "2026-05-01T00:00"


def era_for(stamp: str) -> dict:
    cur = ERAS[0][1]
    for t, c in ERAS:
        if stamp >= t:
            cur = c
    return cur


def wx_floor(base, temp, wind, dome):
    """Independent re-derivation of _weather_adjusted_floor."""
    if dome:
        return base
    d = 0.0
    if temp is not None and temp >= 28.0:
        d += 0.02
    elif temp is not None and temp <= 12.0:
        d -= 0.02
    if wind is not None and wind >= 24.0:
        d += 0.02
    return max(0.40, min(1.20, base + d))


def classify(p, data_pts, lam, floor, C):
    """Independent re-implementation of classify_pick_lr."""
    if data_pts == 0:
        return "PASS", "NO DATA"
    if p >= C["SN"]:
        if C["CE"] is not None and lam is not None and lam > C["CE"]:
            return "PASS", "HIGH LAMBDA"
        return "NRFI", "STRONG"
    if p >= C["LN"]:
        return "NRFI", "LEAN"
    if p > C["PL"]:
        if lam is not None and floor is not None and lam >= floor:
            return "YRFI", "LEAN"
        return "PASS", "NO EDGE"
    if p >= C["PL"]:
        return "PASS", "NO EDGE"
    if lam is not None and floor is not None and lam < floor:
        return "PASS", "LOW LAMBDA"
    if C["SY"] is not None and p >= C["SY"]:
        return "YRFI", "LEAN"
    return "YRFI", "STRONG"


def f(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def main():
    rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
    disag = Counter()
    detail = defaultdict(list)
    n = 0
    for r in rows:
        p = f(r["nrfi_prob"])
        if p is None:
            continue
        lam = f(r["lambda_lr_total"])
        stamp = (r.get("created_at") or "")[:16] or (r["date"] + "T12:00")
        C = era_for(stamp)
        dome = (f(r["wx_is_dome"]) or 0) >= 0.5
        base = C["FL"]
        floor = None
        if base is not None:
            floor = wx_floor(base, f(r["wx_temp_c"]), f(r["wx_wind_kmh"]), dome) \
                if stamp >= WX_FLOOR_FROM else base
        dp = sum(1 for k in ("away_pitcher_q", "home_pitcher_q",
                             "away_batting_q", "home_batting_q")
                 if (r.get(k) or "").strip() != "avg")
        side, strength = classify(p, dp, lam, floor, C)

        # production guards applied after classify_pick_lr
        reasons = []
        if strength not in ("NO DATA", "STRONG") and (
                (r.get("away_top3c_source") or "") != "lineup"
                or (r.get("home_top3c_source") or "") != "lineup"):
            side = "PASS"; reasons.append("LINEUP PENDING")
        if (r.get("away_pitcher_q") == "avg" or r.get("home_pitcher_q") == "avg"):
            side = "PASS"
            nm_a = (r.get("away_pitcher") or "").strip().upper()
            nm_h = (r.get("home_pitcher") or "").strip().upper()
            unann = ((r.get("away_pitcher_q") == "avg" and (not nm_a or nm_a in ("TBD", "TBA", "UNDECIDED", "UNKNOWN")))
                     or (r.get("home_pitcher_q") == "avg" and (not nm_h or nm_h in ("TBD", "TBA", "UNDECIDED", "UNKNOWN"))))
            reasons.append("STARTER PENDING" if unann else "NO DATA")
        if reasons:
            order = ["FLAT ZONE", "LINEUP PENDING", "STARTER PENDING",
                     "NO DATA", "HIGH LAMBDA", "LOW LAMBDA", "NO EDGE"]
            reasons.sort(key=lambda x: order.index(x) if x in order else 99)
            strength = reasons[0]

        got = ((r.get("pick_side") or "").strip(), (r.get("pick_strength") or "").strip())
        n += 1
        if got != (side, strength):
            key = f"{got[0]}/{got[1]}  ->  mine {side}/{strength}"
            disag[key] += 1
            if len(detail[key]) < 6:
                detail[key].append(
                    f"{r['date']} {r['away_team']}@{r['home_team']} p={p:.4f} "
                    f"lam={lam} floor={floor} dp={dp}")

    print(f"rows compared: {n}   disagreements: {sum(disag.values())} "
          f"({100*sum(disag.values())/max(1,n):.2f}%)")
    for k, v in disag.most_common():
        print(f"\n  {v:>5}  {k}")
        for d in detail[k]:
            print(f"          {d}")


if __name__ == "__main__":
    main()

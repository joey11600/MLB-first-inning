"""Recompute dashboard/lib/roi.ts zone aggregation in Python, exactly.

Read-only. Reproduces:
  - z.unitsPL          (what HistoryView ZoneHitRateChart prints)
  - provenance.realPricedPL / realPL(z)  (what RoiPanel ZoneCard prints)
for each window, so the two screens can be diffed.
"""
import csv, sys, os
from datetime import date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV = os.path.join(REPO, "data", "picks_2026.csv")

DEFAULT_WIN_PROFIT_UNITS = 0.909
DEFAULT_LOSS_UNITS = -1.0

TODAY = os.environ.get("AUDIT_TODAY", "2026-07-28")


def windows(today):
    y, m, d = map(int, today.split("-"))
    t = date(y, m, d)
    return {
        "season": f"{today[:4]}-01-01",
        "30d": (t - timedelta(days=29)).isoformat(),
        "7d": (t - timedelta(days=6)).isoformat(),
        "today": today,
    }


def run(start, today):
    zones = {}
    for r in csv.DictReader(open(CSV, newline="", encoding="utf-8")):
        dt = (r.get("date") or "")[:10]
        if not dt or dt < start or dt > today:
            continue
        side = (r.get("pick_side") or "").upper()
        if side not in ("NRFI", "YRFI"):
            side = "PASS"
        strength = (r.get("pick_strength") or "").upper()
        if strength not in ("STRONG", "LEAN", "NO EDGE", "NO DATA",
                            "STARTER PENDING", "LINEUP PENDING", "LOW LAMBDA"):
            strength = "NO EDGE"
        key = f"{side}|{strength}"
        z = zones.setdefault(key, dict(wins=0, losses=0, unitsPL=0.0,
                                       realBets=0, phBets=0, realPL=0.0))
        graded = (r.get("graded_result") or "").upper()
        if graded not in ("WIN", "LOSS"):
            continue
        if graded == "WIN":
            z["wins"] += 1
        else:
            z["losses"] += 1
        if strength == "LEAN" and side in ("NRFI", "YRFI"):
            pl = DEFAULT_WIN_PROFIT_UNITS if graded == "WIN" else DEFAULT_LOSS_UNITS
        else:
            raw = (r.get("profit_loss_units") or "").strip()
            pl = None
            if raw:
                try:
                    pl = float(raw)
                except ValueError:
                    pl = None
            if pl is None:
                pl = DEFAULT_WIN_PROFIT_UNITS if graded == "WIN" else DEFAULT_LOSS_UNITS
        if strength != "LEAN":
            col = r.get("market_nrfi_odds") if side == "NRFI" else r.get("market_yrfi_odds")
            if (col or "").strip():
                z["realBets"] += 1
                z["realPL"] += pl
            else:
                z["phBets"] += 1
        z["unitsPL"] += pl
    return zones


def realPL(z):
    known = z["realBets"] + z["phBets"]
    return z["realPL"] if known > 0 else z["unitsPL"]


if __name__ == "__main__":
    ws = windows(TODAY)
    for wname in ("season", "30d", "7d", "today"):
        start = ws[wname]
        zones = run(start, TODAY)
        print(f"\n=== window={wname}  {start} .. {TODAY} ===")
        print(f"{'zone':<24}{'bets':>5}{'W-L':>10}{'unitsPL(Hist)':>15}"
              f"{'realPL(Roi)':>14}{'delta':>10}{'realShare':>11}")
        tot_units = tot_real = 0.0
        for key in ("NRFI|STRONG", "NRFI|LEAN", "YRFI|LEAN", "YRFI|STRONG"):
            z = zones.get(key)
            if not z:
                continue
            bets = z["wins"] + z["losses"]
            if bets == 0:
                continue
            rp = realPL(z)
            known = z["realBets"] + z["phBets"]
            share = (z["realBets"] / known) if known else float("nan")
            print(f"{key:<24}{bets:>5}{str(z['wins'])+'-'+str(z['losses']):>10}"
                  f"{z['unitsPL']:>15.2f}{rp:>14.2f}{rp - z['unitsPL']:>10.2f}{share:>11.3f}")
            if key.endswith("STRONG"):
                tot_units += z["unitsPL"]
                tot_real += z["realPL"]
        print(f"{'TOTAL (STRONG only)':<24}{'':>5}{'':>10}{tot_units:>15.2f}{tot_real:>14.2f}"
              f"{tot_real - tot_units:>10.2f}")

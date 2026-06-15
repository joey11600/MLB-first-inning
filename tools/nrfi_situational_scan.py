#!/usr/bin/env python3
"""tools/nrfi_situational_scan.py

Hunt for SITUATIONAL NRFI miscalibration: slices where the model's
calibrated NRFI prob is systematically far from the actual NRFI rate,
even though the AGGREGATE is well-calibrated.  Those slices are where a
rework could add value.

For each dimension we bucket graded games and print, per bucket:
  n, mean model NRFI, actual NRFI, gap (model-actual), and a FLAG when
  n>=MIN_N and |gap|>=FLAG_GAP (robust-ish systematic miss).

Read-only.  Usage: python tools/nrfi_situational_scan.py
"""
from __future__ import annotations
import csv
from pathlib import Path

CSV = Path(__file__).resolve().parent.parent / "data" / "picks_2026.csv"
MIN_N = 40
FLAG_GAP = 0.05


def f(r, k):
    try: return float(r.get(k) or "")
    except ValueError: return None


def load():
    rows = []
    with open(CSV, encoding="utf-8") as f_:
        for r in csv.DictReader(f_):
            try:
                fa = int(float(r["fi_away_runs"])); fh = int(float(r["fi_home_runs"]))
                p = float(r["nrfi_prob"])
            except (ValueError, TypeError, KeyError):
                continue
            rows.append({
                "nrfi": 1 if (fa + fh) == 0 else 0,
                "p": p,
                "park": f(r, "fi_park_nrfi_rate"),
                "lam": f(r, "lambda_lr_total"),
                "h_era": f(r, "home_era"), "a_era": f(r, "away_era"),
                "ump": f(r, "home_plate_ump_nrfi_rate"),
                "time": (r.get("game_time_et") or "").strip(),
                "a_obp": f(r, "away_top3c_obp"), "h_obp": f(r, "home_top3c_obp"),
                "row": r,
            })
    return rows


def show(rows, label, keyfn, edges):
    """edges: list of (lo, hi, name)."""
    print(f"\n=== by {label} ===")
    print(f"  {'bucket':<20}{'n':>5}{'modelNRFI':>11}{'actualNRFI':>12}{'gap':>8}")
    for lo, hi, name in edges:
        sub = [x for x in rows if (v := keyfn(x)) is not None and lo <= v < hi]
        if not sub: continue
        n = len(sub); mp = sum(x["p"] for x in sub)/n; ma = sum(x["nrfi"] for x in sub)/n
        flag = "  <== FLAG" if (n >= MIN_N and abs(mp-ma) >= FLAG_GAP) else ""
        print(f"  {name:<20}{n:>5}{mp:>11.3f}{ma:>12.3f}{mp-ma:>+8.3f}{flag}")


rows = load()
print(f"2026 graded games: {len(rows)}  (FLAG = n>={MIN_N} and |gap|>={FLAG_GAP})")
print(f"aggregate: model NRFI={sum(x['p'] for x in rows)/len(rows):.3f}  "
      f"actual={sum(x['nrfi'] for x in rows)/len(rows):.3f}")

# Park NRFI factor (higher = more pitcher-friendly = more NRFI)
show(rows, "park NRFI factor", lambda x: x["park"],
     [(0, 0.48, "hitter park <.48"), (0.48, 0.52, "neutral .48-.52"),
      (0.52, 0.56, "pitcher .52-.56"), (0.56, 2, "extreme >.56")])

# Combined lambda (model's expected total 1st-inn runs)
show(rows, "model lambda_total", lambda x: x["lam"],
     [(0, 0.70, "very low <.70"), (0.70, 0.85, ".70-.85"),
      (0.85, 1.00, ".85-1.0"), (1.00, 1.20, "1.0-1.2"), (1.20, 9, "high >1.2")])

# Better starter's ERA (min of the two) -- ace games should be more NRFI
def min_era(x):
    es = [e for e in (x["h_era"], x["a_era"]) if e is not None]
    return min(es) if es else None
show(rows, "best starter ERA (min)", min_era,
     [(0, 3.0, "ace <3.0"), (3.0, 3.8, "good 3.0-3.8"),
      (3.8, 4.5, "avg 3.8-4.5"), (4.5, 99, "weak >4.5")])

# Worse starter's ERA (max)
def max_era(x):
    es = [e for e in (x["h_era"], x["a_era"]) if e is not None]
    return max(es) if es else None
show(rows, "worst starter ERA (max)", max_era,
     [(0, 3.5, "<3.5"), (3.5, 4.2, "3.5-4.2"), (4.2, 5.0, "4.2-5.0"), (5.0, 99, ">5.0")])

# Umpire NRFI tendency
show(rows, "home-plate ump NRFI rate", lambda x: x["ump"],
     [(0, 0.49, "hitter ump <.49"), (0.49, 0.51, "neutral"), (0.51, 2, "pitcher ump >.51")])

# Day vs night (game_time_et hour)
def hour(x):
    t = x["time"]
    try: return int(t.split(":")[0]) + (12 if "PM" in t.upper() and not t.startswith("12") else 0)
    except (ValueError, IndexError): return None
show(rows, "start hour (ET, ~day/night)", hour,
     [(0, 17, "day <5pm"), (17, 24, "night >=5pm")])

# Lineup quality: combined top-3 OBP of both teams
def comb_obp(x):
    os = [o for o in (x["a_obp"], x["h_obp"]) if o is not None]
    return sum(os)/len(os) if os else None
show(rows, "avg top3 OBP (both teams)", comb_obp,
     [(0, 0.32, "weak <.32"), (0.32, 0.35, ".32-.35"), (0.35, 0.38, ".35-.38"), (0.38, 1, "elite >.38")])

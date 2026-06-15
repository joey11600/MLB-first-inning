#!/usr/bin/env python3
"""tools/batter_fi_obp_premise_check.py -- CHEAP gate before building a
1st-inning batting-order feature.

Premise of the feature: a hitter's FIRST-INNING on-base rate carries
predictive info BEYOND his season OBP (which the model already uses).
Sabermetric prior says no -- inning effects are the pitcher's, not the
batter's. Test it before scraping 5,000 historical lineups.

Two questions, on ~50 real top-of-order hitters (IDs pulled from 2026
lineups), using MLB StatsAPI i01 (1st-inning) hitting splits:
  1. SIZE: how far does i01-OBP sit from season OBP? (~0 => nothing to add)
  2. STABILITY: is a batter's 2024 (i01 - season) gap correlated with his
     2025 gap? If r ~ 0, the gap is noise -> useless as a feature. A real
     trait would persist year to year.

Read-only (StatsAPI GETs only). ~200 calls, a few minutes.
"""
from __future__ import annotations
import csv, json, sys, time
from pathlib import Path
try:
    import statsapi
except ImportError:
    sys.exit("pip install mlb-statsapi")

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"


def _obp(stat):
    v = stat.get("obp")
    if v in (None, "-", ".---", ""):
        return None
    try: return float(v)
    except ValueError: return None


def fi_obp(pid, season):
    """1st-inning (i01) OBP + PA for a batter-season."""
    try:
        d = statsapi.get("person", {"personId": pid,
            "hydrate": f"stats(group=[hitting],type=[statSplits],sitCodes=[i01],season={season})"})
        time.sleep(0.25)
        for sp in d.get("people", [{}])[0].get("stats", [{}])[0].get("splits", []):
            s = sp.get("stat", {})
            pa = float(s.get("plateAppearances") or 0)
            o = _obp(s)
            if o is not None and pa >= 15:
                return o, pa
    except Exception:
        return None
    return None


def season_obp(pid, season):
    try:
        d = statsapi.get("person", {"personId": pid,
            "hydrate": f"stats(group=[hitting],type=[season],season={season})"})
        time.sleep(0.25)
        for sp in d.get("people", [{}])[0].get("stats", [{}])[0].get("splits", []):
            o = _obp(sp.get("stat", {}))
            if o is not None:
                return o
    except Exception:
        return None
    return None


def sample_batter_ids(n=50):
    ids = {}
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for col in ("home_lineup_json", "away_lineup_json"):
                try: lu = json.loads(r.get(col) or "[]")
                except (ValueError, TypeError): continue
                for b in lu[:3]:                    # top-of-order only
                    if isinstance(b, dict) and b.get("id") and (b.get("ab") or 0) >= 60:
                        ids[int(b["id"])] = b.get("name", "")
    return list(ids.items())[:n]


def corr(xs, ys):
    n = len(xs)
    if n < 5: return float("nan")
    mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    sx = (sum((x-mx)**2 for x in xs))**0.5; sy = (sum((y-my)**2 for y in ys))**0.5
    return cov/(sx*sy) if sx*sy else float("nan")


def main():
    batters = sample_batter_ids(50)
    print(f"Sampling {len(batters)} top-of-order hitters (from 2026 lineups, ab>=60)\n")
    g24, g25, gaps_abs = [], [], []
    rows = []
    for pid, name in batters:
        f24, s24 = fi_obp(pid, 2024), season_obp(pid, 2024)
        f25, s25 = fi_obp(pid, 2025), season_obp(pid, 2025)
        if f24 and s24 and f25 and s25:
            gap24 = f24[0] - s24; gap25 = f25[0] - s25
            g24.append(gap24); g25.append(gap25)
            gaps_abs.append(abs(gap24)); gaps_abs.append(abs(gap25))
            rows.append((name[:18], s24, f24[0], gap24, s25, f25[0], gap25))
    print(f"Batters with full 2024+2025 i01+season data: {len(rows)}\n")
    print(f"  {'batter':<18}{'24season':>9}{'24 i01':>8}{'24gap':>8}{'25season':>9}{'25 i01':>8}{'25gap':>8}")
    for nm, s24, f24, g_24, s25, f25, g_25 in sorted(rows, key=lambda x: -abs(x[3]))[:18]:
        print(f"  {nm:<18}{s24:>9.3f}{f24:>8.3f}{g_24:>+8.3f}{s25:>9.3f}{f25:>8.3f}{g_25:>+8.3f}")
    if gaps_abs:
        mean_abs = sum(gaps_abs)/len(gaps_abs)
        print(f"\n  1. SIZE: mean |i01 OBP - season OBP| = {mean_abs:.3f}")
        print(f"     (a batter's 1st-inning OBP sits ~{mean_abs*1000:.0f} pts from his season OBP)")
        r = corr(g24, g25)
        print(f"  2. STABILITY: corr(2024 gap, 2025 gap) = {r:+.3f}")
        print(f"     (r near 0 => the gap is NOISE, not a real trait => feature is DEAD)")
        print(f"     (r >= ~0.3 => a persistent 1st-inning skill worth building)")


if __name__ == "__main__":
    main()

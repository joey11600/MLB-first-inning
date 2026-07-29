"""Section C, redone with the CORRECT columns (nrfi_prob / yrfi_prob) and
the dashboard's own 1-dp-percent rounding."""
import csv, json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tracker
T = json.load(open("data/thresholds.json", encoding="utf-8"))

def chip_units(p_pct, american, t=T):
    p = p_pct/100.0
    if not (0 < p < 1): return 0.0
    b = american/100.0 if american > 0 else 100.0/abs(american)
    full = (p*b - (1-p))/b
    if not (full > 0): return 0.0
    bank = t.get("kellyCurrentBankrollUnits") or t.get("kellyBankrollUnits") or 100
    frac = min(full*t["kellyFraction"], t.get("kellyMaxStakeFrac",0.1))
    u = bank*frac
    return 0.0 if u < t.get("kellyMinStakeUnits",0.1) else u

rows = list(csv.DictReader(open("data/picks_2026.csv", newline="", encoding="utf-8")))
byday = defaultdict(list)
skipped = defaultdict(int)
for r in rows:
    if (r.get("pick_strength") or "").strip().upper() != "STRONG": skipped["not strong"]+=1; continue
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): skipped["no side"]+=1; continue
    col = "market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    o = (r.get(col) or "").strip()
    if not o: skipped["no price"]+=1; continue
    pcol = "nrfi_prob" if side=="NRFI" else "yrfi_prob"
    v = (r.get(pcol) or "").strip()
    if not v: skipped["no prob"]+=1; continue
    try:
        p = float(v); am = float(o)
    except ValueError: skipped["parse"]+=1; continue
    if p > 1: p /= 100.0
    p_pct = round(p*1000)/10.0          # dashboard's rounding
    byday[r["date"]].append((p_pct, am, f"{r['away_team']}@{r['home_team']}", side))
print("row filter:", dict(skipped), " usable days:", len(byday))

def odds_str(am):
    return (f"+{int(am)}" if am > 0 else str(int(am)))

days=bind=0; over_tot=0.0; unfunded_rows=0; total_rows=0
worst=None
detail=[]
for d in sorted(byday):
    sl = byday[d]; days += 1
    tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
    tracker._daily_committed[d] = 0.0
    tt=tc=0.0; z=0; trimmed=0
    for p_pct, am, g, side in sl:
        tr = tracker.kelly_stake_units(p_pct/100.0, odds_str(am), game_date=d)
        tr = 0.0 if tr is None else tr
        u = chip_units(p_pct, am)
        tt += tr; tc += u; total_rows += 1
        if u > 0 and tr == 0: z += 1; unfunded_rows += 1
        elif u - tr > 0.05: trimmed += 1
    if tc - tt > 0.05:
        bind += 1; over_tot += (tc-tt)
        if worst is None or (tc-tt) > worst[3]:
            worst = (d, tc, tt, tc-tt, len(sl), z, trimmed)
    detail.append((d, len(sl), tc, tt, z))

print()
print(f"days with >=1 priced STRONG bet : {days}")
print(f"days where the daily cap binds  : {bind}  ({100*bind/days:.0f}%)")
print(f"total rows                      : {total_rows}")
print(f"rows the chip advertises but the tracker funds at 0.00u : {unfunded_rows}")
print(f"summed over-advertisement across all days : {over_tot:.2f}u")
if worst:
    print(f"worst day {worst[0]}: {worst[4]} bets, chip {worst[1]:.2f}u vs tracker {worst[2]:.2f}u"
          f" (over by {worst[3]:.2f}u); {worst[5]} rows unfunded, {worst[6]} trimmed")
print()
print("binding days (chip total vs tracker total):")
for d,n,tc,tt,z in detail:
    if tc-tt > 0.05:
        print(f"   {d}  n={n}  chip {tc:6.2f}u  tracker {tt:6.2f}u  over {tc-tt:5.2f}u  unfunded={z}")

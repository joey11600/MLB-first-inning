"""INDEPENDENT verification of the BoardRow StakeChip daily-cap claim.
Read-only. Recomputes the chip arithmetic from scratch (not transcribed)
and diffs vs tracker.kelly_stake_units WITH game_date (daily cap active).
"""
import csv, json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tracker

T = json.load(open("data/thresholds.json", encoding="utf-8"))

def js_toFixed1(x):
    # emulate JS Number.prototype.toFixed(1) closely enough (round-half-even
    # on the binary value); python format uses the same binary value.
    return f"{x:.1f}"

def chip_units(p, american, t=T):
    """From scratch, following BoardRow.tsx:895-905 semantics."""
    b = american/100.0 if american > 0 else 100.0/abs(american)
    full = (p*b - (1-p))/b
    if not (full > 0):
        return None, "no edge"
    bank = t.get("kellyCurrentBankrollUnits") or t.get("kellyBankrollUnits") or 100
    frac = min(full * t["kellyFraction"], t.get("kellyMaxStakeFrac", 0.1))
    units = bank * frac
    if units < t.get("kellyMinStakeUnits", 0.1):
        return None, "(chip hidden)"
    return units, js_toFixed1(units) + "u"

print("="*78)
print("A. Static read: which caps does each site apply?")
print("="*78)
src = open("dashboard/components/BoardRow.tsx", encoding="utf-8").read()
live = src[src.index("const bank = t.kellyCurrentBankrollUnits"):]
live = live[:live.index("stake up to")]
print("   BoardRow live path source:")
for ln in live.strip().splitlines():
    print("      " + ln.strip())
print(f"   -> mentions kellyMaxDailyFrac? {'kellyMaxDailyFrac' in live}")
sim = open("dashboard/lib/kelly-sim.ts", encoding="utf-8").read()
print(f"   kelly-sim.ts mentions maxDailyFrac? {'maxDailyFrac' in sim}")
print(f"   tracker.kelly_stake_units applies KELLY_MAX_DAILY_FRAC = {tracker.KELLY_MAX_DAILY_FRAC}")

print()
print("="*78)
print("B. Worked slate, bank=100u, budget = %.2fu" % (100*T["kellyMaxDailyFrac"]))
print("="*78)
tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
D = "2099-02-02"; tracker._daily_committed[D] = 0.0
slate = [(0.7128,-115),(0.65,-140),(0.62,-120),(0.58,-110)]
tc = tt = 0.0
for p,o in slate:
    tr = tracker.kelly_stake_units(p, str(o), game_date=D)
    u, shown = chip_units(p, o)
    tc += (u or 0.0); tt += (tr or 0.0)
    print(f"   p={p:<7}{o:>6}   chip: 'stake up to {shown}'      tracker funds: {tr:.2f}u"
          f"{'   <== CHIP ADVERTISES A BET THAT WILL NOT BE PLACED' if (u or 0)>0 and (tr or 0)==0 else ('   <== chip overstates' if abs((u or 0)-(tr or 0))>0.05 else '')}")
print(f"   TOTAL  chip {tc:.2f}u   tracker {tt:.2f}u   over-advertised {tc-tt:.2f}u")

print()
print("="*78)
print("C. Does the cap bind in REAL slates?  (STRONG rows per day, 2026)")
print("="*78)
rows = list(csv.DictReader(open("data/picks_2026.csv", newline="", encoding="utf-8")))
byday = defaultdict(list)
for r in rows:
    if (r.get("pick_strength") or "").strip().upper() != "STRONG": continue
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    col = "market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    o = (r.get(col) or "").strip()
    if not o: continue
    try:
        p = float(r.get("nrfi_probability") or 0)/(100 if float(r.get("nrfi_probability") or 0)>1 else 1)
    except Exception:
        continue
    p_side = p if side=="NRFI" else 1-p
    try: am = float(o)
    except ValueError: continue
    byday[r["date"]].append((p_side, am))

bind = 0; days = 0; worst = None
for d in sorted(byday):
    sl = byday[d]
    if not sl: continue
    days += 1
    tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
    tracker._daily_committed[d] = 0.0
    tot_tr = 0.0; tot_chip = 0.0; zeroed = 0
    for p, am in sl:
        tr = tracker.kelly_stake_units(p, str(int(am)), game_date=d) or 0.0
        u,_ = chip_units(p, am); u = u or 0.0
        tot_tr += tr; tot_chip += u
        if u > 0 and tr == 0: zeroed += 1
    if tot_chip - tot_tr > 0.05:
        bind += 1
        if worst is None or (tot_chip-tot_tr) > worst[1]-worst[2]:
            worst = (d, tot_chip, tot_tr, len(sl), zeroed)
print(f"   days with >=1 priced STRONG bet: {days}")
print(f"   days where chip total > tracker total (cap binds): {bind}")
if worst:
    print(f"   worst day {worst[0]}: {worst[3]} bets, chip {worst[1]:.2f}u vs tracker {worst[2]:.2f}u,"
          f" rows advertised-but-unfunded = {worst[4]}")
sizes = defaultdict(int)
for d in byday: sizes[len(byday[d])] += 1
print(f"   slate-size histogram (priced STRONG per day): {dict(sorted(sizes.items()))}")

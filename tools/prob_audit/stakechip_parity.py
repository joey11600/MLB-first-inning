"""Read-only: reimplement dashboard StakeChip / kelly-sim.ts arithmetic in
Python and diff against tracker.kelly_stake_units on real board rows."""
import csv
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tracker  # noqa: E402

SEP = "=" * 78
T = json.load(open(os.path.join("data", "thresholds.json"), encoding="utf-8"))


def norm_am(raw):
    s = (raw or "").strip()
    if not s:
        return ""
    return s if s[0] in "+-" else s


def stakechip(p_pct, american, t=T):
    """Literal transcription of BoardRow.tsx StakeChip lines 876-903."""
    try:
        am = float(norm_am(str(american)))
    except ValueError:
        return None
    if am == 0:
        return None
    p = p_pct / 100.0
    if not (0 < p < 1):
        return None
    b = am / 100 if am > 0 else 100 / abs(am)
    full = (p * b - (1 - p)) / b
    if not (full > 0):
        return "no edge"
    bank = t.get("kellyCurrentBankrollUnits", t.get("kellyBankrollUnits", 100))
    frac = min(full * t["kellyFraction"], t.get("kellyMaxStakeFrac", 0.1))
    units = bank * frac
    if units < t.get("kellyMinStakeUnits", 0.1):
        return None          # chip renders NOTHING
    return units             # displayed as units.toFixed(1)


print(SEP)
print(" 1. StakeChip vs tracker on a grid (same p, same price, no daily cap)")
print(SEP)
tracker.kelly_reset_daily_committed()
tracker._bankroll_cache = 100.0
worst = 0.0
for p in (0.36, 0.42, 0.55, 0.6128, 0.65, 0.7128, 0.80):
    for odds in (-160, -140, -120, -110, 100, 130):
        tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
        tr = tracker.kelly_stake_units(p, str(odds))
        # dashboard sees p only to 1 decimal PERCENT
        p_pct = round(p * 100, 1)
        ch = stakechip(p_pct, odds)
        ch_n = ch if isinstance(ch, float) else 0.0
        d = abs((tr or 0.0) - ch_n)
        worst = max(worst, d)
        flag = "  <-- DIFF" if d > 0.005 else ""
        print(f"   p={p:<7} {odds:>5}  tracker={str(tr):>6}  chip={ch if not isinstance(ch,float) else f'{ch:.4f}'}"
              f"  shown='{'' if not isinstance(ch,float) else f'{ch:.1f}u'}'  d={d:.4f}{flag}")
print(f"   MAX |tracker - chip| on the grid = {worst:.4f}u  (pure 1-dp percent rounding)")

print()
print(SEP)
print(" 2. THE DAILY CAP THE CHIP DOES NOT APPLY -- worked example")
print(SEP)
tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
tracker._daily_committed["2099-02-02"] = 0.0
slate = [(0.7128, -115), (0.65, -140), (0.62, -120), (0.58, -110)]
tot_chip = tot_tr = 0.0
for p, o in slate:
    tr = tracker.kelly_stake_units(p, str(o), game_date="2099-02-02") or 0.0
    ch = stakechip(round(p * 100, 1), o)
    ch = ch if isinstance(ch, float) else 0.0
    tot_chip += ch
    tot_tr += tr
    print(f"   p={p} {o:>5}: chip says 'stake up to {ch:.1f}u'   tracker funds {tr:.2f}u")
print(f"   slate total: chip {tot_chip:.2f}u   tracker {tot_tr:.2f}u   "
      f"(daily budget = {100*T['kellyMaxDailyFrac']:.2f}u)")

print()
print(SEP)
print(" 3. REPLAY-STAKE CHIP: what the board actually prints on past rows")
print(SEP)
rec = json.load(open(os.path.join("data", "season_record.json"), encoding="utf-8"))
ledger = list(csv.DictReader(open(os.path.join("data", "picks_2026.csv"),
                                  newline="", encoding="utf-8")))
led = {}
for r in ledger:
    led[(r["date"], f"{r['away_team']}@{r['home_team']}")] = r

print(f"   {'date':<11}{'game':<10}{'side':<6}{'chip shows':>14}{'ledger units':>14}{'ledger P&L':>12}")
rows_out = []
for key in ("real", "projected"):
    for day in rec[key]["days"]:
        for g in day["games"]:
            if g["record"].get("action") != "BET":
                continue
            rows_out.append((key, day["date"], g["game"], g["side"],
                             g["record"].get("stake"),
                             (g.get("ledger") or {}).get("unitsRisked"),
                             (g.get("ledger") or {}).get("pnl")))
    break   # 'real' wins the lookup for any date it covers
rows_out.sort(key=lambda x: -(x[4] or 0))
for r in rows_out[:10]:
    print(f"   {r[1]:<11}{r[2]:<10}{r[3]:<6}{('staked %.2fu' % r[4]):>14}"
          f"{str(r[5]):>14}{str(r[6]):>12}")
mism = [r for r in rows_out if r[5] is not None and abs((r[4] or 0) - r[5]) > 0.011]
print(f"   rows where the chip's 'staked' number != the ledger's units_risked: "
      f"{len(mism)} / {len(rows_out)}")
if mism:
    tot_chip = sum(r[4] for r in mism)
    tot_led = sum(r[5] for r in mism)
    print(f"   summed: chip {tot_chip:.2f}u vs ledger {tot_led:.2f}u")

print()
print(SEP)
print(" 4. Do the chip's sim stakes respect the operator's REAL 100u bank?")
print(SEP)
allb = [g["record"]["stake"] for d in rec["real"]["days"] for g in d["games"]
        if g["record"].get("action") == "BET"]
over10 = [s for s in allb if s > 100 * T["kellyMaxStakeFrac"]]
print(f"   real-replay stakes: n={len(allb)}, max={max(allb):.2f}u")
print(f"   stakes above the live per-bet cap (10% of a 100u bank = 10.00u): {len(over10)}")
print(f"   (they are legal INSIDE the sim, whose bank reached "
      f"{rec['real']['days'][-1].get('simBankAfter')}u -- but the chip prints them "
      f"on a board whose live bank is {T['kellyCurrentBankrollUnits']}u)")

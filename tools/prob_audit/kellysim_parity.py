"""Read-only transcription of dashboard/lib/kelly-sim.ts simulateKelly()
into Python, run on the real ledger, to check its internal arithmetic and
its agreement with tracker's semantics."""
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tracker  # noqa: E402

T = json.load(open(os.path.join("data", "thresholds.json"), encoding="utf-8"))
CFG = dict(fraction=T["kellyFraction"], bankrollUnits=T["kellyBankrollUnits"],
           maxStakeFrac=T["kellyMaxStakeFrac"], maxDailyFrac=T["kellyMaxDailyFrac"],
           minStakeUnits=T["kellyMinStakeUnits"])
SEP = "=" * 78


def num(s):
    if s is None:
        return None
    t = str(s).strip().replace("−", "-").replace("–", "-")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def ppu(a):
    return a / 100 if a > 0 else 100 / abs(a)


def kfrac(p, a):
    b = ppu(a)
    if not (b > 0) or not (0 < p < 1):
        return 0.0
    return max((p * b - (1 - p)) / b, 0.0)


rows = list(csv.DictReader(open(os.path.join("data", "picks_2026.csv"),
                                newline="", encoding="utf-8")))

byday = defaultdict(list)
skipped_no_price = 0
for r in rows:
    graded = (r.get("graded_result") or "").strip().upper()
    if graded not in ("WIN", "LOSS"):
        continue
    if (r.get("bet_placed") or "").strip().upper() != "Y":
        continue
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI", "YRFI"):
        continue
    p = num(r["nrfi_prob"] if side == "NRFI" else r["yrfi_prob"])
    odds = num(r["market_nrfi_odds"] if side == "NRFI" else r["market_yrfi_odds"])
    if p is None or odds is None or odds == 0:
        skipped_no_price += 1
        continue
    byday[(r.get("date") or "").strip()].append(
        dict(p=p, odds=odds, win=graded == "WIN", side=side,
             game=f"{r['away_team']}@{r['home_team']}"))

days = sorted(byday)
bank = peak = CFG["bankrollUnits"]
bets = wins = losses = skipped_zero = 0
flat_all = 0.0            # what kelly-sim.ts computes
flat_funded = 0.0         # the honest apples-to-apples figure
largest = 0.0
maxdd = 0.0
cap_zeroed = edge_zeroed = 0
for d in days:
    morning = bank
    committed = 0.0
    pnl = 0.0
    for b in byday[d]:
        flat_all += ppu(b["odds"]) if b["win"] else -1.0
        f = min(kfrac(b["p"], b["odds"]) * CFG["fraction"], CFG["maxStakeFrac"])
        stake = morning * f
        room = morning * CFG["maxDailyFrac"] - committed
        pre_cap = stake
        stake = min(stake, max(room, 0.0))
        if stake < CFG["minStakeUnits"]:
            skipped_zero += 1
            if pre_cap >= CFG["minStakeUnits"]:
                cap_zeroed += 1
            else:
                edge_zeroed += 1
            continue
        stake = round(stake * 100) / 100
        committed += stake
        bets += 1
        largest = max(largest, stake)
        flat_funded += ppu(b["odds"]) if b["win"] else -1.0
        if b["win"]:
            wins += 1
            pnl += stake * ppu(b["odds"])
        else:
            losses += 1
            pnl -= stake
    bank += pnl
    peak = max(peak, bank)
    if peak > 0:
        maxdd = max(maxdd, (peak - bank) / peak * 100)
    if bank <= 0:
        break

print(SEP)
print(" kelly-sim.ts simulateKelly() transcribed and run on data/picks_2026.csv")
print(SEP)
print(f"   days simulated       {len(days)}")
print(f"   bets funded          {bets}   ({wins}W-{losses}L)")
print(f"   skippedNoPrice       {skipped_no_price}")
print(f"   skippedZeroEdge      {skipped_zero}")
print(f"     ...of which truly zero-edge   {edge_zeroed}")
print(f"     ...of which DAILY-CAP zeroed  {cap_zeroed}   <-- counted as 'zero edge'")
print(f"   largestStake         {largest:.2f}u")
print(f"   finalBank            {bank:.2f}u   profit {bank - CFG['bankrollUnits']:+.2f}u")
print(f"   maxDrawdownPct       {maxdd:.2f}%")
print()
print(f"   flatProfit as kelly-sim.ts computes it (ALL eligible rows) : {flat_all:+.2f}u")
print(f"   flatProfit over ONLY the games Kelly funded                : {flat_funded:+.2f}u")
print(f"   -> the 'apples-to-apples' comparison is off by {flat_all - flat_funded:+.2f}u")
print(f"      because {skipped_zero} unfunded games are in the flat number but not the Kelly one.")

print()
print(SEP)
print(" Does the sim's per-bet cap ever bind, and does it exceed the live cap?")
print(SEP)
over = [x for x in [] ]
print(f"   largest sim stake {largest:.2f}u vs live per-bet ceiling "
      f"{100*CFG['maxStakeFrac']:.2f}u on the operator's real {T['kellyCurrentBankrollUnits']}u bank")

print()
print(SEP)
print(" Intraday compounding: sim uses MORNING bank; tracker re-reads the bank")
print(" every 5-minute import batch, so a game that settles at 8pm moves the")
print(" bank used to size a 10pm game.  Magnitude of that divergence:")
print(SEP)
multi = [(d, len(v)) for d, v in byday.items() if len(v) > 1]
print(f"   days with >1 funded-eligible bet: {len(multi)} / {len(days)}")
print(f"   max bets on one day: {max((n for _, n in multi), default=0)}")
worst = 0.0
for d, v in byday.items():
    if len(v) < 2:
        continue
    # if the first bet settles before the last is sized, the bank moves by its pnl
    f0 = min(kfrac(v[0]["p"], v[0]["odds"]) * CFG["fraction"], CFG["maxStakeFrac"])
    s0 = 100 * f0
    move = s0 * ppu(v[0]["odds"]) if v[0]["win"] else -s0
    worst = max(worst, abs(move) / 100)
print(f"   worst single-bet intraday bank move on a 100u bank: {100*worst:.2f}% "
      f"-> up to {100*worst:.2f}% error in a later same-day stake")

import csv, sys
from pathlib import Path
ROOT=Path(".").resolve(); sys.path.insert(0,str(ROOT))
import mlb_first_inning_predictor as P
from calibration import ProbCalibrator, CIRCalibrator
from tools.season_replay import load_season, decide, simulate, payout, implied
from tools.gate_validation import walk_forward_probs
rows,_=load_season()
raw=list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv",encoding="utf-8")))
cal=ProbCalibrator.load(ROOT/"data"/"calibration_v2.json")
ins=[cal.predict(r["raw"]) for r in rows]
wf=walk_forward_probs(rows)
gate=P._LR_STRONG_YRFI_P

def avg_sp(r):
    L=raw[r["rid"]]
    return (L.get("away_pitcher_q") or "").strip()=="avg" or (L.get("home_pitcher_q") or "").strip()=="avg"
def nolam(r): return r["lambda"] is None

def flat(rs,ps):
    n=w=0; pl=0.0
    for r,p in zip(rs,ps):
        if p is None or not decide(p,r,gate): continue
        if r["yrfi_odds"] is None: continue
        n+=1; w+=r["yrfi_hit"]
        pl += payout(r["yrfi_odds"]) if r["yrfi_hit"] else -1.0
    return n,w,pl

for name,probs in (("IN-SAMPLE",ins),("WALK-FWD",wf)):
    print(f"\n### {name}  (gate {gate}, real prices only)")
    for lab,keep in (("as-is (no guards)", lambda r: True),
                     ("+ avg-SP guard (prod)", lambda r: not avg_sp(r)),
                     ("+ avg-SP + require lambda", lambda r: not avg_sp(r) and not nolam(r))):
        rs=[r for r in rows if keep(r)]; ps=[p for r,p in zip(rows,probs) if keep(r)]
        n,w,pl=flat(rs,ps)
        s=simulate(rs,ps,gate,require_real_price=True)
        print(f"  {lab:<26} bets={n:>4} W={w:>4} hit={100*w/n if n else 0:>5.1f}%"
              f" flat={pl:>+8.2f}u ROI={100*pl/n if n else 0:>+6.2f}%  kelly={s['profit']:>+8.2f}u")

# which rows the guard removes, with their P&L
print("\n### rows removed by the avg-SP guard (in-sample, gate 0.40, priced only)")
tot=0.0
for r,p in zip(rows,ins):
    if not avg_sp(r) or not decide(p,r,gate) or r["yrfi_odds"] is None: continue
    d=payout(r["yrfi_odds"]) if r["yrfi_hit"] else -1.0; tot+=d
    print(f"   {r['date']} {r['away']}@{r['home']:<5} p={p:.4f} odds={r['yrfi_odds']:>7.0f} "
          f"{'W' if r['yrfi_hit'] else 'L'} {d:+.2f}u")
print(f"   TOTAL {tot:+.2f}u")
print("\nselected rows with NO lambda (floor silently skipped):",
      sum(1 for r,p in zip(rows,ins) if decide(p,r,gate) and nolam(r)),
      " of ", sum(1 for r,p in zip(rows,ins) if decide(p,r,gate)))

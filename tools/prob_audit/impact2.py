import csv, sys
from pathlib import Path
ROOT=Path(".").resolve(); sys.path.insert(0,str(ROOT))
import mlb_first_inning_predictor as P
from calibration import ProbCalibrator
from tools.season_replay import load_season, decide, simulate, payout
from tools.gate_validation import walk_forward_probs, block_bootstrap_flat
rows,_=load_season()
raw=list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv",encoding="utf-8")))
cal=ProbCalibrator.load(ROOT/"data"/"calibration_v2.json")
ins=[cal.predict(r["raw"]) for r in rows]
wf=walk_forward_probs(rows)
gate=P._LR_STRONG_YRFI_P
def avg_sp(r):
    L=raw[r["rid"]]
    return (L.get("away_pitcher_q") or "").strip()=="avg" or (L.get("home_pitcher_q") or "").strip()=="avg"
for name,probs in (("IN-SAMPLE",ins),("WALK-FWD",wf)):
    print(f"\n### {name} gate {gate}, real prices only")
    for lab,keep in (("as-is (no guards)",lambda r:True),("+ avg-SP guard (prod)",lambda r:not avg_sp(r))):
        pairs=[(r,p) for r,p in zip(rows,probs) if p is not None and keep(r)]
        rs=[x[0] for x in pairs]; ps=[x[1] for x in pairs]
        bets=[r for r,p in pairs if decide(p,r,gate) and r["yrfi_odds"] is not None]
        n=len(bets); w=sum(b["yrfi_hit"] for b in bets)
        pl=sum(payout(b["yrfi_odds"]) if b["yrfi_hit"] else -1.0 for b in bets)
        s=simulate(rs,ps,gate,require_real_price=True)
        lo,hi=block_bootstrap_flat([{"date":b["date"],"odds":b["yrfi_odds"],"win":b["yrfi_hit"]} for b in bets])
        print(f"  {lab:<24} bets={n:>4} hit={100*w/n if n else 0:>5.1f}% flat={pl:>+8.2f}u"
              f" ROI={100*pl/n if n else 0:>+6.2f}%  CI90=[{lo:+.1f},{hi:+.1f}]  kelly={s['profit']:>+9.2f}u")

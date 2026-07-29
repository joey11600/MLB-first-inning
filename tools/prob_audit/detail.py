import csv, sys
from pathlib import Path
from collections import Counter
ROOT = Path(".").resolve(); sys.path.insert(0,str(ROOT))
import mlb_first_inning_predictor as P
from calibration import ProbCalibrator
from tools.season_replay import load_season, decide
rows,_=load_season()
cal=ProbCalibrator.load(ROOT/"data"/"calibration_v2.json")
probs=[cal.predict(r["raw"]) for r in rows]
raw=list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv",encoding="utf-8")))

for gate in (0.33,0.36,0.40,0.44):
    sel=[(r,p) for r,p in zip(rows,probs) if decide(p,r,gate)]
    c=Counter((raw[r["rid"]]["pick_side"],raw[r["rid"]]["pick_strength"]) for r,_ in sel)
    print(f"gate {gate}: n={len(sel)}  {dict(c)}")
print()

gate=P._LR_STRONG_YRFI_P
sel=[(r,p) for r,p in zip(rows,probs) if decide(p,r,gate)]
print("=== the 15 mismatches at live gate 0.40 ===")
print(f"{'date':<11}{'game':<12}{'ledger':<26}{'replay_p':>9}{'ledger_p':>9}{'lam_csv':>9}{'q(a/h/ab/hb)':>22}{'bet':>4}")
for r,p in sel:
    L=raw[r["rid"]]
    if L["pick_side"]=="YRFI" and L["pick_strength"]=="STRONG": continue
    q="/".join((L[k] or "?") for k in ["away_pitcher_q","home_pitcher_q","away_batting_q","home_batting_q"])
    print(f"{L['date']:<11}{L['away_team']+'@'+L['home_team']:<12}"
          f"{(L['pick_side']+' '+L['pick_strength']+' | '+L['pick_label'])[:25]:<26}"
          f"{p:>9.4f}{float(L['nrfi_prob'] or 'nan'):>9.4f}{L['lambda_lr_total']:>9}{q:>22}{L['bet_placed']:>4}")

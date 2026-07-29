import csv, sys
from pathlib import Path
from collections import Counter
ROOT=Path(".").resolve(); sys.path.insert(0,str(ROOT))
import mlb_first_inning_predictor as P
from calibration import ProbCalibrator
from tools.season_replay import load_season
from tools.gate_validation import select, walk_forward_probs
rows,_=load_season()
cal=ProbCalibrator.load(ROOT/"data"/"calibration_v2.json")
ins=[cal.predict(r["raw"]) for r in rows]
wf=walk_forward_probs(rows)
raw=list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv",encoding="utf-8")))
# select() drops rid, so re-implement inline to keep rid
def sel_idx(probs,gate):
    out=[]
    for i,(r,p) in enumerate(zip(rows,probs)):
        if p is None: continue
        fl=P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR,r["wx_temp"],r["wx_wind"],r["wx_dome"])
        if r["lambda"] is not None and r["lambda"]<fl: continue
        if p>=gate: continue
        out.append(i)
    return out
for name,probs in (("in-sample",ins),("walk-fwd",wf)):
    for gate in (0.33,0.36,0.40,0.44):
        idx=sel_idx(probs,gate)
        c=Counter((raw[rows[i]["rid"]]["pick_side"],raw[rows[i]["rid"]]["pick_strength"]) for i in idx)
        npriced=sum(1 for i in idx if rows[i]["yrfi_odds"] is not None)
        print(f"{name} gate {gate}: n={len(idx)} (real-price {npriced})  {dict(c)}")

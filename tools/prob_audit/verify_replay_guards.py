import csv, sys
from pathlib import Path
from collections import Counter, defaultdict
ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT))
import mlb_first_inning_predictor as P
from calibration import ProbCalibrator
from tools.season_replay import load_season, decide

rows, skipped = load_season()
cal = ProbCalibrator.load(ROOT/"data"/"calibration_v2.json")
probs = [cal.predict(r["raw"]) for r in rows]
gate = P._LR_STRONG_YRFI_P
print("gate =", gate, " rows loaded =", len(rows), " skipped =", skipped)

sel = [(r,p) for r,p in zip(rows,probs) if decide(p,r,gate)]
print("selected by decide() at live gate:", len(sel))

raw = list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv", encoding="utf-8")))
c = Counter()
detail = defaultdict(list)
for r,p in sel:
    led = raw[r["rid"]]
    key = (led.get("pick_side",""), led.get("pick_strength",""), led.get("pick_label",""))
    c[key]+=1
    detail[key].append((led["date"], led["away_team"], led["home_team"], p,
                        led.get("nrfi_prob"), led.get("away_pitcher_q"), led.get("home_pitcher_q"),
                        led.get("away_batting_q"), led.get("home_batting_q"), led.get("bet_placed")))
for k,v in sorted(c.items(), key=lambda kv:-kv[1]):
    print(f"{v:>4}  side={k[0]!r} strength={k[1]!r} label={k[2]!r}")
print()
# per-row data_pts recomputed from ledger q columns
def dpts(led):
    return sum((led.get(k) or "").strip()!="avg" for k in
               ["away_pitcher_q","home_pitcher_q","away_batting_q","home_batting_q"])
zero = [(r,p) for r,p in sel if dpts(raw[r["rid"]])==0]
print("selected rows with recomputed data_pts==0:", len(zero))
for r,p in zero:
    led=raw[r["rid"]]
    print("   ", led["date"], led["away_team"],"@",led["home_team"], "ledger=",led["pick_side"],led["pick_strength"],"/",led["pick_label"],
          " replay_p=%.4f"%p, " ledger_nrfi_prob=",led["nrfi_prob"])

import csv, sys, json
from pathlib import Path
ROOT=Path(".").resolve(); sys.path.insert(0,str(ROOT))
import tools.export_season_record as E
raw=list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv",encoding="utf-8")))
def avg_sp(rid):
    L=raw[rid]
    return (L.get("away_pitcher_q") or "").strip()=="avg" or (L.get("home_pitcher_q") or "").strip()=="avg"

orig=E.disposition
def patched(rows,probs,*,side,gate,fill):
    out=[]
    for tup in orig(rows,probs,side=side,gate=gate,fill=fill):
        r,p,why,extra=tup
        if why=="candidate" and avg_sp(r["rid"]):
            out.append((r,p,"no-pitcher-data",None))
        else: out.append(tup)
    return out

import io, contextlib
def run():
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): E.main()
    return json.load(open(ROOT/"data"/"season_record.json",encoding="utf-8"))

before=json.load(open(ROOT/"data"/"season_record.json",encoding="utf-8"))
E.disposition=patched
after=run()
# restore file
import tempfile,os
E.disposition=orig
run()

for scen in ("projected","real"):
    b,a=before[scen],after[scen]
    print(f"\n### {scen}")
    for k in ("bets","wins","losses","hitRate","flatProfit","edgePts","selectedBets"):
        print(f"   {k:<14} {b[k]!s:>10}  ->  {a[k]!s:>10}")
    print(f"   sim.profit     {b['sim']['profit']!s:>10}  ->  {a['sim']['profit']!s:>10}")
    print(f"   sim.maxDD      {b['sim']['maxDrawdownPct']!s:>10}  ->  {a['sim']['maxDrawdownPct']!s:>10}")
    print(f"   floor.flat     {b['floor']['flatProfit']!s:>10}  ->  {a['floor']['flatProfit']!s:>10}")

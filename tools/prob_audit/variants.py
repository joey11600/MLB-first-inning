import json
rec=json.load(open('data/season_record.json'))
side=rec['real']
rows=[(d['date'],g) for d in side['days'] for g in d['games'] if g['record']['action']=='BET']
print("BET dispositions:", len(rows))
for cond,name in [
  (lambda g: g.get('ledger') is not None, "ledger present"),
  (lambda g: (g.get('ledger') or {}).get('placed') is True, "ledger placed"),
  (lambda g: True, "all"),
]:
    sel=[(d,g) for d,g in rows if cond(g)]
    mm=[(d,g) for d,g in sel if (g.get('ledger') or {}).get('unitsRisked') != g['record'].get('stake')]
    print(f"  {name}: n={len(sel)} mismatch={len(mm)} sumStake={sum(g['record']['stake'] for _,g in sel):.2f} sumLedger={sum((g.get('ledger') or {}).get('unitsRisked') or 0 for _,g in sel):.2f}")
# stake sum over mismatching-with-placed-ledger only
sel=[(d,g) for d,g in rows if (g.get('ledger') or {}).get('placed') is True]
print("sum stake on ledger-placed BET rows: %.2f" % sum(g['record']['stake'] for _,g in sel))

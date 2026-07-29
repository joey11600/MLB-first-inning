import json
rec=json.load(open('data/season_record.json'))
for name in ('real','projected'):
    side=rec[name]
    skip_but_placed=0; skip_but_placed_units=0.0
    bet_and_placed=0; bet_not_placed=0
    for d in side['days']:
        for g in d['games']:
            L=g.get('ledger') or {}
            placed = L.get('placed') is True
            act=g['record']['action']
            if act=='SKIP' and placed:
                skip_but_placed+=1; skip_but_placed_units += (L.get('unitsRisked') or 0)
            if act=='BET' and placed: bet_and_placed+=1
            if act=='BET' and not placed: bet_not_placed+=1
    print(f"{name}: replay SKIP but operator PLACED = {skip_but_placed} rows ({skip_but_placed_units:.2f}u real risk shown as 'model passes')")
    print(f"        replay BET & placed = {bet_and_placed}; replay BET but never placed = {bet_not_placed}")

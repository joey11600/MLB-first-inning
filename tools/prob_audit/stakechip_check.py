import json, collections
rec = json.load(open('data/season_record.json'))

for sidename in ('real','projected'):
    side = rec.get(sidename)
    if not side: continue
    days = side['days']
    print(f"=== {sidename}: {len(days)} days {days[0]['date']}..{days[-1]['date']} kellyFrac={rec['kellyFraction']} startBank={rec['startBank']}")
    nbet=0; mismatch=0; over10=0; mx=0; mxg=None; sumstake=0.0; sumledger=0.0
    ledger_bet_rows=0
    for d in days:
        for g in d['games']:
            r=g['record']; L=g.get('ledger')
            if r['action']!='BET': continue
            nbet+=1
            st=r.get('stake')
            if st is not None:
                sumstake+=st
                if st>10.0+1e-9: over10+=1
                if st>mx: mx=st; mxg=(d['date'],g['game'],g['side'],st)
            if L and L.get('placed'):
                ledger_bet_rows+=1
                ur=L.get('unitsRisked')
                if ur is not None: sumledger+=ur
                if ur is None or abs((st or 0)-ur)>1e-6: mismatch+=1
    print(f"  BET dispositions={nbet}  ledger-placed among them={ledger_bet_rows}  stake!=unitsRisked={mismatch}")
    print(f"  sum replay stake={sumstake:.2f}u  sum ledger unitsRisked (placed rows)={sumledger:.2f}u")
    print(f"  stakes >10.00u: {over10}   max={mx:.2f} @ {mxg}")

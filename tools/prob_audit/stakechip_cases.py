import json
rec=json.load(open('data/season_record.json'))
def show(sidename,date):
    side=rec[sidename]
    d=next((x for x in side['days'] if x['date']==date),None)
    if not d: print(f"{sidename} {date}: no day"); return
    print(f"--- {sidename} {date}  simPnl={d['simPnl']} simBankAfter={d['simBankAfter']} flatPnl={d['flatPnl']}")
    for g in d['games']:
        r=g['record']; L=g.get('ledger')
        print(f"   {g['game']:<12} {g['side']:<5} act={r['action']:<4} stake={r.get('stake')} odds={r.get('odds')} pnl={r.get('pnl')} assumed={r.get('assumed')} | ledger={L}")
show('real','2026-07-27')
show('real','2026-07-08')
show('projected','2026-07-08')
show('projected','2026-04-15')

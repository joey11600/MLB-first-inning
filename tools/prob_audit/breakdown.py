import csv,sys
sys.path.insert(0,'.')
from tracker import american_to_prob
rows=list(csv.DictReader(open('data/picks_2026.csv',encoding='utf-8')))
def f(v):
    try: return float((v or '').strip())
    except: return None
tot=0; bad=[]
for r in rows:
    pick=(r.get('pick_side') or '').upper()
    if pick not in ('NRFI','YRFI'): continue
    odds=r['market_nrfi_odds'] if pick=='NRFI' else r['market_yrfi_odds']
    imp=american_to_prob(odds)
    if imp is None: continue
    tot+=1
    e=f(r.get('edge_on_pick')); p=f(r.get('nrfi_prob') if pick=='NRFI' else r.get('yrfi_prob'))
    if e is None or p is None: continue
    if abs(p-imp-e)>0.001: bad.append(r)
print('rows with pick+price:',tot,' mismatch:',len(bad))
from collections import Counter
print('bet_placed:',Counter((r['bet_placed'] or '_') for r in bad))
print('strength:',Counter(r['pick_strength'] for r in bad))
print('graded:',Counter((r['graded_result'] or 'UNGRADED') for r in bad))
print('dates:',sorted(Counter(r['date'] for r in bad).items())[-8:])
# back-solve check: does stored_edge + stored_implied land in [0,1] and differ from current p
import statistics
ds=[]
for r in bad:
    pick=r['pick_side']
    si=f(r['implied_nrfi_prob'] if pick=='NRFI' else r['implied_yrfi_prob'])
    e=f(r['edge_on_pick']); p=f(r['nrfi_prob'] if pick=='NRFI' else r['yrfi_prob'])
    ds.append((si+e, p))
print('backsolved p range:', min(a for a,_ in ds), max(a for a,_ in ds), ' all in [0,1]:', all(0<=a<=1 for a,_ in ds))
diffs=[abs(a-b) for a,b in ds]
print('mean |backsolved - current| pp:', round(statistics.mean(diffs)*100,2), 'max', round(max(diffs)*100,2))

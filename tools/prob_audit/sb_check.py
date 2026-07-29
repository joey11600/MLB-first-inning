import os, json, urllib.request, urllib.parse
env={}
for line in open('.env',encoding='utf-8'):
    line=line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
url=env.get('SUPABASE_URL'); key=env.get('SUPABASE_SERVICE_KEY') or env.get('SUPABASE_KEY') or env.get('SUPABASE_ANON_KEY')
print('url set:', bool(url), 'key set:', bool(key))
if not (url and key): raise SystemExit(0)

def q(path):
    req=urllib.request.Request(url.rstrip('/')+'/rest/v1/'+path,
        headers={'apikey':key,'Authorization':'Bearer '+key})
    return json.load(urllib.request.urlopen(req, timeout=30))

# the 13 rows: fetch by date+teams
targets=[('2026-05-05','LAD','HOU'),('2026-05-05','TEX','NYY'),('2026-05-05','ATL','SEA'),
         ('2026-05-05','CWS','LAA'),('2026-07-17','PIT','CLE'),('2026-07-18','MIA','MIL'),
         ('2026-07-25','TOR','BOS')]
for d,a,h in targets:
    rows=q(f'picks_2026?date=eq.{d}&away_team=eq.{a}&home_team=eq.{h}&select=date,away_team,home_team,pick_side,pick_label,market_nrfi_odds,market_yrfi_odds,edge_on_pick,bet_placed,clv_pct')
    for r in rows:
        print(r['date'],r['away_team'],r['home_team'],r['pick_label'],
              '| edge_on_pick =',repr(r['edge_on_pick']),
              '| nrfi',r['market_nrfi_odds'],'yrfi',r['market_yrfi_odds'],'bet',r['bet_placed'])

# April row lambda_lr_total
rows=q('picks_2026?date=eq.2026-04-05&select=away_team,home_team,lambda_lr_total,combined_lambda,nrfi_prob,yrfi_prob&limit=5')
print('\n2026-04-05 sample:')
for r in rows: print('  ',r)

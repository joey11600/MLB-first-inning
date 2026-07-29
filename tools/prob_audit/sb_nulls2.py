import json, urllib.request, urllib.parse
env={}
for line in open('.env',encoding='utf-8'):
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
url=env['SUPABASE_URL'].rstrip('/'); key=env['SUPABASE_SERVICE_KEY']
def get(qs):
    req=urllib.request.Request(url+'/rest/v1/picks_2026?'+urllib.parse.quote(qs,safe='=&.,()*'),
        headers={'apikey':key,'Authorization':'Bearer '+key})
    return json.load(urllib.request.urlopen(req,timeout=60))

rows=get('pick_side=in.(NRFI,YRFI)&edge_on_pick=is.null&select=date,away_team,home_team,pick_side,pick_label,market_nrfi_odds,market_yrfi_odds,bet_placed')
hit=[r for r in rows if (r['market_nrfi_odds'] if r['pick_side']=='NRFI' else r['market_yrfi_odds'])]
print('SUPABASE: NRFI/YRFI picks with NULL edge_on_pick :', len(rows))
print('SUPABASE: ...AND a captured price on PICKED side :', len(hit), '<- these render a visible "+0.0%" edge chip')
for r in sorted(hit,key=lambda x:x['date']):
    p=r['market_nrfi_odds'] if r['pick_side']=='NRFI' else r['market_yrfi_odds']
    print('  ',r['date'],r['away_team'],'@',r['home_team'],r['pick_label'],p,'bet='+str(r['bet_placed']))

lp=get('lambda_lr_total=is.null&pick_strength=eq.LINEUP%20PENDING&select=game_pk')
print('\nSUPABASE: NULL lambda_lr_total AND LINEUP PENDING:', len(lp), '(BoardRow:362 path -> latent)')
d=get('lambda_lr_total=is.null&select=date')
dates=sorted({r['date'] for r in d})
print('SUPABASE: NULL lambda_lr_total rows:', len(d), 'across', len(dates), 'dates', dates[0],'..',dates[-1],
      '-> ProjectionPanel shows 0.00 on every one')

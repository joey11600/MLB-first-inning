import json, urllib.request
env={}
for line in open('.env',encoding='utf-8'):
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
url=env['SUPABASE_URL'].rstrip('/'); key=env['SUPABASE_SERVICE_KEY']
def count(qs):
    req=urllib.request.Request(url+'/rest/v1/picks_2026?'+qs+'&select=game_pk',
        headers={'apikey':key,'Authorization':'Bearer '+key,'Prefer':'count=exact','Range':'0-0'})
    r=urllib.request.urlopen(req,timeout=30)
    return int(r.headers['Content-Range'].split('/')[-1])
print('total rows                          :', count('game_pk=not.is.null'))
for col in ['edge_on_pick','lambda_lr_total','combined_lambda','over_1_5_prob','under_1_5_prob','profit_loss_units','clv_pct','units_risked','nrfi_prob','yrfi_prob']:
    print(f'{col:<36}: NULL on {count(col+"=is.null")}')
print()
print('graded WIN/LOSS with NULL profit_loss_units:',
      count('graded_result=in.(WIN,LOSS)&profit_loss_units=is.null'))
print('pick NRFI/YRFI with NULL edge_on_pick      :',
      count('pick_side=in.(NRFI,YRFI)&edge_on_pick=is.null'))
print('  ...of those, bet_placed=Y                :',
      count('pick_side=in.(NRFI,YRFI)&edge_on_pick=is.null&bet_placed=eq.Y'))
print('NULL lambda_lr_total AND pick_strength=LINEUP PENDING:',
      count('lambda_lr_total=is.null&pick_strength=eq.LINEUP PENDING'))

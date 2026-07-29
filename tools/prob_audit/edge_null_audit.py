import csv, collections
p='data/picks_2026.csv'
rows=list(csv.DictReader(open(p,newline='',encoding='utf-8')))
print('total rows', len(rows))

def blank(v): return v is None or str(v).strip()==''

# 1. priced on picked side but blank edge_on_pick
hits=[]
for r in rows:
    side=(r.get('pick_side') or '').strip().upper()
    if side not in ('NRFI','YRFI'): continue
    price = r.get('market_nrfi_odds') if side=='NRFI' else r.get('market_yrfi_odds')
    if blank(price): continue
    if blank(r.get('edge_on_pick')):
        hits.append(r)
print('priced-on-picked-side with BLANK edge_on_pick:', len(hits))
for r in hits[:20]:
    print('  ', r['date'], r['away_team'],'@',r['home_team'], r['pick_label'],
          'price=', (r.get('market_nrfi_odds') if r['pick_side'].strip().upper()=='NRFI' else r.get('market_yrfi_odds')),
          'bet=',r.get('bet_placed'), 'strength=',r.get('pick_strength'))

# also edge_on_pick == "0" explicitly stored
z=[r for r in rows if (r.get('edge_on_pick') or '').strip() not in ('',) and abs(float(r['edge_on_pick']))<1e-12]
print('edge_on_pick stored as exactly 0:', len(z))

# 2. lambda_lr_total blank
b=[r for r in rows if blank(r.get('lambda_lr_total'))]
print('lambda_lr_total blank rows:', len(b))
print('  by date (top):', collections.Counter(r['date'] for r in b).most_common(5))
print('  earliest/latest', min(r['date'] for r in b), max(r['date'] for r in b))
# of those, how many have combined_lambda?
print('  of blank lambda_lr_total, have combined_lambda:', sum(1 for r in b if not blank(r.get('combined_lambda'))))
print('  of blank lambda_lr_total, strengths:', collections.Counter((r.get('pick_strength') or '').strip() for r in b).most_common())

# 3. today's slate
today=[r for r in rows if r['date']=='2026-07-28']
print('2026-07-28 rows:', len(today))
lp=[r for r in today if (r.get('pick_strength') or '').strip().upper()=='LINEUP PENDING']
print('  LINEUP PENDING:', len(lp), 'blank lambda_lr_total among them:', sum(1 for r in lp if blank(r.get('lambda_lr_total'))))
print('  blank lambda_lr_total on whole slate:', sum(1 for r in today if blank(r.get('lambda_lr_total'))))

# 4. profit_loss_units / over_prob / under_prob blanks
for col in ('profit_loss_units','over_1_5_prob','under_1_5_prob','combined_lambda','clv_pct','units_risked'):
    print(f'blank {col}:', sum(1 for r in rows if blank(r.get(col))))

import csv,sys
rows=list(csv.DictReader(open('data/picks_2026.csv',encoding='utf-8')))
cols=['date','away_team','home_team','pick_side','pick_strength','nrfi_prob','yrfi_prob','nrfi_prob_raw','yrfi_prob_raw','market_nrfi_odds','market_yrfi_odds','implied_nrfi_prob','implied_yrfi_prob','edge_nrfi','edge_yrfi','edge_on_pick','bet_placed','units_risked','odds_captured_at','created_at','opened_yrfi_odds']
for r in rows:
    if r['date'] in ('2026-06-17','2026-05-17') and r['away_team'] in ('PIT','CHC'):
        print({c:r[c] for c in cols}); print()

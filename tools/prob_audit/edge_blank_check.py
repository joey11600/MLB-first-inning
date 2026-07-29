import csv, sys
sys.path.insert(0,'.')
from tracker import american_to_prob

rows=list(csv.DictReader(open('data/picks_2026.csv',encoding='utf-8')))
hits=[]
for r in rows:
    pick=(r.get('pick_side') or '').strip()
    if pick not in ('NRFI','YRFI'): continue
    if (r.get('edge_on_pick') or '').strip(): continue
    odds = (r.get('market_nrfi_odds') if pick=='NRFI' else r.get('market_yrfi_odds')) or ''
    imp = american_to_prob(odds.strip())
    if imp is None: continue
    pcol = 'nrfi_prob' if pick=='NRFI' else 'yrfi_prob'
    try:
        p=float((r.get(pcol) or '').strip())
    except (TypeError,ValueError):
        continue
    hits.append((r,pick,odds.strip(),imp,p,p-imp))

print("total blank-edge-but-computable rows:",len(hits))
print()
for r,pick,odds,imp,p,edge in hits:
    print(f"{r['date']} {r['away_team']}@{r['home_team']:>4} pk={r['game_pk']} {pick:4} {r['pick_strength']:6} bet={r['bet_placed']!r:5} odds={odds:>6} imp={imp:.4f} p={p:.4f} edge={edge*100:+.2f}%  units={r['units_risked']!r} pl={r['profit_loss_units']!r} edge_nrfi={r['edge_nrfi']!r} edge_yrfi={r['edge_yrfi']!r} implied_n={r['implied_nrfi_prob']!r} implied_y={r['implied_yrfi_prob']!r} oddscap={r['odds_captured_at']!r}")

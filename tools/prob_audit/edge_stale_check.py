import csv, sys
sys.path.insert(0, '.')
from tracker import american_to_prob

rows = list(csv.DictReader(open('data/picks_2026.csv', encoding='utf-8')))
print("total rows", len(rows))

def f(v):
    try: return float((v or "").strip())
    except: return None

n_priced=0; bad=[]; sidebad=0; sidetot=0
for r in rows:
    pick = (r.get('pick_side') or '').strip().upper()
    if pick not in ('NRFI','YRFI'): continue
    odds = r.get('market_nrfi_odds') if pick=='NRFI' else r.get('market_yrfi_odds')
    imp = american_to_prob(odds)
    stored_edge = f(r.get('edge_on_pick'))
    if imp is None or stored_edge is None: continue
    n_priced += 1
    p = f(r.get('nrfi_prob') if pick=='NRFI' else r.get('yrfi_prob'))
    if p is None: continue
    true_edge = p - imp
    d = abs(true_edge - stored_edge)
    if d > 0.001:
        stored_imp = f(r.get('implied_nrfi_prob') if pick=='NRFI' else r.get('implied_yrfi_prob'))
        price_stale = stored_imp is None or abs(stored_imp - imp) > 1e-3
        bad.append((d, r['date'], r['away_team']+'@'+r['home_team'], pick, p, odds, imp, stored_edge, true_edge, stored_imp, 'PRICE-STALE' if price_stale else 'PROB-STALE'))

# per-side columns
for r in rows:
    for side,pcol,ocol,ecol,icol in (('NRFI','nrfi_prob','market_nrfi_odds','edge_nrfi','implied_nrfi_prob'),
                                     ('YRFI','yrfi_prob','market_yrfi_odds','edge_yrfi','implied_yrfi_prob')):
        imp = american_to_prob(r.get(ocol)); e = f(r.get(ecol)); p = f(r.get(pcol))
        if imp is None or e is None or p is None: continue
        sidetot += 1
        if abs((p-imp)-e) > 0.001: sidebad += 1

print("priced pick rows compared:", n_priced)
print("edge_on_pick mismatches >0.001:", len(bad))
bad.sort(reverse=True)
signflip = [b for b in bad if (b[7]>0) != (b[8]>0)]
print("sign flips:", len(signflip))
print("worst diff pp:", bad[0][0]*100 if bad else 0)
print("by-side cells compared:", sidetot, "mismatched:", sidebad)
print()
print("date       matchup       side  p       odds  imp     stored   true     storedImp  diag")
for b in bad[:15]:
    print(f"{b[1]} {b[2]:<12} {b[3]:<5} {b[4]:.4f} {b[5]:>5} {b[6]:.4f} {b[7]:+.4f} {b[8]:+.4f} {b[9] if b[9] is None else round(b[9],4)}  {b[10]}")
print("\n-- sign flips --")
for b in signflip:
    print(f"{b[1]} {b[2]:<12} {b[3]:<5} p={b[4]:.4f} odds={b[5]} imp={b[6]:.4f} stored={b[7]:+.4f} true={b[8]:+.4f} storedImp={b[9]} {b[10]}")

"""Part 2: magnitude of the required softness, observed movement scale,
and the hidden cost of betting before lineups post."""
import numpy as np, pandas as pd
from datetime import datetime

RNG = np.random.default_rng(7)

def dec(o):
    o = float(o); return 1.0 + (o/100.0 if o > 0 else 100.0/abs(o))
def imp(o):
    o = float(o); return 100.0/(o+100.0) if o > 0 else abs(o)/(abs(o)+100.0)
def payout(o, w): return dec(o)-1.0 if w else -1.0

d = pd.read_csv('data/picks_2026.csv', low_memory=False)
d = d[d['fi_total_runs'].notna()].copy()
d['nrfi_win'] = (d['fi_total_runs'].astype(float) == 0).astype(int)

both = d[d['opened_nrfi_odds'].notna() & d['market_nrfi_odds'].notna()].copy()
both['d_imp'] = both['market_nrfi_odds'].map(imp) - both['opened_nrfi_odds'].map(imp)

print('=' * 100)
print('F. SCALE OF OBSERVED FIRST-INNING LINE MOVEMENT (open -> ~first pitch)')
print('=' * 100)
a = both['d_imp'].abs() * 100
print(f'  n={len(both)}   |move| mean={a.mean():.3f}pp  median={a.median():.3f}pp  '
      f'p95={a.quantile(.95):.3f}pp  p99={a.quantile(.99):.3f}pp  max={a.max():.3f}pp')
print(f'  fraction of games with ANY move at all: {100*(a>0).mean():.1f}%')
print(f'  fraction whose move exceeds the 5.64pp wall: {100*(a>5.64).mean():.2f}%  '
      f'(n={(a>5.64).sum()})')
print()
print('  Interpretation: for the open to be soft enough to matter, the line would have to')
print('  travel >5.64pp between line-post and our capture. Observed travel in the LAST hour')
print(f'  is ~{a.mean():.2f}pp on average. The market would have to do ~{5.64/max(a.mean(),1e-9):.0f}x')
print('  more work in the earlier window than in the window we can see.')

print()
print('=' * 100)
print('G. REQUIRED PRICE, STATED PROPERLY (implied-probability terms)')
print('=' * 100)
pr = d[d['market_nrfi_odds'].notna()]
hit = pr['nrfi_win'].mean(); be = np.mean([imp(o) for o in pr['market_nrfi_odds']])
print(f'  n={len(pr)}  NRFI actual hit={hit:.2%}  captured break-even={be:.2%}  wall={(be-hit)*100:.2f}pp')
print(f'  Mean captured NRFI price {pr["market_nrfi_odds"].mean():+.0f}. Examples of what break-even needs:')
for p in (-150, -130, -120, -110, 100):
    need_dec = 1.0/hit
    print(f'    at a captured {p:+.0f} (implied {imp(p):.1%}), NRFI must hit {imp(p):.1%}; it hits {hit:.1%}')
    break
need_dec = 1.0/hit
need_am = -100.0/(need_dec-1.0) if need_dec < 2 else (need_dec-1.0)*100.0
print(f'  Break-even price for a 48.05% event = {need_am:+.0f} (i.e. plus money).')
print(f'  Mean captured = {pr["market_nrfi_odds"].mean():+.0f}. DK would have to open the NRFI side')
print(f'  at PLUS money on average and let it drift to {pr["market_nrfi_odds"].mean():+.0f} by first pitch.')

print()
print('=' * 100)
print('H. HIDDEN COST: betting at line-post means NO CONFIRMED LINEUPS')
print('=' * 100)
print('  The model consumes top-3 batter features. `*_top3c_source` records whether the')
print('  lineup was confirmed. `team_fallback` == the information state at line-post time.')
sub = d[d['market_nrfi_odds'].notna() & d['home_top3c_source'].notna()].copy()
for src in ('lineup', 'team_fallback'):
    g = sub[sub['home_top3c_source'] == src]
    if len(g) < 5: continue
    pl = np.array([payout(o, w) for o, w in zip(g['market_nrfi_odds'], g['nrfi_win'])])
    # discrimination: AUC of nrfi_prob vs nrfi_win
    y = g['nrfi_win'].values; p = g['nrfi_prob'].astype(float).values
    m = ~np.isnan(p)
    y, p = y[m], p[m]
    if len(set(y)) == 2:
        from scipy.stats import rankdata
        r = rankdata(p); n1 = y.sum(); n0 = len(y)-n1
        auc = (r[y == 1].sum() - n1*(n1+1)/2) / (n1*n0)
    else:
        auc = float('nan')
    print(f'  {src:<15} n={len(g):>4}  NRFI hit={g["nrfi_win"].mean():6.1%}  '
          f'ROI={pl.mean():+7.2%}  model AUC={auc:.4f}')
print('  -> the fallback (no-lineup) state is the exact information the system would have')
print('     if it bet at line-post. Any opening-price gain is offset by a worse model input.')

print()
print('=' * 100)
print('I. GENEROUS-GRANT STRESS TEST: assume the open IS soft by X pp, does NRFI clear?')
print('=' * 100)
nb = d[d['market_nrfi_odds'].notna() & (d['pick_side'].astype(str).str.upper() == 'NRFI')]
stg = nb[nb['pick_strength'].astype(str).str.upper() == 'STRONG']
for label, g in (('ALL games', pr), ('model NRFI picks', nb), ('STRONG NRFI', stg)):
    h = g['nrfi_win'].mean()
    print(f'  {label:<18} n={len(g):>4}  hit={h:.2%}')
    row = []
    for soft in (0, 1, 2, 3, 5, 8):
        # improve every price by `soft` pp of implied probability
        rois = []
        for o, w in zip(g['market_nrfi_odds'], g['nrfi_win']):
            q = max(imp(o) - soft/100.0, 0.02)
            rois.append((1.0/q - 1.0) if w else -1.0)
        row.append(f'+{soft}pp:{np.mean(rois):+7.2%}')
    print('      ROI if open were softer by  ' + '  '.join(row))
print('  (a realistic soft-open is well under 1pp; even a fantasy 5pp leaves STRONG NRFI negative)')

print()
print('=' * 100)
print('J. SEARCH EXPOSURE / WHAT THE ONE POSITIVE CELL IS WORTH')
print('=' * 100)
op = d[d['opened_nrfi_odds'].notna()].copy()
def gdt(r):
    try:
        t = str(r['game_time_et']).replace(' ET','').strip()
        return pd.Timestamp(datetime.strptime(f"{r['date']} {t}", '%Y-%m-%d %I:%M %p'),
                            tz='America/New_York').tz_convert('UTC')
    except Exception: return pd.NaT
op['game_utc'] = op.apply(gdt, axis=1)
op['lead'] = (op['game_utc'] - pd.to_datetime(op['opened_captured_at'], errors='coerce', utc=True)).dt.total_seconds()/3600
g = op[(op['lead'] > 4)]
print(f'  The only positive lead bucket is >4h: n={len(g)}, spanning {g["date"].nunique()} distinct days.')
print(f'  Games per day in that bucket: {len(g)/max(g["date"].nunique(),1):.1f}')
print(f'  Day counts: {dict(g["date"].value_counts().head(10))}')
pl = np.array([payout(o,w) for o,w in zip(g['opened_nrfi_odds'], g['nrfi_win'])])
days = g['date'].unique(); byday = {k:v for k,v in g.groupby('date')}
rois=[]
for _ in range(4000):
    pick = RNG.choice(days, size=len(days), replace=True)
    x=[]
    for dd in pick:
        gg=byday[dd]; x.extend(payout(o,w) for o,w in zip(gg['opened_nrfi_odds'], gg['nrfi_win']))
    rois.append(np.mean(x))
rois=np.array(rois)
print(f'  ROI={pl.mean():+.2%}  day-block 95% CI=[{np.percentile(rois,2.5):+.2%},{np.percentile(rois,97.5):+.2%}]'
      f'  P(ROI>0)={float((rois>0).mean()):.2f}')
print('  -> effectively a handful of DAYS, not a handful of games. n_effective is tiny.')

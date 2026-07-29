"""Scratch: recompute edge_* from stored model probs + stored odds and diff."""
import csv, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from tracker import american_to_prob

rows = list(csv.DictReader(open('data/picks_2026.csv', encoding='utf-8')))

def f(x):
    x = (x or '').strip()
    try:
        return float(x)
    except ValueError:
        return None

tot = 0
mism = []
signflip = []
by_lock = {'Y': [0, 0], 'other': [0, 0]}
for r in rows:
    pick = (r.get('pick_side') or '').strip().upper()
    if pick not in ('NRFI', 'YRFI'):
        continue
    stored = f(r.get('edge_on_pick'))
    if stored is None:
        continue
    odds = r.get('market_nrfi_odds') if pick == 'NRFI' else r.get('market_yrfi_odds')
    imp = american_to_prob(odds)
    p = f(r.get('nrfi_prob') if pick == 'NRFI' else r.get('yrfi_prob'))
    if imp is None or p is None:
        continue
    tot += 1
    recomp = p - imp
    d = abs(recomp - stored)
    bp = (r.get('bet_placed') or '').strip().upper()
    k = 'Y' if bp == 'Y' else 'other'
    by_lock[k][0] += 1
    if d > 0.001:
        by_lock[k][1] += 1
        mism.append((d, r['date'], r['game_pk'], pick, bp, stored, recomp, p, imp,
                     r.get('pick_strength'), r.get('units_risked'), r.get('graded_result')))
        if (stored > 0) != (recomp > 0):
            signflip.append((r['date'], r['game_pk'], pick, bp, stored, recomp, p, imp,
                             r.get('pick_strength'), r.get('graded_result')))

mism.sort(reverse=True)
print(f'rows with a comparable stored edge_on_pick: {tot}')
print(f'mismatch >0.001 (0.1pp): {len(mism)}  ({100*len(mism)/tot:.1f}%)')
print(f'  bet_placed=Y : {by_lock["Y"][1]} / {by_lock["Y"][0]}')
print(f'  bet_placed!=Y: {by_lock["other"][1]} / {by_lock["other"][0]}')
if mism:
    ds = [m[0] for m in mism]
    print(f'max abs diff {max(ds):.4f}   mean abs diff {sum(ds)/len(ds):.4f}')
print(f'sign flips: {len(signflip)}')
for s in signflip:
    print('  FLIP', s)
print('\ntop 15 by |diff|:')
for m in mism[:15]:
    print(f'  d={m[0]:.4f} {m[1]} pk={m[2]} {m[3]} bet={m[4]} stored={m[5]:+.4f} recomp={m[6]:+.4f} p={m[7]:.4f} imp={m[8]:.4f} {m[9]} u={m[10]} {m[11]}')

# Does edge_nrfi + edge_yrfi imply a coherent (but stale) model prob?
print('\n--- implied-stale-model-prob check on mismatched locked rows ---')
n_coh = 0
n_chk = 0
for r in rows:
    bp = (r.get('bet_placed') or '').strip().upper()
    if bp != 'Y':
        continue
    en, ey = f(r.get('edge_nrfi')), f(r.get('edge_yrfi'))
    inr, iyr = f(r.get('implied_nrfi_prob')), f(r.get('implied_yrfi_prob'))
    if None in (en, ey, inr, iyr):
        continue
    stale_n, stale_y = en + inr, ey + iyr
    n_chk += 1
    if abs(stale_n + stale_y - 1.0) < 2e-4:
        n_coh += 1
print(f'locked rows checked {n_chk}; stale-implied model probs summing to 1.0: {n_coh}')

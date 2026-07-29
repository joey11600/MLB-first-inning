import csv, statistics as st
from collections import defaultdict

rows=list(csv.DictReader(open('data/picks_2026.csv',encoding='utf-8')))
zones=defaultdict(lambda: {'w':0,'l':0,'p':[],'praw':[]})
for r in rows:
    d=(r.get('date') or '')[:10]
    if not d.startswith('2026'): continue
    side=(r.get('pick_side') or '').upper()
    stg=(r.get('pick_strength') or '').upper()
    if side not in ('NRFI','YRFI') or stg not in ('STRONG','LEAN'): continue
    g=(r.get('graded_result') or '').upper()
    if g not in ('WIN','LOSS'): continue
    lab=f"{stg} {side}"
    z=zones[lab]
    z['w' if g=='WIN' else 'l']+=1
    try:
        pn=float(r.get('nrfi_prob') or 'nan')
    except ValueError: pn=float('nan')
    try:
        pnr=float(r.get('nrfi_prob_raw') or 'nan')
    except ValueError: pnr=float('nan')
    if pn==pn:
        z['p'].append(pn if side=='NRFI' else 1.0-pn)
    if pnr==pnr:
        z['praw'].append(pnr if side=='NRFI' else 1.0-pnr)

hard={'STRONG NRFI':0.65,'LEAN NRFI':0.54,'LEAN YRFI':0.54,'STRONG YRFI':0.65}
print(f"{'zone':13} {'n':>4} {'hit%':>7} {'meanP':>7} {'medP':>7} {'minP':>6} {'maxP':>6} {'hard':>5} {'gap_hard':>9} {'gap_true':>9} {'meanRAW':>8}")
for lab in ['STRONG NRFI','LEAN NRFI','LEAN YRFI','STRONG YRFI']:
    z=zones.get(lab)
    if not z: continue
    n=z['w']+z['l']; hr=z['w']/n
    mp=st.mean(z['p']); md=st.median(z['p'])
    mraw=st.mean(z['praw']) if z['praw'] else float('nan')
    h=hard[lab]
    print(f"{lab:13} {n:4d} {hr*100:6.2f}% {mp:7.4f} {md:7.4f} {min(z['p']):6.4f} {max(z['p']):6.4f} {h:5.2f} {(hr-h)*100:+8.2f}pp {(hr-mp)*100:+8.2f}pp {mraw:8.4f}")

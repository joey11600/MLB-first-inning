import csv, statistics
from collections import defaultdict

def implied(am):
    am = float(am)
    return (-am)/((-am)+100.0) if am < 0 else 100.0/(am+100.0)

rows = list(csv.DictReader(open('data/picks_2026.csv', encoding='utf-8')))
print("total rows", len(rows))

zones = defaultdict(lambda: dict(w=0,l=0,pl=0.0,rw=0,rl=0,rpl=0.0,imp=[],ph=0))
for r in rows:
    st = (r['pick_strength'] or '').upper()
    side = (r['pick_side'] or '').upper()
    g = (r['graded_result'] or '').upper()
    if st != 'STRONG' or side not in ('NRFI','YRFI'): continue
    if g not in ('WIN','LOSS'): continue
    z = zones[f'STRONG {side}']
    tot = zones['TOTAL']
    plraw = (r['profit_loss_units'] or '').strip()
    try: pl = float(plraw)
    except: pl = (100/110) if g=='WIN' else -1.0
    price = (r['market_nrfi_odds'] if side=='NRFI' else r['market_yrfi_odds']).strip()
    for zz in (z, tot):
        if g=='WIN': zz['w']+=1
        else: zz['l']+=1
        zz['pl']+=pl
        if price:
            zz['rpl']+=pl
            if g=='WIN': zz['rw']+=1
            else: zz['rl']+=1
            zz['imp'].append(implied(price))
        else:
            zz['ph']+=1

for k,z in zones.items():
    bets=z['w']+z['l']; rb=z['rw']+z['rl']
    hr=z['w']/bets
    print(f"\n{k}: all graded {z['w']}W-{z['l']}L n={bets} hit={hr:.4f} PL={z['pl']:+.2f}")
    print(f"   dashboard edge vs 0.5238 = {(hr-110/210)*100:+.2f} pp")
    if rb:
        rhr=z['rw']/rb
        mi=statistics.mean(z['imp'])
        print(f"   real-priced: {z['rw']}W-{z['rl']}L n={rb} hit={rhr:.4f} PL={z['rpl']:+.2f}")
        print(f"   mean implied (vigged) on picked side = {mi:.4f}  -> true BE {mi*100:.2f}%")
        print(f"   real edge vs mean implied = {(rhr-mi)*100:+.2f} pp ; placeholder bets={z['ph']}")

# exact break-even hit rate from payout odds (b = decimal - 1)
print("\n--- exact BE from payouts (real-priced STRONG only) ---")
import statistics as st
bs=[]; res=[]
for r in rows:
    if (r['pick_strength'] or '').upper()!='STRONG': continue
    side=(r['pick_side'] or '').upper()
    if side not in ('NRFI','YRFI'): continue
    g=(r['graded_result'] or '').upper()
    if g not in ('WIN','LOSS'): continue
    p=(r['market_nrfi_odds'] if side=='NRFI' else r['market_yrfi_odds']).strip()
    if not p: continue
    am=float(p)
    b = am/100.0 if am>0 else 100.0/(-am)
    bs.append(b); res.append(1 if g=='WIN' else 0)
mb=st.mean(bs)
print(f"n={len(bs)} mean b={mb:.4f} (avg american ~ {-100/mb:.0f})")
print(f"break-even hit rate 1/(1+mean b) = {1/(1+mb):.4f}")
print(f"actual hit = {st.mean(res):.4f}")
print(f"flat-1u PL at real prices = {sum(b if y else -1 for b,y in zip(bs,res)):+.2f}u")
print(f"PL at fabricated -110 for same set = {sum((100/110) if y else -1 for y in res):+.2f}u")

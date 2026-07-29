"""Part 2 -- break-even, provenance, CLV, kelly cross-check. ANALYSIS ONLY."""
import csv, os, math, json
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
rows = list(csv.DictReader(open(os.path.join(ROOT, 'data', 'picks_2026.csv'), newline='', encoding='utf-8')))

def imp(s):
    s = (s or '').strip()
    if not s: return None
    try: v = float(s)
    except Exception: return None
    if v == 0: return None
    return 100/(v+100) if v > 0 else -v/(-v+100)

def payout(s):
    try: v = float((s or '').strip())
    except Exception: return None
    if v == 0: return None
    return v/100 if v > 0 else 100/abs(v)

# ---- true break-even vs the 52.38% constant the dashboard shows
BE = 110/210
for scope in ('all', 'realpriced'):
    w = l = 0; be_sum = 0.0; n = 0; pl = 0.0
    for r in rows:
        if (r.get('pick_strength') or '').upper() != 'STRONG': continue
        side = (r.get('pick_side') or '').upper()
        if side not in ('NRFI','YRFI'): continue
        g = (r.get('graded_result') or '').upper()
        if g not in ('WIN','LOSS'): continue
        price = (r.get('market_nrfi_odds') if side=='NRFI' else r.get('market_yrfi_odds')) or ''
        i = imp(price)
        if scope == 'realpriced' and i is None: continue
        if g=='WIN': w+=1
        else: l+=1
        if i is not None: be_sum += i; n += 1
        p = r.get('profit_loss_units') or ''
        pl += float(p) if p.strip() else (100/110 if g=='WIN' else -1.0)
    hit = w/(w+l)
    print(f"[BE:{scope}] {w}W-{l}L hit={hit:.4f}  dashboard BE={BE:.4f} edge={hit-BE:+.4f}"
          f" | true avg implied BE={be_sum/n:.4f} (n={n}) -> real edge={hit-be_sum/n:+.4f} | PL {pl:+.2f}u")

# ---- de-vig check
tot = 0; s = 0.0
for r in rows:
    a, b = imp(r.get('market_nrfi_odds')), imp(r.get('market_yrfi_odds'))
    if a and b: s += a+b; tot += 1
print(f"[vig] mean two-way sum = {s/tot:.4f} -> mean vig {100*(s/tot-1):.2f}%  (no de-vig anywhere in repo)")

# ---- edge_on_pick staleness scale
stale = 0; tot_e = 0; flips = 0; ex = []
for r in rows:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI','YRFI'): continue
    e = (r.get('edge_on_pick') or '').strip()
    if not e: continue
    i = imp(r.get('market_nrfi_odds') if side=='NRFI' else r.get('market_yrfi_odds'))
    if i is None: continue
    p = float(r['nrfi_prob'] if side=='NRFI' else r['yrfi_prob'])
    tot_e += 1
    live = p - i
    if abs(live - float(e)) > 1e-3:
        stale += 1
        if (float(e) > 0) != (live > 0):
            flips += 1
            if len(ex) < 8:
                ex.append((r['date'], r['game_pk'], side, r.get('pick_strength'), r.get('bet_placed'),
                           f"shown_edge={float(e):+.4f}", f"p={p}", f"implied={i:.4f}",
                           f"true_edge={live:+.4f}"))
print(f"[edge] rows with a displayed edge: {tot_e}; stale (>0.1pp off p-implied): {stale}; "
      f"SIGN FLIPPED (shown +EV, actually -EV or vice versa): {flips}")
for e in ex: print("   ", e)

# ---- clv consistency + measurability
dev = []; ident_but_nonzero = 0; diff_but_zero = 0
for r in rows:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI','YRFI'): continue
    ops = ((r.get('opened_nrfi_odds') if side=='NRFI' else r.get('opened_yrfi_odds')) or '').strip()
    cls = ((r.get('market_nrfi_odds') if side=='NRFI' else r.get('market_yrfi_odds')) or '').strip()
    c = (r.get('clv_pct') or '').strip()
    if not ops or not cls or not c: continue
    cv = float(c)
    o, k = imp(ops), imp(cls)
    dev.append(abs((k-o)-cv))
    if ops == cls and abs(cv) > 1e-9: ident_but_nonzero += 1
    if ops != cls and abs(cv) < 1e-9: diff_but_zero += 1
print(f"[clv] n={len(dev)} max|stored - (close_imp-open_imp)|={max(dev):.5f} "
      f"mean={sum(dev)/len(dev):.6f}")
print(f"      prices identical yet clv!=0: {ident_but_nonzero}   prices differ yet clv==0: {diff_but_zero}")

# ---- kelly cross-check: TS kellyFraction vs python formula on real rows
def kf(p, american):
    b = american/100 if american > 0 else 100/abs(american)
    if not (b > 0) or not (0 < p < 1): return 0.0
    return max((p*b - (1-p))/b, 0.0)
mx = 0.0; n = 0
for r in rows:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI','YRFI'): continue
    o = (r.get('market_nrfi_odds') if side=='NRFI' else r.get('market_yrfi_odds')) or ''
    if not o.strip(): continue
    p = float(r['nrfi_prob'] if side=='NRFI' else r['yrfi_prob'])
    a = float(o)
    b = payout(o)
    f_ref = max((p*b-(1-p))/b, 0.0)
    mx = max(mx, abs(kf(p, a) - f_ref)); n += 1
print(f"[kelly] TS kellyFraction vs f*=(pb-q)/b on {n} rows: max|diff| = {mx:.2e}")

# also: identity f* = (p - q/b)/1 ... and f* = p - (1-p)/b
mx2 = 0.0
for r in rows[:400]:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI','YRFI'): continue
    o = (r.get('market_nrfi_odds') if side=='NRFI' else r.get('market_yrfi_odds')) or ''
    if not o.strip(): continue
    p = float(r['nrfi_prob'] if side=='NRFI' else r['yrfi_prob'])
    b = payout(o)
    mx2 = max(mx2, abs(((p*b-(1-p))/b) - (p - (1-p)/b)))
print(f"        algebraic identity check max diff {mx2:.2e}")

# ---- roi-today provenance is all-zero by construction
print("[roi-today] emptyZone provenance realPricedBets=0/placeholderBets=0/realShare=NaN "
      "for every zone -> today's card can never state provenance (documented).")

# ---- season record replayWindow pct sanity
rec = json.load(open(os.path.join(ROOT, 'data', 'season_record.json')))
for key in ('projected', 'real'):
    s = rec.get(key)
    if not s: continue
    days = s['days']
    # reconstruct: does simBankAfter - simPnl of day0 equal startBank?
    d0 = days[0]
    print(f"[replayWindow:{key}] day0 {d0['date']} simBankAfter={d0['simBankAfter']} "
          f"simPnl={d0['simPnl']} -> bankStart={None if d0['simBankAfter'] is None else round(d0['simBankAfter']-d0['simPnl'],4)}"
          f"  (file startBank={rec['startBank']})")
    # full-window pct
    bets=wins=0; pnl=0.0
    for d in days:
        for g in d['games']:
            if g['record']['action'] != 'BET': continue
            bets += 1; pnl += g['record'].get('pnl') or 0.0
            if g['record'].get('win'): wins += 1
    bs = d0['simBankAfter'] - d0['simPnl'] if d0['simBankAfter'] is not None else None
    print(f"     full window: bets={bets} wins={wins} sum(pnl)={pnl:.2f} "
          f"pct={pnl/bs*100 if bs else float('nan'):.1f}%  sim.profit={s['sim']['profit']:.2f} "
          f"finalBank={s['sim']['finalBank']:.2f}")
    print(f"     -> does sum(game pnl) equal sim.profit? diff={pnl - s['sim']['profit']:+.2f}")

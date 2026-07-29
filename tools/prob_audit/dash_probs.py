"""Dashboard probability audit -- ANALYSIS ONLY, writes nothing."""
import csv, json, math, os, sys, glob
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def rows(p):
    with open(p, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def f(x):
    try:
        if x is None or str(x).strip() == '': return None
        return float(x)
    except Exception:
        return None

picks = rows(os.path.join(ROOT, 'data', 'picks_2026.csv'))
print(f"picks rows: {len(picks)}")

# ---------------------------------------------------------------- 1. p sums
bad = []
maxdev = 0.0
for r in picks:
    n, y = f(r['nrfi_prob']), f(r['yrfi_prob'])
    if n is None or y is None: continue
    d = abs(n + y - 1.0)
    maxdev = max(maxdev, d)
    if d > 5e-4: bad.append((r['date'], r['game_pk'], n, y, d))
    if not (0.0 <= n <= 1.0) or not (0.0 <= y <= 1.0):
        bad.append(('RANGE', r['date'], n, y))
print(f"[1] nrfi+yrfi==1  max|dev| = {maxdev:.2e}   violations>5e-4: {len(bad)}")

# raw as well
maxdev_raw = 0.0
for r in picks:
    n, y = f(r.get('nrfi_prob_raw')), f(r.get('yrfi_prob_raw'))
    if n is None or y is None: continue
    maxdev_raw = max(maxdev_raw, abs(n + y - 1.0))
print(f"    raw pair    max|dev| = {maxdev_raw:.2e}")

# ------------------------------------------------- 2. lambda == -ln(raw nrfi)
devs = []
for r in picks:
    raw = f(r.get('nrfi_prob_raw')); lam = f(r.get('lambda_lr_total'))
    if raw is None or lam is None or raw <= 0: continue
    devs.append(abs(lam - (-math.log(raw))))
if devs:
    print(f"[2] lambda_lr_total vs -ln(raw p_nrfi): n={len(devs)} max={max(devs):.4f} mean={sum(devs)/len(devs):.5f}")

# ------------------------------------------ 3. board CSV pct vs picks nrfi_prob
# board.ts reads nrfi_pct straight; board-supabase does round(p*1000)/10
worst = []
nmatch = ncmp = 0
for bp in sorted(glob.glob(os.path.join(ROOT, 'data', 'boards', 'board_*.csv'))):
    iso = os.path.basename(bp)[6:-4].replace('_', '-')
    by_pk = {r['game_pk']: r for r in picks if r['date'] == iso}
    for b in rows(bp):
        pr = by_pk.get(b.get('game_pk', ''))
        if not pr: continue
        n = f(pr['nrfi_prob'])
        if n is None: continue
        csv_pct = f(b['nrfi_pct'])
        sb_pct = round(n * 1000) / 10          # board-supabase.ts
        ncmp += 1
        if abs(csv_pct - sb_pct) < 1e-9: nmatch += 1
        else: worst.append((iso, b['game_pk'], csv_pct, sb_pct, n))
print(f"[3] board CSV nrfi_pct vs supabase-scaled: {nmatch}/{ncmp} identical; mismatches {len(worst)}")
for w in worst[:8]: print("     ", w)

# nrfi_pct + yrfi_pct == 100 in board csv?
off = 0; offmax = 0
for bp in sorted(glob.glob(os.path.join(ROOT, 'data', 'boards', 'board_*.csv'))):
    for b in rows(bp):
        a, c = f(b['nrfi_pct']), f(b['yrfi_pct'])
        if a is None or c is None: continue
        if abs(a + c - 100.0) > 1e-9:
            off += 1; offmax = max(offmax, abs(a + c - 100.0))
print(f"[3b] board rows where nrfi_pct+yrfi_pct != 100: {off} (max off {offmax})")

# ---------------------------------------------------- 4. classifier divergence
S_NRFI = 1.01; LEAN_NRFI = 0.50; PASS_LO = 0.44; S_YRFI = 0.40
FLOOR = 0.838; CEIL = 0.52

def wx_floor(temp, wind, dome):
    if dome: return FLOOR
    d = 0.0
    if temp is not None and temp >= 28: d += 0.02
    elif temp is not None and temp <= 12: d -= 0.02
    if wind is not None and wind >= 24: d += 0.02
    return max(0.40, min(1.20, FLOOR + d))

def classify(p, lam, floor):
    if p >= S_NRFI:
        if lam is not None and lam > CEIL: return ('PASS', 'HIGH LAMBDA')
        return ('NRFI', 'STRONG')
    if p >= LEAN_NRFI: return ('NRFI', 'LEAN')
    if p > PASS_LO:
        if lam is not None and lam >= floor: return ('YRFI', 'LEAN')
        return ('PASS', 'NO EDGE')
    if p >= PASS_LO: return ('PASS', 'NO EDGE')
    if lam is not None and lam < floor: return ('PASS', 'LOW LAMBDA')
    if p >= S_YRFI: return ('YRFI', 'LEAN')
    return ('YRFI', 'STRONG')

div_floor = 0; div_round = 0; div_both = 0; n = 0
ex_floor = []; ex_round = []
for r in picks:
    p = f(r['nrfi_prob']); lam = f(r.get('lambda_lr_total'))
    if p is None: continue
    dome = (f(r.get('wx_is_dome')) or 0) >= 0.5
    fl = wx_floor(f(r.get('wx_temp_c')), f(r.get('wx_wind_kmh')), dome)
    n += 1
    py = classify(p, lam, fl)                   # predictor (weather floor, full p)
    ts = classify(round(p * 1000) / 1000.0, lam, FLOOR)   # dashboard (base floor, 0.1% p)
    ts_floor_only = classify(p, lam, FLOOR)
    ts_round_only = classify(round(p * 1000) / 1000.0, lam, fl)
    if py != ts_floor_only:
        div_floor += 1
        if len(ex_floor) < 5: ex_floor.append((r['date'], r['away_team'], r['home_team'], p, lam, fl, py, ts_floor_only))
    if py != ts_round_only:
        div_round += 1
        if len(ex_round) < 5: ex_round.append((r['date'], r['away_team'], r['home_team'], p, lam, py, ts_round_only))
    if py != ts: div_both += 1
print(f"[4] classifyTentative vs classify_pick_lr over n={n}")
print(f"    weather-floor divergences : {div_floor}  ({100*div_floor/n:.2f}%)")
print(f"    pct-rounding divergences  : {div_round}")
print(f"    combined divergences      : {div_both}")
for e in ex_floor: print("     FLOOR ", e)
for e in ex_round: print("     ROUND ", e)

# how many rows have non-neutral weather at all
nonneutral = sum(1 for r in picks
                 if (f(r.get('wx_is_dome')) or 0) < 0.5
                 and wx_floor(f(r.get('wx_temp_c')), f(r.get('wx_wind_kmh')), False) != FLOOR)
print(f"    rows whose weather moves the floor: {nonneutral}")

# -------------------------------------------- 5. american -> implied prob check
def imp(s):
    s = (s or '').strip().replace('−', '-')
    if not s: return None
    try: v = float(s)
    except Exception: return None
    if v == 0: return None
    return 100.0 / (v + 100.0) if v > 0 else (-v) / (-v + 100.0)

bad_odds = Counter()
pairsum = []
for r in picks:
    a, b = imp(r.get('market_nrfi_odds')), imp(r.get('market_yrfi_odds'))
    if a is not None and b is not None:
        pairsum.append(a + b)
    for c in ('market_nrfi_odds', 'market_yrfi_odds', 'opened_nrfi_odds', 'opened_yrfi_odds'):
        v = (r.get(c) or '').strip()
        if v and imp(v) is None: bad_odds[(c, v)] += 1
if pairsum:
    print(f"[5] two-way implied prob sum (vig): n={len(pairsum)} min={min(pairsum):.4f} "
          f"mean={sum(pairsum)/len(pairsum):.4f} max={max(pairsum):.4f}")
print(f"    unparseable odds cells: {dict(bad_odds) if bad_odds else 'none'}")

# ----------------------------------------------------------- 6. edge_on_pick
devs = []; worst_edge = []
for r in picks:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI', 'YRFI'): continue
    o = imp(r.get('market_nrfi_odds') if side == 'NRFI' else r.get('market_yrfi_odds'))
    p = f(r['nrfi_prob'] if side == 'NRFI' else r['yrfi_prob'])
    e = f(r.get('edge_on_pick'))
    if o is None or p is None or e is None: continue
    d = abs((p - o) - e)
    devs.append(d)
    if d > 1e-3: worst_edge.append((r['date'], r['game_pk'], side, p, o, e, p - o))
if devs:
    print(f"[6] edge_on_pick == p - implied: n={len(devs)} max|dev|={max(devs):.5f} "
          f"mean={sum(devs)/len(devs):.6f}  rows off>1e-3: {len(worst_edge)}")
for w in worst_edge[:6]: print("     ", w)

# ------------------------------------------------------------------- 7. CLV
devs = []; nz = 0; tot = 0
for r in picks:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI', 'YRFI'): continue
    op = imp(r.get('opened_nrfi_odds') if side == 'NRFI' else r.get('opened_yrfi_odds'))
    cl = imp(r.get('market_nrfi_odds') if side == 'NRFI' else r.get('market_yrfi_odds'))
    c = f(r.get('clv_pct'))
    if op is None or cl is None or c is None: continue
    tot += 1
    devs.append(abs((cl - op) - c))
    if abs(c) > 1e-9: nz += 1
if devs:
    print(f"[7] clv_pct == close_imp - open_imp: n={tot} max|dev|={max(devs):.6f}  nonzero clv rows={nz}")

# measurability rule: placed bets w/ both prices, differing strings
placed = mdiff = mzero_clv = mnull = 0
for r in picks:
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI', 'YRFI'): continue
    if (r.get('bet_placed') or '').strip().upper() != 'Y': continue
    placed += 1
    op = (r.get('opened_nrfi_odds') if side == 'NRFI' else r.get('opened_yrfi_odds') or '').strip()
    cl = (r.get('market_nrfi_odds') if side == 'NRFI' else r.get('market_yrfi_odds') or '').strip()
    if not op or not cl or op == cl: continue
    mdiff += 1
    c = f(r.get('clv_pct'))
    if c is None: mnull += 1
    elif abs(c) < 1e-9: mzero_clv += 1
print(f"[7b] placed bets={placed}; prices differ on picked side={mdiff}; "
      f"of those clv blank={mnull}, clv==0={mzero_clv}")

# ------------------------------- 8. STRONG rows graded but bet_placed != Y
cnt = Counter(); fb_pl = 0.0; fb_n = 0
for r in picks:
    if (r.get('pick_strength') or '').upper() != 'STRONG': continue
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI', 'YRFI'): continue
    g = (r.get('graded_result') or '').upper()
    if g not in ('WIN', 'LOSS'): continue
    bp = (r.get('bet_placed') or '').strip().upper()
    pl = (r.get('profit_loss_units') or '').strip()
    cnt[(bp, 'plblank' if pl == '' else 'plset')] += 1
    if pl == '':
        fb_n += 1
        fb_pl += (100/110) if g == 'WIN' else -1.0
print(f"[8] graded STRONG rows by (bet_placed, profit_loss_units): {dict(cnt)}")
print(f"    rows roi.ts would settle at the -110 FALLBACK: {fb_n}  -> fabricated P&L {fb_pl:+.2f}u")

# season-to-date sanity: what roi.ts TOTAL would print
tot_pl = 0.0; w = l = 0; realb = phb = 0; realpl = 0.0
for r in picks:
    if (r.get('pick_strength') or '').upper() != 'STRONG': continue
    side = (r.get('pick_side') or '').upper()
    if side not in ('NRFI', 'YRFI'): continue
    g = (r.get('graded_result') or '').upper()
    if g not in ('WIN', 'LOSS'): continue
    if g == 'WIN': w += 1
    else: l += 1
    pl = f(r.get('profit_loss_units'))
    if pl is None: pl = (100/110) if g == 'WIN' else -1.0
    tot_pl += pl
    price = (r.get('market_nrfi_odds') if side == 'NRFI' else r.get('market_yrfi_odds') or '').strip()
    if price: realb += 1; realpl += pl
    else: phb += 1
print(f"[8b] roi.ts season TOTAL would read: {w}W-{l}L  {tot_pl:+.2f}u | "
      f"realPriced {realb} ({realpl:+.2f}u), placeholder {phb}, realShare {realb/(realb+phb):.3f}")
print(f"     hitRate={w/(w+l):.4f}  edgeVsBreakEven={w/(w+l)-110/210:+.4f}")

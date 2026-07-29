"""ANALYSIS ONLY -- drill into the outliers found by ledger_odds_audit."""
import csv, sys, statistics
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import tracker
ap, ppu = tracker.american_to_prob, tracker.payout_per_unit
rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
def F(r,k):
    v=(r.get(k) or "").strip()
    try: return float(v)
    except (ValueError,TypeError): return None

print("--- _fmt behaviour ---")
for v in (-1.0, 0.909, 0.9090909, 1.0, 0.0):
    print("  _fmt(%r,3) = %r   _fmt(%r,2)=%r" % (v, tracker._fmt(v,3), v, tracker._fmt(v,2)))

print()
print("--- A. implied_*_prob outliers (|err| > 1e-3) ---")
for r in rows:
    for col, ocol in (("implied_nrfi_prob","market_nrfi_odds"),("implied_yrfi_prob","market_yrfi_odds")):
        w = ap(r.get(ocol,"")); g = F(r,col)
        if w is None or g is None: continue
        if abs(w-g) > 1e-3:
            print(f"  {r['date']} {r['away_team']}@{r['home_team']} {col}: stored={g} "
                  f"recomp={w:.4f} from {ocol}={r[ocol]!r} book={r.get('sportsbook')!r} "
                  f"bet={r.get('bet_placed')} opened_n={r.get('opened_nrfi_odds')!r} opened_y={r.get('opened_yrfi_odds')!r}")

print()
print("--- B. edge_* outliers (|err| > 1e-3) ---")
cnt=0
for r in rows:
    for col, mcol, ocol in (("edge_nrfi","nrfi_prob","market_nrfi_odds"),
                            ("edge_yrfi","yrfi_prob","market_yrfi_odds")):
        imp=ap(r.get(ocol,"")); m=F(r,mcol); g=F(r,col)
        if imp is None or m is None or g is None: continue
        if abs((m-imp)-g) > 1e-3:
            cnt+=1
            if cnt<=25:
                print(f"  {r['date']} {r['away_team']}@{r['home_team']} {col}: stored={g} "
                      f"recomp={m-imp:+.4f}  model={m} imp={imp:.4f} odds={r[ocol]!r} "
                      f"pick={r.get('pick_side')}/{r.get('pick_strength')} bet={r.get('bet_placed')} book={r.get('sportsbook')!r}")
print(f"  total edge outliers: {cnt}")

print()
print("--- C. edge_on_pick mismatches (all 18) ---")
for r in rows:
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    src=F(r,"edge_nrfi" if side=="NRFI" else "edge_yrfi"); g=F(r,"edge_on_pick")
    if src is None and g is None: continue
    if src is None or g is None or abs(src-g)>1e-9:
        print(f"  {r['date']} {r['away_team']}@{r['home_team']} side={side} "
              f"edge_side={src} edge_on_pick={g} bet={r.get('bet_placed')} "
              f"strength={r.get('pick_strength')} book={r.get('sportsbook')!r} "
              f"label={r.get('pick_label')!r}")

print()
print("--- D. P&L drift, NUMERIC (not string) ---")
bad=[]; n=0
for r in rows:
    w=tracker._calc_pnl(r); g=(r.get("profit_loss_units") or "").strip()
    if w=="" and g=="": continue
    n+=1
    try: wv=float(w) if w else None
    except ValueError: wv=None
    try: gv=float(g) if g else None
    except ValueError: gv=None
    if wv is None or gv is None:
        bad.append((r,"blank",w,g)); continue
    if abs(wv-gv)>5e-4: bad.append((r,"num",w,g))
print(f"  rows compared={n}  numeric drift={len(bad)}")
tot_stored=tot_recomp=0.0
for r,kind,w,g in bad[:30]:
    print(f"   [{kind}] {r['date']} {r['away_team']}@{r['home_team']} side={r.get('pick_side')} "
          f"grade={r.get('graded_result')} bet={r.get('bet_placed')} units={r.get('units_risked')!r} "
          f"n_odds={r.get('market_nrfi_odds')!r} y_odds={r.get('market_yrfi_odds')!r} "
          f"stored={g!r} recomp={w!r}")

print()
print("--- E. Kelly gate: how many STRONG rows did the raw-vs-fair implied flip? ---")
# For rows where Kelly is live (bet decision), does using the de-vigged implied
# change the zero-stake decision?
flip=0; tot=0; stake_delta=[]
for r in rows:
    if (r.get("pick_strength") or "").upper()!="STRONG": continue
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    a,b = ap(r.get("market_nrfi_odds","")), ap(r.get("market_yrfi_odds",""))
    if a is None or b is None: continue
    m=F(r,"nrfi_prob" if side=="NRFI" else "yrfi_prob")
    o=r.get("market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds")
    if m is None: continue
    tot+=1
    f_raw=tracker.kelly_fraction_of_bankroll(m,o)
    imp=a if side=="NRFI" else b
    fair=imp/(a+b)
    bb=ppu(o)
    # "fair" Kelly: stake computed at the same PRICE but using fair-prob gate
    f_fair=max((m*bb-(1-m))/bb,0.0)   # identical - Kelly already uses price
    if f_raw==0 and m>fair: flip+=1
print(f"  STRONG two-sided-priced rows={tot}  rows Kelly zeroes but model beats FAIR prob={flip}")
print("  (Kelly's break-even IS the raw implied prob by construction -- that is correct")
print("   for staking; the question is only whether the EDGE COLUMN is de-vigged.)")

print()
print("--- F. edge threshold usage: is edge_on_pick used as a gate anywhere? ---")

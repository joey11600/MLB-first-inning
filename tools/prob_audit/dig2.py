"""ANALYSIS ONLY -- part 2: sign errors, fallback exposure, vig impact."""
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

print("=" * 78)
print("A. edge_on_pick: WRONG SIGN vs a fresh recompute (this is what the")
print("   board renders and what the BET LOCKED telegram quotes)")
print("=" * 78)
wrong_sign=[]; wrong_any=[]; blank_but_computable=[]
for r in rows:
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    ocol="market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    mcol="nrfi_prob" if side=="NRFI" else "yrfi_prob"
    imp=ap(r.get(ocol,"")); m=F(r,mcol)
    if imp is None or m is None: continue
    true_edge=m-imp
    got=F(r,"edge_on_pick")
    if got is None:
        blank_but_computable.append(r); continue
    if abs(got-true_edge)>1e-3:
        wrong_any.append((r,got,true_edge))
        if (got>=0) != (true_edge>=0):
            wrong_sign.append((r,got,true_edge))
print(f"  priced NRFI/YRFI rows with an edge_on_pick     : "
      f"{sum(1 for r in rows if F(r,'edge_on_pick') is not None)}")
print(f"  stale by >0.001                                : {len(wrong_any)}")
print(f"  ** stale AND the SIGN is wrong **              : {len(wrong_sign)}")
print(f"  blank but computable from stored odds+prob     : {len(blank_but_computable)}")
print()
for r,g,t in wrong_sign:
    print(f"    {r['date']} {r['away_team']}@{r['home_team']} {r['pick_strength']} {r['pick_side']}  "
          f"shown {g*100:+.2f}%  actual {t*100:+.2f}%  bet={r.get('bet_placed')} "
          f"price={r.get('market_nrfi_odds') if r['pick_side']=='NRFI' else r.get('market_yrfi_odds')}")
print()
print("  worst 8 by absolute error:")
for r,g,t in sorted(wrong_any,key=lambda x:-abs(x[1]-x[2]))[:8]:
    print(f"    {r['date']} {r['away_team']}@{r['home_team']} {r['pick_strength']} {r['pick_side']}  "
          f"shown {g*100:+.2f}%  actual {t*100:+.2f}%  err {abs(g-t)*100:.2f}pp  bet={r.get('bet_placed')}")

print()
print("=" * 78)
print("B. implied_*_prob staleness vs current market_*_odds")
print("=" * 78)
bad=0
for r in rows:
    for col,ocol in (("implied_nrfi_prob","market_nrfi_odds"),("implied_yrfi_prob","market_yrfi_odds")):
        w=ap(r.get(ocol,"")); g=F(r,col)
        if w is None or g is None: continue
        if abs(w-g)>1e-3: bad+=1
print(f"  implied cells off by >0.001 from their own odds column: {bad}")

print()
print("=" * 78)
print("C. -110 FALLBACK exposure in the P&L ledger")
print("=" * 78)
fb_w=fb_l=0; fb_pl=0.0; real_pl=0.0; real_n=0
for r in rows:
    g=(r.get("graded_result") or "").upper()
    if g not in ("WIN","LOSS"): continue
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    if (r.get("bet_placed") or "").upper()!="Y": continue
    pl=F(r,"profit_loss_units")
    if pl is None: continue
    col="market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    if ppu(r.get(col,"")) is None:
        fb_pl+=pl
        if g=="WIN": fb_w+=1
        else: fb_l+=1
    else:
        real_pl+=pl; real_n+=1
print(f"  bet_placed=Y graded bets with a REAL picked-side price: {real_n}  P&L={real_pl:+.3f}u")
print(f"  bet_placed=Y graded bets on the -110 FALLBACK        : {fb_w+fb_l} "
      f"({fb_w}W-{fb_l}L)  P&L={fb_pl:+.3f}u")
print(f"  TOTAL stored P&L on bet_placed=Y rows                : {real_pl+fb_pl:+.3f}u")

print()
print("=" * 78)
print("D. VIG: what de-vigging would do to the displayed edge")
print("=" * 78)
# proportional (multiplicative) and additive (shin-lite) de-vig, bet rows only
rowsp=[]
for r in rows:
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    if (r.get("bet_placed") or "").upper()!="Y": continue
    a,b=ap(r.get("market_nrfi_odds","")),ap(r.get("market_yrfi_odds",""))
    if a is None or b is None: continue
    m=F(r,"nrfi_prob" if side=="NRFI" else "yrfi_prob")
    if m is None: continue
    imp=a if side=="NRFI" else b
    tot=a+b
    rowsp.append((r,m,imp,tot))
raw=[m-imp for _,m,imp,_ in rowsp]
prop=[m-imp/tot for _,m,imp,tot in rowsp]
add=[m-(imp-(tot-1)/2) for _,m,imp,tot in rowsp]
print(f"  n placed bets with a two-sided price = {len(rowsp)}")
print(f"  mean edge as computed today (raw)        : {statistics.fmean(raw)*100:+.3f}pp")
print(f"  mean edge if de-vigged proportionally    : {statistics.fmean(prop)*100:+.3f}pp   "
      f"(shift +{ (statistics.fmean(prop)-statistics.fmean(raw))*100:.3f}pp)")
print(f"  mean edge if de-vigged additively        : {statistics.fmean(add)*100:+.3f}pp   "
      f"(shift +{ (statistics.fmean(add)-statistics.fmean(raw))*100:.3f}pp)")
neg=sum(1 for x in raw if x<0); negp=sum(1 for x in prop if x<0)
print(f"  placed bets showing a NEGATIVE edge: raw {neg}/{len(raw)}  ->  de-vigged {negp}/{len(prop)}")
print()
print("  Break-even reference on a -110 / -110 market:")
print(f"    raw implied on either side = {ap('-110'):.4f}  (sum {2*ap('-110'):.4f})")
print(f"    proportional fair prob     = {ap('-110')/(2*ap('-110')):.4f}")
print("    Kelly / bet decisions must use the RAW number (you pay the vig);")
print("    only a 'do we beat the market's opinion' read wants the fair one.")

print()
print("=" * 78)
print("E. Does anything GATE on edge?  (grep-verified)")
print("=" * 78)
print("  tracker._apply_odds_to_row takes min_edge but never reads it "
      "(only in comments at 3402/3623).")
print("  STRONG auto-bets regardless of edge (T2.24); LEAN is track-only.")
print("  => edge_on_pick is DISPLAY + SORT + telegram text only.")

"""ANALYSIS ONLY -- part 3: what moved under the stale edges?"""
import csv, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import tracker
ap = tracker.american_to_prob
rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
def F(r,k):
    v=(r.get(k) or "").strip()
    try: return float(v)
    except (ValueError,TypeError): return None

print("For every stale edge_on_pick: was the PRICE stale, or the PROB stale?")
print("  price-stale  => stored implied_* disagrees with market_*_odds")
print("  prob-stale   => implied_* agrees with odds, so edge_on_pick was")
print("                  computed from an older nrfi_prob/yrfi_prob")
print()
price_stale=prob_stale=0
rows_out=[]
for r in rows:
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    ocol="market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    icol="implied_nrfi_prob" if side=="NRFI" else "implied_yrfi_prob"
    mcol="nrfi_prob" if side=="NRFI" else "yrfi_prob"
    imp=ap(r.get(ocol,"")); m=F(r,mcol); got=F(r,"edge_on_pick"); stored_imp=F(r,icol)
    if imp is None or m is None or got is None: continue
    if abs(got-(m-imp))<=1e-3: continue
    if stored_imp is not None and abs(stored_imp-imp)>1e-3:
        price_stale+=1; kind="PRICE"
    else:
        prob_stale+=1; kind="PROB"
        implied_old_p = got + (stored_imp if stored_imp is not None else imp)
        rows_out.append((r,kind,m,implied_old_p,got,m-imp))
print(f"  price-stale rows: {price_stale}")
print(f"  prob-stale  rows: {prob_stale}")
print()
print("  prob-stale examples -- 'p the edge implies' vs 'p stored now':")
for r,kind,m,oldp,got,true in rows_out[:12]:
    print(f"    {r['date']} {r['away_team']}@{r['home_team']} {r['pick_side']:4} "
          f"edge implies p={oldp:.4f}   stored {r['pick_side'].lower()}_prob={m:.4f}   "
          f"bet={r.get('bet_placed')}")

print()
print("The 13 blank-but-computable edge_on_pick rows (board shows no edge):")
n=0
for r in rows:
    side=(r.get("pick_side") or "").upper()
    if side not in ("NRFI","YRFI"): continue
    ocol="market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    mcol="nrfi_prob" if side=="NRFI" else "yrfi_prob"
    imp=ap(r.get(ocol,"")); m=F(r,mcol)
    if imp is None or m is None: continue
    if F(r,"edge_on_pick") is not None: continue
    n+=1
    print(f"    {r['date']} {r['away_team']}@{r['home_team']} {r['pick_strength']:6} {side} "
          f"would show {(m-imp)*100:+.2f}%  bet={r.get('bet_placed')}")
print(f"  total {n}")

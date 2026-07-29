import csv, statistics as st
def a2p(s):
    s=str(s or "").strip().replace(" ","")
    if not s: return None
    try: o=float(s)
    except ValueError: return None
    if o==0: return None
    return 100.0/(o+100.0) if o>0 else abs(o)/(abs(o)+100.0)
def ppu(s):   # net profit per unit staked
    s=str(s or "").strip().replace(" ","")
    if not s: return None
    try: o=float(s)
    except ValueError: return None
    if o==0: return None
    return o/100.0 if o>0 else 100.0/abs(o)
def F(v):
    v=(v or "").strip()
    try: return float(v)
    except ValueError: return None
rows=list(csv.DictReader(open("data/picks_2026.csv",encoding="utf-8")))

print("[7] Is RAW implied the true break-even?  EV/unit = p*b - (1-p).")
print("    Compare sign(edge_raw) vs sign(EV) and sign(edge_devig) vs sign(EV)\n")
mis_raw=mis_dv=0; n=0
negs=[]
for r in rows:
    if (r.get("bet_placed") or "").strip().upper()!="Y": continue
    side=(r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    nn,yy=a2p(r.get("market_nrfi_odds")),a2p(r.get("market_yrfi_odds"))
    if nn is None or yy is None: continue
    p=F(r.get("nrfi_prob" if side=="NRFI" else "yrfi_prob"))
    b=ppu(r.get("market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"))
    if p is None or b is None: continue
    n+=1
    imp = nn if side=="NRFI" else yy
    fair = imp/(nn+yy)
    ev   = p*b-(1.0-p)
    e_raw, e_dv = p-imp, p-fair
    if (e_raw>0)!=(ev>0): mis_raw+=1
    if (e_dv >0)!=(ev>0): mis_dv +=1
    if e_raw<0:
        negs.append((r.get("date"),f"{r.get('away_team')}@{r.get('home_team')}",side,
                     r.get("market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"),
                     100*e_raw,100*e_dv,ev,F(r.get("profit_loss_units"))))
print(f"    n={n} placed bets")
print(f"    sign(edge RAW)     disagrees with sign(EV):  {mis_raw}  <-- 0 means RAW is the exact break-even")
print(f"    sign(edge DEVIG)   disagrees with sign(EV):  {mis_dv}   <-- these are -EV bets a devig would show as +edge")

print(f"\n[8] The {len(negs)} 'negative edge' placed bets the report says are mislabeled:")
print(f"    {'date':<11}{'game':<10}{'side':<6}{'price':>7}{'edgeRAW':>9}{'edgeDVG':>9}{'EV/u':>9}{'P&L':>8}")
tot_ev=0.0; tot_pl=0.0
for d,g,s,pr,er,ed,ev,pl in negs:
    tot_ev+=ev; tot_pl += pl or 0.0
    print(f"    {d:<11}{g:<10}{s:<6}{pr:>7}{er:>+8.2f}%{ed:>+8.2f}%{ev:>+9.4f}{(pl if pl is not None else 0):>+8.2f}")
print(f"    {'':<34}{'TOTAL':>16}{tot_ev:>+18.4f}{tot_pl:>+8.2f}")
print(f"    mean EV/unit on these = {tot_ev/len(negs):+.4f}  (negative => they really are -EV at the price paid)")
print(f"    realized P&L on these = {tot_pl:+.2f}u")

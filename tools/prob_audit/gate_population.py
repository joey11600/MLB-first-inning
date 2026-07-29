#!/usr/bin/env python3
"""Audit scratch: how many graded 2026 games does the validators' 0.44 gate
select vs the live 0.40 gate?  Read-only."""
import csv, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent

def f(s):
    try: return float((s or "").strip())
    except Exception: return None

rows = list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv", encoding="utf-8")))
graded = []
for r in rows:
    try:
        fa = int(float(r.get("fi_away_runs") or "nan")); fh = int(float(r.get("fi_home_runs") or "nan"))
    except Exception:
        continue
    p = f(r.get("nrfi_prob")); lam = f(r.get("lambda_lr_total"))
    if p is None: continue
    graded.append(dict(date=r["date"], p=p, lam=lam, nrfi=1 if (fa+fh)==0 else 0,
                       yodds=r.get("market_yrfi_odds",""), bet=r.get("bet_placed",""),
                       strength=r.get("pick_strength",""), side=r.get("pick_side","")))

print(f"graded rows with a stored nrfi_prob: {len(graded)}")

def payout(a):
    s=(a or "").strip()
    try: n=int(float(s))
    except Exception: return None
    return n/100.0 if n>0 else 100.0/abs(n)

for gate in (0.44, 0.40, 0.36):
    for use_lam in (False, True):
        sel=[g for g in graded if g["p"]<gate and (not use_lam or (g["lam"] is not None and g["lam"]>=0.838))]
        w=sum(1 for g in sel if g["nrfi"]==0)
        pl=0.0; priced=0
        for g in sel:
            pay=payout(g["yodds"])
            if pay is None: pay=100/110.0
            else: priced+=1
            pl += pay if g["nrfi"]==0 else -1
        n=len(sel)
        print(f"  gate p<{gate}  lam_floor={use_lam!s:<5} n={n:4d}  W={w:4d}  hit={w/n*100 if n else 0:5.1f}%  "
              f"flat P&L={pl:+8.2f}u  (priced {priced})")

a=set(id(g) for g in graded if g["p"]<0.44)
b=set(id(g) for g in graded if g["p"]<0.40)
print(f"\nextra games 0.44 admits over 0.40 (no lambda floor): {len(a)-len(b)}")
la=[g for g in graded if g["p"]<0.44 and g["lam"] is not None and g["lam"]>=0.838]
lb=[g for g in graded if g["p"]<0.40 and g["lam"] is not None and g["lam"]>=0.838]
print(f"extra games 0.44 admits over 0.40 (WITH lambda floor 0.838): {len(la)-len(lb)}")

band=[g for g in graded if 0.40<=g["p"]<0.44 and g["lam"] is not None and g["lam"]>=0.838]
w=sum(1 for g in band if g["nrfi"]==0)
pl=0.0
for g in band:
    pay=payout(g["yodds"]) or (100/110.0)
    pl += pay if g["nrfi"]==0 else -1
print(f"\nthe 0.40-0.44 band alone (lambda>=0.838): n={len(band)} W={w} hit={w/len(band)*100:.1f}% P&L={pl:+.2f}u")

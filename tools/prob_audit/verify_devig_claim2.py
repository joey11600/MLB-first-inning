import csv, statistics as st
def a2p(s):
    s=str(s or "").strip().replace(" ","")
    if not s: return None
    try: o=float(s)
    except ValueError: return None
    if o==0: return None
    return 100.0/(o+100.0) if o>0 else abs(o)/(abs(o)+100.0)
def F(v):
    v=(v or "").strip()
    try: return float(v)
    except ValueError: return None
rows=list(csv.DictReader(open("data/picks_2026.csv",encoding="utf-8")))

# Are the 46 mismatches explained by DEVIG (uniform ~+3.3pp) or by prob staleness?
bad=[]
for r in rows:
    side=(r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    stored=F(r.get("edge_on_pick"))
    p=F(r.get("nrfi_prob" if side=="NRFI" else "yrfi_prob"))
    n,y=a2p(r.get("market_nrfi_odds")),a2p(r.get("market_yrfi_odds"))
    imp=n if side=="NRFI" else y
    if stored is None or p is None or imp is None: continue
    d=stored-(p-imp)
    if abs(d)>1e-4:
        bad.append((d, r.get("date"), side, r.get("bet_placed"),
                    F(r.get("implied_nrfi_prob" if side=="NRFI" else "implied_yrfi_prob")), imp))
print(f"[4] {len(bad)} rows where stored edge != model_p - raw_implied(current market_*)")
ds=[b[0] for b in bad]
print(f"    deviation range {min(ds):+.5f} .. {max(ds):+.5f}   mean {st.mean(ds):+.5f}")
print(f"    a devig would show a UNIFORM shift of about +0.0330; sign split: "
      f"{sum(1 for d in ds if d>0)} pos / {sum(1 for d in ds if d<0)} neg")
# do stored implied_* columns match current market_* odds on those rows?
stale_imp=sum(1 for b in bad if b[4] is not None and abs(b[4]-b[5])>1e-4)
print(f"    of those, rows whose STORED implied_* disagrees with current market_* odds: {stale_imp}")

# Does stored edge match model_p - STORED implied_* column? (the true invariant)
devs=[]
for r in rows:
    side=(r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    stored=F(r.get("edge_on_pick"))
    p=F(r.get("nrfi_prob" if side=="NRFI" else "yrfi_prob"))
    imp=F(r.get("implied_nrfi_prob" if side=="NRFI" else "implied_yrfi_prob"))
    if None in (stored,p,imp): continue
    devs.append(abs(stored-(p-imp)))
print(f"\n[5] stored edge_on_pick == nrfi/yrfi_prob - STORED implied_* column:")
print(f"    n={len(devs)} max|dev|={max(devs):.6f}  rows >1e-4: {sum(1 for d in devs if d>1e-4)}")

# [6] sanity: is a devig anywhere in the stored implied columns?
S=[]
for r in rows:
    n,y=F(r.get("implied_nrfi_prob")),F(r.get("implied_yrfi_prob"))
    if n is not None and y is not None: S.append(n+y)
print(f"\n[6] STORED implied_nrfi_prob + implied_yrfi_prob: n={len(S)} mean={st.mean(S):.5f}")
print(f"    (1.000 would mean de-vigged at write time; ~1.066 means raw)")

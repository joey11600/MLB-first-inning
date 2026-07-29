import csv, statistics as st
rows=list(csv.DictReader(open("data/picks_2026.csv",encoding="utf-8")))
def f(v):
    v=(v or "").strip()
    try: return float(v) if v else None
    except: return None
# which dates lack lambda_lr_total
miss={}
for r in rows:
    if f(r.get("lambda_lr_total")) is None:
        miss[r["date"]]=miss.get(r["date"],0)+1
ks=sorted(miss)
print("rows missing lambda_lr_total: %d across %d dates, %s .. %s" % (sum(miss.values()),len(ks),ks[0],ks[-1]))
miss2=[r["date"] for r in rows if f(r.get("combined_lambda")) is None]
print("rows missing combined_lambda:", len(miss2))

for d in ("2026-07-28","2026-07-27"):
    sl=[r for r in rows if r["date"]==d]
    if not sl: continue
    cl=[f(r["combined_lambda"]) for r in sl if f(r["combined_lambda"]) is not None]
    lr=[f(r["lambda_lr_total"]) for r in sl if f(r["lambda_lr_total"]) is not None]
    print(f"\n{d}: {len(sl)} games")
    print("  combined_lambda mean %.4f (min %.3f max %.3f)"%(st.mean(cl),min(cl),max(cl)))
    print("  lambda_lr_total mean %.4f (min %.3f max %.3f)"%(st.mean(lr),min(lr),max(lr)))
    # ordering
    a=sorted(sl,key=lambda r:-f(r["combined_lambda"]))
    b=sorted(sl,key=lambda r:-f(r["lambda_lr_total"]))
    print("  rank by combined_lambda:", [f'{r["away_team"]}@{r["home_team"]}' for r in a])
    print("  rank by lambda_lr_total:", [f'{r["away_team"]}@{r["home_team"]}' for r in b])
    for r in sl:
        print("   %-9s cl=%.3f lr=%.3f  nrfi=%.3f  %s"%(f'{r["away_team"]}@{r["home_team"]}',f(r["combined_lambda"]),f(r["lambda_lr_total"]),f(r["nrfi_prob"]),r["pick_label"]))

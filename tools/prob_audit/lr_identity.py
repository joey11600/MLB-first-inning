import csv, math, statistics as st
rows=list(csv.DictReader(open("data/picks_2026.csv",encoding="utf-8")))
def f(v):
    v=(v or "").strip()
    try: return float(v) if v else None
    except: return None
d=[]
for r in rows:
    t1,b1,tot=f(r.get("lambda_lr_t1")),f(r.get("lambda_lr_b1")),f(r.get("lambda_lr_total"))
    if None not in (t1,b1,tot): d.append(abs(tot-(t1+b1)))
print("|lambda_lr_total - (t1+b1)| n=%d max %.6f mean %.6f"%(len(d),max(d),st.mean(d)))
# implied raw p_nrfi from each lambda, vs calibrated nrfi_prob (monotone check)
pairs=[(f(r["combined_lambda"]),f(r["lambda_lr_total"]),f(r["nrfi_prob"])) for r in rows
       if None not in (f(r.get("combined_lambda")),f(r.get("lambda_lr_total")),f(r.get("nrfi_prob")))]
def pear(xs,ys):
    mx=st.mean(xs);my=st.mean(ys)
    return sum((a-mx)*(b-my) for a,b in zip(xs,ys))/math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
cl=[p[0] for p in pairs];lr=[p[1] for p in pairs];np_=[p[2] for p in pairs]
print("n=%d"%len(pairs))
print("corr(combined_lambda, calibrated nrfi_prob) = %+.4f"%pear(cl,np_))
print("corr(lambda_lr_total, calibrated nrfi_prob) = %+.4f"%pear(lr,np_))
# implied raw from lambda_lr_total should reconstruct a monotone map to nrfi_prob
imp=[math.exp(-x) for x in lr]
print("corr(exp(-lambda_lr_total), nrfi_prob)      = %+.4f"%pear(imp,np_))

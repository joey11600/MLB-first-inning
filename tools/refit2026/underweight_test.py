#!/usr/bin/env python3
"""underweight_test.py -- is the model UNDER-weighting its pitcher-quality inputs?

THE HYPOTHESIS (operator, 2026-09-02): *"i think xwoba, and also last 10
nrfi/yrfi rates are more important than we think."*

HOW IT IS TESTED.  Not by adding a feature -- these are already in the model --
but by changing how hard the L2 penalty SHRINKS them.  The shipped fit puts
L2 = 0.5 on every feature equally; this refits with a per-feature penalty
vector so the named group can be made 2x / 5x / 25x freer (bigger weights) or
2x tighter, on all three splits, park rebuilt from training seasons only, CIR
calibrator fit on train only, cal-gate ceiling re-derived per split, paired
bootstrap over games.  FOCUS_GROUP=xwoba|form|both isolates each half of the
hypothesis.  A rank-matched fallback covers the documented trap where a freer
fit shifts the calibrated scale and a fixed 0.42 cut selects nothing.

THE ANSWER (2026-09-02), and it splits the hypothesis in two:

1. **fi_xwoba is already weighted about right.**  Its fitted weight is stable
   across all three splits (+0.0324 / +0.0265 / +0.0307) -- the most stable
   coefficient in the model -- and freeing it helps 2024 (Brier -0.00139, 90%
   CI [-0.00227, -0.00050]) while being flat on 2025 and slightly worse on
   2026 (AUC -0.0018).  No change indicated.

2. **The last-5/last-10 NRFI rates are the live lead.**  Freeing them is the
   single best-performing variant in the whole experiment ON 2026: AUC 0.5278
   -> 0.5319, delta +0.0041 with a 90% CI of [+0.0004, +0.0078] that EXCLUDES
   ZERO, Brier -0.00038, and money better on both bases (65 bets @ 69.2%,
   +14.52u flat / +43.58u Kelly at 2x freer, vs 54 @ 68.5%, +10.49u / +28.49u
   shipped).  Flat on 2024 and 2025 -- it never HURTS a split, which is rare
   here, but it clears the bar in only one.

   THE CATCH, and it is why this is a lead and not a ship: the fitted weight
   FLIPS SIGN by season.  2024 wants +0.0097, 2025 wants -0.0089, 2026 wants
   +0.0001, and when freed they diverge further (+0.0076 / -0.0318 / -0.0123).
   Only the negative sign is physically sensible (more no-run history should
   mean fewer runs).  A relationship whose sign is not stable across seasons is
   the same shape as every artifact this directory has already killed.

   Worth noting alongside: these inputs are COARSE.  last-5 takes only 10
   distinct values on 2026 with 21-23% of games sitting at exactly 1.000, and
   last-10 takes 26.  A properly shrunk continuous version (the same
   empirical-Bayes treatment fi_xwoba got) is the obvious next candidate and
   has never been built.

NEXT STEP IF PURSUED: selection-aware permutation null per the
feature_test_methodology memory, then a decision at the next approved refit --
not a standalone weight edit, which cannot be done without a refit anyway
(the feature standardisation is frozen into the shipped artifacts).

Writes nothing.  Read-only validation, like everything else in this directory.

USAGE:
    python tools/refit2026/underweight_test.py              # both groups
    FOCUS_GROUP=xwoba python tools/refit2026/underweight_test.py
    FOCUS_GROUP=form  python tools/refit2026/underweight_test.py
"""
import sys, numpy as np, pandas as pd
from pathlib import Path
ROOT=Path(r"C:\Users\Pinellas Liquidation\MLB-first-inning"); sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/"tools"/"refit2026"))
from calibration import CIRCalibrator
from harness import T1_SHIPPED,B1_SHIPPED,build_park,load,matrix,predict,auc,brier,logloss
from test_fi_pooled import attach
pd.set_option('display.width',270)
fac=pd.read_csv(ROOT/"data/candidates/factor_fi_pooled.csv"); bt=ROOT/"data/backtests"
def ld(p,pc,s):
    d=load(p,pc,s); own={c:pd.to_numeric(d[c],errors='coerce') for c in ('home_fi_xwoba','away_fi_xwoba') if c in d.columns}
    d=attach(d.drop(columns=list(own)),fac)
    for c,v in own.items(): d[c]=v.fillna(d[c]).values
    return d
d24=ld(bt/"backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv","home",2024)
d25=ld(bt/"backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv","home",2025)
d26=ld(ROOT/"data/picks_2026.csv","home_team",2026)
T1=T1_SHIPPED+["home_fi_xwoba"]; B1=B1_SHIPPED+["away_fi_xwoba"]; L2=0.5
def fit_lr_vec(X,y,l2vec,iters=300):
    mu,sd=X.mean(0),X.std(0); sd=np.where(sd<1e-12,1.0,sd)
    Z=np.c_[np.ones(len(X)),(X-mu)/sd]; w=np.zeros(Z.shape[1])
    R=np.diag(np.r_[0.0,np.asarray(l2vec,float)])
    for _ in range(iters):
        p=1/(1+np.exp(-Z@w)); g=Z.T@(y-p)/len(y)-R@w
        H=(Z*(p*(1-p))[:,None]).T@Z/len(y)+R+1e-8*np.eye(Z.shape[1])
        s=np.linalg.solve(H,g); w+=s
        if np.max(np.abs(s))<1e-9: break
    return w,mu,sd
import os
_G=os.environ.get("FOCUS_GROUP","both")
_XW_T1={"home_fi_xwoba"}; _XW_B1={"away_fi_xwoba"}
_RF_T1={"home_p_last5_pitcher_nrfi","home_p_last10_pitcher_nrfi"}
_RF_B1={"away_p_last5_pitcher_nrfi","away_p_last10_pitcher_nrfi"}
FOCUS_T1=_XW_T1 if _G=="xwoba" else _RF_T1 if _G=="form" else _XW_T1|_RF_T1
FOCUS_B1=_XW_B1 if _G=="xwoba" else _RF_B1 if _G=="form" else _XW_B1|_RF_B1
print(f"### FOCUS GROUP = {_G}  -> T1 {sorted(FOCUS_T1)}")
CORE_T1=["fi_park_nrfi_rate","home_fi_xwoba","home_p_last10_pitcher_nrfi","away_top3_ops_vs_oppHand","home_xera","wx_temp_c","wx_humidity"]
CORE_B1=["fi_park_nrfi_rate","away_fi_xwoba","away_p_last10_pitcher_nrfi","home_top3_ops_vs_oppHand","away_whiff_pct_rank","wx_temp_c","wx_humidity"]
def l2v(feats,focus,mult): return [L2*(mult if f in focus else 1.0) for f in feats]
VARIANTS={
 "shipped (L2 0.5 on all)":        ("l2",1.0,None),
 "focus x1/2 penalty (2x freer)":  ("l2",0.5,None),
 "focus x1/5 penalty (5x freer)":  ("l2",0.2,None),
 "focus x1/25 penalty (~unpen.)":  ("l2",0.04,None),
 "focus x4 penalty (2x tighter)":  ("l2",4.0,None),
 "CORE 7 features only":           ("feats",None,(CORE_T1,CORE_B1)),
}
def implied(o): return -o/(-o+100) if o<0 else 100/(o+100)
def payout(o): return o/100 if o>0 else 100/-o
def kelly(p,o):
    b=payout(o); f=min(max((p*b-(1-p))/b,0)*0.25,0.10); s=f*100
    return 0.0 if s<0.1 else max(0.5,round(s))
def run(tr,te,kind,mult,feats):
    t1f,b1f=(feats if kind=="feats" else (T1,B1))
    tr,te=tr.copy(),te.copy()
    for c in ("home_fi_xwoba","away_fi_xwoba"):
        mu=tr[c].mean(); tr[c]=tr[c].fillna(mu); te[c]=te[c].fillna(mu)
    pk,b0=build_park(tr,50)
    lt=l2v(t1f,FOCUS_T1,mult if kind=="l2" else 1.0); lb=l2v(b1f,FOCUS_B1,mult if kind=="l2" else 1.0)
    wt=fit_lr_vec(matrix(tr,t1f,pk,b0),tr.y_t1.values,lt); wb=fit_lr_vec(matrix(tr,b1f,pk,b0),tr.y_b1.values,lb)
    raw=lambda d:(1-predict(*wt,matrix(d,t1f,pk,b0)))*(1-predict(*wb,matrix(d,b1f,pk,b0)))
    rtr,rte=raw(tr),raw(te)
    cal=CIRCalibrator.fit(list(rtr),list((tr.y==0).astype(int)),n_bins=20)
    ctr=np.array([cal.predict(float(v)) for v in rtr]); cte=np.array([cal.predict(float(v)) for v in rte])
    cand=ctr[ctr<0.42]
    # A freer fit shifts the calibrated scale, so a FIXED 0.42 cut can select
    # zero train candidates (the documented refit2026 trap).  Fall back to a
    # rank-matched cut so money stays comparable across variants.
    ceil=float(np.quantile(cand,0.87)) if len(cand)>=20 else float(np.quantile(ctr,0.07))
    wmap={f:wt[0][i+1] for i,f in enumerate(t1f)}; wmap.update({f:wb[0][i+1] for i,f in enumerate(b1f)})
    return cte,ceil,wmap
splits=[("2024 (train 2025)",d25,d24),("2025 (train 2024)",d24,d25),("2026 (train 24+25)",pd.concat([d24,d25],ignore_index=True),d26)]
store={}
for lab,tr,te in splits:
    te=te.reset_index(drop=True); y=(te.y.values==1).astype(int); ynrfi=1-y
    price=pd.to_numeric(te.get("market_yrfi_odds"),errors="coerce") if "market_yrfi_odds" in te.columns else pd.Series(np.nan,index=te.index)
    print("="*118); print(f"  SPLIT {lab}   n={len(te)}")
    print(f"  {'variant':<32} {'AUC':>7} {'Brier':>9} {'logloss':>9} | {'w fi_xwoba':>11} {'w last10':>9} | {'bets':>5} {'hit':>6} {'flat u':>8} {'Kelly u':>8}")
    for name,(kind,mult,feats) in VARIANTS.items():
        cte,ceil,wmap=run(tr,te,kind,mult,feats)
        A,B,L=auc(ynrfi,cte),brier(ynrfi,cte),logloss(ynrfi,cte)
        s=cte<ceil; pr=price[s]; yy=y[s]
        if pr.notna().sum()>0:
            ok=pr.notna().values; o=pr[ok].values; yv=yy[ok]; p=1-cte[s][ok]
            stk=np.array([kelly(pp,oo) for pp,oo in zip(p,o)]); k=stk>0
            flat=np.where(yv[k]==1,[payout(x) for x in o[k]],-1.0).sum()
            kel=np.where(yv[k]==1,stk[k]*[payout(x) for x in o[k]],-stk[k]).sum(); nb=int(k.sum()); hit=yv[k].mean() if k.sum() else np.nan
        else:
            nb=int(s.sum()); hit=yy.mean() if s.sum() else np.nan; flat=np.where(yy==1,100/112,-1.0).sum(); kel=np.nan
        print(f"  {name:<32} {A:7.4f} {B:9.5f} {L:9.5f} | {wmap.get('home_fi_xwoba',np.nan):+11.4f} {wmap.get('home_p_last10_pitcher_nrfi',np.nan):+9.4f} | {nb:>5} {hit:6.3f} {flat:+8.2f} {kel:+8.2f}")
        store[(lab,name)]=cte
    rng=np.random.default_rng(3); a=store[(lab,"shipped (L2 0.5 on all)")]; idx=np.arange(len(y))
    for cand in ["focus x1/5 penalty (5x freer)","CORE 7 features only"]:
        b=store[(lab,cand)]; dB=np.empty(1500); dA=np.empty(1500)
        for i in range(1500):
            ss=rng.choice(idx,len(idx),replace=True)
            dB[i]=brier(ynrfi[ss],b[ss])-brier(ynrfi[ss],a[ss]); dA[i]=auc(ynrfi[ss],b[ss])-auc(ynrfi[ss],a[ss])
        print(f"    {cand} minus shipped: Brier {dB.mean():+.5f} 90% CI [{np.percentile(dB,5):+.5f},{np.percentile(dB,95):+.5f}] (neg=better) | AUC {dA.mean():+.4f} [{np.percentile(dA,5):+.4f},{np.percentile(dA,95):+.4f}]")

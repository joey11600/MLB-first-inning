#!/usr/bin/env python3
"""wx_ablation.py -- does the weather block earn its place in the shipped v3 model?

THE QUESTION (operator, 2026-09-02, looking at a "WHY THIS PICK" panel whose
top four rows were temperature and humidity): *"theres no reason temperature
and humidity should have this much of an impact on the model."*

WHAT THE PANEL ACTUALLY SHOWS.  It ranks the five largest |contribution|
values per half with no magnitude floor, so on a game where nothing else has
an opinion the loudest whisper is printed as if it were a shout.  For the
game in question (MIA@KC 2026-09-02, 37.5 C / 24% humidity -- a 2.7-sigma and
a 2.1-sigma input) ALL FOUR weather terms together moved the pick 2.03
percentage points (NRFI 56.0% -> 54.0%), the verdict was identical either
way, and the total absolute push across all 40 feature slots was 0.385
log-odds.  Across the v3 era a weather input is the single biggest driver in
1 game out of 135.  So the panel was faithful and the reading was wrong --
but the underlying question deserved a real test, which is this file.

THE TEST.  Refit the shipped 20-feature v3 shape (L2 0.5) with weather
subsets removed, on all three splits, park map rebuilt from TRAINING SEASONS
ONLY, CIR calibrator fit on train only, cal-gate ceiling re-derived per split
(87th pctile of train candidates), paired bootstrap over games.

THE ANSWER (2026-09-02): KEEP THE WEATHER FEATURES.  Dropping all four
improves 2024 (Brier -0.00117, 90% CI [-0.00245, +0.00011] -- touches zero),
clearly HURTS 2025 (Brier +0.00161 [+0.00073, +0.00248]; AUC -0.0064
[-0.0103, -0.0026], both CIs exclude zero), and is flat on 2026 (Brier
-0.00017 [-0.00086, +0.00054]; AUC -0.0011 [-0.0087, +0.0065]).  CLAUDE.md:
reject anything that helps in only one direction.  Same for every subset --
"drop temp only" is the best-looking cell on 2025 money (+14.18u flat) and is
worse than shipped on 2026 (+6.92u vs +10.49u).

THE FINDING THAT MATTERS MORE, from the same decomposition.  Share of the
model's game-to-game swing in the v3 era (135 games, real inputs):
    park                        24.0%   <- the single biggest input
    all four weather            11.1%   (temperature 2.1%, humidity 3.2%)
    first-inning pitcher xwOBA  10.0%   (the celebrated v3 feature)
    the other 15 inputs         52.0%
The largest single driver of which games get picked is the park factor -- and
park_null.py (re-run the same day) says the shipped park map ranks 2026 games
WORSE THAN RANDOM RELABELLING (beats 4% of placebos on AUC).  The operator's
instinct that the model keys on the wrong thing is right; the culprit is the
park term, not the weather.  It cannot be removed in isolation (the frozen
feature standardisation) -- ablation belongs to the next approved refit.

Writes nothing.  Read-only validation, like everything else in this directory.
"""
import sys, numpy as np, pandas as pd
from pathlib import Path
ROOT=Path(r"C:\Users\Pinellas Liquidation\MLB-first-inning"); sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/"tools"/"refit2026"))
from calibration import CIRCalibrator
from harness import T1_SHIPPED,B1_SHIPPED,build_park,fit_lr,load,matrix,predict,auc,brier,logloss
from test_fi_pooled import attach
pd.set_option('display.width',260)
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
VARIANTS={
 "shipped (all 20)":         ([],[]),
 "drop temp+humidity":       (["wx_temp_c","wx_humidity"],)*2,
 "drop all weather":         (["wx_temp_c","wx_humidity","wx_wind_kmh","wx_is_dome"],)*2,
 "drop temp only":           (["wx_temp_c"],)*2,
 "drop humidity only":       (["wx_humidity"],)*2,
 "keep dome, drop t/h/w":    (["wx_temp_c","wx_humidity","wx_wind_kmh"],)*2,
}
def implied(o): return -o/(-o+100) if o<0 else 100/(o+100)
def payout(o): return o/100 if o>0 else 100/-o
def kelly(p,o):
    b=payout(o); f=min(max((p*b-(1-p))/b,0)*0.25,0.10); s=f*100
    return 0.0 if s<0.1 else max(0.5,round(s))
def run(tr,te,drop_t1,drop_b1):
    t1f=[f for f in T1 if f not in drop_t1]; b1f=[f for f in B1 if f not in drop_b1]
    tr,te=tr.copy(),te.copy()
    for c in ("home_fi_xwoba","away_fi_xwoba"):
        mu=tr[c].mean(); tr[c]=tr[c].fillna(mu); te[c]=te[c].fillna(mu)
    pk,b0=build_park(tr,50)
    wt=fit_lr(matrix(tr,t1f,pk,b0),tr.y_t1.values,L2); wb=fit_lr(matrix(tr,b1f,pk,b0),tr.y_b1.values,L2)
    raw=lambda d:(1-predict(*wt,matrix(d,t1f,pk,b0)))*(1-predict(*wb,matrix(d,b1f,pk,b0)))
    rtr,rte=raw(tr),raw(te)
    cal=CIRCalibrator.fit(list(rtr),list((tr.y==0).astype(int)),n_bins=20)
    ctr=np.array([cal.predict(float(v)) for v in rtr]); cte=np.array([cal.predict(float(v)) for v in rte])
    ceil=float(np.quantile(ctr[ctr<0.42],0.87))
    return cte,ceil
splits=[("2024 (train 2025)",d25,d24),("2025 (train 2024)",d24,d25),("2026 (train 24+25)",pd.concat([d24,d25],ignore_index=True),d26)]
store={}
for lab,tr,te in splits:
    te=te.reset_index(drop=True); y=(te.y.values==1).astype(int); ynrfi=1-y
    price=pd.to_numeric(te.get("market_yrfi_odds"),errors="coerce") if "market_yrfi_odds" in te.columns else pd.Series(np.nan,index=te.index)
    print("="*104); print(f"  SPLIT {lab}   n={len(te)}   actual YRFI {y.mean():.3f}")
    print(f"  {'variant':<26} {'AUC':>7} {'Brier':>9} {'logloss':>9}  {'STRONG bets':>11} {'hit':>6} {'flat u':>8} {'Kelly u':>8}")
    base=None
    for name,(dt,db) in VARIANTS.items():
        cte,ceil=run(tr,te,dt,db)
        A=auc(ynrfi,cte); B=brier(ynrfi,cte); L=logloss(ynrfi,cte)
        s=cte<ceil
        pr=price[s]; yy=y[s]
        if pr.notna().sum()>0:
            ok=pr.notna().values; o=pr[ok].values; yv=yy[ok]; p=1-cte[s][ok]
            stk=np.array([kelly(pp,oo) for pp,oo in zip(p,o)]); keep=stk>0
            flat=np.where(yv[keep]==1,[payout(x) for x in o[keep]],-1.0).sum()
            kel=np.where(yv[keep]==1,stk[keep]*[payout(x) for x in o[keep]],-stk[keep]).sum()
            nb=int(keep.sum()); hit=yv[keep].mean()
        else:
            nb=int(s.sum()); hit=yy.mean(); flat=np.where(yy==1,100/112,-1.0).sum(); kel=np.nan
        if base is None: base=(A,B,L,cte)
        print(f"  {name:<26} {A:7.4f} {B:9.5f} {L:9.5f}  {nb:>11} {hit:6.3f} {flat:+8.2f} {kel:+8.2f}"+("   <- shipped" if name.startswith("shipped") else ""))
        store[(lab,name)]=cte
    # paired bootstrap of the key contrast
    rng=np.random.default_rng(7); a=store[(lab,"shipped (all 20)")]; b=store[(lab,"drop all weather")]
    dB=np.empty(2000); dA=np.empty(2000); idx=np.arange(len(y))
    for i in range(2000):
        s=rng.choice(idx,len(idx),replace=True)
        dB[i]=brier(ynrfi[s],b[s])-brier(ynrfi[s],a[s]); dA[i]=auc(ynrfi[s],b[s])-auc(ynrfi[s],a[s])
    print(f"  paired bootstrap, DROP-ALL-WEATHER minus shipped:  Brier {dB.mean():+.5f} 90% CI [{np.percentile(dB,5):+.5f},{np.percentile(dB,95):+.5f}] (neg=better)   AUC {dA.mean():+.4f} [{np.percentile(dA,5):+.4f},{np.percentile(dA,95):+.4f}]")

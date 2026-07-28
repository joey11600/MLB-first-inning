#!/usr/bin/env python3
"""
exp3: the noise ceiling for a single half-inning-pair outcome, and the
decisive money question -- does ANY model score carry information the
DraftKings price does not already have?

Three parts:
  1. how much of the model's edge is orthogonal to the market
     (logistic horse race, 2026 real captured prices only)
  2. the achievable-AUC ceiling given the demonstrable spread of true
     NRFI probability
  3. how much better the model would have to get, expressed as the
     required standard deviation of MARKET-ORTHOGONAL true probability
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def main():
    r26 = C.attach_production(C.load_2026())
    r25 = C.attach_production(C.load_2025())

    priced = [r for r in r26 if r["nrfi_odds"] is not None and r["yrfi_odds"] is not None]
    for r in priced:
        a, b = C.implied(r["nrfi_odds"]), C.implied(r["yrfi_odds"])
        r["mkt"] = a / (a + b)          # de-vigged market P(NRFI)
        r["hold"] = a + b - 1.0
        r["be"] = a                     # raw break-even for a NRFI bet

    y = np.array([r["y_nrfi"] for r in priced], float)
    mk = np.array([r["mkt"] for r in priced])
    pm = np.array([r["prod"] for r in priced])

    print("=" * 96)
    print("  EXP3  noise ceiling + market-orthogonality")
    print("=" * 96)
    print(f"  2026 games with BOTH real DK prices: n={len(priced)}   "
          f"actual NRFI={100*y.mean():.2f}%")
    print(f"  mean DK hold={100*np.mean([r['hold'] for r in priced]):.2f}%   "
          f"mean raw NRFI break-even={100*np.mean([r['be'] for r in priced]):.2f}%   "
          f"wall={100*(np.mean([r['be'] for r in priced])-y.mean()):+.2f}pp")
    print(f"  mean de-vigged market P(NRFI)={100*mk.mean():.2f}%  -> de-vigged bias "
          f"{100*(mk.mean()-y.mean()):+.2f}pp (market still over-prices NRFI this much)")

    print("\n  --- 1. WHO RANKS BETTER, AND IS ANY OF IT ORTHOGONAL? ---")
    print(f"      market de-vig  AUC = {C.auc(y, mk):.4f}   sd(p)={mk.std():.4f}")
    print(f"      production     AUC = {C.auc(y, pm):.4f}   sd(p)={pm.std():.4f}")
    print(f"      corr(logit mkt, logit prod) = {np.corrcoef(logit(mk), logit(pm))[0,1]:+.4f}")

    Xm = logit(mk)[:, None]
    Xp = logit(pm)[:, None]
    Xb = np.hstack([Xm, Xp])
    m_only = C.fit_lr(Xm, y, 1e-6)
    p_only = C.fit_lr(Xp, y, 1e-6)
    both = C.fit_lr(Xb, y, 1e-6)
    print(f"      logloss  market only = {C.logloss(y, C.predict_lr(m_only, Xm)):.5f}")
    print(f"      logloss  model  only = {C.logloss(y, C.predict_lr(p_only, Xp)):.5f}")
    print(f"      logloss  both        = {C.logloss(y, C.predict_lr(both, Xb)):.5f}")
    print(f"      standardized coefs in the joint fit: market={both['w'][0]:+.4f}  "
            f"model={both['w'][1]:+.4f}")
    print(f"      joint AUC = {C.auc(y, C.predict_lr(both, Xb)):.4f}")

    # residual of model beyond market
    a = np.polyfit(logit(mk), logit(pm), 1)
    resid = logit(pm) - (a[0] * logit(mk) + a[1])
    print(f"      model's market-orthogonal residual: AUC on NRFI = {C.auc(y, resid):.4f}  "
          f"(0.50 = no independent information)")

    def d(rows):
        yy = np.array([r["y_nrfi"] for r in rows], float)
        if yy.min() == yy.max():
            return float("nan")
        rr = np.array([r["_res"] for r in rows])
        return C.auc(yy, rr)
    for r, v in zip(priced, resid):
        r["_res"] = float(v)
    m, lo, hi = C.block_bootstrap_days(priced, d, B=2000, seed=3)
    print(f"      block bootstrap over days: residual AUC {m:.4f}  95% CI [{lo:.4f}, {hi:.4f}]")

    print("\n  --- 2. ACHIEVABLE-AUC CEILING FOR A ONE-INNING OUTCOME ---")
    print("      A calibrated forecaster's own spread is a LOWER bound on the spread of")
    print("      true P(NRFI).  Best demonstrated spreads:")
    p25 = np.array([r["prod"] for r in r25])
    print(f"        production 2025 sd={p25.std():.4f}   2026 sd={pm.std():.4f}")
    print(f"        DK de-vig  2026 sd={mk.std():.4f}")
    comb = C.predict_lr(both, Xb)
    print(f"        market+model joint (in-sample, optimistic) sd={comb.std():.4f}")
    print("      Oracle AUC if true P(NRFI) ~ Normal(0.485, sd), clipped:")
    rng = np.random.default_rng(0)
    for sd in (0.05, 0.06, 0.075, 0.09, 0.10, 0.125, 0.15, 0.20):
        n = 400000
        pt = np.clip(rng.normal(0.485, sd, n), 0.05, 0.95)
        yy = (rng.random(n) < pt).astype(int)
        print(f"        sd={sd:.3f} -> max achievable AUC = {C.auc(yy, pt):.4f}")

    print("\n  --- 3. HOW MUCH BETTER WOULD IT HAVE TO GET? ---")
    be = np.array([r["be"] for r in priced])
    print(f"      A NRFI bet needs true p > its own price's break-even.  Selecting the")
    print(f"      top q by any score, the wall to clear is the gap between actual hit")
    print(f"      rate and break-even.  Observed at every q it is negative.")
    print(f"      Required: sd of the MARKET-ORTHOGONAL part of true P(NRFI).")
    lam = {0.05: 2.063, 0.10: 1.755, 0.20: 1.400, 0.30: 1.159, 0.50: 0.798}
    base_gap = float(np.mean(be) - y.mean())
    for q, L in sorted(lam.items()):
        need_sd = base_gap / L
        print(f"        top {int(q*100):>3}% of the slate: needs orthogonal sd >= "
              f"{need_sd:.4f} just to break even (gap to close {100*base_gap:.2f}pp, "
              f"selection multiplier {L:.3f})")
    print(f"      Measured orthogonal residual sd in probability terms: ", end="")
    # convert residual (logit space) to probability spread at the mean
    slope = both["w"][1] / both["sd"][1]      # d logit(p_hat) / d resid-ish
    pr = C.predict_lr(both, Xb)
    only_m = C.predict_lr(m_only, Xm)
    print(f"{np.std(pr - only_m):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
exp8: nail the two things that survived, with CIs.

  A. is the YRFI-end / NRFI-end skill asymmetry REAL?  Assumption-free
     version: AUC(bottom q) - AUC(top q), block-bootstrapped over days,
     independently in 2025 and 2026.
  B. is the YRFI money edge model skill, or just DK over-pricing NRFI?
  C. translate "how much better would the NRFI model have to get" onto
     the familiar AUC scale, betting into the REAL observed DK prices.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

Q = 0.35


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())

    print("=" * 96)
    print("  EXP8-A  is the end-asymmetry real?   AUC(YRFI end) - AUC(NRFI end)")
    print("=" * 96)
    for q in (0.20, 0.35, 0.50):
        for tag, rows in (("2025", r25), ("2026", r26)):
            def stat(rr):
                p = np.array([r["prod"] for r in rr])
                y = np.array([r["y_nrfi"] for r in rr])
                if len(rr) < 100:
                    return float("nan")
                hi = p >= np.quantile(p, 1 - q)
                lo = p <= np.quantile(p, q)
                if y[hi].min() == y[hi].max() or y[lo].min() == y[lo].max():
                    return float("nan")
                return C.auc(y[lo], p[lo]) - C.auc(y[hi], p[hi])
            obs = stat(rows)
            m, lo_, hi_ = C.block_bootstrap_days(rows, stat, B=2000, seed=5)
            verdict = "CI excludes 0" if (lo_ > 0 or hi_ < 0) else "CI spans 0"
            print(f"      q={int(q*100):>3}%  {tag}  observed delta={obs:+.4f}  "
                  f"bootstrap {m:+.4f} 95% CI [{lo_:+.4f}, {hi_:+.4f}]  {verdict}")
        print()

    print("=" * 96)
    print("  EXP8-B  is the YRFI money edge SKILL, or just DK over-pricing NRFI?")
    print("=" * 96)
    priced = [r for r in r26 if r["yrfi_odds"] is not None and r["nrfi_odds"] is not None]
    for r in priced:
        a, b = C.implied(r["nrfi_odds"]), C.implied(r["yrfi_odds"])
        r["mkt"] = a / (a + b)
    y = np.array([r["y_nrfi"] for r in priced])
    print(f"      n={len(priced)}")
    print(f"      blind YRFI on every game: hit={100*np.mean(1-y):.2f}%  "
          f"need={100*np.mean([C.implied(r['yrfi_odds']) for r in priced]):.2f}%  "
          f"{sum(C.payout(r['yrfi_odds']) if not r['y_nrfi'] else -1.0 for r in priced):+.2f}u")
    print("      -> a pure market-bias story would show up HERE. It does not.")
    print("      Now split the model score from the market price:")
    # residualize the model against the market -- bet on the part DK does not have
    lg = lambda x: np.log(np.clip(x, 1e-6, 1 - 1e-6) / (1 - np.clip(x, 1e-6, 1 - 1e-6)))
    pm = np.array([r["prod"] for r in priced])
    mk = np.array([r["mkt"] for r in priced])
    a = np.polyfit(lg(mk), lg(pm), 1)
    res = lg(pm) - (a[0] * lg(mk) + a[1])
    for r, v in zip(priced, res):
        r["res"] = float(v)
    for lab, key, sgn in (("raw model p_nrfi (low = bet YRFI)", "prod", 1),
                          ("market-ORTHOGONAL part of the model", "res", 1)):
        s = sorted(priced, key=lambda r: sgn * r[key])
        print(f"      -- rank YRFI bets by {lab} --")
        for q in (0.05, 0.10, 0.20, 0.35):
            k = max(15, int(len(s) * q))
            sub = s[:k]
            h = np.mean([1 - r["y_nrfi"] for r in sub])
            nd = np.mean([C.implied(r["yrfi_odds"]) for r in sub])
            u = sum(C.payout(r["yrfi_odds"]) if not r["y_nrfi"] else -1.0 for r in sub)

            def st(rr):
                ss = sorted(rr, key=lambda r: sgn * r[key])[:max(8, int(len(rr) * q))]
                return float(np.mean([C.payout(r["yrfi_odds"]) if not r["y_nrfi"] else -1.0
                                      for r in ss]))
            mm, l_, h_ = C.block_bootstrap_days(priced, st, B=2000, seed=9)
            print(f"         top {int(q*100):>3}%  n={k:>4}  hit={100*h:>5.2f}%  need={100*nd:>5.2f}%  "
                  f"gap={100*(h-nd):>+6.2f}pp  {u:>+7.2f}u   ROI CI "
                  f"[{100*l_:+.1f}%, {100*h_:+.1f}%]")

    print("\n" + "=" * 96)
    print("  EXP8-C  required NRFI model quality, on the AUC scale, real DK prices")
    print("=" * 96)
    print("      Simulate: truth = DK de-vig + orthogonal noise of size s; the model")
    print("      observes truth with signal fraction f.  Bet its top 20% NRFI at the")
    print("      REAL price attached to each game.  Report the model's own AUC.")
    rng = np.random.default_rng(0)
    odds = np.array([r["nrfi_odds"] for r in priced], float)
    pay = np.array([C.payout(o) for o in odds])
    print(f"      {'orth sd':>9}{'signal f':>10}{'model AUC':>12}{'top20 hit%':>12}"
          f"{'ROI%':>9}")
    for s in (0.03, 0.05, 0.08):
        for f in (0.5, 0.75, 1.0):
            aucs, rois, hits = [], [], []
            for _ in range(200):
                extra = rng.normal(0, s, len(priced))
                pt = np.clip(mk + extra, .02, .98)
                yy = (rng.random(len(priced)) < pt).astype(float)
                sc = f * pt + (1 - f) * rng.normal(pt.mean(), pt.std(), len(priced))
                aucs.append(C.auc(yy, sc))
                o = np.argsort(-sc)[:int(.20 * len(priced))]
                rois.append(np.where(yy[o] > 0, pay[o], -1.0).mean())
                hits.append(yy[o].mean())
            print(f"      {s:>9.2f}{f:>10.2f}{np.mean(aucs):>12.4f}{100*np.mean(hits):>12.2f}"
                  f"{100*np.mean(rois):>+9.2f}")
    print(f"\n      For reference, the production model's ACTUAL AUC on these games: "
          f"{C.auc(y, pm):.4f}")
    print(f"      and DK's own de-vigged price AUC: {C.auc(y, mk):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

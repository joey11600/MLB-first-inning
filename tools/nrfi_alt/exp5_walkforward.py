#!/usr/bin/env python3
"""
exp5: exp4-B produced ONE cell that looked profitable -- a plain direct
LR on the union feature set, trained on the first 60% of 2026, betting
its top 20% NRFI on the last 40%.  n=94.  One split.  That is exactly
the shape of a false positive, so:

  1. does it survive a WALK-FORWARD (refit weekly, score only future)?
  2. does it survive at every split point, or only the one I happened to pick?
  3. does the SAME model trained on 2025 (a season it cannot have peeked
     at) reproduce it on 2026?
  4. block bootstrap over days on the flat-1u profit.
"""
from __future__ import annotations
import sys
from pathlib import Path
from collections import defaultdict
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

L2 = 10.0


def prep(rows):
    for r in rows:
        a, b = C.implied(r["nrfi_odds"]), C.implied(r["yrfi_odds"])
        r["mkt"] = a / (a + b)
        r["be"] = a
        r["pnl"] = C.payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0
    return rows


def slice_stats(rows, key, q):
    s = sorted(rows, key=lambda r: -r[key])
    k = max(15, int(len(s) * q))
    sub = s[:k]
    hit = np.mean([r["y_nrfi"] for r in sub])
    need = np.mean([r["be"] for r in sub])
    u = sum(r["pnl"] for r in sub)
    return k, hit, need, u


def main():
    r26 = prep([r for r in C.attach_production(C.load_2026())
                if r["nrfi_odds"] is not None and r["yrfi_odds"] is not None])
    r25 = C.attach_production(C.load_2025())
    X26, names = C.design(r26)
    X25, _ = C.design(r25)
    y26 = np.array([r["y_nrfi"] for r in r26], float)
    y25 = np.array([r["y_nrfi"] for r in r25], float)
    dates = sorted({r["date"] for r in r26})

    print("=" * 100)
    print("  EXP5  is the exp4-B direct LR real?  n=1128 real-priced 2026 games")
    print("=" * 100)

    # ---- 3. the clean one first: trained on 2025, scored on all of 2026 ----
    print("\n  --- 3. SAME MODEL, TRAINED ON 2025 ONLY, SCORED ON ALL PRICED 2026 ---")
    m25 = C.fit_lr(X25, y25, L2)
    for r, s in zip(r26, C.predict_lr(m25, X26)):
        r["lr25"] = float(s)
    print(f"      AUC on 2026 = {C.auc(y26, [r['lr25'] for r in r26]):.4f}")
    for q in (0.05, 0.10, 0.20, 0.30):
        k, h, nd, u = slice_stats(r26, "lr25", q)
        print(f"      top {int(q*100):>3}%  n={k:>4}  hit={100*h:>5.2f}%  need={100*nd:>5.2f}%  "
              f"gap={100*(h-nd):>+6.2f}pp  {u:>+7.2f}u")

    # ---- 1. walk-forward, refit weekly -------------------------------------
    print("\n  --- 1. WALK-FORWARD on 2026 (expanding window, refit every 7 dates) ---")
    idx_by_date = defaultdict(list)
    for i, r in enumerate(r26):
        idx_by_date[r["date"]].append(i)
    MIN = 300
    model = None
    scored = []
    for di, d in enumerate(dates):
        prior = [i for i, r in enumerate(r26) if r["date"] < d]
        if len(prior) < MIN:
            continue
        if model is None or di % 7 == 0:
            model = C.fit_lr(X26[prior], y26[prior], L2)
        for i in idx_by_date[d]:
            r26[i]["wf"] = float(C.predict_lr(model, X26[i:i + 1])[0])
            scored.append(r26[i])
    print(f"      scored {len(scored)} games from {scored[0]['date']} onward")
    print(f"      AUC = {C.auc([r['y_nrfi'] for r in scored], [r['wf'] for r in scored]):.4f}")
    for q in (0.05, 0.10, 0.20, 0.30, 0.50):
        k, h, nd, u = slice_stats(scored, "wf", q)
        print(f"      top {int(q*100):>3}%  n={k:>4}  hit={100*h:>5.2f}%  need={100*nd:>5.2f}%  "
              f"gap={100*(h-nd):>+6.2f}pp  {u:>+7.2f}u")

    # ---- 2. every split point ---------------------------------------------
    print("\n  --- 2. EVERY CHRONOLOGICAL SPLIT (not just the one that looked good) ---")
    print(f"      {'cut date':<12}{'n_tr':>6}{'n_te':>6}{'top20 hit':>11}{'gap pp':>9}{'u':>9}"
          f"{'top10 hit':>11}{'gap pp':>9}{'u':>9}")
    for frac in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        cut = dates[int(len(dates) * frac)]
        tr = [i for i, r in enumerate(r26) if r["date"] < cut]
        te = [r for r in r26 if r["date"] >= cut]
        if len(tr) < 250 or len(te) < 150:
            continue
        m = C.fit_lr(X26[tr], y26[tr], L2)
        Xte = C.design(te)[0]
        for r, s in zip(te, C.predict_lr(m, Xte)):
            r["sp"] = float(s)
        k2, h2, n2, u2 = slice_stats(te, "sp", 0.20)
        k1, h1, n1, u1 = slice_stats(te, "sp", 0.10)
        print(f"      {cut:<12}{len(tr):>6}{len(te):>6}{100*h2:>10.2f}%{100*(h2-n2):>+9.2f}"
              f"{u2:>+9.2f}{100*h1:>10.2f}%{100*(h1-n1):>+9.2f}{u1:>+9.2f}")

    # ---- 4. bootstrap the walk-forward profit ------------------------------
    print("\n  --- 4. BLOCK BOOTSTRAP over days on the walk-forward top-20% flat profit ---")

    def stat(rows):
        if len(rows) < 40:
            return float("nan")
        s = sorted(rows, key=lambda r: -r["wf"])[:max(10, int(len(rows) * 0.20))]
        return float(np.mean([r["pnl"] for r in s]))

    m, lo, hi = C.block_bootstrap_days(scored, stat, B=3000, seed=11)
    print(f"      ROI per 1u = {100*m:+.2f}%   95% CI [{100*lo:+.2f}%, {100*hi:+.2f}%]"
          f"   -> {'CI spans 0' if lo < 0 < hi else 'CI excludes 0'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

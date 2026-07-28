#!/usr/bin/env python3
"""
r1_specialist_refute.py -- independent re-derivation of the
"specialist NRFI model fit only on the top-35% p_nrfi region" proposal.

ANALYSIS ONLY.  Reads data, writes nothing outside stdout.

Three comparators on the test-season region:
  BASE   production calibrated p_nrfi  (NOTE: leaky -- prod LR was fit on
         2024+2025+2026YTD and the CIR calibrator on 2025+2026, so it has
         seen both test seasons.  This INFLATES the baseline.)
  GEN    same architecture/features/L2, refit on ALL games of the train
         season  (honest comparator: identical leakage profile to SPEC)
  SPEC   same architecture/features/L2, refit on the REGION ONLY of the
         train season  (the proposal)

The GEN-vs-SPEC contrast is the one that actually tests the proposal's
mechanism ("spend capacity only inside the traded region"), because both
sides see exactly the same seasons.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402

L2 = 10.0
TOPQ = 0.35


def region_mask(rows, q=TOPQ, thresh=None):
    p = np.asarray([r["prod"] for r in rows], float)
    if thresh is None:
        thresh = float(np.quantile(p, 1.0 - q))
    return p >= thresh, thresh


def fit_and_score(train_rows, test_rows, tag):
    Xtr, names = C.design(train_rows)
    Xte, _ = C.design(test_rows)
    ytr = np.asarray([r["y_nrfi"] for r in train_rows], float)
    yte = np.asarray([r["y_nrfi"] for r in test_rows], float)

    m_tr, th_tr = region_mask(train_rows)
    m_te, th_te = region_mask(test_rows)

    gen = C.fit_lr(Xtr, ytr, l2=L2)
    spec = C.fit_lr(Xtr[m_tr], ytr[m_tr], l2=L2)

    s_gen = C.predict_lr(gen, Xte)
    s_spec = C.predict_lr(spec, Xte)
    s_base = np.asarray([r["prod"] for r in test_rows], float)

    print(f"\n=== {tag} ===")
    print(f"  train n={len(train_rows)}  region n={int(m_tr.sum())} "
          f"(thresh p_nrfi>={th_tr:.4f})")
    print(f"  test  n={len(test_rows)}   region n={int(m_te.sum())} "
          f"(thresh p_nrfi>={th_te:.4f})  region NRFI rate="
          f"{yte[m_te].mean():.4f}")

    out = {}
    for lbl, s in (("BASE(prod)", s_base), ("GEN(all)", s_gen),
                   ("SPEC(region)", s_spec)):
        a_all = C.auc(yte, s)
        a_reg = C.auc(yte[m_te], s[m_te])
        print(f"  {lbl:14s} AUC full={a_all:.4f}  AUC in-region={a_reg:.4f}")
        out[lbl] = s
    # deltas with day-block bootstrap
    idx = np.where(m_te)[0]
    reg_rows = [test_rows[i] for i in idx]
    for a_lbl, b_lbl in (("SPEC(region)", "BASE(prod)"),
                         ("SPEC(region)", "GEN(all)")):
        sa, sb = out[a_lbl][idx], out[b_lbl][idx]
        pos = {id(test_rows[i]): (yte[i], sa[k], sb[k])
               for k, i in enumerate(idx)}

        def stat(rs, _pos=pos):
            yy = np.array([_pos[id(r)][0] for r in rs])
            aa = np.array([_pos[id(r)][1] for r in rs])
            bb = np.array([_pos[id(r)][2] for r in rs])
            if yy.min() == yy.max():
                return float("nan")
            return C.auc(yy, aa) - C.auc(yy, bb)

        mean, lo, hi = C.block_bootstrap_days(reg_rows, stat, B=2000, seed=7)
        point = C.auc(yte[idx], sa) - C.auc(yte[idx], sb)
        print(f"  delta AUC in-region  {a_lbl} - {b_lbl}: "
              f"{point:+.4f}   boot mean {mean:+.4f}  95% CI "
              f"[{lo:+.4f}, {hi:+.4f}]")
    return spec, gen, m_te


def money(test_rows, mask, score, tag, shade=0.0):
    """NRFI bets on real captured prices, ranked by `score`, top-k slices."""
    idx = [i for i in np.where(mask)[0]
           if test_rows[i]["nrfi_odds"] is not None]
    if not idx:
        print(f"  {tag}: no priced rows")
        return
    s = np.asarray([score[i] for i in idx])
    order = np.argsort(-s)
    print(f"\n  --- {tag} (priced region n={len(idx)}, "
          f"price shade {shade:.0f}c) ---")
    print(f"  {'slice':>10} {'n':>5} {'hit%':>7} {'need%':>7} "
          f"{'edge pp':>8} {'units':>8}  {'95% CI units':>22}")
    for frac in (1.0, 0.75, 0.5, 0.35, 0.25, 0.15, 0.10, 0.05):
        k = max(1, int(round(len(idx) * frac)))
        sel = [idx[j] for j in order[:k]]
        rows = [test_rows[i] for i in sel]
        odds = []
        for r in rows:
            o = r["nrfi_odds"]
            o = o - shade if o > 0 else o - shade  # shade = worse for bettor
            odds.append(o)
        wins = np.array([r["y_nrfi"] for r in rows], float)
        pays = np.array([C.payout(o) for o in odds])
        need = np.mean([C.implied(o) for o in odds])
        u = float(np.sum(np.where(wins > 0, pays, -1.0)))
        pack = {id(r): (wins[j], pays[j]) for j, r in enumerate(rows)}

        def stat(rs, _p=pack):
            if not rs:
                return float("nan")
            return float(np.sum([_p[id(r)][1] if _p[id(r)][0] > 0 else -1.0
                                 for r in rs])) / len(rs) * len(rows)

        _, lo, hi = C.block_bootstrap_days(rows, stat, B=1500, seed=11)
        print(f"  {frac*100:>9.0f}% {k:>5} {100*wins.mean():>7.2f} "
              f"{100*need:>7.2f} {100*(wins.mean()-need):>8.2f} "
              f"{u:>8.2f}  [{lo:>+8.2f}, {hi:>+8.2f}]")


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())

    spec_25, gen_25, m26 = fit_and_score(r25, r26, "TRAIN 2025 -> TEST 2026")
    fit_and_score(r26, r25, "TRAIN 2026 -> TEST 2025")

    # within-2026 forward time split
    days = sorted({r["date"] for r in r26})
    cut = days[int(len(days) * 0.6)]
    tr = [r for r in r26 if r["date"] < cut]
    te = [r for r in r26 if r["date"] >= cut]
    print(f"\n[time split at {cut}: train {len(tr)}, test {len(te)}]")
    fit_and_score(tr, te, "TRAIN 2026-early -> TEST 2026-late")

    # ---- money, strictly out-of-sample: 2025-fit specialist on 2026 ----
    X26, _ = C.design(r26)
    s_spec = C.predict_lr(spec_25, X26)
    s_gen = C.predict_lr(gen_25, X26)
    s_base = np.asarray([r["prod"] for r in r26], float)
    print("\n================ MONEY (2026 real DK prices) ================")
    money(r26, m26, s_spec, "SPEC fit on 2025, ranked in region")
    money(r26, m26, s_base, "BASE production p_nrfi, ranked in region")
    money(r26, m26, s_gen, "GEN fit on 2025 all games, ranked in region")
    print("\n--- 10 cents worse pricing ---")
    money(r26, m26, s_spec, "SPEC fit on 2025", shade=10.0)


if __name__ == "__main__":
    main()

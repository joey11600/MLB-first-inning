#!/usr/bin/env python3
"""park_null.py -- the selection-aware null for the park-shrinkage refit.

park_shrinkage_refit.py tried four variants of fi_park_nrfi_rate and the best
one on the deciding 2026 split was "flat" (every park = the training league
mean): AUC 0.5387 -> 0.5434 and Q1-YRFI hit 56.7% -> 59.0%.  Four candidates
were searched and the winner reported, so per the feature_test_methodology
memory that number CANNOT be read against a fixed threshold -- the search has
to be priced in.  Ignoring selection once moved a p of 0.003 to 0.227 on this
model.

THE NULL.  A park map carries signal only if the REAL park->rate assignment
beats a SHUFFLED one.  Each trial permutes which rate belongs to which park
(same 30 values, same spread, same shrinkage -- only the pairing is destroyed),
refits both half models, and scores 2026.  That distribution is what "a park
feature that means nothing" looks like on this data.

Then the same question for the winner: how often does a placebo beat the real
map by as much as `flat` did?

SPEED.  gather() is the expensive step, so it runs ONCE.  Each trial overwrites
the single park column in the already-built matrices and refits only the LR.
Row order is recovered exactly by gathering with a probe map that gives every
park a unique sentinel value, so no row-filter logic is duplicated.

Writes nothing.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import two_stage_model as TSM           # noqa: E402
from lr_baseline import LogReg          # noqa: E402

BT = ROOT / "data" / "backtests"
F2024 = BT / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv"
F2025 = BT / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv"
F2026 = ROOT / "data" / "picks_2026.csv"
L2_PER_SAMPLE = 0.5
PARK_FEAT = "fi_park_nrfi_rate"
TRIALS = 200

T1F = TSM.T1_PHASE_E3_VSHAND_FI_FEATURES
B1F = TSM.B1_PHASE_E3_VSHAND_FI_FEATURES
IT1 = T1F.index(PARK_FEAT)
IB1 = B1F.index(PARK_FEAT)


def park_counts(paths):
    n, k = {}, {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = (r.get("actual_side") or r.get("actual_result") or "").upper()
                if a not in ("NRFI", "YRFI"):
                    continue
                h = r.get("home", "") or r.get("home_team", "")
                if not h:
                    continue
                n[h] = n.get(h, 0) + 1
                k[h] = k.get(h, 0) + (a == "NRFI")
    return n, k


def shrunk(n, k, K):
    base = sum(k.values()) / sum(n.values())
    return {p: (k[p] + K * base) / (n[p] + K) for p in n}, base


def auc(p, y):
    o = np.argsort(p)
    r = np.empty(len(p), float); r[o] = np.arange(1, len(p) + 1)
    n1 = y.sum(); n0 = len(y) - n1
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def q1_yrfi(p, y):
    o = np.argsort(p); q = o[:len(p) // 5]
    return float((y[q] == 0).mean())


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def main():
    try:
        import mlb_first_inning_predictor as P
        umpc, umpr = P._load_ump_data()
    except Exception:
        umpc, umpr = {}, {}

    trs, te = [F2024, F2025], F2026
    n, k = park_counts(trs)
    parks = sorted(n)
    probe = {p: (i + 1) / 1000.0 for i, p in enumerate(parks)}

    def gather(path):
        # ump held flat for the same reason as park_shrinkage_refit._flatten_ump
        # (flat umpire file since 2026-08-29 + std+1e-9 standardisation).
        blk = TSM.gather(path, probe, phase_e3=True, phase_e3_vshand=True,
                         fi_xwoba=True, ump_cache=umpc, ump_rates_data=umpr)
        for key, feats in (("X_t1", TSM.T1_PHASE_E3_VSHAND_FI_FEATURES),
                           ("X_b1", TSM.B1_PHASE_E3_VSHAND_FI_FEATURES)):
            if "home_plate_ump_nrfi_rate" in feats:
                blk[key][:, feats.index("home_plate_ump_nrfi_rate")] = TSM.LEAGUE_NRFI_RATE
        return blk

    print("gathering once (probe map recovers each row's park exactly)...")
    trb = [gather(p) for p in trs]
    teb = gather(te)
    Xt = np.vstack([b["X_t1"] for b in trb]); yt = np.concatenate([b["y_t1"] for b in trb])
    Xb = np.vstack([b["X_b1"] for b in trb]); yb = np.concatenate([b["y_b1"] for b in trb])
    Tt, Tb = teb["X_t1"].copy(), teb["X_b1"].copy()
    y = ((teb["y_t1"] == 0) & (teb["y_b1"] == 0)).astype(float)

    def decode(col):
        idx = np.rint(col * 1000).astype(int) - 1
        return np.clip(idx, 0, len(parks) - 1)

    tr_i_t1, tr_i_b1 = decode(Xt[:, IT1]), decode(Xb[:, IB1])
    te_i_t1, te_i_b1 = decode(Tt[:, IT1]), decode(Tb[:, IB1])
    print(f"  train n={len(yt)}  test n={len(y)}  parks={len(parks)}")
    print(f"  park recovery check: train {len(np.unique(tr_i_t1))} distinct, "
          f"test {len(np.unique(te_i_t1))} distinct\n")

    l2 = L2_PER_SAMPLE * len(yt)

    def run(vals):
        """vals: array indexed by park index -> rate. Returns (auc, q1, brier)."""
        Xt2, Xb2 = Xt.copy(), Xb.copy(); Tt2, Tb2 = Tt.copy(), Tb.copy()
        Xt2[:, IT1] = vals[tr_i_t1]; Xb2[:, IB1] = vals[tr_i_b1]
        Tt2[:, IT1] = vals[te_i_t1]; Tb2[:, IB1] = vals[te_i_b1]
        m1 = LogReg.fit(Xt2, yt, T1F, l2=l2)
        m2 = LogReg.fit(Xb2, yb, B1F, l2=l2)
        p = (1 - m1.predict_proba(Tt2)) * (1 - m2.predict_proba(Tb2))
        return auc(p, y), q1_yrfi(p, y), brier(p, y)

    real_map, base = shrunk(n, k, 50.0)
    real = np.array([real_map[p] for p in parks])
    flat = np.full(len(parks), base)

    a_real, q_real, b_real = run(real)
    a_flat, q_flat, b_flat = run(flat)
    print(f"  REAL K=50 : AUC {a_real:.4f}  Q1-YRFI {q_real*100:.1f}%  Brier {b_real:.5f}")
    print(f"  FLAT      : AUC {a_flat:.4f}  Q1-YRFI {q_flat*100:.1f}%  Brier {b_flat:.5f}")
    print(f"  observed gain from FLAT: AUC {a_flat-a_real:+.4f}  "
          f"Q1-YRFI {(q_flat-q_real)*100:+.1f}pp\n")

    print(f"running {TRIALS} shuffled-park trials (same 30 rates, pairing destroyed)...")
    rng = np.random.default_rng(20260829)
    A = np.empty(TRIALS); Q = np.empty(TRIALS); B = np.empty(TRIALS)
    for i in range(TRIALS):
        A[i], Q[i], B[i] = run(rng.permutation(real))
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{TRIALS}")

    print("\n" + "=" * 74)
    print("  Q1. Does the REAL park map beat a SHUFFLED one? (is there signal at all)")
    print("=" * 74)
    for nm, obs, arr, hi_good in (("AUC", a_real, A, True),
                                  ("Q1-YRFI hit", q_real, Q, True),
                                  ("Brier", b_real, B, False)):
        pct = float((arr < obs).mean()) * 100 if hi_good else float((arr > obs).mean()) * 100
        print(f"  {nm:<14} real={obs:.5f}   shuffled mean={arr.mean():.5f} "
              f"sd={arr.std():.5f}   real beats {pct:.0f}% of placebos")
    print("  (a real feature should beat ~95%+ of placebos; ~50% means no signal)")

    print("\n" + "=" * 74)
    print("  Q2. Is FLAT's gain bigger than what a placebo swap achieves by luck?")
    print("=" * 74)
    dA = A - a_real; dQ = Q - q_real
    print(f"  observed FLAT gain : AUC {a_flat-a_real:+.4f}   Q1-YRFI {(q_flat-q_real)*100:+.1f}pp")
    print(f"  placebo gains      : AUC mean {dA.mean():+.4f} sd {dA.std():.4f}, "
          f"95th pct {np.percentile(dA,95):+.4f}")
    print(f"                       Q1  mean {dQ.mean()*100:+.1f}pp sd {dQ.std()*100:.1f}pp, "
          f"95th pct {np.percentile(dQ,95)*100:+.1f}pp")
    pA = float((dA >= (a_flat - a_real)).mean())
    pQ = float((dQ >= (q_flat - q_real)).mean())
    print(f"\n  p(placebo AUC gain  >= FLAT's) = {pA:.3f}")
    print(f"  p(placebo Q1  gain  >= FLAT's) = {pQ:.3f}")
    print("  A pure relabelling of parks cannot add information, so any placebo")
    print("  'gain' is the size of this metric's luck. p >= 0.05 = FLAT is noise.")


if __name__ == "__main__":
    main()

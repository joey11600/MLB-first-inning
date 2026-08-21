#!/usr/bin/env python3
"""
Walk-forward test of the lowest-risk repair: refit the CALIBRATOR more often.

WHY THIS IS THE CANDIDATE.  baserate_control.py showed the level error, not
the weights, dominates the money: giving the SHIPPED model an oracle level
correction swings flat ROI by 10-15 percentage points in either direction
(2025->2024 goes -12.0% -> +2.9%; 2024->2025 goes +3.7% -> -6.0%).  Raising
L2 survives that control but adds only a few points on top.

A calibrator is a MONOTONE map.  Refitting it:
  - cannot change the model's ranking (AUC is invariant), so it cannot make
    the discrimination problem worse
  - moves the LEVEL, which is the thing that is actually broken
  - needs no weight refit, so it does not disturb the frozen feature
    standardisation that a park-file change would (see the 2026-08-20 note)
  - already has tooling and a workflow_dispatch action in this repo

The shipped calibrator (data/calibration_v2.json) was fit 2026-07-28 on
train_n=3913.  This asks: if it had instead been refit on a TRAILING WINDOW
of recently graded games, re-fit every N days, what would 2026 have looked
like?

Strictly walk-forward: for each slate date the calibrator is fit only on
games graded STRICTLY BEFORE that date.  Nothing from the day itself or
later is visible.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibration import CIRCalibrator  # noqa: E402
from harness import auc, brier, logloss  # noqa: E402
from money import GATE_NRFI, flat_pnl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260820)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    # Use the LIVE ledger: these are the exact probabilities production
    # produced and bet on, so a improvement here is an improvement to the
    # real thing rather than to a reconstruction of it.
    d = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["fi_total_runs"].notna()].dropna(subset=["lambda_lr_total", "nrfi_prob"]).copy()
    d["y"] = (d["fi_total_runs"] > 0).astype(int)
    d = d.sort_values("date").reset_index(drop=True)
    # nrfi_prob_raw is only populated from 2026-07-29 (308 rows) -- too short a
    # history to walk forward on.  It is exactly exp(-lambda_lr_total): checked
    # on the 308 rows carrying both, correlation 1.0 and max abs diff 3.8e-05.
    # lambda_lr_total goes back to 2026-04-27, so reconstruct raw from that.
    d["raw_nrfi"] = np.exp(-d["lambda_lr_total"].values)
    print(f"live 2026 rows with a reconstructable raw + shipped probability: n={len(d)}  "
          f"({d.date.min().date()} .. {d.date.max().date()})")
    print("  raw = exp(-lambda_lr_total) (pre-calibrator), shipped = nrfi_prob (post)\n")

    y = d["y"].values
    raw_nrfi = d["raw_nrfi"].values
    ship_nrfi = d["nrfi_prob"].values
    nrfi_actual = (d["y"] == 0).astype(int).values

    rows = []
    for W in [30, 45, 60, 90, 120, 9999]:
        for REFIT in [1, 7, 14]:
            out = np.full(len(d), np.nan)
            last_fit_day = None
            cal = None
            for i, (dt, r) in enumerate(zip(d["date"], raw_nrfi)):
                need = (cal is None or last_fit_day is None
                        or (dt - last_fit_day).days >= REFIT)
                if need:
                    lo = dt - pd.Timedelta(days=W)
                    m = (d["date"] < dt) & (d["date"] >= lo)
                    if m.sum() >= 200:
                        cal = CIRCalibrator.fit(list(raw_nrfi[m.values]),
                                                list(nrfi_actual[m.values]), n_bins=20)
                        last_fit_day = dt
                if cal is not None:
                    out[i] = cal.predict(float(r))
            ok = ~np.isnan(out)
            if ok.sum() < 300:
                continue
            yy, pp, ss = y[ok], 1 - out[ok], 1 - ship_nrfi[ok]
            f_new = (1 - pp) < GATE_NRFI
            f_old = (1 - ss) < GATE_NRFI
            rows.append(dict(
                W=("all" if W == 9999 else W), refit=REFIT, scored=int(ok.sum()),
                ll_new=logloss(yy, pp), ll_old=logloss(yy, ss),
                bias_new=pp.mean() - yy.mean(), bias_old=ss.mean() - yy.mean(),
                n_new=int(f_new.sum()), hit_new=(yy[f_new].mean() if f_new.sum() else np.nan),
                roi_new=(flat_pnl(yy[f_new] == 1) / f_new.sum() * 100 if f_new.sum() else np.nan),
                n_old=int(f_old.sum()), hit_old=(yy[f_old].mean() if f_old.sum() else np.nan),
                roi_old=(flat_pnl(yy[f_old] == 1) / f_old.sum() * 100 if f_old.sum() else np.nan),
            ))
    t = pd.DataFrame(rows)
    print("=" * 108)
    print("WALK-FORWARD CALIBRATOR REFIT on the live 2026 ledger")
    print("  W = trailing window (days) the calibrator is fit on; refit = how often it is re-fit")
    print(f"  {'W':>5} {'refit':>6} {'scored':>7} | {'logloss':>17} | {'level bias':>17} | "
          f"{'bets':>11} {'hit':>13} {'flat ROI':>15}")
    for _, r in t.iterrows():
        print(f"  {str(r.W):>5} {r.refit:>6}d {r.scored:>7} | "
              f"{r.ll_new:.5f} v {r.ll_old:.5f} | "
              f"{r.bias_new:+.4f} v {r.bias_old:+.4f} | "
              f"{r.n_new:>4} v {r.n_old:>4} | {r.hit_new:.3f} v {r.hit_old:.3f} | "
              f"{r.roi_new:+6.1f}% v {r.roi_old:+6.1f}%")
    print("  (left value = walk-forward refit, right value = what actually shipped)")

    # best window by logloss, then day-bootstrap its ROI difference
    best = t.loc[t.ll_new.idxmin()]
    print("\n" + "=" * 108)
    print(f"DAY-LEVEL BOOTSTRAP for the best-logloss setting (W={best.W}, refit={best.refit}d)")
    W = 9999 if best.W == "all" else int(best.W)
    REFIT = int(best.refit)
    out = np.full(len(d), np.nan); cal = None; last = None
    for i, (dt, r) in enumerate(zip(d["date"], raw_nrfi)):
        if cal is None or last is None or (dt - last).days >= REFIT:
            lo = dt - pd.Timedelta(days=W)
            m = (d["date"] < dt) & (d["date"] >= lo)
            if m.sum() >= 200:
                cal = CIRCalibrator.fit(list(raw_nrfi[m.values]),
                                        list(nrfi_actual[m.values]), n_bins=20)
                last = dt
        if cal is not None:
            out[i] = cal.predict(float(r))
    ok = ~np.isnan(out)
    sub = d[ok].reset_index(drop=True)
    yy, pp, ss = y[ok], 1 - out[ok], 1 - ship_nrfi[ok]
    days = sub["date"].dt.normalize().values
    uniq = np.unique(days)
    diffs = []
    for _ in range(args.boot):
        pick = rng.choice(len(uniq), len(uniq), replace=True)
        idx = np.concatenate([np.where(days == uniq[k])[0] for k in pick])
        fn = (1 - pp[idx]) < GATE_NRFI
        fo = (1 - ss[idx]) < GATE_NRFI
        rn = flat_pnl(yy[idx][fn] == 1) / max(fn.sum(), 1) * 100
        ro = flat_pnl(yy[idx][fo] == 1) / max(fo.sum(), 1) * 100
        diffs.append(rn - ro)
    diffs = np.array(diffs)
    print(f"  ROI difference {diffs.mean():+.2f}pp  90% CI "
          f"[{np.percentile(diffs,5):+.2f},{np.percentile(diffs,95):+.2f}]  "
          f"P(better)={(diffs>0).mean():.0%}")
    # A calibrator is monotone, so refitting one CANNOT change ranking, and any
    # apparent AUC gain here is an artifact of the pooled series -- the ledger's
    # nrfi_prob was written by SEVERAL calibrator vintages (the CIR swap landed
    # 2026-07-28).  Checked: after the swap, spearman(raw, shipped) = 1.0000 and
    # the two AUCs agree exactly (0.4926); before it, spearman is 0.9649 because
    # the column mixes vintages.  So report ranking as unchanged and do not
    # credit this repair with any of it -- the gain is LEVEL, and only level.
    print(f"  ranking: raw {auc(yy, 1 - raw_nrfi[ok]):.4f} | shipped {auc(yy,ss):.4f} | "
          f"refit {auc(yy,pp):.4f}   <- a monotone map cannot move this; "
          f"the repair is level-only")

    print("\n" + "=" * 108)
    print("BY MONTH -- the point is August, where the shipped level went stale")
    sub["_new"] = pp; sub["_old"] = ss
    for m, g in sub.groupby(sub.date.dt.to_period("M")):
        fn = (1 - g._new) < GATE_NRFI
        fo = (1 - g._old) < GATE_NRFI
        s = f"  {m}  bias {g._new.mean()-g.y.mean():+.3f} v {g._old.mean()-g.y.mean():+.3f}"
        if fn.sum() and fo.sum():
            s += (f" | bets {int(fn.sum()):>3} v {int(fo.sum()):>3}"
                  f" | hit {g.y[fn].mean():.3f} v {g.y[fo].mean():.3f}"
                  f" | ROI {flat_pnl(g.y[fn]==1)/fn.sum()*100:+6.1f}%"
                  f" v {flat_pnl(g.y[fo]==1)/fo.sum()*100:+6.1f}%")
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

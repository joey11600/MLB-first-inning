#!/usr/bin/env python3
"""
tools/edge_floor/placebo_and_2025.py -- ANALYSIS ONLY.

Three things the sweep cannot tell you:

 1. CONCENTRATION.  How much of floor 0.04's +22.77u comes from how few bets?
 2. PLACEBO / SEARCH MULTIPLICITY.  Shuffle the win/loss labels among the
    gated bets (edges and prices held fixed) and re-run the same 6-floor
    sweep.  How often does pure noise hand you a best-floor as good as the
    one we found?  That is the honest p-value for "we searched 6 thresholds".
 3. 2025 REPLICATION.  The 2025 backtest has NO prices, so this is a
    HIT-RATE test, not a profit test.  Under a constant assumed price an
    edge floor is arithmetically a p_yrfi threshold, so the question is:
    inside the gated population, does hit rate rise with p_yrfi in 2025 the
    way the 2026 sweep says it should?
"""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs, select  # noqa: E402
from tools.edge_floor.crux import add_edge, apply_floor, simulate, summary, FLOORS  # noqa: E402
from tools.sliding_window_eval import gather_features, load_parks  # noqa: E402

BT2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"


def main():
    rows, _ = load_season()
    wf = walk_forward_probs(rows)
    gate = P._LR_STRONG_YRFI_P
    bets = add_edge(select(rows, wf, side="YRFI", gate=gate, fill=None))
    bets.sort(key=lambda b: b["date"])
    base = simulate(bets)
    inc = base["staked"]
    b0 = summary(base)

    # ---------------- 1. concentration --------------------------------
    print("=" * 96)
    print("  1.  HOW CONCENTRATED IS THE 0.04 RESULT?")
    print("=" * 96)
    rem = sorted([b for b in inc if b["edge"] < 0.04], key=lambda b: b["pnl"])
    tot = sum(b["pnl"] for b in rem)
    print(f"  floor 0.04 removes {len(rem)} of the incumbent's {b0['n']} staked bets.")
    print(f"  their combined incumbent P&L: {tot:+.2f}u")
    print(f"  {'date':<12}{'edge':>7}{'p_yrfi':>8}{'odds':>7}{'stake':>8}{'res':>6}{'P&L':>9}")
    for b in rem:
        print(f"  {b['date']:<12}{b['edge']:>7.3f}{b['p']:>8.3f}{b['odds']:>7.0f}"
              f"{b['stake']:>7.2f}u{'WIN' if b['win'] else 'LOSS':>6}{b['pnl']:>+8.2f}u")
    worst3 = sum(b["pnl"] for b in rem[:3])
    print(f"\n  the 3 biggest losers among them are {worst3:+.2f}u of that {tot:+.2f}u "
          f"({100*worst3/tot:.0f}%).")
    print(f"  removing 3 specific games is not a strategy; it is a description of")
    print(f"  which games happened to lose.")
    exp_w = sum(b["p"] for b in rem)
    act_w = sum(1 for b in rem if b["win"])
    print(f"\n  those {len(rem)} bets: model expected {exp_w:.1f} wins, got {act_w}. "
          f"If they had hit at\n  their own modelled rate the floor's advantage would "
          f"largely vanish -- see the placebo below.")

    # ---------------- 2. placebo --------------------------------------
    print()
    print("=" * 96)
    print("  2.  PLACEBO -- shuffle win/loss among the gated bets, re-run the same sweep")
    print("=" * 96)
    print("  Edges, prices and dates are held FIXED; only which bets won is permuted,")
    print("  keeping the overall win count.  Any floor advantage that survives is")
    print("  by construction luck.  4000 permutations.")
    pos = [b for b in bets if b["edge"] > 0]
    wins = [b["win"] for b in pos]
    rng = random.Random(11)
    obs = {}
    for f in FLOORS[1:]:
        obs[f] = summary(simulate(apply_floor(bets, f)))["profit"] - b0["profit"]
    obs_best = max(obs.values())

    # flat-1u version (Kelly resim x 4000 x 6 is too slow and path-dependent)
    def flat(bb):
        return sum(payout(b["odds"]) if b["win"] else -1.0 for b in bb)
    obs_flat = {f: flat(apply_floor(pos, f)) - flat(pos) for f in FLOORS[1:]}
    obs_flat_best = max(obs_flat.values())

    hits_any = 0
    hits_per = defaultdict(int)
    N = 4000
    for _ in range(N):
        rng.shuffle(wins)
        for b, w in zip(pos, wins):
            b["win"] = w
        d = {f: flat(apply_floor(pos, f)) - flat(pos) for f in FLOORS[1:]}
        if max(d.values()) >= obs_flat_best:
            hits_any += 1
        for f in FLOORS[1:]:
            if d[f] >= obs_flat[f]:
                hits_per[f] += 1
    # restore
    for b, w in zip(pos, [x["win"] for x in pos]):
        pass
    print(f"\n  observed flat-1u advantage by floor (vs incumbent):")
    for f in FLOORS[1:]:
        print(f"    floor {f:.2f}:  {obs_flat[f]:+6.2f}u   "
              f"single-threshold p = {hits_per[f]/N:.3f}")
    print(f"\n  BEST-OF-6 observed: {obs_flat_best:+.2f}u (at floor 0.04)")
    print(f"  P(noise produces a best-of-6 at least this good) = {hits_any/N:.3f}")
    print(f"  -> with 6 thresholds searched, a result this size is {'NOT ' if hits_any/N>0.05 else ''}"
          f"distinguishable from luck.")

    # ---------------- 3. 2025 replication -----------------------------
    print()
    print("=" * 96)
    print("  3.  2025 REPLICATION -- HIT RATE ONLY (that season has no prices)")
    print("=" * 96)
    parks = load_parks()
    r25 = gather_features(BT2025, parks)
    t1m, b1m = rc.load_lr_models()
    X1 = np.asarray([x["t1"] for x in r25], float)
    X2 = np.asarray([x["b1"] for x in r25], float)
    raw25 = rc.lr_predict_two_stage(t1m, b1m, X1, X2)
    # calibrate 2025 on 2025 itself would leak; fit on 2026 raw/labels instead,
    # which is a genuine cross-season application.
    cal = CIRCalibrator.fit([r["raw"] for r in rows], [r["y_nrfi"] for r in rows],
                            20, ["2026->2025"])
    p25 = [cal.predict(float(v)) for v in raw25]
    lam_by = {}
    with open(BT2025, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lam_by[(r["date"], r["game_pk"])] = r
    gated = []
    for x, p in zip(r25, p25):
        r = lam_by.get((x["date"], x["game_pk"]))
        if r is None:
            continue
        def fn(k):
            try:
                return float(r.get(k) or "")
            except (TypeError, ValueError):
                return None
        lam = fn("lambda_total")
        fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, fn("wx_temp_c"),
                                       fn("wx_wind_kmh"), bool(fn("wx_is_dome") or 0))
        if lam is not None and lam < fl:
            continue
        if p >= gate:
            continue
        gated.append({"p": 1.0 - p, "win": x["y_nrfi"] == 0})
    print(f"  2025 games loaded {len(r25)};  the live rule would BET {len(gated)}")
    print(f"  (calibrator fit on 2026 and applied to 2025 -- a true cross-season use)")
    print()
    print("  Under a CONSTANT assumed price, edge floor f == p_yrfi >= implied + f.")
    print("  Using -125 (implied 0.556) as the stand-in price:")
    print(f"  {'floor f':>9}{'p_yrfi >=':>11}{'bets':>7}{'W':>5}{'L':>5}{'hit%':>8}"
          f"{'vs no floor':>13}")
    imp = implied(-125.0)
    base_hit = 100 * sum(1 for b in gated if b["win"]) / len(gated) if gated else float("nan")
    for f in FLOORS:
        thr = imp + f
        g = [b for b in gated if b["p"] >= thr]
        if not g:
            print(f"  {f:>9.2f}{thr:>11.3f}{0:>7}")
            continue
        w = sum(1 for b in g if b["win"])
        print(f"  {f:>9.2f}{thr:>11.3f}{len(g):>7}{w:>5}{len(g)-w:>5}"
              f"{100*w/len(g):>7.1f}%{100*w/len(g)-base_hit:>+12.1f}")
    print()
    print("  This is NOT a profit test.  It only asks whether the HIT-RATE lift the")
    print("  2026 sweep promises shows up again on an independent season.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Step 2 -- does the effect replicate on 2025, a season nobody searched?

WHAT CAN AND CANNOT BE TESTED HERE
    2025 has NO prices.  So this is NOT a profit test.  It tests the only
    half of `edge` that a price-free season can test: does a HIGHER MODEL
    PROBABILITY predict a HIGHER HIT RATE inside the bet population?

    Under a FIXED assumed price, edge = p_model - implied(const), so
    ranking by edge IS ranking by model probability.  Every "edge >= x"
    row below is therefore literally a model-probability cut.  The price
    half of the effect is untestable on 2025 and is handled in run_2026.

CALIBRATOR
    data/calibration_v2.json was fit on 2025+2026 -- in-sample for 2025.
    Two out-of-sample alternatives are run:
      (a) DAY-BLOCKED CV within 2025: every DATE gets a fold id; a game's
          probability comes from a calibrator fit on the other folds.
          Blocking by day, not by game, keeps a slate from training on
          itself.  Chosen as primary because 2024 is a suspect season.
      (b) FIT ON 2024 ONLY: fully held out, but inherits whatever is
          wrong with 2024.  Run as a robustness check.

    Note the calibrator is MONOTONE, so it cannot change the ORDER of
    games -- only which ones clear the 0.40 gate.  The shape result below
    is calibrator-invariant by construction; the bet COUNT is not.

LR WEIGHTS ARE IN-SAMPLE ON 2025 (fit 2026-05-26 on 2024+2025+2026YTD).
    There is no clean way around this without a full retrain, which the
    task forbids.  It biases the test OPTIMISTIC: if the shape fails to
    replicate even with the model having seen these games, that is
    strong evidence against.  If it replicates, it is weak evidence for.
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402
from tools.edge_floor.common import (load_2026, load_backtest, payout,  # noqa: E402
                                     implied, passes_lambda_floor)

ROOT = Path(__file__).resolve().parent.parent.parent
ASSUMED = -125.0       # a typical DK YRFI price; see 2026 distribution below
FOLDS = 5


def hdr(s):
    print("\n" + "=" * 92)
    print("  " + s)
    print("=" * 92)


def cv_probs(rows, folds=FOLDS):
    """Day-blocked cross-validated calibrator probabilities."""
    dates = sorted({r["date"] for r in rows})
    fold_of = {d: i % folds for i, d in enumerate(dates)}
    out = [None] * len(rows)
    for k in range(folds):
        tr = [r for r in rows if fold_of[r["date"]] != k]
        cal = CIRCalibrator.fit([r["raw"] for r in tr], [r["y_nrfi"] for r in tr],
                                20, [f"cv{k}"])
        for i, r in enumerate(rows):
            if fold_of[r["date"]] == k:
                out[i] = cal.predict(r["raw"])
    return out


def edge_table(bets, floors, label):
    print(f"\n  {label}")
    print(f"    {'edge>=':>8}{'bets':>7}{'hit%':>8}{'need%':>8}{'ROI%':>8}"
          f"{'P&L u':>10}")
    prev = None
    mono = True
    for f in floors:
        s = [b for b in bets if b["edge"] >= f]
        if len(s) < 5:
            print(f"    {f:>8.2f}{len(s):>7}    (n<5, not reported)")
            continue
        w = sum(b["win"] for b in s)
        pl = sum(payout(b["odds"]) if b["win"] else -1.0 for b in s)
        hit = 100 * w / len(s)
        if prev is not None and hit < prev - 1e-9:
            mono = False
        prev = hit
        print(f"    {f:>8.2f}{len(s):>7}{hit:>8.1f}"
              f"{100*st.mean([b['imp'] for b in s]):>8.1f}"
              f"{100*pl/len(s):>8.1f}{pl:>+10.2f}")
    print(f"    -> hit-rate monotone increasing? {'YES' if mono else 'NO'}")
    return mono


def mk(rows, probs, odds_fn):
    out = []
    for r, p in zip(rows, probs):
        if p is None:
            continue
        o = odds_fn(r)
        out.append({"date": r["date"], "p_y": 1 - p, "odds": o,
                    "imp": implied(o), "edge": (1 - p) - implied(o),
                    "win": r["yrfi_hit"], "p_nrfi": p})
    return out


def main():
    r25, _ = load_backtest(2025)
    r26, _ = load_2026()
    gate = P._LR_STRONG_YRFI_P

    hdr("PRICE ASSUMPTION -- what do real 2026 YRFI prices actually look like?")
    real = [r["yrfi_odds"] for r in r26 if r["yrfi_odds"] is not None]
    real.sort()
    print(f"  n={len(real)}  median {real[len(real)//2]:+.0f}  "
          f"p10 {real[int(.10*len(real))]:+.0f}  p90 {real[int(.90*len(real))]:+.0f}")
    print(f"  mean implied prob {100*st.mean([implied(o) for o in real]):.1f}%")
    print(f"  -> 2025 tests use a FIXED assumed price of {ASSUMED:+.0f} "
          f"(implied {100*implied(ASSUMED):.1f}%).")
    print("     With a fixed price, an 'edge >= x' cut IS a model-probability")
    print("     cut.  This is a HIT-RATE test, not a profit test.")

    hdr("CALIBRATOR VARIANTS ON 2025")
    prod = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    p_prod = [prod.predict(r["raw"]) for r in r25]
    p_cv = cv_probs(r25)
    r24, _ = load_backtest(2024)
    cal24 = CIRCalibrator.fit([r["raw"] for r in r24], [r["y_nrfi"] for r in r24],
                              20, ["2024"])
    p_24 = [cal24.predict(r["raw"]) for r in r25]
    print(f"  production (2025+2026, IN-SAMPLE) mean p_nrfi : {st.mean(p_prod):.4f}")
    print(f"  day-blocked CV within 2025 (OOS)  mean p_nrfi : {st.mean(p_cv):.4f}")
    print(f"  fit on 2024 only (OOS)            mean p_nrfi : {st.mean(p_24):.4f}")

    variants = [("day-blocked CV within 2025 (PRIMARY, out-of-sample)", p_cv),
                ("fit on 2024 only (out-of-sample, suspect season)", p_24),
                ("production calibrator (IN-SAMPLE, for reference)", p_prod)]

    hdr("A. LIVE-RULE POPULATION IN 2025  (STRONG gate p<0.40 + lambda floor)")
    for name, pv in variants:
        sel = [(r, p) for r, p in zip(r25, pv)
               if p < gate and passes_lambda_floor(r, "lam_recon")]
        print(f"  {name:<52}: {len(sel):>4} bets of {len(r25)}")
    print("\n  For contrast, the same rule on 2026 fires on 125 of 1138 priced games.")
    print("  2025 is a DIFFERENT REGIME for this model: mean raw p_nrfi 0.529 vs")
    print("  0.467 in 2026, so far fewer 2025 games are pushed under the gate.")

    floors = [0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.16]
    for name, pv in variants:
        sel = [(r, p) for r, p in zip(r25, pv)
               if p < gate and passes_lambda_floor(r, "lam_recon")]
        if len(sel) < 20:
            print(f"\n  {name}: only {len(sel)} bets -- SAMPLE TOO SMALL "
                  "to test a threshold sweep.")
            continue
        bets = mk([x[0] for x in sel], [x[1] for x in sel], lambda r: ASSUMED)
        edge_table(bets, floors, name)

    hdr("B. THE OPERATOR'S ACTUAL POPULATION IN 2025  (NO gate, NO lambda floor)")
    print("  The 495-bet table came from ALL priced 2026 games with edge>=0, not")
    print("  from the games the system bets.  Replicating THAT is the apples-to-")
    print("  apples test of the finding as it was presented.\n")
    for name, pv in variants:
        bets = mk(r25, pv, lambda r: ASSUMED)
        bets = [b for b in bets if b["edge"] >= 0.0]
        edge_table(bets, floors, f"2025, all games, edge>=0 baseline -- {name}")

    hdr("C. SIDE-BY-SIDE SHAPE: 2026 (searched) vs 2025 (not searched)")
    prod26 = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    p26 = [prod26.predict(r["raw"]) for r in r26]
    b26 = mk([r for r in r26 if r["yrfi_odds"] is not None],
             [p for r, p in zip(r26, p26) if r["yrfi_odds"] is not None],
             lambda r: r["yrfi_odds"])
    b26 = [b for b in b26 if b["edge"] >= 0.0]
    edge_table(b26, floors, "2026 all-games (REAL prices) -- the source of the hypothesis")
    b25 = [b for b in mk(r25, p_cv, lambda r: ASSUMED) if b["edge"] >= 0.0]
    edge_table(b25, floors, "2025 all-games (assumed price, day-blocked CV) -- the test")

    hdr("D. CONTROL -- is the 2026 climb reproducible with the PRICE frozen?")
    print("  Re-run 2026 with every real price replaced by the same fixed")
    print(f"  {ASSUMED:+.0f}.  If the climb survives, it is model-driven and 2025 can")
    print("  test it.  If it collapses, the effect lives in the PRICES and no")
    print("  price-free season can confirm it.\n")
    b26f = mk([r for r in r26 if r["yrfi_odds"] is not None],
              [p for r, p in zip(r26, p26) if r["yrfi_odds"] is not None],
              lambda r: ASSUMED)
    b26f = [b for b in b26f if b["edge"] >= 0.0]
    edge_table(b26f, floors, "2026 all-games, PRICES FROZEN at the assumed value")
    return 0


if __name__ == "__main__":
    sys.exit(main())

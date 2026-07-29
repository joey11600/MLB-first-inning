#!/usr/bin/env python3
"""Step 5 -- (a) where the Kelly gain actually comes from, (b) temporal
stability, (c) whether the hit-rate lift replicates on 2025.

(a) If cutting 16 bets that lost 0.9u flat changes the final Kelly bank
    by +21u, the gain is not "avoided losses" -- it is the compounding
    PATH.  Removing an early loser makes every later stake bigger.  That
    is an artefact of one particular sequence, not an edge.

(c) 2025 has NO odds.  Under a CONSTANT assumed price the edge floor is
    algebraically identical to a tighter probability gate, so 2025 can
    only test the MODEL half of the claim.  Said plainly: this is not a
    profit test.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator  # noqa: E402
from tools.edge_floor.base import (  # noqa: E402
    GATE, build_bets, insample_probs, walk_forward_probs, load_season,
    summary, implied, payout)
from tools.edge_floor.step4_kelly_marginal import (  # noqa: E402
    kelly_sim, apply_floor, START)


def amplification(bets, label):
    print(f"\n  {label}")
    base_bank, _, base_sk = kelly_sim(bets)
    print(f"  {'floor':>7}{'cut':>5}{'cut flat P&L':>14}{'cut Kelly P&L':>15}"
          f"{'cut stake u':>13}{'% of turnover':>15}{'d final bank':>14}{'amplif.':>9}")
    tot_stake = sum(b["stake"] for b in base_sk)
    for f in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12):
        kept = apply_floor(bets, f)
        cut = [b for b in bets if b["edge"] < f]
        if not cut or not kept:
            continue
        cut_flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in cut)
        # what those same bets actually staked/returned in the BASE run
        cutset = {(b["date"], b["rid"]) for b in cut}
        cutsk = [b for b in base_sk if (b["date"], b["rid"]) in cutset]
        cut_kelly = sum(b["stake"] * payout(b["odds"]) if b["win"] else -b["stake"]
                        for b in cutsk)
        cut_stake = sum(b["stake"] for b in cutsk)
        bank, _, _ = kelly_sim(kept)
        d = bank - base_bank
        amp = d / cut_kelly if abs(cut_kelly) > 1e-9 else float("nan")
        print(f"  {f:>+7.2f}{len(cut):>5}{cut_flat:>+13.2f}u{cut_kelly:>+14.2f}u"
              f"{cut_stake:>12.2f}u{100*cut_stake/tot_stake:>14.1f}%"
              f"{d:>+13.2f}u{amp:>+9.1f}x")
    print("  amplif. = change in final bank / the P&L the cut bets actually made.")
    print("  A number far from ~-1x means the effect is the COMPOUNDING PATH,")
    print("  not the merit of the bets that were removed.")


def month_split(bets, label):
    print(f"\n  {label} -- month split (flat 1u)")
    bym = defaultdict(list)
    for b in bets:
        bym[b["date"][:7]].append(b)
    print(f"  {'month':<9}{'no floor':>22}{'floor +0.04':>24}{'delta':>10}")
    print(f"  {'':<9}{'n':>6}{'hit%':>7}{'flat':>9}{'n':>8}{'hit%':>7}{'flat':>9}{'':>10}")
    for m in sorted(bym):
        g = bym[m]
        h = apply_floor(g, 0.04)
        s0, s1 = summary(g), summary(h)
        d = s1["pl"] - s0["pl"]
        print(f"  {m:<9}{s0['n']:>6}{s0['hit']:>7.1f}{s0['pl']:>+8.2f}u"
              f"{s1['n']:>8}{s1['hit']:>7.1f}{s1['pl']:>+8.2f}u{d:>+9.2f}u")


# ---------------------------------------------------------------------------
# 2025 replication
# ---------------------------------------------------------------------------

def load_2025():
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    path = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    out = []
    for r in rows:
        side = (r.get("actual_side") or "").upper()
        if side not in ("NRFI", "YRFI"):
            continue
        fp = fi_park.get(r.get("home", ""), rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue

        def fnum(k):
            v = (r.get(k) or "").strip()
            try:
                return float(v)
            except ValueError:
                return None
        out.append({"date": r["date"], "t1": tv, "b1": bv,
                    "yrfi_hit": side == "YRFI",
                    "lambda": fnum("lambda_total"),
                    "wx_temp": fnum("wx_temp_c"), "wx_wind": fnum("wx_wind_kmh"),
                    "wx_dome": bool(fnum("wx_is_dome") or 0)})
    Xt = np.asarray([x["t1"] for x in out], dtype=float)
    Xb = np.asarray([x["b1"] for x in out], dtype=float)
    raw = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    for x, p in zip(out, raw):
        x["raw"] = float(p)
    return out


def main():
    rows, _ = load_season()
    ins, cal = insample_probs(rows)
    wf = walk_forward_probs(rows)
    L_ins = build_bets(rows, ins)
    L_wf = build_bets(rows, wf)

    print("=" * 112)
    print("  (a) WHERE DOES THE KELLY GAIN COME FROM?")
    print("=" * 112)
    amplification(L_ins, "in-sample calibrator, live rule, real prices (n=118)")
    amplification(L_wf, "walk-forward calibrator, live rule, real prices (n=96)")

    print("\n" + "=" * 112)
    print("  (b) TEMPORAL STABILITY of the +0.04 floor")
    print("=" * 112)
    month_split(L_ins, "in-sample")
    month_split(L_wf, "walk-forward")

    print("\n" + "=" * 112)
    print("  (c) 2025 BACKTEST -- HIT-RATE ONLY.  NOT A PROFIT TEST (no odds).")
    print("=" * 112)
    b25 = load_2025()
    print(f"  2025 graded games built: {len(b25)}")
    p25 = [cal.predict(x["raw"]) for x in b25]
    # live rule on 2025
    sel = []
    for x, p in zip(b25, p25):
        fl = P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR, x["wx_temp"],
                                       x["wx_wind"], x["wx_dome"])
        if x["lambda"] is not None and x["lambda"] < fl:
            continue
        if p >= GATE:
            continue
        sel.append({"p_yrfi": 1 - p, "win": x["yrfi_hit"], "date": x["date"]})
    print(f"  live-rule (gate {GATE} + lambda floor) firings in 2025: {len(sel)}")

    print("\n  Under a CONSTANT assumed price the edge floor reduces to a "
          "probability floor:")
    print("      edge >= f   <=>   p_yrfi >= implied(price) + f")
    for fill in (-110, -125, -140):
        imp = implied(fill)
        print(f"\n  assumed price {fill}  (implied {imp:.3f})")
        print(f"  {'floor':>7}{'p_yrfi >=':>11}{'2025 bets':>11}{'2025 hit%':>11}"
              f"{'2026 bets':>11}{'2026 hit%':>11}")
        for f in (0.00, 0.02, 0.04, 0.06, 0.08, 0.10):
            thr = imp + f
            g25 = [b for b in sel if b["p_yrfi"] >= thr]
            g26 = [b for b in L_ins if b["p_yrfi"] >= thr]
            h25 = 100 * sum(b["win"] for b in g25) / len(g25) if g25 else float("nan")
            s26 = summary(g26)
            print(f"  {f:>+7.2f}{thr:>11.3f}{len(g25):>11}{h25:>11.1f}"
                  f"{s26['n']:>11}{s26['hit']:>11.1f}")
        print(f"    (2025 base rate over all {len(sel)} live-rule firings: "
              f"{100*sum(b['win'] for b in sel)/len(sel):.1f}%)")

    print("\n  Pure model-term check on 2025: does a HIGHER p_yrfi predict a")
    print("  higher YRFI rate inside the bet set? (quintiles)")
    ss = sorted(sel, key=lambda b: b["p_yrfi"])
    for i in range(5):
        g = ss[i * len(ss) // 5:(i + 1) * len(ss) // 5]
        if not g:
            continue
        print(f"    p_yrfi {g[0]['p_yrfi']:.3f}..{g[-1]['p_yrfi']:.3f}  "
              f"n={len(g):>4}  hit {100*sum(b['win'] for b in g)/len(g):>5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

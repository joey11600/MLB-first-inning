#!/usr/bin/env python3
"""
tools/nrfi_dd_refute_060b.py -- part 2 of the attack on
"wf p_nrfi >= 0.60 AND lambda_lr_total <= 0.52".

Three tests the original find did not run:
  A. STRUCTURE  -- is the lambda ceiling an independent second dimension,
                   or a monotone restatement of the same score?
  B. CONTROL    -- on the same 10 slates the rule fired on, how did
                   BLINDLY betting NRFI do? If the window itself was hot,
                   the rule selected a date, not a game.
  C. TRANSFER   -- does the same selection geometry beat 58.3% on 2024
                   and 2025 (hit-rate only; those files carry no odds)?

Analysis only. Writes nothing.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import CIRCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402

BT = ROOT / "data" / "backtests"
CEIL = P._LR_LAMBDA_NRFI_CEILING
RULE_DATES = None  # filled in below


def binom_sf(k, n, p):
    return sum(math.comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k, n + 1))


def load_bt(path):
    """Backtest rows -> raw p_nrfi under TODAY's LR weights + outcome."""
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_side") or r.get("graded_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                tot = r.get("fi_total_runs")
                try:
                    a = "NRFI" if float(tot) == 0 else "YRFI"
                except (TypeError, ValueError):
                    continue
            fp = fi_park.get(r.get("home", r.get("home_team", "")), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            out.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                        "y": 1 if a == "NRFI" else 0})
    Xt = np.asarray([x["t1"] for x in out], float)
    Xb = np.asarray([x["b1"] for x in out], float)
    raw = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    for x, p in zip(out, raw):
        x["raw"] = float(p)
        x["lam"] = -math.log(max(1e-9, float(p)))
    out.sort(key=lambda x: x["date"])
    return out


def main():
    rows, _ = load_season()
    wf = walk_forward_probs(rows)

    # rebuild the 14
    bets = []
    for r, p in zip(rows, wf):
        if p is None or p < 0.60:
            continue
        if r["lambda"] is not None and r["lambda"] > CEIL:
            continue
        if r["nrfi_odds"] is None:
            continue
        bets.append({"date": r["date"], "p": p, "raw": r["raw"],
                     "lam": r["lambda"], "odds": r["nrfi_odds"],
                     "win": not r["yrfi_hit"]})
    dates = sorted({b["date"] for b in bets})
    need = st.mean([implied(b["odds"]) for b in bets])

    # ---------------- A. STRUCTURE ---------------------------------------
    print("=" * 100)
    print("  A. IS THE LAMBDA CEILING A SECOND DIMENSION?")
    print("=" * 100)
    print("  In mlb_first_inning_predictor.py:2534  lambda_lr_total = -ln(p_t1_no) + -ln(p_b1_no)")
    print("  and p_nrfi_raw = p_t1_no * p_b1_no, so  lambda_lr_total == -ln(p_nrfi_raw) EXACTLY.")
    print(f"  => 'lambda <= {CEIL}' is identically 'raw p_nrfi >= {math.exp(-CEIL):.4f}'.")
    print("  The CIR calibrator is monotone non-decreasing, so 'calibrated p >= 0.60' is")
    print("  ALSO a floor on the same raw score. The rule has ONE free parameter, not two.")
    print()
    print(f"  raw p_nrfi on the 14 bets: min={min(b['raw'] for b in bets):.4f} "
          f"max={max(b['raw'] for b in bets):.4f}")
    # which constraint binds
    thr = math.exp(-CEIL)
    only_gate = sum(1 for r, p in zip(rows, wf)
                    if p is not None and p >= 0.60 and r["lambda"] is not None and r["lambda"] > CEIL)
    print(f"  games passing gate 0.60 but REJECTED by the lambda ceiling: {only_gate}")
    print(f"  games passing lambda ceiling but rejected by gate 0.60: "
          f"{sum(1 for r,p in zip(rows,wf) if p is not None and p<0.60 and r['lambda'] is not None and r['lambda']<=CEIL)}")

    # ---------------- B. DATE CONTROL ------------------------------------
    print("\n" + "=" * 100)
    print("  B. CONTROL -- blind NRFI on the SAME 10 slates the rule fired on")
    print("=" * 100)
    ctrl = [r for r in rows if r["date"] in set(dates) and r["nrfi_odds"] is not None]
    cw = sum(1 for r in ctrl if not r["yrfi_hit"])
    cpl = sum(payout(r["nrfi_odds"]) if not r["yrfi_hit"] else -1.0 for r in ctrl)
    cneed = st.mean([implied(r["nrfi_odds"]) for r in ctrl])
    print(f"  every priced game on {len(dates)} slates: n={len(ctrl)}  NRFI hit={100*cw/len(ctrl):.1f}%"
          f"  break-even={100*cneed:.1f}%  flat P&L={cpl:+.2f}u  ROI={100*cpl/len(ctrl):+.1f}%")
    print(f"  the rule's 14:                        n=14  NRFI hit=78.6%  break-even={100*need:.1f}%")
    print("\n  monthly NRFI base rate, all graded 2026 games:")
    bym = defaultdict(lambda: [0, 0])
    for r in rows:
        m = bym[r["date"][:7]]
        m[0] += 1
        m[1] += 0 if r["yrfi_hit"] else 1
    for m in sorted(bym):
        n_, w_ = bym[m]
        print(f"    {m}  games={n_:>4}  NRFI={100*w_/n_:>5.1f}%")
    win = [r for r in rows if "2026-04-30" <= r["date"] <= "2026-05-14"]
    wl = sum(1 for r in win if not r["yrfi_hit"])
    print(f"    2026-04-30..05-14 (the rule's window)  games={len(win)}  NRFI={100*wl/len(win):.1f}%")
    rest = [r for r in rows if not ("2026-04-30" <= r["date"] <= "2026-05-14")]
    rl = sum(1 for r in rest if not r["yrfi_hit"])
    print(f"    everything else                        games={len(rest)}  NRFI={100*rl/len(rest):.1f}%")

    # ---------------- C. SEASON TRANSFER ---------------------------------
    print("\n" + "=" * 100)
    print("  C. TRANSFER -- same geometry on 2024 / 2025 (hit-rate only, no odds in those files)")
    print("=" * 100)
    sel_rate_2026 = len([r for r, p in zip(rows, wf)
                         if p is not None and p >= 0.60
                         and r["lambda"] is not None and r["lambda"] <= CEIL]) / \
        max(1, sum(1 for p in wf if p is not None))

    seasons = {
        "2024": load_bt(BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"),
        "2025": load_bt(BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"),
    }
    seasons["2026"] = [{"date": r["date"], "raw": r["raw"], "lam": r["lambda"],
                        "y": 0 if r["yrfi_hit"] else 1} for r in rows]

    print(f"  break-even to clear (mean real DK price on the 14 bets) = {100*need:.1f}%")
    print(f"\n  --- C1. absolute raw floor (== the lambda ceiling), no calibrator at all ---")
    print(f"  {'season':<8}{'games':>7}{'sel':>6}{'sel%':>7}{'NRFI hit%':>11}{'base%':>8}{'lift':>8}{'binom p':>9}")
    for cut, lab in ((math.exp(-CEIL), f"raw >= {math.exp(-CEIL):.4f} (lam<=0.52)"),
                     (math.exp(-0.45), f"raw >= {math.exp(-0.45):.4f} (lam<=0.45)")):
        print(f"  [{lab}]")
        for s in ("2024", "2025", "2026"):
            d = seasons[s]
            g = [x for x in d if x["raw"] >= cut]
            base = st.mean([x["y"] for x in d])
            if not g:
                print(f"  {s:<8}{len(d):>7}{0:>6}{0.0:>7.1f}{'--':>11}{100*base:>8.1f}")
                continue
            hit = st.mean([x["y"] for x in g])
            pb = binom_sf(sum(x["y"] for x in g), len(g), need)
            print(f"  {s:<8}{len(d):>7}{len(g):>6}{100*len(g)/len(d):>7.1f}{100*hit:>11.1f}"
                  f"{100*base:>8.1f}{100*(hit-base):>+8.1f}{pb:>9.4f}")

    print(f"\n  --- C2. matched SELECTION RATE ({100*sel_rate_2026:.2f}% of games, the rule's own rate) ---")
    print(f"  {'season':<8}{'games':>7}{'sel':>6}{'cut(raw)':>10}{'NRFI hit%':>11}{'base%':>8}{'binom p':>9}")
    for s in ("2024", "2025", "2026"):
        d = seasons[s]
        k = max(1, int(round(sel_rate_2026 * len(d))))
        g = sorted(d, key=lambda x: -x["raw"])[:k]
        hit = st.mean([x["y"] for x in g])
        base = st.mean([x["y"] for x in d])
        pb = binom_sf(sum(x["y"] for x in g), len(g), need)
        print(f"  {s:<8}{len(d):>7}{k:>6}{g[-1]['raw']:>10.4f}{100*hit:>11.1f}{100*base:>8.1f}{pb:>9.4f}")

    print("\n  --- C3. top-decile monotonicity of the raw score (does more selectivity help?) ---")
    print(f"  {'season':<8}" + "".join(f"{q:>9}" for q in
          ("top1%", "top2%", "top5%", "top10%", "top20%", "all")))
    for s in ("2024", "2025", "2026"):
        d = sorted(seasons[s], key=lambda x: -x["raw"])
        cells = []
        for q in (0.01, 0.02, 0.05, 0.10, 0.20, 1.0):
            k = max(1, int(round(q * len(d))))
            cells.append(100 * st.mean([x["y"] for x in d[:k]]))
        print(f"  {s:<8}" + "".join(f"{c:>9.1f}" for c in cells))
    print(f"\n  (all figures are NRFI hit %. To be profitable at the rule's real prices a cell")
    print(f"   must clear {100*need:.1f}%.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

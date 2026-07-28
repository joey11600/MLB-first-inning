#!/usr/bin/env python3
"""tools/nrfi_dd_refute_price_lambda.py -- ADVERSARIAL test of the proposed rule

    RULE: bet NRFI when lambda_lr_total <= 0.80 AND market_nrfi_odds >= -105

Claim under test: 130 bets, 51.5% hit vs 49.6% break-even, on real captured
DraftKings prices in 2026.

This script re-derives everything from the raw CSVs (does NOT trust the claim),
then attacks it on three axes: overfitting / pricing / sample size.
Read-only.  Nothing here writes to production files.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def shade(o, cents=10):
    """Make an American price `cents` worse for the bettor."""
    # work in decimal-ish space: convert to profit-per-1u, subtract cents/100
    prof = payout(o) - cents / 100.0
    if prof <= 0.01:
        return -10000.0
    return prof * 100.0 if prof >= 1.0 else -100.0 / prof


def load(paths, outcol, homecol, odds=False):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get(outcol) or "").upper() not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                rows.append({
                    "date": r.get("date", ""),
                    "t1": tv, "b1": bv,
                    "y": 1 if (r.get(outcol) or "").upper() == "NRFI" else 0,
                    "odds": fnum(r.get("market_nrfi_odds")) if odds else None,
                    "yodds": fnum(r.get("market_yrfi_odds")) if odds else None,
                })
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def roi_of(sub, oddskey="odds", cents=0):
    if not sub:
        return float("nan"), 0.0
    pl = 0.0
    for r in sub:
        o = r[oddskey] if cents == 0 else shade(r[oddskey], cents)
        pl += payout(o) if r["y"] else -1.0
    return pl / len(sub), pl


def day_boot(sub, iters=20000, seed=7, cents=0):
    byday = defaultdict(list)
    for r in sub:
        o = r["odds"] if cents == 0 else shade(r["odds"], cents)
        byday[r["date"]].append(payout(o) if r["y"] else -1.0)
    days = list(byday.values())
    if len(days) < 3:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    k = len(days)
    out = np.empty(iters)
    for i in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for j in idx for x in days[j]]
        out[i] = sum(v) / len(v)
    out.sort()
    p_le0 = float((out <= 0).mean())
    return out[int(0.025 * iters)], out[int(0.975 * iters)], p_le0


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PRICE_FLOORS = [-160, -140, -125, -115, -105, +100, +120]

LAM, PF = 0.80, -105


def hdr(t):
    print("\n" + "=" * 92)
    print("  " + t)
    print("=" * 92)


def main():
    s26 = load([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team", True)
    priced = [r for r in s26 if r["odds"] is not None]

    hdr("0. RE-DERIVE THE CLAIM FROM RAW DATA")
    print(f"  2026 graded rows with model features : {len(s26)}")
    print(f"  ... of which have a REAL captured DK NRFI price : {len(priced)}")
    rule = [r for r in priced if r["lam"] <= LAM and r["odds"] >= PF]
    n = len(rule)
    hit = sum(r["y"] for r in rule) / n
    need = st.mean([implied(r["odds"]) for r in rule])
    roi, pl = roi_of(rule)
    print(f"\n  RULE  lam<={LAM} AND nrfi_odds>={PF:+d}")
    print(f"    n={n}   hit={100*hit:.1f}%   break-even needed={100*need:.1f}%   "
          f"P/L={pl:+.2f}u   ROI={100*roi:+.1f}%")
    print(f"    claim was: 130 bets / 51.5% hit / 49.6% break-even")
    print(f"    days spanned: {len(set(r['date'] for r in rule))}")
    print(f"    margin over break-even: {100*(hit-need):+.2f}pp "
          f"({(hit-need)*n:+.1f} extra wins than break-even out of {n})")
    wins = sum(r["y"] for r in rule)
    print(f"    wins={wins} losses={n-wins}. Flipping "
          f"{math.ceil((need-0)*0+1)} single game changes hit by "
          f"{100.0/n:.2f}pp.")

    hdr("1. MARGINAL CONTROLS -- is either leg good on its own?")
    for name, sub in [
        (f"price only  (odds>={PF:+d})", [r for r in priced if r["odds"] >= PF]),
        (f"lambda only (lam<={LAM})", [r for r in priced if r["lam"] <= LAM]),
        ("all priced NRFI bets", priced),
        (f"lam<={LAM} AND odds<{PF:+d} (complement)",
         [r for r in priced if r["lam"] <= LAM and r["odds"] < PF]),
        (f"lam>{LAM} AND odds>={PF:+d} (complement)",
         [r for r in priced if r["lam"] > LAM and r["odds"] >= PF]),
    ]:
        if len(sub) < 5:
            continue
        h = sum(r["y"] for r in sub) / len(sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        ro, p = roi_of(sub)
        print(f"  {name:<42} n={len(sub):>5}  hit={100*h:>5.1f}%  "
              f"need={100*nd:>5.1f}%  ROI={100*ro:>+6.1f}%  P/L={p:>+8.2f}u")
    print("\n  -> If both marginals are negative and only the intersection is\n"
          "     positive, the 'effect' is a pure two-way interaction discovered\n"
          "     by search. That is the signature of an overfit cell.")

    hdr("2. MULTIPLE COMPARISONS -- what does the null produce over 56 cells?")
    # Null: keep prices + selection geometry fixed, resample OUTCOMES from a
    # model with ZERO edge, i.e. each game wins with prob = the book's
    # vig-free implied probability. Then re-run the whole 56-cell grid and
    # record the best cell. Repeat.
    # Devig using the two-sided price where available.
    for r in priced:
        if r["yodds"] is not None:
            tot = implied(r["odds"]) + implied(r["yodds"])
            r["pfair"] = implied(r["odds"]) / tot
        else:
            r["pfair"] = implied(r["odds"]) - 0.022  # ~half of a typical 4.4% hold
    twoside = sum(1 for r in priced if r["yodds"] is not None)
    print(f"  games with BOTH sides priced (true de-vig possible): {twoside}/{len(priced)}")

    cells = []
    for c in CAPS:
        for pf in PRICE_FLOORS:
            idx = [i for i, r in enumerate(priced)
                   if r["lam"] <= c and r["odds"] >= pf]
            if len(idx) >= 20:
                cells.append(((c, pf), np.asarray(idx)))
    print(f"  grid cells with n>=20 (the ones that could have 'won'): {len(cells)}"
          f"  of {len(CAPS)*len(PRICE_FLOORS)} searched")

    pf_arr = np.asarray([r["pfair"] for r in priced])
    win_arr = np.asarray([payout(r["odds"]) for r in priced])
    rng = np.random.default_rng(11)
    ITERS = 20000
    best_null = np.empty(ITERS)
    cell_null = np.empty(ITERS)  # null ROI for OUR cell specifically
    our_idx = np.asarray([i for i, r in enumerate(priced)
                          if r["lam"] <= LAM and r["odds"] >= PF])
    for it in range(ITERS):
        y = (rng.random(len(priced)) < pf_arr).astype(float)
        pnl = np.where(y > 0, win_arr, -1.0)
        b = -9.0
        for _, idx in cells:
            v = pnl[idx].mean()
            if v > b:
                b = v
        best_null[it] = b
        cell_null[it] = pnl[our_idx].mean()
    obs = roi
    print(f"\n  OBSERVED ROI of the winning cell: {100*obs:+.1f}%")
    print(f"  Under the null of ZERO edge (book's de-vigged probability is the truth):")
    print(f"    P(this SPECIFIC cell >= {100*obs:+.1f}%)          = "
          f"{float((cell_null >= obs).mean()):.3f}   <- uncorrected")
    print(f"    P(the BEST OF {len(cells)} cells >= {100*obs:+.1f}%)  = "
          f"{float((best_null >= obs).mean()):.3f}   <- search-corrected")
    print(f"    median best-of-grid ROI under pure noise = "
          f"{100*float(np.median(best_null)):+.1f}%")
    print(f"    90th pct best-of-grid ROI under pure noise = "
          f"{100*float(np.quantile(best_null, 0.90)):+.1f}%")
    print("\n  The brief states ~176 distinct selection rules were examined across the\n"
          "  whole investigation. Sidak correction on the uncorrected p-value:")
    p_un = float((cell_null >= obs).mean())
    for k in (56, 176):
        print(f"    k={k:>4}:  1-(1-p)^k = {1-(1-p_un)**k:.3f}")

    hdr("3. DAY-BLOCK BOOTSTRAP (bets on one slate are correlated)")
    lo, hi, pneg = day_boot(rule)
    print(f"  rule ROI 95% CI over resampled DAYS: [{100*lo:+.1f}%, {100*hi:+.1f}%]"
          f"   P(ROI<=0)={pneg:.3f}")
    print(f"  -> CI {'INCLUDES' if lo <= 0 <= hi else 'excludes'} zero.")

    hdr("4. PRICE ROBUSTNESS -- does it survive worse prices?")
    print(f"  {'shading':<22}{'ROI':>9}{'P/L':>10}{'95% CI (day block)':>28}")
    for cents in (0, 2, 5, 10):
        ro, p = roi_of(rule, cents=cents)
        l2, h2, _ = day_boot(rule, iters=6000, cents=cents)
        print(f"  {f'-{cents} cents':<22}{100*ro:>+8.1f}%{p:>+10.2f}u"
              f"   [{100*l2:>+6.1f}%,{100*h2:>+6.1f}%]")
    print("\n  Mean captured NRFI price in the rule: "
          f"{st.mean([r['odds'] for r in rule]):+.1f}")
    print("  Break-even at that mean price moves ~2.4pp per 10 cents; the whole\n"
          "  claimed margin is +1.9pp.")

    hdr("5. TIME STABILITY WITHIN 2026 (does it persist, or is it one hot patch?)")
    bymon = defaultdict(list)
    for r in rule:
        bymon[r["date"][:7]].append(r)
    print(f"  {'month':<10}{'n':>5}{'hit%':>8}{'need%':>8}{'ROI%':>9}{'P/L':>9}")
    for m in sorted(bymon):
        sub = bymon[m]
        h = sum(x["y"] for x in sub) / len(sub)
        nd = st.mean([implied(x["odds"]) for x in sub])
        ro, p = roi_of(sub)
        print(f"  {m:<10}{len(sub):>5}{100*h:>8.1f}{100*nd:>8.1f}{100*ro:>+9.1f}{p:>+9.2f}")

    hdr("6. HONEST HOLDOUT -- search the grid on the FIRST half of 2026 only,\n"
        "     then bet the winner blind on the SECOND half")
    dates = sorted(set(r["date"] for r in priced))
    cut = dates[len(dates) // 2]
    tr = [r for r in priced if r["date"] < cut]
    te = [r for r in priced if r["date"] >= cut]
    print(f"  split at {cut}:  train n={len(tr)}  test n={len(te)}")
    best = None
    for c in CAPS:
        for pf in PRICE_FLOORS:
            sub = [r for r in tr if r["lam"] <= c and r["odds"] >= pf]
            if len(sub) < 20:
                continue
            ro, _ = roi_of(sub)
            if best is None or ro > best[0]:
                best = (ro, c, pf, len(sub))
    print(f"  best cell in TRAIN half: lam<={best[1]} odds>={best[2]:+d}  "
          f"n={best[3]}  ROI={100*best[0]:+.1f}%")
    sub = [r for r in te if r["lam"] <= best[1] and r["odds"] >= best[2]]
    if sub:
        ro, p = roi_of(sub)
        h = sum(r["y"] for r in sub) / len(sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        print(f"  -> applied blind to TEST half: n={len(sub)}  hit={100*h:.1f}%  "
              f"need={100*nd:.1f}%  ROI={100*ro:+.1f}%  P/L={p:+.2f}u")
    # and the proposed rule itself on each half
    for nm, half in (("train half", tr), ("test half", te)):
        sub = [r for r in half if r["lam"] <= LAM and r["odds"] >= PF]
        if len(sub) < 5:
            continue
        ro, p = roi_of(sub)
        h = sum(r["y"] for r in sub) / len(sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        print(f"  PROPOSED RULE on {nm:<11} n={len(sub):>4}  hit={100*h:>5.1f}%  "
              f"need={100*nd:>5.1f}%  ROI={100*ro:>+6.1f}%  P/L={p:+.2f}u")

    hdr("7. OUT-OF-SAMPLE SEASONS -- the lambda leg on 2024 / 2025 (no odds there)")
    print("  The PRICE leg cannot be tested out of sample: the 2024/2025 backtests")
    print("  carry no captured odds at all. Only the lambda leg is testable.")
    print(f"  Question: does lam<={LAM} lift the NRFI hit rate at all?\n")
    print(f"  {'season':<10}{'n all':>8}{'base hit%':>11}{'n lam<=.80':>12}"
          f"{'hit%':>8}{'lift pp':>9}")
    for tag, path, oc, hc in (
        ("2024", BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv", "actual_side", "home"),
        ("2025", BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv", "actual_side", "home"),
    ):
        s = load([path], oc, hc)
        base = sum(r["y"] for r in s) / len(s)
        sub = [r for r in s if r["lam"] <= LAM]
        h = sum(r["y"] for r in sub) / len(sub)
        print(f"  {tag:<10}{len(s):>8}{100*base:>11.1f}{len(sub):>12}"
              f"{100*h:>8.1f}{100*(h-base):>+9.2f}")
        # what would it have needed at the price the rule actually gets in 2026?
        print(f"            -> needed {100*need:.1f}% to break even at 2026 rule prices; "
              f"{'CLEARS' if h > need else 'FAILS'} by {100*(h-need):+.2f}pp")

    hdr("8. IS THE PRICE LEG SELECTING GAMES THE BOOK IS SHARP ON?")
    cheap = [r for r in priced if r["odds"] >= PF]
    exp_ = [r for r in priced if r["odds"] < PF]
    for nm, sub in (("NRFI priced >= -105 (cheap NRFI)", cheap),
                    ("NRFI priced <  -105 (pricey NRFI)", exp_)):
        h = sum(r["y"] for r in sub) / len(sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        ml = st.mean([r["lam"] for r in sub])
        mp = st.mean([r["raw"] for r in sub])
        print(f"  {nm:<36} n={len(sub):>5} hit={100*h:>5.1f}% need={100*nd:>5.1f}% "
              f"model_p_nrfi={100*mp:>5.1f}% mean_lam={ml:.3f}")
    print("\n  Note the structure: 'odds >= -105' means the BOOK thinks NRFI is")
    print("  unlikely; 'lam <= 0.80' means OUR MODEL thinks NRFI is relatively")
    print("  likely. That is literally a MARKET-DISAGREEMENT filter -- already")
    print("  tested and NEGATIVE on 505 graded games (do-not-retread list).")
    # quantify the disagreement framing directly
    print(f"\n  {'disagreement bucket (model_p - fair_p)':<42}{'n':>6}{'hit%':>8}"
          f"{'need%':>8}{'ROI%':>9}")
    for lo_, hi_ in ((-1, -0.05), (-0.05, 0.0), (0.0, 0.05), (0.05, 1.0)):
        sub = [r for r in priced if lo_ <= (r["raw"] - r["pfair"]) < hi_]
        if len(sub) < 20:
            continue
        h = sum(r["y"] for r in sub) / len(sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        ro, _ = roi_of(sub)
        print(f"  {f'{lo_:+.2f} to {hi_:+.2f}':<42}{len(sub):>6}{100*h:>8.1f}"
              f"{100*nd:>8.1f}{100*ro:>+9.1f}")

    hdr("9. HOW MANY GAMES DECIDE THIS?")
    ex = (hit - need) * n
    print(f"  The rule is {ex:+.2f} wins above break-even across {n} bets.")
    print(f"  Losing {math.ceil(abs(ex))+1} of those wins to coin-flip variance makes it -EV.")
    print(f"  Std error of a {n}-bet hit rate at p=0.5: "
          f"{100*math.sqrt(0.25/n):.2f}pp -- the claimed edge is "
          f"{(hit-need)/math.sqrt(0.25/n):.2f} standard errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

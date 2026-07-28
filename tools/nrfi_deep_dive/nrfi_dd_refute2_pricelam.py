#!/usr/bin/env python3
"""tools/nrfi_dd_refute2_pricelam.py -- second, independent refutation pass on

    bet NRFI iff  lambda <= 0.80  AND  market_nrfi_odds >= -105

Read-only.  Adds what nrfi_dd_refute_pricelam.py could not do:

  A. DEFINITION AUDIT.  The 56-cell grid that discovered this rule
     (tools/nrfi_dd_pricegrid.py) uses lam = -ln(p_nrfi) recomputed from the
     LR models -- NOT the CSV column lambda_lr_total.  Both are reported.
  B. A CORRECT NULL.  refute_pricelam permutes outcomes within a day.  That
     null is invalid here: it re-assigns outcomes across prices, so a +120
     game inherits the day's ~48% NRFI rate and books a fake +5.6% ROI.  The
     right zero-edge null is y ~ Bernoulli(implied_prob) per game, which by
     construction has E[ROI]=0 for every cell at every price.
  C. Search-corrected p-values under that null, for the max cell and for the
     actual selection procedure ("largest-n positive-ROI cell").
  D. Price-point decomposition and monotonicity test of the pricing story.
  E. Out-of-sample on the lambda half: 2024 / 2025 backtests (hit rate only,
     no odds exist there).
"""
from __future__ import annotations
import csv, sys, math, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"
CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, 100, 120]


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


def load_2026():
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            rows.append({"date": r["date"], "t1": tv, "b1": bv,
                         "y": 1 if a == "NRFI" else 0,
                         "odds": fnum(r.get("market_nrfi_odds")),
                         "lam_csv": fnum(r.get("lambda_lr_total"))})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["lam_der"] = -math.log(max(1e-12, float(p)))
    return [r for r in rows if r["odds"] is not None and r["lam_csv"] is not None]


def load_bt(path, outcol, homecol):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get(outcol) or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            rows.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                         "y": 1 if a == "NRFI" else 0,
                         "lam_csv": fnum(r.get("lambda_total"))})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["lam_der"] = -math.log(max(1e-12, float(p)))
    return rows


def summ(sub):
    n = len(sub)
    if not n:
        return None
    hit = sum(r["y"] for r in sub) / n
    pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
    need = st.mean([implied(r["odds"]) for r in sub])
    return dict(n=n, hit=hit, pl=pl, roi=pl / n, need=need)


def main():
    rows = load_2026()
    n = len(rows)
    pay = np.array([payout(r["odds"]) for r in rows])
    imp = np.array([implied(r["odds"]) for r in rows])
    od = np.array([r["odds"] for r in rows])
    y = np.array([r["y"] for r in rows])
    print("=" * 94)
    print("  INDEPENDENT REFUTATION PASS 2")
    print("=" * 94)
    print(f"  priced+graded 2026 NRFI rows: {n}  (single book: DraftKings)")

    # ---------------- A. definition audit ----------------
    print("\n== A. WHICH VARIABLE IS 'lambda'? ==")
    lc = np.array([r["lam_csv"] for r in rows])
    ld = np.array([r["lam_der"] for r in rows])
    print(f"  corr(lambda_lr_total, -ln p_nrfi) = {np.corrcoef(lc, ld)[0,1]:.3f}  -- similar, not the same")
    for key, name in (("lam_der", "-ln(p_nrfi)     <- what the grid searched"),
                      ("lam_csv", "lambda_lr_total <- what the rule says")):
        sub = [r for r in rows if r[key] <= 0.80 and r["odds"] >= -105]
        s = summ(sub)
        print(f"  {name:<36} n={s['n']:>4} hit={100*s['hit']:>5.1f}% need={100*s['need']:>5.1f}%"
              f"  P/L={s['pl']:>+6.2f}u  ROI={100*s['roi']:>+5.1f}%")
    A = {i for i, r in enumerate(rows) if r["lam_der"] <= 0.80 and r["odds"] >= -105}
    B = {i for i, r in enumerate(rows) if r["lam_csv"] <= 0.80 and r["odds"] >= -105}
    print(f"  overlap: both {len(A&B)}   derived-only {len(A-B)}   CSV-only {len(B-A)}"
          f"   -> the two 'same' rules disagree on {len(A^B)} of {len(A|B)} games")

    # ---------------- B/C. correct null ----------------
    print("\n== B. A VALID ZERO-EDGE NULL ==")
    print("  Null: true P(NRFI) for every game EQUALS the DK implied prob, so no cell")
    print("  has any edge and E[ROI]=0 everywhere.  Resample outcomes 20,000x.")
    for lamkey, label in (("lam_der", "derived lam (the searched grid)"),
                          ("lam_csv", "lambda_lr_total (the stated rule)")):
        lam = np.array([r[lamkey] for r in rows])
        masks = [((lam <= c) & (od >= pf)) for c in CAPS for pf in FLOORS]
        live = [m for m in masks if m.sum() >= 20]
        rule = (lam <= 0.80) & (od >= -105)
        obs_rule = float(np.where(y[rule] == 1, pay[rule], -1.0).mean())
        obs_best = max(float(np.where(y[m] == 1, pay[m], -1.0).mean()) for m in live)
        rng = np.random.default_rng(31415)
        IT = 20000
        best = np.empty(IT); sel = np.full(IT, np.nan); rl = np.empty(IT); npos = np.empty(IT)
        for it in range(IT):
            yy = (rng.random(n) < imp)
            val = np.where(yy, pay, -1.0)
            rois = np.array([val[m].mean() for m in live])
            best[it] = rois.max()
            npos[it] = (rois > 0).sum()
            pos = [(live[j].sum(), rois[j]) for j in range(len(live)) if rois[j] > 0]
            if pos:
                sel[it] = max(pos)[1]
            rl[it] = val[rule].mean()
        ok = ~np.isnan(sel)
        print(f"\n  --- {label} ---")
        print(f"  reportable cells (n>=20): {len(live)} of {len(masks)}")
        print(f"  observed: rule ROI {100*obs_rule:+.1f}%   best cell in grid {100*obs_best:+.1f}%")
        print(f"  null max-cell ROI: median {100*np.median(best):+.1f}%  "
              f"95th {100*np.percentile(best,95):+.1f}%   P(max >= rule's {100*obs_rule:+.1f}%) = "
              f"{(best >= obs_rule).mean():.3f}")
        print(f"  null 'largest-n positive cell' ROI: median {100*np.nanmedian(sel):+.1f}%   "
              f"P(>= {100*obs_rule:+.1f}%) = {(sel[ok] >= obs_rule).mean():.3f}   "
              f"(a positive cell exists in {100*ok.mean():.0f}% of null draws)")
        print(f"  null count of positive-ROI cells: median {np.median(npos):.0f}  "
              f"(observed: {sum(1 for m in live if np.where(y[m]==1,pay[m],-1.0).mean()>0)})")
        p_un = (rl >= obs_rule).mean()
        print(f"  UNCORRECTED p for that one cell: {p_un:.3f}"
              f"   Bonferroni x56: {min(1,p_un*56):.3f}"
              f"   x176 (all rules tried): {min(1,p_un*176):.3f}")

    # ---------------- D. price story ----------------
    print("\n== D. DOES THE PRICING STORY HOLD UP? ==")
    print("  The premise is 'the book overprices NRFI, so value lives where NRFI is cheap'.")
    print("  If true, realised-minus-implied should IMPROVE monotonically as the NRFI")
    print("  price gets cheaper (more plus-money).  Every priced game, no lambda filter:")
    edges = [-999, -140, -125, -115, -106, -100, 105, 115, 9999]
    xs, gaps, ns = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        g = [r for r in rows if a <= r["odds"] < b]
        if len(g) < 25:
            continue
        s = summ(g)
        xs.append(st.mean([r["odds"] for r in g]))
        gaps.append(s["hit"] - s["need"]); ns.append(s["n"])
        print(f"    price [{a:+5},{b:+5})  n={s['n']:>4}  implied={100*s['need']:>5.1f}%"
              f"  realised={100*s['hit']:>5.1f}%  gap={100*(s['hit']-s['need']):>+5.1f}pp"
              f"  ROI={100*s['roi']:>+6.1f}%")
    w = np.array(ns, float)
    cx = np.array(xs); cg = np.array(gaps)
    cw = np.corrcoef(cx, cg)[0, 1]
    print(f"  correlation(mean price, edge gap) across buckets = {cw:+.2f}  "
          f"({'monotone as the story predicts' if cw > 0.5 else 'NO monotone trend -- the story fails'})")
    print(f"  buckets with a POSITIVE gap: {sum(1 for g in gaps if g>0)} of {len(gaps)}")

    print("\n  Inside the rule, which exact prices carry it?")
    sub = [r for r in rows if r["lam_der"] <= 0.80 and r["odds"] >= -105]
    byp = defaultdict(list)
    for r in sub:
        byp[int(r["odds"])].append(r)
    for p in sorted(byp):
        s = summ(byp[p])
        if s["n"] < 5:
            continue
        print(f"    exactly {p:+5d}: n={s['n']:>3}  hit={100*s['hit']:>5.1f}%"
              f"  need={100*s['need']:>5.1f}%  P/L={s['pl']:>+6.2f}u  ROI={100*s['roi']:>+6.1f}%")
    only105 = [r for r in sub if r["odds"] == -105]
    rest = [r for r in sub if r["odds"] != -105]
    s1, s2 = summ(only105), summ(rest)
    print(f"  -> price == -105 exactly : n={s1['n']:>3}  P/L={s1['pl']:>+6.2f}u  ROI={100*s1['roi']:>+6.1f}%")
    print(f"  -> everything else       : n={s2['n']:>3}  P/L={s2['pl']:>+6.2f}u  ROI={100*s2['roi']:>+6.1f}%")

    # ---------------- E. out-of-sample lambda half ----------------
    print("\n== E. OUT-OF-SAMPLE (the lambda half only -- no odds exist in the backtests) ==")
    print("  Does 'lam <= 0.80' lift the NRFI hit rate at all, in other seasons?")
    for path, oc, hc, tag in (
            (BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv", "actual_side", "home", "2024"),
            (BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv", "actual_side", "home", "2025")):
        if not path.exists():
            continue
        b = load_bt(path, oc, hc)
        base = sum(r["y"] for r in b) / len(b)
        for key in ("lam_der", "lam_csv"):
            g = [r for r in b if r[key] is not None and r[key] <= 0.80]
            if not g:
                continue
            h = sum(r["y"] for r in g) / len(g)
            print(f"  {tag} {key:<8} all n={len(b):>5} NRFI={100*base:>5.1f}%   "
                  f"lam<=0.80 n={len(g):>5} NRFI={100*h:>5.1f}%   lift={100*(h-base):>+5.1f}pp")
    b26 = [r for r in rows]
    base = sum(r["y"] for r in b26) / len(b26)
    g = [r for r in b26 if r["lam_der"] <= 0.80]
    print(f"  2026 lam_der  all n={len(b26):>5} NRFI={100*base:>5.1f}%   "
          f"lam<=0.80 n={len(g):>5} NRFI={100*sum(r['y'] for r in g)/len(g):>5.1f}%   "
          f"lift={100*(sum(r['y'] for r in g)/len(g)-base):>+5.1f}pp")
    print("  NOTE: a hit-rate lift is NOT profit.  With no captured prices in 2024/2025,")
    print("  the price half of this rule has ZERO out-of-sample seasons available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

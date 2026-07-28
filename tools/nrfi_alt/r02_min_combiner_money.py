#!/usr/bin/env python3
"""REFUTATION PASS 2: does the min() combiner make MONEY at the NRFI end?

AUC is not money.  To bet you need a probability, so the min score is
calibrated OUT OF SAMPLE (isotonic fit on 2025 backtest -> applied to 2026
priced picks) exactly the same way the incumbent product score is.  Then both
are run through the identical edge-threshold bet rule at REAL captured
DraftKings prices, and again at 10 cents worse.

ANALYSIS ONLY.
"""
from __future__ import annotations
import numpy as np
import alt_common as ac
from sklearn.isotonic import IsotonicRegression


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def _worsen(o, cents=10.0):
    """Robust: convert to decimal, subtract cents in american space."""
    o = float(o)
    if o >= 100:
        n = o - cents
        if n >= 100:
            return n
        # crossed into negative territory: +100 == -100
        return -(100.0 + (100.0 - n))
    # negative price: more negative = worse
    return o - cents


def scores(d):
    n_t1 = 1.0 - d["p_t1_run"]
    n_b1 = 1.0 - d["p_b1_run"]
    return {"product": n_t1 * n_b1, "min": np.minimum(n_t1, n_b1)}


def day_boot_roi(dates, units, B=4000, seed=13):
    rng = np.random.default_rng(seed)
    uniq = np.unique(dates)
    by = {u: units[dates == u] for u in uniq}
    sums = np.array([by[u].sum() for u in uniq])
    cnts = np.array([len(by[u]) for u in uniq], float)
    n = len(uniq)
    if n == 0:
        return np.nan, np.nan
    idx = rng.integers(0, n, size=(B, n))
    r = sums[idx].sum(1) / np.maximum(cnts[idx].sum(1), 1)
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def main():
    tr = ac.load("2025bt")
    te = ac.load("2026picks")
    Str, Ste = scores(tr), scores(te)

    # ---- pull real prices off the 2026 rows (same row order as te['y']) ----
    o_n, o_y = [], []
    for r in te["rows"]:
        try:
            a = float(r.get("market_nrfi_odds") or "nan")
            b = float(r.get("market_yrfi_odds") or "nan")
        except ValueError:
            a = b = np.nan
        o_n.append(a); o_y.append(b)
    o_n = np.array(o_n, float); o_y = np.array(o_y, float)
    priced = np.isfinite(o_n) & np.isfinite(o_y)
    print(f"  priced+graded 2026 universe: n={priced.sum()} of {len(o_n)}")

    # ---- out-of-sample calibration of BOTH combiners on 2025 ----
    cal = {}
    for k in ("product", "min"):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
        iso.fit(Str[k], tr["y"])
        cal[k] = iso.predict(Ste[k])

    y = te["y"]
    dates = te["dates"]
    print()
    print("  out-of-sample calibration check on 2026 (Brier, lower=better):")
    for k in ("product", "min"):
        print(f"    {k:<8} brier={np.mean((cal[k]-y)**2):.5f}  "
              f"mean p={cal[k].mean():.4f}  actual={y.mean():.4f}  "
              f"AUC={ac.auc(cal[k], y):.4f}")

    for cents in (0.0, 10.0):
        pn = np.array([_worsen(v, cents) if np.isfinite(v) else np.nan for v in o_n])
        pay = np.array([payout(v) if np.isfinite(v) else np.nan for v in pn])
        imp = np.array([implied(v) if np.isfinite(v) else np.nan for v in pn])
        units_win = np.where(y == 1, pay, -1.0)
        print()
        print("=" * 100)
        print(f"  NRFI BETS AT REAL DK PRICES  ({'closing/captured' if cents==0 else f'{cents:.0f} cents WORSE'})")
        print("=" * 100)
        print(f"  {'combiner':<10}{'edge>=':>8}{'n':>6}{'hit%':>8}{'breakeven%':>12}"
              f"{'ROI':>9}{'units':>9}{'ROI 95% CI (day block)':>28}")
        for k in ("product", "min"):
            for thr in (0.00, 0.02, 0.04, 0.06, 0.08):
                edge = cal[k] - imp
                m = priced & np.isfinite(edge) & (edge >= thr)
                n = int(m.sum())
                if n < 10:
                    print(f"  {k:<10}{thr:>8.2f}{n:>6}   (too few)")
                    continue
                u = units_win[m]
                hit = y[m].mean()
                be = np.mean(1.0 / (1.0 + pay[m]))
                roi = u.mean()
                lo, hi = day_boot_roi(dates[m], u)
                print(f"  {k:<10}{thr:>8.2f}{n:>6}{hit:>8.1%}{be:>12.1%}"
                      f"{roi:>+9.1%}{u.sum():>+9.2f}   [{lo:+.1%}, {hi:+.1%}]")

    # ---- head-to-head on identical bet counts (top-N by edge) ----
    print()
    print("=" * 100)
    print("  HEAD-TO-HEAD AT MATCHED BET COUNT (top-N NRFI by edge, real prices)")
    print("=" * 100)
    pay = np.array([payout(v) if np.isfinite(v) else np.nan for v in o_n])
    imp = np.array([implied(v) if np.isfinite(v) else np.nan for v in o_n])
    units_win = np.where(y == 1, pay, -1.0)
    pidx = np.where(priced)[0]
    print(f"  {'N':<6}{'prod hit%':>11}{'prod ROI':>10}{'min hit%':>11}{'min ROI':>10}"
          f"{'overlap':>10}")
    for N in (50, 100, 200, 300, 500):
        sel = {}
        for k in ("product", "min"):
            e = (cal[k] - imp)[pidx]
            sel[k] = pidx[np.argsort(-e, kind="mergesort")[:N]]
        ov = len(set(sel["product"].tolist()) & set(sel["min"].tolist())) / N
        print(f"  {N:<6}{y[sel['product']].mean():>11.1%}"
              f"{units_win[sel['product']].mean():>+10.1%}"
              f"{y[sel['min']].mean():>11.1%}"
              f"{units_win[sel['min']].mean():>+10.1%}{ov:>10.1%}")

    # ---- what the 5.65pp wall requires ----
    print()
    print("=" * 100)
    print("  THE WALL.  Break-even hit rate required by the captured NRFI prices.")
    print("=" * 100)
    be_all = np.mean(1.0 / (1.0 + pay[priced]))
    print(f"  all priced games: actual NRFI {y[priced].mean():.1%}  vs breakeven "
          f"{be_all:.1%}  -> gap {100*(be_all - y[priced].mean()):.2f}pp")
    for k in ("product", "min"):
        top = pidx[np.argsort(-cal[k][pidx], kind="mergesort")[:int(0.35*len(pidx))]]
        print(f"  top-35% by {k:<8} hit {y[top].mean():.1%}  breakeven "
              f"{np.mean(1.0/(1.0+pay[top])):.1%}  ROI {units_win[top].mean():+.1%}")


if __name__ == "__main__":
    main()

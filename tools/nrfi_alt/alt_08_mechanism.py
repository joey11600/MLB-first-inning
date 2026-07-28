#!/usr/bin/env python3
"""Mechanism + ceiling.

  A. Is the 2026 asymmetry a real discrimination deficit, or an artefact of the
     high regime being a NARROWER slice of p in 2026?  Re-run the gap in
     equal-WIDTH windows straddling 0.50, with a day-block CI, on 2026 OOS.
  B. What actually differs between 2025 and 2026: the p distribution itself.
  C. Ceiling: how high does the REALISED NRFI rate get at the very top of the
     model's range, and what does DK charge there?  Real prices only.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac

CUT = "2026-05-26"


def implied(a):
    a = float(a); return (-a)/(-a+100.) if a < 0 else 100./(a+100.)


def payout(a):
    a = float(a); return 100./-a if a < 0 else a/100.


def gap_ci(p, y, dates, idx_lo, idx_hi, n_boot=3000, seed=31):
    rng = np.random.default_rng(seed)
    both = np.union1d(idx_lo, idx_hi)
    dts = dates[both]; uniq = np.unique(dts)
    by = {u: both[dts == u] for u in uniq}
    slo, shi = set(idx_lo.tolist()), set(idx_hi.tolist())
    diffs = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by[u] for u in pick])
        a = ac.auc(p[[j for j in ii if j in slo]], y[[j for j in ii if j in slo]])
        b = ac.auc(p[[j for j in ii if j in shi]], y[[j for j in ii if j in shi]])
        if np.isfinite(a) and np.isfinite(b):
            diffs.append(a - b)
    diffs = np.asarray(diffs)
    return (np.percentile(diffs, 2.5), np.percentile(diffs, 97.5),
            float((diffs <= 0).mean()))


def main():
    D = {s: ac.load(s) for s in ["2025bt", "2026picks"]}

    print("=" * 112)
    print("  A. SPREAD-MATCHED GAP.  Raw 0.50 split vs equal-WIDTH windows around 0.50.")
    print("     If the gap collapses when the windows are the same width, the")
    print("     'NRFI blindness' is mostly compression of p, not a feature deficit.")
    print("=" * 112)
    print(f"  {'sample':<26}{'test':<20}{'nLo':>6}{'nHi':>6}{'AUClo':>8}{'AUChi':>8}"
          f"{'gap':>9}{'95% CI':>22}{'p':>7}")
    specs = [("2025 full", D["2025bt"], np.ones(len(D["2025bt"]["y"]), bool)),
             ("2026 full", D["2026picks"], np.ones(len(D["2026picks"]["y"]), bool)),
             ("2026 OOS (>05-26)", D["2026picks"], D["2026picks"]["dates"] > CUT)]
    for tag, d, sel in specs:
        p, y, dt = d["cal"], d["y"], d["dates"]
        for test, lo_w, hi_w in [("raw 0.50 split", (0.0, 0.50), (0.50, 1.0)),
                                 ("width-matched .06", (0.44, 0.50), (0.50, 0.56)),
                                 ("width-matched .04", (0.46, 0.50), (0.50, 0.54))]:
            il = np.where(sel & (p >= lo_w[0]) & (p < lo_w[1]))[0]
            ih = np.where(sel & (p >= hi_w[0]) & (p < hi_w[1]))[0]
            if len(il) < 60 or len(ih) < 60:
                continue
            al, ah = ac.auc(p[il], y[il]), ac.auc(p[ih], y[ih])
            lo, hi, pv = gap_ci(p, y, dt, il, ih, n_boot=1200)
            print(f"  {tag:<26}{test:<20}{len(il):>6}{len(ih):>6}{al:>8.4f}{ah:>8.4f}"
                  f"{al-ah:>+9.4f}   [{lo:+.4f},{hi:+.4f}]{pv:>7.3f}")
        print()

    print("=" * 112)
    print("  B. WHAT ACTUALLY DIFFERS BETWEEN THE SEASONS: the p distribution.")
    print("=" * 112)
    print(f"  {'season':<12}{'n':>6}{'mean p':>9}{'sd p':>8}{'frac>=.50':>11}"
          f"{'sd p | >=.50':>14}{'sd p | <.50':>13}{'ratio hi/lo':>13}")
    for s, d in D.items():
        p = d["cal"]
        hi, lo = p[p >= .5], p[p < .5]
        print(f"  {s:<12}{len(p):>6}{p.mean():>9.4f}{p.std():>8.4f}{(p>=.5).mean():>11.3f}"
              f"{hi.std():>14.4f}{lo.std():>13.4f}{hi.std()/lo.std():>13.3f}")
    d = D["2026picks"]
    for tag, m in (("2026 <=05-26", d["dates"] <= CUT), ("2026 >05-26", d["dates"] > CUT)):
        p = d["cal"][m]; hi, lo = p[p >= .5], p[p < .5]
        print(f"  {tag:<12}{len(p):>6}{p.mean():>9.4f}{p.std():>8.4f}{(p>=.5).mean():>11.3f}"
              f"{hi.std():>14.4f}{lo.std():>13.4f}{hi.std()/lo.std():>13.3f}")

    print()
    print("=" * 112)
    print("  C. CEILING.  Top slices of the model's p_nrfi, real DK prices, 2026.")
    print("     The question: does the realised NRFI rate EVER get above what DK charges?")
    print("=" * 112)
    d = D["2026picks"]
    keep, on = [], []
    for i, r in enumerate(d["rows"]):
        try:
            v = float(r.get("market_nrfi_odds") or "nan")
        except ValueError:
            continue
        if np.isfinite(v):
            keep.append(i); on.append(v)
    keep = np.asarray(keep); on = np.asarray(on)
    p, y = d["cal"][keep], d["y"][keep]
    be = np.asarray([implied(v) for v in on]); pay = np.asarray([payout(v) for v in on])
    print(f"  {'top slice':<14}{'n':>6}{'min p':>9}{'actual NRFI':>13}{'DK break-even':>15}"
          f"{'edge':>9}{'ROI':>9}{'ROI 95% CI (day boot)':>26}")
    for frac in (0.50, 0.30, 0.20, 0.10, 0.05, 0.02):
        thr = np.quantile(p, 1 - frac)
        s = np.where(p >= thr)[0]
        if len(s) < 15:
            continue
        pnl = np.where(y[s] == 1, pay[s], -1.0)
        rng = np.random.default_rng(41)
        dts = d["dates"][keep][s]; uniq = np.unique(dts)
        by = {u: np.where(dts == u)[0] for u in uniq}
        bs = []
        for _ in range(2000):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            jj = np.concatenate([by[u] for u in pick])
            bs.append(float(pnl[jj].mean()))
        print(f"  {f'top {frac:.0%}':<14}{len(s):>6}{thr:>9.4f}{y[s].mean():>13.4f}"
              f"{be[s].mean():>15.4f}{y[s].mean()-be[s].mean():>+9.4f}"
              f"{pnl.mean():>+9.4f}   [{np.percentile(bs,2.5):+.4f},{np.percentile(bs,97.5):+.4f}]")
    print()
    print("  mirror -- top slices of p_YRFI (the side the model DOES rank), same rows:")
    py = 1 - p; yy = 1 - y
    oy = []
    for i in keep:
        oy.append(float(d["rows"][i].get("market_yrfi_odds") or "nan"))
    oy = np.asarray(oy)
    m = np.isfinite(oy)
    bey = np.asarray([implied(v) if np.isfinite(v) else np.nan for v in oy])
    payy = np.asarray([payout(v) if np.isfinite(v) else np.nan for v in oy])
    for frac in (0.50, 0.30, 0.20, 0.10, 0.05, 0.02):
        thr = np.quantile(py[m], 1 - frac)
        s = np.where(m & (py >= thr))[0]
        if len(s) < 15:
            continue
        pnl = np.where(yy[s] == 1, payy[s], -1.0)
        print(f"  {f'top {frac:.0%}':<14}{len(s):>6}{thr:>9.4f}{yy[s].mean():>13.4f}"
              f"{bey[s].mean():>15.4f}{yy[s].mean()-bey[s].mean():>+9.4f}{pnl.mean():>+9.4f}")


if __name__ == "__main__":
    main()

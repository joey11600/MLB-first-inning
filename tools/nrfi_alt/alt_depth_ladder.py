#!/usr/bin/env python3
"""
tools/nrfi_alt/alt_depth_ladder.py

REFUTATION TARGET
    "Accept the asymmetry as real and simply raise the NRFI selection bar --
     bet only the deepest slices of p_nrfi."

Re-derives, from scratch, the depth ladder on REAL captured DraftKings NRFI
prices in data/picks_2026.csv, plus:
    * day-block bootstrap CIs
    * in-time-order train/test split (search on H1, confirm on H2)
    * a 2025 out-of-sample discrimination check (no odds there -> hit-rate only)
    * a 10-cent-worse-pricing stress test
    * an explicit count of how many cells the depth sweep searched
    * out-of-fold (walk-forward) calibrator, so the ladder is not read off a
      calibrator that already saw these outcomes
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402

RNG = np.random.default_rng(20260728)


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


def shade(o, cents=10.0):
    """Make the price `cents` worse for the bettor (American odds)."""
    if o < 0:
        return o - cents
    if o - cents >= 100:
        return o - cents
    # crossing the +100/-100 boundary
    return -(200.0 - (o - cents))


# ---------------------------------------------------------------- loading
def load_rows(path, season_tag):
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    with open(path, encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))
    out, skipped = [], 0
    for r in raw_rows:
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            # backtest CSVs store the outcome differently
            fi = fnum(r.get("fi_total_runs"))
            if fi is None:
                continue
            actual = "NRFI" if fi == 0 else "YRFI"
        fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
        try:
            tvec, bvec = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            skipped += 1
            continue
        out.append({
            "season": season_tag,
            "date": r["date"],
            "t1": tvec, "b1": bvec,
            "nrfi_odds": fnum(r.get("market_nrfi_odds")),
            "yrfi_odds": fnum(r.get("market_yrfi_odds")),
            "y_nrfi": 1 if actual == "NRFI" else 0,
        })
    out.sort(key=lambda x: x["date"])
    Xt = np.asarray([x["t1"] for x in out], float)
    Xb = np.asarray([x["b1"] for x in out], float)
    raw = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    for x, p in zip(out, raw):
        x["raw"] = float(p)
    return out, skipped


# ---------------------------------------------------------------- stats
def day_bootstrap(rows, key_pnl, n_boot=4000):
    """Block bootstrap over DAYS. rows carry 'date' and a pnl value."""
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(key_pnl(r))
    days = list(byday)
    if not days:
        return (float("nan"), float("nan"))
    arrs = [np.asarray(byday[d], float) for d in days]
    idx = RNG.integers(0, len(days), size=(n_boot, len(days)))
    roi = np.empty(n_boot)
    for b in range(n_boot):
        sel = [arrs[i] for i in idx[b]]
        cat = np.concatenate(sel)
        roi[b] = cat.mean() if cat.size else np.nan
    return (float(np.nanpercentile(roi, 2.5)), float(np.nanpercentile(roi, 97.5)))


def ladder(rows, side="nrfi", depths=(0.50, 0.40, 0.30, 0.20, 0.15, 0.10,
                                      0.05, 0.02), shade_cents=0.0,
           label=""):
    """rows must have 'p' (calibrated p_nrfi), odds, outcome, date."""
    okey = f"{side}_odds"
    priced = [r for r in rows if r.get(okey) is not None]
    # rank: NRFI -> highest p_nrfi first ; YRFI -> lowest p_nrfi first
    priced.sort(key=lambda r: -r["p"] if side == "nrfi" else r["p"])
    print(f"\n  {label}  (side={side.upper()}, priced n={len(priced)}, "
          f"shade={shade_cents:.0f}c)")
    print(f"    {'depth':>7}{'n':>6}{'hit%':>8}{'need%':>8}{'edge_pp':>9}"
          f"{'ROI%':>8}{'units':>9}   {'95% day-block CI on ROI%'}")
    res = []
    for d in depths:
        k = max(1, int(round(d * len(priced))))
        sub = priced[:k]
        recs = []
        for r in sub:
            o = shade(r[okey], shade_cents) if shade_cents else r[okey]
            win = r["y_nrfi"] == (1 if side == "nrfi" else 0)
            recs.append({"date": r["date"],
                         "pnl": payout(o) if win else -1.0,
                         "win": win, "need": implied(o)})
        hit = np.mean([x["win"] for x in recs])
        need = np.mean([x["need"] for x in recs])
        units = sum(x["pnl"] for x in recs)
        roi = units / len(recs)
        lo, hi = day_bootstrap(recs, lambda x: x["pnl"])
        print(f"    {d:>7.0%}{len(recs):>6}{100*hit:>8.2f}{100*need:>8.2f}"
              f"{100*(hit-need):>+9.2f}{100*roi:>+8.2f}{units:>+9.2f}   "
              f"[{100*lo:+.1f}%, {100*hi:+.1f}%]")
        res.append({"depth": d, "n": len(recs), "hit": hit, "need": need,
                    "roi": roi, "units": units, "ci": (lo, hi)})
    return res


def main():
    print("=" * 108)
    print("  REFUTATION: 'raise the NRFI bar -- bet only the deepest p_nrfi slices'")
    print("=" * 108)

    rows26, sk26 = load_rows(ROOT / "data" / "picks_2026.csv", "2026")
    print(f"  2026 graded rows loaded: {len(rows26)} (skipped {sk26})")
    n_nrfi_priced = sum(1 for r in rows26 if r["nrfi_odds"] is not None)
    print(f"  with a REAL captured DK NRFI price: {n_nrfi_priced}")
    print(f"  span: {rows26[0]['date']} .. {rows26[-1]['date']}")

    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    for r in rows26:
        r["p"] = cal.predict(r["raw"])

    # ------------------------------------------------------- A. as-deployed
    print("\n" + "-" * 108)
    print("  A. AS-DEPLOYED CALIBRATOR (in-sample; the friendliest possible reading)")
    print("-" * 108)
    a = ladder(rows26, "nrfi", label="2026 full season, real DK NRFI prices")

    # sanity mirror: the YRFI side, same machinery
    ladder(rows26, "yrfi", label="MIRROR CHECK -- YRFI side, same code path")

    # ------------------------------------------------------- B. walk-forward cal
    print("\n" + "-" * 108)
    print("  B. WALK-FORWARD CALIBRATOR (each date scored by a CIR fit on strictly")
    print("     earlier games only -- removes the calibrator's hindsight)")
    print("-" * 108)
    dates = sorted({r["date"] for r in rows26})
    idx_by_date = defaultdict(list)
    for i, r in enumerate(rows26):
        idx_by_date[r["date"]].append(i)
    wf = [None] * len(rows26)
    MIN_TRAIN = 200
    for d in dates:
        prior = [i for i in range(len(rows26)) if rows26[i]["date"] < d]
        if len(prior) < MIN_TRAIN:
            continue
        c = CIRCalibrator.fit([rows26[i]["raw"] for i in prior],
                              [rows26[i]["y_nrfi"] for i in prior],
                              20, ["wf"])
        for i in idx_by_date[d]:
            wf[i] = c.predict(rows26[i]["raw"])
    live = []
    for r, p in zip(rows26, wf):
        if p is None:
            continue
        q = dict(r)
        q["p"] = p
        live.append(q)
    print(f"  walk-forward scored games: {len(live)}")
    ladder(live, "nrfi", label="2026 walk-forward, real DK NRFI prices")

    # ------------------------------------------------------- C. time split
    print("\n" + "-" * 108)
    print("  C. TIME SPLIT -- pick the best depth on the FIRST half, then apply it")
    print("     BLIND to the SECOND half (real prices both sides)")
    print("-" * 108)
    priced = [r for r in rows26 if r["nrfi_odds"] is not None]
    priced.sort(key=lambda r: r["date"])
    cut = priced[len(priced) // 2]["date"]
    h1 = [r for r in priced if r["date"] < cut]
    h2 = [r for r in priced if r["date"] >= cut]
    print(f"  cut date {cut}:  H1 n={len(h1)}   H2 n={len(h2)}")
    grid = [round(x, 2) for x in np.arange(0.02, 0.81, 0.02)]
    print(f"  depth grid searched on H1: {len(grid)} cells "
          f"({grid[0]:.0%} .. {grid[-1]:.0%})")
    h1s = sorted(h1, key=lambda r: -r["p"])
    best, best_roi = None, -9e9
    for d in grid:
        k = max(1, int(round(d * len(h1s))))
        sub = h1s[:k]
        u = sum(payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0 for r in sub)
        roi = u / len(sub)
        if roi > best_roi:
            best, best_roi = d, roi
    print(f"  BEST depth on H1: {best:.0%}  ROI {100*best_roi:+.2f}% "
          f"(this is the winner of a {len(grid)}-cell search -- expect it to be lucky)")
    h2s = sorted(h2, key=lambda r: -r["p"])
    k = max(1, int(round(best * len(h2s))))
    sub = h2s[:k]
    recs = [{"date": r["date"],
             "pnl": payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0,
             "win": r["y_nrfi"] == 1, "need": implied(r["nrfi_odds"])}
            for r in sub]
    u = sum(x["pnl"] for x in recs)
    lo, hi = day_bootstrap(recs, lambda x: x["pnl"])
    print(f"  APPLIED BLIND to H2: n={len(recs)} hit={100*np.mean([x['win'] for x in recs]):.2f}% "
          f"need={100*np.mean([x['need'] for x in recs]):.2f}% "
          f"ROI={100*u/len(recs):+.2f}% units={u:+.2f} "
          f"CI[{100*lo:+.1f}%, {100*hi:+.1f}%]")

    # ------------------------------------------------------- D. 10c shade
    print("\n" + "-" * 108)
    print("  D. 10-CENT-WORSE PRICING STRESS TEST (as-deployed calibrator)")
    print("-" * 108)
    ladder(rows26, "nrfi", shade_cents=10.0,
           label="2026, real DK NRFI prices shaded 10c against us")

    # ------------------------------------------------------- E. 2025 OOS
    print("\n" + "-" * 108)
    print("  E. 2025 OUT-OF-SAMPLE -- no odds exist there, so this can only test")
    print("     DISCRIMINATION: does the deepest p_nrfi slice actually hit NRFI more?")
    print("-" * 108)
    p25 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30_truepit.csv"
    rows25, sk25 = load_rows(p25, "2025")
    for r in rows25:
        r["p"] = cal.predict(r["raw"])
    print(f"  2025 games loaded: {len(rows25)} (skipped {sk25})")
    s25 = sorted(rows25, key=lambda r: -r["p"])
    print(f"    {'depth':>7}{'n':>6}{'NRFI hit%':>11}   (2026 real-price break-even "
          f"at that depth shown for reference)")
    ref = {x["depth"]: x["need"] for x in a}
    for d in (0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.05, 0.02):
        k = max(1, int(round(d * len(s25))))
        sub = s25[:k]
        hit = np.mean([r["y_nrfi"] for r in sub])
        print(f"    {d:>7.0%}{len(sub):>6}{100*hit:>11.2f}      "
              f"break-even {100*ref[d]:.2f}%   -> "
              f"{'CLEARS' if hit > ref[d] else 'MISSES'} by {100*(hit-ref[d]):+.2f}pp")

    # ------------------------------------------------------- F. AUC by region
    print("\n" + "-" * 108)
    print("  F. DOES RANKING EVEN WORK INSIDE THE HIGH-p_nrfi REGION?")
    print("     (AUC computed only among games above the median p_nrfi)")
    print("-" * 108)
    for tag, rr in (("2026 (all graded)", rows26), ("2025 (backtest)", rows25)):
        p = np.array([r["p"] for r in rr])
        y = np.array([r["y_nrfi"] for r in rr])
        med = np.median(p)
        for name, mask in (("full range", np.ones_like(y, bool)),
                           ("upper half only", p >= med)):
            pp, yy = p[mask], y[mask]
            if yy.min() == yy.max():
                continue
            order = np.argsort(pp)
            ranks = np.empty(len(pp), float)
            ranks[order] = np.arange(1, len(pp) + 1)
            n1, n0 = yy.sum(), (1 - yy).sum()
            auc = (ranks[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
            print(f"    {tag:<20}{name:<20}n={len(yy):>5}  AUC={auc:.4f}")

    print("\n" + "=" * 108)
    return 0


if __name__ == "__main__":
    sys.exit(main())

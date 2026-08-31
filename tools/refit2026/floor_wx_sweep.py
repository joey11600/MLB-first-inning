#!/usr/bin/env python3
"""
THE WEATHER-BUMP SWEEP: is the +/-0.02 weather adjustment on the STRONG-YRFI
lambda floor still earning its keep on the v3 scale?

WHY.  2026-08-25 rescaled _LR_LAMBDA_YRFI_FLOOR 0.838 -> 0.75 because v3's
L2=0.50 compressed lambda ~0.11 and the fixed cut had silently become a 97%
cull.  The rescale fixed the BASE only.  _weather_adjusted_floor still adds a
fixed +0.02 in hot (>=28C) or windy (>=24km/h) games -- and on the compressed
v3 scale (STRONG-YRFI median lambda ~0.767) a 0.77 bar sits ABOVE the median,
so in hot games the floor again culls over half the book.  August 26-30 live:
6 of 10 STRONG-YRFI candidates demoted, 4 of them only by the heat bump.
Same defect class as the base bug: a fixed offset on a scale the model moved.

WHAT THIS TESTS (every config is a SELECTION rule on the same OOS p vector,
so model-level artifacts cancel in every delta):
  - shipped        base 0.75, wx delta 0.02   (production today)
  - flat075        base 0.75, no wx           (option A)
  - wx010 / wx005  base 0.75, wx 0.01 / 0.005 (option B)
  - cal_gate       no lambda floor; demote candidates above a CALIBRATED
                   p_nrfi ceiling set at the 87th pctile of the TRAIN-season
                   candidate distribution (option C -- scale-stable by
                   construction; 13% = the floor's original design trim)
  - old_838        base 0.838, wx 0.02        (pre-08-25, reference)
  - no_floor       reference
  plus a full grid: base 0.70..0.80 x wx {0, 0.005, 0.01, 0.02}.

GUARDS this file carries because sweeps here have burned us twice
(2026-08-03_gate_sweep_artifact, park_null.py):
  - p_nrfi and lambda correlate -0.97, so floor levels mostly re-slice the
    p-gate.  The only NEW information in the bump is the weather itself, so
    the decision test is weather-specific: permute each game's weather packet
    (temp, wind, dome move together) and re-run the identical selection.
    300 trials, selection-aware: the null re-runs the WHOLE grid and takes
    its best, so search inflation is priced in.
  - Money on 2024/2025 is flat -112 (no first-inning odds exist); read those
    columns as hit-rate arithmetic.  2026 uses the real captured price where
    the ledger has one.
  - All three splits, coverage printed first, day-level bootstrap on deltas.

Usage:  python tools/refit2026/floor_wx_sweep.py [--picks26 PATH] [--boot 2000]
                                                 [--trials 300]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration import CIRCalibrator  # noqa: E402
from harness import T1_SHIPPED, B1_SHIPPED, build_park, fit_lr, load, matrix, predict  # noqa: E402
from test_fi_pooled import attach  # noqa: E402

GATE_NRFI, KELLY_FRAC, CAP_BET, CAP_DAY, DEFAULT_PRICE = 0.42, 0.25, 10.0, 15.0, -112.0
T1_V3 = T1_SHIPPED + ["home_fi_xwoba"]
B1_V3 = B1_SHIPPED + ["away_fi_xwoba"]
L2_V3 = 0.50
HOT_C, COLD_C, WIND_KMH = 28.0, 12.0, 24.0   # mirrors _weather_adjusted_floor
CAL_TRIM = 0.13                               # the floor's original design trim


def dec(price):
    price = float(price)
    return (100.0 / abs(price)) if price < 0 else (price / 100.0)


def qkelly(p, b):
    f = (b * p - (1.0 - p)) / b
    return float(np.clip(f * KELLY_FRAC * 100.0, 0.0, CAP_BET))


def fit_score_both(tr, te):
    """v3 fit on train only -> (raw p_nrfi, calibrated p_nrfi) for test AND
    the calibrated train candidates' p_nrfi (for the cal_gate ceiling)."""
    tr, te = tr.copy(), te.copy()
    for c in [x for x in T1_V3 + B1_V3 if x.endswith("fi_xwoba")]:
        mu = tr[c].mean(); tr[c] = tr[c].fillna(mu); te[c] = te[c].fillna(mu)
    pk, b0 = build_park(tr, 50)
    wt, mt, st = fit_lr(matrix(tr, T1_V3, pk, b0), tr.y_t1.values, L2_V3)
    wb, mb, sb = fit_lr(matrix(tr, B1_V3, pk, b0), tr.y_b1.values, L2_V3)
    def raw(d):
        return (1 - predict(wt, mt, st, matrix(d, T1_V3, pk, b0))) * \
               (1 - predict(wb, mb, sb, matrix(d, B1_V3, pk, b0)))
    raw_tr, raw_te = raw(tr), raw(te)
    cal = CIRCalibrator.fit(list(raw_tr), list((tr.y == 0).astype(int)), n_bins=20)
    cal_te = np.array([cal.predict(float(v)) for v in raw_te])
    cal_tr = np.array([cal.predict(float(v)) for v in raw_tr])
    return raw_te, cal_te, cal_tr


def wx_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Weather exactly as classify_pick_lr sees it: NaN temp/wind = no
    adjustment, dome short-circuits.  NOT the imputed model-matrix values."""
    w = pd.DataFrame(index=d.index)
    w["temp"] = pd.to_numeric(d.get("wx_temp_c"), errors="coerce")
    w["wind"] = pd.to_numeric(d.get("wx_wind_kmh"), errors="coerce")
    dm = d.get("wx_is_dome")
    if dm is None:
        w["dome"] = False
    else:
        w["dome"] = dm.astype(str).str.strip().str.lower().isin(("true", "1", "1.0", "yes", "y"))
    return w


def floors_for(w: pd.DataFrame, base: float, delta: float) -> np.ndarray:
    """Vectorised _weather_adjusted_floor with bump size `delta`."""
    adj = np.zeros(len(w))
    adj += np.where(w.temp.values >= HOT_C, delta, 0.0)
    adj += np.where(w.temp.values <= COLD_C, -delta, 0.0)   # NaN >= / <= is False
    adj += np.where(w.wind.values >= WIND_KMH, delta, 0.0)
    out = np.clip(base + adj, 0.40, 1.20)
    return np.where(w.dome.values, base, out)


def simulate(te, keep_mask):
    """Stakes with production caps over the kept candidates -> bet table."""
    t = te.loc[keep_mask, ["date", "y", "price", "p_cal"]].copy()
    if not len(t):
        return t
    t["p_yrfi"] = 1 - t.p_cal
    t["b"] = t.price.map(dec)
    t["stake"] = [qkelly(p, b) for p, b in zip(t.p_yrfi, t.b)]
    out = {}
    for _, day in t.sort_values(["date", "p_cal"]).groupby("date"):
        used = 0.0
        for idx, r in day.iterrows():
            s = min(r.stake, max(CAP_DAY - used, 0.0)); used += s; out[idx] = s
    t["stake"] = pd.Series(out)
    t = t[t.stake > 0].copy()
    t["won"] = t.y == 1
    t["flat"] = np.where(t.won, t.b, -1.0)
    t["kelly"] = np.where(t.won, t.stake * t.b, -t.stake)
    t["is_no1"] = False
    if len(t):
        t.loc[t.groupby("date").p_cal.idxmin(), "is_no1"] = True
    return t


def summarize(bets, demoted):
    if not len(bets):
        s = dict(bets=0, W=0, L=0, hit=np.nan, flat=0.0, kelly=0.0)
    else:
        s = dict(bets=len(bets), W=int(bets.won.sum()), L=int((~bets.won).sum()),
                 hit=float(bets.won.mean()), flat=float(bets.flat.sum()),
                 kelly=float(bets.kelly.sum()))
    n1 = bets[bets.is_no1] if len(bets) else bets
    s.update(n1=len(n1), n1W=int(n1.won.sum()) if len(n1) else 0,
             n1flat=float(n1.flat.sum()) if len(n1) else 0.0)
    s.update(dn=len(demoted), dhit=float(demoted.y.mean()) if len(demoted) else np.nan)
    return s


def run_config(te, cand_mask, lam, w, base, delta, cal_ceiling=None):
    """Apply one floor config; returns (bets table, demoted rows)."""
    if cal_ceiling is not None:
        keep = cand_mask & (te.p_cal.values <= cal_ceiling)
    elif base <= 0:
        keep = cand_mask
    else:
        keep = cand_mask & (lam >= floors_for(w, base, delta))
    return simulate(te, keep), te.loc[cand_mask & ~keep]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks26", type=Path, default=ROOT / "data" / "picks_2026.csv")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260831)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    fac = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    bt = ROOT / "data" / "backtests"
    d24 = attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fac)
    d25 = attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fac)
    # The live ledger has carried its own fi_xwoba columns since v3 shipped
    # (2026-08-23).  Drop them and take the factor file's walk-forward values,
    # as the 08-22/08-25 validations did -- attach() would otherwise suffix
    # the collision to _x/_y and fit_score_both would KeyError.
    d26 = load(args.picks26, "home_team", 2026)
    d26 = attach(d26.drop(columns=[c for c in ("home_fi_xwoba", "away_fi_xwoba")
                                   if c in d26.columns]), fac)
    for d in (d24, d25, d26):
        d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d24["price"] = DEFAULT_PRICE; d25["price"] = DEFAULT_PRICE
    px = pd.to_numeric(d26.get("market_yrfi_odds"), errors="coerce")
    d26["price"] = px.fillna(DEFAULT_PRICE)

    # ---- coverage first (feature_test_methodology rule 1) ------------------
    print("WEATHER COVERAGE (as the floor sees it; NaN temp = no adjustment)")
    for lab, d in [("2024", d24), ("2025", d25), ("2026", d26)]:
        w = wx_frame(d)
        print(f"  {lab}: n={len(d)}  temp known {w.temp.notna().mean():5.1%}  "
              f"dome {w.dome.mean():5.1%}  hot>=28C {(w.temp >= HOT_C).mean():5.1%}  "
              f"cold<=12C {(w.temp <= COLD_C).mean():5.1%}  wind>=24 {(w.wind >= WIND_KMH).mean():5.1%}")
    print(f"  2026 real captured YRFI prices: {px.notna().sum()} of {len(d26)} "
          f"(rest at {DEFAULT_PRICE:.0f}); 2024/2025 all at {DEFAULT_PRICE:.0f}\n")

    splits = [("2024 (tr 2025)", d25, d24),
              ("2025 (tr 2024)", d24, d25),
              ("2026 (tr 24+25)", pd.concat([d24, d25], ignore_index=True), d26)]

    NAMED = {"no_floor": (0.0, 0.0), "old_838": (0.838, 0.02),
             "shipped": (0.75, 0.02), "wx010": (0.75, 0.01),
             "wx005": (0.75, 0.005), "flat075": (0.75, 0.0)}
    GRID = [(round(b, 2), dx) for b in np.arange(0.70, 0.801, 0.01)
            for dx in (0.0, 0.005, 0.01, 0.02)]

    per_split = {}
    for lab, tr, te in splits:
        te = te.reset_index(drop=True).copy()
        raw, calp, cal_tr = fit_score_both(tr, te)
        te["p_cal"] = calp
        lam = -np.log(np.clip(raw, 1e-9, 1.0))       # lambda_lr_total, exactly
        w = wx_frame(te)
        cand = te.p_cal.values < GATE_NRFI
        tr_cand = cal_tr[cal_tr < GATE_NRFI]
        ceiling = float(np.quantile(tr_cand, 1.0 - CAL_TRIM))
        per_split[lab] = dict(te=te, lam=lam, w=w, cand=cand, ceiling=ceiling)
        print(f"SPLIT {lab}: candidates {cand.sum()}  STRONG-lambda pctiles "
              f"[13th {np.percentile(lam[cand], 13):.3f}, median {np.median(lam[cand]):.3f}, "
              f"87th {np.percentile(lam[cand], 87):.3f}]  cal_gate ceiling {ceiling:.4f}")
    print()

    # ---- named configs, all three splits -----------------------------------
    results = {}
    for lab, _, _ in splits:
        P = per_split[lab]
        for name, (base, dx) in NAMED.items():
            b, dm = run_config(P["te"], P["cand"], P["lam"], P["w"], base, dx)
            results[(lab, name)] = (summarize(b, dm), b)
        b, dm = run_config(P["te"], P["cand"], P["lam"], P["w"], 0, 0, cal_ceiling=P["ceiling"])
        results[(lab, "cal_gate")] = (summarize(b, dm), b)

    names = list(NAMED) + ["cal_gate"]
    print("=" * 118)
    print("NAMED CONFIGS, out of sample (2026 = real prices where captured; 24/25 = flat -112 hit-rate arithmetic)")
    print(f"  {'split':<16} {'config':<9} {'bets':>5} {'record':>9} {'hit':>6} {'flat':>8} {'u/bet':>7} "
          f"{'Kelly':>8} | {'No.1':>4} {'n1 rec':>7} {'n1 flat':>8} | {'cut':>4} {'cut-hit':>7}")
    for lab, _, _ in splits:
        for name in names:
            s, _ = results[(lab, name)]
            ub = s["flat"] / s["bets"] if s["bets"] else float("nan")
            print(f"  {lab:<16} {name:<9} {s['bets']:>5} {s['W']:>4}-{s['L']:<4} {s['hit']:>6.3f} "
                  f"{s['flat']:>+7.2f}u {ub:>+7.4f} {s['kelly']:>+7.2f}u | "
                  f"{s['n1']:>4} {s['n1W']:>3}-{s['n1'] - s['n1W']:<3} {s['n1flat']:>+7.2f}u | "
                  f"{s['dn']:>4} {s['dhit']:>7.3f}")
        print()

    # cal_gate vs flat075 selection overlap (they should be near-identical
    # within a split -- CIR is monotone -- the difference is refit stability)
    for lab, _, _ in splits:
        P = per_split[lab]
        ka = set(run_config(P["te"], P["cand"], P["lam"], P["w"], 0.75, 0.0)[0].index)
        kb = set(run_config(P["te"], P["cand"], P["lam"], P["w"], 0, 0, cal_ceiling=P["ceiling"])[0].index)
        j = len(ka & kb) / max(len(ka | kb), 1)
        print(f"  cal_gate vs flat075 bet-set overlap, {lab}: Jaccard {j:.3f}")
    print()

    # ---- full grid on 2026 (context for the named rows) --------------------
    print("=" * 118)
    print("FULL GRID, 2026 split only (flat u; * = named config)")
    hdr = "  base   " + "".join(f"{('wx' + format(dx, '.3f').rstrip('0').rstrip('.')):>12}" for dx in (0.0, 0.005, 0.01, 0.02))
    print(hdr)
    P = per_split["2026 (tr 24+25)"]
    for basev in sorted({b for b, _ in GRID}):
        cells = []
        for dx in (0.0, 0.005, 0.01, 0.02):
            b, _ = run_config(P["te"], P["cand"], P["lam"], P["w"], basev, dx)
            s = summarize(b, P["te"].iloc[0:0])
            mark = "*" if (basev, dx) in ((0.75, 0.0), (0.75, 0.005), (0.75, 0.01), (0.75, 0.02)) else " "
            cells.append(f"{s['flat']:>+8.1f}u/{s['bets']:<3}{mark}")
        print(f"  {basev:.2f} " + "".join(f"{c:>12}" for c in cells))
    print()

    # ---- day bootstrap: each option minus shipped --------------------------
    print("=" * 118)
    print(f"DAY-LEVEL BOOTSTRAP ({args.boot} draws): config minus shipped")
    for lab, _, _ in splits:
        bs = results[(lab, "shipped")][1]
        days_all = sorted(set(per_split[lab]["te"].date))
        gs = {d: (x.flat.sum(), x[x.is_no1].won.sum(), len(x[x.is_no1])) for d, x in bs.groupby("date")}
        print(f"  {lab}")
        for name in ["flat075", "wx010", "wx005", "cal_gate", "no_floor", "old_838"]:
            bo = results[(lab, name)][1]
            go = {d: (x.flat.sum(), x[x.is_no1].won.sum(), len(x[x.is_no1])) for d, x in bo.groupby("date")}
            days = np.array(days_all)
            dflat, dn1 = [], []
            for _ in range(args.boot):
                pick = days[rng.integers(0, len(days), len(days))]
                fo = sum(go.get(d, (0, 0, 0))[0] for d in pick) - sum(gs.get(d, (0, 0, 0))[0] for d in pick)
                w1 = sum(go.get(d, (0, 0, 0))[1] for d in pick); n1 = sum(go.get(d, (0, 0, 0))[2] for d in pick)
                w2 = sum(gs.get(d, (0, 0, 0))[1] for d in pick); n2 = sum(gs.get(d, (0, 0, 0))[2] for d in pick)
                dflat.append(fo); dn1.append(w1 / max(n1, 1) - w2 / max(n2, 1))
            dflat, dn1 = np.array(dflat), np.array(dn1)
            print(f"    {name:<9} dflat {dflat.mean():>+7.2f}u  90% CI [{np.percentile(dflat, 5):+.2f}, "
                  f"{np.percentile(dflat, 95):+.2f}]  P(>0) {(dflat > 0).mean():>4.0%}   "
                  f"dNo.1-hit {dn1.mean():>+6.3f}  [{np.percentile(dn1, 5):+.3f}, {np.percentile(dn1, 95):+.3f}]")
    print()

    # ---- the weather-specific question, asked directly ---------------------
    # Among near-floor candidates (lambda in [0.75, 0.77)) -- the only games
    # the hot-bump decision actually touches -- do HOT games hit YRFI less
    # often (the bump's premise) or not?
    print("=" * 118)
    print("NEAR-FLOOR BAND lambda in [0.75, 0.77): the games the hot-bump decides.  Bump premise = hot hits LESS.")
    for lab, _, _ in splits:
        P = per_split[lab]
        band = P["cand"] & (P["lam"] >= 0.75) & (P["lam"] < 0.77) & ~P["w"].dome.values
        hot = band & (P["w"].temp.values >= HOT_C)
        cold_ok = band & ~(P["w"].temp.values >= HOT_C)
        yh = P["te"].y.values[hot]; yc = P["te"].y.values[cold_ok]
        # permutation p on the hot/not split within the band
        yb = P["te"].y.values[band]; nh = hot.sum()
        if nh and len(yb) > nh:
            obs = yh.mean() - yc.mean()
            perm = [yb[rng.permutation(len(yb))[:nh]].mean() -
                    yb[rng.permutation(len(yb))[nh:]].mean() for _ in range(5000)]
            p_lo = float(np.mean(np.array(perm) <= obs))   # premise true if hot LOWER
            print(f"  {lab}: band n={band.sum()}  hot {nh}: hit {yh.mean() if nh else float('nan'):.3f}  "
                  f"not-hot {len(yc)}: hit {yc.mean() if len(yc) else float('nan'):.3f}  "
                  f"diff {obs:+.3f}  perm p(hot lower) = {p_lo:.3f}")
        else:
            print(f"  {lab}: band n={band.sum()} hot n={nh} -- too thin to test")
    print()

    # ---- selection-aware placebo null --------------------------------------
    # Permute each game's WHOLE weather packet across the test season, re-run
    # the ENTIRE grid, record (a) flat075-minus-shipped and (b) best-grid-
    # minus-shipped.  The observed values are judged against those spreads.
    print("=" * 118)
    print(f"SELECTION-AWARE PLACEBO NULL ({args.trials} trials, weather packets permuted; 2026 split)")
    P = per_split["2026 (tr 24+25)"]
    te, lam, cand, w0 = P["te"], P["lam"], P["cand"], P["w"]
    ship_flat = summarize(*run_config(te, cand, lam, w0, 0.75, 0.02))["flat"]
    flat075 = summarize(*run_config(te, cand, lam, w0, 0.75, 0.0))["flat"]
    grid_real = {(b, dx): summarize(*run_config(te, cand, lam, w0, b, dx))["flat"] for b, dx in GRID}
    best_real = max(grid_real.values())
    obs_delta = flat075 - ship_flat
    obs_best = best_real - ship_flat
    null_delta, null_best = [], []
    for _ in range(args.trials):
        wp = w0.iloc[rng.permutation(len(w0))].reset_index(drop=True)
        wp.index = w0.index
        sh = summarize(*run_config(te, cand, lam, wp, 0.75, 0.02))["flat"]
        fl = summarize(*run_config(te, cand, lam, wp, 0.75, 0.0))["flat"]
        gb = max(summarize(*run_config(te, cand, lam, wp, b, dx))["flat"] for b, dx in GRID)
        null_delta.append(fl - sh); null_best.append(gb - sh)
    null_delta, null_best = np.array(null_delta), np.array(null_best)
    print(f"  observed: shipped {ship_flat:+.2f}u  flat075 {flat075:+.2f}u  delta {obs_delta:+.2f}u  "
          f"grid best {best_real:+.2f}u (delta {obs_best:+.2f}u)")
    print(f"  null flat075-shipped: mean {null_delta.mean():+.2f}u  sd {null_delta.std():.2f}  "
          f"p(null >= obs) = {(null_delta >= obs_delta).mean():.3f}")
    print(f"  null best-of-grid   : mean {null_best.mean():+.2f}u  sd {null_best.std():.2f}  "
          f"p(null >= obs) = {(null_best >= obs_best).mean():.3f}")
    best_cfg = max(grid_real, key=grid_real.get)
    print(f"  real grid argmax: base {best_cfg[0]:.2f} wx {best_cfg[1]:.3f}")

    # ---- 2026 by month, flat075 minus shipped (when does the bump bind?) ---
    print("\n2026 BY MONTH, flat075 minus shipped (flat u)")
    bsh = results[("2026 (tr 24+25)", "shipped")][1]
    bfl = results[("2026 (tr 24+25)", "flat075")][1]
    gsh = bsh.groupby(bsh.date.dt.to_period("M")).flat.sum()
    gfl = bfl.groupby(bfl.date.dt.to_period("M")).flat.sum()
    months = sorted(set(gsh.index) | set(gfl.index))
    print("  " + "  ".join(f"{str(m)[-2:]}: {gfl.get(m, 0) - gsh.get(m, 0):+.1f}u" for m in months))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

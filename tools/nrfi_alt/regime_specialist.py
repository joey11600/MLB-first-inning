#!/usr/bin/env python3
"""
tools/nrfi_alt/regime_specialist.py

REFUTATION TEST for: "train a dedicated NRFI-regime model -- refit the
31-feature union on ONLY the games the incumbent places at p_nrfi >= 0.50,
and use it to rank inside that region."

Independent re-derivation.  Nothing here is imported from the proposal.

Design:
  * Build the 31-feature UNION vector (the two 18-dim half-inning vectors
    share fi_park + 4 weather + ump, and era_gap is +/- mirrored).
  * Incumbent p_nrfi = production two-stage LR (raw), calibrated with the
    shipped ProbCalibrator only to LOCATE the >= 0.50 region.  The
    calibrator is monotone, so it cannot change any AUC.
  * Specialist = LogReg (same L-BFGS + L2 code the production model uses)
    fit on train-season rows inside the region only.
  * Evaluate on the test season's region rows: AUC(specialist) vs
    AUC(incumbent) on the IDENTICAL row set.
  * CIs by block bootstrap over DAYS.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import recalibrate_v2 as rc          # noqa: E402
from lr_baseline import LogReg       # noqa: E402
from calibration import ProbCalibrator  # noqa: E402

RNG = np.random.default_rng(20260728)

# --------------------------------------------------------------------------
# 31-feature union.  (col-name, default) -- default mirrors recalibrate_v2.
# --------------------------------------------------------------------------
LNR = rc.LEAGUE_NRFI_RATE
LERA = rc.LEAGUE_AVG_ERA
LOBP = rc.LEAGUE_AVG_OBP
LXERA = rc.LEAGUE_AVG_XERA
LSLG = rc.LEAGUE_AVG_SLG
LISO = rc.LEAGUE_AVG_ISO
LOPS = rc.LEAGUE_AVG_OPS_VSHAND
NPR = rc.NEUTRAL_PCT_RANK

UNION = [
    ("home_fip", LERA), ("away_fip", LERA),
    ("home_obp", LOBP), ("away_obp", LOBP),
    ("home_p_last5_pitcher_nrfi", LNR), ("away_p_last5_pitcher_nrfi", LNR),
    ("home_p_last10_pitcher_nrfi", LNR), ("away_p_last10_pitcher_nrfi", LNR),
    ("home_top3c_obp", LOBP), ("away_top3c_obp", LOBP),
    ("home_top3c_slg", LSLG), ("away_top3c_slg", LSLG),
    ("home_top3c_iso", LISO), ("away_top3c_iso", LISO),
    ("home_xera", LXERA), ("away_xera", LXERA),
    ("home_whiff_pct_rank", NPR), ("away_whiff_pct_rank", NPR),
    ("home_pvt_nrfi_rate", LNR), ("away_pvt_nrfi_rate", LNR),
    ("home_avg_ip_per_start", 5.0), ("away_avg_ip_per_start", 5.0),
    ("home_top3_ops_vs_oppHand", LOPS), ("away_top3_ops_vs_oppHand", LOPS),
    ("wx_temp_c", rc.WX_TEMP_DEFAULT), ("wx_wind_kmh", rc.WX_WIND_DEFAULT),
    ("wx_humidity", rc.WX_HUMIDITY_DEFAULT), ("wx_is_dome", 0.0),
    ("home_plate_ump_nrfi_rate", LNR),
]
UNION_NAMES = [c for c, _ in UNION] + ["fi_park", "era_gap"]


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def build_union(r, fi_park):
    v = [rc.coerce_float(r.get(c), d) for c, d in UNION]
    h = rc.coerce_float(r.get("home_era"), LERA)
    a = rc.coerce_float(r.get("away_era"), LERA)
    return v + [fi_park, h - a]


def load(path, season):
    """Returns list of dicts with union vec, t1/b1 vecs, y, date, odds."""
    fi_map = rc.load_fi_park()
    rows = list(csv.DictReader(open(ROOT / path, encoding="utf-8")))
    out = []
    for r in rows:
        if season == 2026:
            actual = (r.get("actual_result") or "").upper()
            home = r.get("home_team", "")
        else:
            actual = (r.get("actual_side") or "").upper()
            home = r.get("home", "")
        if actual not in ("NRFI", "YRFI"):
            continue
        fp = fi_map.get(home, rc.FI_PARK_DEFAULT)
        try:
            t1, b1 = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        out.append({
            "date": r["date"], "season": season,
            "t1": t1, "b1": b1, "u": build_union(r, fp),
            "y": 1 if actual == "NRFI" else 0,
            "nrfi_odds": fnum(r.get("market_nrfi_odds")),
            "yrfi_odds": fnum(r.get("market_yrfi_odds")),
        })
    out.sort(key=lambda x: x["date"])
    t1m, b1m = rc.load_lr_models()
    raw = rc.lr_predict_two_stage(
        t1m, b1m,
        np.asarray([x["t1"] for x in out], float),
        np.asarray([x["b1"] for x in out], float))
    for x, p in zip(out, raw):
        x["raw"] = float(p)
    return out


def auc(y, s):
    y = np.asarray(y, float)
    s = np.asarray(s, float)
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    sv = s[order]
    i = 0
    r = np.arange(1, len(s) + 1, dtype=float)
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = r[i:j + 1].mean()
        i = j + 1
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def day_block_boot(rows, f, B=2000):
    """f(list_of_rows) -> scalar.  Resample DAYS with replacement."""
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    vals = []
    for _ in range(B):
        pick = RNG.integers(0, len(days), len(days))
        samp = [x for k in pick for x in byday[days[k]]]
        v = f(samp)
        if v == v:
            vals.append(v)
    return np.percentile(vals, [2.5, 97.5]) if vals else (float("nan"),) * 2


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def main():
    cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    d25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv", 2025)
    d26 = load("data/picks_2026.csv", 2026)
    for d in (d25, d26):
        for r in d:
            r["p"] = float(cal.predict(r["raw"]))

    print(f"loaded 2025 n={len(d25)}  2026 n={len(d26)}")
    print(f"union features d={len(UNION_NAMES)}")

    HI = 0.50
    for name, d in (("2025", d25), ("2026", d26)):
        hi = [r for r in d if r["p"] >= HI]
        lo = [r for r in d if r["p"] < HI]
        raws = [r["raw"] for r in hi]
        print(f"  {name}: high n={len(hi)} (NRFI rate {np.mean([r['y'] for r in hi]):.4f}, "
              f"raw p range {min(raws):.4f}..{max(raws):.4f})  "
              f"low n={len(lo)} (rate {np.mean([r['y'] for r in lo]):.4f})")

    print("\n" + "=" * 92)
    print("  A. SPECIALIST vs INCUMBENT -- AUC inside the region, out of sample")
    print("=" * 92)
    print(f"  {'cell':<34}{'n':>6}{'inc AUC':>10}{'spec AUC':>10}{'delta':>9}"
          f"{'  95% CI on delta'}")

    results = {}
    for regime, lo_hi in (("high", True), ("low", False)):
        for trname, tr, tename, te in (("2025", d25, "2026", d26),
                                       ("2026", d26, "2025", d25)):
            sel = (lambda r: r["p"] >= HI) if lo_hi else (lambda r: r["p"] < HI)
            TR = [r for r in tr if sel(r)]
            TE = [r for r in te if sel(r)]
            Xtr = np.asarray([r["u"] for r in TR], float)
            ytr = np.asarray([r["y"] for r in TR], float)
            m = LogReg.fit(Xtr, ytr, UNION_NAMES, l2=0.05)
            Xte = np.asarray([r["u"] for r in TE], float)
            spec = m.predict_proba(Xte)
            for r, s in zip(TE, spec):
                r["spec"] = float(s)
            a_i = auc([r["y"] for r in TE], [r["raw"] for r in TE])
            a_s = auc([r["y"] for r in TE], spec)
            ci = day_block_boot(
                TE, lambda rr: auc([r["y"] for r in rr], [r["spec"] for r in rr])
                - auc([r["y"] for r in rr], [r["raw"] for r in rr]))
            lab = f"{regime}: train {trname} -> test {tename}"
            print(f"  {lab:<34}{len(TE):>6}{a_i:>10.4f}{a_s:>10.4f}"
                  f"{a_s - a_i:>+9.4f}   [{ci[0]:+.4f},{ci[1]:+.4f}]")
            results[(regime, trname)] = (len(TE), a_i, a_s, a_s - a_i, ci)
    return results


if __name__ == "__main__":
    main()

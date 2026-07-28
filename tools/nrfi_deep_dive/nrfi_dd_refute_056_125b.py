#!/usr/bin/env python3
"""tools/nrfi_dd_refute_056_125b.py -- part 2 of the audit.

  F. de-vigged zero-edge null: p-value for the cell AND family-wise p for
     "best cell in the 56-cell grid"
  G. concentration (park / series) + park-block bootstrap
  H. sensitivity of the -125 price floor and the 0.56 lambda ceiling
  I. 3-split geometry check (2024 / 2025 / 2026) at the fixed 2026 price
Read-only.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict, Counter
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


def score(rows):
    t1m, b1m = rc.load_lr_models()
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def load26():
    fi_park = rc.load_fi_park()
    out = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("actual_result") or "").upper() not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            out.append({"date": r["date"], "away": r.get("away_team", ""),
                        "home": r.get("home_team", ""), "t1": tv, "b1": bv,
                        "y": 1 if (r.get("actual_result") or "").upper() == "NRFI" else 0,
                        "odds": fnum(r.get("market_nrfi_odds")),
                        "yodds": fnum(r.get("market_yrfi_odds"))})
    return score(out)


def load_bt(paths):
    fi_park = rc.load_fi_park()
    out = []
    for p in paths:
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("actual_side") or "").upper() not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get("home", ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                out.append({"date": r.get("date", ""), "home": r.get("home", ""),
                            "t1": tv, "b1": bv,
                            "y": 1 if (r.get("actual_side") or "").upper() == "NRFI" else 0})
    return score(out)


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, 100, 120]


def roi(sub):
    return sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub) / len(sub)


def main():
    s26 = load26()
    priced = [r for r in s26 if r["odds"] is not None]
    RULE = [r for r in priced if r["lam"] <= 0.56 and r["odds"] >= -125]

    # ---- F. de-vigged zero-edge null ------------------------------------
    print("=" * 96)
    print("  F. ZERO-EDGE NULL (outcomes resampled from the DE-VIGGED DK price)")
    print("=" * 96)
    fair = []
    nodevig = 0
    for r in priced:
        if r["yodds"] is not None:
            a, b = implied(r["odds"]), implied(r["yodds"])
            r["fair"] = a / (a + b)
        else:
            r["fair"] = implied(r["odds"]) - 0.022   # strip ~half of a typical 4.4% hold
            nodevig += 1
    print("  priced n=%d, of which %d had no YRFI price (approximated)" % (len(priced), nodevig))
    print("  mean de-vigged book NRFI prob over the 21 rule games = %.3f"
          % st.mean([r["fair"] for r in RULE]))
    print("  ACTUAL hit rate on those games                      = %.3f"
          % (sum(r["y"] for r in RULE) / len(RULE)))

    rng = np.random.default_rng(2026)
    ITERS = 20000
    obs_cell = roi(RULE)
    cells = []
    for c in CAPS:
        for pf in FLOORS:
            idx = [i for i, r in enumerate(priced) if r["lam"] <= c and r["odds"] >= pf]
            if len(idx) >= 20:
                cells.append((c, pf, np.array(idx)))
    print("  eligible cells (n>=20) in the 56-cell grid: %d" % len(cells))

    fairs = np.array([r["fair"] for r in priced])
    pays = np.array([payout(r["odds"]) for r in priced])
    ge_cell = 0
    ge_family = 0
    maxes = []
    for _ in range(ITERS):
        ysim = (rng.random(len(priced)) < fairs).astype(float)
        pnl = np.where(ysim > 0, pays, -1.0)
        best = -9.0
        for c, pf, idx in cells:
            v = pnl[idx].mean()
            if c == 0.56 and pf == -125:
                if v >= obs_cell:
                    ge_cell += 1
            if v > best:
                best = v
        maxes.append(best)
        if best >= obs_cell:
            ge_family += 1
    maxes.sort()
    print("\n  observed ROI of the 0.56/-125 cell: %+.1f%%" % (100 * obs_cell))
    print("  P(this ONE cell >= %+.1f%% | zero edge)          = %.3f   <- naive p"
          % (100 * obs_cell, ge_cell / ITERS))
    print("  P(BEST of the %d cells >= %+.1f%% | zero edge)   = %.3f   <- search-corrected p"
          % (len(cells), 100 * obs_cell, ge_family / ITERS))
    print("  under zero edge the best-of-grid ROI is typically %+.1f%% (median), "
          "95th pct %+.1f%%" % (100 * maxes[ITERS // 2], 100 * maxes[int(0.95 * ITERS)]))

    # ---- G. concentration ------------------------------------------------
    print("\n" + "=" * 96)
    print("  G. CONCENTRATION -- are these 21 bets 21 independent observations?")
    print("=" * 96)
    ph = Counter(r["home"] for r in RULE)
    print("  home parks: " + ", ".join("%s=%d" % kv for kv in ph.most_common()))
    top2 = sum(v for _, v in ph.most_common(2))
    print("  top-2 parks account for %d of %d bets (%.0f%%); %d distinct parks"
          % (top2, len(RULE), 100 * top2 / len(RULE), len(ph)))
    mm = Counter((r["away"], r["home"]) for r in RULE)
    rep = {k: v for k, v in mm.items() if v > 1}
    print("  repeated same-series matchups: %s" % (rep if rep else "none"))
    for park, k in ph.most_common(3):
        s = [r for r in RULE if r["home"] == park]
        w = sum(r["y"] for r in s)
        print("    %-4s n=%2d  %dW-%dL  P/L=%+.2fu" % (park, k, w, k - w,
              sum(payout(r["odds"]) if r["y"] else -1.0 for r in s)))
    ex = [r for r in RULE if r["home"] not in [p for p, _ in ph.most_common(2)]]
    if ex:
        w = sum(r["y"] for r in ex)
        p = sum(payout(r["odds"]) if r["y"] else -1.0 for r in ex)
        print("  EXCLUDING the top-2 parks: n=%d %dW-%dL P/L=%+.2fu ROI=%+.1f%%"
              % (len(ex), w, len(ex) - w, p, 100 * p / len(ex)))

    # park-block bootstrap
    byp = defaultdict(list)
    for r in RULE:
        byp[r["home"]].append(payout(r["odds"]) if r["y"] else -1.0)
    blocks = list(byp.values())
    rng2 = np.random.default_rng(5)
    out = []
    for _ in range(20000):
        idx = rng2.integers(0, len(blocks), len(blocks))
        v = [x for i in idx for x in blocks[i]]
        out.append(sum(v) / len(v))
    out.sort()
    print("  PARK-block 95%% CI on ROI: [%+.1f%%, %+.1f%%]   P(ROI<=0)=%.3f"
          % (100 * out[500], 100 * out[19500], sum(1 for x in out if x <= 0) / 20000))

    # ---- H. threshold sensitivity ---------------------------------------
    print("\n" + "=" * 96)
    print("  H. THRESHOLD SENSITIVITY -- is the winner a knife edge?")
    print("=" * 96)
    print("  price floor swept at lambda<=0.56:")
    print("  %10s%6s%8s%8s%9s" % ("floor", "n", "hit%", "need%", "ROI%"))
    for pf in (-160, -150, -145, -140, -135, -130, -125, -120, -115, -110, -105):
        sub = [r for r in priced if r["lam"] <= 0.56 and r["odds"] >= pf]
        if not sub:
            continue
        print("  %+10d%6d%8.1f%8.1f%+9.1f"
              % (pf, len(sub), 100 * sum(r["y"] for r in sub) / len(sub),
                 100 * st.mean([implied(r["odds"]) for r in sub]), 100 * roi(sub)))
    print("\n  lambda ceiling swept at price>=-125:")
    print("  %10s%6s%8s%8s%9s" % ("lam<=", "n", "hit%", "need%", "ROI%"))
    for c in (0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65):
        sub = [r for r in priced if r["lam"] <= c and r["odds"] >= -125]
        if not sub:
            continue
        print("  %10.2f%6d%8.1f%8.1f%+9.1f"
              % (c, len(sub), 100 * sum(r["y"] for r in sub) / len(sub),
                 100 * st.mean([implied(r["odds"]) for r in sub]), 100 * roi(sub)))

    # ---- I. 3-split geometry ---------------------------------------------
    print("\n" + "=" * 96)
    print("  I. 3-SPLIT -- does lambda<=0.56 clear 53.6% (the rule's break-even) each season?")
    print("=" * 96)
    srcs = {
        "2024": load_bt([BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"]),
        "2025": load_bt([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"]),
        "2026bt": load_bt([BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
                           BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"]),
    }
    print("  (no odds exist outside 2026, so this bounds ACCURACY only -- and the LR")
    print("   weights were fit on 2024+2025+2026YTD, so these are OPTIMISTIC.)")
    print("  %-8s%8s%10s%10s%12s" % ("season", "n", "base%", "lam<=.56", "vs 53.6%"))
    for nm, rows in srcs.items():
        sub = [r for r in rows if r["lam"] <= 0.56]
        if not sub:
            continue
        h = sum(r["y"] for r in sub) / len(sub)
        print("  %-8s%8d%10.1f%10.1f%+11.1fpp"
              % (nm, len(sub), 100 * sum(r["y"] for r in rows) / len(rows), 100 * h,
                 100 * (h - 0.536)))
    sub26 = [r for r in priced if r["lam"] <= 0.56]
    h = sum(r["y"] for r in sub26) / len(sub26)
    print("  %-8s%8d%10.1f%10.1f%+11.1fpp  <- live, real prices, UNFILTERED by price"
          % ("2026live", len(sub26), 100 * sum(r["y"] for r in priced) / len(priced),
             100 * h, 100 * (h - 0.536)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

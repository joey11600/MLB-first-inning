#!/usr/bin/env python3
"""
Selection-aware null for the game-hour wind_out result (wx_gamehour.py).

Observed: +gh_wind_out improves test logloss in ALL THREE splits (means
+0.246 / +0.067 / +0.132 x1000) while the crosswind placebo hurts in all
three.  This script asks: how often does a wind column with NO information
-- gh_wind_out shuffled WITHIN PARK, preserving each park's wind climate --
go ALL+ too, and how often does its mean-across-splits match the observed?

Within-park shuffling matters: the 2026-08-20 test showed the naive null
mean is not zero (windy parks differ in other ways), so an unconditional
shuffle would flatter the candidate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (T1_SHIPPED, B1_SHIPPED, build_park, fit_lr, load,  # noqa: E402
                     logloss, matrix, predict)
from wx_gamehour import _attach  # noqa: E402
from backtest import PARK_ORIENTATION_CF  # noqa: E402

L2 = 0.50
TRIALS = 120
SEED = 20260823


def main() -> int:
    rng = np.random.default_rng(SEED)
    fi = pd.read_csv(ROOT / "data" / "candidates" / "factor_fi_pooled.csv")
    gh = pd.read_csv(ROOT / "data" / "candidates" / "factor_wx_gamehour.csv")
    bt = ROOT / "data" / "backtests"
    d24 = _attach(_attach(load(bt / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "home", 2024), fi), gh)
    d25 = _attach(_attach(load(bt / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "home", 2025), fi), gh)
    d26 = _attach(_attach(load(ROOT / "data" / "picks_2026.csv", "home_team", 2026), fi), gh)

    T1 = T1_SHIPPED + ["home_fi_xwoba"]; B1 = B1_SHIPPED + ["away_fi_xwoba"]
    for d in (d24, d25, d26):
        m = (pd.to_numeric(d.wx_is_dome, errors="coerce").fillna(0) == 0) \
            & d.gh_wind_deg.notna() & d.park.isin(PARK_ORIENTATION_CF)
        ang = np.radians(pd.to_numeric(d.gh_wind_deg, errors="coerce")
                         - d.park.map(PARK_ORIENTATION_CF))
        spd = pd.to_numeric(d.gh_wind_kmh, errors="coerce")
        d["gh_wind_out"] = np.where(m, -np.cos(ang) * spd, np.nan)
        for c in ("home_fi_xwoba", "away_fi_xwoba"):
            d[c] = d[c].fillna(pd.concat([d24, d25])[c].mean() if c in d else np.nan)

    defs = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
            ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]

    def run_split(tr, te, extra_col):
        """dlogloss x1000 (mean over test rows) of base -> base+extra."""
        t1f = T1 + ([extra_col] if extra_col else [])
        b1f = B1 + ([extra_col] if extra_col else [])
        tr = tr.copy(); te = te.copy()
        if extra_col:
            tr[extra_col] = tr[extra_col].fillna(0.0); te[extra_col] = te[extra_col].fillna(0.0)
        pk, b0 = build_park(tr, 50)
        wt, mt, st = fit_lr(matrix(tr, t1f, pk, b0), tr.y_t1.values, L2)
        wb, mb, sb = fit_lr(matrix(tr, b1f, pk, b0), tr.y_b1.values, L2)
        p = 1 - (1 - predict(wt, mt, st, matrix(te, t1f, pk, b0))) * \
                (1 - predict(wb, mb, sb, matrix(te, b1f, pk, b0)))
        y = te.y.values
        return p, y

    # base predictions once per split
    base = {}
    for lab, tr, te in defs:
        p0, y = run_split(tr, te, None)
        base[lab] = (np.array([logloss(yy, pp) for yy, pp in zip(y, p0)]), y)

    def deltas(col_name):
        out = []
        for lab, tr, te in defs:
            p1, y = run_split(tr, te, col_name)
            ll0, _ = base[lab]
            ll1 = np.array([logloss(yy, pp) for yy, pp in zip(y, p1)])
            out.append(float((ll0 - ll1).mean() * 1000))
        return out

    obs = deltas("gh_wind_out")
    print(f"observed +wind_out dll x1000 per split: "
          + " ".join(f"{v:+.3f}" for v in obs) + f"   mean {np.mean(obs):+.3f}  ALL+ {all(v > 0 for v in obs)}")

    allplus, meanbeat = 0, 0
    for t in range(TRIALS):
        for d in (d24, d25, d26):
            v = d["gh_wind_out"].copy()
            for park, idx in d.groupby("park").groups.items():
                vals = v.loc[idx].values
                v.loc[idx] = vals[rng.permutation(len(vals))]
            d["gh_wind_null"] = v
        # the concat frame must be rebuilt to carry the fresh null column
        global_defs = [("2024->2025", d24, d25), ("2025->2024", d25, d24),
                       ("24+25->2026", pd.concat([d24, d25], ignore_index=True), d26)]
        dl = []
        for lab, tr, te in global_defs:
            p1, y = run_split(tr, te, "gh_wind_null")
            ll0, _ = base[lab]
            ll1 = np.array([logloss(yy, pp) for yy, pp in zip(y, p1)])
            dl.append(float((ll0 - ll1).mean() * 1000))
        allplus += all(v > 0 for v in dl)
        meanbeat += np.mean(dl) >= np.mean(obs)
        if (t + 1) % 20 == 0:
            print(f"  trial {t+1}/{TRIALS}: P(ALL+) so far {allplus/(t+1):.3f}, "
                  f"P(mean >= obs) {meanbeat/(t+1):.3f}")
    print(f"\nNULL ({TRIALS} within-park shuffles): "
          f"P(ALL+ by chance) = {allplus/TRIALS:.3f}   "
          f"P(null mean >= observed mean) = {meanbeat/TRIALS:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

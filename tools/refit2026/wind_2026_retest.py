#!/usr/bin/env python3
"""
Wind direction, re-tested on data the first test never touched: 2026 live.

The 2026-08-20 test (memory: wind_direction_dead) refuted wind direction on
the 2024/2025 backtests -- the crosswind placebo matched the out-component
everywhere.  The operator has reaffirmed the idea ("I think that one
direction might"), and there IS a genuinely new angle: the live predictor
never records wind direction, so 2026 -- the season we actually bet -- was
never tested.  This backfills wind_deg for every graded 2026 game from the
open-meteo archive (same source and 7pm-local convention as the 2024/25
backfill, so results are comparable) and asks:

  1. League-wide 2026: does the out-to-center component predict YRFI?
     Does each DIRECTION (out / in / to-left / to-right) differ?
  2. The BET population: on our STRONG YRFI bets, did wind help or hurt?
  3. The No.1 plays: does wind direction separate the winners from the
     losers -- including the current losing window vs the win window?

The crosswind placebo rides along at every step.  Crosswind has no
mechanism to create runs; if it "works" as well as the out-component, the
out-component's signal is confounding, not physics.  That placebo is what
killed the 2024/25 version.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import backtest  # noqa: E402  (PARK_ORIENTATION_CF, DOMED_PARKS, wind fns, weather cache)


def load_2026() -> pd.DataFrame:
    d = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d.fi_total_runs.notna()].copy()
    d["y"] = (d.fi_total_runs > 0).astype(int)
    return d


def attach_wind(d: pd.DataFrame) -> pd.DataFrame:
    wx = {}
    for pk in sorted(d.home_team.dropna().unique()):
        wx[pk] = backtest.fetch_weather_season(pk, 2026)
    rows = []
    for _, r in d.iterrows():
        pk = r.home_team
        day = r.date.strftime("%Y-%m-%d")
        w = wx.get(pk, {}).get(day)
        if w is None:
            rows.append((np.nan, np.nan, np.nan, np.nan))
            continue
        spd, deg = w.get("wind_kmh"), w.get("wind_deg")
        rows.append((spd, deg,
                     backtest.wind_out_kmh(spd, deg, pk),
                     backtest.wind_cross_kmh(spd, deg, pk)))
    d[["w_spd", "w_deg", "w_out", "w_cross"]] = pd.DataFrame(rows, index=d.index)
    d["dome"] = d.home_team.isin(backtest.DOMED_PARKS)
    return d


def bucket_direction(d: pd.DataFrame) -> pd.Series:
    """Quadrant of the wind relative to the park's center-field bearing.
    'out' = blowing toward CF, 'in' = toward the plate, else left/right."""
    ang = np.degrees(np.arctan2(d.w_cross, d.w_out))  # 0 = pure out
    lab = pd.Series(pd.NA, index=d.index, dtype="object")
    m = d.w_out.notna() & (d.w_spd >= 6) & ~d.dome     # need real wind + outdoors
    a = ang[m].abs()
    lab.loc[m & (a <= 45)] = "OUT to CF"
    lab.loc[m & (a >= 135)] = "IN from CF"
    lab.loc[m & (a > 45) & (a < 135) & (ang > 0)] = "to RIGHT field"
    lab.loc[m & (a > 45) & (a < 135) & (ang < 0)] = "to LEFT field"
    return lab


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--perm", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    d = attach_wind(load_2026())
    out = d[~d.dome & d.w_out.notna()].copy()
    print(f"2026 graded games: {len(d)}   outdoor with archive wind: {len(out)}   "
          f"(coverage {len(out)/max((~d.dome).sum(),1)*100:.0f}% of outdoor)")

    print("\n" + "=" * 96)
    print("1) LEAGUE-WIDE 2026")
    print(f"   corr(wind OUT component, YRFI)   = {out.w_out.corr(out.y):+.4f}   n={len(out)}")
    print(f"   corr(CROSSWIND placebo,  YRFI)   = {out.w_cross.corr(out.y):+.4f}")
    out["dir"] = bucket_direction(out)
    t = out.dropna(subset=["dir"]).groupby("dir").agg(
        n=("y", "size"), yrfi=("y", "mean"), fi_runs=("fi_total_runs", "mean"))
    t["se"] = np.sqrt(t.yrfi * (1 - t.yrfi) / t.n)
    print("\n   by DIRECTION (outdoor, wind >= 6 km/h):")
    print(t.round(3).to_string())
    strong = out[out.w_out.abs() >= 12]
    hi, lo = strong[strong.w_out > 0], strong[strong.w_out < 0]
    if len(hi) > 20 and len(lo) > 20:
        diff = hi.y.mean() - lo.y.mean()
        se = np.sqrt(hi.y.var()/len(hi) + lo.y.var()/len(lo))
        print(f"\n   strong wind (|out| >= 12): OUT n={len(hi)} yrfi={hi.y.mean():.3f}  "
              f"vs IN n={len(lo)} yrfi={lo.y.mean():.3f}   diff={diff:+.3f} (z={diff/se:+.2f})")

    print("\n" + "=" * 96)
    print("2) THE BET POPULATION (STRONG, bet_placed=Y -- these are YRFI bets, so")
    print("   wind OUT should HELP wins if the effect is real)")
    b = out[(out.pick_strength == "STRONG") & (out.bet_placed == "Y")].copy()
    b["won"] = np.where(b.pick_side == "YRFI", b.y == 1, b.y == 0).astype(int)
    print(f"   outdoor bets with wind: n={len(b)}   hit={b.won.mean():.3f}")
    for col, lab in [("w_out", "wind OUT"), ("w_cross", "crosswind placebo")]:
        w1 = b[b[col] > 6]; w0 = b[b[col] < -6]
        print(f"   {lab:18s}: favorable n={len(w1):3d} hit={w1.won.mean() if len(w1) else float('nan'):.3f}   "
              f"unfavorable n={len(w0):3d} hit={w0.won.mean() if len(w0) else float('nan'):.3f}   "
              f"corr(win,{col})={b[col].corr(b.won):+.4f}")
    # permutation on the bet-pop correlation
    obs = b.w_out.corr(b.won)
    null = [abs(pd.Series(b.w_out.values[rng.permutation(len(b))]).corr(pd.Series(b.won.values)))
            for _ in range(args.perm)]
    print(f"   permutation (two-sided) on corr(win, wind_out): observed {obs:+.4f}, "
          f"p = {(np.array(null) >= abs(obs)).mean():.3f}")

    print("\n" + "=" * 96)
    print("3) THE No.1 PLAYS")
    b["rank_val"] = np.where(b.pick_side == "YRFI", b.nrfi_prob, 1 - b.nrfi_prob)
    o = pd.to_numeric(np.where(b.pick_side == "YRFI", b.market_yrfi_odds,
                               b.market_nrfi_odds), errors="coerce")
    b["impl"] = np.where(o < 0, -o/(-o+100), 100/(o+100)); b["impl"] = b["impl"].fillna(1.0)
    b["gname"] = b.away_team.astype(str) + "@" + b.home_team.astype(str)
    b = b.sort_values(["date", "rank_val", "impl", "gname"])
    b["slot"] = b.groupby("date").cumcount() + 1
    n1 = b[b.slot == 1]
    print(f"   outdoor No.1s with wind: n={len(n1)}  "
          f"({int(n1.won.sum())}W-{int((1-n1.won).sum())}L)")
    print(f"   winners : mean wind_out {n1[n1.won==1].w_out.mean():+.2f} km/h   "
          f"crosswind {n1[n1.won==1].w_cross.mean():+.2f}")
    print(f"   losers  : mean wind_out {n1[n1.won==0].w_out.mean():+.2f} km/h   "
          f"crosswind {n1[n1.won==0].w_cross.mean():+.2f}")
    win_w = n1[(n1.date >= "2026-08-01") & (n1.date <= "2026-08-13")]
    los_w = n1[n1.date >= "2026-08-14"]
    if len(win_w) and len(los_w):
        print(f"   WIN window  mean wind_out {win_w.w_out.mean():+.2f}  "
              f"({int(win_w.won.sum())}W-{int((1-win_w.won).sum())}L)")
        print(f"   LOSS window mean wind_out {los_w.w_out.mean():+.2f}  "
              f"({int(los_w.won.sum())}W-{int((1-los_w.won).sum())}L)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
No.1-play autopsy: what separates the winning No.1s from the losing ones?

Asked for directly by the operator 2026-08-21: compare the current losing
streak's No.1 plays against the win streak two weeks prior, then against all
No.1s season-long, and identify the driving factors.

DISCIPLINE.  A wins-vs-losses comparison conditions on the OUTCOME, which is
the reverse of prediction, and with ~30 features on ~100 games the best
split found will look impressive by construction.  So this script:
  1. prints the raw streak-window comparison the operator asked for (facts),
  2. compares W vs L across every feature season-long with a bootstrap CI,
  3. runs the SELECTION-AWARE null (feature_test_methodology): shuffle W/L
     labels, rerun the WHOLE feature sweep, keep the best |effect| each time
     -- a feature only counts if it beats the best the sweep finds in noise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

NUMERIC = [
    # model + market view
    "nrfi_prob", "yrfi_prob", "lambda_lr_total", "combined_lambda",
    "implied_yrfi_prob", "edge_on_pick", "units_risked",
    # pitchers
    "home_era", "away_era", "home_fip", "away_fip", "home_whip", "away_whip",
    "home_xera", "away_xera", "home_k9", "away_k9", "home_bb9", "away_bb9",
    "home_hr9", "away_hr9", "home_whiff_pct_rank", "away_whiff_pct_rank",
    "home_avg_ip_per_start", "away_avg_ip_per_start",
    "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
    "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
    # offenses
    "home_obp", "away_obp", "home_slg", "away_slg", "home_rpg", "away_rpg",
    "home_top3c_obp", "away_top3c_obp", "home_top3c_slg", "away_top3c_slg",
    "home_top3c_iso", "away_top3c_iso",
    "home_top3_ops_vs_oppHand", "away_top3_ops_vs_oppHand",
    # environment
    "park_factor", "wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome",
    "home_plate_ump_nrfi_rate",
]


def implied(o):
    o = pd.to_numeric(o, errors="coerce")
    return np.where(o < 0, -o / (-o + 100.0), 100.0 / (o + 100.0))


def load_no1() -> pd.DataFrame:
    d = pd.read_csv(ROOT / "data" / "picks_2026.csv", low_memory=False)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d.fi_total_runs.notna()].copy()
    d["yrfi"] = (d.fi_total_runs > 0).astype(int)
    b = d[(d.pick_strength == "STRONG") & (d.bet_placed == "Y")].copy()
    b["won"] = np.where(b.pick_side == "YRFI", b.yrfi == 1, b.yrfi == 0).astype(int)
    # mirror top-pick-rank: confidence, then better price, then game name
    b["rank_val"] = np.where(b.pick_side == "YRFI", b.nrfi_prob, 1 - b.nrfi_prob)
    b["impl"] = implied(np.where(b.pick_side == "YRFI",
                                 b.market_yrfi_odds, b.market_nrfi_odds))
    b["impl"] = b["impl"].fillna(1.0)
    b["gname"] = b.away_team.astype(str) + "@" + b.home_team.astype(str)
    b = b.sort_values(["date", "rank_val", "impl", "gname"])
    b["slot"] = b.groupby("date").cumcount() + 1
    n1 = b[b.slot == 1].copy().sort_values("date").reset_index(drop=True)
    for c in NUMERIC:
        if c in n1.columns:
            n1[c] = pd.to_numeric(n1[c], errors="coerce")
    return n1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--perm", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    n1 = load_no1()
    print(f"No.1 plays reconstructed: {len(n1)}  "
          f"({n1.date.min().date()} .. {n1.date.max().date()})   "
          f"record {int(n1.won.sum())}W-{int((1-n1.won).sum())}L "
          f"({n1.won.mean():.3f})")

    # ---------- the two windows the operator is talking about ----------
    WIN_LO, WIN_HI = "2026-08-01", "2026-08-13"
    LOSE_LO = "2026-08-14"
    win = n1[(n1.date >= WIN_LO) & (n1.date <= WIN_HI)]
    lose = n1[n1.date >= LOSE_LO]
    print("\n" + "=" * 100)
    print(f"THE TWO WINDOWS   win-streak {WIN_LO}..{WIN_HI}  vs  losing {LOSE_LO}..now")
    cols = ["date", "gname", "pick_side", "rank_val", "impl", "units_risked",
            "park_factor", "wx_temp_c", "home_era", "away_era", "won"]
    for lab, s in [("WIN WINDOW", win), ("LOSS WINDOW", lose)]:
        z = s[cols].copy()
        z["conf"] = (1 - z.rank_val).round(3)
        z["date"] = z.date.dt.date
        print(f"\n  {lab}  ({int(s.won.sum())}W-{int((1-s.won).sum())}L)")
        print(z.drop(columns=["rank_val"]).to_string(index=False))

    print("\n  window means (the operator's direct question):")
    rows = []
    for c in ["rank_val", "impl", "units_risked", "park_factor", "wx_temp_c",
              "home_era", "away_era", "home_fip", "away_fip",
              "home_rpg", "away_rpg", "home_top3c_slg", "away_top3c_slg"]:
        if c in n1.columns:
            rows.append((c, win[c].mean(), lose[c].mean()))
    t = pd.DataFrame(rows, columns=["feature", "win_window", "loss_window"])
    t["diff"] = t.win_window - t.loss_window
    print(t.round(4).to_string(index=False))
    print(f"\n  parks, win window : {sorted(win.home_team.tolist())}")
    print(f"  parks, loss window: {sorted(lose.home_team.tolist())}")

    # ---------- season-long W vs L sweep with honest null ----------
    print("\n" + "=" * 100)
    print("SEASON-LONG: every feature, winners vs losers, standardized effect size")
    y = n1.won.values
    feats = [c for c in NUMERIC if c in n1.columns and n1[c].notna().sum() > 60
             and n1[c].std() > 0]
    def sweep(labels):
        out = {}
        for c in feats:
            v = n1[c].values
            m = ~np.isnan(v)
            if m.sum() < 60:
                continue
            w, l = v[m][labels[m] == 1], v[m][labels[m] == 0]
            if len(w) < 10 or len(l) < 10:
                continue
            sd = v[m].std()
            out[c] = (w.mean() - l.mean()) / sd if sd > 0 else 0.0
        return out
    obs = sweep(y)
    ranked = sorted(obs.items(), key=lambda kv: -abs(kv[1]))
    print(f"  {'feature':<30} {'effect (SD units)':>18}   (positive = higher in WINNERS)")
    for c, e in ranked[:12]:
        print(f"  {c:<30} {e:>+18.3f}")

    best_feat, best_eff = ranked[0]
    null_best = []
    for _ in range(args.perm):
        null_best.append(max(abs(v) for v in sweep(y[rng.permutation(len(y))]).values()))
    null_best = np.array(null_best)
    p = (null_best >= abs(best_eff)).mean()
    print(f"\n  SELECTION-AWARE NULL over {len(feats)} features, {args.perm} shuffles:")
    print(f"    best |effect| in NOISE: mean {null_best.mean():.3f}  "
          f"90th pct {np.percentile(null_best, 90):.3f}")
    print(f"    observed best ({best_feat}) = {abs(best_eff):.3f}  ->  p = {p:.3f}")
    print("    " + ("PASSES the search-aware bar." if p < 0.05 else
                    "FAILS -- a sweep this wide finds a bigger split in shuffled labels."))

    # ---------- park mix, the visible structural difference ----------
    print("\n" + "=" * 100)
    print("PARK MIX of No.1 plays: win rate by home park (n>=5)")
    pk = n1.groupby("home_team").agg(n=("won", "size"), hit=("won", "mean"),
                                     pnl=("profit_loss_units", "sum"))
    print(pk[pk.n >= 5].sort_values("hit", ascending=False).round(3).to_string())

    # streak math, stated once, honestly
    ph = n1.won.mean()
    print("\n" + "=" * 100)
    print("STREAK ARITHMETIC (context, not a conclusion)")
    print(f"  at the season-long No.1 hit rate of {ph:.3f}, over {len(n1)} slates:")
    sim_max_w, sim_max_l = [], []
    for _ in range(3000):
        s = rng.random(len(n1)) < ph
        runs_w = runs_l = cw = cl = 0
        for v in s:
            cw = cw + 1 if v else 0
            cl = cl + 1 if not v else 0
            runs_w, runs_l = max(runs_w, cw), max(runs_l, cl)
        sim_max_w.append(runs_w); sim_max_l.append(runs_l)
    print(f"  expected LONGEST win streak  : median {int(np.median(sim_max_w))}  "
          f"(90% of seasons reach >= {int(np.percentile(sim_max_w, 10))})")
    print(f"  expected LONGEST loss streak : median {int(np.median(sim_max_l))}  "
          f"(90% of seasons reach >= {int(np.percentile(sim_max_l, 10))})")
    s = "".join("W" if v else "L" for v in n1.won.values)
    def longest(s, ch):
        best = cur = 0
        for c in s:
            cur = cur + 1 if c == ch else 0
            best = max(best, cur)
        return best
    print(f"  actual longest: {longest(s,'W')}W and {longest(s,'L')}L    sequence tail: ...{s[-15:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

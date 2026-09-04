#!/usr/bin/env python3
"""build_fi_form.py -- a properly shrunk, continuous first-inning form rate.

WHY (operator, 2026-09-03).  The shipped `*_p_last5/last10_pitcher_nrfi` inputs
are the fraction of a starter's last 5 / 10 starts in which HE allowed no run in
his own half of the first.  They are raw fractions with no shrinkage, so on 2026
last-5 takes ten distinct values with 21-23% of games sitting at exactly 1.000,
and last-10 takes 26.  `underweight_test.py` found the model gives them a weight
whose SIGN flips by season -- the signature of an input carrying more noise than
signal.  This builds the empirical-Bayes version of the same quantity.

THE MEASUREMENT THAT SETS THE SHRINKAGE (2026-09-03).  Unlike the umpire rate --
which was flattened because tau^2 <= 0 on every season pair -- first-inning
no-run rate HAS real between-pitcher variance, and it is stable:

    season   observed var   binomial-noise var   tau^2      implied K
    2024        0.01416          0.01130        +0.00286    68.2 starts
    2025        0.01523          0.01200        +0.00323    63.4 starts
    2026        0.01555          0.01260        +0.00295    69.7 starts

K ~ 65 STARTS is the headline.  A starter makes ~30 starts a year, so a 10-start
sample deserves 10/(10+65) = 13% weight on itself and 87% on the league mean.
The shipped feature gives it 100%.  That is the defect, and it is the opposite
of "the model under-weights this": the INPUT is ~87% noise, and a fitted weight
near zero is the model responding correctly to it.  Shrinking the input is what
lets a real weight be carried.

Within-season split-half reliability (Spearman-Brown) is +0.37 / +0.16 / +0.22;
cross-season carryover is weak and inconsistent (+0.08, +0.21, -0.14), so the
prior-season weight is swept and expected to want to be LOW -- unlike fi_xwoba,
where pooling across seasons was the whole point.

POINT-IN-TIME BY CONSTRUCTION.  A pitcher's estimate for a game on date D uses
only his starts strictly before D, and the league mean it shrinks toward is the
expanding league rate over all starts strictly before D.  Nothing in the file
can see its own game or any later one, on any split.

Writes only to data/candidates/.  No model artifact is touched.

CLI
    python tools/refit2026/build_fi_form.py            # write the candidate grid
    python tools/refit2026/build_fi_form.py --verify   # reproduce the shipped last-10 column
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BT = ROOT / "data" / "backtests"
OUT = ROOT / "data" / "candidates"
F24 = BT / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv"
F25 = BT / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv"
F26 = ROOT / "data" / "picks_2026.csv"

LEAGUE_SEED = 0.7129     # overall clean-first-inning rate; used only as a prior seed


def start_log() -> pd.DataFrame:
    """One row per (starter, game): did HE allow a run in his own half of the 1st?

    The home starter works the TOP of the first, so the away team's first-inning
    runs are his; the away starter works the BOTTOM.  Keyed on pitcher NAME
    because the 2024/2025 backtest files carry no pitcher id (only 5 of 355
    names in 2026 map to more than one player id, all September call-ups).
    """
    frames = []
    for path, season in ((F24, 2024), (F25, 2025), (F26, 2026)):
        d = pd.read_csv(path, low_memory=False)
        d = d[d["fi_total_runs"].notna()].copy()
        d["date"] = pd.to_datetime(d["date"])
        gp = d["game_pk"] if "game_pk" in d.columns else pd.Series(np.nan, index=d.index)
        for who, runs_col, side in (("home_pitcher", "fi_away_runs", "home"),
                                    ("away_pitcher", "fi_home_runs", "away")):
            frames.append(pd.DataFrame({
                "pid":     d[who].astype(str).str.strip(),
                "date":    d["date"],
                "season":  season,
                "game_pk": pd.to_numeric(gp, errors="coerce"),
                "side":    side,
                "clean":   (pd.to_numeric(d[runs_col], errors="coerce") == 0).astype(float),
            }))
    s = pd.concat(frames, ignore_index=True)
    s = s[s.pid.notna() & (s.pid != "") & (s.pid.str.lower() != "nan") & (s.pid != "TBD")]
    s = s[s.clean.notna()]
    return s.sort_values(["date", "pid"]).reset_index(drop=True)


def estimates(starts: pd.DataFrame, K: float, prior_w: float,
              window: int | None = None, halflife: float | None = None) -> np.ndarray:
    """Point-in-time shrunk clean-first-inning rate, positionally aligned to `starts`.

        est = (sum w_i * clean_i + K * mu_before) / (sum w_i + K)

    `window` keeps only the most recent N starts (the shipped shape, for
    comparison); `halflife` decays older starts continuously; neither = all
    prior starts weighted equally.  `prior_w` multiplies a pitcher's carried
    accumulators when a new season begins.
    """
    decay = 0.5 ** (1.0 / halflife) if halflife else 1.0

    day = starts.groupby("date").clean.agg(["size", "sum"]).sort_index()
    cum_n = day["size"].cumsum().shift(1).fillna(0.0)
    cum_s = day["sum"].cumsum().shift(1).fillna(0.0)
    mu_by_date = ((cum_s + 50.0 * LEAGUE_SEED) / (cum_n + 50.0)).to_dict()

    pos_of = {ix: p for p, ix in enumerate(starts.index)}
    out = np.full(len(starts), np.nan)
    dates = starts["date"].to_dict()
    seasons = starts["season"].to_dict()
    cleans = starts["clean"].to_dict()

    for _pid, grp in starts.groupby("pid", sort=False):
        n_acc = s_acc = 0.0
        season_seen = None
        hist: list[float] = []
        for i in grp.index:
            if season_seen is not None and seasons[i] != season_seen:
                n_acc *= prior_w
                s_acc *= prior_w
                # A hard window DOES cross the season boundary, because the live
                # feature does: pitcher_last_n_first_inning pools the current and
                # prior season game logs and then takes the most recent n.
            season_seen = seasons[i]
            mu = mu_by_date.get(dates[i], LEAGUE_SEED)
            if window:
                h = hist[-window:]
                n_eff = float(len(h)) + n_acc
                s_eff = float(sum(h)) + s_acc
            else:
                n_eff, s_eff = n_acc, s_acc
            out[pos_of[i]] = (s_eff + K * mu) / (n_eff + K) if (n_eff + K) > 0 else mu
            # fold this start in only AFTER emitting, so the estimate is strictly prior
            if window:
                hist.append(float(cleans[i]))
            else:
                n_acc = n_acc * decay + 1.0
                s_acc = s_acc * decay + float(cleans[i])
    return out


# Pre-committed grid.  Kept small on purpose: every cell is a search, and
# test_fi_form.py prices the whole grid against a selection-aware null.
GRID = [
    ("K20_all",      dict(K=20.0,  prior_w=0.3)),
    ("K40_all",      dict(K=40.0,  prior_w=0.3)),
    ("K65_all",      dict(K=65.0,  prior_w=0.3)),   # the empirical-Bayes value
    ("K100_all",     dict(K=100.0, prior_w=0.3)),
    ("K65_pw0",      dict(K=65.0,  prior_w=0.0)),
    ("K65_pw6",      dict(K=65.0,  prior_w=0.6)),
    ("K65_hl15",     dict(K=65.0,  prior_w=0.3, halflife=15.0)),
    ("K65_hl30",     dict(K=65.0,  prior_w=0.3, halflife=30.0)),
    ("K65_win20",    dict(K=65.0,  prior_w=0.3, window=20)),
    ("shipped_like", dict(K=0.0,   prior_w=1.0, window=10)),   # reproduces the live feature
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="check the shipped-like config against the live last-10 column")
    ap.add_argument("--audit", action="store_true",
                    help="independent leakage audit: recompute sampled rows from raw starts")
    args = ap.parse_args()

    st = start_log()

    if args.audit:
        # Recompute a sample of estimates from the raw start log by brute force,
        # using ONLY starts strictly before the game's date, and compare.  An
        # off-by-one that let a pitcher see his own game would show up here.
        K = 65.0
        st = st.copy()
        day = st.groupby("date").clean.agg(["size", "sum"]).sort_index()
        cum_n = day["size"].cumsum().shift(1).fillna(0.0)
        cum_s = day["sum"].cumsum().shift(1).fillna(0.0)
        mu_by_date = ((cum_s + 50.0 * LEAGUE_SEED) / (cum_n + 50.0)).to_dict()

        # prior_w = 1.0 removes the season-rollover weighting entirely, so the
        # expected value is a plain shrunk mean over EVERY strictly-earlier start.
        # That isolates the only question an audit needs to answer: can a row see
        # its own game, or any later one?
        st["est"] = estimates(st, K=K, prior_w=1.0)
        sample = st.sample(n=min(500, len(st)), random_state=7)
        worst = 0.0
        for _, r in sample.iterrows():
            prior = st[(st.pid == r.pid) & (st.date < r.date)]
            mu = mu_by_date.get(r.date, LEAGUE_SEED)
            expect = (prior.clean.sum() + K * mu) / (len(prior) + K)
            worst = max(worst, abs(expect - r.est))
        ok = worst < 1e-9
        print(f"\nLEAKAGE AUDIT (K={K}, prior_w=1.0): {len(sample)} rows recomputed independently")
        print(f"  worst |brute-force recompute - builder| = {worst:.2e}")
        print("  " + ("PASS: every estimate uses strictly-earlier starts only."
                      if ok else "FAIL: the builder does not match its own definition."))

        # The blunt check, on the config actually tested.  A feature that could see
        # its own game would correlate far more strongly with THIS start's outcome
        # than with the next one; a legitimate one correlates about equally with both.
        st["est65"] = estimates(st, K=K, prior_w=0.3)
        s2 = st.sort_values(["pid", "date"]).copy()
        s2["next_clean"] = s2.groupby("pid").clean.shift(-1)
        cur = s2.est65.corr(s2.clean)
        nxt = s2.dropna(subset=["next_clean"]).est65.corr(s2.dropna(subset=["next_clean"]).next_clean)
        print(f"  corr(estimate, THIS start's outcome) = {cur:+.4f}")
        print(f"  corr(estimate, the NEXT start's outcome) = {nxt:+.4f}")
        print("  A leaking feature shows a much larger first number; these should be similar,")
        print("  because both are just 'good pitchers keep the first inning clean more often'.")
        return 0 if ok else 1
    print(f"per-start first-inning log: {len(st)} rows, {st.pid.nunique()} starters, "
          f"seasons {sorted(st.season.unique())}, league clean rate {st.clean.mean():.4f}")

    if args.verify:
        st = st.copy()
        st["mine"] = estimates(st, K=0.0, prior_w=1.0, window=10)
        st["nprior"] = st.groupby("pid").cumcount()
        led = pd.read_csv(F26, low_memory=False)
        led = led[led.fi_total_runs.notna()]
        ref = pd.concat([
            pd.DataFrame({"game_pk": pd.to_numeric(led.game_pk, errors="coerce"), "side": "home",
                          "live": pd.to_numeric(led.home_p_last10_pitcher_nrfi, errors="coerce")}),
            pd.DataFrame({"game_pk": pd.to_numeric(led.game_pk, errors="coerce"), "side": "away",
                          "live": pd.to_numeric(led.away_p_last10_pitcher_nrfi, errors="coerce")}),
        ])
        m = (st[st.season == 2026]
             .merge(ref, on=["game_pk", "side"], how="inner")
             .dropna(subset=["live", "mine"]))
        deep = m[m.nprior >= 12]
        print("\nVERIFY vs the live last-10 column, 2026:")
        print(f"  all comparable rows       n={len(m):5d}  corr={m.mine.corr(m.live):+.3f}  "
              f"mean|diff|={np.abs(m.mine - m.live).mean():.4f}")
        print(f"  pitchers with >=12 prior  n={len(deep):5d}  corr={deep.mine.corr(deep.live):+.3f}  "
              f"mean|diff|={np.abs(deep.mine - deep.live).mean():.4f}")
        print("  (a perfect match is not expected: production also reaches back into the")
        print("   prior season and into games before this file's first date.)")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    wide = st[["game_pk", "side", "date", "season", "pid"]].copy()
    for name, kw in GRID:
        v = pd.Series(estimates(st, **kw), index=st.index)
        wide[name] = v
        print(f"  {name:<14} K={kw.get('K'):>6}  prior_w={kw.get('prior_w')}  "
              f"win={kw.get('window')}  hl={kw.get('halflife')}   "
              f"sd={v.std():.4f}  distinct={v.round(6).nunique():>5}  "
              f"range [{v.min():.3f}, {v.max():.3f}]")
    names = [n for n, _ in GRID]
    home = wide[wide.side == "home"].set_index("game_pk")[names].add_prefix("home_")
    away = wide[wide.side == "away"].set_index("game_pk")[names].add_prefix("away_")
    out = home.join(away, how="outer").reset_index()
    out = out[out.game_pk.notna()]
    path = OUT / "factor_fi_form.csv"
    out.to_csv(path, index=False)
    print(f"\nwrote {path}  ({len(out)} games x {len(GRID)} configs x 2 sides)")
    print("next: python tools/refit2026/test_fi_form.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

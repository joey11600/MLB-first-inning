#!/usr/bin/env python3
"""
tools/test_umpire_persistence.py -- is the umpire NRFI-rate feature
measuring a real, persistent umpire trait, or is it fitting noise?

WHY (2026-07-27 deep-research open question)
--------------------------------------------
The production feature `home_plate_ump_nrfi_rate` comes from
`data/umpire_rates.json`, whose own `training_corpus` field says it was
built from the **2022 and 2023** backtests.  Three problems stack up:

  1. CLAUDE.md forbids training on 2022/2023 ("pre-pitch-clock
     distribution shift makes those seasons hurt the model") -- yet this
     feature is derived from exactly those seasons.
  2. A per-umpire NRFI RATE is an outcome aggregate.  It silently
     absorbs whichever pitchers, lineups and parks that umpire happened
     to be assigned.  Sports Info Solutions built Strike Zone Runs Saved
     specifically to strip that confound out by modelling called-strike-
     above-EXPECTED instead.
  3. Using a prior-season umpire value as a feature presupposes that
     umpire zone tendency PERSISTS across seasons.  The 2026-07-27
     research sweep could not establish persistence (the claim was voted
     down 0-3 by adversarial verifiers).

Persistence is the precondition for the whole feature.  This script
tests it directly on the repo's own data: correlate each umpire's
stored 2022-23 shrunk NRFI rate against their ACTUAL 2026 first-inning
no-run rate.

  - If the correlation is meaningfully positive, the feature tracks a
    real trait and is worth re-encoding properly.
  - If it is ~0, the feature is noise and should be ablated.

Also reports the model's fitted weight and the train/serve scale skew,
since those bear on how much damage a noise feature is doing.

Usage:
    python tools/test_umpire_persistence.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MIN_GAMES_HIST = 15   # umpire must have this many 2022-23 games to be scored
MIN_GAMES_2026 = 8    # ...and this many 2026 games


def spearman(a, b):
    """Rank correlation without a scipy dependency."""
    a, b = np.asarray(a, float), np.asarray(b, float)

    def rank(x):
        order = np.argsort(x)
        r = np.empty(len(x), float)
        r[order] = np.arange(1, len(x) + 1)
        _, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
        s = np.zeros(len(cnt))
        np.add.at(s, inv, r)
        return (s / cnt)[inv]

    ra, rb = rank(a), rank(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def boot_ci(x, y, n=4000, seed=11):
    rng = np.random.default_rng(seed)
    idx = np.arange(len(x))
    out = []
    for _ in range(n):
        s = rng.choice(idx, len(idx), replace=True)
        if len(np.unique(np.asarray(x)[s])) < 3:
            continue
        out.append(np.corrcoef(np.asarray(x)[s], np.asarray(y)[s])[0, 1])
    out = np.sort(out)
    return float(out[int(0.05 * len(out))]), float(out[int(0.95 * len(out))])


def main():
    rates = json.load(open(ROOT / "data" / "umpire_rates.json", encoding="utf-8"))
    print("Feature source: data/umpire_rates.json")
    print(f"  training_corpus : {rates.get('training_corpus')}")
    print(f"  league NRFI rate: {rates.get('league_nrfi_rate')}")
    print(f"  umpires stored  : {len(rates.get('umpires', {}))}")
    print("  NOTE: CLAUDE.md bans 2022/2023 from model training "
          "(pre-pitch-clock shift).\n")

    ump = rates["umpires"]

    # 2026-08-29: this script's verdict ("NO PERSISTENCE ... ABLATE") was
    # acted on.  rebuild_umpire_rates.py measured tau^2 <= 0 on every
    # season pair and flattened the file -- every shrunk_nrfi is now the
    # league rate, so there is no per-ump spread left to correlate and the
    # bootstrap below would divide by zero variance.  Exit cleanly; the
    # test becomes meaningful again only if someone reintroduces spread.
    distinct = {rec.get("shrunk_nrfi") for rec in ump.values()}
    if len(distinct) <= 1:
        print("FILE IS FLAT (every shrunk_nrfi == league rate).")
        print("This is the intended state since 2026-08-29 -- the question this")
        print("script asks was answered (no persistence) and the fix shipped.")
        print("See rebuild_umpire_rates.py and the")
        print("umpire_rates_built_on_banned_seasons memory. Nothing to test.")
        return

    # --- observed 2026 first-inning outcomes per umpire -----------------
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    obs = defaultdict(lambda: [0, 0])   # uid -> [nrfi_games, total_games]
    for r in rows:
        uid = (r.get("home_plate_ump_id") or "").strip()
        res = (r.get("actual_result") or "").upper()
        if not uid or res not in ("NRFI", "YRFI"):
            continue
        obs[uid][1] += 1
        if res == "NRFI":
            obs[uid][0] += 1

    print(f"2026 graded games with an umpire id: "
          f"{sum(v[1] for v in obs.values())} across {len(obs)} umpires\n")

    hist, live, names, ns = [], [], [], []
    for uid, (nrfi, tot) in obs.items():
        if tot < MIN_GAMES_2026 or uid not in ump:
            continue
        rec = ump[uid]
        if rec.get("n_games", 0) < MIN_GAMES_HIST:
            continue
        hist.append(rec["shrunk_nrfi"])
        live.append(nrfi / tot)
        names.append(rec.get("name", uid))
        ns.append(tot)

    print(f"Umpires with >={MIN_GAMES_HIST} historical and >={MIN_GAMES_2026} "
          f"2026 games: {len(hist)}")
    if len(hist) < 10:
        print("  too few to test persistence -- aborting")
        return 1

    hist_a, live_a = np.array(hist), np.array(live)
    pear = float(np.corrcoef(hist_a, live_a)[0, 1])
    spr = spearman(hist_a, live_a)
    lo, hi = boot_ci(hist_a, live_a)

    print("\n=== PERSISTENCE TEST: 2022-23 shrunk NRFI rate vs actual 2026 NRFI rate ===")
    print(f"  n umpires            : {len(hist)}")
    print(f"  Pearson  r           : {pear:+.4f}")
    print(f"  Spearman rho         : {spr:+.4f}")
    print(f"  bootstrap 90% CI on r: [{lo:+.4f}, {hi:+.4f}]")
    print(f"  r^2 (variance explained): {pear**2:.4f}  "
          f"({100*pear**2:.1f}% of 2026 umpire variation)")

    print(f"\n  historical spread: mean {hist_a.mean():.4f}  sd {hist_a.std():.4f}")
    print(f"  2026 spread      : mean {live_a.mean():.4f}  sd {live_a.std():.4f}")

    # How much of the 2026 spread is just binomial noise?
    p = live_a.mean()
    exp_noise_sd = np.sqrt(np.mean([p * (1 - p) / n for n in ns]))
    print(f"\n  Expected sd from pure coin-flip noise at these sample sizes: "
          f"{exp_noise_sd:.4f}")
    print(f"  Observed 2026 sd:                                            "
          f"{live_a.std():.4f}")
    if live_a.std() <= exp_noise_sd:
        print("  -> observed spread does NOT exceed chance: no detectable "
              "umpire-to-umpire signal in 2026 at all.")
    else:
        excess = np.sqrt(max(live_a.var() - exp_noise_sd**2, 0))
        print(f"  -> excess (true) spread beyond noise: {excess:.4f}")

    print("\n  Most extreme historical umpires and what they actually did in 2026:")
    order = np.argsort(hist_a)
    print(f"    {'umpire':<22}{'2022-23':>10}{'2026':>10}{'2026 n':>8}")
    for i in list(order[:4]) + list(order[-4:]):
        print(f"    {names[i]:<22}{hist_a[i]:>10.4f}{live_a[i]:>10.4f}{ns[i]:>8}")

    # --- what the model does with it -----------------------------------
    print("\n=== WHAT THE MODEL DOES WITH THIS FEATURE ===")
    for fn in ("lr_t1", "lr_b1"):
        d = json.load(open(ROOT / "data" / f"{fn}.json", encoding="utf-8"))
        i = d["feature_names"].index("home_plate_ump_nrfi_rate")
        print(f"  {fn}: weight {d['weights'][i]:+.6f}   "
              f"train mean {d['mean'][i]:.4f}  train sd {d['std'][i]:.4f}")
    print(f"  live 2026 sd of the feature: {live_a.std():.4f} "
          f"(vs train sd above -- a mismatch means the fitted scale is wrong)")

    print("\n=== VERDICT ===")
    if abs(pear) < 0.15 or (lo < 0 < hi):
        print("  NO PERSISTENCE DETECTED.  The 2022-23 umpire rate does not")
        print("  predict the same umpire's 2026 first-inning results (the")
        print("  bootstrap CI includes zero).  The precondition for using a")
        print("  prior-season umpire rate as a feature FAILS.")
        print("  Recommendation: ABLATE the feature and confirm on the 3-split.")
        return 0
    print("  Persistence detected -- the feature tracks something real.")
    print("  Recommendation: keep, and consider re-encoding as called-strike-")
    print("  above-expected rather than an outcome NRFI rate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

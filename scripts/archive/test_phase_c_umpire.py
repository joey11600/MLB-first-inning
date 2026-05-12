#!/usr/bin/env python3
"""
test_phase_c_umpire.py -- test home-plate umpire NRFI rate as a feature.

Per-umpire NRFI rate from 2022-2023 (Bayesian shrunk K=50) is attached
to each game via the cached game_pk -> umpire_id map.  Since the umpire
training corpus is upstream of the LR training window (2024-2025) and
the test set (2026), there's no leakage.

Variants (all on top of slim_weather = current production baseline):
  - slim_weather                              # baseline
  - +ump_nrfi                                 # add ump's shrunk NRFI rate
  - +ump_nrfi (single-half)                   # only T1 (or only B1) gets the ump
  - +ump_minus_league                         # ump rate centered at league mean

Test design: train on 2024+2025 CLEAN ; test on 2026 graded (N=348).
"""

import csv
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from lr_baseline import LogReg

LEAGUE_AVG_ERA = 4.20
LEAGUE_AVG_OBP = 0.318
WX_TEMP_DEFAULT     = 20.0
WX_WIND_DEFAULT     = 10.0
WX_HUMIDITY_DEFAULT = 60.0
FI_PARK_DEFAULT = 0.50

BT_2024 = ROOT / "data" / "backtests" / "backtest_2024-04-01_to_2024-09-30.csv"
BT_2025 = ROOT / "data" / "backtests" / "backtest_2025-04-01_to_2025-09-30.csv"
PICKS_2026 = ROOT / "data" / "picks_2026.csv"
UMP_CACHE_PATH = ROOT / "data" / "umpire_cache.json"
UMP_RATES_PATH = ROOT / "data" / "umpire_rates.json"


def coerce(s, default):
    try:
        f = float(s)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def slim_t1(r, park):
    home = r.get("home", "") or r.get("home_team", "")
    return [
        park.get(home, FI_PARK_DEFAULT),
        coerce(r.get("home_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("away_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
        coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
        coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
        coerce(r.get("wx_is_dome"),  0.0),
    ]


def slim_b1(r, park):
    home = r.get("home", "") or r.get("home_team", "")
    return [
        park.get(home, FI_PARK_DEFAULT),
        coerce(r.get("away_fip"), LEAGUE_AVG_ERA),
        coerce(r.get("home_obp"), LEAGUE_AVG_OBP),
        coerce(r.get("wx_temp_c"),   WX_TEMP_DEFAULT),
        coerce(r.get("wx_wind_kmh"), WX_WIND_DEFAULT),
        coerce(r.get("wx_humidity"), WX_HUMIDITY_DEFAULT),
        coerce(r.get("wx_is_dome"),  0.0),
    ]


def get_ump_rate(game_pk: str, ump_cache: dict, ump_rates: dict, league: float) -> float:
    """Return the home-plate umpire's shrunk NRFI rate; league avg fallback."""
    rec = ump_cache.get(str(game_pk))
    if not rec:
        return league
    ump_id = str(rec.get("hp_id", 0))
    if ump_id in ump_rates:
        return ump_rates[ump_id]["shrunk_nrfi"]
    return league


def gather(csv_path, park, variant: str, ump_cache, ump_rates, league_rate,
           clean_only: bool):
    Xt, yt, Xb, yb, ynrfi = [], [], [], [], []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            actual = (r.get("actual_side") or r.get("actual_result") or "").upper()
            if actual not in ("NRFI", "YRFI"): continue
            if clean_only:
                ap = (r.get("away_pitcher_q") or "").lower()
                hp = (r.get("home_pitcher_q") or "").lower()
                if ap == "avg" or hp == "avg": continue
            t1_runs = r.get("fi_away_runs", "")
            b1_runs = r.get("fi_home_runs", "")
            if t1_runs == "" or b1_runs == "": continue
            try:
                t1y = 1 if int(float(t1_runs)) > 0 else 0
                b1y = 1 if int(float(b1_runs)) > 0 else 0
            except (TypeError, ValueError):
                continue

            ump_rate = get_ump_rate(r.get("game_pk", ""), ump_cache, ump_rates, league_rate)
            ump_centered = ump_rate - league_rate

            t1_x = slim_t1(r, park)
            b1_x = slim_b1(r, park)

            if variant == "slim_weather":
                pass
            elif variant == "+ump_nrfi_both":
                t1_x = t1_x + [ump_rate]
                b1_x = b1_x + [ump_rate]
            elif variant == "+ump_nrfi_t1":
                t1_x = t1_x + [ump_rate]
            elif variant == "+ump_nrfi_b1":
                b1_x = b1_x + [ump_rate]
            elif variant == "+ump_centered":
                t1_x = t1_x + [ump_centered]
                b1_x = b1_x + [ump_centered]
            else:
                raise ValueError(variant)

            Xt.append(t1_x); Xb.append(b1_x)
            yt.append(t1y); yb.append(b1y)
            ynrfi.append(1 if actual == "NRFI" else 0)
    return (np.asarray(Xt, dtype=float), np.asarray(yt, dtype=int),
            np.asarray(Xb, dtype=float), np.asarray(yb, dtype=int),
            np.asarray(ynrfi, dtype=int))


def brier(p, y): return float(np.mean((p - y) ** 2))


def q5_hit(p, y):
    order = np.argsort(p); n = len(p)
    q5 = order[-(n // 5):]
    return float(y[q5].mean()), int(y[q5].sum()), len(q5)


def q1_yrfi(p, y):
    order = np.argsort(p); n = len(p)
    q1 = order[:(n // 5)]
    wins = int((y[q1] == 0).sum())
    return wins / len(q1) if len(q1) else 0.0, wins, len(q1)


def main():
    park = json.load(open(ROOT / "data" / "fi_park_factors.json", encoding="utf-8"))
    ump_cache = json.load(open(UMP_CACHE_PATH, encoding="utf-8"))
    ump_rates_full = json.load(open(UMP_RATES_PATH, encoding="utf-8"))
    league_rate = ump_rates_full["league_nrfi_rate"]
    ump_rates = ump_rates_full["umpires"]
    print(f"  league NRFI rate from corpus: {league_rate*100:.2f}%")
    print(f"  distinct umpires:             {len(ump_rates)}")
    print()

    variants = [
        "slim_weather",
        "+ump_nrfi_both",
        "+ump_nrfi_t1",
        "+ump_nrfi_b1",
        "+ump_centered",
    ]

    print("=" * 96)
    print("  Phase C: home-plate umpire NRFI rate as feature")
    print("  Train: 2024+2025 CLEAN (N=2895) ; test: 2026 graded (N=348)")
    print("=" * 96)
    print(f"  {'variant':<18} {'train N':>7} {'Brier':>7} "
          f"{'mean':>8} {'2026 Q5 NRFI':>14} {'2026 Q1 YRFI':>14}")
    print("  " + "-" * 88)

    for v in variants:
        b24 = gather(BT_2024, park, v, ump_cache, ump_rates, league_rate, clean_only=True)
        b25 = gather(BT_2025, park, v, ump_cache, ump_rates, league_rate, clean_only=True)
        Xt_tr = np.vstack([b24[0], b25[0]]); yt_tr = np.concatenate([b24[1], b25[1]])
        Xb_tr = np.vstack([b24[2], b25[2]]); yb_tr = np.concatenate([b24[3], b25[3]])
        n_train = len(yt_tr)

        m_t1 = LogReg.fit(Xt_tr, yt_tr,
                           [f"t{i}" for i in range(Xt_tr.shape[1])], l2=0.05)
        m_b1 = LogReg.fit(Xb_tr, yb_tr,
                           [f"b{i}" for i in range(Xb_tr.shape[1])], l2=0.05)

        te = gather(PICKS_2026, park, v, ump_cache, ump_rates, league_rate, clean_only=False)
        Xt_te, _, Xb_te, _, ynrfi_te = te
        p_nrfi = (1 - m_t1.predict_proba(Xt_te)) * (1 - m_b1.predict_proba(Xb_te))

        b = brier(p_nrfi, ynrfi_te)
        m = float(p_nrfi.mean())
        q5_r, q5_w, q5_n = q5_hit(p_nrfi, ynrfi_te)
        q1_r, q1_w, q1_n = q1_yrfi(p_nrfi, ynrfi_te)

        # Show LR weights to inspect umpire coefficient
        weight_summary = ""
        if v != "slim_weather":
            # The umpire weight is the LAST element of each model's weights
            t1_w = m_t1.w[-1] if v in ("+ump_nrfi_both", "+ump_nrfi_t1", "+ump_centered") else None
            b1_w = m_b1.w[-1] if v in ("+ump_nrfi_both", "+ump_nrfi_b1", "+ump_centered") else None
            if t1_w is not None:
                weight_summary += f" T1_w={t1_w:+.4f}"
            if b1_w is not None:
                weight_summary += f" B1_w={b1_w:+.4f}"

        print(f"  {v:<18} {n_train:>7} {b:>7.4f} {m*100:>7.2f}%  "
              f"{q5_w:>2}-{q5_n - q5_w:<2} ({q5_r*100:>4.1f}%) "
              f"{q1_w:>2}-{q1_n - q1_w:<2} ({q1_r*100:>4.1f}%) "
              f"{weight_summary}")

    print()
    print("  Read: a variant ships if Brier improves >= 0.002 AND")
    print("  Q5 NRFI or Q1 YRFI improves >= 2pp.")
    print("  Negative Brier delta with positive Q1/Q5 = mixed signal, dig deeper.")


if __name__ == "__main__":
    main()

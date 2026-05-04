#!/usr/bin/env python3
"""
tools/backtest_v2_truepit_2026.py -- Phase A1 perfect-input backtest.

For each 2026 game in picks_2026.csv: reconstruct v2's feature vector,
SUBSTITUTE truepit xera + whiff_pct_rank values (from
data/v2_perfect_2026/truepit_per_pitcher_per_date.json) for the home +
away pitchers as of that game date, then re-run v2's LR + calibrator
(UNCHANGED) and re-classify with production thresholds.  Compute
would-be P/L using actual DK odds + actual outcome.  Aggregate.

WHAT THIS ANSWERS
-----------------
"What would v2 have done if its xera + whiff_pct_rank features had
been point-in-time accurate (truepit) instead of season-aggregate-
leaky?"  This isolates the impact of the T3.11-AUDIT leakage on the
2026 season's actual realized P/L.

WHAT THIS DOES NOT ANSWER
-------------------------
- Effect of using actual lineups vs predicted-time lineups (Phase A2)
- Effect of late pitcher scratches (Phase A2)
- Whether v2 with perfect inputs is BETTER than v2 with leaky inputs
  (we don't have ground truth for "what edge SHOULD v2 have had")
- Whether a retrained calibrator on truepit corpus would do better
  than v2's existing calibrator (Phase B)

CONSTRAINT
----------
Reads only.  Does not modify production pipeline, predictor, calibrator
JSONs, picks CSV, dashboard, or anything else.  Output goes to
data/v2_perfect_2026/backtest_results.json + console.

USAGE
-----
  python tools/backtest_v2_truepit_2026.py
  python tools/backtest_v2_truepit_2026.py --since 2026-04-15
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Use production model loader / feature lists / classifier -- read-only
import mlb_first_inning_predictor as P
from db.variants import _lr_predict_with_cap


DEFAULT_WIN_PROFIT = 100 / 110     # -110 fallback
DEFAULT_LOSS_PROFIT = -1.0


def _to_float(v: Any, default: float) -> float:
    if v in (None, "", "null"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _row_to_feature(row: dict, name: str, truepit_overrides: dict) -> float:
    """Single feature lookup -- mirrors backfill_variants._row_to_feature
    but checks `truepit_overrides` first for any feature we're swapping
    in (xera, whiff_pct_rank)."""
    if name in truepit_overrides:
        return truepit_overrides[name]
    # Special features
    if name == "era_gap_t1":
        return _to_float(row.get("home_era"), P._LEAGUE_AVG_XERA) \
             - _to_float(row.get("away_era"), P._LEAGUE_AVG_XERA)
    if name == "era_gap_b1":
        return _to_float(row.get("away_era"), P._LEAGUE_AVG_XERA) \
             - _to_float(row.get("home_era"), P._LEAGUE_AVG_XERA)
    # Fall back defaults same as backfill_variants
    defaults = {
        "fi_park_nrfi_rate":           P._LEAGUE_NRFI_RATE,
        "home_fip":                    P._LEAGUE_AVG_XERA,
        "away_fip":                    P._LEAGUE_AVG_XERA,
        "home_obp":                    0.320,
        "away_obp":                    0.320,
        "wx_temp_c":                   20.0,
        "wx_wind_kmh":                 10.0,
        "wx_humidity":                 60.0,
        "wx_is_dome":                  0.0,
        "home_p_last5_pitcher_nrfi":   P._LEAGUE_NRFI_RATE,
        "away_p_last5_pitcher_nrfi":   P._LEAGUE_NRFI_RATE,
        "home_p_last10_pitcher_nrfi":  P._LEAGUE_NRFI_RATE,
        "away_p_last10_pitcher_nrfi":  P._LEAGUE_NRFI_RATE,
        "home_top3c_obp":              0.320,
        "away_top3c_obp":              0.320,
        "home_top3c_slg":              0.400,
        "away_top3c_slg":              0.400,
        "home_top3c_iso":              0.150,
        "away_top3c_iso":              0.150,
        "home_plate_ump_nrfi_rate":    P._LEAGUE_NRFI_RATE,
        "home_xera":                   P._LEAGUE_AVG_XERA,
        "away_xera":                   P._LEAGUE_AVG_XERA,
        "home_whiff_pct_rank":         P._NEUTRAL_PCT_RANK,
        "away_whiff_pct_rank":         P._NEUTRAL_PCT_RANK,
        "home_pvt_nrfi_rate":          P._LEAGUE_NRFI_RATE,
        "away_pvt_nrfi_rate":          P._LEAGUE_NRFI_RATE,
        "home_avg_ip_per_start":       5.0,
        "away_avg_ip_per_start":       5.0,
    }
    return _to_float(row.get(name), defaults.get(name, 0.0))


def reconstruct_feats(row: dict, truepit_overrides: dict
                      ) -> tuple[list[float], list[float]]:
    """Build (t1_feats, b1_feats) using truepit values where available."""
    t1 = [_row_to_feature(row, n, truepit_overrides) for n in P._T1_EXPECTED_FEATURES]
    b1 = [_row_to_feature(row, n, truepit_overrides) for n in P._B1_EXPECTED_FEATURES]
    return t1, b1


def get_truepit_overrides(row: dict, truepit: dict) -> dict:
    """Look up truepit xera + whiff_pct_rank for home + away pitchers
    as of the game date.  Returns a dict that get_feature() consults
    before falling back to row values."""
    overrides: dict[str, float] = {}
    date_iso = (row.get("date") or "")[:10]
    if not date_iso:
        return overrides

    per_pitcher = truepit.get("per_pitcher", {})

    for prefix, id_col in (("home", "home_pitcher_id"),
                           ("away", "away_pitcher_id")):
        pid = (row.get(id_col) or "").strip()
        if not pid:
            continue
        snap = per_pitcher.get(str(pid), {}).get(date_iso)
        if not snap:
            continue
        # Only override if we have data; otherwise leave row value (which
        # falls back to league-average defaults inside _row_to_feature).
        x = snap.get("xera")
        if x is not None and x > 0:
            overrides[f"{prefix}_xera"] = float(x)
        w = snap.get("whiff_pct_rank")
        if w is not None:
            overrides[f"{prefix}_whiff_pct_rank"] = float(w)

    return overrides


def classify_v2(p_calibrated: float, lambda_total: float,
                weather_floor: float, has_data: bool
                ) -> tuple[str, str]:
    """Mirror production classify_pick_lr.  Production thresholds:
       p_nrfi >= 0.58 -> STRONG NRFI
       p_nrfi <= 0.42 -> STRONG YRFI (subject to lambda floor)
       else PASS NO EDGE
    """
    if not has_data:
        return "PASS", "NO DATA"
    # Strong NRFI
    if p_calibrated >= P._LR_STRONG_NRFI_P:
        return "NRFI", "STRONG"
    # Strong YRFI subject to weather-adjusted lambda floor
    if p_calibrated <= P._LR_PASS_LO_P:
        if lambda_total < weather_floor:
            return "PASS", "LOW LAMBDA"
        return "YRFI", "STRONG"
    return "PASS", "NO EDGE"


def parse_american_to_implied_prob(s: str | None) -> float | None:
    if not s:
        return None
    s = str(s).strip().replace("−", "-")
    try:
        n = float(s)
    except (TypeError, ValueError):
        return None
    if n == 0 or not (n == n):    # nan check
        return None
    if n > 0:
        return 100.0 / (n + 100.0)
    return -n / (-n + 100.0)


def compute_pl(side: str, strength: str, actual_result: str,
               market_nrfi: str, market_yrfi: str,
               row_pl: float | None) -> tuple[str, float]:
    """Return (graded_str, profit_loss_units) for the variant verdict.
    If we'd bet the same side as production, reuse production's
    realized P/L (since we'd have hit the same market price).  If we'd
    bet a DIFFERENT side, compute would-be P/L from the variant side's
    market odds + actual outcome.  If we'd PASS, return ('PASS', 0)."""
    if side == "PASS":
        return "PASS", 0.0
    if actual_result not in ("NRFI", "YRFI"):
        return "", 0.0    # ungraded / postponed
    won = (side == actual_result)
    graded = "WIN" if won else "LOSS"

    # Use the variant side's market price.
    odds_str = market_nrfi if side == "NRFI" else market_yrfi
    p_implied = parse_american_to_implied_prob(odds_str)
    if p_implied and 0 < p_implied < 1:
        # Convert implied prob back to decimal price for win profit calc
        if won:
            # Decimal odds = 1 / implied_prob; profit = decimal - 1
            decimal = 1.0 / p_implied
            return graded, decimal - 1.0
        else:
            return graded, -1.0
    # Fallback to flat -110
    return graded, DEFAULT_WIN_PROFIT if won else DEFAULT_LOSS_PROFIT


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--since", default="2026-04-01",
                    help="Earliest date to evaluate (default: 2026-04-01)")
    ap.add_argument("--until", default=None,
                    help="Latest date (default: today)")
    ap.add_argument("--truepit",
                    default="data/v2_perfect_2026/truepit_per_pitcher_per_date.json",
                    help="Truepit JSON path")
    ap.add_argument("--out",
                    default="data/v2_perfect_2026/backtest_results.json",
                    help="Output JSON path")
    ap.add_argument("--calibrator", default="v2",
                    choices=["v2", "v3"],
                    help="Which calibrator to apply (default: v2 = leaky-trained, "
                         "production).  v3 = truepit-trained shadow.")
    args = ap.parse_args()

    end_dt = args.until or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print("=" * 80)
    print(f"  v2 truepit-Statcast backtest 2026")
    print(f"  Window: {args.since} -> {end_dt}")
    print("=" * 80)

    # Load v2 production LR weights -- UNCHANGED across all runs.
    print("\n  Loading production v2 LR models (read-only)...")
    m_t1, m_b1 = P._load_lr_models()
    if m_t1 is None or m_b1 is None:
        sys.exit("LR models unavailable.")

    # Load chosen calibrator.  v2 = production (leaky training corpus).
    # v3 = existing shadow trained on 2024+2025 truepit (leak-free).
    if args.calibrator == "v3":
        from calibration import ProbCalibrator
        cal_path = REPO_ROOT / "data" / "calibration_v3.json"
        if not cal_path.exists():
            sys.exit(f"Missing {cal_path} (v3 calibrator)")
        cal = ProbCalibrator.load(cal_path)
        print(f"    v3 calibrator loaded; range "
              f"[{min(cal.rates):.4f}, {max(cal.rates):.4f}], "
              f"trained on {cal.train_seasons}")
    else:
        cal = P._load_lr_calibrator()
        print(f"    v2 calibrator loaded; range "
              f"[{min(cal.rates):.4f}, {max(cal.rates):.4f}]")

    # Load truepit data
    truepit_path = REPO_ROOT / args.truepit
    if not truepit_path.exists():
        sys.exit(f"Missing {truepit_path} -- run tools/backfill_truepit_2026.py first")
    with open(truepit_path, encoding="utf-8") as f:
        truepit = json.load(f)
    print(f"    Truepit data: {truepit.get('n_pitchers', 0)} pitchers, "
          f"end_date={truepit.get('end_date', '?')}")

    # Load picks_2026.csv
    csv_path = REPO_ROOT / "data" / "picks_2026.csv"
    if not csv_path.exists():
        sys.exit(f"Missing {csv_path}")

    # Per-row aggregation
    n_rows = 0
    n_changed_verdict = 0
    n_strong_v2 = n_strong_truepit = 0
    pl_v2_actual = 0.0
    pl_truepit = 0.0
    n_v2_wl = n_truepit_wl = 0
    n_v2_w = n_truepit_w = 0
    per_zone: dict[str, dict] = {}
    flips: list[dict] = []

    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = (row.get("date") or "")[:10]
            if not d:
                continue
            if d < args.since or d > end_dt:
                continue
            n_rows += 1

            # Production verdict (recorded in CSV)
            v2_side     = (row.get("pick_side")     or "PASS").upper()
            v2_strength = (row.get("pick_strength") or "NO EDGE").upper()
            actual      = (row.get("actual_result") or "").upper()
            market_n    = row.get("market_nrfi_odds", "")
            market_y    = row.get("market_yrfi_odds", "")
            row_pl      = _to_float(row.get("profit_loss_units"), default=0.0)

            # Compute v2's realized P/L from CSV (read directly, not recomputed)
            v2_graded = (row.get("graded_result") or "").upper()
            if v2_graded in ("WIN", "LOSS"):
                if v2_strength == "STRONG" and v2_side in ("NRFI", "YRFI"):
                    pl_v2_actual += row_pl
                    n_v2_wl += 1
                    if v2_graded == "WIN":
                        n_v2_w += 1
                if v2_strength == "STRONG":
                    n_strong_v2 += 1
            elif v2_strength == "STRONG":
                n_strong_v2 += 1

            # Now compute the truepit-substituted variant
            # Skip data-pass variants (LINEUP/STARTER PENDING / NO DATA) --
            # truepit substitution doesn't change those (they had data
            # quality issues unrelated to xera/whiff).
            if v2_strength in ("LINEUP PENDING", "STARTER PENDING", "NO DATA"):
                continue

            overrides = get_truepit_overrides(row, truepit)

            t1, b1 = reconstruct_feats(row, overrides)
            try:
                p_t1 = _lr_predict_with_cap(t1, m_t1, cap=None)
                p_b1 = _lr_predict_with_cap(b1, m_b1, cap=None)
            except Exception as exc:    # noqa: BLE001
                print(f"    [{d}/{row.get('game_pk')}] LR error: {exc!r}",
                      file=sys.stderr)
                continue
            p_nrfi_raw = (1.0 - p_t1) * (1.0 - p_b1)
            p_nrfi_cal = cal.predict(float(p_nrfi_raw))

            lambda_t1 = -float(p_t1) * 0 + 0  # placeholder (use stored value)
            lambda_total = _to_float(row.get("lambda_lr_total")
                                      or row.get("combined_lambda"), 1.0)
            wx_temp = _to_float(row.get("wx_temp_c"), 20.0)
            wx_wind = _to_float(row.get("wx_wind_kmh"), 10.0)
            wx_dome = bool(_to_float(row.get("wx_is_dome"), 0.0))
            weather_floor = (
                P._LR_LAMBDA_YRFI_FLOOR if wx_dome
                else P._weather_adjusted_floor(P._LR_LAMBDA_YRFI_FLOOR,
                                                wx_temp, wx_wind, wx_is_dome=False)
            )

            # has_data: only flag NO DATA when ALL inputs are placeholders.
            # We've already filtered LINEUP/STARTER PENDING / NO DATA above.
            has_data = True

            tp_side, tp_strength = classify_v2(p_nrfi_cal, lambda_total,
                                                weather_floor, has_data)

            if tp_strength == "STRONG":
                n_strong_truepit += 1

            # Compare to v2's verdict
            if (tp_side, tp_strength) != (v2_side, v2_strength):
                n_changed_verdict += 1
                if len(flips) < 30:
                    flips.append({
                        "date":  d,
                        "game":  f"{row.get('away_team')}@{row.get('home_team')}",
                        "v2":    f"{v2_strength} {v2_side}",
                        "v2_p":  _to_float(row.get("nrfi_prob"), 0.5),
                        "tp":    f"{tp_strength} {tp_side}",
                        "tp_p":  round(p_nrfi_cal, 4),
                        "actual": actual,
                    })

            # Compute truepit P/L if STRONG bet
            if tp_strength == "STRONG" and tp_side in ("NRFI", "YRFI"):
                tp_graded, tp_pl = compute_pl(tp_side, tp_strength, actual,
                                                market_n, market_y, row_pl)
                if tp_graded in ("WIN", "LOSS"):
                    pl_truepit += tp_pl
                    n_truepit_wl += 1
                    if tp_graded == "WIN":
                        n_truepit_w += 1

                # Per-zone tally
                zone = f"{tp_strength} {tp_side}"
                z = per_zone.setdefault(zone, {"n": 0, "w": 0, "l": 0, "pl": 0.0})
                z["n"] += 1
                if tp_graded == "WIN":
                    z["w"] += 1
                    z["pl"] += tp_pl
                elif tp_graded == "LOSS":
                    z["l"] += 1
                    z["pl"] += tp_pl

    # -------------------- Report --------------------
    print()
    print("  Aggregate over window:")
    print(f"    Rows processed:                   {n_rows}")
    print(f"    Verdict differed (v2 vs truepit): {n_changed_verdict} "
          f"({n_changed_verdict / max(n_rows, 1) * 100:.1f}%)")
    print()
    print(f"    v2 (production, recorded):     {n_strong_v2:>4} STRONG, "
          f"{n_v2_w}-{n_v2_wl - n_v2_w} W-L, "
          f"P/L = {pl_v2_actual:+.3f}u")
    print(f"    v2 + truepit Statcast:        {n_strong_truepit:>4} STRONG, "
          f"{n_truepit_w}-{n_truepit_wl - n_truepit_w} W-L, "
          f"P/L = {pl_truepit:+.3f}u")
    print(f"    Delta:                         "
          f"{n_strong_truepit - n_strong_v2:+d} STRONG, "
          f"{pl_truepit - pl_v2_actual:+.3f}u")

    print()
    print("  v2+truepit per-zone breakdown:")
    for zone in sorted(per_zone.keys()):
        z = per_zone[zone]
        n_wl = z["w"] + z["l"]
        hit = (z["w"] / n_wl) if n_wl else float("nan")
        print(f"    {zone:<14}  n={z['n']:>3}  W-L={z['w']}-{z['l']:<3}  "
              f"hit={hit*100:>5.1f}%  P/L={z['pl']:+7.3f}u")

    if flips:
        print()
        print("  First 30 verdict flips (v2 -> v2+truepit):")
        print(f"    {'date':>10}  {'matchup':>10}  {'v2':>15} -> {'truepit':>15}  "
              f"{'actual':>6}")
        for f in flips:
            print(f"    {f['date']:>10}  {f['game']:>10}  "
                  f"{f['v2']:>15} (p={f['v2_p']:.3f}) -> "
                  f"{f['tp']:>15} (p={f['tp_p']:.3f})  {f['actual']:>6}")

    # Save full report
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "window":         {"since": args.since, "until": end_dt},
            "n_rows":         n_rows,
            "n_changed":      n_changed_verdict,
            "v2_actual":      {"n_strong": n_strong_v2, "w": n_v2_w,
                               "l": n_v2_wl - n_v2_w, "pl": round(pl_v2_actual, 3)},
            "v2_truepit":     {"n_strong": n_strong_truepit, "w": n_truepit_w,
                               "l": n_truepit_wl - n_truepit_w,
                               "pl": round(pl_truepit, 3)},
            "delta_pl":       round(pl_truepit - pl_v2_actual, 3),
            "per_zone":       {k: {**v, "pl": round(v["pl"], 3)}
                               for k, v in per_zone.items()},
            "verdict_flips_sample": flips,
            "fitted_at":      datetime.now(timezone.utc).replace(tzinfo=None)
                                       .isoformat(timespec="seconds") + "Z",
        }, f, indent=2)
    print(f"\n  Saved -> {out_path}")


if __name__ == "__main__":
    main()

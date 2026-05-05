#!/usr/bin/env python3
"""
tools/daily_shadow_report.py -- per-day diagnostic.

Runs after the nightly grade cron.  For each placed STRONG bet on the
given date (default: yesterday ET), reports:

  - V2 actual pick + result + P&L (from picks_2026.csv)
  - What V2 + T4.2 priors-pooling WOULD have predicted
  - What V2 + RAW cache (no T4.2) would have predicted (i.e. pre-shrinkage)
  - Whether T4.2 PASSed on the bet, agreed, or flipped
  - Per-pitcher xera/whiff: raw cache vs priors-pooled

Writes:

  data/diagnostics/shadow_YYYY-MM-DD.csv  (one row per placed bet)
  data/diagnostics/shadow_summary.csv     (one row per day, append-mode)

The summary file gives us a moving timeline:
  date | n_bets | v2_W-L | v2_pl | t42_W-L | t42_pl | delta_pl | n_pass

Read this file weekly to verify T4.2 keeps producing positive delta over
production.  If delta turns negative for 5+ days, something drifted.

USAGE
-----
  python tools/daily_shadow_report.py             # yesterday ET
  python tools/daily_shadow_report.py 2026-05-03  # specific date
"""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mlb_first_inning_predictor as mod
from lr_baseline import LogReg

PICKS_CSV    = REPO_ROOT / "data" / "picks_2026.csv"
DIAG_DIR     = REPO_ROOT / "data" / "diagnostics"
SUMMARY_CSV  = DIAG_DIR / "shadow_summary.csv"

LR_T1_PATH = REPO_ROOT / "data" / "lr_t1.json"
LR_B1_PATH = REPO_ROOT / "data" / "lr_b1.json"
CAL_PATH   = REPO_ROOT / "data" / "calibration_v2.json"

STRONG_NRFI_TH = 0.56
STRONG_YRFI_TH = 0.44


def to_f(v, d):
    if v is None or v == "":
        return d
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


def cal_pred(p, c, r):
    if p <= c[0]: return r[0]
    if p >= c[-1]: return r[-1]
    lo, hi = 0, len(c) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if c[mid] <= p: lo = mid
        else: hi = mid
    if c[hi] == c[lo]: return (r[lo] + r[hi]) / 2
    return r[lo] + (p - c[lo]) / (c[hi] - c[lo]) * (r[hi] - r[lo])


def amer_payout(am):
    try:
        a = int(am)
        return a / 100.0 if a > 0 else 100.0 / -a
    except (ValueError, TypeError):
        return 0.909


def predict(t1m, b1m, cal, r, h_xera, h_whiff, a_xera, a_whiff):
    """Run the production LR + calibrator with explicit xera/whiff overrides.

    Mirrors mod.t1_features() / b1_features() output exactly so this is
    apples-to-apples with what the live predictor would have emitted."""
    home_pitcher = {"era": to_f(r.get("home_era"), mod.LEAGUE_AVG_ERA),
                    "fip": to_f(r.get("home_fip"), mod.LEAGUE_AVG_ERA)}
    away_pitcher = {"era": to_f(r.get("away_era"), mod.LEAGUE_AVG_ERA),
                    "fip": to_f(r.get("away_fip"), mod.LEAGUE_AVG_ERA)}
    home_offense = {"obp": to_f(r.get("home_obp"), mod.LEAGUE_AVG_OBP)}
    away_offense = {"obp": to_f(r.get("away_obp"), mod.LEAGUE_AVG_OBP)}
    wx = {"temp_c":   to_f(r.get("wx_temp_c"),   mod.WX_TEMP_DEFAULT),
          "wind_kmh": to_f(r.get("wx_wind_kmh"), mod.WX_WIND_DEFAULT),
          "humidity": to_f(r.get("wx_humidity"), mod.WX_HUMIDITY_DEFAULT),
          "is_dome":  to_f(r.get("wx_is_dome"),  0.0)}

    # Build T1 manually with overridden xera/whiff (same column order as
    # mod._T1_EXPECTED_FEATURES so production LR weights apply correctly).
    h_era = home_pitcher["era"]; a_era = away_pitcher["era"]
    fi_park = mod._load_fi_park_rates().get(r.get("home_team", ""),
                                              mod._FI_PARK_NRFI_DEFAULT)
    f_t1 = [
        fi_park,
        home_pitcher.get("fip", mod.LEAGUE_AVG_ERA),
        away_offense.get("obp", mod.LEAGUE_AVG_OBP),
        wx["temp_c"], wx["wind_kmh"], wx["humidity"], wx["is_dome"],
        to_f(r.get("home_p_last5_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
        to_f(r.get("away_top3c_obp"),            mod.LEAGUE_AVG_OBP),
        to_f(r.get("home_plate_ump_nrfi_rate"),  mod._LEAGUE_NRFI_RATE),
        h_xera,
        h_whiff,
        h_era - a_era,
        to_f(r.get("home_p_last10_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
        to_f(r.get("away_top3c_slg"),            mod.LEAGUE_AVG_SLG),
        to_f(r.get("away_top3c_iso"),            0.169),
        to_f(r.get("home_pvt_nrfi_rate"),        mod._LEAGUE_NRFI_RATE),
        to_f(r.get("home_avg_ip_per_start"),     5.0),
    ]
    f_b1 = [
        fi_park,
        away_pitcher.get("fip", mod.LEAGUE_AVG_ERA),
        home_offense.get("obp", mod.LEAGUE_AVG_OBP),
        wx["temp_c"], wx["wind_kmh"], wx["humidity"], wx["is_dome"],
        to_f(r.get("away_p_last5_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
        to_f(r.get("home_top3c_obp"),            mod.LEAGUE_AVG_OBP),
        to_f(r.get("home_plate_ump_nrfi_rate"),  mod._LEAGUE_NRFI_RATE),
        a_xera,
        a_whiff,
        a_era - h_era,
        to_f(r.get("away_p_last10_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
        to_f(r.get("home_top3c_slg"),            mod.LEAGUE_AVG_SLG),
        to_f(r.get("home_top3c_iso"),            0.169),
        to_f(r.get("away_pvt_nrfi_rate"),        mod._LEAGUE_NRFI_RATE),
        to_f(r.get("away_avg_ip_per_start"),     5.0),
    ]
    p_t1 = t1m.predict_proba_one(f_t1)
    p_b1 = b1m.predict_proba_one(f_b1)
    raw = (1 - p_t1) * (1 - p_b1)
    cal_p = cal_pred(raw, cal["centers"], cal["rates"])
    if cal_p >= STRONG_NRFI_TH:
        pick = "NRFI"
    elif cal_p <= STRONG_YRFI_TH:
        pick = "YRFI"
    else:
        pick = "PASS"
    return raw, cal_p, pick


def main():
    DIAG_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        # Default to yesterday ET (so the daily grade cron picks up
        # the slate that just finished).
        try:
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now_et = datetime.utcnow() - timedelta(hours=4)
        target_date = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"==> Daily shadow report for {target_date}")

    if not PICKS_CSV.exists():
        sys.exit(f"Missing {PICKS_CSV}")

    with open(LR_T1_PATH) as f: t1d = json.load(f)
    with open(LR_B1_PATH) as f: b1d = json.load(f)
    with open(CAL_PATH)   as f: cal = json.load(f)
    t1m = LogReg(t1d["weights"], t1d["bias"], t1d["feature_names"], t1d["mean"], t1d["std"])
    b1m = LogReg(b1d["weights"], b1d["bias"], b1d["feature_names"], b1d["mean"], b1d["std"])

    detail_rows = []
    v2_w = v2_l = t42_w = t42_l = t42_pass = 0
    v2_pl = t42_pl = 0.0

    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("date") != target_date: continue
            if (r.get("bet_placed") or "").upper() != "Y": continue
            if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"): continue
            try:
                h_pid = int(r.get("home_pitcher_id") or 0)
                a_pid = int(r.get("away_pitcher_id") or 0)
            except (ValueError, TypeError):
                continue

            # Production xera/whiff at lock time (raw cache values stored on row)
            h_xera_raw = to_f(r.get("home_xera"), mod._LEAGUE_AVG_XERA)
            h_whiff_raw = to_f(r.get("home_whiff_pct_rank"), mod._NEUTRAL_PCT_RANK)
            a_xera_raw = to_f(r.get("away_xera"), mod._LEAGUE_AVG_XERA)
            a_whiff_raw = to_f(r.get("away_whiff_pct_rank"), mod._NEUTRAL_PCT_RANK)

            # T4.2 priors-pooled values
            h_sc = mod.fetch_pitcher_statcast(h_pid, 2026, date_iso=target_date) if h_pid else {"xera": mod._LEAGUE_AVG_XERA, "whiff_pct_rank": mod._NEUTRAL_PCT_RANK}
            a_sc = mod.fetch_pitcher_statcast(a_pid, 2026, date_iso=target_date) if a_pid else {"xera": mod._LEAGUE_AVG_XERA, "whiff_pct_rank": mod._NEUTRAL_PCT_RANK}

            # Run BOTH variants
            raw_p_v2, cal_p_v2, _ = predict(t1m, b1m, cal, r, h_xera_raw, h_whiff_raw, a_xera_raw, a_whiff_raw)
            raw_p_t42, cal_p_t42, t42_pick = predict(t1m, b1m, cal, r, h_sc["xera"], h_sc["whiff_pct_rank"], a_sc["xera"], a_sc["whiff_pct_rank"])

            v2_side = (r.get("pick_side") or "").upper()
            graded = (r.get("graded_result") or "").upper()
            v2_correct = graded == "WIN"
            actual = v2_side if v2_correct else ("YRFI" if v2_side == "NRFI" else "NRFI")
            v2_pl_row = to_f(r.get("profit_loss_units"), 0.0)

            if t42_pick == "PASS":
                t42_pl_row = 0.0; t42_oc = "PASS"
            elif t42_pick == actual:
                t42_pl_row = amer_payout(r.get(f"market_{t42_pick.lower()}_odds") or "")
                t42_oc = "WIN"
            else:
                t42_pl_row = -1.0; t42_oc = "LOSS"

            v2_pl += v2_pl_row; t42_pl += t42_pl_row
            if v2_correct: v2_w += 1
            else: v2_l += 1
            if t42_oc == "WIN": t42_w += 1
            elif t42_oc == "LOSS": t42_l += 1
            else: t42_pass += 1

            detail_rows.append({
                "date": target_date,
                "matchup": r["away_team"] + "@" + r["home_team"],
                "v2_pick": v2_side,
                "v2_p": round(cal_p_v2, 4),
                "v2_oc": "WIN" if v2_correct else "LOSS",
                "v2_pl": round(v2_pl_row, 3),
                "t42_pick": t42_pick,
                "t42_p": round(cal_p_t42, 4),
                "t42_oc": t42_oc,
                "t42_pl": round(t42_pl_row, 3),
                "agree": "Y" if v2_side == t42_pick else "N",
                "h_xera_raw": round(h_xera_raw, 2),
                "h_xera_pri": round(h_sc["xera"], 2),
                "a_xera_raw": round(a_xera_raw, 2),
                "a_xera_pri": round(a_sc["xera"], 2),
                "actual_side": actual,
            })

    if not detail_rows:
        print(f"  No placed bets on {target_date}; nothing to report.")
        return

    # Detail file
    detail_path = DIAG_DIR / f"shadow_{target_date}.csv"
    with open(detail_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        w.writeheader()
        for row in detail_rows:
            w.writerow(row)
    print(f"  Wrote {detail_path}  ({len(detail_rows)} bets)")

    # Append to summary
    summary_row = {
        "date":       target_date,
        "n_bets":     len(detail_rows),
        "v2_W":       v2_w,
        "v2_L":       v2_l,
        "v2_pl":      round(v2_pl, 3),
        "t42_W":      t42_w,
        "t42_L":      t42_l,
        "t42_pass":   t42_pass,
        "t42_pl":     round(t42_pl, 3),
        "delta_pl":   round(t42_pl - v2_pl, 3),
        "wrote_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    is_new = not SUMMARY_CSV.exists()
    with open(SUMMARY_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(summary_row)
    print(f"  Appended summary -> {SUMMARY_CSV}")
    print()
    print(f"  V2 ACTUAL:    {v2_w}-{v2_l}        P&L = {v2_pl:+.2f}u")
    print(f"  V2 + T4.2:    {t42_w}-{t42_l} ({t42_pass} PASS)  P&L = {t42_pl:+.2f}u")
    print(f"  Delta:                       {t42_pl - v2_pl:+.2f}u")


if __name__ == "__main__":
    main()

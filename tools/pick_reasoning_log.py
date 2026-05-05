#!/usr/bin/env python3
"""
tools/pick_reasoning_log.py -- T4.6 per-pick "why this pick?" log.

WHY THIS EXISTS
---------------
When a bet loses, the question "what feature drove this?" turned into a
4-hour debugging session this week (5/03 disaster).  The answer was
sitting in the row data the whole time -- we just had to extract the
LR's standardized feature contributions and identify the dominant
driver.  This tool does that automatically every night.

Concretely, for each STRONG pick on a target date:

  - Re-build the production feature vectors for T1 + B1 using the
    same path the live predictor uses (mod.t1_features / b1_features
    with date_iso= so T4.2 priors-pooling fires).
  - Run the LR + calibrator to recover raw + calibrated P(NRFI).
  - For each feature, compute (standardized_value * weight) =
    contribution to logit, sort by absolute magnitude, identify the
    top-5 drivers per half.
  - Flag outliers:
      * any feature value that's >= 3 sigma from training mean
      * raw cache xera > 7.0 or < 2.0 (small-sample noise indicators)
      * calibrator output in a flat zone (multiple raw inputs map to
        same calibrated output -> bin collapse, predictions correlated)
  - Write one JSON per date, with a list of pick entries.

OUTPUT
------
  data/diagnostics/picks/<date>.json

When you wonder "why did 5/03 ARI@CHC fire STRONG YRFI?", you grep:

    jq '.picks[] | select(.matchup == "ARI@CHC")' data/diagnostics/picks/2026-05-03.json

and the answer is right there:

    "top_drivers_b1": [
      {"name": "away_xera", "value": 14.71, "z": 11.6, "weight": 0.137,
       "contribution": +1.59, "outlier": true,
       "note": "raw cache xera=14.71 is 99.9th-percentile outlier; pooled value 4.62 (drift)"}
    ]

USAGE
-----
  python tools/pick_reasoning_log.py             # yesterday ET
  python tools/pick_reasoning_log.py 2026-05-03  # specific date
  python tools/pick_reasoning_log.py --include-pass  # log PASS picks too
"""

from __future__ import annotations

import argparse
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

PICKS_CSV  = REPO_ROOT / "data" / "picks_2026.csv"
DIAG_DIR   = REPO_ROOT / "data" / "diagnostics" / "picks"

LR_T1_PATH = REPO_ROOT / "data" / "lr_t1.json"
LR_B1_PATH = REPO_ROOT / "data" / "lr_b1.json"
CAL_PATH   = REPO_ROOT / "data" / "calibration_v2.json"

OUTLIER_Z_THRESHOLD = 3.0
RAW_XERA_NOISE_HIGH = 7.0
RAW_XERA_NOISE_LOW  = 2.0


def to_f(v, d=None):
    if v is None or v == "":
        return d
    try:
        f = float(v)
        return d if math.isnan(f) else f
    except (ValueError, TypeError):
        return d


def yesterday_et_iso() -> str:
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:    # noqa: BLE001
        now = datetime.utcnow() - timedelta(hours=4)
    return (now - timedelta(days=1)).strftime("%Y-%m-%d")


def cal_pred_with_band(p_raw, c, r):
    """Return (calibrated_p, band_info_dict)."""
    if p_raw <= c[0]:
        return r[0], {"band": "below_min", "is_flat": False}
    if p_raw >= c[-1]:
        return r[-1], {"band": "above_max", "is_flat": False}
    lo, hi = 0, len(c) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if c[mid] <= p_raw: lo = mid
        else: hi = mid
    if c[hi] == c[lo]:
        return (r[lo] + r[hi]) / 2, {"band": f"bin_{lo}-{hi}", "is_flat": False}
    # Check if neighboring bins share the same rate (= flat zone)
    flat_left = lo
    flat_right = hi
    while flat_left > 0 and abs(r[flat_left - 1] - r[lo]) < 1e-9:
        flat_left -= 1
    while flat_right < len(r) - 1 and abs(r[flat_right + 1] - r[hi]) < 1e-9:
        flat_right += 1
    flat_size = flat_right - flat_left + 1
    t = (p_raw - c[lo]) / (c[hi] - c[lo])
    cal_p = r[lo] + t * (r[hi] - r[lo])
    return cal_p, {
        "band":          f"bin_{lo}-{hi}",
        "is_flat":       flat_size >= 3,
        "flat_size":     flat_size,
        "flat_rate":     r[lo] if flat_size >= 3 else None,
    }


def feature_contributions(model: LogReg, feats: list[float]) -> list[dict]:
    """Per-feature breakdown of the standardized-input * weight product
    that the LR sums to produce the logit.

    Returns one entry per feature, ranked by absolute contribution
    (highest first).  Each entry has the raw value, the z-score, the
    weight, the contribution to the logit, and an outlier flag."""
    out = []
    for i, name in enumerate(model.feature_names):
        v = float(feats[i])
        mu = float(model.mean[i])
        sd = float(model.std[i]) if model.std[i] > 1e-9 else 1.0
        z = (v - mu) / sd
        w = float(model.w[i])
        contribution = z * w
        out.append({
            "name":         name,
            "value":        round(v, 4),
            "z":            round(z, 3),
            "weight":       round(w, 4),
            "contribution": round(contribution, 4),
            "outlier":      abs(z) >= OUTLIER_Z_THRESHOLD,
        })
    out.sort(key=lambda x: -abs(x["contribution"]))
    return out


def build_args(r):
    home_pitcher = {"era": to_f(r.get("home_era"), mod.LEAGUE_AVG_ERA),
                    "fip": to_f(r.get("home_fip"), mod.LEAGUE_AVG_ERA)}
    away_pitcher = {"era": to_f(r.get("away_era"), mod.LEAGUE_AVG_ERA),
                    "fip": to_f(r.get("away_fip"), mod.LEAGUE_AVG_ERA)}
    home_offense = {"obp": to_f(r.get("home_obp"), mod.LEAGUE_AVG_OBP)}
    away_offense = {"obp": to_f(r.get("away_obp"), mod.LEAGUE_AVG_OBP)}
    wx = {
        "temp_c":   to_f(r.get("wx_temp_c"),   mod.WX_TEMP_DEFAULT),
        "wind_kmh": to_f(r.get("wx_wind_kmh"), mod.WX_WIND_DEFAULT),
        "humidity": to_f(r.get("wx_humidity"), mod.WX_HUMIDITY_DEFAULT),
        "is_dome":  to_f(r.get("wx_is_dome"),  0.0),
    }
    return home_pitcher, away_pitcher, home_offense, away_offense, wx


def warnings_for_pick(top_t1, top_b1, h_xera_raw, a_xera_raw,
                      h_xera_pri, a_xera_pri, cal_band):
    msgs = []
    # Outlier z-scores
    for tag, top in (("T1", top_t1), ("B1", top_b1)):
        for entry in top:
            if entry["outlier"]:
                msgs.append(
                    f"{tag}: feature {entry['name']}={entry['value']:.3f} is "
                    f"z={entry['z']:+.2f} sigma from training mean -- "
                    f"contribution {entry['contribution']:+.3f} to logit"
                )
    # Raw cache extremes
    for label, raw, pri in (("home_xera", h_xera_raw, h_xera_pri),
                              ("away_xera", a_xera_raw, a_xera_pri)):
        if raw is None:
            continue
        if raw > RAW_XERA_NOISE_HIGH or raw < RAW_XERA_NOISE_LOW:
            drift = pri - raw if pri is not None else 0.0
            msgs.append(
                f"raw cache {label}={raw:.2f} is outside "
                f"[{RAW_XERA_NOISE_LOW}, {RAW_XERA_NOISE_HIGH}] (small-sample noise zone); "
                f"priors-pooled value {pri:.2f} (drift {drift:+.2f})"
            )
        elif pri is not None and abs(raw - pri) >= 1.0:
            msgs.append(
                f"raw {label}={raw:.2f} drifted >=1.0 from pooled {pri:.2f} "
                f"-- T4.2 shrinkage active"
            )
    # Flat zone
    if cal_band.get("is_flat"):
        msgs.append(
            f"calibrator flat zone: {cal_band['flat_size']} bins all map to "
            f"rate {cal_band['flat_rate']:.4f} (multiple distinct raw probs collapse here)"
        )
    return msgs


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("date", nargs="?", default=None)
    ap.add_argument("--include-pass", action="store_true",
                    help="Log PASS picks as well as STRONG.")
    args = ap.parse_args()

    target_date = args.date or yesterday_et_iso()
    DIAG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"==> Pick reasoning log for {target_date}")

    if not PICKS_CSV.exists():
        sys.exit(f"Missing {PICKS_CSV}")
    with open(LR_T1_PATH) as f: t1d = json.load(f)
    with open(LR_B1_PATH) as f: b1d = json.load(f)
    with open(CAL_PATH)   as f: cal = json.load(f)
    t1m = LogReg(t1d["weights"], t1d["bias"], t1d["feature_names"], t1d["mean"], t1d["std"])
    b1m = LogReg(b1d["weights"], b1d["bias"], b1d["feature_names"], b1d["mean"], b1d["std"])

    pick_entries: list[dict] = []
    with open(PICKS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("date") != target_date:
                continue
            strength = (r.get("pick_strength") or "").upper()
            if strength != "STRONG" and not args.include_pass:
                continue
            try:
                h_pid = int(r.get("home_pitcher_id") or 0)
                a_pid = int(r.get("away_pitcher_id") or 0)
            except (ValueError, TypeError):
                continue

            home_pitcher, away_pitcher, home_offense, away_offense, wx = build_args(r)

            # Production feature builders -- T4.2 priors-pooling fires via date_iso
            f_t1 = mod.t1_features(
                home_abbr=r.get("home_team", ""),
                home_pitcher=home_pitcher,
                away_offense=away_offense,
                wx=wx,
                home_pitcher_id=h_pid,
                away_pitcher=away_pitcher,
                home_last5_nrfi=to_f(r.get("home_p_last5_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
                home_last10_nrfi=to_f(r.get("home_p_last10_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
                away_top3c_obp=to_f(r.get("away_top3c_obp"), mod.LEAGUE_AVG_OBP),
                away_top3c_slg=to_f(r.get("away_top3c_slg"), mod.LEAGUE_AVG_SLG),
                away_top3c_iso=to_f(r.get("away_top3c_iso"), 0.169),
                ump_rate=to_f(r.get("home_plate_ump_nrfi_rate"), mod._LEAGUE_NRFI_RATE),
                home_pvt_nrfi=to_f(r.get("home_pvt_nrfi_rate"), mod._LEAGUE_NRFI_RATE),
                home_avg_ip_per_start=to_f(r.get("home_avg_ip_per_start"), 5.0),
                season=2026,
                date_iso=target_date,
            )
            f_b1 = mod.b1_features(
                home_abbr=r.get("home_team", ""),
                away_pitcher=away_pitcher,
                home_offense=home_offense,
                wx=wx,
                away_pitcher_id=a_pid,
                home_pitcher=home_pitcher,
                away_last5_nrfi=to_f(r.get("away_p_last5_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
                away_last10_nrfi=to_f(r.get("away_p_last10_pitcher_nrfi"), mod._LEAGUE_NRFI_RATE),
                home_top3c_obp=to_f(r.get("home_top3c_obp"), mod.LEAGUE_AVG_OBP),
                home_top3c_slg=to_f(r.get("home_top3c_slg"), mod.LEAGUE_AVG_SLG),
                home_top3c_iso=to_f(r.get("home_top3c_iso"), 0.169),
                ump_rate=to_f(r.get("home_plate_ump_nrfi_rate"), mod._LEAGUE_NRFI_RATE),
                away_pvt_nrfi=to_f(r.get("away_pvt_nrfi_rate"), mod._LEAGUE_NRFI_RATE),
                away_avg_ip_per_start=to_f(r.get("away_avg_ip_per_start"), 5.0),
                season=2026,
                date_iso=target_date,
            )

            p_t1 = t1m.predict_proba_one(f_t1)
            p_b1 = b1m.predict_proba_one(f_b1)
            raw_nrfi = (1.0 - p_t1) * (1.0 - p_b1)
            cal_p, cal_band = cal_pred_with_band(raw_nrfi, cal["centers"], cal["rates"])

            contribs_t1 = feature_contributions(t1m, f_t1)
            contribs_b1 = feature_contributions(b1m, f_b1)

            # Compare priors-pooled to raw for context
            h_xera_raw = to_f(r.get("home_xera"))
            a_xera_raw = to_f(r.get("away_xera"))
            h_sc = mod.fetch_pitcher_statcast(h_pid, 2026, date_iso=target_date) if h_pid else {"xera": None, "whiff_pct_rank": None}
            a_sc = mod.fetch_pitcher_statcast(a_pid, 2026, date_iso=target_date) if a_pid else {"xera": None, "whiff_pct_rank": None}

            warns = warnings_for_pick(
                contribs_t1[:5], contribs_b1[:5],
                h_xera_raw, a_xera_raw,
                h_sc.get("xera"), a_sc.get("xera"),
                cal_band,
            )

            pick_entries.append({
                "matchup":           r.get("away_team", "") + "@" + r.get("home_team", ""),
                "game_pk":           r.get("game_pk"),
                "pick_side":         (r.get("pick_side") or "").upper(),
                "pick_strength":     strength,
                "graded_result":     (r.get("graded_result") or ""),
                "raw_p_run_t1":      round(p_t1, 4),
                "raw_p_run_b1":      round(p_b1, 4),
                "raw_p_nrfi":        round(raw_nrfi, 4),
                "calibrated_p_nrfi": round(cal_p, 4),
                "calibrator_band":   cal_band,
                "top_drivers_t1":    contribs_t1[:5],
                "top_drivers_b1":    contribs_b1[:5],
                "priors_vs_raw": {
                    "home_xera_raw":    h_xera_raw,
                    "home_xera_pooled": h_sc.get("xera"),
                    "away_xera_raw":    a_xera_raw,
                    "away_xera_pooled": a_sc.get("xera"),
                },
                "pitcher_q": {
                    "home_pitcher_q":  r.get("home_pitcher_q"),
                    "away_pitcher_q":  r.get("away_pitcher_q"),
                },
                "warnings":          warns,
            })

    out_path = DIAG_DIR / f"{target_date}.json"
    payload = {
        "date":         target_date,
        "fitted_at":    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_picks":      len(pick_entries),
        "schema_note":  ("Each pick entry has top-5 LR feature contributions "
                          "per half, calibrator-band info, raw-vs-priors-pooled "
                          "Statcast comparison, and warnings list.  Sort by "
                          "abs(contribution) to find dominant drivers."),
        "picks":        pick_entries,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"  Wrote {out_path}  ({len(pick_entries)} picks)")
    n_warn = sum(len(p["warnings"]) for p in pick_entries)
    print(f"  Total warnings across all picks: {n_warn}")
    # Surface up to 5 dominant warnings
    if n_warn:
        print()
        print("  Top warnings:")
        all_warnings = [(p["matchup"], w) for p in pick_entries for w in p["warnings"]]
        for matchup, w in all_warnings[:6]:
            print(f"    [{matchup}] {w}")


if __name__ == "__main__":
    main()

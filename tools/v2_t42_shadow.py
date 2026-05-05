"""
Clean shadow: use the PRODUCTION t1_features() / b1_features() / LR / calibrator
directly, passing the row data + date.  T4.2 priors-pooling is wired into those
functions already, so this gives the EXACT output the production predictor
would emit if T4.2 had been enabled at lock time.

Run on every placed bet 4/29-5/04, report W/L + P&L vs the production picks.
"""
import csv
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import mlb_first_inning_predictor as mod
from lr_baseline import LogReg


def to_f(v, default):
    if v is None or v == "":
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (ValueError, TypeError):
        return default


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


def build_args_from_row(r):
    """Convert a picks_2026.csv row into the kwargs t1_features() and
    b1_features() expect.  Mirrors what predict_slate() builds at lock time."""
    home_pitcher = {
        "era": to_f(r.get("home_era"), mod.LEAGUE_AVG_ERA),
        "fip": to_f(r.get("home_fip"), mod.LEAGUE_AVG_ERA),
    }
    away_pitcher = {
        "era": to_f(r.get("away_era"), mod.LEAGUE_AVG_ERA),
        "fip": to_f(r.get("away_fip"), mod.LEAGUE_AVG_ERA),
    }
    home_offense = {"obp": to_f(r.get("home_obp"), mod.LEAGUE_AVG_OBP)}
    away_offense = {"obp": to_f(r.get("away_obp"), mod.LEAGUE_AVG_OBP)}
    wx = {
        "temp_c":   to_f(r.get("wx_temp_c"),   mod.WX_TEMP_DEFAULT),
        "wind_kmh": to_f(r.get("wx_wind_kmh"), mod.WX_WIND_DEFAULT),
        "humidity": to_f(r.get("wx_humidity"), mod.WX_HUMIDITY_DEFAULT),
        "is_dome":  to_f(r.get("wx_is_dome"),  0.0),
    }
    return home_pitcher, away_pitcher, home_offense, away_offense, wx


def main():
    # Load production LR + calibrator
    with open(REPO_ROOT / "data/lr_t1.json")          as f: t1d = json.load(f)
    with open(REPO_ROOT / "data/lr_b1.json")          as f: b1d = json.load(f)
    with open(REPO_ROOT / "data/calibration_v2.json") as f: cal = json.load(f)
    t1m = LogReg(t1d["weights"], t1d["bias"], t1d["feature_names"], t1d["mean"], t1d["std"])
    b1m = LogReg(b1d["weights"], b1d["bias"], b1d["feature_names"], b1d["mean"], b1d["std"])

    # Verify T4.2 is enabled
    print(f"T4.2 priors-pooling enabled in predictor module: {mod._USE_TRUEPIT_PRIORS}")
    priors = mod._load_truepit_priors()
    print(f"Truepit priors loaded: {len(priors)} pitchers\n")

    # Threshold (match production)
    STRONG_NRFI_TH = 0.56
    STRONG_YRFI_TH = 0.44

    rows_out = []
    with open(REPO_ROOT / "data/picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = r.get("date") or ""
            if d < "2026-04-29" or d > "2026-05-04":
                continue
            if (r.get("bet_placed") or "").upper() != "Y":
                continue
            if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"):
                continue
            try:
                h_pid = int(r.get("home_pitcher_id") or 0)
                a_pid = int(r.get("away_pitcher_id") or 0)
            except (ValueError, TypeError):
                continue

            home_pitcher, away_pitcher, home_offense, away_offense, wx = build_args_from_row(r)

            # Call PRODUCTION feature builders -- T4.2 priors-pooling kicks in via date_iso
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
                date_iso=d,
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
                date_iso=d,
            )

            p_t1 = t1m.predict_proba_one(f_t1)
            p_b1 = b1m.predict_proba_one(f_b1)
            raw_nrfi = (1.0 - p_t1) * (1.0 - p_b1)
            new_p_nrfi = cal_pred(raw_nrfi, cal["centers"], cal["rates"])

            if new_p_nrfi >= STRONG_NRFI_TH:
                new_pick = "NRFI"
            elif new_p_nrfi <= STRONG_YRFI_TH:
                new_pick = "YRFI"
            else:
                new_pick = "PASS"

            v2_side = (r.get("pick_side") or "").upper()
            graded = (r.get("graded_result") or "").upper()
            v2_correct = (graded == "WIN")
            actual = v2_side if v2_correct else ("YRFI" if v2_side == "NRFI" else "NRFI")
            v2_pl = to_f(r.get("profit_loss_units"), 0.0)
            v2_p_nrfi = to_f(r.get("nrfi_prob"), 0.5)

            if new_pick == "PASS":
                new_pl = 0.0; new_outcome = "PASS"
            elif new_pick == actual:
                new_pl = amer_payout(r.get(f"market_{new_pick.lower()}_odds") or "")
                new_outcome = "WIN"
            else:
                new_pl = -1.0; new_outcome = "LOSS"

            rows_out.append({
                "d": d,
                "match": r["away_team"] + "@" + r["home_team"],
                "v2_side": v2_side,
                "v2_p": v2_p_nrfi,
                "v2_pl": v2_pl,
                "v2_oc": "WIN" if v2_correct else "LOSS",
                "raw": raw_nrfi,
                "new_p": new_p_nrfi,
                "new_pick": new_pick,
                "new_pl": new_pl,
                "new_oc": new_outcome,
                "actual": actual,
            })

    print(f"{'date':>10} {'match':>9} | {'v2 pick':>5} {'v2_p':>5} {'v2 P/L':>6} {'v2':>3} | {'raw':>5} {'new_p':>5} {'pick':>5} {'P/L':>6} {'oc':>4}")
    print("-" * 110)
    v2_w = v2_l = new_w = new_l = new_pass = 0
    v2_total = new_total = 0.0
    for x in rows_out:
        v2_total += x["v2_pl"]; new_total += x["new_pl"]
        if x["v2_oc"] == "WIN": v2_w += 1
        else: v2_l += 1
        if x["new_oc"] == "WIN": new_w += 1
        elif x["new_oc"] == "LOSS": new_l += 1
        else: new_pass += 1
        print(f"{x['d']:>10} {x['match']:>9} | {x['v2_side']:>5} {x['v2_p']:>5.3f} {x['v2_pl']:>+5.2f}u {x['v2_oc'][0]:>3} | {x['raw']:>5.3f} {x['new_p']:>5.3f} {x['new_pick']:>5} {x['new_pl']:>+5.2f}u {x['new_oc'][:3]:>4}")
    print("-" * 110)
    print()
    print(f"  V2 ACTUAL          {v2_w}-{v2_l}        P&L = {v2_total:+.2f}u  ({v2_w + v2_l} bets)")
    print(f"  V2+T4.2 SHADOW     {new_w}-{new_l}  ({new_pass} PASS)  P&L = {new_total:+.2f}u")
    print(f"  Delta              {new_total - v2_total:+.2f}u")


if __name__ == "__main__":
    main()

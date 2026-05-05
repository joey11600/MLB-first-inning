"""
Clean T4.2 shadow simulation on last 3 days of placed bets.
Substitutes priors-pooled xera/whiff into the production T1/B1 LR + cal_v2.
Computes hypothetical P&L if T4.2 had been live.
"""
import csv, json, sys, math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import mlb_first_inning_predictor as m
from lr_baseline import LogReg

LEAGUE_NRFI_RATE = 0.50
LEAGUE_AVG_OBP   = 0.318
LEAGUE_AVG_SLG   = 0.414
LEAGUE_AVG_ERA   = 4.20
LEAGUE_AVG_XERA  = 4.20
NEUTRAL_PCT_RANK = 50.0
LEAGUE_AVG_ISO   = 0.169
WX_TEMP_DEFAULT  = 20.0
WX_WIND_DEFAULT  = 10.0
WX_HUMIDITY_DEFAULT = 60.0


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
    if c[hi] == c[lo]:
        return (r[lo] + r[hi]) / 2
    return r[lo] + (p - c[lo]) / (c[hi] - c[lo]) * (r[hi] - r[lo])


def amer_payout(am_str):
    try:
        am = int(am_str)
        return am / 100.0 if am > 0 else 100.0 / -am
    except (ValueError, TypeError):
        return 0.909


def build_t1(r, h_xera, h_whiff):
    h_era = to_f(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = to_f(r.get("away_era"), LEAGUE_AVG_ERA)
    return [
        to_f(r.get("park_factor"),                LEAGUE_NRFI_RATE),
        to_f(r.get("home_fip"),                   LEAGUE_AVG_ERA),
        to_f(r.get("away_obp"),                   LEAGUE_AVG_OBP),
        to_f(r.get("wx_temp_c"),                  WX_TEMP_DEFAULT),
        to_f(r.get("wx_wind_kmh"),                WX_WIND_DEFAULT),
        to_f(r.get("wx_humidity"),                WX_HUMIDITY_DEFAULT),
        to_f(r.get("wx_is_dome"),                 0.0),
        to_f(r.get("home_p_last5_pitcher_nrfi"),  LEAGUE_NRFI_RATE),
        to_f(r.get("away_top3c_obp"),             LEAGUE_AVG_OBP),
        to_f(r.get("home_plate_ump_nrfi_rate"),   LEAGUE_NRFI_RATE),
        h_xera,
        h_whiff,
        h_era - a_era,
        to_f(r.get("home_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        to_f(r.get("away_top3c_slg"),             LEAGUE_AVG_SLG),
        to_f(r.get("away_top3c_iso"),             LEAGUE_AVG_ISO),
        to_f(r.get("home_pvt_nrfi_rate"),         LEAGUE_NRFI_RATE),
        to_f(r.get("home_avg_ip_per_start"),      5.0),
    ]


def build_b1(r, a_xera, a_whiff):
    h_era = to_f(r.get("home_era"), LEAGUE_AVG_ERA)
    a_era = to_f(r.get("away_era"), LEAGUE_AVG_ERA)
    return [
        to_f(r.get("park_factor"),                LEAGUE_NRFI_RATE),
        to_f(r.get("away_fip"),                   LEAGUE_AVG_ERA),
        to_f(r.get("home_obp"),                   LEAGUE_AVG_OBP),
        to_f(r.get("wx_temp_c"),                  WX_TEMP_DEFAULT),
        to_f(r.get("wx_wind_kmh"),                WX_WIND_DEFAULT),
        to_f(r.get("wx_humidity"),                WX_HUMIDITY_DEFAULT),
        to_f(r.get("wx_is_dome"),                 0.0),
        to_f(r.get("away_p_last5_pitcher_nrfi"),  LEAGUE_NRFI_RATE),
        to_f(r.get("home_top3c_obp"),             LEAGUE_AVG_OBP),
        to_f(r.get("home_plate_ump_nrfi_rate"),   LEAGUE_NRFI_RATE),
        a_xera,
        a_whiff,
        a_era - h_era,
        to_f(r.get("away_p_last10_pitcher_nrfi"), LEAGUE_NRFI_RATE),
        to_f(r.get("home_top3c_slg"),             LEAGUE_AVG_SLG),
        to_f(r.get("home_top3c_iso"),             LEAGUE_AVG_ISO),
        to_f(r.get("away_pvt_nrfi_rate"),         LEAGUE_NRFI_RATE),
        to_f(r.get("away_avg_ip_per_start"),      5.0),
    ]


def main():
    with open(REPO_ROOT / "data/lr_t1.json")          as f: t1d = json.load(f)
    with open(REPO_ROOT / "data/lr_b1.json")          as f: b1d = json.load(f)
    with open(REPO_ROOT / "data/calibration_v2.json") as f: cal = json.load(f)
    t1m = LogReg(t1d["weights"], t1d["bias"], t1d["feature_names"], t1d["mean"], t1d["std"])
    b1m = LogReg(b1d["weights"], b1d["bias"], b1d["feature_names"], b1d["mean"], b1d["std"])

    results = []
    with open(REPO_ROOT / "data/picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("bet_placed") or "").upper() != "Y": continue
            d = r["date"]
            if d < "2026-05-02" or d > "2026-05-04": continue
            if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"): continue
            try:
                h_pid = int(r.get("home_pitcher_id") or 0)
                a_pid = int(r.get("away_pitcher_id") or 0)
            except (ValueError, TypeError):
                continue
            h_sc = m.fetch_pitcher_statcast(h_pid, 2026, date_iso=d) if h_pid else {"xera": LEAGUE_AVG_XERA, "whiff_pct_rank": NEUTRAL_PCT_RANK}
            a_sc = m.fetch_pitcher_statcast(a_pid, 2026, date_iso=d) if a_pid else {"xera": LEAGUE_AVG_XERA, "whiff_pct_rank": NEUTRAL_PCT_RANK}

            f_t1 = build_t1(r, h_sc["xera"], h_sc["whiff_pct_rank"])
            f_b1 = build_b1(r, a_sc["xera"], a_sc["whiff_pct_rank"])
            p_t1 = t1m.predict_proba_one(f_t1)
            p_b1 = b1m.predict_proba_one(f_b1)
            raw = (1 - p_t1) * (1 - p_b1)
            new_p = cal_pred(raw, cal["centers"], cal["rates"])

            if new_p >= 0.56:
                new_pick = "NRFI"
            elif new_p <= 0.44:
                new_pick = "YRFI"
            else:
                new_pick = "PASS"

            v2_side = (r.get("pick_side") or "").upper()
            graded = r.get("graded_result", "").upper()
            v2_correct = graded == "WIN"
            actual = v2_side if v2_correct else ("YRFI" if v2_side == "NRFI" else "NRFI")
            v2_pl = to_f(r.get("profit_loss_units"), 0.0)
            v2_p = to_f(r.get("nrfi_prob"), 0.5)

            if new_pick == "PASS":
                new_pl = 0.0; new_oc = "PASS"
            elif new_pick == actual:
                new_pl = amer_payout(r.get(f"market_{new_pick.lower()}_odds") or "")
                new_oc = "WIN"
            else:
                new_pl = -1.0; new_oc = "LOSS"

            results.append({
                "date": d, "matchup": r["away_team"]+"@"+r["home_team"],
                "v2_side": v2_side, "v2_p": v2_p, "v2_pl": v2_pl,
                "v2_outcome": "WIN" if v2_correct else "LOSS",
                "new_pick": new_pick, "new_p": new_p, "new_pl": new_pl, "new_oc": new_oc,
                "h_xera_raw": to_f(r.get("home_xera"), LEAGUE_AVG_XERA),
                "h_xera_pri": h_sc["xera"],
                "a_xera_raw": to_f(r.get("away_xera"), LEAGUE_AVG_XERA),
                "a_xera_pri": a_sc["xera"],
                "actual": actual,
            })

    print()
    print("LAST 3 DAYS: T4.2 priors-pooled shadow vs V2 actual placed bets")
    print()
    fmt_hdr = "{:>10} {:>9} | {:>5} {:>7} {:>7} | {:>9} {:>8} {:>7} | {:>13}"
    print(fmt_hdr.format("date", "matchup", "v2", "v2 P(N)", "v2 P/L", "NEW pick", "NEW P(N)", "NEW P/L", "xera_a/h"))
    print("-" * 110)

    v2_w = v2_l = new_w = new_l = new_pass = 0
    v2_total = new_total = 0.0
    for r in results:
        v2_total += r["v2_pl"]; new_total += r["new_pl"]
        if r["v2_outcome"] == "WIN": v2_w += 1
        else: v2_l += 1
        if r["new_oc"] == "WIN": new_w += 1
        elif r["new_oc"] == "LOSS": new_l += 1
        else: new_pass += 1
        xera_str = f"{r['a_xera_raw']:>4.2f}->{r['a_xera_pri']:<4.2f}/{r['h_xera_raw']:<4.2f}->{r['h_xera_pri']:<4.2f}"
        print("{:>10} {:>9} | {:>5} {:>7.3f} {:>+6.2f}u | {:>9} {:>8.3f} {:>+6.2f}u | {}".format(
            r["date"], r["matchup"], r["v2_side"], r["v2_p"], r["v2_pl"],
            r["new_pick"], r["new_p"], r["new_pl"], xera_str
        ))

    print("-" * 110)
    print()
    print(f"  V2 ACTUAL  (last 3 days, real bets):  {v2_w}-{v2_l}   P&L = {v2_total:+.2f}u  over {len(results)} bets")
    print(f"  T4.2 SHADOW (same {len(results)} games):       {new_w}-{new_l} ({new_pass} PASS)   P&L = {new_total:+.2f}u")
    print(f"  Delta: T4.2 - V2 = {new_total - v2_total:+.2f}u")


if __name__ == "__main__":
    main()

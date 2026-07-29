#!/usr/bin/env python3
"""ANALYSIS ONLY -- rebuild the T1/B1 feature vectors from the persisted CSV
columns, re-run the logistic forward pass with data/lr_t1.json + lr_b1.json,
and diff the recomputed lambdas / raw product / calibrated prob against what
is stored.  Restricted to rows where every feature is recoverable."""
import csv, json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import mlb_first_inning_predictor as P
from calibration import ProbCalibrator

cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
m_t1 = json.load(open(ROOT / "data" / "lr_t1.json"))
m_b1 = json.load(open(ROOT / "data" / "lr_b1.json"))
fi_park = json.load(open(ROOT / "data" / "fi_park_factors.json"))

print("t1 model keys:", [k for k in m_t1 if k != 'weights'],
      "n_weights:", len(m_t1["weights"]))
print("feature names in file:", m_t1.get("features"))
print("expected order        :", P._T1_EXPECTED_FEATURES)
print("match:", m_t1.get("features") == P._T1_EXPECTED_FEATURES)
print("b1 match:", m_b1.get("features") == P._B1_EXPECTED_FEATURES)
print()

rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))

def f(x, default=None):
    try:
        s = str(x).strip()
        return float(s) if s else default
    except Exception:
        return default

def sig(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z); return e / (1.0 + e)

def fwd(feats, m):
    z = m["bias"]
    for x, mean, std, w in zip(feats, m["mean"], m["std"], m["weights"]):
        if std <= 0:
            continue
        z += w * (x - mean) / std
    return sig(z)

res = []
skipped = 0
for r in rows:
    if not r.get("lambda_lr_t1"):
        skipped += 1; continue
    if (r.get("wx_is_dome") or "0").strip() not in ("0", "0.0"):
        skipped += 1; continue           # dome rows blank out the weather cols
    home = r["home_team"]
    fp = fi_park.get(home)
    if fp is None:
        skipped += 1; continue
    need = ["home_fip", "away_obp", "wx_temp_c", "wx_wind_kmh", "wx_humidity",
            "home_p_last5_pitcher_nrfi", "away_top3c_obp", "home_plate_ump_nrfi_rate",
            "home_xera", "home_whiff_pct_rank", "home_era", "away_era",
            "home_p_last10_pitcher_nrfi", "away_top3c_slg", "away_top3c_iso",
            "home_pvt_nrfi_rate", "home_avg_ip_per_start", "away_top3_ops_vs_oppHand",
            "away_fip", "home_obp", "away_p_last5_pitcher_nrfi", "home_top3c_obp",
            "away_xera", "away_whiff_pct_rank", "away_p_last10_pitcher_nrfi",
            "home_top3c_slg", "home_top3c_iso", "away_pvt_nrfi_rate",
            "away_avg_ip_per_start", "home_top3_ops_vs_oppHand"]
    v = {k: f(r.get(k)) for k in need}
    if any(x is None for x in v.values()):
        skipped += 1; continue

    t1 = [fp, v["home_fip"], v["away_obp"], v["wx_temp_c"], v["wx_wind_kmh"],
          v["wx_humidity"], 0.0, v["home_p_last5_pitcher_nrfi"], v["away_top3c_obp"],
          v["home_plate_ump_nrfi_rate"], v["home_xera"], v["home_whiff_pct_rank"],
          v["home_era"] - v["away_era"], v["home_p_last10_pitcher_nrfi"],
          v["away_top3c_slg"], v["away_top3c_iso"], v["home_pvt_nrfi_rate"],
          v["home_avg_ip_per_start"], v["away_top3_ops_vs_oppHand"]]
    b1 = [fp, v["away_fip"], v["home_obp"], v["wx_temp_c"], v["wx_wind_kmh"],
          v["wx_humidity"], 0.0, v["away_p_last5_pitcher_nrfi"], v["home_top3c_obp"],
          v["home_plate_ump_nrfi_rate"], v["away_xera"], v["away_whiff_pct_rank"],
          v["away_era"] - v["home_era"], v["away_p_last10_pitcher_nrfi"],
          v["home_top3c_slg"], v["home_top3c_iso"], v["away_pvt_nrfi_rate"],
          v["away_avg_ip_per_start"], v["home_top3_ops_vs_oppHand"]]

    p_t1, p_b1 = fwd(t1, m_t1), fwd(b1, m_b1)
    lt1 = -math.log(max(1e-9, 1 - p_t1)); lb1 = -math.log(max(1e-9, 1 - p_b1))
    raw = (1 - p_t1) * (1 - p_b1)
    calp = cal.predict(raw)
    res.append({
        "date": r["date"], "g": f"{r['away_team']}@{r['home_team']}",
        "d_t1": lt1 - f(r["lambda_lr_t1"]),
        "d_b1": lb1 - f(r["lambda_lr_b1"]),
        "d_tot": (lt1 + lb1) - f(r["lambda_lr_total"]),
        "d_lnraw": (-math.log(raw)) - f(r["lambda_lr_total"]),
        "d_cal": calp - f(r["nrfi_prob"]),
        "raw": raw, "calp": calp,
    })

print(f"rows replayed: {len(res)}   skipped (dome / missing feature / pre-LR): {skipped}")
if res:
    for k in ("d_t1", "d_b1", "d_tot", "d_lnraw", "d_cal"):
        a = [abs(x[k]) for x in res]
        print(f"  {k:8s} max={max(a):.5f}  mean={sum(a)/len(a):.6f}  n>5e-5={sum(1 for y in a if y>5e-5)}")
    # restrict to rows written under the CURRENT model+calibrator vintage
    recent = [x for x in res if x["date"] >= "2026-07-28"]
    print(f"\n  --- rows from 2026-07-28 (current model + current calibrator) n={len(recent)} ---")
    for k in ("d_t1", "d_b1", "d_tot", "d_lnraw", "d_cal"):
        a = [abs(x[k]) for x in recent] or [0]
        print(f"  {k:8s} max={max(a):.6f}  mean={sum(a)/len(a):.7f}")
    for x in recent[:10]:
        print(f"   {x['date']} {x['g']:10s} d_t1={x['d_t1']:+.5f} d_b1={x['d_b1']:+.5f} "
              f"d_cal={x['d_cal']:+.5f} raw={x['raw']:.4f} cal={x['calp']:.4f}")

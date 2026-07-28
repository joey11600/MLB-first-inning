#!/usr/bin/env python3
"""tools/nrfi_dd_coverage.py -- is the 2024 vs 2025 divergence real, or a
feature-coverage artifact?  Reports, per source CSV, the % of rows where each
model input is actually PRESENT (vs silently defaulted by coerce_float), plus
the raw-probability distribution.  Read-only."""
from __future__ import annotations
import csv, math, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"
FEATS = ["home_fip", "away_fip", "home_obp", "away_obp",
         "wx_temp_c", "wx_wind_kmh", "wx_humidity",
         "home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi",
         "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
         "home_top3c_obp", "away_top3c_obp", "home_top3c_slg", "away_top3c_slg",
         "home_top3c_iso", "away_top3c_iso",
         "home_plate_ump_nrfi_rate", "home_xera", "away_xera",
         "home_whiff_pct_rank", "away_whiff_pct_rank",
         "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
         "home_avg_ip_per_start", "away_avg_ip_per_start",
         "home_top3_ops_vs_oppHand", "away_top3_ops_vs_oppHand",
         "home_era", "away_era"]

SRC = {
    "2024bt": [BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"],
    "2025bt": [BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"],
    "2026bt": [BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
               BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"],
    "2026picks": [ROOT / "data" / "picks_2026.csv"],
}


def present(v):
    if v is None:
        return False
    s = str(v).strip()
    if s in ("", "None", "nan", "NaN"):
        return False
    try:
        return math.isfinite(float(s))
    except ValueError:
        return False


def main():
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    cov = {}
    dist = {}
    for name, paths in SRC.items():
        rows = []
        for p in paths:
            with open(p, encoding="utf-8") as f:
                rows += list(csv.DictReader(f))
        outcol = "actual_result" if "picks" in name else "actual_side"
        homecol = "home_team" if "picks" in name else "home"
        rows = [r for r in rows if (r.get(outcol) or "").upper() in ("NRFI", "YRFI")]
        cov[name] = {k: 100.0 * sum(present(r.get(k)) for r in rows) / max(1, len(rows))
                     for k in FEATS}
        cov[name]["__n"] = len(rows)
        Xt, Xb, ys = [], [], []
        for r in rows:
            fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            Xt.append(tv); Xb.append(bv)
            ys.append(1 if (r.get(outcol) or "").upper() == "NRFI" else 0)
        raw = rc.lr_predict_two_stage(t1m, b1m, np.asarray(Xt, float), np.asarray(Xb, float))
        lam = -np.log(np.clip(raw, 1e-12, 1))
        dist[name] = (raw, lam, np.asarray(ys))

    print("=" * 96)
    print("  FEATURE PRESENCE (% of graded rows where the value is a real number)")
    print("=" * 96)
    names = list(SRC)
    print(f"  {'feature':<32}" + "".join(f"{n:>12}" for n in names))
    print(f"  {'n graded':<32}" + "".join(f"{cov[n]['__n']:>12}" for n in names))
    for k in FEATS:
        vals = [cov[n][k] for n in names]
        flag = "  <== GAP" if (max(vals) - min(vals)) > 25 else ""
        print(f"  {k:<32}" + "".join(f"{v:>11.0f}%" for v in vals) + flag)

    print("\n" + "=" * 96)
    print("  RAW NRFI PROB / LAMBDA DISTRIBUTION + BASE RATE")
    print("=" * 96)
    print(f"  {'source':<12}{'n':>7}{'baseNRFI':>10}{'raw mean':>10}{'raw p10':>9}"
          f"{'raw p50':>9}{'raw p90':>9}{'lam p10':>9}{'lam p50':>9}{'lam<=.52':>10}")
    for n in names:
        raw, lam, ys = dist[n]
        print(f"  {n:<12}{len(raw):>7}{ys.mean():>10.3f}{raw.mean():>10.3f}"
              f"{np.percentile(raw,10):>9.3f}{np.percentile(raw,50):>9.3f}"
              f"{np.percentile(raw,90):>9.3f}"
              f"{np.percentile(lam,10):>9.3f}{np.percentile(lam,50):>9.3f}"
              f"{100*(lam<=0.52).mean():>9.1f}%")

    print("\n  CALIBRATION-IN-THE-TAIL: actual NRFI rate by RAW decile, per season")
    print(f"  {'decile':<10}" + "".join(f"{n:>14}" for n in names))
    for d in range(10):
        line = f"  {d*10}-{d*10+10}%    "
        for n in names:
            raw, lam, ys = dist[n]
            lo, hi = np.percentile(raw, d * 10), np.percentile(raw, d * 10 + 10)
            m = (raw >= lo) & (raw <= hi if d == 9 else raw < hi)
            line += f"{ys[m].mean():>8.3f}({m.sum():>4})" if m.sum() else f"{'-':>14}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

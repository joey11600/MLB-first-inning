#!/usr/bin/env python3
"""tools/nrfi_dd_auc.py -- how much NRFI discrimination does the model
actually have in each season, and are the 2024 inputs degenerate?
Read-only."""
from __future__ import annotations
import csv, math, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"
SRC = {
    "2024bt": ([BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"], "actual_side", "home"),
    "2025bt": ([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home"),
    "2026bt": ([BT / "backtest_2026-04-01_to_2026-05-11_truepit.csv",
                BT / "backtest_2026-05-12_to_2026-05-26_truepit.csv"], "actual_side", "home"),
    "2026picks": ([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team"),
}
KEY = ["home_p_last5_pitcher_nrfi", "away_p_last5_pitcher_nrfi",
       "home_p_last10_pitcher_nrfi", "away_p_last10_pitcher_nrfi",
       "home_pvt_nrfi_rate", "away_pvt_nrfi_rate",
       "home_xera", "away_xera", "home_whiff_pct_rank", "away_whiff_pct_rank",
       "home_avg_ip_per_start", "away_avg_ip_per_start",
       "home_top3c_obp", "away_top3c_obp", "home_top3c_iso", "away_top3c_iso",
       "home_top3_ops_vs_oppHand", "away_top3_ops_vs_oppHand",
       "home_fip", "away_fip", "home_era", "away_era", "home_obp", "away_obp"]


def auc(scores, y):
    order = np.argsort(scores)
    r = np.empty(len(scores), float)
    s = scores[order]
    i = 0
    ranks = np.arange(1, len(s) + 1, dtype=float)
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        r[order[i:j + 1]] = ranks[i:j + 1].mean()
        i = j + 1
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def main():
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    print("=" * 92)
    print("  MODEL DISCRIMINATION ON NRFI, BY SEASON  (AUC 0.50 = coin flip)")
    print("=" * 92)
    print(f"  {'source':<12}{'n':>7}{'base':>8}{'AUC raw':>10}{'Brier':>9}"
          f"{'top-decile NRFI':>18}{'bot-decile':>12}")
    store = {}
    for name, (paths, outcol, homecol) in SRC.items():
        rows = []
        for p in paths:
            with open(p, encoding="utf-8") as f:
                rows += [r for r in csv.DictReader(f)
                         if (r.get(outcol) or "").upper() in ("NRFI", "YRFI")]
        Xt, Xb, ys = [], [], []
        for r in rows:
            fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            Xt.append(tv); Xb.append(bv)
            ys.append(1 if (r.get(outcol) or "").upper() == "NRFI" else 0)
        raw = np.asarray(rc.lr_predict_two_stage(t1m, b1m, np.asarray(Xt, float),
                                                 np.asarray(Xb, float)), float)
        y = np.asarray(ys)
        a = auc(raw, y)
        br = float(np.mean((raw - y) ** 2))
        top = y[raw >= np.percentile(raw, 90)].mean()
        bot = y[raw <= np.percentile(raw, 10)].mean()
        print(f"  {name:<12}{len(y):>7}{y.mean():>8.3f}{a:>10.4f}{br:>9.4f}"
              f"{top:>18.3f}{bot:>12.3f}")
        store[name] = (rows, raw, y)

    print("\n" + "=" * 92)
    print("  INPUT SPREAD (std dev) PER SEASON -- a near-zero std = degenerate/defaulted column")
    print("=" * 92)
    names = list(SRC)
    print(f"  {'feature':<32}" + "".join(f"{n:>12}" for n in names))
    for k in KEY:
        line = f"  {k:<32}"
        stds = []
        for n in names:
            vals = []
            for r in store[n][0]:
                try:
                    v = float(r.get(k) or "")
                    if math.isfinite(v):
                        vals.append(v)
                except ValueError:
                    pass
            s = float(np.std(vals)) if len(vals) > 5 else float("nan")
            stds.append(s)
            line += f"{s:>12.4f}"
        ok = [s for s in stds if s == s and s > 0]
        if ok and (max(ok) / max(1e-9, min(ok))) > 2.0:
            line += "  <== SPREAD MISMATCH"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

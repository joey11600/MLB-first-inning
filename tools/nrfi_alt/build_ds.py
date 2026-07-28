#!/usr/bin/env python3
"""Build the 2026 real-priced dataset for the profit-target refutation.

Independent re-derivation: does NOT reuse season_replay's simulate().
Only borrows recalibrate_v2's feature construction (that is the model's
own input schema, re-deriving it would be a copy anyway).

Outputs data to tools/nrfi_alt/ds_2026.npz + ds_2026_meta.csv
"""
from __future__ import annotations
import csv, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def main():
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
    keep = []
    for rid, r in enumerate(rows):
        act = (r.get("actual_result") or "").upper()
        if act not in ("NRFI", "YRFI"):
            continue
        no, yo = fnum(r.get("market_nrfi_odds")), fnum(r.get("market_yrfi_odds"))
        if no is None or yo is None:
            continue
        fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        keep.append((rid, r, tv, bv, no, yo, act))

    Xt = np.asarray([k[2] for k in keep], float)
    Xb = np.asarray([k[3] for k in keep], float)
    raw = np.asarray(rc.lr_predict_two_stage(t1m, b1m, Xt, Xb), float)

    recs = []
    for i, (rid, r, tv, bv, no, yo, act) in enumerate(keep):
        i_n, i_y = implied(no), implied(yo)
        vig = i_n + i_y
        recs.append(dict(
            rid=rid, date=r["date"], away=r.get("away_team", ""), home=r.get("home_team", ""),
            gn=(r.get("game_number") or "1"),
            y_nrfi=1 if act == "NRFI" else 0,
            nrfi_odds=no, yrfi_odds=yo,
            pay_n=payout(no), pay_y=payout(yo),
            imp_n=i_n, imp_y=i_y, vig=vig,
            dev_n=i_n / vig, dev_y=i_y / vig,
            p_raw=raw[i],
            p_live=fnum(r.get("nrfi_prob")),
            lam=fnum(r.get("lambda_lr_total")),
            park=fnum(r.get("park_factor")),
        ))
    meta = pd.DataFrame(recs)
    meta["date"] = pd.to_datetime(meta["date"])
    order = np.argsort(meta["date"].values, kind="stable")
    meta = meta.iloc[order].reset_index(drop=True)
    Xt, Xb = Xt[order], Xb[order]
    np.savez(Path(__file__).parent / "ds_2026.npz", Xt=Xt, Xb=Xb)
    meta.to_csv(Path(__file__).parent / "ds_2026_meta.csv", index=False)
    print("n =", len(meta), meta["date"].min().date(), "->", meta["date"].max().date())
    print("NRFI hit rate", meta["y_nrfi"].mean().round(4))
    print("mean vig", meta["vig"].mean().round(4))
    print("p_live null", meta["p_live"].isna().sum())
    # sanity: bet-everything NRFI at real prices
    u = np.where(meta.y_nrfi == 1, meta.pay_n, -1.0)
    print("bet-all-NRFI units %.2f  ROI %.2f%%" % (u.sum(), 100 * u.mean()))
    uy = np.where(meta.y_nrfi == 0, meta.pay_y, -1.0)
    print("bet-all-YRFI units %.2f  ROI %.2f%%" % (uy.sum(), 100 * uy.mean()))
    print("break-even implied NRFI mean %.4f  actual %.4f"
          % (meta.imp_n.mean(), meta.y_nrfi.mean()))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""tools/nrfi_dd_lam060_audit.py -- independent adversarial re-derivation of

    RULE: bet NRFI when lambda_lr_total <= 0.60 AND market_nrfi_odds >= -115

Read-only. Touches no production config.

  1  re-derive the cell on real captured DK prices (both lambda definitions)
  2  multiple-comparisons exposure: permutation null on the MAX-ROI cell
  3  price stress: same bets, 5/10/15 cents worse
  4  what the rule mechanically is (model vs devigged book)
  5  time split inside 2026
  6  out-of-sample hit-rate ceiling on 2024 / 2025 backtests
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

BT = ROOT / "data" / "backtests"


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


def worse(o, cents=10):
    """Move an American price `cents` against the bettor."""
    if o > 0:
        n = o - cents
        return n if n >= 100 else -(200 - n)
    return o - cents


def devig(no, yo):
    a, b = implied(no), implied(yo)
    return a / (a + b)


def load_2026():
    rows = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            rows.append({
                "date": r["date"], "month": r["date"][:7],
                "odds": fnum(r.get("market_nrfi_odds")),
                "yodds": fnum(r.get("market_yrfi_odds")),
                "y": 1 if a == "NRFI" else 0,
                "p": fnum(r.get("nrfi_prob")),
                "lam_csv": fnum(r.get("lambda_lr_total")),
                "raw": r,
            })
    return rows


def add_model_lambda(rows, homecol="home_team"):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    keep = []
    for r in rows:
        r["lam_mdl"] = None
        fp = fi_park.get(r["raw"].get(homecol, ""), rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r["raw"], fp)
        except Exception:
            continue
        r["_t1"], r["_b1"] = tv, bv
        keep.append(r)
    if keep:
        Xt = np.asarray([r["_t1"] for r in keep], float)
        Xb = np.asarray([r["_b1"] for r in keep], float)
        for r, p in zip(keep, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
            r["lam_mdl"] = -math.log(max(1e-12, float(p)))
    return rows


def load_backtest(path, outcol, homecol):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get(outcol) or "").upper() not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            rows.append({"date": r.get("date", ""), "t1": tv, "b1": bv,
                         "y": 1 if (r.get(outcol) or "").upper() == "NRFI" else 0})
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["lam_mdl"] = -math.log(max(1e-12, float(p)))
    return rows


def day_boot(sub, key, iters=10000, seed=7):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(key(r))
    days = list(byday.values())
    if len(days) < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    k, out = len(days), []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for i in idx for x in days[i]]
        out.append(sum(v) / len(v))
    out.sort()
    return out[int(0.025 * iters)], out[int(0.975 * iters)]


def cell_stats(sub, ok="odds"):
    n = len(sub)
    if n == 0:
        return None
    hit = sum(r["y"] for r in sub) / n
    need = st.mean([implied(r[ok]) for r in sub])
    pl = sum(payout(r[ok]) if r["y"] else -1.0 for r in sub)
    return {"n": n, "hit": hit, "need": need, "pl": pl, "roi": pl / n}


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PRICE_FLOORS = [-160, -140, -125, -115, -105, 100, 120]


def main():
    rows = add_model_lambda(load_2026())

    def priced(lk, need_y=False):
        return [r for r in rows if r["odds"] is not None and r.get(lk) is not None
                and (not need_y or r["yodds"] is not None)]

    print("=" * 98)
    print("  1.  RE-DERIVING THE CELL  (lambda <= 0.60 AND DK NRFI price >= -115)")
    print("=" * 98)
    for lk, lbl in (("lam_csv", "lambda_lr_total, the CSV column the rule literally names"),
                    ("lam_mdl", "-log(p_raw) recomputed from live LR (what the 56-cell grid used)")):
        pr = priced(lk)
        sub = [r for r in pr if r[lk] <= 0.60 and r["odds"] >= -115]
        s = cell_stats(sub)
        lo, hi = day_boot(sub, lambda r: payout(r["odds"]) if r["y"] else -1.0)
        nd = len({r["date"] for r in sub})
        print(f"\n  {lbl}")
        print(f"    priced+lambda universe n={len(pr)}      cell n={s['n']} over {nd} distinct days")
        print(f"    hit {100*s['hit']:.1f}%   break-even (mean vig'd implied) {100*s['need']:.1f}%"
              f"   -> edge {100*(s['hit']-s['need']):+.1f}pp")
        print(f"    P/L {s['pl']:+.2f}u   ROI {100*s['roi']:+.1f}%   day-block 95% CI "
              f"[{100*lo:+.1f}%, {100*hi:+.1f}%]")
        seq = "".join("W" if r["y"] else "L" for r in sorted(sub, key=lambda r: r["date"]))
        print(f"    chrono W/L: {seq}")

    print("\n" + "=" * 98)
    print("  2.  MULTIPLE COMPARISONS -- how good does the BEST of 56 cells look under the")
    print("      null 'the book's devigged price is the truth, we have zero edge'?")
    print("=" * 98)
    lk = "lam_mdl"
    pr = priced(lk, need_y=True)
    lam = np.array([r[lk] for r in pr])
    od = np.array([r["odds"] for r in pr])
    yv = np.array([r["y"] for r in pr])
    pay = np.where(od > 0, od / 100.0, 100.0 / np.abs(od))
    pf_fair = np.array([devig(r["odds"], r["yodds"]) for r in pr])
    print(f"  universe with BOTH sides priced: n={len(pr)}")

    masks, keys = [], []
    for c in CAPS:
        for p0 in PRICE_FLOORS:
            m = (lam <= c) & (od >= p0)
            if m.sum() >= 20:
                masks.append(m)
                keys.append((c, p0))
    obs = np.array([np.where(yv[m], pay[m], -1.0).mean() for m in masks])
    order = np.argsort(-obs)
    print(f"  cells in grid: {len(CAPS)*len(PRICE_FLOORS)}   cells with n>=20 (eligible): {len(masks)}")
    print(f"\n  {'lam<=':>8}{'price>=':>10}{'n':>6}{'hit%':>8}{'need%':>8}{'ROI%':>9}")
    for i in order[:6]:
        c, p0 = keys[i]
        m = masks[i]
        print(f"  {('inf' if c > 9 else f'{c:.2f}'):>8}{p0:>+10d}{m.sum():>6}"
              f"{100*yv[m].mean():>8.1f}{100*np.mean([implied(x) for x in od[m]]):>8.1f}"
              f"{100*obs[i]:>+9.1f}")
    obs_max = obs.max()
    tgt = None
    for i, (c, p0) in enumerate(keys):
        if abs(c - 0.60) < 1e-9 and p0 == -115:
            tgt = i
    obs_tgt = obs[tgt] if tgt is not None else float("nan")

    rng = np.random.default_rng(2026)
    ITER = 20000
    mx = np.empty(ITER)
    sg = np.empty(ITER)
    M = np.vstack(masks)
    for i in range(ITER):
        ysim = rng.random(len(pr)) < pf_fair
        val = np.where(ysim, pay, -1.0)
        r = (M * val).sum(1) / M.sum(1)
        mx[i] = r.max()
        sg[i] = r[tgt]
    print(f"\n  observed best-of-{len(masks)} ROI:  {100*obs_max:+.1f}%")
    print(f"  null best-of-{len(masks)} ROI:      median {100*np.median(mx):+.1f}%"
          f"   90th {100*np.quantile(mx, .90):+.1f}%   95th {100*np.quantile(mx, .95):+.1f}%")
    print(f"  FAMILY-WISE p-value  P[null best >= observed best] = {(mx >= obs_max).mean():.3f}")
    print(f"\n  observed lam<=0.60 & >=-115 ROI: {100*obs_tgt:+.1f}%")
    print(f"  uncorrected p for that single cell = {(sg >= obs_tgt).mean():.3f}")

    print("\n" + "=" * 98)
    print("  3.  PRICE STRESS -- identical bets, priced N cents worse")
    print("=" * 98)
    for lk in ("lam_csv", "lam_mdl"):
        sub = [r for r in priced(lk) if r[lk] <= 0.60 and r["odds"] >= -115]
        print(f"\n  lambda def {lk}   (n={len(sub)})")
        for cents in (0, 5, 10, 15):
            s2 = [{**r, "o2": worse(r["odds"], cents)} for r in sub]
            s = cell_stats(s2, "o2")
            print(f"    -{cents:>2}c   hit {100*s['hit']:>5.1f}%   need {100*s['need']:>5.1f}%"
                  f"   P/L {s['pl']:+6.2f}u   ROI {100*s['roi']:+6.1f}%")

    print("\n" + "=" * 98)
    print("  4.  WHAT THE RULE MECHANICALLY IS  (model P vs devigged book P)")
    print("=" * 98)
    for lk in ("lam_csv", "lam_mdl"):
        allp = priced(lk, need_y=True)
        sub = [r for r in allp if r[lk] <= 0.60 and r["odds"] >= -115]
        print(f"\n  lambda def {lk}")
        for tag, rs in (("whole priced season", allp), ("the candidate cell", sub)):
            fair = st.mean([devig(r["odds"], r["yodds"]) for r in rs])
            mdl = st.mean([math.exp(-r[lk]) for r in rs])
            cal = st.mean([r["p"] for r in rs if r["p"] is not None])
            act = sum(r["y"] for r in rs) / len(rs)
            print(f"    {tag:<22} n={len(rs):>4}  book(devig)={100*fair:.1f}%"
                  f"  model raw={100*mdl:.1f}%  model calib={100*cal:.1f}%  actual={100*act:.1f}%")
        dis = [100 * (math.exp(-r[lk]) - devig(r["odds"], r["yodds"])) for r in sub]
        print(f"    mean model-minus-book gap inside the cell: {st.mean(dis):+.1f}pp")

    print("\n" + "=" * 98)
    print("  5.  TIME SPLIT INSIDE 2026")
    print("=" * 98)
    for lk in ("lam_csv", "lam_mdl"):
        pr2 = priced(lk)
        print(f"\n  lambda def {lk}")
        for tag, mo in (("May", {"2026-05"}), ("Jun", {"2026-06"}), ("Jul", {"2026-07"}),
                        ("May+Jun (find)", {"2026-05", "2026-06"}),
                        ("Jul (confirm)", {"2026-07"})):
            sub = [r for r in pr2 if r["month"] in mo and r[lk] <= 0.60 and r["odds"] >= -115]
            if not sub:
                continue
            s = cell_stats(sub)
            print(f"    {tag:<16} n={s['n']:>3}  hit {100*s['hit']:>5.1f}%  need {100*s['need']:>5.1f}%"
                  f"  P/L {s['pl']:+6.2f}u  ROI {100*s['roi']:+6.1f}%")

    print("\n" + "=" * 98)
    print("  6.  OUT-OF-SAMPLE HIT-RATE CEILING (2024/2025 backtests have NO odds:")
    print("      this bounds ACCURACY, not profit -- the price half is untestable there)")
    print("=" * 98)
    pr = priced("lam_mdl")
    sub26 = [r for r in pr if r["lam_mdl"] <= 0.60 and r["odds"] >= -115]
    need26 = st.mean([implied(r["odds"]) for r in sub26])
    print(f"  break-even the book charged inside the 2026 cell: {100*need26:.1f}%\n")
    store = {}
    for name, path, oc, hc in (
        ("2024", BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv", "actual_side", "home"),
        ("2025", BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv", "actual_side", "home"),
    ):
        if path.exists():
            store[name] = load_backtest(path, oc, hc)
        else:
            print(f"  MISSING {path}")
    store["2026"] = [r for r in rows if r.get("lam_mdl") is not None]

    print(f"  {'season':<8}{'n':>8}{'base%':>9}{'lam<=.60 n':>12}{'hit%':>8}{'lift':>10}{'vs need':>11}")
    for k in ("2024", "2025", "2026"):
        rr = store.get(k)
        if not rr:
            continue
        base = sum(r["y"] for r in rr) / len(rr)
        s = [r for r in rr if r["lam_mdl"] <= 0.60]
        h = (sum(r["y"] for r in s) / len(s)) if s else float("nan")
        print(f"  {k:<8}{len(rr):>8}{100*base:>9.1f}{len(s):>12}{100*h:>8.1f}"
              f"{100*(h-base):>+9.1f}pp{100*(h-need26):>+10.1f}pp")

    print("\n  NRFI hit rate by lambda ceiling, three seasons:")
    print(f"  {'lam<=':>8}" + "".join(f"{k:>20}" for k in ("2024", "2025", "2026")))
    for c in (0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 99.0):
        line = f"  {('inf' if c > 9 else f'{c:.2f}'):>8}"
        for k in ("2024", "2025", "2026"):
            rr = store.get(k) or []
            ss = [r for r in rr if r["lam_mdl"] <= c]
            txt = f"{100*sum(r['y'] for r in ss)/len(ss):.1f}% (n={len(ss)})" if ss else "-"
            line += f"{txt:>20}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
tools/nrfi_dd_floors.py -- "what floors work best" for NRFI.

Two halves:

A. THE 2D SWEEP ON REAL PRICES (2026 picks CSV).
   Sweep p_nrfi gate x lambda ceiling, flat 1u at the REAL captured DK
   NRFI price. Count every cell searched (multiple-comparisons exposure),
   then re-check the best cells on a time holdout (discover on May+June,
   confirm on July).

B. THE HIT-RATE CEILING (2024 / 2025 / 2026 backtests, NO odds).
   No prices there, so this bounds ACCURACY, not profit. Question:
   does ANY p_nrfi floor / lambda ceiling get the NRFI hit rate above
   the ~57-58% the book charges in that region? If not, the pricing
   question is moot.

Read-only. Touches no production config.
"""
from __future__ import annotations

import csv
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def load_real():
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    out = []
    for r in raw:
        no = fnum(r.get("market_nrfi_odds"))
        if not (r.get("sportsbook") or "").strip() or no is None:
            continue
        a = (r.get("actual_result") or "").upper()
        if a not in ("NRFI", "YRFI"):
            continue
        out.append({
            "date": r["date"], "month": r["date"][:7],
            "odds": no, "hit": 1 if a == "NRFI" else 0,
            "p": fnum(r.get("nrfi_prob")), "lam": fnum(r.get("lambda_lr_total")),
            "park": fnum(r.get("park_factor")),
        })
    return out


def day_ci(rows, fn, iters=4000, seed=11):
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    days = list(byday)
    if len(days) < 3:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    vals = []
    for _ in range(iters):
        s = []
        for _ in range(len(days)):
            s.extend(byday[days[rnd.randrange(len(days))]])
        v = fn(s)
        if v is not None and not math.isnan(v):
            vals.append(v)
    if len(vals) < 100:
        return (float("nan"), float("nan"))
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1])


def upb(rows):
    if not rows:
        return None
    return sum(payout(r["odds"]) if r["hit"] else -1.0 for r in rows) / len(rows)


def summarize(sel, label):
    if not sel:
        return f"{label:>34} : n=0"
    n = len(sel)
    hit = sum(r["hit"] for r in sel) / n
    need = sum(implied(r["odds"]) for r in sel) / n
    u = upb(sel)
    lo, hi = day_ci(sel, upb) if n >= 20 else (float("nan"), float("nan"))
    cis = f"[{lo:+.3f},{hi:+.3f}]" if not math.isnan(lo) else "n<20"
    return (f"{label:>34} : n={n:>4} hit={hit:.3f} need={need:.3f} "
            f"u/bet={u:+.3f} tot={u * n:+7.2f}u  95%CI {cis}")


def sweep(rows, tag):
    print()
    print("=" * 100)
    print(f"2D SWEEP -- p_nrfi FLOOR x lambda CEILING, real DK prices  [{tag}]")
    print("=" * 100)
    pf = [0.40, 0.44, 0.48, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64]
    lc = [0.55, 0.60, 0.65, 0.70, 0.75, 0.85, 0.95, 9.9]
    cells = 0
    winners = []
    hdr = "p_floor \\ lam_cap  " + "".join(f"{c:>14.2f}" for c in lc)
    print(hdr)
    print("                     " + "  (u/bet  n=...)" * 1)
    for p0 in pf:
        line = f"{p0:>17.2f}  "
        for c in lc:
            sel = [r for r in rows if r["p"] is not None and r["lam"] is not None
                   and r["p"] >= p0 and r["lam"] <= c]
            cells += 1
            if len(sel) < 20:
                line += f"{'n=' + str(len(sel)):>14}"
                continue
            u = upb(sel)
            line += f"{u:>+.3f} n={len(sel):<4d}".rjust(14)
            if u > 0:
                winners.append((p0, c, len(sel), u))
        print(line)
    print(f"\nCELLS SEARCHED: {cells}  (multiple-comparisons exposure -- "
          f"at 5% you expect ~{cells * 0.05:.0f} false positives by chance alone)")
    if winners:
        print("POSITIVE CELLS (u/bet > 0), n>=20:")
        for p0, c, n, u in sorted(winners, key=lambda x: -x[3]):
            print(f"   p_nrfi>={p0:.2f} AND lambda<={c:.2f}  n={n:>4}  u/bet={u:+.4f}  tot={u * n:+.2f}u")
    else:
        print("POSITIVE CELLS (u/bet > 0, n>=20): *** NONE ***")
    return winners


def main():
    rows = load_real()
    print(f"real-priced settled 2026 games: {len(rows)}  "
          f"over {len(set(r['date'] for r in rows))} days")

    # ---------- A: full-sample sweep ----------
    w_all = sweep(rows, "FULL 2026 real-priced sample")

    # ---------- A2: discover on May+June, confirm on July ----------
    disc = [r for r in rows if r["month"] in ("2026-04", "2026-05", "2026-06")]
    hold = [r for r in rows if r["month"] == "2026-07"]
    print(f"\n\nTIME SPLIT: discovery n={len(disc)} (Apr-Jun), holdout n={len(hold)} (Jul)")
    w_disc = sweep(disc, "DISCOVERY: Apr-Jun only")
    print()
    print("=" * 100)
    print("HOLDOUT CHECK -- every cell that looked positive in Apr-Jun, re-measured on JULY")
    print("=" * 100)
    if not w_disc:
        print("nothing to carry forward: no positive cell in the discovery half.")
    for p0, c, n, u in sorted(w_disc, key=lambda x: -x[3]):
        sel = [r for r in hold if r["p"] is not None and r["lam"] is not None
               and r["p"] >= p0 and r["lam"] <= c]
        print(summarize(sel, f"p>={p0:.2f} lam<={c:.2f}"))

    # ---------- named reference rules ----------
    print()
    print("=" * 100)
    print("REFERENCE RULES (incl. the historical gates), real DK prices, full 2026")
    print("=" * 100)
    for lab, fn in [
        ("bet every game", lambda r: True),
        ("old live gate p>=0.56", lambda r: r["p"] >= 0.56),
        ("post-6/07 candidate p>=0.62", lambda r: r["p"] >= 0.62),
        ("p>=0.60", lambda r: r["p"] >= 0.60),
        ("p>=0.60 AND lam<=0.55", lambda r: r["p"] >= 0.60 and r["lam"] <= 0.55),
        ("p>=0.56 AND lam<=0.65", lambda r: r["p"] >= 0.56 and r["lam"] <= 0.65),
        ("lam 0.85-0.95 only (the one +cell)", lambda r: 0.85 <= r["lam"] < 0.95),
        ("pitchers park (pf<0.97)", lambda r: r["park"] < 0.97),
        ("NRFI priced as dog (odds>0)", lambda r: r["odds"] > 0),
        ("NRFI cheap (implied<=0.50)", lambda r: implied(r["odds"]) <= 0.50),
    ]:
        sel = [r for r in rows if r["p"] is not None and r["lam"] is not None
               and r["park"] is not None and fn(r)]
        print(summarize(sel, lab))

    # ---------- B: hit-rate ceiling on the big backtests ----------
    print()
    print("=" * 100)
    print("B. HIT-RATE CEILING on the odds-free backtests (bounds ACCURACY, not profit)")
    print("   break-even at DK's typical NRFI price in the high-p region is ~0.575-0.585")
    print("=" * 100)
    files = {
        "2024": "backtest_2024-04-01_to_2024-09-30_truepit.csv",
        "2025": "backtest_2025-04-01_to_2025-09-30_truepit.csv",
        "2026a": "backtest_2026-04-01_to_2026-05-11_truepit.csv",
        "2026b": "backtest_2026-05-12_to_2026-05-26_truepit.csv",
    }
    data = {}
    for k, fn_ in files.items():
        with open(ROOT / "data" / "backtests" / fn_, encoding="utf-8") as f:
            rr = list(csv.DictReader(f))
        g = []
        for r in rr:
            a = (r.get("actual_side") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            g.append({"date": r["date"], "hit": 1 if a == "NRFI" else 0,
                      "p": fnum(r.get("nrfi_prob")), "lam": fnum(r.get("lambda_total")),
                      "park": fnum(r.get("park_factor"))})
        data[k] = [x for x in g if x["p"] is not None and x["lam"] is not None]
        print(f"  {k}: {len(data[k])} graded games, base NRFI rate "
              f"{sum(x['hit'] for x in data[k]) / max(1, len(data[k])):.4f}")

    print()
    print(f"{'rule':>34}" + "".join(f"{k:>18}" for k in files))
    for p0 in [0.50, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.70]:
        for c in [0.60, 0.75, 9.9]:
            lab = f"p>={p0:.2f} lam<={c:.2f}" if c < 9 else f"p>={p0:.2f}"
            cells_txt = ""
            for k in files:
                sel = [x for x in data[k] if x["p"] >= p0 and x["lam"] <= c]
                if len(sel) < 15:
                    cells_txt += f"{'n=' + str(len(sel)):>18}"
                else:
                    h = sum(x["hit"] for x in sel) / len(sel)
                    cells_txt += f"{h:>10.3f}/{len(sel):<7d}"[:18].rjust(18)
            print(f"{lab:>34}{cells_txt}")
        if c == 9.9:
            pass


if __name__ == "__main__":
    main()

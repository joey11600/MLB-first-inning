#!/usr/bin/env python3
"""tools/nrfi_dd_refute_056_125.py -- adversarial audit of the candidate rule

    RULE: lambda_lr_total <= 0.56 AND market_nrfi_odds >= -125  -> bet NRFI
    claim: 21 bets, 57.1% hit vs 53.6% break-even, +5.5% ROI

Read-only. Does not touch production files.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402


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


def worsen(o, cents=10):
    """Move an American price `cents` worse for the bettor."""
    if o >= 100:
        nn = o - cents
        return nn if nn >= 100 else -(200 - nn)
    return o - cents


def load26():
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    out = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("actual_result") or "").upper() not in ("NRFI", "YRFI"):
                continue
            fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
            try:
                tv, bv = rc._build_t1_b1_phase_e3(r, fp)
            except Exception:
                continue
            out.append({
                "date": r["date"], "away": r.get("away_team"), "home": r.get("home_team"),
                "t1": tv, "b1": bv,
                "y": 1 if (r.get("actual_result") or "").upper() == "NRFI" else 0,
                "odds": fnum(r.get("market_nrfi_odds")),
                "yodds": fnum(r.get("market_yrfi_odds")),
                "open": fnum(r.get("opened_nrfi_odds")),
                "csv_lam": fnum(r.get("lambda_lr_total")),
            })
    Xt = np.asarray([r["t1"] for r in out], float)
    Xb = np.asarray([r["b1"] for r in out], float)
    for r, p in zip(out, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return out


def day_boot_roi(sub, iters=20000, seed=7):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(payout(r["odds"]) if r["y"] else -1.0)
    days = list(byday.values())
    rng = np.random.default_rng(seed)
    k = len(days)
    out = []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for i in idx for x in days[i]]
        out.append(sum(v) / len(v))
    out.sort()
    return (out[int(0.025 * iters)], out[int(0.975 * iters)],
            sum(1 for x in out if x <= 0) / iters, len(days))


def main():
    s26 = load26()
    priced = [r for r in s26 if r["odds"] is not None]
    RULE = [r for r in priced if r["lam"] <= 0.56 and r["odds"] >= -125]
    n = len(RULE)
    hits = sum(r["y"] for r in RULE)
    pl = sum(payout(r["odds"]) if r["y"] else -1.0 for r in RULE)
    need = st.mean([implied(r["odds"]) for r in RULE])
    print("=" * 96)
    print("  A. REPRODUCTION (recomputed lambda from live LR weights)")
    print("=" * 96)
    print("  n=%d  %dW-%dL  hit=%.1f%%  need=%.1f%%  P/L=%+.2fu  ROI=%+.1f%%"
          % (n, hits, n - hits, 100 * hits / n, 100 * need, pl, 100 * pl / n))
    lo, hi, pneg, ndays = day_boot_roi(RULE)
    print("  day-block 95%% CI on ROI: [%+.1f%%, %+.1f%%]  P(ROI<=0)=%.3f  distinct days=%d"
          % (100 * lo, 100 * hi, pneg, ndays))

    R2 = [r for r in priced if (r["csv_lam"] is not None and r["csv_lam"] <= 0.56
                                and r["odds"] >= -125)]
    if R2:
        h2 = sum(r["y"] for r in R2)
        p2 = sum(payout(r["odds"]) if r["y"] else -1.0 for r in R2)
        print("\n  Using the CSV column lambda_lr_total verbatim: n=%d %dW-%dL hit=%.1f%% ROI=%+.1f%%"
              % (len(R2), h2, len(R2) - h2, 100 * h2 / len(R2), 100 * p2 / len(R2)))
    else:
        print("\n  CSV column lambda_lr_total: no qualifying rows")
    ov = len(set(id(x) for x in R2) & set(id(x) for x in RULE))
    print("  overlap between the two definitions: %d of %d rows" % (ov, n))

    print("\n" + "=" * 96)
    print("  B. FRAGILITY")
    print("=" * 96)
    wins = sorted([payout(r["odds"]) for r in RULE if r["y"]])
    for k in (1, 2, 3):
        if len(wins) >= k:
            delta = sum(wins[-k:]) + k
            print("  flip %d biggest winner(s) -> loser: P/L %+.2fu  ROI %+.1f%%"
                  % (k, pl - delta, 100 * (pl - delta) / n))
    print("  one game = %.1f%% of the sample" % (100.0 / n))

    print("\n" + "=" * 96)
    print("  C. PRICE SHOCK -- 5/10/15/20 cents worse (bet set held fixed)")
    print("=" * 96)
    print("  %6s%6s%8s%8s%9s%9s" % ("shock", "n", "hit%", "need%", "P/L u", "ROI%"))
    for cents in (0, 5, 10, 15, 20):
        sub = [dict(r, odds=worsen(r["odds"], cents)) for r in RULE]
        p = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
        nd = st.mean([implied(r["odds"]) for r in sub])
        hh = sum(r["y"] for r in sub) / len(sub)
        print("  %5dc%6d%8.1f%8.1f%+9.2f%+9.1f" % (cents, len(sub), 100 * hh, 100 * nd, p,
                                                   100 * p / len(sub)))
    for cents in (5, 10):
        band = [dict(r, odds=worsen(r["odds"], cents)) for r in priced if r["lam"] <= 0.56]
        sub = [r for r in band if r["odds"] >= -125]
        if sub:
            p = sum(payout(r["odds"]) if r["y"] else -1.0 for r in sub)
            print("  gate RE-APPLIED after a %dc shade: n=%d ROI=%+.1f%%"
                  % (cents, len(sub), 100 * p / len(sub)))

    print("\n" + "=" * 96)
    print("  D. WHAT IS THE PRICE FILTER DOING?")
    print("=" * 96)
    band = [r for r in priced if r["lam"] <= 0.56]
    print("  lambda<=0.56 band: n=%d, rule keeps %d (%.0f%%)" % (len(band), n, 100 * n / len(band)))
    kept = [r for r in band if r["odds"] >= -125]
    drop = [r for r in band if r["odds"] < -125]
    for nm, s in (("KEPT (>=-125)", kept), ("DROPPED (<-125)", drop)):
        if not s:
            continue
        print("  %-16s n=%3d  book-implied NRFI=%.1f%%  model raw p=%.1f%%  "
              "disagreement=%+.1fpp  hit=%.1f%%"
              % (nm, len(s), 100 * st.mean([implied(r["odds"]) for r in s]),
                 100 * st.mean([r["raw"] for r in s]),
                 100 * st.mean([r["raw"] - implied(r["odds"]) for r in s]),
                 100 * sum(r["y"] for r in s) / len(s)))

    print("\n" + "=" * 96)
    print("  E. TEMPORAL SPLIT WITHIN 2026")
    print("=" * 96)
    ds = sorted(set(r["date"] for r in RULE))
    mid = ds[len(ds) // 2]
    for nm, s in (("first half", [r for r in RULE if r["date"] < mid]),
                  ("second half", [r for r in RULE if r["date"] >= mid])):
        if not s:
            continue
        p = sum(payout(r["odds"]) if r["y"] else -1.0 for r in s)
        w = sum(r["y"] for r in s)
        print("  %-12s n=%3d  %dW-%dL  ROI=%+.1f%%  P/L=%+.2fu"
              % (nm, len(s), w, len(s) - w, 100 * p / len(s), p))
    bym = defaultdict(list)
    for r in RULE:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        s = bym[m]
        p = sum(payout(r["odds"]) if r["y"] else -1.0 for r in s)
        w = sum(r["y"] for r in s)
        print("    %s: n=%2d  %dW-%dL  P/L=%+.2fu" % (m, len(s), w, len(s) - w, p))
    print("\n  the %d bets fall on %d distinct days" % (n, len(ds)))
    print("\n  per-bet detail:")
    for r in sorted(RULE, key=lambda x: x["date"]):
        print("    %s %-4s@%-4s lam=%.3f raw=%.3f odds=%+5d imp=%.3f %s"
              % (r["date"], r["away"], r["home"], r["lam"], r["raw"], int(r["odds"]),
                 implied(r["odds"]), "WIN " if r["y"] else "LOSS"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

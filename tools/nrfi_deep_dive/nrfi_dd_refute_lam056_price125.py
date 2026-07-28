#!/usr/bin/env python3
"""tools/nrfi_dd_refute_lam056_price125.py -- ADVERSARIAL test of the proposed rule

    RULE: bet NRFI when  lambda_lr_total <= 0.56  AND  market_nrfi_odds >= -125

Claim under test: 21 bets, 57.1% hit vs 53.6% break-even, +5.5% ROI.
Search exposure claimed: 56-cell grid (8 lambda caps x 7 price floors).

Read-only.  Nothing here writes to the ledger or to production config.
"""
from __future__ import annotations
import csv, math, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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
    """Move a moneyline `cents` toward the book, in american-odds cents."""
    if o > 0:
        n = o - cents
        return n if n >= 100 else -(100 + (100 - n))
    n = o - cents
    return n


def load():
    rows = []
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = (r.get("actual_result") or "").upper()
            if a not in ("NRFI", "YRFI"):
                continue
            lam = fnum(r.get("lambda_lr_total"))
            if lam is None:
                continue
            rows.append({
                "date": r.get("date", ""),
                "lam": lam,
                "y": 1 if a == "NRFI" else 0,
                "o": fnum(r.get("market_nrfi_odds")),
                "p": fnum(r.get("nrfi_prob")),
            })
    return rows


def stats(sub):
    n = len(sub)
    if n == 0:
        return None
    hits = sum(r["y"] for r in sub)
    pl = sum(payout(r["o"]) if r["y"] else -1.0 for r in sub)
    need = st.mean([implied(r["o"]) for r in sub])
    return {"n": n, "hit": hits / n, "need": need, "pl": pl, "roi": pl / n,
            "hits": hits}


def day_boot(sub, iters=20000, seed=7):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(payout(r["o"]) if r["y"] else -1.0)
    days = list(byday.values())
    rng = np.random.default_rng(seed)
    k = len(days)
    out = []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for i in idx for x in days[i]]
        out.append(sum(v) / len(v))
    out = np.sort(np.asarray(out))
    return out[int(0.025 * iters)], out[int(0.975 * iters)], k, out


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
FLOORS = [-160, -140, -125, -115, -105, +100, +120]


def main():
    rows = load()
    priced = [r for r in rows if r["o"] is not None]
    print("=" * 96)
    print("  DATA".ljust(96))
    print("=" * 96)
    print(f"  graded 2026 rows ................ {len(rows)}")
    print(f"  with a REAL captured DK NRFI price {len(priced)}")
    print(f"  overall NRFI base rate .......... {100*sum(r['y'] for r in rows)/len(rows):.1f}%")
    print(f"  mean book-implied NRFI (priced) . {100*st.mean([implied(r['o']) for r in priced]):.1f}%")
    print(f"  actual NRFI rate (priced) ....... {100*sum(r['y'] for r in priced)/len(priced):.1f}%")

    # ---------------- 1. reproduce the claim -----------------------------
    rule = [r for r in priced if r["lam"] <= 0.56 and r["o"] >= -125]
    geo = [r for r in priced if r["lam"] <= 0.56]
    prc = [r for r in priced if r["o"] >= -125]
    print("\n" + "=" * 96)
    print("  1. REPRODUCING THE CLAIM (real captured DK prices only)")
    print("=" * 96)
    print(f"  {'subset':<40}{'n':>5}{'hit%':>8}{'need%':>8}{'P/L u':>9}{'ROI%':>9}")
    for name, sub in (("RULE lam<=0.56 AND price>=-125", rule),
                      ("geometry only  lam<=0.56", geo),
                      ("price only     price>=-125", prc),
                      ("all priced NRFI", priced)):
        s = stats(sub)
        print(f"  {name:<40}{s['n']:>5}{100*s['hit']:>8.1f}{100*s['need']:>8.1f}"
              f"{s['pl']:>+9.2f}{100*s['roi']:>+9.1f}")

    lo, hi, ndays, dist = day_boot(rule)
    s = stats(rule)
    print(f"\n  RULE day-block bootstrap (n={s['n']} bets over {ndays} distinct days)")
    print(f"    ROI 95% CI  [{100*lo:+.1f}%, {100*hi:+.1f}%]")
    print(f"    P(ROI <= 0) under resampling = {100*float((dist<=0).mean()):.1f}%")
    print(f"    bets per day: {s['n']/ndays:.2f}")

    # ---------------- 2. is the price filter mechanical? -----------------
    print("\n" + "=" * 96)
    print("  2. IS THE PRICE FILTER FINDING EDGE, OR JUST BUYING A LOWER BREAK-EVEN?")
    print("=" * 96)
    print("  If the filter finds real edge, hit% should RISE relative to need%.")
    print("  If it is mechanical, hit% stays flat and only need% falls.\n")
    print(f"  {'subset':<40}{'n':>5}{'hit%':>8}{'need%':>8}{'hit-need':>10}")
    for name, sub in (("lam<=0.56, price WORSE than -125", [r for r in geo if r["o"] < -125]),
                      ("lam<=0.56, price >= -125  (RULE)", rule),
                      ("lam>0.56,  price WORSE than -125", [r for r in priced if r["lam"] > 0.56 and r["o"] < -125]),
                      ("lam>0.56,  price >= -125", [r for r in priced if r["lam"] > 0.56 and r["o"] >= -125])):
        s = stats(sub)
        if s is None:
            continue
        print(f"  {name:<40}{s['n']:>5}{100*s['hit']:>8.1f}{100*s['need']:>8.1f}"
              f"{100*(s['hit']-s['need']):>+9.1f}pp")

    # ---------------- 3. 10 cents worse ----------------------------------
    print("\n" + "=" * 96)
    print("  3. PRICE ROBUSTNESS -- what if we get 10 cents worse than captured?")
    print("=" * 96)
    for cents in (0, 5, 10, 15):
        pl = sum(payout(worsen(r["o"], cents)) if r["y"] else -1.0 for r in rule)
        # re-apply the filter at the degraded price too (honest: the filter
        # is defined on the price we can actually get)
        sub2 = [r for r in priced if r["lam"] <= 0.56 and worsen(r["o"], cents) >= -125]
        pl2 = sum(payout(worsen(r["o"], cents)) if r["y"] else -1.0 for r in sub2)
        print(f"  -{cents:>2} cents:  same 21 bets ROI {100*pl/len(rule):+6.1f}%"
              f"   |  filter re-applied at degraded price: n={len(sub2):>3}"
              f" ROI {100*pl2/max(1,len(sub2)):+6.1f}%")

    # ---------------- 4. multiple comparisons ----------------------------
    print("\n" + "=" * 96)
    print("  4. MULTIPLE COMPARISONS -- how surprising is +5.5% given a 56-cell search?")
    print("=" * 96)
    cells = []
    for c in CAPS:
        for pf in FLOORS:
            sub = [r for r in priced if r["lam"] <= c and r["o"] >= pf]
            if len(sub) >= 20:
                cells.append((stats(sub)["roi"], c, pf, len(sub), sub))
    cells.sort(reverse=True)
    print(f"  cells with n>=20: {len(cells)} of {len(CAPS)*len(FLOORS)} evaluated")
    print(f"  {'rank':>5}{'lam<=':>8}{'price>=':>10}{'n':>6}{'ROI%':>9}")
    for i, (roi, c, pf, n, _) in enumerate(cells[:6], 1):
        print(f"  {i:>5}{('inf' if c > 9 else f'{c:.2f}'):>8}{pf:>+10d}{n:>6}{100*roi:>+9.1f}")

    # Null simulation: keep the SAME games, same lambdas, same prices, but
    # re-draw each outcome from the book's implied probability (= zero edge).
    # Then re-run the whole 56-cell search and record the best ROI.
    print("\n  NULL SIMULATION: assume the book is exactly right (zero edge on every")
    print("  game), keep the real slate/lambda/price structure, re-draw outcomes,")
    print("  and re-run the identical 56-cell search 5,000 times.")
    rng = np.random.default_rng(3)
    lam = np.asarray([r["lam"] for r in priced])
    od = np.asarray([r["o"] for r in priced])
    q = np.asarray([implied(o) for o in od])
    pay = np.asarray([payout(o) for o in od])
    masks = []
    for c in CAPS:
        for pf in FLOORS:
            m = (lam <= c) & (od >= pf)
            if m.sum() >= 20:
                masks.append(m)
    ITERS = 5000
    best = np.empty(ITERS)
    rulem = (lam <= 0.56) & (od >= -125)
    rule_null = np.empty(ITERS)
    for i in range(ITERS):
        y = (rng.random(len(q)) < q).astype(float)
        v = np.where(y > 0, pay, -1.0)
        best[i] = max(v[m].mean() for m in masks)
        rule_null[i] = v[rulem].mean()
    obs = stats(rule)["roi"]
    print(f"  observed best-cell ROI in the real data ....... {100*max(x[0] for x in cells):+.1f}%")
    print(f"  observed RULE cell ROI ........................ {100*obs:+.1f}%")
    print(f"  null distribution of the BEST of 56 cells: median {100*np.median(best):+.1f}%, "
          f"95th pct {100*np.percentile(best,95):+.1f}%")
    print(f"  P(best-of-56 >= +5.5% | ZERO EDGE) ............ {100*float((best>=obs).mean()):.1f}%")
    print(f"  P(this single cell >= +5.5% | ZERO EDGE) ...... {100*float((rule_null>=obs).mean()):.1f}%")

    # ---------------- 5. out-of-sample within 2026 -----------------------
    print("\n" + "=" * 96)
    print("  5. OUT-OF-SAMPLE: SPLIT THE ONLY SEASON THAT HAS PRICES")
    print("=" * 96)
    print("  2024/2025 backtests carry NO odds, so the PRICE half of this rule")
    print("  cannot be tested on another season at all.  The only split available")
    print("  is chronological inside 2026.\n")
    ds = sorted({r["date"] for r in priced})
    mid = ds[len(ds) // 2]
    print(f"  split date = {mid}")
    print(f"  {'half':<28}{'n':>5}{'hit%':>8}{'need%':>8}{'P/L u':>9}{'ROI%':>9}")
    for name, f in (("first half (<= mid)", lambda r: r["date"] <= mid),
                    ("second half (> mid)", lambda r: r["date"] > mid)):
        sub = [r for r in rule if f(r)]
        s = stats(sub)
        if s is None:
            print(f"  {name:<28}{0:>5}")
            continue
        print(f"  {name:<28}{s['n']:>5}{100*s['hit']:>8.1f}{100*s['need']:>8.1f}"
              f"{s['pl']:>+9.2f}{100*s['roi']:>+9.1f}")

    print("\n  Month by month:")
    print(f"  {'month':<10}{'n':>5}{'W':>4}{'L':>4}{'hit%':>8}{'need%':>8}{'P/L u':>9}")
    bym = defaultdict(list)
    for r in rule:
        bym[r["date"][:7]].append(r)
    for m in sorted(bym):
        s = stats(bym[m])
        print(f"  {m:<10}{s['n']:>5}{s['hits']:>4}{s['n']-s['hits']:>4}"
              f"{100*s['hit']:>8.1f}{100*s['need']:>8.1f}{s['pl']:>+9.2f}")

    # ---------------- 6. how fragile? ------------------------------------
    print("\n" + "=" * 96)
    print("  6. FRAGILITY -- flip the single biggest winner to a loss")
    print("=" * 96)
    s = stats(rule)
    wins = sorted([r for r in rule if r["y"]], key=lambda r: -payout(r["o"]))
    if wins:
        big = wins[0]
        pl2 = s["pl"] - payout(big["o"]) - 1.0
        print(f"  biggest win: {big['date']} at {big['o']:+.0f} (+{payout(big['o']):.2f}u)")
        print(f"  flip it to a loss -> P/L {pl2:+.2f}u, ROI {100*pl2/s['n']:+.1f}%")
    print(f"  bets needed to swing to break-even: rule is {s['hits']}W-{s['n']-s['hits']}L;"
          f" ONE fewer win => {100*(s['pl']-payout(st.mean([r['o'] for r in rule if r['y']]))-1)/s['n']:+.1f}% approx")

    # sensitivity of the two knobs
    print("\n  Knob sensitivity (does the cell sit on a plateau or a spike?):")
    print(f"  {'lam<=':>8}" + "".join(f"{('>='+f'{p:+d}'):>11}" for p in (-135, -130, -125, -120, -115)))
    for c in (0.52, 0.54, 0.56, 0.58, 0.60):
        line = f"  {c:>8.2f}"
        for pf in (-135, -130, -125, -120, -115):
            sub = [r for r in priced if r["lam"] <= c and r["o"] >= pf]
            if len(sub) < 5:
                line += f"{'.':>11}"
            else:
                line += f"{100*stats(sub)['roi']:>+9.1f}%({len(sub)})".rjust(11)
        print(line)

    # ---------------- 7. hit-rate half on other seasons ------------------
    print("\n" + "=" * 96)
    print("  7. THE HIT-RATE HALF ON OTHER SEASONS (no odds there -- accuracy only)")
    print("=" * 96)
    BT = ROOT / "data" / "backtests"
    for label, path, col in (("2024", BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv", "actual_side"),
                             ("2025", BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv", "actual_side")):
        if not path.exists():
            continue
        n = k = 0
        nt = kt = 0
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                a = (r.get(col) or "").upper()
                if a not in ("NRFI", "YRFI"):
                    continue
                lam = fnum(r.get("lambda_lr_total")) or fnum(r.get("combined_lambda"))
                y = 1 if a == "NRFI" else 0
                nt += 1
                kt += y
                if lam is not None and lam <= 0.56:
                    n += 1
                    k += y
        if n:
            print(f"  {label}: lam<=0.56 -> {k}/{n} = {100*k/n:.1f}% NRFI"
                  f"   (season base {100*kt/nt:.1f}%, lift {100*(k/n - kt/nt):+.1f}pp)")
        else:
            print(f"  {label}: no usable lambda_lr_total column in this backtest CSV")
    return 0


if __name__ == "__main__":
    sys.exit(main())

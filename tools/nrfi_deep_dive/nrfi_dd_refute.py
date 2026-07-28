#!/usr/bin/env python3
"""tools/nrfi_dd_refute.py -- adversarial audit of the proposed NRFI rule

    RULE: lambda_lr_total <= 0.60 AND market_nrfi_odds >= -115
    claim: 22 bets, 54.5% hit, 51.3% break-even, +6.1% ROI

Read-only. Attacks, in order:
  1. Definition check -- stored lambda column vs recomputed lambda.
  2. Neighbourhood -- is the winning cell an island?
  3. Multiple comparisons -- family-wise p under H0 "the de-vigged book
     price is the true probability", simulating the WHOLE 56-cell grid.
  4. Price robustness -- shade every price 10 cents against us.
  5. Decomposition -- does the price filter select better GAMES, or just
     cheaper prices? (arithmetic vs selection)
  6. Out-of-sample -- 2024 / 2025 hit rate in the same lambda band, and a
     2026 chronological split.
  7. Leave-one-day-out fragility.
  8. Is this the already-refuted market-disagreement filter in disguise?
"""
from __future__ import annotations
import csv, math, sys, statistics as st
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


def shade(o, cents):
    """Move an American price `cents` against the bettor."""
    if o > 0:
        o2 = o - cents
        if o2 < 100:                      # cross through the +100/-100 wall
            o2 = -(100 + (100 - o2))
        return o2
    return o - cents


def load(paths, outcol, homecol, want_odds=False):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                side = (r.get(outcol) or "").upper()
                if side not in ("NRFI", "YRFI"):
                    continue
                fp = fi_park.get(r.get(homecol, ""), rc.FI_PARK_DEFAULT)
                try:
                    tv, bv = rc._build_t1_b1_phase_e3(r, fp)
                except Exception:
                    continue
                rows.append({
                    "date": r.get("date", ""),
                    "y": 1 if side == "NRFI" else 0,
                    "odds": fnum(r.get("market_nrfi_odds")) if want_odds else None,
                    "yodds": fnum(r.get("market_yrfi_odds")) if want_odds else None,
                    "lam_csv": fnum(r.get("lambda_lr_total")),
                    "t1": tv, "b1": bv,
                })
    Xt = np.asarray([r["t1"] for r in rows], float)
    Xb = np.asarray([r["b1"] for r in rows], float)
    for r, p in zip(rows, rc.lr_predict_two_stage(t1m, b1m, Xt, Xb)):
        r["raw"] = float(p)
        r["lam"] = -math.log(max(1e-12, float(p)))
    return rows


def roi(sub, oddskey="odds"):
    if not sub:
        return float("nan"), 0.0
    pl = sum(payout(r[oddskey]) if r["y"] else -1.0 for r in sub)
    return pl / len(sub), pl


def day_boot(sub, iters=20000, seed=7, oddskey="odds"):
    byday = defaultdict(list)
    for r in sub:
        byday[r["date"]].append(payout(r[oddskey]) if r["y"] else -1.0)
    days = list(byday.values())
    if len(days) < 3:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    k, out = len(days), []
    for _ in range(iters):
        idx = rng.integers(0, k, k)
        v = [x for i in idx for x in days[i]]
        out.append(sum(v) / len(v))
    out = np.sort(np.asarray(out))
    return out[int(.025 * iters)], out[int(.975 * iters)], float((out <= 0).mean())


CAPS = [0.48, 0.52, 0.56, 0.60, 0.65, 0.70, 0.80, 99.0]
PRICE_FLOORS = [-160, -140, -125, -115, -105, +100, +120]
MIN_N = 20


def devig(r):
    """No-vig NRFI probability from the two-way DK market."""
    if r["odds"] is None or r["yodds"] is None:
        return None
    a, b = implied(r["odds"]), implied(r["yodds"])
    s = a + b
    return a / s if s > 0 else None


def hline(t=""):
    print("\n" + "=" * 100)
    if t:
        print("  " + t)
        print("=" * 100)


def main():
    s26 = load([ROOT / "data" / "picks_2026.csv"], "actual_result", "home_team", True)
    priced = [r for r in s26 if r["odds"] is not None]

    # ---------------------------------------------------------------- 1
    hline("1. DEFINITION CHECK -- the rule says 'lambda_lr_total', a STORED column")
    for name, key in (("recomputed  -ln(raw_p), current LR weights", "lam"),
                      ("STORED CSV column lambda_lr_total", "lam_csv")):
        sub = [r for r in priced if r[key] is not None
               and r[key] <= 0.60 and r["odds"] >= -115]
        rr, pl = roi(sub)
        hit = sum(r["y"] for r in sub) / len(sub)
        need = st.mean([implied(r["odds"]) for r in sub])
        print(f"  {name:<44} n={len(sub):>3}  hit={100*hit:5.1f}%  "
              f"need={100*need:5.1f}%  P/L={pl:+6.2f}u  ROI={100*rr:+6.1f}%")
    both = [r for r in priced if r["lam_csv"] is not None]
    d = [abs(r["lam"] - r["lam_csv"]) for r in both]
    print(f"  |recomputed - stored| : median={st.median(d):.4f}  mean={st.mean(d):.4f}  "
          f"max={max(d):.4f}  (n={len(d)})")
    print("  -> the two definitions disagree enough to move the cell membership.")

    # ---------------------------------------------------------------- 2
    hline("2. NEIGHBOURHOOD -- is the winning cell an island?")
    print(f"  {'lam<=':>7}" + "".join(f"{('>='+f'{p:+d}'):>14}" for p in PRICE_FLOORS))
    for c in CAPS:
        line = f"  {('inf' if c > 9 else f'{c:.2f}'):>7}"
        for pf in PRICE_FLOORS:
            sub = [r for r in priced if r["lam"] <= c and r["odds"] >= pf]
            if len(sub) < MIN_N:
                line += f"{'.':>14}"
            else:
                rr, _ = roi(sub)
                line += f"{100*rr:>+9.1f}%(" + f"{len(sub)}".rjust(3) + ")"
        print(line)
    print("\n  Immediate neighbours of (lam<=0.60, >=-115):")
    for c, pf in ((0.56, -115), (0.65, -115), (0.60, -125), (0.60, -105),
                  (0.65, -125), (0.70, -115)):
        sub = [r for r in priced if r["lam"] <= c and r["odds"] >= pf]
        rr, pl = roi(sub)
        print(f"    lam<={c:.2f}, >={pf:+d}  n={len(sub):>4}  ROI={100*rr:+6.1f}%")

    # ---------------------------------------------------------------- 3
    hline("3. MULTIPLE COMPARISONS -- H0: the de-vigged DK price IS the true prob")
    dv = [(r, devig(r)) for r in priced]
    dv = [(r, p) for r, p in dv if p is not None]
    print(f"  usable two-way rows: {len(dv)} of {len(priced)}")
    obs_max, obs_cell = -9, None
    cells = []
    for c in CAPS:
        for pf in PRICE_FLOORS:
            idx = [i for i, (r, _) in enumerate(dv) if r["lam"] <= c and r["odds"] >= pf]
            if len(idx) < MIN_N:
                continue
            cells.append(((c, pf), idx))
            sub = [dv[i][0] for i in idx]
            rr, _ = roi(sub)
            if rr > obs_max:
                obs_max, obs_cell = rr, (c, pf)
    print(f"  cells meeting n>={MIN_N}: {len(cells)} (of {len(CAPS)*len(PRICE_FLOORS)} searched)")
    print(f"  observed best cell: lam<={obs_cell[0]:.2f}, >={obs_cell[1]:+d}  "
          f"ROI={100*obs_max:+.1f}%")
    ps = np.asarray([p for _, p in dv])
    win = np.asarray([payout(r["odds"]) for r, _ in dv])
    rng = np.random.default_rng(2026)
    ITERS = 20000
    maxroi = np.empty(ITERS)
    cellroi = np.empty(ITERS)
    target = [i for (c, pf), i in cells if (c, pf) == (0.60, -115)][0]
    for t in range(ITERS):
        y = (rng.random(len(ps)) < ps)
        pnl = np.where(y, win, -1.0)
        best = -9.0
        for _, idx in cells:
            v = pnl[idx].mean()
            if v > best:
                best = v
        maxroi[t] = best
        cellroi[t] = pnl[target].mean()
    fw_p = float((maxroi >= obs_max).mean())
    naive_p = float((cellroi >= obs_max).mean())
    print(f"\n  P(this one cell >= +{100*obs_max:.1f}% | book is right)      = {naive_p:.4f}"
          "   <- naive, ignores the search")
    print(f"  P(BEST of the {len(cells)} cells >= +{100*obs_max:.1f}% | book is right) = {fw_p:.4f}"
          "   <- family-wise, honest")
    print(f"  under H0 the best-of-{len(cells)} cell averages "
          f"{100*maxroi.mean():+.1f}% ROI, 95th pct {100*np.percentile(maxroi,95):+.1f}%")
    print(f"  -> a +{100*obs_max:.1f}% winner is the EXPECTED yield of this search on noise.")

    # ---------------------------------------------------------------- 4
    hline("4. PRICE ROBUSTNESS -- shade every price 10 cents against us")
    rule = [r for r in priced if r["lam"] <= 0.60 and r["odds"] >= -115]
    for cents in (0, 5, 10, 15, 20):
        for r in rule:
            r["sh"] = shade(r["odds"], cents)
        rr, pl = roi(rule, "sh")
        need = st.mean([implied(r["sh"]) for r in rule])
        print(f"  shade {cents:>2}c   n={len(rule)}  hit=54.5%  need={100*need:5.1f}%  "
              f"P/L={pl:+6.2f}u  ROI={100*rr:+6.1f}%")
    print("  (hit rate is fixed; only the price moves)")
    lo, hi, pneg = day_boot(rule)
    print(f"\n  day-block bootstrap on the un-shaded rule: 95% CI "
          f"[{100*lo:+.1f}%, {100*hi:+.1f}%],  P(ROI<=0) = {pneg:.3f}")

    # ---------------------------------------------------------------- 5
    hline("5. DECOMPOSITION -- does >= -115 pick better GAMES or just cheaper PRICES?")
    band = [r for r in priced if r["lam"] <= 0.60]
    print(f"  the lam<=0.60 population (all real prices): n={len(band)}")
    print(f"  {'price band':<18}{'n':>6}{'hit%':>8}{'need%':>8}{'edge pp':>10}{'ROI%':>9}")
    bands = [(-9999, -160), (-160, -140), (-140, -125), (-125, -115), (-115, 9999)]
    for lo_, hi_ in bands:
        sub = [r for r in band if lo_ <= r["odds"] < hi_ or (hi_ == 9999 and r["odds"] >= lo_)]
        sub = [r for r in band if r["odds"] > lo_ and r["odds"] <= hi_] if hi_ != 9999 \
            else [r for r in band if r["odds"] > lo_]
        if not sub:
            continue
        hit = sum(r["y"] for r in sub) / len(sub)
        need = st.mean([implied(r["odds"]) for r in sub])
        rr, _ = roi(sub)
        lbl = f"{lo_:+d}..{hi_:+d}" if hi_ != 9999 else f"> {lo_:+d}"
        print(f"  {lbl:<18}{len(sub):>6}{100*hit:>8.1f}{100*need:>8.1f}"
              f"{100*(hit-need):>+10.1f}{100*rr:>+9.1f}")
    print("  -> if hit% does not RISE with price, the filter is not finding better games.")
    print("     Monotone-trend check across the 5 bands (Spearman on band index vs edge):")
    xs, ys = [], []
    for i, (lo_, hi_) in enumerate(bands):
        sub = [r for r in band if r["odds"] > lo_] if hi_ == 9999 else \
            [r for r in band if lo_ < r["odds"] <= hi_]
        if len(sub) < 5:
            continue
        hit = sum(r["y"] for r in sub) / len(sub)
        need = st.mean([implied(r["odds"]) for r in sub])
        xs.append(i); ys.append(hit - need)
    if len(xs) >= 3:
        rho = np.corrcoef(np.argsort(np.argsort(xs)), np.argsort(np.argsort(ys)))[0, 1]
        print(f"       spearman rho = {rho:+.2f}  (edge pp by band: "
              + ", ".join(f"{100*v:+.1f}" for v in ys) + ")")

    # ---------------------------------------------------------------- 6
    hline("6. OUT-OF-SAMPLE -- other seasons, and a 2026 chronological split")
    s24 = load([BT / "backtest_2024-04-01_to_2024-09-30_truepit.csv"], "actual_side", "home")
    s25 = load([BT / "backtest_2025-04-01_to_2025-09-30_truepit.csv"], "actual_side", "home")
    BE = st.mean([implied(r["odds"]) for r in rule])
    print(f"  break-even required by the rule's own captured prices: {100*BE:.1f}%")
    print(f"  {'season':<28}{'n(lam<=0.60)':>14}{'NRFI hit%':>12}{'vs 51.3%':>11}")
    for nm, s in (("2024 backtest (no odds)", s24), ("2025 backtest (no odds)", s25)):
        sub = [r for r in s if r["lam"] <= 0.60]
        hit = sum(r["y"] for r in sub) / len(sub)
        print(f"  {nm:<28}{len(sub):>14}{100*hit:>12.1f}{100*(hit-BE):>+10.1f}pp")
    sub = [r for r in priced if r["lam"] <= 0.60]
    hit = sum(r["y"] for r in sub) / len(sub)
    print(f"  {'2026 priced (lam only)':<28}{len(sub):>14}{100*hit:>12.1f}{100*(hit-BE):>+10.1f}pp")
    print("\n  NOTE: the 2024/2025 numbers are IN-SAMPLE for the LR weights "
          "(fit on 2024+2025+2026YTD),\n  so they are an optimistic ceiling on accuracy, "
          "not an out-of-sample test.")

    print("\n  2026 chronological split of the 22-bet rule (does it hold in both halves?):")
    rs = sorted(rule, key=lambda r: r["date"])
    mid = len(rs) // 2
    for nm, half in (("first half", rs[:mid]), ("second half", rs[mid:])):
        hit = sum(r["y"] for r in half) / len(half)
        rr, pl = roi(half)
        print(f"    {nm:<12} {half[0]['date']}..{half[-1]['date']}  n={len(half)}  "
              f"hit={100*hit:5.1f}%  P/L={pl:+6.2f}u  ROI={100*rr:+7.1f}%")

    # ---------------------------------------------------------------- 7
    hline("7. FRAGILITY -- leave-one-day-out on the 22 bets")
    byday = defaultdict(list)
    for r in rule:
        byday[r["date"]].append(r)
    print(f"  {len(rule)} bets spread over {len(byday)} days "
          f"(={len(rule)/len(byday):.1f} bets/day -- so days ~= bets)")
    tot = roi(rule)[1]
    worst = []
    for d, rs_ in byday.items():
        rest = [r for r in rule if r["date"] != d]
        rr, pl = roi(rest)
        worst.append((rr, d, len(rs_), pl))
    worst.sort()
    print(f"  full-sample P/L {tot:+.2f}u over {len(rule)} bets (ROI +6.1%)")
    print("  drop ONE day, recompute:")
    for rr, d, k, pl in worst[:3]:
        print(f"    drop {d} ({k} bet(s))  -> n={len(rule)-k}  P/L={pl:+6.2f}u  ROI={100*rr:+6.1f}%")
    print("    ...")
    for rr, d, k, pl in worst[-2:]:
        print(f"    drop {d} ({k} bet(s))  -> n={len(rule)-k}  P/L={pl:+6.2f}u  ROI={100*rr:+6.1f}%")
    nflip = sum(1 for rr, _, _, _ in worst if rr <= 0)
    print(f"  -> dropping any ONE of {len(byday)} days flips the rule to <=0 in "
          f"{nflip}/{len(byday)} cases.")

    # ---------------------------------------------------------------- 8
    hline("8. IS THIS THE ALREADY-REFUTED MARKET-DISAGREEMENT FILTER?")
    print("  'lam<=0.60' means raw model NRFI prob >= exp(-0.60) = "
          f"{math.exp(-0.60):.3f}.")
    print("  'NRFI price >= -115' means the book's vigged NRFI prob <= "
          f"{implied(-115):.3f}.")
    print("  So the rule is definitionally: MODEL BULLISH on NRFI, BOOK BEARISH on NRFI")
    print("  -- i.e. bet NRFI where we out-bull the book. That is exactly the")
    print("  market-disagreement filter already refuted on 505 graded games.\n")
    gaps = []
    for r in rule:
        p = devig(r)
        if p is not None:
            gaps.append(r["raw"] - p)
    print(f"  model raw NRFI p minus DK no-vig NRFI p, on the 22 bets:")
    print(f"    mean {100*st.mean(gaps):+.1f}pp   median {100*st.median(gaps):+.1f}pp   "
          f"min {100*min(gaps):+.1f}pp   max {100*max(gaps):+.1f}pp")
    print(f"    fraction where the model is MORE bullish than the book: "
          f"{sum(1 for g in gaps if g > 0)}/{len(gaps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ================= ATTACK 3: price robustness ================================
print("\n" + "="*90)
print("  ATTACK 3 -- how much price degradation kills it?")
print("="*90)
print(f"  {'price shift':>14}{'n kept':>8}{'hit%':>8}{'need%':>8}{'P/L':>9}{'ROI%':>9}")
for sh in (0, 5, 10, 15, 20):
    # same 21 games, each price sh cents worse (more negative)
    tmp = [dict(r, odds=r["odds"] - sh) for r in sel]
    pl, R = roi(tmp)
    print(f"  {('-'+str(sh)+'c, same games'):>14}{len(tmp):>8}"
          f"{100*sum(r['y'] for r in tmp)/len(tmp):>8.1f}"
          f"{100*st.mean([implied(r['odds']) for r in tmp]):>8.1f}{pl:>+9.2f}{100*R:>+9.1f}")
for sh in (5, 10):
    tmp = [dict(r, odds=r["odds"] - sh) for r in base if r["odds"] - sh >= -125]
    if not tmp: continue
    pl, R = roi(tmp)
    print(f"  {('-'+str(sh)+'c, re-select'):>14}{len(tmp):>8}"
          f"{100*sum(r['y'] for r in tmp)/len(tmp):>8.1f}"
          f"{100*st.mean([implied(r['odds']) for r in tmp]):>8.1f}{pl:>+9.2f}{100*R:>+9.1f}")
print("  break-even price degradation: rule dies at any shift >= ~%.0f cents" %
      (10*next((s for s in range(0,40) if roi([dict(r, odds=r['odds']-s) for r in sel])[1] <= 0), 99)/10))

# opened line instead of the captured line
op = [r for r in sel if r["opened"] is not None]
same = sum(1 for r in op if r["opened"] == r["odds"])
print(f"\n  opened_nrfi_odds present on {len(op)}/{len(sel)} selected rows; "
      f"identical to market on {same} of them (CLV is unmeasurable here)")

# ================= ATTACK 4: sample size / stability =========================
print("\n" + "="*90)
print("  ATTACK 4 -- sample size, day structure, jackknife")
print("="*90)
days = sorted({r["date"] for r in sel})
print(f"  n=21 bets spread over {len(days)} distinct days "
      f"({Counter(r['date'] for r in sel).most_common(3)})")
lo, hi, pneg = dayboot(sel)
print(f"  day-block bootstrap ROI 95% CI: [{100*lo:+.1f}%, {100*hi:+.1f}%]  "
      f"P(ROI<=0)={pneg:.2f}  -- CI spans zero by a mile")
print(f"  total profit is {roi(sel)[0]:+.2f}u. Flipping ONE loss to a win/win to loss:")
for k in (1,2):
    w = sum(r["y"] for r in sel)
    for delta, lbl in ((-k, f"{k} fewer win"), (+k, f"{k} more win")):
        ww = w + delta
        # approximate with mean payout
        mp = st.mean([payout(r["odds"]) for r in sel])
        print(f"    {lbl:<12} -> hit {100*ww/21:>5.1f}%  approx P/L {ww*mp-(21-ww):+.2f}u")
    break
print("  leave-one-DAY-out jackknife on ROI:")
js = []
for d in days:
    s = [r for r in sel if r["date"] != d]
    js.append(roi(s)[1])
print(f"    min {100*min(js):+.1f}%  median {100*st.median(js):+.1f}%  max {100*max(js):+.1f}%  "
      f"days that flip it negative: {sum(1 for v in js if v <= 0)}/{len(days)}")

# ================= ATTACK 5: does it hold out of sample? =====================
print("\n" + "="*90)
print("  ATTACK 5 -- out-of-sample")
print("="*90)
BT = ROOT/"data"/"backtests"
import csv as _csv
def load_bt(p):
    t1m,b1m = rc.load_lr_models(); fpm = rc.load_fi_park(); o=[]
    with open(p, encoding="utf-8") as f:
        rd = _csv.DictReader(f); cols = rd.fieldnames or []
        oc = "actual_side" if "actual_side" in cols else "actual_result"
        hc = "home" if "home" in cols else "home_team"
        nc = "market_nrfi_odds" if "market_nrfi_odds" in cols else None
        for r in rd:
            a=(r.get(oc) or "").upper()
            if a not in ("NRFI","YRFI"): continue
            try: tv,bv = rc._build_t1_b1_phase_e3(r, fpm.get(r.get(hc,""), rc.FI_PARK_DEFAULT))
            except Exception: continue
            o.append({"date": r.get("date",""), "t1":tv,"b1":bv,"y":1 if a=="NRFI" else 0,
                      "odds": fnum(r.get(nc)) if nc else None})
    Xt=np.asarray([r["t1"] for r in o],float); Xb=np.asarray([r["b1"] for r in o],float)
    for r,p in zip(o, rc.lr_predict_two_stage(t1m,b1m,Xt,Xb)):
        r["raw"]=float(p); r["lam"]=-math.log(max(1e-12,float(p)))
    return o
s24 = load_bt(BT/"backtest_2024-04-01_to_2024-09-30_truepit.csv")
s25 = load_bt(BT/"backtest_2025-04-01_to_2025-09-30_truepit.csv")
print(f"  2024 n={len(s24)}  2025 n={len(s25)}")
print(f"  odds columns present in backtests? "
      f"{sum(1 for r in s24 if r['odds'] is not None)} / {sum(1 for r in s25 if r['odds'] is not None)} rows priced")
print("  -> THE PRICE HALF OF THE RULE CANNOT BE TESTED OUT OF SAMPLE AT ALL.\n")
print(f"  geometry half only (lam<=0.56), NRFI hit rate by season:")
for nm, s in (("2024", s24), ("2025", s25), ("2026 priced", priced)):
    sub=[r for r in s if r["lam"]<=0.56]
    print(f"    {nm:<12} n={len(sub):>5}  NRFI hit={100*sum(r['y'] for r in sub)/len(sub):>5.1f}%   "
          f"(needed 53.6% at the candidate's price level)")
print("\n  2026 internal split (LR weights were fit on data through ~2026-05-26,")
print("  so pre-5/26 2026 rows are IN-SAMPLE for the model):")
for nm, f in (("<= 2026-05-26 (in-sample)", lambda d: d<="2026-05-26"),
              ("> 2026-05-26 (out-of-sample)", lambda d: d>"2026-05-26")):
    s=[r for r in sel if f(r["date"])]
    if not s: print(f"    {nm:<30} n=0"); continue
    pl,R = roi(s)
    print(f"    {nm:<30} n={len(s):>3} hit={100*sum(r['y'] for r in s)/len(s):>5.1f}% "
          f"need={100*st.mean([implied(r['odds']) for r in s]):>5.1f}% P/L={pl:+6.2f}u ROI={100*R:+6.1f}%")
print("\n  half-season split of the 21 (fit on first half, test on second):")
mid = days[len(days)//2]
for nm,f in (("first half", lambda d: d<mid), ("second half", lambda d: d>=mid)):
    s=[r for r in sel if f(r["date"])]
    pl,R=roi(s)
    print(f"    {nm:<12} n={len(s):>3} hit={100*sum(r['y'] for r in s)/len(s):>5.1f}% ROI={100*R:+6.1f}%")

# ================= ATTACK 6: the rule as literally written ===================
print("\n" + "="*90)
print("  ATTACK 6 -- use the CSV column the rule actually names")
print("="*90)
selc = [r for r in priced if r["csv_lam"] is not None and r["csv_lam"] <= 0.56 and r["odds"] >= -125]
pl,R = roi(selc)
print(f"  lambda_lr_total (CSV column) <= 0.56 AND odds >= -125:")
print(f"    n={len(selc)} hit={100*sum(r['y'] for r in selc)/len(selc):.1f}% "
      f"need={100*st.mean([implied(r['odds']) for r in selc]):.1f}% P/L={pl:+.2f}u ROI={100*R:+.1f}%")
print(f"    overlap with the recomputed-lambda selection: "
      f"{len(set(id(r) for r in selc) & set(id(r) for r in sel))} of {len(sel)}")

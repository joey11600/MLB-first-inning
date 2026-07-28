#!/usr/bin/env python3
"""tools/nrfi_dd_disagreement_rerun.py -- 2026-07-28 re-run of the ONE idea the
2026-06-07 NRFI rework left open.

The closing note of that rework read:
    "The ONLY non-dead idea is structural: NRFI is tracked as LEAN now, so
     re-run tools/nrfi_market_disagreement.py monthly. If the 10pp+
     disagreement bucket's CI ever clears 0 on a much larger sample
     (was n=15, hopeless), reconsider."

This script does that, and fixes three methodology problems in the original:

  1. ORIGINAL BOOTSTRAP RESAMPLED BETS, NOT DAYS. Bets on the same slate share
     weather, umpire pools and a common model state; they are correlated. An
     i.i.d. bet bootstrap therefore reports a CI that is too NARROW. We block
     bootstrap over DAYS and report the design effect (how much the CI widens).
  2. DUPLICATE ROWS. Six game_pk values appear on two dates (postponed then
     resumed). The original counted both legs. We dedupe on (game_pk,
     game_number), keeping the last row.
  3. NO OUT-OF-SAMPLE SLICE. The 1,128-game sample today CONTAINS the 505-game
     sample the hypothesis was generated on. Re-running on the superset is not
     confirmation. We split at 2026-06-07 and report the post-06-07 games as a
     genuine held-out slice.

Also tests the INVERSE (the "mirror trade"): if our NRFI disagreements are us
being wrong, then betting YRFI at the captured YRFI price on exactly those
games should be positive. Same buckets, opposite side, real prices.

Read-only. Analysis only -- touches no production file.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
SEED, B = 20260728, 20000
SPLIT_DATE = "2026-06-07"          # the date the prior rework closed


def imp(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def payout(a):
    s = (a or "").strip()
    try:
        n = float(s)
    except (ValueError, TypeError):
        return None
    if n == 0:
        return None
    return (n / 100.0) if n > 0 else (100.0 / abs(n))


# ---------------------------------------------------------------------------
# bootstraps
# ---------------------------------------------------------------------------

def boot_ci_bets(pls, seed=SEED):
    """i.i.d. bootstrap over BETS -- what the original script did. Too narrow."""
    if len(pls) < 8:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    arr = np.asarray(pls, dtype=float)
    means = rng.choice(arr, size=(B, len(arr)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def boot_ci_days(rows, seed=SEED):
    """BLOCK bootstrap over DAYS. Resample whole slates with replacement, then
    pool every bet in the resampled slates and take the mean. This is the
    correct CI when bets on a slate are correlated."""
    if len(rows) < 8:
        return (float("nan"), float("nan"))
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r["pl"])
    days = list(byday.keys())
    if len(days) < 5:
        return (float("nan"), float("nan"))
    blocks = [np.asarray(byday[d], dtype=float) for d in days]
    sums = np.array([b.sum() for b in blocks])
    cnts = np.array([len(b) for b in blocks])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(B, len(days)))
    tot = sums[idx].sum(axis=1)
    cnt = cnts[idx].sum(axis=1)
    means = tot / np.maximum(cnt, 1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def load():
    raw = list(csv.DictReader(open(PICKS, encoding="utf-8")))
    # dedupe postponed/resumed doubles: keep LAST row per (game_pk, game_number)
    seen = {}
    for r in raw:
        key = (r.get("game_pk", ""), r.get("game_number", ""))
        seen[key] = r
    rows, dropped = [], len(raw) - len(seen)
    for r in seen.values():
        try:
            fa = int(float(r["fi_away_runs"]))
            fh = int(float(r["fi_home_runs"]))
            m_nrfi = float(r["nrfi_prob"])
        except (ValueError, TypeError, KeyError):
            continue
        iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
        pN, pY = payout(r.get("market_nrfi_odds")), payout(r.get("market_yrfi_odds"))
        if not iN or not iY or pN is None or pY is None:
            continue
        mkt_nrfi = iN / (iN + iY)
        nrfi = 1 if (fa + fh) == 0 else 0
        try:
            lam = float(r.get("lambda_lr_total") or "nan")
        except (ValueError, TypeError):
            lam = float("nan")
        rows.append({
            "date": r["date"],
            "matchup": f"{r.get('away_team','')}@{r.get('home_team','')}",
            "model": m_nrfi,
            "mkt": mkt_nrfi,
            "disagree": m_nrfi - mkt_nrfi,
            "nrfi": nrfi,
            "lam": lam,
            "pl": (pN if nrfi else -1.0),          # bet NRFI at captured price
            "pl_fade": (-1.0 if nrfi else pY),     # bet YRFI at captured price
            "be_nrfi": iN,
            "be_yrfi": iY,
        })
    rows.sort(key=lambda x: x["date"])
    return rows, dropped


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def show(rows, label, side="nrfi", ci=False, both_ci=False):
    if not rows:
        print(f"  {label:<28} n=   0")
        return None
    key = "pl" if side == "nrfi" else "pl_fade"
    bekey = "be_nrfi" if side == "nrfi" else "be_yrfi"
    n = len(rows)
    wins = sum(r["nrfi"] if side == "nrfi" else (1 - r["nrfi"]) for r in rows)
    pl = sum(r[key] for r in rows)
    be = sum(r[bekey] for r in rows) / n
    ndays = len({r["date"] for r in rows})
    s = (f"  {label:<28} n={n:>4} d={ndays:>3}  hit {wins/n*100:>4.1f}% "
         f"(need {be*100:.1f}%)  ROI {pl/n*100:>+5.1f}%  P&L {pl:>+6.1f}u")
    lo = hi = float("nan")
    if ci or both_ci:
        pls = [r[key] for r in rows]
        blo, bhi = boot_ci_bets(pls)
        rr = [{"date": r["date"], "pl": r[key]} for r in rows]
        lo, hi = boot_ci_days(rr)
        verdict = "REAL (CI>0)" if lo > 0 else "unproven (CI spans 0)"
        s += f"  dayCI[{lo*100:+.0f}%,{hi*100:+.0f}%] {verdict}"
        if both_ci and not math.isnan(blo):
            de = (hi - lo) / (bhi - blo) if (bhi - blo) else float("nan")
            s += f"  (betCI[{blo*100:+.0f}%,{bhi*100:+.0f}%], widen x{de:.2f})"
    print(s)
    return {"n": n, "days": ndays, "roi": pl / n, "lo": lo, "hi": hi,
            "hit": wins / n, "be": be, "pl": pl}


BUCKETS = [
    ("model<=market (agree/bear)", lambda d: d < 0.00),
    ("small +disagree 0-3pp", lambda d: 0.00 <= d < 0.03),
    ("mid   +disagree 3-6pp", lambda d: 0.03 <= d < 0.06),
    ("big   +disagree 6-10pp", lambda d: 0.06 <= d < 0.10),
    ("HUGE  +disagree 10pp+", lambda d: d >= 0.10),
]


def bucket_table(rows, side, ci_last_two=True, both_ci=False):
    lean = [r for r in rows if r["model"] >= 0.50]
    out = {}
    for i, (label, fn) in enumerate(BUCKETS):
        sub = [r for r in lean if fn(r["disagree"])]
        want_ci = ci_last_two and i >= 3
        out[label] = show(sub, label, side=side, ci=want_ci, both_ci=both_ci and want_ci)
    return out


def sample_size_needed(sd, target_roi, alpha=1.96, power=0.84, deff=1.0):
    """bets needed to detect a TRUE roi of `target_roi` at 95%/80% power."""
    return ((alpha + power) ** 2) * (sd ** 2) * deff / (target_roi ** 2)


def main():
    rows, dropped = load()
    days = sorted({r["date"] for r in rows})
    print("=" * 116)
    print("  NRFI MARKET-DISAGREEMENT RE-RUN -- 2026-07-28")
    print("=" * 116)
    print(f"  graded games with REAL captured DK NRFI+YRFI prices : {len(rows)}"
          f"   ({len(days)} distinct slates, {days[0]} .. {days[-1]})")
    print(f"  duplicate (game_pk,game_number) rows dropped        : {dropped}")
    print(f"  prior run (2026-06-07) had                          : 505 games, "
          f"n=15 in the 10pp+ bucket")
    print(f"  GROWTH                                              : "
          f"+{len(rows)-505} games ({len(rows)/505:.2f}x)")

    print("\n" + "=" * 116)
    print("  1. BET NRFI  --  BASELINE (no disagreement filter), real prices")
    print("=" * 116)
    show(rows, "all priced games", ci=True, both_ci=True)
    show([r for r in rows if r["model"] >= 0.50], "model leans NRFI (>=.50)", ci=True, both_ci=True)
    show([r for r in rows if r["model"] >= 0.56], "old STRONG gate (>=.56)", ci=True, both_ci=True)
    show([r for r in rows if r["model"] >= 0.62], "last STRONG gate (>=.62)", ci=True, both_ci=True)

    print("\n" + "=" * 116)
    print("  2. BET NRFI  --  THE TEST: among model-NRFI-leaning games, by DISAGREEMENT")
    print("     (full sample -- NOTE: this CONTAINS the 505 games the hypothesis came from)")
    print("=" * 116)
    full = bucket_table(rows, "nrfi", both_ci=True)

    print("\n" + "=" * 116)
    print(f"  3. BET NRFI  --  SPLIT at {SPLIT_DATE} (the date the prior rework closed)")
    print("=" * 116)
    pre = [r for r in rows if r["date"] < SPLIT_DATE]
    post = [r for r in rows if r["date"] >= SPLIT_DATE]
    print(f"\n  --- IN-SAMPLE  (< {SPLIT_DATE}, n={len(pre)}) : the sample the idea was born on")
    bucket_table(pre, "nrfi")
    print(f"\n  --- HELD OUT   (>= {SPLIT_DATE}, n={len(post)}) : games the hypothesis never saw")
    post_t = bucket_table(post, "nrfi", both_ci=True)

    print("\n" + "=" * 116)
    print("  4. THE MIRROR TRADE -- fade our own NRFI signal: bet YRFI at the captured")
    print("     YRFI price on exactly the games where we most out-bull the book")
    print("=" * 116)
    show(rows, "all priced games (bet YRFI)", side="yrfi", ci=True, both_ci=True)
    show([r for r in rows if r["model"] >= 0.50], "fade model-leans-NRFI (>=.50)",
         side="yrfi", ci=True, both_ci=True)
    show([r for r in rows if r["model"] >= 0.62], "fade STRONG-NRFI zone (>=.62)",
         side="yrfi", ci=True, both_ci=True)
    print()
    mirror = bucket_table(rows, "yrfi", both_ci=True)
    print(f"\n  --- MIRROR, HELD OUT (>= {SPLIT_DATE}) ---")
    bucket_table(post, "yrfi")

    print("\n" + "=" * 116)
    print("  5. HOW BIG A SAMPLE WOULD ACTUALLY RESOLVE THE 10pp+ BUCKET?")
    print("=" * 116)
    lean = [r for r in rows if r["model"] >= 0.50]
    huge = [r for r in lean if r["disagree"] >= 0.10]
    pls = np.asarray([r["pl"] for r in huge], dtype=float)
    sd = float(pls.std(ddof=1))
    # design effect measured from the two bootstraps
    blo, bhi = boot_ci_bets([r["pl"] for r in huge])
    dlo, dhi = boot_ci_days([{"date": r["date"], "pl": r["pl"]} for r in huge])
    deff = ((dhi - dlo) / (bhi - blo)) ** 2 if (bhi - blo) else 1.0
    rate = len(huge) / len(days)
    print(f"  observed  : n={len(huge)} bets over {len(days)} priced slates "
          f"({rate:.2f} qualifying bets/day)")
    print(f"  per-bet SD: {sd:.3f}   day-clustering design effect: x{deff:.2f} "
          f"(variance inflation)")
    print(f"  observed ROI {100*pls.mean():+.1f}%  (a POSITIVE {100*(dhi):+.0f}% is the "
          f"most the day-CI still permits)")
    print()
    print(f"  {'true edge you want to detect':<34}{'bets needed':>13}{'priced slates':>16}"
          f"{'MLB seasons':>14}")
    for tgt in (0.10, 0.05, 0.03, 0.02):
        nb = sample_size_needed(sd, tgt, deff=deff)
        nd = nb / rate if rate else float("nan")
        print(f"  ROI = {tgt*100:>4.1f}%{'':<24}{nb:>13,.0f}{nd:>16,.0f}"
              f"{nd/162:>14,.1f}")
    print("\n  (a 'priced slate' here = one day on which we captured DK prices; the")
    print("   2026 season has produced 88 of them, so 162 is generous.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

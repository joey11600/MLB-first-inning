#!/usr/bin/env python3
"""
tools/kelly_backtest.py -- what would bankroll-fraction (Kelly) staking
have returned on the real 2026 bet history?

CONTEXT / WHY THIS IS DANGEROUS
-------------------------------
CLAUDE.md records that the operator previously REJECTED Kelly sizing
(T4.25-27) in favour of flat 1u.  This script exists because the operator
explicitly asked for it on 2026-07-27.  Read the health warning it prints.

Kelly stakes are proportional to your claimed edge:

    f* = (p * b - q) / b        b = decimal payout - 1,  q = 1 - p

so the stake is only as trustworthy as `p`.  The 2026-07-27 investigation
measured that this model's `p` is systematically OVERCONFIDENT exactly
where it bets most:

    model says 59.2%  ->  reality 50.3%   (157 bets)
    model says 62.3%  ->  reality 55.1%   (107 bets)
    model says 67.3%  ->  reality 67.1%   ( 85 bets)

Kelly fed an inflated p does not just bet badly, it bets BIG badly. So
this script reports every variant against three probability sources:

  claimed   -- the number the production calibrator actually printed
  shrunk    -- claimed p pulled toward the market's implied probability,
               which is what an honest reading of the calibration table
               above implies
  market    -- the sportsbook's own implied probability (a control: by
               construction this has zero edge and should lose the vig)

Staking variants: flat 1u (today's policy), full / half / quarter Kelly.

Bankroll model: start at 100u so that "1u" == 1% of starting bankroll,
matching the operator's existing mental model.  All of a given day's
bets are sized off the bankroll as it stood at the START of that day --
you cannot compound intraday on games that run concurrently.

Usage:
    python tools/kelly_backtest.py
    python tools/kelly_backtest.py --selective   # also run the p>=0.64 gate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

START_BANKROLL = 100.0
# Hard sanity cap: never stake more than this share of bankroll on one bet.
# Full Kelly on a claimed 10pp edge at even money asks for ~20%; uncapped
# that is how bankrolls die. Reported alongside the uncapped count.
MAX_STAKE_FRACTION = 0.25


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def decimal_b(odds: float) -> float:
    """Net decimal payout per 1 staked."""
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def implied(odds: float) -> float:
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def kelly_fraction(p: float, odds: float) -> float:
    b = decimal_b(odds)
    q = 1.0 - p
    f = (p * b - q) / b
    return max(f, 0.0)


# ---------------------------------------------------------------------------
# Load the real bet history
# ---------------------------------------------------------------------------

def load_bets():
    """Every graded, placed 2026 bet that has a REAL captured DK price."""
    from db.supabase_writer import _get_client
    client = _get_client()
    rows = []
    if client is not None:
        PAGE, off = 1000, 0
        while True:
            res = (client.table("picks_2026").select("*")
                   .order("date").range(off, off + PAGE - 1).execute())
            d = res.data or []
            rows += d
            if len(d) < PAGE:
                break
            off += PAGE
    if not rows:
        import csv
        with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    bets = []
    for r in rows:
        if (r.get("bet_placed") or "").upper() != "Y":
            continue
        if (r.get("graded_result") or "") not in ("WIN", "LOSS"):
            continue
        side = (r.get("pick_side") or "").upper()
        p = fnum(r.get("yrfi_prob")) if side == "YRFI" else fnum(r.get("nrfi_prob"))
        od = (fnum(r.get("market_yrfi_odds")) if side == "YRFI"
              else fnum(r.get("market_nrfi_odds")))
        if p is None or od is None:
            continue
        bets.append({
            "date": r["date"],
            "game": f"{r.get('away_team')}@{r.get('home_team')}",
            "side": side,
            "p_claimed": p,
            "odds": od,
            "win": r["graded_result"] == "WIN",
        })
    bets.sort(key=lambda x: (x["date"], x["game"]))
    return bets


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(bets, stake_fn, label):
    """Run the ledger chronologically.  Returns a result dict."""
    bank = START_BANKROLL
    peak = bank
    max_dd = 0.0
    n = w = 0
    staked_total = 0.0
    biggest_stake_frac = 0.0
    n_capped = 0
    curve = []

    by_day = defaultdict(list)
    for b in bets:
        by_day[b["date"]].append(b)

    for day in sorted(by_day):
        day_bank = bank            # size everything off the morning bankroll
        day_pnl = 0.0
        for b in by_day[day]:
            frac = stake_fn(b)
            # Epsilon, not 0: a zero-edge bet yields a Kelly fraction of
            # ~1e-17 rather than exactly 0, which would otherwise show up
            # as "N bets, 0.0u turnover" and read as a bug.
            if frac <= 1e-9:
                continue
            if frac > MAX_STAKE_FRACTION:
                n_capped += 1
                frac = MAX_STAKE_FRACTION
            stake = day_bank * frac
            if stake <= 0 or stake > bank:
                stake = min(stake, max(bank, 0.0))
            if stake <= 0:
                continue
            biggest_stake_frac = max(biggest_stake_frac, frac)
            n += 1
            staked_total += stake
            if b["win"]:
                w += 1
                day_pnl += stake * decimal_b(b["odds"])
            else:
                day_pnl -= stake
        bank += day_pnl
        peak = max(peak, bank)
        if peak > 0:
            max_dd = max(max_dd, (peak - bank) / peak)
        curve.append((day, bank))
        if bank <= 0:
            break

    return {
        "label": label,
        "bets": n,
        "wins": w,
        "final": bank,
        "profit": bank - START_BANKROLL,
        "roi_on_turnover": (bank - START_BANKROLL) / staked_total * 100 if staked_total else 0.0,
        "turnover": staked_total,
        "max_dd": max_dd * 100,
        "biggest_stake_pct": biggest_stake_frac * 100,
        "n_capped": n_capped,
        "curve": curve,
        "busted": bank <= 0,
    }


def shrink_toward_market(p: float, odds: float, weight: float) -> float:
    """Pull the model's claimed probability toward the market's implied
    probability.  weight=0 -> pure model, weight=1 -> pure market."""
    return (1 - weight) * p + weight * implied(odds)


def report(results, title):
    print("\n" + "=" * 100)
    print(f"  {title}")
    print("=" * 100)
    print(f"  {'staking plan':<34}{'bets':>6}{'W-L':>10}{'final bank':>12}"
          f"{'profit':>11}{'max DD':>9}{'top stake':>11}")
    for r in results:
        wl = f"{r['wins']}-{r['bets']-r['wins']}"
        bust = "  BUSTED" if r["busted"] else ""
        print(f"  {r['label']:<34}{r['bets']:>6}{wl:>10}{r['final']:>11.2f}u"
              f"{r['profit']:>+10.2f}u{r['max_dd']:>8.1f}%{r['biggest_stake_pct']:>10.1f}%{bust}")


def walk_forward(bets):
    """The honest version of the selective run.

    Section B picks the 0.64 gate using the whole season and then scores
    itself on that same season -- the threshold is in-sample, so its
    profit is an upper bound, not an expectation.  Here the threshold is
    re-chosen every day using ONLY bets that had already settled, so each
    day's decision could genuinely have been made that morning.
    """
    print("\n" + "=" * 100)
    print("  C. WALK-FORWARD (honest) -- threshold re-picked each day from prior settled bets only")
    print("=" * 100)

    dates = sorted({b["date"] for b in bets})
    CANDS = [0.56, 0.58, 0.60, 0.62, 0.64, 0.66]
    MIN_PRIOR, MIN_SURVIVORS = 40, 15

    def chooser():
        """Returns date -> threshold decided from strictly-prior data."""
        out = {}
        for d in dates:
            prior = [b for b in bets if b["date"] < d]
            if len(prior) < MIN_PRIOR:
                out[d] = None
                continue
            best = None
            for th in CANDS:
                g = [b for b in prior if b["p_claimed"] >= th]
                if len(g) < MIN_SURVIVORS:
                    continue
                roi = sum((decimal_b(b["odds"]) if b["win"] else -1.0) for b in g) / len(g)
                if best is None or roi > best[1]:
                    best = (th, roi)
            out[d] = best[0] if best else None
        return out

    gate = chooser()
    live = [b for b in bets if gate.get(b["date"]) is not None
            and b["p_claimed"] >= gate[b["date"]]]
    skipped_days = sum(1 for d in dates if gate.get(d) is None)
    print(f"  {len(live)} of {len(bets)} bets survive the walk-forward gate")
    print(f"  ({skipped_days} early days had too little history to choose a threshold "
          f"and were sat out entirely)")
    if not live:
        print("  nothing to simulate")
        return

    flat = sum((decimal_b(b["odds"]) if b["win"] else -1.0) for b in live)
    hit = sum(1 for b in live if b["win"]) / len(live)
    print(f"  flat-1u on the walk-forward selection: {hit:.1%} hit, {flat:+.2f}u")

    variants = [
        ("flat 1u", lambda b: 0.01),
        ("full Kelly  (claimed p)", lambda b: kelly_fraction(b["p_claimed"], b["odds"])),
        ("half Kelly  (claimed p)", lambda b: 0.50 * kelly_fraction(b["p_claimed"], b["odds"])),
        ("quarter Kelly (claimed p)", lambda b: 0.25 * kelly_fraction(b["p_claimed"], b["odds"])),
        ("eighth Kelly (claimed p)", lambda b: 0.125 * kelly_fraction(b["p_claimed"], b["odds"])),
    ]
    res = [simulate(live, fn, lbl) for lbl, fn in variants]
    report(res, f"C. WALK-FORWARD selection -- {len(live)} bets, no hindsight in the gate")
    print("\n  This is the number to plan around.  Section B's figure assumes you knew")
    print("  the best threshold in April; this one does not.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selective", action="store_true",
                    help="also run everything through the p>=0.64 gate")
    args = ap.parse_args()

    bets = load_bets()
    print(f"Loaded {len(bets)} graded, placed 2026 bets with a real captured DK price.")
    print(f"Bankroll model: start {START_BANKROLL:.0f}u, so flat '1u' = 1% of starting bank.")
    print(f"Stake cap: {MAX_STAKE_FRACTION:.0%} of bankroll on any single bet.")

    flat_pl = sum((decimal_b(b["odds"]) if b["win"] else -1.0) for b in bets)
    hit = sum(1 for b in bets if b["win"]) / len(bets)
    print(f"\nReference -- the ACTUAL flat-1u result on these bets: "
          f"{hit:.1%} hit, {flat_pl:+.2f}u")

    # -----------------------------------------------------------------
    # Health warning
    # -----------------------------------------------------------------
    print("\n" + "!" * 100)
    print("  HEALTH WARNING -- Kelly is only as good as the probability fed into it.")
    print("  Measured 2026 calibration on placed bets:")
    print("     model claims 59.2%  ->  actually won 50.3%   (157 bets)")
    print("     model claims 62.3%  ->  actually won 55.1%   (107 bets)")
    print("     model claims 67.3%  ->  actually won 67.1%   ( 85 bets)")
    print("  The first two buckets are where ~75% of the volume lives, and the model")
    print("  overstates them by 7-9 percentage points.  Kelly turns that overstatement")
    print("  into oversized stakes.  Read the 'claimed' rows below as the REALISTIC")
    print("  outcome of shipping Kelly today, not the 'shrunk' rows.")
    print("!" * 100)

    variants = [
        ("flat 1u (today's policy)", lambda b: 0.01),
        ("full Kelly  (claimed p)", lambda b: kelly_fraction(b["p_claimed"], b["odds"])),
        ("half Kelly  (claimed p)", lambda b: 0.50 * kelly_fraction(b["p_claimed"], b["odds"])),
        ("quarter Kelly (claimed p)", lambda b: 0.25 * kelly_fraction(b["p_claimed"], b["odds"])),
        ("full Kelly  (shrunk 50% to mkt)",
         lambda b: kelly_fraction(shrink_toward_market(b["p_claimed"], b["odds"], 0.5), b["odds"])),
        ("half Kelly  (shrunk 50% to mkt)",
         lambda b: 0.50 * kelly_fraction(shrink_toward_market(b["p_claimed"], b["odds"], 0.5), b["odds"])),
        ("full Kelly  (market p = control)",
         lambda b: kelly_fraction(implied(b["odds"]), b["odds"])),
    ]

    res = [simulate(bets, fn, lbl) for lbl, fn in variants]
    report(res, f"A. ALL {len(bets)} PLACED BETS -- current selection (STRONG gate, p_nrfi<0.44)")

    print("\n  Turnover / efficiency:")
    print(f"  {'staking plan':<34}{'turnover':>12}{'ROI on turnover':>18}{'stakes hitting cap':>20}")
    for r in res:
        print(f"  {r['label']:<34}{r['turnover']:>11.1f}u{r['roi_on_turnover']:>17.2f}%"
              f"{r['n_capped']:>20}")

    if args.selective:
        sel = [b for b in bets if b["p_claimed"] >= 0.64]
        flat_sel = sum((decimal_b(b["odds"]) if b["win"] else -1.0) for b in sel)
        hs = sum(1 for b in sel if b["win"]) / len(sel)
        print(f"\n\nSelective universe: {len(sel)} bets at model p >= 0.64  "
              f"({hs:.1%} hit, flat-1u {flat_sel:+.2f}u)")
        res2 = [simulate(sel, fn, lbl) for lbl, fn in variants]
        report(res2, f"B. SELECTIVE -- only the {len(sel)} bets at model p >= 0.64")
        print("\n  Turnover / efficiency:")
        print(f"  {'staking plan':<34}{'turnover':>12}{'ROI on turnover':>18}{'stakes hitting cap':>20}")
        for r in res2:
            print(f"  {r['label']:<34}{r['turnover']:>11.1f}u{r['roi_on_turnover']:>17.2f}%"
                  f"{r['n_capped']:>20}")

        walk_forward(bets)

        out = ROOT / "data" / "diagnostics" / "kelly_backtest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({
                "generated_for": "2026-07-27 operator request",
                "all_bets": [{k: v for k, v in r.items() if k != "curve"} for r in res],
                "selective": [{k: v for k, v in r.items() if k != "curve"} for r in res2],
            }, f, indent=2)
        print(f"\n  Wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

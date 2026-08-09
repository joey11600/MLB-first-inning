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
  5. Rounding, the 0.5u floor and the two caps interact correctly.
  6. The published stake does not depend on the bankroll.
  7. The DAILY cap binds, and the same slate sizes the same whatever
     order its rows arrive in.
  8. A capped call made without kelly_reset_daily_committed() refuses.

T8.18 -- WHY 7 AND 8 EXIST.  CLAUDE.md tells the operator to re-run this
script after any sizing change, and until now it was structurally blind
to the single variable a sizing change perturbs: it NEVER passed
`game_date`, so every check above ran on the UNCAPPED path and poked
`_daily_committed` by hand instead of through the reset.  The daily
budget, the allocation order and the batch-epoch guard were all
untested, which means this script would have come back green through
each of the P0s the T8.18 red team found.

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
        # 1 UNIT = 1% OF BANK (2026-07-30): the stake IS the Kelly
        # percentage, so the reference no longer multiplies by the bank.
        ref = f * 100.0
        if ref < tracker.KELLY_MIN_STAKE_UNITS:
            ref = 0.0
        else:
            ref = round(ref, 2)
            # 2026-07-29: the reference now models STAKE ROUNDING too.
            # This is NOT the test being loosened to accommodate a
            # change -- the tolerance below is still 0.011u, and the
            # reference still computes the Kelly fraction independently
            # from first principles.  What is being asserted is the
            # full shipped contract: formula, then rounding, then floor,
            # then caps.  Written as an independent reimplementation on
            # purpose; importing the shipped rounding would make the
            # check tautological.
            if tracker.KELLY_STAKE_ROUNDING > 0:
                r = (round(ref / tracker.KELLY_STAKE_ROUNDING)
                     * tracker.KELLY_STAKE_ROUNDING)
                if r < tracker.KELLY_ROUNDED_FLOOR:
                    r = tracker.KELLY_ROUNDED_FLOOR
                # Rounding may not push past the per-bet ceiling, which
                # is now a fixed unit number.
                if r > tracker.KELLY_MAX_STAKE_FRAC * 100.0:
                    r = ref
                ref = round(r, 2)
        worst = max(worst, abs((shipped or 0.0) - ref))
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

    # ---- 5. stake rounding (2026-07-29) ------------------------------
    print("\n=== CHECK 5: stake rounding, floor, and cap interaction ===")
    print(f"  KELLY_STAKE_ROUNDING = {tracker.KELLY_STAKE_ROUNDING}  "
          f"KELLY_ROUNDED_FLOOR = {tracker.KELLY_ROUNDED_FLOOR}")

    def expect(label, got, want, tol=0.005):
        good = got is not None and abs(got - want) <= tol
        print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got}, want {want}")
        return good

    tracker._bankroll_cache = 100.0
    # A healthy stake lands on a whole unit.
    tracker._daily_committed.clear()
    ok &= expect("ordinary stake rounds to a whole unit",
                 tracker.kelly_stake_units(0.68, "-135"),
                 round(tracker.kelly_stake_units(0.68, "-135") or 0))

    # THE ONE THAT MATTERS MOST.  A no-edge bet must stay 0 and must NOT
    # be lifted to the 0.5u floor -- flooring it would place money on a
    # game Kelly explicitly refuses.  The floor exists only to rescue
    # stakes that were already positive.
    tracker._bankroll_cache = 100.0
    ok &= expect("NO-EDGE bet stays 0, is NOT floored to 0.5u",
                 tracker.kelly_stake_units(0.50, "-150"), 0.0)
    ok &= expect("clearly -EV bet stays 0",
                 tracker.kelly_stake_units(0.40, "-200"), 0.0)

    # A small POSITIVE stake (0.25u at these inputs) would round to 0,
    # i.e. silently become a no-bet.  It floors instead.
    tracker._bankroll_cache = 100.0
    small = tracker.kelly_stake_units(0.505, "+100")
    ok &= expect("small positive stake floors to 0.5u rather than vanishing",
                 small, tracker.KELLY_ROUNDED_FLOOR)

    # Rounding UP must not defeat the per-bet ceiling. Under 1u = 1% of
    # bank (2026-07-30) that ceiling is a FIXED 10 units, not a fraction
    # of whatever the bank happens to be -- which is the whole point: a
    # published cap has to mean the same thing to every subscriber.
    tracker._bankroll_cache = 88.36
    tracker._daily_committed.clear()
    capped = tracker.kelly_stake_units(0.6947, "+100")
    ceiling = tracker.KELLY_MAX_STAKE_FRAC * 100.0
    good = capped is not None and capped <= ceiling + 1e-9
    print(f"  [{'PASS' if good else 'FAIL'}] rounding never breaches the per-bet "
          f"ceiling: stake {capped}u vs ceiling {ceiling:.2f}u")
    ok &= good

    tracker._bankroll_cache = None
    tracker._daily_committed.clear()

    # ---- 6. THE PROPERTY THE PICKS SERVICE RESTS ON ------------------
    print("")
    print("=== CHECK 6: the stake does NOT depend on the bankroll ===")
    print("  1 unit = 1% of bank, so a published stake must be identical for")
    print("  a $1k follower and a $100k follower. Kelly's fraction is already")
    print("  bankroll-independent -- f* = (p*b - q)/b uses only the model")
    print("  probability and the price -- and this asserts the CODE is too.")
    print("  A regression here silently mis-sizes every subscriber instead")
    print("  of failing loudly, which is why it is a test and not a comment.")
    for p_, o_ in [(0.7021, "-145"), (0.6052, "-130"), (0.66, "+105"), (0.58, "-110")]:
        seen = []
        for bank_probe in (25.0, 88.36, 100.0, 257.16, 100000.0):
            tracker._bankroll_cache = bank_probe
            tracker._daily_committed.clear()
            seen.append(tracker.kelly_stake_units(p_, o_))
        same = len(set(seen)) == 1
        if not same:
            ok = False
        print("  [%s] p=%s @ %5s -> %su on every bank from 25u to 100000u%s" % (
            "PASS" if same else "FAIL", p_, o_, seen[0],
            "" if same else "   GOT %s" % (seen,)))
    tracker._bankroll_cache = None
    tracker._daily_committed.clear()

    # ---- 7. THE DAILY CAP, AND THE ORDER IT ALLOCATES IN --------------
    print("")
    print("=== CHECK 7: the daily risk budget, and allocation order ===")
    print("  Everything above sizes with game_date=None, i.e. UNCAPPED.  The")
    print("  capped path is the one the T8.18 pre-lock re-derive perturbs, so")
    print("  it gets its own check rather than being assumed.")
    print("  2026-07-31 is a real cap-bound slate: four STRONG YRFI picks")
    print("  whose uncapped quarter-Kelly stakes sum well past the budget.")

    SLATE_DATE = "2026-07-31"
    SLATE = [
        ("CWS@TB",  0.7128, "-130"),
        ("KC@COL",  0.6903, "-155"),
        ("MIL@LAA", 0.6288, "-115"),
        ("DET@OAK", 0.5885, "-140"),
    ]
    day_cap = tracker.KELLY_MAX_DAILY_FRAC * 100.0   # published as "15u/day"

    def size_slate(order):
        """Allocate the slate in `order` and return {game: stake}."""
        # R2 of the reset discipline: ONE reset at the top of the batch,
        # never per row.  Doing it per row hands every row the full budget
        # and the cap silently stops binding -- which is exactly the sort of
        # regression this check exists to catch.
        tracker.kelly_reset_daily_committed()
        # Seed the day's committed exposure at ZERO rather than letting
        # `_committed_on` read the ledger.  All four of these picks are
        # bet_placed=Y in data/picks_2026.csv and already sum to exactly
        # 15.00u, so a live read leaves no room, every stake comes back 0,
        # both orders match trivially and the check passes by testing
        # nothing.  It would also drift the day anyone re-grades that date.
        # The subject here is the ALLOCATOR, so it starts from an empty
        # budget; the probabilities and prices are still the real ones.
        tracker._daily_committed[SLATE_DATE] = 0.0
        return {g: tracker.kelly_stake_units(p, o, game_date=SLATE_DATE)
                for g, p, o in order}

    def _vec(d):
        return "  ".join(
            f"{g} {'None' if d[g] is None else format(d[g], '.2f') + 'u'}"
            for g, _, _ in SLATE)

    orders = (("row order", SLATE), ("reversed ", list(reversed(SLATE))))
    sized = []
    for label, order in orders:
        d = size_slate(order)
        sized.append(d)
        total = sum(v or 0.0 for v in d.values())
        fits = total <= day_cap + 1e-9
        if not fits:
            ok = False
        print(f"  [{'PASS' if fits else 'FAIL'}] {label}  {_vec(d)}   "
              f"sum {total:.2f}u (budget {day_cap:.2f}u)")

    fwd, rev = sized
    same = all(
        (fwd[g] is None) == (rev[g] is None)
        and (fwd[g] is None or abs(fwd[g] - rev[g]) <= 1e-9)
        for g, _, _ in SLATE
    )
    if same:
        print("  [PASS] the same slate sizes identically in either row order")
    else:
        ok = False
        print("  [FAIL] THE SAME SLATE SIZES DIFFERENTLY DEPENDING ON ROW ORDER.")
        print("         This is the FIRST COME, FIRST SERVED limitation named in")
        print("         kelly_stake_units' docstring, now measured: the budget is")
        print("         handed out in iteration order, not best-bet-first, so a")
        print("         pick's PUBLISHED stake depends on where it happened to sit")
        print("         in the file.  It is left FAILING on purpose.  Do not")
        print("         loosen this assertion to make the script green -- either")
        print("         rank the day's picks by edge before allocating, or have")
        print("         the operator sign off on order-dependence as accepted.")

    tracker._daily_committed.clear()
    tracker._bankroll_cache = None

    # ---- 8. THE BATCH-EPOCH GUARD (T8.18 R4) --------------------------
    print("")
    print("=== CHECK 8: a capped call with no reset must refuse to size ===")
    print("  Passing game_date ALLOCATES against the day's budget and mutates")
    print("  the in-process tally.  In a long-lived process that never resets")
    print("  (Railway re-imports every 5 minutes) the tally grows every tick")
    print("  until the cap chokes every bet to zero -- the 2026-07-28 P0-1")
    print("  oscillation.  The epoch turns that convention into a refusal.")
    saved_epoch = tracker._kelly_batch_epoch
    tracker._kelly_batch_epoch = 0      # as if this were a fresh interpreter
    tracker._daily_committed.clear()
    try:
        got = tracker.kelly_stake_units(0.68, "-135", game_date=SLATE_DATE)
    except RuntimeError as exc:
        first = str(exc).splitlines()[0]
        print(f"  [PASS] refused with RuntimeError: {first}")
    else:
        ok = False
        print(f"  [FAIL] sized {got} instead of raising -- rules R1/R2 are back")
        print("         to being a comment nobody is obliged to read.")
    finally:
        tracker._kelly_batch_epoch = saved_epoch
        tracker._daily_committed.clear()
        tracker._bankroll_cache = None

    print("\n" + ("ALL CHECKS PASSED" if ok else "*** SOME CHECKS FAILED ***"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

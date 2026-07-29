"""Read-only numeric audit of the Kelly staking mathematics.

Does NOT modify any file.  Run from repo root:  python tools/prob_audit/staking_audit.py
"""
import csv
import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tracker  # noqa: E402

SEP = "=" * 78


def hand_b(odds):
    """b = decimal odds - 1, computed independently with exact fractions."""
    o = Fraction(str(odds))
    if o > 0:
        dec = 1 + o / 100          # +150 -> 2.50
    else:
        dec = 1 + 100 / (-o)       # -140 -> 1.714285...
    return dec - 1


def hand_full_kelly(p, odds):
    b = hand_b(odds)
    q = 1 - Fraction(str(p))
    f = (Fraction(str(p)) * b - q) / b
    return float(f), float(b)


def hand_stake(p, odds, bank=100.0, frac=0.25, cap=0.10, mind=0.10):
    f, _ = hand_full_kelly(p, odds)
    f = max(f, 0.0)
    f = min(f * frac, cap)
    s = bank * f
    if s < mind:
        return 0.0
    return round(s, 2)


print(SEP)
print(" 1. CONSTANTS AS LOADED")
print(SEP)
for k in ("KELLY_ENABLED", "KELLY_FRACTION", "KELLY_BANKROLL_UNITS",
          "KELLY_MAX_STAKE_FRAC", "KELLY_MIN_STAKE_UNITS",
          "KELLY_MAX_DAILY_FRAC", "KELLY_BANKROLL_EPOCH"):
    print(f"   {k:<24} = {getattr(tracker, k)}")

print()
print(SEP)
print(" 2. payout_per_unit()  vs exact b = decimal-1")
print(SEP)
maxd = 0.0
for odds in (-400, -250, -140, -135, -120, -110, -105, 100, 105, 110, 125, 150, 200, 350):
    got = tracker.payout_per_unit(str(odds))
    want = float(hand_b(odds))
    maxd = max(maxd, abs(got - want))
    print(f"   {odds:>6}  code b={got:.10f}  exact b={want:.10f}  d={abs(got-want):.2e}")
print(f"   MAX |diff| = {maxd:.3e}")

print()
print(SEP)
print(" 3. kelly_fraction_of_bankroll()  vs exact f* = (p*b - q)/b")
print(SEP)
maxd = 0.0
cases = [(0.65, -140), (0.60, -110), (0.5238, -110), (0.50, -110), (0.45, -110),
         (0.62, 100), (0.40, 150), (0.34, 200), (0.70, -250), (0.5, -150),
         (0.5238095238, -110), (0.6666667, -200)]
for p, odds in cases:
    got = tracker.kelly_fraction_of_bankroll(p, str(odds))
    want, b = hand_full_kelly(p, odds)
    want_clamped = max(want, 0.0)
    maxd = max(maxd, abs(got - want_clamped))
    print(f"   p={p:<12} {odds:>5}  b={b:.6f}  code f*={got:.8f}  exact={want_clamped:.8f}"
          f"  raw={want:+.6f}")
print(f"   MAX |diff| = {maxd:.3e}")

print()
print(SEP)
print(" 4. EV / breakeven cross-check: f* must be 0 exactly at p = implied prob")
print(SEP)
for odds in (-400, -140, -110, 100, 150, 250):
    imp = tracker.american_to_prob(str(odds))
    f_at = tracker.kelly_fraction_of_bankroll(imp, str(odds))
    f_lo = tracker.kelly_fraction_of_bankroll(imp - 0.01, str(odds))
    f_hi = tracker.kelly_fraction_of_bankroll(imp + 0.01, str(odds))
    print(f"   {odds:>5}  implied={imp:.6f}  f*(imp)={f_at:.3e}  "
          f"f*(imp-1%)={f_lo:.6f}  f*(imp+1%)={f_hi:.6f}")

print()
print(SEP)
print(" 5. kelly_stake_units()  vs hand-computed, bank=100, NO daily cap")
print(SEP)
tracker.kelly_reset_daily_committed()
tracker._bankroll_cache = 100.0
maxd = 0.0
for p, odds in [(0.65, -140), (0.65, -110), (0.60, -110), (0.5238, -110),
                (0.52, -110), (0.50, -110), (0.45, -110), (0.75, -110),
                (0.90, -110), (0.40, 150), (0.62, 100), (0.5240, -110)]:
    got = tracker.kelly_stake_units(p, str(odds))
    want = hand_stake(p, odds)
    maxd = max(maxd, abs((got if got is not None else -999) - want))
    print(f"   p={p:<7} {odds:>5}  code={got}  hand={want}")
print(f"   MAX |diff| = {maxd:.3e}")

print()
print(SEP)
print(" 6. THE HEADLINE SANITY CHECK: p=0.65 @ -140 on a 100u bank")
print(SEP)
tracker.kelly_reset_daily_committed()
tracker._bankroll_cache = 100.0
f_exact, b = hand_full_kelly(0.65, -140)
print(f"   b = 100/140                 = {b:.10f}")
print(f"   f* = (.65*b - .35)/b        = {f_exact:.10f}   (= {f_exact*100:.4f}% of bank)")
print(f"   quarter Kelly               = {f_exact*0.25:.10f}   ({f_exact*25:.4f}%)")
print(f"   per-bet cap 10%             -> {min(f_exact*0.25, 0.10):.10f}")
print(f"   x 100u bank                 -> {100*min(f_exact*0.25,0.10):.4f}u")
print(f"   code says                   -> {tracker.kelly_stake_units(0.65, '-140')}u")

print()
print(SEP)
print(" 7. CAP ORDER: is the 10% cap on the FRACTIONAL or the FULL Kelly stake?")
print(SEP)
# p high enough that full Kelly > 40% but quarter Kelly < 10%
for p, odds in [(0.80, -110), (0.90, -110), (0.95, -110), (0.99, -110)]:
    tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
    full = tracker.kelly_fraction_of_bankroll(p, str(odds))
    got = tracker.kelly_stake_units(p, str(odds))
    if_cap_on_full = 100 * min(full, 0.10) * 0.25
    if_cap_on_frac = 100 * min(full * 0.25, 0.10)
    print(f"   p={p} {odds}: full f*={full:.4f}  code={got:.2f}u   "
          f"cap-then-fraction={if_cap_on_full:.2f}u   fraction-then-cap={if_cap_on_frac:.2f}u")

print()
print(SEP)
print(" 8. DAILY CAP (15% of bank), first-come-first-served")
print(SEP)
tracker.kelly_reset_daily_committed()
tracker._bankroll_cache = 100.0
tracker._daily_committed["2099-01-01"] = 0.0
running = 0.0
for i in range(6):
    s = tracker.kelly_stake_units(0.65, "-140", game_date="2099-01-01")
    running += s or 0
    print(f"   bet {i+1}: stake={s}   cumulative={running:.2f}u   "
          f"tally={tracker._daily_committed['2099-01-01']:.2f}")
print(f"   budget = 15% x 100u = 15.00u ; total staked = {running:.2f}u")

print()
print(SEP)
print(" 9. DEGENERATE INPUTS")
print(SEP)
tracker.kelly_reset_daily_committed(); tracker._bankroll_cache = 100.0
for p, odds, why in [(None, "-110", "p missing"),
                     (0.0, "-110", "p=0"), (1.0, "-110", "p=1"),
                     (1.5, "-110", "p>1"), (-0.2, "-110", "p<0"),
                     (0.65, "", "no price"), (0.65, "0", "odds=0"),
                     (0.65, "abc", "garbage price"),
                     (0.65, "  -140  ", "whitespace"),
                     (0.65, "+140", "plus sign"),
                     (0.65, "−140", "unicode minus")]:
    try:
        got = tracker.kelly_stake_units(p, odds)
    except Exception as e:  # noqa: BLE001
        got = f"RAISED {type(e).__name__}: {e}"
    print(f"   p={str(p):<6} odds={odds!r:<12} ({why:<15}) -> {got}")

print()
print(SEP)
print(" 10. american_to_prob branches (used for edge, feeds the Kelly decision)")
print(SEP)
for odds in (-110, 110, 100, -100, -140, 250):
    p = tracker.american_to_prob(str(odds))
    print(f"   {odds:>5} -> implied {p:.6f}")
n, y = tracker.american_to_prob("-110"), tracker.american_to_prob("-110")
print(f"   two-sided -110/-110 sums to {n+y:.6f}  (vig = {(n+y-1)*100:.2f}%)")
print("   -> NOTE: no de-vigging anywhere; edge is model_p - RAW implied.")

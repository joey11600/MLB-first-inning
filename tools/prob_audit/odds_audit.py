"""ANALYSIS ONLY -- odds mathematics audit.  Writes nothing."""
import csv, json, math, sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tracker
from db import variants as dbv
sys.path.insert(0, str(ROOT / "workers"))
import importlib.util
spec = importlib.util.spec_from_file_location("live_state_mod", ROOT / "workers" / "live_state.py")

# ---- 1. cross-implementation agreement on implied prob ---------------------
CASES = ["+100", "-100", "+150", "-150", "0", "", "  ", "abc", "110", "-110",
         "+110", "−130", "+2500", "-2500", "1", "-1", "100", "+0", "-0",
         "1.5", " -105 ", "+105.0", None]

def ts_implied(s):
    """faithful port of dashboard BoardRow.parseAmericanToImpliedProb"""
    if s is None or s == "": return None      # JS: !s is true for "" and null
    t = s.strip().replace("−", "-")
    try:
        n = float(t) if t != "" else float("nan")   # JS Number("") === 0 !
    except ValueError:
        return None
    if t.strip() == "":
        n = 0.0                                     # Number("   ") === 0
    if not math.isfinite(n) or n == 0: return None
    if n > 0: return 100/(n+100)
    return -n/(-n+100)

def ts_norm(raw):
    """port of BoardRow.normalizeAmericanOdds"""
    s = (raw or "").strip()
    if not s: return ""
    if s[0] in "+-": return s
    try: v = float(s)
    except ValueError: v = float("nan")
    return ("+" + s) if (v == v and v > 0) else s

print("=" * 78)
print("1. AMERICAN -> IMPLIED PROBABILITY: cross-implementation table")
print("=" * 78)
print(f"{'input':>10} | {'tracker':>10} | {'db.variants':>11} | {'TS BoardRow':>11} | agree")
for c in CASES:
    a = tracker.american_to_prob(c) if c is not None else tracker.american_to_prob("")
    b = dbv.american_to_prob(c)
    d = ts_implied(c)
    def f(x): return "None" if x is None else f"{x:.6f}"
    ok = (f(a) == f(b) == f(d))
    print(f"{str(c)!r:>10} | {f(a):>10} | {f(b):>11} | {f(d):>11} | {'OK' if ok else '**MISMATCH**'}")

# ---- 2. payout ------------------------------------------------------------
def ts_payout_kelly(american):  # kelly-sim.ts payoutPerUnit
    return american/100 if american > 0 else 100/abs(american)
def ts_payout_route(s):  # api/shadow-pnl/route.ts
    if not s: return None
    try: n = float(str(s).strip())
    except ValueError: return None
    if not math.isfinite(n) or n == 0: return None
    return n/100 if n > 0 else 100/abs(n)

print()
print("=" * 78)
print("2. PAYOUT PER UNIT")
print("=" * 78)
print(f"{'input':>10} | {'tracker':>10} | {'db.variants':>11} | {'TS route':>10} | agree")
for c in CASES:
    a = tracker.payout_per_unit(c) if c is not None else tracker.payout_per_unit("")
    b = dbv.american_to_payout(c)
    d = ts_payout_route(c)
    def f(x): return "None" if x is None else f"{x:.6f}"
    ok = (f(a) == f(b) == f(d))
    print(f"{str(c)!r:>10} | {f(a):>10} | {f(b):>11} | {f(d):>10} | {'OK' if ok else '**MISMATCH**'}")

# identity check: implied == 1/(1+b)
print()
maxid = 0.0
for o in range(-3000, 3001, 5):
    if o == 0 or -100 < o < 100: continue
    s = str(o)
    p = tracker.american_to_prob(s); b = tracker.payout_per_unit(s)
    maxid = max(maxid, abs(p - 1.0/(1.0+b)))
print(f"identity  implied == 1/(1+payout)  max abs err over odds -3000..+3000: {maxid:.3e}")

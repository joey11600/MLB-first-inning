"""Cause attribution: for each disagreeing row, is the flip driven by the
stale probability/floor CONSTANTS, or by feeding combined_lambda instead
of lambda_lr_total?  ANALYSIS ONLY."""
import csv, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import tracker as T
import mlb_first_inning_predictor as P


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "picks_2026.csv"),
                                encoding="utf-8")))
reach = [r for r in rows
         if (r.get("pick_strength") or "").strip().upper() in ("LINEUP PENDING", "STARTER PENDING")
         and (r.get("graded_result") or "").strip().upper() == "PASS"
         and (r.get("actual_result") or "").strip().upper() in ("NRFI", "YRFI")]

print(f"{'date':11s} {'match':12s} {'as-shipped':>18s} {'stale-gates+lrlam':>18s} "
      f"{'live':>18s}  cause")
for r in sorted(reach, key=lambda x: x["date"]):
    p, cl, lt = f(r["nrfi_prob"]), f(r["combined_lambda"]), f(r["lambda_lr_total"])
    dome = str(r.get("wx_is_dome", "")).strip().lower() in ("y", "yes", "true", "1")
    kw = dict(wx_temp_c=None if dome else f(r.get("wx_temp_c")),
              wx_wind_kmh=None if dome else f(r.get("wx_wind_kmh")),
              wx_is_dome=dome)
    a = T._classify_tentative_lean(p, cl)   # as shipped
    b = T._classify_tentative_lean(p, lt)   # stale gates, CORRECT lambda
    c = P.classify_pick_lr(p, 1, lt, **kw)  # live
    if a == c:
        continue
    if a != b and b == c:
        cause = "lambda SOURCE alone"
    elif a == b and b != c:
        cause = "stale CONSTANTS alone"
    else:
        cause = "both contribute"
    print(f"{r['date']:11s} {r['away_team']+'@'+r['home_team']:12s} "
          f"{a[1]+' '+a[0]:>18s} {b[1]+' '+b[0]:>18s} {c[1]+' '+c[0]:>18s}  {cause}")

# --- ledger-wide: does the lambda source alone ever flip the floor verdict
# under the LIVE constants, restricted to rows the notifier could see?
print("\n--- ledger-wide floor-straddle counts (context, not notifier rows) ---")
d = [(f(r["combined_lambda"]), f(r["lambda_lr_total"])) for r in rows
     if f(r.get("combined_lambda")) is not None and f(r.get("lambda_lr_total")) is not None]
print(f"n={len(d)}")
for fl in (0.78, 0.838):
    n = sum(1 for a, b in d if (a < fl) != (b < fl))
    hi = sum(1 for a, b in d if b < fl <= a)   # legacy lambda LOOKS ok, real one fails
    print(f"  floor {fl}: straddle={n}  of which combined_lambda>=floor but "
          f"lambda_lr_total<floor (false 'lambda ok') = {hi}")

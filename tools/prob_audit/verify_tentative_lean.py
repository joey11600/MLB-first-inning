"""ANALYSIS ONLY -- independent verification of the reported defect at
tracker.py:1962 (_classify_tentative_lean stale thresholds + combined_lambda).

Reads the real ledger, replays both classifiers, prints diffs.
"""
import csv, os, sys, math
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import tracker as T
import mlb_first_inning_predictor as P

print("LIVE predictor constants:")
for n in ("_LR_STRONG_NRFI_P", "_LR_LEAN_NRFI_P", "_LR_PASS_LO_P",
          "_LR_LEAN_YRFI_P", "_LR_STRONG_YRFI_P", "_LR_LAMBDA_YRFI_FLOOR",
          "_LR_LAMBDA_NRFI_CEILING"):
    print(f"   {n:26s} = {getattr(P, n, 'MISSING')}")
print()

rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "picks_2026.csv"),
                               encoding="utf-8")))
print(f"ledger rows: {len(rows)}")


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def truthy_dome(v):
    return str(v).strip().lower() in ("y", "yes", "true", "1")


# ---------------------------------------------------------------- gate 1
# Which rows actually reach the notifier?  Replicate its self-filters.
reach = []
for r in rows:
    st = (r.get("pick_strength") or "").strip().upper()
    if st not in ("LINEUP PENDING", "STARTER PENDING"):
        continue
    if (r.get("graded_result") or "").strip().upper() != "PASS":
        continue
    if (r.get("actual_result") or "").strip().upper() not in ("NRFI", "YRFI"):
        continue
    reach.append(r)
print(f"rows reaching _notify_lineup_pending_resolved_telegram: {len(reach)}\n")

# ---------------------------------------------------------------- gate 2
disagree = []
for r in reach:
    p = f(r.get("nrfi_prob"))
    # what the notifier feeds in (line 2001):
    lam_notif = f(r.get("combined_lambda")) if (r.get("combined_lambda") or "") else f(r.get("lambda_lr_total"))
    lam_true = f(r.get("lambda_lr_total"))
    got = T._classify_tentative_lean(r.get("nrfi_prob"),
                                     r.get("combined_lambda") or r.get("lambda_lr_total"))
    wx_dome = truthy_dome(r.get("wx_is_dome"))
    want = P.classify_pick_lr(
        p, 1 if p is not None else 0, lam_true,
        wx_temp_c=None if wx_dome else f(r.get("wx_temp_c")),
        wx_wind_kmh=None if wx_dome else f(r.get("wx_wind_kmh")),
        wx_is_dome=wx_dome,
    )
    same = (got[0] == want[0] and got[1] == want[1])
    if not same:
        disagree.append((r, p, lam_notif, lam_true, got, want))

print(f"DISAGREEMENTS: {len(disagree)} / {len(reach)} "
      f"({100.0*len(disagree)/max(1,len(reach)):.0f}%)\n")
print(f"{'date':11s} {'match':12s} {'p_nrfi':>7s} {'c_lam':>7s} {'lr_lam':>7s} "
      f"{'notifier says':>22s}   {'live model says':22s} {'actual':6s} fires?")
for r, p, ln, lt, got, want in disagree:
    fires = "YES-PING" if got[0] != "PASS" else "silent"
    print(f"{r['date']:11s} {r['away_team']+'@'+r['home_team']:12s} "
          f"{p:7.4f} {(ln if ln is not None else float('nan')):7.4f} "
          f"{(lt if lt is not None else float('nan')):7.4f} "
          f"{got[1]+' '+got[0]:>22s} | {want[1]+' '+want[0]:22s} "
          f"{r.get('actual_result',''):6s} {fires}")

# ---------------------------------------------------------------- gate 3
print("\n--- combined_lambda vs lambda_lr_total, whole ledger ---")
d = [(f(r.get("combined_lambda")), f(r.get("lambda_lr_total")), r)
     for r in rows]
d = [x for x in d if x[0] is not None and x[1] is not None]
diffs = [abs(a - b) for a, b, _ in d]
print(f"n={len(d)}  mean|diff|={sum(diffs)/len(diffs):.4f}  max={max(diffs):.4f}")
FLOOR_OLD, FLOOR_NEW = 0.78, P._LR_LAMBDA_YRFI_FLOOR
flip_old = sum(1 for a, b, _ in d if (a < FLOOR_OLD) != (b < FLOOR_OLD))
flip_new = sum(1 for a, b, _ in d if (a < FLOOR_NEW) != (b < FLOOR_NEW))
print(f"rows where the two lambdas straddle the 0.78 floor: {flip_old}")
print(f"rows where the two lambdas straddle the {FLOOR_NEW} floor: {flip_new}")

# ---------------------------------------------------------------- gate 4
# Isolate the two causes: stale thresholds alone vs the lambda-source alone.
only_thresh = only_lam = both = 0
for r in reach:
    p = f(r.get("nrfi_prob"))
    lt = f(r.get("lambda_lr_total"))
    a = T._classify_tentative_lean(p, r.get("combined_lambda") or lt)
    b = T._classify_tentative_lean(p, lt)          # correct lambda, stale gates
    wx_dome = truthy_dome(r.get("wx_is_dome"))
    c = P.classify_pick_lr(p, 1, lt,
                           wx_temp_c=None if wx_dome else f(r.get("wx_temp_c")),
                           wx_wind_kmh=None if wx_dome else f(r.get("wx_wind_kmh")),
                           wx_is_dome=wx_dome)
    if a != b and b != c:
        both += 1
    elif a != b:
        only_lam += 1
    elif b != c:
        only_thresh += 1
print(f"\ncause split over the {len(reach)} notifier rows: "
      f"lambda-source only={only_lam}  stale-thresholds only={only_thresh}  both={both}")

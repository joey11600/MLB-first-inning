"""Point-in-time replay: was the tentative-lean ping wrong ON THE DAY it
fired, or only wrong when replayed against today's constants?

Reconstructs the predictor gate values that were live on each row's game
date from git history, then re-runs classify_pick_lr's logic with those.
ANALYSIS ONLY.
"""
import csv, os, sys, bisect
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import tracker as T
import mlb_first_inning_predictor as P

# (effective_date, value) from `git log -G` on the constant lines.
HIST = {
    "STRONG_NRFI": [("2026-04-25", 0.60), ("2026-04-27", 0.62), ("2026-04-27", 0.58),
                    ("2026-04-29", 0.56), ("2026-06-03", 0.62), ("2026-06-15", 1.01)],
    "LEAN_NRFI":   [("2026-04-25", 0.53), ("2026-04-27", 0.58), ("2026-04-29", 0.56),
                    ("2026-05-12", 0.50)],
    "FLOOR":       [("2026-04-29", 0.78), ("2026-05-19", 0.838)],
    "STRONG_YRFI": [("2026-04-25", 0.44), ("2026-07-27", 0.36), ("2026-07-28", 0.40)],
}
PASS_LO = 0.44


def at(key, d):
    v = None
    for dt, val in HIST[key]:
        if dt <= d:
            v = val
    return v


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_at(p, lam, d, floor_eff):
    """classify_pick_lr's structure with the constants live on date d.
    LEAN tier only exists from 2026-05-12 (Phase 1.3)."""
    sn, ln, sy = at("STRONG_NRFI", d), at("LEAN_NRFI", d), at("STRONG_YRFI", d)
    lean_era = d >= "2026-05-12"
    if p >= sn:
        return ("NRFI", "STRONG")
    if lean_era and p >= ln:
        return ("NRFI", "LEAN")
    if p > PASS_LO:
        if lean_era and lam is not None and lam >= floor_eff:
            return ("YRFI", "LEAN")
        return ("PASS", "NO EDGE")
    if p >= PASS_LO:
        return ("PASS", "NO EDGE")
    if lam is not None and lam < floor_eff:
        return ("PASS", "LOW LAMBDA")
    if p >= sy:
        return ("YRFI", "LEAN")
    return ("YRFI", "STRONG")


rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "picks_2026.csv"),
                                encoding="utf-8")))
reach = [r for r in rows
         if (r.get("pick_strength") or "").strip().upper() in ("LINEUP PENDING", "STARTER PENDING")
         and (r.get("graded_result") or "").strip().upper() == "PASS"
         and (r.get("actual_result") or "").strip().upper() in ("NRFI", "YRFI")]

bad_then = bad_now = 0
print(f"{'date':11s} {'match':12s} {'p':>6s} {'clam':>6s} {'lrlam':>6s} "
      f"{'notifier':>18s} | {'live-THEN':18s} | {'live-NOW':18s}")
for r in sorted(reach, key=lambda x: x["date"]):
    d = r["date"]
    p, cl, lt = f(r["nrfi_prob"]), f(r["combined_lambda"]), f(r["lambda_lr_total"])
    dome = str(r.get("wx_is_dome", "")).strip().lower() in ("y", "yes", "true", "1")
    base_floor = at("FLOOR", d)
    if base_floor is None:      # no lambda floor existed before 2026-04-29
        base_floor = 0.0
    floor_eff = P._weather_adjusted_floor(
        base_floor,
        None if dome else f(r.get("wx_temp_c")),
        None if dome else f(r.get("wx_wind_kmh")), dome)
    got = T._classify_tentative_lean(p, r.get("combined_lambda") or lt)
    then = classify_at(p, lt, d, floor_eff)
    now = P.classify_pick_lr(p, 1, lt,
                             wx_temp_c=None if dome else f(r.get("wx_temp_c")),
                             wx_wind_kmh=None if dome else f(r.get("wx_wind_kmh")),
                             wx_is_dome=dome)
    if got != then:
        bad_then += 1
    if got != now:
        bad_now += 1
    flag = "  <-- WRONG WHEN SENT" if got != then else ("  (stale only)" if got != now else "")
    print(f"{d:11s} {r['away_team']+'@'+r['home_team']:12s} {p:6.4f} "
          f"{(cl if cl is not None else float('nan')):6.3f} "
          f"{(lt if lt is not None else float('nan')):6.3f} "
          f"{got[1]+' '+got[0]:>18s} | {then[1]+' '+then[0]:18s} | "
          f"{now[1]+' '+now[0]:18s}{flag}")

print(f"\nwrong AT THE TIME OF SENDING : {bad_then} / {len(reach)}")
print(f"wrong vs TODAY'S constants   : {bad_now} / {len(reach)}")

# how many ledger rows even have lambda_lr_total / combined_lambda
have_cl = sum(1 for r in rows if (r.get("combined_lambda") or "").strip())
have_lt = sum(1 for r in rows if (r.get("lambda_lr_total") or "").strip())
both = sum(1 for r in rows if (r.get("combined_lambda") or "").strip()
           and (r.get("lambda_lr_total") or "").strip())
only_lt = sum(1 for r in rows if not (r.get("combined_lambda") or "").strip()
              and (r.get("lambda_lr_total") or "").strip())
print(f"\nledger n={len(rows)}  combined_lambda present={have_cl}  "
      f"lambda_lr_total present={have_lt}  both={both}  "
      f"rows where the `or` fallback actually reaches lambda_lr_total={only_lt}")

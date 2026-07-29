"""Independent verification of the BoardRow.classifyTentative floor claim.

CLAIM: classifyTentative (dashboard/components/BoardRow.tsx:189,199) gates the
YRFI lambda branches on t.lambdaYrfiFloor (the BASE 0.838 from thresholds.json)
while the predictor's classify_pick_lr gates on _weather_adjusted_floor(0.838,
temp, wind, dome).  BoardRow already receives the correct per-game value as
row.yrfiFloorUsed and never passes it in.

This script re-derives BOTH floors from scratch (no import of the predictor's
helper for the floor itself -- an independent re-implementation from the
docstring rules) and replays the classifier both ways.

ANALYSIS ONLY -- reads data/picks_2026.csv, writes nothing.
"""
import csv, os, sys, math

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSV = os.path.join(ROOT, "data", "picks_2026.csv")

# --- thresholds, read from the same file the dashboard reads -----------------
import json
TH = json.load(open(os.path.join(ROOT, "data", "thresholds.json")))
STRONG_NRFI = TH["strongNrfiP"]; LEAN_NRFI = TH["leanNrfiP"]
PASS_LO     = TH["passLoP"];     STRONG_YRFI = TH["strongYrfiP"]
BASE_FLOOR  = TH["lambdaYrfiFloor"]; NRFI_CEIL = TH["lambdaNrfiCeiling"]


def fnum(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def weather_floor(base, temp, wind, dome):
    """Independent re-implementation of _weather_adjusted_floor."""
    if dome:
        return base
    d = 0.0
    if temp is not None and temp >= 28.0:
        d += 0.02
    elif temp is not None and temp <= 12.0:
        d -= 0.02
    if wind is not None and wind >= 24.0:
        d += 0.02
    return max(0.40, min(1.20, base + d))


def classify(p, lam, floor):
    """Straight port of classifyTentative / classify_pick_lr's zone logic,
    parameterised on the floor so we can run it both ways."""
    if p >= STRONG_NRFI:
        if lam is not None and lam > NRFI_CEIL:
            return ("PASS", "HIGH LAMBDA")
        return ("NRFI", "STRONG")
    if p >= LEAN_NRFI:
        return ("NRFI", "LEAN")
    if p > PASS_LO:
        if lam is not None and lam >= floor:
            return ("YRFI", "LEAN")
        return ("PASS", "NO EDGE")
    if p >= PASS_LO:
        return ("PASS", "NO EDGE")
    if lam is not None and lam < floor:
        return ("PASS", "LOW LAMBDA")
    if p >= STRONG_YRFI:
        return ("YRFI", "LEAN")
    return ("YRFI", "STRONG")


rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
print(f"rows in picks_2026.csv: {len(rows)}")

n_wx_moves = 0
flips = []
usable = 0
floor_hist = {}
pending_rows = 0
pending_flips = []

for r in rows:
    p_raw = fnum(r["nrfi_prob"])
    lam = fnum(r["lambda_lr_total"])
    if lam is None:
        lam = fnum(r["combined_lambda"])
    if p_raw is None:
        continue
    usable += 1
    temp = fnum(r["wx_temp_c"])
    wind = fnum(r["wx_wind_kmh"])
    dome_v = fnum(r["wx_is_dome"])
    dome = bool(dome_v and dome_v >= 0.5)
    wf = weather_floor(BASE_FLOOR, temp, wind, dome)
    floor_hist[round(wf, 3)] = floor_hist.get(round(wf, 3), 0) + 1
    if abs(wf - BASE_FLOOR) > 1e-12:
        n_wx_moves += 1

    # Dashboard rounds nrfi_prob to one decimal PERCENT before classifying:
    #   nrfiPct = Math.round(num * 1000) / 10 ; then / 100
    p_dash = round(p_raw * 1000) / 10 / 100

    dash = classify(p_dash, lam, BASE_FLOOR)   # what BoardRow renders today
    real = classify(p_dash, lam, wf)           # what it would render with yrfiFloorUsed
    if dash != real:
        flips.append((r, p_raw, p_dash, lam, wf, dash, real))
    if r["pick_strength"].strip().upper() == "LINEUP PENDING":
        pending_rows += 1
        if dash != real:
            pending_flips.append(r)

print(f"usable rows (nrfi_prob present): {usable}")
print(f"rows whose weather MOVES the floor off {BASE_FLOOR}: {n_wx_moves}"
      f"  ({100.0*n_wx_moves/usable:.1f}%)")
print("floor distribution:", dict(sorted(floor_hist.items())))
print(f"\nVERDICT FLIPS (base floor vs weather floor, same p): {len(flips)}")
for r, p_raw, p_dash, lam, wf, dash, real in flips:
    print(f"  {r['date']} {r['away_team']}@{r['home_team']:<4} "
          f"p_raw={p_raw:.4f} p_dash={p_dash:.3f} lam={lam:.4f} "
          f"floor={wf:.3f} (T={r['wx_temp_c']},W={r['wx_wind_kmh']},dome={r['wx_is_dome']})"
          f"  dashboard={dash[0]}/{dash[1]:<11} predictor={real[0]}/{real[1]}")

print(f"\nrows stored as LINEUP PENDING: {pending_rows}; of those flipped: {len(pending_flips)}")

# How near-miss is this?  Count rows whose lambda sits inside the +/-0.02 band
# around the base floor -- those are the ones a floor shift can move.
band = [r for r in rows
        if fnum(r["lambda_lr_total"]) is not None
        and abs(fnum(r["lambda_lr_total"]) - BASE_FLOOR) <= 0.02]
print(f"rows with |lambda_lr_total - {BASE_FLOOR}| <= 0.02 (the exposed band): {len(band)}")

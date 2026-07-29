#!/usr/bin/env python3
"""Recency + live-impact slice of the LambdaMeter zone mismatch."""
import csv, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import mlb_first_inning_predictor as P

def meter_zone(y):
    if y < 0.40: return "STRONG NRFI"
    if y < 0.47: return "LEAN NRFI"
    if y < 0.53: return "PASS"
    if y < 0.60: return "LEAN YRFI"
    return "STRONG YRFI"

def prob_band(p):
    if p >= P._LR_STRONG_NRFI_P: return "STRONG NRFI"
    if p >= P._LR_LEAN_NRFI_P:   return "LEAN NRFI"
    if p >  P._LR_PASS_LO_P:     return "LEAN YRFI"
    if p >= P._LR_PASS_LO_P:     return "PASS"
    if p >= P._LR_STRONG_YRFI_P: return "LEAN YRFI"
    return "STRONG YRFI"

def f(x):
    try: return float(x)
    except Exception: return None

rows = list(csv.DictReader(open(ROOT/"data"/"picks_2026.csv", encoding="utf-8")))

# since NRFI was disabled
CUT = "2026-06-07"
per = defaultdict(Counter)
sn_recent = []
for r in rows:
    p, y = f(r["nrfi_prob"]), f(r["yrfi_prob"])
    if p is None or y is None: continue
    yd = (round(y*1000)/10)/100.0
    mz, mb = meter_zone(yd), prob_band(p)
    era = "post-2026-06-07" if r["date"] >= CUT else "pre"
    per[era]["rows"] += 1
    if mz != mb: per[era]["mismatch"] += 1
    if mz == "STRONG NRFI":
        per[era]["meter paints STRONG NRFI"] += 1
        if era != "pre": sn_recent.append((r["date"], r["away_team"], r["home_team"], p, yd,
                                           r["pick_side"], r["pick_strength"], r["bet_placed"]))

for era in ("pre", "post-2026-06-07"):
    c = per[era]
    print(f"{era:16s} rows={c['rows']:5d} zone-mismatch={c['mismatch']:5d} "
          f"({100*c['mismatch']/max(c['rows'],1):.1f}%)  meter-paints-STRONG-NRFI={c['meter paints STRONG NRFI']}")

print(f"\nSTRONG-NRFI-painted rows since {CUT} (side is disabled): {len(sn_recent)}")
for d in sn_recent[-12:]:
    print(f"   {d[0]} {d[1]}@{d[2]} p_nrfi={d[3]:.4f} yrfi_disp={d[4]:.3f} "
          f"stored={d[5]}/{d[6]} bet={d[7]}")

# calibrator reachable range
print("\n== reachable calibrated p_nrfi range ==")
ps = [f(r["nrfi_prob"]) for r in rows if f(r["nrfi_prob"]) is not None]
print(f"  min={min(ps):.4f} max={max(ps):.4f}  -> P(YRFI) in [{1-max(ps):.4f}, {1-min(ps):.4f}]")

# latest slate
last = max(r["date"] for r in rows)
print(f"\n== latest slate {last} ==")
for r in rows:
    if r["date"] != last: continue
    p, y = f(r["nrfi_prob"]), f(r["yrfi_prob"])
    if p is None: continue
    yd = (round(y*1000)/10)/100.0
    mz, mb = meter_zone(yd), prob_band(p)
    flag = "  <-- MISMATCH" if mz != mb else ""
    print(f"  {r['away_team']:4s}@{r['home_team']:4s} yrfi={yd:.3f} meter={mz:12s} "
          f"band={mb:12s} stored={r['pick_side']}/{r['pick_strength']}{flag}")

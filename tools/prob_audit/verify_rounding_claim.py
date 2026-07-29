"""Verify the claim that classifyTentative is fed a 1-decimal-rounded
probability (row.nrfiPct / 100) and that this flips verdicts.

ANALYSIS ONLY -- reads data, writes nothing.
"""
import csv, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TH = {
    "strongNrfiP": 1.01,
    "leanNrfiP": 0.50,
    "passLoP": 0.44,
    "leanYrfiP": 0.50,
    "lambdaYrfiFloor": 0.838,
    "lambdaNrfiCeiling": 0.52,
    "strongYrfiP": 0.40,
}
# load live thresholds if present
tp = os.path.join(ROOT, "data", "thresholds.json")
if os.path.exists(tp):
    live = json.load(open(tp, encoding="utf-8"))
    print("live thresholds.json:", {k: live.get(k) for k in TH})
    for k in TH:
        if isinstance(live.get(k), (int, float)):
            TH[k] = live[k]


def classify_tentative(p, lam, t=TH):
    """Faithful port of dashboard/components/BoardRow.tsx classifyTentative."""
    if p >= t["strongNrfiP"]:
        if t.get("lambdaNrfiCeiling") is not None and lam is not None and lam > t["lambdaNrfiCeiling"]:
            return ("PASS", "HIGH LAMBDA")
        return ("NRFI", "STRONG")
    if p >= t["leanNrfiP"]:
        return ("NRFI", "LEAN")
    if p > t["passLoP"]:
        if lam is not None and lam >= t["lambdaYrfiFloor"]:
            return ("YRFI", "LEAN")
        return ("PASS", "NO EDGE")
    if p >= t["passLoP"]:
        return ("PASS", "NO EDGE")
    if lam is not None and lam < t["lambdaYrfiFloor"]:
        return ("PASS", "LOW LAMBDA")
    if t.get("strongYrfiP") is not None and p >= t["strongYrfiP"]:
        return ("YRFI", "LEAN")
    return ("YRFI", "STRONG")


def js_round_half_up(x):
    """JS Math.round: half rounds toward +Infinity."""
    import math
    return math.floor(x + 0.5)


def dash_p(p_full):
    """What the dashboard actually feeds classifyTentative.

    Supabase path : Math.round(p*1000)/10  -> percent w/ 1 decimal, /100
    CSV board path: predictor writes f"{p*100:.1f}" -> same granularity
    """
    pct = js_round_half_up(p_full * 1000) / 10.0
    return pct / 100.0


def csv_board_p(p_full):
    """Python f"{x:.1f}" uses round-half-even on the decimal repr."""
    return float(f"{p_full * 100:.1f}") / 100.0


rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "picks_2026.csv"), encoding="utf-8")))
print(f"\nrows in picks_2026.csv: {len(rows)}")


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


flips = []
flips_csvpath = []
maxdiff = 0.0
n_eval = 0
for r in rows:
    p = f(r.get("nrfi_prob"))
    lam = f(r.get("lambda_lr_total"))
    if p is None:
        continue
    n_eval += 1
    pr = dash_p(p)
    pc = csv_board_p(p)
    maxdiff = max(maxdiff, abs(pr - p))
    a = classify_tentative(p, lam)
    b = classify_tentative(pr, lam)
    c = classify_tentative(pc, lam)
    if a != b:
        flips.append((r, p, pr, a, b, lam))
    if a != c:
        flips_csvpath.append((r, p, pc, a, c, lam))

print(f"evaluated: {n_eval}   max |rounded - full| = {maxdiff:.6f}")
print(f"\nFLIPS (supabase JS-round path): {len(flips)}")
for r, p, pr, a, b, lam in flips:
    print(f"  {r['date']} {r['away_team']:>3}@{r['home_team']:<3} "
          f"p={p:.6f} -> {pr:.4f}  lam={lam}  full={a}  rounded={b}   "
          f"stored pick={r['pick_side']}/{r['pick_strength']}")

print(f"\nFLIPS (CSV board f'{{:.1f}}' path): {len(flips_csvpath)}")
for r, p, pc, a, c, lam in flips_csvpath:
    print(f"  {r['date']} {r['away_team']:>3}@{r['home_team']:<3} "
          f"p={p:.6f} -> {pc:.4f}  full={a}  rounded={c}   "
          f"stored pick={r['pick_side']}/{r['pick_strength']}")

# --- The gate that decides whether any of this reaches the screen -------
print("\n--- reachability: classifyTentative only runs when pickStrength == 'LINEUP PENDING' ---")
import collections
print(collections.Counter(r["pick_strength"] for r in rows).most_common())

pend = [r for r in rows if r["pick_strength"] == "LINEUP PENDING"]
print(f"LINEUP PENDING rows ever persisted in picks_2026.csv: {len(pend)}")

flip_dates = {r["date"] for r, *_ in flips}
print("flip rows that are LINEUP PENDING:",
      sum(1 for r, *_ in flips if r["pick_strength"] == "LINEUP PENDING"))

#!/usr/bin/env python3
"""
analyze_zone_asymmetry.py -- forensic on why STRONG NRFI underperforms STRONG YRFI.

Three reports on 2026 graded picks:

  1. Per-zone hit rate, sample size, Wilson 95% CI, P&L at -110.
  2. Feature distribution: WIN vs LOSS within each STRONG zone.  If a
     feature differs significantly between W and L, it might be a
     NRFI-specific or YRFI-specific signal.
  3. Threshold sensitivity: what if we tightened STRONG NRFI to 0.62 or
     0.65?  How many picks remain and at what hit rate?

The answers tell us:
  - Is the asymmetry real or sample noise?
  - Are there features that help NRFI bets but not YRFI (or vice versa)?
  - Should we tighten the NRFI threshold given its observed weakness?
"""

import csv
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")

ROOT = Path(__file__).parent
PICKS = ROOT / "data" / "picks_2026.csv"

# Feature set we'll inspect for WIN-vs-LOSS distribution differences
FEATURES = [
    "lambda_lr_total", "lambda_lr_t1", "lambda_lr_b1",
    "fi_park_nrfi_rate",
    "home_fip", "home_hr9", "home_bb9", "home_k9",
    "away_fip", "away_hr9", "away_bb9", "away_k9",
    "home_obp", "home_slg", "home_rpg",
    "away_obp", "away_slg", "away_rpg",
]

WIN_UNIT  = 100/110
LOSS_UNIT = -1.0


def wilson_95(wins, n):
    if n == 0: return (0.0, 0.0)
    z = 1.96
    p = wins / n
    denom = 1 + z*z/n
    centre = (p + z*z/(2*n)) / denom
    half = (z/denom) * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (centre - half, centre + half)


def coerce(s):
    if s in (None, ""): return None
    try: return float(s)
    except (TypeError, ValueError): return None


def fmt_floats(arr):
    arr = [a for a in arr if a is not None]
    if not arr: return "n/a"
    return f"mean={np.mean(arr):.3f}  median={np.median(arr):.3f}  std={np.std(arr):.3f}"


def t_test_indep(a, b):
    """Welch's t-test, returns (t, approx_p) for two-sided."""
    a = np.asarray([x for x in a if x is not None])
    b = np.asarray([x for x in b if x is not None])
    if len(a) < 2 or len(b) < 2: return 0.0, 1.0
    m_a, m_b = a.mean(), b.mean()
    v_a, v_b = a.var(ddof=1), b.var(ddof=1)
    n_a, n_b = len(a), len(b)
    se = math.sqrt(v_a/n_a + v_b/n_b)
    if se == 0: return 0.0, 1.0
    t = (m_a - m_b) / se
    p = math.erfc(abs(t)/math.sqrt(2))
    return t, p


def main():
    with open(PICKS, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Filter to graded bet zones (NRFI + YRFI, not PASS or POSTPONED)
    zones = {}
    for r in rows:
        side  = (r.get("pick_side") or "").upper()
        strg  = (r.get("pick_strength") or "").upper()
        graded = (r.get("graded_result") or "").upper()
        if graded not in ("WIN", "LOSS"): continue
        if side not in ("NRFI", "YRFI"): continue
        key = f"{strg} {side}"
        zones.setdefault(key, []).append((r, graded))

    print("=" * 78)
    print("  Zone-by-zone forensic on 2026 graded picks (LR-v3 retro)")
    print("=" * 78)

    # ============== Report 1: hit rate + Wilson CI ==============
    print("\n[1] Headline numbers per zone")
    print("    " + "-" * 70)
    print(f"    {'zone':<16} {'record':>10} {'hit':>7} {'95% CI':>16} "
          f"{'units':>8}")

    summary = {}
    for label, picks in sorted(zones.items()):
        wins = sum(1 for _, g in picks if g == "WIN")
        n    = len(picks)
        l    = n - wins
        rate = wins / n if n else 0
        lo, hi = wilson_95(wins, n)
        units = wins * WIN_UNIT + l * LOSS_UNIT
        summary[label] = (wins, l, n, rate, lo, hi, units)
        print(f"    {label:<16} {wins:>3}-{l:<3}    {rate*100:>5.1f}%  "
              f"[{lo*100:>4.1f},{hi*100:>5.1f}]  {units:>+7.2f}u")

    # Overlap check between STRONG NRFI and STRONG YRFI CIs
    if "STRONG NRFI" in summary and "STRONG YRFI" in summary:
        n_lo, n_hi = summary["STRONG NRFI"][4], summary["STRONG NRFI"][5]
        y_lo, y_hi = summary["STRONG YRFI"][4], summary["STRONG YRFI"][5]
        overlap = max(n_lo, y_lo) <= min(n_hi, y_hi)
        print(f"\n    STRONG NRFI vs STRONG YRFI 95% CIs "
              f"{'OVERLAP' if overlap else 'do NOT overlap'} "
              f"-- {'asymmetry within sample noise' if overlap else 'real asymmetry'}")

    # ============== Report 2: feature distribution WIN vs LOSS, per zone ==============
    print("\n\n[2] Feature distribution: WINS vs LOSSES within STRONG zones")
    print("    Looking for features where mean differs between W and L by >= 1 std")
    print("    -- those are candidate predictive signals SPECIFIC to that zone.")

    for zone in ["STRONG NRFI", "STRONG YRFI", "LEAN NRFI", "LEAN YRFI"]:
        if zone not in zones:
            continue
        picks = zones[zone]
        wins = [r for r, g in picks if g == "WIN"]
        losses = [r for r, g in picks if g == "LOSS"]
        if not wins or not losses:
            continue
        print(f"\n    --- {zone}  (W={len(wins)}, L={len(losses)}) ---")
        diffs = []
        for feat in FEATURES:
            w_vals = [coerce(r.get(feat)) for r in wins]
            l_vals = [coerce(r.get(feat)) for r in losses]
            w_clean = [x for x in w_vals if x is not None]
            l_clean = [x for x in l_vals if x is not None]
            if len(w_clean) < 3 or len(l_clean) < 3:
                continue
            w_m, l_m = np.mean(w_clean), np.mean(l_clean)
            pooled_std = np.sqrt((np.var(w_clean) + np.var(l_clean)) / 2)
            if pooled_std == 0:
                continue
            cohen_d = (w_m - l_m) / pooled_std
            t, p = t_test_indep(w_clean, l_clean)
            diffs.append((feat, w_m, l_m, cohen_d, p))
        # Sort by absolute Cohen's d
        diffs.sort(key=lambda x: -abs(x[3]))
        print(f"    {'feature':<22} {'W mean':>10} {'L mean':>10} "
              f"{'delta':>9} {'cohen d':>9} {'p~':>8}")
        for feat, w_m, l_m, d, p in diffs[:8]:
            sig = "***" if p < 0.05 else "**" if p < 0.10 else "" if abs(d) < 0.5 else "*"
            print(f"    {feat:<22} {w_m:>10.3f} {l_m:>10.3f} "
                  f"{w_m - l_m:>+9.3f} {d:>+9.2f} {p:>8.3f}  {sig}")

    # ============== Report 3: threshold sensitivity for STRONG NRFI ==============
    print("\n\n[3] Threshold sensitivity for STRONG NRFI")
    print("    Currently calibrated >= 0.60 = STRONG.  What if we tightened?")
    print("    Counts and hit rates among graded picks where the model classified NRFI:")

    # Pull all NRFI picks (any strength) with calibrated nrfi_prob
    nrfi_picks = []
    for r in rows:
        graded = (r.get("graded_result") or "").upper()
        side   = (r.get("pick_side") or "").upper()
        if graded not in ("WIN", "LOSS"): continue
        if side != "NRFI": continue
        p = coerce(r.get("nrfi_prob"))
        if p is None: continue
        nrfi_picks.append((p, graded))

    if nrfi_picks:
        print(f"    Total NRFI bet picks graded: {len(nrfi_picks)}")
        print(f"    {'threshold':>10} {'count':>6} {'wins':>5} {'hit':>7} {'95% CI':>16} {'units':>8}")
        for thresh in [0.53, 0.55, 0.58, 0.60, 0.62, 0.65, 0.68]:
            sub = [g for p, g in nrfi_picks if p >= thresh]
            n = len(sub)
            w = sub.count("WIN")
            l = n - w
            rate = w / n if n else 0
            lo, hi = wilson_95(w, n)
            units = w * WIN_UNIT + l * LOSS_UNIT
            print(f"    >= {thresh*100:>5.1f}%   {n:>6} {w:>5} {rate*100:>5.1f}%  "
                  f"[{lo*100:>4.1f},{hi*100:>5.1f}]  {units:>+7.2f}u")

    # Same for YRFI
    print("\n    Threshold sensitivity for STRONG YRFI (current: P(NRFI) <= 0.40)")
    yrfi_picks = []
    for r in rows:
        graded = (r.get("graded_result") or "").upper()
        side   = (r.get("pick_side") or "").upper()
        if graded not in ("WIN", "LOSS"): continue
        if side != "YRFI": continue
        p = coerce(r.get("nrfi_prob"))
        if p is None: continue
        yrfi_picks.append((p, graded))

    if yrfi_picks:
        print(f"    Total YRFI bet picks graded: {len(yrfi_picks)}")
        print(f"    {'P(NRFI)<=':>10} {'count':>6} {'wins':>5} {'hit':>7} {'95% CI':>16} {'units':>8}")
        for thresh in [0.47, 0.45, 0.42, 0.40, 0.38, 0.35]:
            sub = [g for p, g in yrfi_picks if p <= thresh]
            n = len(sub)
            w = sub.count("WIN")
            l = n - w
            rate = w / n if n else 0
            lo, hi = wilson_95(w, n)
            units = w * WIN_UNIT + l * LOSS_UNIT
            print(f"    <= {thresh*100:>5.1f}%   {n:>6} {w:>5} {rate*100:>5.1f}%  "
                  f"[{lo*100:>4.1f},{hi*100:>5.1f}]  {units:>+7.2f}u")
    print()


if __name__ == "__main__":
    main()

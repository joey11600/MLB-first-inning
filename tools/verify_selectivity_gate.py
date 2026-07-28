#!/usr/bin/env python3
"""
tools/verify_selectivity_gate.py -- prove the shipped T-SELECTIVITY gate
reproduces the backtest it was justified by.

A threshold change is only trustworthy if the CODE actually selects the
bets the analysis said it would.  This replays every graded 2026 pick
through the real, imported `classify_pick_lr` (not a reimplementation of
it) and settles the survivors at their real captured DK price.

IMPORTANT -- replay is not the same as history.  The live ledger's 349
placed bets were fired under the rules in force AT THE TIME, and several
of those rules have since moved (most notably _LR_LAMBDA_YRFI_FLOOR,
0.78 -> 0.838).  Replaying through today's classifier applies today's
floor retroactively, so the old gate yields ~300 bets here, not 349.
That is expected and is not a defect.

The meaningful comparison is therefore replay-vs-replay: hold every
other rule at its current value and change ONLY the STRONG-YRFI gate.

Expected (replay, today's ruleset):
    old gate (p_nrfi < 0.44) : ~300 bets, ~58% hit, ~ +9u,  ~+3% ROI
    new gate (p_nrfi < 0.36) :  ~86 bets, ~70% hit, ~+16u, ~+19% ROI

For reference, the raw historical ledger (mixed rulesets, the number
quoted to the operator) was 349 bets / 55.9% / -2.20u at the old gate.

Usage:
    python tools/verify_selectivity_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def settle(odds, hit):
    if not hit:
        return -1.0
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def breakeven(odds):
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def load_rows():
    from db.supabase_writer import _get_client
    client = _get_client()
    rows = []
    if client is not None:
        PAGE, off = 1000, 0
        while True:
            res = (client.table("picks_2026").select("*")
                   .order("date").range(off, off + PAGE - 1).execute())
            d = res.data or []
            rows += d
            if len(d) < PAGE:
                break
            off += PAGE
    if not rows:
        import csv
        with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    return rows


def replay(rows, strong_yrfi_p):
    """Re-classify every graded row at the given STRONG-YRFI gate using
    the production classifier, and settle the STRONG YRFI survivors."""
    orig = P._LR_STRONG_YRFI_P
    P._LR_STRONG_YRFI_P = strong_yrfi_p
    try:
        n = w = 0
        pl = 0.0
        need = []
        for r in rows:
            if (r.get("graded_result") or "") not in ("WIN", "LOSS"):
                continue
            p_nrfi = fnum(r.get("nrfi_prob"))
            lam = fnum(r.get("lambda_lr_total"))
            if p_nrfi is None:
                continue
            # NOTE the signature: classify_pick_lr(p_nrfi, data_pts,
            # lambda_total, ...).  The 2nd positional is data_pts, NOT
            # lambda -- passing lambda there silently leaves lambda_total
            # as None, which disables the whole lambda-floor guard and
            # inflates the STRONG count.  Mirror the production call at
            # mlb_first_inning_predictor.py:2486 exactly.
            dome = fnum(r.get("wx_is_dome"))
            side, strength = P.classify_pick_lr(
                p_nrfi,
                4,                       # data_pts: these rows all have real data
                lam,
                wx_temp_c=fnum(r.get("wx_temp_c")),
                wx_wind_kmh=fnum(r.get("wx_wind_kmh")),
                wx_is_dome=bool(dome) if dome is not None else False,
            )
            if (side, strength) != ("YRFI", "STRONG"):
                continue
            odds = fnum(r.get("market_yrfi_odds"))
            if odds is None:
                continue          # no real price -> excluded from P&L
            hit = (r.get("actual_result") or "").upper() == "YRFI"
            n += 1
            w += hit
            pl += settle(odds, hit)
            need.append(breakeven(odds))
        return n, w, pl, (sum(need) / len(need) if need else 0.0)
    finally:
        P._LR_STRONG_YRFI_P = orig


def main():
    rows = load_rows()
    print(f"Replaying {len(rows)} 2026 rows through the production classify_pick_lr()\n")
    print(f"  {'gate':<26}{'bets':>6}{'W':>5}{'L':>5}{'hit%':>8}{'need':>7}"
          f"{'P&L':>10}{'ROI%':>8}")

    results = {}
    for label, th in (("OLD  p_nrfi < 0.44", 0.44),
                      ("NEW  p_nrfi < 0.36", 0.36)):
        n, w, pl, nd = replay(rows, th)
        results[th] = (n, w, pl, nd)
        if n == 0:
            print(f"  {label:<26}{0:>6}   (no qualifying bets)")
            continue
        print(f"  {label:<26}{n:>6}{w:>5}{n-w:>5}{100*w/n:>7.1f}%{100*nd:>6.1f}%"
              f"{pl:>+9.2f}u{100*pl/n:>7.2f}%")

    print(f"\n  live constant is currently _LR_STRONG_YRFI_P = {P._LR_STRONG_YRFI_P}")

    # ---- replay-vs-replay assertions ---------------------------------
    # Both arms run through the SAME classifier with the SAME lambda
    # floor and weather adjustment; the only difference is the gate.
    n_old, w_old, pl_old, need_old = results[0.44]
    n_new, w_new, pl_new, need_new = results[0.36]
    roi_old = 100 * pl_old / max(n_old, 1)
    roi_new = 100 * pl_new / max(n_new, 1)
    hit_old = 100 * w_old / max(n_old, 1)
    hit_new = 100 * w_new / max(n_new, 1)

    print(f"\n  delta (new - old): {pl_new - pl_old:+.2f}u on "
          f"{n_old - n_new} fewer bets; ROI {roi_old:+.2f}% -> {roi_new:+.2f}%; "
          f"hit {hit_old:.1f}% -> {hit_new:.1f}%")

    print("\n  checks (replay vs replay -- only the gate differs):")
    ok = True
    for desc, cond in [
        (f"new gate is more selective (got {n_new} vs {n_old} bets)", n_new < n_old),
        (f"new gate cuts volume 60-80% (got {100*(1-n_new/max(n_old,1)):.0f}%)",
         0.60 <= 1 - n_new / max(n_old, 1) <= 0.80),
        (f"new gate hit rate is higher (got {hit_new:.1f}% vs {hit_old:.1f}%)",
         hit_new > hit_old),
        (f"new gate hit rate ~70% (got {hit_new:.1f}%)", 63.0 <= hit_new <= 76.0),
        (f"new gate earns MORE total profit on FEWER bets "
         f"(got {pl_new:+.2f}u vs {pl_old:+.2f}u)", pl_new > pl_old),
        (f"new gate ROI is at least 3x the old (got {roi_new:.1f}% vs {roi_old:.1f}%)",
         roi_new >= 3 * max(roi_old, 0.1)),
        (f"new gate clears its average break-even "
         f"(got {hit_new:.1f}% vs {100*need_new:.1f}% needed)",
         hit_new > 100 * need_new),
    ]:
        mark = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"    [{mark}] {desc}")

    print("\n  " + ("ALL CHECKS PASSED -- the shipped gate behaves as validated."
                    if ok else
                    "*** the shipped gate does NOT behave as validated. ***"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

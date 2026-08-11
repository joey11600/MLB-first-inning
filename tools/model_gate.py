#!/usr/bin/env python3
"""
tools/model_gate.py -- does this change move the model, and if so, which way?

WHY THIS EXISTS (T8.28).  From 2026-05-06 to 2026-08-10 this repo had NO
automatic check on model quality, while docs/PLAYBOOK.md section 4 told
every reader that merging a predictor PR "will run automatically" against
a shadow gate and fail under `delta_pl < -2.0u`.  That workflow had been
deleted three months earlier.  Anyone following the playbook merged a
predictor change believing a model check had passed.  Nothing ran.

`tests.yml` runs on every push, but it proves the money PLUMBING is
self-consistent -- Python, the parity fixtures and the dashboard's
TypeScript all agree on a number.  A change that quietly makes the
PREDICTIONS worse passes every bit of it green.  This closes that.

WHAT IT PROVES, AND WHAT IT DOES NOT
------------------------------------
It re-scores a fixed, committed holdout of real 2026 games with whatever
model code and artifacts are in the working tree, and emits a fingerprint
plus metrics.  Run it at two commits and compare:

  * FINGERPRINTS IDENTICAL -> the change provably did not move a single
    prediction.  This is the common and most valuable case: refactors,
    logging, ops and dashboard work should all land here, and any that
    does NOT is telling you something you did not expect.
  * FINGERPRINTS DIFFER    -> the change moves predictions.  The metric
    deltas say which way, on this holdout.

THE HOLDOUT IS ALL THREE SEASONS: 3,728 real games, 2024 / 2025 / 2026,
reported per season as well as in aggregate -- because the entire point
of three splits is that a change can help one year and hurt another, and
an aggregate hides exactly the cross-year failure the protocol exists to
catch.  (Shipped 2026-08-10 covering 2026 only, 524 games; the operator
added the repaired 2024/2025 files the same day.)

It still does NOT prove a change is safe to ship.  A metric win here is
weak evidence -- `2026-08-03_gate_sweep_artifact` records a finding that
passed a selection-aware permutation null at p=0.000 AND walk-forward
4-of-4 and was an artifact anyway.  This holdout is FIXED, so it is
reused on every push and will be implicitly overfitted by anyone who
iterates against it.  The three-split out-of-sample protocol CLAUDE.md
calls non-negotiable is still yours to run.  This is a tripwire, not a
verdict.

WHY IT ONLY WARNS.  Operator's call, 2026-08-10, and it is the right one:
this branch takes ~30 automated pushes a day and a red gate during a live
slate could block a real fix under time pressure.  The report is loud;
the exit code is always 0.  Turning it into a blocker is a one-line
change in the workflow, deliberately not taken.

RE-SCORES FROM FEATURE COLUMNS, NEVER FROM THE VERDICT COLUMNS.
`pick_side` / `pick_strength` / `nrfi_prob` / `lambda_total` in the
backtest files are retired-Poisson artifacts that were never rebuilt --
measured AUC ~0.50, a coin flip.  Reading them as "what the model would
have done" is the single most repeated mistake in this repo's history.
This script builds features and calls the live model.

USAGE
    python tools/model_gate.py --fingerprint out.json
    python tools/model_gate.py --compare base.json candidate.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The committed holdout, `(season, path)`. EVERY FILE HERE IS `_ptfix` AND
# THAT IS LOAD-BEARING.
#
# `_ptfix` is the point-in-time repair. `truepit` and `truepit_pit` carry
# SEASON-FINAL era/fip/obp -- on opening day the file already knows how a
# pitcher finishes the year. `_pit` reads like "point-in-time" and is NOT;
# it is a different backfill entirely and is still leaked. Verified on disk
# 2026-08-10, share of pitchers whose ERA varies within the season:
#
#     truepit            0.0% / 0.0%     <- leaked
#     truepit_pit        0.0% / 0.0%     <- leaked, despite the name
#     truepit_ptfix     62.6% / 64.8%    <- the repair, and what we use
#
# Scoring a gate on leaked data would make it confidently wrong: the leak is
# worth ~+0.011 AUC, about a THIRD of this model's entire edge over a coin
# flip. If you ever change these paths, re-run that test first.
HOLDOUTS = [
    ("2024", ROOT / "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv"),
    ("2025", ROOT / "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv"),
    ("2026", ROOT / "data/backtests/backtest_2026-04-01_to_2026-05-11_truepit_ptfix.csv"),
    ("2026", ROOT / "data/backtests/backtest_2026-05-12_to_2026-05-26_truepit_ptfix.csv"),
]

# The 2024/2025 files predate the umpire feature and have no
# `home_plate_ump_nrfi_rate` column at all. `two_stage_model._ump_rate_for`
# falls back to LEAGUE_NRFI_RATE = 0.50 in exactly this situation, so we do
# the same and stay consistent with how the model was trained. It is an
# imputed constant on those two seasons -- fine for a BEFORE/AFTER
# comparison, where both sides get the identical input, and not something to
# read as a real umpire signal.
LEAGUE_NRFI_RATE = 0.50

# Rounded before hashing so that pure floating-point noise -- a different
# BLAS, a reordered sum -- does not masquerade as a model change. 9 places
# is far finer than any real model movement and far coarser than FP jitter.
FP_PLACES = 9

# Coverage floor. 3,728 of 5,537 holdout rows scored on 2026-08-10
# (2024: 1689, 2025: 1515, 2026: 524). The skips are overwhelmingly rows
# missing a feature column the file never carried -- a data hole, not a model
# fault. The floor is set to catch a feature RENAME (which would silently
# shrink coverage and let a 6-game gate still report "unchanged") while
# tolerating ordinary data gaps. See the check in score_holdout().
EXPECTED_ROWS = 3728
MIN_ROWS = 3300


def _f(row: dict, key: str) -> float | None:
    """Read a float from a CSV cell, treating blanks as missing."""
    v = (row.get(key) or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _outcome(row: dict) -> int | None:
    """1 if the first inning was scoreless (NRFI), 0 if it scored, None if
    the row carries no usable outcome.

    Two file vintages, two spellings. The 2026 files carry `actual_result`
    ("NRFI"/"YRFI"); the 2024/2025 files carry the raw first-inning runs and
    no verdict string. Derive from the runs when the string is absent --
    never from `pick_side`/`nrfi_prob`, which in these files are
    retired-Poisson artifacts scoring AUC ~0.50.
    """
    res = (row.get("actual_result") or "").strip().upper()
    if res in ("NRFI", "YRFI"):
        return 1 if res == "NRFI" else 0
    a, h = _f(row, "fi_away_runs"), _f(row, "fi_home_runs")
    if a is None or h is None:
        return None
    return 1 if (a == 0 and h == 0) else 0


def _feats(row: dict, names: list[str], derived: dict) -> list[float] | None:
    """Build a feature vector in the model's declared order.

    Returns None if any feature is missing, and the caller SKIPS that row.
    Skipping rather than imputing is deliberate: a zero-filled feature is a
    silent lie that would move the prediction and be attributed to the
    change under test.
    """
    out = []
    for n in names:
        v = derived[n] if n in derived else _f(row, n)
        if v is None:
            return None
        out.append(v)
    return out


def score_holdout() -> dict:
    """Score every usable holdout row with the working tree's model."""
    import mlb_first_inning_predictor as mp

    t1_names = mp._T1_EXPECTED_FEATURES
    b1_names = mp._B1_EXPECTED_FEATURES
    cal = mp._load_lr_calibrator()
    cal_apply = None
    if cal is not None:
        for meth in ("apply", "transform", "predict", "calibrate", "__call__"):
            if hasattr(cal, meth):
                cal_apply = getattr(cal, meth)
                break

    preds: dict[str, float] = {}
    ys: dict[str, int] = {}
    seasons: dict[str, str] = {}
    skipped = 0

    for season, path in HOLDOUTS:
        if not path.exists():
            raise SystemExit(f"holdout missing: {path}")
        for row in csv.DictReader(path.open(encoding="utf-8")):
            y = _outcome(row)
            if y is None:
                skipped += 1
                continue

            h_era, a_era = _f(row, "home_era"), _f(row, "away_era")
            if h_era is None or a_era is None:
                skipped += 1
                continue
            derived = {"era_gap_t1": h_era - a_era, "era_gap_b1": a_era - h_era}
            # See LEAGUE_NRFI_RATE: the 2024/2025 files have no umpire column.
            if "home_plate_ump_nrfi_rate" not in row:
                derived["home_plate_ump_nrfi_rate"] = LEAGUE_NRFI_RATE

            t1 = _feats(row, t1_names, derived)
            b1 = _feats(row, b1_names, derived)
            if t1 is None or b1 is None:
                skipped += 1
                continue

            raw = mp.lr_predict_nrfi(t1, b1)
            if raw is None:
                skipped += 1
                continue
            p = raw
            if cal_apply is not None:
                try:
                    p = float(cal_apply(raw))
                except Exception:
                    p = raw

            # Stable key: date + teams. Sorted output makes the hash
            # independent of file or row ordering.
            # Team columns are spelled `away_team`/`home_team` in the 2026
            # files and `away`/`home` in the 2024/2025 ones. Fall through, and
            # prefer `game_pk` as the tiebreaker so a doubleheader gets two
            # distinct keys rather than a positional counter (which would
            # change if a row were ever inserted, silently breaking the
            # before/after pairing).
            away = (row.get("away_team") or row.get("away") or "").strip()
            home = (row.get("home_team") or row.get("home") or "").strip()
            pk = (row.get("game_pk") or "").strip()
            key = f"{season}|{(row.get('date') or '').strip()}|{away}@{home}"
            if pk:
                key += f"|{pk}"
            if key in preds:            # doubleheader: disambiguate
                key += f"|{len(preds)}"
            preds[key] = round(float(p), FP_PLACES)
            ys[key] = y
            seasons[key] = season

    if not preds:
        raise SystemExit(
            "model_gate scored 0 rows -- the holdout, the feature names or the "
            "model artifacts changed shape. Investigate; do not ignore."
        )

    # A GUARD THAT QUIETLY STOPS GUARDING IS THE T8.28 FAILURE AGAIN.
    # `_feats` skips any row with a missing feature, so renaming one feature
    # would not crash -- it would silently shrink the holdout, and a gate
    # scoring 6 games would still cheerfully report "predictions unchanged".
    # 524 of 735 rows scored on 2026-08-10 (the 211 skips are overwhelmingly
    # absent `wx_wind_kmh`, a coverage hole in the backtest file, not a model
    # fault). Warn hard below 450 rather than fail, so a genuine data gap
    # cannot block a push -- but nobody can miss it either.
    if len(preds) < MIN_ROWS:
        msg = (f"model_gate coverage collapsed to {len(preds)} rows "
               f"(expected ~{EXPECTED_ROWS}, floor {MIN_ROWS}). A feature was "
               f"probably renamed or the holdout changed. THE GATE IS NOT "
               f"MEASURING WHAT YOU THINK IT IS.")
        print(f"::error::{msg}", file=sys.stderr)
        print(f"!! {msg}", file=sys.stderr)

    keys = sorted(preds)
    blob = "\n".join(f"{k}={preds[k]:.{FP_PLACES}f}" for k in keys)
    return {
        "fingerprint": hashlib.sha256(blob.encode()).hexdigest(),
        "n": len(keys),
        "skipped": skipped,
        "metrics": _metrics([preds[k] for k in keys], [ys[k] for k in keys]),
        # Per season, because the whole point of three splits is that a
        # change can help one year and hurt another -- an aggregate hides
        # exactly the cross-year failure the protocol exists to catch.
        "by_season": {
            s_: _metrics([preds[k] for k in keys if seasons[k] == s_],
                         [ys[k] for k in keys if seasons[k] == s_])
            for s_ in sorted(set(seasons.values()))
        },
        "season_counts": {s_: sum(1 for k in keys if seasons[k] == s_)
                          for s_ in sorted(set(seasons.values()))},
        "preds": {k: preds[k] for k in keys},
    }


def _metrics(ps: list[float], ys: list[int]) -> dict:
    """Brier and log loss (both LOWER IS BETTER), plus distribution shape.

    Brier is the headline because it is proper -- it cannot be gamed by
    shifting confidence without improving discrimination -- and because the
    repo's own retrain comparisons are argued on Brier.
    """
    n = len(ps)
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    eps = 1e-12
    ll = -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
        for p, y in zip(ps, ys)
    ) / n
    return {
        "brier": round(brier, 6),
        "log_loss": round(ll, 6),
        "mean_pred": round(sum(ps) / n, 6),
        "base_rate": round(sum(ys) / n, 6),
        "min_pred": round(min(ps), 6),
        "max_pred": round(max(ps), 6),
    }


def compare(base: dict, cand: dict) -> None:
    """Print the report. Never raises, never exits non-zero -- warn-only."""
    bar = "=" * 68
    print(bar)
    print("MODEL GATE (T8.28/T8.29) -- 2024+2025+2026 holdout, warn-only")
    print(bar)

    if base.get("fingerprint") == cand.get("fingerprint"):
        print(f"\n  PREDICTIONS UNCHANGED across {cand['n']} games.")
        print("  This change provably did not move the model.\n")
        print(f"  fingerprint {cand['fingerprint'][:16]}  brier "
              f"{cand['metrics']['brier']}")
        print(bar)
        return

    print("\n  *** PREDICTIONS MOVED ***\n")
    if base.get("n") != cand.get("n"):
        print(f"  !! Scored row count changed: {base.get('n')} -> {cand.get('n')}.")
        print("     Metric deltas below are NOT comparable across different")
        print("     row sets. Find out why the coverage changed first.\n")

    bp, cp = base.get("preds", {}), cand.get("preds", {})
    shared = sorted(set(bp) & set(cp))
    moved = [(k, cp[k] - bp[k]) for k in shared if cp[k] != bp[k]]
    if shared:
        print(f"  games moved: {len(moved)} of {len(shared)} shared")
    if moved:
        diffs = sorted((abs(d) for _, d in moved), reverse=True)
        print(f"  largest move: {diffs[0]:.6f}   median move: "
              f"{diffs[len(diffs)//2]:.6f}")
        print("\n  most-moved games:")
        for k, d in sorted(moved, key=lambda kv: -abs(kv[1]))[:5]:
            print(f"    {k:34s} {bp[k]:.4f} -> {cp[k]:.4f}  ({d:+.4f})")

    # PER SEASON FIRST. Adding 2024/2025 was worth doing precisely because an
    # aggregate hides a change that helps one era and hurts another, so the
    # cross-year answer goes above the aggregate, not below it.
    bs, cs = base.get("by_season", {}), cand.get("by_season", {})
    if bs and cs:
        print("\n  PER SEASON (brier, lower is better):")
        print(f"    {'season':8s} {'n':>6s}  {'base':>10s} {'candidate':>10s}  {'delta':>11s}")
        for s_ in sorted(set(bs) | set(cs)):
            b_, c_ = bs.get(s_), cs.get(s_)
            if not b_ or not c_:
                continue
            d_ = c_["brier"] - b_["brier"]
            n_ = cand.get("season_counts", {}).get(s_, "?")
            verdict = "BETTER" if d_ < 0 else ("WORSE" if d_ > 0 else "same")
            print(f"    {s_:8s} {n_:>6}  {b_['brier']:>10.6f} {c_['brier']:>10.6f}  "
                  f"{d_:>+11.6f}  {verdict}")
        deltas = [cs[s_]["brier"] - bs[s_]["brier"] for s_ in bs if s_ in cs]
        if deltas and not (all(d <= 0 for d in deltas) or all(d >= 0 for d in deltas)):
            print("\n    !! MIXED ACROSS SEASONS -- helps some years, hurts others.")
            print("       That is the shape of a fit to one era rather than a real")
            print("       improvement. Do not ship on the aggregate alone.")

    print("\n  metric            base        candidate   delta")
    for m in ("brier", "log_loss", "mean_pred"):
        b, c = base["metrics"].get(m), cand["metrics"].get(m)
        if b is None or c is None:
            continue
        d = c - b
        note = ""
        if m in ("brier", "log_loss"):
            note = "  BETTER" if d < 0 else ("  WORSE" if d > 0 else "  same")
        print(f"  {m:16s}  {b:<11.6f} {c:<11.6f} {d:+.6f}{note}")

    print("\n  Brier and log loss are LOWER-IS-BETTER.")
    print("  A win here is NOT proof the change is good. This holdout is FIXED,")
    print("  so anyone iterating against it will overfit it; and a finding can")
    print("  pass a permutation null at p=0.000 and still be an artifact (see")
    print("  the 2026-08-03 gate sweep). Run the three-split out-of-sample")
    print("  protocol before shipping a model change, and get sign-off.")
    print(bar)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--fingerprint", metavar="OUT.json",
                    help="score the 3-season holdout with the current tree")
    ap.add_argument("--compare", nargs=2, metavar=("BASE.json", "CAND.json"),
                    help="report the difference between two fingerprints")
    a = ap.parse_args()

    if a.fingerprint:
        res = score_holdout()
        Path(a.fingerprint).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"scored {res['n']} games (skipped {res['skipped']})  "
              f"brier {res['metrics']['brier']}  "
              f"fingerprint {res['fingerprint'][:16]}")
        return 0

    if a.compare:
        b = json.loads(Path(a.compare[0]).read_text(encoding="utf-8"))
        c = json.loads(Path(a.compare[1]).read_text(encoding="utf-8"))
        compare(b, c)
        return 0            # warn-only, always

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

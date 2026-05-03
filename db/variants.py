"""
db/variants.py — T2.51 A/B model harness.

Computes shadow-pick verdicts for the 3 candidate model variants identified
in docs/MODEL_REVIEW_2026_05_09.md, alongside the production pick.  Each
variant runs against the SAME LR model + calibrator as production -- they
differ only in post-LR transformations (capped contributions for A,
stricter thresholds for C).  No retraining required.

Variants:
  A   — Cap each per-feature LR contribution at +/- 0.45 log-odds before
        the sigmoid.  Targets `quiet_inning` + `outside_top3_event`
        losses where a single dominant feature (xERA) drives the verdict.
        REJECTED in 2026-05-02 backfill (-43.58u vs production).
  C   — Raise YRFI STRONG threshold from p_yrfi >= 0.56 to p_yrfi >= 0.58
        (i.e. lower _LR_LEAN_YRFI_P from 0.44 to 0.42).  Picks in the
        0.56-0.58 band become YRFI LEAN (bet only if edge >= 2%) rather
        than STRONG.  Targets `quiet_inning` losses on borderline bets.
        REJECTED in 2026-05-02 backfill (-24.85u vs production).
  AC  — Both A and C combined (kitchen-sink test).
        REJECTED in 2026-05-02 backfill (-40.48u vs production).
  D   — Raise YRFI lambda floor from 0.78 to 1.00.  After A/C/AC failed,
        a fresh look at the historical W/L distribution by combined_lambda
        showed YRFI hit rate is 47% in the 0.90-1.00 lambda zone vs 76%
        in 1.10+.  The current floor (0.78) lets through marginal
        low-lambda YRFI bets that lose money on aggregate.  This variant
        skips them (PASS - LOW LAMBDA) and only fires YRFI when the
        model projects >= 1 expected run.  No prob/threshold change;
        only the floor.

Each variant returns a `VariantPick` with the same shape as the production
verdict so downstream P/L computation is identical.

This module is pure-Python + has no external imports beyond `math`; safe
to import from the live predictor, the backfill, or Phase 2 dashboard
analytics without dragging in numpy/sklearn.
"""

from __future__ import annotations

import math
from typing import NamedTuple


# Per-feature contribution cap for variant A and AC.  Chosen as +/- 0.45
# log-odds because:
#   - The largest single contribution observed in the 2026 audit was
#     home_xera at -0.7165 on TEX@DET (yesterday's STRONG NRFI loss).
#   - 0.45 trims that to roughly 65% of its raw magnitude -- still
#     allows xERA to drive the verdict, but requires multi-feature
#     agreement before the model commits STRONG.
#   - Symmetric (+/- 0.45) keeps NRFI vs YRFI cuts balanced.
VARIANT_A_CONTRIB_CAP = 0.45

# Variant C threshold.  Lower _LR_LEAN_YRFI_P from 0.44 to 0.42 so that
# YRFI STRONG requires p_nrfi < 0.42 (i.e. p_yrfi > 0.58, was 0.56).
# Picks in the 0.42-0.44 band classify as YRFI LEAN -- bet only if edge
# clears the 2% gate set by tracker._apply_odds_to_row.
VARIANT_C_LEAN_YRFI_P = 0.42

# Variant D lambda floor.  Raise from production's 0.78 to 1.00.
# Empirical basis (2026-04-01 to 2026-05-02 STRONG YRFI bets):
#   lambda 0.70-0.90 (n=25):  14W/11L = 56% hit, +1.51u
#   lambda 0.90-1.00 (n=36):  17W/19L = 47% hit, -3.31u   <-- LOSING
#   lambda 1.00-1.10 (n=39):  24W/15L = 62% hit, +6.56u
#   lambda 1.10+    (n=38):  29W/9L  = 76% hit, +16.95u
# Raising to 1.00 eliminates the LOSING zone but also cuts the slightly-
# profitable 0.70-0.90 zone.  Net: trades a small +1.51u for a clean
# avoidance of -3.31u, net +1.80u over 32 days, with 61 fewer bets
# (lower variance for similar return).
VARIANT_D_LAMBDA_FLOOR = 1.00


# Production thresholds (kept locally here so this module doesn't import
# the predictor and create a circular dependency; if the predictor's
# constants ever drift, update both).
_PROD_STRONG_NRFI_P = 0.56
_PROD_LEAN_NRFI_P   = 0.56
_PROD_PASS_LO_P     = 0.44
_PROD_LEAN_YRFI_P   = 0.44


class VariantPick(NamedTuple):
    """Output of one variant for one game."""
    pick_side:     str      # 'NRFI' | 'YRFI' | 'PASS'
    pick_strength: str      # 'STRONG' | 'LEAN' | 'NO EDGE' | 'NO DATA' | 'LOW LAMBDA' | etc.
    pick_label:    str
    nrfi_prob:     float
    yrfi_prob:     float


def _sigmoid(z: float) -> float:
    """Stable sigmoid -- avoids exp overflow on large |z|."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _lr_predict_with_cap(features: list[float], m: dict, cap: float | None) -> float:
    """LR forward pass with per-feature contribution optionally clipped to
    `+/- cap`.  Mirrors `mlb_first_inning_predictor._lr_predict_one` but
    accepts a contribution cap parameter.  Pass `cap=None` to disable
    (then this is a verbatim re-implementation of production)."""
    z = m["bias"]
    for x, mean, std, w in zip(features, m["mean"], m["std"], m["weights"]):
        if std <= 0:
            continue
        c = w * (x - mean) / std
        if cap is not None and cap > 0:
            if c >  cap:
                c =  cap
            elif c < -cap:
                c = -cap
        z += c
    return _sigmoid(z)


def _classify(p_nrfi: float, data_pts: int,
              lambda_total:    float | None,
              lambda_floor:    float | None,
              lean_yrfi_p:     float = _PROD_LEAN_YRFI_P) -> tuple[str, str]:
    """Replicates `mlb_first_inning_predictor.classify_pick_lr` but takes
    `lean_yrfi_p` and the already-weather-adjusted lambda floor as
    parameters so the variant can override them.

    Returns (pick_side, pick_strength).  pick_label is composed by the
    caller."""
    if data_pts == 0:
        return "PASS", "NO DATA"
    if p_nrfi >= _PROD_STRONG_NRFI_P:
        return "NRFI", "STRONG"
    if p_nrfi >= _PROD_LEAN_NRFI_P:
        return "NRFI", "LEAN"
    if p_nrfi >= _PROD_PASS_LO_P:
        return "PASS", "NO EDGE"
    # Below PASS_LO -- candidate YRFI side.  Apply lambda floor first.
    if (lambda_total is not None and lambda_floor is not None
            and lambda_total < lambda_floor):
        return "PASS", "LOW LAMBDA"
    if p_nrfi >= lean_yrfi_p:
        return "YRFI", "LEAN"
    return "YRFI", "STRONG"


def _label_for(side: str, strength: str) -> str:
    """Compose the human-readable pick_label string the same way
    tracker.log_picks does, so dashboards / Telegram are consistent."""
    if side == "PASS":
        if strength == "NO DATA":          return "PASS - No data"
        if strength == "STARTER PENDING":  return "PASS - Starter pending"
        if strength == "LINEUP PENDING":   return "PASS - Lineup pending"
        if strength == "LOW LAMBDA":       return "PASS - Low lambda"
        return "PASS - No edge"
    return f"{strength} {side}"


def compute_variants(
    *,
    t1_feats:         list[float],
    b1_feats:         list[float],
    m_t1:             dict,
    m_b1:             dict,
    calibrator:       object | None,    # has .predict(p) method, or None
    data_pts:         int,
    lambda_total:     float | None,
    lambda_floor:     float | None,     # caller passes weather-adjusted value
) -> dict[str, VariantPick]:
    """Compute pick verdicts for all three variants on one game.

    Returns {"A": VariantPick, "C": VariantPick, "AC": VariantPick}.
    The caller is responsible for storing these alongside the production
    verdict (which is computed via the existing predictor flow).

    Pure-Python; no IO; safe to call inside the predict loop.
    """
    def cal(p):
        return calibrator.predict(p) if calibrator is not None else p

    # Variant A -- capped contributions, production thresholds
    p_t1_a = _lr_predict_with_cap(t1_feats, m_t1, VARIANT_A_CONTRIB_CAP)
    p_b1_a = _lr_predict_with_cap(b1_feats, m_b1, VARIANT_A_CONTRIB_CAP)
    p_nrfi_a = cal((1.0 - p_t1_a) * (1.0 - p_b1_a))
    side_a, strength_a = _classify(p_nrfi_a, data_pts, lambda_total, lambda_floor,
                                   lean_yrfi_p=_PROD_LEAN_YRFI_P)
    pick_a = VariantPick(
        pick_side=side_a, pick_strength=strength_a,
        pick_label=_label_for(side_a, strength_a),
        nrfi_prob=p_nrfi_a, yrfi_prob=1.0 - p_nrfi_a,
    )

    # Variant C -- production probabilities (uncapped), stricter YRFI threshold
    p_t1_c = _lr_predict_with_cap(t1_feats, m_t1, cap=None)
    p_b1_c = _lr_predict_with_cap(b1_feats, m_b1, cap=None)
    p_nrfi_c = cal((1.0 - p_t1_c) * (1.0 - p_b1_c))
    side_c, strength_c = _classify(p_nrfi_c, data_pts, lambda_total, lambda_floor,
                                   lean_yrfi_p=VARIANT_C_LEAN_YRFI_P)
    pick_c = VariantPick(
        pick_side=side_c, pick_strength=strength_c,
        pick_label=_label_for(side_c, strength_c),
        nrfi_prob=p_nrfi_c, yrfi_prob=1.0 - p_nrfi_c,
    )

    # Variant AC -- both A's capped contributions AND C's threshold
    p_nrfi_ac = cal((1.0 - p_t1_a) * (1.0 - p_b1_a))
    side_ac, strength_ac = _classify(p_nrfi_ac, data_pts, lambda_total, lambda_floor,
                                     lean_yrfi_p=VARIANT_C_LEAN_YRFI_P)
    pick_ac = VariantPick(
        pick_side=side_ac, pick_strength=strength_ac,
        pick_label=_label_for(side_ac, strength_ac),
        nrfi_prob=p_nrfi_ac, yrfi_prob=1.0 - p_nrfi_ac,
    )

    # Variant D -- production probabilities + thresholds, but
    # lambda floor raised from 0.78 to 1.00 so weak-lambda YRFI
    # bets demote to PASS LOW LAMBDA.
    side_d, strength_d = _classify(
        p_nrfi_c, data_pts, lambda_total,
        lambda_floor=VARIANT_D_LAMBDA_FLOOR,
        lean_yrfi_p=_PROD_LEAN_YRFI_P,
    )
    pick_d = VariantPick(
        pick_side=side_d, pick_strength=strength_d,
        pick_label=_label_for(side_d, strength_d),
        nrfi_prob=p_nrfi_c, yrfi_prob=1.0 - p_nrfi_c,
    )

    return {"A": pick_a, "C": pick_c, "AC": pick_ac, "D": pick_d}


# ---------------------------------------------------------------------------
# Counterfactual P/L
# ---------------------------------------------------------------------------

def american_to_prob(odds: str | float | None) -> float | None:
    """Mirrors tracker.american_to_prob.  Kept local so this module is
    importable without pulling tracker."""
    if odds in (None, ""):
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    if o > 0:
        return 100.0 / (o + 100.0)
    return -o / (-o + 100.0)


def american_to_payout(odds: str | float | None, units: float = 1.0) -> float | None:
    """Profit (not stake-back) from a 1u win at the given American odds.
    Loss returns -units."""
    if odds in (None, ""):
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    if o > 0:
        return units * (o / 100.0)
    return units * (100.0 / -o)


def variant_would_bet(pick: VariantPick, *,
                      market_nrfi_odds: str | None,
                      market_yrfi_odds: str | None,
                      min_edge_lean: float = 0.02) -> tuple[bool, float]:
    """Decide whether the variant would actually place this bet, and at
    what stake.  Mirrors tracker._apply_odds_to_row's auto-bet rules:

      STRONG NRFI / YRFI  -> bet 1.0u regardless of edge
      LEAN   NRFI / YRFI  -> bet 0.5u IFF edge_on_pick >= min_edge_lean
      anything else        -> no bet, 0u

    Returns (would_bet: bool, would_be_units: float)."""
    if pick.pick_side not in ("NRFI", "YRFI"):
        return (False, 0.0)
    if pick.pick_strength == "STRONG":
        return (True, 1.0)
    if pick.pick_strength == "LEAN":
        # Compute edge on the pick side
        if pick.pick_side == "NRFI":
            implied = american_to_prob(market_nrfi_odds)
            model_p = pick.nrfi_prob
        else:
            implied = american_to_prob(market_yrfi_odds)
            model_p = pick.yrfi_prob
        if implied is None or model_p is None:
            return (False, 0.5)    # would-be stake recorded, but skip without edge
        edge = model_p - implied
        if edge >= min_edge_lean:
            return (True, 0.5)
        return (False, 0.5)
    return (False, 0.0)


def variant_pl(pick: VariantPick, *,
               actual_result:    str | None,    # 'NRFI' | 'YRFI' | 'POSTPONED' | etc.
               market_nrfi_odds: str | None,
               market_yrfi_odds: str | None,
               would_be_units:   float,
               would_bet:        bool) -> tuple[str, float]:
    """Compute the variant's counterfactual W/L grade and unit P/L.

    Returns (graded_result, profit_loss_units).
      - 'PASS'      if variant would_bet=False
      - 'POSTPONED' / 'SUSPENDED' / 'CANCELLED'  if game didn't play
      - 'WIN'       if pick matched actual; +payout in units
      - 'LOSS'      else; -would_be_units"""
    if not actual_result:
        return ("", 0.0)
    if actual_result.upper() in ("POSTPONED", "SUSPENDED", "CANCELLED"):
        return (actual_result.upper(), 0.0)
    if not would_bet or pick.pick_side not in ("NRFI", "YRFI"):
        return ("PASS", 0.0)
    won = (pick.pick_side.upper() == actual_result.upper())
    if won:
        odds = market_nrfi_odds if pick.pick_side == "NRFI" else market_yrfi_odds
        payout = american_to_payout(odds, units=would_be_units)
        if payout is None:
            # No odds recorded -- fall back to a -110 payout assumption
            payout = would_be_units * (100.0 / 110.0)
        return ("WIN", payout)
    return ("LOSS", -would_be_units)

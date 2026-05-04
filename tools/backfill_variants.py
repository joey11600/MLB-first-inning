#!/usr/bin/env python3
"""
tools/backfill_variants.py — T2.51 A/B harness backfill.

Walks every graded picks_<season> row and computes shadow-pick verdicts
for variants A / C / AC alongside the production verdict that's already
recorded.  Upserts to the `pick_variants` Supabase table.  Idempotent
(composite PK matches picks_2026, ON CONFLICT upsert).

Usage:
  python tools/backfill_variants.py                    # all 2026 graded picks
  python tools/backfill_variants.py --since 2026-04-01
  python tools/backfill_variants.py --season 2025
  python tools/backfill_variants.py --reclassify       # rebuild even already-done

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY  (loaded from .env if present).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from db.supabase_writer import _get_client
from db.variants import (
    compute_variants, variant_would_bet, variant_pl,
)

# Pull the LR models, calibrator, and feature lists from the live predictor
# so the backfill replays the EXACT pipeline production used.
import mlb_first_inning_predictor as P


# --- Feature reconstruction --------------------------------------------------

# Special features computed on the fly (not stored as a single column).
def _row_to_feature(row: dict, name: str) -> float:
    """Return the value of one feature (by name) for one picks row.
    Replicates the lookups two_stage_model.t1_features / b1_features
    perform during live predict, against the SAME row that was written
    to picks_<season> at predict time."""
    if name == "era_gap_t1":
        return _to_float(row.get("home_era"), P._LEAGUE_AVG_XERA) \
             - _to_float(row.get("away_era"), P._LEAGUE_AVG_XERA)
    if name == "era_gap_b1":
        return _to_float(row.get("away_era"), P._LEAGUE_AVG_XERA) \
             - _to_float(row.get("home_era"), P._LEAGUE_AVG_XERA)

    # Default per-feature placeholders mirror the production coerce()
    # calls in two_stage_model.t1_features / b1_features.
    defaults = {
        "fi_park_nrfi_rate":           P._LEAGUE_NRFI_RATE,
        "home_fip":                    P._LEAGUE_AVG_XERA,
        "away_fip":                    P._LEAGUE_AVG_XERA,
        "home_obp":                    0.320,    # league avg OBP
        "away_obp":                    0.320,
        "wx_temp_c":                   20.0,
        "wx_wind_kmh":                 10.0,
        "wx_humidity":                 60.0,
        "wx_is_dome":                  0.0,
        "home_p_last5_pitcher_nrfi":   P._LEAGUE_NRFI_RATE,
        "away_p_last5_pitcher_nrfi":   P._LEAGUE_NRFI_RATE,
        "home_p_last10_pitcher_nrfi":  P._LEAGUE_NRFI_RATE,
        "away_p_last10_pitcher_nrfi":  P._LEAGUE_NRFI_RATE,
        "home_top3c_obp":              0.320,
        "away_top3c_obp":              0.320,
        "home_top3c_slg":              0.400,
        "away_top3c_slg":              0.400,
        "home_top3c_iso":              0.150,
        "away_top3c_iso":              0.150,
        "home_plate_ump_nrfi_rate":    P._LEAGUE_NRFI_RATE,
        "home_xera":                   P._LEAGUE_AVG_XERA,
        "away_xera":                   P._LEAGUE_AVG_XERA,
        "home_whiff_pct_rank":         P._NEUTRAL_PCT_RANK,
        "away_whiff_pct_rank":         P._NEUTRAL_PCT_RANK,
        "home_pvt_nrfi_rate":          P._LEAGUE_NRFI_RATE,
        "away_pvt_nrfi_rate":          P._LEAGUE_NRFI_RATE,
        "home_avg_ip_per_start":       5.0,
        "away_avg_ip_per_start":       5.0,
    }
    return _to_float(row.get(name), defaults.get(name, 0.0))


def _to_float(v: Any, default: float) -> float:
    if v in (None, "", "null"):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# T3.13: lazy-load + cache the v3 calibrator (truepit-trained) for Variant K
_V3_CAL = None
_V3_CAL_LOADED = False
def _load_v3_calibrator():
    """Lazy-loaded ProbCalibrator for Variant K shadow.  Returns None if
    data/calibration_v3.json is missing (graceful degradation -- Variant K
    falls back to mirroring production)."""
    global _V3_CAL, _V3_CAL_LOADED
    if _V3_CAL_LOADED:
        return _V3_CAL
    _V3_CAL_LOADED = True
    import json
    cal_path = REPO_ROOT / "data" / "calibration_v3.json"
    if not cal_path.exists():
        print(f"  [warn] {cal_path} not found; Variant K will mirror production",
              file=sys.stderr)
        return None
    try:
        from calibration import ProbCalibrator
        with open(cal_path, encoding="utf-8") as f:
            d = json.load(f)
        _V3_CAL = ProbCalibrator(
            bin_centers   = d["centers"],
            bin_rates     = d["rates"],
            train_n       = d.get("train_n", 0),
            train_seasons = d.get("train_seasons", []),
        )
        return _V3_CAL
    except Exception as exc:    # noqa: BLE001
        print(f"  [warn] failed to load v3 calibrator: {exc!r}", file=sys.stderr)
        return None


def reconstruct_feats(row: dict) -> tuple[list[float], list[float]]:
    """Build (t1_feats, b1_feats) in the canonical order expected by
    the LR models, from a stored picks row."""
    t1 = [_row_to_feature(row, n) for n in P._T1_EXPECTED_FEATURES]
    b1 = [_row_to_feature(row, n) for n in P._B1_EXPECTED_FEATURES]
    return t1, b1


# --- Lambda floor (weather-aware, mirrors classify_pick_lr) ------------------

def _lambda_floor_for(row: dict) -> float:
    """Replicate _weather_adjusted_floor on a stored row.  Returns the
    floor value the variant classifier should compare lambda_total
    against.  Mirrors the live pipeline's logic."""
    wx_is_dome = bool(_to_float(row.get("wx_is_dome"), 0.0))
    if wx_is_dome:
        return P._LR_LAMBDA_YRFI_FLOOR
    wx_temp = _to_float(row.get("wx_temp_c"), 20.0)
    wx_wind = _to_float(row.get("wx_wind_kmh"), 10.0)
    return P._weather_adjusted_floor(
        P._LR_LAMBDA_YRFI_FLOOR, wx_temp, wx_wind, wx_is_dome=False,
    )


# --- Backfill main ----------------------------------------------------------

def fetch_rows_to_process(client: Any, season: int, since: str | None) -> list[dict]:
    """Pull picks_<season> rows.  We backfill ALL rows (not just losses)
    so the harness can compute hit rates, not just loss-prevention.
    Includes ungraded rows so we get tomorrow's slate as soon as
    today's predict cycle runs."""
    table = f"picks_{season}"
    cols = (
        "date,game_pk,away_team,home_team,game_time_et,"
        "pick_side,pick_strength,pick_label,bet_placed,units_risked,"
        "nrfi_prob,yrfi_prob,combined_lambda,lambda_lr_t1,lambda_lr_b1,lambda_lr_total,"
        # Feature columns -- required for variant A's recompute
        "home_era,away_era,"
        "home_fip,away_fip,home_obp,away_obp,"
        "home_top3c_obp,away_top3c_obp,home_top3c_slg,away_top3c_slg,"
        "home_top3c_iso,away_top3c_iso,"
        "home_p_last5_pitcher_nrfi,away_p_last5_pitcher_nrfi,"
        "home_p_last10_pitcher_nrfi,away_p_last10_pitcher_nrfi,"
        "home_plate_ump_nrfi_rate,"
        "home_xera,away_xera,home_whiff_pct_rank,away_whiff_pct_rank,"
        "home_pvt_nrfi_rate,away_pvt_nrfi_rate,"
        "home_avg_ip_per_start,away_avg_ip_per_start,"
        "park_factor,wx_temp_c,wx_wind_kmh,wx_humidity,wx_is_dome,"
        # Outcome / odds for counterfactual P/L
        "actual_result,graded_result,fi_total_runs,"
        "market_nrfi_odds,market_yrfi_odds,"
        "home_pitcher_q,away_pitcher_q,home_batting_q,away_batting_q"
    )
    q = client.table(table).select(cols)
    if since:
        q = q.gte("date", since)
    return q.execute().data or []


def already_done_keys(client: Any) -> set[tuple[str, str, str]]:
    """Return {(date, game_pk, variant_name)} so re-runs are no-ops."""
    res = client.table("pick_variants").select("date,game_pk,variant_name").execute()
    return {(r["date"], r["game_pk"], r["variant_name"]) for r in (res.data or [])}


def upsert_variant_row(client: Any, row: dict, variant_name: str,
                       pick, would_bet: bool, would_be_units: float,
                       graded_result: str, profit_loss_units: float) -> None:
    payload = {
        "date":              row["date"],
        "game_pk":           str(row["game_pk"]),
        "variant_name":      variant_name,
        "away_team":         row.get("away_team"),
        "home_team":         row.get("home_team"),
        "pick_side":         pick.pick_side,
        "pick_strength":     pick.pick_strength,
        "pick_label":        pick.pick_label,
        "nrfi_prob":         pick.nrfi_prob,
        "yrfi_prob":         pick.yrfi_prob,
        "would_be_units":    would_be_units,
        "would_bet":         would_bet,
        "graded_result":     graded_result or None,
        "fi_total_runs":     row.get("fi_total_runs"),
        "profit_loss_units": profit_loss_units if graded_result else None,
        "classified_at":     datetime.now(timezone.utc)
                                       .replace(tzinfo=None)
                                       .isoformat(timespec="seconds") + "Z",
        "graded_at":         (datetime.now(timezone.utc)
                                       .replace(tzinfo=None)
                                       .isoformat(timespec="seconds") + "Z"
                              if graded_result else None),
    }
    client.table("pick_variants").upsert(
        payload, on_conflict="date,game_pk,variant_name"
    ).execute()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--since",      metavar="YYYY-MM-DD")
    parser.add_argument("--season",     type=int, default=datetime.now().year)
    parser.add_argument("--reclassify", action="store_true")
    parser.add_argument("--dry-run",    action="store_true")
    args = parser.parse_args()

    client = _get_client()
    if client is None:
        sys.exit("Supabase not configured.")

    rows = fetch_rows_to_process(client, args.season, args.since)
    if not rows:
        print(f"No rows found in picks_{args.season} (since={args.since or 'any'}).")
        return

    skip_keys = set() if args.reclassify else already_done_keys(client)
    print(f"Processing {len(rows)} pick rows; "
          f"{'force-reclassifying all' if args.reclassify else f'{len(skip_keys)//3} games already done'}.\n")

    # Load production model + calibrator (cached after first call).
    m_t1, m_b1 = P._load_lr_models()
    cal = P._load_lr_calibrator()
    if m_t1 is None or m_b1 is None:
        sys.exit("LR models not available; cannot backfill.")

    n_done = n_skipped = n_err = 0
    for row in rows:
        date = row["date"]
        gp   = str(row["game_pk"])
        if not args.reclassify and (date, gp, "A") in skip_keys:
            n_skipped += 1
            continue

        # `data_pts` -- approximate from quality flags (production uses
        # an actual blended_inputs counter, but for backfill the only
        # signal we need is "is anything 'avg'?" since data_pts==0
        # short-circuits classify_pick_lr to PASS-NO-DATA).
        any_avg = any(
            (row.get(k) or "").lower() == "avg"
            for k in ("home_pitcher_q", "away_pitcher_q",
                       "home_batting_q", "away_batting_q")
        )
        data_pts = 0 if any_avg else 4

        try:
            t1_feats, b1_feats = reconstruct_feats(row)
            lambda_total = _to_float(row.get("lambda_lr_total"), 1.0)
            lambda_floor = _lambda_floor_for(row)
            variants = compute_variants(
                t1_feats=t1_feats, b1_feats=b1_feats,
                m_t1=m_t1, m_b1=m_b1, calibrator=cal,
                data_pts=data_pts,
                lambda_total=lambda_total,
                lambda_floor=lambda_floor,
            )
        except Exception as exc:    # noqa: BLE001
            print(f"  [err] {date}/{gp} feature reconstruction failed: {exc!r}",
                  file=sys.stderr)
            n_err += 1
            continue

        # Production applies data-readiness PASS guards (LINEUP PENDING,
        # STARTER PENDING, NO DATA) AFTER classify_pick_lr.  Those guards
        # depend on lineup_source / pitcher_q which the variant compute
        # doesn't see.  If production was forced to PASS for data reasons,
        # the variant must also PASS -- otherwise the A/B reads as
        # "variants find tons of profitable bets" when really they're
        # betting on lineup-pending / TBD-starter games that were never
        # eligible.  Mirror production's PASS reason verbatim.
        prod_strength = (row.get("pick_strength") or "").upper()
        DATA_PASS = {"LINEUP PENDING", "STARTER PENDING", "NO DATA"}
        from db.variants import (
            VariantPick, _label_for, VARIANT_D_LAMBDA_FLOOR,
            VARIANT_E_SKIP_P_LO, VARIANT_E_SKIP_P_HI,
            VARIANT_F_THIN_QS, VARIANT_F_BOTH_LTD_SKIP,
            VARIANT_G_YRFI_BAND_LO, VARIANT_G_YRFI_BAND_HI,
            VARIANT_H_NRFI_THR,
            VARIANT_J_YRFI_BAND_LO, VARIANT_J_YRFI_BAND_HI,
            VARIANT_K_USES_V3,
        )
        if prod_strength in DATA_PASS:
            forced = VariantPick(
                pick_side="PASS",
                pick_strength=prod_strength,
                pick_label=_label_for("PASS", prod_strength),
                nrfi_prob=variants["A"].nrfi_prob,
                yrfi_prob=variants["A"].yrfi_prob,
            )
            variants = {k: forced for k in variants}

        # T2.52 fix: Variant D as a CLEAN POST-FILTER on production's
        # recorded verdict.  Avoids feature-reconstruction noise that
        # causes border-line NRFI picks to flip due to small float
        # differences (verified empirically: D-via-recompute showed
        # disagreements on STRONG NRFI rows, which is impossible if
        # lambda floor only applies to YRFI side).
        #
        # The CORRECT definition of variant D: "production's recipe but
        # with lambda floor at 1.00".  Implementation: take production's
        # pick + verdict + probability AS RECORDED, then if production
        # would bet YRFI AND lambda_lr_total < 1.00, demote to
        # PASS LOW LAMBDA.  Otherwise variant D == production.
        prod_side  = (row.get("pick_side") or "").upper()
        prod_lambd = _to_float(row.get("lambda_lr_total"), 1.0)
        prod_nrfi  = _to_float(row.get("nrfi_prob"), 0.5)
        prod_yrfi  = _to_float(row.get("yrfi_prob"), 0.5)
        # Mirror-production helper for clean variant fallback.  When the
        # variant doesn't differ from production, we copy the recorded
        # row verbatim (avoids any feature-reconstruction noise).
        def _mirror_prod() -> VariantPick:
            return VariantPick(
                pick_side=prod_side or "PASS",
                pick_strength=prod_strength or "NO EDGE",
                pick_label=row.get("pick_label") or _label_for(
                    prod_side or "PASS", prod_strength or "NO EDGE",
                ),
                nrfi_prob=prod_nrfi,
                yrfi_prob=1.0 - prod_nrfi,
            )

        # Variant D: raise YRFI lambda floor 0.78 -> 1.00.
        if prod_side == "YRFI" and prod_lambd < VARIANT_D_LAMBDA_FLOOR:
            variants["D"] = VariantPick(
                pick_side="PASS", pick_strength="LOW LAMBDA",
                pick_label=_label_for("PASS", "LOW LAMBDA"),
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["D"] = _mirror_prod()

        # Variant E (T2.59): skip soft-edge STRONG (P=0.60-0.62).
        # Only fires when production is STRONG NRFI/YRFI.
        prod_p_pick = (
            prod_nrfi if prod_side == "NRFI"
            else prod_yrfi if prod_side == "YRFI"
            else 0.0
        )
        if (prod_strength == "STRONG"
                and prod_side in ("NRFI", "YRFI")
                and VARIANT_E_SKIP_P_LO <= prod_p_pick < VARIANT_E_SKIP_P_HI):
            variants["E"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label="PASS - Soft edge skip (T2.59 variant E)",
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["E"] = _mirror_prod()

        # Variant F (T2.59): skip thin-sample pitcher matchups.
        away_q = (row.get("away_pitcher_q") or "").lower()
        home_q = (row.get("home_pitcher_q") or "").lower()
        either_thin = (away_q in VARIANT_F_THIN_QS) or (home_q in VARIANT_F_THIN_QS)
        both_ltd    = (away_q == "ltd") and (home_q == "ltd") and VARIANT_F_BOTH_LTD_SKIP
        if (prod_strength == "STRONG"
                and prod_side in ("NRFI", "YRFI")
                and (either_thin or both_ltd)):
            variants["F"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label=("PASS - Thin sample skip "
                            f"(away_q={away_q}, home_q={home_q})"),
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["F"] = _mirror_prod()

        # Variant G (T3.12): skip STRONG YRFI in the calibrated 0.37-0.40
        # "losing valley".  The calibrator's range is [0.3623, 0.6620],
        # so STRONG YRFI bets cluster between 0.36 and 0.43.  Within that
        # window, the 0.37-0.40 band has 41% hit rate / -6.26u over 30d
        # while floor (0.36-0.37) and ceiling (0.40-0.42) bands win at
        # 65-68%.  Targets the "neither floor nor ceiling" cases where the
        # calibrator nudged upward (so it's not at the YRFI-most-confident
        # floor) but the raw model wasn't ceiling-bound either.
        if (prod_strength == "STRONG" and prod_side == "YRFI"
                and VARIANT_G_YRFI_BAND_LO <= prod_nrfi < VARIANT_G_YRFI_BAND_HI):
            variants["G"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label="PASS - YRFI losing valley (T3.12 variant G)",
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["G"] = _mirror_prod()

        # Variant H (T3.12): tighten STRONG NRFI threshold to P(NRFI)>=0.62
        # (was 0.58).  Demotes NRFI bets in the 0.58-0.62 band to PASS.
        # Pre-empts a worry that borderline NRFI bets near the production
        # threshold are weak; backfill says they aren't (NRFI bets profit
        # uniformly across the 0.58-0.66 range).  Variant runs as a
        # rejection signal so the dashboard can surface "we tested this,
        # it lost".
        if (prod_strength == "STRONG" and prod_side == "NRFI"
                and prod_nrfi < VARIANT_H_NRFI_THR):
            variants["H"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label="PASS - NRFI threshold tightened (T3.12 variant H)",
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["H"] = _mirror_prod()

        # Variant I (T3.12): G + H combined.
        if (prod_strength == "STRONG" and prod_side == "YRFI"
                and VARIANT_G_YRFI_BAND_LO <= prod_nrfi < VARIANT_G_YRFI_BAND_HI):
            variants["I"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label="PASS - YRFI losing valley (T3.12 variant I)",
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        elif (prod_strength == "STRONG" and prod_side == "NRFI"
                and prod_nrfi < VARIANT_H_NRFI_THR):
            variants["I"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label="PASS - NRFI threshold tightened (T3.12 variant I)",
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["I"] = _mirror_prod()

        # Variant J (T3.12 refinement): skip only the narrow 0.37-0.38
        # calibrated-P(NRFI) sub-band on STRONG YRFI bets.  Refined from
        # Variant G after the 2025 holdout test (tools/test_variant_g_2025.py)
        # showed Variant G's full 0.37-0.40 band is mostly noise out-of-sample,
        # but the 0.37-0.38 sub-band reproduces as a clear loser on both
        # samples (combined: 24 bets, 7-17, 29% hit, -11.01u).
        if (prod_strength == "STRONG" and prod_side == "YRFI"
                and VARIANT_J_YRFI_BAND_LO <= prod_nrfi < VARIANT_J_YRFI_BAND_HI):
            variants["J"] = VariantPick(
                pick_side="PASS", pick_strength="NO EDGE",
                pick_label="PASS - YRFI 0.37-0.38 sub-band (T3.12 variant J)",
                nrfi_prob=prod_nrfi, yrfi_prob=1.0 - prod_nrfi,
            )
        else:
            variants["J"] = _mirror_prod()

        # Variant K (T3.13): apply v3 calibrator (calibration_v3.json,
        # fit on 2024+2025 truepit corpus) to nrfi_prob_raw, then re-classify
        # using production thresholds.  Tests "what would the model do
        # under a leak-free calibrator?" Live shadow.  Requires raw probs
        # to be present on the row (added 2026-05-03 via T3.13 schema
        # migration).  Historical rows pre-T3.13 have raw=null and Variant
        # K mirrors production for those.
        nrfi_p_raw = _to_float(row.get("nrfi_prob_raw"), -1.0)
        if VARIANT_K_USES_V3 and nrfi_p_raw >= 0.0:
            # Lazy-load v3 calibrator (cached in module scope below)
            cal_v3 = _load_v3_calibrator()
            if cal_v3 is not None:
                p_v3 = cal_v3.predict(float(nrfi_p_raw))
                # Re-classify with production thresholds (0.58 / 0.42).
                # We replicate _classify here without the data_pts/lambda
                # gates because variant K is JUST about the calibrator;
                # if production passed for data reasons (LINEUP PENDING,
                # NO DATA, etc.) we already mirrored above.  At this point
                # we only re-classify when production made a real verdict.
                from db.variants import _PROD_STRONG_NRFI_P, _PROD_PASS_LO_P
                if p_v3 >= _PROD_STRONG_NRFI_P:
                    side_k, strength_k = "NRFI", "STRONG"
                elif p_v3 < _PROD_PASS_LO_P:
                    side_k, strength_k = "YRFI", "STRONG"
                else:
                    side_k, strength_k = "PASS", "NO EDGE"
                variants["K"] = VariantPick(
                    pick_side=side_k, pick_strength=strength_k,
                    pick_label=_label_for(side_k, strength_k) + " (v3 calibrator)",
                    nrfi_prob=p_v3, yrfi_prob=1.0 - p_v3,
                )
            else:
                variants["K"] = _mirror_prod()
        else:
            # Pre-T3.13 row (no raw stored): mirror production
            variants["K"] = _mirror_prod()

        for vname, pick in variants.items():
            would_bet, would_be_units = variant_would_bet(
                pick,
                market_nrfi_odds=row.get("market_nrfi_odds"),
                market_yrfi_odds=row.get("market_yrfi_odds"),
            )
            graded, pl = variant_pl(
                pick,
                actual_result=row.get("actual_result"),
                market_nrfi_odds=row.get("market_nrfi_odds"),
                market_yrfi_odds=row.get("market_yrfi_odds"),
                would_be_units=would_be_units,
                would_bet=would_bet,
            )
            if not args.dry_run:
                try:
                    upsert_variant_row(
                        client, row, vname, pick,
                        would_bet, would_be_units,
                        graded, pl,
                    )
                except Exception as exc:    # noqa: BLE001
                    print(f"  [err] upsert {date}/{gp}/{vname}: {exc!r}",
                          file=sys.stderr)
                    n_err += 1
                    continue

        n_done += 1

    print(f"\nProcessed {n_done} games ({n_done * 3} variant rows). "
          f"Skipped {n_skipped}. Errors {n_err}.")


if __name__ == "__main__":
    main()

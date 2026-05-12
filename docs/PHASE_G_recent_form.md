# Phase G — Top-3 batter recent-form features

Started 2026-05-12 after the regime-drift signal in early May.

## Why this exists

The current LR has season-to-date batter stats:
`top3c_obp`, `top3c_slg`, `top3c_iso` -- average of the top 3 batters'
season-long numbers.

These miss short-term hot/cold streaks.  Aaron Judge having a great
April but a brutal first week of May still reads "elite" to the model
because the season average is still high.  Conversely, a Coors batter
on a 2-week tear shows up as average until enough games accumulate.

The 5/08-5/11 losing streak smelled like this kind of drift -- "players
just sucking lately."  Refitting the LR on a smaller window
(2026-only V2.3 candidate) was the wrong fix: it adds variance to all
36 LR coefficients to chase a signal that's specifically in the
batter-form dimension.

Phase G adds the recent-form signal directly as features.

## What gets added

Six new features (3 per side):

| Feature | Type | Description |
|---|---|---|
| `away_top3c_last10_obp` | T1 input | Top-3 batters' average OBP over their last 10 games |
| `away_top3c_last10_slg` | T1 input | Same but SLG |
| `away_top3c_last10_iso` | T1 input | Same but ISO |
| `home_top3c_last10_obp` | B1 input | Home equivalent (B1 = away pitcher vs home offense) |
| `home_top3c_last10_slg` | B1 input | |
| `home_top3c_last10_iso` | B1 input | |

LR feature count: 18 → 21 per half.

## Computation

For each game on date D and each lineup batter:
1. Fetch the batter's MLB Stats API gameLog for the relevant season(s).
2. Filter to games where `date < D`.
3. Take the last 10 chronologically.
4. Sum AB / PA / H / BB / HBP / SF / TB across those 10 games.
5. Compute OBP / SLG / ISO from the sums (not averages-of-game-rates --
   smaller games would weight too heavily).
6. Take the team-level mean across the top 3 batters in the lineup.

If a batter has < 10 games in current season, backfill from prior
season (same logic as current `current_season_to_date_batter`).  If
neither season has any games, drop the batter from the average (don't
zero-fill -- that biases low).

## Files touched

| File | Change |
|---|---|
| `backtest.py` | New `recent_form_batter()` + `top3_last10_stats()` |
| `tools/backfill_top3_last10.py` (new) | Backfill historical CSVs |
| `tracker.py FIELDS` | Six new columns appended |
| `mlb_first_inning_predictor.py` | Compute at predict time; add to feature list |
| `two_stage_model.py` | Add to T1/B1 feature names |
| `recalibrate_v2.py` | Sync T1/B1 feature lists |
| `data/cache/batter_gamelog/` | Per-(player_id, season) JSON cache |

## Validation gate

Before deploying to production:
1. Refit LR on 2024+2025 with the new features.
2. Run 3-split OOS (2024→2025, 2025→2024, 2024+2025→2026).
3. Required: Brier improves on AT LEAST 2 of 3 splits, AND combined
   2026 Brier improves by ≥ 0.003 (same threshold as V2.2 deploy).
4. Run walk-forward backtest (tools/v23_walkforward_backtest.py
   adapted to include new features) -- must beat V2.2's +35.5u or
   at minimum stay within 5u.

If any of these gates fail, don't deploy.  Investigate why.

## Rollout plan

1. **Phase G.1 (tonight):** Build fetcher functions + backfill
   script + scaffolding.  No production changes yet.
2. **Phase G.2 (overnight):** Run backfill across 2024+2025+2026.
   ~2-3 hours of MLB Stats API calls (rate-limited).  Writes
   to `data/cache/batter_gamelog/`.
3. **Phase G.3 (next session):** Refit LR with new features.
   Run 3-split OOS.  Walk-forward backtest.  If gates pass,
   prepare V2.3 deployment.
4. **Phase G.4 (next session, after validation):** Update
   production weights + retrain calibrator + bump MODEL_VERSION
   to V2.3 in mlb_first_inning_predictor.py.  Keep V2.2
   archived for rollback.

## What's deliberately NOT in scope

- Per-batter recent-form features (would be 9 per side × 2 sides =
  18 features, too sparse).  Aggregate to top-3 mean instead.
- Last-30 or other window lengths.  Pick 10 to match the existing
  pitcher `last10_pitcher_nrfi` window.
- Recent-form for ALL lineup batters (not just top 3).  The model
  uses top 3 because they bat in T1/B1 of the 1st inning.
- Splits-aware recent form (vs L/R pitcher, home/away).  These
  add cells but require much larger samples per cell.  Maybe Phase H.

## Rollback path

If V2.3 (with Phase G features) underperforms in shadow tracking:
1. Revert MODEL_VERSION to V2.2.
2. Restore `data/lr_t1.json` / `data/lr_b1.json` from `data/archive/v2.2/`.
3. Restore `data/calibration_v2.json` from same.
4. Leave new CSV columns in place (they're harmless when LR doesn't
   reference them).
5. Document the reason in `docs/PHASE_G_recent_form.md`.

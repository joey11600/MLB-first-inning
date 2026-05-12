# 2026-05-12 — model consistency audit (negative results)

Investigated 5 hypotheses for why the May 8-11 losing streak happened
and whether any structural model change improves consistency.
**All five tested negative.**  V2.2 remains the best model on this data.

## Bottom line

The 4-day rough patch was variance, not a fixable model failure.  The
trailing-30-day record (60.6% hit, +21.8u) confirms the model is healthy;
the trailing-7-day -1.5u is within normal range for a 60% hit-rate model
running flat 1u.

Don't deploy any of the tested alternatives.  Keep V2.2 in production.
Reasonable Brier improvements (>= 0.003) appear to require **new data
sources**, not new model architectures over the existing 18 features.

## Tests run + results

### Test 1: Thin-pitcher demotion (turned OFF)

Built 2026-05-10 from a 5-day window (6W-10L on STRONG bets with at
least one sm/ltd pitcher).  Walk-forward backtest across the full
2026 season showed the demotion would have cost the season **-19u**
(it caught a 5-day fluke; over 6 weeks thin-pitcher games were
profitable).

**Action:** flipped `active: false` in `data/cluster_demotions.json`.
STRONG bets with thin pitchers will fire normally going forward.

### Test 2: 2026-only training (V2.3 candidate)

Refit LR on rolling 2026-only data via daily walk-forward.

| Window | Brier on 2026 | Δ vs V2.2 |
|---|---|---|
| 2024+2025 (V2.2) | 0.2443 | baseline |
| **2025 only** | 0.2496 | +0.0053 worse |
| 2024 only | 0.2466 | +0.0023 worse |
| 2026 only (chronological 80/20) | 0.2464 (recent slice) | -0.0077 better on **recent** slice |

Walk-forward V2.3 simulation across full season: **+24.1u vs V2.2's
+35.5u actual**.  V2.3 wins on recent data but loses over the full
season -- not a clean improvement.

**Action:** none.  Smaller training window has higher variance per
coefficient and underperforms over time.  Tested via
`tools/v23_walkforward_backtest.py`.

### Test 3: Phase G — top-3 batter last-10-games features

Six new features: `away/home_top3c_last10_obp/slg/iso`.  Captures
short-term batter form (e.g., Aaron Judge's last 10 games OBP=0.478
vs YTD=0.406).

3-split OOS Brier comparison after backfilling 2024+2025+2026
historical features:

| Split | V2.2 (18-feat) | Phase G (21-feat) | Δ |
|---|---|---|---|
| Train 2024, test 2025 | 0.2511 | 0.2518 | +0.0007 (worse) |
| Train 2025, test 2024 | 0.2595 | 0.2592 | -0.0003 |
| Train 24+25, test 2026 | 0.2443 | 0.2442 | -0.0001 |

Required for deploy: combined 2026 Brier delta ≤ -0.003.  Got -0.0001.
**Gate FAIL.**

**Action:** none.  Features built, backfilled, validation harness
shipped, but model unchanged.  Reusable infrastructure for future
feature experiments.  Files:
- `tools/backfill_top3_last10.py`
- `tools/phase_g_validation.py`
- `docs/PHASE_G_recent_form.md`
- new columns in `tracker.py FIELDS`

### Test 4: XGBoost architecture (V3 candidate)

Trained XGBoost two-stage models (T1 + B1 separately) on same 18
features as V2.2.  Hyperparameter sweep across 12 combinations.

| Variant | 2026 Brier | Δ vs V2.2 LR |
|---|---|---|
| **V2.2 LR raw** | **0.2443** | baseline |
| Best XGB two-stage (max_depth=4, lr=0.1) | 0.2466 | +0.0023 (worse) |
| XGB two-stage + isotonic calibrator | 0.2484 | +0.0041 (worse) |
| XGB single-stage (combined features) | 0.2458 | +0.0015 (worse) |

**All XGBoost variants lose to V2.2 LR.**  This tells us:
- Signal is mostly linear (LR captures it efficiently)
- Non-linear feature interactions are weak (trees can't find them)
- 4800 training samples isn't enough for tree models to beat LR

**Action:** none.  XGB infrastructure shipped
(`tools/two_stage_xgb.py`) for future tests.

### Test 5: Drop 2024, train on 2025 + 2026 only

Tested earlier as part of the V2.3 investigation.

| Slice | Train | Brier on recent 108 |
|---|---|---|
| 2024+2025 (current) | 4802 | 0.2541 |
| **2025+2026-train** | **2825** | **0.2519** |
| 2026 only | 432 | 0.2464 |

The "drop 2024" variant beats current by only 0.0022 -- within noise
on n=108.  Doesn't clear the 0.003 deploy threshold.

**Action:** none.

## What we learned

1. **V2.2 is genuinely near-optimal on this data.**  Three independent
   tests (Phase G features, smaller training window, different model
   architecture) all failed to clear a 0.003 Brier improvement.  The
   model is fitting the available signal efficiently.

2. **The losing streak was variance.**  Statistically uncomfortable
   (~1-in-70 event) but not fixable by model adjustments because
   there's no consistent model failure to fix.

3. **Real consistency improvements require NEW data, not new
   architecture.**  Candidates for future Phase H+ feature work:
   - Bullpen quality (pitcher pull risk)
   - Batter platoon splits (vs L/R pitcher)
   - Park-specific batter/pitcher matchup history
   - Real-time weather updates (windspeed change near first pitch)
   - Catcher framing rates (rejected in T4.1 but worth re-testing
     after a year of new data)

   None of these is a guaranteed Brier improvement.  Each is a
   multi-day project with expected ~0.001-0.005 improvement.

## Infrastructure shipped tonight (reusable)

- `tools/v23_walkforward_backtest.py` -- day-by-day simulation harness
- `tools/phase_g_validation.py` -- 3-split OOS comparison harness
- `tools/backfill_top3_last10.py` -- batter recent-form backfill
- `tools/two_stage_xgb.py` -- XGBoost training pipeline
- `docs/PHASE_G_recent_form.md` -- design doc + validation gates
- `docs/2026-05-12_model_consistency_audit.md` -- this doc

These tools work for any future feature experiment.  Standard flow:
1. Build feature in `backtest.py` + add to FIELDS.
2. Backfill historical data via `tools/backfill_top3_last10.py` pattern.
3. Add feature to `T1_FEATURES`/`B1_FEATURES` constants.
4. Run `tools/phase_g_validation.py` for 3-split OOS.
5. If gates pass, walk-forward backtest.
6. If gates STILL pass, deploy.

## Production state at end of session

- `V2.2` LR weights still in `data/lr_t1.json` / `data/lr_b1.json`
- Calibrator `data/calibration_v2.json` unchanged
- Thin-pitcher demotion **INACTIVE** (`data/cluster_demotions.json`)
- V2.1 shadow tracker running (`tools/v21_shadow_predict.py`
  via daily cron)
- Calibration drift monitor running
- Tomorrow morning's predict cron generates fresh V2.2 picks with
  no demotions, no Phase G, no XGB

## Operator decision tree going forward

If V2.2 keeps producing 60% hit rate over the next 30 graded bets:
  → Trust the model.  Don't deploy any of the tested alternatives.

If V2.2 underperforms V2.1 shadow by 3+ units over 30 graded bets:
  → Telegram alert fires.  Roll back to V2.1 per the procedure in
    CHANGELOG entry "2026-05-11 V2.2 deployed".

If V2.2 calibration-drift monitor fires (Brier degrades 0.01+):
  → Investigate the specific bucket that drifted.  Manual recalibrate
    via `python recalibrate_v2.py` (no auto-recal).

If the operator wants to keep testing despite tonight's negatives:
  → New data sources are the right path.  Phase H proposals welcome.

# 2026-05-11 system audit -- findings + improvement plan

Generated 2026-05-11 ET after a full review of the predictor + tracker
pipeline, prompted by a losing streak 5/08-5/10 and a TG `feature_drift`
alert.  Snapshot of what we know now; revisit on each Friday eval.

---

## Bottom line

- **30-day STRONG record:** 34W-30L (53%), **-1.73u**.  Break-even.
- **The losing cluster I originally flagged (`yrfi_040_band`, 0.37-0.42)
  was misdefined.**  The actual loss zone is `nrfi_p < 0.40` (deep YRFI):
  6W-12L, -7.00u.  The 0.40-0.44 band is *profitable*: 14W-6L, +6.57u.
- **The thin-pitcher demotion deployed 5/10 will over-correct** because
  it skips profitable marg_yrfi-thin bets along with the actually-losing
  deep_yrfi-thin bets.  Friday's shadow P&L decides the fix.
- **The 5/08-5/10 losing streak was small-sample variance** -- combined
  6-day window has near-zero bias (actual NRFI 50.0% vs predicted 49.0%).
- **The weekly auto-recalibrator was disabled tonight** (commit `d10dd75`)
  because it was violating CLAUDE.md's OOS-validation-is-non-negotiable
  rule.

---

## Diagnostic data (30-day STRONG bets, n=64)

### nrfi_prob bucket performance

| Bucket | nrfi_p | n | Record | Hit | P&L |
|---|---|---|---|---|---|
| Deep YRFI | <0.40 | 18 | 6W-12L | 33% | -7.00u |
| Marg YRFI | 0.40-0.44 | 20 | 14W-6L | 70% | +6.57u |
| Marg NRFI | 0.56-0.60 | 5 | 2W-3L | 40% | -1.32u |
| Deep NRFI | >=0.60 | 21 | 12W-9L | 57% | +0.03u |

### bucket x pitcher quality cross-tab

```
                       live           ltd            sm
deep_yrfi  (<0.40)   0W-3L -3.00u   4W-4L -0.87u   2W-5L -3.14u
marg_yrfi  (0.40-44) 4W-0L +3.97u   6W-5L +0.22u   4W-1L +2.37u
marg_nrfi  (0.56-60) 1W-0L +0.77u   1W-2L -1.09u   0W-1L -1.00u
deep_nrfi  (>=0.60)  4W-2L +1.00u   5W-4L -0.48u   3W-3L -0.49u
```

- Deep YRFI with LIVE pitchers is 0W-3L.  Thin-pitcher demotion misses it.
- Marg YRFI is profitable across ALL pitcher quality combos.
- Pitcher quality is a *weaker* signal than nrfi_p bucket over 30 days
  (the 5-day live=7-1 vs thin=6-10 finding was small-sample variance).

### Calibration audit (5/04 vs 5/11)

| Slice | N | 5/04 Brier | 5/11 Brier | Delta |
|---|---|---|---|---|
| 5/05-5/07 (winning OOS) | 38 | 0.2272 | 0.2257 | -0.0015 (better) |
| 5/08-5/10 (losing OOS) | 44 | 0.2564 | 0.2556 | -0.0008 (~tie) |
| 5/05-5/10 combined OOS | 82 | 0.2429 | 0.2418 | -0.0011 (better) |

5/11 calibrator passes audit -- keep in production.

---

## Active issues

| ID | Issue | Severity |
|---|---|---|
| I1 | Deep YRFI cluster (<0.40 nrfi_p) is real, currently un-targeted, -7u/30d | HIGH |
| I2 | Isotonic calibrator has structural flat zones (pick_cluster HIGH alerts) | MEDIUM |
| I3 | Thin-pitcher demotion is likely too broad (cuts 78% of STRONG bets) | MEDIUM |
| I4 | Loss-cluster monitor predicate straddles a profit boundary | LOW |

## Latent risks

| ID | Risk | Mitigation |
|---|---|---|
| L1 | No auto-adapt to a real regime shift now that recalibrator is off | Brier monitor (R3) |
| L2 | No automated rollback if a demotion is wrong | Re-eval reminders (R4) |
| L3 | LR coefficients haven't been refit since 4/29 -- could be drifting | OOS LR refit (R7) |
| L4 | Park-factor file is now frozen at 5/11 snapshot | Probably fine -- monitor |
| L5 | Bet volume could drop too low with multiple demotions | Cluster-aware sizing or surgical demotions |

---

## Improvement plan (ranked by confidence / value)

### High-confidence (do these)

- **R1**: Replace thin-pitcher demotion with deep_yrfi demotion, **after
  Friday's shadow PnL check**.  Evidence: deep_yrfi 6W-12L (-7u) regardless
  of pitcher quality; thin-pitcher 25W-25L (-4.5u) includes profitable
  marg_yrfi bets.  Pending operator decision Friday 2026-05-14.
- **R2**: Fix loss_cluster_monitor predicate: `0.370 <= p <= 0.420` →
  `p < 0.400`.  No betting impact, just observability.  Ship tonight.
- **R3**: Build `tools/calibration_drift_monitor.py` that runs on grade
  cron, computes trailing-30d Brier per nrfi_p bucket, fires Telegram
  if any bucket degrades by 0.01+.  Replaces the auto-recalibrator's
  drift-detection role without auto-deploying anything.
- **R4**: Add `reevaluate_after` ISO-date field to each entry in
  `data/cluster_demotions.json`.  Cron step checks daily and fires
  Telegram on/after that date with a `python tools/cluster_shadow_pnl.py`
  snapshot.

### Medium-confidence (worth investigating)

- **R5**: Implement Platt-scaling calibrator (logistic over the raw
  prob) as a candidate alongside the existing isotonic.  Compare on
  OOS slices; deploy ONLY if it strictly improves Brier AND eliminates
  the flat zones the pick_cluster HIGH alerts keep flagging.
- **R6 (SKIPPED per operator)**: per-bucket bet-sizing override.
  Operator confirmed flat-1u policy stands.

### Speculative (need OOS testing first)

- **R7**: Refit LR coefficients (`lr_t1.json` / `lr_b1.json`) on
  trailing 2026 + 2025 with the test_*.py 3-split protocol.  Reject
  if 2024→2025 or 2025→2024 Brier degrades.  Last refit was Phase F
  on 4/29 -- could be drifting on mid-season pitcher distribution.
- **R8**: Audit the 18 deep_yrfi games for a missing feature.
  Operator's working hypothesis: elite top-3 offense (Yankees w/
  Aaron Judge solo HR in 1st on 5/10) isn't weighted strongly enough
  in the LR.  Look for common structural factor in the 12 losses.

---

## What NOT to do

- Daily recalibration -- chases noise, multiplies un-validated refit risk.
- Edge gate on STRONG -- operator policy is auto-Y at any odds.
- Kelly / fractional sizing -- operator policy is flat 1u.
- Manual `pick_strength` edits -- always go through cluster_demotions.json
  so the change is journaled and reversible.
- Touching the locked predict cadence (30-min from 5pm-10pm ET).

---

## Order of operations

1. **Done tonight:**
   - Disabled weekly auto-recalibrator (`d10dd75`).
   - This audit document.
   - R2: Loss-cluster monitor predicate fix.
   - R3: Brier drift monitor.
   - R4: Re-eval reminders for active demotions.
   - R5: Platt calibrator candidate (built + compared; NOT deployed).
   - R7: LR refit candidate (built + validated; NOT deployed).
   - R8: Deep_yrfi feature audit (analytical report).
2. **Friday 2026-05-14:**
   - Run shadow P&L on thin_pitcher_strong_v1.
   - Decide R1 (swap to deep_yrfi demotion) based on results.
3. **Next week:**
   - Evaluate R5 (Platt) and R7 (LR refit) candidates against trailing data.
   - Decide whether to deploy either.
   - Act on R8 if a missing-feature pattern is identified.

---

## Open questions for operator

- **R1 timing.**  Friday eval is the natural decision point.  Confirm.
- **R7 deployment threshold.**  If the refit improves Brier on the
  trailing-OOS slice by N units, you'd want to deploy.  What's N?
  Default: only deploy if all three OOS splits show non-negative
  delta AND combined Brier improves by 0.003+.
- **R8 feature candidate.**  If the audit finds a clear missing
  signal (e.g. elite-hitter detection), do we add it to the model
  immediately or stack it behind R7's refit?  Default: stack
  behind R7 so we don't double-change.

---

## R-task results (executed 2026-05-11)

### R2 result -- ✅ SHIPPED (this commit)

- `tools/loss_cluster_monitor.py` cluster `yrfi_040_band` renamed to
  `yrfi_deep`; predicate simplified to `p < 0.40`.  Lambda + park gates
  dropped (no longer subset the cluster usefully on the corrected
  predicate).
- Dry-run confirms: 14d window 6W-12L matched, 0 alerts fire (last 5
  graded was 2W-3L, just under the 4-loss threshold).

### R3 result -- ✅ SHIPPED (this commit)

- New `tools/calibration_drift_monitor.py`.  Runs on grade cron.
- Computes trailing-30d Brier per nrfi_p bucket; alerts on >= +0.01
  bucket delta or >= +0.005 aggregate delta vs prior 30d.
- Today's snapshot: per-bucket Brier OK across the board (deep_yrfi
  0.305, marg_yrfi 0.223, marg_nrfi 0.273, deep_nrfi 0.245).  No
  alerts.  Prior-window comparison kicks in once we have 60 days
  of graded STRONG bets (currently 30).

### R4 result -- ✅ SHIPPED (this commit)

- `data/cluster_demotions.json`: added `reevaluate_after` field to
  schema.  `thin_pitcher_strong_v1` set to `2026-05-14`.
- New `tools/demotion_reeval_reminder.py`.  Runs on grade cron; on
  due dates, fires a Telegram with the active demotion's shadow-P&L
  snapshot + decision tree.

### R5 result -- ⚠️ CANDIDATE BUILT, NOT WORTH DEPLOYING

- New `tools/platt_candidate.py` fits Platt-scaling (logit-logistic)
  calibrator on 2025+2026 combined.
- Comparison vs production isotonic:

  | Slice | Iso Brier | Platt Brier | Delta | Verdict |
  |---|---|---|---|---|
  | 5/05-5/10 (N=82) | 0.2418 | 0.2458 | +0.0040 | ISO BETTER |
  | 5/01-5/10 (N=139) | 0.2503 | 0.2558 | +0.0055 | ISO BETTER |
  | All 2026 (N=534) | 0.2455 | 0.2480 | +0.0026 | ISO BETTER |

- Iso wins every slice.  Per-bucket Brier same story: every bucket
  is iso-better by 0.001-0.003.
- **Interpretation:** the isotonic flat zones aren't a bug after all
  -- distinct raw probabilities really do map to the same true rate
  on this data.  Platt's smooth curve loses information by averaging
  them out.  The pick_cluster HIGH drift alerts are noisy signals
  flagging structurally-correct flat zones.
- Candidate saved at `data/calibration_platt_candidate.json` for
  reference but **do not deploy**.

### R7 result -- ⚠️ CANDIDATE BUILT, BIG FINDING (real coefficient drift)

- 3-split OOS protocol via `two_stage_model.py --phase-e3`:

  | Split | Train | Test | Two-stage Brier | V2 (single LR) Brier |
  |---|---|---|---|---|
  | 1 | 2024 truepit | 2025 truepit | 0.2511 | 0.2481 |
  | 2 | 2025 truepit | 2024 truepit | 0.2595 | 0.2558 |
  | 3 | 2024 + 2025 | 2026 picks | **0.2437** | 0.2451 |

- Split 3 is the production-relevant comparison.  Candidate (refit on
  current backtests + production code) has Brier 0.2437 vs production
  raw Brier 0.2479 (measured separately via `recalibrate_v2.py`).
  **Improvement: -0.0042**, which clears the 0.003+ deployment
  threshold.
- **Why the candidate differs from production:** production LR
  weights were last refit 2026-04-29 (Phase F), but the 2024/2025
  truepit backtest CSVs were updated 2026-05-03 (T4.1 / T3.12: "xwOBA
  -> xERA proxy anchor corrected 0.310 -> 0.3205").  Production
  weights are STALE relative to the corrected training data.
- **Biggest coefficient deltas (T1 stage):**

  | Feature | Prod | Candidate | Delta | Notes |
  |---|---|---|---|---|
  | home_xera | +0.2964 | +0.0404 | -0.2560 | 8x reduction (xERA correction) |
  | away_top3c_iso | +0.1982 | +0.3813 | +0.1831 | Doubled (power) |
  | away_top3c_slg | -0.2097 | -0.4280 | -0.2183 | Doubled magnitude (opposite sign) |
  | home_fip | -0.0745 | +0.0492 | +0.1236 | **Sign flip** |

- **Candidates saved at** `data/candidates/lr_t1_split3.json` and
  `data/candidates/lr_b1_split3.json`.  Operator decides deployment.
- **If deploying:** also re-run `recalibrate_v2.py` so the calibrator
  matches the new raw distribution.

### R8 result -- 🔍 MISSING-SIGNAL CONFIRMED (operator hypothesis was right)

Operator hypothesis: the 5/10 NYY@MIL STRONG NRFI was wrong because
Aaron Judge can put a top-3 batter line over the fence in the first
inning, and the model didn't price elite Yankees offense correctly.

**Data check:**  5/10 NYY@MIL row had `away_top3c_iso=0.309`,
`away_top3c_slg=0.561`, `away_top3c_obp=0.379` -- elite power across
the board.  Model still picked STRONG NRFI at p=0.6312 and game went
YRFI (NYY scored top of 1).  Same story 5/09: away_top3c_iso=0.356,
slg=0.637, also STRONG NRFI, also lost YRFI.

**Stratified across all 30-day STRONG bets:**

```
STRONG YRFI bets stratified by max(top3c_iso):
  max_iso >= 0.25 (elite power present):  4W-1L (80%)  +2.62u
  max_iso <  0.25 (no elite power):       16W-17L (48%) -3.06u

STRONG NRFI bets stratified by max(top3c_iso):
  max_iso >= 0.25 (elite power present):  2W-2L (50%)  -0.49u
  max_iso <  0.25 (no elite power):       12W-10L (55%) -0.80u
```

When elite top-3 power is present:
- YRFI hit rate is +32pp higher than when absent (80% vs 48%)
- NRFI hit rate is -5pp lower (50% vs 55%)

**Root cause: multicollinearity.**  Production T1 LR coefficients:

- `away_top3c_iso` = +0.20 (positive → elite power *raises* P(T1 run), correct sign)
- `away_top3c_slg` = -0.21 (negative → elite power *lowers* P(T1 run), wrong sign)

ISO and SLG are highly correlated (both measure power).  Logistic
regression can't tell them apart and ends up with opposite signs that
mostly cancel each other.  The model HAS the elite-power signal but
its representation is broken.  For the 5/10 NYY:

```
T1 contribution from away_top3c_iso:  0.309 * +0.20 = +0.062
T1 contribution from away_top3c_slg:  0.561 * -0.21 = -0.118
Net effect of elite NYY offense on T1: -0.056   <-- WRONG direction
```

**Concerning side-finding:** the R7 candidate AMPLIFIES this problem
(iso=+0.38, slg=-0.43 -> net effect -0.124, even more wrong on
Yankees-style games).  Candidate's aggregate Brier improvement comes
at the cost of *worse* predictions on elite-offense games.

**Proposed fixes (ranked by ease):**

1. **Raise L2 regularization** in two_stage_model.py training.  Higher
   L2 shrinks correlated coefficients toward 0 jointly, reducing the
   seesaw.  Simple parameter tune; needs A/B against OOS.
2. **Drop SLG, keep ISO** (or vice versa).  Cleaner signal, no
   redundancy.  Requires feature-importance test to pick the winner.
3. **Replace slg + iso with a single composite** (e.g., top3c_xwOBA
   or `(slg + iso) / 2`).  Same information, no redundancy.
4. **Add a binary elite-power flag** (`max_iso >= 0.25`) as an
   additional feature.  Non-linear capture; works alongside continuous
   features.

Recommended sequence: try (1) tomorrow as a quick win; if Brier
holds, stack (4) for an explicit elite-power signal.  Defer (2)/(3)
until we know whether ISO or SLG carries the real signal.

---

## Bottom-line operator recommendations

1. **Hold the line on Friday's thin-pitcher demotion eval** -- the
   reminder cron will surface it automatically (R4 shipped).
2. **Consider deploying R7 candidate after the demotion eval** if the
   R8 multicollinearity fix is also applied -- otherwise R7 alone
   amplifies the elite-offense miss.
3. **Don't ship the Platt candidate (R5)** -- iso isn't actually broken.
4. **Highest expected-value follow-up:** R8 fix #1 (raise L2) on top
   of the R7 candidate.  This addresses the user's NYY observation
   directly without adding new features.

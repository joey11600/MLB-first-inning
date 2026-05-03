# Model Review — Scheduled for 2026-05-09 (one week from 2026-05-02)

**Status:** observation phase. **No model changes ship until this review fires.**

The 2026-05-02 audit + loss-analyzer (T2.48 / T2.49) surfaced two
specific hypotheses about systematic model weakness.  Rather than
ship a fix on a 66-loss sample, we're letting the data accumulate
for a week (target: 100+ losses) and then running this checklist.

The auto-classifier runs on every GHA grade cycle (T2.49 wired into
`.github/workflows/daily.yml`), so by 2026-05-09 the
`loss_analysis` Supabase table will have ~7 more days of fresh
classifications without any manual touches.

---

## What we learned in the 2026-05-02 audit

`tools/analyze_losses.py` ran over the 2026 season since 2026-04-01.
Result: **66 losses**, distributed by primary failure mode:

| Mode                  | Count | %    | Units lost | Type        |
|-----------------------|-------|------|------------|-------------|
| `quiet_inning`        | 22    | 33%  | 22u        | Calibration |
| `sequencing`          | 19    | 29%  | 19u        | Variance    |
| `pitcher_dominated`   | 12    | 18%  | 12u        | Calibration |
| `bunched_contact`     | 8     | 12%  | 8u         | Variance    |
| `outside_top3_event`  | 3     | 5%   | 3u         | Calibration |
| `lineup_changed_late` | 2     | 3%   | 2u         | Data lag    |
| `data_quality`        | 0     | 0%   | 0u         | —           |

**Verdict: 39/66 (59%) actionable, 27/66 (41%) variance floor.**

### Distinctive feature signatures (`tools/inspect_mode.py`)

For each actionable mode, the deep-dive ranked features by deviation
from the slate baseline (in stdevs).  The loud signals:

**`quiet_inning`** (22 losses, 22u):
- `home_xera`           **+1.11** vs slate  (0.81σ)  ← model trusts bad-xERA pitchers to give up runs
- `lambda_lr_total`     **+0.21** vs slate  (1.24σ)  ← model expects more runs
- `home_whiff_pct_rank` −7 vs slate              ← but pitcher whiffs LESS than avg
- Pattern: **high-xERA + low-whiff = soft-contact pitcher who quietly gets outs.**
  Model sees high xERA → predicts YRFI → soft contact → no runs → LOSS.

**`pitcher_dominated`** (12 losses, 12u):
- `away_whiff_pct_rank` **+15.8** vs slate (0.60σ)  ← away pitcher is high-K
- `yrfi_prob`           **+0.06** vs slate (0.77σ)  ← model still picks YRFI
- Pattern: model picks YRFI but the away pitcher's K-stuff dominates the inning.
  `whiff_pct_rank` exists in the model but isn't dominant enough to flip
  the verdict on these matchups.

**`outside_top3_event`** (3 losses, 3u):
- `home_xera`     **−1.98** vs slate (1.44σ)  ← elite-xERA pitcher
- `home_fip`      −0.83 vs slate (1.14σ)  ← elite FIP
- `home_era`      −0.91 vs slate (1.12σ)  ← elite ERA
- `nrfi_prob`     **+0.18** vs slate (2.43σ)  ← model very confident NRFI
- Pattern: **mirror image of `quiet_inning`.**  Model sees low xERA → STRONG NRFI →
  bottom-of-order HR breaks it.  Same xERA-dominance bug, opposite direction.

### Unifying theory

**The LR model is over-trusting xERA at both extremes.**  When xERA is
extreme on either end (≤2.5 or ≥5.5), it dominates the log-odds
contribution and the model goes hard on the corresponding pick.  But xERA
has its own variance — high-xERA pitchers can still be soft-contact
competent, low-xERA pitchers can still surrender deep-order HRs.

This explains 25 of 66 losses (38%) — both `quiet_inning` and
`outside_top3_event` share the same root cause.

---

## The three candidate experiments (NOT yet shipped)

These are the levers the data points to.  Each is reversible and gets
its own backtest before live deployment.

### Candidate A — Cap per-feature LR contribution

**Mechanism:** in `mlb_first_inning_predictor.classify_pick_lr`, after
computing per-feature contributions, clip each to `[-0.45, +0.45]`
log-odds.  Forces multi-feature agreement before the model goes STRONG.

**Targets:** `quiet_inning` + `outside_top3_event` (single-feature xERA
dominance).  Theoretical ceiling: 25 losses / 25u prevented.

**Risks:**
- Suppresses legitimate strong signals (an elite pitcher SHOULD push
  the model strongly toward NRFI; capping might miss real edges).
- May lower overall hit rate by demoting STRONG → LEAN on borderline picks.

**Backtest design:** rerun 2024 + 2025 seasons with the cap applied;
compare W/L + ROI vs the unclamped baseline.  Ship only if both
seasons' Brier scores improve **and** STRONG hit rate doesn't regress.

### Candidate B — Add interaction feature `xera × whiff`

**Mechanism:** add a new LR feature
`home_xera_x_whiff = home_xera × (1 - home_whiff_pct_rank/100)`.
High-xERA + low-whiff = "soft contact" → dampens YRFI lean.

**Targets:** `quiet_inning` specifically (the soft-contact pitcher
profile).  Theoretical ceiling: 22 losses / 22u prevented.

**Risks:**
- Adds a feature → requires a full LR retrain on 2024+2025 + recalibration.
- Interaction features are notoriously hard to backtest cleanly; could
  overfit to the 22 examples we observed.

**Backtest design:** same as A.  Plus: explicit cross-validation —
train on 2024, test on 2025, and vice versa.  Reject if the gain
doesn't hold across both folds.

### Candidate C — Raise YRFI STRONG threshold from 0.62 → 0.64

**Mechanism:** in `classify_pick_lr`, change the YRFI STRONG threshold.
`thresholds.json` may also need updating.

**Targets:** demotes borderline-STRONG YRFI bets to LEAN (or PASS for
LEAN-without-edge).  18 of 22 `quiet_inning` losses had P(YRFI) in
[0.57, 0.64], so most would flip to LEAN — and if the implied DK odds
don't show ≥2% edge on LEAN, they'd PASS entirely.

**Targets:** `quiet_inning` (33% of losses).
Theoretical ceiling: ~18 losses / 18u prevented, BUT also some WINS
demoted, so net is unclear without backtest.

**Risks:**
- Mechanically demotes ALL borderline YRFI STRONG bets, including the
  ones that would have won.  Needs honest backtest of net unit P&L,
  not just loss prevention.

**Backtest design:** simplest of the three — just change the threshold,
re-grade the historical slate, count W/L deltas.  Ship if net unit P&L
improves on BOTH 2024 and 2025 holdouts.

---

## The decision checklist for 2026-05-09

Run this exact sequence:

```bash
# 1. Get a fresh classification of every loss in the season
python tools/analyze_losses.py --since 2026-04-01 --reclassify

# 2. Re-inspect each actionable mode
python tools/inspect_mode.py --mode quiet_inning        --since 2026-04-01
python tools/inspect_mode.py --mode pitcher_dominated   --since 2026-04-01
python tools/inspect_mode.py --mode outside_top3_event  --since 2026-04-01
```

### Decision rules

| Observation                                                                     | Action                                                  |
|---------------------------------------------------------------------------------|---------------------------------------------------------|
| `quiet_inning` is still ≥ 25% of losses **AND** the xERA signature persists     | Backtest **Candidate A** + **Candidate C** in parallel  |
| `pitcher_dominated` is still ≥ 15% of losses **AND** away_whiff_pct_rank > 0.5σ | Add to backtest queue but secondary priority            |
| `outside_top3_event` is still ≤ 5%                                              | Don't ship the 4-9 ISO feature; it's not enough payoff  |
| Hit rate has dropped below 60% over the trailing 30d                            | **STOP** — investigate before any model change          |
| Hit rate is steady at 65-70%                                                    | Pure ship-decision; no urgency, evaluate on edge gain   |
| All actionable modes are <10% AND total loss volume is low                      | No model changes; revisit in another 2 weeks            |

### What `success` looks like for any candidate

A candidate is shippable iff **all three** are true:

1. Brier score on holdout improves by ≥ 0.005 (typical signal threshold).
2. Cross-validation on 2024↔2025 splits shows the gain in BOTH folds.
3. STRONG hit rate doesn't regress more than 1.5pp.

(These are the ship rules from `docs/KB.md`; they apply to any model change.)

### What "no change" looks like

If after the 2026-05-09 review:
- All three candidates fail the ship test, OR
- The actionable-mode percentages have shifted (e.g. `quiet_inning` is
  now only 15% as the slate matures), then

**we accept the current model's variance floor and stop tweaking.**
The system is hitting +41.99u over 30 days at 65.3% — that's a
profitable model.  The marginal gain from any of A/B/C is uncertain
and the cost of a regression is real.  Don't ship for the sake of
shipping.

---

## What runs hands-off until 2026-05-09

- **Every GHA grade cycle:** `tools/analyze_losses.py` runs and
  upserts new classifications to `loss_analysis`.  No manual action.
- **Every Railway predictor cycle (5 min):** picks + odds + grading
  flow as normal.  No change.
- **Every Railway live-state cycle (10s):** scratch detection + game
  state push as normal.
- **Every GHA hourly cron:** predict + grade + scrape + import as backup.

If anything significant shifts (hit rate drops, daily losses spike,
classifier surfaces a new dominant mode), the operator can run
`analyze_losses.py` + `inspect_mode.py` interactively at any time.

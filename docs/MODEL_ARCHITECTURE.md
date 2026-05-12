# NRFI Terminal — Complete Model Architecture

Single-source reference for the NRFI/YRFI prediction system as of
**2026-05-12, model version V2.2**.

This document is designed for an external analyst / data scientist to
understand the entire system end-to-end without needing to read code.
Every prediction, every threshold, every feature, and every operational
control is documented here.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [What the model predicts](#2-what-the-model-predicts)
3. [System architecture](#3-system-architecture)
4. [Mathematical model](#4-mathematical-model)
5. [Feature catalog (all 18 features)](#5-feature-catalog-all-18-features)
6. [Calibrator](#6-calibrator)
7. [Decision rules: STRONG vs PASS](#7-decision-rules-strong-vs-pass)
8. [Training data + methodology](#8-training-data--methodology)
9. [Validation gates](#9-validation-gates)
10. [Current production state](#10-current-production-state)
11. [Operational layer](#11-operational-layer)
12. [Monitoring & alerts](#12-monitoring--alerts)
13. [Performance metrics](#13-performance-metrics)
14. [Test history (what's been tried)](#14-test-history-whats-been-tried)
15. [Known limitations](#15-known-limitations)
16. [Candidate future improvements](#16-candidate-future-improvements)
17. [Code map](#17-code-map)
18. [Glossary](#18-glossary)

---

## 1. Executive summary

**What it is.** A betting system that predicts whether the first inning
of an MLB game will have zero runs (NRFI) or one+ runs (YRFI), and
auto-places flat 1-unit bets when the model is confident.

**Current model version.** V2.2 (deployed 2026-05-11).

**Architecture.** Two-stage logistic regression (T1 + B1 half-innings
modeled separately, combined assuming independence), followed by an
isotonic-regression calibrator.

**Performance to date (2026-04-01 → 2026-05-12).**
- 141W-90L on STRONG bets (61.0% hit rate)
- +35.5u cumulative P&L over ~230 graded bets
- 60-day Brier on combined 2026 OOS = 0.2443

**Active operational policies.**
- Flat 1u sizing on every STRONG bet (NO Kelly, NO bankroll-adjustment)
- STRONG NRFI auto-bets regardless of edge
- STRONG YRFI requires lambda ≥ 0.78
- No active cluster demotions (`thin_pitcher_strong_v1` was retired
  2026-05-12 after walk-forward backtest evidence)

**Things known to be near-optimal on current features.**
Five separate architecture experiments tried on 2026-05-12 — including
XGBoost, recent-form features, and smaller training windows — all
failed to clear a 0.003 Brier improvement gate.  Future improvement
likely requires *new data sources*, not new architectures over the
existing 18 features.

---

## 2. What the model predicts

For every MLB game on every slate, the model outputs:

| Output | Meaning |
|---|---|
| `nrfi_prob` | P(no run scored in 1st inning) ∈ [0, 1], calibrated |
| `yrfi_prob` | 1 - nrfi_prob |
| `combined_lambda` | Model's expected total 1st-inning runs (Poisson-ish) |
| `pick_side` | NRFI / YRFI / PASS |
| `pick_strength` | STRONG / LEAN / NO EDGE / LINEUP PENDING / STARTER PENDING / LOW LAMBDA / NO DATA |

**STRONG** picks auto-commit a 1-unit bet (`bet_placed=Y`); anything
else is `bet_placed=N` (no money committed).  Currently the LEAN zone
is empty in production -- the calibrator produces only STRONG or PASS.

---

## 3. System architecture

```
+-----------------+   hourly cron    +-------------------+    +----------+
|                 | ---------------> |                   | -> | Supabase |
| MLB Stats API   |                  |                   |    +----------+
| (statsapi.mlb)  |                  | mlb_first_inning_ |          |
|                 |                  | predictor.py      |          v
| - schedule      |                  |                   |  +---------------+
| - boxscore      |                  | log_picks()       |  |               |
| - person stats  |                  |                   |  | dashboard     |
| - lineups       |                  +-------------------+  | (Vercel /     |
+-----------------+                          |              |  Next.js)     |
                                             v              |               |
+-----------------+                  +-------------------+  +---------------+
|                 |                  |                   |        ^
| DraftKings      | ---------------> | scrape_dk_odds.py |        |
| Sportsbook API  | hourly cron      | apply_manual_     |  +-----+-----+
|                 |                  |   odds.py         |  | /api/board|
+-----------------+                  +-------------------+  | /api/roi  |
                                             |              | (server)  |
                                             v              +-----------+
+-----------------+                  +-------------------+
|                 |                  |                   |
| Open-Meteo      | ---------------> | weather features  |
| Weather API     |                  | per park          |
|                 |                  +-------------------+
+-----------------+                          |
                                             v
                                  +-------------------+
                                  | data/picks_2026   |
                                  |   .csv            |  <- canonical
                                  |                   |     ledger
                                  | + Supabase mirror |
                                  +-------------------+
                                             |
                                             v
                                  +-------------------+
                                  | Telegram bot      |
                                  | @nrfi_terminal    |  <- alerts on
                                  |   _bot            |     pick flips,
                                  +-------------------+     drift, etc.
```

### Component responsibilities

- **`mlb_first_inning_predictor.py`** — Single-file predictor. Pulls
  schedules, fetches pitcher / batter / weather / umpire data, builds
  feature vectors, runs the LR + calibrator, writes verdicts to
  `picks_2026.csv` via `tracker.log_picks()`.
- **`tracker.py`** — CSV ledger + Supabase mirror + grading.  Owns
  the schema (FIELDS list), the lock-aware refresh logic (so post-lock
  picks freeze), and grading from MLB API.
- **`scrape_dk_odds.py`** — Pulls DraftKings 1st-inning lines, writes
  them to picks rows when matched by game_pk.
- **GitHub Actions cron** (`.github/workflows/daily.yml`) — Runs
  every hour (and every 30 min in the 5pm-10pm ET window) to refresh
  picks; runs every night at 11:30 PM ET to grade yesterday.
- **Railway worker** (`workers/predictor_loop.py`) — Runs every 5
  minutes as a continuous loop.  Faster predict cadence than GHA
  cron alone; uses same code paths.
- **Dashboard** (Next.js at `nrfi-terminal.vercel.app`) — Reads
  picks_2026.csv (via filesystem) + Supabase (for live updates).
- **Telegram bot** — Pings on pick flips, drift alerts, cluster
  streaks, demotion reminders.

---

## 4. Mathematical model

### Two-stage logistic regression

The 1st inning has two distinct halves with different pitcher/batter
combinations:

- **Top of 1st (T1)**: home pitcher faces away batters
- **Bottom of 1st (B1)**: away pitcher faces home batters

We fit two separate logistic regressions, one per half:

```
P(T1_run) = sigmoid(W_t1 · X_t1 + b_t1)
P(B1_run) = sigmoid(W_b1 · X_b1 + b_b1)
```

where `X_t1` is an 18-dimensional feature vector capturing T1-relevant
inputs (home pitcher quality + away offense quality + venue/weather/umpire),
and `X_b1` is the parallel B1 vector.

Each X is z-score standardized using the training-set mean/stddev for
each feature (stored alongside the weights in `lr_t1.json` /
`lr_b1.json`).

### Combining halves: independence assumption

```
P(NRFI_raw) = P(no T1 run) * P(no B1 run)
            = (1 - P(T1_run)) * (1 - P(B1_run))
```

This treats the two halves as independent.  It's a simplification
(weather effects correlate across both halves), but it produces a
well-grounded joint probability without needing a single model with
36 features and the multicollinearity that would create.

### Calibration step

The raw `P(NRFI_raw)` is passed through an isotonic-regression
calibrator (see [Section 6](#6-calibrator)) to produce the final
`nrfi_prob` used for STRONG/PASS decisions:

```
nrfi_prob = calibrator.predict(P(NRFI_raw))
```

### Lambda (expected total 1st-inning runs)

A separate Poisson-ish estimate of total runs:

```
combined_lambda = lambda_lr_t1 + lambda_lr_b1
```

where each `lambda_lr_*` is fit by a separate regression that predicts
expected runs (not just run probability).  Used for the YRFI lambda
floor (STRONG YRFI only fires when `combined_lambda >= 0.78`).

---

## 5. Feature catalog (all 18 features)

Same feature concept applies to both T1 and B1, mirrored across sides.
The T1 features are listed; B1 swaps "home" ↔ "away" everywhere except
the umpire (which is the same).

### Half-inning structural

1. **`fi_park_nrfi_rate`** — Empirical 1st-inning NRFI rate at this park
   over recent seasons.  Captures venue effects (Coors vs Petco).
   Source: `data/fi_park_factors.json`, rebuilt periodically.

### Home pitcher (T1) / Away pitcher (B1) signals

2. **`home_fip`** — Pitcher's Fielding-Independent Pitching (ERA estimate
   based only on Ks, BBs, HRs).  Bayesian-blended with prior year +
   league avg.  Stable predictor of pitcher quality.
3. **`home_xera`** — Expected ERA based on Statcast batted-ball data
   (priors-pooled per T4.2).  Captures pitcher quality independent of
   defense / sequencing luck.
4. **`home_whiff_pct_rank`** — Pitcher's whiff% percentile rank vs
   league (priors-pooled).  Higher = more swings-and-misses = lower
   contact = more NRFI tilt.
5. **`era_gap_t1`** — `home_era - away_era`.  Signed difference; the
   half-with-the-worse-pitcher is more likely to give up a run.
6. **`home_p_last5_pitcher_nrfi`** — Pitcher's NRFI rate over their
   last 5 STARTS.  Short-term form signal.
7. **`home_p_last10_pitcher_nrfi`** — Same but last 10 starts.
   Smoother recent-form signal.
8. **`home_pvt_nrfi_rate`** — Career NRFI rate of this pitcher vs
   this opposing team (Bayesian-shrunk toward pitcher's overall
   rate).  Captures matchup history.  Phase F addition.
9. **`home_avg_ip_per_start`** — Pitcher's average innings per start
   over last 5 starts.  < 3 IP = opener pattern, predicts bullpen
   roulette in early innings.  Phase F addition.

### Away offense (T1) / Home offense (B1) signals

10. **`away_obp`** — Team's overall on-base percentage (season-to-date,
    Bayesian-blended).
11. **`away_top3c_obp`** — Top 3 of today's lineup's OBP, PA-weighted.
    More precise than team OBP because it focuses on who actually
    bats in the 1st inning.  Falls back to team if lineup unposted.
12. **`away_top3c_slg`** — Top 3 batters' SLG (slugging).  Power
    signal: how many extra bases per at-bat.
13. **`away_top3c_iso`** — Top 3 batters' ISO (isolated power = SLG
    minus batting average).  Pure power signal: extra bases above
    what singles would produce.

### Weather (open-meteo per park)

14. **`wx_temp_c`** — Temperature in Celsius at park.  Warmer air
    travels farther; affects HR rates.
15. **`wx_wind_kmh`** — Wind speed at park.  High wind out = more HRs;
    wind in = fewer.
16. **`wx_humidity`** — Relative humidity.  Higher humidity = less
    dense air = balls travel farther.
17. **`wx_is_dome`** — Binary: 1 if indoors (no real weather effects),
    0 if outdoor.

### Umpire signal

18. **`home_plate_ump_nrfi_rate`** — The home-plate umpire's career
    NRFI rate, Bayesian-shrunk toward league average using
    `min_appearances=20`.  Captures called-strike-zone tendency.

### Feature provenance / quality tags

Each pitcher and team-batting input has a "quality tag" that doesn't
go into the LR but flags how reliable the input is:

- **`live`** — pitcher has ≥ 80 IP in current season (full sample)
- **`ltd`** — 20-80 IP (limited)
- **`sm`** — 1-20 IP (small)
- **`avg`** — no usable data; using league average defaults

When ANY input on either side is `avg`, the model labels the row
`NO DATA` and forces PASS.

---

## 6. Calibrator

### Why we need one

Raw LR output systematically over-predicts NRFI by ~3pp on 2026 data
(verified by `diagnose_strong_nrfi.py` historical analysis).  Without
calibration, picks at the STRONG threshold would be 3pp too confident.

### How it works

The calibrator is an **isotonic regression** in probability space,
fit on (raw_LR_prediction, actual_outcome) pairs:

- Training data: 2025 backtest (full season) + 2026 graded picks
- N=2927 samples
- 20 bins
- Monotonic increasing constraint (higher raw → higher calibrated)

The calibrator file (`data/calibration_v2.json`) stores 20 (center,
rate) tuples.  At predict time, we linearly interpolate between
adjacent bins.

### What it looks like in practice

The current V2 calibrator produces "flat zones" where multiple
distinct raw probabilities map to the same calibrated value:

```
raw 53.0% → 48.8%   |
raw 53.8% → 48.8%   |  5 raw bins
raw 54.7% → 48.8%   |  all calibrate
raw 55.5% → 48.8%   |  to 48.8%
raw 56.4% → 48.8%   |
raw 57.3% → 56.5%   <- jump
raw 58.2% → 56.5%
raw 59.5% → 63.7%
```

These flat zones are an isotonic regression artifact and reflect that
within those raw-prob ranges, the empirical NRFI rate is genuinely
indistinguishable.  Switching to Platt scaling (smooth logistic
curve) was tested 2026-05-11 and lost by 0.003-0.006 Brier on every
slice; the flat zones are *correct* on this data.

### When it gets refit

- Manual: when operator decides via `python recalibrate_v2.py` followed
  by a deploy commit.
- Automatic: was weekly via cron, **DISABLED 2026-05-11** because no
  OOS validation guard.

---

## 7. Decision rules: STRONG vs PASS

After the calibrator outputs `nrfi_prob`, classification follows these
thresholds:

```
if nrfi_prob >= 0.56:                      STRONG NRFI
elif nrfi_prob < 0.44:
    if combined_lambda >= 0.78:            STRONG YRFI
    else:                                  PASS · LOW LAMBDA
else:                                       PASS · NO EDGE   (the 0.44-0.56 dead zone)
```

### Threshold justification

- **`STRONG_NRFI_P = 0.56`** (T2 audit): tightened from the original
  0.60 to capture additional profitable bets in the 0.56-0.60 band.
  Profitable historically (+27.7u live rebet vs +17.7u at 0.58).
- **`PASS_LO_P = 0.44`** (T2 audit): loosened from 0.40 to capture
  +5 profitable bets in the 0.40-0.42 zone.
- **`LAMBDA_YRFI_FLOOR = 0.78`**: at lambda 0.74-0.78, the calibrator
  squashes raw NRFI prob hard enough to cross 0.44, but those games
  sit in a soft-edge zone where historical hit rate falls to ~44%.
  Backtest: filtering this 9-pick bucket lifts season +1.36u with no
  downside.

### Guards that override these rules

Several conditions force PASS regardless of `nrfi_prob`:

| Condition | Pick state | Reason |
|---|---|---|
| Lineup not posted, top3c from team-fallback | `LINEUP PENDING` | Top-3 stats imputed, model confidence reduced |
| Starting pitcher unknown / TBD | `STARTER PENDING` | Pitcher quality unknown |
| Both top3c sources = `league_default` AND lineup empty | `NO DATA` | Insufficient inputs |
| Game graded W/L/PASS/POSTPONED/SUSPENDED | (frozen) | Settled play |
| Game already started (>= game_time_et) | (frozen) | Post-lock |
| `bet_placed='Y'` already stamped | (frozen) | T2.23 bet-time odds lock |
| Pre-game game_time - 60 min reached | (frozen at current verdict) | T-60 lock |

### Bet sizing

- STRONG → 1.0 units (flat).  No Kelly, no bankroll-adjustment.
- Operator policy explicitly excludes per-bet sizing variation.

---

## 8. Training data + methodology

### Sources

| Source | Rows | Date range |
|---|---|---|
| `data/backtests/backtest_2024-04-01_to_2024-09-30_truepit.csv` | 2409 | 2024 season (April 1 - September 30) |
| `data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv` | 2393 | 2025 season |
| `data/picks_2026.csv` | 570+ | 2026 season (in-progress, growing daily) |

Pre-pitch-clock seasons (2022, 2023) are deliberately **excluded** from
training -- the distribution shift after the pitch clock was introduced
(2023+) hurt backtests when included.  This is documented in
`nrfi_model_architecture.md` (operator memory).

### Training procedure

```
1. Run `python two_stage_model.py --phase-e3 \
       --train 2024_truepit.csv 2025_truepit.csv \
       --test  picks_2026.csv \
       --save-t1 data/lr_t1.json --save-b1 data/lr_b1.json`
2. Run `python recalibrate_v2.py` to refit calibrator on 2025+2026
   combined.
3. Update MODEL_VERSION constant in
   mlb_first_inning_predictor.py.
4. Commit + push to claude/mlb-inning-run-predictor-QyazL branch.
```

L2 regularization = 0.05 (default in `two_stage_model.py`).  Tested
values 0.05-10 on 2026-05-11; no material change in Brier (sweep in
`data/candidates/v2.2_l2_sweep/`).

### "truepit" caveat

The `_truepit` backtest files use real per-pitcher / per-batter
Statcast stats as of the actual game date, eliminating future
data leakage from rolling-forward stats.  Earlier `_leakfree`
versions used different methodology -- current production uses
`_truepit`.

### Schema-evolution policy

When adding a feature (e.g. Phase G top3c_last10_*), historical
CSVs are backfilled via a script (`tools/backfill_top3_last10.py` is
the canonical example).  New columns are appended to
`tracker.FIELDS`, blanks are tolerated, and the predictor uses
default fallbacks at predict time.

---

## 9. Validation gates

Before any model change ships to production, it must clear:

### Gate A: 3-split OOS Brier

```
S1: train 2024, test 2025    -- Brier must improve
S2: train 2025, test 2024    -- Brier must improve
S3: train 2024+2025, test 2026  -- Brier must improve by >= 0.003
```

Required: pass on >= 2 of 3 splits AND S3 Δ ≤ -0.003.

### Gate B: Walk-forward backtest

Re-run history with the new model.  Required: matches or beats
current V2.2's +35.5u season-to-date, OR stays within 5u.

### Gate C: No regression on elite-power subset

When changing features that touch top-3 batter inputs, validate
Brier on the elite-power slice (max top3c_iso >= 0.25) doesn't
regress.  This guards against subtle multicollinearity costs.

### Gate D: Shadow track for 2 weeks

After deploy, run the prior version in shadow alongside the new one.
If new model underperforms shadow by 3u+ over 30 graded bets, roll
back.

These gates have rejected every V3 candidate tested so far
(2026-05-12 audit).

---

## 10. Current production state

| Component | Version / value | Last touched |
|---|---|---|
| LR T1 weights | `data/lr_t1.json` (V2.2) | 2026-05-11 |
| LR B1 weights | `data/lr_b1.json` (V2.2) | 2026-05-11 |
| Calibrator | `data/calibration_v2.json` | 2026-05-11 |
| Park factors | `data/fi_park_factors.json` | 2026-05-11 |
| MODEL_VERSION | `"V2.2"` | 2026-05-11 |
| STRONG_NRFI_P | 0.56 | T2 audit |
| PASS_LO_P | 0.44 | T2 audit |
| LAMBDA_YRFI_FLOOR | 0.78 | Backtested |
| Active cluster demotions | none (all retired) | 2026-05-12 |
| Weekly auto-recalibrate | DISABLED | 2026-05-11 |
| V2.1 shadow tracker | RUNNING | 2026-05-11 |
| Calibration drift monitor | RUNNING | 2026-05-11 |
| Cluster demotion re-eval cron | RUNNING (no-op while no active demotions) | 2026-05-11 |
| Cluster discovery scanner | RUNS NIGHTLY (read-only output to logs) | 2026-05-10 |
| Loss-cluster monitor | RUNNING (`yrfi_deep` predicate, alert if 5L in 6) | 2026-05-11 |

### Archived for rollback

- `data/archive/v2.1/lr_t1.json`
- `data/archive/v2.1/lr_b1.json`
- `data/archive/v2.1/calibration_v2.json`
- `data/archive/v2.1/fi_park_factors.json`

To roll back: `cp data/archive/v2.1/* data/`; flip MODEL_VERSION to
"V2.1"; commit + push.  Documented in `CHANGELOG.md` under the
2026-05-11 V2.2 deploy entry.

---

## 11. Operational layer

Several control surfaces sit on top of the model itself, allowing the
operator to override or adjust without retraining:

### Cluster demotions (`data/cluster_demotions.json`)

Operator-maintained list of feature predicates that, when matched,
flip a STRONG row to PASS-with-explanation.  Use case: a model
verdict the operator wants to skip until further data resolves
the question.

Schema:
```json
{
  "demotions": [
    {
      "id": "unique_id",
      "reason": "human-readable why",
      "side": "NRFI|YRFI|null",
      "nrfi_prob": {"min": 0.0, "max": 0.4},
      "combined_lambda": {"min": 0.8, "max": 1.3},
      "park_factor": {"min": 0.9, "max": 1.3},
      "pitcher_quality_min": ["sm","ltd"],
      "active": true,
      "reevaluate_after": "2026-05-14"
    }
  ]
}
```

Applied by `tools/apply_cluster_demotion.py` in the predict cron.
Demoted rows render as `PASS - Cluster demotion: STRONG XYZ (id)` on
the dashboard.

**Currently no active demotions** (thin_pitcher_strong_v1 retired
2026-05-12 after walk-forward backtest evidence -- demotion would
have cost -19u over the season).

### Manual odds overrides (`data/manual_odds_overrides.csv`)

Operator manually enters DK prices for games where the auto-scrape
missed.  Applied by `tools/apply_manual_odds.py` in predict cron.
Patches `market_*_odds` + recomputes `profit_loss_units`.

### Pick lock policy

Three independent locks prevent post-decision drift:

1. **Graded terminal** (`graded_result in WIN/LOSS/PASS/POSTPONED/SUSPENDED`):
   row frozen, never re-predicted.
2. **>24h past slate date**: row frozen even if game_time_et missing
   / malformed.
3. **`created_at` > 12h stale**: row frozen (likely a frozen-game
   state).
4. **T-60 minutes pre-game**: pick verdict frozen so the bet timing
   matches odds locked in by `bet_placed=Y`.

### Bet-time odds lock (T2.23)

Once `bet_placed='Y'` is stamped, `market_*_odds` freezes.
Subsequent DK scrapes update `opened_*_odds` only (closing-line
tracking).  Rationale: the operator is in the bet at that price;
moving the displayed line would create false dissonance.

---

## 12. Monitoring & alerts

### Telegram alerts (via `@nrfi_terminal_bot`)

| Alert type | Trigger | Dedup window |
|---|---|---|
| `pick_flip` | Pre-lock pick_side changes | 4h |
| `weather_drift` | Wind/temp/humidity shift on placed bet | 6h |
| `loss_cluster_streak` | Last 5-of-6 in defined cluster are L (≤20% hit) | 14d |
| `tentative_resolved` | LINEUP-PENDING row resolves to WIN | 24h |
| `strong_orphan_no_odds` | STRONG bet grades W/L without captured DK odds | 24h |
| `calibration_drift` | Per-bucket Brier degrades 0.01+ vs prior 30d | per date |
| `demotion_reeval` | Active demotion's reevaluate_after date reached | per date |
| `v22_shadow_underperform` | V2.2 P&L below V2.1 shadow by 3u+ over 30 graded | per date |

Configured via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` GHA secrets.
Silent no-op without them.  Failures caught + logged but never break
the predictor.

### Diagnostic outputs

- `data/diagnostics/picks/<date>.json` — Per-pick "why this pick?"
  features + LR contribution breakdown + warnings list (e.g., extreme
  xera, flat-zone hit).
- `data/diagnostics/drift_<date>.csv` — Per-feature distribution
  drift vs trailing 7-day baseline.
- `data/diagnostics/drift_alerts.csv` — Running log of HIGH-severity
  drift events.
- `data/diagnostics/shadow_summary.csv` — V2.x shadow comparison
  rolling totals.

### Dashboard surfaces

- **Bankroll equity curve** with expected-trend overlay (added 2026-05-12)
- **Active demotions banner** above the board
- **Shadow P&L card** next to RoiPanel
- **Cluster bucket badges** on each pick row (added 2026-05-11)
- **Pass-reason chip** explaining why a row is PASS
- **Elite-power chip** flagging top-3 ISO >= 0.25
- **Lambda chip** showing exact value on LOW LAMBDA rows
- **Data quality badge** flagging fallback-data rows
- **Model version pill** on expanded GameDetails

---

## 13. Performance metrics

### Season-to-date (2026-04-01 → 2026-05-12)

| Metric | Value |
|---|---|
| Total STRONG bets | 232 |
| Wins | 141 |
| Losses | 90 |
| Ungraded | 1 (today's pending) |
| Hit rate | 61.0% |
| Cumulative P&L | +35.5u |
| Avg per-bet ROI | +0.154u |

Break-even hit rate at -110 odds: 52.4%.  Current 61% is comfortably
above.

### Trailing windows

| Window | Bets | Record | P&L |
|---|---|---|---|
| 30 days | 155 | 94W-61L | +21.8u |
| 7 days | 25 | 13W-12L | -1.5u |
| 4 days (5/08-5/11) | 15 | 5W-10L | -6.2u |
| Today (5/12, in progress) | 1 | 0W-0L (ungraded) | — |

### Calibration quality

- Combined 2026 Brier (LR raw, no calibrator): 0.2479
- Combined 2026 Brier (V2.2 calibrated): 0.2443
- Calibrator improvement: -0.0036 Brier
- Per-bucket Brier (`docs/2026-05-11_system_audit.md`):
  - deep_yrfi (<0.40): 0.305
  - marg_yrfi (0.40-0.44): 0.223
  - marg_nrfi (0.56-0.60): 0.273
  - deep_nrfi (>=0.60): 0.245

### Per-bucket hit rates

| Bucket | Trailing-30d hit rate | P&L |
|---|---|---|
| deep_yrfi (<0.40) | 33% (6W-12L) | -7.00u |
| marg_yrfi (0.40-0.44) | 70% (14W-6L) | +6.57u |
| marg_nrfi (0.56-0.60) | 33% (2W-4L) | -2.32u |
| deep_nrfi (≥0.60) | 57% (12W-9L) | +0.03u |

---

## 14. Test history (what's been tried)

Comprehensive list of model-side experiments, with outcomes:

### Shipped (in production)

| Date | Change | Brier impact | P&L impact |
|---|---|---|---|
| 2026-04-29 | Phase F: pvt_nrfi_rate + avg_ip_per_start | -0.001 | +est. |
| 2026-04-29 | Eliminate data leakage in backtests | -0.002 | +5.9u |
| 2026-04-29 | last10_pitcher_nrfi feature | -0.001 | +est. |
| 2026-04-29 | top3c slg + iso power signal | -0.001 | +est. |
| 2026-04-29 | signed era_gap feature | -0.001 | +est. |
| 2026-05-04 | Auto-recalibrate (weekly) | -0.0004 | small |
| 2026-05-06 | V2.1 lock-in | (baseline) | (baseline) |
| 2026-05-11 | V2.2 (xERA anchor fix refit) | -0.0042 | est. +est. |
| 2026-05-12 | Retire thin_pitcher_strong_v1 demotion | n/a | +est. |

### Tested + REJECTED

| Date | Hypothesis | Result |
|---|---|---|
| 2026-05-03 | T2.53 disabled ERA-blend shrinkage | -4.6u in one day; reverted |
| 2026-05-04 | T4.1 catcher framing feature | -2.17u walk-forward; rejected |
| 2026-05-11 | Platt-scaling calibrator | +0.003-0.006 Brier; rejected |
| 2026-05-11 | Drop top3c_slg OR top3c_iso | +0.0024-0.0027 Brier; rejected |
| 2026-05-11 | L2 sweep | no material change |
| 2026-05-12 | Thin-pitcher demotion | -19u walk-forward; retired |
| 2026-05-12 | V2.3 = 2026-only training | underperforms vs V2.1 actual |
| 2026-05-12 | Phase G: top3c_last10_* features | -0.0001 Brier; rejected |
| 2026-05-12 | XGBoost (two-stage + single-stage) | +0.0015-0.0041 Brier; rejected |
| 2026-05-12 | 2025+2026-train (drop 2024) | +0.0022 Brier, within noise |

### Pending observation

| Item | Started | Decision date |
|---|---|---|
| V2.1 shadow tracker | 2026-05-11 | After ~30 graded bets |
| V2.2 30-bet performance check | 2026-05-11 | When n=30 graded |

---

## 15. Known limitations

### Structural

1. **Calibrator flat zones.** Isotonic regression artifact; structurally
   correct on this data but creates jumps at zone boundaries.  Switching
   to Platt scaling tested + lost; nothing to do.
2. **Two-stage independence assumption.** Real T1/B1 outcomes correlate
   weakly (shared weather, shared field).  Modeling jointly would
   add complexity for small gain.
3. **LR has 36 coefficients total.** Estimated on 4800 train samples
   → ~133 samples per parameter.  Stable, but limits the complexity
   of relationships the LR can learn.

### Data quality

4. **Pre-game lineup uncertainty.** ~30% of STRONG bets lock at T-60
   with team-fallback top3c data when MLB hasn't posted the lineup.
   We've tightened the T-60 → lineups-required-for-STRONG guard
   (T2.20-T2.26), but residual error remains.
5. **Pitcher quality drift mid-season.** Players added/removed from
   the rotation, arsenal changes, injuries.  The model doesn't see
   these in real time -- xera shrinkage helps but doesn't eliminate.
6. **No bullpen modeling.** Starting pitcher can be pulled after
   2 innings; the LR doesn't see who's coming in.
7. **No platoon splits.** Lefty batter vs lefty pitcher is treated
   the same as RvR.
8. **No defense modeling.** Outfield jumps, infielder ranges, catcher
   framing -- none in the model (catcher framing tested + rejected).

### Operational

9. **Shadow tracking is V2.1 vs V2.2 only.** Other shadow comparisons
   would need new infrastructure.
10. **Manual odds override drift.** Operator-entered prices can be
    stale if not updated before lock.
11. **Cron rate-limiting.** Predict crons run every 30-60 min;
    sub-30-min weather drift isn't captured.

---

## 16. Candidate future improvements

Ranked by expected Brier improvement.  None has been validated; all
are speculative until tested.

### High-effort, high-uncertainty

1. **Bullpen quality features** (expected pull risk, bullpen ERA).
   Real signal on opener-heavy or quick-hook teams.
2. **Per-pitcher pitch-mix features** (FB%, slider usage, splitter
   appearance).  Available from Statcast.  Could capture stuff
   quality independent of ERA.

### Medium-effort, low-uncertainty

3. **Batter platoon splits** (top3c_obp_vs_L, top3c_obp_vs_R).  Known
   real signal; cheap to compute.  Risk: small samples per split.
4. **Park-specific batter/pitcher history**.  Different shapes of park
   (Coors, Petco) interact with batter/pitcher style.  Cheap.

### Higher-frequency observability

5. **Sub-15-min weather refresh on locked games.**  Cron currently
   hourly; could spin up a faster-cadence pre-game weather job.
6. **Real-time umpire scratch detection.**  Rare but happens.  Would
   require live cross-check during T-60 window.

### Model architecture

7. **Hierarchical model** (per-team-pair fixed effects).  Lots of new
   parameters; needs careful regularization.
8. **Mixture model** (separate LR for different game phases or
   archetypes).  Powerful but hard to interpret.

### Calibration

9. **More frequent calibrator refits with OOS guard.**  Currently
   manual; could be weekly with auto-rollback if Brier regresses.
10. **Per-bucket calibration override** for known-broken zones (e.g.
    deep_yrfi).  Risk of overfitting to recent data.

### Tested + rejected (do NOT revisit unless new data justifies)

- Catcher framing (T4.1: -2.17u walk-forward)
- XGBoost on current features (2026-05-12)
- Phase G recent-form features (2026-05-12)
- 2026-only training (2026-05-12)
- Platt-scaling calibrator (2026-05-11)
- Per-bet sizing variation (operator policy)

---

## 17. Code map

```
MLB-first-inning/
├── mlb_first_inning_predictor.py    # Main predictor (1900+ lines)
├── tracker.py                       # CSV ledger + Supabase mirror
├── backtest.py                      # Per-pitcher/batter API fetchers
├── two_stage_model.py               # LR training script
├── recalibrate_v2.py                # Calibrator refit script
├── lr_baseline.py                   # LogReg implementation
├── calibration.py                   # ProbCalibrator (isotonic)
├── workers/
│   ├── predictor_loop.py            # Railway 5-min loop
│   └── live_state.py                # Live grade/odds worker
├── tools/
│   ├── apply_cluster_demotion.py    # Demotion applier
│   ├── apply_manual_odds.py         # Manual odds patcher
│   ├── calibration_drift_monitor.py # Per-bucket Brier alerter
│   ├── cluster_discovery.py         # Find new candidate clusters
│   ├── cluster_shadow_pnl.py        # Shadow P&L for active demotions
│   ├── demotion_reeval_reminder.py  # Telegram reminder on due date
│   ├── feature_drift_monitor.py     # Feature distribution drift
│   ├── loss_cluster_monitor.py      # Streak-based cluster alerts
│   ├── pl_calc.py                   # Canonical P&L calculator
│   ├── reconcile.py                 # Post-grade sweep
│   ├── v21_shadow_predict.py        # V2.1 shadow predictions
│   ├── v21_vs_v22_compare.py        # Shadow performance comparison
│   ├── v23_walkforward_backtest.py  # 2026-only walk-forward
│   ├── two_stage_xgb.py             # XGBoost training script
│   ├── phase_g_validation.py        # 3-split OOS harness
│   ├── backfill_top3_last10.py      # Phase G backfill
│   └── v22_feature_ablation.py      # Feature drop tests
├── dashboard/                        # Next.js dashboard
│   ├── app/api/                      # Server endpoints
│   ├── components/                   # React components
│   └── lib/                          # Server-side helpers (roi.ts, board.ts)
├── data/
│   ├── lr_t1.json                   # T1 LR weights (V2.2)
│   ├── lr_b1.json                   # B1 LR weights (V2.2)
│   ├── calibration_v2.json          # Isotonic calibrator (V2)
│   ├── fi_park_factors.json         # Per-park NRFI rates
│   ├── picks_2026.csv               # Season ledger
│   ├── pick_changes.csv             # Pick flip journal
│   ├── cluster_demotions.json       # Operator-maintained demotions
│   ├── manual_odds_overrides.csv    # Operator-entered DK prices
│   ├── notifications_log            # Telegram dedup
│   ├── archive/v2.1/                # Rollback snapshot
│   ├── candidates/                  # Shelved test models
│   └── backtests/
│       ├── backtest_2024-*_truepit.csv
│       └── backtest_2025-*_truepit.csv
├── docs/
│   ├── MODEL_ARCHITECTURE.md        # This document
│   ├── KB.md                        # System overview
│   ├── PLAYBOOK.md                  # Operational runbook
│   ├── CLUSTER_DISCOVERY.md         # 3-stage cluster pipeline
│   ├── MANUAL_ODDS.md               # Operator override workflow
│   ├── PHASE_G_recent_form.md       # Phase G design (rejected)
│   ├── 2026-05-11_system_audit.md   # First audit
│   └── 2026-05-12_model_consistency_audit.md  # Negative-results audit
├── CHANGELOG.md                      # Dated change log
├── ROADMAP.md                        # Forward-looking upgrade list
├── AUDIT.md                          # Running audit checkbox list
├── CLAUDE.md                         # Agent rules (this repo)
└── .github/workflows/daily.yml       # GHA cron orchestration
```

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **NRFI** | No Run First Inning — neither team scores in the 1st |
| **YRFI** | Yes Run First Inning — at least 1 run scored in the 1st |
| **T1** | Top of 1st inning — away team batting |
| **B1** | Bottom of 1st inning — home team batting |
| **STRONG** | High-confidence pick, auto-bets 1 unit |
| **LEAN** | Medium-confidence pick (currently empty zone in V2.2) |
| **PASS** | Model declines to bet (multiple sub-reasons) |
| **OBP** | On-base percentage |
| **SLG** | Slugging percentage |
| **ISO** | Isolated power = SLG - batting average |
| **FIP** | Fielding-Independent Pitching = ERA estimate from Ks/BBs/HRs |
| **xERA** | Expected ERA from Statcast batted-ball quality |
| **whiff%** | Pitcher's swing-and-miss rate |
| **pvt** | Pitcher-vs-team (BvP at team level) |
| **top3c** | Top-3 batters in the lineup (combined) |
| **calibrator** | Isotonic-regression post-LR mapping |
| **brier** | Brier score = mean((predicted - actual)²); lower = better |
| **CLV** | Closing-line value (open price vs close price) |
| **flat 1u** | Constant 1-unit bet size on every STRONG pick |
| **lock** | Frozen pick state (graded / >24h / T-60 / bet placed) |
| **demotion** | Operator-set rule that forces STRONG to PASS for a feature pattern |
| **shadow** | Parallel model evaluation without real-money exposure |
| **walk-forward** | Backtest that retrains daily, predicts the next day |

---

*Last updated: 2026-05-12.  Maintainer: operator + agent.  Update
when MODEL_VERSION bumps or major operational policy changes.*

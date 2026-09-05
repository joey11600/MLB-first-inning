# Changelog

Dated log of meaningful changes to the NRFI Terminal system (predictor, tracker,
dashboard, ops). For the running list of open audit items see [AUDIT.md](./AUDIT.md).
For the forward-looking upgrade list see [ROADMAP.md](./ROADMAP.md).
For the system overview see [docs/KB.md](./docs/KB.md).

Format: latest first. Each entry is grouped Added / Changed / Fixed / Deferred
with audit IDs (`T1.1`, `T4.15`, …) cross-referenced to AUDIT.md. Performance
section captures actual picks accuracy on/around the change date.

---

## [2026-09-04b] - The shadow model gets a card on /history, read straight from Supabase

### Added

- **`dashboard/lib/shadow-compare.ts`** -- a PURE mirror of
  `tools/shadow_report.py`: same filters (graded rows with a first-inning
  result; live bets = `bet_placed=Y` on the YRFI side; shadow bets =
  `shadow_pick_label` STRONG YRFI), same sizing (`stakeUnitsFor`, the board's
  own quarter-Kelly), same No.1 rule (highest p(YRFI), better price breaking
  ties), same paired-nights output. No `node:fs`, no Supabase client, so a
  client component can import its types without dragging the filesystem into
  the browser bundle (the `top-pick-rank.ts` lesson). Every total is a
  fixed-basis `FlatUnits`; the units guard passed (8 cumulative forms
  rejected, 6 point-in-time forms accepted).
- **`dashboard/components/ShadowModelCard.tsx`** on `/history`, right after the
  No.1's own record: the live model (as actually staked / same-rule
  quarter-Kelly / flat 1u), the shadow model's would-have-been on the same two
  bases, each model's No.1 with the PAIRED line (nights both had a top play,
  same game count, records side by side), a by-night table, the label
  agreement census, and tonight's STRONG lists for both. Reuses
  `TopPickHistory.module.css` so it reads as one family. When no graded row
  carries a shadow value yet it says so instead of showing zeros.
- `app/history/page.tsx` builds the report server-side through the same
  Supabase-first `loadLedgerRows` the ROI panel uses, so **the card works and
  updates live while the GitHub cron is down (T8.42)** -- the ledger rows with
  shadow values exist only in Supabase until that runner is back.
- First live reading (prod build, 2026-09-04 ~21:00 ET): 14 graded games;
  live 2-0 +4.91u booked / +3.46u same-rule; shadow 2-0 +4.91u same-rule on
  the same two games; No.1 STL@COL for both, W; labels agree on 13 of 14
  (NYY@SD: live LEAN NRFI, shadow PASS).

No pick, gate, stake, alert or card reads any shadow field. Display only.

---

## [2026-09-04] - SHADOW MODEL LIVE; and the 09-03 "validated" verdict is CORRECTED to "inconclusive" after a data fix

### Added -- the shadow model (operator: "run an alternate version side by side")

- **Every predict tick now scores each game twice.** The live model's verdict
  is published, staked and alerted exactly as before. A second model -- the
  candidate in `data/candidates/refit2026_fiform/` -- is scored on the same
  inputs and its opinion goes into six new ledger columns: `shadow_model`,
  `shadow_nrfi_prob`, `shadow_nrfi_prob_raw`, `shadow_pick_label`,
  `home_fi_form`, `away_fi_form`. Nothing downstream reads them. Kill switch
  `NRFI_SHADOW_MODEL=disabled`.
- **Fail-open, and pinned by tests.** `mlb_first_inning_predictor._shadow_score`
  builds the candidate's input vectors BY NAME from the live vectors (a
  reordered or dropped live input cannot misalign a weight), supplies the one
  input the live model lacks, classifies with the candidate's own re-derived
  ceiling (`classify_pick_lr(..., max_p=)`, a new optional parameter whose
  default leaves every existing caller byte-identical), and copies the live
  data-availability PASS reasons (lineup / starter pending, no data) because
  those are about the inputs, not the model. A missing or malformed candidate
  directory, an input the live vector cannot supply, or any exception leaves
  the six fields blank and the live pick untouched. `tests/test_shadow_model.py`
  (13 tests) pins the live classifier with and without `max_p`, that the live
  model objects are not touched, every failure mode, the kill switch, and the
  ledger/Supabase wiring. Suite: **315 passed.** Cost: 0.2 ms per game; the
  form-rate history rebuilds in 0.13 s.
- **`fi_form.py`** (repo root, importable by the predictor, no numpy/pandas):
  a starter's clean-first-inning rate this season, shrunk with K=65 starts of
  prior toward the expanding league rate, from starts strictly before the game
  date. History comes from the two backtest files plus the ledger's graded rows
  -- no external fetch. `python fi_form.py --check` reproduces the research
  columns for THREE configurations (K65_pw0, K65_all, K65_hl15) on 4,005
  pitcher-games with worst disagreement **0.00e+00**; the test suite runs it.
  The candidate's `meta.json` carries the parameters it was fit with and the
  predictor passes them through, so the live input is on the training scale.
- **Ledger + mirror:** six columns appended last to `tracker.FIELDS`, written
  from the predictor's result dict, frozen with the bet alongside the live
  probabilities (so the two models are compared at the same instant), mapped
  in `PICKS_CONVERTERS` and `PICKS_FIELD_MAP`, documented in `db/schema.sql`.
  Supabase `picks_2026` received them via migration
  `picks_2026_add_shadow_model_columns` BEFORE this shipped (verified by query).
- **`tools/shadow_report.py`** + a non-blocking grade-cron step: the paired
  comparison (same nights, same prices) of what each model would have bet,
  three lines per model -- booked (live only), same-rule quarter-Kelly for
  both, flat 1u -- plus each model's No.1 per night and label agreement.
  Writes `data/diagnostics/shadow_report.json`. The shadow's would-be stake is
  computed HERE, not in the money path. A dashboard panel is a follow-up; the
  JSON is the contract.
- `tools/refit2026/refit_fi_form_candidate.py --variant {add,swap10} --config
  NAME [--out DIR]` writes candidate artifacts in the predictor's schema with
  the cal-gate ceiling re-derived per candidate; three alternates are kept
  under `data/candidates/refit2026_fiform_*/` with their own `meta.json`.

### Fixed -- a ledger data trap, found by the equivalence check (T8.41)

- **A rescheduled game carries the same `game_pk` on two dates, and the
  ORIGINAL row is graded with the makeup game's result under starters who
  never threw that first inning.** 13 such games in the 2026 ledger, 31 in the
  2025 backtest file, 35 in 2024 (e.g. PIT@NYY 2026-07-21 lists Will Warren;
  the game was played 07-22 with Max Fried; both rows read "0 runs"). Every
  per-game join in `tools/refit2026` that merges on `game_pk` turned those
  into a cartesian product, and the 09-03 form-rate column had one pitcher's
  value written onto another's game (Kyle Leahy, 05-22). Both form-rate
  builders now keep the LATEST row per (season, game_pk, side); `test_fi_form`
  and `refit_fi_form_candidate` drop the superseded rows from every frame so
  both models are scored on the same clean games. The other harness scripts
  still merge on raw `game_pk` -- logged as T8.41.

### CORRECTED -- the 2026-09-03b verdict

With the duplicates handled, the shrunk form rate's advantage is about a third
of what was reported, and **it no longer clears the selection-aware null.**
Same protocol, clean frames, 300 trials:

| | 09-03 (contaminated join) | 09-04 (clean) |
|---|---|---|
| cells passing all three splits | 12 of 30 | 10 of 30 |
| best 2026 dAUC | +0.0070 | +0.0026 |
| best-in-noise, mean / 90th pct / max | +0.0012 / +0.0023 / +0.0060 | +0.0013 / +0.0030 / +0.0066 |
| survivors per noise trial (vs observed) | 1.4 (vs 12) | 1.2 (vs 10) |
| selection-aware p | 0.000 | **0.110** |

Read plainly: on clean data the search finds a result this size in noise about
one time in nine. The feature is positive in all three splits for ten of
thirty configurations, and ten survivors against a noise mean of 1.2 is still
a notable separation by count -- but the headline number does not pass the
bar this repo holds every candidate to, and the 09-03 "VALIDATED" label is
withdrawn. Also on clean data: the internal control still holds (the
unshrunk reconstruction fails the three splits: +0.0006 / -0.0002 / -0.0004),
the leakage audit is still exact, and the 2026 money is still positive.

**What the shadow loads: `swap10 / K65_pw0`** -- the raw last-10 fraction
REPLACED by its shrunk version (K=65, no prior-season carry, which is what the
persistence measurement predicted), umpire input out, 19 features per half,
ceiling 0.4114. Chosen over the marginally higher-scoring `add` cells because
its fitted weight is negative on BOTH halves (-0.0018 T1, -0.0305 B1 --
cleaner history means fewer runs), where the `add` cells fit the top-of-1st
weight the wrong way round. Clean three-split dAUC +0.0006 / +0.0006 / +0.0020,
2026 90% CI [-0.0005, +0.0045]. Money on clean 2026 at the production gate:
85 bets 68.2%, +17.25u flat / +37.69u Kelly, No.1 69 nights 72.5% +31.45u,
against the live model's 89 bets 64.0%, +10.94u / +42.83u, No.1 68.1% +46.52u
on the same basis -- better flat, worse at Kelly, and none of it outside noise.
**This is a candidate whose backtest is inconclusive, which is exactly what a
shadow is for: live paired rows at zero cost to the product.** Not a model
change; not a recommendation to ship.

No pick, gate, stake, alert or card reads any shadow field. Live pick path
unchanged.

---

## [2026-09-03b] - VALIDATED CANDIDATE: the shrunk first-inning form rate clears every bar (not shipped) -- **CORRECTED 2026-09-04: the join that built this column double-counted rescheduled games; on clean data the null is p=0.110, not 0.000. See [2026-09-04].**

### Added

- **`tools/refit2026/build_fi_form.py`** and **`tools/refit2026/test_fi_form.py`**
  (both write only to `data/candidates/`). The operator asked for the shrunk
  continuous version of the last-10 NRFI rate. Built, tested, and it is the
  first candidate since the pooled first-inning xwOBA (2026-08-21) to clear
  every bar in `feature_test_methodology`.

### The measurement that set the shrinkage

Unlike the umpire rate -- flattened 2026-08-29 because tau^2 <= 0 on every
season pair -- a starter's first-inning clean rate HAS real between-pitcher
variance, and it is stable across all three seasons:

| season | observed var | binomial-noise var | tau^2 | implied K |
|---|---|---|---|---|
| 2024 | 0.01416 | 0.01130 | +0.00286 | 68.2 starts |
| 2025 | 0.01523 | 0.01200 | +0.00323 | 63.4 starts |
| 2026 | 0.01555 | 0.01260 | +0.00295 | 69.7 starts |

**K ~ 65 starts is the whole story.** A starter makes ~30 starts a year, so a
10-start sample deserves 10/(10+65) = **13%** weight on itself and 87% on the
league mean. The shipped `*_p_last10_pitcher_nrfi` gives it **100%**. That is
the defect -- and it is the opposite of "the model under-weights this": the
INPUT is ~87% noise, so the near-zero fitted weight (and its season-flipping
sign, found 2026-09-03) was the model responding correctly. Shrinking the
input is what lets a real weight be carried. Within-season split-half
reliability is +0.37 / +0.16 / +0.22 (Spearman-Brown); cross-season carryover
is weak and inconsistent (+0.08 / +0.21 / -0.14).

### Results

- Reconstruction verified: the `shipped_like` config (K=0, window 10, pooled
  across the season boundary the way `pitcher_last_n_first_inning` does)
  reproduces the live last-10 column at **corr +0.921, mean |diff| 0.025** for
  pitchers with >=12 prior starts in-file. The residual is games outside the
  backtest windows, which production reaches via statsapi and this rebuild
  cannot.
- Granularity: `K65_all` has sd 0.0212 and ~11,000 distinct values against the
  raw fraction's sd 0.18 and **26** distinct values.
- **12 of 30 cells beat shipped on AUC in all three splits.** Best cell
  `add / K65_pw0`: 2026 **+0.0070** (90% CI [+0.0034, +0.0105]), 2024 +0.0039,
  2025 +0.0007; 2026 dBrier **-0.00051**, CI [-0.00096, -0.00009], also
  excluding zero.
- **The internal control:** `shipped_like` -- same rebuild, same code path, no
  shrinkage -- FAILS the three-split test (+0.0004 / -0.0013 / -0.0019). The
  gain is the shrinkage, not the reconstruction.
- **Selection-aware null, 300 trials**, the entire procedure (30 cells x 3
  splits, same all-three-positive filter) re-run on values shuffled within
  season: noise produces **1.4** survivors per trial against the observed
  **12**; best-in-noise mean +0.0012, sd 0.0009, **max +0.0060 across all 300
  trials** -- below the observed +0.0070. **p = 0.000.**
- `prior_w = 0` wins in every variant, which is what the persistence
  measurement predicted rather than a random grid winner.
- **Leakage audit** (`--audit`): 500 rows recomputed by brute force from the
  raw start log, worst disagreement **0.00e+00**; correlation with THIS
  start's outcome (+0.0420) and the NEXT start's (+0.0323) are similar, as
  they must be for a feature that cannot see its own game.

### Money (2026 only, the one season with real captured prices; ceiling re-derived per config)

| config | bets | hit | flat | Kelly | No.1 nights | No.1 hit | No.1 Kelly |
|---|---|---|---|---|---|---|---|
| shipped | 64 | 70.3% | +16.77u | +44.01u | 54 | 74.1% | +42.26u |
| add / K65_pw0 | 93 | 66.7% | +17.56u | +49.08u | 70 | 70.0% | +44.81u |
| add / K65_all | 89 | 69.7% | +21.31u | +56.11u | 69 | 72.5% | +48.70u |

More bets at a similar hit rate, so more total units. **Stated honestly: the
No.1's hit rate is slightly LOWER on a larger set of nights, and per the
refit2026 README the 2026 money LEVEL is flattered by the train/test base-rate
gap.** The discrimination gain and its permutation null are the durable
finding; the units are that finding priced at one season's odds.

### Not shipped

Shipping is a model change and an operator decision. It needs a full refit
(weights + CIR calibrator + the predictor's feature list + the cal-gate
ceiling re-derived) AND a nightly production builder for the feature, the way
`fi_pitcher_pool.py` serves `fi_xwoba` -- the candidate file is a research
artifact built from the backtest CSVs and the ledger, which production cannot
depend on. No model, gate, staking or ledger code touched.

---

## [2026-09-03] - The "Why this pick" panel was printing every driver BACKWARDS; and the recent-form inputs are a real lead

### Fixed -- the driver panel (display only; no pick, gate, stake or ledger value is computed from any of this)

- **The N/Y direction on every row of "Why this pick" was inverted.** Each
  half-inning model predicts the log-odds of a RUN, so a positive
  `contribution` pushes toward YRFI. `_lr_feature_contributions`'s docstring
  claimed the opposite, and `WhyThisPickPanel` faithfully implemented the
  docstring. Live consequence on the 2026-09-02 MIA@KC card the operator
  flagged: a 37.5 C day was labelled as arguing for NO run, and a starter
  with a **perfect last-10 no-run record** was labelled as arguing FOR one.
  Verified against a hand recomputation of `w * (x - mean) / std` on three
  independent rows. Docstring corrected at the source; the panel now derives
  direction from the points figure below. `lib/pick-reasons.ts` (the Brief)
  uses only `Math.abs(contribution)` for ordering and was never affected.
- **Bars were normalised by the largest contribution IN THAT GAME**, so a
  game where nothing had an opinion still rendered a full-length bar. That is
  what made a 0.5-point weather nudge look like the thing driving the pick.
  Bars now use a fixed scale (6.0 points = full track, the 75th percentile of
  "biggest driver in a game" across 2026), so short bars mean the model is
  near a coin flip and two games can be compared.
- **Figures are in percentage points, not log-odds.** A contribution `c` in
  half `h` moves the pick by `-(1 - p_other) * p_h * (1 - p_h) * c`, with each
  half's run probability recovered from the stored lambdas. Checked against a
  full rescore of MIA@KC: estimates the four weather rows at -1.84 points
  where an exact recompute gives -2.03, hence "≈". Falls back to log-odds on
  rows predating the stored half-inning projections.
- **New "No strong drivers" note** when the biggest input moves the pick less
  than 1.0 point (7.1% of 2026 games; the flagged card was in the quietest 3%).
- **`home_fi_xwoba` / `away_fi_xwoba` had no display name or tooltip** and
  printed as the raw column name -- the one input that measures the first
  inning specifically, unlabelled since v3 shipped 2026-08-23.
- Verified on a production build in the browser: all eight rows on the
  2026-09-03 MIA@KC card now read correctly (37.1 C -> Y, last-10 rate 1.000
  -> N, 6.5 IP/start -> N), bars 4-9% wide, quiet note present, figure on one
  line. `tsc --noEmit` and `next build` green.

### Investigated -- "xwoba and last-10 NRFI rates are more important than we think"

`tools/refit2026/underweight_test.py` (new, writes nothing) refits the shipped
v3 shape with a per-feature L2 vector, making the named group 2x/5x/25x freer
or 2x tighter, on all three splits. The hypothesis splits in two:

- **fi_xwoba is already about right.** Its weight is the most stable
  coefficient in the model (+0.0324 / +0.0265 / +0.0307 across splits).
  Freeing it helps 2024 (Brier -0.00139, 90% CI [-0.00227, -0.00050]), is flat
  on 2025, and is slightly worse on 2026 (AUC -0.0018). No change indicated.
- **The last-5/last-10 rates are the live lead.** Freeing them is the best
  single result in the experiment on 2026: AUC 0.5278 -> 0.5319 (+0.0041, 90%
  CI [+0.0004, +0.0078], **excludes zero**), Brier -0.00038, and money better
  on both bases (65 bets @ 69.2%, +14.52u flat / +43.58u Kelly at 2x freer, vs
  54 @ 68.5%, +10.49u / +28.49u shipped). Flat on 2024 and 2025 -- it never
  hurts a split, which is rare here, but clears the bar in only one.
- **Why it is a lead and not a ship:** the fitted weight FLIPS SIGN by season
  (+0.0097 / -0.0089 / +0.0001 shipped; +0.0076 / -0.0318 / -0.0123 when
  freed). Only the negative sign is physically sensible. An unstable sign is
  the shape of every artifact this directory has already killed.
- **Concrete follow-up the test suggests:** these inputs are COARSE -- last-5
  takes 10 distinct values on 2026 with 21-23% of games at exactly 1.000,
  last-10 takes 26. A properly shrunk continuous version, the same
  empirical-Bayes treatment `fi_xwoba` got, has never been built. That is the
  candidate, not a weight edit (which needs a refit regardless, since the
  feature standardisation is frozen into the shipped artifacts).
- Also confirmed en route: tightening these features hurts AUC in ALL THREE
  splits, so the direction of the operator's instinct is right even where the
  magnitude does not clear the bar.

No model, gate, staking or ledger code touched.

---

## [2026-09-02c] - "Temperature and humidity shouldn't matter this much": tested, they don't -- but the PARK term does, and it is the worthless one

### Investigated (read-only; no model, gate, staking or ledger code touched)

Operator flagged a board card (MIA@KC 2026-09-02, LEAN NRFI, seven runs
scored) whose "WHY THIS PICK" panel led with temperature and humidity.

- **What weather actually did to that pick: 2.03 percentage points.** All
  four weather inputs together moved it from NRFI 56.0% to 54.0%; the
  verdict is identical either way. Total absolute push across all 40 feature
  slots was **0.385 log-odds** -- one of the quietest games of the season.
  The pick was LEAN = track-only, so no money was on it.
- **Why the panel looked that way.** It prints the top five |contribution|
  rows per half with **no magnitude floor**, so when nothing has an opinion
  the smallest real signal is rendered at full bar length. 37.5 C is a
  2.7-sigma input and 24% humidity a 2.1-sigma input, so on an extreme
  weather day those rows float to the top by default. Across the v3 era a
  weather input is the single biggest driver in **1 game out of 135** (7.5%
  of games season-wide, where pre-08-23 rows have no fi_xwoba).
- **`tools/refit2026/wx_ablation.py` (new, writes nothing) -- KEEP THE
  WEATHER FEATURES.** Shipped v3 shape refit with weather subsets removed,
  three splits, park rebuilt from train only, CIR on train only, cal-gate
  ceiling re-derived per split, paired bootstrap over games. Dropping all
  four helps 2024 (Brier -0.00117, 90% CI [-0.00245, +0.00011] -- touches
  zero), clearly HURTS 2025 (Brier +0.00161 [+0.00073, +0.00248]; AUC
  -0.0064 [-0.0103, -0.0026], both CIs exclude zero), flat on 2026 (Brier
  -0.00017 [-0.00086, +0.00054]). Helps in one direction only -> reject per
  CLAUDE.md. Every narrower subset behaves the same way ("drop temp only" is
  the best 2025 money cell at +14.18u flat and is worse than shipped on 2026,
  +6.92u vs +10.49u).

### The finding that matters more, from the same decomposition

Share of the model's game-to-game swing, v3 era (135 games, real inputs):

| input | share of the swing |
|---|---|
| **park** | **24.0%** |
| all four weather | 11.1% (temperature 2.1%, humidity 3.2%) |
| first-inning pitcher xwOBA (the v3 feature) | 10.0% |
| the other 15 pitcher/batter inputs | 52.0% |

The single largest driver of which games get picked is the park factor --
and `park_null.py`, re-run the same day, says the shipped park map ranks 2026
games **worse than random relabelling** (beats 4% of placebos on AUC). That
is why the recent STRONG book reads BAL@COL, COL@WSH, CHC@ARI, STL@LAD. The
operator's instinct that the model keys on the wrong thing is correct; the
culprit is the park term, not the weather. It cannot be removed in isolation
(the frozen feature standardisation, 2026-08-20 item 3) -- ablation belongs
to the next approved refit, alongside the umpire feature.

### Trap recorded (bit this session, twice)

Decomposing contributions from the ledger requires production's own defaults.
`harness.DEFAULTS` has no `fi_xwoba` key, so a missing value fills with 0.0
and standardises to z = -13, which reported the feature as 70% (then 100%) of
the model's swing. The pool's `league` entry is `{pa, woba}` running sums, not
a rate -- the neutral fill is the model's own stored `mean`. Same family as
the naive-`attach()` landmine noted in [2026-09-02].

---

## [2026-09-02b] - Follow-ups #2 and #3 from the review: ceiling re-derived (no change), park refit re-run (no change), two validation scripts repaired

### Investigated -- STRONG-YRFI ceiling re-derived on PRODUCTION's own scale (review item 2; nothing shipped)

- The 0.413 ceiling was derived 08-31 from the harness's 24+25 fit. Re-derived
  with the SHIPPED artifacts (`lr_t1/lr_b1.json`, `fi_park_factors.json`,
  `calibration_v2.json`, flat umpire rate) over the train corpus (24+25+2026
  to 08-21, n=6673, matching `train_n`), same rule (87th pctile of calibrated
  p_nrfi among candidates p<0.42): **0.4120** vs shipped 0.413. Per season:
  2024 .4114 / 2025 .4127 / 2026 .4122; share of candidates above 0.413 =
  10.5% on the production scale (the sweep's 10.7-13.1% trim). Live v3-era
  candidates: 16, of which 0.413 and 0.412 each trim the same one (08-25
  HOU@NYY). On the 2026 ledger re-scored through the shipped artifacts
  (in-sample, real prices, May on) the two ceilings differ by ONE bet
  (a loss). Verdict: the ceiling already sits on production's scale --
  **`_LR_STRONG_YRFI_MAX_P` stays 0.413.** The production-vs-backtest gap
  found in the morning review lives in the LEVEL of the top games (park
  file), not in where the ceiling cuts.

### Investigated -- park re-shrink + refit, re-run on today's data (review item 3; nothing shipped)

- `park_shrinkage_refit.py` + `park_null.py` (2026-08-29 protocol, v3 20
  features, L2 0.5, park map rebuilt inside each split from train seasons):
  2026 split shipped K=50 AUC 0.5348 / Brier 0.24898 / Q1-YRFI 57.4%;
  K=150/250/500 move Brier by -0.000005..-0.000011 (CI excludes zero but 20x
  below a real effect, and flat-to-worse on both historical splits, as on
  08-29); flat (league mean) AUC 0.5391, Brier +0.000039 (CI spans zero).
  Null: the real map beats **4%** of shuffled-park placebos on AUC, 28% on
  Q1-YRFI, 88% on Brier; FLAT's AUC gain (+0.0043) is matched by the
  average placebo (+0.0039, p=0.45). Same verdict as 08-29: no shrinkage
  setting reliably helps, `flat` is the search, PRIOR_GAMES stays 50 and the
  feature stays in until the next approved refit (ablation there).

### Fixed

- **Both park validation scripts scored the 2026 split WORSE THAN CHANCE
  today (AUC 0.489, log-loss 0.726) -- an artifact, not a finding.** The
  umpire file was rebuilt FLAT on 08-29, so the cached lookup returns one
  constant for every 2024/2025 training row, while 2026 ledger rows still
  carry the per-umpire values stored before that date. `lr_baseline.LogReg`
  standardises by `std + 1e-9`, so the constant training column turned the
  2026 values into z-scores of ~1e7. Both scripts now hold the umpire
  feature at `LEAGUE_NRFI_RATE` in every block (production's input from now
  on). The 2024->2025 and 2025->2024 splits were unaffected (constant on
  both sides). Any other `two_stage_model.gather` consumer that mixes the
  2024/25 files with the 2026 ledger has the same landmine until the ump
  feature is ablated at the next refit.

No model, gate, staking or ledger code touched.

---

## [2026-09-02] - System review: the hot streak and the crash were the same model at 5x leverage; one monitor repaired

### Investigated (analysis only -- nothing in the model, gates, staking or ledger touched)

Operator asked why the system has been "so terrible lately", why early August
was hot, and whether the probabilities are off ("some games should be worth
more... then they hit when our model said pass"). Measured, in order:

- **Timeline (STRONG bets placed, `tools/pl_calc.py` basis).** Aug 1-13:
  19 bets, 78.9%, +35.6u booked / +7.3u flat. Aug 14-22: 10 bets, 20.0%,
  -33.2u / -6.5u -- SAME 19-feature model, same weights (05-26), same parks
  (COL/ARI/LAA), stakes 5-8u. v3 went live 08-23; since then 9 bets, 2-7,
  -10.7u / -5.5u (4 of them since the 08-31 cal-gate, 0-4). Kelly era
  overall (07-27 on): 52 bets, 51.9% hit against a 63.9% claim and a 56.6%
  break-even; quarter-Kelly at the realised hit rate is 0u. The streaks are
  the staking, not a regime -- see memory `2026-08-21_the_streaks_are_the_staking`.
- **Ranking vs the market, by window (AUC model / market):** flat era
  .511/.530, hot .539/.620, crash .425/.582, v3 .532/.596. The market ranked
  better in every window.
- **"Passes that hit" are not under-rated over the season.** Non-STRONG games
  the market priced >=56% YRFI: 156 games, 51.3% hit (old era); 19 games,
  68% in the v3 era (small n). The over-confidence is on the BETS (claimed
  63%, delivered 50% since 07-15, n=88), not the passes.
- **Live pipeline vs the validated v3 backtest bet DIFFERENT games.** Kelly
  era: 52 live bets, of which the OOS v3 backtest (train 24+25) calls STRONG
  on 14 (57%); the 38 live-only bets hit 50%; the 7 backtest-only games hit
  7-7. v3 era: production's raw p_nrfi sits 0.01-0.03 BELOW an honest
  re-score on every one of the 9 bets (corr 0.87-0.91 across 135 rows), and
  the calibrator's steep first segment turns that into 0.05-0.09
  calibrated. Reproducing production from stored inputs + shipped weights
  gives corr 0.9625 (mean |diff| 0.0035); the largest remaining component is
  the park file: shipped COL .393 / WSH .486 / ARI .437 / NYY .494 vs the
  train-only map .441 / .542 / .475 / .520 (T1 park weight -0.035, so COL
  alone is +0.12 vs +0.07 on the YRFI logit). Recent bets: BAL@COL x3,
  STL@LAD, ATL@WSH, COL@WSH x2, CHC@ARI, HOU@NYY x2 -- a park-and-heat book,
  on a feature `park_null.py` already showed ranks 2026 worse than random
  relabelling (2026-08-29). Not acted on: park rebuild and refit are one
  change (2026-08-20 item 3).
- **Checked and clean:** price capture median 57 min before first pitch
  (the ~2h git lag on 09-01 is GHA commit cadence, not late bets);
  `sizing_prob` == `yrfi_prob` on every bet since 08-14; bet rows frozen at
  bet time; pool `as_of` 2026-09-01; in-sample vs 5-fold out-of-fold CIR on
  v3 moves the tail cap only 65%->64% (not the lever). Drift monitor
  dry-run: 30d Brier .2497 -> .2683, would fire.
- **Rollout-plan triggers (docs/PLAN_2026-08-22 §5):** volume 0.62 STRONG/day
  08-23..30 (below the <60% trigger) -- restored to 2/day by the cal-gate;
  No.1 record n=5 of the 30 the plan requires; claimed-vs-actual overshoot
  >10pp but over ~10 days, not the 3 weeks the plan asks for.

### Fixed

- **`tools/refit2026/no1_since_may26.py` crashed (`KeyError: home_fi_xwoba`)**
  -- the weekly No.1 monitor the rollout plan prescribes. `attach()` merges
  the factor file on game_pk, and the ledger has carried its own
  `home/away_fi_xwoba` since 08-23, so the merge produced `_x/_y` suffixes.
  It now prefers the ledger's own value and falls back to the factor file.
  Also recorded: `data/candidates/factor_fi_pooled.csv` ends 2026-08-22, so
  any harness script still reading it mean-fills every later game
  (`backtest_ship.py` and friends still do). Output today: REAL ledger No.1
  since 05-26 = 83 nights 52-31, +63.48u Kelly / +7.79u flat; August 11-11
  -6.2u; the v3 counterfactual was 9-6 -0.5u in August.

### Noted, not fixed

- `odds_diagnostic.yml` ran green every day 08-23..09-01 but the repo holds
  multi-book/F5 snapshots for only 3 of those days (12 F5 games in total).
  The F5-market AUC comparison promised for "after ~2 weeks" cannot be run
  yet; check why the daily runs commit nothing.

---

## [2026-08-31b] - Cal-gate SHIPPED: calibrated STRONG-YRFI ceiling replaces the lambda floor + weather bump

### Changed

- **`classify_pick_lr` STRONG-YRFI demotion is now a calibrated-probability
  ceiling: STRONG requires `p_nrfi < _LR_STRONG_YRFI_MAX_P` (0.413); the rest
  of the `p < 0.44` band tracks as LEAN YRFI.** The raw-lambda floor + its
  ±0.02 weather bump no longer touch the STRONG side ("PASS - Low lambda"
  stops appearing on new YRFI rows); the weather-adjusted floor survives only
  as the track-only LEAN-band gate (0.44 < p < 0.50), unchanged. Operator
  approved option 1 from the [2026-08-31] sweep below. Derivation: 87th pctile
  of calibrated p_nrfi among train-corpus (24+25) STRONG candidates via the v3
  OOS pipeline — the ~13% trim the floor was originally designed to make, now
  on a scale that cannot silently move at a refit (trims 10.7%/11.1%/13.1%
  across the three splits vs the raw floor's 0%/~50%/13%). As-shipped OOS at
  fixed 0.413: 2025 88 bets 56.8% +6.64u (shipped gate: −2.04u), 2026 85 bets
  71.8% +23.98u, No.1 51-19 (shipped: +21.32u, 41-13); 2024 dissents −9.07u on
  fake −112 odds (same dissent the 08-25 rescale shipped over). Re-derive the
  ceiling at every refit (one quantile query); a stale value degrades
  gracefully because the calibrated scale holds its meaning.
- **`dashboard/lib/classify.ts` tentative mirror updated in the same commit**
  (CLAUDE.md change-one-change-both rule): uses `strongYrfiMaxP` from
  `thresholds.json` when present, falls back to the pre-cal-gate path (floor +
  `strongYrfiP`) for an older cached JSON; predictor exports the new field.
  Stale fallback default `lambdaYrfiFloor: 0.838` refreshed to 0.75 while
  there. `db/variants.py::_classify` deliberately keeps the parameterized
  old rule — it exists to replay variants, not to mirror production.
- Verification: 14-case classifier check (all three heat-bump victims now
  STRONG; ceiling boundary exact at 0.413; LEAN band + NRFI paths unchanged),
  301 money-path tests green, dashboard prod build green (units guard +
  typecheck). Backup: tag `pre-cal-gate-2026-08-31`.

---

## [2026-08-31] - Weather bump on the YRFI lambda floor: swept every option, shipped nothing yet

### Added

- **`tools/refit2026/floor_wx_sweep.py`** — three-split OOS sweep of the
  STRONG-YRFI lambda-floor family after the operator reported (third night
  running) the No.1 card landing on "PASS - Low lambda" and then hitting.
  Diagnosis: the 2026-08-25 rescale (0.838 → 0.75) fixed the BASE only;
  `_weather_adjusted_floor` still adds a fixed +0.02 in hot (≥28°C) games, and
  on the v3 scale (STRONG-candidate median lambda 0.767) the 0.77 hot bar sits
  ABOVE the median — in August that culls most top candidates. Live 08-26..30:
  6 of 10 STRONG-YRFI candidates demoted, 4 only by the heat bump (those 4
  went 2-2; the band is thin, which is why the sweep exists).
  Configs: shipped (.75+wx.02), flat .75, wx±.01, wx±.005, old .838, no floor,
  calibrated-ceiling gate (87th train-candidate pctile, the option-2 durable
  fix deferred 08-25), plus a 44-cell grid; day bootstraps; near-floor
  hot-vs-not permutation; selection-aware placebo null (weather packets
  permuted, whole grid re-run, 300 trials, all three splits).

### Findings (validation only — gate unchanged pending operator decision)

- **The bump carries no weather-specific signal in any split.** Near-floor
  band (λ∈[0.75,0.77)) hot-vs-not permutation: 2024 p=0.155, 2025 p=0.971
  (hot hit MORE — premise inverted), 2026 p=0.482. Placebo-bump null on
  flat075−shipped: 2026 obs +4.66u vs placebo mean +4.05u (p=0.373 — the real
  bump is exactly as costly as a RANDOM bump); 2025 obs +2.36u, worse than 88%
  of placebos; 2024 obs −9.96u, better than 92% of placebos (p=0.077) — but
  even placebo bumps helped 2024 (mean −3.60u) because that pool hit 51.6%,
  below the −112 break-even, so ANY volume cut made fake-odds money there.
- **flat075** (remove bump): 2026 +4.51u vs shipped (90% CI [−1.43,+10.36],
  P(better)=90%), identical 73.5% hit on 15 more bets; 2025 +2.34u (P=84%);
  2024 −9.82u (P=4% — the fake-odds dissent, same shape the 08-25 rescale
  shipped over). Grid argmax on 2026 is exactly base .75 / wx 0.
- **cal_gate** (replace floor+bump with calibrated p_nrfi ceiling at the 87th
  train-candidate pctile): the only mechanism whose trim rate is stable across
  seasons — 12.6%/21%/13% vs the raw floor's 0%/50%/13% (the λ scale itself
  wobbles: split medians 0.782/0.747/0.767, so ANY fixed λ cut changes meaning
  every year, L2 changes aside). 2025 +7.30u vs shipped (P=91%) and the only
  significant No.1 delta anywhere (+14.0pp hit, 90% CI [+3.5,+24.6]);
  2026 +2.61u (P=73%); 2024 −9.80u. Jaccard vs flat075 on 2026: 0.976.
- **Refuted:** old 0.838 fires 0 bets on 2025 and 2 on 2026 (−19.4u vs
  shipped); wx±.01/±.005 dominated by flat075 on 2025+2026; no-floor loses
  the genuinely bad tail (2026 flat075 cut bucket hit 42.1%, so a floor at
  SOME level still earns its keep).
- Ledger quirk found en route: model columns (λ, probs) keep updating after a
  pick's label freezes, so final row values can drift from decision-time
  values (e.g. 08-25 HOU@NYY STRONG at final λ 0.7395 < any floor). Labels in
  `pick_changes.csv` are the decision-time truth.

### Deferred

- The gate change itself (flat075 vs cal_gate vs keep) — operator decision
  pending; both live options restore the heat-bump victims identically on the
  08-26..30 board.

### Fixed

- **`data/umpire_rates.json` was built from the banned 2022/2023 seasons and
  ranked 2026 umpires backwards; rebuilt from 2025+2026 with TOTAL shrinkage.**
  The old file's own `training_corpus` declared the 2022/23 backtests — the
  exact seasons CLAUDE.md forbids for training. Its tercile of most
  NRFI-friendly umps produced 47.3% NRFI in 2026 against 50.5% for its most
  YRFI-friendly tercile (r = −0.031, n=1,752), its league level (0.5084) was
  a 2022-23 number, and `tools/test_umpire_persistence.py` (2026-07-27) had
  already concluded "NO PERSISTENCE DETECTED … ABLATE".
  The decisive new measurement: **cross-season covariance of per-ump
  first-inning NRFI rate is negative on every permitted pair** (2024v2025
  cov −0.0014 / r −0.18; 2025v2026 −0.0003 / −0.03; 2024v2026 −0.0007 /
  −0.07; umps with ≥15 games both years). τ² ≤ 0 ⇒ the empirical-Bayes
  shrinkage is **total**: `rebuild_umpire_rates.py` writes every ump's
  `shrunk_nrfi` = the 2025+2026 league rate (0.4963; 2024 dropped for its
  53.5% anomaly, same precedent as the park rebuild), keeps raw per-ump
  counts for audit, refuses 2022/2023 inputs in code, and is idempotent.
  Both consumers verified serving the flat value (predictor
  `fetch_umpire_rate` and trainer `_ump_rate_for`); `umpire_rates_split.json`
  has **zero consumers** (dead data, left in place).
- **Why flatten instead of dropping the feature:** ablation is a 19-feature
  refit + predictor loader change + calibrator refit — and the same-day park
  experiment showed 2026 refit "gains" are luck-dominated. Flattening the
  input neutralizes the feature through the frozen weights (|w| 0.0054/0.0062,
  ranks 13 and 18 of 20) with no architecture change. Ablation at the next
  approved refit remains the right end state.
- **Impact, measured before shipping** (frozen weights + shipped CIR
  calibrator; one pre-v3 legacy row with non-probability stored halves
  excluded): `lambda_lr_total` mean −0.0020, sd 0.0047, max |d| 0.019;
  **tonight's board: zero verdict changes**; going forward ~14 λ-floor and
  ~20 p-cut rows per ~1,650 games (~2%) sit close enough to a gate to land
  differently. Graded-2026 old-vs-flat: Brier +0.00007 (placebo p = 0.475),
  AUC +0.0045 — **bracketed by the placebo** (shuffled ump values average
  +0.0034, p = 0.705), because the old values ranked worse than noise. No
  performance claim either way; this ships as input correctness, like the
  morning's venue fix.
- **`tools/test_umpire_persistence.py` exits cleanly on a flat file** instead
  of crashing in its bootstrap (zero variance → empty array). It prints that
  its question was answered and the fix shipped; it becomes meaningful again
  only if per-ump spread is ever reintroduced.
  Backup: `data/umpire_rates.json.bak-2026-08-29-pre-ump-fix`.

---

## [2026-08-29] - Park factors rebuilt (TB was a different stadium); a rotted regression guard

### Fixed

- **`data/fi_park_factors.json` was 3+ months stale, and Tampa Bay's value
  described a building the Rays no longer play in.** The file was last rebuilt
  2026-05-19, so its 2025 half put TB at **George M. Steinbrenner Field**, the
  open-air park used while Tropicana Field was repaired after Hurricane Milton.
  They returned to the rebuilt Trop for 2026 and the first-inning rates differ
  by 13 points (2025 @ Steinbrenner 30/77 = 39.0% NRFI; 2026 @ Tropicana
  35/67 = 52.2%). The blend produced 43.8%, and since `fi_park_nrfi_rate` is the
  largest single weight in both half-inning models (T1 −0.0354, B1 −0.0331 on
  standardized inputs) it does not nudge a probability, it flips the side: it
  was the top factor behind a **58% YRFI** read on SD@TB on 2026-08-29 that a
  park-corrected independent estimate put at **56% NRFI**.
  `rebuild_park_factors.py` grew a `VENUE_CHANGED_SINCE_2025` set (currently
  `{"TB"}`) whose members use 2026 data only; the league base rate still counts
  every graded game. Oakland is deliberately **not** listed — the A's played
  Sutter Health Park in both seasons, so their two years are the same building.
  TB 43.8% → 51.1% (+7.3pp); other movers TEX −5.1, CHC −4.7, KC −4.2, BOS −4.1,
  NYY +4.1, MIA +3.3, MIL −3.2, COL −3.1.
  Impact measured before shipping: **mean change in `lambda_lr_total` ≈ 0.000**
  season-wide (1646 games), so unlike the v3 L2 change (see 2026-08-25) this
  does **not** re-scale the lambda distribution under the 0.75 YRFI floor — it
  moves individual parks. On today's board no pick verdict flips; SD@TB moves
  furthest (0.7464 → 0.7134) and toward NRFI, the direction the independent
  estimate already favored.

- **`tests/test_weather_sticky.py` — the regression guard for the 2026-08-23
  frozen-row incident — had been failing for 3 days and nobody could see it.**
  Four of its nine tests pinned `DATE = "2026-08-23"`, the incident's own date.
  `_wx_cache_save()` prunes keys older than 3 days and prunes the **in-memory**
  dict, not just the file, so the entry was evicted by the same call that wrote
  it. The tests passed for three days and then failed permanently from
  2026-08-26. `DATE` is now `date.today().isoformat()` with a comment saying why
  it must stay relative. The behaviour under test was never date-specific.
  Suite goes 297 passed / 4 failed → **301 passed**.

### Added

- **Nightly run for `tests.yml`** (`schedule: "12 9 * * *"`). The workflow has
  `paths-ignore: data/**`, and the automation pushes ~30 data-only commits a
  day, so a stretch with no code change runs nothing at all: the last code push
  before this was 2026-08-25 and the weather tests broke on 2026-08-26, one day
  later. There was no push to blame and no run to go red. A ~20s suite once a
  day bounds that blind spot to 24 hours.

### Tested and REJECTED — the park-shrinkage retrain (operator-approved, same day)

- **Refit the model at four park-shrinkage settings, validated out of sample,
  and shipped nothing.** `tools/refit2026/park_shrinkage_refit.py` rebuilds the
  park map inside each split from **training seasons only** (never from
  `data/fi_park_factors.json`, which scores +0.690 in-sample against −0.057
  out) and refits the shipped 20-feature v3 set at L2 0.5 across all three
  splits. Coverage printed first: 28–30 distinct park values per split, so
  nothing collapsed to a constant.
  - K=150/250/500 move 2026 Brier by −0.000006…−0.000012 with bootstrap CIs
    excluding zero — but that is ~20× smaller than a typical candidate effect
    on this model and it **does not replicate**: both historical splits come
    back flat-to-worse. Fails CLAUDE.md's "reject any feature that helps in
    only one direction".
  - `flat` (every park = league mean) looked genuinely good on the deciding
    2026 split: **AUC 0.5387 → 0.5434, Q1-YRFI hit 56.7% → 59.0% (+2.3pp on
    the actual bet population)**.
  - **The selection-aware null killed it** (`tools/refit2026/park_null.py`,
    200 trials). Permuting *which rate belongs to which park* — same values,
    same spread, only the pairing destroyed — shows the shipped map ranks 2026
    **worse than random relabelling**: it beats just **4%** of placebos on AUC
    and **2%** on Q1-YRFI. So almost any change to this feature improves 2026,
    and `flat`'s gain is ordinary: **p = 0.425 (AUC), p = 0.265 (Q1-YRFI)**.
  - **Verdict: `PRIOR_GAMES` stays 50, the feature stays in, no weights
    changed.** `data/lr_t1.json` / `lr_b1.json` / `calibration_v2.json` are
    byte-identical; the live model is still `refit2026_fixwoba`, L2 0.5,
    train_n 6673. The 4th-percentile draw is bad luck, not a reversal —
    year-over-year park correlation is +0.13 (positive, just tiny) and `flat`
    is *worse* on both historical splits. Changing it would fit one season's
    luck. Third time this shape has appeared here; see
    `2026-08-03_gate_sweep_artifact`.
  - This does **not** retract the venue fix above. "Tampa Bay's value was
    computed from the wrong building" is a data-correctness repair; "the
    feature barely predicts" is a separate finding. Both are true.

### Deferred — superseded by the test above

- ~~**`PRIOR_GAMES = 50` is measurably too low**~~ — raising it was
  operator-approved, tested the same day, and **rejected**; see above. The
  raw-rate evidence below is still accurate, it just does not survive contact
  with a refit and a search-aware null. Kept because the measurements are the
  reason the test was worth running, and because they are the right numbers to
  quote when someone next proposes tuning this feature.
  Park first-inning NRFI rate is mostly noise: year-over-year correlation
  2025 vs 2026 across 30 parks is
  **r = +0.13** (r² = 0.017), and the observed 2026 spread (sd 7.4pp) is barely
  above the 6.2pp expected from coin-flip noise at ~66 games/park, implying a
  true park sd of only ~4.1pp. Two out-of-sample tests: **2025 → 2026** picks a
  best prior of ~250–500 and rates prior-50 *worse than giving every park the
  league mean*; **2026 H1 → H2** picks ≥1000, i.e. the flat mean, with every
  smaller prior losing to flat. The catch is that the live LR weights were fit
  against factors with sd 4.07pp; prior 250 compresses that to 1.79pp and so
  shrinks the feature's real contribution by more than half — a de facto weight
  change with no refit. That is exactly why the refit above was run rather than
  simply raising the number, and the refit is what settled it. The reasoning
  stays in `rebuild_park_factors.py`'s docstring so the next reader does not
  "fix" `PRIOR_GAMES` casually.

---

## [2026-08-25b] - Data-quality badge no longer calls an announced rookie "TBD"

### Fixed

- **`DataQualityBadge` tooltip conflated "pitcher not announced" with "pitcher
  announced but thin MLB history"** (`dashboard/components/BoardRow.tsx`). Both
  cases print when `pitcher.quality === 'avg'`, and the copy always said
  "pitcher: TBD / league-avg fallback". Operator report on 2026-08-25 COL@WSH:
  Mason Adams was announced (career MLB IP: 0.1 — one out) and correctly hit
  the league-avg fallback + `PASS - No data`, but the badge's "TBD" made it look
  like the probable-pitcher fetch had failed. The predictor already makes this
  exact distinction (STARTER PENDING vs NO DATA, T2.24), and `noDataReason` in
  the same file already words it correctly; the badge now mirrors them: an
  announced pitcher renders "announced, but too little MLB history — league-avg
  fallback" with the name, an unannounced one renders "not announced yet (TBD)".
  Model behavior unchanged — this was presentation copy only.

---

## [2026-08-25] - The STRONG-YRFI lambda floor was stale under v3: rescaled 0.838 -> 0.75

Operator (again): the night's No.1 pick kept showing **PASS · LOW λ** and then
the game had a first-inning run. Confirmed real and root-caused to a gate that
v3 quietly broke — not variance.

### The diagnosis

`_LR_LAMBDA_YRFI_FLOOR` is the minimum projected first-inning runs
(`lambda_lr_total`) a STRONG YRFI bet must clear. It was set to 0.838 for the
pre-v3 model. v3 (L2 0.05 -> 0.50, shipped 2026-08-23) compresses raw output,
dropping `lambda_lr_total` ~0.11 across the board. So a fixed floor silently
changed meaning: on 2026 out-of-sample the STRONG-YRFI **median lambda fell
0.876 -> 0.767**, and the fraction of STRONG YRFI bets clearing 0.838 fell from
**87% (a ~13% trim) to 2.5% (a ~97% cull)**. It was demoting **85 of 88** nightly
No.1 YRFI picks; those 85, if bet, went **62-23 (72.9%), +21.96u** on real 2026
prices. This is the direct cause of the recurring "why is my #1 a pass" complaint
since 08-23.

### Validation (out of sample, v3, `tools/refit2026/backtest_ship.py` pipeline)

Three-split re-score, train on the other seasons, CIR calibrator on train only.
STRONG-YRFI kept P&L by floor: 2024 (flat -112, no real odds), 2025 (flat -112),
2026 (real captured prices). 0.75 = ~13th percentile of v3's STRONG lambda, which
restores the ORIGINAL ~13% trim on the new scale — the same mechanical-rescale
operation as the earlier 0.78 -> 0.838 step.

| floor | 2024 | 2025 | 2026 (real) | #1 picks restored (of 85) |
|---|---|---|---|---|
| 0.838 (old) | +8.2u | +0.0u | +2.3u | 0 |
| **0.75 (new)** | -12.6u | +0.3u | **+30.8u** | **77 (58-19)** |

Honest caveat: 2024 prefers a higher floor, but only on *assumed* -112 odds (no
real 2024 first-inning prices exist); the 2026 result is on real money. 0.80 was
the only all-splits-positive value but restored just 22 of 85 No.1 picks — operator
chose 0.75 to actually fix the product surface. The deeper structural fix (gate on
calibrated Yes% instead of raw lambda, so it can't break on the next refit) is
deferred.

### Changed

- `mlb_first_inning_predictor.py`: `_LR_LAMBDA_YRFI_FLOOR` 0.838 -> 0.75, with a
  comment documenting the v3 scale shift and the OOS validation. Single source of
  truth (all tools read it by reference; the dashboard "LOW LAMBDA" code is
  display-only), so no mirror to sync.

### Deferred

- Structural replacement of the raw-lambda floor with a calibrated-probability
  gate (scale-stable across L2/refit changes). See memory
  `2026-08-25_lambda_floor_stale_v3`.

Tests: 301/301 money-path pytest pass; fixture parity 21,402 + 121 cases match.
Backup/revert: tag `pre-floor-change-2026-08-25`.

---

## [2026-08-23] - A lost weather fetch froze a wrong probability: sticky weather, and a detector for host disagreement

Operator: *"tell me why the #1 pick today went from 60% to 65% after the game
was already over?"* -> *"we need to fully diagnose this asap"* -> *"implement
the fixes"*. Nothing was recomputed after the game; the number that moved was
which HOST's frozen copy the dashboard happened to be serving.

### The diagnosis (CLE@COL, 2026-08-23)

`_fetch_open_meteo_forecast` returned None on one Railway cycle and
`fetch_game_weather` silently substituted the neutral defaults, so the game
was scored as a 20 C calm day instead of Denver's real 32 C afternoon. That
cycle was the last one before first pitch, so `_pick_is_locked` froze it:
Railway's copy held 60.0%, the GHA copy (and the committed ledger) held
65.2%, and Supabase — last-writer-wins, with Railway writing 12x more often —
served 60.0% for four hours. It "changed" at 7:44 PM only because pasting the
Odds API key redeployed Railway, which reloads its CSV from git.

**Proof, not inference:** rebuilding the game from the row's own recorded
features gives raw 0.4450 -> **0.3483** with the real weather and raw 0.4682
-> **0.4006** with the defaults, against the **0.4003** Supabase was serving.
No other input loss (lineups, umpire, fi_xwoba) lands within four points. The
trigger is visible too: a fresh container started 18:52Z, 18 minutes before
the 19:10Z first pitch, and `data/cache/` is gitignored — so it had to
re-fetch every park at once, with `past_days=92` (~2,200 hourly rows) per
call. Railway's logs for that window are purged (deployment REMOVED).

**A number I got wrong and retracted mid-diagnosis:** I first reported "29% of
August's outdoor rows have blank weather". All 90 blank rows are DOMES
(`wx_is_dome = 1.0`; the filter compared against `'1'`). A failed fetch is not
blank — it writes the defaults as ordinary numbers. Real rate, by the exact
20.0/10.0/60.0 signature: **10 of 798 outdoor rows since June 1 (1.25%), none
since July 29.** Rare and silent, not chronic.

### Fixed

- **Sticky weather** (`mlb_first_inning_predictor.py`): fresh-cache -> live ->
  **last good reading for that game, at any age** -> defaults. Defaults are
  now the last resort instead of the first, so a fetch blip can no longer move
  the model's output at all. `data/cache/weather_live.json` per (park, ET
  date); every reading carries a `source`.
- **Cold containers warm from the ledger.** `data/cache/` is gitignored, so a
  just-redeployed container knew nothing — the incident's exact state. The
  cache now seeds from the committed ledger (skipping rows that were
  themselves scored on defaults), stamped as UNKNOWN age so it is a parachute
  and never suppresses a live fetch.
- **Cheaper cadence:** `past_days` is what the target date needs (1 for
  today, was a flat 92) and `WX_CACHE_TTL_MIN` (25) turns 12 cycles/hour into
  ~2 requests/hour per park. That is the throttling exposure that produced
  the refusal in the first place.
- **Visible:** the run footer prints `Weather inputs: N live, N cached, N
  stale, N DEFAULT, N dome`, mirrored to Supabase `system_status`
  (`weather_health`) via a new shared writer
  `db.supabase_writer.upsert_system_status`. The Ops Health card shows a red
  **"weather N default"** chip (amber when a reading was merely reused) and
  the status escalates to warn with the parks named.
- **reconcile I6 — frozen-row divergence, REPORT-ONLY** (same reasoning as
  I5): compares this host's CSV against Supabase for frozen rows, writes
  `system_status.frozen_divergence`, and the card shows a "frozen split"
  chip. It never heals — picking a winner between two frozen copies is
  arbitrary, and rewriting a frozen row is the write T2.23 refuses.
  **Its first live run immediately found six more**, all from 2026-08-22
  (deltas 0.001–0.014) — so the mechanism recurs at low amplitude and
  CLE@COL's 0.052 was the extreme, not a freak. That also set the design:
  two thresholds, 0.001 to RECORD and 0.02 to ALERT. A chip that is amber
  every night is a chip nobody reads, so the noise floor is counted
  (`minor`) and summarised in one line, never alerted.
- **Unchanged on purpose:** the 19:00 ET weather slot (game-hour weather was
  tested and rejected the same day), and every past row.

Tests: `tests/test_weather_sticky.py` (+9, including the incident itself as a
regression guard) and `tests/test_reconcile_frozen_divergence.py` (+7). 300 pass.

**Left open deliberately:** which host should WIN a frozen disagreement.
There is no authority to appeal to — Railway never pulls git mid-life, so the
committed ledger cannot be consulted live. Detector first; revisit if I6 ever
fires.

---

## [2026-08-23] - Weather at first pitch, wind direction round 4: built, tested, NOT shipped

Operator: *"make sure all [factors] are focused on first inning stats only ...
what about only focusing on the weather forecast for the first 10 minutes from
game time ... wind direction ... could potentially help carry a ball."* All
three answered the same evening: `tools/refit2026/wx_gamehour.py`
(--build/--test), `wx_wind_null.py`, factor file
`data/candidates/factor_wx_gamehour.csv` (4,867 outdoor games 2024-26 with
weather at the true first-pitch hour; MLB schedule API x open-meteo archive;
coverage 100/100/99.1%).

### Found on the way

**The shipped model never used the game hour.** Training
(`backtest.fetch_weather_season`) and live (`_fetch_open_meteo_forecast`)
both take the 19:00 America/New_York slot for EVERY game -- a 1:35 PM start
gets weather from five hours after its first inning. Train == serve (no
skew), but the input is a proxy; the true game-hour input differs by
|dT| mean 1.3-2.1 C, p90 ~4 C.

### Tested, and the verdicts

- **Game-hour weather swap** (wx_temp_c / wx_wind_kmh / wx_humidity on the
  live 20-feature set, L2 0.5): 2024->2025 **-1.92 [-3.04,-0.90]** x1000
  logloss (worse, CI clear of zero), 2025->2024 +2.59, 24+25->2026 -0.31;
  No.1 sim .736 -> .733. Helps in one direction only -> **rejected** by the
  standing methodology. (Hourly is the practical "first 10 minutes": the
  inning lasts ~20 min and within-hour drift is far below forecast error.)
- **Wind direction as a feature, at game-hour** -- the retest
  `wind_direction_dead` explicitly allowed. Closest result in four rounds:
  out-to-CF component ALL+ across the splits (+0.246/+0.067/+0.132) with the
  crosswind placebo negative in all three; within-park permutation null:
  P(mean >= obs) = 0.017 but P(ALL+ pattern by chance) = 0.092; and the
  decisive product check: **No.1 hit .736 -> .694** (the same failure that
  rejected fi_k in the v3 sprint). **Not shipped.** Season-end re-score rule
  recorded in the memory `2026-08-23_wx_gamehour`.

### The first-inning audit (operator's first question)

7 of the 20 live features are first-inning-specific (park/ump/pitcher-last-5
/pitcher-last-10/pitcher-vs-team first-inning rates + pooled fi_xwoba), the
batter side uses only the top-3 hitters by construction, and first-inning
versions of the season-wide remainder (fi_k/fi_bb/fi_csw/fi_velo/fi_fstrike/
fi_zone, batter pooled, team FI propensity, FI runs allowed) were built and
tested in the v3 sprint -- only fi_xwoba survived; fi_k passed accuracy but
dropped the No.1 and was rejected. Nothing in the model is a whole-game stat
because nobody checked; the season-wide ones are there because their
first-inning versions lost.

---

## [2026-08-23] - Odds credits: one price per game at the lock, every book in the same call (~315/day -> ~75/day)

Operator: *"we need to plan a better way to save on credits ... for the number
one pick we need to get the accurate odds at the time that the pick is locking
... for everything else let's come up with a plan"* -> *"go with what you feel
is best."*

### The measurement that changed the design

The lock-time fetch was built for DraftKings, which posted the first-inning
line a median 63 min before first pitch -- so a 5-minute poll from T-120 paid
only once DK quoted (~3 credits/game). **FanDuel posts by the morning.** On
every priced row of Aug 20-22, `opened_captured_at` sits at T-114..119, i.e.
the price was already up when the window opened; the 1 PM snapshot on Aug 23
showed all 15 games quoted by 4-5 books ten hours before the late games. Under
`120:55` every one of the ~13 cycles per game therefore cost a credit:
**~195/day** -- which is exactly how the free key died in 2.5 days -- plus the
4x/day snapshots at ~120/day. Status quo ≈ 315/day ≈ 9,500/month.

### Changed

- **Window `120:55` -> `65:50`** (Railway `ODDS_API_WINDOWS`; code default
  updated): ~3 fetches per game, the last one being the lock cycle. The bet
  still locks on the first cycle at/after T-60 with a price fetched in that
  same cycle, so the №1's ledger price -- and the edge-vs-market check -- are
  exactly as accurate as before at ~1/4 the cost. ~45/day for a full slate.
  Set at 2:20 PM ET, before the evening windows opened.
- **One call, every book** -- `tools/fetch_odds_api.py --regions us
  --ledger-book fanduel`: The Odds API charges *markets x regions* and "us" is
  one region however many books answer, so asking for every US book costs
  exactly what FanDuel-alone cost. FanDuel still goes to the ledger file (the
  one-book rule is intact -- `--book`/`--ledger-book` are mutually exclusive,
  the local filter still applies); every book's quote is appended to
  `data/diagnostics/odds/lock_<date>.csv` (`--raw-append`) and mirrored into
  Supabase `odds_multibook` by the loop (`odds-multibook` step, display only).
  **The board's "best price" and the hero's "best price" are now the best
  price AT THE LOCK** -- the moment the BET LOCKED alert fires -- not a 1 PM
  snapshot. +0 credits. `workers/predictor_loop.py step_fetch_odds_api`.
- **Snapshots 4x/day -> 1x/day at 1 PM ET** (`odds_diagnostic.yml`): keeps
  the morning view of all books and the F5 lines for the research questions;
  freshness at the lock now comes from the at-lock call. 120 -> ~30/day.
- **Credit balance on the Ops Health card.** `fetch_odds_api.py` writes the
  balance the API reports into Supabase `system_status`
  (`odds_api_credits:<host>`, migration `system_status_table`, anon read);
  `/api/health-live` returns it; the card shows "odds credits 19,790" (amber
  under 2,000, red under 100 on Railway, which also sets status warn/degraded
  with a plain reason). Per HOST, because Railway and GitHub Actions held
  different keys today -- when they disagree by more than a few hundred the
  chip says "Railway 27 · GHA 19,790" until they hold the same key.
- **The odds file carries each row's own capture time** (`captured_at_utc`,
  last column) and `tracker.import_odds` uses it as the stamp when present.
  Fixes a display defect: the `--merge`d file re-imported preserved rows every
  cycle and the importer stamped them with the import time, so an unlocked
  game read "captured just now" all evening although its price was from 1 PM.
  Locked rows were never affected (T2.23 freezes them first).
  `tests/test_odds_capture_stamp.py` (+7). 284 tests pass.

### Budget after

~45/day at the lock + ~30/day snapshot ≈ **75/day ≈ 2,300/month, 11% of the
20,000 plan** (was ~315/day), with the №1's best-available price more accurate
than before. Floors: Railway `ODDS_API_MIN_CREDITS=1` (set today to spend the
old key's last credits; the money path is now cadence-bounded so the floor is
moot), snapshot `--min-credits 2000` (the reserve line).

### Still on the operator

Paste the 20K key into Railway (see the previous section). The card's credit
chip will read "Railway 27" -- and the status amber -- until it is done; that
is the chip doing its job.

---

## [2026-08-23] - Ops: the odds-key mix-up, a reconcile notice storm, a red tests badge, and "clear the errors"

Operator: *"there's multiple errors in the dashboard some of which are relating
to credits from the odds API. I have now upped my membership to the $30 a month
with 20,000 credits a month. go through each error... then clear the errors."*
Four distinct things were behind the red badge; all four are dealt with.

### Found

1. **`odds-api` x 91 (the errors).** Every 5-minute Railway cycle since
   2026-08-22 17:07Z logged *"refusing to start: would leave 47 credits, below the
   --min-credits floor of 50"*. The guard did its job: the key on **Railway** is
   the OLD free-tier key (500 credits/month -- the 2026-08-11 FanDuel-at-lock
   loop at ~50/day ran it down in 11 days, exactly as `fetch_odds_api.py`'s own
   header predicted). The key the operator added to **GitHub Actions** the same
   morning is a DIFFERENT key on the 20,000-credit plan (`used 0, remaining
   20000` on its first run at 15:57Z, while Railway's key read `used 450,
   remaining 50` at 17:47Z). The Odds API keeps the key on an in-place upgrade,
   so the 20K key is a second key -- and Railway never got it.
   **Consequence:** no lock-time FanDuel price was captured from 08-22 ~1 PM ET
   until 08-23 2 PM ET (2 of 15 games priced on 08-22, 0 of 15 on 08-23 until
   the fix). No bet was affected -- neither night produced a STRONG pick -- but
   CLV/opened-price tracking was blind for both slates.
2. **`reconcile-heal` x 326 (notices, not errors).** `tools/reconcile.py`
   invariants I3/I4 expect a `strong_locked` / `strong_graded` notification for
   EVERY placed STRONG bet, but since the №1-only policy (2026-08-05) the
   notifiers fire only for the night's top pick and return early with no dedup
   row. On the 08-21 slate (two STRONG bets) the runner-up was therefore
   "healed" every 5 minutes for 36 hours (`NOTIFY_FRESHNESS_HOURS`), 2026-08-22
   00:00Z -> 2026-08-23 13:40Z. Nothing was changed by those heals.
3. **`tests` workflow red on the last three pushes.** `shortBook()` in
   `BoardRow.tsx` gained RIV/BOL/BOV/BUS/MYB for line shopping (commit 58eef763)
   and `tracker._book_label` did not -- `test_the_python_and_typescript_rules_agree`
   caught the split exactly as designed.
4. **`MODEL_UPDATED_FROM` was off by one day.** The ship commit was pushed
   2026-08-23 10:18 ET; the 08-22 slate was picked by the previous weights.
   The `/history` sub-total said "Since the Aug 22 model update". No figure was
   affected (no No.1 bet on 08-22), the label was wrong.

### Fixed

- **Railway floor lowered for the old key's last credits** -- `ODDS_API_MIN_CREDITS=5`
  (service variable; was 50). The next cycle priced the two games in window
  ("wrote 2 rows across 1 books ... credits remaining: 48"). The real fix is the
  operator pasting the 20K key into the Railway service variable `ODDS_API_KEY`
  (click-by-click given in chat); until then the old key's ~45 credits cover
  roughly one evening slate.
- **The diagnostic capture can never starve the money path** --
  `.github/workflows/odds_diagnostic.yml` now passes `--min-credits 2000`: the
  4x/day multi-book snapshot refuses to spend once fewer than 2,000 credits
  would remain, leaving the lock-time fetch (~50/day) 40 days of headroom in
  the worst case. Budget on the 20K plan: ~120/day diagnostic + ~50/day money
  path = ~5,100/month.
- **`tracker._book_label`** mirrors `shortBook()` again (RIV/BOL/BOV/BUS/MYB);
  the "unknown book is named in full" test now uses Circa Sports, which neither
  side abbreviates. 277 tests pass.
- **`tools/reconcile.py` I3/I4 honour the №1-only policy** -- new
  `_is_nights_top_pick(row, same_night_rows)` mirrors
  `tracker._row_is_nights_top_pick` (ranked against the same slate's Supabase
  rows, fail-OPEN like the gate) and a STRONG bet the policy would not notify
  about is no longer an anomaly. `tests/test_reconcile_no1_gate.py` (+3) pins
  it, including the fail-open case.
- **`MODEL_UPDATED_FROM = "2026-08-23"`**; `/history` copy and the sub-total
  heading now say Aug 23; the four ship sections below are re-dated.
- **"Clear the errors": `system_errors.resolved_at` / `resolved_note`**
  (Supabase migration `system_errors_add_resolved_columns`, partial index on
  unresolved rows). `/api/health-live` excludes resolved rows, so the Ops
  Health card clears when a fault has been dealt with instead of 24 hours
  later. Rows are stamped, never deleted -- the log is intact. Stamped today:
  91 `odds-api` refusals and 325 spurious `reconcile-heal` notices, each with a
  note saying why. To clear a future batch: `update system_errors set
  resolved_at = now(), resolved_note = '<why>' where step = '<step>' and
  resolved_at is null and captured_at_utc < '<ts>'` (Supabase SQL editor or
  ask the agent).

### Operator action still needed

Paste the 20K key into Railway -> project **mlb-first-inning** -> service
**MLB-first-inning** -> Variables -> `ODDS_API_KEY` -> Deploy. The moment it
is live, the Railway log's first line per cycle reads `credits used N,
remaining 19xxx` instead of `remaining 4x`.

---

## [2026-08-23] - Line shopping is live: multi-book capture 4x/day, mirrored to Supabase, best price on the board

Operator added the `ODDS_API_KEY` secret; the capture ran on the first try
(356 rows over 15 games, 20,000 credits remaining). **Day-one measurement:**
across the slate, the best of 4-5 books vs the worst moves the first-inning
break-even from 53.6% to **50.6% -- ~3 points** -- edge-sized against a system
whose measured edge at one book was ~0 vs a 56.2% break-even. Best book varies
by game (FanDuel best on CLE@COL at -138 vs BetMGM -155; Caesars +130 vs
FanDuel +114 on STL@PHI). Mean overround by book: FanDuel 6.05%, BetMGM 6.16%,
Caesars 6.42%, BetOnline 6.97%, BetRivers 7.44%. F5 totals also returned
(3.5-6.0 lines, seven books).

**Shipped**
- `.github/workflows/odds_diagnostic.yml` now runs **four snapshots a day**
  (13:00/15:00/17:00/19:00 ET, ~120 credits/day), one CSV per snapshot
  (`data/diagnostics/odds/raw_<date>_<HHMM>.csv`), and mirrors each into
  Supabase via `tools/odds_multibook_to_supabase.py`.
- Supabase table `odds_multibook` (migration `odds_multibook_table`): natural
  unique key for idempotent upserts, indexed by slate date + game, RLS with the
  same anon/authenticated read policies as `picks_2026`. Today's 356 rows loaded.
- Dashboard: `GameDetail.bestOdds` (best NRFI/YRFI price + book, books counted,
  snapshot time) attached in `board-supabase.ts` from the **latest snapshot per
  game** (0.5 line only; "best" = largest American number). `OddsChip` prints
  **"best -105 MGM"** after the ledger price only when a better price for the
  *same side* exists elsewhere -- an instruction about where to bet, never a
  replacement for the ledger's number -- and the tooltip lists best NRFI/YRFI
  with book count and age on every priced chip, including PASS rows.
  Additive: a game with no snapshot renders exactly as before.

- Same day, two more surfaces: a muted **"mkt" chip** on the board BEFORE the
  ledger has a price (the snapshot is often earlier than the ledger's ~1h-
  before-lock capture, which is exactly when a bettor shops) showing the best
  NRFI/YRFI with book abbreviations; and **"best price"** beside "bet up to" on
  the No.1 hero card for the pick's side. Verified live: 15 "mkt" chips on the
  08-23 board with 4-5-book tooltips. `shortBook` learned RIV/BOL/BOV/BUS/MYB.

**Not changed, on purpose:** the ledger's price basis (one book, captured at
lock). Line shopping is surfaced for the bettor; changing what the record is
measured at is a product decision (memory `odds_source_strategy`).

---

## [2026-08-23] - Rollout plan follow-ups #2, #5, #6/#7 shipped

- **#2 Dashboard** -- `/history` now carries a "Since the Aug 22 model update"
  block (`TopPickReport.sinceUpdate`: record, at Kelly, flat, staked -- the
  identical rule over nights >= `MODEL_UPDATED_FROM`), rendered beside the
  season figures and never blended into them, plus a dashed `--rule` marker on
  the cumulative-units chart at the first settled night under the updated
  model. Verified live. `CURRENT_SYSTEM_FROM` stays 2026-05-26 on purpose.
- **#5 Ledger columns** -- `home_fi_xwoba` / `away_fi_xwoba` (the model inputs
  as the predictor saw them at pick time) appended LAST to `tracker.FIELDS`,
  written from the predictor's result dict, **frozen with the bet** once
  `bet_placed=Y` (added to the locked-row preserve list, like the lambdas),
  mapped in `db/supabase_writer.PICKS_CONVERTERS` and
  `db/migrate_csv_to_supabase.py`. Supabase `picks_2026` gained the two
  nullable double-precision columns by migration
  `picks_2026_add_fi_xwoba_columns` BEFORE the writer change shipped
  (106 -> 108 columns; the CSV is 117 -> 119; the mirror-is-not-the-CSV gap
  is unchanged in kind). `tests/test_sizing_prob_stamp.py` now asserts the
  tail of the schema explicitly so a reorder or an insertion ahead of
  `sizing_prob` fails. Suite: 271 passed.
- **#6/#7 Odds** -- `tools/fetch_odds_api.py --markets ... --raw-output` and
  `.github/workflows/odds_diagnostic.yml` (17:00 UTC daily + manual): every
  book's first-inning AND first-5-innings totals into
  `data/diagnostics/odds/raw_<date>.csv`, long format, diagnostic only.
  **Waits on one thing:** the `ODDS_API_KEY` repository secret for GitHub
  Actions (the key lives on Railway). Until then the job prints the
  click-by-click instruction and exits clean. ~30 credits/day.

---

## [2026-08-23] - Two foot-guns removed: the recalibrate action and the official trainer

Follow-ups #3 and #4 of the rollout plan.

**`recalibrate_v2.py`** (the `recalibrate` workflow_dispatch action) was failing
safe -- its own hardcoded 19-name feature list no longer matched the live
weights, so it exited before writing. Ported properly: feature lists are now
**imported from the predictor** (they can no longer drift), the pooled
first-inning xwOBA is supplied per game from the committed point-in-time
factor file (league mean where absent), the 2025 source is the `_ptfix`
leakage-repaired file (it read `truepit`), and the fit is **CIR** -- the shape
that has shipped since 2026-07-28 and that the 0.42 gate was re-derived on --
not PAV. Dry-run: 4,279 games scored, 20-long vectors, writes `kind: cir`;
the shipped calibrator was restored after the dry run (unchanged).

**`two_stage_model.py`** gains `--fi-xwoba`: the 20-feature set that ships,
values from the factor file, and a **production-path guard** -- saving any
other feature set to `data/lr_t1.json` / `lr_b1.json` is refused (the live
loader would reject it and fall back to the legacy path) unless
`--allow-legacy-save`. Found and fixed an L2-units trap in the process:
`lr_baseline.LogReg.fit` penalises the SUM of log-losses, so `--l2 0.05` was
~0.05/N per sample (nearly unregularised); the validated fitter penalises the
MEAN. With `--fi-xwoba`, `--l2` is read in the validated per-sample units and
scaled by N. The trainer now reproduces the shipped fit: fi_xwoba weight
0.0330 vs 0.0328, mean |Δw| 0.001. The one difference is the park input (the
trainer reads `fi_park_factors.json`, the same source the predictor feeds; the
ship refit used a pool-rebuilt map) -- measured on the gate's 3,728-game
holdout, shipped Brier 0.247740 vs trainer 0.247928, so the shipped weights
stay and the trainer is the canonical path for the next refit.

---

## [2026-08-23] - Rollout plan, ledger continuity, model gate taught the new feature

- **`docs/PLAN_2026-08-22_model_v3_rollout.md`** -- the operating plan: the
  ledger is continuous (the No.1 strategy's +66.2u since May 26 stands; the new
  model appends from Aug 22, nothing rewritten), what is live, first-day
  confirmation, daily operation of the pool, 30-day monitoring table, two
  things that must not be run as they stand (the PAV `recalibrate` action; any
  19-feature refit), the one-commit revert, and the follow-up list.
- `dashboard/components/TopPickHistory.tsx` -- the `/history` lead now states
  the continuity rule ("one continuous ledger... model updated Aug 22... nothing
  before that date is rewritten"). `CURRENT_SYSTEM_FROM` stays 2026-05-26 on
  purpose. A "since the Aug 22 update" sub-total is the next dashboard task.
- `tools/model_gate.py` -- scored 0 rows on the 20-feature push (the new input is
  not a holdout column). It now supplies it per game from the committed
  point-in-time factor file, league mean where a game has none. Local
  before/after on 3,728 holdout games: Brier 0.248322 -> 0.247740, BETTER on
  every season -- PASS. Pushed with `[gate-override]` because the baseline
  commit's gate cannot score the new model; the override is attributable in git.
- `tools/refit2026/no1_since_may26.py` -- reproduces the dashboard's +66.16u and
  runs the May-26 refit counterfactual (old +6.28u vs new +41.31u, same nights
  +28.8u [+6.1, +51.7], P=98%). The real 66.2u is not "old model, clean" -- it
  benefited from the 07-28 calibrator refit and gate changes -- so the honest
  counterfactual is the pair, not 66.2 + 35.

---

## [2026-08-23] - SHIPPED: pooled first-inning pitcher xwOBA + L2 0.50 (20-feature model)

> Date note: the ship commit `f7952566` was pushed **2026-08-23 10:18 ET**. This
> and the three sections above were first logged under 08-22 (the session's
> working date); the 08-22 slate was still picked by the previous weights, so
> the dashboard's `MODEL_UPDATED_FROM` is 2026-08-23.

Operator: *"so this whole time the model never knew we were focused on the first
inning only... don't forget about the fixes to make though."* Both done.

**What shipped (one commit, code + weights + state + cron + docs together, because
`_load_one` refuses a weights file whose feature list disagrees with the code):**

- `fi_pitcher_pool.py` (repo root, imported by the predictor) -- the pooled
  first-inning pitcher xwOBA as a **running-sums state**: per pitcher, this
  season's and prior seasons' (PA, wOBA) plus league totals; K_PA=60 shrinkage,
  prior seasons x0.6 at rollover (folded only for pitchers who appeared in the
  season being closed -- mirrors the validated batch builder; the incremental
  rebuild matches it to max |diff| 0.00005 over all 613 pitchers). Advances one
  day at a time from Savant; "yesterday" is computed in **Eastern time** so a
  UTC runner can never ingest today's partial slate and freeze it as complete.
  `data/fi_pitcher_pool.json` (81 KB, 1,283 pitchers, as of 2026-08-22) is
  committed. `--rebuild` regenerates it from the research cache.
- `mlb_first_inning_predictor.py` -- `home_fi_xwoba` appended to
  `_T1_EXPECTED_FEATURES`, `away_fi_xwoba` to `_B1_EXPECTED_FEATURES`;
  `t1_features`/`b1_features` take the value (league-mean default); call sites
  pass `_fi_xwoba_for(pitcher_id)`. `_load_fi_pitcher_pool()` loads once,
  refreshes when behind (bounded 12 days, `FI_POOL_REFRESH=0` disables) and
  **fails open** to the last good state on any error.
- `data/lr_t1.json`, `data/lr_b1.json`, `data/calibration_v2.json` -- the
  candidate artifacts (20 features, L2 0.50, CIR refit on 2024-26, train_n 6673;
  feature weight +0.0328 T1 / +0.0240 B1). Backups:
  `data/*.bak-2026-08-22-pre-fixwoba`.
- `.github/workflows/daily.yml` -- grade job step "Advance first-inning pitcher
  pool" (`python fi_pitcher_pool.py --update`, soft-fail); the existing commit
  step carries the state forward.
- `dashboard/lib/pick-reasons.ts` -- the two names mapped to home-/away-pitcher
  so the brief can attribute them (unmapped names are never sentenced).
- `tests/test_fi_pitcher_pool.py` -- 7 tests (ingest, idempotence, shrinkage,
  rollover semantics, ET date, roundtrip, batch-equivalence when the cache is
  present). Suite: 271 passed.

**Verified locally before push:** models load with 20 names; both feature
vectors are 20 long; synthetic game -- both starters cold (0.36) p(YRFI) .527,
league-average .513, both sharp (0.28) .499: higher xwOBA allowed -> more runs.

**The operator's question -- which other inputs have the same whole-game-average
problem?** Classified and, where a first-inning version was buildable, tested
(all under the full protocol, on both the old and new base):

| input the model uses | what it measures | first-inning-specific version | result |
|---|---|---|---|
| `home/away_fip`, `_xera`, `_whiff_pct_rank`, `era_gap` | starter, whole game, this season | pooled 1st-inning xwOBA / K% / CSW / velo drop / F-strike / zone | **xwOBA shipped**; K% passes but lowers the No.1; rest dead |
| `p_last5/last10_pitcher_nrfi` | starter's 1st inning, but 5-10 innings | pooled 1st-inning run-allowed rate (`fi_ra`) | ALL+ on the old model, redundant once xwOBA is in |
| `away/home_obp`, `top3c_obp/slg/iso`, `top3_ops_vs_oppHand` | lineup, whole game, this season | pooled top-3 xwOBA / K%, top-3 1st-inning xwOBA, leadoff alone, slots 4-5, platoon | all dead (the lineup side the model has is sufficient) |
| `fi_park_nrfi_rate`, `home_plate_ump_nrfi_rate`, `pvt_nrfi_rate` | already 1st-inning-specific | -- | park has no OOS value; ump/pvt ~0 weight |
| `wx_*`, `avg_ip_per_start` | game-time weather, opener detection | n/a (already right) | -- |

So: every pitcher input was a whole-game average; the lineup inputs were too but
their first-inning versions add nothing; the one that mattered is now in.

**Reversal:** restore the three `.bak` files and remove the two names from the
feature lists (one commit). No gate, staking, or ledger code touched.

---

## [2026-08-22] - Schedule/fatigue factors and the rpg stacking term: tested, dead

`tools/refit2026/build_schedule.py` (from the per-inning linescores): extra
innings yesterday (#17), consecutive game days (#19), games in the last 7.
All three fail both bases and lower the No.1 hit (.736 -> .699-.709); null:
survivors none. The game-level `rpg_sum` stacking term that survived over the
SHIPPED base at p=0.052 does not survive over the candidate (-0.44 on 25->24):
L2 0.5 + fi_xwoba absorb it.

With this the Statcast 20-factor backlog is exhausted in the pooled form.
Candidate unchanged: fi_xwoba + L2 0.5.

---

## [2026-08-22] - Team defense and sprint speed: tested, dead

`tools/refit2026/build_defense_speed.py`: the fielding team's PRIOR-season Outs
Above Average (Savant fielder leaderboard summed per club; the all-teams pull
silently returns one club, so it is fetched club by club) and the batting
top-3's prior-season mean sprint speed (backlog #12), 100% / 99.5% coverage.
Both bases, three splits, No.1 metric, null:

- `def_oaa`: +0.0021 / +0.0014 / **-0.0026** AUC on the new base; doubles the
  gate's bets (121 -> 236) and lowers the No.1 hit (.736 -> .678). Dead.
- `top3_sprint`: **-0.0053** on 24->25 (CI below zero), flat elsewhere. Dead.
- Null over the pair: survivors none.

---

## [2026-08-22] - Batter side and platoon, pooled: tested, no addition survives

`tools/refit2026/build_batter_pooled.py` over all 6,611 games (lineup cards
from `fetch_batting_orders.py`, 6,595 resolved): top-3 pooled xwOBA and K%,
top-3 first-inning xwOBA, leadoff alone, slots 4-5, the opposing starter's
pooled xwOBA vs each hitter's side (platoon), a pooled pitcher x lineup
interaction, plus pooled first-pitch-strike and zone rates for the pitcher.
Per-half refit on BOTH bases (shipped; fi_xwoba + L2 0.5), three splits, the
No.1 metric, and the search-aware stacking null over the nine:

- **Survivors: none.** Noise yields >=1 survivor 37.5% of the time; observed 0.
- Near-miss: `platoon_xwoba` -- dAUC positive in all six cells (both bases x
  three splits; +0.0106 on 2026 over shipped, +0.0049 over the new base) but
  logloss negative on 25->24 in both, and it LOWERS the No.1 hit on both bases
  (.736 -> .679; .657 -> .646) while firing ~50% more bets.
- `mid_xwoba` is ALL+ on the shipped base with a NEGATIVE coefficient (better
  4-5 hitters -> fewer runs) -- a wrong-sign survivor is noise.
- `pool_x` ALL+ on the shipped base only; balloons the gate 152 -> 279 bets.
- `top3_fi_xwoba` raises the No.1 hit to .762 on the new base but fails 2026.

Reading: the first-inning signal lives in the pitcher (~15-20 pitches, ~4
batters); the lineup side the model already carries (top-3 OBP/SLG) is
sufficient. The candidate stays fi_xwoba + L2 0.5.

---

## [2026-08-22] - The No.1 product metric, and the second feature sweep on the new base

Operator: *"our number one pick model was seeming to be the best... we are not
stopping until we find something to improve the model and become more
profitable and make better picks."* Two things done in response.

**1. The No.1 itself is now measured** (`tools/refit2026/no1_sim.py`). Out of
sample on 2026 (fit on 2024+25), through the real shape (LR -> CIR -> gate
0.42), each night's No.1 = the lowest calibrated p_nrfi among gate-firing
games:

| config | slates | No.1 hit | flat P&L (-112) |
|---|---|---|---|
| TODAY (shipped, L2 0.05) | 99 | .657 | +24.0u |
| L2 0.50 only | 83 | .759 | +36.3u |
| feature only (+fi_xwoba, L2 0.05) | 99 | .657 | +24.0u |
| **CANDIDATE (+fi_xwoba, L2 0.50)** | **87** | **.736** | **+34.1u** |

Candidate vs today on the 82 common nights: .695 -> .732, dHIT +0.038
[+0.000, +0.085], P(better) 89%, same game picked 90% of nights. By month the
candidate beats today in 4 of 5; **August .538 -> .769** (13 slates each). The
L2 change drives most of the No.1 lift; the pitcher feature drives the broad
AUC/logloss gain and keeps the lift while firing more nights. L2 is a plateau
on this metric (0.5 -> 2.0: .735-.744); 0.5 keeps the best 2026 logloss.

**2. Second sweep on the new base** (`tools/refit2026/test_feature_set.py`,
per-half refit over fi_xwoba + L2 0.5, three splits, plus the No.1 metric):

| candidate | 3-split | note |
|---|---|---|
| fi_k (pooled 1st-inning K%) | ALL+ | correct negative sign, but No.1 hit .736 -> .698 -- not added |
| fi_velo (pooled 1st-inning FB velo minus own all-inning mean; builder extended) | fails 24->25 | |
| velo_vs_own (old season-reset build) | excluded | 53-69% coverage |
| top3 chase / K / contact-quality (old CSVs, as features) | fail | mixed signs |
| cold-pitcher x lineup OBP interaction | fails | |
| team_fi_score (own 1st-inning scoring history, pooled, from linescores) | fails | already carried by lineup/pitcher features |
| team_fi_allow | ALL+ by <=0.001 | sign flips on shipped base; doubles gate bets, No.1 .736 -> .684 |
| recency-weighted training (half-life 60-480d) | fails | neutral at 480, harmful at <=120 |

Pending the lineup-card fetch (`fetch_batting_orders.py`): pooled top-3 xwOBA /
K%, top-3 first-inning xwOBA, leadoff alone, slots 4-5, pitcher platoon xwOBA
vs the lineup's sides, pooled first-pitch-strike and zone rates.

No model, gate, staking, or ledger code touched.

---

## [2026-08-21] - Target horizon: the same inputs rank 3- and 5-inning scoring far better than the 1st inning

`tools/refit2026/fetch_linescores_full.py` pulled per-inning linescores for
all 6,611 games in the three datasets from MLB statsapi (0 failures; H=1
totals match the ledger on 99.8-100% of rows). `target_horizon.py` then refit
the two-stage model -- SAME 19 features per half -- against "runs through
inning H exceed the train-season median":

| H | 2024->2025 | 2025->2024 | 24+25->2026 |
|---|---|---|---|
| 1 (the product) | 0.5198 | 0.5174 | 0.5259 |
| 3 | 0.5308 | 0.5372 | **0.5790** |
| 5 (F5) | 0.5398 | 0.5441 | **0.5691** |
| 9 | 0.5332 | 0.5535 | 0.5735 |

AUC climbs with the horizon in all three splits: the inputs are informative
and a single inning is too noisy a target for them to surface. On 2026 the
H=1 -> H=3 gain (+5.3 points) exceeds every model improvement ever validated
here combined. NOT shown: whether we beat the F5 market -- no F5 odds have
ever been captured (The Odds API `totals_1st_5_innings`; F5 vig ~4.5% vs
6.55% on the 1st-inning total). Strategic finding, recorded in memory
`2026-08-21_target_horizon`; no product change.

---

## [2026-08-21] - FIRST VALIDATED MODEL IMPROVEMENT: pooled first-inning xwOBA (candidate, not shipped)

Operator: *"keep going and don't stop until you find something that improves
the model."* Found one -- the first of ~65 candidates tested in this repo to
clear the full `feature_test_methodology` bar.

**What it is.** A starter's first-inning expected-wOBA allowed (`fi_xwoba`),
built from the local Statcast pitch-level cache (`data/cache/statcast_zone`,
all innings, 2024-03 .. yesterday), POOLED ACROSS SEASONS (earlier seasons
x0.6), empirical-Bayes shrunk toward the league mean (K_PA=60), and computed
strictly from plate appearances before each game date. Batch 1 on 2026-08-02
built the season-to-date version and it failed for the sample-size reason that
memory itself diagnosed; the pooled version it prescribed had never been built.
Home pitcher's value -> T1, away pitcher's -> B1.

**Evidence** (`tools/refit2026/test_fi_pooled.py`, `test_fi_money.py`,
`robust_fi.py`):

- Coverage 89 / 95 / 96%; real variation (sd 0.0245, 1155 distinct values).
- Stacking over the refit shipped model, three splits: +0.63 / +0.96 / +1.39
  (x1e-3 logloss) -- survivor. Selection-aware null (8 candidates, 200 full
  sweeps on permuted columns): best-mean in noise 0.223, 90th pct 0.449;
  observed 0.993 -> **p = 0.000**.
- As a real per-half refit feature: dAUC +0.0015 / +0.0012 / +0.0071; logloss
  CI above zero on 2 of 3 splits. Coefficient POSITIVE and stable in every
  split, both halves.
- Robust: ALL+ at K_PA 30/60/120 and prior-season weight 0.3/0.6/1.0 -- a
  plateau, not a spike. Stacks with the L2 0.05->0.50 change.
- **Combined (fi_xwoba + L2 0.5) vs today's model: AUC +0.0070 / +0.0049 /
  +0.0129**, logloss +3.96 / +9.30 / +1.40 (x1e-3).
- Independent leakage audit: one July game's value recomputed from raw pitches
  strictly before the date, 0.3144 = 0.3144; zero cache rows with
  game_date != file date.
- At the gate (LR -> CIR -> 0.42), 2026: 152 bets @ 63.2% +18.1% -> 161 @
  65.8% +23.1%; August 17 @ 52.9% -> 21 @ 71.4%. Slate-day bootstrap dROI
  +5.07pp [-1.46, +11.88], P(better) 91%; level-fixed dROI +3.1 / -2.8 / +6.7.
  Money is directional, not yet past the CI -- stated plainly.

**Added**
- `tools/refit2026/build_fi_pitcher_pooled.py` -- the factor builder; also
  dumps `data/candidates/fi_pitcher_pooled_current.json` (613 pitchers, as of
  2026-08-21) for predict-time use.
- `tools/refit2026/refit_candidate.py` -- candidate `lr_t1.json`, `lr_b1.json`,
  `calibration_v2.json` in the predictor's exact schema, under
  `data/candidates/refit2026_fixwoba/`. Weights on the feature +0.0328 (T1)
  / +0.0240 (B1). **Not written to `data/lr_*.json`.**
- `tools/refit2026/test_fi_pooled.py`, `test_fi_money.py`, `robust_fi.py`,
  `fetch_linescores_full.py` (per-inning linescores for a multi-inning-target
  test, still running at commit time).

**Fixed**
- `tools/scrape_statcast_zone.py` -- the 2026 window was hardcoded to end
  2026-08-03 (the day of the original scrape), so every later run reported
  "skipped, already cached" and fetched nothing. Clamped to yesterday. Cache
  now 544 day-files through 2026-08-21.

**Deferred -- the production wiring, which waits for the operator's go**
(money-path change): append `home_fi_xwoba` / `away_fi_xwoba` to
`_T1/_B1_EXPECTED_FEATURES`, load the current-state JSON in
`mlb_first_inning_predictor` (league-mean default), add a daily
`scrape_statcast_zone.py --seasons 2026` + rebuild step to the grade cron,
swap in the candidate artifacts, and refit on the sliding-window cadence.

No model, gate, staking, or ledger code touched. Tests: 264 passed.

---

## [2026-08-21] - No.1 autopsy, wind retested on live data, candidate-factor sweep

Operator asked three things: what separates the winning No.1s from the losing
ones, retest wind direction ("I think that one direction might"), and hunt new
stats. All three run under the full feature_test_methodology protocol.

**No.1 autopsy** (`tools/refit2026/no1_autopsy.py`). The win window
(08-01..08-13, 8W-3L) and loss window (08-14.., 1W-6L) are the *same bets*:
same parks dominating both (COL/LAA/CWS; LAD@COL lost 8/17, won 8/18, lost
8/19), confidence 0.677 v 0.661, price 0.576 v 0.565. Season-long, winners vs
losers across 49 features: the best split (home_obp, 0.425 SD) is SMALLER than
the average best split the same sweep finds on shuffled labels (0.472 SD),
selection-aware p = 0.68. At the No.1's 61.1% season hit rate the expected
longest streaks are ~8W/~4L over 108 slates; actual 10W/3L. One actionable
cell: No.1s at LAA are 4-8, -11.1u -- handed to the loss-cluster pipeline
(discovery -> monitor), NOT acted on directly per CLUSTER_DISCOVERY.md.

**Wind direction, third test** (`tools/refit2026/wind_2026_retest.py`). New
angle honoring the operator's reaffirmation: 2026 live games -- never tested
before because the predictor does not record wind direction. Backfilled
wind_deg from the open-meteo archive for all 1380 outdoor graded games (100%
coverage). League-wide corr(wind_out, YRFI) +0.030 with the crosswind placebo
at -0.043 (placebo bigger, again). By direction: OUT-to-CF 51.8% YRFI vs IN
48.6%, but placebo directions spread wider. On our own bets: corr(win,
wind_out) = -0.0002, permutation p = 0.997. Refuted a third time, now on the
bets themselves.

**Candidate sweep** (`tools/refit2026/candidate_factors.py`). 12 unused
columns (k9/bb9/hr9/whip/era/rpg/slg sums, mins/maxes, lefty count;
pitcher_q/batting_q excluded at 0% coverage) tested for incremental value over
the refit shipped model, three splits + selection-aware null (250 full-sweep
trials). Sole survivor: rpg_sum (both teams' runs/game), mean +1.05e-3
logloss, selection-aware p = 0.052 -- borderline. Confirming test FAILED: as a
per-half refit feature (away_rpg to T1, home_rpg to B1) it is worse in all
three splits. Status: a stacking-only lead, backlogged, not shipped. It
converges with the legacy-Poisson finding (the one model built from team run
environments out-ranks the LR on 2026): the under-used signal is the
game-level scoring environment.

No model, gate, staking, or ledger code touched. Tests: 264 passed.

---

## [2026-08-20] - Repair validation: it is the LEVEL, not the weights (tools/refit2026)

Follow-on to the decay investigation. Operator approved building and validating
the proposed combined repair (refit weights + fix the collinear feature pair +
properly-shrunk park factors). Built, validated three ways, and **most of the
proposal did not survive** — the parts that did point somewhere else.

**Added** — `tools/refit2026/` (validation only; writes nothing to `data/`, the
ledger, or any model artifact). Park factors are rebuilt inside every split from
training seasons only, so no result here can be contaminated the way the shipped
file was. See its README for the traps.

**What did not survive**

- *Fixing the collinear slg/iso pair does nothing.* Dropping either half moves
  AUC by ≤0.002 in either direction and fails all three splits. The L2 penalty
  was already absorbing it. This was the headline of the proposal and it is dead.
- *Re-shrinking the park factor does nothing either* — ≤0.0002 AUC. Correct in
  principle (the file is ~3× under-shrunk) but there is no signal to recover.

**What did survive, and how far**

- *Raising L2 from 0.05 to 0.5* is the only variant that beats shipped on AUC in
  all three splits (+0.0053 / +0.0031 / +0.0053), with a smooth bias/variance
  curve rather than a spike, and better logloss in all three.

**The confound that reframed everything.** `money.py` first showed L2=0.5 at
+33.8% ROI on 2026 against shipped's +18.4%. That is not skill. Flat ROI at the
0.42 gate tracks the *direction of the train/test base-rate gap* almost
perfectly (2024→2025 −3.8pp gap → +3.7%; 2025→2024 +3.8pp → −12.0%;
24+25→2026 −2.3pp → +18.4%). Giving the **shipped** model an oracle level
correction swings it from −12.0% to +2.9% and from +3.7% to −6.0%. A gate is a
cut point on a level, so a wrong level *is* the result. L2 does survive that
control (+6.7 / +1.5 / +9.9 pp) but is a minor term beside it.

**Where that leads: refit the calibrator more often.** A calibrator is a
monotone map — it cannot change ranking, so it cannot make the discrimination
problem worse, and it needs no weight refit (so it does not disturb the frozen
feature standardisation a park-file change would). Walk-forward on the live 2026
ledger, fitting only on games graded strictly earlier:

- logloss better in **18 of 18** window/cadence settings (0.694–0.696 v 0.70059)
- level bias **+0.0243 → +0.008…+0.019**, in all 18
- flat ROI better in all 18 (+9.9%…+20.7% v +4.9%), bets 121–177 v 326 at a
  higher hit rate (0.588–0.645 v 0.561)

**Deferred, and why.** Not shipped. The money case is directional, not
established: the day-level bootstrap is **+4.58pp, 90% CI [−3.95, +12.99],
P=82%** — it does not exclude zero. And by month it is better in June and July
but *worse in August* (+12.2% v +16.0% on 20 v 29 bets), which is the month the
repair was motivated by. Calibration quality and level bias improve reliably;
money does not yet clear the bar in `feature_test_methodology`.

**One correction to this session's own output.** `recal_walkforward.py` briefly
printed an AUC gain for the refit series. A monotone map cannot move ranking;
that number is an artifact of pooling calibrator vintages (the ledger's
`nrfi_prob` spans the 2026-07-28 CIR swap — after it spearman(raw, shipped) is
1.0000 and the AUCs agree exactly at 0.4926; before it spearman is 0.9649). The
script now prints raw/shipped/refit side by side and says the repair is
level-only.

Tests: 264 passed. No model, gate, staking, or ledger code touched.

---

## [2026-08-20] - Model decay investigation: four defects, three verdicts

Operator: *"the model has been doing terrible lately. do NOT just respond with
'its variance'. its not. something is wrong."* Correct. Last 7 days 1W-7L
(-29.484u), last 30 days 28W-28L (-3.802u), season still +36.697u / 58.2%
(all figures from `tools/pl_calc.py`).

**Finding 1 — the league moved ~20% and the model did not.** First-inning runs
league-wide: 1.17 (Jun-a) → 0.99 (Jul-b) → 0.89 (Aug-a). `lambda_lr_total` over
the same span: 0.783 → 0.771 → 0.772. Decomposed against the model's own
training SDs, the total predicted shift in T1 log-lambda is **+0.027** — the
wrong direction. All 19 features are season-cumulative rate stats that
under-react by design; there is no league-level or date term; and the largest
actual mover is temperature (+1.44 SD, April cold → August hot) carrying a
POSITIVE weight. Consequence: STRONG bets since Jul 15 said 63.8%, hit **52.7%**.
Quarter-Kelly sizes hardest exactly where the model is most wrong.

**Finding 2 — the market out-ranks us, and our disagreement is inverted.**
n=1435 priced games: model AUC **0.510** [0.485, 0.535], market **0.548**;
paired bootstrap says market better with 99.4% confidence. Games where we call
YRFI likelier than the market hit 48.1% (Jun), 46.3% (Jul), **40.7%** (Aug);
where we call it less likely, **67.5%** in Aug.

**Finding 3 — the park factor has no out-of-sample value (corrects an earlier
read in this same session).** `data/fi_park_factors.json` was built 2026-05-19
*from* `picks_2026.csv`. Split on its build date: in-sample **+0.690**
[+0.518, +0.841], out-of-sample **−0.057** [−0.384, +0.277]. The first pass
here reported "+0.569 Apr–Jun, dead from July" — that was the file measured
against its own training data. Underneath, park FI rates barely repeat
(2024↔2025 r=+0.16, 2025↔2026 r=+0.17, all CIs span zero; split-half within a
season r≈0.26). Correct shrinkage is ~3× heavier than shipped (prior ~309 vs
`PRIOR_GAMES=50`).

**Finding 4 — swapping back to the legacy Poisson: TESTED, REFUTED.** On 2026
production data `combined_lambda` out-ranks the shipped two-stage LR 0.5560 vs
0.5124 (n=1499, +0.044, P=99.6%), and it is *not* the calibrator (LR raw 0.5113
vs calibrated 0.5124 — identical, as a monotone map must be). But the 2024/2025
backtests cannot test this as they sit: they were generated by the legacy
pipeline, where `lambda_total` and `yrfi_prob` correlate **0.997**. Re-scored
through the shipped `lr_t1.json`/`lr_b1.json`, the three splits are
2024 shipped **0.5406** vs 0.4992, 2025 shipped **0.5700** vs 0.5134,
2026 shipped 0.5124 vs legacy **0.5560**. One direction only → rejected.

**What the re-score did establish:** the shipped model scores 0.54/0.57 on
2024/2025 and ~0.52 on 2026, and within 2026 its in-sample (0.5208) and
out-of-sample (0.5241) AUCs are the same. Not overfitting — a relationship that
held in 2024–25 and no longer holds. August: 0.4915 [0.4327, 0.5491].

**A rolling level correction does not fix it — tested.** Walk-forward
intercept-only recalibration (14/21/30/45/60/90d windows, offset fitted only on
prior games) removes the bias but worsens logloss *and* Brier at every window.
On bets it is period-dependent: season-wide it drops 121 that hit **62.8%**
while keeping 318 that hit 54.7%; from Jul 15 it drops 39 that hit 46.2% and
keeps 40 that hit 60.0%. Helping only inside the window where the problem was
found is a fitted fix, not a fix.

**Wind direction — tested, refuted.** See `wind_direction_dead` memory. The
crosswind placebo matches or beats the real out-component at every stage
(raw +0.0312 vs +0.0342; within park-month +0.0337 vs +0.0257; best sweep z
+2.58 vs +2.05). Fails the three-split rule (2024→2025 better by 0.0006,
2025→2024 worse by 0.0041), permutation p=0.0625, and Wrigley — the one park
where the effect is universally accepted — is flat at +0.062, z=0.60.

**Deferred** — no model, gate, staking, or ledger code changed. Park rebuild and
any refit are ONE change, because the model standardises `fi_park_nrfi_rate`
with a stored std of 0.0408 and a properly shrunk file is 2.3× narrower.

---

## [2026-08-20] - Drift monitors could never dedup, and never logged (T8.40)

Operator: *"the model has been doing terrible lately. do NOT just respond with
'its variance'."* Investigating that turned up an ops defect first.

`notifications_log` has **zero rows for `calibration_drift` and `feature_drift`,
ever** — going back to the table's first row on 2026-05-02 — while every other
event type logs normally (`strong_graded` 318, `daily_heartbeat` 33,
`stake_drift` 4). Both monitors run in the same `daily.yml` grade job as
`stake_drift` and call the same `tracker._notify_event_telegram`.

**Root cause:** four steps passed `TELEGRAM_*` but not `SUPABASE_*`.
`notifications_log` lives in Supabase and *both* halves of the notify contract
read it:

- `_notify_event_dedup_check` → `_get_client()` is None → returns `False`
  (fail-open) → **every run re-pings**
- `_notify_event_record` → `_get_client()` is None → returns early →
  **no audit row, ever**

So these alerts fired *every single day* with no dedup and left no trace. That
is exactly the spam the operator reported on 2026-08-13 (*"i keep getting
telegram notifications saying calibration drift"*), and it explains why the
fix that day — registering `"calibration_drift": 7 * 24 * 60` in
`_DEDUP_WINDOW_M` — did nothing: **the dedup window is read from a database
the step could not reach.** Fourth instance of an alert-plumbing gap after
discord_board (8/06), watchdog (8/07), calibration_drift (8/13).

The cost is not a missing alert, it is a *devalued* one. The monitor did detect
the August decay — `deep_yrfi` hit rate **77% → 54%**, Brier 0.2306 → 0.2651 —
but arrived looking identical to the previous hundred daily pings.

**Fixed**

- `.github/workflows/daily.yml` — added `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`
  to the 4 Telegram-sending steps that lacked them: *Feature drift monitor
  (T4.5)*, *Calibration drift monitor (R3)*, *V2.1 vs V2.2 shadow comparison*,
  *Cluster demotion re-eval reminder (R4)*. All 10 Telegram steps now carry
  both credential pairs.
- `tools/calibration_drift_monitor.py` — the closing line said
  `"NOT sent (dedup or no creds)"`, which lumped four distinct states into one
  string and actively misled this investigation. It now names which: no creds
  (and which one), Supabase missing (*and warns that dedup is failing open*),
  deduped (with the key), or send rejected. The dedup state is sampled
  **before** the send, because a failed send still writes a row with
  `delivered=False` and would otherwise be misreported as a dedup hit.

Tests: 264 passed. No model, gate, staking, or ledger code touched.

---

## [2026-08-20] - Odds source switched to The Odds API, priced at FanDuel

Operator: *"the odds are not working. we need to use odds api."* Correct — the
whole 08-20 slate had no price at all, with a STRONG YRFI due to lock at 13:10 ET.

**The documented plan was refuted by the first live call.**

`odds_source_strategy` assumed we could buy *DraftKings'* number from The Odds
API — "same book, different pipe", which is what kept the published series
continuous. **DraftKings does not offer `totals_1st_1_innings` there.** Measured,
not inferred: DK returns a normal moneyline quote on the same event and nothing
on the first-inning total, while FanDuel / BetOnline.ag / BetRivers / BetMGM
each cover 9 of 9 games. So enabling the feed with its default `--book
draftkings` was a silent no-op — it returned nothing and cost nothing.

**Changed**

- `PREDICTOR_ODDS_API=enabled`, `ODDS_API_BOOK=fanduel` on Railway. Operator
  chose FanDuel over BetMGM / BetOnline / best-of-N because the picks are sold
  and the published price must be one a subscriber can actually take (BetOnline
  is offshore). Cost of that choice, stated plainly: FanDuel was the *worst* of
  the three on the day's STRONG — -122 against BetOnline's -103 and BetMGM's
  -110, a 19-cent spread worth roughly two stake steps. n=1; measure over a week.
- `PREDICTOR_SCRAPE_DK=skip`, so a recovered DraftKings scraper cannot write its
  price into the same file and leave the ledger silently mixing two bases.
- **The book name is read from the row, never hardcoded.** Three subscriber
  Telegram bodies and one Discord line printed a literal "DK" beside the price.
  A follower reading "DK -122" would open DraftKings and find a different number
  against a FanDuel-sized stake — T8.30's rule applied to the book name.
  `tracker._book_label` mirrors `shortBook()` in `BoardRow.tsx`, and a test
  parses the TSX to assert the two agree.

**Unchanged, deliberately**

- **Every past night keeps its DraftKings price.** Nothing is re-priced;
  2026-08-20 is recorded as the date the source changed. Re-pricing history
  would alter the record of games already played.
- Dashboard copy describing the DK-priced *record* is left as-is — still true.

**Verified**

- 3 of 9 games priced at 15:25Z with `book=FanDuel` (STL@CIN -104, TOR@TB +102,
  SF@CLE +128); the rest price as they enter the 120:55 window.
- Measured cost: **9 credits per full-slate sweep** — the event list is free,
  1 credit per game. Free tier is 500/month; the $30 "20K" tier covers it.
- 13 tests in `tests/test_book_label.py`. Suite 264 green. `tsc --noEmit` clean.

---

## [2026-08-19b] - The night's card now publishes when the pick locks, not when GitHub gets round to it

Operator: *"it is Wednesday, August 19 but the cards section still only has the
x post for Tuesday … make sure that it updates every single day right as the
number one pick locks."* Two separate faults sat behind that one symptom.

**Fixed**

- **T8.38 / T8.18 PART 2 — the No.1 was never committing, so no card could
  exist.** DraftKings started 403ing the Railway worker; `odds_captured_at`
  froze at 15:19 UTC across all 15 rows and stayed frozen for nine hours. A
  pick only commits inside `_apply_odds_to_row`, which runs *only when a fresh
  price arrives* — so LAD@COL sat at `bet_placed=N` with a 7u stake and a -145
  price well past its 19:40 ET lock cutoff, and `make_card` (correctly) refuses
  to draw an uncommitted pick. `tools/lock_commit.py` exists for exactly this
  and was switched off. **`NRFI_LOCK_COMMIT=enabled` set on Railway**, ten days
  after PART 1 and only after checking the documented precondition:
  `stake_drift.py` reported **0 violations over 28 locked STRONG rows on 16
  slates**. Rollback is deleting the Railway variable.
- **T8.38 — the card render only ever ran on GitHub Actions, so it could land
  after first pitch.** `workers.predictor_loop.step_publish_cards` now draws and
  publishes the cards + X post in the SAME Railway cycle that commits the pick.
  Measured on the night it shipped: the commit landed 00:30 UTC and the next
  GHA tick was 01:00 — the "tonight's play" post would have appeared twenty
  minutes after the game it advertised had started.

**Changed**

- The new step runs **dead last in the cycle, after the watchdog** — same rule
  as the Discord broadcasts: everything above owns money, data or monitoring,
  this owns marketing, and a slow Pillow render must never push a bet commit or
  the dead-man's switch later.
- It redraws **only when the No.1's signature changes** (matchup, side, stake,
  price, committed). A 5-minute cycle across a 17-hour window would otherwise
  be ~200 renders and ~200 OpenRouter calls a day to upsert three identical
  objects. The cache is a module global — the worker is long-lived, and the
  worst a restart costs is one redundant render.
- **A host without `OPENROUTER_API_KEY` publishes the card but NOT the post.**
  `make_post`'s template fallback is correct when one host renders — absent,
  not broken. With two hosts it becomes a *downgrade*: GitHub Actions holds the
  key and writes the real paragraph, and Railway (which does not) would upsert
  the generic template over it, ~6x more often. Caught live on the first night
  this step ran. The card carries no generated text and cannot be degraded, so
  it still publishes every cycle. Setting `OPENROUTER_API_KEY` on the Railway
  service is all it takes to get the post same-cycle too.
- **The card and the post carry SEPARATE cache markers.** With one shared
  marker, a post stuck failing (the `cards` bucket rejected `text/plain` with a
  415 once already) would redraw the three heavy Pillow plates every 5 minutes
  all night — ~200 renders and ~660MB of uploads to retry one small text
  object. Split, the cheap half retries on its own.
- **Committed picks only** — `--allow-uncommitted` is deliberately not passed.
  `pl_calc` counts only `bet_placed=Y`, so publishing an uncommitted play would
  put a bet on the public card that the tracked P&L does not contain (T8.30).
  A stale card is a much smaller failure than a published bet the ledger denies.
- GHA keeps its own copy of the step. Two hosts upserting the same three
  date+plate-named objects is redundancy, not a race, and the card must survive
  either host being down.
- Kill switch: `PREDICTOR_PUBLISH_CARDS=off`.

**Verified**

- Wednesday's cards + post published at 00:34 UTC — 4 minutes after the lock,
  6 minutes before first pitch — carrying **3u @ -160**, matching the committed
  row exactly. The stake had correctly re-derived 7u → 3u as DK moved to -160
  and p(YRFI) fell to 66.1% (T8.18 PART 1 doing its job).
- `tests/test_card_publish_step.py` — 12 tests covering redraw-on-change,
  skip-when-unchanged, committed-only, no-post-without-a-card, retry after a
  failed render, the kill switch, soft-fail, and the cycle ordering. Suite 246
  green (was 234).

**Not touched:** model weights, gates, thresholds, calibration, the staking
formula, and every money column in `tracker.py`. The only money-path change is
the Railway flag flip, which is the shipped-dark T8.18 PART 2 being armed.

**Open:** DraftKings now 403s Railway intermittently — the wall that has been
on GitHub Actions since May. Railway is the only working capture source, so
this is the real underlying risk; operator has asked to price a paid odds feed
rather than keep scraping.

---

## [2026-08-19] - Railway: stop the cron's own data commits from restarting the workers

Operator reported "the runs on Railway keep failing" for both first-inning and
strikeouts. Neither model was broken — both were running. Diagnosis below, plus
the one config fix that was actually warranted.

**What was NOT wrong** (recorded so the next session doesn't re-chase it):

- **strikeouts: the wall of `SKIPPED` deploys is by design.** That worker
  commits `dashboard/public/data.json` every 5 min and pushes to its own repo,
  which asks Railway to redeploy the worker that just pushed. `railway.json`
  there carries `watchPatterns` with `!/dashboard/**`, so Railway correctly
  declines. ~290 grey rows/day is the loop-breaker working, not a failure.
  Note it also means the *container image* only rolls on a code commit — but
  the worker `git reset`s to origin every cycle, so Python changes still land
  within 5 min; only `requirements.txt` / `Dockerfile` changes need a redeploy.
- **first-inning: the 5 `FAILED` deploys were a burst on 08-18, 15:39–23:29 UTC,
  and stopped on their own.** Builds all succeeded (image pushed); they died at
  container start with zero deploy log lines, and the predictor and
  live-scoreboard services failed at *identical* timestamps (21:35, 23:05,
  23:29). Two independent processes don't fail at the same instant for their
  own reasons — that was platform-side. No code fix applied, none warranted.

**Changed**

- **`railway.json` now carries `watchPatterns`.** first-inning had none, so all
  ~17 daily `auto: predict` commits from the GHA cron rebuilt and restarted
  *both* Railway services. Confirmed on 08-19: deploy at 14:27:27 →
  `Starting Container` at 14:28:37 (predictor) and 14:29:23 (scoreboard).
  `workers/predictor_loop.py` already names "mid-cycle redeploy" as drift it
  has to heal from; this removes the cause.

  The exclusion list is deliberately **narrow, not `!/data/**`** the way the
  strikeouts repo does it. That repo keeps its model in `models/`; this one
  keeps the model *and* operator config inside `data/` — `lr_t1.json`,
  `calibration_v*.json`, `fi_park_factors.json`, `manual_odds_overrides.csv`,
  `cluster_demotions.json`. Excluding all of `data/` would have frozen the
  operator's odds overrides and cluster demotions on a long-lived container.
  Only the six churning *outputs* are excluded (plus `backups/`), each verified
  write-only or ephemeral before being added:
  - `picks_2026.csv` — `predictor_loop.py` header: *"the container's local CSV
    is ephemeral — that's fine… Supabase is the source of truth."*
  - `thresholds.json` / `season_record.json` — exports **for the dashboard**;
    the live gates are Python constants, `_write_thresholds_json()` only
    publishes them outward.
  - `pick_changes.csv`, `system_errors.csv`, `boards/`, `diagnostics/` — journals
    and run artifacts, never read back by the predict path.

  Model, gates, staking and the ledger were **not touched**.

**Fixed**

- **The weather archive had been returning nothing, all season, for every
  park (`T8.37`).** `backtest.fetch_weather_season` asked Open-Meteo's
  *archive* endpoint for `{season}-04-01 → {season}-09-30`. In-season, Sep 30
  is a **future** date, and the archive refuses the request outright:
  `"Parameter 'end_date' is out of allowed range from 1940-01-01 to
  2026-08-19"`. Every outdoor park, every run — the Railway log has been
  printing `[weather PIT 2026] error: HTTP Error 400` on a loop.

  **Picks were not wrong.** `fetch_game_weather` falls back to the live
  *forecast* endpoint, so live slates still received real temperature, wind
  and humidity. The costs were elsewhere: ~7 seconds burned per park on each
  cold start — the first predict cycle after a restart ran **86.5s against
  19.2s** for the next — and any *historical* lookup (grading, backtests) got
  league-mean defaults instead of the weather that actually occurred.

  The cache made it self-concealing. `weather_season` is set to **TTL=0** on
  the reasoning *"historical archive doesn't change"* — true of a finished
  season, false of the one in progress. All 22 parks had `{}` stored under
  `<PARK>_<SEASON>` with no expiry, so clamping the date alone would have
  been a **silent no-op on any warm cache**. The fix therefore does both:
  clamps `end_date` to yesterday (the archive only carries settled days, and
  today's games take the forecast path regardless) *and* folds the clamped
  end into the cache key — so the live season re-fetches a one-day-longer
  window each day while finished seasons keep the plain key and stay cached
  forever.

  Verified against the live API: 2026 went **0 → 140 dates** (Apr 1 → Aug 18),
  PHI 2026-08-15 now returns `temp 30.3°C / wind 10.7 km/h / humidity 31%`
  instead of blanks, Tropicana still correctly reports `is_dome=1` with
  neutral defaults, 2025 still returns its full 183 dates on the plain key,
  and the call takes ~1.2s instead of timing out. **234 tests green.**

  No model weights, gates, thresholds or staking logic were touched — this
  restores an input that was already supposed to be arriving.

---

## [2026-08-15b] - First live generation: one crash and three unsourced claims

The operator added `OPENROUTER_API_KEY` and the AI path ran for real for the
first time. Everything below was found by that one run and the four that
followed — the no-key path had been hiding all of it.

**Fixed**

- **`build_facts` crashed with `UnboundLocalError: mc`.** An earlier refactor
  changed `row, mc = n["row"], _mc()` to `row = n.get("row") or {}` and left
  the trailing `del mc` orphaned. Nothing caught it because `analysis()`
  returns before `build_facts()` when there is no API key — so every test and
  every local run had skipped the function entirely. Now covered by
  `test_build_facts_runs_on_a_realistic_night`.

- **The guard read digits only.** Sonnet was given "scoreless in 2 of his
  last 5 starts" and wrote "allowed a first-inning run in **three of his last
  five**" — correct, but reached by subtraction (the one thing the fact sheet
  exists to prevent) and written in words, invisible to a digit scanner.
  Fixed at both ends: the facts now state **both framings** so nothing needs
  inverting, and `_numbers_in` reads spelled-out counts two–twelve. "one" is
  excluded on purpose — it is an article and a pronoun far more often than a
  count. Words appearing in the facts allow themselves, so "per nine" lives.

- **The model invented the causal link between a starter and a lineup**:
  *"the Angels counter that vulnerability"* — about the Angels' **own**
  starter. It had both lineups and both starters but no stated relation
  between them. The top-three fact now names the pitcher they actually face,
  and the prompt says a starter's weakness is exploited by the opposing
  lineup, never his own. Fixed in the fact sheet, not by hoping.

- **It named the ballpark** (*"into Anaheim"*) — true for the Angels, never
  supplied, and wrong the moment a club plays a neutral-site series. Prompt
  now forbids naming venue or city.

**Changed**

- `docs/AI_POST.md` "honest limit" now lists these as concrete examples. The
  class of problem does not go away: numbers are enforced, prose is only
  instructed. Read the paragraph before posting it.

228/228 tests pass. No model weights, gates, staking or ledger columns.

---

## [2026-08-15] - The X post writes itself (OpenRouter), with a fabrication guard

**Added**

- `tools/cards/make_post.py` — the ready-to-paste X post that goes beside the
  night's cards. Published to the same `cards` bucket as
  `backfist_<date>_post.txt`, rendered on `/cards` above that night's images
  with a **Copy** button. Runs on the hourly predict step, only when a card
  was actually drawn.

- **The header is deterministic; the model only writes the paragraph.** The
  play, the units, the side and the price are built in Python from the ledger
  row exactly as the card builds them — an LLM never touches them. Same
  principle as T8.30: the money-facing line keys off what the system actually
  STAKED, and generated prose does not go near it.

- **The model is handed pre-formatted English, not raw numbers.** Every fact
  arrives as a finished fragment ("scoreless 1st in 4 of his last 5 starts"),
  so composing never requires arithmetic. A model that cannot compute cannot
  miscompute. Facts come from columns the predictor already wrote: both
  starters' hand/ERA/WHIP/K9, each one's recent first-inning record, both
  top-threes with on-base, park factor, roof, temperature and wind.

- **`_unsourced_numbers` — every number written back is checked against the
  facts supplied.** A prompt is a request, not a control, and a wrong ERA on
  a public card is the fabrication failure this project has rules about. On a
  violation it retries once with the offending numbers quoted back, then
  falls back to a template with no model in the loop.
  - **Decimals lenient, integers strict**, deliberately. "A 3.7 ERA" for a
    supplied 3.67 is ordinary prose; but the dangerous invention is a COUNT
    ("scored first in 8 of their last 10"), and under rounding rules that 8
    would pass as a rounded 7.58 K/9.
  - **The seed allowlist is one entry.** It began as {1, 3, 5, 9} for "1st
    inning" / "top 3" / "last 5" / "per nine" — and seeding 3 alone let
    "homered in 3 straight games" through. The facts spell those as words, so
    the seeds were never needed. Every seeded integer is a free pass handed
    to a fabricated count.
  - Pinned by `tests/test_post_fabrication_guard.py` (13 tests).

- **Degrades to a deterministic template with no `OPENROUTER_API_KEY`**, and
  says so. Same contract as the Telegram notifier: absent, not broken.
  Model is configurable via `OPENROUTER_MODEL`, with fallback slugs so a
  renamed identifier cannot cost a night its post.

**Changed**

- `storage.buckets` migration `allow_text_posts_in_cards_bucket` — the bucket
  was created `image/png`/`image/jpeg` only and rejected the post with
  `415 invalid_mime_type`. Kept in the same bucket as the images on purpose:
  one publication, one retention job, and the existing anon SELECT policy
  already covers it.
- `prune_cards.py` now matches `.txt` as well as `.png`. Without it the post
  was counted "not a card" and accumulated forever — observed live, where
  08-13's cards were pruned and its post survived as an orphan.
- `load_night` returns the winning `row`, so a consumer needing a column the
  summary does not name reads it without re-implementing the No.1 ranking.

**Fixed**

- The Copy button did nothing when the clipboard was refused. `writeText`
  throws `NotAllowedError` even in a focused, secure context that exposes the
  API — measured on a prod build over localhost. It now selects the post so
  the keyboard shortcut works and the label says so.
- `build_facts` assumed `n["row"]` and `analysis` built the fact list before
  checking for an API key, so the no-key path — the path this runs until a
  key is added — depended on data the template never needs.

**Untouched:** no model weights, gates, thresholds, calibration, staking or
ledger columns. 221/221 tests pass.

---

## [2026-08-14c] - The figures row says what the numbers are

**Changed**

- **"We make it" / "Price needs" are gone.** Operator didn't like either
  label, and the row was carrying three figures where the story needs five.
  It now reads, in order: **MARKET** (the price's implied probability) ·
  **OUR MODEL** · **EDGE** · **STAKE** · **ODDS**. The odds moved out of the
  stake's label into a column of their own.
  - **Only the edge takes a tone colour** — green positive, crimson
    negative. It is the one figure that says whether the bet is any good,
    and the sign carries the meaning with hue only reinforcing it, per the
    palette rule. The model percentage used to be green for no reason.
  - Real minus (U+2212) on the edge, matching `fmt_odds`. A hyphen next to a
    full-width minus is visible when both sit in the same mono row.

- **Columns are sized to their content, not cut into equal slices.** Five
  figures of very different lengths ("+12.3%" against "7.0u") in five equal
  columns leaves a fat gap after the short ones and almost none after the
  long ones; the row read as badly kerned. Each pair is measured and the
  remainder spent as one uniform gutter. The value type also shrinks until
  the row genuinely fits, so a sixth figure could not break it — worst
  plausible case (100.0% / −100.0% / 10.0u / +1500) still fits at full
  46px with 116px of gutter, so the shrink is insurance, not a live path.

**Fixed**

- **The edge is DERIVED, never read from `edge_on_pick`.** Same rule, and
  the same reasoning, as the dashboard's `deriveEdge`: the stored column is
  written by a different process than the one that writes `nrfi_prob`, so
  they drift — 41 rows disagree with a correct recomputation (mean 1.66pp,
  worst 7.75pp) and 2026-06-17 PIT@OAK has the **sign backwards**, stored
  +4.8% on a bet whose real edge at −150 is −0.6%. Publishing that on a card
  is worse than publishing it on a board. Deriving from the two figures
  printed beside it also means the card and the board cannot contradict each
  other: same inputs, same formula, same number.

**Untouched:** no model weights, gates, thresholds, calibration, staking or
ledger columns — the edge is a display derivation, and the stored column is
left exactly as it was. 207/207 tests pass.

---

## [2026-08-14b] - Faces on the card; the bucket stops growing

**Added**

- **Player portraits.** Both starters at 76px and all six top-of-the-order
  hitters at 34px, from `midfield.mlbstatic.com/v1/people/<id>/spots/240` —
  the same CDN the dashboard already uses for headshots. Every ID needed was
  already on the ledger row (`*_pitcher_id`, and `id` inside
  `*_lineup_json`). Drawn disc-first, then the cutout, then a hairline ring:
  MLB's portraits are transparent cutouts, so pasting one straight onto the
  plate leaves a head floating with no ground under it.
  - **Fetched, not vendored** — unlike the 30 club marks. ~750 active players
    and eight faces that change nightly is not a fixed cost. Cache lives in
    the system temp dir, deliberately **outside the repo**: the cron commits
    `data/` every tick and a cache under it would be committed too.
  - `headshot()` never raises. A face that will not load costs the card a
    portrait and nothing else — the disc is still drawn, so the two club
    columns stay aligned either way. (MLB returns a generic silhouette for
    unknown IDs rather than a 404, so even a bad ID degrades to a real image.)

- `tools/cards/prune_cards.py` — retention for the `cards` bucket. A night is
  ~3.3MB across three plates, so an unpruned bucket grows ~100MB/month; two
  nights were already 6.5MB. Runs on the hourly predict step, keeping today
  only (`--keep-days 1`).
  - Safe to automate because a card is a **derived** artefact: it is drawn
    from one ledger row, that row stops changing once graded, and
    `make_card.py --date <d> --publish` redraws any past night. Demonstrated
    in testing — a set deleted by the bug below was restored by re-running it.
  - Two guards. `--require-date` deletes nothing unless the bucket already
    holds a card for that date, so a failed render or a quiet slate leaves
    yesterday's set in place instead of emptying `/cards`. And the filename
    pattern is an allowlist — anything that is not
    `backfist_<date>_<plate>.png` is counted and skipped, never removed.

**Fixed**

- **The prune deleted the night it had just published.** Caught in testing,
  not production. The `--require-date` guard and the retention cutoff were
  computed from **two different clocks**: the guard confirmed a card for
  2026-08-13 existed, then the cutoff came from `now()` — already 08-14 —
  and removed the 08-13 set the same run had just uploaded. The guard passed
  and the delete was still wrong. The window is now anchored to
  `--require-date`, making it "keep N nights ending at the night we just
  published" regardless of when the clock ticks over mid-run. In production
  this only bites at the ET midnight rollover (a step capturing `TODAY_ISO`
  at 11:59pm reaching the prune at 12:00am). Pinned by
  `tests/test_card_prune_window.py` (7 tests, incl. the allowlist).

**Untouched:** no model weights, gates, thresholds, calibration, staking or
ledger columns. 207/207 tests pass.

---

## [2026-08-14] - Cards: the matchup goes on the card, and a cron finally draws it

**Fixed**

- **Cards and Settings were unreachable on a phone.** `.headerActions` was a
  four-item `nowrap` flex row with an intrinsic width of 362px sharing a
  343px content box with the brand. Measured on the live page at 375px:
  History clipped at the viewport edge, **Cards started at x=393 and the
  settings button ran to x=538** — both entirely off-screen. The page has no
  horizontal scroll (`documentElement.scrollWidth === 375`), so there was no
  gesture that reached either one. Actions now take their own full-width grid
  row at ≤600px with tightened pills (~305px of 343px used), and
  `flex-wrap: wrap` on the base rule means a future fourth destination drops
  to a second line instead of vanishing. Verified at 375px (one row, 54px
  spare) and 320px (gear wraps, still nothing off-screen).

- **The lineup was on every card-eligible row and the card never showed it.**
  `_lineup()` parsed `*_lineup_json` with `json.loads` only. The CSV stores
  real JSON, but **Supabase — the DEFAULT source — hands back the Python
  repr** of the same list (`[{'ab': 395, ...}]`, single-quoted), which
  `json.loads` rejects. The rejection was silent, so every Supabase-rendered
  card printed "lineup not posted" with the hitters sitting right there in
  the column. Now falls back to `ast.literal_eval` (literal-only, cannot
  execute) and passes through an already-deserialised list.

**Added**

- **The No.1 card now carries the matchup.** Both club marks, both starters
  (name, throwing hand, ERA, WHIP) and both top-threes (name + OBP) in two
  columns split by a gutter hairline. To make room the display headline drops
  from three lines at ~110px to one line at ~50px — `fit_block`'s box is now
  one line tall on purpose, because it maximises FONT SIZE rather than line
  count and a taller box let it prefer 57px-on-two-lines (orphaning "1ST")
  over 50px-on-one. Date and first pitch moved into the gold eyebrow, freeing
  the 64px line they had under the matchup. **Every value is a column the
  predictor already wrote when it priced the game** — nothing is fetched or
  recomputed, so a card cannot disagree with the row that produced the bet.
  - Lineups are posted on ~94% of bet rows; the other 6% gets the composite
    the model actually used, labelled `Lineup not posted · .338 OBP`. Never a
    guessed nine.
  - Smoke-tested over all 132 ledger dates: 98 renderable No.1s, 34 quiet
    slates, **0 errors**. Longest club name (DIAMONDBACKS), longest pitcher
    names and accented names (José Tena) all fit.

- `tools/cards/logos/` — the 30 club marks as PNG (544KB), vendored by the
  new `tools/cards/fetch_logos.py` from `midfield.mlbstatic.com`, which is
  already the dashboard's headshot source. Vendored rather than fetched at
  render time for the same reason the fonts and plates are: a CDN hiccup on
  the hourly cron must not publish a card with a hole in it. The abbr→id map
  is parsed out of `mlb_first_inning_predictor.py`, not copied.

- `_backfill_display_cols` — Supabase's mirror has 106 columns to the CSV's
  117, and `*_pitcher_throws_hand` is one of the eleven missing, so cards
  printed "3.67 ERA" where they meant "LHP · 3.67 ERA". Fills that from the
  CSV. Deliberately an **allowlist of display-only columns, read-only**:
  nothing named there can reach a price, stake, probability or graded result.

**Changed**

- **The cards auto-update now.** New `Publish Backfist cards (predict)` step
  in `daily.yml` runs on every hourly predict tick, after the lock commit
  (the card prints the stake and price, which the lock commit writes).
  Filenames are date+plate so re-renders upsert the same three objects.
  Fail-soft: a slate with no priced STRONG YRFI play is a normal night and
  logs a `::notice::`, not a failure.
  - **Two things were broken, not one.** No cron ever called the renderer,
    AND `Pillow` was never in `requirements.txt` — so no cron *could* have.
    Fixing either alone changes nothing; both ship here.

**Untouched:** no model weights, gates, thresholds, calibration, staking or
ledger columns were modified. 200/200 tests pass; dashboard prebuild guards
(units, Kelly parity) pass.

---

## [2026-08-13h] - The sizing_prob column: the ledger records what sized the bet (T8.35)

**Added**

- `sizing_prob` — new ledger column (CSV + Supabase `picks_2026`,
  migration `t835_add_sizing_prob` applied): the probability the Kelly
  sizer ACTUALLY USED when it last wrote `units_risked`. Stamped only
  inside `tracker._size_row_stake`'s Kelly branches — the stamp and the
  stake are the same read of the same cell, by construction. **Blank
  means "this stake is not probability-sized"** (flat fallback, LEAN
  notional, orphan heal) — so the stake-drift exemption class is now
  visible in data too.
  - A Kelly **refusal** stamps as well (0 units from exactly this p at
    this price — auditable like a stake). The T8.18 keep-alive 0.5u
    floor deliberately does NOT re-stamp: the floor isn't
    probability-sized; the last honest stamp stands until commit.
  - On the incident row this would have read `yrfi_prob=0.6687` beside
    `sizing_prob=0.5864` — the splice visible on the row itself instead
    of via Railway log forensics. With layers 1–2 live the two should
    now always match; divergence = a new bug announcing itself.
  - Plumbing (each hop is a place T8.23 taught us a column dies):
    `tracker.FIELDS` (appended LAST — never reorder a CSV schema),
    `log_picks` preserve list (or every predict tick wipes it),
    `new_row` literal, `supabase_writer.PICKS_CONVERTERS` (numeric),
    `_PRESERVE_ON_BLANK_FIELDS` (a predict-path mirror must not blank
    the sizer's stamp — membership also auto-enrolls it in
    `sync_csv_from_supabase._SYNC_COLUMNS`, so it travels to the GHA
    CSV exactly like `units_risked`), `db/schema.sql`,
    `db/migrate_csv_to_supabase.py`.
  - Historical rows: NULL/blank (honest — we did not record what sized
    them; do NOT backfill from `stake_drift` replays, which reconstruct
    the rule, not the actual read).
  - Dashboard: deliberately no display yet — the column is for the
    ledger, forensics, and the nightly replay; a UI surface can follow
    if ever wanted.
  - Tests: `tests/test_sizing_prob_stamp.py` (10 — stamp on projection/
    commit/refusal/incident-shape, blank on flat/LEAN/PASS, keep-alive
    non-restamp, re-derive co-movement, end-to-end wiring). Full suite
    200 passed. Live sync dry-run with the new SELECT: clean.

## [2026-08-13g] - Game-time-change alert (T8.35 follow-on)

**Added**

- `tracker._game_time_change_candidate` + `_notify_game_time_change_telegram`
  — ops Telegram when a slate row's scheduled game time MOVES pre-lock.
  Every pre-lock protection is denominated in minutes before the lock
  (game time − 60); on 2026-08-13 the 2:10→1:10 ET correction silently
  deleted an hour of that runway and the No.1 committed inside a lineup
  outage twelve minutes later. This makes the next such compression
  visible the moment it lands.
  - **Batched:** one ping per detection run even if a schedule rebuild
    moves five games at once. Loud header (⏰⚠️ LOCK EARLIER) when any
    move shrinks a lock; calm header otherwise. Body shows old→new,
    direction, the NEW lock time with runway remaining, and the row's
    pick context — worded per the T8.16/T8.17 rule: a pre-lock stake
    prints as "projected … NOT LOCKED", never as a commitment.
  - **Silences, deliberately:** sub-5-minute jitter; doubleheader game-2
    churn (MLB lists G2 at G1+5min and corrects it later — the routine
    cleanup the post-lock `allow_update` exists for); placeholder→real
    transitions ("After Game 1" resolving is a time appearing, not
    moving); locked rows (a change there is a delay, not a compressed
    lock); started/graded rows.
  - `game_time_change` registered in `_DEDUP_WINDOW_M` (12h) same
    commit; key carries each game's NEW time so a second move the same
    day pings through the window.
  - Detection hooks into `log_picks`' merge (fires on whichever host
    sees the transition first; dedup collapses the pair). Advisory only
    — wrapped so a notify failure can never block the ledger write.
  - Tests: `tests/test_game_time_change_alert.py` (14 — 2 fires, 7
    silences, 5 notifier: batching, headers, key signature,
    projection-vs-locked wording). Full suite 190 passed.

## [2026-08-13f] - T8.35 layer 2 shipped: the probability that sized a bet travels with the bet

**Added**

- **Bet adoption sync** (`tools/sync_csv_from_supabase.py`) — at the
  moment a GHA-side CSV first learns `bet_placed=Y` from Supabase (the
  N→Y transition), the committing host's probability set
  (`nrfi_prob`/`yrfi_prob` + raws) and pick identity
  (`pick_side`/`pick_strength`/`pick_label`) now sync atomically with
  the money columns. The T2.25 freeze that engages on the next
  `log_picks` run then preserves the values the bet was actually sized
  from — not this host's unrelated local compute. `stake_drift`'s
  invariant (stake == rule(published probability, price)) becomes true
  by construction on every host, not just the sizing one.
  - **The splice this kills:** 2026-08-13, Railway committed the No.1
    coherently as (58.6%, 2u); GHA's sync pulled the money but not the
    probability, froze its own pre-outage 66.87% beside Railway's 2u,
    and mirrored the splice back over Supabase — the published record
    then claimed a 7u probability next to a 2u stake.
  - **Strictly N→Y.** Frozen rows (already `Y`) never re-adopt — a later
    Supabase writer must not silently edit the probability under a
    settled bet (T2.23/T2.25 class). Unplaced rows keep their own fresh
    compute — pre-lock, local is the honest number (T8.18). Blank remote
    values never overwrite. Adoptions print a
    `[sync] BET ADOPTED …` line for the workflow log.
  - Pick identity rides along because the committing host may have
    committed STRONG while this host's fresh compute had demoted the
    row — adopting the stake without the identity would manufacture a
    `bet_placed=Y` LEAN/PASS row, violating LEAN-is-track-only.
  - Tests: `tests/test_bet_adoption_sync.py` (6 — the incident replayed
    and killed, identity ride-along, blank-skip, frozen-row protection,
    unplaced-row protection, one-shot adoption). Full suite 176 passed.
  - Verified read-only against live Supabase (`--dry-run --days 3`):
    39 rows, routine updates only, zero adoptions — the path arms only
    on commits the CSV hasn't seen.
  - No workflow change needed: both sync call sites (predict + grade
    jobs) already run before their compute steps.

**T8.35 status: all three layers + the row heal are now shipped.**
Layer 1 (sticky lineups) defends the input, layer 2 (this) makes the
published record coherent by construction, layer 3 (alarms + nightly
stake-drift replay) watches both. The audit item is closed with a
standing watch: the next MLB card-flap will be announced by the
`lineup_regression` ping, and any residual stake/probability mismatch
pings the same night from the grade cron.

## [2026-08-13e] - T8.35 layer 1 shipped: sticky lineups (operator-approved model-input change)

**Added**

- **Sticky lineups** (`mlb_first_inning_predictor.py`) — a lineup card,
  once seen, is only ever REPLACED, never forgotten. When the live
  `fetch_top3_batters` returns an empty side, the predict run refills it
  with the last posted card's batter IDs from the ledger row's own
  `*_lineup_json`; a NON-empty fetch always wins, so a real scratch or
  bench shuffle replaces the memory instead of being masked by it. The
  sticky IDs flow through the same `current_season_top3_stats` /
  `top3_ops_vs_hand` calls as a live card — batter stats stay fresh; only
  the ROSTER is remembered.
  - **Why the ledger row and not the data/cache layer:** the cache dies
    with its host — gitignored (GHA runners start empty every run) and
    reset on every Railway auto-deploy (hourly+). Proven on the incident:
    Railway redeployed at 11:58 ET with an empty cache and its first
    fetch landed inside the outage. The row survives both lifecycles via
    git and is the SAME memory on every host.
  - Sides running on memory are tagged `*_top3c_source="lineup_sticky"`
    (honest ledger; drift monitor already classes this column noisy-
    categorical; dashboard doesn't read it; Supabase passthrough). The
    LINEUP PENDING guard treats both tags as "card present". The layer-3
    alarm now distinguishes BRIDGED (`lineup→lineup_sticky`, calm body:
    "sticky memory kept the last posted card") from REGRESSED
    (`→team_fallback`), with the new state in the dedup key so an
    escalation mid-window still pings.
  - **Replayed against 2026-08-13:** at the 12:06 ET cycle the fresh
    fetch loses the home side; sticky restores Meidroth/Grichuk/Vargas
    (the exact withdrawn card, asserted by ID in
    `tests/test_sticky_lineups.py::test_the_2026_08_13_outage_is_bridged`)
    → the model keeps its 66.9% inputs → quarter-Kelly sizes ~7u.
  - **Rollout:** behind `NRFI_STICKY_LINEUPS=enabled`, default OFF in
    code, now set on BOTH hosts (Railway variable + `daily.yml`
    predict-step env) — both, because a non-sticky host writing
    team_fallback into the row mid-outage destroys the memory the sticky
    host needs. Kill switch: delete the Railway var + env line; rows
    self-heal to `lineup` on the next successful fetch. No CSV repair
    needed either way.
  - Tests: `tests/test_sticky_lineups.py` (13 — incident replay by real
    IDs, scratch-replaces-memory, chain persistence via `lineup_sticky`
    source, partial/malformed-memory refusals, flag contract) plus 2 new
    alarm-interplay tests. Full suite 170 passed.

**Changed**

- `tracker._notify_lineup_regression_telegram` — bridged-vs-regressed
  message variants + per-transition dedup keys (see above).

## [2026-08-13d] - T8.35 layer 3 shipped: two alarms so the next outage is loud

**Added**

- `tracker._notify_lineup_regression_telegram` — ops Telegram the moment a
  side that WAS lineup-sourced regresses to team/league fallback on a
  pre-lock STRONG row. Fires on the transition only (once the regressed
  row is written the stored source is no longer "lineup", so a cycle
  can't re-fire); silent when the bet is already placed (stake frozen,
  nothing to protect), when the game has started, when the row is
  terminally graded, or when neither side of the merge is STRONG — the
  regression demoting the fresh verdict below STRONG still fires, since
  the pick vanishing off the board IS the event. Called from `log_picks`
  after the T8.18 re-derive so the ping quotes the stake the row now
  actually projects; baseline captured per loop iteration (a stale
  `existing` from the previous game was the obvious trap). Deliberately
  NOT gated by the №1-only policy — money-integrity class, like
  strong_orphan_no_odds. On 2026-08-13 it would have fired at ~12:06 ET,
  four minutes before the lock window opened.
- `tools/stake_drift.py --notify` + nightly wiring in `daily.yml` (grade
  job) — the T8.18 PART 3 replay existed but ran only when a human typed
  it; the 2026-08-13 2u-vs-7u splice sat invisible until the operator
  asked. Now it replays every settled slate since the era floor every
  night and pings ops Telegram on any violation surviving the exemptions.
  Event key is the violating-set signature: a known violation stays
  silent inside the 24h window, a NEW one changes the signature and pings
  through it. Read-only contract unchanged — it reports, never heals.
- Both event types registered in `_DEDUP_WINDOW_M` in the same commit
  (`lineup_regression` 12h, `stake_drift` 24h) — per the standing rule,
  fourth-time's-the-charm on the unregistered-type bug class.
- `tests/test_lineup_regression_alert.py` — 13 tests: 4 pin when the
  alarm fires (incl. key format and body forensics), 6 pin the silences
  (placed bet / no STRONG / recovery direction / graded / started /
  nothing regressed), 1 pins the raise contract, 2 cover the stake-drift
  notifier (signature key, never-raises). Full suite 156 passed.

**Deferred (need operator approval — model input / money path)**

- Sticky lineup cards (layer 1) and size-from-published-value (layer 2)
  remain proposals in `docs/proposals/one_source_stake.md`. A
  `sizing_prob` ledger column and a game-time-change alert are listed
  there as candidates. Nothing behavioral shipped.

## [2026-08-13c] - T8.35 root cause found: MLB withdrew the lineup card (investigation only)

**Changed (docs — diagnosis corrected, no code or data touched)**

- The 2026-08-13b entry below and commit `b95e1905`'s message attributed
  the 2u/7u splice to host drift ("Railway computed 58.6% from stale
  lineups while GHA published 66.87%"). **Disproven the same day:**
  - Railway deploy `3f26d0ef` (15:04–15:58Z) printed **66.9%** every
    cycle — Railway was never behind.
  - GHA's own 16:47 run log shows its FRESH compute at **58.6%**
    (">> STRONG YRFI | YRFI 58.6%") beside the frozen 66.9% board line.
  - The actual mechanism: the CWS lineup card posted ~15:03Z, was
    **withdrawn from MLB's schedule/lineups feed 15:58→16:06Z**, and
    returned by ~17:03Z unchanged (actual first-pitch top-3 =
    Meidroth/Grichuk/Vargas = the withdrawn card). Both hosts tracked the
    flapping source faithfully; the bet locked at 16:11:33 — 90 seconds
    into a lock window that the same-hour game-time correction
    (2:10→1:10 ET) had moved an hour earlier — sizing on the outage-
    degraded 58.6% → 2u, while T2.25 froze the published 66.87% over it.
  - The 7u heal's standing improves: 66.87%'s inputs matched the real
    lineup; 58.6% was a transient data outage, not a model downgrade.
- `AUDIT.md` T8.35 rewritten with the corrected cause (correction trail
  preserved in the entry). `docs/proposals/one_source_stake.md` rewritten:
  host consolidation demoted to ops hygiene — it would NOT have prevented
  this (a single host would have coherently staked 2u on a published
  58.6%); the targeted fixes are sticky lineup cards, sizing from the
  published value, and a lineup-regression alarm. All still DRAFT,
  awaiting operator approval.

## [2026-08-13b] - The No.1 staked 2u where the rule said 7u (T8.35)

**Fixed (data)**

- `data/picks_2026.csv` + Supabase — 2026-08-13 CIN@CWS (pk 824561),
  STRONG YRFI @ -120, corrected **2u → 7u**; `profit_loss_units`
  **+1.667u → +5.833u**; the edge triple recomputed from the row's own
  published probability (`edge_on_pick` 0.0409 → 0.1232).
  - Operator report: *"the number one pick today, in which it won, only
    was staking two units."* Correct — the rule says 7u on the 66.87%
    the system published.
  - **Cause (T8.35):** GHA and Railway each ran the model and disagreed
    — GHA 66.87% (published), Railway **58.6%** (sized the bet, being the
    only host that can reach DraftKings). Quarter-Kelly is right in both;
    the row carried one host's probability beside the other's stake.
  - Applied by `tools/heal_2026_08_13_split_brain_stake.py` — a
    deliberate, journaled money-path write, per
    `data/stake_drift_exempt.csv`'s rule that `stake_drift.py` reports
    and stops. Every figure comes from a shipped function
    (`kelly_stake_units`, `_apply_edges_to_row`, `_calc_pnl`); none is
    hand-arithmetic. Writes CSV **and** Supabase (`patch_picks`, so the
    grade and odds already in Supabase are untouched — the 2026-05-05
    wipe used a full mirror). Idempotent.
  - The edge triple moves **with** the stake deliberately: leaving
    `edge_on_pick=0.0409` (which encodes p=0.5864) next to a 7u stake
    (p=0.6687) rebuilds the exact inconsistency the heal removes — T8.18's
    "all three edge columns move together or none do".
  - Verified after: `tools/pl_calc.py` → +5.833u, stored == recomputed,
    no drift. `tools/stake_drift.py --date 2026-08-13` → 0 violations.
  - **Note this is the opposite call to 2026-08-09**, where DET@OAK and
    SD@ARI were *preserved* in `stake_drift_exempt.csv`. Those two were
    over-staked winners, so correcting would have cost ~4.97u; this one
    was under-staked on a winner. Direction is the operator's call each
    time; both are now on the record.

**Added**

- `docs/proposals/one_source_stake.md` — **DRAFT, nothing shipped.**
  Five options for making the stake and the published probability come
  from one place, with a recommended sequence (diagnose Railway's lineup
  staleness → coherence guard at commit → `sizing_prob` column → unify on
  one model host) and the testing bar each must clear. Awaiting operator
  review; the money path is untouched until then.

**Deferred**

- The cause itself. T8.35 stays OPEN. The row is healed; the architecture
  that produced it is not. Its hard prerequisite — why Railway, running
  12× more often, held *older* lineups than GHA — is still unexplained.

## [2026-08-13a] - Telegram: `calibration_drift` re-fired every single day

**Fixed**

- `tracker._DEDUP_WINDOW_M` — registered `calibration_drift` at 7 days.
  Operator report: *"i keep getting telegram notifications saying
  calibration drift."*
  - `tools/calibration_drift_monitor.py` already builds a WEEK-keyed event
    key (`calibration_drift:<iso_week>:<drifting-buckets>`) precisely so a
    slow-moving 30-day condition pings about once a week. That key was
    doing nothing: `_notify_event_dedup_check` looks back
    `_DEDUP_WINDOW_M.get(event_type, 5)` **minutes**, and the type was
    never registered — so the daily cron was always outside the 5-minute
    fallback and re-sent the same alert every day the condition held.
  - The window now matches the key the monitor already computes. A new
    bucket joining the drift changes the signature, so a genuinely
    worsening picture still pings immediately.
  - **Third instance of this exact unregistered-type bug** — `discord_board`
    (2026-08-06, re-posted THE BOARD to paying subscribers all night) and
    `watchdog` (2026-08-07, ~12 pages/hour). The dict's own comments warn
    about it twice. Treat "new event type" and "add to `_DEDUP_WINDOW_M`"
    as one step.
  - No model, gate, staking or ledger code touched; delivery cadence only.

## [2026-08-13b] - `calibration_monitor`: flagged a bucket, then denied it

**Fixed**

- `tools/calibration_monitor.py` — `is_drift_persistent()` required a bucket
  to be flagged in the 14d **and** 30d windows specifically. `flagged` needs
  `n >= MIN_BUCKET_N` (30) bets in ONE probability bucket, and a fortnight
  rarely has 30 STRONG bets in total (2026-07-31..08-13 had 25 across four
  buckets), so the 14d window could essentially never qualify — the alert was
  structurally unreachable.
  - Now: flagged in **>= 2 windows with the drift pointing the same way**,
    which is what the module docstring has always promised ("two consecutive
    flagged windows"). The stats quoted are from the longest flagged window,
    so an alert cites its best-sampled evidence.
  - The verdict line no longer prints *"No persistent drift detected. All
    buckets within variance bounds"* underneath rows the report just marked
    `***`. When single-window flags stand it now names them and says plainly
    that they are not yet persistence.
  - Surfaced by the 2026-08-13 fortnight review: the 0.55–0.60 bucket is
    **43.5% actual vs 57.5% stated over 60d at n=85 (−14.0pp)** — the
    biggest, best-sampled miscalibration in the system — and the tool was
    reporting no drift. It still does not page (one window is not
    persistence), but it now says so out loud instead of contradicting
    itself.
  - Read-only monitor. No model, gate, staking or ledger code touched, and
    the change fires no new alert on current data.

## [2026-08-13c] - `clv_tracker`: ROI was a Kelly numerator over a flat denominator

**Fixed**

- `tools/clv_tracker.py` — `show()` computed `ROI = sum(profit_loss_units) / n`.
  The numerator is the ledger's **quarter-Kelly** settlement (stakes of 3–9u
  since 2026-07-27); the denominator is the **bet count**. Correct while every
  bet was a flat 1u, wrong the moment quarter-Kelly went live — it overstated
  return by roughly the average stake, printing `+259.2%` and `+375.7%` in the
  2026-08-13 review.
  - ROI and its bootstrap CI are now computed on a **flat 1u** settlement, the
    same basis `edge_reality_check.py` and `market_signal_check.py` use, so all
    three tools' ROI figures are finally comparable.
    `market_signal_check.py` never had the bug — it builds a flat-1u P&L first.
  - Real money is still reported, in a `booked` column labelled as the
    quarter-Kelly stakes actually placed. Both bases print side by side; the
    header says which is which.
  - Flat payout derives from the price's implied probability
    (`payout == (1-imp)/imp`, exact), so no second parse of the odds string.
  - Verified against an independent implementation: the fortnight's
    "big edge 10pp+" bucket reads ROI +38.6% / flat +5.8u, matching
    `market_signal_check.py`'s +39% / +5.8u on the same population.
  - Read-only reporting script; no prediction path imports it.

## [2026-08-12c] - Backfist Bets social cards: generator + /cards page

**Added**

- `tools/cards/make_card.py` — renders the night's №1 play as a square
  1080×1080 PNG for X, under the public brand **Backfist Bets** (not "NRFI
  Terminal", which stays internal). Backdrops are AI-generated art plates
  (`tools/cards/plates/`, generated once via Higgsfield `nano_banana_pro`)
  that deliberately contain **no text**; every character is drawn by Pillow
  from ledger data, so a published figure cannot drift from the dashboard.
  Fonts vendored in `tools/cards/fonts/` (Archivo/Archivo Black display,
  JetBrains Mono figures); logo in `tools/cards/brand/`. Brand colours are
  sampled from the logo (`#007030` green, `#E0C060` gold).
  - №1 selection **ranks explicitly** (YRFI, `bet_placed=Y`, priced, lowest
    `nrfi_prob` — mirroring `pl_calc.select_top_picks`) and uses
    `tracker._row_is_nights_top_pick` only as a cross-check, because the
    gate fails open by design and returned a PASS row when used as the
    finder. Verified against `pl_calc --top-pick` (both pick COL@ARI for
    2026-08-12).
  - Copy rules: YRFI reads "Either team scores in the 1st" (side-neutral —
    "Yes run" beside a matchup misread as a bet on the away team); model %
    is always paired with the price's break-even; full club names parsed
    from `dashboard/lib/team-names.ts` at run time (never a second copy);
    "Tonight's/Today's" follows first pitch.
  - `--publish` uploads to the new public-read Supabase storage bucket
    `cards` (service key writes, anon SELECT-only via storage policy;
    created by migrations `create_social_cards_bucket` +
    `allow_anon_list_cards_bucket`).
- `dashboard/app/cards/page.tsx` + `components/CardsView.{tsx,module.css}` —
  `/cards`: lists the bucket newest-night-first and offers **Save / Share**.
  On phones this fetches the PNG and opens the native share sheet (Web Share
  API with a `File`), because cross-origin `<a download>` silently navigates
  instead of saving on iOS; desktop falls back to a same-origin object-URL
  download. The page is a *viewer only* — one renderer exists, in Python, so
  the preview can never disagree with the posted image. Newest night loads
  eagerly; the archive lazy-loads. Nav link "Cards" added in
  `DashboardShell`.

---

## [2026-08-12b] - The calibration chart had no endpoint to fetch (T8.34)

**Added**

`dashboard/app/api/calibration/route.ts`. `<ReliabilityCurve />` has been mounted
in `DashboardShell` since the 2026-07-28 chart spec and self-fetches
`/api/calibration`. **The route was never written** — `git log` has no record of
it in any commit. The fetch 404'd, the component's `.catch()` set state to
`"error"`, and its render returned `null`, which is deliberate: *"a missing
diagnostic is a zero-pixel outcome, not an error surface"* (2026-08-05
redesign). So an approved chart failed **silently for two weeks**; the only
evidence was a 404 in the browser console, spotted while verifying T8.33.

The route is read-only — no pick, stake or ledger column is touched. It reads
through `loadLedgerRows`, the same paginated Supabase-then-CSV reader `/history`
and the No.1 tracker use, so it cannot disagree with them about the season and
cannot hit the PostgREST 1000-row cap that has already truncated `pl_calc` and
the date picker.

What it serves, for the current season:

- **bins** — every graded first inning binned by the model's P(YRFI) at width
  0.05. Bins under 20 games are dropped rather than plotted (`droppedBins`
  reports how many; 1 of 9 today), per InsightCharts' "never invent a number".
  Wilson bounds are deliberately not sent — the component derives them, and one
  implementation cannot disagree with itself.
- **gate** — `1 - strongYrfiP` from `thresholds.json` (0.58), the STRONG YRFI
  boundary expressed on the chart's axis.
- **betRegion** — aggregate over every graded game at or above the gate,
  computed from raw rows rather than by summing plotted bins, so a suppressed
  thin bin still counts. The top bin is where the STRONG plays live and is the
  likeliest to be too thin to draw on its own.
- **breakEven** — mean implied probability of the DK prices actually paid on
  placed YRFI bets. Never the flat -110 fallback: that price was never real.

Deliberately every graded game, not just the bets — a calibration curve drawn
only over games we bet is drawn over a selected sample and would flatter itself.

**What it now says**, and it is worth reading before it surprises anyone: in the
band we bet, the model claims 61.8% and reality delivered 57.1% across 468
games, against 55.9% needed to break even. The 95% interval (52.5–61.5%)
contains break-even, so **the edge is still not proven** — the same conclusion
`2026-06-04_edge_investigation` and `2026-08-01_kelly_refinements_dead` already
reached, now visible on the homepage instead of buried in a memory. This is not
in tension with `/history`'s 48−21 / 69.6%: that figure is the top-ranked play
of each night, a selected subset; this is every game above the gate.

Verified on a PRODUCTION build: `/api/calibration` returns 200, the chart renders
with its own caveat text, and the homepage load shows no 404s.

---

## [2026-08-12a] - The board reported 5u of profit that was never won (T8.33)

**Fixed**

Four dashboard surfaces recomputed a pick's stake with `stakeUnitsFor()` instead
of reading the one the ledger recorded. `stakeUnitsFor` is pure quarter-Kelly and
knows nothing about the 15u/day cap `tracker.kelly_stake_units` applies on top —
so on a cap-bound night every one of them answered with the stake a play WANTED
rather than the one that was placed.

Found while explaining the T8.32 near-miss to the operator: the 2026-08-11 card
read **"22.00u sized across 3 STRONG picks · +16.64u"** while the board's own
stake chips on the same screen read 8.00 + 1.00 + 6.00 = **15.00u**, and
`tools/pl_calc.py` — the canonical answer per CLAUDE.md — read **+11.640u**.
COL@ARI was recomputed at its uncapped 8u after the cap had trimmed it to 1u.

**The exposure figure was the smaller half of this.** A total captioned "at risk"
overstating real exposure by 7u is bad; a card reporting **5 units of profit that
was never won** is a different order of defect. `tonightFromBoard` derives P&L
from the same stake, so the fabricated size propagated straight into the money.

Fixed in all four, each now LEDGER FIRST — recompute only when nothing was
recorded:

| surface | was | now |
|---|---|---|
| `lib/reconcile.ts` `tonightFromBoard` | recompute (fed the 22.00u **and the +16.64u**) | ledger |
| `TonightsActionCard` `summarizeSides` | recompute | ledger |
| `TonightsActionCard` `extractPlays` | recompute | ledger |
| `app/brief/page.tsx` | recompute | ledger |

Two `> 0` tests also became `!= null` (`TopPlayHero`, ×2) and `BoardRow`'s stake
chip gained an explicit refusal branch. `units_risked` has **three** states and
collapsing them into two is T8.30 wearing a new hat: `> 0` staked, `== 0` the
system deliberately refused, `null` never sized. Only the third is a missing
figure — sending a recorded zero into the recompute prints a stake for a bet
nobody placed. Reachable today via a cap-zeroed row; the board now shows
"stake none".

Each surface carried a comment claiming it matched `BoardRow`'s StakeChip, which
went ledger-first on 2026-07-30. None of them had followed. The stale argument
for recomputing — that pre-2026-07-30 ledger rows were sized under the old
bankroll × Kelly% rule — was already settled then: 1.00u is what was staked on
those nights, and printing a stake nobody placed is the worse failure.

**Verified against a PRODUCTION build** (`npm run build` + `next start`, not dev
— see the `dashboard_verification_trap` memory). 2026-08-11 now reads "15.00u
sized across 3 STRONG picks · +11.64u", matching both the chips and `pl_calc.py`
exactly. 2026-08-10's refused TB@OAK reads "stake none · 0.00u sized". No console
errors; `/brief` and `/history` unaffected.

Presentation only — no change to staking, the cap, `tracker.py`, or the ledger.

---

## [2026-08-11e] - The No.1 keeps its stake when it locks last (T8.32)

**Fixed**

The 15u/day risk budget is handed out in LOCK order, and games lock at their own
first-pitch-minus-60. T8.19 already sorts each import batch best-bet-first — but
two picks three hours apart are never in the same batch, so a weak 6:45 PM game
takes its stake before a strong 9:40 PM game is even a candidate.

Found while explaining to the operator why COL@ARI moved from 8u to 1u on the
2026-08-11 board. That was the cap working as designed — CHC@WSH took 6u at 5:45
PM, TEX@LAA took 8u at 8:38 PM, leaving 1u — but it was harmless only because
the No.1 happened to be the 9:38 PM game. TEX@LAA and COL@ARI were both 71.28%;
the No.1 badge was decided by price (-135 vs -140), and the budget by a
**two-minute** gap in first pitch. Swap the start times and the published No.1
goes out at 1u.

That is not primarily an EV problem — reordering the whole budget best-first was
measured at +0.6u/season with a CI spanning zero. It is a PRODUCT problem: the
No.1 is the play that is published, sold and bet by subscribers.

`kelly_stake_units` now takes `reserve_units`, and `_size_row_stake` holds the
night's No.1 stake back from any pick committing ahead of it. New helpers
`_game_ident`, `_select_nights_top_pick`, `_top_pick_reservation`.

Deliberately narrow, and deliberately inert:

- **No.1 only.** Reserving for No.2 as well would have zeroed CHC@WSH on
  2026-08-11 — a bet that won. That turns "trim the last pick" into "skip the
  early pick", a far bigger change than the one asked for.
- **Commit only.** A pre-lock projection stays a pure function of (probability,
  price); letting the reservation touch it would make a published stake
  order-dependent, which is rule R1 and the P0-1 oscillation class.
- **Releases the moment the No.1 commits** — via `_allocated_idents` for the
  same batch and `bet_placed=Y` across batches. Reserving on top of a stake
  already inside `_daily_committed` would under-size every later pick by the
  No.1's whole stake.
- **Fails open.** Any unreadable ledger, unparseable probability or missing
  price reserves 0.0, i.e. exactly the pre-T8.32 behaviour. Never fabricate a
  price to reserve against.

**Measured** (replay of every settled slate since the current sizing rules went
live on 2026-07-30, driving the real `kelly_stake_units`):

| | |
|---|---|
| days replayed | 10 |
| days whose stakes change | **0** |
| simulated realised total, before vs after | +23.33u vs +23.33u |

A pure no-op on what actually happened. Under stress — force the No.1 to be the
last game to lock on each real slate — it earns its keep on 2 of the 5
multi-pick days: 2026-07-31's No.1 would have been published at **4u instead of
8u**, and 2026-08-01's at 6u instead of 7u.

13 tests added to `tests/test_money.py`, including the counterfactual with the
reservation disabled (the No.1 does get 1u), a no-op regression pinning the real
2026-08-11 slate, and a guard asserting `_select_nights_top_pick` and
`_row_is_nights_top_pick` crown the same game across generated slates — the two
No.1 rules are separate functions on purpose (the live notification gate was not
worth destabilising) so the test is what keeps them from drifting.

Caps themselves are UNCHANGED: still 10u per bet, 15u per day, quarter-Kelly.
Model, gates and calibration untouched.

---

## [2026-08-10k] - The override fired on commits that merely mentioned it (T8.29)

**Fixed**

The gate matched `[gate-override]` anywhere in a commit message, so **any commit
that discussed the token disabled the gate**. Both `3c394956` (which introduced
the escape hatch) and `377993d4` (which documented it) carry the string in their
bodies — each would have silently overridden the gate it was shipping. So would
any future changelog entry, revert, or doc quoting it.

Found by running the audit command this playbook had just documented, and
noticing it returned two commits that never overrode anything. The documented
command was grepping the same way the workflow was.

Now matched on the **commit subject only** — the line you type after `-m`. Prose
in a body cannot trip it; an override must be typed deliberately where it is
visible in `git log --oneline`. The audit command greps formatted output rather
than `git log --grep`, for the same reason.

Verified against real commits: the three most recent feature/doc commits all
correctly read as **no override**, while a genuine `... [gate-override]` subject
still matches.

**Generalises:** a control keyed off free text will eventually fire on text
written *about* it. Documentation quotes the token; changelogs quote it; reverts
quote it. Match somewhere prose does not go.

---

## [2026-08-11d] - Pay only for prices we use: ask DK, not the region (T8.31)

**Changed**

Operator challenged the credit maths — *"if we only used 2 credits, but it
allowed you to pull the odds for all the games already, you may be wrong."*
The challenge was right to make and produced a **7x** further saving.

**What the docs settled.** Cost is `[unique markets RETURNED] x [regions]`,
**per request**. The bulk `/odds` endpoint is one request for the whole slate;
the per-event endpoint is one request each — which is where 1-credit-per-game
came from. Tested directly: the bulk endpoint **cannot** serve this market —
`HTTP 422 INVALID_MARKET: Markets not supported by this endpoint:
totals_1st_1_innings`. So per-event is forced, and that part of the model held.

**What the docs also revealed, and this is the win:** *"responses with empty
data do not count towards the usage quota"*, and up to 10 bookmakers counts as
one region. We were sending `regions=us`. DraftKings posts a first-inning line
a median 63 min out — but FanDuel, BetMGM and BetRivers post hours earlier — so
every early fetch **returned those books, cost a credit, and was then discarded**
by the `--book draftkings` filter.

Measured on CLE@DET at T-280:

| request | books returned | cost |
|---|---|---|
| `bookmakers=draftkings` | none | **0** |
| `regions=us` | fanduel, betonlineag, betmgm, betrivers | **1** |

`odds_params()` now sends `bookmakers=<book>` instead of `regions` whenever
`--book` is set, normalising a display name (`DraftKings`) to the API key
(`draftkings`) — a mismatch there returns nothing, all evening, silently.
`regions` remains the path for a deliberate multi-book diagnostic pull, the one
case where paying for other books is the point. The local `--book` filter stays
as well: a server-side parameter is not something to stake the record on.

**This inverts the design.** The window no longer has to be surgical to be
cheap, so the two-phase `120:115,75:55` collapses back to a single generous
`120:55`. Everything before DK quotes is free; we start paying exactly when the
data becomes worth having. It also captures the price the **moment** it
appears rather than at the next window boundary, so the ~11% of games with an
early line get real movement history.

| stage | credits/day | % of 20k tier |
|---|---|---|
| original continuous window | 373 | 56% |
| two-phase (11c) | 101 | 15% |
| **DK-only + `120:55`** | **~50 expected** | **~7%** |

3 new tests (132 total) pinning the cost model — notably that `regions` must
not creep back in alongside a `--book` request, since that silently restores
the paid path on every pre-posting fetch.

**Untouched:** model, gates, staking, ledger, `strongYrfiP` (0.42).

---

## [2026-08-11c] - Two-phase odds polling: 373 -> 101 credits/day (T8.31)

**Changed**

The odds fetch now runs in **two phases** instead of one continuous 120-minute
window, cutting tonight's slate from **373 credits to 101** (56% -> 15% of the
20,000/month tier). Operator is adding a strikeouts model on the same key, so
the headroom is the point.

Measured over 244 placed bets, and the numbers are why the shape changed:

| | median | distribution |
|---|---|---|
| DK first posts a price | 63 min out | 87% in the 60-120 band, only 11% earlier |
| the price a bet is PLACED at | 57 min out | 89% inside 60 min |

So the money lives in a narrow band around the T-60 lock, and a continuous
window spends most of its credits watching a market DraftKings has not opened.

- `75:55` — **the money.** Several attempts so one miss cannot leave the slate
  unpriced at commit. A single shot at T-62 would miss ~45% of games, because
  the median first post is T-63.
- `120:115` — **the movement probe.** Only the ~11% of games priced early can
  drift at all; for the rest the price exists about six minutes before we bet
  it, which is why 245 of 263 bets showed zero open-to-lock change. One credit
  per game keeps that finding measurable rather than assumed.

Everything cut is a fetch that could not have changed a placed bet: the removed
spend is the T-115..T-75 dead gap and the post-lock stretch, where
`market_*_odds` is already frozen by T2.23.

`--windows HI:LO,HI:LO` (env `ODDS_API_WINDOWS`, default `120:115,75:55`),
with `parse_windows()` rejecting a reversed pair loudly — `55:75` would match
nothing and silently leave every slate unpriced. `--within-minutes` is kept as
the honest single-band form for a manual whole-slate pull.

7 new tests (129 total, up from 124), including one that pins the >3x saving
against a simulated 15-game evening slate so the claim cannot rot.

**Verified live** on Railway's key at 1:41 PM ET: 15/15 events correctly
skipped, **0 credits spent**.

**Untouched:** model, gates, staking, ledger, `strongYrfiP` (0.42).

---

## [2026-08-11b] - The Odds API wired into the loop (T8.31)

**Added**

`step_fetch_odds_api()` in `workers/predictor_loop.py`, between scrape-dk and
import-odds. It writes the same `data/odds/dk_<date>.csv` the importer already
reads, so the import path is unchanged — one step, not a pipeline rewrite.
Position matters both ways: after scrape-dk so a direct-scrape file (on any
host where that still works) is merged onto rather than clobbered; before
import-odds so a fetch is turned into ledger rows on the same cycle instead of
sitting five minutes closer to the lock.

**Off by default** behind `PREDICTOR_ODDS_API=enabled`, mirroring
`PREDICTOR_SCRAPE_DK` — the money path cannot change because a deploy happened,
only because an operator set the variable, and it can be killed from Railway's
dashboard without a deploy.

**The window is the cost control, not a nicety.** Every event costs 1 credit
and this loop runs every 5 minutes, so fetching the whole card each cycle would
spend ~180 credits/hour on markets that do not exist yet — DraftKings posts a
first-inning line a median 63 min before *its own* first pitch. New flags:

- `--within-minutes N` — only events within N minutes of first pitch (loop: 120)
- `--skip-started` — a started game can never be priced usefully
- `--merge` — a windowed fetch holds only part of the slate, and `import_odds`
  re-reads the whole file every cycle; overwriting would delete prices already
  captured for games that locked. A re-fetched game **replaces** its earlier
  row rather than appending, because the importer applies every matching row in
  file order and a duplicate would let the *older* price win.
- `--min-credits N` — a floor (loop: 50) so a runaway cadence cannot reach zero
  mid-month and silently unprice every remaining slate.

`select_events_in_window()` and `merge_rows()` are extracted rather than inline
specifically so they can be tested — both decide what gets spent and what
reaches the ledger. 11 regression tests in `tests/test_odds_api_fetch.py`
(124 total, up from 113).

**Verified live** against Railway's key: at 12:17 PM ET with first pitch 6:41
PM, the window correctly skipped 15/15 events, spent **0 credits**, exit 0.

**Two risks retired for 2 credits.** The plan *does* serve
`totals_1st_1_innings` (4 books quoted it), and **DraftKings is in the feed** —
it quotes h2h on 15/15 games. So DK's absence from the first-inning market at
midday is timing, not coverage. That independently corroborates the 08-10
finding that locking earlier is impossible: the same late posting explains both.

**Credit budget.** Operator is moving to the 20,000/month tier. At ~15 games
polled across a 2-hour pre-game window that is ~360 credits/day (~10,800/month),
which leaves room for both an opening and a lock-time price — so CLV tracking
survives, unlike the free tier's one-shot-per-game.

**Untouched:** model, gates, staking, ledger, `strongYrfiP` (0.42). No API key
is in any tracked file or in git history (verified both).

---

## [2026-08-11a] - DK now blocks Railway; `--book` guard added (T8.31)

**Fixed (infrastructure) — DIAGNOSIS, the repair is not shipped yet**

Odds stopped capturing entirely. 2026-08-11: **15 games, 0 priced**, last
successful capture 10:56 PM ET on 08-10. Root cause: **DraftKings now 403s
Railway's egress IP.** Railway was the only working odds source, so the whole
odds path is down.

Ruled out, in order, because each has bitten before:

- **Not a dead/disabled scraper.** It runs every cycle and is refused.
- **Not a missing `curl_cffi`.** The logged `HTTPError: HTTP Error 403: ` is
  produced by exactly one library — curl_cffi's `raise_for_status` formats
  `f"HTTP Error {status}: {reason}"`, verified against its source. `requests`
  emits `403 Client Error: Forbidden for url: …` and the urllib fallback logs
  a `repr` and has no warmup. So the Chrome TLS impersonation **is** running
  and is being blocked anyway.
- **Not the 08-05 subcategory rotation.** Sub 20150 still returns a full valid
  payload (15 events / 15 markets / 60 selections) and `extract_odds` parses
  15 clean rows — *from a residential IP*. That failure looked like 200-with-
  zero-markets; this is an outright refusal.
- **Not fixable by redeploying.** Four redeploys since the last good capture
  (04:31, 04:50, 05:56, 09:50 UTC), all still blocked — so unlike 2026-08-06
  this is not one unlucky ephemeral IP. Same failure class as the Contabo box.

**Added**

`tools/fetch_odds_api.py --book <name>` — keep exactly one sportsbook.
**Required on the money path.** Without it the tool emits every US book into
one file, and `tracker.import_odds` applies every matching row in FILE ORDER
against the same pick, so **the last book in the file silently becomes the
ledger's price**. The published No.1 record is a DraftKings-priced series
(stakes move ~17% per 10c; the win-loss line itself changes at ±20c because
refused nights become bets), so an arbitrary basis is a different product
wearing this one's label. Matches the aggregator's `title` or `key`,
case-insensitively; a book that is not quoting yields **nothing**, never a
fallback to another book. A multi-book file now prints a loud
DO-NOT-IMPORT warning and the tool refuses to suggest importing it.
6 new self-test assertions; `--self-test` passes.

**Costed** (real slates, trailing 14d): average **14.6 credits** per
full-slate fetch (1 to list events + 1 per game). One fetch/day = **437
credits/month**, inside the 500 free tier with ~13% headroom — but that is a
single attempt per night with no redundancy. Two/day = 874, three/day = 1,311.
This supersedes the 8/6 "~$59/mo like-for-like" estimate, which predates any
measurement of real slate sizes.

**Not done, deliberately:** the loop is NOT wired to the new source, and no
key was committed. Wiring it touches the money path and needs operator sign-off.

**Untouched:** model, gates, staking, ledger, `strongYrfiP` (0.42).

---

## [2026-08-10j] - A refused No.1 is not a play, and never was a result (T8.30)

**Fixed**

TB@OAK locked tonight with quarter-Kelly having refused it — `units_risked` 0,
`edge_on_pick` −0.9% — and the subscriber channel got a padlocked
**"TONIGHT'S №1 PLAY"** carrying **"Don't take worse than -130."** beside a
quoted price of **-145**. The message contradicts itself two lines apart, but
only for a reader who does the arithmetic; everything with visual weight says
BET. The stake line was missing rather than zero because `if stake:` treats
`0.0` as absent.

**This is T8.18 in the two places it was never applied.** `build_board` grew
the "**This is not a bet.**" branch on 2026-08-06 and the other two message
builders did not, so one fix covered one of the three surfaces that needed it.

**The settle ping was the worse half**, and was ~40 minutes from firing. It
would have published "✅ THE №1 WON" above a running record that *excludes*
the game — both `select_top_picks` and `dashboard/lib/top-pick.ts` drop a
night whose stake is zero — implying an inclusion that never happened. On a
loss it fails the other way: a loss the record never absorbs.

- `is_refused()` — the three states callers were collapsing into two:
  `>0` staked → speak in the imperative; `==0` refused → say so and publish
  no price; `None` unpriced → the ladder message, a real product path and
  **not** a refusal.
- `build_no_play()` + a new `discord_noplay` broadcast. Operator's call: a
  refused No.1 gets its **own** message rather than a softened play message,
  because a headline naming the night's play should only exist when there is
  one. It publishes no `pass_price` — a "don't take worse than" line is a
  betting instruction. Separate event key so neither shape dedupes the other
  away, and it says "NO PLAY ON THE №1" rather than "NO PLAY TONIGHT" when the
  card still has a staked play (T8.16 with the sign flipped).
- Settle ping routes a refused row to **NO ACTION** and states plainly that
  the record is unchanged by it.
- `_fmt_units()` — `f"{0.5:.0f}"` is `"0"`, so a floored 0.5u stake could
  print "Stake 0 units" and read as the refusal above.
- `discord_noplay` registered in `tracker._DEDUP_WINDOW_M` **in the same
  commit**; an unregistered type inherits the 5-minute fallback and
  republishes ~12×/hour (the 2026-08-06 board incident).
- 16 regression tests, `tests/test_refused_top_pick.py`.

**The model, the gates, staking and the ledger are untouched.** The ledger had
this right already: `tools/pl_calc.py --top-pick` reads **47-21, +88.89u** at
quarter-Kelly with tonight correctly absent.

Commit `f8407483`.

**Deferred**

*Locking bets earlier to capture the edge* — measured, and the data says no.
DraftKings does not post first-inning lines early enough for it to be a lever:
across 263 placed STRONG YRFI bets the median gap between the **first**
captured price and the **bet** price is **6 minutes**, and only **17 (6%)**
ever had a price more than two hours before lock. On those 17, locking at the
first price would have gained **+0.26pp** of edge per bet and 12 of them never
moved at all. Tonight was the rare exception (−120 → −145 in the last two
hours), and ~45% of its lost edge was the model correctly revising itself as
lineups posted — which is what the T-60 lock exists to wait for. Revisit only
if the odds source changes; see the memory `odds_source_strategy`.

---

## [2026-08-10j] - The gate override, documented -- and what "blocking" actually means

**Added**

PLAYBOOK section 4a: the `[gate-override]` escape hatch — the exact command,
what it does not do (it never hides the finding; the report and
`VERDICT: BLOCKED` are still printed and logged), legitimate uses vs smells,
the `git log --grep` audit command, and the two run IDs proving it works in
both directions.

**Fixed — an overstatement in my own docs from earlier today**

Section 4 said the gate "blocks". **It does not stop anything.** Verified
2026-08-10: this branch has no protection rule and no required status checks
(`branches/<branch>/protection` → 404). A red gate does not reject the push,
does not stop Vercel deploying, and does not stop the predictor cron picking
the code up. The change is live either way. What the gate provides is a loud,
permanent, attributable *record*, not a veto.

Saying otherwise would have been the exact T8.28 failure — a doc promising
protection that does not exist — committed on the same day I removed the last
one. Section 4a now states the limit first, and names what an actual veto would
require (branch protection with `model gate / predictions moved?` as a required
check) plus why it has deliberately not been enabled: ~30 automated pushes a
day means a required check turns any CI outage into a frozen money pipeline.

Also corrected: an earlier draft suggested a later empty commit could carry the
override. It cannot — a commit touching no files matches none of the workflow's
`paths:`, so the workflow does not re-run at all.

---

## [2026-08-10i] - The model gate now BLOCKS (T8.29)

**Changed**

Operator reversed the warn-only decision made earlier the same day. The gate
now fails the build.

**What blocks:** aggregate Brier worse, **any single season** worse, or the
mixed pattern (helps one era, hurts another) that is the signature of a fit to
one era. A change that improves every season **passes** — real model work is
not obstructed. Verified across six cases including the one that matters most,
a genuine improvement passing, and the one the three splits exist for: an
aggregate win with 2025 worse still blocks.

**It also blocks a real `data/thresholds.json` edit, and that closes a hole I
had left open.** A threshold is the cut applied *after* the probability — move
`strongYrfiP` and every probability the gate measures is byte-identical while
what gets **bet** changes. The gate would have reported "PREDICTIONS UNCHANGED":
true, and deeply misleading. It now blocks and asks for a human.
`writtenAtUtc` is excluded, because that timestamp is the **only** thing every
`auto: predict` tick changes in that file — watching it would have fired the
gate ~20×/day on nothing and, once blocking, handed the money branch twenty
daily chances to go red for no reason.

**The escape hatch: `[gate-override]` in a commit message.** The original worry
about blocking — a genuine fix at 7pm with games starting — is real, so the
bypass needs no secret, no dashboard and no second person, and it lands in git
history forever. A gate nobody can bypass under pressure gets deleted; one
bypassed loudly survives. The override scans the whole pushed commit range, not
just HEAD.

---

## [2026-08-10h] - The model gate now covers all three splits (T8.29)

**Changed**

Holdout goes from 524 games (2026 only) to **3,728 across 2024 + 2025 + 2026**,
by committing the two repaired historical files. Reported **per season as well
as in aggregate**, because an aggregate hides the exact failure three splits
exist to catch.

Demonstrated on a synthetic 0.1% shift: aggregate Brier said **BETTER**, while
2025 alone said **WORSE**, and the gate printed `!! MIXED ACROSS SEASONS`. The
2026-only version would have said "better, ship it".

| season | n | brier |
|---|---|---|
| 2024 | 1,689 | 0.24827 |
| 2025 | 1,515 | 0.24846 |
| 2026 | 524 | 0.24807 |

**`_ptfix` only, verified on disk rather than trusted.** `truepit` and
`truepit_pit` carry season-final ERA/FIP/OBP — on opening day the file already
knows how a pitcher finishes the year — and `_pit` *reads* like "point-in-time"
while being the leaked one, a trap that has misled two prior sessions. Measured
share of pitchers whose ERA varies within the season: `truepit` 0.0%/0.0%,
`truepit_pit` 0.0%/0.0%, **`truepit_ptfix` 62.6%/64.8%**. Gating on leaked data
would make the gate confidently wrong — the leak is worth ~+0.011 AUC, about a
third of this model's entire edge over a coin flip.

**Repo cost 13 MB, and it cannot reach the Vercel bundle** — `copy-data.mjs`
uses an allowlist (`boards/`, `picks_<year>.csv`, and a handful of named files)
and never copies `data/backtests/`. So this cannot repeat the 250 MB deploy
breakage of 2026-08-05.

Two file-format differences handled explicitly: the 2024/2025 files carry no
`actual_result`, so the outcome derives from `fi_away_runs`/`fi_home_runs` (0
blank rows); and they predate the umpire feature, so
`home_plate_ump_nrfi_rate` is imputed at `LEAGUE_NRFI_RATE = 0.50`, exactly
what `two_stage_model._ump_rate_for` falls back to. Both sides of a comparison
get identical inputs, so the before/after remains valid.

Coverage floor raised 450 → 3,300. Re-verified: determinism, detection, unique
keys across seasons, and the floor firing on a simulated feature rename.

---

## [2026-08-10g] - The model gate, rebuilt honestly (T8.29)

**Added**

`tools/model_gate.py` + `.github/workflows/model_gate.yml`. Re-scores a fixed
committed holdout of **524 real 2026 games** with the model code and artifacts
from before a push and after it, and reports whether any prediction moved.

| verdict | meaning |
|---|---|
| PREDICTIONS UNCHANGED | the change provably did not move the model — the common, most useful answer |
| PREDICTIONS MOVED | per-game moves + Brier / log-loss deltas |

**Its own workflow, not a job in tests.yml, and that is the point.** `tests.yml`
carries `paths-ignore: data/**` because the automation pushes ~30 data commits a
day — but **the model weights live under `data/`** (`lr_t1.json`, `lr_b1.json`,
`calibration_v2.json`). A weights change is the most consequential edit anyone
can make here and in tests.yml it would have matched `paths-ignore` and
triggered nothing. This workflow uses a precise allowlist instead.

**Warn-only, by the operator's decision.** ~30 automated pushes a day, and a red
gate during a live slate could block a genuine fix under time pressure. It
cannot stop you shipping a regression; it makes sure you know you are.

**What it does NOT prove, stated in the tool itself.** 2026 only — the repaired
2024/2025 files are not in git and cannot be rebuilt in CI (they come from a
232 MB gitignored cache). Committing them is 13 MB and safe from the Vercel
bundle (`copy-data.mjs` allowlists, and never copies `data/backtests/`), so this
is an upgrade path, not a wall. A metric win on ~500 games is weak evidence —
see `2026-08-03_gate_sweep_artifact`. The three-split protocol is still yours.

**Re-scores from FEATURE columns, never the verdict columns.** `pick_side` /
`nrfi_prob` / `lambda_total` in the backtest files are retired-Poisson artifacts
at AUC ~0.50; reading them as "what the model would have done" is this repo's
most-repeated mistake.

**Verified by making it fail, not by reading it.** Determinism (same tree twice
→ same fingerprint); detection (an in-memory 0.1% shift → different fingerprint,
no file touched); restore; a full worktree simulation of the CI flow; and the
coverage floor (a renamed feature → hard stop, because a gate that quietly stops
guarding is the T8.28 failure again).

**Fixed**

PLAYBOOK sections 1.2 and 3 still routed to `tools/daily_shadow_report.py`,
`data/diagnostics/shadow_<DATE>.csv` and `shadow_summary.csv` — all deleted
2026-05-06, all missed by the T8.28 sweep. Rerouted to the gate and to
`pl_calc`. Section 4 and KB.md updated so they describe the gate that now
exists rather than the one that doesn't.

---

## [2026-08-10f] - The docs promised a pre-merge model gate deleted three months earlier (T8.28)

**Fixed**

`.github/workflows/shadow_gate.yml` (T4.7) was removed **2026-05-06** in
b125aa45 ("v2.1 lock-in: archive V2 toggle, remove V3 + shadow surface
entirely") — deliberately, along with `tools/daily_shadow_report.py` (T4.4),
`tools/v2_t42_shadow.py`, `ShadowDeltaCard.tsx` (T4.9) and
`data/diagnostics/shadow_summary.csv`. The code removal was clean. The docs
never caught up and kept describing all five as live for three months.

**The dangerous one was PLAYBOOK section 4**, an entire procedure headed *"I'm
about to merge a PR that touches the predictor"*, which stated the gate "will
run automatically" and would fail the PR under `delta_pl < -2.0u`. Anyone
following it merged a predictor change believing a model-quality check had
passed. Nothing ran. A stale "you're covered" is worse than no doc, because it
stops you looking.

Rewritten to say what actually exists. `tests.yml` runs on every push and proves
the money **plumbing** is self-consistent — Python ↔ fixtures ↔ TypeScript — and
says nothing about whether the model improved; a change that quietly worsens
predictions passes it green. Section 4 now states plainly that **the operator is
the gate**, and routes to the three-split out-of-sample protocol CLAUDE.md
already calls non-negotiable. Also corrected: KB.md's six-layer list (3 of 6
dead), PLAYBOOK's diagnostic-stack table (3 dead rows), SELF_HOSTED_RUNNER's
cutover list. The surviving V2.1-vs-V2.2 track (`tools/v21_shadow_predict.py` →
`data/diagnostics/v21_v22_disagreements.csv`) is now labelled observability,
not a gate.

**Correction to `[2026-08-10e]`:** that entry said CLAUDE.md carried the stale
citation. It does not — neither CLAUDE.md nor AGENTS.md has ever mentioned
shadow_gate. The citations were in docs/KB.md, docs/PLAYBOOK.md and
docs/SELF_HOSTED_RUNNER.md.

**Generalises:** deleting a subsystem is only half the change — grep the docs
for its filenames in the same commit. A removed guard that is still documented
becomes a false assurance, which is worse than never having had it.

---

## [2026-08-10e] - Swept the other workflows for the T8.26 gating defect (T8.27)

**Fixed**

Audited all four workflows for the pattern T8.26 fixed. Two more instances.

**`tests.yml` / `dashboard` job — the other half of the same guard.** `Units
guard` and `Kelly + pass-price parity` are independent (one type-checks a
generated probe file, the other diffs a committed fixture against Python;
neither reads the other's output — verified in both `.mjs` sources). Sequenced,
a units-guard failure silenced the parity guard — the money one. This was missed
when T8.26 landed because the two halves of the stake-math protection live in
**different jobs**: `money` checks the fixtures against live Python, `dashboard`
checks the TypeScript against those same fixtures. Fixing one left the other
silenceable. *Rule: when you decouple one guard, go find its other half.*

**`backup.yml` — Snapshot → Prune → Commit are peers, not stages.** Two teeth:
a snapshot failure skipped the prune, and the canonical snapshot failure is a
full disk — the exact condition pruning would relieve, so the gate disabled the
cleanup precisely when needed; and a prune failure skipped the commit,
discarding the snapshot the job exists to make. The commit is load-bearing for
the prune too: `git add data/backups` stages the deletions, so a prune whose
commit never runs achieves nothing that outlives the runner.

**Measured while fixing it: tracked `data/` is 155.1 MB, of which 126.0 MB is
`data/backups`.** Against the 250 MB limit that already broke every deploy on
2026-08-05, the prune is not housekeeping — it is what keeps deploys alive, and
it was gated behind a step that can fail. (Note for future measurement: `git
ls-files data | xargs du -ch | tail -1` reports ~11 MB and is wrong — xargs
splits the list and only the final batch's total is printed.)

Both later steps now run on `!cancelled()`, gated only on the checkout.
**Accepted trade, recorded so nobody reverts it:** a mid-way snapshot failure
now commits a partial backup. Deliberate — a partial backup restores more than
none, the job still goes red, and the alternative discards the prune as well.

**Not a defect: `daily.yml`.** Checked and clean by construction — 15 of its 24
steps wrap their work in `set +e` + `|| echo "::warning::"`, doing T8.26's job at
the shell level, so a broken drift monitor cannot stop `Commit data changes`. The
three steps that can fail are genuine prerequisites. `runner_watchdog.yml` is a
single step.

**Doc drift found in passing:** `.github/workflows/shadow_gate.yml` is cited as
running automatically before a predictor merge, and does not exist. *(Corrected
in `[2026-08-10f]`: the citation is in docs/KB.md, docs/PLAYBOOK.md and
docs/SELF_HOSTED_RUNNER.md — **not** CLAUDE.md, which never mentioned it. This
entry named the wrong file.)*

---

## [2026-08-10d] - Actions bumped off deprecated Node 20

**Changed**

All seven `actions/*` pins across the three workflows that use them, onto the
current majors — GitHub had begun annotating every run with a Node 20
deprecation warning and force-running them on Node 24 anyway:

| | was | now |
|---|---|---|
| `actions/checkout` | v4 | **v7** (backup.yml, daily.yml, tests.yml ×2) |
| `actions/setup-python` | v5 | **v7** (daily.yml, tests.yml) |
| `actions/setup-node` | v4 | **v7** (tests.yml) |

Warnings only, nothing was failing — but Node 20 is on its way out, and these
would eventually have stopped running rather than merely complaining.

**The one real hazard was the self-hosted runner, and it was checked, not
assumed.** `daily.yml` (the predict cron) and `backup.yml` both carry
`runs-on: ${{ vars.RUNNER_LABEL || 'ubuntu-latest' }}`, and `RUNNER_LABEL` is
set to `self-hosted` — so they run on the Contabo box, not a GitHub runner.
Every release from `checkout@v5` / `setup-python@v6` / `setup-node@v5` onward
requires **runner ≥ v2.327.1**, a floor GitHub-hosted runners meet invisibly and
a self-hosted one need not. Too-new an action fails the whole job, which on
`daily.yml` means no picks. Runner `vmi3065305` measured at **2.336.0** before
the bump — clear. Recorded in [docs/SELF_HOSTED_RUNNER.md](./docs/SELF_HOSTED_RUNNER.md)
with the one-line query, since it is invisible from the workflow files.

**Breaking changes reviewed per major, not skipped.** `checkout` v5→v7 and
`setup-python` v6→v7 break nothing beyond the runner floor. `setup-node` v5 adds
automatic caching when `package.json` declares `packageManager` — `dashboard/package.json`
declares no such field, and the workflow sets `cache: npm` explicitly, so the
new behaviour does not engage. Every input in use (`fetch-depth`,
`python-version`, `cache`, `node-version`, `cache-dependency-path`) survives
unchanged. The diff is seven version strings and nothing else.

---

## [2026-08-10c] - The parity guard no longer depends on the tests passing (T8.26)

**Changed**

`.github/workflows/tests.yml` — the money job now runs `Fixtures still match
Python` FIRST, and runs `Money-path tests` regardless of its verdict:

    - name: Money-path tests
      if: ${{ !cancelled() && steps.deps.outcome == 'success' }}

A step with no `if:` carries an implicit `success()`, so by default every step
is gated on the ones before it. That is right for a build pipeline, where a
later stage consumes an earlier stage's output, and wrong for independent
checks, where it lets the first thing to break decide whether anything else
gets to speak. T8.25 was that bug firing: a sys.path accident in test
collection — carrying no information about the money math — suppressed the
stake-parity guard for four pushes while CI said only "failing".

**Swapping the order alone would have mirrored the bug**, not fixed it; a parity
failure would then have hidden the tests. The gate between two peer checks is
the defect. Order only decides which one you read first.

`!cancelled()` rather than `always()` because this workflow sets
`cancel-in-progress` and the hourly automation supersedes runs routinely, so
`always()` would keep working on an abandoned run. The `steps.deps` clause keeps
the one real prerequisite: under a failed `pip install` neither check can reach
a meaningful verdict, and a wall of import errors would bury the real cause.

**Verified by making it fail, not by reading the YAML** — the whole lesson of
T8.25 being that this class of defect passes inspection. Scratch branch with the
parity STEP forced to `exit 1` (never a fixture; no money file was touched),
[run 31415202287](https://github.com/joey11600/MLB-first-inning/actions/runs/31415202287):

    X  Fixtures still match Python   -> exit 1
    ✓  Money-path tests              -> 97 passed
    X  job conclusion                -> failure

The second step ran on a failed first step, and the job still went red — a real
failure is surfaced, not masked. Branch deleted after the run.

**Generalises:** ask of any two CI steps whether the second CONSUMES the first
or merely FOLLOWS it. If it merely follows, the implicit `success()` is a silent
single point of failure.

---

## [2026-08-10b] - CI red for four pushes, and the parity guard went with it (T8.25)

**Fixed**

`pytest tests/` died at collection with:

    ERROR collecting tests/test_allocation_order.py
    E   ModuleNotFoundError: No module named 'tracker'

`tests/` has no `__init__.py`, so pytest's prepend import mode puts `tests/` on
`sys.path` and never the repo root. `import tracker` resolved only because six
of nine test modules each carried a private `sys.path.insert(...)`, and because
collection is ALPHABETICAL — whichever module sorted first silently fixed the
path for every module after it. Three modules never had the line and passed on
that ordering alone. `test_allocation_order.py` (cb5d5c88) lacks it and sorts
FIRST, so nothing had run yet, and the other 92 tests never executed.

**Green locally, red in CI, same commit.** `python -m pytest tests/` prepends
CWD and reports 97 passed; bare `pytest tests/` — what the workflow runs — does
not. That is the whole difference, and it is why this shipped.

**The exposure was the step behind it.** `Money-path tests` runs before
`Fixtures still match Python`, so `parity_fixtures.py --check` never ran on any
of the four red pushes (cb5d5c88, 47bd7db1, 6c31c460, 7590fabd — all the same
error, verified in each log). That is the only guard that catches the dashboard's
stake math drifting from the Python that sizes real bets; `check-kelly-parity.mjs`
compares against a committed fixture and never invokes Python (T8.12). It was
unexercised across a stake-allocation change (T8.19), a ledger fix (T8.23) and a
notification fix (T8.22). Run on the fixed tree, both clean — 21402 Kelly cases
and 121 pass-price cases match Python. Nothing was hiding behind the failure.

Fixed in `tests/conftest.py`, imported before any test module in its directory,
so it covers every test whether or not the author thought about it — the same
reasoning as the `autouse` production-write guards already living there.
Verified by running each of the three previously-unguarded modules ALONE under
the no-CWD form (5 / 4 / 12 passed), which the private copies cannot explain;
full suite 97 passed under both runner forms. The six private copies stay:
redundant, harmless, and they keep `python tests/test_money.py` working directly.

**Generalises:** a per-file import fixup that only works in collection order is
a latent failure in every file that lacks it, and alphabet picks which one
exposes it. Shared setup belongs in `conftest.py`. Separately — a CI step
ordered behind a fragile one inherits its outages silently.

---

## [2026-08-10a] - The skip's reach-back fetched from a remote that does not exist (T8.24)

**Fixed**

`should-build.sh` compares against `VERCEL_GIT_PREVIOUS_SHA`, and when that
commit has fallen out of Vercel's shallow clone it fetches it back. That fetch
was:

    git fetch --quiet --depth=1 origin "$PREV" 2>/dev/null || true

**There is no remote named `origin` in Vercel's build container.** Measured on
the SIBLING strikeouts project the same day, whose equivalent script — after
being changed to print its failures instead of discarding them — reported, three
times over:

    fatal: 'origin' does not appear to be a git repository

That checkout carries the objects and the refs and no configured remote at all.
Every `git fetch ... origin ...` was dead on arrival, and `2>/dev/null || true`
meant it said so to nobody.

**Inferred here, not observed here.** Same platform, so very likely the same,
but this project's own build has never printed it — the error went to
`/dev/null`. The new `remotes configured: [...]` line answers it on the first
build log that reaches the fetch branch. Until one does, that claim stays open.

**Why this mattered more here than there.** Strikeouts fell through to BUILDING,
which is only expensive — it burned 91 CPU-hours over Aug 7-10 doing it. This
script falls through to the NARROW COMPARISON against `HEAD^`, which is the one
comparison the file exists to prevent: a code commit buried under a data commit
in the same push is invisible to it, gets skipped, and never deploys, with
nothing turning red on a live money dashboard. T8.6 removed that comparison from
the happy path; the dead fetch quietly reinstated it as the failure path.

**Not observed firing here.** This repo pushes ~21 commits a day against
strikeouts' ~125, so the last build stays inside the shallow window and the
fetch is rarely needed. Sampled build logs all show `comparing against LAST
BUILD` on the direct path. It was a latent hole, not an active fault — and
because the error went to `/dev/null`, had it fired there would be no trace.

`remote_candidates()` now yields every configured remote and then the provider
URL rebuilt from `VERCEL_GIT_REPO_OWNER` / `VERCEL_GIT_REPO_SLUG`, and each is
tried in turn. Not "pick one": *a remote exists* and *that remote can serve this
object* are different claims, and an expired token baked into a checkout URL
would otherwise drop us straight back onto the silent narrow path this fix
exists to close. The remote list, every candidate tried, and every failure are
printed.

**The fetch must fail, not hang.** It runs under `GIT_TERMINAL_PROMPT=0` with
credential helpers disabled, so a missing credential comes back as a printed
error instead of blocking on a password prompt nobody can answer. An
`ignoreCommand` that hangs stalls the deploy — worse than either verdict.

**The `--depth=1` fetch-by-SHA shape was always right** and is kept. One object,
no history walk. GitHub does serve a reachable raw SHA, anonymously — confirmed
here with the credential helper disabled, not assumed.

**It must be the full 40-character SHA.** An abbreviated one is parsed as a ref
name and returns `couldn't find remote ref` — indistinguishable from "GitHub
refuses raw SHA fetches", and it produced a false negative in this fix's own
verification until the harness was corrected. `VERCEL_GIT_PREVIOUS_SHA` is
full-length, so production is unaffected; it is written down because the next
person to test this by hand will hit it.

Verified against a harness reproducing the production shape — `--no-local
--depth=5` clone, `git remote remove origin`, invoked from `dashboard/`, with a
baseline outside the shallow window — on real history:

    control  pre-fix, code in gap -> SKIPPING build                (0)  <- the bug
    A  data-only gap of 9  -> fetch ok -> SKIPPING build           (0)
    B  code commit in gap  -> fetch ok -> BUILDING, names tracker.py (1)
    C  no remote, no env   -> NO REMOTE AVAILABLE -> narrow, loud  (1)
    D  broken remote + env -> fails fast, cascades, LAST BUILD     (1)
    E  working remote      -> used directly, no regression         (1)

The control is the point. A normal clone passes on the broken script *and* the
fixed one — it has an origin, and a local remote serves any SHA. Only the
production shape tells them apart, which is how an earlier diagnosis of this bug
went wrong.

**The reach-back depends on this repo being public.** The derived URL is fetched
anonymously; there are no credentials in the build container. If the repo is
ever made private, every reach-back fails and every data commit starts building
again — loudly, now that failures print rather than vanish.

The narrow fallback is deliberately left in place rather than converted to an
unconditional build — operator's call, and with the reach-back working it should
no longer be reachable in practice. 97 tests pass.

## [2026-08-09d] - Thirteen columns were wiped on every predict tick (T8.23)

**This is why CLV has been unmeasurable all season. It was never a capture gap.**

`log_picks` rebuilds each row from a dict LITERAL and copies a short `preserve`
list back over it. `csv.DictWriter` substitutes `""` for any FIELDS key the
literal lacks — so thirteen columns, in neither place, were blanked on every
predict tick, roughly twelve times a day.

Twelve of the thirteen just lost data. The thirteenth repaired itself with the
WRONG value, which is why nobody caught it: `_apply_odds_to_row` re-seeds the
opening price only when it is blank —

```python
if not (row.get("opened_nrfi_odds") or "").strip():
    row["opened_nrfi_odds"] = nrfi_odds
```

— so the wipe made it re-seed from the CURRENT scrape every cycle. The "opening"
line was never the opening line; it was the most recent one. **1191 of 1277
priced rows (93.3%) have `opened == market`, and it was still 93.3% over the
first nine days of August.** A market that appears never to move.

The other nine belong to other tools — the v21 shadow model and the last-10
top-3 splits — and were being erased by a process that does not own them.
`clv_pct` is recomputed on every odds import, so it lost nothing on its own;
it is preserved with the group rather than left for someone to re-litigate.

### Fixed

All thirteen added to `preserve`. Only the pre-game branch leaked — the
locked/graded branch already copies everything outside `allow_update`.

`tests/test_preserve_columns.py` fails if any FIELDS column is neither set by
the literal nor preserved, so the next column added has to pick a side. (The
first pass at that check used a lowercase-only pattern and wrongly flagged
`away_top3_ops_vs_oppHand` / `home_top3_ops_vs_oppHand` as damaged; both are
set at `tracker.py:879` and are fine. The test now matches case.)

### What this does NOT fix

**The 1191 historical rows cannot be repaired.** The true opening price was
never stored anywhere — each tick overwrote it — so there is nothing to
recover it from. 86 rows carry genuine line movement and remain valid.
CLV becomes measurable on new rows from the next predict tick forward, which
means the first honest read on it is roughly a month away. Any CLV figure
covering earlier dates is measuring the price against itself.

---

## [2026-08-09c] - The №1-only alert crowned every pick, not one (T8.22)

The policy shipped 2026-08-05. Measured against `notifications_log` rather
than reasoned about: **both multi-pick slates since then fired a "BET LOCKED"
alert for every pick.**

| slate | alerts fired | games |
|---|---|---|
| 2026-08-05 | **2** | TB@COL + WSH@PHI |
| 2026-08-06 | **2** | WSH@PHI + SD@ARI |
| 2026-08-07 | 1 | LAD@ARI *(single STRONG pick)* |
| 2026-08-09 | 1 | LAD@ARI *(single STRONG pick)* |

Two for two. On 08-06 the one that pinged FIRST (WSH@PHI, 6:05 PM) was **4
confidence points worse** than the play that pinged later. Discord published
the correct №1 that night, so the two surfaces disagreed.

### Cause

`bet_placed="N"` is overloaded and the rival scan collapsed the two meanings:

* **DECLINED** — edge gate, daily cap, or `apply_cluster_demotion`. Never a
  candidate. All three write a non-positive stake.
* **PENDING** — T2.58's pre-lock state, "will commit at its own lock". A live
  play carrying a POSITIVE stake.

Games lock at their own first-pitch-minus-60, so when one flipped to "Y" every
other STRONG pick was still "N" — and the scan discarded them all as non-bets.
Each game in turn looked around, saw an empty field, and crowned itself.

### Fixed

New `tracker._is_declined_not_pending`: a rival is skipped only when it is `N`
**with no positive stake**. That is the same discriminator
`tools/end_of_day_check.py` already uses to tell a pending row from a refusal,
so the two agree by construction. Applied to both the self-check and the rival
scan. Fails open on an unparseable stake — losing a real №1 is worse than one
extra ping, matching the gate's overall stance.

Replayed against the real ledger: **08-05 → TB@COL only; 08-06 → SD@ARI only.**
One ping, the right game, both nights, in both lock orders. Note 08-06 now
alerts *later* in the evening, which is correct — the best play locks later.

No dashboard change needed: `top-pick-rank.ts` filters to STRONG and has never
had a commit-state filter, so this moves tracker INTO line with the board
rather than away from it. `tests/test_top_pick_gate.py` pins both real shapes,
both lock orders, the tie-break, and that a demoted rival cannot silence the
night.

---

## [2026-08-09b] - Allocation order, the manual-odds twin, and a test that wrote to production

Three follow-ons to T8.18, all operator-directed.

### Fixed — the budget now goes to the best bet, not the first one (T8.19)

`import_odds` handed out the 15u daily budget in DraftKings' file order, so on
a cap-bound slate a pick's PUBLISHED STAKE depended on its row position. The
real 2026-07-31 four-game slate:

```
file order   CWS@TB 8u   KC@COL 5u   MIL@LAA 2u   DET@OAK 0u
reversed     CWS@TB 4u   KC@COL 5u   MIL@LAA 5u   DET@OAK 0.5u
```

Same games, same prices, same probabilities — and MIL@LAA is either a 2u bet or
a 5u bet depending on nothing to do with the bet. The weakest play could take
money the strongest one then could not have. `kelly_stake_units`' docstring had
already named this as its known limitation and named the fix.

Matching and sizing are now two passes: matching stays in file order, sizing
runs best-bet-first via `_top_pick_rank_tuple` — the same ordering the №1 rule,
`dashboard/lib/top-pick-rank.ts` and `tools/lock_commit.py` use, so the budget
and the headline can never disagree about which play is best. Only rows inside
their lock window consume budget (T8.18), so on most slates this reorders
nothing; it bites exactly when two picks commit in the same batch.
`verify_kelly_wiring` CHECK 7 now passes: both orders give the identical vector.

### Fixed — `apply_manual_odds.py` was a second T8.18 in the same column

It stamped `bet_placed="Y"` with **no lock-window check** and a flat `"1"` stake,
writing the two columns independently. Three faults in four lines: committing
outside the lock window contradicts T2.58 and froze any row it touched out of
the T8.18 re-derive *forever* via the T2.23 lock; a flat 1u is not the published
stake (quarter-Kelly sizes these 2u–10u); and splitting the pair is what let the
2026-07-28 heal fabricate bets. Now routed through `_size_row_stake`, which
decides commit-vs-pending from the lock window and writes both columns together.
A row already committed at a real stake is left alone (T2.23). Dormant path —
the override file is empty — but it would have quietly undone the fix.

### Added — `tests/conftest.py`, because the test suite wrote to production

**Twice in one session a test run put fabricated rows into the production
Supabase table and they rendered on the public dashboard** — once as
"THE №1 PLAY · CCC at DDD · STAKE 10u", once as four plausible-looking games
(CWS@TB 8u, KC@COL 5u, …) indistinguishable from real picks at a glance. They
also ate 11u of the daily budget, which made the new drift check report the real
LAD@ARI pick as wrong — a false positive that cost time to chase.

The mechanism is quiet and reasonable-looking: `log_picks` and `import_odds`
both end by calling `_mirror_picks_to_supabase`, a no-op when the Supabase env
vars are unset and a **live production write** when they are set. On the
operator's machine they are set — that is how the real predictor runs. So any
test driving either function writes to production, and nothing says so.

Three `autouse` guards now apply to every test in the directory whether or not
the author thought about it, which is the point: an opt-in guard would have
prevented neither incident.

- Supabase: env vars unset **and** the mirror replaced (either alone has a hole
  — a module caching a client at import time slips past the env check).
- Telegram: a commit-path test could otherwise fire a real "BET LOCKED" push
  about a game that does not exist.
- The ledger: `_write_rows` refuses a target inside the repo's `data/`. Guards
  the write rather than `_csv_path`, because several correct tests stub
  `_read_rows` and let the path be computed without using it.

Verified: Supabase traffic from the suite went 12 → 0, database clean.

### Changed — two T8.18 rows preserved by operator decision

2026-08-02 DET@OAK (published 7u, rule says 1u) and 2026-08-04 SD@ARI (9u vs 8u)
are genuine T8.18 drift and were left as published. Both were WINS, so
preserving them **flatters** the record by ~4.97u — recorded in
`data/stake_drift_exempt.csv` with that stated plainly, so the reason the board
reads better than the rule is written down next to the rows that cause it.

---

## [2026-08-09] - The stake froze against a probability the model had abandoned (T8.18)

Same family as T8.16/T8.17 — a published number that stopped tracking the thing
it claims to describe — but this one is arithmetic, not copy.

The dashboard's №1 play showed **STAKE 2u / WON +1.67u** while `/history` showed
**5.00u / WON 4.17u** for the same bet (LAD@ARI, YRFI, −120). Both were faithful
to their source: the hero, the board and Discord read `units_risked` from the
ledger; `/history` and the ¼-Kelly reconcile panel recompute via
`stakeUnitsFor`. They disagreed because the ledger row was internally
inconsistent.

### Timeline (ET)

| time | event | p(YRFI) | units_risked |
|---|---|---|---|
| 02:00 | DK −120 captured; edge + stake sized | 0.5831 | **2** |
| 13:04 | predictor revises | 0.6059 | 2 |
| 14:40 | predictor revises | 0.6288 | 2 |
| 15:10 | pick locks — nothing re-derives the stake | 0.6288 | 2 |
| 15:11 | Discord publishes "THE №1 PLAY · Stake 2 units" | 0.6288 | 2 |
| 16:42 | `end_of_day_check` heals the orphan, books P&L off 2u | 0.6288 | 2 |

`tracker.kelly_stake_units(0.5831, "-120")` → 2.0.
`tracker.kelly_stake_units(0.6288, "-120")` → 5.0.

### Root cause

Three rules that are each individually correct:

1. `units_risked`, `edge_on_pick` and `market_*_odds` are on the always-preserve
   list (`tracker.py:901`), so they are only ever recomputed when a **new price
   arrives**. No second DK price arrived all day — `odds_captured_at` never
   advanced past 02:00.
2. The T2.25 probability freeze is gated on `bet_placed == "Y"`
   (`tracker.py:923`), which under T2.58 does not happen until the row enters
   its 60-minute lock window. Pre-lock the row sits at `N`, so the probability
   is free to move while the stake cannot follow.
3. `tools/end_of_day_check.py:285` then stamps `bet_placed="Y"` post-game and
   deliberately preserves the recorded stake — so a stake that was never
   re-derived at lock becomes a "placed bet".

Net: **sizing is a side effect of a price arriving, not a step in the lock.**
Between first capture and lock (13 hours here) the probability drifts free.

The fingerprint is a row whose `edge_on_pick` disagrees with `p − implied_p`:
this row stored 0.0376 while the board printed +8.3%. Present on 4 of 25
quarter-Kelly-era STRONG bets: 07-27, 08-02, 08-04, 08-09. (A separate, benign
class — the fractional stakes 9.56u / 5.97u / 2.08u / 1.50u — predates
whole-unit rounding and is correctly frozen history.)

### Fixed

- `data/picks_2026.csv` + Supabase, 2026-08-09 LAD@ARI re-derived from the
  probability held at lock: `units_risked` 2 → **5.0**, `edge_on_pick`
  0.0376 → **0.0833**, `profit_loss_units` 1.667 → **4.167**.
  Written via `tracker._write_rows` (atomic) and `tracker._calc_pnl`;
  `tools/pl_calc.py --date 2026-08-09` reports no DRIFT.
  Operator decision: the record should show what the rule says at the
  probability the pick locked on. Note Discord had already published 2u, so
  `/history`'s "AS ACTUALLY STAKED" figure moves with this.

### Added

- `tools/verify_kelly_wiring.py` — **CHECK 7** (the daily risk budget) and
  **CHECK 8** (the batch-epoch guard). CLAUDE.md tells the operator to re-run
  this script after any sizing change, and it was structurally blind to the one
  variable a sizing change perturbs: it never passed `game_date`, so every check
  ran on the UNCAPPED path and poked `_daily_committed` by hand instead of
  through `kelly_reset_daily_committed()`. CHECK 7 sizes the real cap-bound
  2026-07-31 slate in two row orders; CHECK 8 asserts a capped call made without
  a reset raises rather than silently allocating.
- **CHECK 7 fails on first run, and is left failing.** The same four picks size
  `8 / 5 / 2 / 0` in file order and `4 / 5 / 5 / 0.5` reversed — CWS@TB's
  published stake halves depending on where the row sat. Both totals respect the
  15u budget, so this is the FIRST COME, FIRST SERVED limitation already named
  in `kelly_stake_units`' docstring, now measured rather than assumed. Needs an
  operator decision: rank the slate by edge before allocating, or accept
  order-dependence. Do not weaken the assertion to make the script green.

### Fixed (Discord)

- `discord_broadcasts.stake_for()` — a ledger stake of exactly `"0.0"` now
  returns `0.0` instead of falling through to the uncapped recompute below it.
  Since T8.18 a commit-time refusal (¼-Kelly finds no edge at the price, or the
  day's budget is spent) is written as the literal `"0.0"` so it survives the
  Supabase round trip; the old `booked > 0` test read that as nothing-booked and
  republished it as a confident positive stake.
- `build_board` — a refused play prints `No stake at the current price — NOT
  LOCKED` and says plainly that it is not a bet, rather than
  `Projected stake 0 units`. Positive projections keep the T8.17 wording.
- `discord_broadcasts` — recorded in a comment that the missing `game_date=` on
  its `kelly_stake_units` call is DELIBERATE and load-bearing (T8.18 rule R1):
  this module runs in-process in the long-lived Railway loop, and a capped call
  here would accumulate the day's tally forever.

### Added (T8.18 PART 3 — the system checks itself)

- `tools/stake_drift.py` — new. Answers "does `units_risked` still match the
  rule that was supposed to produce it?" for every locked STRONG row. Wired
  into `tools/pl_calc.py` (a **STAKE DRIFT** section printed after the existing
  P&L DRIFT block, sharing its exit-code contract) and into
  `tools/reconcile.py` as **invariant I5**.
- **The naive invariant turned out to be unimplementable, and that is the
  interesting part.** `units_risked == kelly_stake_units(p, odds)` per row
  fails both ways: *with* `game_date` the day's budget is already seeded from
  the ledger — including the row being re-derived — so every row recomputes to
  0 and 17 of 18 locked rows flag; *without* it, every legitimately cap-trimmed
  row flags (2026-07-31 MIL@LAA stores 1.5u, recomputes to 5.0u, and 1.5 is
  CORRECT). The shipped check is a **per-day replay**: reset the tally, take the
  slate's locked STRONG rows, sort best-bet-first using `lock_commit._rank_key`
  itself, re-derive each with `game_date` live, compare the resulting vector.
- **Era floor `2026-07-30`** (the unit re-basing). Without it the replay reports
  310 violations over 366 rows — pre-re-basing history sized under rules that no
  longer exist. Exemptions for POSTPONED/SUSPENDED, `DraftKings (manual)`,
  cluster demotions, and an operator escape hatch at
  `data/stake_drift_exempt.csv` for the flat stakes written by
  `end_of_day_check`'s and reconcile-I1's orphan heals.
- **I5 NEVER HEALS**, deliberately. Rewriting a locked stake is a money-path
  write from two concurrent hosts against a table with no version column, and
  T2.23 says a placed bet's terms are frozen. It records to `system_errors`,
  deduped through `notifications_log` on a key containing both figures — so a
  persistent violation writes ONE row rather than ~288 a day, and a stake that
  *changes* re-fires.
- **What it can't see, on the record**: on a cap-bound night the ledger and the
  replay can split one 15u budget differently and both be right (2026-07-31,
  8/5/1.5/0.5 vs 8/5/2/0). Such a day is reported as CAP-ORDER, not a violation
  — which means a genuinely stale stake can hide behind a matching day total on
  the ~13% of nights where the cap binds. Accepted: the alternative is an alarm
  every cap-bound night and a check nobody reads. Once `lock_commit` is the
  writer the two orders are the same function and CAP-ORDER should stop
  appearing at all.
- On the live ledger it flags exactly the two known-untouched victims —
  2026-08-02 DET@OAK (7u vs 1u) and 2026-08-04 SD@ARI (9u vs 8u) — clears the
  2026-07-31 cap artifact, and clears the already-corrected 2026-08-09 LAD@ARI.

### Tests (T8.18 PART 1)

- `tests/test_stake_rederive.py` — new, 14 cases over
  `tracker._rederive_pre_lock_stake`. Three prove the stake MOVES: the
  2026-08-09 LAD@ARI victim replayed across three predict ticks
  (0.5831 → 0.6060 → 0.6288 ⇒ 2u → 3u → **5u**, `edge_on_pick`
  0.0376 → 0.0605 → **0.0833**) while `market_*_odds` / `odds_captured_at` /
  `opened_*` stay byte-identical; three static ticks over the cap-bound
  2026-07-31 slate returning an identical vector (the P0-1 oscillation guard,
  restated at the CALLER — `tests/test_money.py` calls the reset inside its own
  helper, which pins the reset function and not that any caller uses it); and
  rule R1, that a projection leaves `_daily_committed` and the batch epoch
  untouched.
- The other eleven pin the doors that must stay shut: a decided no-bet is never
  re-opened (and `end_of_day_check.find_orphaned_strong_bets` still returns
  nothing for it after four ticks of drift — the 2026-07-28 P0-2 door), a
  published stake is floored rather than blanked, LEAN / cluster-demoted /
  placeholder-time / started / placed / graded rows are frozen, and the feature
  is a no-op unless `NRFI_STAKE_REDERIVE=enabled`.
- **Every negative case carries a `_moves()` control** that repairs the one
  disqualifying attribute and asserts the stake then DOES move. Measured
  without the guards, the frozen fixtures would publish 5u — and the decided
  no-bet would publish **10u at `bet_placed="N"`**, which is exactly the
  `N + units>0` pair the orphan heal reads as a bet to stamp.
- No test reads `data/picks_2026.csv` (verified: zero filesystem touches for
  the file) and no test hardcodes a slate date — every time is derived from
  `now`, which is what stopped `tests/test_selection.py` from going red
  overnight on 2026-08-07. Re-run green at frozen clocks of 00:30, 12:00 and
  22:30 ET, on the DST-fallback night, and across a year boundary.

### Deferred (needs operator sign-off — money path)

- **The root fix**: re-derive `units_risked` + `edge_on_pick` on every pre-lock
  tick from the current probability and the locked price, then freeze stake,
  edge, probability and price together in one atomic moment at T-60. Removes
  the drift window entirely and makes the two surfaces agree by construction.
- 2026-08-02 DET@OAK (recorded 7u, row's numbers say 1u) and 2026-08-04 SD@ARI
  (9u vs 8u) left untouched pending the same decision. They are no longer
  invisible: `tools/stake_drift.py` reports both on every reconcile tick, and
  will keep reporting them until they are healed or exempted.

---

## [2026-08-08a] - THE BOARD announced a stake it had not committed (T8.17)

T8.16 with the sign flipped. That one had the board claiming a VERDICT it had
not reached ("declined them all") on games still waiting on lineups. This one
has it claiming a COMMITMENT it has not made.

### What subscribers saw

**2:07 PM** — THE BOARD (fired at T-60 before a 3:05 PM opener):

> # ⭐ THE No.1 PLAY
> **CLE @ CWS** · 7:15 PM ET
> ### Stake 6 units

CLE@CWS does not lock until **6:15 PM** — four hours later. It did not survive
them. The model's own journal (`pick_changes`) records both plays reversing:

| time | game | change |
|---|---|---|
| 3:37 PM | TOR@PHI | `STRONG YRFI` → `LEAN YRFI` |
| 4:02 PM | CLE@CWS | `STRONG YRFI` → `LEAN YRFI` |

leaving the slate with **no STRONG pick at all**, while the channel still held
a published instruction to stake 6 units — 6% of a subscriber's bankroll — on a
game the system had stopped backing.

### Root cause

The same root as T8.16: **the board speaks at slate time about picks that decide
at game time.** It fires at T-60 before the FIRST game; every later game is
still unlocked when it prints, and stays unlocked for hours. T8.16 taught it to
stop calling unjudged games "declined". It still printed unlocked plays as
committed bets with a stake and a price floor.

### Fixed

- `discord_broadcasts.is_locked()` — new; has this pick passed its OWN lock
  (T-60 before ITS first pitch), mirroring `LOCK_MINUTES_PREGAME`.
- `build_board` — a stake is an INSTRUCTION and is only printed as one once the
  pick can no longer change. Before its lock a play reads `Projected stake N
  units — NOT LOCKED`, names the lock time, and says plainly *"This is not a bet
  yet… Act on THE No.1 PLAY message, not on this line."*
- The No.1 crown is withheld until the lock: an unlocked leader is headed
  `OUT IN FRONT — NOT LOCKED`, and section headings degrade
  `THE PLAYS`/`ALSO PLAYING` → `IN CONTENTION`/`ALSO IN CONTENTION`.
- Locked plays are untouched — a genuine committed play still prints its stake
  and price floor.

### Fixed (tests)

- `tests/test_selection.py` — the lock tests hardcoded `2026-08-06`, and
  `_pick_is_locked` defensively locks any slate date >24 h old. They passed the
  day they shipped (2026-08-07) and went red overnight with no code change,
  initially appearing to blame a fix that never touched `tracker.py`. Now
  computed from the current ET date, with the trap documented in-file.

### Verified

- 44 tests passing (3 new, pinning: no committed stake before the lock, the
  crown waits for the lock, and a locked play still prints a real instruction).
- Replayed against tonight's actual ledger at the actual 2:07 PM fire time.

### Correction recorded

Mid-diagnosis I read `bet_placed='N'` on both plays and concluded the system had
*declined* them — i.e. that we had published a bet the model refused. That was
wrong. `tracker.py:4054-4060` sets `bet_placed='N'` with a non-zero
`units_risked` to mean **"pending, will commit at lock"**. `bet_placed` is
overloaded (not-yet / zero-edge decline / PASS) and `units_risked` is what
separates them. `is_strong()` ignoring `bet_placed` is correct behaviour.

---

## [2026-08-07j] - THE BOARD claimed a verdict it had not reached (T8.16)

Operator: *"one discord message says we have no #1 pick, then the other says we
do."* Correct, and the board was the one lying.

### What subscribers saw

**5:42 PM** — THE BOARD:

> ## NO PLAY TONIGHT
> The model looked at every game and declined them all.
>
> ## PASSING (8)
> `LAD @ ARI`  9:40 PM  57.4%  **Lineup Pending**

**8:41 PM** — THE No.1 PLAY: `LAD @ ARI · YRFI · Stake 3 units`.

The board contradicted **its own body** — four of the eight "passing" games were
`Lineup Pending`, not declined — and then contradicted itself again three hours
later. A subscriber who read it and stopped watching missed the only play of the
night.

### The mechanism is structural, not a typo

THE BOARD fires at T-60 before the **FIRST** game of the slate. A pick commits
60 minutes before **ITS OWN** first pitch, when the lineup posts. On a card
running 6:40 PM to 10:15 PM the board is written at 5:40 PM — hours before the
late games have lineups. **It is incapable of having judged them.**

"Declined" and "not yet decided" are different claims. The code only had a word
for the first.

### The fix

New `is_undecided(row)` — `LINEUP PENDING` / `STARTER PENDING` / any label
containing "pending" — and three honest branches:

| state | says |
|---|---|
| no plays, games pending | **NOT SET YET — N games still waiting on lineups**, plus "watch for THE No.1 PLAY" |
| no plays, all judged | NO PLAY TONIGHT — *"every game has been judged and declined"* (now true) |
| a play, games pending | the No.1, plus *"N games still waiting and could still commit"* |

The quiet-night message is kept, because replacing one false claim with another
would be no better: when every game really has been judged, the board should say
so plainly.

### Also corrected: a diagnosis I got wrong mid-flight

While investigating I read the LOCAL `data/picks_2026.csv` and found LAD@ARI as
`PASS / LINEUP PENDING / bet_placed=N` — and briefly concluded we had published
a 3-unit bet on a game the system never committed to. **That was my error: the
local CSV was stale.** Supabase, the live source, has it as `STRONG YRFI /
bet_placed=Y / units_risked=3`. The No.1 message was correct throughout; only
the board was wrong. When diagnosing live state, read Supabase — the working
copy lags the cron.

### Tests

`tests/test_board_copy.py`, 5 tests pinning all three branches and both pending
shapes. Suite: **41 passing**.

---

## [2026-08-07i] - the No.1 gate: doubleheaders and demoted rows (T8.13, T8.14)

Both were found by the test suite written hours earlier, pinned `xfail(strict)`,
and are now fixed -- so the strict marker did its job: fixing them turned the
suite red and forced the markers off.

### Fixed - a doubleheader crowned BOTH halves No.1 (T8.13)

`_row_is_nights_top_pick` excluded "self" by `AWAY@HOME` name. Both halves of a
doubleheader share that name, so each excluded the OTHER as itself and both
returned True -- two "tonight's No.1 play" alerts under a No.1-ONLY policy.

Identity is now `game_pk`, falling back to `name#game_number` for pre-2026-04
rows that carry no game_pk. Not yet triggered live (no real slate has two STRONG
rows sharing a name) but 18 slate+name keys already carry more than one row, so
it was one doubleheader away.

### Fixed - a demoted no-bet row could take the No.1 slot (T8.14)

The rival scan filtered on `pick_strength` and never looked at `bet_placed`,
while `tools/apply_cluster_demotion.py` deliberately sets `bet_placed='N'`
WITHOUT touching strength. So a row the system had decided not to bet still
competed for No.1 -- and won, silencing the alert for the game the money was
actually on. Live on 1 of 123 slates (2026-04-29 TB@CLE).

Rows explicitly marked `bet_placed='N'` are now excluded, both as rivals and as
candidates. **Empty is NOT excluded** and that distinction matters: before the
odds import runs every row is pending, and treating "unknown" as "no bet" would
silence the entire slate.

Verified on the real ledger: **exactly one No.1 on all 123 slates**, unchanged.
Suite is 37 passing, 0 xfailed.

### Measured, not fixed - is NRFI actually being judged unfairly?

Prompted by a fair question: if NRFI No.1s were being excluded from the record,
were they being written off wrongly? Checked, and the answer is no -- the
premise inverts:

* The three nights whose overall top play was NRFI went **0W-3L**. The wins in
  the earlier comparison were the YRFI *substitutes*, not the NRFI picks.
* Headline STRONG NRFI looks fine: 57W-39L, 59.4%, +8.53u -- a HIGHER hit rate
  than STRONG YRFI's 58.6%. But split by whether a real book price was ever
  captured:

| | record | hit | units |
|---|---|---|---|
| STRONG NRFI, **real** price | 22W-27L | **44.9%** | **-11.29u** |
| STRONG NRFI, invented -110 | 35W-12L | 74.5% | +19.82u |
| STRONG YRFI, **real** price | 186W-131L | 58.7% | +30.73u |
| STRONG YRFI, invented -110 | 76W-54L | 58.5% | +15.08u |

Every unit of NRFI's apparent profit sits on bets **no book ever priced**. On
the 49 where a real price existed it hit 44.9% -- against a 52.4% break-even at
-110. YRFI reads the same in both buckets (58.7 vs 58.5), which is what a real
edge looks like; NRFI's 44.9-vs-74.5 split is the signature of an artefact.

Consistent with the prior work (`2026-07-28_nrfi_deep_dive`): ~300 selection
rules tested and refuted 12-of-12, and stripping ALL the vig still leaves NRFI
at -4.68%. The 2026-06-07 decision to switch STRONG NRFI off stands.

---

## [2026-08-07h] - the published record now counts the night's actual No.1 (T8.15)

### Fixed - a postponed No.1 was silently replaced by the runner-up

Both `tools/pl_calc.select_top_picks` and `dashboard/lib/top-pick.ts` filtered
unsettled rows out BEFORE ranking the night. So when the top play did not
settle, the second-best game was promoted and ITS result was counted as the
No.1's.

Live, 2026-06-11: the most confident YRFI play was **ATL@CWS (p_nrfi 0.3219)
and it was POSTPONED**. The record counted **CHC@COL (0.3543), which LOST**. A
game the system would never have graded that night contributed a loss to the
published record.

A postponement is NO ACTION -- not a result, and not a licence to substitute a
different game. Both surfaces now rank first and require the winner to have
settled; a night whose No.1 never settled is excluded and counted, so the
exclusion is visible rather than silent.

| | record | hit | units |
|---|---|---|---|
| before | 46-21 | 68.7% | +84.72u |
| after | **46-20** | **69.7%** | **+87.72u** |

The correction REMOVES a loss that was never the No.1, so the number moves in
the operator's favour -- which is a reason to be careful about it, not a reason
to enjoy it. It was verified from both ends: `pl_calc --top-pick` and the
dashboard now print the same 46-20 / +87.72u.

Fixing the Python alone would have recreated the disease this session has been
clearing all day, so `lib/top-pick.ts` got the same reordering. It needed TWO
changes, not one: the `graded` check AND the `unitsRisked`/`pnl` null check both
sat in front of the ranking, and a postponed row has an empty `profit_loss_units`.
Moving only the first left the number unchanged, which is how the mistake was
caught -- the dashboard still said 46-21 after the "fix".

### Kept, and now disclosed - NRFI is excluded from the record

The other 3 of the 4 announced-vs-recorded mismatches are the operator's own
rule (2026-08-03): *"STRONG NRFI was switched off 2026-06-07 for losing in every
band, and showing them as the record of a system that would not place them is
simply wrong."* That reasoning holds and the rule is unchanged. Reverting it
would have cost 15.59u and 2 wins (44-22 / 66.7% / +69.14u).

What was wrong was the LABEL, on one surface. Discord said *"Every night's top
play since 2026-05-26"* -- true of the arithmetic, misleading about the
population, because on 15 of 92 nights the overall top play was an NRFI pick and
the best YRFI play stands in its place. It now reads:

> _The top YRFI play of every night since 2026-05-26, when the live model was
> fit, sized by today's rules. NRFI is excluded — it was switched off
> 2026-06-07 for losing._

The dashboard already said "under today's rules" and "the top YRFI play of each
night"; it now also names the exclusion. The distinction being drawn is between
a MODELLED record -- what today's rules would have produced, which is legitimate
and useful -- and a transcript of what was alerted. Both are defensible; only
one of them was being claimed.

### Added - a regression test

`tests/test_selection.py` pins the 2026-06-11 shape directly: a night whose top
play is POSTPONED must contribute NO result, and when the top play does settle
it is the one counted. 35 passing, 2 xfailed.

---

## [2026-08-07g] - the money path gets tests, and CI, and the guard stops trusting itself (T8.11, T8.12)

### Added - the first tests on the money path (T8.11)

`tests/test_money.py` + `tests/test_selection.py`: **34 passing, 2 xfailed**,
covering `kelly_stake_units`, `_calc_pnl`, the caps, `_top_pick_rank_tuple`,
`_row_is_nights_top_pick` and `_pick_is_locked`.

Before this there were ZERO. The ~40 files named `test_*.py` under `tools/` and
`scripts/archive/` are model experiments that assert nothing, which is why
`pytest.ini` sets `testpaths = tests` -- pointing pytest at the repo root would
re-run a season of research instead of the money tests.

**Every expected value was EXECUTED against the real code**, never hand-derived.
That distinction is the point: a suite built from the author's own arithmetic
pins the author, not the system. Four parallel agents ran 91 candidate
invariants through the live functions; the ones kept are the ones that
reproduced.

Weighted toward regressions, because each is a night that already cost money:
the 3.4975 -> 4u double-round (2026-08-06), half-to-even at 2.4975,
`KELLY_ROUNDED_FLOOR` lifting a sub-half-unit to 0.5 rather than dropping it (it
silently dropped 16 of 301 bets once), the daily-cap double-count that made
stakes oscillate every 5 minutes, and bankroll-independence (the 5.97u vs 17.00u
same-bet discrepancy).

Also pinned because they are semantics people get wrong: **`0.0` and `None` mean
different things.** `0.0` is "Kelly forbids this bet"; `None` is "cannot size,
fall back to flat". If a missing DK price ever returned `0.0`, a scrape miss
would silently CANCEL the bet instead of falling back -- picks appearing to
vanish, the failure mode the operator gets burned by most.

### Added - CI on every push (T8.11)

`.github/workflows/tests.yml`, two jobs, `ubuntu-latest` so it still runs when
the self-hosted VPS is down -- which is exactly when someone is pushing a fix.
Skips `data/**` for the same reason Vercel does.

Until now the repo had **no automated check on any push at all**. The only
enforcement anywhere was Vercel `prebuild`, which since T8.4 does not even run
on a data-only push.

### Fixed - the parity guard was a frozen oracle (T8.12)

`check-kelly-parity.mjs` compares the dashboard to a **committed fixture** and
never invokes Python. So changing `tracker.kelly_stake_units` left the fixture
still, left the TypeScript still, and the guard compared them to each other and
printed `ok` -- while the number that stakes the real bet had walked away from
the number published. It proved the dashboard matched THE FIXTURE, not today's
tracker.

Demonstrated rather than argued. Setting `NRFI_KELLY_ROUNDING=0.5` -- an env
var, **no file changes at all**, exactly what a Railway config edit looks like:

| | result |
|---|---|
| real stake, tonight's bet | **4.0u -> 3.5u** |
| old guard (dashboard vs fixture) | **"ok"** |
| new check (fixture vs live Python) | **6,950 value changes, exit 1** |

`tools/parity_fixtures.py` is now the ONE generator for both fixtures, with
`--check` (CI) and `--write` (after a deliberate rule change). The loop closes
across two places because neither can do both halves: **Vercel** checks
TypeScript against the fixture (no Python on that builder), **CI** checks the
fixture against Python.

Regenerating revealed the original fixture was a one-off of 5,369 cases against
the canonical generator's 21,328 -- with **0 value conflicts**, so no money rule
had drifted, only coverage. Fixture and generator are now locked together.

### Found while writing the tests - two real defects, NOT fixed here

Both marked `xfail(strict=True)`, so the suite goes RED the moment either is
fixed and the marker must be removed. Deliberately **not** pinned as correct:

* **A doubleheader crowns BOTH halves No.1.** Self-exclusion in
  `_row_is_nights_top_pick` is by `AWAY@HOME` name, not `game_pk`, so each half
  excludes the other as "self". Under the No.1-only policy that is two
  "tonight's No.1 play" alerts. Not yet triggered live -- no real slate has two
  STRONG rows sharing a name -- but 18 slate+name keys already carry more than
  one row.
* **A cluster-demoted no-bet row can take the No.1 slot.** The rival scan
  filters on `pick_strength` and never checks `bet_placed`, while
  `tools/apply_cluster_demotion.py` deliberately sets `bet_placed='N'` without
  touching strength. The demoted row wins and silences the alert for the game
  the money is actually on. Live on 1 of 123 slates (2026-04-29 TB@CLE).

---

## [2026-08-07f] - the two subscriber-facing contradictions (T8.9, T8.10)

Both are the disease we fixed this morning, found by going looking for it
rather than by waiting for it to be reported: one rule implemented twice,
and a decision taken from a display value.

### Fixed — the №1 pick could be a DIFFERENT GAME on the dashboard (T8.9)

`board-supabase.ts:216` rounds the model probability to one decimal for
the screen, and every dashboard caller fed `nrfiPct / 100` into
`selectTopPick`. Python ranks at full precision. The formula matched; the
**inputs** did not — cause (2) of the Kelly bug, applied to pick
selection instead of sizing.

Replayed over all 114 slates with ≥2 STRONG picks, **3 named a different
game**:

| date | Python → Discord, Telegram, record | Dashboard showed |
|---|---|---|
| 2026-06-15 | PIT@OAK | LAA@ARI |
| 2026-06-20 | PIT@COL | CIN@NYY |
| 2026-07-08 | COL@LAD | NYY@TB |

Python's answer gates every pick-facing alert and the published win-loss
record, so on those nights the record counted one game while the board,
the hero card and `/brief` named another.

`BoardRow` now carries `nrfiP` — full precision, for deciding — beside
`nrfiPct`, which is documented as display-only. The three callers switched.
`RankableBet.modelP`'s doc said "as the system printed it", which is
precisely the phrasing that invited the bug; it now says full precision
and names the field.

**Verified with the REAL compiled module**, not a re-implementation:
`tsc` on `lib/top-pick-rank.ts`, driven over every slate against Python's
own `_row_is_nights_top_pick`. **Before: 3 disagreements. After: 0.**

Two honest limits recorded in code: the CSV fallback path still ranks at
1dp because the board CSV stores `nrfi_pct` already rounded — there is no
full-precision value on disk to recover, and that branch only runs during
a Supabase outage. And `DashboardShell.breakTie` is left alone: it orders
the board FOR DISPLAY and is not the №1 rule, so it is now labelled as
such rather than "unified" into something it is not. Its sort keys did
move to full precision, since two games printing "58.2" were being treated
as tied when the model had separated them.

### Fixed — "don't take worse than" disagreed between surfaces (T8.10)

Published for the same bet on 2026-08-07:

| surface | said |
|---|---|
| Discord | don't take worse than **−165** |
| Dashboard | BET UP TO **−162** |

`price-ladder.ts` solves analytically on the RAW quarter-Kelly and ceils;
`discord_broadcasts.pass_price` walked −100 downward in 5-cent steps
against the ROUNDED stake. Python's docstring claimed to mirror the
TypeScript. It did not.

**Python was the wrong side, and the plan for this fix had it backwards.**
The 5-cent grid stops at the coarsest point where the *rounded* stake is
still ≥1u, which includes prices whose raw quarter-Kelly is already under
a unit — exactly the bets `price-ladder.ts` documents the operator
deciding not to publish (2026-08-04: "the ladder ends at the last full
unit"). Every divergence told a subscriber they could lay a WORSE price
than the dashboard allowed:

    p=0.6343  python -165  dashboard -162
    p=0.62    python -155  dashboard -152
    p=0.70    python -225  dashboard -219

`pass_price` is now the same analytic solve with the same ceil.
**Swept 2,497 (probability, card-price) pairs against the compiled
TypeScript: 0 mismatches.** Tonight's bet now reads −162 on both.

Also removed `price_ladder()` — 26 lines, called by nothing since the
ladder came out of the messages on 2026-08-06, while still advertising
that it mirrored the TypeScript. Dead code that claims a guarantee it does
not keep is worse than none, because the next person needing a ladder
would have reached for it.

### Added — the parity guard now covers the SECOND published number

`check-kelly-parity.mjs` compiled only `kelly-sim.ts`. The ladder built on
top of it drifted independently and invisibly. It now compiles
`price-ladder.ts` too and checks `passAt` against a Python-generated
fixture, and its failure message names WHICH mirror broke — a message that
blamed `kelly-sim.ts` for a ladder drift would send the reader to the
wrong file, which is its own small version of this bug.

Verified by reintroducing the exact mistake the code comment warns about
(`Math.ceil` → `Math.round`): 60 failures, correctly attributed to
`price-ladder.ts`, green again on restore. The first version of the check
reported 11 failures that were the harness's own fault — it used −110 as a
universal card price, which has no edge at p≈0.50, so the ladder was
correctly null. Fixed before shipping.

---

## [2026-08-07e] - two ops defects: a false page waiting to fire, and a spammer (T8.7, T8.8)

### Fixed — /api/health was measuring BUILD age, not data age (T8.7)

A regression from T8.4, caught by looking for the same disease elsewhere
rather than by anything alarming.

`route.ts` derived `lastPredictAt` from the bundled
`data/thresholds.json`. That was correct while every push rebuilt the
site — build age and data age were the same number. Once data commits
stopped triggering builds they came apart. Measured live on 2026-08-07:

| source | lastPredictAt |
|---|---|
| what /api/health reported | 17:44:48 (the bundled file) |
| what the predictor had actually done | 18:16 (Supabase) |

**This was a false page waiting to fire.** `watchdog.check_dashboard()`
PAGES on BROKEN, and BROKEN triggers at >240 min during prime hours — so
roughly four hours after the last CODE push it would have invented a
"Dashboard BROKEN" alert about a perfectly healthy system, and kept
inventing it. It failed the other way too: the 24h error window empties
as the bundle ages, so a real error storm could have read as a clean OK.

`/api/health-live` was never affected (it reads Supabase), so
`runner_watchdog.yml`'s "RAILWAY IS DOWN" check was safe — verified
separately before assuming it.

Now Supabase-first for BOTH the timestamp and the 24h error window, same
pattern as `loadBoard`, with the bundle as fallback. A new
`freshnessSource` field (`supabase` / `bundle` / `none`) says which
answered, so the endpoint can no longer be quietly wrong about its own
freshness.

Verified by forcing both branches: with Supabase configured,
`freshnessSource=supabase`, `lastPredictAt` 18:21, errors 3; with it
blanked and rebuilt, `freshnessSource=bundle`, `lastPredictAt` the
bundled 17:44, errors still populated from the CSV. The first pass of
that test reported `none` while successfully reading the bundle — the
fallback branch never set the field. Fixed before shipping.

### Fixed — the watchdog would have sent ~12 messages an hour (T8.8)

`watchdog` was not registered in `_DEDUP_WINDOW_M`, so it inherited the
**5-minute** fallback while the Railway loop cycles every 5 minutes. The
file's own docstring promised "~3 messages, not 72" over six hours. A
persistent page-level fault would have sent roughly 12 an hour, in the
one file whose stated purpose is preventing alarm fatigue — and it is
the same unregistered-event-type bug that published THE BOARD three
times the night before.

`_ESCALATION_MIN = (0, 60, 240, 720)` was defined at `watchdog.py:271`
and **never read by anything**. Deleted, and the docstring rewritten to
describe the mechanism that actually exists: the hour-bucketed event key
plus a 60-minute window, both required.

Simulated a continuous fault across 12 consecutive 5-minute cycles:
**1 send, down from 12.**

`transport_check` — the only other unregistered type — is now registered
at 5 min explicitly, because a human smoke-testing the webhook wants to
run it twice in a row. Registered so it reads as a decision rather than
an oversight.

---

## [2026-08-07d] - the skip check compares against the last BUILD, not the last commit (T8.6)

### Confirmed on the real runner

Build log for `fd9c42d`:

    Running "bash scripts/should-build.sh"
    should-build: invoked from scripts, operating at /vercel/path0
    should-build: comparing against LAST BUILD 80086205
    should-build: non-data changes since 80086205 — BUILDING
    AUDIT.md
    CHANGELOG.md
    dashboard/scripts/should-build.sh

Four things measured that had only been inferred: `VERCEL_GIT_PREVIOUS_SHA`
IS populated and resolved to `80086205`, which really was the last
successful build; `ignoreCommand` really does run from the Root
Directory (`invoked from scripts` ⇒ cwd was `dashboard/`), which is the
assumption the whole pathspec trap hangs on; the `cd` to the repo root
landed at `/vercel/path0`; and the comparison named the right files.

### Fixed — a code commit could be silently skipped

T8.4 shipped with a real hole, found by reading the sibling strikeouts
project's history, where a follow-up commit reads "Compare the
build-skip against the last BUILD, not the last commit (A-023a)".

**Vercel builds once per PUSH, at the tip — not once per commit.** So
`git diff HEAD^ HEAD` asks the wrong question. Push a code commit and a
data commit together and Vercel evaluates only the tip, sees data-only,
skips — and the code commit never deploys. It is the worst shape of
failure this file exists to prevent: a skip that reports success while
shipping nothing.

Reproduced against real history rather than argued. Last build
`34167933`, push = [`de272431` (code), `aa21ba8a` (data)], tip
`aa21ba8a`:

| comparison | verdict |
|---|---|
| old, `HEAD^ HEAD` | **SKIP** — `de272431` never deploys |
| new, vs last build | **BUILD** — catches `watchdog.py`, `CHANGELOG.md` |

### How

`VERCEL_GIT_PREVIOUS_SHA` — "the git SHA of the last successful
deployment for the project and branch", exposed ONLY when an Ignored
Build Step is configured, i.e. precisely here. Diffing from it covers
every commit since the last real build, however many pushes that spans.

**The shallow-clone interaction gets WORSE as the skipping gets
better.** Vercel clones shallow, and every skip pushes the last
*successful* build further back. At ~30 skipped data commits a day the
previous build will routinely sit outside the fetched history — sha
present, object missing. Handled with a targeted
`git fetch --depth=1 origin <sha>`: `git diff A B` needs both objects,
not the path between them.

### Edge cases, all exercised

| case | verdict |
|---|---|
| last build precedes a buried code commit | **BUILD** (the fix) |
| last build IS that code commit | SKIP (saving preserved) |
| `PREV` == HEAD (redeploy of same commit) | **BUILD** |
| `PREV` unset (first run with an ignore step) | narrow fallback, logged |
| `PREV` unreachable after fetch | narrow fallback, logged |

The narrow fallback keeps today's behaviour — correct for the
single-commit push that is the norm here — but it cannot see a buried
code commit, so it says so in the build log rather than passing
silently. Replay over the last 25 commits still gives 0
misclassifications.

---

## [2026-08-07c] - the stale fallback now announces itself (T8.5)

Follow-on to T8.4. Since data commits no longer rebuild the site, the
CSVs baked into the Vercel build are refreshed only when code ships, so
the Supabase-outage fallback can be days stale.

### Rejected — "rebuild on the daily backup commit to keep it fresh"

The obvious fix, and it does not work. `auto: daily backup snapshot`
lands at **05:51-08:12 ET**, and at that hour the board reads:

    1,LAD,ARI,...,PASS,LINEUP PENDING,...
    2,HOU,SD, ...,PASS,LINEUP PENDING,...

Every game LINEUP PENDING, no picks, no odds. Building on that commit
would cost a build a day and bundle a board with ZERO actionable picks
— so during an evening outage the fallback would confidently serve an
empty slate while real picks existed. Measured, not assumed.

### Shipped instead — make the fallback visible

The hazard was never the staleness; it was the SILENCE. `loadBoard()`
swallows a Supabase failure and serves the bundle, so a stale board is
pixel-identical to a live one.

* `BoardResponse.source` is now `"supabase" | "csv"`, set at all three
  exits of `loadBoard`.
* `/api/health` reports `boardSource` and goes **DEGRADED** with
  "Board served from the build-time CSV fallback, not Supabase —
  figures may be stale" when the fallback is live during game hours.
* `watchdog.check_board_source()` PAGES on it, so the operator is told
  rather than left to notice.

Costs zero build minutes, and unlike a fresher-stale-copy it addresses
the actual failure.

### Verified by forcing the branch, not by reading it

`NEXT_PUBLIC_*` vars are inlined at BUILD time, so blanking the env file
and restarting proved nothing — the first attempt still reported
`supabase`. Rebuilt with the vars blank:

    boardSource : csv
    status      : DEGRADED
    reason      : Board served from the build-time CSV fallback...
    board       : still renders, 15 rows (graceful, not an error)

and `generatedAt` came back as `...464Z` — millisecond precision with a
trailing Z, the filesystem `toISOString()` signature, versus the
microsecond `+00:00` Postgres form on the live path. The same tell that
proved Supabase was serving production in T8.4, observed from the other
side.

Watchdog predicate unit-checked in all three states: supabase/game-hours
silent, csv/game-hours PAGE, csv/3am silent.

### Blast radius, for the record

The Vercel bundle backs ONLY the operator dashboard.
`discord_broadcasts.load_slate()` reads `picks_<year>.csv` from
Railway's own checkout, so the subscriber product is unaffected by
bundle staleness entirely.

---

## [2026-08-07b] - stop rebuilding the site for data commits (T8.4)

### Added — `ignoreCommand` skips builds on data-only commits

Vercel rebuilt the whole site on every push, and this repo's automation
pushes constantly. Measured over 2026-08-01..07 on the deploy branch:
**242 commits, of which 188 (78%) were `auto:` data commits.** Each one
ran a full `next build`. This project burned **99h 28m of Build CPU
Minutes, 51.6% of the plan** for the cycle; with the sibling strikeouts
project at 40.5%, the two accounted for 92% of the allowance.

`dashboard/vercel.json` now carries
`"ignoreCommand": "bash scripts/should-build.sh"`. The 16 `crons`
entries are untouched — crons are not builds and thinning them would
break the pipeline while saving nothing.

### Verified FIRST — the board really is served from Supabase

This is NOT the sibling static-export project, so the safety of skipping
builds is not obvious. `dashboard/lib/board.ts:loadBoard()` is
Supabase-first with a filesystem fallback onto the build-time copy that
`scripts/copy-data.mjs` bakes in. If production were on the filesystem
branch, skipping builds would silently freeze the picks on a live money
system.

Settled empirically, not by reading env vars — watching whether served
data moves without a deploy:

| time | `generatedAt` | last deployment |
|---|---|---|
| 14:59:30 | 14:58:16.722598 | 14:55:53 `9859b163` |
| 15:01:02 | 14:58:16.722598 | 14:55:53 `9859b163` |
| **15:02:33** | **15:01:52.965955** | 14:55:53 `9859b163` *(unchanged)* |

The data advanced twice with no new deployment, which a file baked into
the bundle cannot do. Corroborating: `generatedAt` carries microseconds
and a `+00:00` offset (Postgres `timestamptz`), while the filesystem
branch renders `stat.mtime.toISOString()` — milliseconds and a trailing
`Z`. Different producer. **Supabase is serving the board; the change is
safe.**

### The pathspec trap, measured rather than guessed

The Vercel Root Directory is `dashboard/`, so `ignoreCommand` runs from
there and git pathspecs resolve against THAT directory. A bare
`:(exclude)data` means `dashboard/data`, which is gitignored and appears
in zero commits. The initial assumption was that this would skip
everything. **It does the opposite:**

    from dashboard/, ':(exclude)data'      auto: predict -> BUILD
    from dashboard/, ':(exclude,top)data'  auto: predict -> SKIP

Excluding the non-existent `dashboard/data` leaves the real `data/`
files in the diff, so every commit looks like it has code changes. That
is worse than an error: the deploy succeeds, the site is fine, and the
only symptom is that the bill never falls. The script therefore both
`cd`s to the repo root AND uses `:(exclude,top)data`.

### Proven before pushing

- **Classifier replay, last 25 commits: 14 SKIP / 11 BUILD, 0
  misclassifications.** Every `auto:` SKIP, every code commit BUILD.
- **All 188 `auto:` commits touch ONLY `data/`** — 1,875 file changes,
  zero outside it. The single exclusion covers `auto: predict`
  (boards, diagnostics, picks, pick_changes, system_errors, thresholds),
  `auto: grade`, and `auto: daily backup snapshot` (`data/backups/**`).
- Fails toward BUILDING on every uncertainty: no git root, no parent
  commit (shallow clone), or any git error.
- `bash scripts/...` rather than `./scripts/...`, because the executable
  bit does not survive a Windows checkout reliably.

### Added — `.gitattributes` with `*.sh text eol=lf`

The repo had none. A shell script committed from Windows with CRLF runs
locally and dies on Vercel's Linux runner with `
: command not found`.
Verified the committed blob contains 0 CR bytes.

### VERIFIED IN PRODUCTION — first `auto:` commit after the fix was skipped

| commit | Vercel status | duration | sha |
|---|---|---|---|
| `auto: predict 2026-08-07` | **Canceled** | **6s** | `ccf19bb` |
| `perf(vercel): skip builds for data-only commits` | Ready | 43s | `68faa78` |

6 seconds is the ignoreCommand running and exiting 0; 43s is a real
build. The code commit still builds, the data commit does not.

MEASUREMENT TRAP, recorded because it produced a false alarm: the first
check counted GitHub *deployment records* for the sha and found 1,
which read as "still building". **Vercel creates a deployment record
even when it skips, and marks it Canceled** — and the Vercel dashboard
hides Canceled behind a status filter that defaults to 6 of 7, so the
skipped deployment is invisible until that filter is turned on. Counting
records answers "did Vercel react to the push", not "did it build".
The correct instrument is the deployment STATE (Canceled vs Ready), or
simply the duration.

### Honest accounting — what this actually saves

| date | total | `auto:` | code |
|---|---|---|---|
| 2026-08-07 | 26 | 23 | 3 |
| 2026-08-06 | 49 | **27** | **22** |
| 2026-08-05 | 40 | 32 | 8 |
| 2026-08-04 | 35 | 32 | 3 |
| 2026-08-03 | 51 | 33 | 18 |
| 2026-08-02 | 32 | 32 | 0 |

The 49-commit spike on 08-06 was 22 real code commits from a
development push — legitimate rebuilds this change would NOT have
prevented. It removes 27 of those 49. What it really fixes is the
permanent floor: ~30 `auto:` commits every day forever, including days
like 08-02 that were 32 commits and 100% automation. Expect ~78%
fewer builds on average, approaching 100% on days with no development.

### Accepted consequence, recorded rather than discovered later

The bundled CSVs now refresh only when a code commit lands, so the
Supabase-outage fallback degrades from "as fresh as the last build"
(minutes) to "possibly days stale". Supabase is the primary and that
fallback was always best-effort, but it is a real change.

---

## [2026-08-07] - the dead-man's switch could have been held green by the wrong host

### Done — the dead-man's switch is live, on Telegram, and verified

Two healthchecks.io checks, each pinged by a DIFFERENT host so neither
can mask the other:

| check | period / grace | pinged by | env var |
|---|---|---|---|
| NRFI Railway predictor | 15 min / 10 min | `watchdog.heartbeat()` | `HEALTHCHECKS_URL_PREDICTOR` |
| NRFI predict cron | 1 day / 1 hour | `daily.yml` | `HEALTHCHECKS_URL` |

Both alert to Telegram (direct message, not a group — ops alerts must
never land in a group per the standing rule) AND email. Both delivery
paths were TESTED, not assumed: "Delivered, now" on each. Both checks
are receiving real pings from their own host.

Verification note: the Railway ping was confirmed by watching the
service's own heartbeat arrive after a redeploy, not by hitting the ping
URL by hand -- which would have proved nothing about the code path.

### Fixed — Railway's heartbeat now uses its own env var (`watchdog.heartbeat`)

`daily.yml` pings `HEALTHCHECKS_URL` after every successful GitHub run,
and `watchdog.heartbeat()` read THE SAME NAME. Pasting one check URL
into both places would have made GitHub's hourly ping hold the check
green while Railway lay dead -- which is the single failure the
dead-man's switch exists to detect. Two hosts sharing one switch means it
reports "at least one of them is alive", which is not a useful sentence.

Railway now reads `HEALTHCHECKS_URL_PREDICTOR`. Different name, so the
two cannot be pasted into the same box by accident. It still falls back
to the old name rather than going silent, but prints why that is weaker
than the operator thinks.

### Corrected — the watchdog docstring overstated the gap

It claimed "an outage of Railway itself is currently undetected". Not
true: `runner_watchdog.yml` already polls /api/health-live and alerts
"RAILWAY IS DOWN" on a stale predict. The real gap is narrower and worth
stating precisely -- that check reaches Railway THROUGH VERCEL, so a
failed curl leaves `RAIL_MIN=-1` and the check is skipped SILENTLY. It
cannot tell "Railway is fine" from "I could not look." The dead-man's
switch is the answer to that specific blind spot, because it depends on
neither Vercel nor GitHub.

---

## [2026-08-06e] - the No.1 settle ping, and it reports losses too

### Added — BROADCAST 5: THE No.1 SETTLED (`build_top_pick_settled`)

Operator request: an instant ping the moment the No.1 lands, rather than
waiting for the end-of-night summary. FINAL RESULTS is gated on EVERY
game on the board grading, so on a night whose No.1 is an early game the
result sat unannounced for hours. This closes that gap: it fires on the
one row grading and makes no claim about the rest of the slate, so it
needs no time backstop.

Fires BEFORE final in `due_broadcasts`, so on a night where the No.1 is
also the last game the headline lands above the summary rather than
under it. Dedupe key `settled:{date}:{gamePk}` at 24h -- and
`discord_settled` was added to `_DEDUP_WINDOW_M` IN THE SAME COMMIT,
because the fallback is 5 minutes and the loop runs every 5 minutes,
which is exactly how THE BOARD published three times earlier today.

**IT FIRES ON A LOSS TOO.** The request was for a "won" ping. A channel
that pings on wins and goes quiet on losses is the oldest tell in paid
picks and would be read that way within a week -- by exactly the
subscribers paying for a verifiable record. It also destroys the asset
being sold: a record is only worth something if the losses arrive with
the same volume as the wins. FINAL RESULTS and THE LEDGER already
publish losses, so a win-only ping would conceal nothing and merely look
like an attempt to. The one-line change and its consequence are recorded
in the function docstring.

POSTPONED / SUSPENDED / VOID render as "NO ACTION -- no bet stands, and
nothing is added to the record" rather than being dressed as a result.

The running-record line comes from `tools/pl_calc.py --top-pick`, the
same source as THE LEDGER and filtered to the same date bound, so the
two messages cannot disagree about the record an hour apart.

Wired into `--which`, `--resend` and `tools/discord_retract.py`.

### Note — tonight's ping was deliberately suppressed

2026-08-06's No.1 had already settled and FINAL RESULTS had announced
the win before this shipped. A suppression record was written for
`settled:2026-08-06:825053` so the deploy could not announce the same
result a second time, out of order, to a paying channel. It starts clean
on the next slate.

---

## [2026-08-06d] - a bad post can now be un-posted; the stake agrees everywhere

### Added — message-id capture and retraction (`discord_notify.py`, `tools/discord_retract.py`)

Discord returns the created message object on every `?wait=true` post,
and the transport was throwing it away. Its `id` is the only handle that
can ever delete the message again, so the four posts carrying the
dashboard URL on 2026-08-06 had to be deleted by hand.

`_post_once` now returns that object, `_send_raw` collects one id per
DELIVERED PART (a split board is N messages, not one) and returns them
even when the overall send FAILED -- a board that dies on part 3 has
already published parts 1 and 2, and those are exactly the orphans
someone needs to retract.

Ids live in a NEW Supabase table `discord_messages`, deliberately not a
column on `notifications_log`. That table's write is what stops a
broadcast repeating; if id-capture shared it, a failure here could
resurrect the 5-minute-window bug from earlier today. Losing the index
costs the ability to UN-send; losing the dedupe row costs the ability to
NOT DOUBLE-send. Those are not equally bad, so they do not share a
failure domain.

    python tools/discord_retract.py --list --date 2026-08-06
    python tools/discord_retract.py --event toppick --date 2026-08-06 --yes

A 404 counts as success -- the operator may already have deleted it by
hand, and a tool that cries failure over an absent message trains people
to ignore it. Retraction deliberately does NOT clear the dedupe record,
so retract-then-replace stays a two-step human decision; an automatic
replacement is how a formatting bug becomes a post loop.

`send(..., force=True)` bypasses the dedupe CHECK for a deliberate
replacement but still writes the dedupe RECORD, so the 24h window
re-arms. `run_broadcasts` cannot set it; only `--resend <which> --yes`
can, which prints the full body first.

### Fixed — the dashboard and Discord disagreed about the stake (`kelly-sim.ts`, `BoardRow.tsx`)

Discord published "4 units" on tonight's No.1 while the board printed
"STAKE 3.00u" for the same bet. TWO independent causes, both silent:

1. **The Kelly implementations diverged.** `tracker.kelly_stake_units`
   rounds TWICE -- `round(x, 2)` then round to whole units -- so
   SD@ARI's exact 3.4975u became 3.5 became 4. `lib/kelly-sim.ts`
   rounded once: `Math.round(3.4975)` = 3. Every stake in [x.495, x.5)
   diverged, as did every exact half, because Python rounds
   half-to-EVEN and `Math.round` rounds half UP.
   `kelly-sim.ts` is now an exact mirror, including a `roundHalfEven`
   helper. Verified over **396,622 (probability, price) pairs across
   p=0.20-0.85 and prices -400..+400: 0 disagreements.** An early
   attempt using a 1e-9 tie tolerance still failed 18 of them --
   quarter-Kelly constantly produces values like 3.4949999999999997,
   which any such tolerance misreads as a tie and bumps by a whole
   unit. The comparison must be exact.
   **No published history moves: 0 of 372 historical STRONG rows
   change.**

2. **The board sized from a rounded probability.** `yrfiPct` is
   1-decimal (`board-supabase.ts:217`), so the chip recomputed from
   0.634 where tracker sized from 0.6343. On the real ledger that alone
   changes the stake on 5 of 372 rows, and no amount of arithmetic
   parity can fix it because the INPUT is lossy.
   `StakeChip` now prints `units_risked` -- what the bet was actually
   placed at, and what Discord published -- and recomputes only for a
   pre-lock row that has not been sized yet. This reverses the
   2026-07-30 decision to always recompute; pre-Kelly rows read 1.00u
   and mid-July rows read 5.97u, which is what was staked on those
   nights.

### Fixed — FINAL RESULTS could publish 20 minutes into an 11-game slate

The backstop that stops results being announced before the games was
anchored to the FIRST pitch of the slate. On a card running 12:35 PM to
9:40 PM that is 12:55 PM. Replaying a full slate with every row graded,
FINAL RESULTS and THE LEDGER both came due at 12:55 PM and published
"Every first inning on the board is complete" over ten games that had
not started.

`all(_terminal(r))` normally hides this, because rows only grade after
their own first inning. It bites precisely when that test goes trivially
true for the wrong reason -- a mass postponement (POSTPONED counts as
terminal), a stale re-read of a finished slate, or any grader bug that
fills the column early. That is the same shape as the 2026-08-05 replay
which motivated the backstop originally: the fix was right in kind and
one game short in degree.

Now anchored to `last_pitch_of_slate + 20 min`, so results cannot be
announced until every game on the board has had a first inning. Verified
on the degenerate all-graded slate: silent at 12:55 PM, silent at
9:00 PM with ten games in, publishes at 10:00 PM. THE LEDGER is nested
inside the same condition and inherits the guard.

Found by sweeping a simulated clock across a whole day rather than
checking a single moment -- the single-point check passed.

### Changed — THE BOARD now leads with the №1 (operator request)

Operator, 2026-08-06: *"the #1 pick needs to be highlighted most
importantly."* The board printed both strong plays as equal bullets
under "THE PLAYS (2)". But the №1 is not merely the first item in a
list -- it is the tracked product: the published record, the dashboard
hero and the separate lock-time broadcast are all ABOUT that one play,
so listing it as one of N under-sells the only number the service is
judged on.

It now gets its own `# ⭐ THE №1 PLAY` section at the top with the same
shape as the lock-time message, and the remaining strong plays move to
"ALSO PLAYING". User-facing headings across all four messages now say
"№1", matching the dashboard.

THE №1 IS RESOLVED FROM THE WHOLE SLATE, NOT FROM WHAT IS STILL
UNSTARTED. `top_pick` applies `tracker._row_is_nights_top_pick`, the
same gate the dashboard and the record use. Running it over the
not-yet-started subset would crown the best REMAINING play on a late
board, and "№1" would then mean something different in the channel than
it means in the record -- the same class of quiet divergence that
produced the 3u/4u contradiction. If the true №1 has already started,
the headline section is simply omitted.

Also corrected two stale docs in the same file: the module header still
advertised price "ladders" that were removed earlier tonight, and
`stake_for`'s docstring asserted the dashboard and Discord staking
implementations were "identical" -- which the parity work disproved.
Both now describe what the code actually does.

### Fixed — two blockers found by adversarial review before they shipped

Both were caught by a red-team pass over the retraction change, not by
testing it. Both are the kind that only surface in production.

**A retry on an AMBIGUOUS failure double-posted and orphaned the id.**
`_post_once` returns status 0 on a timeout/reset and 5xx on a gateway
error. In both cases the POST has already left the process and Discord
may have created the message before the response was lost, but
`_send_raw` counted them as "not delivered" and retried up to 4 times.
That is up to four identical messages in a paying channel, of which at
most one gets an id -- the exact un-addressable orphan the retraction
work exists to eliminate, introduced by the retraction work itself.
Execute Webhook has no idempotency key, so it cannot be fixed at the API
layer. Those two statuses are no longer retried; 429 still is, because a
rate-limited request was rejected rather than created. The `_send_raw`
docstring also claimed a failed send "lets the next cycle retry" -- it
does not, because `_notify_event_dedup_check` counts rows without
filtering on `delivered`. Corrected.

**THE BOARD published an already-settled game as a play to bet.**
`build_board` selected on `is_strong` alone and took no `now`, so it was
structurally incapable of knowing a game had started. Tonight's 8:43 /
8:49 / 8:58 PM boards each listed WSH@PHI -- 6:05 PM, already graded WIN
+2.50u -- under **THE PLAYS** with "Stake 3u" and a price floor. For a
product whose whole value is a verifiable FORWARD record, posting a
winner after it wins is indistinguishable from post-hoc winner claiming.
It is the FINAL RESULTS time backstop with the sign flipped, and the
board had no equivalent. `build_board` is now time-aware: started games
are dropped from the actionable sections and counted in one honest line
("_10 games already underway — not listed below._"). The T-60 trigger
path is unchanged, because nothing has started then. The footer also
stopped referring to a price ladder that was removed earlier tonight,
and now counts prices over the games actually LISTED.

### Added — build-time parity guard (`dashboard/scripts/check-kelly-parity.mjs`)

Two implementations of one money rule will drift, and when they do
nothing complains -- both keep returning a plausible number. The guard
COMPILES the real `lib/kelly-sim.ts` (a hand-copied duplicate would pass
while the shipped file was broken) and checks it against 5,398 cases
generated from `tracker.kelly_stake_units`, densest around the rounding
edges where they actually disagreed. Wired into `prebuild` beside
`check-units-guard.mjs`, so a drift fails `next build` rather than
reaching a subscriber. Verified it catches a reintroduced
single-rounding regression (310 failures) and passes on the fix.

---

## [2026-08-06c] - the Discord broadcasts repeated, and published the console URL

Two subscriber-facing defects on the first live night of the Discord
product. Both were found from the operator's own report; neither had any
alarm attached to it, which is its own finding.

### Fixed — a 5-minute dedupe window let every broadcast repeat (`49bc7682`)

`tracker._notify_event_dedup_check` ends in

    window_m = _DEDUP_WINDOW_M.get(event_type, 5)

and none of `discord_board` / `discord_toppick` / `discord_final` /
`discord_ledger` were in that dict, so all four inherited the FIVE
MINUTE fallback. The Railway loop runs every five minutes. Result, from
`notifications_log`: THE BOARD and TONIGHT'S No.1 each delivered at
8:43, 8:49 and 8:58 PM ET — three sends apiece to a paying channel, and
a suppression record written by hand to silence the board expired five
minutes later and let it publish anyway. All four now sit at 24h.

The dict now carries a comment saying why, because the next `discord_*`
event added without an entry reproduces this exactly.

### Fixed — the dashboard URL was published to subscribers (`49bc7682`, `d0e1a2b3`)

Operator: *"you need to never send the link to the actual dashboard
ever. that is only for me."* It was in three messages — the board
footer, the No.1 pointing at `/brief`, and the ledger pointing at
`/history` — and shipped in four delivered posts before it was caught.
The dashboard is an OPERATOR CONSOLE: it shows leans, passes with their
reasons, model diagnostics, the full ledger and the replay. The
subscriber product is the message itself.

The `DASH` constant is deleted outright rather than left unused, and the
transport's User-Agent header no longer carries the host either, so
there is nothing in the Discord path to reach for.

### Fixed — the stake printed from the ledger, not a re-derivation (`21c847c3`)

Discord said 4 units where the dashboard said 3 on the same play. Both
were recomputing Kelly instead of reading it: the raw stake was 3.4975u,
sitting on the 3.5 rounding boundary, and the dashboard fed the rounded
1-decimal probability (63.4%) where Discord fed full precision
(0.6343...). Rounding fell in opposite directions. Both surfaces now
print `units_risked` from the ledger, which is the number the bet was
actually placed at. The price ladder was dropped from Discord in the
same commit at the operator's request.

### Not fixed — the messages already in the channel

Four delivered posts contain the URL and cannot be retracted from here:
`?wait=true` returns the created message object, but the transport
discards it, so no message IDs were ever stored. They need deleting by
hand in Discord. Storing the returned ID would make a bad broadcast
retractable and is the obvious follow-up.

---

## [2026-08-06b] - the notification lag: two causes, one fixed, one reverted

Operator: *"the telegram notifications for some reason are all lagged."*
Two independent causes. One is fixed; the other was attempted, FAILED
VERIFICATION, and was reverted — recorded here so the next attempt
starts from evidence instead of the same wrong guess.

### Fixed — the loop drifted (`ee51746e`)

`workers/predictor_loop.py` ran `cycle(); time.sleep(300)`, making the
real period `cycle_duration + 300s` rather than 300s. `cycle()` runs
predict + grade + scrape + import + reconcile with subprocess timeouts
of up to 300s EACH, so a slow night silently turned the "5-minute loop"
into a 10-minute one and the drift compounded through the evening. Now
sleeps the REMAINDER: a 90s cycle sleeps 210s and the next still starts
on the 5-minute mark; an overrun starts immediately and logs it.

### The bigger cause, still OPEN — Railway rebuilds on every data commit

Railway's **Watch Paths were empty**, so every push rebuilds the
predictor service. The GitHub cron commits `data/` ~20-25x/day, and
each one tears down the container, reinstalls dependencies and restarts
the loop from zero — so the loop never establishes a cadence, and every
restart also resets the container CSV back to git (which is why
`step_sync_csv_from_supabase` has to exist at all). Deployment history
showed six rebuilds in five hours, all from `auto: predict` commits.

### ATTEMPTED AND REVERTED — `/**` does not match root-level files

Set Watch Paths to:

    /**
    !/data/**

reasoning it was fail-safe (if the negation were ignored we would fall
back to deploy-on-everything). **The verification proved otherwise and
the config was reverted within minutes.** Evidence from Railway's own
deployment list:

| commit | files touched | result |
|---|---|---|
| `47e47775` FINAL RESULTS | `discord_broadcasts.py` (ROOT) | **SKIPPED** — "No changes to watched files" |
| `auto: predict` | `data/**` (NESTED) | **Deployed** |

Exactly backwards. **`/**` matched the nested `data/` path but NOT a
root-level file**, so the one thing the config had to protect — code
reaching production — was the thing it broke. This matters here more
than in most repos: **11 of the system's Python entry points live at
the repository root**, including `tracker.py`,
`mlb_first_inning_predictor.py`, `scrape_dk_odds.py` and both new
`discord_*.py` modules.

**Lesson for the next attempt:** do not trust a glob's semantics on
this setting without testing BOTH directions, and prefer
`railway.json`'s `build.watchPatterns` (version-controlled, reviewable)
over the dashboard field. A correct allowlist must name root files
explicitly, e.g. `/*.py` alongside `/workers/**`, `/db/**`, `/tools/**`,
`/requirements.txt`, `/Procfile`, `/railway.json` — and must be proven
with a root-file commit before being trusted.

Watch Paths are empty again: deploys are noisy but correct, which is
the right way round to be wrong.

---

## [2026-08-06] - the alarm outside the building (run #3184 post-mortem)

Run #3184 reported `failure` with ZERO steps and no log. The operator
found it by chance an hour later, because nothing alerted.

### What happened — CORRECTED after SSH forensics on the runner host

**IT WAS A GITHUB ACTIONS OUTAGE. The VPS did nothing wrong.** Two
earlier theories were both WRONG and are recorded here so nobody
re-derives them: (1) the concurrency rule superseding a pending run —
refuted, #3185 was created 4s AFTER #3184 was already dead; (2) "the
Contabo box stopped answering GitHub" — refuted, it is the reverse.

githubstatus.com: **"Incident with Actions", impact CRITICAL, opened
2026-08-06T15:22Z**, still investigating at 18:20Z. GitHub's own
wording: *"Some workflow runs are failing to start or failing partway
through."* #3184 is "failing to start".

Mechanically, from the runner's own `_diag` listener log: GitHub's
backend returned **HTTP 503 ServiceUnavailable** to the runner's
`acquirejob` and `renewjob` calls — **152 of them between 16:00 and
18:00 UTC**, against a baseline of ~35 in the PREVIOUS MONTH. Session
renewal exhausted its 4 retries repeatedly (~once a minute, 17:01-17:19),
so GitHub's control plane could not keep the runner's session alive and
marked its own runner offline (`status=offline, busy=true` — busy
because it still thought a job was assigned). Job #3184 then had no
live session to be handed to, waited 16m57s, and GitHub gave up:
*"The job was not acquired by Runner of type self-hosted even after
multiple attempts"* (`runner_id: 0`, 0 steps, 22-byte log). Both
`run-actions-1-azure-eastus` and `run-actions-3-azure-eastus` 503'd, so
it was not one bad backend.

The host was provably healthy throughout, measured over SSH: **uptime
181 days (no reboot), zero OOM-killer events in the entire journal,
disk 27%, 8.4 GiB RAM available, runner service `NRestarts=0` running
continuously since Aug 1, api.github.com 200 in 31 ms, 0% packet loss.**
There is nothing to fix on that box.

Cost: one lost run out of 20, a 69-minute gap in GitHub-side refreshes
(16:10:49 → 17:09:32 per the runner's journal). No pick lost. The money
path (Railway) was unaffected throughout.

Root cause of the *invisibility*: `runs-on: ${{ vars.RUNNER_LABEL ||
'ubuntu-latest' }}` is not a failover. `||` tests whether the VARIABLE
is empty, never whether the machine is ALIVE — and `RUNNER_LABEL` is
set to `self-hosted`, so jobs queue against a dead box for up to 24h.
The one failure ping in daily.yml is a step INSIDE the job that never
started, wired to a `HEALTHCHECKS_URL` secret that does not exist here.

### Added

- **`.github/workflows/runner_watchdog.yml`** — `runs-on` HARDCODED to
  `ubuntu-latest` (a watchdog on the machine it watches cannot report
  that machine's death). Three signals from the runs API: a job unpicked
  20m+ (**this outage's exact signature — would have fired ~40 min
  before the operator noticed**), no success in 90m+, newest run failed.
  Schedule-aware (awake 13:00–03:59 UTC only): daily.yml is silent
  06:00–12:00 UTC by design, so a naive staleness alarm would cry wolf
  ~12× nightly and get muted. Stateless escalating dedupe (20/60/180m
  stuck; 90m/3h/6h/12h stale) → a day-long outage sends ~4 messages, not
  30. `force_test` dispatch input proves the chain end-to-end on demand.

### Fixed

- **The false comment** at `daily.yml:111`, which claimed workflows
  "still work … if the self-hosted runner is offline". Replaced with the
  real behaviour, the manual recovery, the hosted-minutes billing
  caveat, and the note that the only true failover is a second runner
  sharing the `self-hosted` label (`runs-on` with two labels means
  "must have BOTH").

### Found while testing — UNFIXED, needs the operator

- **GitHub-side Telegram alerts to the "Backfist Bets" channel have been
  failing silently for months.** The `force_test` run delivered
  `HTTP 200` to the operator's DM and **`HTTP 400`** to the channel.
  Cause: the group was upgraded to a supergroup, which CHANGES its
  chat id. Railway's env has the new id (`-1003953933618`, type
  `supergroup`); the GitHub secret still holds the old one
  (`-5115372935`, type `group`, `can_send_messages: false`) and was last
  updated 2026-05-02. Nothing logged it — `system_errors.csv` has 0
  `telegram-send` rows. Railway-sent alerts (BET LOCKED, pre-game) reach
  the channel fine; GitHub-sent ones (results, digest) never have.
  **Fix: update the `TELEGRAM_CHAT_ID` repo secret to
  `5285688562,-1003953933618`.**

### KNOWN LIMIT OF THE WATCHDOG — read before trusting it

**The watchdog runs ON GitHub Actions, so it cannot alert you about a
GitHub Actions outage.** It is pinned to `ubuntu-latest`, which makes it
survive the *runner box* dying — the case it was built for — but during
an Actions-wide outage the watchdog's own scheduled run may never start
either. Today's incident is precisely that case, so the alarm shipped
in this entry would NOT have caught today's failure.

Truly outage-proof alerting has to live somewhere GitHub does not
control. This system already has two such places: the **Railway**
service (independent, already running a 5-min loop, already holds the
Telegram credentials) and **cron on the runner VPS**. Either can poll
the GitHub API and ping Telegram when runs stop. Not built yet.

### Still open

- **Why the Contabo box "went offline" is now ANSWERED** (GitHub's
  outage, above) — but note the runner self-heals and needs no
  intervention. Do NOT flip `RUNNER_LABEL` to `ubuntu-latest` during an
  Actions outage: GitHub-hosted runners are affected too, usually worse.
- **The "backup" DraftKings scrape is fiction** — 403 for 90 consecutive
  days, no odds file written since 2026-05-04. Railway is the ONLY odds
  source, with no backup. Unrelated to this outage; the bigger risk.

---

## [2026-08-05g] - Telegram follows the redesign: №1-only notifications

Operator: *"we need to fix the telegram notifications. it should only
be sending out the #1 pick now."*

### Changed

- **All nine pick-facing Telegram notifiers now pass through one gate,
  `tracker._row_is_nights_top_pick`**, and stay silent for any STRONG
  play that is not the night's №1: flip-to-strong, bet-locked,
  graded WIN/LOSS (both the tracker path and end_of_day_check's),
  voided, pregame reminder, CLV move, weather change, starter scratch.
  The tentative-lean recap (`_notify_lineup_pending_resolved_telegram`)
  is disabled outright behind a commented early return — those are
  PASS rows, never the №1.
- **The №1 rule is the dashboard's rule, verbatim**: mirrors
  `dashboard/lib/top-pick-rank.ts` (confidence in the side bet, then
  the better price, then the game name; a missing price cannot win a
  tie-break). The gate treats the passed row as the FRESH state of its
  own game and ranks it against the rest of the field from the ledger,
  so a mid-run flip is judged correctly even before the CSV write.
- **Fail-open**: if the ledger is unreadable or a field won't parse,
  the alert sends (a wrongly-silenced №1 alert costs the product's one
  notification; a stray extra costs an eye-roll) and logs to stderr.
- **Ops alerts deliberately untouched**: strong_orphan_no_odds (the
  manual-odds heal workflow depends on it), bankroll milestone, daily
  digest, heartbeat, loss-cluster monitor.

Tested against the live ledger (tonight's №1 TB@COL passes; garbage
fails open) and synthetic fields: stronger rival silences the weaker,
an exact confidence tie is decided by the better price, an unpriced
rival cannot win the tie, LEAN rivals are ignored.

---

## [2026-08-05f] - the last two rooms: record zone + expanded row join the redesign

Operator: *"do the performance panel and expanded row view too."*

### Changed

- **The record zone speaks the page's grammar.** RoiPanel's head gains
  the heavy-rule-and-eyebrow treatment and is retitled **"The record ·
  the whole system"** (was "Performance") — with the №1 pick leading
  the page, this zone is explicitly the whole system's book. The "Why
  the system did that" zone's hairline was promoted to the same 2px
  rule. No money math touched anywhere in this commit — labels, rules
  and copy only.
- **DayReconcile speaks English**: "not replayed yet" → "replay
  pending" (same claim, stops reading as breakage; still distinct from
  "the replay passed"), "λ below the YRFI run floor" → "expected runs
  below the YRFI floor", ticker-strip "MODEL REPLAY not replayed yet"
  → "MODEL REPLAY pending".
- **Expanded row plain-English pass** (GameDetails):
  - "Blended inputs 3/4" → **"Inputs with real data 3/4"** with a
    tooltip; meaning verified against the predictor (how many of the
    two starters + two lineups carried real data vs league-average
    fallback). Park factor gains a tooltip.
  - Pick diagnostics translated: sub-line "T4.2 priors-pooling status,
    pitcher data quality, calibrator band" → "The data-quality checks
    the model ran on this game"; "xera shrinkage (T4.2)" → "thin
    pitcher data" with a sentence ("too few innings to take his 2.71
    at face value, so the model blended it toward the league norm");
    "pitcher_q tag: ltd" → "pitcher sample size: limited" (codes
    mapped at render, unknown codes pass through); calibrator flat-zone
    paragraph rewritten as "confidence band"; footer drops the tool
    name and ticket number.
  - LambdaMeter's screen-reader label now says "Chance a run scores in
    the first inning: 71.3%" instead of "P(YRFI) 0.713".

Verified on a local prod build with headless screenshots: both zones
carry the rule grammar, "Inputs with real data 4/4" renders in the
expanded row, replay strip reads "pending".

---

## [2026-08-05e] - the paper gets ink: deepened palette + polish pass

Operator: *"do all 3 polish items. i still feel like the all white is
really hard on the eyes, and the ui should be improved."*

### Changed

- **Paper deepened.** `--background` #FBFAF7 → **#F2EDE1** (aged
  newsprint, visibly not white) and — the load-bearing inversion —
  cards are now **lighter than the page** (`--card` #FBF8F1), so a card
  reads as a fresh sheet on a desk instead of a hairline on glare. All
  companion tones deepened in step (muted/accent/border/rule), and the
  money inks nudged one step darker to hold their ratios on the deeper
  surfaces: gain #137355→#0F6A4E, loss #A01D14→#9A1B12, attn
  #845608→#7A5007. Every text token RE-MEASURED (WCAG): ≥5.0:1 on
  background, muted and card; `--rule` 3.79:1. Not a palette reversal —
  same hues, darker paper; matrix/cyan-rose/warm stay retired.
  CLAUDE.md, PRODUCT.md and the palette memory updated with the new
  values in the same commit.
- **The board gets a section head** — the hero's rule-and-eyebrow
  grammar carried down: a 2px rule, "THE BOARD · GAME BY GAME", and the
  shown/total count on the right.

### Fixed (the three polish items)

- Ticker's `λ̄ 0.753` — the last bare notation on the page — now reads
  **"avg runs 0.75"** with a plain-English tooltip.
- /history's masthead title now uses the Fraunces display serif,
  matching the homepage hero: two pages, one voice at headline sizes.
- Hero deck line width 62ch → 74ch, killing the orphaned "63.0%." wrap
  on desktop.

---

## [2026-08-05d] - the front page: the №1 play IS the product now

Operator: *"i want the #1 pick system to be our main system. we still
track the total system record and units, but the #1 pick is the main
system. the current dashboard is just so bland, so many confusing
things, the ui seems broken, the controls are all weird."*

### Added

- **`TopPlayHero`** — the homepage lead is now one story, newspaper
  front-page grammar: double rule, "THE №1 PLAY" eyebrow, the matchup
  in Fraunces display type ("Tampa Bay at Colorado"), a plain-English
  deck ("The bet: a run scores in the first inning"), and the money
  line as label-over-figure pairs — side · price · stake · bet-up-to ·
  first pitch. Settled nights get a WON/LOST rubber stamp with the
  realized units; pending nights show the lock countdown; a no-play
  night renders calm and intentional per PRODUCT.md. The #1 system's
  REAL-money record rides under it as a credentials row (45–21 ·
  hit% vs needs% · units at ¼-Kelly · last 10), from the same
  `loadTopPickReport` /history uses, so the two surfaces cannot quote
  different records. Other STRONG plays appear as one "Also on the
  card" line. Which game is #1 comes from `selectTopPick` — the same
  rule as the board badge and /brief.
- **`RunJobControl`** — the GitHub Actions predict/grade dispatch
  buttons, extracted from the board's filter row into the Settings
  menu under "Run the pipeline". Ops machinery is not a view control.

### Changed

- **`TonightsActionCard` unmounted** (file kept). It counted things and
  needed a paragraph to explain its own chips; its totals live in the
  ticker and its play list is the hero now.
- **ControlPanel speaks English**: sort options are "Best YRFI chance
  first / Most expected runs first / …" instead of "P(YRFI) high → low"
  and "λ high → low" (keys unchanged, so persisted filters survive).
  Side order is All · NRFI · YRFI · Pass (Pass sat between the two
  sides it is not one of). Date options render "Wed · Aug 5" instead of
  raw ISO. The board row's visible "λ 0.72" chip is now "runs 0.72".
- **/history leads with the #1 pick system**: the real-money record
  card and its full working (cumulative units, month-by-month, every
  play) moved above the simulated whole-system replay, which is
  retitled "The whole system · ¼-Kelly · Simulated" and keeps the
  total record + units the operator still wants tracked. Masthead:
  "The №1 pick, then the whole system".

### Fixed

- **The permanent "Not enough data yet — the calibration figures could
  not be read" card** on the homepage now renders nothing when the
  file is unreadable or too thin to plot. A missing diagnostic is a
  zero-pixel outcome, not an error surface that makes the UI look
  broken.

Verified on a local production build (`next build` + `next start`,
per the dashboard-verification-trap rule): hero + credentials render
with real figures, Fraunces at 52px desktop / 30px at 375px wide, no
horizontal overflow at phone width, Settings menu hosts Predict/Grade,
history section order confirmed by text position, zero console errors.

---

## [2026-08-05c] - health badge un-pinned: the 403 wall is a notice, not an error

`/api/health` said DEGRADED whenever ANY `system_errors.csv` row landed
in the last 24h — and the IP-blocked GHA backup scrape logs ~29
"Fetch failed: HTTP Error 403" rows every day (see [2026-08-05b]).
Net effect: the badge was mathematically incapable of showing OK for
months, and `/api/health-live` idled at "warn" the same way
(`errorsLastHour ≥ 1`). An alarm that is always on protects nothing.

### Fixed

- **Both health routes now classify the known-blocked scrape as a
  NOTICE, not an error** — extending the existing T3.14 notices
  mechanism. The match is the SIGNATURE (`step == scrape-dk-odds` AND
  terminal `Fetch failed: HTTP Error 403` line), not the step name, so
  a scrape failure with any other ending (read timeout, the
  [2026-08-05b] zero-markets rotation) still counts as real.
- **`/api/health-live` now truncates messages at response time, not
  parse time** — the terminal 403 line sits past char 200 of the
  logged stderr tail, so the old parse-time `.slice(0, 200)` would
  have blinded the classifier.
- `/api/health` gains `knownNoiseCount24h` so debugging sessions can
  still see the journal is alive. `system_errors.csv` itself is
  untouched — the journal keeps recording everything; only the badge
  arithmetic ignores the wall.

---

## [2026-08-05b] - DK odds restored: the subcategory id rotated overnight

First slate with ZERO odds captured (0/15; prior 7 days were 100%).
`bet_placed` blank on all 15 rows — the day's STRONG pick had no price,
no stake, no bet. Diagnosis unpicked three layers:

1. **The GHA scrape's 403 is old news, not the cause.** DK's CDN has
   rejected GitHub-hosted runner IPs on effectively every run since
   ~2026-05-04 (last committed `data/odds/dk_*.csv` is 05-04; ~27-30
   logged failures/day for weeks). The workflow itself calls its scrape
   step "(backup)".
2. **The real odds source is the Railway `MLB-first-inning` service**
   (`PREDICTOR_SCRAPE_DK=enabled`, 5-min loop; the `worker` service is
   scoreboard-only). It is not IP-blocked — and on 2026-08-05 its
   fetches started returning **200 with zero runs markets**.
3. **DraftKings retired subcategory 11024 ("Runs - 1st Inning")
   overnight** and replaced it with **20150 ("1st Inning Runs")**. Same
   market name, same O/U 0.5 selection schema, new id. Bonus trap: the
   category-1024 endpoint now returns only its default subcategory's
   markets (Hits Exact), so filtering the category response can never
   find runs again — the subcategory endpoint must be fetched directly.

### Fixed

- **`scrape_dk_odds.py`: `RUNS_1ST_SUB` 11024 → 20150**, and both fetch
  paths now hit `.../categories/1024/subcategories/<id>` via
  `_dk_market_url()`. Verified live from a residential IP: 12/15 games
  priced (3 already locked — first pitch pulls the market, expected).
- **Self-heal for the next rotation**: when a fetch parses 0 runs
  markets during prime hours (9am-5pm ET) with nothing captured yet
  today, `discover_runs_subcategory()` reads DK's own subcategory
  catalog, finds the category-1024 entry named like "Runs", and retries
  once with that id — logging the new id loudly. Guards keep the extra
  fetch pair off overnight/post-lock/off-season ticks. Tested by
  forcing the dead id through `main()`: WARNING fired, discovery
  returned 20150, healed run wrote 12 games in 2 fetches.

### Notes

- Railway auto-redeploys this branch on push, so the fix reaches the
  odds loop with no extra ops. GHA stays 403'd either way; Railway
  remains the capture path.
- The 3 already-started games of 2026-08-05 are permanently un-priced
  (never captured before lock). Do not backfill — `manual_odds_overrides
  .csv` is the only sanctioned heal if the operator has the numbers.

---

## [2026-08-05] - deploys un-bricked: the backup prune that never pruned

Every Vercel production deploy errored from 11:11 UTC (the daily backup
snapshot commit) to 18:19 UTC — 10 consecutive ERROR builds — while the
live site kept serving the 07:42 build. `/api/health` said it plainly:
`"No data refresh in 649 min (prime hours)"`. The build compiled fine;
the failure was at packaging:

> The Vercel Function "api/active-demotions" is 251.15mb uncompressed
> which exceeds the maximum uncompressed size limit of 250mb.

Two stacked causes:

1. **The 30-day backup prune never fired once.** `backup.yml` pruned
   with `find -mtime +30`, but `actions/checkout` stamps every file with
   the clone time, so no file ever *looked* older than 30 days. 94 daily
   snapshots accumulated (2026-05-02 → 2026-08-04), 217+ MB tracked.
2. **The whole tracked `data/` tree ships inside every serverless
   function.** Route handlers probe `../data` at request time
   (`dataDir()`), so Next's file tracer conservatively pulls the entire
   repo data dir into each function bundle. Wanted for picks CSVs and
   `cluster_demotions.json`; fatal once backups pushed tracked `data/`
   to ~245 MB. The 2026-08-05 snapshot (+4.25 MB) was the straw.

### Fixed

- **Pruned 64 stale snapshots** (2026-05-02 → 2026-07-05, ~108 MB,
  4,358 files) via `git rm` — recoverable from git history. 31 daily
  snapshots (2026-07-06 → 2026-08-05) + the 6 named model-weight
  backups remain.
- **`backup.yml` prune now compares the snapshot's date-named directory
  against a `date -u -d '30 days ago'` cutoff** (ISO dates compare as
  strings) instead of mtimes. The existing `git add data/backups`
  commit step already stages deletions, so prunes reach the repo.
- **`dashboard/next.config.mjs` gains `outputFileTracingExcludes` for
  `data/backups/**`** (both `../`-relative and `**/`-anchored
  spellings), so backups never enter a function bundle again regardless
  of how large the directory grows. Verified on a local prod build:
  0 `data/backups` entries across all `.nft.json` trace manifests,
  boards/picks entries still present.

### Notes

- GitHub Actions itself had **zero failed runs** — the red annotations
  are (a) this DK odds 403 (see below) surfaced by the error logger and
  (b) a Node 20 deprecation warning on `actions/checkout@v4` /
  `actions/setup-python@v5` (harmless until GitHub drops the fallback).
- **Separate:** odds capture also died on 2026-08-05 (0/15 games
  priced). Root cause turned out to be a DK subcategory-id rotation,
  not the 403 — fixed the same day, see [2026-08-05b] below.

---

## [2026-08-04b] - the card gets a price limit: "bet up to -234"

Operator: *"shouldn't it be 'Bet up to -XXX' so people know to not bet
over that number?"*

### The card was half an instruction

The board printed the stake and the price it was sized at, and nothing
else. A subscriber who opened DraftKings and found a different number
had nothing to check it against — and the stake is a FUNCTION of the
price, so the right bet changes as the price moves. Tonight's #1 is 9u
at -125; the same play is 3u at -200 and no play at all past -234.

### Added

- **`dashboard/lib/price-ladder.ts`** — pure, client-safe (BoardRow is a
  client component, so this may never reach `node:fs`; it imports only
  from `lib/kelly-sim`, which has no imports of its own). Builds the
  rungs from `stakeUnitsFor`, the same function that prints the stake
  chip, so a rung can never contradict the stake beside it.
- **`LimitChip` on the board row** — `BET UP TO -234` next to the stake.
  Renders on exactly the condition StakeChip's primary path uses, so the
  limit and the stake always appear together or not at all.
- **`PriceLadderPanel` in the expanded row** — the full ladder, a rung
  per whole unit down to 1u, then the pass line. The row carries only
  the limit because the row is read on a phone in ten seconds.

### The limit is deliberately NOT break-even

Break-even on the 07-31 CWS@TB play is -248 — the price at which the bet
becomes worthless. Publishing that tells a subscriber to lay a number
with nothing in it. `passAt` is instead the worst price still worth a
FULL UNIT of quarter-Kelly, which is -234.

That stops short of the shipped stake rule ON PURPOSE. `stakeUnitsFor`
keeps betting past `passAt` because `KELLY_ROUNDED_FLOOR` lifts a
sub-half-unit stake to 0.5u instead of discarding it (that floor recovers
15 of 16 bets plain rounding would drop — see `tracker.py`). The
consequence is real 0.5u money on almost no edge: at -140/-141/-142 a
58.9% pick wants 0.34u / 0.24u / 0.13u and stakes 0.5u at all three.
Those are precisely the bets someone would take AT a published limit, so
the ladder ends at the last full unit. Operator's call. **Do not "align"
the two.**

`passAt` uses `Math.ceil`, not `Math.round`: the solve returns a
fractional price (-133.6) and rounding it to -134 would publish a limit
one cent past the full-unit line — a price we tell people to take and
would not take ourselves.

### Cards with no room are a normal state, not an error

When the card price is already at or past the full-unit line the ladder
reports `noRoom` and the UI says "take -140 or better" instead of
printing a "bet up to" that is a BETTER price than ours. Two of ten
plays sampled on 08-04 were like this (0.5u and 1.0u cards).

### Verified

Against a **production** build (`npm run start`, per the dev-cache trap),
not dev. Tonight's #1 SD@ARI at 71.3% / -125 renders `STAKE 9.00u` +
`BET UP TO -234`, ladder `-125 9u … -228 1u`, `-234 or worse pass`.
LEAN rows correctly render neither. Measured at 375px: no page overflow,
no nested scroll (the 15rem cap was putting the common case in a scroll
box inside an already-scrolling drawer — now 24rem). Both themes clear
contrast on `--muted`: `--attn` 5.51 light, 6.85 dark. `tsc --noEmit`
clean, units guard passes.

---

## [2026-08-04a] - pending picks get briefs; the brief link becomes a real button

Operator: *"when i click a specific brief, it just takes me to the brief
for the #1 pick... i also want to make the brief pages look better. it
should be an actual button too thats filled."*

### The bug was coverage, not routing

Routing was verified correct on production — clicking a specific brief
navigates to that game, URL and content both. What was wrong is which
rows HAD a brief. The rule was "STRONG or LEAN only", and on the 08-04
slate **14 of 15 games were LINEUP PENDING**, which is a PASS in the
ledger. So the only brief button anywhere on the board was the #1's, and
clicking "a brief" could only ever land on the #1.

### Fixed

- **A LINEUP PENDING row with a clear tentative lean now gets a brief.**
  It is not the model declining to have an opinion — it is the model
  waiting, and the board already prints its tentative lean. Critically,
  every figure the brief shows is ALREADY FINAL: the park rate, both
  teams' last-10 first innings, both starters' scoreless-first records
  and the head-to-head all come from team codes, pitcher ids and the
  ledger. **None of them reads the lineup.** Only the verdict is
  provisional, so the page says so and prints no stake. Tonight's board
  went from 1 briefable game to 8.
- **STARTER PENDING stays excluded**, and that is the line: there the
  pitcher data is league-average fallback on BOTH sides, so the two
  biggest figures on the page would be fabrications. A pending row whose
  tentative is itself a PASS stays out too.
- **The side comes from the verdict, not the row.** A pending row's
  `pickSide` is literally `"PASS"`; reading it would have briefed the
  wrong half of the inning and scored every reason against it. `oddsOn()`
  takes an explicit side for the same reason — it was quoting the NRFI
  price on a YRFI lean.
- **`classifyTentative` + `DEFAULT_THRESHOLDS` hoisted** out of the
  client-only `BoardRow.tsx` into `lib/classify.ts`, so the server-side
  brief page can share the one copy. Same move `lib/top-pick-rank.ts`
  records, same reason. `briefVerdictOf()` there is now the single
  briefable rule, called by BOTH the page and the board's button.

### Changed — the visual pass

- **The brief link is a filled button**: ink fill, paper text, 15.9:1,
  44px, the shared `--shadow` scale, arrow nudge on hover and a press
  state that doesn't move the layout. It is the only action in the
  drawer, so by the one-primary-CTA rule it takes the solid treatment.
- **The ticket is a card.** Four naked rows between two hairlines made
  the most-read block on the page — what the bet IS — the least defined
  thing on it.
- **The record row is four stat tiles**, so the figures the operator
  quotes are separable at a glance while talking.
- **The picks list is a row of lifting cards.** The old hover inverted
  each row to solid ink, which forced every chip inside to have its
  colour overridden back — so the "lean · not bet" marker lost its own
  ink at the exact moment it was pointed at. Those overrides are gone.
- **The lean / pending note is a proper callout** with a tinted surface.
  It is load-bearing: it is what stops a tracked call being staked.

No new colour: everything works through existing tokens. Verified on a
production build in light AND dark, desktop and 375px: contrast 15.9
(button), 14.31 (list card), 7.45–7.98 (`--attn` figures in dark), no
horizontal overflow, pending chips carry the word plus a dashed edge.

---

## [2026-08-03m] - the brief's ballpark sentence names a club, not a "city"

Operator, on seeing the STL at NYY brief: *"fix the yankees ballpark
wording."*

### Fixed

- **"a run has scored in the first inning 55% of the time in the
  Yankees."** The park sentence in `lib/pick-reasons.ts` read
  `in ${cityOf(home)}`, and for five clubs the `city` field is a CLUB
  name because the city is shared or would not identify the team — LAA,
  LAD, NYM, NYY, OAK. Now `at home for ${clubOf(home)}`, which is
  grammatical for all 30 and is also exactly what a park factor
  measures: the home team's home games.
- **A second, quieter bug in the same line.** Both Chicago clubs
  rendered "in Chicago", so the sentence named neither ballpark. "At
  home for the Cubs" / "for the White Sox" separates them.
- `BriefView`'s no-park-factor fallback moved to `clubOf` too, so both
  mentions of a ballpark on the page name the same thing.

### Two alternatives rejected, recorded so they are not retried

- **A possessive** ("the Yankees' ballpark") needs a different
  apostrophe for the Red Sox and the White Sox — 30 chances to be wrong.
- **Real venue names** ("at Coors Field") read best of all and are still
  wrong to ship: 30 hard-coded names go stale on naming-rights deals and
  relocations, and saying the wrong stadium into a camera is worse than
  saying a plain phrase.

`cityOf()` now carries a doc comment saying it must never follow "in",
because that is the trap, and it is invisible until a brief happens to
land on one of the five clubs.

Verified on a production build: all 30 clubs rendered, plus the live
2026-08-03 briefs for NYY ("at home for the Yankees") and COL ("at home
for the Rockies", with the season-alone divergence clause intact).

---

## [2026-08-03l] - a brief for every pick, and a door into it from the row

Operator: *"can we make an individual brief for every single pick of the
day, including leans. skip passes. and i want to be able to navigate to
the brief by adding a brief button inside each pick dropdown."*

### Added

- **`BriefLink` — the door.** Every expanded row on the board whose pick
  is STRONG or LEAN now leads with a "Read the brief" button linking to
  `/brief?game=<gamePk>&date=<slate>`. First child of the drawer, above
  the notice stack: the notices are conditional, so anchoring below them
  would put the same control at a different height on every row. 44px in
  pixels, not rem — the app's root font is 15px, so a rem touch target
  comes out short. `components/GameDetails.tsx`.
- **Three honest dead ends** on `/brief`, where there was one. A game
  the model PASSED on says so and explains that writing a brief for it
  would mean inventing the case; a gamePk not on the slate says the link
  points at another date; an empty slate keeps the old "no play tonight".
  All three render the slate's pick list underneath, so every dead end
  has a way out.
- **`?date=`** on `/brief`, carried by every in-page link. Without it a
  brief opened from an old slate resolves against tonight's board and
  reports its own game missing.

### Changed

- **`/brief` briefs any STRONG or LEAN row, not only the #1.** A LEAN is
  a verdict the model committed to and the ledger grades — there is
  something true to explain. A PASS is the model declining to have an
  opinion, so it gets no page. Same filter on both ends: the brief page's
  `isBriefable()` and `BriefLink`'s guard.
- **A lean says it is not a bet, three times**: in the tag above the
  matchup (`LEAN · NOT BET`), in the ticket where the stake would be
  ("nothing — this one is not bet"), and in a ruled paragraph under it.
  Deliberate repetition — this surface is read ALOUD, and a qualifier
  stated once is the one that falls off in the edit.
- **`stake` is null for a LEAN before it reaches the view.** Quarter
  Kelly will happily size a lean's probability, and the figure would be
  plausible, set at 20px in the ticket, and read out. The tracker marks
  every lean `bet_placed='N'` by rule, so printing a stake beside one is
  an instruction to risk money the system never intends to risk. Same
  guard the board's stake chip runs.
- **"Also on tonight" carries every committed pick, each tagged** `#1
  BET` / `BET` / `LEAN · NOT BET`, ordered #1 first, then bets, then
  leans by game time. The list used to be STRONG-only, so "on the list"
  and "wagered" were the same thing and neither needed saying.
- **The #1 record block names whose record it is** when the brief on
  screen is not the #1. The block follows whichever game was #1 each
  night; on any other brief the unqualified title would be read aloud as
  that game's record.
- The brief's season now comes from the SLATE date, not the server's
  clock, so an old slate reads the right season's ledger.

Verified against a production build on the 2026-08-03 slate: 1 STRONG +
3 LEAN got briefs and buttons, 3 LINEUP PENDING and 1 NO EDGE got
neither. Every new text token clears 4.5:1 (`--attn` at 6.07, ink at
15.9); no horizontal overflow at 375px; the drawer button measures 44px.
Colour is reinforcement only — every lean marker prints the word.

---

## [2026-08-03k] - 2026 pitcher-id resolution fixed; the fair refit test finally runs

Operator: *"fix the 2026 game_pk resolution so we can test properly."*

### The bug was one lookup

`backfill_pit_pitching_stats.py` resolved pitchers only through
`pitcher_id_cache.json` (game_pk -> ids). **The 2026 backtest files carry
`away_pitcher_id` / `home_pitcher_id` columns directly** and the
2024/2025 files do not, so 2026 went almost entirely to the
league-average fallback. The row's own columns now win, with the game_pk
map kept as the fallback for the older files.

| file | league-avg fallback BEFORE | AFTER |
|---|---|---|
| 2026-04-01..05-11 | 43.2% | **9.2%** |
| 2026-05-12..05-26 | **100%** | **11.5%** |

Season-to-date coverage went 12.7% -> 39.6% and 0% -> 69.2%. The April
file leans on prior-season (51.2%), which is correct: few starts exist
that early, and the prior season is complete so it cannot leak.

### The test it unblocked

Refit twice on **production's own window** (2024 + 2025 + 2026 through
05-11), once leaky and once clean, held out on the 866 graded 2026 games
from 05-27. Rank-matched, flat 1u:

| N | PRODUCTION | refit LEAKY | refit CLEAN |
|---|---|---|---|
| 40 | **+10.5** | +3.6 | +6.0 |
| 80 | **+11.9** | +9.1 | +5.2 |
| 120 | **+16.0** | +7.3 | +10.9 |
| 160 | +11.8 | **+15.4** | +14.5 |
| 200 | +7.0 | +15.2 | **+18.1** |
| **sum** | **+57.3** | +50.6 | +54.8 |

### Three findings

1. **The leak fix is worth something, and this is the first test able to
   see it.** Clean beats leaky at 3 of 5 counts and by **+4.2u** on the
   sum. The earlier comparison could not show this because both
   candidates were handicapped by a missing year of training data.
2. **Neither refit beats production**, but clean is now close: +54.8
   against +57.3 over ~600 bets. That gap is well inside noise, so the
   honest reading is "no better", not "worse".
3. **Production's edge is concentrated at the top of its ranking.** It
   wins clearly at N=40/80/120 and LOSES at N=160/200. The refits are
   better in the tail. That is consistent with the #1-pick pattern seen
   earlier the same day, and it is the one genuinely new lead here.

**Still does not ship.** Sixth refit variant tested, sixth to fail to
beat the frozen 2026-05-26 weights.

### Caveat on the Kelly columns

Kelly stakes above were computed on RAW two-stage output for all three
models, because a candidate has no calibrator of its own. Stake size
depends on probability magnitude, so those figures are not comparable
across models; only the FLAT column, which uses ranking alone, is.

---

## [2026-08-03j] - Production refit on the clean files: TESTED, does not ship

Operator: *"refit the production model on the clean files. then backtest
it and compare profit."* Done. **It does not beat the incumbent.**

### What was run

`two_stage_model.py --phase-e3-vshand` (the live 19-feature architecture)
fit twice on 2024+2025, once on the leaky files and once on the `_ptfix`
ones, so the only difference between the two candidates is the leak.
Saved to `data/candidates/`, production untouched. Scored against the
881 graded 2026 games since 05-26 that carry a captured DraftKings price.

### The methodological correction that decided it

At the live `p < 0.42` gate the refits bet **17 and 21 games against
production's 136** - not because they are worse, but because a gate is a
cut point on a distribution and a differently-trained model puts its
probabilities on a different scale. `2026-08-02_architecture_and_skip_rules`
already records the rule: **never compare architectures at a fixed
probability cut; match bet count by rank.** Re-run that way:

| N (each model's own top-N) | PRODUCTION | refit LEAKY | refit CLEAN |
|---|---|---|---|
| 40 | **+10.5u** | +4.7u | -0.2u |
| 80 | **+11.9u** | +7.6u | -0.1u |
| 120 | **+14.0u** | -0.2u | +1.3u |
| 136 | **+18.1u** | -2.0u | +1.3u |
| 180 | **+10.2u** | +4.2u | -3.1u |

Flat 1u. Production wins at **all five** counts.

### Two conclusions, and they are different

1. **The refit does not ship.** That is now the fifth refit variant to
   lose to the frozen 2026-05-26 weights (see
   `2026-07-28_refit_tested_dont`, which failed four holdout lengths).
2. **The leak fix did NOT produce the loss, and did not produce a gain
   either.** Clean vs leaky head to head is 2-3 on flat units across the
   five counts - a wash inside noise. Both refits lose to production for
   the same reason: they were trained on 2024+2025 only, while production
   also saw 2026 through 05-11, and that 2026 data is doing real work.

### Why a like-for-like clean refit is still blocked

Adding 2026 to the clean training set is not currently possible: the
2026 backtest files resolve only **12.7%** (Apr-May 11) and **0%**
(May 12-26) of pitcher-slots to season-to-date figures, the rest falling
back to league average. Fixing 2026 game_pk -> pitcher-id resolution is
the prerequisite for a fair test, and until then this comparison cannot
be run without handicapping the candidate.

**The leakage fix remains correct and worth keeping** - training on
future data is wrong regardless of whether removing it happens to help
this particular holdout - but it is not, on this evidence, a money
improvement.

---

## [2026-08-03i] - The training-data leakage is fixed, and it moved the weights

Operator: *"yes fix the leakage. but dont lose focus on my goal."*

### The leak, measured

`era` / `fip` / `whip` / `k9` / `bb9` / `hr9` in the 2024+2025 backtest
CSVs were SEASON-FINAL values, so an April game was predicted with the
pitcher's September numbers. Pitchers with >=5 starts, share with ZERO
within-season variation in `home_fip`: **92%**. Present in the `_pit`
files too; only `xera` was already point-in-time.

### The fix

`tools/backfill_pit_pitching_stats.py` rebuilds all six from
`data/cache/pitcher_gamelog_v2` (per-start ip/er/k/bb/hr/h, 2021-2026)
using ONLY starts before the game's own date, resolving pitchers through
`pitcher_id_cache.json` (game_pk -> ids, 100% on 2024, 90% on 2025).
cFIP is derived per season rather than hardcoded, defined as whatever
makes league FIP equal league ERA. Fallback ladder, counted and printed:
season-to-date if >=20 IP, else prior season's final line, else league
average. Writes `*_ptfix.csv`; originals untouched.

Coverage: 2024 **73.7%** season-to-date / 17.1% prior / 9.2% league.
2025 **69.9% / 11.7% / 18.4%**.

Result: `home_fip` zero-variation share **92% -> 2%** (2024) and
**92% -> 0%** (2025).

### What it is worth, train 2024 -> test 2025 held out

| | AUC | Brier |
|---|---|---|
| train leaky, test leaky | 0.5448 | 0.24994 |
| **train clean, test clean** | **0.5397** | 0.25119 |
| **train leaky, test CLEAN (what production does)** | **0.5317** | 0.25334 |

The third row is the real one: a model trained on leaky data and served
clean loses **0.008 AUC** against one trained clean. That is the cost of
the leak in production terms, and it is now recoverable.

### The weights, which is what the operator actually asked about

| feature | leaky w | clean w | |
|---|---|---|---|
| `home_fip` | **+0.169** | **+0.009** | inflated ~19x by the leak |
| `away_era` | +0.047 | **-0.092** | **sign flips** |
| `away_whip` | -0.090 | **+0.007** | **sign flips** |
| `home_hr9` | -0.099 | -0.010 | |
| `fi_park_nrfi_rate` | **-0.209** | **-0.211** | unchanged, and still the largest |

So: the operator's hunch that the system "heavily relies on the park
rate" is **correct and legitimate** - it is the biggest weight by a wide
margin and the leak never touched it. Several pitching features only
looked useful because of the leak; cleaned, they are near zero and two
of them flip sign.

### Not yet done

The production model has NOT been refit on the clean files. The numbers
above come from a plain logistic fit over 17 features to measure the
leak, not from `two_stage_model.py` at its real feature set. A
production refit needs the full 3-split protocol in
`feature_test_methodology`.

---

## [2026-08-03h] - The +115.72u explained, the equity curve replaced, the drawdown chart deleted

Operator: *"convert the equity curve to cumulative units, drop the
drawdown chart. and also, that doesnt make any sense, because our actual
current profit shows +115.72u so i want you to fully analyze and explain
it."*

### THE ANALYSIS - what +115.72u actually was

It came from `season_record.json` -> `real.sim.profit`: a bank compounded
from 100 to 215.72. It was never the operator's money, and it differs
from the ledger figure on **three independent axes at once**. Holding
staking constant at flat 1u wherever possible:

| | figure | bets | hit |
|---|---|---|---|
| 1. replay, compounded, in-sample calibrator | **+115.72u** | 132 | 64.4% |
| 2. same replay, flat 1u a bet | **+18.18u** | 132 | 64.4% |
| 3. replay, flat 1u, WALK-FORWARD (no hindsight) | **+10.26u** | 119 | 62.2% |
| 4. what was ACTUALLY bet, flat 1u | **+4.85u** | 240 | 57.1% |

Per bet, flat: replay in-sample **+0.1377u**, replay walk-forward
**+0.0862u**, actually bet **+0.0202u**.

So the gap decomposes as:

- **Compounding is 6.4x of it.** +18.18u flat becomes +115.72u once the
  bank grows and later stakes ride on it.
- **Hindsight is +7.92u of the flat figure.** The shipped calibrator was
  fit on 2025+2026 and has already seen the outcomes it is scored
  against - the file says so in its own `caveat` field. The walk-forward
  floor, refitting from prior games only, drops 64.4% to 62.2%.
- **The rest is SELECTION.** The replay re-scores which games qualify
  with today's gate and bets 132 where the ledger actually bet 240. It
  is choosing different, better games with the benefit of the final
  model - not recording what was placed.

None of that is a bug in the replay; it is what a replay IS, and the
file documented it. The bug was putting it on the same page as the
ledger under the word "profit" with no bridge between them.

### Removed

- **The bankroll equity curve** and **the underwater/drawdown plot**.
  Both are bank-shaped: an equity curve IS compounding, and a drawdown
  is measured against a running high-water mark, so neither survives the
  removal of compounding. Converting them in place would have left two
  charts drawing the same cumulative line twice.

### Added

- **A cumulative-units chart** in each of the two settled-bet sections,
  reading the LEDGER at quarter-Kelly - the same source as every other
  figure beside it, so the page now has one number instead of two. A
  running sum, not a bank: a five-unit night in May is drawn the same
  height as a five-unit night in August.

### Fixed

- The chart letterboxed into the middle 65% of its box: `max-height`
  fought `height: auto`, so the viewBox scaled to ~220px, got clipped to
  180, and the browser centred the drawing. The viewBox aspect alone
  decides the height now.

---

## [2026-08-03g] - Compounding removed, quarter-Kelly everywhere, the whole system gets a history

Operator: *"i want to remove compounding from the dashboard... everywhere
on the dashboard, quarter kelly needs to be used... stop saying
SIMULATED... i like how you have the day by day history for the #1 pick
system, but why is that only for that system? also, you need to fix the
formatting."*

### Removed - compounding, everywhere

*"Compounding is up to the bettor, not the system."* Correct, and it is
the same principle that makes a published stake bankroll-free: the
system emits a unit COUNT, and what a unit is worth after a winning week
is the follower's business. Gone: `bank` levels, peak, max drawdown, the
per-slice `bankEnd`/`ret`, and the "100u becomes" column. Max drawdown
is replaced by **worst single night**, which needs no bank behind it.

### Changed - "simulated" was the wrong word, and that was my error

The Kelly figures were labelled SIMULATED with a dashed rule. That was
overcautious and the operator was right to reject it: every game, price
and result is real, and only the STAKE comes from the rule rather than
from history -- which is *equally* true of the flat-1u figure nobody
would call simulated. Quarter-Kelly is what the system publishes, so it
is what the system's record is measured at. Labels are now bases, not
warnings: **At quarter-Kelly** / **At a flat 1 unit** / **As actually
staked**. `realized` still prints beside them because a flat unit was
staked until 2026-07-27, but it is no longer framed as a correction.

### Added - the whole system, not just the #1

`loadTopPickReport(season, topOnly=false)` keeps every qualifying bet
instead of one a night. Same rules, same staking, same component, so the
two sections can be read against each other without holding two
definitions.

| | bets | record | hit | needs | at ¼-Kelly | flat 1u |
|---|---|---|---|---|---|---|
| #1 play | 63 | 42-21 | 66.7% | 57.4% | **+68.23u** | +10.37u |
| whole system | 240 | 137-103 | 57.1% | 55.7% | **+54.12u** | +4.85u |

### Fixed - the table formatting the operator flagged

- **Headers did not sit over their own columns.** `.table thead th` set
  `text-align: left` unconditionally while every numeric cell under it is
  right-aligned, so PRICE and STAKE floated left of figures sitting
  right. `.right` now applies to headers too.
- **No column rules.** A settled-bet table is read across a row AND down
  a column; with horizontal rules only, the eye has nothing holding a
  column together. `border-right` on every cell, none on the last, and a
  `--rule` under the header row.
- Cell padding moved from one-sided to symmetric so the rules actually
  separate columns rather than hugging the text.

### Docs

CLAUDE.md and AGENTS.md's "NEVER SUM UNITS ACROSS DAYS" rule contradicted
the shipped product and is rewritten: sum on a FIXED basis and name it;
do not publish compounded bank levels. `CumulativeUnits` still has no
renderer; `FlatUnits` is the sanctioned one.

---

## [2026-08-03f] - The #1 section becomes the CURRENT system, and half its money becomes a simulation

Operator, reading the every-bet table: *"it still includes NRFI, which
we've seen that NRFI doesnt apparently work... most of the bets are only
betting 1 unit. quarter kelly should be applied for all the history...
i also thought i told you to start the #1 pick model since we started
getting real odds data in may"*

All three correct. The May 26 cut had been added as a SIDE BLOCK while
the headline still led with the old series; that was a fair reading of
"add it" and the wrong reading of the intent.

### Changed - the section is now one population, defined by today's rules

1. **YRFI only.** 15 of the old 92 nights had an NRFI play as their #1.
   NRFI has been off since 2026-06-07 for losing in every band, so
   showing them as the record of a system that would not place them was
   simply wrong. On those nights the top YRFI play is counted instead.
2. **From 2026-05-26**, when the live weights were fit. Earlier picks
   came from a model that no longer exists.
3. **Staked at quarter-Kelly**, via the same `stakeUnitsFor` the board
   uses. 85 of the old 92 bets were recorded at exactly 1.00u because
   Kelly only went live 2026-07-27.

92 bets -> 65 qualifying -> **63**, because Kelly finds no edge at the
price paid on 2 of them and would not bet at all. That drop is counted
and printed rather than silently sized to zero (the trap recorded in the
`kelly_staking` memory).

### The honesty problem this creates, and how it is handled

Points 1 and 2 are FILTERS and stay factual. Point 3 is a
COUNTERFACTUAL, and the gap is not small:

| basis | figure | status |
|---|---|---|
| flat 1u a night | **+10.37u** | fact |
| actually staked and returned | **+7.49u** | fact |
| quarter-Kelly | **+68.23u** | **simulation** |
| ...compounding | **100.00u -> 184.77u** | **simulation** |

Roughly **nine times** the realized number, because the median stake
moves from 1.00u to 5.00u. The operator SELLS these picks, so:

- both simulated figures carry the word "simulated" in the basis label,
  a dashed rule under the label AND a dashed underline on the figure
  itself, so the marker travels with the number rather than its caption
- the marker is a FORM difference, not a hue one: globals.css reserves
  hue for real money and spending it on provenance would weaken both
- `totals.realized` is printed beside them, always
- the note says outright which two are facts and which two are not

`/brief` deliberately shows the **flat 1u** figure instead: that surface
gets read aloud, and a caveat is the first thing to fall off on camera.

### Note

`tools/season_replay.py` already excluded NRFI (`decide()` is YRFI-only
and says why), so the backtest needed no change on that count.
`bySide` is dropped from the report: with the series YRFI-only it had
one row.

---

## [2026-08-03e] - UI upgrade: rounded corners, paper shadows, a display serif

Operator: *"i want to upgrade the ui too. rounded corners. shadows. nice
fonts. etc"* -- straight after the Newsprint repalette.

### Added

- **A radius SCALE, not a value.** `--radius` was a single `0rem` (the
  July square-corner spec). One number cannot serve a 4px chip and a
  full-width panel, so: `--radius-sm` 6px (chips, inputs), `--radius`
  10px (cards, rows, panels), `--radius-lg` 16px. 51 existing call sites
  already read `var(--radius)` and picked it up for free; the pills at
  `999px` were already round and are untouched.
- **Shadows built for paper.** The old set was tuned for a near-black
  page -- heavy pure-black alphas that read as grey mud on `#FBFAF7`.
  The new tokens are ink-tinted (`rgba(33,30,26,...)`) and LAYERED: a
  1px contact edge, a tight shadow, a wide soft one, which is what makes
  a card look like it is resting on paper rather than floating over it.
  `--shadow-lift` is new, for hover and open states.
- **A third typeface, and now each one has a job.** Fraunces (variable,
  optical-size axis) for display; Inter for prose; JetBrains Mono for
  figures. The contrast IS the system: serif says "this is the thing",
  sans says "this is prose", mono says "this is a number". Applied to
  the matchup headline, the Brief's section heads, the #1-history title,
  the wordmark and the slate date -- all of which were previously mono,
  i.e. the interface was calling the product's own name a number.
  Fraunces rather than Playfair on purpose: Playfair is the reflex every
  editorial redesign reaches for first.

### Changed

- Board rows rest ON the paper instead of being outlined on it: the 1px
  border goes transparent and the shadow's own contact edge does that
  job, so a card no longer carries two competing outlines. Hover and
  open states move to `--shadow-lift`.
- Section heads on /brief drop `text-transform: uppercase` at 13px for
  the serif at 22px. Tracked uppercase was doing the work of hierarchy
  that the type scale can now do properly.

### Note

Fraunces is a fourth family on the Google Fonts `@import`, which is
render-blocking. It is variable, so it is one file rather than four
weights, but if first paint ever matters this import is the thing to
move to `next/font`.

---

## [2026-08-03c] - Design critique, 11 agents, and the contradiction it caught

An 11-agent critique of /brief and /history: five independent lenses
(hierarchy, number provenance, accessibility, read-aloud copy,
responsive), each with an adversarial verifier that had to REFUTE its
own lens's findings against the real source. 11 of 20 survived.

Everything below was found by looking at the RENDERED page in a real
browser at 1910px and 375px. None of it was visible in the DOM dumps
the first pass relied on.

### Fixed - the contradiction, which is the one that mattered

**The ballpark block printed two figures fifteen points apart, twenty
lines from each other, and neither named its basis.** The headline
`58%` is the model's park factor: Bayesian-shrunk over LAST SEASON PLUS
THIS ONE with a 50-game prior. The line below it, `41 of the 56 first
innings actually played here this season`, is **73%**, raw 2026. Both
true. Read consecutively into a camera they contradict each other, and
the word "actually" quietly accused the first figure of being fake.
Arizona reads 58 vs 55, Miami 54 vs 46, so the direction is not even
consistent. Both sentences now name their basis, and when they diverge
by more than eight points the reason sentence carries BOTH.

This is precisely the failure /brief exists to prevent, and it shipped
in the first version of it.

### Fixed - the rest

- **A losing season would have printed in the gain colour.**
  `.recordFigure` set no colour, so it inherited `--foreground` #00FF41,
  byte-identical to `--gain`, and carried no `data-money`. The one money
  figure on the filmed page was the only one in the codebase unmarked.
- **The bet ticket was unspeakable.** Bare `YRFI` (whose inverse is one
  slip away), `7.00u` (no spoken form), an em dash when no price was
  captured, and worst `Model 71% likely`, which never named WHAT was
  likely: on an NRFI night that figure means the opposite of the
  ballpark percentage on the same page, and both can read 58%. Now:
  "a run scores in the first inning", "7.00 units", "71% chance a run
  scores in the first inning", with the acronym demoted to a tag.
- **Three different KINDS of number wore the same suffix, size and
  hue.** The three UNITS PROFIT figures now carry a basis eyebrow ABOVE
  the number, which is also the order they have to be spoken, and the
  compounded one is marked as a different kind by form, not hue.
- **On a 375px phone both money columns were off-screen and no label
  survived the swipe.** `min-width: 34rem` resolved to 510px in a 311px
  box; `position: sticky; top: 0` on the thead was inert because
  `overflow-x: auto` makes the scroller its own scrollport with no
  height constraint. Headers now wrap (cells never do), min-width drops
  to 26rem, the row label is `sticky; left: 0`, and money moved to the
  left of each table. 39% hidden -> 12-15%, money VISIBLE on load.
- **20 hand-written rem font sizes against a documented px system.**
  globals.css says verbatim "ALL PX, NEVER REM... Six px values total:
  44 / 26 / 20 / 13 / 12 / 11" and ships role classes. BriefView had 13
  distinct rem sizes, TopPickHistory 7, three of them BELOW the 11px
  floor (9.38 / 10.31 / 10.31) - reintroducing by a different route the
  exact defect that retired VT323 on 2026-07-30. Now 7 px steps, nothing
  under 11px. The single deviation from the six is a 16px prose step for
  the read-aloud sentence, kept deliberately because 13px is the board's
  glance size and this is a reading surface; `.matchup` keeps its clamp
  because a fixed 44px monospace headline wraps "Baltimore at Cleveland"
  to three lines on a phone.

### The measure rework (operator: "yes rework the measure, make it wider")

/brief used 690px of a 1910px viewport, 36% of the screen, and its prose
capped at FOUR ch values that resolved to four pixel widths (612 / 523 /
520 / 482) because they sat at four font sizes. Four ragged right edges
in one column.

**Wider could not mean longer lines.** Prose stops being readable past
about 75 characters, and this page is read ALOUD, where the failure mode
is losing your place mid-sentence on camera. At 16px JetBrains Mono a
620px line is already 64 characters; stretching it to fill 1910px would
put it near 190 and make the page worse at its only job. So the width
went into LAYOUT:

- container 46rem (690px) -> **1080px**, about 76% of a 1430px screen
- one `--measure` custom property (620px) replaces all four ch caps, so
  there is one right edge per section rather than four
- **at >= 60rem the reasons are two columns**: label and figure in a
  190px rail, the spoken sentence in the measure column beside it. This
  is what actually consumes the width, and it halves a reason's vertical
  space, so less scrolls past while the camera is running.
- the section intro joins the sentence column, SCOPED to reason blocks
  only: "The numbers" has no rail, so indenting its intro would have
  pushed it 230px right of the content it introduces, reintroducing the
  same defect one level down.
- `.count` loses `margin-left: auto`, which had stranded the "2" beside
  "THE CASE FOR IT" 515px from the words it counts. Now 159px.
- `.strip` capped at 740px so a single digit does not sit in a 105px cell

Measured after: reason blocks one edge at 1038px, plain blocks one edge
at 810px, the two-up pitcher grid on its own column edges. Three
structural families, each justified by its own layout, instead of four
accidental widths at the same nesting level. Mobile untouched: the
measure exceeds the viewport there and every breakpoint is above it.

---

## [2026-08-03b] - A real backtest of the #1 play, and the window that is not one

Operator: *"what about since may when we got correct odds, and with our
new filters, floors, and everything from our new system, not from what
our old system used to pick"* and then *"i want you to run a real
backtest of the new system."*

Two different things, and the value is in keeping them apart.

### Added

- **`tools/season_replay.py --top-only`** - restricts the replay to ONE
  bet a night, the slate's #1, ranked by the same rule as
  `lib/top-pick-rank.ts` (confidence, then the better price). A backtest
  that defined "#1" differently from the live board would be measuring a
  strategy nobody runs.
- **`tools/season_replay.py --since YYYY-MM-DD`** - restricts what is
  EVALUATED, deliberately not what is trained on. The first draft
  filtered rows at load, which silently starved the walk-forward: it
  refits from games strictly before each date, and those are exactly the
  games a `--since` would have discarded.
- **"Under the current system" block** on /history's #1 section:
  `TopPickReport.currentSystem`, YRFI-only and re-ranked, from
  2026-05-26. Labelled **not a backtest** in the copy, pointing at the
  replay command for the real thing.

### The numbers

Ledger, today's selection rule applied backwards (65 bets, from 05-26):
**44-21, 67.7%** against a 57.7% break-even, **+11.36u** flat.

Replay, `--top-only --since 2026-05-26`, walk-forward calibrator:
**37-19, 66.1%** against 58.2%, **+7.63u** flat, 56 bets.

Replay, `--top-only`, whole season, walk-forward: **66.2%**, **+10.61u**
flat over 71 bets.

The convergence is the finding: three routes to roughly +8u to +13u flat
on the #1 play. The replay also bets FEWER games than the ledger did (71
vs 92) for about the same flat profit, which is the current gate being
more selective.

### The trap this entry exists to record

**The replay's COMPOUNDED figures are not comparable to the ledger's and
must never be quoted beside them.** The replay applies quarter-Kelly to
every bet from April; in reality **85 of the 92 #1 bets were staked at
exactly 1.00u**, because Kelly only went live 2026-07-27. So the replay
reports 100u -> 193.68u while the ledger reports 100u -> 106.59u for
overlapping bets. Both are right about different strategies. Only the
**flat 1u** column compares.

Also: every interval still crosses its break-even. 56 bets gives 95%
[53.0-77.1%] against 58.2% needed. Positive on every route, proven on
none, which is the same conclusion the 2026-06-04 edge investigation
reached and it has not moved.

---

## [2026-08-03] - THE BRIEF: the #1 play, explained in sentences

Operator: *"i want to start making content where i post a video about the
#1 play from the model. i need to be able to give reasons why its the #1
play, and not just mentioning data that doesnt make any sense to people
... the Rays didnt score in the first inning at all in their last series
of 3 games against the white sox."*

A third usage scene, and it is not the one PRODUCT.md describes. The board
answers "what do I bet and how much" in thirty seconds on a phone; the
history page answers "how did it go" at a desk. Neither can be read ALOUD
to an audience who cannot see the screen, which is what filming requires.

### Added

- **`/brief`** (`app/brief/page.tsx`, `components/BriefView.tsx`) - the
  night's #1 play written as a script: the bet, the case for it, the case
  against it, then the supporting numbers. Bare `/brief` uses the shared
  #1 selector; `?game=<gamePk>` briefs any game on the slate.
- **`lib/first-inning-form.ts`** - per-team last-10 first-inning form,
  per-pitcher scoreless-first record, park rate and rank, head-to-head and
  current series. All DERIVED FROM THE LEDGER, which already logs every
  game on every slate plus its first-inning line, so there is no new
  scraper and no new failure mode. Verified 2026-08-03: 119 slate dates
  with exactly three calendar gaps (07-13/14/15), which are the All-Star
  break.
- **`lib/pick-reasons.ts`** - turns model features into speakable
  sentences (`fi_park_nrfi_rate z=-1.476` becomes "a run scores in the
  first inning 58% of the time in Colorado"). Where per-pick diagnostics
  exist, the model's own contribution magnitudes ORDER the reasons; the
  wording is always written here and the figures always come from the
  ledger, so nothing is invented to fit a story.
- **`lib/team-names.ts`** - "TB" is not a word. Abbreviations stay on the
  board and expand to "Tampa Bay" / "the Rays" on the brief.
- **`components/TopPickHistory.tsx`** - the #1 play's full record on
  /history: bank growth from 100u, last-10, month-by-month and by-side
  tables, and every settled #1 play. Answers the operator's "units profit"
  request the only way it can be answered (see below).
- **`lib/top-pick.ts`** gains `last10`, `bank`, `byMonth`, `bySide`, `all`.
- Board header now links to the brief, as does the "#1" explainer note.

### Changed

- **`selectTopPick()` hoisted into `lib/top-pick-rank.ts`** (T2.61). The
  comparator was already shared between the board badge and the history
  card, but each re-implemented the FOLD around it including the game-name
  tiebreak - so the rule was shared and the selection was not. A brief
  that disagreed with the board about which game is #1 would be the worst
  version of that bug, because the operator would be filming it.
- `scripts/copy-data.mjs` now ships `fi_park_factors.json`, without which
  the brief silently drops its ballpark reason on deployed builds.

### Notable decisions

- **UNITS PROFIT IS BACK, AND THE OLD RULE WAS TOO BROAD.** First pass
  refused to print any season unit total and showed bank growth alone.
  Operator: *"units profit should be available. that doesnt make sense.
  every bet should be making the same amount of units as a person with a
  $1000 bankroll, or someone with a $10,000 bankroll."* Correct, and the
  original rule confused two things. What breaks a unit sum is the unit's
  DOLLAR VALUE MOVING between the bets being added, which happens only
  under compounding - not the passage of time. On a FIXED basis the sum
  is exact and is identical on any bankroll, which is exactly the point
  when you are selling picks. The section now prints all three:

  | basis | #1 pick, season |
  |---|---|
  | flat 1u every night | **+10.70u** |
  | at the published stakes, never re-sizing | **+7.82u** |
  | re-sizing to 1% of the running bank | +6.59u (100.00u -> 106.59u) |

  `lib/units.ts` gains a `FlatUnits` brand and `formatFlatUnits()` for
  the legitimate case; `CumulativeUnits` still has no renderer and never
  will. `formatUnits()` now rejects BOTH brands, so a season total can
  never be printed by the same function as a single night's P&L - on
  screen they are indistinguishable. Guard extended to 8 rejected forms
  and 6 accepted ones, including `formatFlatUnits(asCumulative(x))` which
  must stay an error so `asFlat` cannot launder a compounding sum.
- **Stats that cut AGAINST the pick are a first-class block, not hidden.**
  The operator's opening instinct was that Tampa Bay were "due" a first-
  inning run after a scoreless series. That is the gambler's fallacy, and
  it argues the opposite way from the YRFI bet the model actually made.
  The card shows the figure, labels it as cutting against, and lets the
  operator answer it on camera.
- **Park standing is a TIER, not an ordinal.** The first draft printed
  "2nd most run-friendly of 30 parks" for Colorado; Arizona sits at 0.4239
  and Colorado at 0.4241, a dead heat. Ordinals claim a ranking the data
  cannot support.

### Fixed (found by measuring the rendered page, not by eye)

- Strip opponent labels rendered at **8.4px** at 375px - a third smaller
  than the 11-13px that got VT323 retired for being unreadable in this
  exact scene. The strip now wraps to two rows of five on a phone, which
  doubles cell width to 67px and lifts the label to 10.3px.
- Brief nav links measured **17px tall**, then 41px after a `2.75rem`
  min-height (the app's root font is 15px, not 16px). Now `44px` in
  pixels, because a fingertip is not a typographic measure.

### Performance snapshot

#1 play, real captured prices, as of 2026-08-03: **59-33 (64.1%)** against
a 57.6% break-even, +6.2% per unit staked, bank 100.00u -> 106.59u, deepest
drawdown -12.6%. Last 10: 4-6.

---

## [2026-07-31] - #1 leads the board; the pick column speaks in white

Operator, on a screenshot: *"visually, why is the #1 pick not at the top.
also, what even makes it the #1 pick? also, can we make it so that any of
the picks on this screen are not green, and they have white text thats a
different font, not terminal, thats more readable."*

### 1. Why #1 was not at the top - a missing tie-break

CWS@TB and KC@COL were **exactly tied** at `p_nrfi = 0.287200`. The board's
`P(YRFI) high -> low` comparator was probability *only*, so it returned 0,
the stable sort kept the incoming order, and KC@COL led while the badge sat
on CWS@TB.

Neither was wrong alone - the badge breaks ties on the better price
(-135 beats -155) and the sort broke them not at all. But two rankings of
one slate disagreeing about which game is first made the badge look
arbitrary.

Ties at the top are **not rare and will not become rare**: the current CIR
calibrator has a floor clamp at `0.2872` that 63 games sit on, so the
strongest plays are the *most* likely to tie. Fixed by giving the
probability sorts the same tie-break, rather than pinning the badged row -
#1 now leads because it genuinely ranks first.

### 2. What makes it #1 - said out loud

A note under the board, rendered only when a badge is present:

> **#1** The model's most confident bet tonight - the STRONG play furthest
> from a coin flip. When two are equally confident, the one at the better
> price wins. Its running record is the #1 pick card on the history page.

It was only ever in the badge's hover tooltip: undiscoverable on desktop,
non-existent on a phone.

### 3. The pick column is white, in Inter

Two new tokens, scoped to the PICK column only - everything else on the
board stays phosphor mono, so the pick reads as the one thing on the row
speaking plainly.

- `--pick-ink: #FFFFFF` - **20.38:1** on `--card`, up from green's 14.93:1
- `--font-ui: Inter` - proportional, drawn for UI text at small sizes;
  tracking cut 0.10em -> 0.02em (wide tracking is a *monospace* mannerism
  that buys nothing on a proportional face and costs real width on
  "PENDING - LOCKS 7:40 PM ET")

**Four side-as-hue violations found and fixed on the way**, all of them
live, none reached by the 2026-07-29 pass that cleaned the pick pills:

| element | was | why it mattered |
|---|---|---|
| `.oddsNrfi` / `.oddsYrfi` chip | `--primary` / `--destructive` on background, border, price **and** book label | the price of every YRFI bet - the only side this system bets - rendered in the **loss** colour on a game that had not started, and the red tint over a green page is what made this chip read orange |
| `.oddsSideLabel` | green / red by side | the N/Y marker *is* the side; it did not also need to be the side's colour |
| `.pickLabelLeanNrfi` / `Yrfi` | `--primary` / `--destructive` + glow | same defect on the tentative-lean tail of PENDING pills |
| `.pickPillLocking .pickLabel` | `--foreground` | most specific rule on the label - it beat every other change and is why the two locking picks stayed green after the first pass |

Audited rather than spot-fixed, per the palette memory's own standing rule
(*"grep every call site, don't just fix the component you were looking
at"*): a scripted sweep of every `color` / `background` / `border-color`
declaration on a pick-column selector now reports **no money hue left in
the pick column**. The only green remaining there is the `#1` badge, which
keeps `--attn` deliberately as the row asking for attention.

### Verified

Production build. #1 badge on the first row. Pick labels, prices and stakes
all `rgb(255,255,255)` in Inter. 375px and 1280px: **zero overflowing
elements**, pill does not overflow with the wider proportional face.

---

## [2026-07-30i] — "#1" badge on the board's top bet

Operator: *"i would like to be able to tell which bet is the top bet."*

The /history card has tracked the #1 pick's record since 2026-07-30g, but
the board never said which of tonight's plays **is** it — so the figure
was only ever readable after the fact.

### Added — a `#1` badge, leading the pick cell

Leads rather than trails: the operator reads this board left to right in
about thirty seconds, and a marker arriving after the pill and two chips
is a marker they find second. "#1 STRONG YRFI" also reads as a sentence.

Outlined, `--attn`, never filled and never a money hue — a filled chip in
`--gain` would read as *"this one won"* on a game that has not started.
The badge is an identifier, and identifiers on this board are outlines.
The `#1` glyph carries the meaning, so nothing depends on the colour.

**STRONG only.** LEAN is tracked and never wagered, so a "top bet" that
is not a bet would be an instruction to risk money the system does not
intend to risk — the same rule the stake chip enforces.

### One definition of "#1", shared

`lib/top-pick-rank.ts` is new: the comparator, and nothing else. Both the
server-side history card and the client-side board badge import it, so
the two cannot disagree about which game was #1 — which is exactly how
this dashboard has produced contradictions before.

It is a **separate file** because the rule started inside `lib/top-pick.ts`,
which reads the ledger and therefore imports `node:fs`. `BoardTable` is a
client component, so importing from there dragged the filesystem into the
browser bundle and webpack refused to build:

> `UnhandledSchemeError: Reading from "node:fs" is not handled`

A useful failure — it says the rule and the loader are different concerns.

### Verified against the tie dates, which are the hard case

18% of nights have two or more bets sharing the top probability exactly
(the retired calibrator's flat steps). Confirmed the board and the
history card resolve them identically:

| date | tied | board badges | history card picks |
|---|---|---|---|
| 2026-06-13 | 3 at 59.4% | **LAD@CWS** (−110) | **LAD@CWS** |
| 2026-07-12 | 5 at 59.4% | **OAK@CWS** (−110) | **OAK@CWS** |

Both are the best-priced of the tied set, then alphabetical — the rule
as written.

Also confirmed the badge is computed over `rows` and not `sortedRows`, so
re-sorting the table by edge or result does not move it: it is a property
of the slate, not of the display order.

### Verified

Production build. Tonight: 1 STRONG, 1 badge, on WSH@ATL — the most
confident play on the board. 375px and 1280px: zero overflowing elements,
pick cell does not overflow with the badge added.

---

## [2026-07-30h] — Provenance stamp on every replay-driven figure

Operator: *"why did the profit change again from yesterday?"*

Nothing was wrong. But **three different sets of numbers appeared on
`/history` in one day** and nothing on screen said why:

1. the unit re-basing changed what a "u" means in the Day column
   (2026-07-28 went from −10.00u to −4.61u — same money, new ruler)
2. the STRONG gate moved 0.40 → 0.42
3. the nightly replay rebuild will re-simulate the **whole season** under
   the new gate, moving every historical row

(3) is the surprising one, and it is inherent rather than a bug: these
charts answer *"what would today's system have done all season"*, so
changing today's system rewrites the history by design. **A chart that
silently self-rewrites is indistinguishable from a broken one.**

### Added — `ReplayStamp`

Every replay-driven card now carries the gate and build time that
produced it:

> `Replay · gate 0.40 · built 30 Jul 06:07 UTC`

Mounted on four: the hero, the week card, the equity curve, and the
daily ledger. Built once in `HistoryView` and passed down, so the four
copies cannot drift into describing different builds — which would be a
worse version of the problem it solves.

**Not on the #1 pick card, deliberately.** That reads the ledger, so it
can only move when a real bet settles. Stamping it would imply a
volatility it does not have.

### The staleness line is the part that earns its keep

`thresholds.json` holds the **live** gate; `season_record.json` holds the
one its replay was **built** at. When they disagree the figures on screen
are already known to be superseded, and the operator can be told *before*
the rebuild instead of discovering it afterwards. That is exactly the
state this shipped in — live 0.42, replay built at 0.40:

> The live gate is now 0.42. These figures were replayed at 0.40, so
> every row will move at the next nightly rebuild — no real bet changes,
> the simulation just re-runs under the new rule.

Verified self-clearing rather than a permanent banner: stale now
(0.40 vs 0.42), **not** stale after the rebuild (0.42 vs 0.42), stale
again if the gate moves later (0.42 vs 0.45).

What the next rebuild will actually do, measured in advance: 104 bets →
**123**, final bank 209.89u → **218.61u**, and eight of the last nine
rows move (2026-07-25 goes from no-bet to −4.00u).

### Verified

Production build. Four stamps on the four replay cards, none on the #1
pick card, staleness firing on all four. 375px and 1280px: zero
overflowing elements on `/history`, no console errors. `--attn` on the
warning, 10px `--muted-foreground` on the stamp line — chrome, never
competing with the figure it describes.

---

## [2026-07-30g] — "#1 pick" tracker on /history, from the REAL ledger

Operator: *"i want to see what the #1 pick record and profit would be"*
— then, on being shown replay figures: *"i want you to get the real
numbers."*

### Real, not replayed

This card reads `picks_2026.csv` / Supabase: bets actually **placed**, at
prices actually **captured**, graded on real results, using the units
actually **staked**. It touches `season_record.json` nowhere. Every other
performance surface on that page is the replay — today's model re-scoring
history — which is a different and more flattering question.

**How much more flattering:** the replay says the #1 pick beat its price
with confidence (69.4%, CI excluding break-even). The real ledger says
**64.8% against 57.5% needed, and the interval still straddles it.**

| window | record | hit | needs | staked | return | 95% range |
|---|---|---|---|---|---|---|
| Season | 57–31 | 64.8% | 57.5% | 101.53u | **+6.1%** | 54–74% |
| Last 30 days | 19–7 | 73.1% | 58.9% | 39.53u | +1.7% | 54–86% |
| Last 14 days | 7–6 | 53.8% | 59.9% | 26.53u | −26.4% | 29–77% |
| Last 7 days | 2–4 | 33.3% | 57.1% | 19.53u | −40.0% | 10–70% |

Every figure reproduced by an independent Python pass over the CSV.

### Two defects found while building it, both fixed

**The #1 pick was not deterministic.** The retired calibrator emitted
flat steps — 115 games on `p = 0.4064` alone — so **18% of nights have
two or more bets sharing the top probability exactly**. With a plain
`min()` the winner is whichever row the loader returned first, and this
card reads Supabase live with a CSV fallback whose order differs. The
same season read **58–30 from one source and 56–32 from the other**.
Tie-break is now confidence → **better price** → game name: a real
decision rule (when the model can't separate two games, the one paying
more is the higher-edge bet) rather than an arbitrary stabiliser.

**A population mismatch in the first draft.** The record covered every
STRONG pick (115 nights) while the money covered only the ones actually
bet (88) — two figures, two populations, one table. Every column now
comes from the identical set, and nights with no captured price are
excluded *and counted* on the card's face.

### Also

- `lib/roi.loadLedgerRows()` extracted and exported. The "Supabase, then
  CSV, then give up" sequence was inline in `loadRoi`; a second copy is
  how two surfaces start disagreeing about which night they describe.
- The money figure is **returned ÷ staked**, never a unit total — units
  from different dates are different money (`lib/units.ts`).
- The 95% range is **on the card, not in a footnote**. One bet a night
  makes short windows uninformative: the 7-day row is six bets and its
  interval runs sixty points wide. Printing "33.3%" beside "73.1%" with
  nothing else invites "the model broke this week".
- Renders **bright** with money hues, directly under the dim simulated
  week card. That contrast is the real-vs-simulated convention teaching
  itself, which PRODUCT.md asks to be readable without a legend.

### Verified

Production build, 375px and 1280px: zero overflowing elements on
`/history`. On mobile the seven columns fold to three rows with inline
labels — nothing is dropped, which was the mistake in the first draft of
the responsive rules.

---

## [2026-07-30f] — STRONG YRFI gate 0.40 → 0.42

Operator: *"why is there much less games being chosen to be bet on. this
is an issue."* It was, and by more than anyone intended.

### The cause: two changes in 24 hours, and nobody re-derived the gate

| date | change | intended |
|---|---|---|
| 2026-07-27 | gate 0.44 → 0.40 | yes |
| 2026-07-28 | calibrator swapped to CIR | yes |

**A gate is a cut point on a distribution.** The CIR swap changed the
distribution — the old calibrator emitted only 41% unique values and
leaned on flat steps, CIR emits 95% unique — so the same number 0.40
started selecting far fewer games. Nobody re-derived the cut after the
shape moved.

Measured: today's model bets **124 games where the live system actually
bet 308** — 40% of the volume, down every single week, and only about
half of that reduction was chosen.

### Where the old volume actually came from

The old calibrator had two clamps, and they were doing opposite things:

- **p = 0.4064, 115 games.** Games it could not tell apart, auto-fired
  under the 0.44 gate. They went **50.9% against a 54.9% break-even.**
  CIR dissolved this and that volume is gone on purpose.
- **p = 0.3219, 32 games.** The floor clamp — 26% of all bets and **52%
  of the season's profit**, 25-7 for a 78.1% hit rate. Checked
  individually: **all 32 still clear at both 0.40 and 0.42**, because
  CIR moved them *down* to ~0.287.

So the swap dropped unrankable games and kept the good ones. That part
is the calibrator working correctly, and it is not recoverable volume.

### The change

`_LR_STRONG_YRFI_P` 0.40 → 0.42. The 2026-07-28 sweep went
0.36 → 0.40 → 0.44 and skipped the space between, so this is new ground
rather than a re-tread of the refuted 0.44. (An April 2026 note in the
same file independently landed on 0.42 as well, on n=5.)

Through the repo's own walk-forward harness, `tools/season_record.py`:

| gate | record | hit% | need% | bets | flat | Kelly bank | maxDD |
|---|---|---|---|---|---|---|---|
| 0.40 | 75-38 | 66.4% | 57.4% | 113 | +18.10u | 255.70u | 8.4% |
| **0.42** | 98-57 | 63.2% | 56.7% | **155** | **+18.07u** | 263.14u | 9.4% |

**+37% more bets for the same flat profit** (0.03u apart), a slightly
better Kelly bank, 1pp more drawdown. Positive in all four months.

### This is a VOLUME decision, not a proven edge improvement

Stated plainly so nobody later reads it as proof:

- flat walk-forward, 6 rolling cuts: 0.42 best **6 of 6**
- **Kelly** walk-forward, same 6 cuts: 0.42 best **3 of 6 — a tie**
- block bootstrap on the difference, resampling whole slate days:
  flat `[−7.01u, +13.24u]`, Kelly `[−14.61u, +33.22u]` — **both span
  zero**, 0.42 ahead in ~76% of resamples

Money-neutral, volume-positive. The operator asked for the Kelly view
specifically, and it is what weakened the walk-forward from unanimous to
a tie — the flat-only view would have overstated the case.

The Kelly figures are the same edge levered **7.5×** (+3.57u flat reads
as +26.67u compounded), exactly what `tools/edge_floor` meant by "never
judge a filter on final Kelly bank". Flat decided this; Kelly says what
it does to the bank.

### What it does not do

Restore ~24 bets/week. That number was substantially manufactured by the
0.4064 clamp. Realistic volume is ~7.5/week at 0.42 against ~6.4 at
0.40. **Do not chase the rest by loosening further** — 0.44 is worse on
every measure tested (59.3% hit, +16.35u flat, 224.01u bank, 13.4% DD).

Tonight (2026-07-30) this turns 0 bets into 2: SEA@LAD at 0.4116 and
BOS@OAK at 0.4157.

### Separately found, not fixed here

`tools/season_record.simulate()` adds a fixed unit count to a growing
bank. Before the 2026-07-30 unit re-basing the stake was `bank * f` and
so already scaled; now sizing is bankroll-free (`stake = f * 100`), so
the simulator no longer compounds. The same season reads **+132%
additive** and **+234% compounded**. The dashboard's equity curve shows
the additive one. Flagged, not changed — it moves every figure on the
dashboard and deserves its own decision.

---

## [2026-07-30e] — The unit conversion is finished, and a guard so it stays finished

**1 unit = 1% of bankroll. The bankroll is always 100 units.** Stakes
were already converted; the ledger's *reporting* was not. Every place
the dashboard added units across time has been changed to report **bank
growth (100 → X)** or a **percentage return**.

### Why a sum is not money

Growth changes the DOLLAR VALUE of a unit, never the number of units
bet. So a 1-unit win when a unit was $100 and a 1-unit win when it was
$150 are amounts in different currencies, and adding them produces a
figure that means nothing. Over the last 7 replay days:

| | |
|---|---|
| naive sum of daily P&L | **−13.17u** |
| what the bank did | 223.07 → 209.89 |
| honest figure | **−5.91u** |

The sum is 2.2× the truth. In a winning stretch it errs the other way,
which is the direction that misleads a paying subscriber.

### Added — `lib/units.ts`, and the guard that fails the build

`CumulativeUnits` is a branded number. Every across-time sum is typed as
one, and `formatUnits()` **refuses the brand at compile time** — so
`next build` fails and Vercel never deploys it. `ReplayWindow.pnl`,
`.yrfi.pnl`, `.nrfi.pnl`, `.flat` and `.flatPnl` all carry it now.

Two right answers replace it: `formatBankGrowth(100, 209.89)` and
`formatReturn()`. Plus `returnAsUnits()` — legitimate only because the
bank is 100 units by definition, so a percentage and a unit count are
the same number. It takes a **fraction**, so it cannot be called with a
naive sum by mistake.

`scripts/check-units-guard.mjs` guards the guard, and runs in
`prebuild` on every deploy. It compiles a probe of 6 forbidden and 5
legitimate forms and fails if any lands the wrong way — because the
whole protection is one function signature, and widening it to `number`
to silence an error would disable everything and change nothing
visible. Negative-tested: weakening `formatUnits` makes the script exit
1 with all six violations named.

### Changed — every cumulative surface

| surface | was | now |
|---|---|---|
| `/history` hero | `+109.89u` summed | return on a 100u bank + `bank 100.00u → 209.89u` |
| daily ledger col 3 | `Cumulative` units | **`Bank`** — a level, untoned |
| daily ledger col 2 | raw `simPnl` | **re-based** to the bank that night opened with |
| equity curve axis | `+NNNu` cumulative | bank levels, unsigned, anchored at 100u not 0 |
| "Window P&L" tile | cumulative units | **`Bank now`** 209.89u |
| "All-time high" tile | cumulative units | **`Peak bank`** 233.07u |
| "Max drawdown" tile | `−NN.NNu` | **−11.2%** of the peak it fell from |
| underwater chart | depth in units | depth as **% of peak**; input is now bank levels |
| zone hit-rate table | season unit totals | **return per unit staked** |
| RoiPanel season/floor | `fmtU(sim.profit)` | bank growth + return |
| divergence card | segment unit totals | return per unit staked |

### Two disagreements found and closed

- **The ledger table contradicted the new week card.** 2026-07-28 read
  −10.00u in the table and −4.61u in the card — same night, same
  system, both on screen. The table's figure was raw compounded units;
  it is re-based now and both read −4.61u.
- **`/` and `/history` disagreed about the season by 0.06u** (+109.95u
  vs +109.89u). RoiPanel divided a sum of per-game P&L by the opening
  bank; `/history` divided the bank endpoints. The exporter rounds the
  two separately. Both now divide the same two bank levels, so they
  agree by construction rather than by luck.

### Fixed — two pre-existing `/history` breaks at 375px

Neither was caused by the font swap (measured byte-identical under
VT323 and JetBrains Mono; both come from fixed pixel geometry).

- **The distribution bar was drawn at full magnitude in a half-width
  wing.** A diverging bar centred at 50% gives each side 50% to grow
  into; the fill used the whole magnitude, so the biggest day ran from
  50% to 150% and `overflow: hidden` clipped it. Not cosmetic — every
  large day capped at the same visible length, so the worst night of the
  season and a night 40% smaller drew identical bars, in the column that
  exists to compare magnitudes. Ten rows were overflowing, the worst by
  133px of a 265px track.
- **The zone table could not fit a phone at any type size.** Its
  `minmax()` minimums total 518px of irreducible width inside a 343px
  card, and `html { overflow-x: clip }` meant it did not scroll either —
  the rate, the P&L and the right edge of every bar were simply cut off.
  Two-row grid under 720px.

**`/history` at 375px goes from 18 overflowing elements to 0.**

### Verified

Production build. Every figure reproduced against an independent Python
pass over `season_record.json`, and the three surfaces cross-checked:

| | `/` RoiPanel | `/history` hero | week card |
|---|---|---|---|
| Season | +109.89u | +109.89u | — |
| Last 7d | −5.91u · 1-4 · 5 bets | — | −5.91u · 1–4 · 5 bets |

Ledger rows Jul 27/28/29 render −1.81u / −4.61u / +1.37u against banks
217.07 / 207.07 / 209.89 — all matching. Peak bank 233.07u, max
drawdown −11.2%, and the underwater card's "deepest" now reads the same
−11.2% instead of a units figure that measured something else.

`tools/pl_calc.py --window 7d` reports the LEDGER at **−12.773u**, no
drift. That is a different population from the replay's −5.91% and is
unchanged by this work.

### Not converted, deliberately

The ledger's stored `profit_loss_units` is still in old compounded units
for Kelly-era rows — 2026-07-28 NYY@CWS is stored as `9.56` units
risked. Correcting that is a data migration through `tracker.py`, not a
display change, and it rewrites history in `picks_2026.csv`. The zone
table's "per unit staked" divides by the bet count, i.e. assumes one
unit a bet, which is exact for every row before 2026-07-28 and
progressively wrong after; the card says so on its face.

`ReplayWindow.pct` is left in place but documented as never-render: it
divides an all-sides `pnl` by a YRFI-only `bankStart` and lands on
−7.7% where the bank says −5.91%. Nothing reads it.

---

## [2026-07-30d] — "Week at a glance" card on /history

Operator found an analytics card online (a "BudgetCard": big figure,
smooth sparkline, hover tooltip) and asked for the same visual idea
**built natively, not pasted in.**

### Why pasting it was never on

Both reasons were checked, not assumed:

- **No Tailwind, no shadcn in this project.** It is 16 CSS Modules files
  plus custom properties. Every utility class in that component would
  have rendered as *nothing* — not a broken style, an absent one, which
  looks like a layout bug and gets debugged as one.
- **It ships hardcoded fake data** — a `$30.739` balance, an invented
  week, indigo `#5B52E5` lines. This dashboard has spent days removing
  invented numbers; a fabricated balance in the most prominent card
  would be the single most believable wrong number on the page.

### Added — `WeekAtAGlance.tsx` + `.module.css`

CSS Modules matching `.chartCard`'s shell exactly. Tokens only
(`--muted-foreground`, `--foreground`, `--border`, `--card`); **no new
hex, no `--gain`/`--loss`**. Real data from `season_record.json`
`real.days[]`, the same object the hero above it reads. Mounted on
`/history` above the equity curve. No npm dependency added.

Fixed at 7 days and deliberately does **not** follow the page's
window toggle: a card captioned "last 7 days" that silently becomes 30
is the two-figures-one-label trap this page has been cleared of twice.

### The headline is not the obvious number, and that is the point

The obvious headline is "sum the last seven days". That is exactly what
the unit model forbids — the replay compounds the unit *count*, so a
10.00u loss on a 217u bank and a 2.00u loss on a 223u bank are not the
same quantity:

| | |
|---|---|
| naive sum of `simPnl` | **−13.17u** ← wrong, and 2.2× the truth |
| what the bank actually did | 223.07 → 209.89 |
| honest figure | **−5.91u** (−5.91% of bank) |

The curve plots the bank **indexed to 100 at window open**, so its last
point and the headline are one quantity read two ways and cannot
disagree. Verified: compounding the five daily returns gives −5.90u
against the bank ratio's −5.91u, the gap being float rounding.

### Added — `rebaseLastDays()` in `lib/season-record.ts`

Divides each day's P&L by the bank it *opened* with. That ratio is
bankroll-free — it is what a $1k follower and a $25k follower both
experienced — and the ratios compound to exactly the bank ratio.

It also reports `offSideBets` separately rather than dropping them.
`simBankAfter` stakes **YRFI only** (exporter fix, 2026-07-30) but
`day.games` still *contains* NRFI rows — 24 of them in the real-price
window, 1 in the last 7 days. Counting bets by walking games while
reading a YRFI-only bank puts two populations in one sentence. The card
names the excluded NRFI bet on its own face.

**Latent trap found, not yet fixed:** `ReplayWindow.pct` divides an
all-sides `pnl` by a YRFI-only `bankStart` and lands on −7.7% where the
bank says −5.91%. Nothing renders it today — `RoiPanel` computes its own
from `yrfi.pnl` — so it is a loaded gun rather than a live defect.

### Simulated, held apart by brightness

Every figure here is a replay, so nothing is tone-coloured no matter
which way the week went; the sign plus the word "up"/"down" carries
direction. Under the matrix palette this rule does more work than usual
because `--foreground` and `--gain` are both `#00FF41` — hue cannot mark
real money when the page is one hue, so brightness is the whole
distinction and this card sits on the dim side of it. The area fill uses
the existing `--sim-hatch` rhythm, rebuilt as an SVG `<pattern>` because
`fill` takes a paint server and not a CSS image.

### Verified

Production build. Every rendered figure reproduced against an
independent Python calculation over `season_record.json`:

| day | day P&L | week to date |
|---|---|---|
| Jul 24 | −0.90u | −0.90u |
| Jul 26 | 0.00u | −0.90u |
| Jul 27 | −1.81u | −2.69u |
| Jul 28 | −4.61u | −7.17u |
| Jul 29 | +1.37u | −5.90u |

Headline −5.91u / −5.91% of bank / 1–4 over 5 bets / Jul 24 → Jul 29.
375px: card 343px wide, **zero internal overflow**, `/history` still at
its pre-existing 18 offenders with none from this card. Pointer
hit-testing confirmed routing to all five bands.

**Not verified in-browser:** real mouse-hover and keyboard-focus firing.
The Browser pane is hidden in this environment, so Chrome dispatches no
`focus`/`focusin` events at all and React's enter/leave delegation never
runs. Handlers are confirmed attached and the render path is confirmed
correct when state is set; the event dispatch itself is untested.

---

## [2026-07-30c] — VT323 out, JetBrains Mono in

Operator: *"VT323 is too hard to read ... it is a pixel font -- at 11-13px
on a phone it is genuinely hard to read."* Correct, and it conflicts with
the usage scene PRODUCT.md actually describes: phone in hand, legible at
arm's length, thirty seconds of attention.

**Palette untouched.** Matrix black / phosphor green / alarm red is
unchanged; every measured contrast ratio is identical because no colour
token moved. This is a typeface change only.

### Why JetBrains Mono over IBM Plex Mono

Both were live options. The deciding number is x-height, measured on the
running page with canvas `actualBoundingBoxAscent` rather than taken from
a specimen:

| metric | VT323 | JetBrains Mono | delta |
|---|---|---|---|
| x-height | 0.40em | **0.55em** | +37% |
| cap-height | 0.56em | 0.73em | +30% |
| advance | 0.40em | 0.60em | **+50%** |

`font-size` sets a box; x-height decides how much ink is in it. Every
11-13px label on this dashboard was drawing more than a third smaller
than its declared size implied. JetBrains Mono spends its em on lowercase
rather than ascender clearance, and its digits keep open counters at
small sizes — which matters on a board whose job is telling `+4.17u` from
`+4.77u`. Plex Mono is the more conventionally proportioned face and
would have bought less.

Four weights are now loaded (400/500/600/700). The type scale has
declared four since it was written; VT323 ships one, so every weight
distinction in it had been a no-op or a synthesised smear.

### Fixed — four clipped elements, all fixed px widths tuned to 0.40em

The advance row is the cost of the swap and it is the larger number: text
is half again as wide at the same size.

| element | was | now | what was lost |
|---|---|---|---|
| board TIME column | 74px | **88px** | **every 10/11/12 o'clock start time** — 8 chars needs 84.3px, 7 needs 73.8px, so late games lost their last character and every other game fit exactly |
| ticker summary strip | — | λ̄ + games hidden ≤820px | content went 280px → 420px in a 271px masked box; the two figures stopped rendering *silently* |
| Tonight's Action side label | 50px | **56px** | trailing letter of `PENDING` |
| date select min-width | 158px | **146px** | trailing nav button border |

The ticker items hidden on mobile are the two this component's own header
calls "slate context that is NOT a bet count", and `.rightCap` was already
hidden at that breakpoint on the same reasoning. Both figures remain on
the page below. This converts an accidental omission into a deliberate one.

Deleted `font-feature-settings: "ss01" 1, "cv11" 1, "ss03" 1` from
`html, body` — Inter/Geist stylistic sets left behind by a font this
dashboard stopped using two palettes ago. Inert under VT323; under a face
that defines ss01/ss03 they are live instructions to substitute glyphs
nobody chose.

### Fixed — pre-existing, found while verifying at 375px

Browsing to any past date puts a "jump to today" badge beside the date
cluster. `.dateRow` never wrapped, so the badge *squeezed* the cluster
(215px of content into 169px) and `overflow: hidden` ate the trailing nav
button — the operator lost "next date" precisely when browsing history.
Now wraps instead of shrinking.

### Verified

Production build, not dev. Both pages at 375px and 1280px: **zero
overflowing elements on `/`**, zero clipped start times, ticker fits
exactly (271px in 271px), no console errors.

`/history` retains 18 overflowing elements at 375px — measured byte-for-byte
identical under both fonts (fixed px grid minimums of 482px in the zone
chart, and a diverging bar whose halves are drawn at full width instead of
half). Pre-existing, font-independent, not addressed here.

---

## [2026-07-30b] — The replay stops staking NRFI; /history charts the system

Operator: *"i wanted to remove the flat unit tracking ... and all the
charts should reflect the kelly sizing with the new system going back to
the start of the season, what our system would have picked and our
profit."*

### The replay was staking a side the system does not bet

`export_season_record.py` ran `simulate(y_bets + n_bets)` — it staked
**NRFI**. `_LR_STRONG_NRFI_P` has been 1.01 ("off") since 2026-06-07 and
the last real NRFI bet was 2026-06-14, so the headline bank curve
described a system nobody runs.

| | before | after |
|---|---|---|
| bets staked | 127 | **106** |
| bank | 100u → 227.11u | **100u → 262.88u** |
| max drawdown | 27.9% | **23.7%** |

Every consumer was already compensating by hand: the dashboard headline
read `yrfi.pnl` instead of the sim's own profit, and the daily and flat
figures each re-excluded NRFI separately. **Three hand-corrections for
one wrong input.** Fixed at the source. NRFI is still tracked — 24
would-be bets, −15.04u — simulated on its own independent bank so the
detail survives without touching the money path.

`day.simPnl` / `day.flatPnl` had the same split: the bank was YRFI-only
while the day totals beside it still folded NRFI in (+147.88u vs
+162.92u). Now both read the staked side only.

### /history charts the system, not the retired ledger

One series feeds the equity curve, drawdown, days-under-water and the
daily table, so switching it rewires the page at once. It now reads the
replay's compounding bank.

Removed with it: the **flat-1u** line and stat (operator's request — the
system stakes quarter-Kelly, so a flat figure describes a scheme nobody
runs), the **"what actually happened"** ledger line, and the
System/You column split, which existed only to reconcile a replay
headline against a ledger table that is no longer on the page.

### Two bugs caught while wiring it

1. **The oldest visible row reported the whole season as one day.** Day
   P&L was derived by differencing the cumulative with `prev` seeded at
   0, so on any window not starting at the opener the first row showed
   its entire season-to-date total — "Last 30 days" had a +150u day. Now
   read from the record's stored `simPnl`; no edge case, exact.
2. **The hero and its own table were in different units.** The headline
   rebased to a 100u base while the table is absolute, so 30d read
   +13.23u above a column summing to +30.7u. The headline is now raw
   window profit and the bank it was earned on is named in the sub-line.

Verified per window — hero equals its Day P&L column exactly:
season +162.92u (100u bank), 30d +30.71u (232u bank), 7d −40.28u (303u
bank).


## [2026-07-30] — Old ledger removed; the "Flat 1u" stat was measuring the wrong bets

Operator: *"remove the old ledger entirely ... still keep the data
saved"*, and *"are there any other bugs or errors to fix?"*

### Removed — the flat-1u ledger block

Gone from `RoiPanel`, along with `LedgerRow` and its helpers (96 lines).
It reported the same nights as the system card above it under an
accounting method retired 2026-07-28 (flat 1u, looser gate, NRFI still
live), and its own footnote conceded the figures rested on a placeholder
−110. Season-wide it read −8.93u, of which **−11.29u is NRFI** from a
strategy switched off 2026-06-07.

**No data was deleted, deliberately.** Every row is untouched in
`data/picks_2026.csv` and Supabase. `tools/pl_calc.py` reports it and
`tools/kelly_season_backfill.py` compares flat against every Kelly
fraction on demand, so "what would flat 1u have done" is still one
command away. This removed a *render*, not a *record*.

### Fixed — "Flat 1u" covered a different set of bets than its headline

Shipped in 2026-07-29i, wrong from the start. `flatPnl` summed each
day's day-level `flatPnl`, **which includes NRFI** — while every
headline it sits beside is YRFI-only, because NRFI is tracked and never
bet.

| | shown | correct |
|---|---|---|
| Season flat | +9.29u | **+12.30u** |

The −2.97u gap is 22 NRFI would-be bets. A figure captioned *"the same
bets, unlevered"* that silently covered a **different** set of bets is
exactly the defect this dashboard spent two days removing — reintroduced
by the fix for it.

`flat` is now accumulated per game inside the same `action === "BET"`
loop that produces `pnl`, and split per side, so the two cannot describe
different populations. Both call sites read `yrfi.flat`.

### Also fixed — the phantom-deleted files

`DashboardShell`'s comment claimed `StatusLine`, `ShadowPnlCard` and
`SlateProjections` "were DELETED outright on 2026-07-28 — files and
stylesheets", and that `SummaryStrip` was "still live: OpsHealthCard and
TonightsActionCard both import it. Do not delete it."

**Both claims were false.** All four `.tsx` files and all four
stylesheets were still on disk, and the two "imports" of `SummaryStrip`
were *prose mentions inside comments* — verified by grepping actual
`from "..."` lines, which returned nothing for any of the four.
`ShadowPnlCard` was additionally the sole caller of `/api/shadow-pnl`,
leaving that route live in production with no consumer.

Deleted for real: 8 files plus the orphaned route (now 404). Verified
against `.next/static/chunks` that none reached the bundle before
removing, and both pages render with a clean console afterwards.

The comment now records why this matters beyond tidiness: **a comment
asserting code is gone when it is not is how the
`realPricedCumulativePL` bug survived** — documentation stating a state
nobody re-checked. If you claim a deletion there, run the grep in the
same commit.


## [2026-07-29k] — The daily ledger now reconciles to the headline

Operator, on the daily ledger: *"i think these numbers are off"*.

**They were not.** Verified across all 27 rows: the running total is
internally exact, every `cumulative` equals the previous row's plus the
day's P&L, and the distribution bars are proportional. Nothing was
miscalculated.

**But the page was genuinely confusing, and that is on the redesign.**
The hero at the top now reads **+14.54u** (the system) while the table
below ends at **−4.41u** (the ledger), and the column was headed only
"Day P&L" — it never said *whose*. A table ending on a different number
than the headline above it, with no label distinguishing the two
populations, reads as an arithmetic error. That is the same failure mode
as *"i thought we were up 90+ units"*, one layer down.

### Both columns, each summing to its own headline

`Day P&L` → **`System`** and **`You`**. Verified live: the System column
sums to **+14.54u** (exactly the hero) and You to **−4.42u** (the ledger,
to rounding). The per-day divergence is now legible — 2026-07-27 the
system lost 5.35u on a night the operator made 1.29u.

`System` renders in neutral ink at every sign because it is simulated,
beside a real-money column that carries tone. `—` means the replay has
no entry for that date, which is different from a 0.00u day where it
looked and declined.

### Two traps, both checked numerically rather than assumed

1. **`day.simPnl` is unusable for this.** It includes NRFI, which the
   hero excludes because NRFI is not bet. Over the 30-day window
   `day.simPnl` rebases to +10.42u against the hero's +14.54u — the
   −4.12u gap is exactly the NRFI side. The per-day figure is summed
   from each day's *games*, YRFI only, matching `replayWindow`'s own
   bucketing.
2. **Rebasing needs one divisor**, the bank at window start, not each
   day's own bank. Scaling every day by the same constant is what makes
   the column sum to the hero.

Five grid tracks now; wraps to two on a phone with header and rows
sharing the same flow so they stay aligned. Dead `.tiles` / `.tileBig`
responsive overrides removed with the tile family they styled.


## [2026-07-29j] — /history gets the decision-first hierarchy

Redesign pass 2. The dashboard got hierarchy on 2026-07-29g; /history
was deferred and still opened with **two identically-weighted bordered
tiles stacked on each other** — the system's figure and the ledger's.

That is the exact pattern PRODUCT.md names as the reason this page is
not scannable ("a stack of same-weight cards"), and it did something
worse than look flat: two equal cards for two numbers that answer
*different* questions read as a contradiction. It is the same confusion
that produced *"i thought we were up 90+ units"*.

### One hero, one subordinate line

- **The system leads at 40px** — that is what the page is for.
- **The unlevered twin sits directly beneath it.** Over 30 days those
  read +14.54u and +0.89u: the same bets, one multiplied ~16× by
  compounding. Adjacent, that is obvious; apart, invisible.
- **"What actually happened" is a line, not a rival card.**

The colour law produces a deliberate inversion here: the **big** figure
is neutral ink because it is a replay, and the **small** one carries
`--loss` rose because it is real money. Hue marks what is *real*, not
what is loud.

### Dead code removed

`tileTone()` and the whole `.tile` family (`.tiles .tile .tileBig
.tileLabel .tileSub .tileProv .tileTonePos .tileToneNeg`) — 77 lines of
CSS, verified at 0 usages after the collapse. `.tileTonePos` /
`.tileToneNeg` also carried `inset 3px 0 0` side stripes, the
coloured-edge-as-hierarchy pattern removed everywhere else that day.

Caught while wiring it: `tileTone()` was briefly hung on the inline
`.actualFig` span. It returns a CARD-level class that colours a
descendant `.tileBig` and paints that inset stripe — so on a span it
coloured nothing and drew a stray bar. Replaced with
`.actualFig[data-tone]`.

Verified at 375px and 1270px: no horizontal overflow, hero above the
fold on a phone, all four downstream sections (equity, drawdown, zones,
daily ledger) intact, clean console on a fresh tab.


## [2026-07-29i] — Every date filter now answers "what would the new system have done"

Operator: *"update the entire dashboard so i can see what our new record
and profit would be when i choose the date filters"*.

### /history had the same filters and answered a different question

The main dashboard and /history both offer 7d / 30d / season. The
dashboard showed the **replay** (today's rules), /history showed the
**realised ledger** — which is mostly bets placed under rules that no
longer apply: NRFI was live then and is switched off now, and the YRFI
gate has since tightened. So the same filter on two pages gave two
unrelated numbers with nothing saying why.

/history now leads with a windowed system card, `real` side, matching
the dashboard figure exactly:

| window | the system (¼-Kelly) | the actual ledger |
|---|---|---|
| Last 7 days | −16.03u (0-4) | −11.15u |
| Last 30 days | +14.54u (21-13) | −4.41u |
| Season | +144.85u (67-38) | −8.93u |

Both are true and they answer different questions; they are now labelled
as such instead of looking like a contradiction. The record is read
server-side so the card renders on first paint, and soft-fails to null —
a missing record costs one card, never the page.

### "Priced: N of N" → "Flat 1u"

That tile counted bets with a captured DraftKings price, which mattered
while the headline came from `projected` and a third of its book was
filled at an assumed −125. The headline now comes from `real`, which by
construction has **zero** assumed prices — so the tile could only print
"43 of 43". A stat with no variance is not a stat.

The slot goes to the figure that actually explains the headline: **the
same bets, unlevered, at a flat 1 unit.**

| window | ¼-Kelly | flat 1u |
|---|---|---|
| Last 7 days | −16.03u | −6.00u |
| Last 30 days | +14.54u | **+0.89u** |
| Season | +144.85u | **+9.29u** |

Last 30 days is the one to read twice: **+14.54u levered off +0.89u of
edge.** That is almost entirely compounding, not model performance —
precisely the distinction the operator had been missing, now visible
without asking for it.

### The truncated window says why

The header said the season starts 2026-04-01 while the card reported
from 2026-05-07, unexplained. `real` only covers dates where prices were
actually captured; April has none. Stated inline now, rather than left
as a date mismatch the reader has to rationalise.


## [2026-07-29h] — The system record drops the invented prices

Operator, after being told the season was down: *"i thought we were up
90+ units profit on the season with the new system and the kelley
sizing"* — then, asked what they wanted: *"the real record should be
based off if we started with 100u bankroll, sizing properly"*.

### The headline was compounding prices that were never observed

`season_record.json` carries two sides. `projected` fills every bet with
no captured DraftKings price at an assumed **−125** — 62 of its 194
bets, a third of the book. `real` is the same model, same gate, same
quarter-Kelly sizing, on the subset whose price was actually observed.

The SystemCard headline read `projected ?? real`. Every other consumer
in the app already preferred `real` first; this one surface did not, and
it is the one rendered at 32px.

| | bets | invented prices | flat edge | ¼-Kelly from 100u |
|---|---|---|---|---|
| `projected` | 194 | 62 | +32.46u | → 866.08u (**+766u**) |
| `real` | 127 | 0 | +9.33u | → 227.11u (**+127u**) |

The exporter's own docstring already warned about this: *"a simulated
100u bank turns a +34u edge into a +880u 'profit' that was never
earned."* Headline is now `real ?? projected`; SEASON reads **+144.85u**
(YRFI only, 105 bets) instead of **+822.19u**. `projected` is not
deleted — it still renders inside "How this number was computed", which
is the disclosure that exists to state the price assumption.

Also regenerated `season_record.json`, which was stale: it described 191
bets under an older gate while a fresh replay produces 194.

### The number is still a simulation, and the gap is SELECTION not sizing

Both figures are `Simulated`-tagged and neutral-toned per the colour
law. The honest reconciliation the operator needed:

- **Real ledger, real prices, what actually happened: −10.55u** over 528
  graded STRONG bets.
- **Replay, real prices, quarter-Kelly from 100u: +127u** over 127 bets.

Those differ mainly because **the replay bets a quarter as often** — it
applies today's tightened gate to the whole season, where the ledger bet
everything the system flagged STRONG at the looser gates in force at the
time. Resizing a losing selection does not make it win; the replay wins
by *not taking* ~400 of those bets. Recorded here because "make the
simulation real" is an operational change (bet the replay's slate), not
a reporting one.


## [2026-07-29g] — Decision-first redesign: the card now names the plays

Operator asked for a full UI redesign and chose **decision-first
triage**: the top of the screen answers only "what do I bet tonight and
how much", everything analytical below.

### The card counted things and never said which games to bet

`TonightsActionCard` opened with "**2** flagged STRONG", a NRFI/YRFI
split and a passed tally. PRODUCT.md says the scene is a phone in one
hand in the hour before first pitch, asking *"what do I bet tonight, and
how much?"* — and answering it required scrolling past a ~400px
performance panel to a 16-row table and picking the STRONG rows out by
eye. **A count is a summary of the answer, not the answer.**

The card now leads with the plays themselves — matchup, first pitch,
side, stake, price, and the lock deadline — one 56px+ row each, sorted
by **what closes next** rather than board rank, with locked and graded
plays sinking to the bottom because there is nothing left to do about
them. Total exposure follows as a single line.

Stake resolution is byte-identical to the board's StakeChip (replay
first, ledger second); the same quantity in three places must not drift.

### Reordered: analysis moved below the slate

`RoiPanel` sat above the board. **This reverses an earlier explicit
operator request** ("the record is the second thing he wants to see") and
is called out in the source rather than quietly changed — on a phone it
put 400px of analysis between the decision and the slate the decision
came from. Order is now: plays → board → performance → why. Restoring the
old position is a one-block move; nothing depends on it.

### `lib/lock.ts` — one definition of the deadline

`computeLockAt` / `formatLockTime` lived inside BoardRow. The decision
card needs the same deadline, and two copies of a deadline are two
chances to disagree about it. Extracted with `minutesUntil` and
`formatCountdown`, and unit-tested: T-60 arithmetic, an EDT and an EST
slate (DST is derived, never hardcoded), placeholder times returning null
rather than inventing a deadline, and the countdown wording.

The deadline is the one element that earns `--attn` by the page's own
colour law ("a decision is waiting on you"), and it goes solid inside 45
minutes. No pulse — PRODUCT.md names sportsbook urgency as an
anti-reference.

Verified at 375px and desktop: no horizontal overflow, plays above the
fold, and the empty state stays calm ("No games flagged tonight. Nothing
to bet — the model passed on 4 of 5 games") on a no-play slate, which is
about a third of nights.

/history keeps its current layout for now, per the chosen scope.


## [2026-07-29f] — /history showed the wrong number and a frozen one; amber → violet

Operator: *"fix https://nrfi-terminal.vercel.app/history because it
doesnt show the real numbers and updates"*. Two independent defects.

### Fixed — the real-priced series was consumed but NEVER PRODUCED

`HistoryView` has read `realPricedCumulativePL` since the 2026-07-28
audit. **`lib/roi.ts` never produced it.** The consumer shipped without
the producer, so the fallback fired on every render: the page charted
the fabricated −110 series and printed *"Reload the page to pick up the
real-priced figure"* — advice that could never work, because no reload
could conjure a field nothing wrote.

The gap it hid was not subtle. The season headline read **+21.86u**
while the zone table directly beneath it summed to **−12.67u**. Opposite
signs, same bets, one screen. Now **−8.93u**, and it agrees with the
split line to the cent.

**Why it went unnoticed:** the field was read through an inline cast,
`data as RoiResponse & { realPricedCumulativePL?: SeriesPoint[] }`. An
*optional* property on a cast type cannot fail to compile when the
producer omits it — there was no type error, only a silent `undefined`.
Both `realPricedCumulativePL` and `stakeEpoch` are now declared on
`RoiResponse` itself and read directly, so deleting the producer is a
compile error. Adding them immediately surfaced two more producers that
had been silently incomplete (`roi-today.ts`, and `loadRoi`'s empty
fallback) — exactly the point.

### Fixed — the page could only ever be as fresh as the last deploy

`loadRoi` read `picks_<year>.csv` off disk, and `npm run prebuild`
copies `data/` into the bundle at **build time**. A Vercel deployment's
filesystem is immutable, so /history showed a frozen snapshot: the
operator watched HOU@LAA settle +4.117u on the main board while
/history still showed the night at −2.08u. `dynamic = "force-dynamic"`
did not help — it re-runs the render, and re-reading a frozen file
yields the frozen answer.

Now Supabase-first with CSV fallback, mirroring `lib/board.ts` (which
already did this, which is why the two pages disagreed). **Paginated**,
because PostgREST caps at 1000 rows and a season is ~2400 — an
unpaginated read would have silently dropped the oldest 60% of the
season, the same cap that previously truncated `pl_calc` and the date
picker. Jul 29 now reads **+2.04u**, matching `pl_calc`.

**Boundary bug caught while wiring it:** Supabase returns native
Postgres types (`profit_loss_units` as a JS number) while `parseCsv`
returns strings, and the 250-line aggregator calls `.trim()` throughout.
Handing it raw rows crashed the page with *"(r.profit_loss_units ??
'').trim is not a function"*. Rows are now normalised to the CSV string
shape at the boundary rather than teaching every call site to handle
both.

### Fixed — "split by side" silently dropped real money

The split line read `betZones` only, which excludes anything whose
`pick_side` is PASS. But a PASS-labelled row can hold a real bet at a
real price: a STRONG pick still labelled "LINEUP PENDING" when its lock
window closed gets `bet_placed=Y` and settles normally (2026-07-27
NYY@CWS, +0.909u). Season-wide that is **+1.62u over 3 bets**, and
dropping it left the split summing to −10.55u under a −8.93u headline.
Same population now; they agree exactly.

### Changed — `--attn` amber → violet

Operator: *"change the amber to violet"*. Amber was the last warm hue on
screen. Dark `#fbbf24` → `#a78bfa` (7.10 / 6.35 / 5.49), light `#7e5800`
→ `#6d28d9` (6.36 / 6.97 / 5.74). The three money hues now sit at
**190 / 255 / 345** — no two within 65°, none warm, and violet's
luminance lands between gain and loss so they stay ordered in greyscale.
App icons re-keyed too.


## [2026-07-29e] — The 112 inverted capture timestamps, healed

Operator authorised repairing history. `tools/heal_capture_ts_inversions.py`.

**Which column was corrupt — measured, not assumed.** Within its own
slate day, on the inverted rows, `odds_captured_at` sits at the **7th
percentile** (healthy baseline: 38th) while `opened_captured_at` sits at
the 29th (healthy: 43rd). A "latest price seen" that early is the
dragged-back one; `opened_captured_at` is the trustworthy side.

**Recovery was attempted before clamping.** All 94 daily backup
snapshots (2026-05-02 onward) were searched for a surviving
pre-corruption value — a capture later than the corrupted one and
consistent with `opened_captured_at`. **Zero of the 112 rows had one**;
the drag-back always preceded the daily snapshot. No real value
survived, so the repair sets `odds_captured_at = opened_captured_at`:
a real observed timestamp for that row, explicitly a **lower bound**.
Those rows now mean "the price had been seen by at least this time".
11 of them have `bet_placed=Y`, so their lock time is a lower bound too.

**Verified surgical.** Money-column fingerprint
(`market_*_odds`, `bet_placed`, `units_risked`, `profit_loss_units`,
`graded_result`, `pick_side`, `pick_strength`, `opened_*`) is
byte-identical before and after. Exactly 112 cell changes, in exactly
one column, across an unchanged 1579 rows. `pl_calc` reports stored and
recomputed P&L agreeing at −6.030u with no drift. Re-running the heal
now finds 0 — idempotent.

### Fixed while healing — the guard had the wrong rule for one column

The two timestamps move in **opposite** directions:
`odds_captured_at` is "latest seen" (high-water mark),
`opened_captured_at` is "first seen" (**low**-water mark). The first cut
of the guard applied `advance_capture_ts` to both, which would have
locked in the wrong direction and let `opened_*` drift forward — the
same class of defect, mirrored. New `tracker.retreat_capture_ts` for the
low-water side. Measured before shipping: `opened_*` had not in fact
drifted forward (29th vs 43rd percentile), so this closes a latent hole
rather than an active one.

### Fixed — the heal's own audit trail failed silently

`_record_pick_change` takes a required keyword-only `captured_at` that
the first version omitted, so all 112 journal calls raised `TypeError`
into a blanket `except` and the heal completed with an **empty audit
trail**. Backfilled from the git diff (exact old→new pairs) — 112
entries now in `pick_changes.csv`. The script now counts successful
journal writes and warns loudly if the count does not match the rows it
edited, so a heal can never again report success while its audit trail
quietly failed.


## [2026-07-29d] — Kelly stakes round to whole units, floored at 0.5u

Operator asked whether stakes should be rounded, since quarter Kelly
produces figures like `5.97u` and `2.08u` that have to be typed into
DraftKings by hand. Then chose the variant: *"round to whole units, but
any bets that might round to 0 should just round to 0.5 units."*

Measured first, over all 348 graded real-priced STRONG bets:

| sizing | profit | bets placed | max DD |
|---|---|---|---|
| exact (was shipped) | +81.20u | 301 | 39.6% |
| whole units, small → no bet | +92.79u | **285** | 40.0% |
| **whole units, floor 0.5u (shipped)** | +83.83u | **300** | 39.7% |

**Plain whole-unit rounding silently drops 16 of 301 bets** — anything
under 0.5u rounds to zero, and zero is a no-bet. That is a hidden bet
gate arriving through a convenience change, the exact class of surprise
CLAUDE.md's money rules exist to prevent. The floor recovers 15 of them.

The +2.63u over exact sizing is **noise, not an edge** — same bets,
slightly different sizes, landing favourably by chance. Do not quote it
as an improvement. The case for rounding is convenience at no cost.

`NRFI_KELLY_ROUNDING` (default `1.0`, set `0` to disable) and
`NRFI_KELLY_ROUNDED_FLOOR` (default `0.5`).

### Two safety properties, both tested

- **A no-edge bet is never floored into a real bet.** Kelly returns 0
  for two reasons — the model does not beat the market's implied
  probability, or the daily cap left no room — and both are deliberate
  refusals. The rounding block sits *below* the no-bet gate, so it only
  ever operates on stakes that already earned the right to exist.
- **Rounding up cannot breach a cap.** Found while wiring this: on an
  88.36u bank the per-bet ceiling is 8.836u, and an 8.60u stake rounds
  to 9.00u — over it. A convenience feature would have defeated a risk
  guard rail. When the rounded figure does not fit under the per-bet
  ceiling *or* the daily budget, the exact stake is kept instead.

`tools/verify_kelly_wiring.py` gains CHECK 5 covering both, plus the
floor and ordinary rounding. CHECK 2 now models rounding in its
independent reference implementation rather than being loosened — the
tolerance is still 0.011u and it reports 0.0000u disagreement.

Tonight's two bets are unaffected: T2.23 freezes `units_risked` once
`bet_placed=Y`, so they stay at the 5.97u and 2.08u they were placed at.

---

## [2026-07-29c] — Units lead the headline; the replay stops wearing the money hue

Operator: *"why are the 'last X days' filters showing percentages and
not units? im so confused?"*

### Changed — the performance headline is units, not a percentage

The card's docstring justified the percentage: the raw replay compounds
100u → ~1200u by late July, so its own units describe a bankroll nobody
has. True, but the fix for that is the **rebasing**, which the card
already does via `bankUnits`. With both in place the headline read
`+16.3%` above a sub-line reading `+16.33u on your 100u bankroll` —
**the same number twice**, since at a 100u bank a unit *is* a percent.
The operator stakes in units and was shown their result in the one unit
they don't think in. Units now lead; the percentage moves to the
sub-line (kept, not deleted — the two separate again if the bankroll
ever moves off 100u).

### Fixed — a backtest was rendering as realized P&L

Swapping the headline to units exposed this, and made it worse: SEASON
showed a 32px **`+822.19u` in `--gain` cyan**, visually identical to
TONIGHT's real `−2.08u`.

Quarter-Kelly went live 2026-07-28; every bet before that was flat 1u.
The card's own code says it: *"TONIGHT comes from the board, not the
record. Everything else comes from the replay."* So every window except
TODAY is a simulation of a staking scheme that was not in use, and all
four were toned from `y.pnl` as though they were money.

globals.css, verbatim: *"SIMULATED FIGURES ARE NEVER TONE-COLOURED.
Coloured = your money. Neutral = a back-test. No exception, no
carve-out."* This was the exception. PRODUCT.md lists it as the
product's central design problem (four kinds of number at
near-identical visual weight) and as an explicit anti-reference.

Replay windows now render in plain `--foreground` and carry a
`Simulated` tag in the eyebrow. TODAY keeps its tone — it is the only
real-money figure on the card. Verified in-browser: TODAY `rgb(251, 92,
120)`, the other three `rgb(233, 239, 246)`.

---

## [2026-07-29b] — Capture-timestamp guard + the palette reversal

Operator: *"fix the timestamp bug then fix the colors. i hate the
orange colors. it needs to be bright colors that look great."*

### Fixed — `odds_captured_at` could run backwards

112 of 1129 rows (9.9%) carried an `odds_captured_at` EARLIER than
their own `opened_captured_at` — impossible by construction, since
`_apply_odds_to_row` assigns both from the same value on first import
and only ever moves the former forward.

**Cause:** `tools/sync_csv_from_supabase.py` (runs every predict and
grade tick) merges the Supabase mirror into the CSV *column by column*
and skips any column blank in Supabase, so a row could be assembled out
of two different capture moments — a lagging mirror's
`odds_captured_at` beside a fresher `opened_captured_at` the CSV
already had. The file's own comment asserted "Supabase is the fresher
writer for these columns"; that holds for values, not for time.

**Fix:** capture timestamps are now high-water marks. New
`tracker.parse_capture_ts` / `capture_ts_regressed` /
`advance_capture_ts`, wired into both write sites. The sync reports
`N advanced, N rejected as backwards` each run.

No money was affected — `bet_placed` and `units_risked` on the affected
rows were correct — but the T2.23 lock freezes `odds_captured_at` when
a bet commits, making it the only ledger evidence of *when* a bet
locked, so the T2.58 window was unauditable and CLV was suspect.

New `tools/verify_capture_ts_monotonic.py` (7 + 6 + 5 + 1 assertions,
all passing) including a replay of the original defect. Historical rows
are **not** repaired: that rewrites the ledger and needs operator
sign-off per CLAUDE.md's data rules.

### Changed — the warm palette is retired

This reverses a preference recorded in CLAUDE.md, AGENTS.md and
PRODUCT.md as "explicitly and repeatedly chosen." All three were
updated in this commit, because a future agent reading the old note
would helpfully put the orange back.

Swapping three accent tokens would not have worked: every *surface*
was hue ~30 too, so the background, cards, borders and body text were
all brown or tan. "The orange colors" was an accurate description of
essentially the whole screen.

| | old | new (dark) | new (light) |
|---|---|---|---|
| background | `#12100e` | `#0a0e14` | `#eef3f8` |
| foreground | `#f0e4d3` | `#e9eff6` | `#0f1a24` |
| `--gain` | peach `#f5a465` | cyan `#22d3ee` | teal `#0b5f77` |
| `--loss` | tomato `#ec8060` | rose `#fb5c78` | crimson `#b81a3c` |
| `--attn` | amber `#f0c96e` | amber `#fbbf24` | gold `#7e5800` |

The money hues are now ~150° apart instead of sharing a 30° wedge of
orange. The previous pass had to fight for luminance separation
precisely because it had no hue to spend; separation now survives
greyscale, dim phone screens and red-green colour blindness.
Terminal green/red remains rejected and absent.

Every ratio was recomputed and verified in the browser against rendered
surfaces, not asserted. All money hues and both ink tones clear AA on
all three surfaces in both themes. `--border` now clears 3:1 on all
three in dark, retiring the old palette's knowing 2.95 concession.

### Fixed — two more side-as-hue violations found during the repalette

Swapping the tokens made these obvious, because they got *louder*:

- **The pick pills painted side as money.** `.nrfiStrong`/`.nrfiLean`
  were `--primary` (= gain), `.yrfiStrong`/`.yrfiLean` were
  `--destructive` (= loss), `.passTone` was `--secondary` (= at risk) —
  on the pill background, dot and label of every row. Every YRFI row
  rendered in the losing colour whether it won or lost, and PASS rows
  (usually the largest group) carried the at-risk hue while having no
  money on them at all. Rewritten to weight: side via the dot's ink
  (`--side-nrfi` / `--side-yrfi`), strength via label ink and border.
- **`.resultPass`** used `--attn` on the result chip of a game the model
  declined. Now neutral. `.resultWin`/`.resultLoss` were already correct
  and are unchanged.

Warm elements on the rendered page: **52 → 3**, and all three survivors
are correct uses of `--attn` ("8.05u at risk", the change-banner dot).

### Also

- PWA manifest `theme_color` was `#5dff9a` — phosphor **green**, the
  palette the operator rejected — sitting on their home screen. All four
  app icons were green + orange; recoloured. `themeColor` and
  `msapplication-TileColor` re-keyed to the new `--background`.

### Follow-up — four survivors a hex grep structurally could not find

The first sweep matched `#rrggbb`. These four were live orange in other
notations and were caught only by re-sweeping for `rgb()`/`rgba()`
triples and URL-encoded `%23` forms:

- **The browser-tab favicon** (`layout.tsx`) — inline data-URI SVG with
  `fill='%23f5a465'`. URL-encoded, so the `#` anchor missed it.
- **`::selection`** (`globals.css`) — highlighting any text on the page
  handed back a band of the retired peach. Written as `rgba()`.
- **The brand mark's glow** (`DashboardShell`) — `rgba(245,164,101)` at
  the top-left of every screen, the most persistent orange left.
- **`LambdaMeter`'s drop shadow** — a warm near-black, now cool.

Lesson recorded in the source: a colour audit must sweep `rgb()`,
`rgba()` and `%23` forms, not just hex literals. Final verification
scans computed `boxShadow` and `backgroundImage` as well as text and
background colours: **3 warm elements page-wide, all correct `--attn`.**

---

## [2026-07-29] — Money-path verification + the last side-hue numerals

Operator asked for three things: confirm Kelly sizing is working, confirm
the right picks are being placed and on time, and critique the design
because *"i still think its so confusing with the data."*

### Verified (no code change needed)

- **Kelly sizing is correct end to end.** `tools/verify_kelly_wiring.py`
  passes all four checks. Reproduced both of tonight's live stakes by
  hand from the shipped helper: TOR@WSH `p=0.6052 @ −130` → 2.08u off a
  90.44u bank; HOU@LAA `p=0.7021 @ −145` → 5.97u off an 88.36u bank
  (the bank had compounded down by TOR@WSH's −2.08u). Exact to the cent,
  which also confirms the 2026-07-28 P0-1 fix holds: the stake did not
  oscillate across the evening's odds re-imports.
- **Both of tonight's STRONG picks locked inside the T2.58 window.**
  TOR@WSH at T−38min, HOU@LAA at T−58min. Season median notice is 57
  minutes; the Vercel cron cadence (:00/:30 UTC) against a 60-minute lock
  window bounds operator notice to roughly 30–60 minutes, which the data
  bears out.

### Fixed

- **The retired side-hue scheme was still live on every board row.**
  `.distLabelNrfi` was `--primary` and `.distLabelYrfi` was
  `--destructive` — exactly what the 2026-07-28 colour law abolished
  ("a peach number meant either 'this is an NRFI pick' or 'you made
  money' and the reader could not tell which"), and what its side-ink
  note forbids ("NEVER on a numeral"). The recolour pass moved the dots
  and bar fills and missed these two, so every row printed a peach and a
  rust figure that were not money, inches from an edge % and a stake that
  were. Probabilities now render in plain ink with a weight-coded side
  tag.
- **`DemotionsBanner` used `--primary` (= `--gain`) for a demotion.**
  A peach edge means real money UP; this banner announces bets being
  demoted. Moved to `--attn`, and the 3px side stripe dropped for a
  hairline.

### Changed — information architecture

Tonight's P&L was rendered **seven times** on one screen. Now four, each
with a distinct role (sticky ticker / decision hero / window-scoped
performance / reconcile-table caption). Nothing was deleted; the cuts
were restatements and progressive disclosure.

- `DayReconcile` stated the same night three times inside one card
  (header chain, footer lines, prose paragraph). Footer now carries only
  the W-L split the chain cannot say; the paragraph is reduced to the one
  sentence that explains the you-vs-replay difference, and is gated on a
  replay existing. The duplicate big "You −2.08u" figure is gone.
- The four-sentence flagged/placed/settled legend is now a collapsed
  disclosure on `TonightsActionCard`. Same words, one tap away.
- The superseded **"Older ledger · flat 1u"** block is collapsed by
  default. It reports the same nights under an accounting method the
  system stopped using, and its own footnote concedes the figures rest on
  a placeholder −110 — a knowingly-wrong number at eye level below the
  right one. Rows unchanged behind the disclosure.
- Board rows show **one** probability, not a pair summing to 100, tagged
  with the side it refers to and reported for the pick side (so it is
  never the number the reader has to subtract from 100).
- A STRONG pick that has not locked yet is the only row on the board with
  a deadline, and it was styled identically to "LINEUP PENDING", which
  needs nothing from anyone. It now carries `--attn` per the colour law.
  Deliberately no pulse: PRODUCT.md names sportsbook urgency as an
  anti-reference.
- Removed 3px `border-left` accents from `DayReconcile`, `RoiPanel` and
  `DemotionsBanner` (coloured side stripes standing in for hierarchy).

### Open

- **`odds_captured_at` runs backwards on 112 of 1129 rows (9.9%)**, still
  occurring as of today. It is set to the same value as
  `opened_captured_at` on first import and only ever moves forward, so an
  earlier value is impossible. Money is unaffected (`bet_placed` and
  `units_risked` are correct), but it is the only ledger evidence of when
  a bet locked, so it makes the T2.58 window unauditable from data and
  likely corrupts CLV. Prime suspect is
  `tools/sync_csv_from_supabase.py` writing a lagging mirror value back
  over the fresher local one. Not fixed here — needs isolation first, and
  no historical backfill without operator sign-off.

---

## [2026-07-28] — Dashboard rebuild: one night, one set of numbers

Operator report: *"the dashboard looks like shit visually and its so
difficult to read"*, and separately that it *"is not reflecting the
proper units won or lost per day"*. Both were true, and the second was
not a display bug.

### The defect

One night rendered three different ways on one screen, with nothing
explaining the difference:

| surface | 2026-07-27 |
|---|---|
| ticker | `6 STRONG YRFI` |
| ledger card | `4 graded bets · −0.33u` |
| season record | `1 bet · −11.15u` |

Two independent causes stacked:

1. **The gate moved and the ledger is frozen at the old one.** Those six
   picks were made under the 0.44 cutoff; the record replays the current
   0.40. Five of the six scored 0.418–0.450 — through 0.44, not through
   0.40. Both numbers were right about different systems.
2. **The record card was mislabelled.** It said "CURRENT MODEL REPLAYED"
   while scoring every game with a calibrator rebuilt from scratch at each
   date. That curve reads +0.0252 higher than the shipped one in April,
   +0.0077 in July, and because YRFI fires on a LOW p_nrfi, reading high
   means betting less. Over the real window the gap alone was 31 bets and
   +6.71u of flat profit.

### Changed

- **The record now reports BOTH methods and leads with the deployed one**
  (`tools/export_season_record.py`). Headline scores with
  `data/calibration_v2.json` at the live gate — the model actually
  running. The no-hindsight walk-forward figure is computed alongside and
  printed beside it as the floor, never hidden.

  | | bets | record | flat |
  |---|---|---|---|
  | REAL deployed (headline) | 125 | 78-47, 62.4% | **+11.33u** |
  | REAL walk-forward (floor) | 94 | 61-33, 64.9% | +11.23u |
  | PROJECTED deployed | 190 | 127-63, 66.8% | **+34.66u** |
  | PROJECTED walk-forward | 139 | 94-45, 67.6% | +25.45u |

  Note the real window: 31 extra bets, +0.10u. The deployed model's extra
  volume is roughly break-even; the edge is in the shared core.

- **The headline is flat profit, not the compounded bankroll.** Quarter
  Kelly on an imaginary 100u bank turns a +34.66u edge into +879.64u. That
  figure still renders — it is one sentence tagged SIMULATED inside the
  replay card, and `.simCard` forces every figure in that card to
  `--foreground !important` so a simulated number can never appear in the
  same peach as real profit. A one-week dismissible note says where it
  went; the operator's incident history is entirely about things
  appearing to vanish.

- **Real money and simulated money are now different surfaces.** Ledger =
  raised card, tone rail. Replay = recessed, hatched left rail, no tone.

### Added

- **`DayReconcile`** — the per-date drill-down that reconciles the three
  counts game by game: `FLAGGED 6 · PLACED 4 · SETTLED 4 −0.33u`, with the
  replay count deliberately OFF that chain (it is a different population,
  not a fourth stage), and a plain-English reason on every skip:
  *"model wasn't confident enough (0.418 vs 0.40 needed)"*.
- **`dashboard/lib/reconcile.ts`** — the single source for every count on
  the page. One function, one string, quoted verbatim by the ticker, the
  hero card and the day header. The three-numbers problem was three
  components each deriving its own count.
- **`dashboard/lib/season-record.ts`** — one definition of the record's
  shape, replacing inline interfaces that a schema change turns into a
  runtime crash rather than a type error.
- `selectedBets` / `droppedZeroStake` / `droppedFlatPnl` disclosure —
  PROJECTED stakes 190 of 225 qualifying bets; the 35 Kelly-zeroed ones
  used to vanish from the headline silently.

### Fixed

- **Doubleheader double-count** (`tools/export_season_record.py`,
  `tools/season_replay.py`). `(date, away, home)` is not a key: both legs
  of 2026-07-19 LAD@NYY and 2026-07-22 PIT@NYY rendered as the same bet
  twice and doubled their day totals. `load_season` now emits a stable
  `rid` (CSV row index) and the record joins on it. Season totals were
  unaffected — only the day view collapsed. Doubleheader legs now label as
  `LAD@NYY G2`.
- **"TONIGHT CLV +0.00pp" was never a measurement.** Two defects:
  `board-supabase.ts` coerced NULL to 0 via `num()` (Supabase is the
  production read path, so every `clvPct != null` guard in the codebase
  was dead), and separately the CSV genuinely stores `0.0000` for most
  placed rows because the price freezes on placement (T2.23) — opening and
  taken price are the same recorded number. A bet now counts as measured
  only when the picked side has both prices AND they differ; otherwise the
  card reads **"Not measurable"** with the reason, never a number.
- **Light-mode contrast below AA.** `--primary` #b05f28→#9a4f1c
  (4.51/4.08/3.70 → 5.80/5.25/4.76 on card/background/muted),
  `--secondary` #a4690f→#8a5407 (4.42/4.00/3.63 → 6.07/5.50/4.98),
  `--muted-foreground` #7c6b59→#6b5a48 (4.96/4.49/4.07 → 6.40/5.79/5.25),
  `--destructive` #a84a30→#9c4228 (5.52/5.00/4.53 → 6.32/5.71/5.18).
  Applied to BOTH light blocks — `.light` and the
  `prefers-color-scheme` copy — which had silently diverged.
- **Terminal green was still in the tree.** `GameDetails.module.css` fell
  back to `#2e8b57` (sea green) and `#c08a1d` because `--success` and
  `--warning` were never declared. Tokens added, fallbacks removed.
- **Zone card colour disagreed with its own number** — tone keyed off the
  placeholder-inflated `unitsPL` while the card printed `realPL`, so a
  zone could print a loss in the colour of a win.
- **Both watermarks removed.** The 56px rotated "PAPER" overlapped the
  −52.4pp figure. Deleted with the `z-index: 1` rule that was the only
  thing holding it behind the numbers — that rule alone would have put the
  word on top.
- Typography: monospace is now for figures only. Nine classes were
  monospace *prose*, which was most of "everything is monospace at nearly
  one size". Six sizes replace 43; the unused 24-class `.t-*` scale that
  nothing referenced is deleted.
- A null record side no longer hides the entire card (the guard required
  both sides truthy, failing silently).
- `season_record.json` is now written atomically — a cron tick could read
  it mid-write.

### Changed — the record is now read in Kelly units, not flat

Operator: *"i thought we are completely done with the flat units. all of
our dashboard must reflect the new kelley sizing. even going back to the
start of the season."* The system stakes by quarter-Kelly as of 2026-07-28,
so the record leads with it.

| | Kelly (headline) | flat 1u (reference) |
|---|---|---|
| REAL, 5/07 → 7/28, 125 bets | **+157.69u** (bank 100 → 257.69) | +11.33u |
| WHOLE SEASON, 4/01 → 7/28, 190 bets | **+879.64u** (bank 100 → 979.64) | +34.66u |
| no-hindsight floor (real) | +130.18u | +11.23u |
| no-hindsight floor (season) | +365.10u | +25.45u |

Flat stays on every column as one line — *"Same bets at flat 1u: +11.33u.
The gap is leverage, not edge."* — because the two answer different
questions and the difference is 14x.

New on each column, because an average hides what compounding asks for:
**typical bet 7.99u · biggest 20.82u** (real), **22.17u · 79.14u**
(whole season). Deepest drawdown 18.6% on both.

`replayText()` now quotes the Kelly figure too; it was still quoting flat
while the day footer led with Kelly, which put two different replay
numbers on one screen.

### Fixed — the date picker could not reach most of the season

`listAvailableDates` in `dashboard/lib/board-supabase.ts` capped its query
at 500 rows with the comment *"well above a full MLB season's slate
count"*. The cap counts **rows, not dates**, and there is one row per
game: at ~13 games a night, 500 rows reached back roughly 38 days. Every
older date then failed `available.includes(requestedIso)` and fell through
to `available[0]` — **serving tonight's board under the requested date,
silently**. Selecting 2026-04-15 just snapped back to tonight.

Now paginated with `.range()` rather than a bigger `.limit()`, because
PostgREST enforces its own 1000-row server-side max — the same cap that
silently truncated `pl_calc`. A requested-but-unavailable date now logs a
warning instead of substituting in silence. Verified 2026-04-01, 04-15,
05-20, 06-10 and 07-27 all serve their own slate.

Also: `DayReconcile` resolves a date against the REAL record first and
falls back to PROJECTED, so the 36 April dates that exist only in the
projected record are reachable instead of rendering empty.

### Fixed — the board's stake chips still read flat 1.00u

Operator: *"it literally doesnt render with kelly stakes... it still says
staked 1.00u."* The record card was converted to Kelly but the per-game
chips on the board were not.

The chip displayed `detail.unitsRisked`, which is the LEDGER's stake —
flat `1.00` on every row placed before Kelly went live. Browsing to April
therefore showed "staked 1.00u" on every game.

The chip now reads the quarter-Kelly stake the CURRENT model would place,
taken from the same `season_record.json` the day-reconcile table uses, so
the two surfaces cannot disagree. 2026-04-15 now renders
`STAKED 17.13u*` / `2.97u*` / `8.94u*` against the day table's identical
17.13 / 2.97 / 8.94.

Three details that matter:

- **The replay lookup runs BEFORE the STRONG guard.** The old gate and the
  current model disagree about which games qualify, so the model sometimes
  stakes a game the row labels PASS or LEAN — 2026-04-15 COL@HOU at 8.94u
  would have been hidden entirely.
- **Games the current model declines read `MODEL PASSES`**, not a stake and
  not a blank.
- **Tonight is unaffected**: an un-replayed slate falls through to the live
  figure, and a locked row's recorded stake IS the Kelly number.

`*` marks a price the replay assumed at −125 rather than captured.

`DashboardShell` now fetches `season_record.json` **once** and shares it
with both the board and the performance panel; each was about to fetch
~376 KB independently, which also let them drift.

### Changed — the dashboard now leads with THE SYSTEM, not the old ledger

Operator: *"you need to make it so that the 'placed' bets are actually the
kelley sized bets with the new model."* The panel had been leading with
the legacy flat-1u ledger and treating the current model as a footnote.
Inverted.

**`SystemCard` is now the headline** — current model, quarter-Kelly, for
whichever window the toggle is on, back to opening day. The old ledger is
one quiet line: *"Older ledger — what was actually bet under the previous
gate at a flat 1u stake … Superseded on 2026-07-28."*

Two corrections that came out of the operator catching a bad number:

**1. The headline is a PERCENTAGE, and units are rebased to the real
bankroll.** The replay compounds from 100u in April, so by late July its
bank is ~1200u and an ordinary losing week printed as **−188.50u** — a
figure that reads like a catastrophe against a real 100u bankroll where
the same week is **−15.67u**. Units without the bank they were staked
from are meaningless. Every unit figure on the card is now
`pct × startBank`, so it answers "what would this have cost me".

**2. NRFI is reported separately and never folded into the headline.**
`_LR_STRONG_NRFI_P` is 1.01 — the live system does not place NRFI. Three
of the seven "bets" in the last-7-days figure were NRFI, so the combined
number reported losses on bets that would never have been made. The card
now shows the YRFI record as the figure and NRFI as a tracked-not-bet
note.

Last 7 days went from a reported **−58.85u** to the correct
**−15.7% (−15.67u), 4 bets, 0-4 YRFI** — independently confirmed by
re-simulating those four bets from a 100u bank with `tracker.kelly_stake_units`
(−15.79u).

Every window, for the record:

| window | result | bets | record | hit |
|---|---|---|---|---|
| Last 7 days | −15.7% | 4 | 0-4 | 0.0% |
| Last 30 days | +17.5% | 34 | 21-13 | 61.8% |
| Season to date | +915.9% | 158 | 109-49 | 69.0% |

`TotalCard`, `MigrationNote` and `WindowReplayCard` deleted — superseded.

### Investigated — NRFI profitability: closed, negative

Operator asked to enable NRFI ("it's supposed to work good now") and then,
shown the 12-14 record, asked for a deep dive on what floors would make it
profitable. 20-agent workflow: 4 investigations, ~300 selection rules,
every candidate attacked by 3 independent skeptics. **Nothing survived —
12 of 12 refutations succeeded.** `_LR_STRONG_NRFI_P` stays 1.01; no
production behaviour changed.

**The wall.** On 1,122 settled 2026 games with a real captured DK NRFI
price: NRFI hit 48.0%, the price required 53.7%, blind flat betting
returns −10.6% (95% CI [−15.5%, −5.5%]). Of that 5.65pp gap, only 3.31pp
is vig. **Strip 100% of the vig and NRFI still returns −4.68%** — so
line-shopping cannot fix it.

**"What floors work best" — none.** Tightening lifts the hit rate 48%→58%,
but the required rate rises faster, 53.7%→58.5%. Best of hundreds of
cells: lambda ≤ 0.52 at 58.1% vs 58.5% needed (−0.2%, n=43).

Two structural findings:
- **The gate and the ceiling are the same knob.** `lambda_lr_total ==
  −ln(raw p_nrfi)` exactly, verified to 0.002 on 783 rows. There is no
  2-D grid to tune.
- **`_LR_LAMBDA_NRFI_CEILING` was dead code.** At gate 0.62 every
  qualifying game already sat inside the 0.52 ceiling, so the +5.44u the
  comment credited to it cannot be its doing. Comment corrected.

Most telling: under simulated pure noise, a grid search this size yields a
best cell averaging +15–20% ROI by luck. The real best cells were +3.6%
and +6.1% — **the search found less profit than chance manufactures.**

Methodology trap recorded: the 2024 backtest is unusable as an NRFI
validation split (a model fit on 2024 scores below chance on 2024, CV AUC
0.4897), so the mandated 3-split cannot be run for this question.

Full do-not-retread list in user memory `2026-07-28_nrfi_deep_dive`.
Analysis scripts preserved read-only in `tools/nrfi_deep_dive/` (44 files).

### Changed — dashboard recoloured and cut down; audit's display defects fixed

Operator: *"recolor the dashboard, remove redundant things from the
dashboard, simplify it more."* Plus the display half of the probability
audit, which had been queued behind this work to avoid edit collisions.

**Cut** (each surface's information survives, or is noted): ClvStat (CLV
is structurally unmeasurable — it only ever said so), LegacyLedgerLine
(it was the arithmetic sum of two zone cards 40px below it), the LEAN
zone cards and LeanBlock (paper money for a tier that is never wagered,
rendered in the same card shape as real money — the root of the
distrust), and the duplicated RecordColumn renderings (the model's record
was on screen four times, on four different populations). Panel is now:
**THE SYSTEM → OLDER LEDGER → WHY THE SYSTEM DID THAT → board.**

**Recoloured.** Surfaces untouched — this morning's desaturation stands
and the warmth still lives in the ink. What changed is the accent range,
so peach/rust/amber are distinguishable rather than three shades of one
orange, plus new `--side-nrfi` / `--side-yrfi` tokens 2.4x apart in
luminance. Borders lifted (dark 2.02 → 2.95 on muted; light 1.58 → 2.72).

**It also found a live accessibility failure I had missed:**
`--accent-cyan` in light mode was 3.96 / 4.32 / 3.51 against
background / card / muted — below AA on all three, across 21 call sites.
Now aliased to `--muted-foreground` at 5.79 / 6.33 / 5.13. My earlier
claim that "every contrast pair passes AA" was true only of dark mode.

### Fixed — the display defects from the probability audit

- **HistoryView showed +33.50u for a season the ROI panel showed as
  −1.03u.** Opposite signs, same bets. It summed the raw column including
  177 graded bets settled against a fabricated −110. Now uses the
  real-priced figure, mirroring RoiPanel. The footnote asserting "Actual
  P/L uses real DK odds when captured" — printed directly under the
  inflated column — is rewritten to say how many bets are excluded.
- **Zone cards mixed two populations on one line.** The units figure came
  from real-priced bets; the hit rate and edge on the next line came from
  all graded bets. STRONG NRFI literally rendered *"−11.29u · 59.4% hit ·
  +7.0pp"* where the −11.29u is 49 bets that went 22-27 (44.9%) and the
  59.4% folds in 47 placeholder-priced bets that went 35-12.
  `ZoneProvenance` now carries `realPricedWins/Losses`.
- **"vs break-even" was hardcoded at −110 (52.38%)** while the real-priced
  bets averaged an implied 56%. Every edge figure was overstated ~4
  points. Now computed from the prices actually paid, per zone; the −110
  constant survives only for LEAN, where the flat hypothetical is correct.
- **`num()` coerced missing values to 0**, so 349 April rows rendered
  *"0.00 projected first-inning runs"* badged green (strongest-NRFI tone)
  with the correct value in the next fallback, and 16 rows with a real
  price but no stored edge rendered a fabricated *"+0.0%"* — including
  "Skipped: edge +0.0%". Both now use `nullableNum`.
- **The board displayed and sorted by the legacy Poisson lambda** rather
  than the model's own `lambda_lr_total` (r=0.43, 36% pairwise rank
  inversion). Now prefers the model's.
- `Ticker` accepts the shared `night` object rather than recomputing.

### Deferred

- Doubleheader `game_pk` is not unique in `picks_2026.csv` (1563 rows,
  1543 distinct) and 2026-06-17 SF@ATL has both legs labelled game 1. No
  P&L impact today — neither row was bet — but the writer should be fixed.
  Spawned as a separate task.

---

## [2026-07-27] — Loss investigation: the leak is selectivity, not the calibrator

Operator asked why the system is losing. Investigation of all 526 graded
placed bets. Three diagnostic tools added; **no production behaviour
changed** — every candidate fix is a betting-policy change and is parked
pending operator sign-off.

### Findings

- **The season "+32.7u" is not real.** April captured a real DK price on
  only 6 of 176 placed bets (3%); the other 170 settled at `_calc_pnl`'s
  flat -110 fallback. April's 64.2% hit rate is genuine, but at the ~-131
  average the 6 captured rows actually show, the month is worth ~+23u, not
  +39u. Odds capture became reliable 2026-05-01 (94% May, 100% Jun/Jul).
  **Real-price record since 5/01: -6.40u over 350 bets** (May -3.65u,
  June -3.37u, July +0.62u). Overall 55.9% hit against a 56.1% break-even.

- **The STRONG gate is far too loose.** `_LR_PASS_LO_P = 0.44` admits
  **648 of 1520 graded 2026 games (42.6%)** as STRONG YRFI. A genuine
  strong edge cannot exist on 43% of a slate.

- **Two calibrator plateaus sit directly under the gate.**
  `data/calibration_v2.json` bins 1-3 all map to calibrated NRFI 0.40639
  (YRFI 0.5936) — 204 games, 98 distinct lambda values, one probability.
  A second plateau at 0.43836 holds 85 more. Together **289 of the 648
  qualifying games (45%) come from two "I can't tell these apart" values.**
  Plateau bets: 51.4% hit vs 54.9% market-implied, **-7.18u**. Non-plateau
  bets at the same average price: 57.0% hit, **+8.40u**.

- **Rebuilding the calibrator does NOT fix the P&L** (negative result,
  worth recording so it is not retried). A calibrator is a *monotone*
  relabelling, so it cannot change which games rank highest — only the
  number printed on them. Measured: AUC is 0.5346 for the raw model and
  0.5334-0.5352 for every candidate, and **at matched bet volume all six
  candidates select the same games for the same P&L** (+21.51u at 100
  bets, +21.53u at 150 bets, identical across the board). Out-of-sample
  Brier differences are ≤0.0005. The plateau is a real defect, but it
  costs money only because it shovels marginal games over a fixed gate —
  raising the gate on the existing calibrator achieves the same thing.

- **The calibrator fix does matter for Kelly**, which is the reason to
  keep it on the table: 204 games sharing one probability means
  bankroll-fraction staking would size them all identically off a number
  known to be wrong.

### Added

- **`tools/calibrator_bakeoff.py`** — out-of-sample comparison of six
  calibrators (iso20/iso40 = current family, cir20/cir40 = Centered
  Isotonic Regression, platt, blend) under the mandatory CLAUDE.md
  3-split rule (2024→2025, 2025→2024, 2024+2025→2026). Reports Brier,
  log loss, ECE, and plateau mass. `--money` adds a 2026 real-odds P&L
  test. CIR cuts plateau mass 68.2% → 12.5% at no Brier cost; Platt
  eliminates it entirely.
- **`tools/calibrator_shape_vs_selectivity.py`** — separates "ranks games
  better" (AUC, equal-volume P&L) from "bets less often". This is the
  tool that produced the negative result above; run it before proposing
  any future calibrator swap.
- **`tools/kelly_backtest.py`** — bankroll-fraction staking backtest on
  the real bet ledger, per operator request 2026-07-27 (overrides the
  earlier T4.25-27 flat-1u-only preference). Full/half/quarter/eighth
  Kelly against three probability sources (model-claimed, shrunk-to-
  market, market-implied control). Writes
  `data/diagnostics/kelly_backtest.json`.

### Kelly results (100u starting bankroll, 25% single-bet stake cap)

| selection | flat 1u | quarter K | half K | full K |
|---|---|---|---|---|
| current 349 bets | -3.65u | +4.25u | -40.40u | **-98.04u (bankroll 1.96, 99% DD)** |
| walk-forward gate, 109 bets | +6.49u | +32.45u | **+47.98u** | +16.66u (86% DD) |
| in-sample p≥0.64, 105 bets | +17.08u | +83.21u | +182.46u | +326.51u |

Full Kelly on today's selection **wipes out the bankroll.** Note half
Kelly beats full Kelly on the walk-forward set — the signature of staking
past the growth-optimal point on overstated probabilities. The p≥0.64 row
is in-sample (threshold chosen on the same data) and is an upper bound,
not a forecast; the walk-forward row is the honest number.

### Changed — SHIPPED (operator approved 2026-07-27)

- **`_LR_STRONG_YRFI_P = 0.36` — dedicated STRONG YRFI gate** (
  `mlb_first_inning_predictor.py`). STRONG YRFI now requires calibrated
  `p_nrfi < 0.36` (YRFI ≥ 0.64); the 0.36–0.44 band that previously fired
  STRONG is demoted to **LEAN YRFI (tracked, never bet)**. Deliberately a
  NEW constant rather than moving `_LR_PASS_LO_P`, so the PASS boundary
  and LEAN/PASS semantics are untouched and demoted games stay visible on
  the board instead of vanishing. Reversal: set to 0.44.
  - Mirrored in `dashboard/components/BoardRow.tsx` (`classifyTentative`
    + `DEFAULT_THRESHOLDS`) and `dashboard/lib/types.ts`; exported through
    `data/thresholds.json` as `strongYrfiP`. Field is optional in TS so
    older deploys fall back to the previous behaviour.
  - Verified by **`tools/verify_selectivity_gate.py`**, which replays every
    graded 2026 row through the real imported `classify_pick_lr` (not a
    reimplementation) and settles at real captured DK prices. Holding all
    other rules at current values and changing only the gate:

    | gate | bets | hit | need | P&L | ROI |
    |---|---|---|---|---|---|
    | old `< 0.44` | 285 | 57.2% | 56.0% | +4.82u | +1.7% |
    | new `< 0.36` | 86 | 69.8% | 58.6% | **+16.25u** | **+18.9%** |

    +11.43u on 199 fewer bets. Note this replay differs from the raw
    ledger figure quoted to the operator (349 bets / -2.20u) because the
    ledger spans several rulesets — `_LR_LAMBDA_YRFI_FLOOR` moved
    0.78 → 0.838 mid-season — whereas the replay applies today's rules
    throughout. Replay-vs-replay is the honest comparison.

### Feature investigation — three candidates tested, two rejected, none shipped

Deep-research sweep (109 agents, 26 sources, 25 claims adversarially
verified 3 votes each; **16 refuted vs 9 surviving**) followed by
3-split testing on this repo's own data. Net result: **no feature change
is justified.** No production model files were touched.

- **RETRACTED — the `top3c_iso` / `top3c_slg` collinearity is NOT a
  defect.** An earlier note in this investigation called the pair
  "fragile" because they carry near-equal opposite-signed weights at
  r≈0.93. That inference is empirically false here. Coefficient trace
  across the 3 training splits shows they are the **most stable
  coefficients in the model** (ISO relative sd **0.06**, SLG **0.16**,
  no sign flips). Dropping SLG makes it worse: ISO collapses toward
  zero, OBP **flips sign** (rel sd 1.15), and sign consistency among
  meaningful features falls **87% → 71%**. Accuracy flat either way
  (Brier delta ≤0.0005; AUC 0.5279 → 0.5244; better in only 2 of 3
  splits, failing the "must help in every direction" rule). The
  research recommended this drop on general methodological grounds; the
  3-split refuted it. **Left alone.**

- **`home_plate_ump_nrfi_rate` is dead weight, confirmed three ways.**
  New `tools/test_umpire_persistence.py` shows the feature's
  precondition fails: the stored 2022-23 shrunk rate correlates
  **r = -0.138** (Spearman -0.126, bootstrap 90% CI [-0.305, +0.042])
  with the same umpire's actual 2026 first-inning results, and the 2026
  umpire-to-umpire spread (sd 0.104) is **smaller than pure binomial
  noise** at those sample sizes (0.122) — no umpire signal exists in
  2026 at all. Both tails fully reverse (Will Little 0.421 → 0.579;
  Adam Beck 0.625 → 0.421). Compounding: `data/umpire_rates.json`
  `training_corpus` is the **2022 + 2023** backtests, the seasons
  CLAUDE.md bans for pitch-clock distribution shift; and the feature is
  mis-scaled live (train sd 0.0167 vs live sd 0.104, with B1 weight
  +0.0172). Ablation is **perfectly flat** — Brier identical to five
  decimals in all three splits.
  **Not shipped:** worth zero measured accuracy, and removing it
  requires regenerating `lr_t1.json`/`lr_b1.json` (`recalibrate_v2._load_one`
  hard-exits on a `feature_names` mismatch). Should ride along with the
  next scheduled `weekly_refit.py`, not an out-of-band retrain that
  perturbs every live prediction for no gain.

- **Rejected without testing, on research evidence:** re-encoding the
  umpire as Strike Zone Runs Saved (effect is only ~0.02 runs per
  half-inning, and SZRS splits credit four ways across catcher / umpire
  / pitcher / batter so it is not cleanly reimplementable); swapping
  xERA for Stuff+/Pitching+ (every head-to-head superiority claim was
  refuted, and Stuff+'s next-season ERA correlation collapses 0.41 →
  0.14 for pitchers who change teams, implying it partly encodes team
  and park). F-Strike% remains **untested** — every candidate effect
  size was refuted, so there is no citable number to justify the work.

- **Caveat recorded by the research itself:** no first-inning-specific
  evidence exists in any source found. Every effect size recovered is
  season-long and full-game. Per-half-inning figures are pro-rated
  arithmetic, not measurement.

### Added

- **`tools/test_umpire_persistence.py`** — tests whether a prior-season
  umpire rate predicts the same umpire's later results, with bootstrap
  CI and a binomial-noise floor comparison. Run before trusting any
  umpire-derived feature.
- **`tools/test_ablation_slg_ump.py`** — 3-split ablation harness.
  Masks columns out of the exact production feature matrices (rather
  than rebuilding them) so construction stays identical to production,
  trains on **per-half** targets as `two_stage_model.py` does, and
  reports coefficient sign stability as the primary endpoint.
- **`.claude/workflows/deep-research.js`** — the deep-research workflow
  harness, installed so it resolves by name in this repo. NOT committed:
  `.claude/` is gitignored, so this is a local-machine artifact only and
  will need reinstalling on another checkout.

### Changed — Kelly staking ENABLED at quarter Kelly (operator decision)

After reviewing the full-season backfill below, the operator enabled
quarter Kelly on a ~100-unit bankroll. `KELLY_ENABLED` now defaults to
**True**; `NRFI_KELLY_ENABLED=0` is the kill switch and takes effect on
the next cron tick with no code change.

- **Typical stakes go from 1u to roughly 4-7u.** At -135: yrfi_p 0.64 →
  3.85u, 0.66 → 5.03u, 0.68 → 6.20u, 0.70 → 7.37u, 0.75 → 10.00u (cap).
- **Bankroll epoch added (`KELLY_BANKROLL_EPOCH`, default 2026-07-28).**
  Caught before going live: `current_bankroll_units()` summed the WHOLE
  season's realized P&L (+32.7u) on top of the nominal bank, so the first
  Kelly bet would have sized off ~133u instead of 100u — **every stake
  33% too large**. Worse, that +32.7u is itself ~15u inflated by April's
  -110 fallback, so the error would have compounded on partly-fabricated
  profit. Only P&L from the epoch forward now compounds. Verified: the
  computed starting bankroll is exactly 100.00u.

### Added — Kelly bankroll-fraction staking

Built at operator request 2026-07-27, explicitly reversing the T4.25-27
flat-1u-only preference recorded in CLAUDE.md.

- **`tracker.kelly_fraction_of_bankroll` / `current_bankroll_units` /
  `kelly_stake_units`**, wired into `_apply_odds_to_row`'s sizing block.
  STRONG only; LEAN keeps its notional flat size. Requires BOTH a real
  captured DK price and the model's probability for the picked side —
  with either missing it returns `None` and the caller falls back to the
  flat stake. It never fabricates a stake from a missing price, which is
  the same failure mode as the -110 fallback that inflated April.
- **Config (all env vars):** `NRFI_KELLY_ENABLED` (default off),
  `NRFI_KELLY_FRACTION` (default **0.25**, quarter Kelly),
  `NRFI_KELLY_BANKROLL` (default 100 units, so 1u still reads as 1% of
  bank), `NRFI_KELLY_MAX_STAKE` (default 0.10 = 10% hard cap).
- **Bankroll compounds**: nominal bank + realized season P&L, read once
  and cached per process (this is called per-row inside odds-import
  loops; re-reading the ledger each time would be quadratic).
- **Stakes freeze on bet placement** automatically — the existing T2.23
  odds lock already covers `units_risked`.
- **`_calc_pnl` needed no change**; it already honours `units_risked`.

**Why quarter and not half.** Kelly stakes scale with *claimed* edge, and
this model's claimed edge is measurably inflated where it bets most
(claims 59.2% → won 50.3% on 157 bets; claims 62.3% → won 55.1% on 107).
Simulated on the real ledger at the shipped gate, 100u start:

| staking | final | profit | max DD | top stake |
|---|---|---|---|---|
| flat 1u | 114.15u | +14.15u | 4.2% | 1.0% |
| 1/8 Kelly | 130.52u | +30.52u | 13.3% | 4.6% |
| **1/4 Kelly (default)** | **163.20u** | **+63.20u** | **25.3%** | 9.3% |
| 1/2 Kelly | 218.01u | +118.01u | 35.2% | 10.0% (capped) |

On the PRE-gate selection full Kelly took 100u to **1.96u** (99% drawdown)
— Kelly is only survivable on top of the tightened gate shipped the same
day. Half Kelly also beat full Kelly on the walk-forward set, the
signature of staking past the growth-optimal point on inflated
probabilities.

**POLICY CONSEQUENCE, called out explicitly:** Kelly stakes 0 whenever
the model's probability does not beat the market's implied probability,
and a 0 stake sets `bet_placed="N"`. Enabling Kelly therefore
*implicitly adds an edge gate to STRONG* — the thing CLAUDE.md/T2.24 says
requires explicit operator permission. That is inherent to Kelly, not a
bug, but it means enabling it changes which bets fire, not just their
size. 4 of the bets in the simulated window were skipped this way.

- **`tools/verify_kelly_wiring.py`** — drives the SHIPPED
  `tracker.kelly_stake_units` (not a local copy of the formula) over the
  real ledger. Confirms default-off, exact formula agreement (largest
  disagreement 0.0000u over 349 bets), cap enforcement, 0 stake on -EV,
  and `None` fallback when no price was captured.

### Added — top-N-per-day analysis

- **`tools/top_n_per_day.py`**, answering "what if we only took the top
  pick every day?" over the 86 betting days with real captured prices:

| strategy | bets | hit | P&L | ROI |
|---|---|---|---|---|
| bet everything | 349 | 55.9% | -1.89u | -0.5% |
| top 1/day by confidence | 86 | 66.3% | +12.49u | +14.5% |
| top 1/day by **edge** | 86 | 62.8% | **+15.65u** | **+18.2%** |
| top 2/day by confidence | 167 | 64.7% | +22.61u | +13.5% |

  Ranking by EDGE (model p minus market implied) is the EV-correct
  criterion and the only variant positive in **every** full month
  (May +4.37u, Jun +5.04u, Jul +6.47u); confidence-ranking went flat in
  June. Edge-ranking also gets a far better average price (-59 vs -136).
  Longest losing streak 4 bets (edge) / 3 bets (confidence).
  Both are hindsight over a partial season — a one-per-day strategy
  concentrates all variance into very few bets.

### Fixed — Kelly same-day exposure was uncapped (found post-ship, same day)

The 10% per-bet cap constrained each stake but nothing constrained the
DAY. Kelly's formula sizes one bet against one outcome assuming the
bankroll compounds before the next; same-slate bets are placed together
and settle together, so each is really risked against the same bankroll
at the same time. Sizing N same-day bets each at the full fraction
over-commits.

Measured at the shipped gate over 55 betting days (100u, quarter Kelly):
worst day put **24.51u at risk across 4 bets**, three days exceeded 20u,
seven exceeded 15u — while the per-bet cap never once bound.

- Adds **`KELLY_MAX_DAILY_FRAC`** (`NRFI_KELLY_MAX_DAILY`, default 0.15)
  and `_committed_on()`, seeded per date from the ledger so a fresh cron
  process sees what earlier ticks already committed.
- Result: worst-day exposure **24.5% → 13.0%**, and the backfilled final
  bank *improved* 157.79u → 177.93u, because the trimming lands on heavy
  slates that included losers.
- **Known limitation, documented in-code:** the daily budget is allocated
  first-come-first-served in row order, not best-bet-first. On a day that
  exhausts the budget, later picks are trimmed or skipped even if
  stronger. The cap binds on 7 of 55 days at a 1.2-bet average slate, so
  impact is small; revisit if the slate widens.

### Found, not yet fixed — three open items

- **The model has not been refit in 62 days.** `lr_t1.json` /
  `calibration_v2.json` last changed 2026-05-26. `daily.yml:64` records
  that the weekly auto-recalibrate cron was **disabled on 2026-05-11**;
  `recalibrate` survives only as a manual `workflow_dispatch` option. The
  memory note describing a live "weekly_refit.py workflow" is stale. A
  62-day-old calibrator is being served against a drifting run
  environment — and it is the same calibrator whose flat step this
  investigation identified.
- **CLV is not measurable.** Of 531 placed bets, 307 have
  `opened_*_odds` exactly equal to `market_*_odds` and only 20 show any
  movement (204 missing one side). So the 1am opener-capture cron is not
  producing usable open→bet line movement, and every CLV number the
  system reports is meaningless rather than merely small.
- **Single sportsbook.** Every row is DraftKings. No line shopping at
  all. Sensitivity on the 105 bets at the new gate: a 10-cent better
  average price is worth **+3.83u** (ROI 13.47% → 17.12%); 20 cents is
  worth +8.24u. Unlike any model change this is a *certain* gain, and it
  is plausibly the largest single improvement still available.

### Investigated — re-derived every gate and limit under Kelly staking

Operator hypothesis (2026-07-28): every threshold this system ever chose
was evaluated under FLAT 1u, where a marginal bet costs a whole unit when
it loses. Kelly changes that — a bet whose model probability doesn't beat
the market gets staked ZERO, so a band that bleeds at flat 1u might be
neutral-or-better under Kelly, implying the correct gate is now *lower*.

New `tools/kelly_gate_sweep.py` sweeps gate x Kelly fraction x daily cap
x per-bet cap x min-edge, with per-month consistency, block-bootstrap CIs
and a true walk-forward.

**The hypothesis is right for exactly one band, and instructively wrong
elsewhere:**

| band | flat 1u | Kelly funded | Kelly P&L | verdict |
|---|---|---|---|---|
| 0.56-0.60 | -14.27u | 137/155 | **-36.91u** | Kelly makes it WORSE |
| 0.60-0.64 | -3.95u | 76/82 | **+5.23u** | **rescued by Kelly** |
| 0.64-0.68 | +14.59u | 87/91 | +81.30u | improved |
| 0.68+ | -1.21u | 13/13 | -8.64u | worse (n=13) |

**Why 0.56-0.60 gets worse is the important part.** Kelly can only filter
on *claimed* edge. That band is exactly where the calibrator's 0.5936 flat
step lives, so the model claims an edge it does not have — and Kelly
responds by funding 88% of those bets and sizing them UP. Kelly's
self-filtering is only as honest as the probability feeding it.

**Nothing should change.** Gate 0.64 remains best on every criterion:

| gate | flat | 1/8 K | 1/4 K | maxDD | months + | bootstrap 90% CI |
|---|---|---|---|---|---|---|
| 0.56 | -4.84u | +0.86u | +48.63u | 35.9% | 2/3 | [-41.08, +317.16] zero |
| 0.60 | +9.42u | +25.67u | +84.92u | 28.6% | 3/3 | [-13.28, +306.26] zero |
| **0.64 (live)** | +13.38u | +28.28u | **+90.18u** | **15.2%** | **3/3** | **[+5.76, +247.33]** |
| 0.66 | +11.78u | +26.15u | +69.88u | 23.7% | 3/3 | — |

0.64 is the **only** configuration whose bootstrap CI excludes zero, and
it has both the lowest drawdown and the least month-concentration (best
month 53% of profit, vs 118% at gate 0.56 where June was negative).

- **Daily cap 15% is near-optimal**, confirming the value shipped hours
  earlier: at gate 0.64, 10% → +85.99u, **15% → +90.18u**, 25% → +57.79u,
  uncapped → +57.79u. At the loose 0.56 gate the cap does even more work
  (10% → +126.95u vs uncapped -10.89u).
- **The per-bet cap is redundant** — 10% and 25% give identical results
  because the daily cap binds first. Left as-is; no reason to touch it.
- **A min-edge filter adds nothing** (+90.18u → +90.49u at edge>=2%).
  Kelly already zeroes non-positive-EV bets, so an explicit floor is
  duplicated work.
- **Walk-forward, the honest number** (gate re-chosen daily from prior
  settled bets only): flat 1u **+6.15u**, 1/8 Kelly **+14.13u**,
  1/4 Kelly **+44.60u**. Quarter Kelly is ~7x flat even with no hindsight.

**Consequence — the calibrator rebuild is now worth doing.** Under flat
staking it was proven worthless (all monotone calibrators select the same
bets at equal volume, 2026-07-27). Under Kelly the calibrator's OUTPUT
VALUE is the `p` in the Kelly formula and therefore sets stake size
directly, so the 0.5936 plateau now makes Kelly stake 204 different games
off one wrong number. CIR cuts plateau mass 68% → 12% at zero Brier cost.
This reverses the earlier "not worth shipping" verdict.

### Changed — SHIPPED: calibrator replaced with Centered Isotonic Regression

Reverses the 2026-07-27 "not worth shipping" verdict, for a reason that
did not exist then. Under FLAT staking a calibrator is a monotone
relabelling, so shape provably cannot change which bets fire at equal
volume. Under Kelly the calibrated probability IS the `p` in the Kelly
formula and therefore sets STAKE SIZE directly. The live curve's flat
step gave 204 graded 2026 games (98 distinct lambdas) the single value
NRFI 0.40639 / YRFI 0.5936, and Kelly staked all of them off that one
number — in exactly the 0.56-0.60 band the gate sweep measured going
from -14.27u flat to -36.91u under Kelly.

**This is a DATA swap, not a code change.** CIR emits the same
`{centers, rates}` pair list, so `ProbCalibrator.load()` reads it
unchanged. Rollback is `git checkout <prev> -- data/calibration_v2.json`.

- **`tools/fit_cir_calibrator.py`** — fits CIR, validates it, and refuses
  `--write` unless all three criteria pass.

**Validation (CLAUDE.md 3-split):**

| split | iso20 Brier | CIR Brier | Δ | iso plateau | CIR plateau |
|---|---|---|---|---|---|
| 2024→2025 | 0.25029 | 0.25029 | −0.00000 | 87.2% | 24.4% |
| 2025→2024 | 0.25816 | 0.25872 | +0.00057 | 57.7% | 4.7% |
| 2024+2025→2026 | 0.24799 | 0.24786 | −0.00014 | 59.6% | 8.5% |

Degraded Brier in 1 of 3 splits — within the rule's allowance of 1.

**Live curve, before → after:**
- knots 20 → **11**, longest flat run 3 → **1** (i.e. no flats at all),
  knots inside flat runs 17 → **0**
- on the 1520 graded 2026 games: distinct probabilities 689 → **1437**,
  plateau mass 51.7% → **4.1%**, games on the 0.5936 step **204 → 0**
- mean |probability change| 0.0231, max 0.0477; games clearing the STRONG
  gate 162 → 137
- the old dead zone now ramps: raw 0.38/0.40/0.42/0.44 → p_nrfi
  0.4166/0.4292/0.4423/0.4664 (previously all 0.4064)

**Kelly money test** (both arms trained on 2025+2026, so both are
optimistic — the *delta* is the signal): live +209.49u / 13.1% maxDD vs
CIR **+322.19u** / 11.7% maxDD, **+112.70u** better on 15 fewer bets at a
higher hit rate (67.6% → 70.1%).

### Fixed — T4.6 calibrator shape validator had never run

`_validate_calibrator_shape` read `cal._xs` / `cal._ys`, but
`ProbCalibrator` has only ever stored `centers` / `rates`. The getattr
chain resolved to `None` and the function returned early on every call
since T4.6 shipped — the safety check was dead code the whole time.
Fixed to read the real attribute names (fallbacks retained), and the
warning threshold raised 5pp → 15pp because CIR deliberately collapses
each pooled run to one knot, so larger inter-knot steps are intended
rather than overfitting (the live curve's largest legitimate step is
~12.8pp). Verified: silent on the real curve, warns on a broken one.

### Added — line-shopping infrastructure (highest-value remaining change)

Every bet in the ledger has been DraftKings; there has never been any
line shopping. On the 105 bets clearing the shipped gate (avg price
-142): 5 cents better = +1.84u, **10 cents = +3.83u** (ROI 13.47% ->
17.12%), 20 cents = +8.24u. Unlike every model change tested on
2026-07-27/28 this is a *certain* gain, and it compounds with Kelly
because a better price raises the Kelly fraction as well as the payout.

- **`tools/merge_odds_books.py`** — takes N per-book odds CSVs and emits
  one best-price CSV for `--import-odds`. Best price is chosen **per
  side**, since we only ever bet one side, so NRFI and YRFI may come from
  different books; the merged row records which book won each. Games
  quoted by only one book still survive the merge.
  - Comparison is done on **payout**, not on the American number. Naive
    numeric comparison is wrong across the +/- boundary and silently
    picks the worse price: it prefers -110 over -105, and would take
    -150 over +100. `--self-test` covers exactly these cases.
- **`tools/fetch_odds_api.py`** — multi-book source via The Odds API.
  NRFI/YRFI is not a named market anywhere; it is the first-inning total
  at a 0.5 line (`totals_1st_1_innings`, Under = NRFI, Over = YRFI). The
  parser ignores any 1.5-line outcome, which would otherwise silently
  price a different bet.

**Why an aggregator instead of more scrapers.** `scrape_dk_odds.py` talks
to DraftKings' undocumented internal API; its own docstring notes DK
changes that URL about once a year, and 2026-05-03 showed their CDN
fingerprinting our egress into read timeouts. One such scraper per book
multiplies that fragility. (Confirmed while building this: DK's endpoint
returns HTTP 403 from this environment entirely.)

**CALL BUDGET — read before scheduling.** `totals_1st_1_innings` is an
"additional market", served only from the per-event endpoint, so one
fetch costs 1 call to list events + 1 per event ≈ **16 credits on a
15-game slate**. The free tier is 500/month, i.e. roughly **one fetch per
day**. Wiring this into the ~12x-daily predict cron would exhaust the
quota in about two days. Run it once near lock time, or buy a tier.
`--dry-run` reports the cost without spending credits, and the tool
refuses to start if remaining credits are below the number needed.

**Not yet wired into the cron, and deliberately so.** Two preconditions
are outside this repo: (1) an `ODDS_API_KEY`, and (2) the operator
actually holding accounts at the books that win the price — otherwise the
output is a diagnostic ("DK was 12 cents off best"), not an instruction.

**Testing status, stated plainly.** `merge_odds_books.py` is fully tested
including the +/- boundary. `fetch_odds_api.py` is written against the
documented schema and self-tested on a synthetic payload, but has **never
been run against the live API** — there is no key in this environment.
Treat the first live run as verification: check the row count against the
slate before trusting any price.

### Added — dashboard shows the counterfactual Kelly bankroll

Operator asked for the dashboard record to reflect "what it would be if
we'd started Kelly from the beginning".

**The ledger is NOT rewritten.** The obvious implementation — overwriting
`units_risked` / `profit_loss_units` with simulated stakes — would destroy
the only record of what was actually risked at a real price and make the
simulation permanently unauditable. That is the 2026-05-05 backfill-mirror
failure. Instead the counterfactual is recomputed **on read** from each
row's stored probability and captured price.

- **`dashboard/lib/kelly-sim.ts`** — day-by-day compounding replay.
  Mirrors `tracker.kelly_stake_units` including the per-bet and same-day
  exposure caps.
- **Config comes from `data/thresholds.json`**, which the predictor now
  exports from tracker.py's own constants (`kellyFraction`,
  `kellyBankrollUnits`, `kellyMaxStakeFrac`, `kellyMaxDailyFrac`,
  `kellyMinStakeUnits`, `kellyEpoch`). Re-deriving Kelly's parameters in
  TypeScript would drift the moment either side was tuned.
- **`KellyCard` in RoiPanel** — dashed border + "SIM" watermark, matching
  the LEAN paper-trade card so a simulated bankroll can't be mistaken for
  realized P&L. Hidden on the TODAY tab (a compounding season figure
  there invites reading +95u as tonight's result); shown on 7d/30d/season.

**Result: 100u → 195.51u (+95.51u)**, 165W-122L over 287 staked bets
since 2026-04-29, max drawdown 33.8%, largest single stake 19.20u (10% of
a bankroll that had grown to ~192u). The same bets flat-1u: **-0.69u**.

**Why this is far better than the -10.89u in the earlier season backfill:**
that backfill ran *before* `KELLY_MAX_DAILY_FRAC` existed, so it had no
same-day exposure cap. The gate sweep independently measured the same
effect (uncapped -10.89u vs +48.63u with a 15% daily cap). The daily cap
is doing a large share of the work, not the Kelly formula alone.

**Verification:** the TypeScript simulation was cross-checked against an
independent Python reimplementation on the same CSV — final bankroll,
profit, bet count, W/L, max drawdown and largest stake all agree exactly.

**Caveats stated on the card and worth repeating.** 11 bets are marked
unsizeable (no captured price — Kelly's stake is a function of the price,
so inventing one recreates the April artefact). The simulation uses the
probabilities stored at pick time, which came from the OLD plateaued
calibrator; live Kelly now runs on the CIR curve, so future results are
not drawn from the same distribution as this backfill.

### Found — picks_2026.csv and Supabase disagree about April `bet_placed`

While validating the above: the committed CSV has **10** April rows
flagged `bet_placed=Y`, while the Supabase snapshot has **176**. The
dashboard reads the CSV, `tools/pl_calc.py` prefers Supabase. That is why
the Kelly curve starts 2026-04-29 rather than at opening day, and it is a
pre-existing divergence this change did not cause. Not fixed here —
reconciling them changes what every historical total reports, which needs
its own decision. `tools/diff_csv_vs_supabase.py` exists for this.

### Investigated — the "62-day-stale model" is not costing anything; refit REJECTED

Ran `tools/weekly_refit.py` to retrain the two-stage LR on
2024 + 2025 + 2026-thru-07-20, holding out 2026-07-21..27 (93 games).

**It shipped, and it should not have.** Its gate was "P&L >= prod - 1.0u
AND Brier <= prod + 0.005" — both asymmetric and generous, so a candidate
that is measurably WORSE on both still passes. This run was exactly that:
delta P&L **+0.00u**, delta Brier **+0.0037 (worse)** — and it shipped.
That is the weakness which got the weekly cron disabled on 2026-05-11.

**Independent review (`tools/verify_refit.py`, new) said no:**
- Clean holdout Brier: previous 0.26070, new 0.26438 — new is worse, but
  the bootstrap 90% CI on the delta is **[-0.00104, +0.00851]**, i.e.
  indistinguishable from noise on 93 games.
- It churns the book: STRONG YRFI picks 92 → 103, with **31 added and 20
  dropped** — ~51 of ~100 picks change. Mean probability move 0.0199
  (max 0.1099), which under Kelly changes stake sizes everywhere.
- Kelly money, **in-sample for the new model** so it should be flattered:
  previous +300.00u / 11.7% maxDD vs new **+78.25u / 30.2% maxDD**. It
  gives back most of the bankroll and nearly triples the drawdown on data
  it was trained on.

**Rolled back.** Production is the 5/26 LR weights + this morning's CIR
calibrator. Conclusion: the model's age is not currently costing measurable
accuracy, and a refit today would trade a known-good model for a
different one with no evidence behind it.

### Fixed — refit gate now defaults to the incumbent

`tools/weekly_refit.py` decision gate rewritten. A refit perturbs every
live prediction and (since 2026-07-27) every Kelly stake, so it must earn
its place rather than merely fail to embarrass itself:

- Brier must **improve** — no "within tolerance" pass.
- The improvement must survive a **block bootstrap** on the holdout
  (entire 90% CI below zero). A ~90-game window moves ~0.005 on noise.
- P&L must not regress **at all**.
- New `MIN_HOLDOUT_GAMES = 90` — below that the run declines to decide
  rather than deciding on noise.

Re-ran against the tightened gate: **VALIDATION FAILED, production
unchanged** — the correct outcome.

### Fixed — the refit path would have silently reverted the CIR calibrator

`tools/walk_forward_eval.fit_calibrator` called
`ProbCalibrator.fit(..., n_bins=20)` (plain PAV). Since `weekly_refit.py`
overwrites `data/calibration_v2.json` on a successful refit, the first
successful refit would have **silently restored a plateaued curve** and
undone the CIR ship — reintroducing the flat step that Kelly now sizes
stakes off. Now uses `CIRCalibrator`.

- **`CIRCalibrator` promoted from `tools/calibrator_bakeoff.py` into
  `calibration.py`**, its canonical home, so every fit path shares one
  definition instead of the bake-off owning a copy the refit path didn't
  know about.

### Corrected — the NRFI check was reading a 6-week-stale sample

Operator caught this. The first `nrfi_reenable_check.py` filtered on
`pick_strength == "STRONG"` and judged re-enabling on 49 picks ending
2026-06-14. But disabling NRFI set `_LR_STRONG_NRFI_P = 1.01`, which no
probability can exceed — so from that date the classifier stopped emitting
"STRONG NRFI" entirely and the same games came out as **LEAN NRFI**. The
strength LABEL changed meaning; the probability did not. The filter
therefore discarded **158 graded predictions (157 with real prices)** —
every NRFI call the model made while we sat out.

Fixed to select on side + probability. Sample goes 49 → **309**, of which
**184 are genuinely out-of-sample** (we bet none of them, so none of our
money touched those lines).

**The conclusion is unchanged and now much better supported:**

| segment | n | hit | needs | flat 1u | Kelly |
|---|---|---|---|---|---|
| all NRFI predictions | 309 | 46.3% | 55.9% | **-53.89u** | -46.65u |
| ...actually bet | 49 | 44.9% | 57.9% | -11.29u | -30.57u |
| ...predicted, never bet | 260 | 46.5% | 55.6% | **-42.60u** | -23.14u |
| **since 6/07 (clean OOS)** | **184** | **46.7%** | **55.1%** | **-28.67u** | -33.15u |

Still negative in every probability band (-9.5pp / -8.4pp / -11.2pp /
-11.4pp) and at every re-enable threshold from 0.55 to 0.66. The six weeks
we sat out confirm it rather than overturning it.

### Investigated — NRFI stays OFF, and CLV is unmeasurable for structural reasons

**NRFI: do not re-enable.** The 2026-06-07 decision holds, now re-tested
under Kelly + the tightened gate + the CIR calibrator (the three things
that changed since). `tools/nrfi_reenable_check.py`, on all 49 graded
STRONG NRFI picks with a real captured DK price:

- **44.9% hit against a 57.9% break-even — a -13pp edge.** Flat 1u
  **-11.29u**; under quarter Kelly **-30.57u** at 37.5% drawdown.
- **Every probability band is negative**: 0.50-0.60 **-17.2pp**,
  0.60-0.62 -13.6pp, 0.62-0.65 -3.7pp, 0.65+ **-17.2pp**. There is no
  sub-range where the model beats the NRFI price.
- **Every re-enable threshold loses**, flat and Kelly, from 0.55 through
  0.66. The least-bad (0.64) is still -1.03u flat / -14.08u Kelly.
- Kelly makes NRFI *worse* at every threshold — same mechanism as the
  0.56-0.60 YRFI band: the model claims an edge it does not have, so
  Kelly funds and enlarges it.

**Resolves a dashboard discrepancy:** the ROI panel shows STRONG NRFI at
59.4% / +8.53u, but only 49 of those 96 picks have a real captured price
and those went **44.9%**. The other 47 settled at the -110 placeholder
and are mostly April — the same artefact that inflated the season total.
**The dashboard's STRONG NRFI line is not a real result.**

**CLV: the instrument works; the market doesn't move.** Earlier this
investigation flagged "307 of 531 rows have opened == market" as broken
capture. Measuring properly: 83% of placed rows *do* carry two distinct
observations, but the **median gap between them is 0.1 hours** (mean 0.2h,
max 4.4h), and the first capture lands a median **~1.0h before first
pitch**. DraftKings does not post this niche first-inning market until
shortly before the game, and the T2.58 lock commits the bet minutes later.

So there is no window between market-open and our entry in which a line
could move — 93% show an identical price across the gap, and when it does
move the median is 5 cents. **CLV is not mis-instrumented; it is
structurally unavailable for this market at our bet timing.** No code fix
would produce a number, because there is no second observation to make.
Recorded rather than "fixed": the honest action is to stop treating CLV
as a validation signal here, not to manufacture one.

### Investigated — is the NRFI signal INVERTED? Suggestive, not proven

Operator: "I think we may be predicting it wrong, or targeting the wrong
probabilities or brackets." Tested on all 309 NRFI-side predictions with
both prices captured.

**The model's NRFI zone is genuinely mis-ordered.** Actual YRFI rate by
what the model said:

| model says | actual YRFI rate |
|---|---|
| YRFI | 56.7% |
| **NRFI** | **53.7%** |
| PASS | 47.8% |

Games the model calls NRFI are **more** YRFI-prone than the ones it calls
PASS. The ranking is inverted between those two zones — the PASS bucket
is a better NRFI detector than the NRFI bucket is. That is a real defect,
and it is the concrete form of "we may be predicting it wrong".

**Fading it looks profitable but does not clear the bar.** Betting YRFI on
games the model calls NRFI: 53.7% vs 50.6% needed, **+3.1pp, +18.25u,
+5.91% ROI** over 309 games.

Control test rules out a market artefact: blind-betting YRFI on **every**
graded game LOSES (51.9% vs 52.9% needed, -1.0pp, -19.17u). So YRFI is not
generically underpriced; the model is carrying real information and
partially inverting it.

But it fails validation:
- full-sample bootstrap 90% CI on ROI **[-3.43%, +15.14%]** — includes zero
- clean out-of-sample (184 picks since 6/07, none ever bet): +3.12% ROI,
  CI **[-8.75%, +14.47%]** — includes zero
- **July was negative (-4.05u)**; positive in 3 of 4 months
- most of the profit sits in the 0.50-0.55 band (+17.25u of +18.25u),
  i.e. where the model is barely leaning at all

For comparison, the STRONG-gate change that shipped had a CI of
[+1.8%, +28.4%], excluding zero. The fade does not meet that standard.
**Do not act on it.** Recorded as a watch item.

**Also tested and rejected:** betting NRFI on the PASS zone (-1.9pp,
-15.17u, CI [-9.8%, +4.1%] spans zero).

**The durable finding is the mis-ordering, not the fade.** The right fix
is in the model — the NRFI side of the classifier is not separating
low-scoring games from the PASS zone — not a new bet type layered on top.

### Added — PROJECTED PROFIT / REAL PROFIT: the system record, on the dashboard

Operator spec (2026-07-28), now live end to end:

- **PROJECTED PROFIT** — whole season, every game the current system
  would bet, missing DraftKings prices filled at an explicit **-125**:
  **94W-45L (67.6%), edge +10.3pp, flat +25.45u, bank 100u → 465.10u,
  maxDD 18.1%** (37 of 139 bets priced by assumption, and the card says
  so).
- **REAL PROFIT** — **2026-05-07 onward** (first day DK capture became
  reliable: 99.6% of games priced from there), real captured prices
  only, nothing assumed: **61W-33L (64.9%), edge +6.9pp, flat +11.23u,
  bank 100u → 230.18u, maxDD 18.0%**.

Both include **YRFI (live 0.40 gate) and NRFI (p≥0.60)** per the
operator's decision. Live NRFI *betting* remains disabled — only 9
real-priced NRFI bets exist — but the displayed record counts both
sides. Method is walk-forward: the calibrator at each date is refit
from strictly earlier games, so no game is scored by a curve that saw
its outcome.

- Each side of the card has a **day-by-day drill-down**: every betting
  day, expandable to the individual bets — game, side, price (marked
  `est.` when assumed), stake, WIN/LOSS, P&L, and bank after the day.
- Pipeline: `tools/export_season_record.py` (rewritten) →
  `data/season_record.json` → `copy-data.mjs` → `/api/season-record` →
  `SeasonRecordCard` in RoiPanel, rendered under every window.
- Wired into the nightly grade tick in `daily.yml`, so the record
  refreshes itself after each day's grading; the existing
  `git add data/` commit step picks it up.
- `gate_validation.select()` now carries game/side/assumed on each bet
  (additive keys) so the drill-down can name the bets.

### Fixed — the session-long "changes don't render" mystery, explained

The dev tab's console finally surfaced it:
`Text content did not match. Server: "Net P&L · real prices only"
Client: "Net P&L · bet zones only"` — the server rendered NEW code while
the browser hydrated a STALE cached client chunk. Next dev serves chunks
at unhashed URLs, and the embedded browser cached them across reloads,
cache-busting query params, and dev-server restarts. Every false
"it doesn't render" in this session — including the stake-chip hunt —
traces to this. Production builds use content-hashed chunk filenames and
are immune; the card was verified against `npm run build` + `npm run
start` (a `nrfi-dashboard-prod` launch config now exists for exactly
this). **Verify dashboard changes against a prod build, not the dev
server, from now on.**

And with a STRONG pick appearing on tonight's slate during verification,
the stake chip rendered on its own: `PENDING · LOCKS 6:40 PM ET · DK Y
-110 · STAKE UP TO 4.4u`. The chip was never broken — every earlier test
ran against slates with zero STRONG rows.

### Changed — record card: profit headline + date-picker day view

Operator feedback on first contact with the card:
- **Headline is now the PROFIT** (+365.10u / +130.18u — every unit above
  the 100u start), with the bankroll (100u → 465.10u / 230.18u) moved to
  the sub-line. Previously the big number was the final bank.
- **The slate date picker now drives a selected-day strip** in both
  record columns: pick any date and each column shows that day's SYSTEM
  bets — game, side, price, Kelly stake, result, P&L, bank after — or
  says "no system bets this day". Previously day filtering only touched
  the ledger cards below, which show the flat-1u era and made it look
  like the record was "stuck on flat 1 unit".
- Bet-count question answered with the same-window head-to-head: old
  system 5/07-onward, real prices: **314 bets, 55.4% vs 56.2% needed,
  -5.34u**. New record, same window: **94 bets, 64.9% vs 58.0% needed,
  +11.23u**. The ~220 missing bets are the 0.40-0.44 band (117 games),
  LEAN NRFI, and low-lambda games — the volume that was losing.

### Changed — the redesign, actually shipped

Operator: "I don't even think we redesigned the dashboard like we
planned." Correct. Now done, per the approved shape brief + CLAUDE.md:

- **Warm brown/peach palette, terminal green killed.** Token-level
  re-tint of globals.css (both themes), DashboardShell glow, favicon:
  espresso surfaces (#171009/#20160e), cream ink (#f0e4d3), **peach
  primary #f5a465** (was phosphor #5dff9a), rust YRFI #e06a48, amber
  PASS #e9b45b. Every component inherits via the existing variables.
- **Section order now answers "what do I bet tonight" first**: hero →
  health banners (render only when wrong) → RoiPanel (System Record
  first) → date picker + board → slate distribution → experiment
  plumbing → footer. SummaryStrip no longer sits between the hero and
  the money numbers.
- **The ledger can no longer impersonate the record.** The old
  "Net P&L · real prices only" card is relabelled **"Ledger · bets
  actually placed · flat 1u before 7/28"** — it aggregates what was
  historically wagered, which legitimately disagrees with the System
  Record on past days (7/25: ledger went 0-4; the system record sat the
  day out). The disagreement was the "units per day are wrong" report.
- **Stake-scaling footnote** on the record card: the same bet sizes
  differently in the two columns because stakes are a % of each
  record's own compounding bankroll (7/27 TOR@WSH: 11.15u projected vs
  5.52u real). Without the note that reads as a math error.

Verified against a production build: --primary computes to #f5a465,
record card above the relabelled ledger, footnote rendered.

### Fixed — full-code audit: 4 money bugs + 4 wrong-number bugs (16-agent review)

An ultracode audit workflow (6 lens-specific reviewers, every P0/P1
finding adversarially verified against the code) confirmed 8 of 35 raw
findings. All 8 fixed:

**P0 · Kelly daily cap double-counted on every odds re-import**
(`tracker.py`). `_committed_on` seeded from ALL STRONG rows' stakes —
including the pre-lock rows the batch was about to re-size — and each
re-size ADDED the fresh stake without releasing the old one, so
committed exposure ran ~2x truth. With Railway re-importing every 5
minutes, stakes on any normal day oscillated full → trimmed → zero
across ticks, and whatever value existed when the lock window flipped
the row froze forever. Every offline replay tool already worked around
this with a manual reset; production had none. Fix: seed only from
locked (`bet_placed=Y`) rows + new `kelly_reset_daily_committed()`
called at the top of every import batch (also refreshes the bankroll
cache, which never expired in a long-lived process). **Regression: 3
consecutive simulated import batches now produce identical stakes;
verify_kelly_wiring all-pass.**

**P0 · end_of_day heal fabricated bets** (`tools/end_of_day_check.py`).
The orphan-heal predicate skipped only `bet_placed=Y`, so deliberate
`N` rows — Kelly's zero-stake edge gate, daily-cap-zeroed picks,
pre-lock pendings — got retroactively stamped `Y` at flat 1.00u,
booking P&L for bets never made and (via the compounding bankroll)
mis-sizing every later stake. Now heals only truly-blank rows and
preserves any recorded Kelly stake instead of flattening it to 1.00.

**P0 · StakeChip sized from a static bankroll** (`BoardRow.tsx`). The
chip used the nominal 100u while the tracker stakes from the compounded
bank — in a drawdown the chip overstates the real stake. The predictor
now exports `kellyCurrentBankrollUnits` each tick and the chip uses it;
once a bet locks, the chip displays the ledger's frozen `unitsRisked`
verbatim ("staked N.NNu") instead of recomputing.

**P0 · hero card hard-coded 1u per bet** (`TonightsActionCard.tsx`).
"X.Xu staked" summed a constant 1 while quarter-Kelly stakes 4-10u —
understating tonight's real exposure severalfold. Now sums the ledger's
`unitsRisked`; rows without a recorded stake contribute 0, never a guess.

**P1 · TotalCard caption/tone described a different bet set than its
number** — headline was real-priced P&L, caption counted all graded bets
and the card colour keyed off the placeholder-inflated sum. All three
now follow the priced subset, with the record labelled "counts all
graded" when they differ.

**P1 · record's method string overclaimed walk-forward.** Only the
calibrator is walk-forward; the LR weights are the fixed 2026-05-26
refit (trained through 5/11), so bets ≤5/11 are partially in-sample at
the weight layer. The JSON now says exactly that.

**P1 · record silently dropped Kelly-zeroed bets from its W-L.**
Projected: 47 of 186 qualifying bets (+3.97u flat) got zero stake and
vanished from the headline. Now disclosed in the JSON
(`selectedBets/droppedZeroStake/droppedFlatPnl`) and on the card
("staked 139 of 186 qualifying").

**P1 · sizing bankroll compounded -110-fallback P&L** (`tracker.py`).
A post-epoch WIN with no captured price books fallback profit that then
scales every later Kelly stake — the April artefact, recreated inside
the money path. The compounding loop now skips rows without a real
picked-side price.

Refuted by verification (not fixed, on purpose): the TODAY-eyebrow
provenance claim and the record-vs-ledger day-strip mismatch claim.
12 lower-priority P2 findings logged in the audit output for later.

### Deferred (still awaiting operator decision)

- Swapping the calibrator to CIR. Brier-neutral, kills the plateau.
  Only worth shipping as a prerequisite for Kelly, since on its own it
  does not change which games get bet (see negative result above).
- Kelly sizing itself. Now safer than it was — the selection fix above is
  the precondition — but still unshipped. If adopted: half Kelly or less,
  and only after the calibrator plateau is gone.

---

## [2026-07-19] — Units fill hourly (not nightly) + daily system heartbeat

Operator reported "won units aren't tracking" and "telegrams come in
late." Investigation traced both to a single scheduling gap, and added a
heartbeat so a quiet day is never mistaken for a broken one.

### Fixed

- **Orphaned-STRONG healer now runs on `predict` ticks too, not only the
  nightly `grade`** (`.github/workflows/daily.yml`). `bet_placed=Y`,
  `units_risked`, `profit_loss_units`, and the `strong_graded` WIN/LOSS
  Telegram were all applied exclusively by `end_of_day_check.py`, which
  the workflow gated to the single `30 3 * * *` (11:30pm ET) grade run.
  So afternoon wins sat at `+0.000u` with no result ping until ~midnight
  (and that grade cron itself often fires 1-3h late). The predict step
  already live-grades finished 1st innings, so running the safety net
  right after it heals each game within ~1 cron tick. Idempotent, soft-
  fail, and the `strong_graded` ping has a 24h dedup window, so the extra
  hourly invocations are silent no-ops when the slate is already clean.

### Added

- **`tools/daily_heartbeat.py` + workflow wiring** — one Telegram per day
  ("☀️ N games today · picks: X STRONG, Y LEAN" or "No MLB regular-season
  games today"). Motivated by 2026-07-13, when an All-Star-break no-games
  day (correctly 0 picks / 0 alerts) was indistinguishable from an outage.
  New `daily_heartbeat` event type in `tracker._DEDUP_WINDOW_M` (18h ->
  one/day); not in `_SUPERGROUP_ALLOWED_EVENTS`, so it lands only in the
  operator DM. Fires on any predict tick from 11am ET on (dedup keeps it
  to one send), robust to a single scheduled cron being skipped.

---

## [2026-07-19] — Fix P&L calculator reading only the first 1000 rows

Operator reported "won units aren't tracking properly." Root cause found
in `tools/pl_calc.py`: the Supabase read used a bare
`.select("*").execute()`, which PostgREST caps at ~1000 rows. Mid-season
the ledger had grown to 1438 rows, so the calculator silently saw only
the **oldest** 1000 picks and could not see anything after **2026-06-14** —
35 days of bets, including every recent win, were invisible to it.

### Fixed

- **`tools/pl_calc.py` now pages through the whole `picks_<season>` table**
  (`.order("date").order("game_pk").range(offset, offset+PAGE-1)` in a loop
  until a short page) instead of one capped fetch. Post-fix the tool reads
  all 1438 rows and reports the full season (295W/205L, +39.60u; stored ==
  recomputed, no drift). The CSV fallback path was never reached before
  because a non-empty capped Supabase result short-circuited it.

### Notes

- Same unpaginated-read pattern still exists in `tools/analyze_losses.py`
  and `tools/backfill_variants.py` (different tables); flagged for a
  follow-up, not fixed here.
- The **dashboard** is unaffected: `dashboard/lib/roi.ts` and `board.ts`
  read the full `picks_<year>.csv` from disk, not the capped query.

---

## [2026-06-07] — Curtail STRONG NRFI (YRFI-only) after a full prediction rework

Operator pushed for a complete NRFI rework ("something is wrong with how
we predict that"). I ran one, end to end, and the honest result is:
**the NRFI prediction is sound — the side is unprofitable for structural
reasons, not a math bug.** So we stop betting it.

### Changed

- **`_LR_STRONG_NRFI_P` 0.62 → 1.01** (mlb_first_inning_predictor.py).
  1.01 = "off": no `nrfi_prob` ever clears it, so every NRFI-leaning game
  now routes to **LEAN NRFI (tracked, `bet_placed=N`)** instead of a real
  bet. We bet **YRFI only**. Reversible: set back to 0.62.
- **YRFI invariance proven empirically**: re-classified all 917 graded
  games under 0.62 vs 1.01 — 59 picks changed, **all 59 STRONG NRFI →
  LEAN NRFI, 0 YRFI picks touched**. YRFI is the `nrfi_prob<0.44` side; the
  changed gate only ever fires for high `nrfi_prob`.

### Why (the rework, in evidence)

- **Decomposition**: season NRFI **−10.5u** vs YRFI **+8.0u** (real odds).
  NRFI is the entire reason the book is red; YRFI-only would be +8u.
- **The prediction is NOT broken** (`tools/nrfi_prediction_diagnostic.py`):
  per-half run model near-perfect (pred 28.4% vs actual 28.3%), the two
  halves are independent (corr −0.08, so the product formula is valid),
  aggregate NRFI calibrated (47.5% vs 47.9%) and it even **beats the
  market** (book prices NRFI ~52%, truth 48%).
- **No prediction lever found**: situational miscalibration all died on
  the 4,802-game backtest (`tools/nrfi_situational_scan.py`); `whip_gap`
  null in 3-split (`tools/whip_gap_retrain_test.py`, redundant w/ FIP);
  the 1st-inning batting-order idea is dead — a hitter's 1st-inning OBP is
  pure sampling noise (year-to-year gap corr **+0.06**;
  `tools/batter_fi_obp_premise_check.py`).
- **No profitability lever**: the "only bet NRFI when we disagree with the
  book" filter tested **negative on 505 graded games**
  (`tools/nrfi_market_disagreement.py`) — even in the biggest-disagreement
  bucket we hit 53% needing 57%. On NRFI, our disagreements with the book
  are *us* being wrong. The market is efficient on NRFI; we can't out-
  predict an efficient price on a near-coin-flip inning.
- Full do-not-retread record: user memory `2026-06-07_nrfi_rework`.

### Added (read-only analysis tools, the investigation record)

- `tools/nrfi_prediction_diagnostic.py`, `tools/nrfi_situational_scan.py`,
  `tools/whip_gap_retrain_test.py`, `tools/batter_fi_obp_premise_check.py`,
  `tools/nrfi_market_disagreement.py`; extended
  `tools/nrfi_threshold_study.py` grid to include an "NRFI off" sentinel.

---

## [2026-06-07] — Quiet the noisy calibration-drift Telegram (was crying wolf daily)

### Fixed

- Operator: "I keep getting calibration drift messages." Diagnosed
  `tools/calibration_drift_monitor.py` as the source and found three
  reasons it over-fired:
  1. **Tiny-sample triggers.** Per-bucket alerts fired at a minimum of 8
     bets in each window. A 0.04 Brier swing on ~15 bets is noise — and
     the flagged buckets were exactly that size (n=14–18). Raised the
     minimum to **30** (matches the aggregate gate and the sister tool
     `calibration_monitor.py`). With the real data, nothing spurious fires.
  2. **Daily re-fire.** The Telegram dedup key included the date, so a
     slow-moving 30-day condition pinged a *fresh* alert every single day.
     Re-keyed to **ISO-week + which buckets drifted** → at most ~one ping
     per week per distinct pattern; a genuinely new drift still pings.
  3. **Stale buckets.** Boundaries were still 0.56/0.60; STRONG NRFI moved
     to 0.62 on 6/04. Updated to 0.62/0.66 so the discontinued 0.56–0.62
     dead-band bets sort into `pass_zone` instead of inflating the
     marg/deep-NRFI Brier. (The report now shows that dead-band clearly:
     pass_zone 37% hit / −7.1u over 30d — bets we *already stopped making*.)
- Also made local runs unicode-safe (the alert emoji crashed cp1252
  consoles; the Telegram body was always UTF-8 and unaffected).
- Observability-only: no model, calibrator, pick, bet, or odds change.
  The rigorous sister monitor (T2.59, hit-rate vs stated, 7pp + n≥30 +
  persistence) is untouched and still reports "no persistent drift," so
  real drift is still covered. File: `tools/calibration_drift_monitor.py`.

---

## [2026-06-05] — Show the *weather-adjusted* YRFI floor in the demotion tooltip

### Fixed

- Follow-up to yesterday's tooltip fix. Operator caught a still-wrong
  case: CWS@PHI 6/05 read *"the model's run projection (0.85) is below
  the 0.84 floor"* — 0.85 is **above** 0.84. The demotion was correct;
  the displayed floor was not.
- **Root cause: the YRFI lambda floor is weather-adjusted at decision
  time** (`mlb_first_inning_predictor.py::_weather_adjusted_floor`, T4.3).
  Hot (≥28 °C) or windy (≥24 km/h) games raise the floor +0.02 each;
  cold lowers it 0.02; dome neutralises. CWS@PHI was 32.7 °C, so the real
  floor was **0.858**, not the 0.838 base — and `lambda_lr_total = 0.8525`
  is below 0.858. The dashboard had been showing the static base.
- Fix: the dashboard now recomputes the **same per-game floor the
  predictor used** and shows it — *"0.85 below this game's 0.86 floor
  (raised because it's a hot/windy park today)"*. Added a `yrfiFloorUsed`
  field to `BoardRow`, computed in `lib/board-supabase.ts` (live path,
  which has the weather columns via `select *`) by a TS mirror of
  `_weather_adjusted_floor` — flagged KEEP-IN-SYNC with the Python. CSV
  fallback path uses the 0.838 base (board snapshots carry no weather).
- Also added a tie-safe number formatter so a projection within ~0.003 of
  the floor never renders as "0.84 below 0.84" (bumps to 3 dp only on a
  tie). Display-only; no model or bet behavior changed. Files:
  `dashboard/components/BoardRow.tsx`, `dashboard/lib/board-supabase.ts`,
  `dashboard/lib/board.ts`, `dashboard/lib/types.ts`.

---

## [2026-06-04] — Fix self-contradicting "PASS · LOW λ" tooltip (wrong lambda + stale floor)

### Fixed

- The board's `PASS · LOW λ` demotion tooltip showed nonsense like
  *"Combined λ 1.13 below the 0.78 floor"* — 1.13 is plainly **above**
  0.78. Operator caught it (BAL@BOS 6/04). Two bugs underneath:
  1. **Wrong lambda displayed against the floor.** The tooltip printed
     `combined_lambda` (the legacy Poisson display value, 1.13), but the
     YRFI floor demotion is actually decided by `lambda_lr_total` (the
     production model's own first-inning run projection — 0.80 for that
     game). The two are different numbers from different models; comparing
     the *displayed* one to the floor was never meaningful.
  2. **Stale floor value.** The tooltip strings hardcoded `0.78`; the real
     production floor (`_LR_LAMBDA_YRFI_FLOOR`) has been `0.838` since the
     5/26 ship. `DEFAULT_THRESHOLDS.lambdaYrfiFloor` was also still `0.78`.
- Now: the demotion tooltips reference the model's actual run projection
  (`lambda_lr_total`, 0.80) vs the correct floor (0.838), so "0.80 below
  the 0.84 floor" reads true. The big "λ" chip still shows
  `combined_lambda` (unchanged — it's what the board sorts by), but its
  hover note now explains the floor uses the model projection, not the
  displayed value.
- Plumbing: added `lambdaLrTotal` to the `BoardRow` type and populated it
  in both board builders (`lib/board.ts` CSV path via `toNumber`,
  `lib/board-supabase.ts` live path via `nullableNum`) so missing values
  are `null` (no false "0.00 below floor"). No model/bet behavior changed
  — display/explanation only. Fixed `DEFAULT_THRESHOLDS.lambdaYrfiFloor`
  0.78 → 0.838. Files: `dashboard/components/BoardRow.tsx`,
  `dashboard/lib/types.ts`, `dashboard/lib/board.ts`,
  `dashboard/lib/board-supabase.ts`.

---

## [2026-06-04] — STRONG NRFI threshold 0.56 → 0.62 (validated): stop betting the vig-break-even band

Diagnosed *why* NRFI accuracy was poor (operator pushed past "it's
variance"): a real, localized calibration problem, not noise.

### Why

- Calibration reliability on all graded games: when the model says
  `nrfi_prob` 0.56-0.62 it actually goes NRFI only ~56-57% -- right at
  the ~-130 vig break-even, i.e. NO edge -- while 0.62-0.66 goes 64%
  and 0.66+ goes 71% (clears the vig).  Robust signal (shows in May and
  June), unlike the weather lead which died on the 4,802-game backtest.
- The STRONG NRFI threshold (0.56) sat inside that dead band.  It was
  *previously* 0.62; the 2026-04-29 loosening to 0.56 was a YRFI-focused
  change (recapture 0.43-band YRFI picks) that dragged NRFI down with it.
- This raise REVERTS NRFI to the previously-validated 0.62.

### Validation (tools/nrfi_threshold_study.py, tools/edge_reality_check.py)

- Realized ROI on placed STRONG NRFI bets rises monotonically with the
  threshold: 0.56=-16%, 0.60=-8%, 0.62=-3%, 0.64=+6%.
- **TRUE walk-forward** (threshold chosen on prior weeks, applied blind):
  **+4.26u** vs leaving it at 0.56.  Clears the same bar the lambda
  ceiling did (+9.57u) -- and that the lower-YRFI-floor idea FAILED
  (-3.56u, not shipped).
- Effect: turns STRONG NRFI from a -16% / -7.3u drag into ~break-even by
  skipping the band where the model paid vig for a coin flip.  It does
  not make NRFI a strong bet; it stops the bleed.

### Changed

- `mlb_first_inning_predictor.py`: `_LR_STRONG_NRFI_P` 0.56 → 0.62.  The
  0.56-0.62 band now classifies as LEAN NRFI (track-only, `bet_placed=N`).
  Comments + the LEAN-band docstring updated.
- `dashboard/components/BoardRow.tsx`: `DEFAULT_THRESHOLDS.strongNrfiP`
  0.56 → 0.62 so the tentative classifier matches.  `npm run build` passes.
- `data/thresholds.json` picks up the new value on the next predict run.

### YRFI INVARIANCE (proven, not assumed)

20,200-cell grid test (every `p_nrfi` × `lambda`): **0 changes** where
`p_nrfi < 0.56` (the YRFI/PASS side), changes only on the NRFI side.
The winning YRFI engine is mathematically untouched.

### Context

This sits on top of the existing NRFI lambda ceiling (0.52): the
threshold filters low-confidence NRFI, the ceiling filters
high-projected-runs NRFI.  Complementary.  Also note the honest finding
from this session: across 139 real-odds bets the overall edge is NOT yet
statistically proven (bootstrap 95% CIs span 0); the recent profit was
concentrated in one week.  This change is about removing a *known* −EV
band, not claiming a proven edge.

### Rollback

`_LR_STRONG_NRFI_P = 0.56` in mlb_first_inning_predictor.py, commit, push.

---

## [2026-06-02] — Fix: stop the daily refresh-priors failure + silence false-alarm drift Telegrams

Investigated recurring GitHub Actions failures + recurring Telegram
"error" pings.  Two independent root causes, both fixed in Python (no
workflow-file edit -- this client lacks GitHub `workflow` OAuth scope).

### Fixed #1 — daily `refresh-priors` workflow failure (GitHub red X + email)

- Root cause: `pybaseball` was never in `requirements.txt`, so the CI
  backfill (`tools/backfill_truepit_2026.py`) hits
  `sys.exit("pip install pybaseball")` on import.  The `refresh-priors`
  job (cron `0 6 * * *`, once daily) wipes the per-pitch cache first,
  so the failed backfill leaves it empty; `build_truepit_2026_with_priors.py`
  then rebuilds 0 pitchers and writes an empty JSON; daily.yml's sanity
  check sees <100 pitchers and aborts (exit 1).  Failing every morning
  since ~2026-05-04 (when the priors JSON was last built locally).
- Impact was ZERO on the model: the sanity check correctly blocked the
  empty file from ever being committed, so production ran on the 206-
  pitcher 2026-05-04 priors the whole time.  Loud-but-safe.
- Fix: `build_truepit_2026_with_priors.py` now guards the write -- if a
  rebuild is degenerate (<100 pitchers) AND a healthy file already
  exists, it KEEPS the existing file and exits 0 instead of clobbering
  it.  daily.yml's sanity check then reads the preserved 206-pitcher
  file and passes; the commit step sees no change ("nothing to commit",
  exit 0).  Net: the run goes green, the model is unchanged, and a real
  refresh resumes automatically if/when the cache repopulates.

### Fixed #3 — daily false-alarm "feature drift HIGH" Telegram

- `tools/feature_drift_monitor.py` fired a HIGH-severity Telegram on
  `pick_cluster >= 4` (largest set of picks within 0.005 calibrated
  P(NRFI)).  That HIGH fired ~daily.  But the flat-zone study
  (tools/filter_impact_check.py, 2026-05-26) already proved clustered
  STRONG picks hit ~64% -- clustering is NOT predictive of bad
  outcomes.  So this was a daily false alarm.
- Fix: `severity_for_pick_cluster` now caps at MEDIUM (>=4 -> MEDIUM,
  >=3 -> LOW).  Telegram fires on HIGH only, so the cluster pings stop;
  the cluster size still appears in the drift CSV + summary for
  visibility.  Real drift signals (>=3sigma feature moves, >=30pp tag
  shifts) still escalate to HIGH and still Telegram.

### Deferred #2 — actually un-freezing the priors (NOT done)

- Adding `pybaseball` to make the daily refresh truly work would feed
  fresher Statcast into the model -- i.e. it CAN change picks.  With the
  model winning on the frozen priors, this is held pending an explicit
  decision; it also adds a heavy dep to all ~25 daily runs + a 30-50min
  flaky Statcast pull.  Tracked separately.

---

## [2026-06-01] — Fix: dashboard false-BROKEN from malformed writtenAtUtc timestamp

Investigating why nrfi-terminal.vercel.app showed status BROKEN / "no
refresh in 522 min" while picks were current, the root cause turned out
to be NOT the alias (which correctly points at the latest production
deployment) and NOT the cron (which runs hourly) — it was a malformed
timestamp.

### Root cause

- `mlb_first_inning_predictor._write_thresholds_json` wrote
  `writtenAtUtc` as `datetime.now(ZoneInfo("UTC")).isoformat(timespec=
  "seconds") + "Z"`.  Because the datetime is tz-AWARE, isoformat()
  already appends `+00:00`, so the result was `"...+00:00Z"` — an
  invalid ISO-8601 string carrying BOTH an offset and a Z.
- `dashboard/app/api/health/route.ts` does `Date.parse(writtenAtUtc)`,
  which returns `NaN` for that malformed form, so `lastPredictAt`
  stayed null and the route fell back to the most-recent
  `pick_changes.csv` flip time.  On quiet pick-flip days (e.g. today,
  4 stable picks) the last flip was hours old -> false "BROKEN".
  On busy days frequent flips masked the bug, which is why it was
  intermittent.
- This was the only production site with the bug: every other
  `isoformat() + "Z"` in the tree uses a NAIVE `datetime.utcnow()` (or
  `.replace(tzinfo=None)`), which yields a valid `"...Z"`.

### Fixed

- `mlb_first_inning_predictor.py`: emit `writtenAtUtc` via
  `strftime("%Y-%m-%dT%H:%M:%SZ")` — a clean, parseable UTC stamp.
- `dashboard/app/api/health/route.ts`: defensively strip a redundant
  trailing `Z` when an offset is present before `Date.parse`, so old
  bundled snapshots and any future producer slip can't regress this.
  Dashboard `npm run build` passes.

### Impact

Display/health-status only — zero effect on picks, bets, P&L, or the
model.  The alias was never stale (confirmed via Vercel API:
nrfi-terminal.vercel.app -> latest READY production deployment).  The
dashboard self-heals on the next predict run (writes a valid timestamp
+ triggers a redeploy that bundles it).

---

## [2026-06-01] — NRFI lambda ceiling (T1-NRFI): stop STRONG NRFI bleed without touching YRFI

The 2026-05-26 sliding-window retrain made the YRFI side excellent
(STRONG YRFI 17W/6L over the next 5 days, +7.56u) but STRONG NRFI kept
bleeding (20W/23L bet, ~−8.6u over 30d).  Root-cause investigation +
a pressure-tested fix.

### Why NRFI was bleeding (investigation)

- League-wide first-inning NRFI rate dropped Apr 49.9% -> early-May
  48.9% -> late-May 46.0%.  The retrained model + calibrator were tuned
  to a higher base rate, so STRONG NRFI ran overconfident: at cal_p>=0.65
  the actual hit rate was 40%; at 0.56-0.60 it was 36%.
- A full recalibration was tested and REJECTED: it would have cut YRFI
  bet volume ~59% (70 -> 29 on the 14-day holdout) and turned +2.3u into
  −10.8u.  The current YRFI edge is an *exploitable* gap between our
  model and DK's slow-moving line; "honest" recalibration removes the
  exaggeration that makes YRFI profitable.  (tools/recalibrate_only.py
  documents this — DO NOT recalibrate without re-reading it.)
- Feature-level study of NRFI losses: 53% were the HOME team scoring in
  the bottom of the first; home top-3 OBP was the most robust separator
  of NRFI wins vs losses (Cohen's d 0.34/0.43 across both halves of the
  sample).  Conceptual gap: the model uses "top-3 by OPS," not the actual
  1-2-3 batting order that determines first-inning scoring.  -> Track 2.

### Added — `_LR_LAMBDA_NRFI_CEILING` (NRFI-only lambda ceiling)

- `mlb_first_inning_predictor.py`: STRONG NRFI is demoted to PASS
  "HIGH LAMBDA" when the model's own `lambda_lr_total` (expected
  first-inning runs) exceeds **0.52**.  Mirror image of the existing
  `_LR_LAMBDA_YRFI_FLOOR`.  Resolves the internal contradiction where
  the model fired STRONG NRFI while projecting >0.5 runs.
- **Pressure-test evidence** (2026-04-27 -> 2026-06-01):
  - True walk-forward (threshold chosen on prior weeks only, applied
    blind): **+9.57u** vs no-gate; threshold stabilized at 0.50.
  - Robustness, all 51 graded STRONG NRFI at flat −110: a contiguous
    BASIN of good caps 0.48/0.50/0.52 = +2.33 / +5.89 / +5.44u,
    degrading smoothly to no-gate (−5.31u).  Not a knife-edge.
  - Kept bets hit 71-76% vs 51% ungated.
  - Chose 0.52 (loose edge of basin) to keep volume as insurance if the
    league reverts NRFI-friendly; 0.50 was the in-sample optimum.
- **YRFI INVARIANCE PROVEN, not assumed**: the ceiling check lives ONLY
  inside the `p_nrfi >= 0.56` branch of `classify_pick_lr`.  A 20,200-cell
  grid test (every p_nrfi x lambda combo) confirmed **0 changes** on the
  YRFI/PASS side (p_nrfi < 0.56) and changes only on the NRFI side.  The
  5-day YRFI winning structure is mathematically untouched.

### Changed — display + plumbing for the new PASS reason

- `tracker.py`: "HIGH LAMBDA" -> "High lambda" label; added to the
  PASS-reason label map.
- `mlb_first_inning_predictor.py`: "HIGH LAMBDA" added to the PASS-reason
  sort order, the board zone map, and `data/thresholds.json` output
  (`lambdaNrfiCeiling`).
- Dashboard parity (so the tentative classifier never drifts from
  Python): `lib/types.ts` (PickStrength gains HIGH LAMBDA + FLAT ZONE;
  PickThresholds gains optional `lambdaNrfiCeiling`), `components/
  BoardRow.tsx` (classifyTentative mirrors the ceiling; "PASS · HIGH λ"
  pill), `lib/board.ts` + `lib/board-supabase.ts` (parse the optional
  ceiling without rejecting older thresholds payloads).  Dashboard
  `npm run build` passes.

### Deferred — Track 2 (the real NRFI fix)

- Start capturing FULL batting order at predict time (currently only
  top-3-by-OPS is stored), then build an NRFI feature that uses the
  actual 1-2-3 hitters' on-base.  Must pass 3-split out-of-sample
  validation before shipping.  This ceiling is the interim bleed-stopper.

### Rollback

One constant: set `_LR_LAMBDA_NRFI_CEILING = 99` in
`mlb_first_inning_predictor.py`, commit, push.  Next cron run reverts to
ungated STRONG NRFI within the hour.  YRFI unaffected either way.

---

## [2026-05-26] — Sliding-window retrain: T1+B1+calibrator refit on 2024+2025+2026YTD, validated weekly-retrain workflow

After two weeks of slow losses (−5.33u realized 5/12–5/26), investigated
whether retraining on more recent data would help.  Built a candidate
model trained on 2024 + 2025 + 2026 through 5/11, validated against
multiple holdout windows.

### Added — sliding-window retrain shipped

- Trained Phase E.3 + VSHAND (19 features per half) on combined
  2024+2025+2026YTD truepit data (n=2933 graded games).  Replaced
  production `data/lr_t1.json`, `data/lr_b1.json`,
  `data/calibration_v2.json`; old files preserved as
  `*.bak-2026-05-26-prod-prephase`.
- Architecture is unchanged from previous production; the only
  difference is the training window includes 2026 partial.
- Validation:
  - **14-day holdout (5/12-5/26) head-to-head**: candidate −1.48u
    vs production −11.82u at flat −110 (apples-to-apples eval
    pipeline, no production guards applied).  Net **+10.34u**.
  - **6-week walk-forward (4/14-5/25)**: candidate +18.64u vs
    production +12.18u over 565 games at flat −110, net **+6.45u**.
    Candidate wins or ties 5 of 6 weeks.
  - **2025 in-sample check**: candidate Brier 0.244 vs production
    0.245 — no degradation.
  - **LR weight comparison**: shifts are principled (stronger park
    + pitcher quality + home offense signals; weaker humidity +
    redundant offense signals), not chaotic.

### Added — validated weekly retrain workflow (manual trigger)

- `tools/weekly_refit.py`: fits a candidate on
  (2024+2025+2026 through last week), evaluates BOTH candidate and
  current production on the most recent 7-day window, ships only if
  candidate P&L ≥ prod P&L − 1.0u AND candidate Brier ≤ prod Brier
  + 0.005.  Backs up production files before overwriting.  Exit
  codes: 0 ship, 1 validation fail, 2 script error.
- `.github/workflows/weekly_retrain.yml`: workflow_dispatch trigger
  for `tools/weekly_refit.py`.  Commits + pushes new model files
  ONLY if the gate passed.  **No schedule yet** — manual trigger
  for ~4 weeks while we watch behavior, then convert to weekly
  cron.  Respects the 2026-05-11 policy in `daily.yml` ("weekly
  auto-recalibrate was disabled because it shipped without
  validation"): this workflow has the validation built in.
- First post-ship invocation correctly REFUSED to ship a 5/18-
  trained refit because it underperformed the just-shipped 5/11
  candidate by −2.18u on the 5/19-5/25 holdout.  Gate works.

### Added — calibrator flat-zone diagnostic (DISABLED guard)

- `calibration.py`: new `ProbCalibrator.predict_with_band(p)`
  method returns `(calibrated_p, band_info)` where `band_info`
  exposes `band`, `is_flat`, `flat_size`, `flat_rate`.  Mirrors
  the inline detection logic in `tools/pick_reasoning_log.py`.
- `mlb_first_inning_predictor.py`: added `_FLAT_ZONE_DEMOTE_SIZE`
  constant and a guard that demotes STRONG → PASS "FLAT ZONE"
  when a pick lands in a calibrator flat zone with flat_size
  ≥ threshold.  Wired through to `tracker.py` pick-label
  composition.
- Threshold set to 99 = **DISABLED** based on empirical study
  (`tools/filter_impact_check.py`): picks landing in flat zones
  hit at **63.6%** over a 109-bet 30-day window — they're our
  best picks, not our worst.  The calibrator's flat zones are a
  statement about training-data noise, not about pick quality.
  Filter wiring left in place for future experimentation; raise
  threshold to enable.

### Tools

- `tools/build_2026_truepit.py`: augments `picks_2026.csv` with
  `actual_side` + `fi_park_nrfi_rate` so it can be used as a
  truepit-format training CSV by `two_stage_model.py`.
- `tools/sliding_window_eval.py`: head-to-head candidate vs
  production on a holdout, with hypothetical P&L using logged
  DK odds.
- `tools/walk_forward_eval.py`: walks across weekly windows
  comparing static-train vs sliding-window training.  Used to
  validate the +6.45u multi-week signal.
- `tools/filter_impact_check.py`: empirical study of which
  STRONG picks the flat-zone filter would demote and what the
  P&L impact would be.

### Performance snapshot (2026-04-27 to 2026-05-26, STRONG bets only)

- Pre-ship production (2024+2025-trained):  
  64W / 53L (54.7% hit), realized P&L **−0.79u** over 117 bets.
- Eval-pipeline projection of the new shipped candidate on the
  same period:  
  ~+6u improvement projected at flat −110 (real-money result
  will depend on DK odds we actually take).

### Rollback

If the shipped candidate underperforms over the next 5–7 days:
```
cp data/lr_t1.json.bak-2026-05-26-prod-prephase data/lr_t1.json
cp data/lr_b1.json.bak-2026-05-26-prod-prephase data/lr_b1.json
cp data/calibration_v2.json.bak-2026-05-26-prod-prephase data/calibration_v2.json
git add data/*.json
git commit -m "revert 2026-05-26 sliding-window retrain (underperformed live)"
git push origin claude/mlb-inning-run-predictor-QyazL
```

---

## [2026-05-19] — Model-refresh ship: i01 fix + 2024-2025 vintage constants + park factor refresh

Lands the bug-fix portion of a wider model-refresh investigation
(2026-05-19 session).  Two architectural experiments (FIE retest with
the i01 fix in place, and offense×pitcher interaction terms) were both
tested and FAILED Gate A — those experiments are NOT shipped.  The
shipped diff is the hygienic vintage refresh plus the i01 typo fix.

### Fixed — pitcher first-inning ERA fetch (i01 sitCode typo)

- `backtest.py:654` and `mlb_first_inning_predictor.py:667`:
  `sitCodes=[i1]` → `sitCodes=[i01]`.  The `i1` code silently returned
  empty splits for ~3 weeks (since commit a82677a, 2026-04-25), causing
  `prior_season_pitcher_fi` to always fall through to the no-FI-data
  branch.  Verified post-fix against Skubal 2025 (31.0 IP / 1.45 ERA),
  Webb 2025 (34.0 IP / 3.71 ERA), Cole 2023 (33.0 IP / 2.73 ERA).
  Pre-existing improvement_log row 2026-05-12-bug-prior-season-pitcher-fi-i1.
  Real-world impact on LR picks: minimal — production LR doesn't
  consume FI ERA directly (the legacy lambda diagnostic does).

### Changed — league constants refreshed 2023-2024 → 2024-2025

- `mlb_first_inning_predictor.py`, `two_stage_model.py`,
  `recalibrate_v2.py`, `tools/v21_shadow_predict.py`: 9 LEAGUE_AVG_*
  constants now derived empirically from
  `data/backtests/backtest_{2024,2025}-*_truepit.csv` (n=9,604 first
  half-innings).
  Largest deltas (>1% from prior values):
  - `LEAGUE_FIRST_INNING_RUNS`: 0.475 → 0.510  (+7.5%)
  - `LEAGUE_AVG_BB9`:           3.20  → 2.93   (-8.4%)
  - `LEAGUE_AVG_K9`:            8.9   → 8.75   (-1.7%)
  - `LEAGUE_AVG_SLG`:           0.414 → 0.407  (-1.7%)
  - `FIP_CONSTANT`:             3.10  → 3.23   (re-aligned to new ERA)
  Other constants moved <1%.  Per the predictor's own LEAGUE_CONSTANTS
  block warning, all constants and park factors refresh together.
  Pre-existing improvement_log row 2026-05-12-bug-league-first-inning-runs-stale.
- `_LR_LAMBDA_YRFI_FLOOR`: 0.78 → 0.838 in
  `mlb_first_inning_predictor.py:958`, `tools/v21_shadow_predict.py`,
  `tools/v23_walkforward_backtest.py`.  Mechanical scaling of
  0.78 × (0.510/0.475) to keep the STRONG YRFI gate internally
  consistent with the new league base rate.  Empirical re-derivation
  deferred (future work).

### Changed — park factors rebuilt on 2025+2026 to-date

- `rebuild_park_factors.py`: BT_2025 path now points at the `_truepit`
  CSV (non-truepit version was archived during May rev pass).
- `data/fi_park_factors.json`: rebuilt.  Source mix 2025 (n=2393) +
  2026 to-date (n=596) = 2989 graded games.  Base NRFI rate 49.78%.
  All parks shifted <1pp from prior values — refresh was mostly
  cosmetic but keeps the data current.

### Added — recalibrate_v2.py `--since` flag + walk-forward tool

- `recalibrate_v2.py`: optional `--since YYYY-MM-DD` argument for
  trailing-window calibrator refits.  Default behavior unchanged
  (full 2025+2026 fit).  Also updated `BT_2025_PATH` to truepit CSV.
- `tools/walkforward_model_refresh.py`: new validation tool that
  re-scores historical picks under proposed model changes and compares
  hypothetical vs actual P&L.

### Deferred — architecture work for next session

The session's architectural experiments (FIE retest and offense ×
pitcher interaction terms) both FAILED Gate A with the same +0.0014
Brier delta on a small n=201 holdout — strong indication that the LR
is at its plateau on current features under linear architecture.
Future work needs a genuinely non-linear stage (gradient boost on
residuals, or MLP) — explicit multi-week project, not in this commit.
Companion improvement_log row 2026-05-14-finding-phase3-interaction-architecture.

---

## [2026-05-12] — Playbook Phase 1.3: LEAN tier (track-only) + dashboard TOTAL preserves real-money meaning

Resurrects the LEAN classifier tier as TRACK-ONLY (never bet) so the
playbook's 60-graded-LEAN-pick break-even analysis has data to feed
on.  Ships with a dashboard fix that protects the season +35.5u
"real-money" P&L number from being silently redefined to include
hypothetical LEAN picks.

### Changed — Classifier thresholds + structure

- `mlb_first_inning_predictor.py`: `_LR_LEAN_NRFI_P` 0.56 → 0.50 and
  `_LR_LEAN_YRFI_P` 0.44 → 0.50.  Carves the legacy 0.44-0.56 PASS
  dead zone into two LEAN bands:
  - LEAN NRFI: `0.50 <= p_nrfi < 0.56`
  - LEAN YRFI: `0.44 <  p_nrfi < 0.50` AND combined lambda ≥
    weather-adjusted YRFI floor (default 0.78)
- `classify_pick_lr` restructured.  The previous structure short-
  circuited the 0.44-0.50 band into PASS NO EDGE before the LEAN
  YRFI branch could fire.  The new structure mirrors the playbook
  spec exactly.  STRONG NRFI / STRONG YRFI / LOW LAMBDA boundaries
  are unchanged.  13 boundary tests pass.
- `tracker._apply_odds_to_row`: LEAN picks ALWAYS take the
  `bet_placed = 'N'` path regardless of edge.  The previous
  "LEAN with edge ≥ min_edge → bet" branch is intentionally removed.
  `units_risked` is still recorded (0.5u default) so the playbook's
  60-graded-LEAN-pick break-even analysis has counterfactual stakes.
- `data/thresholds.json`: regenerated with the new constants;
  dashboard's tentative-classifier reads this file at request time.

### Changed — Dashboard TOTAL P&L preserves its prior meaning

Operator caught the contamination risk before push: rolling LEAN into
TOTAL would have silently redefined the +35.5u headline metric to
"STRONG + LEAN performance" instead of "real-money STRONG only."  Fix:

- `dashboard/lib/roi.ts` + `dashboard/lib/roi-today.ts`: TOTAL
  aggregation now strictly filters to STRONG zones.  LEAN's hypothetical
  P&L is computed in a separate `leanPaperTrade` field on the
  `RoiResponse` (LEAN's realized `profit_loss_units` is 0 because
  `bet_placed='N'`; we substitute a flat -110 hypothetical for the
  paper-trade view only).  `cumulativePL` chart series also excludes
  LEAN -- the bankroll curve stays a real-money curve.
- `dashboard/components/RoiPanel.tsx` + `.module.css`: new
  `LeanPaperTradeCard` component rendered only when at least one LEAN
  pick exists in the window.  Visually distinct from the TOTAL card
  via a dashed border and a diagonal "PAPER" watermark.  Eyebrow:
  "LEAN paper-trade · NOT BET".  Shows hit rate, hypothetical paper P&L
  at flat -110, pick count, and edge vs the 52.4% break-even bar.
- `dashboard/components/BoardRow.tsx`: TS mirror `classifyTentative`
  restructured to match the new Python classifier; default thresholds
  updated to `leanNrfiP=0.50` / `leanYrfiP=0.50`.
- `dashboard/components/ControlPanel.tsx` / `StatusLine.tsx`:
  stale "LEAN tier was removed" comments updated; LEAN+ filter is
  now first-class (already had the right behavior in DashboardShell).

### Sanity check

- Ported the new TS aggregation logic to Python and ran it against
  the live `data/picks_2026.csv`.  Season TOTAL = +35.535u, record
  141W-90L (61.0%).  Exact match with `python tools/pl_calc.py
  --window season`, which is the canonical P&L oracle.  The +35.5u
  headline survives the change unchanged.
- LEAN paper-trade currently shows 0 picks (Phase 1.3 hasn't run
  the cron yet); card will appear once LEAN rows accumulate.

### Operational notes

- `MODEL_VERSION` stays at `V2.2`.  This is a classifier-threshold
  change, not a weight change -- no retraining performed.
- LEAN picks now appear in `picks_2026.csv` with `pick_strength=LEAN`
  and `bet_placed=N` once lineups post and the next cron tick runs.
  Historical replay against existing rows (290 in the dead-zone band)
  produces 27 LEAN NRFI + 236 LEAN YRFI + 27 PASS (lambda gate fails)
  under the new logic.  The ~9:1 YRFI:NRFI split is a real property
  of the calibrator's output distribution (mass concentrated in
  [0.46, 0.50]; only 10 historical rows in [0.50, 0.54]) -- the
  classifier is symmetric across both bands.

---

## [2026-05-12] — Playbook Phase 1.1 + 1.2 foundation logging

Two zero-risk logging additions per `MLB_MODEL_IMPROVEMENT_PLAYBOOK.md`
Phase 1.  No predictor, tracker, classifier, or dashboard behavior
changes; only new files plus a cron hook.  Setup ahead of Phase 1.3
(LEAN tier reactivation) which is held on the candidate branch for
operator review.

### Added — Phase 1.1: improvement-log file

- `data/improvement_log.csv` (new): canonical record of every model
  change attempted from here on.  Columns mirror the playbook spec
  (`test_id, date_started, date_decided, change_description,
  brier_s1, brier_s2, brier_s3, walkforward_pnl, shadow_pnl,
  gate_result, notes`).  First row documents this Phase 1 setup itself.

### Added — Phase 1.2: V2.1/V2.2 disagreement-only log

- `tools/v21_v22_disagreements_log.py` (new): writes
  `data/diagnostics/v21_v22_disagreements.csv` containing only the
  picks where V2.1 (shadow) and V2.2 (live) disagree.  Agreements
  carry no comparative signal; disagreements are 100% of the
  informative sample.  Wired into the grade cron in `daily.yml`
  immediately after the existing `v21_vs_v22_compare` step (soft-fail
  with `set +e` so a bug here can never break the grade cycle).
- Columns: `date, game_pk, v21_pick, v22_pick, v21_prob, v22_prob,
  actual_outcome, v21_correct, v22_correct`.  Idempotent overwrite
  each run via atomic tempfile+os.replace.

### Prerequisite work

- `data/archive/v2.2/`: backup of V2.2 weights (`lr_t1.json`,
  `lr_b1.json`, `calibration_v2.json`, `fi_park_factors.json`) so a
  future rollback has a snapshot to copy from.  Mirrors the
  `data/archive/v2.1/` pattern.

### Operational notes

- `MODEL_VERSION` stays at `V2.2`.  No model weights, thresholds,
  or classifier behavior changed in this commit.
- The disagreement log starts populating on the next grade cron tick.
  As of this push the shadow tracker only has ~6 picks of history (V2.1
  shadow predict started 2026-05-11), so expect <5 disagreement rows
  initially.  Sample grows ~10/day.

---

## [2026-05-11] — V2.1 shadow tracker + dashboard demotion banner + shadow-P&L card

Three shipped changes to make the V2.2 deploy reversible and the
ongoing demotion experiments visible at a glance.

### 1. V2.1 shadow tracker (safety net for the V2.2 deploy)

- `tracker.py FIELDS`: three new optional columns:
  - `v21_shadow_nrfi_prob`
  - `v21_shadow_pick_side`
  - `v21_shadow_pick_strength`
  Schema-evolution code in `_read_rows` backfills blanks on first
  write; nothing breaks for older rows.
- `tools/v21_shadow_predict.py` (new): rebuilds T1/B1 feature
  vectors from CSV row columns, loads archived V2.1 weights from
  `data/archive/v2.1/`, computes calibrated P(NRFI) under V2.1, and
  stamps the three shadow columns.  Idempotent.  Wired into the
  predict + grade cron in `daily.yml` after `apply_cluster_demotion`
  so the shadow records V2.1's verdict independent of demotion policy.
- `tools/v21_vs_v22_compare.py` (new): reads the shadow + live
  columns, reports day-by-day + trailing-30 W-L + P&L for both
  versions, and fires Telegram if V2.2 underperforms V2.1 by 3u+
  over 30 graded STRONG picks.  Wired into the grade cron.
- Comparison treats cluster-demoted V2.2 rows as PASS for accounting
  (we didn't bet them) but uses the original verdict from the label
  for the "what V2.2 intended" intent check.

The shadow data accumulates from this commit forward.  After ~30
graded STRONG bets, we have a real apples-to-apples comparison and
can either ratify V2.2 or roll back per the procedure in the
"V2.2 deployed" entry below.

### 2. Active demotions banner (`/api/active-demotions` +
`dashboard/components/DemotionsBanner.tsx`)

- New API route reads `data/cluster_demotions.json`, counts how many
  of today's + trailing-7d rows were demoted under each active rule
  (matched on the `"PASS - Cluster demotion: ... (id)"` prefix the
  applier stamps), and returns a summary per cluster including
  `reevaluateAfter` + days-until.
- Component renders a small banner above the board with the cluster
  id, re-eval countdown (color-coded: green / amber / red), and
  today + trailing-7d demoted counts.  Renders nothing when no
  demotions are active.

Why: a 4-day demotion experiment can quietly become permanent if
nobody remembers to look at the data on day 4.  The banner makes
the active state unmissable + the countdown reminds the operator
when to evaluate.

### 3. Shadow P&L card (`/api/shadow-pnl` +
`dashboard/components/ShadowPnlCard.tsx`)

- New API route mirrors `tools/cluster_shadow_pnl.py` logic in
  TypeScript: for each active demotion, splits matching graded
  rows into REAL (placed-before-demotion) / SHADOW (skipped) /
  TOTAL with W-L counts and P&L.
- Component shows a compact 3-column card next to RoiPanel with
  the decision-tree footer (≥ 5W-2L = over-corrected, ≤ 2W-5L =
  real signal, mixed = wait).

Why: previously you had to run `python tools/cluster_shadow_pnl.py`
from CLI to see how the demotion was doing.  Now it's a glance
on the dashboard.

### Files changed

- `tracker.py` — three new optional CSV columns.
- `tools/v21_shadow_predict.py` (new)
- `tools/v21_vs_v22_compare.py` (new)
- `.github/workflows/daily.yml` — shadow-predict step (predict +
  grade paths) + v21_vs_v22_compare alert (grade only).
- `dashboard/app/api/active-demotions/route.ts` (new)
- `dashboard/app/api/shadow-pnl/route.ts` (new)
- `dashboard/components/DemotionsBanner.tsx` + `.module.css` (new)
- `dashboard/components/ShadowPnlCard.tsx` + `.module.css` (new)
- `dashboard/components/DashboardShell.tsx` — imports + renders
  both new components.

All TypeScript clean (`tsc --noEmit` exit 0).

---

## [2026-05-11] — V2.2 deployed: refit LR weights on corrected truepit backtests

**Production change.**  Same Phase E.3 + Phase F feature set, same
isotonic calibrator architecture; refit weights against the 5/03-
corrected truepit backtest CSVs (T4.1 / T3.12: "xwOBA->xERA proxy
anchor corrected 0.310 -> 0.3205").  Bumps `MODEL_VERSION` from V2.1
to V2.2.

### Forward-sim on 5/09-5/10 (29 graded picks): v2.2 vs v2.1

- **5 STRONG -> PASS flips on losing days** -- all 5 were the
  actual losses we wanted to avoid:
  - 5/09 STL@SD (STRONG YRFI -> PASS): was LOSS, **saved -1.00u**
  - 5/09 HOU@CIN (STRONG YRFI -> PASS): was LOSS, **saved -1.00u**
  - 5/10 NYY@MIL (STRONG NRFI -> PASS): was LOSS, **saved -1.00u**
    (operator's flagged Yankees-elite-offense miss)
  - 5/10 TB@BOS (STRONG NRFI -> PASS): was LOSS, **saved -1.00u**
- 1 STRONG -> PASS flip on a winning day:
  - 5/09 LAA@TOR (STRONG NRFI -> PASS): was WIN, cost +0.83u
- 1 PASS -> STRONG flip (5/09 CHC@TEX); outcome lost in window.
- Net P&L impact on those days: **+3.17u** (had v2.2 been live).

### What changed in the weights

Production V2.1 weights were last refit 2026-04-29 (Phase F lock-in).
The training backtests were updated 2026-05-03 with the xwOBA->xERA
proxy correction.  V2.1 weights are STALE relative to the corrected
training data.  V2.2 refit closes that gap.

Biggest T1 coefficient deltas (V2.1 -> V2.2):

| Feature | V2.1 | V2.2 | Delta |
|---|---|---|---|
| home_xera | +0.2964 | +0.0404 | -0.2560 |
| away_top3c_iso | +0.1982 | +0.3813 | +0.1831 |
| away_top3c_slg | -0.2097 | -0.4280 | -0.2183 |
| home_fip | -0.0745 | +0.0492 | +0.1236 (sign flip) |

Calibrator was also re-fit on the new raw distribution
(`recalibrate_v2.py` ran post-refit) so calibration matches the
new raw output range.

### 3-split OOS validation (passed)

- Split 1 (train 2024 truepit, test 2025): Brier 0.2511 (acceptable)
- Split 2 (train 2025 truepit, test 2024): Brier 0.2595 (acceptable)
- Split 3 (train 2024+2025, test 2026): **Brier 0.2437**
  - Production V2.1 raw Brier on same test set: 0.2479
  - **Improvement: -0.0042** (clears 0.003+ deployment threshold)

### Feature ablation -- decided NOT to drop top3c_slg or iso

Tested dropping top3c_slg and top3c_iso separately as fixes for the
multicollinearity (R8 finding).  Result:

| Variant | 2026 Brier | Elite-power Brier |
|---|---|---|
| FULL (keep both) | 0.2442 | 0.2390 |
| Drop SLG | 0.2466 | 0.2427 |
| Drop ISO | 0.2469 | 0.2422 |

Keeping both is best.  The opposing signs are capturing real signal
(ISO = pure power, SLG-above-ISO = singles/contact).  The 5/09-5/10
NYY losses were the model's recent variance, not a structural
mispricing -- v2.2's refit + recalibrated bins handle them
correctly (see forward-sim above).

### Files changed

- `data/archive/v2.1/` (new) -- snapshot of pre-deploy V2.1 weights
  + calibrator + park factors.  For rollback: copy these back into
  `data/lr_t1.json`, `data/lr_b1.json`, `data/calibration_v2.json`,
  `data/fi_park_factors.json` and bump MODEL_VERSION back to V2.1.
- `data/lr_t1.json` / `data/lr_b1.json` -- V2.2 weights.
- `data/calibration_v2.json` -- V2.2 calibrator refit on new raw dist.
- `mlb_first_inning_predictor.py` -- `MODEL_VERSION = "V2.2"` plus
  inline doc explaining the bump.
- `data/candidates/` (new) -- the OOS-validation candidates from
  splits 1/2/3 kept for audit trail.
- `tools/v22_feature_ablation.py` (new) -- ablation script used to
  confirm we shouldn't drop SLG/ISO.

### Rollback

If v2.2 underperforms over the next ~30 graded STRONG bets:
```
cp data/archive/v2.1/lr_t1.json data/lr_t1.json
cp data/archive/v2.1/lr_b1.json data/lr_b1.json
cp data/archive/v2.1/calibration_v2.json data/calibration_v2.json
cp data/archive/v2.1/fi_park_factors.json data/fi_park_factors.json
sed -i 's/MODEL_VERSION = "V2.2"/MODEL_VERSION = "V2.1"/' \
  mlb_first_inning_predictor.py
git commit -am "Rollback to V2.1"
git push
```

Per operator policy, all plays remain flat 1u regardless of model
version.  No per-bet sizing changes.

---

## [2026-05-11] — System audit R2/R3/R4/R5/R7/R8 — observability + candidates

Follow-up to the auto-recalibrate disable.  Operator asked for the
full R2-R8 sequence (R6 skipped per policy: no per-bucket bet sizing).
See `docs/2026-05-11_system_audit.md` for the full report including
data tables, candidate weights, and the multicollinearity finding on
elite top-3 offense.

### Shipped (R2-R4): observability + reminders

- **R2 — `tools/loss_cluster_monitor.py`** — `yrfi_040_band` cluster
  renamed to `yrfi_deep`; predicate simplified to `nrfi_prob < 0.40`
  (was `0.370 <= p <= 0.420 AND lambda + park gates`).  The original
  band straddled a profit boundary; 30-day data showed `[0.40, 0.44]`
  is *profitable* (14W-6L, +6.57u) while `<0.40` is the actual loss
  zone (6W-12L, -7.00u).
- **R3 — `tools/calibration_drift_monitor.py`** (new) — wired into
  the grade cron.  Computes per-bucket Brier on trailing 30-day
  STRONG bets; alerts on >= +0.01 bucket delta or >= +0.005 aggregate
  delta vs prior 30d.  Closes the drift-detection loop the disabled
  weekly auto-recalibrator used to fill, without auto-deploying.
- **R4 — `tools/demotion_reeval_reminder.py`** (new) + schema bump
  on `data/cluster_demotions.json` — each demotion entry can now
  carry a `reevaluate_after` ISO date.  On/after that date, the
  cron fires a Telegram with the current shadow-P&L snapshot +
  decision tree so the operator can keep/flip/remove the demotion.
  `thin_pitcher_strong_v1` set to re-eval 2026-05-14.

### Candidates built (R5, R7) — NOT deployed

- **R5 — `tools/platt_candidate.py`** + `data/calibration_platt_candidate.json`
  — Platt-scaling (logit-logistic) calibrator candidate.  Lost to
  production isotonic on every OOS slice (Brier +0.003-0.006 worse).
  Conclusion: isotonic flat zones are a feature, not a bug, on this
  data -- distinct raw probs genuinely map to identical true rates.
  **Do not deploy.**
- **R7 — `data/candidates/lr_t1_split3.json` + `lr_b1_split3.json`**
  — refit LR weights via `two_stage_model.py --phase-e3` on the same
  2024+2025 truepit backtests production trained on.  Result: real
  coefficient drift, candidate has Brier 0.2437 on 2026 vs production
  raw Brier 0.2479 (-0.0042 improvement, clears 0.003+ threshold).
  Root cause: production weights last refit 4/29 (Phase F) but the
  training backtest CSVs were updated 5/03 (xwOBA->xERA proxy anchor
  correction).  Production is stale relative to corrected training
  data.  **Operator decides whether to deploy** -- if so, also re-run
  `recalibrate_v2.py` so the calibrator matches the new raw distribution.

### Finding (R8) — multicollinearity in top-3 power features

Operator hypothesis: 5/10 NYY@MIL STRONG NRFI lost because elite
Yankees offense wasn't priced in.  Data confirms.

- Stratifying 30-day STRONG bets by `max(top3c_iso)`:
  - STRONG YRFI + elite power (max_iso >= 0.25):  4W-1L  (80%)  +2.62u
  - STRONG YRFI + no elite power:                 16W-17L (48%) -3.06u
  - STRONG NRFI + elite power:                    2W-2L  (50%)  -0.49u
  - STRONG NRFI + no elite power:                 12W-10L (55%) -0.80u

- Elite power IS a +32pp signal for YRFI hits when present, but the
  T1 LR coefficients `away_top3c_iso=+0.20` and `away_top3c_slg=-0.21`
  have *opposite signs*.  ISO and SLG are highly correlated (both
  measure power); the LR can't separate them and produces a
  near-cancelling pair.  Net effect of elite NYY offense on the 5/10
  T1 logit: -0.056 (wrong sign).

- **The R7 candidate AMPLIFIES this:** iso=+0.38, slg=-0.43.  Net
  effect on 5/10 NYY: -0.124, even more wrong.  R7's aggregate Brier
  improvement comes at the cost of worse predictions on elite-offense
  games.

- Recommended fix: raise L2 regularization in `two_stage_model.py`
  training.  Higher L2 shrinks correlated coefficients toward 0
  jointly, reducing the seesaw.  Alternative: drop SLG (or ISO),
  or replace with a composite.  See audit doc for full ranking.

### Cron wiring

- `daily.yml` grade step now runs:
  1. `tools/feature_drift_monitor.py` (existing T4.5 alert)
  2. **NEW** `tools/calibration_drift_monitor.py` (R3)
  3. **NEW** `tools/demotion_reeval_reminder.py` (R4)
  4. `tools/pick_reasoning_log.py` (existing T4.6)
- All soft-failing -- no new failure mode for the grade pipeline.

---

## [2026-05-11] — Disabled weekly auto-recalibrate cron (OOS validation gap)

Operator audit revealed the weekly recalibrate cron in
`.github/workflows/daily.yml` was refitting the production
calibrator + park-factor file every Monday at 04:45 UTC with
**no out-of-sample validation, no Brier-regression guard, no
rollback path**.  Per CLAUDE.md:

> Out-of-sample validation is non-negotiable for any model change.

The 5/11 forensic audit showed the practical impact of each
weekly refit is small (bin shifts of 1-2pp, OOS Brier within
±0.001 between refits — see commit ledger entries below for the
5/04 vs 5/11 comparison), but the GHA runner has no way to KNOW
whether a given week's refit is net-positive before shipping it.
That's the structural risk.

The 5/11 refit itself passed audit (Brier improved -0.0011 on
the 82-row 5/05-5/10 OOS slice) and stays in production; the
issue is the next refit was scheduled to ship blindly.

### Changed

- `.github/workflows/daily.yml` — commented out the Monday
  04:45 UTC `cron: "45 4 * * 1"` schedule.  Manual recalibration
  still available via `workflow_dispatch action: recalibrate`;
  guidance in the inline comment is to run the test_*.py 3-split
  OOS validation first and only ship if no regression.

### Forensic note: 5/05-5/10 was not a model regression

Day-by-day actual NRFI rates over the suspect window showed
the calibrator was correct on average -- it just lived through
back-to-back streaks in opposite directions:

| Window | Actual NRFI rate | Model predicted | Bias |
|---|---|---|---|
| 5/05-5/07 (won +7.23u) | 39.5% | 48.5% | +9.05pp |
| 5/08-5/10 (lost -5.22u) | 59.1% | 49.4% | -9.68pp |
| 5/05-5/10 combined | 50.0% | 49.0% | **+1.00pp** |

Combined window has near-zero bias.  The losing streak was
small-sample variance reverting from a hot streak, not a
recalibration drift.  The yrfi_040_band cluster (1W-5L on YRFI
in that specific shape) is a real localized signal -- which is
why we kept the thin-pitcher demotion -- but it does NOT
indicate a global model failure.

---

## [2026-05-11] — Cluster-demoted rows render as PASS with explanatory tooltip

Operator feedback after the 5/10 thin-pitcher demotion landed:
"if we're not betting on it or tracking the units for our
official record, then it should just say PASS with an
explanation in the dropdown why it's a PASS."  Previously the
demotion only flipped `bet_placed=N` and left `pick_side` /
`pick_strength` as STRONG NRFI/YRFI, which left the dashboard
showing a STRONG-toned pill that was actually a no-bet.

### Changed

- `tools/apply_cluster_demotion.py` — on a match, now also
  overwrites `pick_side='PASS'`, `pick_strength='NO EDGE'`, and
  encodes the original verdict + cluster id in `pick_label`
  using the magic prefix
  `"PASS - Cluster demotion: STRONG YRFI (thin_pitcher_strong_v1)"`.
  The prefix is the canonical "is this row demoted?" test
  everywhere downstream (shadow PnL tool, dashboard tooltip).
  Re-running on a row that already carries the prefix
  re-applies the demoted display state (in case the predictor
  regenerated pick_side back to STRONG on its pre-lock refresh,
  since pick_side isn't in the preserve list) but skips the
  journal write — one `pick_changes.csv` entry per row total,
  not 24 per day.
- `tools/cluster_shadow_pnl.py` — parses the original verdict
  out of `pick_label` for demoted rows; falls back to
  `actual_result` (NRFI/YRFI) for shadow W/L derivation since
  `graded_result` is now "PASS" for demoted rows.  Also prefers
  `opened_*_odds` (FIRST scrape captured by the T4.28 CLV
  pipeline, closest to the price we'd have bet at) over
  `market_*_odds` (latest scrape, closer to the close) when
  computing hypothetical P&L.
- `dashboard/components/BoardRow.tsx` — `PickPill` now matches
  the demotion prefix on `row.pickLabel` and renders a tooltip
  that names the demotion id, surfaces the model's original
  verdict, and points to `data/cluster_demotions.json` +
  `tools/cluster_shadow_pnl.py` for evaluation.  No new fields
  on `BoardRow` / `GameDetail` — everything reads from
  pickLabel.  TypeScript clean (`tsc --noEmit` exit 0).

---

## [2026-05-10] — Thin-pitcher STRONG demotion + shadow-P&L evaluator

Five-day window post v2.1 deploy (5/06–5/10) showed STRONG
NRFI/YRFI bets stratified hard on pitcher data quality:
both-`live` pitchers went 7W-1L (+4.89u), at-least-one-thin
(`sm`/`ltd`) went 6W-10L (-5.34u).  Operator opted to demote
NOW + run the inverse experiment via shadow-P&L tracking,
rather than waiting for the documented monitor-first protocol
to confirm.  Re-evaluation target 2026-05-14.

### Added

- `data/cluster_demotions.json` now contains one active entry,
  `thin_pitcher_strong_v1`: skips bet placement on STRONG
  NRFI/YRFI rows where the worst-quality pitcher is `sm` or
  `ltd`.  Predicate has no side / probability / lambda / park
  bounds — pitcher-data quality alone gates it.  The
  `apply_cluster_demotion.py` cron step picks it up on the
  next predict tick.  Reversible via `"active": false`.
- `tools/cluster_shadow_pnl.py` — new evaluator that, for each
  active demotion, prints REAL (bets the system still placed
  that match the predicate, e.g. pre-demotion history),
  SHADOW (bets the demotion skipped — hypothetical 1u P&L
  using captured market odds or flat -110 fallback), and
  TOTAL (the counterfactual: what the cluster would have
  done WITHOUT the demotion).  Use trailing shadow record to
  decide whether to keep `active=true` or flip it off.
  Run `python tools/cluster_shadow_pnl.py --since 2026-05-11`
  to see only post-demotion skips.

### Changed

- `memory/MEMORY.md` (auto-memory index) — added
  `thin_pitcher_demotion.md` entry so future agents see the
  active demotion + re-evaluation criteria.

---

## [2026-05-08] — Pending-pill cleanup for graded games + tentative-lean Telegram ping

Reported by operator on 2026-05-08: NYY@MIL and DET@KC (both
7:40 PM ET) locked at PASS · LINEUP PENDING with a tentative
STRONG NRFI lean.  Both first innings ended 0-0 (NRFI), but the
dashboard pill kept reading "PENDING · STRONG NRFI" with the
dashed border + pulsing dot for hours after the games had
effectively won the lean.  No Telegram ping fired for either
case (PASS rows don't trigger `_notify_strong_graded_telegram`).

### Fixed

- `dashboard/components/BoardRow.tsx::PickPill` — once
  `detail.gradedResult` is set, drop the dashed border, the
  pulsing dot, the "PENDING ·" prefix, and the pre-lock
  countdown.  Tentative lean still renders (just as
  "STRONG NRFI" or "STRONG YRFI") so the operator can see
  what the model leaned, but the row no longer reads as
  "still waiting."  Ungraded LINEUP/STARTER PENDING rows
  keep the existing dashed-pulsing treatment.
- `dashboard/components/OpsHealthCard.tsx` — defensive `?? {}`
  guards on the two `Object.keys` / `Object.entries` calls
  for `errorCountsByStep`, plus default `recentErrors = []` in
  the destructure.  Without them a partial /api/health-live
  response (e.g. Supabase-not-configured) crashed the entire
  dashboard with "Cannot convert undefined or null to object".

### Added

- `tracker._notify_lineup_pending_resolved_telegram` — fires
  once per game when a LINEUP PENDING / STARTER PENDING row
  grades to PASS with a non-PASS tentative lean.  Tells the
  operator whether the lean would have won or lost so they
  don't have to scrape the dashboard for that signal.  Wired
  into `grade_date()` next to the existing strong-graded
  ping; new event type `tentative_resolved` (deduped via
  notifications_log, 24h window).
- `tracker._classify_tentative_lean` — Python mirror of the
  dashboard's `classifyTentative` + the predictor's
  `classify_pick_lr` thresholds, so the new ping computes the
  same lean the pill renders.
- `grade_date()` retro-fire pass: after the per-row grading
  loop finishes, iterate today's slate (ET-gated) and call
  `_notify_lineup_pending_resolved_telegram` for every row.
  The per-row loop only reaches the notify call for rows it
  grades right now, so already-terminal rows from earlier
  cron ticks never fired the new ping.  This pass catches
  them on the next predict / grade cron, exactly once per
  game (notifications_log dedup).  ET-gated so historical
  re-grades don't backfill-flood the operator.

### Manual override (one-shot retro heal)

Operator made the call to count both 2026-05-08 LINEUP
PENDING wins (NYY@MIL + DET@KC) as actual bets, not PASSes.
Both first innings ended NRFI 0-0 = the model's tentative
STRONG NRFI lean was right; without the manual flip neither
shows up in today's record / units even though the lean
landed.

- `tools/heal_2026_05_08_lineup_wins.py` — idempotent script
  that flips both rows to STRONG NRFI WIN, locks
  market_nrfi_odds to the lock-time DK price (= captured
  `opened_nrfi_odds`: -140 for NYY@MIL, -125 for DET@KC),
  recomputes `profit_loss_units` via `tracker._calc_pnl`
  (+0.714u + +0.800u = +1.514u net), writes a
  `pick_changes.csv` journal entry per row, mirrors the
  rows to Supabase, and fires the standard
  `_notify_strong_graded_telegram` for each.  Re-running is
  a no-op (target rows detect already-healed shape;
  `notifications_log` dedups the Telegram side).
- `.github/workflows/daily.yml` predict step calls the heal
  script BEFORE `sync_csv_from_supabase` so the Supabase
  mirror lands before sync pulls back into CSV.  Soft-fail.
  The heal is idempotent so this step is safe to leave in
  the workflow indefinitely.

### Result

Today's record (2026-05-08) goes from 0-2-8 / -2.000u to
**2-2-6 / -0.486u** once the heal mirrors land in Supabase
on the next predict cron tick.

---

## [2026-05-08] — Don't lose STRONG bets to a stale lineup endpoint

Same-day root-cause fix for the 2026-05-08 NYY@MIL + DET@KC
incident.  Three independent failures stacked to keep both
rows at PASS - LINEUP PENDING:

  1. `backtest.fetch_top3_batters` only reads from
     `liveData.boxscore.teams.<side>.battingOrder`, which MLB
     populates AFTER first pitch.  Pre-game predict runs
     therefore always saw empty arrays and fell through to
     team-fallback, which then triggered the LINEUP PENDING
     guard regardless of whether the lineup card was actually
     published on MLB's pre-game endpoints.
  2. The LINEUP PENDING guard in `mlb_first_inning_predictor`
     forced PASS on every non-NO-DATA row that had a
     team-fallback `top3c_source` -- including STRONG verdicts
     that sit 6+pp above the threshold and could not flip
     under the small (≤2.26pp) shifts the original guard
     comment cited.
  3. Vercel cron tick cadence had a 60-minute gap covering
     the lock-time window for 7:40pm ET starts (21 UTC = 5pm,
     then nothing until 23 UTC = 7pm), so even if the
     boxscore endpoint had eventually exposed the lineup at
     6:30pm, no predict run was scheduled to pick it up before
     the T-60 lock at 6:40pm.

### Fixed

- `backtest.fetch_top3_batters` now falls back to the schedule
  endpoint with `hydrate=lineups` when the boxscore returns an
  empty `battingOrder`.  Schedule lineups expose
  `lineups.homePlayers` / `lineups.awayPlayers` -- the actual
  pre-game lineup card MLB publishes 2-3 hours before first
  pitch -- so the predictor sees the announced lineup as soon
  as MLB posts it instead of waiting for first pitch.  Boxscore
  remains the primary path; schedule fallback only fires when
  one or both sides are missing, so live games stay on the
  authoritative actually-batted source.
- `mlb_first_inning_predictor` LINEUP PENDING guard now skips
  STRONG verdicts (`pick_conf == "STRONG"`).  Operator policy
  per CLAUDE.md is to commit STRONG signals at whatever odds
  DK has; the guard's protection (small lineup-driven prob
  shifts demoting a pick) cannot apply to STRONG since the
  smallest possible shift to flip STRONG (`p < 0.56`) requires
  a 6+pp move that real lineups have never produced in
  observed history.  Guard still applies to LEAN / NO EDGE /
  LOW LAMBDA / etc., where lineup data CAN materially change
  the verdict.
- `dashboard/vercel.json` adds 30-minute-cadence Vercel cron
  entries between 21 UTC (5pm ET) and 02 UTC (10pm ET).  Each
  entry hits `/api/cron/predict`, which dispatches the GHA
  daily.yml workflow with the predict action.  Worst-case
  pre-lock staleness for a 7:40pm game is now 30 min instead
  of the previous 60-min gap that masked the lineup post.

### Defense-in-depth shape

Today's incident required ALL THREE of the above to fail
simultaneously.  After this commit, the same incident requires
all three of: (a) MLB's schedule endpoint to ALSO not have the
lineup at predict time, (b) the team-fallback verdict to be
LEAN / NO EDGE / etc. (not STRONG), AND (c) the cron tick
within 30 min of lock to fail or run late enough to miss the
window.  Any single layer holding catches the case.

---

## [2026-05-08] — PASS rows can re-evaluate post-lock as long as the game hasn't started

Same-day follow-up to the LINEUP/STARTER PENDING incident.
Operator: "the pit game was stuck as starter pending still.
we need to diagnose and fix this issue once and for all. it
should have been automatically set."

Root cause for PIT@SF specifically: at 7:58pm ET (the last
predict run before the 9:15pm T-60 lock), MLB's
`probablePitcher` field still showed home_pitcher=TBD --
Robbie Ray was announced afterwards.  Once the row hit lock,
the existing freeze policy preserved EVERYTHING the predictor
generates -- including pitcher fields -- so even when later
predict runs at 9:17pm and beyond saw Ray in the API, the
row's pick_strength stayed at PASS - STARTER PENDING.

The standard freeze rationale (T2.25: don't flip a STRONG
verdict after the user is in the bet) doesn't apply to PASS
rows: no money is committed at the lock-time PASS, so
re-evaluating up to first pitch is strictly upside.  Three
worst cases:

  1. PASS - PENDING -> STRONG NRFI: the user gains a bet
     they would have missed.  bet_placed=Y fires from
     `_apply_odds_to_row`'s normal lock-window auto-bet path
     once the next odds-import tick lands.
  2. PASS - PENDING -> PASS - NO EDGE: the row's label
     resolves to its actual verdict instead of staying in a
     stale "we don't know yet" state.  Already partly
     supported by the T2.14 `pass_label_refresh` hack but
     that only refreshed pick_side/strength/label, not the
     underlying inputs that drove them; now the whole row
     refreshes consistently.
  3. PASS - NO EDGE -> STRONG (rare): same as #1.

### Fixed

- `tracker._game_has_started`: new helper.  True once `now`
  (ET) crosses the row's `game_time_et`; mirrors
  `_is_inside_lock_window`'s defensive default for placeholder
  game-times so the abandoned-row case is still covered by
  defensive lock #1.
- `tracker.log_picks`: compute
  `post_lock_pass_refresh_eligible` once per existing row
  (PASS / no bet / no terminal grade / game not started) and
  thread it through both the change-detection notification
  gate AND the lock-bypass branch.  When eligible, the row
  follows the pre-lock full-refresh path, so pitcher / lineup
  / weather / probabilities / label all update consistently.
  The standard `pick_flip` Telegram fires on label changes
  -- including post-lock PENDING -> STRONG upgrades -- so the
  operator gets the standard "PENDING -> STRONG NRFI" ping
  the moment the bet commits.

### Net effect

Tomorrow's slate: when MLB announces a starter or lineup
post-lock-but-pre-first-pitch, the predictor will re-evaluate
the affected row, the dashboard pill will flip from "PASS -
STARTER PENDING" / "PASS - LINEUP PENDING" to whatever the
resolved verdict is, and (if the resolved verdict is STRONG)
the next odds-import tick auto-flips bet_placed to Y at the
in-lock-window DK price.  Today's PIT@SF was already past
first pitch when this code shipped so the row stays at the
post-game NO EDGE label that
`pass_label_refresh` already produced -- but the underlying
fix is in place for the next slate.

---

## [2026-05-10] — Cluster discovery + demotion pipeline

Operator: "we need to start finding more unprofitable clusters,
then we can make micro adjustments based on different specific
details, and then lower the total probability when certain
specific patterns arise again if they tend to lose over and over
again."

Built out a three-stage pipeline for identifying, watching, and
acting on loss clusters without overfitting to noise.

### Pipeline shape

```
1. DISCOVER             →   2. MONITOR                →   3. DEMOTE
   cluster_discovery.py     loss_cluster_monitor.py       apply_cluster_
                                                           demotion.py
   (read-only ledger        (defined cluster's            (skip bet placement
    scan; ranked              recent-5 record;             on confirmed bad
    candidates)               Telegram alert)              clusters via JSON)
```

Each stage is gated to prevent acting on small-sample noise.  A
candidate from stage 1 must pass through stage 2's runtime
confirmation (recent 5 = ≥4L) before the operator considers adding
it to stage 3's demotion config.  Stage 3 is fully reversible via
the JSON.

### Added

- `tools/cluster_discovery.py` — scans the season ledger for
  STRONG-bet feature combinations that have underperformed.  Three
  resolutions: (side, prob_band), (side, prob_band, lambda_band),
  (side, prob_band, lambda_band, pitcher_min_q).  Filters on
  sample-size and hit-rate floors; ranks by net P&L drag.  Output
  is read-only; never mutates CSVs or fires Telegram.  Initial run
  surfaced **STRONG YRFI with nrfi_p < 0.40 = 6W-11L (-6.0u over
  17 bets)** as the strongest candidate.
- `data/cluster_demotions.json` — operator-maintained demotions
  ledger.  Each entry declares a predicate (side, nrfi_prob band,
  lambda band, park band, pitcher_quality_min) plus an `active`
  flag.  Empty by default at launch.
- `tools/apply_cluster_demotion.py` — reads the JSON, finds every
  ungraded STRONG row matching an active demotion, and sets
  `bet_placed='N' + units_risked=''` so the bet is suppressed.
  Does NOT change `pick_side / pick_strength / pick_label` -- the
  model verdict stays visible on the dashboard for transparency;
  only the money commit is suppressed.  Idempotent.  Journals
  every demotion event to `pick_changes.csv`.
- Both predict and grade paths in `.github/workflows/daily.yml`
  now invoke `apply_cluster_demotion.py` before
  `sync_csv_from_supabase` (same precedence as the manual-odds
  override step).  Cluster discovery runs once per nightly grade
  cron with the trailing-21-day window; output goes to the
  workflow log for operator review.

### Documentation

- [docs/CLUSTER_DISCOVERY.md](./docs/CLUSTER_DISCOVERY.md) walks
  through each stage's purpose, the operator workflow for adding
  a new cluster (monitor) or demotion (skip), the safety rules
  baked in, and when to back off a demotion.
- CLAUDE.md money-rules section gets a one-paragraph pointer.

### What did NOT ship (deliberate)

- No automatic demotion of newly-discovered clusters.  Discovery
  is read-only by design; the operator decides which candidates
  graduate to monitor + demotion.
- No probability-modification layer.  The "lower the probability"
  framing maps cleanest to a calibrator refit
  (`recalibrate_v2.py`); the demotion path is a tactical bet-skip,
  not a model change.  Once enough confirmed clusters accumulate,
  refitting the calibrator on recent data is the durable fix.

---

## [2026-05-10] — Loss-cluster streak monitor

Operator on 2026-05-09 noticed that **5 of the 7 STRONG losses since
v2.1 deployed** (2026-05-06) shared a specific shape: STRONG YRFI bets
where `nrfi_p` ≈ 0.40 + `combined_lambda` ≈ 1.0, all of which ended
NRFI 0-0 (pitchers shut both halves down despite the model expecting
~1 first-inning run).  Operator's directive: "keep note of these
things where you're noticing the same type of pick is losing
constantly. if that loses again then we will have to adjust."

Added an automated watchdog that catches that signal the moment
it crosses a clear threshold.

### Added

- `tools/loss_cluster_monitor.py` -- defines named feature clusters
  and watches each one's recent-N record after every grading sweep.
  When a cluster's last 5 graded matches show ≥4 losses with
  hit rate ≤20%, fires a `loss_cluster_streak` Telegram alert
  with the recent trail and the operator's documented action plan
  (manual judgment skip OR `recalibrate_v2.py` on trailing 30-60
  days).  Two clusters defined at launch:
    1. **`yrfi_040_band`**: STRONG YRFI · `nrfi_p` 0.370-0.420
       AND `combined_lambda` 0.80-1.30 AND `park_factor` 0.90-1.30.
    2. **`nrfi_marginal_strong`**: STRONG NRFI · `nrfi_p` 0.560-0.590
       (barely above the STRONG threshold, low variance margin).
  Adding a new cluster: append a dict to `CLUSTERS` in the script.
- `tracker._DEDUP_WINDOW_M["loss_cluster_streak"] = 24*60`: 24h
  dedup per (cluster_id, date) so the alert doesn't re-spam across
  cron ticks.
- `tools/loss_cluster_monitor.py` wired into both predict and grade
  paths in `.github/workflows/daily.yml`, soft-fail.

### Memory

- `memory/loss_cluster_yrfi_040_band.md` documents the active watch:
  cluster definition, why it looks like drift not variance, the
  operator's manual-skip plan, and the recalibration path if the
  cluster confirms.

### Threshold rationale

Tuned against 2026-05-09 data: cluster sat at **2 losses in last 5**
(no alert).  Today's slate (2026-05-10) has two STRONG YRFI bets
matching the cluster (OAK@BAL, HOU@CIN) -- if both lose, recent-5
hits 4 of 5 = 20% hit rate, alert fires.  If only one loses, alert
holds off until next instance.

---

## [2026-05-09] — Manual DK odds overrides + orphan-bet Telegram alert

System audit on 2026-05-09 (operator: "make sure that the
tracking is being done properly") found that **112 of 220
graded STRONG bets across the season (51%) had used the -110
fallback** because no DK odds were captured at grade time.
Cause: the chronic Railway-down failure mode on the odds-
import worker.  GHA can't scrape DK directly (DK 403s GHA's
Azure IPs), so when Railway is down the row grades with empty
`market_*_odds`, `tracker._calc_pnl` returns the -110
fallback (=+0.909u for a win), and `tools/end_of_day_check.py`
silently stamps the row at that price as if -110 were the
real DK entry price.  The dashboard's `OddsChip` shows
"DK -110*" with an asterisk in this case, but the asterisk is
easy to miss; from the operator's perspective the row looks
like a real -110 bet.

### Added

- `data/manual_odds_overrides.csv` -- a user-maintained
  ledger.  Operator drops a row in whenever they need to record
  the actual DK entry price for a bet that the auto-scrape
  missed.  Format documented inline + in
  [docs/MANUAL_ODDS.md](./docs/MANUAL_ODDS.md).
- `tools/apply_manual_odds.py` -- idempotent heal script.
  Reads the override ledger, finds the matching pick row by
  `(date, game_pk)` (or `(date, away, home)` when game_pk is
  blank), patches `market_*_odds` / `sportsbook` /
  `odds_captured_at`, sets `bet_placed=Y` + `units_risked=1`
  for STRONG NRFI/YRFI rows that weren't already, recomputes
  `profit_loss_units` via `tracker._calc_pnl` from the
  supplied odds, journals each change to `pick_changes.csv`,
  and mirrors to Supabase.  Idempotent: re-runs with the same
  override are a no-op.
- `tracker._notify_strong_orphan_no_odds_telegram` -- fires
  the moment a STRONG bet grades W/L with empty
  `market_*_odds` (before this code shipped, the row would
  have been silently stamped at -110).  Body includes the
  exact line to add to `manual_odds_overrides.csv` to heal
  it.  New event type `strong_orphan_no_odds` with 24h
  notifications_log dedup window.  Wired into `grade_date`
  inline (catches new graded rows) AND the retro pass for
  today's slate (catches rows graded by an earlier cron tick
  before this code shipped).
- Both predict and grade paths in `.github/workflows/daily.yml`
  now invoke `tools/apply_manual_odds.py` before
  `sync_csv_from_supabase` so the override's mirror lands
  authoritatively.  Soft-fail.
- Documentation: [docs/MANUAL_ODDS.md](./docs/MANUAL_ODDS.md)
  + a CLAUDE.md money-rules entry explaining the override
  flow.

### Operator workflow going forward

1. STRONG bet grades without captured DK odds.
2. Telegram pings: "⚠️ NO DK ODDS CAPTURED · NYY @ MIL ·
   STRONG NRFI · WIN" with the exact override-CSV line to add.
3. Operator pastes the line into `data/manual_odds_overrides.csv`
   with their actual DK entry price + commits.
4. Next predict/grade cron tick (within 30 min) runs
   `apply_manual_odds.py`, which patches `market_*_odds` and
   recomputes `profit_loss_units`.
5. Dashboard / pl_calc / Supabase all reflect the real price.

Verified by sanity test: dry-run + apply + idempotency check
on a known orphan (TEX@NYY 2026-05-05 STRONG YRFI WIN).
Stored P&L moved from +0.909u (-110 fallback) to +1.150u
(real +115 entry) on the override.

---

## [2026-05-09] — RoiPanel "Last 7d" / "Last 30d" off-by-one fixed

Operator on 5/09: "the units tracked are wrong. for example,
last 7 days in the dashboard says -0.69, but i did the math
and it should be +1.93".  The 1.93 turned out to match the
STRONG NRFI side total for the corrected window, but the
total being -0.69 was the symptom of an off-by-one in the
window math.

`dashboard/lib/roi.ts` was using `isoMinusDays(7)` for the 7d
window and `isoMinusDays(30)` for 30d, then summing rows where
`startDate <= date <= today`.  Both endpoints inclusive ->
the window was actually 8 / 31 calendar days, not 7 / 30.
The extra day was what swung the total from +0.000u (the
canonical `tools/pl_calc.py --window 7d` answer for
2026-05-09's 7-day window) to -0.69u (which silently included
2026-05-02's -0.69u day).

### Fixed

- `dashboard/lib/roi.ts` window math: 7d now starts
  `isoMinusDays(6)` (today - 6 days), 30d now starts
  `isoMinusDays(29)` (today - 29 days).  Spans match
  `tools/pl_calc.py`'s `today - (days - 1)` exactly.
- After the fix, the dashboard's 7d total agrees with
  `pl_calc --window 7d` and the per-zone breakdown matches
  the operator's hand math (STRONG NRFI = +1.87u over the
  trailing seven days; STRONG YRFI = -1.87u; bet-zones total
  = -0.00u).



V2.1 (V2 LR + T4.2 priors-pooling + V2 calibrator) was already
locked-on at T4.10.  This commit completes the archival: every
V2-vs-V2.1 toggle, the V3 (Variant K) shadow dashboard surface,
and the V2-vs-V2.1 daily shadow comparison are all removed.

### Removed (V3 + shadow dashboard surface)

- `dashboard/components/ModelToggle.tsx` (+ module CSS) — the V2/V3
  pill in the header.
- `dashboard/components/ShadowDeltaCard.tsx` (+ module CSS) — the
  V2-vs-V2.1 shadow delta tile on the home page.
- `dashboard/app/history/v3/page.tsx` — the `/history/v3` route.
- `dashboard/app/api/shadow-summary/route.ts` — feed for the shadow
  delta tile.
- `dashboard/lib/roi.ts::loadV3Roi` — Variant K ROI aggregator.
- `model` prop + `v3` branches in: `BoardRow`, `BoardTable`, `RoiPanel`,
  `SummaryStrip`, `TonightsActionCard`, `HistoryView`, `GameDetails`,
  `DashboardShell`.
- `v3?: { ... }` fields on `BoardRow` and `GameDetail` types.
- `loadVariantKByGamePk` + the V3 splice in `lib/board-supabase.ts`.
- `?model=v3` handling in `app/api/roi/route.ts`.

### Removed (shadow tooling)

- `tools/daily_shadow_report.py` — built per-day shadow CSVs comparing
  V2 actual placed bets vs V2+T4.2 shadow.
- `tools/v2_t42_shadow.py` — pre-PR shadow regression gate.
- `.github/workflows/shadow_gate.yml` — PR check that runs
  `tools/v2_t42_shadow.py` against the trailing 14 days.
- Daily.yml: removed "Daily T4.2 shadow report" step + "Backfill
  variants A/C/AC" step (the A/B harness is no longer maintained).

### Removed (V2 toggle)

- `_USE_TRUEPIT_PRIORS` constant in `mlb_first_inning_predictor.py`.
  Was locked-on at T4.10 but kept as a vestigial toggle; deleted now
  along with the conditional in `fetch_pitcher_statcast`.  Priors-
  pooling is the only path for `xera` / `whiff_pct_rank` features;
  raw season cache stays as a rookies-without-priors fallback.

### Archived (kept on disk for historical reference)

- `data/diagnostics/shadow_*.csv` → `data/archive/diagnostics/`
- `data/calibration_v3.json` → `data/archive/`
- `data/v5_shadow_report.json` → `data/archive/`
- `data/v2_perfect_2026/backtest_v3cal_*.json` → `data/archive/v2_perfect_2026/`

### Left intact

- Supabase `pick_variants` table (no new writes, but historical rows
  preserved in case of future model post-mortem).
- `db/variants.py` (orchestration code referenced nowhere on the live
  path; harmless to leave on disk).
- `MODEL_VERSION = "V2.1"` constant remains as the per-pick label.
  Bumping convention noted in code: `V2.x` for feature-engineering
  improvements that keep the 18-feature LR architecture; `V3` for
  actual architecture changes.

### Verified

- `python -m py_compile` across all touched files: clean.
- `cd dashboard && npm run build`: clean. Bundle: home page 91.8 kB
  (down from 94 kB pre-cleanup); `/history/v3` and `/api/shadow-summary`
  no longer in the route table.
- Preview server: dashboard renders correctly. No V2/V3 toggle in
  header; no ShadowDeltaCard tile; "Bankroll @ DK" section no longer
  shows the "v3 shadow" label variant.
- `python tools/pl_calc.py`: still reports +2.220u for 2026-05-05
  (5W/2L), unchanged.

---

## [2026-05-05] — System reliability bundle: safer mirror, end-of-day safety net, drift-aware digest, GHA cleanup

Four upgrades that together close out the failure modes uncovered
during today's incident.  None of them ship new model behavior;
they're all reliability / observability.

### Added

- **`db.supabase_writer.patch_picks(rows, season, fields)`**: targeted
  field-level update on `picks_<season>` rows.  Unlike `mirror_picks`
  (which builds a full-row payload and upserts it -- any column blank
  in the source dict gets written as blank in Postgres), `patch_picks`
  sends only the listed fields.  Other columns on the destination row
  stay untouched.  Today's failure mode -- a backfill mirror with
  blank `market_*_odds` and blank `graded_result` overwriting real
  values in Supabase -- is structurally impossible with this primitive.
  Wrapped in `tracker._patch_picks_to_supabase` for caller convenience.

- **`tools/end_of_day_check.py`**: nightly safety net.  Scans the
  target slate (default: yesterday ET) for STRONG NRFI/YRFI picks
  whose game graded WIN/LOSS but whose `bet_placed` is empty -- the
  exact "DK closed market before scraper got odds" failure mode that
  bit today (4 STRONG bets stayed bookkeeping-orphaned all night).
  Auto-flips them to `bet_placed=Y, units_risked=1.0`, recomputes
  `profit_loss_units` via `tracker._calc_pnl`, and patches Supabase
  via the new `patch_picks` primitive.  Sends a single Telegram alert
  listing what was retro-fixed.  Silent if everything was placed
  correctly.

- **`.github/workflows/daily.yml` end-of-day step**: the safety net
  now runs automatically after the nightly grade cron, on both
  TODAY's slate (catches the just-finished games) and YESTERDAY's
  (catches any late west-coast game that graded after yesterday's
  safety-net run).

### Changed

- **Daily digest now shows P&L drift inline**: `_notify_daily_digest_telegram`
  takes optional `today_pl_recomputed` + `today_drift_rows` args.
  When the recomputed total differs from the stored total, the
  Telegram body inlines a warning: "stored +X.XXu vs recomputed
  +Y.YYu (N row(s)). Run `tools/pl_calc.py` to diagnose."  Same
  drift detection `pl_calc` runs; the digest now surfaces it
  proactively without the user having to check.

- **GitHub Actions DK scraping removed entirely** (T-CLEANUP-2026-05-05):
  - Every-5-min `odds-only` cron schedule deleted.
  - Entire `Run odds-only capture` step deleted (live grade + DK
    scrape + import-odds + live_state --once fallback all moved to
    Railway).
  - DK scrape + odds-import block removed from the hourly `predict`
    step (kept the rest: catch-up grade yesterday, live-grade today,
    Statcast predict, pick reasoning).

  Why: GitHub's Azure-range runner IPs get fingerprinted as bot
  traffic by DraftKings' CDN and 403'd 100% of the time -- even with
  the curl_cffi TLS impersonation fix.  Railway's Google-Cloud
  us-east4 IP is clean, so DK scraping moved entirely there.  Removing
  the GHA path eliminates ~2880 daily error-log entries from failed
  scrapes.

### Why this matters

Today's 30-minute "what's the right number?" debugging session was
caused by ONE mirror bug (mirror sends blanks → wipes real values).
The new `patch_picks` makes that class of bug impossible.  The safety
net catches the second-order effect (orphaned STRONG bets from any
cause).  The drift-aware digest catches anything either of the above
miss.  And the GHA cleanup means real errors (a Railway outage, a
model bug) aren't buried under hourly DK 403 noise.

---

## [2026-05-05] — `tools/pl_calc.py` canonical P&L calculator

Single command that prints the verified P&L for any date or window.
Reads `picks_<season>.csv`, recomputes per-row P&L with the same
helper the rest of the system uses (`tracker._calc_pnl`), and shows
both the stored and recomputed totals side-by-side so any drift is
visible at a glance.

Why: today I quoted +3.22u in chat, the user saw +2.55u in the
dashboard, and the actual answer was +2.22u.  Three numbers for
one slate within ten minutes.  Mental arithmetic is banned for
P&L going forward; the calculator is the canonical answer.

Usage:
- Today's slate:                  `python tools/pl_calc.py`
- Specific date:                  `python tools/pl_calc.py --date 2026-05-04`
- Trailing 7d / 30d / season:     `python tools/pl_calc.py --window 7d`

Bonus: the script flags rows where stored `profit_loss_units`
disagrees with the recomputed value -- catches the exact failure
mode that made today's incident a 30-minute debugging session
(my backfill mirror sent blank market odds and graded_result to
Supabase, overwriting real values; the recompute would have
flagged it instantly).

`CLAUDE.md` and `AGENTS.md` both updated to require running the
calculator before stating any P&L figure to the user.

---

## [2026-05-05] — Pick-lock alignment + proper DK 403 fix (curl_cffi)

Two fixes that together restore the "T-60min auto-bet + BET LOCKED
Telegram" workflow that's been silently broken for ~weeks.

### Fixed

- **Pick-refresh lock aligned with auto-bet lock**: `tracker._pick_is_locked`
  now uses `_pick_lock_minutes()` (default 60) instead of a hardcoded
  5 min.  Previously the predictor could keep refreshing the verdict
  until 5 min before first pitch even though the auto-bet path fires
  at 60 min, meaning a STRONG bet could be committed (`bet_placed=Y`,
  market odds frozen) and THEN flipped to a different side or PASS by
  the predictor in the 55 min between.  Both lock concepts now use
  the same window.  Dashboard's "PENDING · LOCKS HH:MM (60 min
  pre-game)" display already used 60; this aligns the backend.

- **DraftKings 403 properly fixed via curl_cffi**: `scrape_dk_odds.py`
  now prefers `curl_cffi.requests` with `impersonate="chrome120"` and
  falls back to plain `requests` when curl_cffi isn't available.
  Plain `requests` exposes Python's distinctive TLS fingerprint
  (JA3), which DK's CDN started rejecting with 403 Forbidden in early
  May.  curl_cffi wraps libcurl and impersonates a real Chrome
  handshake byte-for-byte, defeating that detection layer.  Verified
  bypass on 2026-05-05: API returns 200 + valid 14-event JSON where
  plain `requests` had been getting 403 for hours.  This unblocks the
  whole odds capture chain (morning 10am ET cron, every-5-min
  odds-only ticks, predict-cycle import_odds), which in turn
  re-enables the BET LOCKED Telegram alerts at T-60min for STRONG
  bets.

- **`requirements.txt`**: pinned `curl_cffi>=0.7.0,<1.0`.

### Why this matters

Workflow user expects: morning scrape captures odds for every game,
displays them in the dashboard.  At T-60min before first pitch for any
STRONG NRFI/YRFI pick: pick freezes, `bet_placed=Y` flips, market
odds lock at that moment's price, "🔒 BET LOCKED · STRONG NRFI"
Telegram fires with team / time / DK price / units / edge so user
can place the bet on DK.

Why it wasn't working: DK 403s blocked all odds capture, which broke
every downstream step (no odds → no `bet_placed=Y` flip → no Telegram
fire).  Fixing the 403 restores the entire chain.  The 5-min vs
60-min mismatch was a separate bug that would have caused odd
mid-bet pick flips once odds capture resumed.

---

## [2026-05-05] — Follow-ups: live-state team hydrate, DK warmup GET, agent-rule sync

Three small fixes after the morning audit-fix push.

### Added

- **AGENTS.md** committed to repo (was previously untracked).  Branch
  references corrected from `Codex/...` to `claude/...` to match the
  branch Vercel + GitHub Actions actually watch.  Closes the handoff's
  "branch-name discrepancy" deferred item.
- **Communication-style rule** added at the top of both `CLAUDE.md` and
  `AGENTS.md`: the user is not well-versed in developer terminology
  and has explicitly asked agents to talk to them like a complete
  novice.  Codifies the rule so every future session reads it before
  acting.

### Fixed

- **`/api/live-state` route**: hydrate list now includes `team` so
  team abbreviations (NYY, BOS, ...) populate.  Without it the proxy
  returned `away="?"` / `home="?"` for every game and the dashboard
  rendered "?@?" rows on the polling-fallback path.  Mirrors what
  `workers/live_state.py` already requests.
- **DK scraper 403 mitigation**: `scrape_dk_odds.fetch_dk_first_inning_runs`
  now performs a warmup GET against
  `sportsbook.draftkings.com/leagues/baseball/mlb` before the API
  call.  Cookies set by the warmup are auto-attached to the same
  `requests.Session` for the API call, making the API request look
  like a real browser session rather than a cold cookie-less hit.
  Best-effort: warmup failure is non-fatal.  If 403s persist next
  escalation is curl_cffi (TLS-fingerprint masking) or a residential
  proxy.

### Deferred

- **Railway live-state + predictor_loop workers**: code is ready in
  `workers/`, `Procfile`, `railway.json`, `requirements.txt` but the
  Railway services are gone (per T4.19).  Restoring requires a
  Railway dashboard session the agent can't run -- separate handoff.

---

## [2026-05-05] — Audit handoff fixes (first-inning grading, ET dates, board parity, cron auth)

Six review findings from the full-codebase audit closed in one pass.
Plan from [docs/HANDOFF_FIXES_2026-05-05.md](./docs/HANDOFF_FIXES_2026-05-05.md).

### Fixed

- **First-inning completion (P1)** — strict completion rule applied
  identically in `tracker._fetch_first_inning`, `workers/live_state.py`
  `parse_game`, and `dashboard/app/api/live-state/route.ts`: `Final`
  OR `currentInning >= 2` OR `currentInning == 1 AND inningState ==
  "End"`.  B1 / Middle-of-1 are no longer treated as complete.
  `tracker.grade_date` now also gates normal grading on the new
  `result["complete"]` flag, including the postponed/suspended
  fall-through, so a 0-0 in-progress B1 can no longer be graded NRFI
  before the home half ends.
- **ET-aware "today" (P2)** — new `dashboard/lib/date.ts` exposes
  `todayEtIso()` / `etIsoFromDate()` via
  `Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" })`.
  Adopted in `useLiveGameState`, `useSupabaseRealtime`,
  `DashboardShell` (live polling cadence), `lib/roi.ts` (server-side
  `isoToday`), and `app/api/health-live/route.ts` (replaced the buggy
  `toLocaleString → new Date → toISOString` round-trip).  Late-evening
  ET slates no longer disable Realtime/polling/ROI windows after 8 PM
  ET when UTC has rolled to tomorrow.
- **Supabase board parity (P2)** — `dashboard/lib/board-supabase.ts`:
  row order and `BoardRow.lambda` now use `combined_lambda` first
  (with `lambda_lr_total` fallback for legacy rows), matching the
  predictor's CSV board output and the CSV-fallback read path.
- **Cron auth (P2)** — `/api/cron/predict` and `/api/cron/grade` now
  REQUIRE `CRON_SECRET` (500 if unset, 401 on Bearer mismatch); the
  `x-vercel-cron-signature`-only fallback is removed because that
  header is unauthenticated and trivially spoofable.  `/api/run-job`
  now requires `RUN_JOB_SECRET` whenever `GITHUB_TOKEN` is configured
  (no more open-by-default fallback for a workflow_dispatch trigger).
- **Stale defensive lock self-unlock (P3)** — `tracker.log_picks`:
  `created_at` removed from the locked-row `allow_update` set.  A row
  locked solely by the stale-`created_at` defensive rule can no
  longer self-unlock by refreshing its own timestamp on the next
  predictor run.

### Deferred

- **Mojibake pitcher names (P3)** — `Cristopher Sánchez` /
  `Randy Vásquez` still flagged by `verify_data.py`.  Held for a
  separate pass; the handoff explicitly cautions against rewriting
  the ledger without first auditing Supabase + dashboard data
  mirrors and identifying the encoding source.
- **Branch-name discrepancy** in `AGENTS.md` (`Codex/...` vs
  `claude/...`).  Held until reconciled with whichever branch Vercel
  + GitHub Actions are actually watching.

### Verified

- `python -m py_compile tracker.py workers/live_state.py
  mlb_first_inning_predictor.py` — clean.
- `cd dashboard && npm run build` — clean (no TS errors, all routes
  built).
- `python verify_data.py` — same pre-existing P3 mojibake FAIL only.
- `python mlb_first_inning_predictor.py --summary --last 10` —
  loads, prints expected season-to-date summary.

### Deploy notes

- `/api/cron/predict` and `/api/cron/grade` now 500 (rather than
  silently fall through) when `CRON_SECRET` is unset on the Vercel
  project.  Verify the env var is configured before relying on the
  Vercel cron path; the GitHub Actions native `schedule:` trigger
  remains the primary path either way.
- Same caveat for `RUN_JOB_SECRET` once `GITHUB_TOKEN` is set on the
  Vercel project: the dashboard's manual run button will 401 until
  both are configured.

---

## [2026-05-04] — T4.2 priors-pooling deployed + full diagnostic stack (T4.2 → T4.10)

**The big one.** Root-caused the 2026-05-03 -4.56u disaster, deployed
a fix, and built six diagnostic layers so the next regression is
found in minutes instead of days.

### Background

T2.53 (committed 2026-05-03 11:16 ET) "fixed" the early-season
pitcher_q tag classification, marking ~24 of 30 pitchers as `'live'`
instead of `'ltd'`.  The tag flip silently disabled a protective
ERA-blend that had been shrinking small-sample 2026 stats toward 2025
priors.  Result: extreme xera values like 14.71 (a pitcher with 5
batted balls in 2026) reached the LR uncalibrated and drove confident
STRONG YRFI bets.  5/03 went 2-6, lost -4.56u, and we didn't know why.

### Added

- **T4.2** priors-pooled `fetch_pitcher_statcast()` in
  `mlb_first_inning_predictor.py`.  Reads
  `data/v2_perfect_2026/truepit_priors_per_pitcher_per_date.json` for
  per-pitcher per-date snapshots that pool 2025 full-season priors with
  2026 cumulative-through-yesterday data.  Shadow on 2026-04-29 to
  2026-05-04 placed bets:  V2 actual 13-13 -2.61u, V2+T4.2 9-5 +1.80u
  (12 PASS), delta +4.41u.  Saves the 5/03 disaster down to -1.55u.

- **T4.2 daily refresh** at 6 UTC (`.github/workflows/daily.yml`).
  Wipes per-pitch 2026 cache, refetches via pybaseball, rebuilds the
  priors JSON.  ~30-50 min runtime, well before the 12 UTC predict cron.

- **T4.4** `tools/daily_shadow_report.py`.  Per-day "what would V2 +
  T4.2 priors-pooling have done?" comparison.  Writes
  `data/diagnostics/shadow_<date>.csv` plus a moving-timeline
  `shadow_summary.csv`.  Wired into the nightly grade cron.

- **T4.5** `tools/feature_drift_monitor.py`.  Daily comparison of
  pitcher_q distribution + xera/whiff/top3c distributions + pick
  clustering vs the trailing 7-day baseline.  Telegram alert on HIGH
  severity.  When run retroactively against 5/03, fires 5 HIGH alerts
  (4 pitcher_q live/ltd flips + 1 calibrator-cluster bin-collapse) --
  vs 0 HIGH on the prior normal day.  Time-to-detection drops from
  24+ hours to under 30 seconds.

- **T4.6** `tools/pick_reasoning_log.py`.  Per-pick JSON dump showing
  top-5 LR feature contributions per half (z-score, weight, logit
  contribution), calibrator flat-zone detection, raw-cache vs priors-
  pooled Statcast, pitcher_q tags, and a warnings list.  When a pick
  loses, `jq '.picks[] | select(.matchup == "X@Y") | .warnings'` gives
  the dominant driver in seconds.

- **T4.7** `.github/workflows/shadow_gate.yml` + parametrized
  `tools/v2_t42_shadow.py`.  Pre-PR gate that runs the T4.2 shadow on
  the trailing 14 days using the PR's code path.  Posts the result as
  a PR comment and fails the status check if `delta_pl < -2u`.  Would
  have caught T2.53 before merge.

- **T4.8** `docs/PLAYBOOK.md`.  Standard checklist for: bad day on
  STRONG bets, drift alert fired, shadow delta negative for 5+ days,
  PR about to be merged, live state not updating.  Each section routes
  to the specific tool that gives a definitive answer.

- **T4.9** `dashboard/components/ShadowDeltaCard.tsx` +
  `dashboard/app/api/shadow-summary/route.ts`.  At-a-glance dashboard
  tile showing trailing 7-day T4.2 delta with status pill (ok / warn /
  regress).  Click to expand 14-day timeline table.  `copy-data.mjs`
  now also bundles `data/diagnostics/shadow_summary.csv`.

### Changed

- **T4.10**: `_USE_TRUEPIT_PRIORS` is now locked-on with an explicit
  comment in `mlb_first_inning_predictor.py`.  The toggle was kept
  during shake-out; the consistently positive shadow delta justifies
  making it permanent.  Future model architecture changes that need
  raw inputs should add a new lookup path, not disable this one.

- **T4.4 (deployment)**: bet halt lifted.  `daily.yml` `--min-edge`
  reverted from 0.99 (the T4.3 emergency halt) to the production
  default 0.02 in both the predict step and the 5-min odds-only tick.

### Fixed

- **Multicollinear sign-flip damage** in production LR weights
  identified during diagnosis (`home_fip` -0.0745, `away_top3c_slg`
  -0.2097, `away_fip` -0.0619, `home_obp` -0.0148, `home_top3c_slg`
  -0.1276).  These are artifacts of multicollinearity (xera covers
  pitcher quality, ISO covers power, top3c_obp covers offense; the
  redundant features fight with the dominant ones and flip signs to
  compensate).  Did NOT rebuild the model -- V5/V6/V7 candidate
  rebuilds all underperformed on 3-fold backtest and on 2026 placed
  bets.  T4.2 data-layer shrinkage addresses the real problem
  (extreme inputs reaching the LR), which was the actual cause of
  the 5/03 disaster.

### Performance

- 4/29-5/04 placed bets:  V2 actual 13-13 (50.0%) -2.61u; V2+T4.2
  shadow 9-5 (64.3%, 12 PASS) +1.80u; delta +4.41u.
- 5/03 alone:  V2 2-6 (25.0%) -4.56u; V2+T4.2 shadow 2-3 (40.0%, 3 PASS)
  -1.55u; delta +3.00u.
- 3-fold cross-year backtest of multiple architectures: prod 18-feature
  LR aggregates -5.59u over 2880 STRONG-zone picks (-0.2% ROI); the
  V2+T4.2 priors-pooling path is the targeted fix for 2026's specific
  small-sample-noise pathology, not a claim that the model is robustly
  +EV across years.

### Deferred

- Sliding-window LR rebuild (train on rolling last 60 days, refit
  daily).  Cross-year transfer is broken because MLB drifts annually.
  T4.2 caps small-sample 2026 noise but doesn't address the underlying
  cross-year problem.  Re-evaluate after 2-4 weeks of post-T4.2 live
  data.
- Market-edge model using DK NRFI implied prob as primary feature.
  Markets recalibrate to current-season conditions automatically;
  model would learn "when is the market mispriced" -- smaller
  question than "what's the true probability".  Blocker: no historical
  DK odds for 2022-2025; need to start logging forward.
- Three-line P&L distinction on dashboard (Realized / Paper / Backtest)
  to permanently fix the conflation that confused the operator during
  this session's investigation.

---

## [2026-05-03] — Variants G/H/I added to A/B harness after worst-day deep dive (T3.12)

After 2-6 record on 8 placed bets (-4.55u, worst day in 30 by 3.4×), forensic
analysis surfaced four structural insights documented in `docs/KB.md`'s new
"Known structural limitations" section:

1. Calibrator `data/calibration_v2.json` clamps P(NRFI) to [0.3623, 0.6620]
2. Within the YRFI band, 0.37-0.40 is a "losing valley" (41% hit, -6.26u/30d)
3. NRFI bets win 71%, YRFI bets win 60% (11pp gap, but 2.7× more YRFI volume)
4. Model can't see slate-context — predicts ~47% NRFI every day regardless of
   actual slate-wide NRFI rate (which swings 10%-75%)

### Added — three new A/B harness variants (`db/variants.py` + `tools/backfill_variants.py`)

- **Variant G**: skip STRONG YRFI in calibrated 0.37-0.40 band
- **Variant H**: tighten STRONG NRFI threshold from P(NRFI)≥0.58 to ≥0.62
- **Variant I**: G + H combined

Wired into `tools/abtest_report.py`.  Backfilled across all 405 graded
2026 STRONG picks (`--reclassify --since 2026-04-04`).

### 30-day backfill verdict

| Variant | Bets | W-L | Hit | P/L | Δ vs PROD |
|---|---|---|---|---|---|
| **PROD** | 185 | 115-68 | 62.2% | **+35.11u** | — |
| **VAR-G** ✅ | 156 | 103-51 | 66.0% | **+41.38u** | **+6.27u** |
| VAR-E | 160 | 101-57 | 63.1% | +33.56u | -1.55u |
| VAR-I (G+H) | 144 | 93-49 | 64.6% | +34.29u | -0.82u |
| VAR-D | 166 | 102-63 | 61.4% | +28.89u | -6.22u |
| VAR-H ❌ | 173 | 105-66 | 60.7% | +28.03u | -7.08u |
| VAR-C ❌ | 146 | 84-60 | 57.5% | +15.36u | -19.75u |
| VAR-AC ❌ | 115 | 59-55 | 51.3% | -1.84u | -36.95u |
| VAR-F ❌ | 5 | 2-3 | 40.0% | -1.55u | -36.67u |
| VAR-A ❌ | 135 | 69-65 | 51.1% | -2.75u | -37.86u |

**Variant G is the only profitable variant** in this harness round.

### Variant G partially fails 2025 holdout — Variant J emerges as the real signal

Built `tools/test_variant_g_2025.py` to test Variant G out-of-sample by
training LR + calibrator on 2024 only, then evaluating on 2025 full season.
**Two methodology runs** because the leak-free run lost the calibrator
range Variant G needs:

  Test 1 (leak-free 2024 -> leak-free 2025):   calibrator range
                                               [0.44, 0.63] -- no STRONG
                                               YRFI bets fire at all,
                                               can't be tested.
  Test 2 (leaky 2024 -> leaky 2025, mimics     calibrator range
         production methodology):              [0.33, 0.67] -- 558
                                               STRONG bets identified.

Test 2 result: Variant G nets only **+1.00u over 558 bets** vs production's
+34.17u → +35.17u.  Well within noise.  The +6.27u in-sample lift WAS
mostly selection bias.

But Test 2 ALSO surfaced a real, narrower signal.  The 0.37-0.40 "valley"
splits into two halves on 2025:

  [0.37, 0.38)   15 bets,  5-10,  33% hit,  -5.83u   <-- real losing zone
  [0.38, 0.40)   19 bets, 13-6,   68% hit,  +4.83u   <-- winning zone

The 30d 2026 in-sample also showed [0.37, 0.38) as a clear loser (9 bets,
2-7, 22% hit, -5.18u).  Combined across both independent samples:

  24 bets skipped, 7-17 (29% hit), -11.01u total saved.

### Added — Variant J (refined Variant G)

`db/variants.py` + `tools/backfill_variants.py`.  Skips ONLY the
0.37-0.38 calibrated-P(NRFI) sub-band on STRONG YRFI bets.  Backfilled
across all 405 graded 2026 picks.

  PROD          185 bets  115-68  62.2%   +35.11u
  VAR-G         156 bets  103-51  66.0%   +41.38u  (in-sample +6.27u, 2025 +1.00u)
  VAR-J         176 bets  113-61  64.2%   +40.30u  delta +5.19u  (2025 +5.83u)

Variant J reproduces on BOTH 30d 2026 in-sample AND 2025 full-season
holdout — the only variant tested to date that does so.

### Still NOT shipped to production

Variant J is the strongest candidate but still under the +10u-on->=2-folds
shipping bar.  Walk-forward gate remains broken pending the per-game
xera/whiff backfill (T3.11-AUDIT).  Variant J runs as a shadow pick only;
production threshold remains P(NRFI) ≤ 0.42 for STRONG YRFI.

If Variant J reproduces on a third independent sample (2024 holdout when
the per-game backfill lands), ship.  Until then: shadow.

### Update — strict walk-forward via per-pitch backfill (T3.12 Test 3)

After 728 pitchers fetched via `tools/backfill_xera_pit_perpitch.py`,
producing leak-free `backtest_*_truepit.csv` (cumulative-through-yesterday
xwOBA-derived xera + cross-pitcher whiff_pct_rank from raw per-pitch Statcast):

| Test | Methodology | STRONG YRFI bets | STRONG NRFI bets | Variant J lift |
|---|---|---|---|---|
| Test 1 (prior-year proxy 2024 → 2025) | leak-free but conservative | 0 | 413 | +0u (no YRFI to filter) |
| Test 2 (leaky 2024 → leaky 2025) | matches production methodology | 172 (60% hit) | 386 (68% hit) | +1u (G), +5.83u (J) |
| Test 3 (TRUE point-in-time 2024 → 2025) | strict walk-forward | **0** | **329 (54% hit)** | +0u (no YRFI to filter) |

**Result: under strict walk-forward, Variant J cannot be tested because
the model produces ZERO STRONG YRFI bets.  Calibrator range is [0.4583,
0.6357], floor is 0.05+ ABOVE the 0.42 YRFI threshold.**

But the bigger finding is that **STRONG NRFI bets, which are 68% hit
under leaky walk-forward, drop to 54% under strict walk-forward** —
roughly coin-flip at -110 odds.  The production model's apparent profit
edge is largely an artifact of feature leakage in the training data.

Variant J is now formally REJECTED because:
- Cannot reproduce on strict walk-forward (no STRONG YRFI bets exist)
- The premise (skipping a "losing band" within YRFI bets) only applies
  to the leaky-data calibrator, not to a genuinely-trained model

Methodology caveats documented in `docs/KB.md` "Headline finding" section:
the xwOBA→xERA proxy is simplified vs MLB's official formula, the
whiff_pct_rank computation uses 200-swing minimum, and 240 pitcher-rows
in 2025 had no pitcher_id mapping.  These could understate the model's
true leak-free signal.  But the qualitative conclusion (calibrator too
conservative for YRFI bets, NRFI bets at break-even) is robust.

### T4.1: catcher framing investigation -- REJECTED on walk-forward

User accelerated the catcher framing investigation from the scheduled
2026-05-15 remote agent to "do it now."  Built end-to-end pipeline:

  tools/build_catcher_framing.py
    Per-season catcher framing scores via per-pitch Statcast.  Filter to
    "called" pitches (called_strike + ball, no swings) in the "shadow zone"
    (within 4 inches of strike zone edge).  Per catcher: shadow_strike_rate
    vs league baseline = framing_score.  Multiply by shadow_pitches =
    extra_strikes.  Output: data/catcher_framing_cache.json (200+ catchers
    over 2024+2025; ~700K pitches per season scanned).

    NOTE: pybaseball.statcast_catcher_framing is broken (Savant changed
    the CSV endpoint; returns HTML).  This script bypasses it via raw
    per-pitch fetch + manual computation.

  tools/extract_catchers_per_game.py
    For each game, identify the FIRST-INNING catcher per side from the
    per-pitch fielder_2 column.  Need this because catchers swap mid-game
    and we predict the FIRST inning specifically.  Output:
    data/cache/catchers_per_game.json (4,741 games for 2024+2025, 100%
    coverage).

  tools/backfill_catcher_framing_to_csvs.py
    Joins framing + catchers caches into _truepit backtest CSVs.  Adds 6
    columns: home/away_catcher_id, home/away_catcher_framing,
    home/away_catcher_extra_strikes.

  tools/test_catcher_framing.py
    Walk-forward test: phase_e3 (16 features) vs phase_e4 (phase_e3 + 1
    catcher framing feature per half) on 2024 truepit -> 2025 truepit.

WALK-FORWARD RESULT (2024 truepit -> 2025 truepit):

  Phase E.3 (no framing):  347 bets, 190-157, 54.8% hit, +1.33u, Brier 0.2511
  Phase E.4 (+framing):    373 bets, 203-170, 54.4% hit, -0.83u, Brier 0.2518
  Delta E.4 vs E.3:        -2.17u P/L, -0.33pp hit rate, +0.0007 Brier (worse)

LR weights on the new feature:
  T1 home_catcher_framing: +0.0092  (essentially zero, wrong sign)
  B1 away_catcher_framing: -0.0445  (small, expected sign)

VERDICT: REJECT catcher framing for the LR model.

WHY IT FAILED:
- Industry consensus puts catcher framing at ~10-20 runs/season for top
  framers.  Spread evenly that's ~0.05 runs per first-inning -- well
  below the model's signal floor.
- The LR weight magnitudes confirm this: even the larger of the two
  weights (-0.0445 standardized) is too small to meaningfully shift
  predictions.
- Single-fold walk-forward with -2.17u P/L is small-sample but consistent
  with "near-zero true effect."  Multi-fold would only confirm the
  rejection more confidently.

CLOSES the catcher framing thread.  The scheduled remote agent for
2026-05-15 should be cancelled (no longer needed; the question is
answered).

DELIVERABLES committed:
  - data/catcher_framing_cache.json     (201 catchers, 2024+2025)
  - data/cache/catchers_per_game.json   (4,741 games)
  - data/backtests/*_truepit.csv        (with 6 new framing columns)
  - tools/build_catcher_framing.py
  - tools/extract_catchers_per_game.py
  - tools/backfill_catcher_framing_to_csvs.py
  - tools/test_catcher_framing.py

The data + tools are reusable: if a future model architecture needs
catcher framing for some other task (e.g. CLV prediction, lineup
context), the pipeline is in place.

### Three followups complete (T3.12 #1-3, 2026-05-03 evening)

#### Followup #1: refit calibrator on truepit corpus

`tools/refit_calibrator_truepit.py` builds `data/calibration_v3.json`
from 2024+2025 truepit (leak-free) data.  Sits next to v2; not
auto-deployed.

|              | range            | Brier  | bets | hit   | P/L     | ROI   |
|--------------|------------------|--------|------|-------|---------|-------|
| v2 (leaky)   | [0.3623, 0.6620] | 0.2498 | 712  | 59.0% | +58.00u | +8.1% |
| v3 (truepit) | [0.3833, 0.6116] | 0.2475 | 467  | 59.5% | +42.67u | +9.1% |

v3 is BETTER calibrated (lower Brier) and produces FEWER bets at
HIGHER ROI per bet.  Deployment pending walk-forward on a true holdout
(only available after 2026 season ends).

#### Followup #2: corrected xwOBA→xERA proxy anchor

Investigated empirical xwoba distribution across 725 cached pitcher-
season files: per-pitcher mean = 0.3205 (was anchoring at 0.310).
Updated `tools/backfill_xera_pit_perpitch.py` and regenerated truepit
CSVs (no API re-fetch).  Test 3 result improved slightly:

  Old proxy:  329 bets, 54.4% hit, -0.83u
  New proxy:  347 bets, 54.8% hit, +1.33u

Qualitative finding unchanged (no STRONG YRFI bets fire on single-
season truepit calibrator; NRFI bets at break-even).

#### Followup #3: realistic bankroll expectations

Documented in `docs/KB.md` "Realistic bankroll expectations" section.
Bottom line:

- **Live 30d +19% ROI is NOT the long-run expectation.**  Consistent
  estimate from 3 honest backtests: +5 to +9% ROI long-term.
- **Expected monthly P/L: +10-20u**, not +36u.  Plan around +10-20u.
- **Bad days are normal.**  1-of-5 STRONG day = once-a-month.
- **Today is variance**, on top of real but smaller edge than the
  live 30d sample suggested.

### What this means for the broader project

The roadmap's Variant J line item is closed REJECTED.  The deeper
question now is: **does the production model have any real edge once
calibrator leakage is fixed?**  Three paths forward:

1. **Refit production calibrator on leak-free corpus** — use truepit
   2024 + 2025 to fit the calibrator (vs current 2025+2026 leaky data).
   Production model would become more conservative; fewer STRONG bets
   per slate but each more confident.
2. **Investigate methodology suspicion** — improve the xwOBA→xERA
   proxy (use MLB's official formula instead of linear slope) and
   redo Test 3 to confirm the break-even result isn't a methodology
   artifact.
3. **Accept the finding and adjust expectations** — the model has
   a small real edge inflated by leaky training data into a larger
   apparent edge.  Live betting at flat 1u stakes assumes the apparent
   edge; if real edge is half of that, downside risk is substantial.

These are the actual next steps.  None ship tonight.

---

### Deferred (need walk-forward to validate)

- **Refit calibrator** on a leak-free corpus to widen the 0.36-0.66 range.
- **Add slate-context features** (slate-mean P(NRFI), count of high-quality
  starters, etc.) so the model can express slate-wide NRFI lean.

### Telegram + Railway operational fixes (separate, this evening)

Updated Railway predictor + worker `TELEGRAM_CHAT_ID` to new supergroup id
(-5115372935 → -1003953933618 after Telegram migrated the Backfist Bets
group from regular group to supergroup).  Bot still requires manual
"Send Messages" permission grant in Telegram (cannot be set via API).
GitHub Actions secret update typed; pending user 2FA confirmation.
Added `PREDICTOR_SCRAPE_DK=skip` env var on predictor service to suppress
known-noise scrape-dk failures (T2.56 was already the documented default).

---

## [2026-05-03] — Walk-forward framework shipped + same-day audit + retraction

After a rough 1-4 day on 2026-05-03 (eventually 1-of-5+), built the walk-forward
framework, made an inflated claim, then audited it the same day after the user
pushed back. Net result: framework exists, two variants honest, one variant
(phase_e3) retracted pending point-in-time backfill.

### Added — `tools/walk_forward.py` (T3.11 / Tier 3 #11)

Walk-forward backtest framework. Trains on prior seasons, tests on the next,
multi-fold across 2022 → 2025. For each fold reports:

- Brier score vs climatology (skill % = 1 − Brier/climatology)
- Top-quintile NRFI hit rate (Q5)
- Bottom-quintile YRFI hit rate (Q1)
- Simulated betting P&L at -120 vig under production STRONG thresholds
  (NRFI ≥ 0.58, YRFI ≤ 0.42; net 0.83u win, -1.0u loss)

Compares two baseline variants (`slim`, `slim_weather`) across 3 multi-season
folds plus a single-fold check on the production `phase_e3` model. Optional
`--save-json`. Verdict block auto-classifies each variant PASS / PASS-Brier-only /
MIXED / FAIL.

**Slim variants (LEAK-FREE — these results stand)**:
- `slim_weather` — 3 folds, 448 bets, 247-201 (55.1%), +4.83u (+1.1% ROI). FAIL on Brier.
- `slim` — 3 folds, 225 bets, 121-104 (53.8%), -3.17u (-1.4% ROI). FAIL.

### Retracted — `tools/walk_forward.py` `--include-e3` claim (T3.11-AUDIT)

Initial run reported phase_e3 at 572 bets / 58.0% hit / +36.67u / +6.4% ROI on
2024→2025 with positive Brier skill. Audited same day after user pushback and
found feature leakage:

- `home_xera` / `away_xera` and `home_whiff_pct_rank` / `away_whiff_pct_rank`
  are pulled from `data/statcast_pitcher_cache.json`, which is keyed by
  `(season, pid)`. So every game in the 2025 backtest gets the pitcher's
  END-OF-2025 xera and whiff_pct_rank — perfect future-data leakage.

Removing those 4 features and re-running (`tools/walk_forward_leakfree.py`):

| Phase E3 fold (2024 → 2025) | Bets | W-L  | Hit  | P/L     | ROI    | Brier skill |
|---|---|---|---|---|---|---|
| With leak (initial claim)    | 572  | 332-240 | 58.0% | +36.67u | +6.4%  | +0.46% ✅ |
| Leak-free (audit)            | 471  | 252-219 | 53.5% | -9.00u  | -1.9%  | -0.59% ❌ |

The "phase_e3 PASSES walk-forward" claim is **retracted**. The model has not
been validated on a clean walk-forward yet.

The other 14 features per half are properly point-in-time (filtered by
`date < target_date_iso` in `pitcher_last_n_first_inning`,
`pitcher_vs_team_nrfi_rate`, `pitcher_role_features`, `current_season_top3_stats`).
Umpire NRFI rates trained only on 2022+2023 (per `umpire_rates.json` metadata),
so safe for use in 2024+ tests.

### Live production data (unaffected by backtest leak — point-in-time in production)

| Window | STRONG bets | W-L | Hit | P/L |
|---|---|---|---|---|
| Last 30d | 184 | 116-68 | 63.04% | +36.13u |
| Last 14d | 75 | 48-27 | 64.0% | +15.32u |
| Last 7d | 40 | 26-14 | 65.0% | +8.32u |
| 2026-05-03 (today) | 5 | 1-4 | 20.0% | -3.35u |

vs break-even at -110 (52.4%): z = 2.89, one-sided p = **0.0019**. Wilson 95% CI
on true rate: 56.1% – 70.0%. So the model HAS real edge in live production; the
backtest leak inflated the *measurement* of that edge but didn't fabricate it.

### Verdict on today's 1-of-5(+) losing run

Even at the WORST-case true rate (52.4%, zero edge), 1-of-5 STRONG bets has
probability 15.9% — happens 1-in-6 days. At the point-estimate 63%, it's 6.6%
(1-in-15). Today's pain is consistent with variance under any rate the data
supports. No structural failure mode change in `loss_analysis` table buckets.
**No model action taken**, but the walk-forward gatekeeper is not yet proven
honest until the point-in-time backfill lands.

### Followup committed (T3.11-AUDIT-FIX, pending)

1. `tools/backfill_xera_whiff_pit.py` — recompute per-game cumulative xera and
   whiff_pct_rank from per-pitch Statcast data, replacing the `(season, pid)` cache.
2. Regenerate 2024 + 2025 backtest CSVs against the new cache.
3. Re-run `tools/walk_forward.py --include-e3` for honest phase_e3 verdict.

---

## [2026-05-02] — Real-time architecture migration (Phases 1.5 / 2 / 3 / 4 / 6)

Six-phase push that took the dashboard from "polled CSV reads" to a
**Supabase Postgres + Realtime + Railway workers** stack with a **PWA-installable
Next.js dashboard** receiving sub-second push of model + game-state updates.
The predictor model itself is **untouched** — same LR-v3 weights, same
classifier, same bet-time locks. The change is purely data plumbing + freshness.

End-to-end latency dropped:
- Predictions:    60-180 min (GHA cron drift) → **~5 min** (Railway 5-min loop)
- Live game state: 30 sec polling             → **~10 sec** push
- Pick flips on the dashboard: Vercel rebuild  → **~200ms** Realtime push

### Performance snapshot — 2026-05-02

| Window | Active picks (W-L) | Win rate | Net P&L |
|---|---|---|---|
| Last 30 days | **113-60** | **65.3%** | **+41.99u** at -110 fallback |
| Last 7 days | included above | 69%+ | continuing 4/30 streak |
| 2026-05-02 (today, in-progress) | 0-0, 2 STRONG bets pending | — | ARI@CHC NRFI +4.32% edge, BAL@NYY YRFI +4.63% edge — both auto-`bet=Y` |

### Added — Phase 1: Supabase project + schema (yesterday's prep, but listed for context)

- **db/schema.sql** — 5 tables: `picks_2026` (mirrors tracker.FIELDS field-for-field),
  `pick_changes` (intraday flip journal), `system_errors` (cron failure log),
  `live_game_state` (Phase 4 worker writes here), `odds_history` (Phase 5 placeholder).
  Composite PK `(date, game_pk)` handles doubleheaders correctly. JSONB columns
  for lineup + top-factors. Realtime publication enabled on the 3 tables the
  dashboard subscribes to.
- **db/migrate_csv_to_supabase.py** — one-off bulk migration with PICKS_FIELD_MAP
  for type conversions. 413 picks + 23 pick_changes successfully migrated.
- **RLS migration** — `enable_rls_with_anon_read_only_policies`. anon +
  authenticated SELECT-only policies on all 5 tables; service_role bypasses
  RLS so tracker.py + workers keep writing freely. Resolves all 5
  ERROR-level + 1 WARN security advisor lints.

### Added — Phase 1.5: Supabase dual-write from tracker.py (T2.30 / `bae3f34`)

- **db/supabase_writer.py** — lazy-loaded helper module. `mirror_picks(rows, season)`
  bulk-upserts to `picks_<season>` with ON CONFLICT (date, game_pk).
  `mirror_pick_change(...)` inserts a single journal row. `mirror_system_error(...)`
  inserts an ops-health row. All public entry points catch all exceptions, log
  to stderr, return 0/False on failure — never raise. Silent no-op when
  SUPABASE_URL / SUPABASE_SERVICE_KEY env vars are unset.
- **tracker.py wiring** — `_mirror_picks_to_supabase` + `_mirror_pick_change_to_supabase`
  helpers added. Four call sites: after each `_write_rows` in `log_picks`,
  `grade_date`, `import_odds`, plus inside `_record_pick_change`. Each callsite
  passes only the rows that actually changed in the current call (not the full
  slate) to keep egress minimal. Wrapped in try/except that swallows everything
  — CSV remains source of truth, Supabase is the mirror.
- **requirements.txt** — `supabase>=2.0,<3.0` + `python-dotenv>=1.0,<2.0`,
  marked as optional at runtime.
- **.github/workflows/daily.yml** — `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`
  surfaced in the predict + grade env blocks. GitHub repo secrets added via
  the Actions settings UI; cron starts dual-writing automatically.

### Added — Phase 2: Dashboard read-side cutover to Supabase + Realtime (T2.31 / `d078dbc`)

- **dashboard/lib/supabase.ts** — server-side + browser-side client factories.
  Lazy-cached singletons. Returns null when env vars unset so callers can
  gracefully fall back. Server client disables Realtime + auth (overhead-free),
  browser client persists session.
- **dashboard/lib/board-supabase.ts** — `loadBoardFromSupabase(iso)` returns
  the same `BoardResponse` shape as the CSV reader, populated from
  `picks_<season>` + `pick_changes`. Mirrors all the normalizers (PickSide,
  GradedResult, BatterLine, etc.) so results are interchangeable. Server-side
  only.
- **dashboard/lib/board.ts (modified)** — `loadBoard(iso)` now tries Supabase
  first, falls back to CSV when Supabase is unconfigured / unreachable / has
  no rows for that date. Available-dates list is merged from BOTH sources so
  the date picker stays correct during the Phase-1.5 transition.
- **dashboard/lib/useSupabaseRealtime.ts** — client hook subscribing to
  `postgres_changes` on `picks_<season>` / `pick_changes` / `live_game_state`
  for the displayed date. Fires a callback that triggers `/api/board` refetch.
  Auto-skips on past dates and when env vars are missing.
- **dashboard/components/DashboardShell.tsx** — wires `useSupabaseRealtime` into
  the existing 30s/90s polling loop. Polling stays as heartbeat fallback;
  Realtime push is now the primary update path.
- **package.json** — `@supabase/supabase-js@^2.45.0` (~60kB on the main route).
- **Vercel env vars** — `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  set on the production + preview environments.

### Added — Phase 4: Railway live game-state worker (T2.32 / `a2b8410`)

- **workers/live_state.py** — long-running Railway worker. Single sync loop,
  10s tick cadence during active hours (10am-2am ET), 5min quiet sleep
  outside. Diff-skips unchanged games (caches last-seen state per `game_pk`,
  upserts only on real change). One MLB schedule call per tick with
  `hydrate=linescore,team` (the `team` hydrate is what gets us 3-letter
  abbreviations — without it we'd be writing `?` placeholders). Graceful
  SIGTERM so Railway's deploy-rollover doesn't mid-write a row.
- **Procfile** — `worker: python workers/live_state.py` (default service).
- **railway.json** — Nixpacks builder, ALWAYS restart with 10 retries.
- **Migration** — `live_game_state_auto_bump_updated_at`. Adds an UPDATE
  trigger so `updated_at` advances on every write. Without this, the
  default `NOW()` only fires on INSERT, so the dashboard couldn't tell
  when fresh push arrived.
- **Refactored `dashboard/lib/useLiveGameState.ts`** — two branches:
  Supabase Realtime (preferred when env vars set; initial SELECT then
  subscribe + merge events into local state); `/api/live-state` polling
  (back-compat fallback). Same return shape (`byGamePk` + `byTeam`) so
  every consumer keeps working with no changes. Worst-case score
  freshness dropped from 30 sec polling to ~10 sec push.

### Added — Phase 3: Railway predictor loop (T2.33 / `7925fe6`, fix `8dd0cb7`)

- **workers/predictor_loop.py** — runs the full predict + grade + scrape +
  import-odds flow every 5 minutes during active hours (9am-2am ET).
  Subprocess-based: each step shells out to the existing scripts
  (`mlb_first_inning_predictor.py`, `scrape_dk_odds.py`), so no code
  duplication — features added to those scripts pick up automatically on
  next deploy. Per-step timeouts (180s grade, 300s predict, 120s
  scrape+import) so a stuck MLB API call can't wedge the whole loop.
  Predict is hard-fail (aborts the cycle); grade / scrape / import are
  soft-fail. Smoke-tested locally: full cycle in 85s.
- **Second Railway service** in the same project (`capable-nourishment`).
  Custom Start Command override: `python workers/predictor_loop.py`. Same
  `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` env vars.
- **railway.json fix** — removed `startCommand` from the file. It was
  overriding the UI's Custom Start Command, so the predictor service was
  silently running `live_state.py` (both services were doing the same
  thing). Procfile now drives the default; UI overrides take effect for
  per-service customization.

### Added — Phase 6: Installable PWA (T2.34 / `8dd0cb7`)

- **dashboard/public/manifest.json** — name / short_name / theme color
  matching the in-app phosphor green (`#5dff9a` on `#07090b`). Standalone
  display, portrait orientation, shortcuts to Today + History.
- **Icons**: `icon-192.svg`, `icon-512.svg`, `icon-maskable.svg`,
  `apple-touch-icon.svg` — all phosphor-diamond mark over near-black,
  matching the in-app brand. Maskable variant has 60% safe-zone for
  Android adaptive icon clipping.
- **dashboard/public/sw.js** — service worker. Pre-caches shell on install.
  Network-first for `/api/*` (live data, never serves stale). Cache-first
  for `/_next/static/*` + immutable assets. Stale-while-revalidate for
  HTML (instant boot, refresh-in-background). Old caches purged via
  versioned cache name. `push` + `notificationclick` handlers stubbed
  for future Web Push (currently no-op since Telegram covers mobile).
- **dashboard/app/layout.tsx (modified)** — Next.js 14 metadata: manifest
  link, applicationName, appleWebApp config, viewport.themeColor for
  light + dark, viewport-fit cover for iPhone notch. Service worker
  registration in a deferred-load script so it doesn't block FCP.

### Fixed — Dashboard SSR was caching Supabase responses (T2.35 / `91d094c`)

Symptom: `/api/board` returned fresh Supabase data (latest `generatedAt`
timestamp matching the most recent Railway predictor write), but the
SSR page (`/?date=...`) served stale data with a `generatedAt` matching
the last GHA cron commit's CSV mtime — ~1.5 hours old.

Root cause: Next.js 14 wraps the global `fetch` in server components
with its data-cache layer. `dynamic = "force-dynamic"` only prevents
*route* caching; fetches inside server components are STILL memoized
for the build's lifetime unless either:
- The component declares `fetchCache = "force-no-store"`, or
- Each fetch passes `cache: "no-store"`, or
- The route declares `revalidate = 0`.

`/api/board` is a Route Handler with `revalidate = 0` (immune).
`/` page only had `dynamic = "force-dynamic"` (vulnerable).

Fix at two layers:
1. **dashboard/app/page.tsx** — added `export const fetchCache = "force-no-store"`.
2. **dashboard/lib/supabase.ts** — wrapped the server client's fetch with
   a `cache: "no-store"` override so any future page using
   `loadBoardFromSupabase` is immune by default, no per-page flag needed.

### Fixed — PASS-row OddsChip was clipping (T2.29 / `2de0c3a`)

The dual-side PASS chip ("DK · N -130 · Y +100") natural width is
~160px, but `dashboard/components/BoardRow.module.css` had the odds
column at `minmax(150px, 0.5fr)` — at 1281px desktop it resolved to
~159px. Combined with `.row { overflow: hidden }` and `.oddsChip
{ flex: 0 0 auto }`, the chip's right edge was silently clipped on
every PASS row.

Fix: rebalance within the *same* 1236px min-width budget (no breakpoint
shifts). Bumped odds to `minmax(172px, 0.6fr)`, trimmed pick to
`minmax(240px, 0.95fr)`, added 4px to the caret track for breathing
room from the YRFI% number. At 1281px the odds column now lands at
~184px — comfortable for the dual-side chip with breathing room.

### Changed — Production dashboard URL renamed

User asked for a cleaner, more memorable production URL than the
auto-generated `dashboard-pink-seven-64.vercel.app`.  Added a new
Vercel domain alias **`nrfi-terminal.vercel.app`** to the
`mlb-nrfi-yrfi` project and made it the primary URL going forward.

The old `dashboard-pink-seven-64.vercel.app` URL stays live as a
secondary alias so existing bookmarks / deep links / Telegram
references don't break.  Both serve the same Vercel deployment;
either resolves to the same SSR + Realtime stack.

Updated all internal doc references (`CLAUDE.md`, `docs/KB.md`) to
use the new URL.  The dashboard itself doesn't hardcode its URL
anywhere meaningful — the bookmark/share text just gets shorter.

### Fixed — Telegram pick-flip pings missing from Railway runs (T2.36 / `442fe4d`)

User reported "the Telegram notifications are not live and it's already
missed a couple."  Two compounding bugs:

1. **Railway predictor service was missing TELEGRAM secrets.**  Phase 3
   (Railway predictor every 5 min) was deployed earlier today with
   `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` env vars but NOT
   `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.  Result: any flip
   detected between hourly GHA cron firings produced zero pings.
   `pick_changes` table for 2026-05-02 17:00 UTC showed 6+ actionable
   flips in the past hour, all from Railway, all silent.

2. **Duplicate flip rows from Railway + GHA racing.**  Both runners
   write to the same Supabase `pick_changes` table when they detect
   a flip relative to their own local CSV state.  Once Railway also
   had the Telegram secrets, every flip would have fired 2-4 pings.

Code fix (this commit, addresses #2):
- `_notify_pick_flip_telegram` now queries Supabase for any prior
  pick_changes row with the same `(date, game_pk, new_pick_label)`
  within the last 5 minutes.  By the time the function runs,
  `_record_pick_change` has already inserted THIS runner's row, so
  `count >= 2` means another runner is ahead of us → skip.
  Fail-OPEN on any error (network / module missing) so a transient
  Supabase hiccup never silently drops a legitimate ping.
- New `_flip_category(old, new)` helper classifies the flip into
  `commit` / `demote` / `side` with appropriate emoji.
- New `_format_flip_message` builds a richer HTML-formatted body:
  category headline, P(NRFI)/P(YRFI) probability line, hyperlink
  to the dashboard (`<a href="https://nrfi-terminal.vercel.app/?date=...">`).
  Telegram parse_mode=HTML + disable_web_page_preview so URL
  preview cards don't push content below the fold.
- `DASHBOARD_URL` env var override (defaults to
  `https://nrfi-terminal.vercel.app`) so preview deploys can point
  at a non-prod URL.
- `log_picks` callsite passes `game_pk` + `row_context` (with
  nrfi_prob / yrfi_prob) so the notifier has dedup keys + body
  context.

Manual step (#1 above) now also done: with explicit user
permission, retrieved both secrets via Telegram Web (BotFather
`/mybots` for the bot token; `localStorage.user_auth` for the
user's chat ID), verified end-to-end with a live test ping, then
pasted both into the Railway predictor service Variables panel
via Raw Editor.  Test ping was received in the user's NRFI
Terminal chat.  Predictor service redeployed with the full set of
4 env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.  Temp files holding the
secrets in `$env:TEMP` were wiped + clipboard cleared after the
Railway paste landed.  Next actionable pick flip detected by
Railway should produce exactly one ping (no duplicates between
runners thanks to the Supabase dedup query).

### Changed — Telegram pings now STRONG-only (T2.37)

User feedback: "I don't want any telegram notifications for passes
or anything like that.  The only Telegram notifications I want is
when there's a strong pick."

The previous filter (`_is_actionable_label`) considered both LEAN
and STRONG as actionable.  T2.37 tightens it to STRONG only:
`_notify_pick_flip_telegram` now bails when the NEW label is not
STRONG NRFI or STRONG YRFI.

Pings that DO fire after this change:
  • PASS / pending → STRONG NRFI       (commit; the most common one)
  • PASS / pending → STRONG YRFI       (commit)
  • LEAN → STRONG (same side)          (promotion; high signal)
  • STRONG NRFI → STRONG YRFI          (side flip; rare, high impact)

Pings that get filtered (silent):
  • Anything → LEAN  (LEAN as final state -- skip)
  • Anything → PASS  (demote / no-edge -- skip)
  • PASS-variant churn (LINEUP↔STARTER↔NO EDGE) -- skip
  • STRONG → LEAN / PASS (demotes -- user already saw the STRONG
    ping; demote is just noise)

Tested against all 11 known label variants; all classified correctly.

### Added — 8 new STRONG-only Telegram event types (T2.38)

User asked: "implement all of those" referring to the 7 additional
Telegram notification ideas brainstormed earlier (the user already
restricted pings to STRONG-only via T2.37).

This commit ships a unified notifier framework + 8 event types
on top of the existing flip ping:

  Shared infrastructure:
    • New Supabase `notifications_log` table with RLS + indexes.
      Records every (event_type, event_key, body, delivered) tuple
      so future runs can dedup against it.  Migration applied.
    • New `_notify_event_telegram(event_type, event_key, body)`
      dispatcher in tracker.py.  Three-step flow per event:
        1. Dedup query against notifications_log (window per event_type)
        2. Send via _send_telegram_html (HTML body + suppressed preview)
        3. Record to notifications_log for audit + future dedup
      Fail-OPEN at every layer so a Supabase / Telegram outage never
      silently drops a real signal AND never breaks the predictor.
    • `_DEDUP_WINDOW_M` map per event_type:
        flip_to_strong       5 min
        strong_graded        24 h
        strong_voided        24 h
        strong_pregame       6 h
        strong_clv           24 h
        strong_weather       6 h
        bankroll_milestone   90 days
        daily_digest         18 h
        ops_health           1 h

  New event types (all STRONG-only, all with bet_placed=Y guards
  except daily_digest / bankroll_milestone / ops_health):

    #1 strong_graded     — fires when a STRONG bet is graded WIN/LOSS.
       Body: ✅/❌ icon, side, score line, P&L, today record.
       Trigger: tracker.grade_date final-grade branch.

    #3 strong_voided     — fires on POSTPONED / SUSPENDED for a
       STRONG bet.  Body: ⚠️ + units returned + no grade recorded.
       Trigger: tracker.grade_date POSTPONED / stale-scheduled branches.

    #6 bankroll_milestone — fires when season P&L crosses ±10/25/50/
       75/100/150/200/300/500u.  Body: 🏆 + record + season P&L + hit rate.
       Trigger: tracker.grade_date after any new grade lands.

    #4 daily_digest      — once-per-slate end-of-day wrap.  Body: 🌙 +
       today record + today P&L + season totals + tomorrow slate count.
       Trigger: tracker.grade_date when ALL of today's games are
       terminally graded AND the slate date == today ET.

    #2 strong_pregame    — 30-min-before-first-pitch reminder for a
       placed STRONG bet.  Body: ⏰ + DK price + edge + units + "last
       call".  Trigger: predictor_loop.step_pregame_alert_check —
       sweeps today's CSV after each cycle, fires when delta to first
       pitch is in [25, 35] minutes.

    #5 strong_clv        — fires when DK shifts ≥5pp toward our pick
       on a placed STRONG bet (positive CLV signal).  Body: 💸 +
       opened% → now% + delta.  Trigger:
       tracker._apply_odds_to_row before the bet-time-lock early-return.
       Doesn't update market_*_odds — those stay locked per T2.23.

    #7 ops_health        — fires when predictor hasn't written to
       picks_<season>.updated_at in ≥30 min.  Body: 🚨 + stall age +
       "check Railway / GHA logs."  Trigger: live_state worker, every
       10 cycles (~100s during games, ~50min during quiet hours).

    #8 strong_weather    — fires when wx_wind_kmh shifts ≥5 km/h, or
       wx_temp_c ≥5°C, or wx_humidity ≥20pp from the bet-time values
       on a placed STRONG bet.  Body: 🌬 + summary + "informational
       only — bet locked."  Trigger: tracker.log_picks when an existing
       bet_placed=Y STRONG row is updated with materially different
       wx_*.

  Existing T2.36/T2.37 flip notifier was refactored to use the
  unified dispatcher (no behavior change; just plumbing).

Smoke test: dry-run rendered all 9 event types with realistic
sample data; bodies parse correctly, hyperlinks well-formed,
icons display properly, dedup keys are unique per (event_type,
deterministic key).  AST parse clean across tracker.py +
workers/live_state.py + workers/predictor_loop.py.

The Railway predictor + live-state services already have
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars (T2.36).  Next
deploy picks up the new notifier code automatically.

### Tested & rejected — Pitcher days-rest feature (T2.39)

User asked to ship pitcher days-rest as a model feature
(per `ROADMAP.md` Tier 1 #2).  Investigation:
- `backfill_days_rest.py` already exists at the repo root.
- 2024 and 2025 backtest CSVs already have `away_days_rest` +
  `home_days_rest` columns (from a prior run).
- `picks_2026.csv` does NOT have these columns (`tracker.FIELDS`
  filters them out on every `_write_rows`).
- The production `data/lr_model.json` has 11 features; none is
  rest-related.

Wrote `test_days_rest.py` (mirrors `test_era_gap.py` template) and
ran a 2-split cross-validation (2024→2025, 2025→2024).  Skipped
the 2026 holdout split because picks_2026 lacks the columns; 2-way
cross is sufficient to gate a model change.

4 variants tested:
- `+rest_raw`         — pitcher's own days-rest in their half
- `+rest_short_flag`  — 1 if rest ≤ 4 days, else 0
- `+rest_signed_gap`  — opposing rest − own rest per half
- `+rest_raw+short_flag` — both

Results vs baseline (sum P&L across both splits at flat -110):
- baseline:                +69.7u
- +rest_raw:               +32.1u    (-37.6u)  ❌
- +rest_short_flag:        +48.3u    (-21.4u)  ❌
- +rest_signed_gap:        +77.9u    (+8.2u)   below ship bar
- +rest_raw+short_flag:     +9.8u    (-59.9u)  ❌

Best variant (`+rest_signed_gap`) gained only +8.2u P&L, below
the +10u ship bar, AND regressed STRONG YRFI hit rate from 61.9%
to 58.8% on the 2024→2025 split.  Per `CLAUDE.md` test methodology
("reject any feature that helps in only one direction" + "STRONG
hit rates don't drop on holdout"), this is a clear reject.

Logged the result in `docs/KB.md` "What's NOT in the model" so
future Claude / human sessions don't retest unless they have a
fundamentally different feature-engineering approach.  Test
artifact `test_days_rest.py` kept in the repo for posterity (same
treatment as `test_era_gap.py`).

Mechanism interpretation: rest signal isn't separable from the
FIP/ERA/last-5 features the model already uses.  A pitcher on
short rest pitches worse, which manifests as higher FIP / lower
last-5 NRFI rate already; the explicit rest variable adds noise.

### Added — Pre-game scratch detector (T2.40)

User picked Tier 1 #4 from `ROADMAP.md` after the days-rest model
feature failed validation.  Goal: detect when a starter scratches
before first pitch on a placed STRONG bet, alert the user, and
let the next predictor cycle recompute with the replacement.

Implementation extends the existing Phase-4 live-state worker
(already polling MLB Stats API every 10s) so we don't add a new
Railway service for this:

- `workers/live_state.py` schedule call: `hydrate=linescore,team`
  → `hydrate=linescore,team,probablePitcher`.  The probable-pitcher
  hydrate adds `teams.{away,home}.probablePitcher.{id,fullName}`
  per game.  Zero extra round-trips.
- `parse_game()` now also extracts:
    `_probable_away_id`, `_probable_home_id`,
    `_probable_away_name`, `_probable_home_name`
  Leading-underscore prefix marks them as "internal worker fields"
  not part of the `live_game_state` table schema.
- New `_strip_internal_fields()` helper drops `_*` keys before the
  Supabase upsert so PostgREST doesn't reject the row with a 400.
- `state_signature()` is unchanged, so the diff-skip cache still
  triggers off the user-visible game state — a change in probable
  pitcher alone doesn't push a no-op live_game_state update.
- `run_cycle()` return shape extended to also yield the FULL row
  list (with `_probable_*` retained) so downstream consumers
  (check_scratches) can use them.
- New `check_scratches()` function:
    1. Filters fetched slate to pre-game games only (Preview state)
    2. Queries Supabase picks_<season> for today's STRONG +
       bet_placed=Y rows (`pick_strength=STRONG AND bet_placed=Y`)
    3. For each match, compares our recorded `away_pitcher_id` /
       `home_pitcher_id` to the live `_probable_*_id`
    4. On any mismatch, fires `_notify_strong_scratch_telegram(...)`
- Throttle: scratch check runs every 6 cycles (~60s in active mode,
  ~30min in quiet mode) to avoid pounding Supabase + MLB API.
- `notifications_log` 6h dedup ensures even with the throttle the
  user sees at most one ping per game per scratched side.

New notifier function in `tracker.py`:
- `_notify_strong_scratch_telegram(row, scratched_side, original_name,
  replacement_name)` -- standard T2.38 framework, message body:
    ⚠️ Starter scratched · STRONG NRFI
    ARI @ CHC · 8:05 PM ET
    AWAY starter: Trevor Williams → **Slade Cecconi**
    Bet stays locked at the original prediction (T2.25); next predictor
    cycle will recompute with the new starter.
    View on dashboard →

  Includes the existing T2.38 dedup (event_type=`strong_scratch`,
  event_key=`strong_scratch:{game_pk}:{side}`).  6-hour window
  per side per game.  Self-filters non-STRONG / non-bet-placed rows.

Notification framework `_DEDUP_WINDOW_M` gains `"strong_scratch":
6 * 60`.

Smoke test:
- AST clean across `tracker.py` + `workers/live_state.py`
- Format render: scratch-alert message renders correctly with
  bold pitcher name, hyperlink to dashboard
- `python workers/live_state.py --once` exits clean: 15/15 games
  pushed to Supabase (proving `_probable_*` strip works -- the
  upsert would 400 otherwise), check_scratches + check_ops_health
  both ran silently in the same cycle
- Supabase live_game_state row `updated_at` advanced from old to
  7 sec ago, confirming the upsert path works with the new fields

Edge cases handled:
- TBD pitcher (`our_away == 0` or `our_home == 0`): skip, don't
  alert -- those rows haven't had a real pitcher recorded yet.
- Probable-pitcher not yet posted by MLB: skip, wait for next cycle.
- Game in progress / Final: skip, scratch is moot once first pitch
  has happened.
- Doubleheader: each game-pk is checked independently; only the
  affected game alerts.

What's NOT in this iteration (could ship later):
- Dashboard visual badge on rows where the locked-in pitcher no
  longer matches the live probable.  Currently the alert is
  Telegram-only.
- Auto-trigger an immediate predictor re-run when a scratch is
  detected.  Currently the user just waits ≤5 min for the next
  Railway predictor cycle.

### Fixed — Duplicate flip-to-strong Telegram ping (T2.41)

User reported a duplicate Telegram message for a flip-to-strong
event.  Investigation in `notifications_log`:

  flip_to_strong:822746:STRONG YRFI  fired 2x
    18:37:06 UTC  ←  Railway predictor cycle
    18:43:10 UTC  ←  GHA cron OR another Railway cycle
    span: 364 sec (6 min, 4 sec)

`pick_changes` table for game_pk=822746 (MIL@WSH) showed the same
PASS - Lineup pending → STRONG YRFI transition logged TWICE, 6 min
apart -- exactly the cross-runner race documented in the original
T2.36 design note: Railway and GHA each maintain independent local
CSV state, so each can detect the same flip from its own pre-state.

The original 5-min dedup window was a hair too short to absorb a
race + cycle-drift case.  Bumped `flip_to_strong` window from
5 min → 24h.  Semantics now match user expectation: one ping per
(game_pk, side) per day, regardless of how many times the pick
churns through PASS/LEAN states.  If the pick later demotes and
re-commits hours later, the bet is already locked at the first
commit (T2.25), so the re-ping adds no information the user
needs.

Other event types' dedup windows are unchanged.

### Added — Pre-game scratch detector (T2.40)

User picked Tier 1 #4 from `ROADMAP.md` after the days-rest model
feature failed validation.  Goal: detect when a starter scratches
before first pitch on a placed STRONG bet, alert the user, and
let the next predictor cycle recompute with the replacement.

Implementation extends the existing Phase-4 live-state worker
(already polling MLB Stats API every 10s) so we don't add a new
Railway service for this:

- `workers/live_state.py` schedule call: `hydrate=linescore,team`
  → `hydrate=linescore,team,probablePitcher`.  The probable-pitcher
  hydrate adds `teams.{away,home}.probablePitcher.{id,fullName}`
  per game.  Zero extra round-trips.
- `parse_game()` now also extracts `_probable_*_id` /
  `_probable_*_name` per side (leading underscore marks them as
  internal worker-only fields, not part of the live_game_state
  table schema).
- New `_strip_internal_fields()` helper drops `_*` keys before the
  Supabase upsert so PostgREST doesn't reject the row.
- `state_signature()` is unchanged, so the diff-skip cache still
  triggers off the user-visible game state — a probable-pitcher
  change alone doesn't push a no-op live_game_state update.
- `run_cycle()` return shape extended to also yield the FULL row
  list (with `_probable_*` retained) for downstream consumers.
- New `check_scratches()` function:
    1. Filters fetched slate to pre-game games only (Preview state)
    2. Queries Supabase picks_<season> for today's STRONG +
       bet_placed=Y rows
    3. Compares our recorded `away_pitcher_id` / `home_pitcher_id`
       to the live `_probable_*_id`
    4. On any mismatch, fires `_notify_strong_scratch_telegram(...)`
- Throttle: scratch check runs every 6 cycles (~60s in active mode).
- 6h dedup window per (game, side) so the same scratch doesn't
  re-ping across multiple cycles.

New notifier function `_notify_strong_scratch_telegram(row,
scratched_side, original_name, replacement_name)`.  Standard T2.38
framework.  Body example:
  ⚠️ Starter scratched · STRONG NRFI
  ARI @ CHC · 8:05 PM ET
  AWAY starter: Trevor Williams → **Slade Cecconi**
  Bet stays locked at the original prediction (T2.25); next predictor
  cycle will recompute with the new starter.

`_DEDUP_WINDOW_M["strong_scratch"] = 6 * 60`.

Smoke test:
- AST clean across `tracker.py` + `workers/live_state.py`
- Format render: scratch-alert message renders correctly with bold
  pitcher name + hyperlink to dashboard
- `python workers/live_state.py --once` exits clean: 15/15 games
  pushed to Supabase (proving the `_probable_*` strip works); both
  check_scratches and check_ops_health ran silently
- Supabase live_game_state row `updated_at` advanced to 7s after
  the test, confirming the upsert path works with the new fields

Edge cases:
- TBD pitcher: skipped (false-positive guard)
- Probable-pitcher not yet posted: skipped, wait for next cycle
- Game in progress / Final: skipped (scratch is moot post-first-pitch)
- Doubleheader: each game_pk independently checked

What's deferred (could ship later):
- Dashboard visual badge on rows where the locked pitcher diverges
  from the live probable.
- Auto-trigger an immediate predictor re-run on scratch detection
  (currently the user just waits ≤5 min for the next Railway cycle).

### Added — Bankroll equity curve on /history (T2.42)

User picked Tier 2 #6 from `ROADMAP.md`.  Adds a dedicated equity-
curve view above the existing daily-breakdown chart on the
`/history` page.  Pure SVG, no new charting library.

What's on screen:

  • **Equity line** — bold phosphor-green stroke with a soft halo,
    drawn over a translucent area fill below the line.  Y-axis pinned
    to include zero so "where we started" is always visible.
  • **All-time-high watermark** — dashed horizontal line at the peak
    + a phosphor diamond marker at the date the peak occurred.
  • **Drawdown shading** — red-tinted polygons rendered between the
    running peak and the equity line wherever we're below ATH.  Each
    contiguous drawdown segment is its own polygon so the shading
    cleanly disappears when we're back at ATH.
  • **Current-point marker** — solid dot at the latest cumulative
    value.
  • **Stats panel** under the chart, six cells:
      Bankroll · All-time high · Max drawdown · Current drawdown ·
      Volatility · Sharpe (annualized).
    Sharpe uses per-day mean / stdev × √252 for the annualization
    convention bettors recognize.  Max drawdown shown as both raw
    units and % of peak.

`computeEquityStats(days)` is a pure helper that computes:
  - peak / peakDate / trough / troughDate
  - maxDrawdown (units) + maxDrawdownPct
  - currentDrawdown / currentDrawdownPct
  - daysAtAth (count of days where cum == running peak)
  - vol (per-day stdev) and sharpe (mean/vol × √252)

Single-pass O(n) over the days array.

Files:
  - `dashboard/components/HistoryView.tsx` — new `EquityCurveChart`
    component + `computeEquityStats` helper, inserted before the
    existing `PnlChart`.  Renamed the daily chart's section from
    "Equity curve · daily" → "Daily breakdown" since this new view
    is the proper equity curve.
  - `dashboard/components/HistoryView.module.css` — new classes
    `.equityArea`, `.equityLine`, `.equityPeakLine`,
    `.equityPeakMarker`, `.equityCurrentMarker`, `.equityDrawdown`,
    `.equityStats`, `.equityStatCell`, `.equityStatLabel`,
    `.equityStatBig`, `.equityStatSub`, plus legend-variant tokens
    `.legendSwatch[data-tone="drawdown"]`, `.legendDot[data-tone="peak"]`,
    `.legendLine[data-tone="equity"]`.  Stats panel collapses
    6 → 3 cols at 980px and 6 → 2 cols at 600px.

Bundle impact: `/history` route 5.3kB → 6.7kB (+1.4kB).  No new
deps — pure SVG.

The 7d / 30d / season window selector at the top of the page works
unchanged; switching window re-fetches `/api/roi?window=...` and
the equity chart recomputes with the narrower data set.  Stats
re-derive automatically from the filtered rows.

### Added — Multi-recipient Telegram broadcast (T2.43)

Lets the same Telegram notifications fan out to multiple chats from
a single env var.  Created a Telegram group named **"Backfist Bets"**
so the operator can add friends and have them receive every alert
the operator sees, with no extra wiring per recipient.

`tracker._send_telegram_html` now treats `TELEGRAM_CHAT_ID` as a
**comma-separated CSV** instead of a single id.  Each entry can be:
  • a positive int — DM to a person       (e.g. `5285688562`)
  • a negative int — group / channel      (e.g. `-5115372935`)

Per-recipient delivery loop with **soft fail**: one bad chat_id (bot
kicked from a group, chat blocked, etc.) does NOT prevent delivery
to the other recipients.  Returns `True` if at least one delivery
succeeded.  Back-compat: a single chat_id with no comma still works
unchanged.

The dedup framework from T2.38 (`notifications_log` Supabase table)
is per-event-type, not per-recipient — so each of the 8 STRONG-only
event types still fires at most once per dedup window, but the ping
goes to all recipients atomically.

Operationally, three places store the chat_id and all three were
updated to the CSV `5285688562,-5115372935`:
  - Railway predictor service (`MLB-first-inning`) — env var
  - Railway worker service (`worker`) — env var
  - GitHub Actions repo secret `TELEGRAM_CHAT_ID` — for the daily
    backup predictor that still runs in GHA

The bot (`@nrfi_terminal_bot`) was added to the "Backfist Bets"
group as a member.  No admin permissions required for read-only
broadcast use.

Files:
  - `tracker.py` — `_send_telegram_html` rewritten for CSV fan-out
    (45 +/-, 24 -).  Function-level docstring documents the contract.

Live verification: a manual `sendMessage` call (one round-trip per
recipient) returned `ok=True message_id=24` to the personal chat
and `ok=True message_id=25` to the Backfist Bets group.

### Added — Multi-recipient Telegram broadcast (T2.43)

Lets the same Telegram notifications fan out to multiple chats from
a single env var.  Created a Telegram group named **"Backfist Bets"**
so the operator can add friends and have them receive every alert,
with no extra wiring per recipient.

`tracker._send_telegram_html` now treats `TELEGRAM_CHAT_ID` as a
**comma-separated CSV** instead of a single id.  Each entry can be:
  • a positive int — DM to a person       (e.g. `5285688562`)
  • a negative int — group / channel      (e.g. `-5115372935`)

Per-recipient delivery loop with **soft fail**: one bad chat_id (bot
kicked, chat blocked, etc.) does NOT prevent delivery to the others.
Returns `True` if at least one delivery succeeded.  Back-compat: a
single chat_id with no comma still works unchanged.

The dedup framework from T2.38 (`notifications_log` Supabase table)
is per-event-type, not per-recipient -- each of the 8 STRONG-only
event types still fires at most once per dedup window, but the ping
goes to all recipients atomically.

Three env stores synced to `5285688562,-5115372935`:
  - Railway predictor service (`MLB-first-inning`)
  - Railway worker service (`worker`)
  - GitHub Actions repo secret `TELEGRAM_CHAT_ID`

The bot (`@nrfi_terminal_bot`) added to the "Backfist Bets" group.
Live verified: a manual `sendMessage` returned `ok=True` to both
the personal chat and the group.

### Added — xERA disambiguation + per-feature hover tooltips (T2.44)

User confusion case: looked at TEX@DET and saw "Home pitcher ERA
2.340" in the Why-this-pick panel while the player card showed ERA
4.20 -- read the lowercase "x" out of "xERA" as a typo.  The two
numbers don't conflict: 2.340 is Statcast xERA (top model factor on
the row, contribution -0.7165 toward NRFI), 4.20 is the raw season
ERA on the card.

Two unmissable fixes in `dashboard/components/GameDetails.tsx`:

  1. Re-labeled `home_xera` / `away_xera` from "Home pitcher xERA"
     to "Home pitcher xERA (Statcast)" in `prettyFeatureName`.

  2. New `featureTooltip(name)` helper providing one-sentence
     plain-English descriptions for every LR feature.  Wired as a
     native `title=""` on each row's name span -- hover (desktop)
     and long-press (mobile), no library, no JS.  xERA tooltip
     explicitly calls out the Statcast vs raw-ERA distinction.

Covers all ~30 features in the prettyFeatureName map: park rate,
FIP, OBP/SLG/ISO top-3 splits, last-5/last-10 starter NRFI rates,
ump zone NRFI rate, xERA, whiff-rank, ERA gap (T1/B1), pvt career
NRFI, IP/start, weather inputs.

### Added / Changed / Fixed — Post-audit hardening (T2.45)

Five fixes synthesized from a three-agent audit (model+tracker,
dashboard, workers+ops) plus per-claim verification against actual
code.  Surface area: silent-failure detection + dead-UI cleanup.
None of these change pick logic.

#### Removed — dead First-inning split UI

Both readers (`board.ts` CSV / `board-supabase.ts`) supplied
0/null for `fiEra` / `fiWhip` / `fiIp`.  The picks_2026 schema
doesn't have `fi_era` / `fi_whip` / `fi_ip` columns, and the
Supabase reader hardcoded zeros.  `GameDetails.tsx` then
conditionally rendered the section only when `fiIp > 0`, so it
**never rendered for any row, ever**.

Deleted from: `types.ts` (interface), `board.ts` +
`board-supabase.ts` (readers), `GameDetails.tsx` (block),
`GameDetails.module.css` (.fiIp class).  Net -32 LOC.

If this feature is wanted in the future, the path is: backfill
per-pitcher first-inning splits via MLB Stats API
(`/api/v1/people/{id}/stats?stats=statSplits&group=pitching`) ->
new columns `away_fi_era`/`home_fi_era` etc -> repopulate the
type interface + readers + section.  ~4-6 hr.

#### Added — notifications_log DDL in schema.sql

The T2.38 dedup framework writes to `notifications_log` and the
production Supabase project has the table (verified: 17 rows),
but the DDL was never added to `db/schema.sql`.  A future fresh
deploy on a new Supabase project would silently fail-open on
dedup checks -> duplicate Telegram alerts everywhere.

Added: `CREATE TABLE notifications_log (id, captured_at_utc,
event_type, event_key, chat_id, body, delivered)`,
`idx_notifications_dedup` on `(event_type, event_key,
captured_at_utc DESC)` for the hot dedup query,
`idx_notifications_recent` on `(captured_at_utc DESC)` for
audit reads, RLS enable, anon + authenticated SELECT policies.
Idempotent -- safe to re-run on the production project.

#### Added — Railway worker errors -> system_errors

GHA cron records every step's failure to `system_errors` via the
`record_err` helper in `daily.yml`.  The Railway predictor +
live-state workers logged failures only to stderr, so the
dashboard's planned ops-health story showed "all green" while
the worker was silently degraded.

  - `workers/predictor_loop.py`: `_record_step_failure()` helper
    lazy-imports `db.supabase_writer.mirror_system_error` (so the
    worker boots even without supabase-py).  Wired into `cycle()`
    for every non-zero RC: grade-yesterday, grade-today, predict,
    scrape-dk, import-odds, pregame-alert.
  - `workers/live_state.py`: `_record_step_failure()` reuses the
    worker's existing Supabase client (saves an import).  Wired
    into the live-state upsert path and the scratch-detector
    `picks_<season>` select path.

Both helpers fail-open per the worker resilience contract: a
Supabase outage cannot escalate into a worker crash.

#### Fixed — TELEGRAM_CHAT_ID format validation

The T2.43 multi-recipient broadcast splits the env var on commas
and trims whitespace, but never validated each entry's shape.  A
malformed value like `"5285688562, , -5115372935"` or
`"5285688562 garbage"` would survive the strip + filter and
reach Telegram's API as an opaque 400.

Added a `/^-?\d+$/` regex check in `tracker._send_telegram_html`;
malformed entries are dropped with a structured stderr warning
naming each rejected value, and broadcast continues to the valid
recipients.  Bot DMs (positive int) and groups (negative int,
with optional `-100` supergroup prefix) are both accepted.

#### Fixed — picks upsert: per-batch retry + system_errors record

Old behavior in `db.supabase_writer.mirror_picks`: a single
try/except wrapped the whole batch loop, so if batch 1 succeeded
(200 rows) but batch 2 failed, the function returned 0 (losing
the partial success signal) AND skipped batches 3, 4, ...  A
transient blip on one batch silently lost rows for the rest of
the cycle.

New behavior:
  - Each batch gets its own try/except + up-to-3 attempts with
    simple linear backoff (0.5s, 1.5s).
  - Persistent batch failures inline-insert to `system_errors`
    (NOT a recursive `mirror_system_error` call -- avoids a
    feedback loop if Supabase itself is the failing target).
  - Returns the actual count successfully upserted (no longer
    all-or-nothing).
  - Subsequent batches still proceed after a failed batch.

Result: the dashboard's ops health surfaces real Supabase write
degradation in near-real-time, and partial successes stop being
silently retried-from-scratch on the next cycle.

### Operations / runtime services — current state

| Service | Where | Cadence | What it does |
|---|---|---|---|
| Predictor (primary) | Railway (`capable-nourishment` / MLB-first-inning) | every 5 min, 9am-2am ET | predict + grade + scrape DK + import odds → Supabase |
| Live game-state | Railway (`capable-nourishment` / worker) | every 10s, 10am-2am ET | poll MLB Stats API → live_game_state |
| Predictor (backup) | GHA cron `daily.yml` | every UTC hour 12-23 + extras | same as primary; commits CSVs to git for archival |
| Daily backup snapshot | GHA cron `backup.yml` | 5am ET | snapshot CSVs → `data/backups/<DATE>/` |
| Vercel rebuild | Vercel CI on git push | per cron commit | rebuilds dashboard with copied CSV state (legacy fallback path) |

### Risks / known issues

- **Two predictors writing to Supabase in parallel** (Railway every 5 min, GHA
  every ~60 min). Both compute the same model on the same MLB data; race is
  benign because both upserts use ON CONFLICT (date, game_pk) and the
  bet-time pick lock prevents stomping placed bets. Not a problem in practice;
  worth a stronger leader-election mechanism if we ever see the cron lag
  compound.
- **Predictor service named "MLB-first-inning"** in Railway (auto-generated when
  added as second service). Cosmetic only; could rename to "predictor" for
  clarity.

---

## [2026-05-01] — Tier 1-4 Audit Cleanup

Single-day push that closed the Tier 1 / 2 / 3 audit (46 items) plus 14 of
the Tier 4 improvement items. 60/74 total audit items shipped. Predictor
behavior unchanged on the model side — the hardening is around durability,
operability, dashboard polish, and post-mortem visibility.

### Performance snapshot

| Window | Active picks (W-L) | Win rate | Notes |
|---|---|---|---|
| **Yesterday (4/30)** | **4-2** | **66.7%** | 11 games, 5 PASS, 1 actual bet placed (TOR@MIN NRFI -130, won) |
| Last 7 days  | 23-10 | 69.7% | One zero-pick day (4/28 was all-PASS) |
| April 2026   | 113-63 | 64.2% | NRFI side: 35-11 (76.1%), YRFI side: 78-52 (60.0%) |
| Season-to-date | 113-63 | 64.2% | 219 PASS picks, 3 postponed, 15 ungraded |

**Total P&L tracked across all picks at -110 fallback: +39.6u.** This is what
the dashboard `/history` TOTAL displays — `dashboard/lib/roi.ts:248-260`
falls back to flat -110 (+0.909 / -1.000) for any pick without an imported
real price, so every WIN/LOSS contributes regardless of whether DK odds
were captured. At a 64.2% hit rate, the model is well above the 52.4%
break-even line.

Real-odds P&L over the 4 bets where DK odds were imported AND edge cleared
the 2% threshold (`bet_placed=Y`): -0.49u (2W-2L). The gap between the
two numbers is purely a coverage problem: only 6 of 176 graded picks
have any odds at all, because the DK scraper landed on **2026-04-29**
(commit `f8dc174`) — we have no historical odds before that date and
DK doesn't expose them. Going forward, hourly scrapes during open-market
hours should bring coverage up; see the "Odds capture coverage fix"
entry below.

### Added — Operations / Monitoring

- **T3.1** `/api/health` endpoint returning OK / STALE / DEGRADED / BROKEN
  based on `thresholds.json` writtenAtUtc + recent `system_errors.csv` rows.
  Designed for Healthchecks.io / UptimeRobot pings.
- **T3.2** `ALERT_WEBHOOK_URL` cron-failure pings (Slack/Discord/ntfy compatible).
  Quiet no-op when secret is unset.
- **T3.4** New `.github/workflows/backup.yml` — daily 5am ET snapshot of
  picks/boards/pick_changes/thresholds/system_errors into `data/backups/<DATE>/`.
  Prunes older than 30 days. Commits + pushes.
- **T4.12** Healthchecks.io dead-man's-switch ping on cron success/fail.
- **T1.3** `system_errors.csv` ledger — every cron failure (predict, grade,
  scrape, odds-import) now logs structured rows + emits `::warning::` GitHub
  annotations.

### Added — Dashboard / UX

- **T3.10** `DataQualityBadge` per-row "!" chip when ANY input is on a fallback
  (TBD pitcher, league-avg offense, lineup not posted). Two severity tones.
- **T4.15** "Why this pick?" panel — top-5 LR feature contributions per half
  with signed bars + friendly names + raw values. New CSV columns
  `top_factors_t1_json` / `top_factors_b1_json` carry the data.
- **T4.16** `CalendarHeatmap` on /history — 7-row grid colored by day P&L.
- **T4.17** `ZoneHitRateChart` on /history — per-zone hit rate vs 52.4%
  break-even line.
- **T4.23** `CalibrationPlot` on /history — predicted vs actual hit-rate
  scatter with y=x reference.
- **T4.18** Pitcher-name search in the filter query (matches team OR pitcher).
- **T4.20** Browser notifications on pick flips (opt-in 🔕/🔔 toggle).
- **T4.22** Sort-by-result column header (W → L → PASS → PP → ungraded).
- **T4.24** Multi-row expand — pin 2+ games open simultaneously to compare.
- **T4.21** Sub-600px card layout with 44px touch targets (iOS HIG).
- **T4.28** CLV tracking — `opened_*_odds`, `clv_pct` columns; closing line
  value computed at grade-time on the picked side.

### Changed — Predictor / Tracker

- **T1.1** `tracker._write_rows` writes via tempfile + fsync + `os.replace`
  for atomic CSV swaps. Eliminates torn writes between racing cron firings
  and concurrent Vercel build reads.
- **T1.6** Push retry: 3 attempts → 8 with 5-30s jittered backoff. CSV-only
  conflicts auto-resolve via `--ours` (each cron is a complete recomputation).
- **T2.8** Cron schedule expanded to UTC 12-23 (every hour) so it covers
  both EDT and EST without manual DST shifting.
- **T2.11** FI weight cap now scales with sample size (25/40/55/65% at
  10/20/30/30+ FI IP). A pitcher with 30 FI IP gets 60%+ weight instead of
  being capped at 40%.
- **T2.9** Pick thresholds now flow Python → `data/thresholds.json` → TS
  classifier on dashboard. No more drift between the two implementations.
- **T4.3** Lambda floor (0.78 baseline) now scales with weather: hot/cold/wind
  adjustments ±0.04 max. Dome games skip the adjustment.

### Changed — Dashboard layout

- **T3.18** Filters persist via URL params (shareable links) AND localStorage
  (cross-session). `?side=NRFI&strength=STRONG&sort=lambda-desc` works.
- **T2.15** `app/page.tsx` validates `?date=` against strict YYYY-MM-DD
  regex + calendar validity. Invalid params fall through to latest-available
  instead of being silently coerced.
- **Cramped board layout** (today, post-T4) — widened pick cell from 220→252px
  min so OddsChip stops clipping; widened NRFI/YRFI columns 60→76px to fit
  bar+number content; meter narrowed 112→96px min; pickCell wraps to 2 lines
  for the rare LINEUP PENDING + tentative + odds combo.

### Changed — Strong-auto-bet + accurate STARTER PENDING vs NO DATA labels (T2.24)

User clarified two policy items:

1. **"Place a bet on every strong play."** The 2% edge gate was filtering
   out STRONG picks with marginal-edge odds (e.g., ATL@COL STRONG YRFI
   at -150 with edge -1.3% → previously `bet_placed=N`). User's actual
   policy: if the model commits STRONG, the bet goes in regardless of
   the recorded edge. `tracker._apply_odds_to_row` now auto-Y on every
   STRONG NRFI/YRFI pick. LEAN keeps the 2% gate (model less certain
   on LEAN, want a margin-of-safety on price).

   **Retroactive impact**: 3 historical STRONG picks flipped from
   `bet_placed=N` to `Y`:
   - 4/29 KC@OAK YRFI -135 (WIN, was 0u → now +0.741u)
   - 4/30 KC@OAK YRFI -130 (WIN, was 0u → now +0.769u)
   - 5/01 ATL@COL YRFI -150 (graded later tonight)

   Net retroactive P&L correction: **+1.51u** to season-to-date.

2. **"STARTER PENDING" was lying when starter was named.** HOU@BOS 5/01
   showed `STARTER PENDING` despite Boston naming Jake Bennett (his
   MLB debut, zero prior stats). The predictor's
   `pitcher_q='avg'` guard fired the same label for two distinct
   conditions:
   - Truly TBD pitcher (no name announced yet)
   - Named pitcher with insufficient MLB history (rookie debut)

   `mlb_first_inning_predictor.py:2102-2125` now differentiates via
   `_name_announced(name)`:
   - Name unannounced ("TBD"/empty/etc.) → `STARTER PENDING` (existing)
   - Name announced + quality `avg` → **`NO DATA`** (new path)

   Both still PASS the game (we don't bet without real data) but the
   label is truthful. HOU@BOS will now show `NO DATA` once the
   predictor re-runs.

### Changed — Bet-time odds lock (T2.23)

User feedback: "once we pull real odds for a pick, the odds should lock
for that game, because that's when you're supposed to put the bet in."

The dashboard's OddsChip was reading `market_*_odds` and updating on
every hourly scrape. For PASS / pending rows that's correct (we want
to track the live market). For rows where `bet_placed=Y` (we already
locked in the bet at that price), the moving chip created a confusing
"my edge is changing" feel even though the user was already in the
position.

**Fix**: `tracker._apply_odds_to_row` now early-returns on rows that
have `bet_placed=Y` AND non-blank `market_*_odds`. Effect: once a bet
is placed at price X, `market_*_odds` stays X for the rest of the
day. The OddsChip on the dashboard freezes alongside it. Sportsbook
name still refreshes (in case of book migration). `profit_loss_units`
still computes at lock time when the row grades.

**Trade-off**: closing-line capture is given up on bet-placed games.
`market_*` would have tracked the latest scrape and become the
closing line for traditional CLV. But:
- `opened_*_odds` (T4.28) still records the first scrape, so we have
  "open → bet" line movement (which IS the CLV that affects us, since
  post-bet movement doesn't help the user)
- The user explicitly preferred bet-time stability over closing-line
  data; this is the right trade-off for our use case

**Lock release**: if `bet_placed=Y` but `market_*` is blank (legacy /
corruption), the lock is treated as invalid and the row re-evaluates.
This handles edge cases without hand-coded escape hatches.

End-to-end tested: 4 scenarios (locked update blocked, unlocked still
updates, bet_placed=N keeps updating to find better edge, locked-then-
graded computes P&L correctly at -130 → 0.769u win).

### Added — Telegram pick-flip notifier (T2.22)

The user wanted a phone ping when a pick commits ("notify me when the
next STRONG/LEAN lands so I don't have to camp the dashboard"). Shipped
end-to-end via Telegram:

- New `@nrfi_terminal_bot` created via @BotFather. Token + chat ID
  captured during setup. Test message verified live.
- New `_notify_pick_flip_telegram` in `tracker.py` posts to
  `https://api.telegram.org/bot<TOKEN>/sendMessage` whenever a pick
  flips. Wired into the existing `_record_pick_change` site so every
  pick_changes.csv entry also generates a Telegram ping.
- **Filter**: only notifies when at least one side of the flip is
  **actionable** (`STRONG`/`LEAN` `NRFI`/`YRFI`). PASS-variant churn
  (LINEUP PENDING ↔ STARTER PENDING ↔ NO EDGE) stays quiet — that's
  data-quality noise, not betting decisions.
- **Tone-coded icon** matches the dashboard's odds-chip color scheme:
  🟫 STRONG NRFI, 🟥 STRONG YRFI, 🟧/🟨 leans, ⬜ demotes.
- **Format** (mobile-friendly):
  ```
  🟫 Pick flip · 2026-05-01
  PHI @ MIA  (7:10 PM ET)
  PASS - Lineup pending  →  STRONG YRFI
  ```
- **Configured via**: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars.
  Both must be set; either missing → silent no-op. Keeps local dev
  quiet and back-compat with existing deploys.
- **Workflow**: `daily.yml` predict step now exposes both as
  `${{ secrets.TELEGRAM_BOT_TOKEN }}` / `${{ secrets.TELEGRAM_CHAT_ID }}`.
- **Failure handling**: any error (network, bad token, etc.) is caught
  and logged to stderr — never breaks the predictor cron. Notifications
  are advisory.

### Added — Tier 1 scraper improvements (T2.20, T2.21)

Three reliability improvements to the DK odds scraper, all shipped together:

**T2.20 — Schedule-aware coverage alerting + overnight cron**
- Scraper now queries `https://statsapi.mlb.com/api/v1/schedule` after each
  capture and warns to stderr if `captured/scheduled < 80%` during prime
  hours (9am-1pm ET). Previously we only alerted on 0 captures (T1.4) —
  4/15 looked identical to 15/15 from the workflow's perspective.
  StatsAPI failures fall through silently to avoid false alarms.
- Added overnight cron at `0 5 * * *` (1am EDT / 12am EST) to catch DK's
  overnight opening lines for CLV tracking. The earliest existing cron
  was 12 UTC (7am ET); we were missing ~12hr of pre-game line movement.
  Workflow's action selector also updated to map `0 5 * * *` to `predict`.

**T2.21 — Doubleheader odds disambiguation**
- The scraper's merge logic keyed by `(date, away, home)` so DH-1 and
  DH-2 collided and only the second survived. The importer in
  `tracker.import_odds` had the matching issue with `by_team[(date,
  away, home)] = int`. Confirmed via 2026-04-30 HOU@BAL: G1 had no
  odds (graded LOSS un-priced), G2 did.
- Scraper now emits `start_time_utc` (DK's `event.startEventDate`) per
  row, and `_row_key` includes start time so DH halves stay distinct
  in the merged file.
- Importer's `by_team` is now `dict[..., list[int]]` and a new
  `_pick_dh_candidate` helper picks the picks_2026 row whose
  `game_time_et` parses to a UTC time within 90 min of the odds row's
  `start_time_utc` (ties broken by smallest delta).
- Match priority: `pk → teams+time → teams (legacy)`. The legacy
  fallback keeps older odds files (before this change) working without
  reimport.
- 90-min tolerance: well inside half the typical DH gap (~3.5h between
  DH-1 and DH-2), so they can never both match the same odds row.

End-to-end tested: scraper merge preserves both DH halves with distinct
start times; `_pick_dh_candidate` correctly picks index 0 for DH-1 odds
and index 1 for DH-2 odds, returns None when nothing is in range.

### Fixed — Deploy-overwrite race (T2.19)

A real production incident, captured here so it never happens again.

The Vercel project auto-deploys on every push to
`claude/mlb-inning-run-predictor-QyazL`. The cron pushes ~12 commits/day
(`auto: predict <date>`). When an agent or developer runs
`vercel --prod` with **uncommitted local code changes**, the manual deploy
ships local files — but within ~60 minutes the next cron push triggers
a NEW auto-deploy that builds from the remote branch source (without the
uncommitted changes), and that auto-deploy silently overwrites the alias.

Today this happened to the T2.17 and T2.18 fixes back-to-back. The
sequence was: T2.17 deploy → cron push → auto-deploy reverted T2.17 →
T2.18 deploy → cron push → auto-deploy reverted T2.18. The user saw
"odds disappeared" on two computers and asked "why does this shit keep
happening." It kept happening because the failure mode was structural,
not bad luck.

**Three-layer prevention now in place**:

1. **`CLAUDE.md`** at the repo root — agent rules document with the
   deploy procedure spelled out. Auto-loaded by future Claude sessions
   so the rule travels with the codebase.
2. **`dashboard/scripts/safe-deploy.sh`** — guarded wrapper around
   `vercel --prod`. Aborts if (a) working tree is dirty, (b) current
   branch isn't the production branch, or (c) local HEAD differs from
   `origin/<branch>`. Verified end-to-end: it correctly refused to run
   while there were uncommitted CLAUDE.md / scripts/safe-deploy.sh /
   package.json changes.
3. **`npm run deploy`** — the only sanctioned CLI deploy path; wired
   to the guard above. Anyone (human or agent) who tries the old
   `npx vercel --prod` directly still works, but `npm run deploy` is
   the documented path that's been load-bearing tested.

**The canonical deploy is still `git push`.** Vercel auto-deploys from
the push, the alias points at that commit's build by design, and a
later cron push can't race because it'd be a newer commit deploying its
own code (which already includes the previous push's code). The guard
script is for the rare cases where you genuinely need a CLI deploy
(env-var test, emergency rollback) — it makes those cases safe by
forcing a state where the cron can't overwrite you.

### Fixed — Odds layout: own column + tone-coded by pick side (T2.18)

After T2.17 made the chip visible on PASS rows by inlining it into the
PICK cell, the user pointed out three real UX issues: (1) odds should
have their own column for proper scanning, (2) tone should match the
pick side (NRFI brown vs YRFI red, muted for pending), and (3) it
wasn't clear which price was NRFI vs YRFI. Shipped:

- New `Odds` column header between PICK and EDGE; grid expanded from
  10 to 11 columns (header + body + mobile breakpoints all updated).
- Three new tone classes: `.oddsNrfi` (warm-brown), `.oddsYrfi` (red),
  `.oddsPending` (desaturated muted). Skipped-bet rows additionally
  get `.oddsSkipped` (dashed border) so we can see "we picked this side
  but didn't bet" without losing the side color.
- `N` and `Y` letter labels prefix each price (small ticker style,
  9.5px/0.10em, 0.72 opacity) so the chip reads `DK  N -135` for NRFI
  picks, `DK  Y +120` for YRFI picks, and `DK  N -130 · Y +100` for
  PASS rows showing both sides.

### Fixed — Odds visibility on PASS rows

- **T2.17** `OddsChip` was returning `null` for every row where
  `pickSide === "PASS"`, which silently hid the captured market price on
  every "no edge" / lineup-pending / starter-pending row. Today (5/01)
  all 15 picks were in PASS state because the model was waiting on
  lineups, so the dashboard looked like the scraper had failed —
  but the underlying CSV had full coverage. Now PASS rows render a
  neutral both-sides chip (`DK -130 · +100`); NRFI/YRFI rows keep their
  single-side, tone-coded chip with the same bet/skip styling as before.

### Fixed — Odds capture coverage

- **T2.16** `scrape_dk_odds.py` was overwriting `data/odds/dk_<DATE>.csv`
  on every hourly cron run with whatever DK had open at that moment. The
  noon run might capture 8 games, the 5pm run capture only 1 (most games
  locked), and the file would end up with just 1 row — losing the day's
  earlier captures from the audit trail. `picks_2026.csv` survived via
  UPSERT in the importer, but the file was useless for re-import or
  debugging coverage gaps.

  Fix: scraper now reads the existing file, merges with the fresh fetch
  (fresher snapshot wins per game), and writes the union. Also: 3-attempt
  exponential-backoff retry on the DK API call (most missed-coverage
  hours during the 04-29/04-30 window were transient network blips), and
  a smarter 0-games-returned path that exits 0 (preserves existing file)
  instead of triggering the stale-API-IDs alarm when we already have
  data from earlier in the day.

  End-to-end test passed: starting file with 3 games + a fresh fetch with
  1 update + 1 new game produces a 4-game merged file (existing rows
  preserved, updated row gets new odds, new row added).

  Pre-04-29 picks (178 of them) cannot be backfilled — DK doesn't expose
  historical odds. Forward coverage from this fix should improve markedly:
  a single successful early-morning capture now sticks for the whole day
  even if subsequent hours fail or only catch a subset.

### Fixed

- **T1.2** Doubleheader detail-key collision. Dashboard now stores details
  under DH-aware compound key `${away}@${home}#${gameNumber}` (plus gamePk).
  DH-2 rows never load DH-1's data, even on legacy CSVs without gamePk.
- **T1.5** `graded_result="POSTPONED"` is no longer permanent. Only WIN/LOSS/PASS
  are terminal — POSTPONED/SUSPENDED rows re-grade on every run.
- **T1.7** `safe_float` negative guard verified + defense-in-depth `_nn_float`
  / `_nn_int` helpers in `current_season_top3_per_batter`.
- **T1.8** Schedule fetch: 4-attempt exponential backoff (0/2/5/10s) before
  exit. Was a bare `except: sys.exit()`.
- **T1.4** DK scraper exits with distinct code 2 when 0 games during prime
  hours (9am-5pm ET) → workflow records "DK API IDs likely stale".
- **T2.2 + T2.12** `_pick_is_locked` has 3 defensive locks: graded-result
  terminal, slate-date >24h past, `created_at` >12h stale. Plus skips parse
  on non-numeric `game_time_et` (DH-Y placeholders). Bet snapshots can no
  longer be overwritten by parse failures.
- **T2.4** `fetch_pitcher_gamelog` filters by `gameType in VALID_GAME_TYPES`
  (R/F/D/L/W). Spring training + exhibition no longer inflate IP-weight.
- **T2.6** `DashboardShell` interval+listener effect has empty deps; `data.date`
  read via ref. Mounting is idempotent — no interval accumulation on date
  refetches.
- **T4.6** `_validate_calibrator_shape` runs at calibrator load. WARNs when
  neighboring bins jump >5pp (overfitting on small holdouts).
- **T4.7** `two_stage_model.py` refuses to train if `--test` file is also in
  `--train` list (resolved-path comparison). Catches the canonical leakage
  failure mode.

### Deferred

These were investigated and deferred (not closed):

- **T4.1** Catcher framing feature — auto-scheduled remote agent in 2 weeks
  (2026-05-15) to investigate Baseball Savant data sources, build cached
  fetch, backtest, and PR the change only if Brier improves AND zone hit
  rates don't regress.
- **T4.2** Umpire zone width — bundled with T4.1 review.
- **T4.4** Catcher-pitcher pairing — needs new data source.
- **T4.5** Refit LR with more features — bundled with T4.1.
- **T4.8** Catcher framing data source selection — covered by T4.1.
- **T4.9 / T4.10** CSV → SQLite/Postgres migration — atomic write fix in
  T1.1 already eliminates the race conditions, so the migration is no
  longer urgent.
- **T4.11** S3/Backblaze backup — superseded by T3.4 git-based backup.
- **T4.13 / T4.14** Predict on Vercel / Railway — major refactor, low ROI
  given current GHA usage (~890 min/month, well under 2000 free tier).

### Skipped per user preference

- **T4.25 / T4.26 / T4.27** Kelly sizing, bankroll-aware sizing, per-zone
  edge thresholds — user is sticking with flat 1u plays; current 2% edge
  threshold works.

---

## How to update this file

When you ship something meaningful, add a dated section above with the
audit ID (or `(no audit ID)` if it didn't come from AUDIT.md), 1-3 lines
of what changed, and update AUDIT.md's checkbox in the same commit. Keep
this file's "Performance snapshot" current to the change date — it's the
fastest way for a future session to know whether the model is still hot
or has regressed.

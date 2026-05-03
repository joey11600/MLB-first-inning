# NRFI Terminal — Upgrade Roadmap

Forward-looking list of system upgrades, ranked by **impact × effort**.
Companion to:
- `CHANGELOG.md` — what shipped, when, why (history)
- `AUDIT.md` — known bugs / fragility / hygiene items
- `docs/KB.md` — current system architecture

**Convention:** every meaningful fix or addition gets:
1. A new entry in `CHANGELOG.md` (dated, with audit ID `T<x.y>`)
2. The matching item below checked off `[x]` with a date + commit SHA
3. The architecture in `docs/KB.md` updated if the data flow changes

---

## ✅ Recently shipped (May 2026)

| Date | Ref | Item |
|---|---|---|
| 2026-05-02 | `2de0c3a` | **T2.29** — Rebalance board grid so PASS-row OddsChip stops clipping |
| 2026-05-02 | `bae3f34` | **T2.30 / Phase 1.5** — Supabase dual-write from `tracker.py` |
| 2026-05-02 | `d078dbc` | **T2.31 / Phase 2** — Dashboard read-side cutover to Supabase + Realtime push |
| 2026-05-02 | `a2b8410` | **T2.32 / Phase 4** — Railway live game-state worker (10s cadence) |
| 2026-05-02 | `7925fe6` + `8dd0cb7` | **T2.33 / Phase 3** — Railway predictor loop (5min cadence) + start-command fix |
| 2026-05-02 | `8dd0cb7` | **T2.34 / Phase 6** — PWA bundle (manifest + icons + service worker + iOS meta) |
| 2026-05-02 | `91d094c` | **T2.35** — Bypass Next.js fetch cache on dashboard SSR (was serving stale data) |
| 2026-05-02 | `442fe4d` + `6d23152` | **T2.36 + T2.37** — Telegram notifier upgrade (Supabase dedup, HTML format, dashboard hyperlink) + STRONG-only filter |
| 2026-05-02 | `e80a0a1` | **T2.38** — 8 new STRONG-only Telegram event types: graded W/L, voided, pregame, CLV, weather, milestone, daily digest, ops health |
| 2026-05-02 | `e5a0f84` | **T2.39** — Pitcher days-rest feature tested (4 variants on 2024↔2025) and rejected per ship rules; logged in KB.md |
| 2026-05-02 | `2c55af5` | **T2.40 + T2.41** — Pre-game starter-scratch detector (Telegram alert on STRONG bets affected by a probable-pitcher change) + dedup window fix on `flip_to_strong` (5min → 24h, prevents Railway×GHA race-induced duplicate pings) |
| 2026-05-02 | `fc55aee` | **T2.42** — Bankroll equity curve on `/history`: SVG line + drawdown shading + ATH marker + 6-stat panel (Bankroll, ATH, Max DD, Current DD, Vol, Sharpe). +1.4kB bundle. |
| 2026-05-02 | `eb00199` | **T2.43** — Multi-recipient Telegram broadcast. `TELEGRAM_CHAT_ID` upgraded from a single id to comma-separated CSV; `_send_telegram_html` fans out per-recipient with soft-fail. Created **"Backfist Bets"** group; bot added; live ping verified to both personal + group. Three env stores synced (Railway predictor, Railway worker, GHA secret). |
| 2026-05-02 | `2f477e6` | **T2.44** — xERA disambiguation in "Why this pick" panel. Re-labeled `home_xera`/`away_xera` to "Home pitcher xERA (Statcast)" + new `featureTooltip()` helper providing one-sentence plain-English descriptions for every LR feature, wired as `title=""` on each row name. |
| 2026-05-02 | `44923a3` | **T2.45** — Post-audit hardening (5 fixes). (1) Removed dead First-inning split UI (never rendered for any row). (2) Added `notifications_log` DDL to `schema.sql` (production has the table; reproducibility gap fixed). (3) Wired Railway predictor + live-state worker failures into `system_errors` so the dashboard surfaces a degraded worker instead of showing "all green". (4) Validated `TELEGRAM_CHAT_ID` format with `/^-?\d+$/` regex; malformed entries dropped with stderr warning. (5) `mirror_picks` upsert is now per-batch retry (3 attempts, linear backoff) with system_errors recording on persistent failure -- partial successes no longer silently lost. |
| 2026-05-02 | `d7a5aae` | **T2.46** — Defensive dedupe in `mirror_picks` payload. DK scrape produced 2 rows for CIN@PIT (mid-day start-time revision) → matched_indices got duplicate idx → Postgres SQLSTATE 21000 → entire mirror batch rejected → grades silently 1hr behind on dashboard. Fix: dedupe by (date, game_pk) before upsert, keep latest. |
| 2026-05-02 | `36a1804` | **T2.47** — `scrape_dk_odds.py` start-time bucketing (round to nearest 30 min in `_row_key`) so DK's intra-day schedule revisions don't appear as DH halves. Plus `predictor_loop.run()` now tees stderr → `_LAST_STDERR_TAIL` so failures land in `system_errors.message` with real diagnostics instead of `rc=1`. |
| 2026-05-02 | `deeb7a2` | **T2.48** — Loss-analyzer Phase 1: `tools/analyze_losses.py` classifier + Supabase `loss_analysis` table. 7 mutually-exclusive failure modes (data_quality / lineup_changed_late / outside_top3_event / pitcher_dominated / sequencing / quiet_inning / bunched_contact). Backfill on 66 historical losses: 59% actionable, 41% variance floor. Auto-runs in GHA after every grade cycle (T2.50). |
| 2026-05-02 | `78f2805` | **T2.49** — Fixed weather alert firing on completed games (added `_pick_is_locked` guard at call site + `graded_result` short-circuit in notifier). Plus `tools/inspect_mode.py` for deep-dive on any failure mode bucket. Used to identify the xERA-dominance hypothesis. |
| 2026-05-02 | `a9ee943` | **T2.50** — Auto-run `analyze_losses.py` daily after GHA grade + `docs/MODEL_REVIEW_2026_05_09.md` decision framework with explicit IF/THEN rules and "no change" clause for when proposed variants fail their ship gate. |
| 2026-05-02 | `6294dcc` | **T2.51 / Tier 3 #14** — A/B model harness shipped. New `pick_variants` Supabase table + `db/variants.py` compute module + `tools/backfill_variants.py` + `tools/abtest_report.py`. 3 candidate variants (A: cap LR contributions ±0.45; C: raise YRFI STRONG p threshold 0.56→0.58; AC: both) ran against 424 historical picks. **All three rejected** vs production: VAR-A −41u, VAR-C −23u, VAR-AC −37u. Daily auto-backfill via GHA. |
| 2026-05-02 | `5e28625` | **T2.52** — Variant D (raise YRFI lambda floor 0.78→1.00) tested as clean post-filter on production verdict + harness pagination fix. **Variant D also rejected** (−6.42u vs production). The 17 bets D removed went 12W/5L = 70.6% hit rate — better than production's overall 63.5% — proving the bucket-based "losing zone" was selection bias on the same data. **All 4 variants now rejected; the 'no change' clause in `docs/MODEL_REVIEW_2026_05_09.md` is triggered.** |
| 2026-05-03 | `83b4f75` | **T2.53** — Compound PASS labels + early-season pitcher quality tag fix. (1) `pass_reasons` list flows from predictor → tracker so a row with multiple guards (e.g. `BAL@NYY` 2026-05-03: lineup pending AND `Trey Gibson` debut pitcher) shows "PASS - Lineup pending + No data" instead of just "No data". (2) `_pitcher_quality_tag(ip, prior_ip)` now uses effective IP = curr + min(prior, 120) instead of curr-only — fixes systemic mid-season miscalibration where 24/30 veterans were tagged 'ltd'/'sm' (Max Fried 1127 IP showing as ltd, etc.). Underlying stats were always correct (Bayesian blend used prior year); only the displayed tag was misleading. |
| 2026-05-03 | `fdac3b5` | **T3.11 / Tier 3 #11** — Walk-forward backtest framework (`tools/walk_forward.py`). Trains on prior seasons, tests on next, multi-fold (2022→2023, 2022+2023→2024, 2022+2023+2024→2025) plus single-fold E3 check (2024→2025) for the production phase_e3 model. Reports per-fold Brier vs climatology, top/bottom-quintile NRFI/YRFI hit rates, and simulated betting P&L at -120 vig under production STRONG thresholds (NRFI ≥ 0.58 / YRFI ≤ 0.42). **First run validates that production phase_e3 model PASSES walk-forward**: 572 bets, 332-240 W-L (58.0% hit rate), +36.67u P/L (+6.4% ROI), Brier 0.2488 vs climatology 0.2500 (positive skill) on 2025 holdout the model never saw. Slim and slim_weather baselines fail walk-forward consistently — confirming the structural feature additions (xera, pvt_nrfi, whiff, last5/10, etc.) carry the edge. Now the explicit gatekeeper for any future model variant: must clear PASS (positive Brier skill + profitable) on >= 2 folds before shipping. |

End result: the dashboard is now an **installable PWA** receiving **sub-second Realtime push** of model predictions every 5 minutes (Railway predictor) and game state every 10 seconds (Railway live-state worker). Loss-analyzer + A/B harness run automatically on every grade cycle. Four candidate model variants all rejected via backfill-driven A/B.

---

## 🔥 Tier 1 — direct ROI moves

These improve money outcomes without touching the working model.

| # | Status | Effort | Edge gain | Item |
|---|---|---|---|---|
| 1 | [needs user opt-in] | 2–3 hr | **+10–30% ROI** | **Kelly fractional bet sizing** in `tracker._apply_odds_to_row`. Currently flat 1u STRONG / 0.5u LEAN. Quarter-Kelly sizing (units = `0.25 × edge / (price-1)`) typically lifts ROI 10-20% without raising variance much. ⚠ **CLAUDE.md "Money rules"** explicitly says "Flat 1u plays only. User explicitly rejected Kelly / fractional / bankroll-aware sizing." Don't ship without re-checking with the user. |
| 2 | [tested 2026-05-02 · rejected] | 3 hr | — | **Pitcher days-rest feature**. `away_days_rest` + `home_days_rest` already backfilled in 2024/2025 backtests; `backfill_days_rest.py` is on disk. Tested 4 variants via `test_days_rest.py` against the Phase E.3 baseline on a 2-split 2024↔2025 cross-validation. Best variant (`+rest_signed_gap`) +8.2u sum P&L vs baseline (below the +10u ship bar) AND regressed STRONG YRFI hit rate by 3.1pp on the 2024→2025 split. Other 3 variants regressed -21 to -60u. Joins the "tested, didn't help" list in `docs/KB.md`. Reason: rest signal isn't separable from the FIP/ERA/last-5 features already in the model. |
| 3 | [3× rejected] | 4–6 hr | — | **Wind-direction × park-orientation**. Tested THREE different framings, all null on out-of-sample Brier: (1) raw `wx_wind_kmh` only — kept as small feature, marginal contribution; (2) decomposed `wind_out` + `wind_cross` (Phase E.1) — null; (3) wind × park-orientation continuous interaction (cross-validation 2024/2025) — null. Three structural reasons wind doesn't move the needle for 1st-inning specifically: (a) inning is too short — most 1st-inning runs come from singles/walks where wind is irrelevant, and HRs are too rare in a 6-batter sample to drive predictable scoring; (b) pitchers ADJUST to wind (induce grounders against out-blowing wind, attack zone against in-blowing); (c) Open-Meteo gives stadium-level wind, not warning-track wind, so SNR is low. ONE framing not yet tested: **categorical buckets** ("blowing OUT to RF" / "OUT to CF" / "OUT to LF" / "IN from CF" / "CROSS L→R" / "CROSS R→L" / "NONE") combined with park-specific orientation as discrete categories, possibly multiplied by wind speed. Prior probability of working given 3 prior nulls: **~15-20%**. Don't pursue until Tier 1 backlog (`whip_gap_signed`, `recent_form_gap`, `whiff_gap_signed`) is exhausted — those have higher EV/hour. If ever attempted, target Coors + Wrigley specifically (the two parks with documented wind-sensitivity history) rather than a global feature. |
| 4 | [shipped 2026-05-02 · T2.40] | 3 hr | Prevents bad-data losses | **Pre-game injury / scratch detection**. Extended the existing Phase-4 live-state worker to also poll `probablePitcher` and compare to our recorded pitcher_id on STRONG bets. Telegram alert via the T2.38 framework, 6h dedup, 60s throttle. See `workers/live_state.py` `check_scratches()`. |
| 5 | [scheduled T+14d] | 6 hr | +2–4% NRFI edge | **Catcher framing** (T4.1 in AUDIT). Remote agent scheduled via `/schedule` to investigate Baseball Savant on 2026-05-16. Backtest gates before any model change. |

---

## 🟡 Tier 2 — visibility & risk control

| # | Status | Effort | Item |
|---|---|---|---|
| 6 | [shipped 2026-05-02 · T2.42] | 3 hr | **Bankroll equity curve** on `/history` page. Pure SVG (no charting lib). Equity line + drawdown shading + ATH watermark + 6-stat panel (Bankroll / ATH / Max DD / Current DD / Vol / Sharpe). +1.4kB bundle. See `EquityCurveChart` in `dashboard/components/HistoryView.tsx`. |
| 7 | [ ] | 2–3 hr | **Live DK line-drift chip** per row. `opened_*_odds` + `clv_pct` already exist; just not surfaced. Shows "DK -135 → -150 (sharp move toward us)". |
| 8 | [rejected 2026-05-03] | — | **Drawdown circuit breaker**. User decision: "defeats the purpose of betting the whole system." Stop-loss locks in drawdowns by skipping the recovery window; whole-slate strategy treats variance as expected at 60-65% hit rate. See "Decisions ratified" below. |
| 9 | [ ] | 3–4 hr | **Ops health card** on dashboard. Last predict cycle, last odds scrape, system_errors today, Railway worker status, parity check. |
| 10 | [ ] | 2 hr | **Today's CLV summary** in summary strip. "Today CLV: +0.8pp avg" — leading indicator of model sharpness. |

---

## 🔵 Tier 3 — model robustness & validation

| # | Status | Effort | Item |
|---|---|---|---|
| 11 | [shipped 2026-05-03 · T3.11] | 6–8 hr | **Walk-forward backtest framework** (`tools/walk_forward.py`). 3 historical folds + phase_e3 single-fold check. First run: production phase_e3 PASSES (58% hit, +36.67u over 572 bets on unseen 2025 season); slim baselines FAIL — confirming the structural features carry the edge. **Now the gatekeeper for new variants**: must clear PASS on >= 2 folds before shipping. |
| 12 | [ ] | 3–4 hr | **Confidence intervals on hit rate**. 95% CI Bayesian shading on the +41.99u stat. |
| 13 | [ ] | 5–6 hr | **Model drift detector**. Rolling 30-day calibration test; alarm if P=0.6 doesn't actually win 60%. |
| 14 | [shipped 2026-05-02 · T2.51 + T2.52] | 6–8 hr | **A/B model harness**. `db/variants.py` + `tools/backfill_variants.py` + `tools/abtest_report.py`. Runs daily via GHA. Used to reject variants A/C/AC/D over 32-day backfill (all underperformed production by 6-44u). Adding a new variant is now ~30 lines. |
| 15 | [ ] | 4 hr | **Backfill tooling**. Generalize the existing `backfill_phase_*.py` scripts into a single `backfill.py --feature X --season Y`. |

---

## 🟢 Tier 4 — UX & convenience

| # | Status | Effort | Item |
|---|---|---|---|
| 16 | [ ] | 10–15 hr | **Auto-bet placement on DK** OR a "bet card" deep link that pre-fills the side + line on DK. |
| 17 | [ ] | 4–6 hr | **iOS native push via Web Push + VAPID**. Service worker stubs already in `dashboard/public/sw.js`. Telegram already covers this; redundant but nicer. |
| 18 | [ ] | 1 hr each | **Slack / Discord webhooks** mirroring the Telegram path. `ALERT_WEBHOOK_URL` already exists. |
| 19 | [ ] | 2 hr | **Slate confidence rating**. "3 STRONG / 5 LEAN / 7 PASS = High confidence" badge in summary strip. |
| 20 | [ ] | 3 hr | **Bet history search + filters** on `/history`. |

---

## ⚪ Tier 5 — speculative / longer-term

| # | Status | Effort | Item |
|---|---|---|---|
| 21 | [ ] | 8–12 hr | **XGBoost replacing LR**. +1-3% accuracy typical. Don't ship without #11 walk-forward. |
| 22 | [ ] | 6–10 hr | **Live in-game hedging logic**. If you bet YRFI -150 and T1 ends 0-0, hedge B1 NRFI for guaranteed mid-bet profit. |
| 23 | [ ] | 16+ hr/sport | **Other sports — NBA Q1, NFL H1, NHL P1**. Same architecture; same data sources. Big TAM, big lift. |
| 24 | [ ] | 12+ hr | **Player props (first-inning pitcher Ks, runs O/U 0.5)**. Different model, same data sources. |

---

## ⛔ Explicitly out of scope

- **Phase 5 multi-book odds** (DK + FanDuel + BetMGM) — user decision: DK only, prioritize speed over best-line shopping.
- **Public betting % integration** — first-inning markets too thin for reliable sharp/square data.
- **DK cash-out automation** — DK's cash-out values are hostile, rarely +EV.
- **Daily park factor refresh** — park factors use 3-year rolling windows (~240 games per park); a single day moves the average by ~0.4%, well below the model's signal floor (thresholds operate at 0.02-0.05). Weekly cadence via the `recalibrate` workflow is correct. The ONE moment that matters is the offseason re-fit when full prior-season data + structural changes (fence moves, humidors) land. Daily refresh would add overhead with zero pick-affecting signal gain. Decision ratified 2026-05-03.

---

## 🧠 Decisions ratified by the data (locked in until counter-evidence)

These are NOT open items — they're explicit "do not pursue" decisions backed
by tested-and-rejected evidence. Listed here so future planning sessions
don't waste time re-litigating settled questions.

| Decision | Date | Evidence | Re-open if... |
|---|---|---|---|
| **No model changes until walk-forward exists** | 2026-05-02 | Variants A (cap LR contributions), C (raise YRFI threshold), AC (both), D (raise lambda floor) all rejected via T2.51 A/B harness backfill. Underperformed production by 6-44u over 32 days. The bucket-analysis "patterns" that motivated each variant turned out to be selection bias on the same data we tested against. | A walk-forward backtest framework exists (Tier 3 #11) AND a new variant clears the +10u improvement bar across both 2024↔2025 cross-folds without STRONG hit-rate regression. |
| **xERA-dominance hypothesis is dead** | 2026-05-02 | Variants A and AC both attempted to cap per-feature LR contributions at ±0.45 log-odds, targeting `quiet_inning` + `outside_top3_event` losses where xERA seemed to dominate. Both produced ~−40u over 32 days. Capping kills the model's ability to identify high-conviction bets — STRONG hit rate collapsed from 64% to 51-53%. | Same as above (walk-forward + +10u clear). |
| **YRFI threshold raise is dead** | 2026-05-02 | Variant C (require p_yrfi ≥ 0.58 instead of 0.56) produced −23u over 32 days. Cuts more profitable STRONG bets than it saves losses. | Same as above. |
| **YRFI lambda floor raise (0.78 → 1.00) is dead** | 2026-05-02 | Variant D removed 17 production bets that went 12W/5L = **70.6% hit rate** — better than production's 63.5% overall. The "0.90-1.00 lambda losing zone" was selection bias on the same sample. | Same as above. |
| **Soft-edge skip (P=0.60-0.62) is dead** | 2026-05-03 | Variant E (T2.59) skipped STRONG bets where stated P(pick) was 0.60-0.62. Tested via the harness on 433 graded picks since 2026-04-01: −1.55u vs production. Hit rate marginally improved (+0.9pp) but bet volume dropped by 25, eliminating a slightly-positive-EV slice. The 0.60-0.62 band looked weak in 14d but normalizes to ~60% over 30d, which IS profitable at -110 odds. Soft-edge bets aren't the problem. | Same as above. |
| **Thin-sample pitcher skip is dead** | 2026-05-03 | Variant F (T2.59) skipped STRONG bets when ANY pitcher was 'sm'/'avg' OR BOTH were 'ltd'. Tested on 433 picks: only 3 bets passed the filter (filter is too aggressive — most early-season STRONG bets have at least one thin-sample pitcher). −39u vs production. | Same as above. |
| **Wind × park-orientation is dead** (3 framings) | Phase A / E.1 / 2024-2025 backtests | (1) raw wind speed = small marginal feature kept; (2) decomposed `wind_out` + `wind_cross` = null; (3) continuous wind × park-orientation interaction = null. Structural reasons: 1st inning too short for wind-affected events to dominate, pitchers adjust to wind, Open-Meteo wind isn't warning-track wind. | A FUNDAMENTALLY new framing (categorical buckets like "OUT_RF" / "IN_CF" / "CROSS_L2R" with park-specific lookup) gets tested AND clears the +10u bar. Even then, prior probability is ~15-20% based on 3 prior nulls. |
| **Pitcher days-rest is dead** | 2026-05-02 | 4 variants tested via `test_days_rest.py` on 2024↔2025 cross-validation: best (+rest_signed_gap) +8.2u (below +10u bar) AND regressed STRONG YRFI hit rate by 3.1pp on 2024→2025. Other 3 variants regressed −21u to −60u. Rest signal isn't separable from FIP/ERA/last-5 features. | Genuinely new feature engineering (e.g. rest × pitcher_FIP interaction, Tier 2 #10 in variant_backlog.md) clears the bar. |
| **Daily park factor refresh is wasteful** | 2026-05-03 | Park factors use 3-year rolling windows; per-day movement is ~0.0005, well below the model's 0.02-0.05 signal threshold. Weekly cadence is correct. | Park structural changes mid-season (fence moved, humidor changed) — but those are once-per-decade events. |
| **Telegram bet-sizing stays flat 1u STRONG / 0.5u LEAN** | Pre-2026-05-01 | User explicitly rejected Kelly / fractional / bankroll-aware sizing (CLAUDE.md "Money rules"). Kelly typically lifts ROI 10-20% but adds variance and contradicts user's "flat plays only" preference. | User changes their mind. Don't ship without explicit re-approval. |
| **Drawdown circuit breaker is rejected** | 2026-05-03 | User decision: "that defeats the purpose of betting the whole system." Auto-PASS after N losses would lock in losses by skipping the recovery window. The whole-slate strategy treats variance as a feature, not a bug -- short streaks (good or bad) are expected at 60-65% hit rate and the model recovers in expectation. Premature stop-loss converts paper drawdowns into real ones. | User changes their mind. Don't ship without explicit re-approval. |

---

## Recommended execution order

If shipping in priority order (revised 2026-05-03 after 4 variant rejections):

**Tier 1 (next session, observability + UX, ~6-9 hr total):**
- #9 **Ops health card** (~3-4 hr) — surfaces the `system_errors` data T2.45 made visible. Currently invisible infrastructure.
- #7 **Live DK line-drift chip** (~2-3 hr) — already-stored `opened_*_odds` + `clv_pct`, just not surfaced.
- #10 **CLV summary in summary strip** (~2 hr) — leading indicator of model sharpness.

**Tier 2 (variance protection):**
- ❌ #8 Drawdown circuit breaker — **rejected by user** ("defeats the purpose of betting the whole system"). See Decisions ratified.

**Tier 3 (the gatekeeper, ~6-8 hr):**
- ✅ #11 **Walk-forward backtest framework** — shipped 2026-05-03 (T3.11). Production phase_e3 model passes walk-forward on 2025 holdout. Now gates all future variants.

**Month 2+ (now that walk-forward exists):**
- #5 Catcher framing (scheduled agent fires 2026-05-15) — must clear walk-forward bar
- #12 Confidence intervals
- #13 Drift detector
- #21 XGBoost A/B (must clear walk-forward bar)

**Explicit DO-NOT-TOUCH for the foreseeable future** (see "Decisions ratified" above):
- ❌ Any new variant testing without walk-forward in place first
- ❌ Wind × park orientation re-tests in any form
- ❌ Days-rest feature re-tests
- ❌ Kelly / fractional sizing
- ❌ Daily park factor refresh
- ❌ Drawdown circuit breaker / stop-loss

---

## How to update this file

When you ship something:

1. Move its row from the relevant tier into the **Recently shipped** table at the top, with date + commit SHA.
2. Add a corresponding `### Added/Changed/Fixed` entry to `CHANGELOG.md` under today's date section.
3. If the architecture changed (new worker, new table, new data flow), update the diagram in `docs/KB.md`.
4. Commit all three files together so future-you (and future-Claude) see the full picture.

When you discover a new upgrade idea:

1. Drop it in the appropriate tier with `[ ]` status, effort estimate, and edge / value rationale.
2. Tag with audit ID `T<series>.<x>` if it overlaps with an existing audit item.

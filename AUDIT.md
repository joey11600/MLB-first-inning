# NRFI Terminal — System Audit

Generated 2026-05-01 from a 4-agent parallel audit covering predictor, tracker,
dashboard, and operations. Items are tagged `[ ]` (open) / `[x]` (fixed) /
`[~]` (in progress) so we can check them off as we work through.

Severity legend:
- 🔴 Critical: real bug, money risk, or data corruption
- 🟠 Likely: probable issue, hard to confirm without testing
- 🟡 Smell: fragility / hygiene / will bite eventually
- 🟢 Improvement: not a bug, but a valuable addition

---

## 🟠 TIER 8 — 2026-08-04 backtest-file integrity

Found while chasing an unrelated question ("does a first-inning drought
predict the next first inning?" — it does not; see CHANGELOG). No live
bet is affected: `two_stage_model.py` reads none of these columns and
the live ledger's own columns are healthy.

- [ ] **T8.1 — 🔴 `_pit` is NOT the point-in-time fix; `_ptfix` is**
  `2026-08-02_training_data_leakage` recorded the ERA/FIP/OBP repair as
  landing in `*_truepit_pit.csv`. It did not. Measured on disk: share of
  pitchers with 2+ starts whose ERA varies within the season is **0.0%**
  in both `truepit` and `truepit_pit`, and **73.3% / 77.5%** in
  `truepit_ptfix` and `truepit_pit_ptfix`. `away_era` is byte-identical
  between `truepit` and `truepit_pit` in 100% of rows. `_pit` is the
  separate `backfill_pit_pitching_stats.py` backfill (CHANGELOG
  2026-08-03k). **The name reads as "point-in-time" and is not** — it
  has already misled at least two analyses into auditing a still-leaked
  file. Fix: rename the variants, or add a `README` in `data/backtests/`
  stating which suffix means what.

- [ ] **T8.2 — 🟠 verdict columns in 2024/2025 backtests are retired-Poisson artefacts**
  In **all four** 2024/2025 variants including `_ptfix`:
  `nrfi_prob == nrfi_prob_raw` in 100% of rows (calibrator never
  applied), and `nrfi_prob == exp(−lambda_total)` in 100% of rows (max
  dev 7e-05) — the old Poisson transform, not the two-stage LR that has
  shipped for months. `lambda_total` is byte-identical between `_pit`
  and `_ptfix`, so the point-in-time repair rebuilt era/fip/obp and
  never recomputed λ or anything derived from it. Measured signal of
  `lambda_total` in those files: **AUC 0.5008 (2024) / 0.4866 (2025)** —
  a coin flip — against 0.0535 directional strength for
  `combined_lambda` on the live 2026 ledger. This is the mechanism
  behind the already-recorded "2024 backtest is below-chance on itself".
  **Rule: never read `pick_side` / `pick_strength` / `nrfi_prob` /
  `yrfi_prob` / `lambda_total` from a 2024/2025 backtest as "what the
  model would have done" — re-score from the feature columns.**

- [x] **T8.3 — row alignment is NOT corrupt** ✅ 2026-08-04
  Ruled out explicitly, because it was the scary hypothesis:
  `fi_park_nrfi_rate` discriminates normally in every file (0.0544 /
  0.0497 / 0.0838 directional strength). A scrambled file could not do
  that. The feature columns are usable; only the derived verdict
  columns are stale.

---

## 🔴 TIER 7 — 2026-07-28 money-path + dashboard audit

16-agent review (6 lenses, adversarial verification). 35 raw findings,
8 confirmed, all fixed. See CHANGELOG 2026-07-28.

- [x] **T7.1 — Kelly daily cap double-counted on every odds re-import** ✅ 2026-07-28
  `tracker._committed_on` seeded from ALL STRONG rows including the pre-lock rows the batch was about to re-size, and each re-size ADDED without releasing. Committed exposure ran ~2x truth; with Railway re-importing every 5 minutes, stakes oscillated full → trimmed → zero and froze at whatever the lock window caught. Now seeds only from `bet_placed='Y'`, plus `kelly_reset_daily_committed()` at the top of every `import_odds` batch (also clears the never-expiring `_bankroll_cache`). Regression: three consecutive simulated batches now produce identical stakes.

- [x] **T7.2 — end_of_day heal fabricated bets from deliberate no-bets** ✅ 2026-07-28
  Orphan-heal skipped only `bet_placed='Y'`, sweeping Kelly zero-stake / cap-zeroed / pre-lock-pending `'N'` rows into `Y` at flat 1.00u. Invented P&L then mis-sized later stakes through the compounding bankroll. Heals only truly-blank rows; preserves recorded Kelly stakes.

- [x] **T7.3 — StakeChip sized from the static nominal bankroll** ✅ 2026-07-28
  Chip used 100u while `tracker` sizes from the compounded bank — overstates the stake in a drawdown. Predictor exports `kellyCurrentBankrollUnits`; once locked the chip shows the ledger's frozen `unitsRisked`.

- [x] **T7.4 — hero card hard-coded 1u per placed bet** ✅ 2026-07-28
  `TonightsActionCard` summed a constant 1 under live quarter-Kelly (4-10u stakes), understating the night's exposure severalfold.

- [x] **T7.5 — sizing bankroll compounded -110 placeholder P&L** ✅ 2026-07-28
  `current_bankroll_units()` counted wins settled at the flat fallback price — the April artefact, inside the money path. Now skips rows without a real picked-side price.

- [x] **T7.6 — season record claimed to replay the live model but did not** ✅ 2026-07-28
  Scored with a walk-forward calibrator reading +0.008 to +0.027 higher than the shipped one; since YRFI fires on a LOW p_nrfi that cost 31 bets over the real window. Now reports the deployed figure as the headline with the walk-forward figure beside it as the no-hindsight floor.

- [x] **T7.7 — doubleheader key collision in the season record** ✅ 2026-07-28
  `(date, away, home)` is not a key: both legs of 2026-07-19 LAD@NYY and 2026-07-22 PIT@NYY rendered as the same bet twice and doubled their day totals. `load_season` emits a stable `rid`; legs label as `G2`. Season totals were unaffected.

- [x] **T7.8 — CLV rendered "+0.00pp" for an unmeasurable quantity** ✅ 2026-07-28
  `board-supabase.ts` coerced NULL to 0 via `num()` (Supabase is the production path, so every `clvPct != null` guard was dead), and the CSV genuinely stores `0.0000` because the price freezes on placement. Now measured only when opening and taken price differ; otherwise reads "Not measurable".

- [x] **T7.9 — ZoneCard tone disagreed with its own number** ✅ 2026-07-28
  Tone keyed off placeholder-inflated `unitsPL` while the card printed `realPL`.

- [x] **T7.10 — watermarks overlapped live figures; sub-AA contrast** ✅ 2026-07-28
  The 56px rotated "PAPER" sat on the −52.4pp value. Both watermarks removed together with the `z-index` rule holding them behind text. Light mode: four tokens below AA, fixed in BOTH light blocks (they had diverged). Dark mode: card border was 1.17:1 (invisible) → 2.27:1, `--muted-foreground` 5.75 → 7.25, `--destructive` lifted to clear AA on the lightened muted surface.

- [x] **T7.12 — date picker could not reach most of the season** ✅ 2026-07-28
  `listAvailableDates` capped at 500 ROWS (one row per game, ~13/night) while its comment described it as a slate count, so only ~38 days were listable. Older dates failed the `available.includes()` test and silently served tonight's board under the requested date. Paginated with `.range()`; a bigger `.limit()` would not have worked because PostgREST enforces a 1000-row server-side max. Unavailable dates now log instead of substituting silently.

- [ ] **T7.11 — `game_pk` is not unique in picks_2026.csv** 🟡
  1563 rows, 1543 distinct; doubleheader legs share one pk and 2026-06-17 SF@ATL has both legs labelled game 1. No P&L impact today. Worked around by `rid`; the writer should still be fixed.

---

## 🔴 TIER 1 — Real bugs, fix this week

These can corrupt picks, lose data, or silently mis-grade.

- [x] **T1.1 — CSV concurrent-write race** ✅ 2026-05-01
  `tracker.py:_write_rows` now writes to a temp file in the same directory, fsyncs, and `os.replace()`'s atomically. Eliminates torn writes between racing cron firings and concurrent Vercel build reads. Also hardened `_record_pick_change` to detect existing header by content (not just file existence) so racing appends don't double-write the header.

- [x] **T1.2 — Doubleheader detail key collision** ✅ 2026-05-01
  `dashboard/lib/board.ts` now stores details under a DH-aware compound key `"${away}@${home}#${gameNumber}"` in addition to gamePk. `BoardTable.tsx` falls back through `gamePk → away@home#N → away@home`, so DH-2 rows never load DH-1's data even on legacy CSVs without gamePk.

- [x] **T1.3 — `|| true` swallows grade + scrape + import-odds errors** ✅ 2026-05-01
  `.github/workflows/daily.yml` predict + grade steps now capture exit codes, write `data/system_errors.csv` rows for any failure, and emit GitHub `::warning::` annotations. Predict failures now hard-fail the run; ancillary failures (catch-up grade, DK scrape, odds import) soft-fail with logged context.

- [x] **T1.4 — DK scraper hardcoded API IDs with no monitoring** ✅ 2026-05-01
  `scrape_dk_odds.py` exits with code 2 (distinct from 0=success and 1=fetch error) when it returns 0 games during prime hours (9am-5pm ET). The workflow recognizes exit 2 and records "DK API IDs likely stale" in `system_errors.csv`.

- [x] **T1.5 — `graded_result="POSTPONED"` is permanent** ✅ 2026-05-01
  `tracker.grade_picks` now treats only `WIN/LOSS/PASS` as terminal grades. `POSTPONED/SUSPENDED` rows are re-checked on every grade run, so makeup games and resumed games get their real W/L recorded the next time MLB reports Final.

- [x] **T1.6 — Bounded git push retry can lose 15-min run** ✅ 2026-05-01
  `.github/workflows/daily.yml` push loop bumped from 3 attempts to 8 with 5-30s jittered backoff. CSV-only conflicts (anything under `data/`) auto-resolve by `--ours` (this run's freshly-computed output) since each cron is a complete recomputation. Non-data conflicts still abort with the conflicting file list logged.

- [x] **T1.7 — `safe_float` allows negatives into model** ✅ 2026-05-01
  Verified: `safe_float` already guards (`v >= 0`). Hardened `current_season_top3_per_batter` in backtest.py with non-negative `_nn_float` / `_nn_int` helpers as defense-in-depth — bad values surface as `None` (em-dash on dashboard) instead of polluting the lineup card.

- [x] **T1.8 — Bare `except: sys.exit()` on schedule fetch** ✅ 2026-05-01
  `fetch_schedule` now retries 4 times with exponential backoff (0/2/5/10s). Logs each retry to stderr. Only exits if all 4 attempts fail, with the last exception's message attached.

- [x] **T1.9 — Predict-step push race with Vercel build** ✅ 2026-05-01
  Bundled into T1.1 (atomic write) + T1.6 (5-second sleep before `git add` in the commit step). Vercel auto-deploy can no longer pick up a truncated CSV.

**Tier 1 status: 9/9 complete.**

---

## 🟠 TIER 2 — Probable bugs / soon

- [x] **T2.1** ✅ Already fixed in earlier roi.ts change. Verified at `roi.ts:271,277` — PASS picks seed `dayPL.set(date, 0)` so all-PASS days show on the chart.
- [x] **T2.2 + T2.12** ✅ 2026-05-01 — `_pick_is_locked` now has 3 defensive locks: graded-result terminal, slate-date >24h past, `created_at` >12h stale. Plus skips parse on non-numeric `game_time_et` (DH-Y placeholders). Bet snapshots can no longer be overwritten by parse failures.
- [x] **T2.3** ✅ 2026-05-01 — `_apply_odds_to_row` now stores would-be `units_risked` even when `bet_placed=N`, so post-mortem can compute counterfactual P&L for skipped bets. `_calc_pnl` short-circuits on bet_placed=N so no double-counting.
- [x] **T2.4** ✅ 2026-05-01 — `fetch_pitcher_gamelog` now filters by `gameType in VALID_GAME_TYPES` (R/F/D/L/W). Spring training + exhibition games no longer inflate the pitcher-blend IP-weight. Cache TTL is 12h so existing entries refresh naturally.
- [x] **T2.5** ✅ Already fixed in T1.1b — `_record_pick_change` detects header by reading first line content, not just file existence. Two racing appends no longer write duplicate headers.
- [x] **T2.6** ✅ 2026-05-01 — `DashboardShell` interval+listener effect now has empty deps array; current `data.date` is read via a ref. Mounting is idempotent — no interval accumulation across date refetches.
- [x] **T2.7** ✅ 2026-05-01 — `grade_date` now grades suspended/postponed games normally if the 1st inning was complete before suspension. Otherwise marks SUSPENDED-no-bet as before. Combined with T1.5 regrade gate, resumed games eventually pick up real W/L.
- [x] **T2.8** ✅ 2026-05-01 — Cron schedule expanded to UTC 12-23 (every hour) so it covers both EDT (8am-7pm) and EST (7am-6pm) without manual DST shifting. No more November/March panic.
- [x] **T2.9** ✅ 2026-05-01 — Predictor writes `data/thresholds.json` on every run; `loadBoard` reads it; `BoardResponse.thresholds` flows through `BoardTable` → `BoardRowItem` → `TentativeChip`. Hardcoded TS defaults retained as fallback. No more drift between Python and TS classifiers.
- [x] **T2.10** ✅ Verified — actual GHA usage is ~890 min/month (mean 137s × 13 runs/day × 30 days), well under 2000 free-tier limit. Audit's pessimistic 1700-1800 estimate was wrong. No fix needed.
- [x] **T2.11** ✅ 2026-05-01 — FI weight cap now scales with sample size: 25% / 40% / 55% / 65% caps at 10/20/30/30+ FI IP. Linear ramp via `min(fi_ip / 50.0, cap)`. A pitcher with 30 FI IP (a full season's worth) now gets 60%+ weight instead of being arbitrarily capped at 40%.
- [x] **T2.13** ✅ 2026-05-01 — `log_picks` now warns on duplicate `(date, game_pk)` keys when building the index. Silent overwrite of DH-1 by DH-2 (rare but possible if MLB returns same pk) is now logged loudly.
- [x] **T2.14** ✅ 2026-05-01 — `pass_label_refresh` now requires existing_grade not in (WIN/LOSS/PASS) AND existing_bet != "Y". Belt-and-suspenders against any future code path that accidentally lets bet_placed=Y on a PASS row.
- [x] **T2.15** ✅ 2026-05-01 — `app/page.tsx` validates `?date=` against strict `YYYY-MM-DD` regex + calendar validity before passing to `loadBoard`. Invalid params fall through to null (latest available date) instead of being silently coerced to today.
- [x] **T2.16** ✅ 2026-05-01 — `scrape_dk_odds.py` hourly file overwrite. Each cron run was clobbering `data/odds/dk_<DATE>.csv` with whatever DK markets were currently OPEN, so a 5pm run that captured 1 game would erase the 8 captured at 9am. `picks_2026.csv` survived via UPSERT in the importer, but the daily file became a useless audit trail and any re-import would only see the residue of the last hourly run. Now: read existing → merge with fresh fetch (fresher snapshot wins per-game) → write merged. Also added 3-attempt exponential backoff on the DK API call and a smarter empty-fetch path: if 0 markets returned but the file already has rows from earlier today, exit 0 (success) instead of triggering the "stale category IDs" alarm. Tier 2 because the data loss was real even if `picks_2026.csv` didn't expose it directly — a future re-import flow would have noticed.
- [x] **T2.17** ✅ 2026-05-01 — `OddsChip` was hardcoded to render `null` whenever `pickSide === "PASS"`, which silently hid every captured DK price on PASS rows.  Today (5/01) all 15 games are PASS (LINEUP PENDING / STARTER PENDING / NO EDGE) because the model is waiting on lineups, so the dashboard showed zero odds chips even though 15/15 markets were imported correctly.  Now: PASS rows render a both-sides neutral chip (`DK -130 · +100`) so the user can confirm market coverage at a glance.  NRFI/YRFI rows keep the existing single-side chip with bet/skip styling.  No behavior change to the underlying odds capture or import flow.
- [x] **T2.18** ✅ 2026-05-01 — Odds got their own grid column (between PICK and EDGE) instead of being crammed inside the PICK cell.  Tone-coded by pickSide: warm-brown (`oddsNrfi`) for NRFI picks, red (`oddsYrfi`) for YRFI, desaturated muted (`oddsPending`) for PASS / LINEUP PENDING / STARTER PENDING.  Side labels (`N` / `Y`) prefix each price so it's unambiguous which way the line is — PASS rows show both sides (`DK  N -130 · Y +100`), active picks show the picked side only (`DK  N -135`).  Bet=N (skipped on edge) overlays a dashed border on the tone-colored chip.
- [x] **T2.19** ✅ 2026-05-01 — Deploy-overwrite race condition.  The Vercel project auto-deploys on every push to `claude/mlb-inning-run-predictor-QyazL`.  The GitHub Actions cron pushes ~12 commits/day (one per hourly `auto: predict <date>` run).  When a developer/agent ran `vercel --prod` with uncommitted local code changes, the manual deploy shipped local files — and the next cron push (within ~60 min) triggered a fresh auto-deploy that built from the REMOTE branch source (without the uncommitted changes) and silently overwrote the alias.  Today (5/01) this happened twice on T2.17 and T2.18 fixes.  Three-layer prevention: (1) `CLAUDE.md` at repo root with explicit deploy rules (auto-loaded by future agent sessions); (2) `dashboard/scripts/safe-deploy.sh` guard that aborts if working tree is dirty or local HEAD differs from origin; (3) `npm run deploy` wired to that guard.  The canonical deploy path is `git push` — Vercel auto-deploys from the push, which can never be raced because the alias points at THAT commit's build by design.
- [x] **T2.20** ✅ 2026-05-01 — Schedule-aware coverage alerting + overnight cron.  Previously `scrape_dk_odds.py` only alerted on 0 captures during prime hours (T1.4); 4/15 looked identical to 15/15 from the workflow's perspective.  Now: after every capture, the scraper queries `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD` for the day's game count, compares to captured count, and prints `WARNING: PARTIAL COVERAGE ...` to stderr if captured/scheduled < 80% during 9am-1pm ET.  StatsAPI fetch failures fall through silently (don't false-alarm just because StatsAPI is briefly down).  Also added overnight cron at 5 UTC (1am EDT / 12am EST) to catch DK's overnight opening lines for CLV tracking — the previous earliest cron at 12 UTC (7am ET) missed ~12hr of pre-game line movement.
- [x] **T2.21** ✅ 2026-05-01 — Doubleheader odds disambiguation.  The scraper merge keyed by `(date, away, home)`, so DH-1 and DH-2 (same teams, different start times) collided in the merge dict and only the second-listed game survived.  The importer's `by_team` lookup had the same issue: a single `int` per team key meant DH-2 clobbered DH-1, leaving DH-1 unmatched on every DH day.  Confirmed via 2026-04-30 HOU@BAL: G1 had no odds (graded LOSS un-priced), G2 did.  Fix: scraper now (a) emits `start_time_utc` (DK's `event.startEventDate`) per row, (b) `_row_key` includes start time so DH halves stay distinct.  Importer now (a) `by_team` is `dict[..., list[int]]` instead of `dict[..., int]`, (b) new `_pick_dh_candidate` helper picks the picks_2026 row whose `game_time_et` parses to a UTC time within 90 min of the odds row's `start_time_utc`, breaking ties by smallest delta.  Match priority: pk → teams+time → teams (legacy fallback for old odds files lacking the new column).  90-min tolerance is well inside half-the-DH-gap (typical DH-1 / DH-2 are ~3.5h apart) so they can never both match the same odds row.
- [x] **T2.22** ✅ 2026-05-01 — Telegram pick-flip notifier.  New `_notify_pick_flip_telegram` in `tracker.py` posts to a Telegram bot when a pick flips to/from an actionable state (STRONG / LEAN NRFI/YRFI).  Filters internally so PASS-variant churn (LINEUP PENDING ↔ STARTER PENDING ↔ NO EDGE) doesn't spam the user — only commits, demotes, and side-flips ping.  Wired into the existing `_record_pick_change` site so every cron flip both writes to `pick_changes.csv` AND pings Telegram.  Configured via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars; silent no-op when unset (keeps local dev quiet and stays back-compat).  Workflow `daily.yml` exposes both as secrets to the predict step.  Setup includes the bot `@nrfi_terminal_bot` (created via @BotFather), test message verified end-to-end.
- [x] **T2.24** ✅ 2026-05-01 — Two-part fix: (a) Predictor now distinguishes "STARTER PENDING" (TBD/unannounced pitcher name) from "NO DATA" (named pitcher with insufficient MLB stats — rookie debut / call-up).  Both trigger `pitcher_q='avg'` but the user-facing label was misleading: HOU@BOS 5/01 showed STARTER PENDING despite Boston naming Jake Bennett (his MLB debut, zero historical data).  Fix in `mlb_first_inning_predictor.py:2102-2125`: differentiate by checking `_name_announced(pitcher_name)`.  Truly TBD → STARTER PENDING; named-but-stats-fallback → NO DATA.  (b) `tracker._apply_odds_to_row` now auto-bets on every STRONG pick regardless of edge: user's policy is "if the model commits STRONG, we bet at whatever odds DK has".  LEAN keeps the 2% edge gate (model is less certain).  Retroactively flipped 2 historical bets from N→Y: 4/29 KC@OAK YRFI -135 (WIN, +0.741u) and 4/30 KC@OAK YRFI -130 (WIN, +0.769u) — previously contributing 0u, now correctly +1.51u to season P&L.  Plus 5/01 ATL@COL STRONG YRFI -150 flipped to Y (will grade tonight).
- [x] **T2.23** ✅ 2026-05-01 — Bet-time odds lock.  Once `bet_placed=Y` is recorded for a row, subsequent `--import-odds` runs no longer overwrite `market_*_odds`, `edge_*`, `bet_placed`, or `units_risked`.  Rationale: the user is already in the bet at the recorded price; further DK line movement is irrelevant to their position, and a moving OddsChip on the dashboard makes them second-guess a closed decision.  Trade-off: forgo closing-line capture on bet-placed games (`market_*` would otherwise track latest scrape and become the closing line).  `opened_*_odds` (T4.28) still records the FIRST price ever seen, so we preserve "open → bet" line movement — which is the CLV that matters since post-bet movement doesn't help the user.  P&L still computes correctly at lock time when the row grades (verified: locked +100 NRFI WIN → 0.769u).  Lock releases automatically if `bet_placed=Y` exists but `market_*_odds` are blank (corruption / legacy row).

- [x] **T2.25** ✅ 2026-05-01 — Bet-time pick lock.  Once a bet has been placed (`bet_placed=Y`), `tracker.log_picks` now also preserves `pick_side`, `pick_strength`, `pick_label`, `nrfi_prob`, `yrfi_prob`, `lambda_lr_t1/b1/total`, `combined_lambda`, `over/under_1_5_prob`, `blended_inputs` -- the full moment-of-bet snapshot freezes so a post-bet weather refresh / lineup tweak can't flip pick_side from STRONG YRFI to PASS-NO-EDGE underneath the user.  Confirmed via 2026-05-01 ATL@COL: wind at Coors Field dropped 11.9 → 5.6 km/h between morning + evening fetches; the 20-feature T1/B1 LR models pulled P(YRFI) from 0.587 → 0.551, demoting STRONG → PASS even though the user was already in the bet.  Plus dashboard `pickLabelText` now displays `PASS` (not `NO DATA`) on the pick chip; new `noDataReason()` helper builds a human-readable explanation in the tooltip ("Jake Bennett (BOS) has insufficient MLB stats — likely a rookie debut...").  Chip stays clean, hover reveals the why.
- [x] **T2.26** ✅ 2026-05-01 — Extended evening cron coverage.  Previous schedule was UTC 12-23 (= 8am-7pm ET in EDT, 7am-6pm ET in EST), so a 9pm or 10pm ET game's last data fetch was 2-3 hours pre-game.  Added UTC 0/1/2 (= 8pm/9pm/10pm EDT) so late games get a fresh weather + lineup snapshot within 60-90 min of first pitch before the T2.25 bet-time lock kicks in.  Cost: ~3 extra GHA runs/day (~6-9 min) — well under the 2000-min/month free-tier ceiling.  Considered Options B (per-game watcher) and C (event-trigger): rejected because GHA cron is best-effort with 1-3 hour drift on free runners, undermining per-game precision.  MLB / Open-Meteo / DK don't expose webhooks; "trigger-based" collapses into Option B with smaller intervals anyway.  Will revisit only if Phase 1 evidence shows bets actually flipping between 8pm cron and game time.

- [x] **T2.27** ✅ 2026-05-01 — Live-grade today on every predict cron.  Previous workflow only catch-up-graded *yesterday's* games during predict; today's games were graded once daily at the dedicated UTC 3:30 grade cron (11:30 PM ET).  Result: a STRONG bet that won in the bottom of the 1st at 7:30 PM ET wouldn't show as graded WIN on the dashboard until ~midnight, a 3-4 hour visibility delay.  Confirmed via 2026-05-01 PHI@MIA: bottom of 1st had a Miami run (YRFI hit) at ~7:40 PM ET; the row stayed `graded_result=` blank on the dashboard until manually live-graded.  Fix: `daily.yml` predict step now also calls `--grade --date $TODAY_ISO`.  `tracker.grade_date` already skips games whose 1st inning isn't complete (returns "not yet complete -- skipping"), so this is idempotent and safe to run hourly.  Soft-fails if grade-today errors so predict still runs.

**Tier 2 status: 27/27 complete.**

---

## 🟡 TIER 3 — Operational hygiene

- [x] **T3.1** ✅ 2026-05-01 — `/api/health` endpoint returns `{status, reasons, minutesSinceRefresh, latestBoard, latestPicks, recentErrors[]}`. Surfaces OK/STALE/DEGRADED/BROKEN status based on data freshness + recent system_errors.csv. Designed for Healthchecks.io / UptimeRobot pings.
- [x] **T3.2** ✅ 2026-05-01 — `daily.yml` `record_err` helper now POSTs JSON to `${{ secrets.ALERT_WEBHOOK_URL }}` (Slack/Discord/ntfy compatible) on every captured error. Stays silent if the secret is unset (back-compat).
- [x] **T3.3** ✅ 2026-05-01 — `/api/run-job` now optionally requires `body.secret == process.env.RUN_JOB_SECRET`. Endpoint stays open if env var unset (back-compat); enabled by setting RUN_JOB_SECRET in Vercel.
- [x] **T3.4** ✅ 2026-05-01 — New `.github/workflows/backup.yml` snapshots picks/boards/pick_changes/thresholds/system_errors into `data/backups/<YYYY-MM-DD>/` daily at 5am ET, prunes older than 30 days, commits + pushes.
- [x] **T3.5** ✅ 2026-05-01 — `_prune_change_log` runs at end of every `log_picks` invocation, keeping pick_changes.csv to last 90 days. Atomic rewrite via tempfile + os.replace; bounded growth.
- [x] **T3.6** ✅ 2026-05-01 — `requirements.txt` now pins upper bounds (`<2.0`, `<3.0`, etc.) so a major-version release of any dep can't silently break the predictor.
- [x] **T3.7** ✅ 2026-05-01 — `TARGET_BRANCH` now reads from `process.env.TARGET_BRANCH` in all three cron routes (run-job, cron/predict, cron/grade). Hardcoded fallback retained for back-compat.
- [x] **T3.8** ✅ 2026-05-01 — `_lr_predict_one` now logs a one-time WARNING per (model, feature_idx) when `std <= 0`. Previously silent skip; now visible in cron logs so a broken training set is surfaced quickly.
- [x] **T3.9** ✅ 2026-05-01 — `_load_fi_park_rates` now WARNs when the file is missing/empty/malformed, with a "run rebuild_park_factors.py" hint. Silent fallback to neutral 0.50 default no longer hides a de-featured model.
- [x] **T3.10** ✅ 2026-05-01 — New `DataQualityBadge` component on each board row shows a small `!` chip when ANY input is on a fallback (TBD pitcher, league-avg offense, lineup not posted). Two severity tones (high/med). Hover for full issue list.
- [x] **T3.11** ✅ 2026-05-01 — Added `LEAGUE_CONSTANTS_VERSION` and `LEAGUE_CONSTANTS_VERIFIED` stamps next to the constants. Comment now lists the procedure for refresh: refresh ALL constants together + rebuild park factors + refit calibrator.
- [x] **T3.12** ✅ 2026-05-01 — `_read_rows` now compares header against `FIELDS`, logs WARNINGs for unknown columns (will be dropped on next write) and missing columns (will be back-filled). Schema drift visible at read time.
- [x] **T3.13** ✅ Verified — Vercel cron entries at UTC 13/15/17/19/21/23 + GHA hourly at 12-23 UTC = redundant coverage at every hour. Single-point-of-failure at "even hours" claim is moot.
- [x] **T3.14** ✅ 2026-05-01 — `OddsChip` tooltip now shows odds capture freshness (`Captured 47 min ago`). User can tell stale odds (last night's import) from current ones.
- [x] **T3.15** ✅ 2026-05-01 — Lambda meter track now layers a low-contrast diagonal-stripe pattern over the gradient. Colorblind users have a non-color signal of position; sighted users barely notice.
- [x] **T3.16** ✅ 2026-05-01 — `ResultBadge` now has descriptive `aria-label`s: "Win. First inning 1 run away, 1 run home, actual side YRFI." Screen readers announce the full outcome.
- [x] **T3.17** ✅ 2026-05-01 — `.clickable:focus-visible` outline bumped from 2px inside-edge to 3px outside-edge with a soft glow. Keyboard users can clearly see which row is focused.
- [x] **T3.18** ✅ 2026-05-01 — Filters persist via URL params (shareable) AND localStorage (cross-session). `?side=NRFI&strength=STRONG&sort=lambda-desc` now works.
- [x] **T3.19** ✅ 2026-05-01 — `.rankTag` chip now has `overflow: hidden` + ellipsis on its text content so unusual placeholder strings ("After G10", "Suspended After G1") don't overflow the time column. Tooltip retains full text.
- [x] **T3.20** ✅ 2026-05-01 — `copy-data.mjs` now exits 1 (not 0) when source dir is missing AND `VERCEL || CI` env var is set. Builds in CI fail loudly if data is missing; local dev still gracefully skips.
- [x] **T3.21** ✅ 2026-05-01 — Closed with explicit comment in `mlb_first_inning_predictor.py` documenting the contract: `_BOARD_CSV_FIELDS` and `tracker.FIELDS` are intentionally different schemas (board = ranking summary, picks = full ledger). Canonicalizing would either bloat the board CSV or strip the ledger.
- [x] **T3.22** ✅ Already fixed in T1.1 — atomic write via tempfile + os.replace eliminates concurrency race.

**Tier 3 status: 22/22 complete.**

---

## 🟢 TIER 4 — Improvements / new features

### Model & ML
- [ ] **T4.1** — Catcher framing feature — DEFERRED (needs new data source, MLBAM xwOBA-allowed framing or Baseball Savant fork)
- [ ] **T4.2** — Umpire zone width feature — DEFERRED (needs umpire DB integration)
- [x] **T4.3** ✅ 2026-05-01 — Lambda floor now scales with weather: hot (≥28°C) +0.02, cold (≤12°C) -0.02, strong wind (≥24 km/h) +0.02. Dome games skip adjustment entirely. ±0.04 max range to avoid overcorrecting on a feature already in the LR.
- [ ] **T4.4** — Catcher-pitcher pairing — DEFERRED (model addition)
- [ ] **T4.5** — Refit LR with more features — DEFERRED (requires backtest training run)
- [x] **T4.6** ✅ 2026-05-01 — `_validate_calibrator_shape` runs at calibrator load. Logs WARNINGs when neighboring bins jump >5pp (sign of overfitting on small holdouts).
- [x] **T4.7** ✅ 2026-05-01 — `two_stage_model.py` now refuses to train if `--test` file is also in `--train` list (resolved-path comparison). Catches the canonical leakage failure mode.
- [ ] **T4.8** — Catcher framing data source — DEFERRED (covered by T4.1)

### Operations / Infrastructure
- [ ] **T4.9** — Migrate CSV → SQLite — DEFERRED (atomic-write fix in T1.1 already eliminates the race conditions)
- [ ] **T4.10** — Migrate CSV → Supabase/Postgres — DEFERRED (needs major infra refactor)
- [ ] **T4.11** — Backup picks_2026 to S3/Backblaze — DEFERRED (T3.4 GitHub-based backup already covers durability)
- [x] **T4.12** ✅ 2026-05-01 — `daily.yml` pings `${{ secrets.HEALTHCHECKS_URL }}` on success and `${HEALTHCHECKS_URL%/}/fail` on failure. Quiet no-op when unset. Combined with `/api/health` from T3.1 for full dead-man's-switch coverage.
- [ ] **T4.13** — Predict on Vercel directly — DEFERRED (major refactor)
- [ ] **T4.14** — Railway migration — DEFERRED (would need full pipeline rewrite)

### Dashboard / UX
- [x] **T4.15** ✅ 2026-05-01 — "Why this pick?" panel in expanded GameDetails. Shows top-5 LR feature contributions per half (signed `w*(x-mean)/std`) with friendly names + signed bars + raw values. Predictor writes `top_factors_t1_json` / `top_factors_b1_json` columns; dashboard parses + renders.
- [x] **T4.16** ✅ 2026-05-01 — `CalendarHeatmap` on /history: 7-row grid colored by day P&L (warm-brown for wins, red for losses, intensity = magnitude). Window-aware (only days in selected window are full-opacity).
- [x] **T4.17** ✅ 2026-05-01 — `ZoneHitRateChart` on /history: per-zone hit rate bars vs the 52.4% break-even line at -110. Above-break-even zones tinted brown, below tinted red. Shows zone P&L and bet count.
- [x] **T4.18** ✅ 2026-05-01 — Filter query now matches team abbrs OR either pitcher's name. Type "Verlander" to find every game he starts.
- [ ] **T4.19** — Saved filter presets — covered by T3.18 (URL/localStorage persistence)
- [x] **T4.20** ✅ 2026-05-01 — Browser notifications on pick flips (opt-in via 🔕/🔔 toggle in header). Tab-active only; service worker not needed. Compares pickChanges array between refetches and notifies on new entries.
- [x] **T4.21** ✅ 2026-05-01 — Below 600px the board switches to a 2-column card layout with 44px touch targets. Tested at 360px width. iOS HIG compliant.
- [x] **T4.22** ✅ 2026-05-01 — Result column header is now a sort toggle. Click to group by graded outcome (W → L → PASS → PP → ungraded), within bucket falls back to original order.
- [x] **T4.23** ✅ 2026-05-01 — `CalibrationPlot` on /history: scatter of predicted-prob vs actual-hit-rate per zone with diagonal y=x reference. Dot size = bet count. Stems show direction of miscalibration.
- [x] **T4.24** ✅ 2026-05-01 — Multi-row expand: clicking row toggles its expansion without closing others. Pin 2+ games open and scroll to compare their "Why this pick?" panels + lineup cards side-by-side.

### Money management — ALL SKIPPED PER USER PREFERENCE (sticking with flat 1u plays)
- [ ] **T4.25** — Kelly fraction sizing — SKIPPED (user preference)
- [ ] **T4.26** — Bankroll-aware bet sizing — SKIPPED (user preference)
- [ ] **T4.27** — Min/max edge thresholds per zone — SKIPPED (user preference; current 2% threshold works)
- [x] **T4.28** ✅ 2026-05-01 — CLV tracking: new `opened_*_odds`, `opened_captured_at`, `clv_pct` columns. `opened_*` is set ONCE on first odds import (never overwritten); `market_*` keeps tracking the latest scrape so it ends up as the closing line when DK pulls the market. CLV % = closing implied prob - opened implied prob, on the picked side. Positive = market moved toward our pick = we beat the close.

**Tier 4 status: 14/28 shipped, 11 deferred (substantial work / new data sources), 3 skipped per user preference.**

---

## Working order (Tier 1, this week)

1. **T1.1** atomic CSV write — eliminates the "lost run" race-condition class. ~30 min.
2. **T1.7** safe_float negative guard — single-line fix. ~5 min.
3. **T1.8** schedule fetch retry — wrap in retry helper. ~15 min.
4. **T1.5** postponed regrade — flip the skip condition. ~10 min.
5. **T1.4** DK scraper 0-game alert — emit warning when empty. ~20 min.
6. **T1.2** dashboard DH detail key — refactor row→detail join. ~30 min.
7. **T1.3** workflow `|| true` removal + error capture — write to `system_errors.csv`. ~45 min.
8. **T1.6** push retry overhaul — unbounded with jitter + CSV-only conflict resolution. ~45 min.
9. **T1.9** verify T1.1 + add 5s sleep before push. ~10 min.

Total estimated effort: ~3.5 hours of focused work plus testing.

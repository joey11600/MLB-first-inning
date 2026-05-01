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

**Tier 2 status: 15/15 complete.**

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
- [ ] **T3.21** — Deferred. `_BOARD_CSV_FIELDS` and `FIELDS` are different schemas by design (board CSV is a small ranking projection; picks CSV is the full ledger). Making them canonical would require a bigger reorg. Not actively breaking anything.
- [x] **T3.22** ✅ Already fixed in T1.1 — atomic write via tempfile + os.replace eliminates concurrency race.

**Tier 3 status: 21/22 complete (T3.21 intentionally deferred — see note).**

---

## 🟢 TIER 4 — Improvements / new features

### Model & ML
- [ ] **T4.1** — Add catcher framing feature (top-3 ABs face listed catcher; ~+2-4% NRFI edge documented)
- [ ] **T4.2** — Umpire zone width feature (announced 24h pre-game, public databases available)
- [ ] **T4.3** — Lambda floor scaling with weather (wind-out-to-CF games warrant higher YRFI threshold)
- [ ] **T4.4** — Catcher-pitcher pairing for game-script effects
- [ ] **T4.5** — Refit LR with more features (away_fip, away_bb9, away_obp, weather, park) — current 4-feature model probably underfits
- [ ] **T4.6** — Validate calibration shape (PAV-only allows jumps; add Lowess smoother check)
- [ ] **T4.7** — Backtest holdout (confirm proper train/test split, no leakage)
- [ ] **T4.8** — Catcher framing data source: MLBAM xwOBA-allowed framing or Baseball Savant fork

### Operations / Infrastructure
- [ ] **T4.9** — Migrate CSV → SQLite (eliminates T1.1, T2.5, T3.22 race conditions in one move)
- [ ] **T4.10** — Migrate CSV → Supabase/Postgres (adds dashboard query speed + multi-writer safety)
- [ ] **T4.11** — Backup picks_2026 to S3/Backblaze daily (~$0.50/mo)
- [ ] **T4.12** — Healthchecks.io ping on every successful cron (dead-man's-switch)
- [ ] **T4.13** — Move predict logic into Vercel Function directly (drop GHA dispatch layer; eliminates 1-3h cron lag)
- [ ] **T4.14** — Migrate to Railway ($5/mo) for native Python cron with sub-30s lag

### Dashboard / UX
- [ ] **T4.15** — "Why this pick?" panel — feature-importance breakdown (top 5 contributors via LR weights × normalized inputs)
- [ ] **T4.16** — Calendar heatmap on /history (green/red days, click-to-drill)
- [ ] **T4.17** — Win-rate by zone chart (STRONG NRFI hits at X%, LEAN NRFI at Y%, etc.)
- [ ] **T4.18** — Pitcher search across slate
- [ ] **T4.19** — Saved filter presets ("STRONG only", "high-edge only", "lineups posted")
- [ ] **T4.20** — Browser notifications (opt-in) for "high-edge bet appeared" / "pick flipped"
- [ ] **T4.21** — Mobile card layout (board doesn't gracefully collapse below ~700px)
- [ ] **T4.22** — Sortable result column (currently only edge; add lambda, strength, start time)
- [ ] **T4.23** — Bet-history tab grouped by zone, with calibration plot (predicted % vs actual hit %)
- [ ] **T4.24** — Side-by-side game compare (pick 2 games, see feature deltas)

### Money management
- [ ] **T4.25** — Kelly fraction sizing (currently units_risked = 1.0 fixed; scale by edge × bankroll)
- [ ] **T4.26** — Bankroll-aware bet sizing (pull bankroll from config, stake fractionally)
- [ ] **T4.27** — Min/max edge thresholds per zone (different cutoffs for STRONG vs LEAN)
- [ ] **T4.28** — Closing-line value tracking (capture odds at lockout vs current; CLV is the leading EV indicator)

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

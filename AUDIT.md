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

- [ ] **T2.1** — PASS-only days hide-then-show in cumulativePL (verify seeding hits consistently) — `dashboard/lib/roi.ts`
- [ ] **T2.2** — Lock-preserve format parsing fragile — `tracker.py:204-229` (game_time_et without "ET" silently returns False; bet snapshot can be overwritten)
- [ ] **T2.3** — `bet_placed="N"` zeroes `units_risked` (audit trail loss) — `tracker.py:876-882`
- [ ] **T2.4** — Spring training games leak into pitcher-blend weights — `backtest.py:305-323` (filter `gameType=R` only)
- [ ] **T2.5** — `pick_changes.csv` append race: duplicate headers if two crons land within 1s — `tracker.py:506-510`
- [ ] **T2.6** — Memory leak risk in dashboard auto-refetch interval (deps array recreates interval) — `DashboardShell.tsx:61-79`
- [ ] **T2.7** — Suspended-game mid-1st grading broken (resumed game new pk) — `backtest.py:309, tracker.py:364`
- [ ] **T2.8** — DST transition not automated (Nov 2 silently mis-times everything) — `daily.yml:3-4`
- [ ] **T2.9** — Tentative-pick thresholds duplicated in TS (must match Python by hand) — `BoardRow.tsx:29-33`
- [ ] **T2.10** — GHA free tier at 85-90% quota (1700-1800 / 2000 min/mo) — `daily.yml`
- [ ] **T2.11** — Pitcher blending weight cap (40%) arbitrary, doesn't scale with FI sample size — `predictor.py:639-642`
- [ ] **T2.12** — `_pick_is_locked` returns False on parse failure of `game_time_et` (could allow overwrite of locked bet) — `tracker.py:204-229`
- [ ] **T2.13** — DH game_pk collision not detected — `tracker.py:248-252` (silent index overwrite if MLB returns same pk for DH-1 + DH-2)
- [ ] **T2.14** — `pass_label_refresh` flips PASS strength repeatedly across cron runs — `tracker.py:444-449`
- [ ] **T2.15** — Date param `?date=2099-12-31` silently swaps to today — `app/page.tsx:11`

---

## 🟡 TIER 3 — Operational hygiene

- [ ] **T3.1** — No `/api/health` endpoint for liveness — `dashboard/app/api/`
- [ ] **T3.2** — No Slack/email alerts on cron failure — `daily.yml`
- [ ] **T3.3** — `/api/run-job` publicly callable (no auth) — `dashboard/app/api/run-job/route.ts`
- [ ] **T3.4** — No automated backups (`data/backups/` is manual) — `data/backups/`
- [ ] **T3.5** — `pick_changes.csv` grows unbounded (no rotation) — `tracker.py`
- [ ] **T3.6** — `requirements.txt` lower bounds only (no `<2.0` upper) — `requirements.txt`
- [ ] **T3.7** — TARGET_BRANCH hardcoded in 3 cron route files — `run-job, cron/predict, cron/grade`
- [ ] **T3.8** — LR-v4 features with `std<=0` silently skipped (not flagged) — `predictor.py:770-772`
- [ ] **T3.9** — FI park factors silently default to 0.50 if file missing — `predictor.py:695`
- [ ] **T3.10** — Pitcher ID=None ("TBD") doesn't surface "data quality degraded" badge — `predictor.py:419`
- [ ] **T3.11** — Hardcoded league constants stale (no version stamp) — `predictor.py:65-86`
- [ ] **T3.12** — CSV schema not validated on read — `tracker.py:154-164`
- [ ] **T3.13** — Vercel cron gap at even hours (10/12/2/4/6/8 PM) — `vercel.json`
- [ ] **T3.14** — OddsChip can show stale odds without "captured at" timestamp — `BoardRow.tsx:259-286`
- [ ] **T3.15** — Color-only signaling on lambda meter (a11y) — `LambdaMeter.tsx`
- [ ] **T3.16** — Result badge "0-2" has no aria-label for screen readers — `BoardRow.tsx ResultBadge`
- [ ] **T3.17** — No keyboard focus indicator visible on board rows — `BoardRow.module.css`
- [ ] **T3.18** — Filters don't persist across page reloads — `ControlPanel.tsx`
- [ ] **T3.19** — DH-2 "After G10" (10-game DH) overflows 82px column — `BoardRow.tsx:320-332`
- [ ] **T3.20** — `copy-data.mjs` exits 0 silently if source missing (build ships empty data) — `dashboard/scripts/copy-data.mjs`
- [ ] **T3.21** — `_BOARD_CSV_FIELDS` and `FIELDS` not enforced as canonical; column drift across writers possible — `predictor.py + tracker.py`
- [ ] **T3.22** — Concurrency in `_write_rows` has no locking — `tracker.py:168` (subset of T1.1)

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

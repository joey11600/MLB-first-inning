# NRFI Terminal — Knowledge Base

Single-page overview for future Claude (or human) sessions picking up this
project. Read this first; it pointers out to the deeper docs at the bottom.

Last refreshed: **2026-05-01**.

---

## What this is

An MLB first-inning **NRFI** (no run first inning) / **YRFI** (yes run first
inning) prediction system. Daily pipeline:

1. **Predict** — pulls schedule + pitcher/team stats from MLB StatsAPI,
   feeds a 4-feature logistic regression, produces a board of picks (STRONG
   NRFI / LEAN NRFI / PASS / LEAN YRFI / STRONG YRFI per game).
2. **Track** — appends picks to `data/picks_2026.csv`, captures DraftKings
   odds on the picked side, computes edge vs implied prob, and flags
   `bet_placed=Y` when edge clears the 2% threshold.
3. **Grade** — re-pulls box scores; writes `graded_result` (WIN/LOSS/PASS),
   first-inning runs, and `profit_loss_units` per row.
4. **Display** — Next.js dashboard ([dashboard-pink-seven-64.vercel.app](https://dashboard-pink-seven-64.vercel.app))
   reads the CSVs, renders the board with live odds chips + result badges +
   a "Why this pick?" panel showing LR feature contributions.

The cron loop runs every hour 12-23 UTC via GitHub Actions, with redundant
Vercel cron entries every 2 UTC hours as backup.

---

## Performance snapshot — 2026-05-01

(Refresh this section in CHANGELOG.md when you push performance-relevant changes.)

| Window | Active picks (W-L) | Win rate |
|---|---|---|
| Yesterday (4/30) | 4-2 | **66.7%** |
| Last 7 days | 23-10 | 69.7% |
| Season-to-date (April) | 113-63 | **64.2%** |
| └ NRFI side | 35-11 | 76.1% |
| └ YRFI side | 78-52 | 60.0% |

**Dashboard total P&L** (every pick at real-odds-where-imported, else -110):
**+39.6u over 176 graded picks**.  Real-odds-only subset (4 bets,
`bet_placed=Y`): -0.49u — but that's a coverage artifact, not a model
problem. The DK scraper landed 2026-04-29; pre-04-29 picks have no odds
and never will (DK doesn't expose history).

Historical break-even rates from 4,802-game backtest:

| Zone | Hist win rate | Break-even odds |
|---|---|---|
| STRONG NRFI (P≥0.60) | 57.5% | -135 |
| LEAN NRFI (0.53-0.60) | 53.5% | -115 |
| LEAN YRFI (0.40-0.47) | 54.5% | -120 |
| STRONG YRFI (P<0.40) | 62.9% | -169 |

Picks accuracy is currently ~7pp above the long-run expectation. The gap
between pick accuracy (64%) and executed-bet record (50%) is driven by
how few games clear both "odds captured" AND "edge ≥ 2%".

---

## Architecture

```
                 ┌─────────────────────────────────────────────────┐
                 │           GitHub Actions (UTC 12-23)            │
                 │                                                 │
   StatsAPI ───► │ predict step → mlb_first_inning_predictor.py   │
   Open-Meteo    │   ├─ writes data/boards/board_<DATE>.csv        │
   DK API ─────► │   └─ writes data/picks_2026.csv (append)        │
                 │                                                 │
                 │ scrape DK odds → scrape_dk_odds.py              │
                 │   └─ writes data/odds/dk_<DATE>.csv             │
                 │                                                 │
                 │ grade step → tracker.grade_picks                │
                 │   └─ updates picks_2026.csv graded_result       │
                 │                                                 │
                 │ commit + push (8-attempt jittered retry)        │
                 │   └─ ALERT_WEBHOOK_URL on failure (T3.2)        │
                 │   └─ HEALTHCHECKS_URL ping (T4.12)              │
                 └─────────────────────────────────────────────────┘
                                          │ git push
                                          ▼
                 ┌─────────────────────────────────────────────────┐
                 │  Vercel auto-deploy (joeys-projects/dashboard)  │
                 │   prebuild: copy ../data → ./data               │
                 │   /api/board, /api/details, /api/health          │
                 │   live URL: dashboard-pink-seven-64.vercel.app  │
                 └─────────────────────────────────────────────────┘
```

The single source of truth is the CSV pair: `data/picks_2026.csv` (the
ledger, all 97 columns) and `data/boards/board_<DATE>.csv` (the day's
ranked summary, leaner schema). They are intentionally different schemas
— see T3.21 in AUDIT.md.

---

## File map

### Predictor / model
- `mlb_first_inning_predictor.py` — live predictor entry point. Loads LR
  + park factors lazily; falls back to `classify_pick(lambda)` if missing.
  Functions to know: `lr_active()`, `lr_features()`, `lr_predict_nrfi()`,
  `classify_pick_lr()`, `_lr_feature_contributions()` (for the Why-panel).
- `data/lr_model.json` — production LR weights/bias/standardization.
- `data/fi_park_factors.json` — empirical first-inning NRFI rate per park.
- `data/calibration_v2.json` — isotonic P(NRFI) calibrator.
- `data/thresholds.json` — written every run; sourced by both Python and
  TS classifiers (T2.9).
- `lr_baseline.py` — LR fit/eval CLI. `--save PATH` writes a production model.
- `backtest.py` — builds historical slates with prior-year stats. Has
  `--build-park-factors CSV...` for park-factors regen. Cache namespace
  `data/cache/`. Filters `gameType in VALID_GAME_TYPES` (T2.4).
- `two_stage_model.py` — train/eval for the T1+B1 split model. Refuses to
  train if `--test` is in `--train` list (T4.7 leakage guard).

### Tracker / grading
- `tracker.py` — `log_picks`, `grade_picks`, `_apply_odds_to_row`. Atomic
  writes via tempfile + fsync + os.replace (T1.1). 3-lock pick freezing
  (T2.2 + T2.12). Re-grades POSTPONED (T1.5).
- `data/picks_2026.csv` — full ledger, 97 columns. Append-only on first
  pick of the day; updates in-place for grading + odds.
- `data/pick_changes.csv` — every pick flip logged (90-day rolling, T3.5).
- `data/system_errors.csv` — every cron failure (T1.3).

### Workflows / ops
- `.github/workflows/daily.yml` — predict + grade + scrape every hour
  UTC 12-23. 8-attempt push retry with `--ours` for CSV conflicts (T1.6).
- `.github/workflows/backup.yml` — daily 5am ET snapshot (T3.4).
- `requirements.txt` — pinned with upper bounds (T3.6).

### Dashboard (Next.js, lives in `dashboard/`)
- `app/page.tsx` — server component, `loadBoard(date?)`. Strict
  YYYY-MM-DD validation on `?date=` (T2.15).
- `app/api/board/route.ts` — board API.
- `app/api/details/route.ts` — game details (lineup, pitcher cards).
- `app/api/health/route.ts` — health endpoint (T3.1).
- `app/api/cron/{predict,grade}/route.ts` — Vercel cron entrypoints
  (redundant with GHA).
- `components/BoardTable.tsx` / `BoardRow.tsx` — main board.
- `components/GameDetails.tsx` — expanded panel; renders `WhyThisPickPanel`,
  lineup cards, pitcher comparison.
- `components/HistoryView.tsx` — /history page; CalendarHeatmap (T4.16),
  ZoneHitRateChart (T4.17), CalibrationPlot (T4.23).
- `components/DashboardShell.tsx` — filter persistence (T3.18), pitcher
  search (T4.18), browser notifications (T4.20).
- `lib/board.ts` — `loadBoard`, `loadDetails`, `parseFactorsJson`,
  `loadThresholds`, DH-aware detail-key composition (T1.2).
- `lib/types.ts` — TS types for everything.
- `scripts/copy-data.mjs` — prebuild copy of `../data` → `./data` with
  whitelist (boards/picks/pick_changes/thresholds/system_errors).

---

## Daily operations cycle

You don't normally have to do anything — GHA runs the full cycle hourly.
Manual interventions:

| Need | Command |
|---|---|
| Force a fresh slate now | `python mlb_first_inning_predictor.py --date 2026-05-01` |
| Re-grade a date | `python tracker.py grade --date 2026-04-30` |
| Re-scrape DK odds | `python scrape_dk_odds.py` |
| Redeploy dashboard | `cd dashboard && npx vercel --prod` |
| Check health | `curl https://dashboard-pink-seven-64.vercel.app/api/health` |
| Run a one-off backtest | `python backtest.py --season 2026` |

If GHA is broken: check the latest run at https://github.com/joey11600/MLB-first-inning/actions
and read `data/system_errors.csv` for structured failure rows.

---

## How to make changes

### Adding a feature to the LR model

1. Backfill the column into the backtest CSV via a `backfill_*.py` script
   (one exists per feature class). The CSV is rebuilt from the cache, so
   if you add the column to `backtest.py` and rerun `--season`, it lands.
2. Cross-validate with `test_*.py`-style 3-split (2024→2025, 2025→2024,
   2024+2025→2026). Reject if it helps in only one direction.
3. Refit the production LR on the two most recent completed seasons:
   ```
   python lr_baseline.py \
     --train data/backtests/backtest_<Y-1>_to.csv \
              data/backtests/backtest_<Y>_to.csv \
     --features <existing-4-features> <new-feature> \
     --save data/lr_model.json
   ```
4. Rebuild park factors from the same CSVs.
5. The live predictor picks up the new model automatically on next invocation.

The current 4-feature production model:
1. `fi_park_nrfi_rate` — empirical first-inning NRFI rate at home park
2. `home_fip` — home pitcher FIP (season)
3. `home_hr9` — home pitcher HR/9
4. `home_bb9` — home pitcher BB/9

See `~/.claude/projects/.../memory/nrfi_model_architecture.md` for the
full list of tested-and-rejected features (don't redo that work).

### Modifying the dashboard

**The canonical deploy is `git push`.** Vercel auto-deploys from any push
to `claude/mlb-inning-run-predictor-QyazL`. Workflow:

```
cd dashboard
npm install                   # one-time
npm run dev                   # local at http://localhost:3000
# make changes…
npm run build                 # verify build passes
cd ..
git add <files>
git commit -m "..."
git push origin claude/mlb-inning-run-predictor-QyazL
# Auto-deploy lands within ~60s. Verify with:
#   curl -sL https://dashboard-pink-seven-64.vercel.app/ | grep -c "<your-marker>"
```

**DO NOT run `vercel --prod` or `npx vercel --prod` directly** for code
changes — this caused real incidents on 2026-05-01 (see CLAUDE.md and
T2.19 in AUDIT.md). The cron pushes ~12 `auto: predict` commits/day,
and each one triggers an auto-deploy from the remote branch source. A
manual CLI deploy with uncommitted local changes will be silently
overwritten by the next cron push's auto-deploy.

If you genuinely need a CLI deploy (env-var test, emergency rollback,
non-code change), use `cd dashboard && npm run deploy` — it runs
`scripts/safe-deploy.sh` which aborts if the working tree is dirty or
the local branch isn't pushed.

The prebuild data-copy quirk is documented in `nrfi_dashboard.md`. If you
touch `next.config.mjs`, `package.json` scripts, or `lib/board.dataDir()`,
re-verify end-to-end with a fresh build.

### Modifying cron behavior

`.github/workflows/daily.yml` — UTC schedule covers EDT/EST without DST
shifts. Be careful with `record_err` helper: it logs to `system_errors.csv`
AND pings `ALERT_WEBHOOK_URL` AND emits `::warning::`. Don't break the
ledger format; downstream `/api/health` parses it.

---

## What's NOT in the model (already tested, didn't help)

Important to avoid retreading. From cross-validation across 2024/2025
backtests, none of these improved out-of-sample Brier:

- Top-3 batter aggregates (prior-year and current-season)
- Pitcher last-5 / last-10 NRFI history (informational only on dashboard)
- Weather (temp / wind / humidity)
- Wind × park-orientation interactions
- Pitcher current-season blend stats
- 2022 / 2023 backtest data (pitch-clock distribution shift)
- Handedness / platoon advantage features
- xERA alone, xwOBA alone (only `xera + whiff` combo helped, see Phase E.3)
- Umpire NRFI rate alone

Full list with mechanism explanations in `nrfi_model_architecture.md`.

---

## What MIGHT work (untested, in backlog)

See `~/.claude/projects/.../memory/variant_backlog.md` for prioritized
ideas. Top candidates:

1. **Catcher framing runs** — auto-scheduled to investigate 2026-05-15
   via remote agent (T4.1).
2. **`whip_gap_signed`** — derived from existing CSV columns; quick test.
3. **Umpire zone width** — needs umpire DB (T4.2).
4. **Pitcher × lineup interaction** — multiplicative explicit feature.

Test methodology: `test_era_gap.py` template. Ship if total 3-split P&L
beats baseline by ≥10u AND no STRONG hit-rate regression on holdout.

---

## Where else to look

| Document | What's there |
|---|---|
| [AUDIT.md](../AUDIT.md) | Open / closed audit items, all 4 tiers |
| [CHANGELOG.md](../CHANGELOG.md) | Dated log of shipped changes + perf snapshots |
| `~/.claude/projects/.../memory/nrfi_model_architecture.md` | Model internals, feature list, retraining policy |
| `~/.claude/projects/.../memory/nrfi_dashboard.md` | Dashboard URL + redeploy quirks + visual identity |
| `~/.claude/projects/.../memory/variant_backlog.md` | Untested feature ideas (Tier 1-4) |
| `~/.claude/projects/.../memory/MEMORY.md` | Memory index |

The memory files persist across Claude sessions. The repo files (this KB,
CHANGELOG, AUDIT) are the source of truth for the project itself and are
versioned with the code.

---

## Conventions

- Audit IDs are `T<tier>.<n>` — e.g. `T1.1`, `T4.15`. Stable forever; never
  recycled.
- Every shipped audit item has a `✅ <date>` mark in AUDIT.md and a row in
  CHANGELOG.md.
- Don't introduce a new `T?.?` ID without adding it to AUDIT.md first.
- Don't commit raw API responses or sensitive odds-feed credentials. The
  DraftKings categoryId/subcategoryId in `scrape_dk_odds.py` are not
  secrets (they're public API IDs) but `RUN_JOB_SECRET` and webhook URLs
  ARE secrets and live in env / Vercel settings only.
- The CSV ledger format is stable. If you must add a column, do it at the
  END so existing parsers don't choke on positional fields. `tracker._read_rows`
  warns on schema drift (T3.12).

# NRFI Terminal — Knowledge Base

Single-page overview for future Claude (or human) sessions picking up this
project. Read this first; it pointers out to the deeper docs at the bottom.

Last refreshed: **2026-05-04** (T4.2 priors-pooling deployed as the
permanent production path; full diagnostic stack T4.4–T4.9 now monitors
it nightly).  See [CHANGELOG.md](../CHANGELOG.md) for the 2026-05-04 entry.

## TL;DR for someone picking this up cold

The model is a logistic-regression NRFI predictor with **20 features per
half-inning** (19 season-average inputs + the 2026-08-22 pooled
first-inning pitcher xwOBA, L2 0.5), calibrated by isotonic regression.  As of 2026-05-04 the
production pipeline reads pitcher xera/whiff via **Bayesian-pooled
priors** (2025 prior + 2026 cumulative-thru-yesterday) instead of the
raw season cache, because raw 2026 small-sample stats produced extreme
outliers (xera=14.71 from 5 batted balls) that drove confident-but-
wrong bets and lost -4.56u in a single day on 2026-05-03.

Six diagnostic layers run nightly so a regression like that surfaces
within hours instead of days:

  - `tools/feature_drift_monitor.py` (T4.5) detects pitcher_q + xera shifts
  - `tools/pick_reasoning_log.py`    (T4.6) per-pick feature contributions
  - `tools/v21_shadow_predict.py`    V2.1-vs-V2.2 shadow track (observability)
  - `docs/PLAYBOOK.md`               (T4.8) when-to-do-what process doc
  - `tools/model_gate.py` + `.github/workflows/model_gate.yml` (T8.29)
    re-scores a fixed 3,728-game 2024+2025+2026 holdout before/after a push
    and reports whether predictions moved, per season. FAILS the build on a
    worse/mixed result, but does NOT stop the push or the deploy -- branch
    protection is deliberately OFF; see PLAYBOOK 4a

Three of the original six layers are GONE, removed 2026-05-06 with the
V2/T4.2 shadow surface (b125aa45): `daily_shadow_report.py` (T4.4),
`shadow_gate.yml` (T4.7) and `ShadowDeltaCard.tsx` (T4.9). This list
still named all three on 2026-08-10 — the shadow_gate line in particular
promised an automatic pre-merge model check that had not existed for
three months. **Nothing PREVENTS a predictor change from shipping.** The T8.29 gate
fails the build on a worse result, but the branch is unprotected, so a red
gate is a loud record, not a veto. Required status checks were tested
2026-08-10 and REJECT direct pushes outright -- which would stop the
predictor cron writing picks. See PLAYBOOK 4a.

If something breaks: **read PLAYBOOK.md first.** It routes every common
failure mode to the specific tool that gives you the answer in <5 min.

---

## The performance panel (rebuilt 2026-07-28)

Two surfaces that must never be confusable:

| surface | what it is | visual language |
|---|---|---|
| **Ledger** (`TotalCard`) | real money, real DK prices, bets actually placed | raised card, tone rail, figures take `--primary` / `--destructive` |
| **Model replay** (`SeasonRecordCard`) | a simulation of what the current model would do | recessed card, hatched left rail, `.simCard` forces every figure to `--foreground !important` |

**`data/season_record.json`** (written by `tools/export_season_record.py`,
served verbatim by `/api/season-record`) carries two figure sets per side:

  - the **headline**, scored with the shipped calibrator at the live gate
    — "what is the system I am running worth"
  - the **floor** (`side.floor`), a walk-forward calibrator refit at each
    date from strictly earlier games — the no-hindsight bound

The compounded Kelly bankroll lives under `side.sim`, never at the top
level. `side.flatProfit` is the headline. `projected` and `real` are both
nullable.

`days[]` carries a per-date reconciliation: every game either the live
ledger flagged STRONG or the current model would bet, with both
dispositions and a machine-readable skip `code`.

**Two rules that exist because breaking them caused a real incident:**

1. **Every bet count on the page comes from `dashboard/lib/reconcile.ts`.**
   The panel once showed `6 STRONG YRFI`, `4 graded bets` and `1 bet` for
   the same night because three components each derived their own count.
2. **`days[].flatPnl` is the REPLAY's flat P&L, not the ledger's**
   (2026-07-27: −1.00 vs −0.33). Sum `games[].ledger.pnl` for the
   operator's own number.

Card inventory: `TonightsActionCard` → `TotalCard` → zone cards →
`DayReconcile` → `SeasonRecordCard` → lean block → no-bet row.
`KellyCard` and the standalone `LeanPaperTradeCard` no longer exist.

Types live in `dashboard/lib/season-record.ts` — one definition. A stale
inline copy is not a type error, it is a runtime crash.

---

## Three surfaces, three questions (2026-08-03)

| route | question | scene |
|---|---|---|
| `/` | what do I bet, and how much | phone, 30 seconds, hour before first pitch |
| `/brief` | why is this the play | desk, camera running, read aloud |
| `/history` | how has this gone | desk, tolerates density |

**`/brief`** is the explanatory surface. Bare `/brief` shows the night's
#1; `?game=<gamePk>` briefs a specific game and `?date=` picks the slate.

**Which rows have a brief: `briefVerdictOf()` in `lib/classify.ts`, and
nowhere else (2026-08-04).** Both the page and the board's `BriefLink`
button call it, so the button and the page cannot disagree. It returns
`{side, strength, pending}` or null:

| row | brief? | why |
|---|---|---|
| `STRONG` | yes, staked | the bet |
| `LEAN` | yes, no stake | a verdict the ledger grades, never wagered |
| `LINEUP PENDING` + clear tentative lean | yes, no stake, "not locked in" | the model waiting, not declining |
| `LINEUP PENDING` + tentative PASS | no | genuinely no opinion |
| `STARTER PENDING` | no | pitcher data is fallback on BOTH sides — the page's two biggest figures would be fabricated |
| any other PASS | no | a case would have to be invented |

Pending rows are in because **every figure the brief shows is already
final**: park rate, both teams' last-10 first innings, both starters'
scoreless-first records and the head-to-head all come from team codes,
pitcher ids and the ledger. None reads the lineup. Only the verdict is
provisional. This was a real bug on 2026-08-04, when 14 of 15 games were
pending and the only brief button on the board was the #1's.

**Read the verdict's side, never the row's, once pending rows are in
scope.** A `LINEUP PENDING` row's `pickSide` is `"PASS"`; using it briefs
the wrong half of the inning and scores every reason against it. The same
applies to pricing — `oddsOn()` takes an explicit side.

**A lean never prints a stake.** `stake` is nulled for a LEAN in the page
before the view sees it, because quarter Kelly will happily size one and
the tracker marks every lean `bet_placed='N'` by rule. The page says "not
a bet" in the tag, in the ticket, and in a paragraph — this surface is
read aloud, where a qualifier stated once falls off in the edit.

- `lib/first-inning-form.ts` — per-team last-10 first-inning form,
  per-pitcher scoreless-first record, park rate + rank, head-to-head and
  current series. **Derived entirely from `picks_<season>.csv`**, which
  logs every game on every slate plus its graded first-inning line. No new
  scraper. The one exception is a pitcher's last-10 rate: the STORED
  `*_p_last10_pitcher_nrfi` (from StatsAPI) is authoritative and the
  ledger reconstruction agrees on 540 of 562 checks, the misses all one
  game apart because the ledger holds the PROBABLE starter.
- `lib/pick-reasons.ts` — model features to speakable sentences. Where
  `data/diagnostics/picks/<date>.json` exists (last 7 days ship), the
  model's own contribution magnitudes order the reasons.
- `lib/team-names.ts` — abbreviation to "Tampa Bay" / "the Rays".
- `lib/price-ladder.ts` — "bet up to -234", plus how many units at each
  price down to it. Pure and client-safe (BoardRow is a client
  component); rungs come from `stakeUnitsFor` so they can never
  contradict the stake chip. **Its limit is the worst price still worth
  a full unit, NOT break-even, and it deliberately stops short of the
  shipped stake rule** — `KELLY_ROUNDED_FLOOR` keeps betting 0.5u past
  it on almost no edge, and those are exactly the bets a subscriber
  would take at a published limit. Read the file header before changing
  either boundary.

**One rule that exists because it would be filmed:** the #1 pick is
selected by `selectTopPick()` in `lib/top-pick-rank.ts` and nowhere else.
The board badge, the history card and the brief all call it. Three
surfaces answering "which game is #1" with their own fold is how this
dashboard has produced contradictions before.

**`fi_park_factors.json` must stay in `scripts/copy-data.mjs`.** Without
it the brief silently drops its ballpark reason on deployed builds only,
which is the hardest class of bug to notice.

---

## What this is

An MLB first-inning **NRFI** (no run first inning) / **YRFI** (yes run first
inning) prediction system. Daily pipeline:

1. **Predict** — pulls schedule + pitcher/team stats from MLB StatsAPI,
   feeds a 4-feature logistic regression, produces a board of picks (STRONG
   NRFI / LEAN NRFI / PASS / LEAN YRFI / STRONG YRFI per game).
2. **Track** — appends picks to `data/picks_2026.csv`, captures FanDuel
   odds on the picked side, computes edge vs implied prob, and flags
   `bet_placed=Y` when edge clears the 2% threshold.
3. **Grade** — re-pulls box scores; writes `graded_result` (WIN/LOSS/PASS),
   first-inning runs, and `profit_loss_units` per row.
4. **Display** — Next.js dashboard ([nrfi-terminal.vercel.app](https://nrfi-terminal.vercel.app))
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

## Architecture (post-Phase-1.5/2/3/4/6 — current)

```
              MLB Stats API   Open-Meteo   The Odds API
                   │              │            │
                   ▼              ▼            ▼
   ┌────────────────────────────────────────────────────────┐
   │ Railway "MLB-first-inning" (predictor) — every 5 min   │
   │   workers/predictor_loop.py                            │
   │     1. catch-up grade yesterday                        │
   │     2. live-grade today's completed 1sts               │
   │     3. predict → mlb_first_inning_predictor.py         │
   │     4. fetch_odds_api.py  (FanDuel; T8.39)             │
   │     5. import_odds → tracker.import_odds               │
   │     6. tools/lock_commit.py  (T8.18 PART 2)            │
   │     … pre-game alert, reconcile, Discord, watchdog     │
   │    11. cards + X post  (T8.38, LAST — marketing)       │
   │   ALL writes dual-write to Supabase via                │
   │   db/supabase_writer.py (Phase 1.5)                    │
   └────────────────────────────────────────────────────────┘
                                      │
   ┌────────────────────────────────────────────────────────┐
   │ Railway "worker" (live-state) — every 10s              │
   │   workers/live_state.py                                │
   │     poll MLB schedule + linescore → upsert             │
   │     Supabase live_game_state on change                 │
   └────────────────────────────────────────────────────────┘
                                      │
                                      ▼  (dual-write, idempotent UPSERT)
   ┌────────────────────────────────────────────────────────┐
   │ Supabase Postgres (uubhwrmhlfnsvracdzbg)               │
   │   picks_2026, pick_changes, live_game_state,           │
   │   system_errors, odds_history                          │
   │   RLS: anon/authenticated SELECT-only                  │
   │   service_role bypasses RLS for the workers            │
   │   Realtime publication: picks_2026, pick_changes,      │
   │   live_game_state                                      │
   └────────────────────────────────────────────────────────┘
                                      │
                                      ▼  Realtime push (~200ms)
   ┌────────────────────────────────────────────────────────┐
   │ Next.js dashboard (Vercel mlb-nrfi-yrfi project)       │
   │   loadBoard() reads Supabase → falls back to CSV       │
   │   useSupabaseRealtime → triggers refetch on change     │
   │   useLiveGameState → SELECT + Realtime subscribe       │
   │   PWA installable (manifest + sw.js + icons)           │
   │   live URL: nrfi-terminal.vercel.app         │
   └────────────────────────────────────────────────────────┘

   Backup / archival path (still running, kept as redundancy):

   GHA daily.yml cron — UTC every hour 12-23 plus extras
     • Same predict + grade + scrape + import cycle as Railway.
     • Dual-writes to Supabase via the same path.
     • Commits CSVs to git for archival + Vercel rebuild.
     • Acts as a backup if Railway is down.
```

**Source of truth: Supabase Postgres tables.** The CSV ledger
(`data/picks_2026.csv` + `data/boards/board_<DATE>.csv`) is still
written by both Railway + GHA, but only the GHA path commits them to
git for archival. The dashboard reads Supabase first; CSV is fallback.

**Latency contract** (worst-case freshness):

| Event | Worst case | Path |
|---|---|---|
| Run scores in B1 | ~10 sec | live-state worker poll → Realtime push |
| Inning ends, bet graded | ~10 sec | same |
| Lineup posts | ~5 min | predictor cycle catches it on next tick |
| Pick flips on weather/lineup | ~5 min | same |
| Odds drift | ~5 min | fetch → import → upsert in same cycle |
| Bet placed (manual via dashboard chip) | <1 sec | local state |

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
- `data/fi_pitcher_pool.json` — 2026-08-22: per-starter pooled FIRST-INNING
  xwOBA allowed, as running sums (`fi_pitcher_pool.py`). Advances one day at
  a time from Savant (nightly cron step + predict-time self-refresh, fail-open).
  Rebuild from the research cache: `python fi_pitcher_pool.py --rebuild`.
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
- `.github/workflows/daily.yml` — backup predict + grade + scrape every hour
  UTC 12-23. 8-attempt push retry with `--ours` for CSV conflicts (T1.6).
  Now also dual-writes to Supabase via the SUPABASE_URL/KEY secrets.
- `.github/workflows/backup.yml` — daily 5am ET snapshot (T3.4).
- `.github/workflows/odds_diagnostic.yml` — 4x/day (13/15/17/19 ET)
  multi-book first-inning + F5 totals via The Odds API → CSV under
  `data/diagnostics/odds/` + Supabase `odds_multibook` (the board's
  "best price" chip). `--min-credits 2000` keeps a reserve for the
  money path (2026-08-23).
- `requirements.txt` — pinned with upper bounds (T3.6); includes
  `supabase>=2.0,<3.0` + `python-dotenv>=1.0,<2.0` for the dual-write.

### Railway workers (Phase 3 + 4)
- `workers/live_state.py` — Phase 4. 10s MLB Stats API poll → upsert
  `live_game_state`. Diff-skips unchanged games. Active hours window
  10am-2am ET, quiet sleep otherwise.
- `workers/predictor_loop.py` — Phase 3. 5min cycle running the full
  predict+grade+scrape+import flow. Subprocess-based (shells out to
  the same scripts GHA uses). Ends with `step_publish_cards` (T8.38,
  2026-08-19), which draws the Backfist cards + X post in the SAME cycle
  that commits the No.1 — the render used to run only on the GHA tick and
  could publish "tonight's play" after first pitch. It sits below the
  watchdog so marketing can never delay money, data or monitoring, and
  redraws only when the No.1's signature changes. `PREDICTOR_PUBLISH_CARDS=off`
  disables it.
- `Procfile` — `worker: python workers/live_state.py` (default service).
- `railway.json` — Nixpacks builder config; `startCommand` deliberately
  NOT set (would override per-service UI customizations). Also carries
  `watchPatterns` (T8.36, 2026-08-19): the cron's own `auto: predict`
  commits touch only `data/` outputs, and without this every one of the
  ~17 daily commits rebuilt and restarted BOTH services mid-cycle. The
  list excludes only verified write-only/ephemeral outputs
  (`picks_2026.csv`, `pick_changes.csv`, `system_errors.csv`,
  `thresholds.json`, `season_record.json`, `boards/`, `diagnostics/`,
  `backups/`). **Do NOT widen it to `!/data/**`** — unlike the
  MLB-Strikeouts repo, this one keeps the model (`lr_t1.json`,
  `calibration_v*.json`) and operator config
  (`manual_odds_overrides.csv`, `cluster_demotions.json`) inside
  `data/`; excluding those would freeze an operator edit on a
  long-lived container.
- Railway project: **capable-nourishment** (workspace `joey1160`).
  Two services in one project:
  1. `worker` → live_state.py (default Procfile)
  2. `MLB-first-inning` → predictor_loop.py (Custom Start Command UI override)
  Env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` on both services.
  Optional: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` for predictor
  pick-flip pings.

### Supabase (Phase 1 / 1.5 / 2)
- `db/schema.sql` — full DDL: 5 tables + indexes + RLS policies +
  realtime publication + auto-bump triggers. Idempotent.
- `db/migrate_csv_to_supabase.py` — one-off bulk migrator. Re-runnable
  via `--dry-run`.
- `db/supabase_writer.py` — Phase 1.5 dual-write helper. Lazy client,
  silent no-op when env vars unset, swallows all errors. Re-used by
  tracker.py at the four call sites in `log_picks`, `grade_date`,
  `import_odds`, `_record_pick_change`.
- Supabase project: **nrfi-terminal** (id `uubhwrmhlfnsvracdzbg`).
  - `SUPABASE_URL=https://uubhwrmhlfnsvracdzbg.supabase.co`
  - `SUPABASE_SERVICE_KEY` (service-role JWT — bypasses RLS).
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY` (anon JWT — RLS-protected SELECT-only).
  - All three live in `.env` locally + GitHub Actions secrets + Railway
    env vars + Vercel project env vars.

### Dashboard (Next.js, lives in `dashboard/`)
- `app/page.tsx` — server component, `loadBoard(date?)`. Strict
  YYYY-MM-DD validation on `?date=` (T2.15). `fetchCache = "force-no-store"`
  added in T2.35 to prevent Next.js from caching Supabase responses.
- `app/layout.tsx` — Phase 6 PWA: manifest link, appleWebApp config,
  viewport.themeColor for light + dark, service worker registration.
- `app/api/board/route.ts` — board API. Reads via `loadBoard` (Supabase
  first, CSV fallback).
- `app/api/details/route.ts` — game details (lineup, pitcher cards).
- `app/api/live-state/route.ts` — MLB Stats API proxy (back-compat
  fallback for browsers without Realtime).
- `app/api/health/route.ts` — health endpoint (T3.1).
- `app/api/cron/{predict,grade}/route.ts` — Vercel cron entrypoints.
- `components/BoardTable.tsx` / `BoardRow.tsx` — main board.
- `components/GameDetails.tsx` — expanded panel; renders `WhyThisPickPanel`,
  lineup cards, pitcher comparison.
- `components/HistoryView.tsx` — /history page; CalendarHeatmap (T4.16),
  ZoneHitRateChart (T4.17), CalibrationPlot (T4.23).
- `app/cards/page.tsx` + `components/CardsView.tsx` — /cards, the Backfist
  Bets social-card viewer. Lists the public `cards` storage bucket (anon
  SELECT policy); Save/Share uses the Web Share API on phones. The images
  are rendered ONLY by `tools/cards/make_card.py` (`--publish` uploads with
  the service key) — this page never draws a card itself, so preview and
  posted image cannot disagree. Rendered hourly by the `Publish Backfist
  cards (predict)` step in `daily.yml` (2026-08-14); before that nothing
  called the renderer and Pillow was not even a declared dependency, so the
  bucket only ever held hand-run cards. Club marks are vendored PNGs in
  `tools/cards/logos/` — refresh with `tools/cards/fetch_logos.py`. Player
  portraits are FETCHED per render (too many players to vendor) and cached in
  the system temp dir, never under `data/`, which the cron commits hourly.
  The same cron step then runs `tools/cards/prune_cards.py --keep-days 1`,
  which keeps today's set only; it refuses to delete anything unless today's
  cards are already in the bucket, so a failed render never empties the page.
  `tools/cards/make_post.py` writes that night's ready-to-paste X post to the
  same bucket as `backfist_<date>_post.txt` (OpenRouter; `OPENROUTER_API_KEY`
  absent = deterministic template, silently). **The header — play, units,
  side, price — is built in Python and never generated**, and every number in
  the generated paragraph is checked against the facts supplied
  (`_unsourced_numbers`; decimals lenient, integers strict) before it is
  allowed out. See [docs/AI_POST.md](./AI_POST.md).
- `components/DashboardShell.tsx` — filter persistence (T3.18), pitcher
  search (T4.18), browser notifications (T4.20), Realtime push hook
  (Phase 2 / T2.31).
- `lib/board.ts` — `loadBoard`. Tries Supabase first via
  `loadBoardFromSupabase`, falls back to CSV.
- `lib/board-supabase.ts` — Phase 2 Supabase reader. Same `BoardResponse`
  shape as the CSV path; rows mapped 1:1 from `picks_<season>` table.
- `lib/supabase.ts` — Phase 2. Server + browser client factories.
  Server fetch wrapped with `cache: "no-store"` so Next.js doesn't
  memoize Supabase responses (T2.35).
- `lib/useSupabaseRealtime.ts` — Phase 2 client hook subscribing to
  `postgres_changes` on picks/pick_changes/live_game_state.
- `lib/useLiveGameState.ts` — Phase 4 refactor. Two branches:
  Supabase Realtime (when env vars set) or `/api/live-state` polling.
- `lib/types.ts` — TS types for everything.
- `public/manifest.json`, `icon-{192,512,maskable}.svg`,
  `apple-touch-icon.svg`, `sw.js` — Phase 6 PWA bundle.
- `scripts/copy-data.mjs` — prebuild copy of `../data` → `./data` with
  whitelist. Now serves only as a CSV-fallback path; primary data
  source is Supabase.

---

## Daily operations cycle

You don't normally have to do anything — Railway runs the full cycle
every 5 min, GHA backs it up hourly. Manual interventions:

| Need | Command |
|---|---|
| Force a fresh predict now | `python mlb_first_inning_predictor.py` |
| Force a fresh predict on Railway | redeploy "MLB-first-inning" service in Railway |
| Re-grade a date | `python mlb_first_inning_predictor.py --date 2026-04-30 --grade` |
| Re-scrape DK odds | `python scrape_dk_odds.py` |
| Advance the first-inning pitcher pool to yesterday | `python fi_pitcher_pool.py --update` |
| One starter's pooled first-inning xwOBA | `python fi_pitcher_pool.py --show <pitcher_id>` |
| Smoke-test predictor loop locally | `python workers/predictor_loop.py --once` |
| Smoke-test live-state worker locally | `python workers/live_state.py --once --debug` |
| Check parity (CSV vs Supabase) | `python -m db.supabase_writer` |
| Check health | `curl https://nrfi-terminal.vercel.app/api/health` |
| Run a one-off backtest | `python backtest.py --season 2026` |
| Browse Supabase rows | https://supabase.com/dashboard/project/uubhwrmhlfnsvracdzbg |
| View Railway worker logs | https://railway.com/project/51d66094-e55f-4281-90df-033f20246d75 |

If something is broken:
- **Railway worker silent**: check Deploy Logs in Railway dashboard.
  Confirm Custom Start Command (UI) is set; railway.json must NOT
  override it (T2.34 lesson).
- **Supabase reads stale**: confirm `fetchCache = "force-no-store"` is
  on every page that calls `loadBoard` (T2.35 lesson).
- **GHA cron broken**: https://github.com/joey11600/MLB-first-inning/actions
  + read `data/system_errors.csv` for structured failure rows.
- **Dashboard offline**: check Vercel deployment status. Service worker
  caches the shell so a brief outage still renders last-known UI.
- **Ops Health card shows errors** (the "System" strip on the board): it
  counts Supabase `system_errors` rows from the last 24 h whose
  `resolved_at` is NULL (`/api/health-live`). Diagnose the step named in
  the newest row (Railway deploy logs for `odds-api` / `predict` / …,
  GHA logs for cron steps), fix the cause, then STAMP the rows resolved
  -- never delete them, the table is a log:
  `update system_errors set resolved_at = now(), resolved_note = '<why>'
  where step = '<step>' and resolved_at is null and captured_at_utc < now();`
  (Supabase SQL editor). Columns added 2026-08-23.
- **`odds-api: refusing to start: would leave N credits…`**: The Odds API
  key on the Railway service is near its plan's floor. Both places that
  use the key must carry the SAME key: Railway service variable
  `ODDS_API_KEY` (the lock-time money path, ~50 credits/day) and the GHA
  secret `ODDS_API_KEY` (the 4x/day multi-book snapshot, ~120/day, which
  refuses below 2,000 remaining so it can never starve the money path).
  Plan since 2026-08-23: 20,000 credits/month (~5,100/month used). The
  Railway floor is `ODDS_API_MIN_CREDITS` (service variable; set to 5 on
  2026-08-23 while the old free-tier key's last credits were spent).
  Every cycle's first Railway log line prints `credits used N, remaining M`
  -- that is the live balance of whatever key Railway holds.

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
#   curl -sL https://nrfi-terminal.vercel.app/ | grep -c "<your-marker>"
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

### Telegram pick-flip notifier (T2.22)

Bot `@nrfi_terminal_bot` pings the user on every actionable pick flip
(commits, demotes, side-flips). Filters out PASS-variant churn
(LINEUP↔STARTER↔NO-EDGE) which would just spam.

Configured via two env vars / GHA secrets, both required:
- `TELEGRAM_BOT_TOKEN` — bot token from @BotFather
- `TELEGRAM_CHAT_ID` — user's Telegram user ID (from @userinfobot)

When either is unset, `_notify_pick_flip_telegram` is a silent no-op,
so local dev / unconfigured deploys don't try to ping.

To test manually:
```bash
TOKEN='<bot-token>' CHAT_ID='<chat-id>' python -c "
import os; os.environ['TELEGRAM_BOT_TOKEN']=os.environ['TOKEN']
os.environ['TELEGRAM_CHAT_ID']=os.environ['CHAT_ID']
from tracker import _notify_pick_flip_telegram
_notify_pick_flip_telegram(iso_date='2026-05-01', away_team='TEST',
    home_team='SIM', game_time='9:00 PM', old_label='PASS - Lineup pending',
    new_label='STRONG NRFI')
"
```

To rotate the token: in @BotFather, `/revoke` → pick the bot → confirm.
Then update both the local env and the GitHub Actions secret.

### Modifying cron behavior

`.github/workflows/daily.yml` — UTC schedule covers EDT/EST without DST
shifts. Be careful with `record_err` helper: it logs to `system_errors.csv`
AND pings `ALERT_WEBHOOK_URL` AND emits `::warning::`. Don't break the
ledger format; downstream `/api/health` parses it.

---

## Known structural limitations (post-2026-05-03 deep dive — T3.12)

After the worst single-day loss in 30 days (-4.55u, 2-6 record on 8 placed
bets) the slate was forensically deconstructed.  Findings worth preserving
here so future sessions don't blame "today is variance" without checking:

### 1. The calibrator clamps the prediction range

`data/calibration_v2.json` was fit on 2025+2026 raw model outputs vs
actual outcomes.  Its rate range is **[0.3623, 0.6620]**, meaning:

- The model can never output P(NRFI) below 0.3623 (= P(YRFI) above 0.6377)
- The model can never output P(NRFI) above 0.6620

So **every STRONG YRFI bet at the calibration floor looks identical in the
log** — the model can't distinguish "weakly leans YRFI" from "strongly
leans YRFI" once both raw signals fall below 0.353 (the lowest training
bin's input edge).  21% of all 30d STRONG YRFI bets (29/135) hit this
exact floor.

### 2. Within-band hit-rate non-monotonicity

STRONG YRFI hit rate by calibrated P(NRFI) bucket (last 30d):

| P(NRFI) band | n | W-L | Hit | P/L |
|---|---|---|---|---|
| [0.36, 0.37) | 31 | 21-10 | **67.7%** | **+8.92u** ← floor (good) |
| [0.37, 0.38) | 9 | 2-7 | 22.2% | -5.18u ← losing |
| [0.38, 0.40) | 20 | 10-10 | 50.0% | -1.08u ← losing |
| [0.40, 0.42) | 63 | 41-22 | **65.1%** | **+14.85u** ← ceiling (good) |
| [0.42, 0.43) | 11 | 6-5 | 54.5% | +0.45u |

The 0.37-0.40 "losing valley" is the band where the calibrator pulled the
raw signal up off the floor but the raw model wasn't ceiling-bound either.
Hypothesis: this is the "weak edge" zone where the model is least confident.

### 3. NRFI vs YRFI hit-rate gap

| Side | 30d N graded | W-L | Hit |
|---|---|---|---|
| **NRFI** | 49 | 35-14 | **71.4%** |
| **YRFI** | 133 | 80-53 | **60.2%** |

11pp gap.  Model places 2.7× more YRFI bets than NRFI bets despite the
lower hit rate.  Asymmetric thresholds may be appropriate but require
walk-forward validation.

### 4. Slate-context blindness

Per-day model `mean(P(NRFI))` ranges 0.43-0.50 across 29 days, while
**actual NRFI rate** ranges 10%-75%.  The model is structurally unable to
distinguish "today's slate is NRFI-leaning" from "today's slate is
YRFI-leaning" — it predicts ~47% NRFI every single day.  6 of the 7 worst
P/L days in 30 days are NRFI-leaning slates where the model over-bet YRFI.

### REALISTIC BANKROLL EXPECTATIONS (T3.12 followup #3, 2026-05-03)

After three nights of forensic work the long-run expected ROI on this
model is genuinely uncertain.  Three converging data points:

| Source | N bets | Hit rate | ROI | Notes |
|---|---|---|---|---|
| Live production, last 30d | 184 | 63.04% | **+19.0%** | Real bets, real money, p=0.002 vs break-even |
| Test 2 (leaky walk-forward 2024→2025) | 558 | 57.9% | +6.1% | Production-style methodology, full season |
| Test 3 (strict walk-forward 2024→2025) | 347 | 54.8% | +0.4% | Single-season truepit, only NRFI bets |
| v3 calibrator on 2024+2025 truepit | 464 | 59.5% | +9.1% | Multi-season truepit, both NRFI+YRFI bets |

**The +19% ROI on live data is almost certainly NOT the long-run expectation.**
It's a 30-day sample that combines:
- A real underlying edge of ~5-9% ROI (consistent across the 3 backtests)
- Plus positive variance (the 30d run got lucky on top of real edge)
- Plus possible calibrator inflation from training-data leakage

Honest long-run expectation:
- **Mean ROI: +5 to +9%** (consistent across honest tests)
- **30-day windows can swing widely**: -10u to +50u is normal at this volume
- **Expected hit rate: 57-60%** (not the live 63%)
- **Today's bad day is normal** at this true rate (1-of-5 STRONG bets at p=0.60 has 8% probability — once-a-month event)

What this means operationally:
- **Don't treat the live 30d +36u as "the expected month."** It's a good month.
- **Bad days are part of the model.** A 1-of-5 day will happen ~once a month even at 63% true rate.
- **Bankroll sizing**: at 1u flat stakes per bet, monthly expected return is **+10-20u, not +36u**. Plan around the +10-20u figure.
- **Stop-loss / drawdown limits**: a -5u to -10u day will happen ~once a month.  -15u in a day would be unusual but not impossible.

What we DON'T know yet:
- Whether the production calibrator (v2, leaky) or v3 (truepit) is better
  on FRESH 2026 data.  Can't test until 2026 ends.
- Whether the 0.37-0.38 "losing band" was real or selection bias (Variant J
  rejected on strict walk-forward; weak signal on combined samples).
- How much the per-game point-in-time xera improvement helps vs hurts
  bet volume.  v3 cuts bet volume ~35% with similar hit rate.

Action items reflected in CHANGELOG:
1. v3 calibrator built but NOT deployed
2. Variant J rejected (closes the variant exploration thread)
3. Calibration leakage acknowledged but not auto-fixed (production v2
   stays until walk-forward on 2026 holdout becomes available)

---

### THE HEADLINE FINDING — Strict walk-forward (T3.12 Test 3, 2026-05-03)

After per-pitch xera/whiff backfill (`tools/backfill_xera_pit_perpitch.py`)
producing data/backtests/backtest_*_truepit.csv with cumulative-through-
yesterday xwOBA-derived xera + cross-pitcher whiff_pct_rank rebuilt from
raw Statcast, ran Test 3 of `tools/test_variant_g_2025.py`:

  Train: backtest_2024-*_truepit.csv (true point-in-time)
  Test:  backtest_2025-*_truepit.csv (true point-in-time)

Result:

  Calibrator range : [0.4583, 0.6357]   (vs production [0.3623, 0.6620])
  STRONG bets identified: 329 -- ALL NRFI, ZERO YRFI
  Hit rate         : 179-150 = 54.4%
  P/L              : -0.83u over 329 bets

**Two huge implications:**

1. **Under strict walk-forward, the model produces NO STRONG YRFI bets at all.**
   The calibrator's floor doesn't reach down to the 0.42 YRFI threshold.
   Every STRONG YRFI bet in production is likely an artifact of leaky
   training data inflating the calibrator's range.

2. **STRONG NRFI bets are break-even under strict walk-forward** (54.4%
   at -110 = roughly break-even).  Compare:
     Leaky walk-forward (Test 2):    NRFI 67.9% hit rate, profitable
     True walk-forward (Test 3):     NRFI 54.4% hit rate, ~break-even
   That's a -13.5pp drop in NRFI hit rate when xera/whiff leakage is removed.

The model's profitable production edge appears to be LARGELY calibrator-
driven, not signal-driven.  When training data is genuinely leak-free,
the model's edge mostly disappears.

**CAVEATS** (the truepit methodology may be over-conservative):
- xwoba -> xera proxy uses a simplified linear slope (32 ERA/xwoba)
  rather than MLB's official xERA formula, which is non-linear and
  uses individual batted-ball xwoba (not aggregate)
- whiff_pct_rank uses cross-pitcher per-date sort with min 200 swings
  (matches Savant's documented threshold) but may differ slightly
- Some pitchers had partial-season fetches (240 missing pid rows)
- 2024 -> 2025 has known year-over-year drift (pitch-clock effects,
  pitcher quality tier shifts)

But the QUALITATIVE finding stands: under strict walk-forward, the
calibrator is structurally too conservative to produce STRONG YRFI bets,
and STRONG NRFI bets are no better than coin-flip.

**Variant J is moot.**  It targeted a calibrated P(NRFI) band that
doesn't exist under strict walk-forward.

### Variants tested in response (T3.12, 2026-05-03)

Four new variants added to the A/B harness:

- **Variant G**: skip STRONG YRFI in calibrated 0.37-0.40 band.
  30d in-sample: +6.27u vs production (kept 156 at 66.0% hit, +41.38u).
  **2025 full-season holdout (`tools/test_variant_g_2025.py` Test 2):
  only +1.00u** — well within noise.  Largely SELECTION BIAS.
- **Variant H**: tighten STRONG NRFI threshold from P(NRFI)≥0.58 to ≥0.62.
  30d in-sample: -7.08u vs production.  REJECT (skips winners).
- **Variant I**: G + H combined.  30d in-sample: -0.82u.  REJECT.
- **Variant J** (refined G after 2025 holdout): skip ONLY the narrow
  0.37-0.38 sub-band on STRONG YRFI bets.  Reproduces on both samples:

  | Sample              | Skipped bets | W-L  | Hit  | P/L saved |
  |---------------------|-------------:|-----:|-----:|----------:|
  | 30d 2026 in-sample  | 9            | 2-7  | 22%  | +5.18u    |
  | 2025 full-season    | 15           | 5-10 | 33%  | +5.83u    |
  | **Combined**        | **24**       | **7-17** | **29%** | **+11.01u** |

  30d backfill P/L: production +35.11u → Variant J +40.30u (**+5.19u**).
  Both samples agree the 0.37-0.38 sub-band is structurally weak.  This
  is the strongest variant signal we have.

### What we learned about Variant G's "0.37-0.40 valley"

The 0.37-0.40 band actually splits into two very different sub-bands.
On 2025 holdout:

  - 0.37-0.38: 15 bets, 5-10, 33% hit, **-5.83u**  ← real losing zone
  - 0.38-0.40: 19 bets, 13-6, 68% hit, **+4.83u**  ← winning zone

The 30d 2026 in-sample showed both as losing.  The 2025 holdout shows
0.37-0.38 still losing but 0.38-0.40 winning.  Variant G killed both
indiscriminately, so its in-sample win was selection bias on the
0.38-0.40 portion.  Variant J fixes this by skipping only the narrow
0.37-0.38 zone that reproduces.

### Why Test 1 (leak-free 2024 → leak-free 2025) couldn't validate

The leak-free 2024-trained calibrator has rate range [0.4417, 0.6279]
vs production's [0.3623, 0.6620].  Without the floor reaching down to
0.36, NO STRONG YRFI bets fire (they all need calibrated P(NRFI) ≤ 0.42).
So Variant G's 0.37-0.40 band has zero bets in this regime — can't be
tested.  The calibrator's range depends on training-data composition,
which depends on whether xera/whiff are leaky.  Test 2 uses the same
"leaky 2024 → leaky 2025" methodology as production, which reproduces
the production calibrator's range and lets us actually test the variant.
Strict walk-forward requires per-game point-in-time xera/whiff (deferred).

**Status: variants run as shadow picks only.**  Walk-forward gate is
broken pending the per-game Statcast point-in-time backfill (T3.11-AUDIT;
see `tools/backfill_xera_whiff_pit.py`).  No production threshold change
ships until walk-forward is honest.  Variant J is the most-validated
candidate to date (+11u savings reproduced on both 30d 2026 and full
2025 season), but still under the +10u-on-≥2-leak-free-folds bar.

### Two larger fixes deferred

- **Refit calibrator on a leak-free corpus** (2024+2025 backtests, after
  per-game xera/whiff backfill).  Goal: widen the rate range so the model
  can express stronger conviction than 0.36-0.66.  Blocked on the same
  walk-forward fix.
- **Add slate-context features**: e.g. slate-mean predicted P(NRFI) as a
  per-game feature, or count of high-quality starters across the slate.
  Requires full LR retrain.  Defer to Tier 5 / catcher-framing remote
  agent if it lands.

---

## What's NOT in the model (already tested, didn't help)

Important to avoid retreading. From cross-validation across 2024/2025
backtests, none of these improved out-of-sample Brier:

- **Catcher framing** (T4.1, tested 2026-05-03 evening).  Built end-to-end
  pipeline (`tools/build_catcher_framing.py` + `extract_catchers_per_game.py`
  + `backfill_catcher_framing_to_csvs.py` + `test_catcher_framing.py`)
  using per-pitch Statcast data with the standard "shadow zone" definition
  for borderline pitches.  Walk-forward 2024→2025 truepit:

    Phase E.3 (no framing):  347 bets, 190-157, 54.8% hit, +1.33u, Brier 0.2511
    Phase E.4 (+framing):    373 bets, 203-170, 54.4% hit, -0.83u, Brier 0.2518
    Delta: -2.17u P/L, -0.33pp hit, slightly worse Brier.

  LR weights on the framing features:
    T1 home_catcher_framing: +0.0092 (essentially zero, wrong sign)
    B1 away_catcher_framing: -0.0445 (small, expected sign)

  Conclusion: industry consensus says catcher framing is worth ~10-20
  runs/season for top framers = ~0.05 runs per first-inning, well below
  the model's signal floor.  The LR weight magnitudes confirm this.
  Pre-emptively closes the scheduled remote agent for 2026-05-15.



- Top-3 batter aggregates (prior-year and current-season)
- Pitcher last-5 / last-10 NRFI history (informational only on dashboard)
- Weather (temp / wind / humidity)
- Wind × park-orientation interactions
- Pitcher current-season blend stats
- 2022 / 2023 backtest data (pitch-clock distribution shift)
- Handedness / platoon advantage features
- xERA alone, xwOBA alone (only `xera + whiff` combo helped, see Phase E.3)
- Umpire NRFI rate alone
- **Pitcher days-rest** (tested 2026-05-02 via `test_days_rest.py`).
  4 variants tried: raw rest per half, short-rest flag (≤4d), signed
  gap (away_rest − home_rest), and raw+flag combo.  Best variant
  (`+rest_signed_gap`) showed +8.2u sum P&L over baseline across the
  2-split 2024↔2025 cross-validation — below the +10u ship bar — AND
  regressed STRONG YRFI hit rate from 61.9% → 58.8% on the
  2024→2025 split.  Three of the four variants made things noticeably
  worse (-21u to -60u sum P&L).  Conclusion: rest signal isn't
  separable from the FIP/ERA/last-5 features the model already uses.

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
| [ROADMAP.md](../ROADMAP.md) | Forward-looking upgrade list (Tier 1-5), status, recommended sequence |
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

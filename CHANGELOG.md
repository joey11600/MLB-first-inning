# Changelog

Dated log of meaningful changes to the NRFI Terminal system (predictor, tracker,
dashboard, ops). For the running list of open audit items see [AUDIT.md](./AUDIT.md).
For the forward-looking upgrade list see [ROADMAP.md](./ROADMAP.md).
For the system overview see [docs/KB.md](./docs/KB.md).

Format: latest first. Each entry is grouped Added / Changed / Fixed / Deferred
with audit IDs (`T1.1`, `T4.15`, …) cross-referenced to AUDIT.md. Performance
section captures actual picks accuracy on/around the change date.

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

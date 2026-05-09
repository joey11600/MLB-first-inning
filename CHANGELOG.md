# Changelog

Dated log of meaningful changes to the NRFI Terminal system (predictor, tracker,
dashboard, ops). For the running list of open audit items see [AUDIT.md](./AUDIT.md).
For the forward-looking upgrade list see [ROADMAP.md](./ROADMAP.md).
For the system overview see [docs/KB.md](./docs/KB.md).

Format: latest first. Each entry is grouped Added / Changed / Fixed / Deferred
with audit IDs (`T1.1`, `T4.15`, …) cross-referenced to AUDIT.md. Performance
section captures actual picks accuracy on/around the change date.

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

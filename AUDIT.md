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

- [x] **T8.24 — the skip's reach-back fetched from a remote that does
  not exist, and T8.6's banned comparison was its failure path**
  ✅ 2026-08-10
  `should-build.sh` recovered an out-of-shallow-clone
  `VERCEL_GIT_PREVIOUS_SHA` with `git fetch --depth=1 origin "$PREV"
  2>/dev/null || true`. **Vercel's build container has no remote named
  `origin` — it has no configured remote at all.** Measured on the
  SIBLING strikeouts project 2026-08-10, whose equivalent script printed
  `fatal: 'origin' does not appear to be a git repository` three times
  once its failures stopped going to `/dev/null`. Dead on arrival, and
  silent about it.
  **That last fact is INFERRED here, not observed.** Same platform, so
  very likely, but this project's own build has never printed it — the
  error went to `/dev/null`. The new `remotes configured: [...]` line
  settles it on the first build log that reaches the fetch branch;
  until one does, the container's remote list is unconfirmed. Recorded
  as an open loop rather than closed by assertion.
  **The consequence here is worse than the bill it caused there.**
  Strikeouts fails toward BUILDING (91 CPU-hours, Aug 7-10). This script
  falls to the NARROW COMPARISON against `HEAD^` — precisely the
  comparison T8.6 exists to eliminate, in which a code commit under a
  data commit in the same push is skipped and never deploys with nothing
  turning red. T8.6 removed it from the happy path; the dead fetch
  reinstated it as the failure path.
  **Not observed firing.** ~21 commits/day here against strikeouts'
  ~125, so the last build stays inside the shallow window and sampled
  logs all show the direct `comparing against LAST BUILD` path. Latent,
  not active — and unprovable after the fact, since the error was
  discarded.
  Fixed: `remote_candidates()` yields every configured remote and then
  the URL rebuilt from `VERCEL_GIT_REPO_OWNER` / `VERCEL_GIT_REPO_SLUG`,
  and each is tried in turn — because "a remote exists" and "that remote
  can serve this object" are different claims, and treating the first as
  the second puts us straight back on the silent narrow path. Remote
  list, each candidate tried, and every failure are printed. The
  `--depth=1` fetch-by-SHA shape is kept.
  The fetch runs under `GIT_TERMINAL_PROMPT=0` and `-c
  credential.helper=` so a missing credential FAILS instead of HANGING;
  an `ignoreCommand` blocked on a password prompt nobody can answer
  stalls the deploy, which is worse than either verdict. Case D below
  exercised exactly that.
  Verified against a harness reproducing the production shape — depth-5
  `--no-local` clone, `git remote remove origin`, invoked from
  `dashboard/`, baseline outside the shallow window — on REAL history:
  ```
  control  pre-fix script, code in gap  -> SKIPPING build     (0)  <- the bug
  A  data-only gap of 9   -> fetch ok  -> SKIPPING build      (0)
  B  code commit in gap   -> fetch ok  -> BUILDING, names tracker.py (1)
  C  no remote, no env    -> NO REMOTE AVAILABLE -> narrow, loud (1)
  D  broken remote + env  -> fails fast, cascades, LAST BUILD (1)
  E  working remote       -> used directly, no regression     (1)
  ```
  The control matters: a NORMAL clone passes on both the broken and the
  fixed script, because it has an origin and a local remote serves any
  SHA. Only the production shape separates them. 97 tests pass.
  **`--depth=1` fetch-by-SHA needs the FULL 40-char id.** An abbreviated
  one is parsed as a ref name and returns `couldn't find remote ref`,
  which is indistinguishable from "GitHub refuses raw SHA fetches" —
  it produced a false negative in this fix's own verification until the
  harness was corrected. `VERCEL_GIT_PREVIOUS_SHA` is full-length.
  **The reach-back depends on this repo being PUBLIC.** The derived URL
  is fetched anonymously; there are no credentials in the build
  container. If the repo is ever made private every reach-back fails and
  every data commit starts building again — loudly, now that failures
  print. Verified public 2026-08-10 with the credential helper disabled.
  Narrow fallback deliberately left in place — operator's call.
  **Generalises:** a fail-safe that fires without saying why converts a
  broken recovery into either a recurring bill or a silent non-deploy.
  `2>/dev/null || true` on a recovery path is the bug, not the git.

- [x] **T8.25 — CI was red for four pushes on a sys.path accident, and it
  took the parity guard down with it** ✅ 2026-08-10
  `tests/` has no `__init__.py`, so pytest's prepend import mode puts
  `tests/` on `sys.path` and never the repo root. `import tracker`
  therefore resolved only because six of nine test modules each carried a
  private `sys.path.insert(...)` line — and because collection is
  ALPHABETICAL, whichever module sorted first silently fixed the path for
  every module after it. Three modules never had the line
  (`test_allocation_order.py`, `test_preserve_columns.py`,
  `test_top_pick_gate.py`) and passed purely on that ordering.
  `test_allocation_order.py` landed in cb5d5c88 without the line and
  sorts FIRST, so nothing had run yet: collection died on
  `ModuleNotFoundError: No module named 'tracker'` and all 92 other tests
  never executed.
  **It reproduces on the runner form, not the local one.** `python -m
  pytest tests/` prepends CWD and shows 97 passed; bare `pytest tests/`
  — what `.github/workflows/tests.yml` runs — does not. Same code, same
  tests, opposite verdicts, which is why it shipped green.
  **The real exposure was the step behind it.** `Money-path tests` runs
  before `Fixtures still match Python`, so `parity_fixtures.py --check`
  never ran on any of the four red pushes. That is the ONLY guard that
  catches the dashboard's stake math drifting from the Python that sizes
  real bets — `check-kelly-parity.mjs` compares against a committed
  fixture and never invokes Python (T8.12). It was unexercised across a
  stake-allocation change (T8.19), a ledger fix (T8.23) and a
  notification fix (T8.22). Run on the fixed tree: `kelly-parity-fixture
  .json ok (21402 cases)`, `pass-price-fixture.json ok (121 cases)` — no
  drift was hiding behind the failure.
  Fixed in `tests/conftest.py`, which pytest imports before any test
  module in its directory, so it covers every test whether or not the
  author thought about it — the same reasoning as the `autouse`
  production-write guards already there. Verified by running each of the
  three previously-unguarded modules ALONE under the no-CWD form (5 / 4 /
  12 passed), which the private copies cannot explain. The six private
  copies are left in place: redundant, harmless, and they keep `python
  tests/test_money.py` working when run directly.
  **Generalises:** a per-file import fixup that only works in collection
  order is a latent failure in every file that lacks it, and the file
  that exposes it is chosen by alphabet, not by risk. Shared setup
  belongs in `conftest.py`. And a CI step ordered behind a fragile one
  inherits its outages silently — the guard that protects money should
  not be reachable only by passing the tests first. Fixed as T8.26.

- [x] **T8.26 — the parity guard was reachable only by passing the tests
  first** ✅ 2026-08-10
  A GitHub Actions step with no `if:` carries an implicit `success()`, so
  by default every step is gated on all the ones before it. Correct for a
  BUILD PIPELINE, where a later stage consumes an earlier stage's output;
  wrong for INDEPENDENT CHECKS, where it lets the first thing to break
  decide whether anything else gets to speak. T8.25 is that bug firing:
  a sys.path accident in test COLLECTION — carrying no information about
  the money math at all — suppressed `parity_fixtures.py --check` for
  four pushes, and the CI page said only "failing".
  Fixed: parity runs FIRST (money-critical and cheap) and the tests run
  regardless of its verdict, under
  `if: ${{ !cancelled() && steps.deps.outcome == 'success' }}`.
  **Swapping the order alone would have MIRRORED the bug**, not fixed it
  — a parity failure would then have hidden the tests. The gate between
  the two peers is the defect; the order is only which one you read
  first.
  `!cancelled()` not `always()`: this workflow sets `cancel-in-progress`
  and the hourly automation supersedes runs routinely, so `always()`
  would keep working on an abandoned run. The `steps.deps` clause keeps
  the ONE real prerequisite — under a failed `pip install` neither check
  can reach a meaningful verdict, and a wall of import errors would bury
  the real cause rather than add to it.
  **VERIFIED BY MAKING IT FAIL, not by reading the YAML** — the whole
  lesson of T8.25 being that this class of defect passes inspection.
  Scratch branch `ci/prove-step-independence`, parity step forced to
  `exit 1` (the STEP sabotaged, never a fixture — no money file was
  touched), run 31415202287:
  ```
  X  Fixtures still match Python   -> exit 1
  ✓  Money-path tests              -> 97 passed
  X  job conclusion                -> failure
  ```
  The second step ran on a failed first step, and the job still went red
  — a real failure is surfaced, not masked. Branch deleted after the run.
  **Generalises:** independent checks must not be sequenced as though
  they were dependent stages. Ask of any two CI steps whether the second
  CONSUMES the first or merely FOLLOWS it; if it merely follows, the
  implicit `success()` is a silent single point of failure.
  Swept for other instances as T8.27 — there were two.

- [x] **T8.27 — the same gating defect in the dashboard job and in
  backup.yml** ✅ 2026-08-10
  Audit of all four workflows for the T8.26 pattern. Two more instances,
  one of them the OTHER HALF of the guard T8.26 had just fixed.
  **`tests.yml` / `dashboard` job.** `Units guard` and `Kelly +
  pass-price parity` are independent — the first writes a probe file and
  type-checks it, the second diffs a committed fixture against Python;
  neither reads the other's output (verified in the two `.mjs` sources).
  Sequenced, a units-guard failure silenced the parity guard, and the
  parity guard is the MONEY one. Missed when T8.26 landed because the two
  halves of the stake-math protection live in DIFFERENT JOBS: `money`
  checks fixtures against live Python, `dashboard` checks the TypeScript
  against those same fixtures. **Rule: when you decouple one guard, go
  find its other half.**
  **`backup.yml`.** Snapshot → Prune → Commit were sequenced but are
  peers. Two teeth: (1) snapshot failure skips the prune, and the
  canonical snapshot failure is a full disk — the exact condition pruning
  would relieve, so the gate disables the cleanup precisely when it is
  needed; (2) prune failure skips the commit, discarding the snapshot,
  which is the job's whole purpose. And the commit is load-bearing for
  the prune, not just the snapshot: `git add data/backups` stages the
  DELETIONS too, so a prune whose commit never runs achieves nothing that
  outlives the runner. This file already carries the scar — silent
  non-pruning grew 94 snapshots until the tracked tree pushed the Vercel
  bundle past 250MB and every deploy errored (2026-08-05).
  **Measured while fixing it: tracked `data/` is 155.1 MB, of which
  126.0 MB is `data/backups`** (4,142 tracked files; earlier `xargs du |
  tail -1` readings undercount badly — xargs splits and only the last
  batch total is printed). Against a 250MB limit that has already broken
  deploys once, the prune is not housekeeping, it is the thing keeping
  the deploy alive, and it was gated behind a step that can fail.
  Fixed: both later steps run on `!cancelled()`, gated only on the
  checkout — the one true prerequisite.
  **ACCEPTED TRADE, recorded so nobody "fixes" it back:** a mid-way
  snapshot failure now commits a PARTIAL backup. Deliberate — a partial
  backup restores more than none, the job still goes red so the operator
  sees it, and the alternative silently discards the prune as well.
  **NOT a defect: `daily.yml`.** Checked and clean, by construction. 15
  of its 24 steps wrap their work in `set +e` + `|| echo "::warning::"`,
  which does T8.26's job at the shell level — a broken drift monitor or
  loss-classifier cannot stop `Commit data changes`. The three steps that
  CAN fail (install deps, decide action, recalibrate) are genuine
  prerequisites where skipping downstream is correct; recalibrate
  especially, since it rebuilds park factors and then refits the
  calibrator on them, so a failed refit SHOULD discard the new park
  factors rather than ship a calibrator that never saw them.
  `runner_watchdog.yml` is a single step, n/a.
  **Doc drift found in passing:** `.github/workflows/shadow_gate.yml` is
  cited as running automatically before a predictor merge, and does not
  exist. The safety net described is not there. Closed as T8.28.
  *(Correction: this line originally named CLAUDE.md as the source of
  the claim. It is not — CLAUDE.md and AGENTS.md never mention
  shadow_gate. The citations are in docs/KB.md, docs/PLAYBOOK.md and
  docs/SELF_HOSTED_RUNNER.md.)*

- [x] **T8.28 — the docs promised a pre-merge model gate that was
  deleted three months earlier** ✅ 2026-08-10
  `.github/workflows/shadow_gate.yml` (T4.7) was removed **2026-05-06**
  in b125aa45 ("v2.1 lock-in: archive V2 toggle, remove V3 + shadow
  surface entirely"), deliberately and along with the rest of that
  surface: `tools/daily_shadow_report.py` (T4.4), `tools/v2_t42_shadow.py`,
  `dashboard/components/ShadowDeltaCard.tsx` (T4.9) and
  `data/diagnostics/shadow_summary.csv`. The code removal was clean; the
  DOCS never caught up, and kept describing all five as live for three
  months.
  **The dangerous one was PLAYBOOK section 4**, an entire procedure
  headed *"I'm about to merge a PR that touches the predictor"* which
  told the reader the gate "will run automatically" and would fail the
  PR under `delta_pl < -2.0u`. Anyone following it merged a predictor
  change believing a model-quality check had passed. Nothing ran. **A
  stale "you're covered" is worse than no doc at all, because it stops
  you looking.**
  Also stale: KB.md's six-layer diagnostic list (3 of 6 dead),
  PLAYBOOK's diagnostic-stack table (3 dead rows), and
  SELF_HOSTED_RUNNER's cutover list.
  Fixed by writing down what actually exists: `tests.yml` runs on every
  push, and it proves the money PLUMBING is self-consistent (Python ↔
  fixtures ↔ TypeScript) — **it says nothing about whether the model got
  better or worse, and a change that quietly worsens predictions passes
  it green.** Section 4 now says the operator IS the gate and routes to
  the three-split out-of-sample protocol. The surviving V2.1-vs-V2.2
  track (`tools/v21_shadow_predict.py` →
  `data/diagnostics/v21_v22_disagreements.csv`) is labelled
  observability, not a gate.
  **Generalises:** deleting a subsystem is only half the change. Grep
  the docs for its filenames in the same commit — a removed guard that
  is still documented converts into a false assurance, which is a worse
  state than never having had it.

- [x] **T8.35 — the No.1 locked mid-outage: MLB withdrew the lineup card** ✅ 2026-08-13
  2026-08-13 CIN@CWS, the night's **No.1**, published at YRFI 66.87% and
  staked **2u** where the rule says **7u**. It won: +1.667u booked instead
  of +5.833u, a **4.17u shortfall on the play that is actually sold**.
  **Cause (corrected same day — the first write-up of this entry and the
  `b95e1905` commit message blamed host drift; that is DISPROVEN):**
  the CWS lineup card posted ~15:03Z (→ 66.9%), was **withdrawn from
  MLB's `schedule?hydrate=lineups` feed between 15:58 and 16:06Z**, and
  returned by ~17:03Z — unchanged, and matching the actual first-pitch
  order. During the outage the model silently regressed the home side to
  team_fallback → **58.6% on BOTH hosts** (Railway's 16:06+ cycles AND
  GHA's own 16:47 run's fresh compute — its log prints ">> STRONG YRFI |
  YRFI 58.6%" beside the frozen 66.9% board line). Railway's 15:04–15:58
  deploy printed 66.9% every cycle; the hosts agreed at every instant.
  Three coincidences stacked: the game-time correction (2:10→1:10 ET,
  caught 15:58) moved the lock an hour earlier to 16:10; the card was
  pulled in exactly those minutes; the commit fired at **16:11:33** — 90
  seconds into the window, sizing quarter-Kelly on the live 58.6% → 2u.
  T2.25 then froze the published 66.87% over it.
  **Not T8.18** — the re-derive tracked its input faithfully; the INPUT
  regressed. `stake_drift.py` (PART 3) caught the splice after the fact.
  **The real defect:** `fetch_top3_batters` has no memory — a card seen
  10 minutes ago that vanishes is treated as never-posted, silently, with
  no alarm; and the stake is sized from the live compute while the
  published probability freezes from an earlier one, so a transient input
  regression in the lock minute splices the row — on one host or many.
  **Row healed** to 7u via `tools/heal_2026_08_13_split_brain_stake.py`
  (operator decision, journaled) — sound, and strengthened by the finding:
  the 66.87% inputs matched the real lineup; 58.6% was the data outage.
  **Fix proposed, not shipped:** `docs/proposals/one_source_stake.md`
  (rewritten post-correction: sticky lineup cards, size-from-published-
  value, lineup-regression alarm; host consolidation demoted).
  **Layer 3 SHIPPED 2026-08-13** (same day): `lineup_regression` ops
  Telegram (fires on the lineup→fallback transition, pre-lock STRONG
  only — would have pinged at ~12:06 ET, four minutes before lock) +
  `stake_drift.py --notify` wired into the nightly grade cron so a
  mis-sized stake surfaces the same night.
  **Layer 1 SHIPPED 2026-08-13** (operator-approved): sticky lineups —
  a card once seen is only ever replaced, never forgotten; empty fetch
  sides refill from the ledger row's own `lineup_json` (the one memory
  that survives GHA's fresh runners AND Railway's hourly redeploys),
  non-empty fetches always win so real scratches replace honestly.
  `NRFI_STICKY_LINEUPS=enabled` on BOTH hosts (Railway var + daily.yml).
  Incident replayed in `tests/test_sticky_lineups.py` — the withdrawn
  CWS card restores by ID at the 12:06 state.
  **Layer 2 SHIPPED 2026-08-13** (operator-approved): bet-adoption sync —
  when a CSV copy first learns `bet_placed=Y` from Supabase, the
  committing host's probability set + pick identity sync atomically with
  the money, so the T2.25 freeze preserves the values the bet was sized
  from. Strictly N→Y: frozen rows never re-adopt, unplaced rows keep
  their own compute. `tests/test_bet_adoption_sync.py`.
  **CLOSED with a standing watch:** all three layers + the row heal
  shipped same-day. The next card-flap announces itself via the
  `lineup_regression` ping; any residual stake/probability mismatch
  pings the same night from the grade cron's stake-drift replay. If
  either watch fires unexpectedly, reopen here.
  **Generalises:** before blaming an infrastructure host for a data
  discrepancy, check whether the SOURCE itself was stable across the
  window — two consumers reading a flapping feed at different moments
  look exactly like two consumers disagreeing with each other.

- [x] **T8.34 — a chart shipped with no endpoint behind it** ✅ 2026-08-12
  `<ReliabilityCurve />` has been mounted in `DashboardShell` since the
  2026-07-28 spec and self-fetches `/api/calibration`. That route was
  **never written** — no commit in `git log` ever contained it. The fetch
  404'd, `.catch()` set state to "error", and the component rendered
  `null` by design ("a missing diagnostic is a zero-pixel outcome, not an
  error surface"). An approved chart was therefore absent for two weeks
  with no visible symptom except a console 404, found while verifying
  T8.33.
  **Fix:** `dashboard/app/api/calibration/route.ts`, read-only, reading
  through `loadLedgerRows` so it shares `/history`'s paginated
  Supabase-then-CSV path and cannot hit the PostgREST 1000-row cap.
  Bins under 20 games are dropped, not drawn; `betRegion` is computed
  from raw rows so a suppressed thin bin still counts; `breakEven` uses
  only real captured prices, never the flat -110 fallback.
  **What it reveals:** in the bet band the model claims 61.8% against
  57.1% delivered over 468 games, needing 55.9% — 95% interval
  52.5–61.5%, which contains break-even. Consistent with
  `2026-06-04_edge_investigation`; not in tension with /history's 69.6%,
  which is the top-ranked play per night rather than every game above
  the gate.
  **Generalises:** the graceful-degradation rule that makes a missing
  diagnostic render nothing also makes a NEVER-BUILT diagnostic render
  nothing. Silent-by-design failure modes need a liveness check
  somewhere, or "absent" and "broken" become indistinguishable. A
  console 404 was the only difference here.

- [x] **T8.33 — the board reported 5u of profit that was never won** ✅ 2026-08-12
  Four dashboard surfaces recomputed a stake with `stakeUnitsFor()` rather
  than reading the recorded one. That helper is pure quarter-Kelly with no
  knowledge of the 15u/day cap, so on a cap-bound night each answered with
  what a play WANTED, not what was placed.
  **Measured, 2026-08-11:** the card read "22.00u sized across 3 STRONG
  picks · +16.64u"; the stake chips on the same screen read
  8.00 + 1.00 + 6.00 = 15.00u; `tools/pl_calc.py` read +11.640u. COL@ARI
  was recomputed at its uncapped 8u after the cap trimmed it to 1u.
  **The P&L was the serious half.** `tonightFromBoard` derives profit from
  the same stake, so a display defect became 5 units of fabricated profit.
  An exposure figure 7u too high is bad; an invented gain is worse.
  **Fix:** ledger-first in `lib/reconcile.ts`, `TonightsActionCard`
  (×2) and `app/brief/page.tsx`; two `> 0` tests → `!= null` in
  `TopPlayHero`; explicit refusal branch on `BoardRow`'s chip.
  `units_risked` has THREE states (T8.30) and a recorded zero is a
  refusal, not a missing figure.
  **Verified on a PRODUCTION build:** 08-11 now reads 15.00u / +11.64u,
  matching the chips and `pl_calc.py`; 08-10's refused TB@OAK reads
  "stake none". Presentation only — staking, cap and ledger untouched.
  **Generalises:** every one of the four carried a comment claiming it
  matched `BoardRow`'s StakeChip, which had gone ledger-first on
  2026-07-30. A comment asserting parity is not parity. When a rule
  changes in one surface, grep for the surfaces that CLAIM to follow it —
  T8.30 said "fix all builders" about `discord_broadcasts.py` and the
  same sentence was true here.

- [x] **T8.32 — the daily budget could shortchange the published No.1** ✅ 2026-08-11
  The 15u/day cap is allocated in LOCK order (first-pitch-minus-60), so a
  weak early game takes its stake hours before a strong late game is even
  a candidate. T8.19 fixed the within-batch half; games three hours apart
  are never in the same batch.
  **How it surfaced:** the operator asked why COL@ARI went 8u → 1u on the
  2026-08-11 board. The cap was working — CHC@WSH 6u at 5:45 PM, TEX@LAA
  8u at 8:38 PM, 1u left — but TEX@LAA and COL@ARI were both 71.28% and
  the No.1 badge turned on price while the budget turned on a **two-minute**
  gap in first pitch. Reverse the start times and the published No.1 ships
  at 1u.
  **Not an EV fix.** Best-first reordering of the whole budget was already
  measured at +0.6u/season, CI spanning zero (memory
  `2026-08-01_kelly_refinements_dead`). This is a product fix: the No.1 is
  what subscribers actually bet.
  **Fix:** `kelly_stake_units(reserve_units=...)`, held by `_size_row_stake`
  at COMMIT only, for the No.1 only, released the moment the No.1 commits
  (`_allocated_idents` within a batch, `bet_placed=Y` across them). Fails
  open to the old behaviour on any bad read; never fabricates a price.
  **Measured:** 0 of 10 settled slates since 2026-07-30 change; simulated
  realised total identical (+23.33u). Under a forced "No.1 locks last"
  stress it rescues 2 of 5 multi-pick days — 07-31's No.1 would have gone
  out at 4u instead of 8u.
  **Generalises:** a risk control that is EV-neutral can still be a product
  defect. "Which bet gets trimmed" is invisible in a season total and very
  visible on the one line you publish.

- [ ] **T8.31 — DraftKings now blocks Railway; the odds path is down** 🔴 OPEN 2026-08-11
  2026-08-11: 15 games, **0 priced**; last capture 10:56 PM ET 08-10.
  Railway's egress IP is 403'd by DK — the same failure class that killed
  the Contabo box, and Railway was the ONLY working source.
  **Confirmed it is the IP, not the code:** curl_cffi Chrome
  impersonation *is* running (its `raise_for_status` is the only thing
  that formats `HTTP Error 403: `, verified against library source), sub
  20150 still returns a full valid payload, and the identical code from a
  residential IP returns 15 clean odds rows. Four redeploys since the last
  good capture are all blocked, so unlike 2026-08-06 this is not one
  unlucky ephemeral IP.
  **Decided repair** (operator, 2026-08-11): buy DK's price from The Odds
  API — same book, so no era boundary and no record reset. Blocker is an
  `ODDS_API_KEY` in Railway, not code.
  **Shipped so far:** `--book` guard on `tools/fetch_odds_api.py` (see
  CHANGELOG 2026-08-11a) — without it a multi-book file re-prices the
  ledger at whichever book sorts last, because `import_odds` applies every
  matching row in file order.
  **Wiring SHIPPED 2026-08-11** (operator sign-off same day):
  `step_fetch_odds_api()` runs between scrape-dk and import-odds, writing
  the same `data/odds/dk_<date>.csv` the importer already reads — so the
  import path is unchanged. Gated on `PREDICTOR_ODDS_API=enabled`, off by
  default, killable from Railway's dashboard without a deploy.
  **The window is the cost control.** Every event is 1 credit and the loop
  runs every 5 min, so `--within-minutes 120 --skip-started` restricts each
  cycle to games actually approaching their lock; `--merge` keeps the
  already-captured prices in the file rather than overwriting them, and
  `--min-credits 50` is a floor so a runaway cadence cannot reach zero
  mid-month and silently unprice every remaining slate.
  Verified live against Railway's key: window correctly skipped 15/15
  events at 12:17 PM (first pitch 6:41 PM), **0 credits spent**, exit 0.
  11 regression tests in `tests/test_odds_api_fetch.py`.
  **Probes cost 2 credits and retired two risks:** the plan DOES serve
  `totals_1st_1_innings` (4 books quoted it), and **DraftKings is in the
  feed** — it quotes h2h on 15/15 games. DK's absence from the
  first-inning market at midday is TIMING, not plan coverage, which
  independently corroborates [[lock_earlier_dead]]: DK posts that line
  ~an hour before its own first pitch.
  **Still open:** confirm a real DK price lands tonight once games enter
  the window (~4:40 PM ET), and disable the now-useless
  `PREDICTOR_SCRAPE_DK` once the API path is proven.
  Rejected and not to be re-proposed: proxying, Railway static outbound
  IPs, a second budget VPS, and scraping from the operator's home
  connection — see the memory `odds_source_strategy`.

- [x] **T8.30 — a refused No.1 was published as a bet** ✅ 2026-08-10
  Live to subscribers. TB@OAK locked with quarter-Kelly having refused it
  (`units_risked` 0, `edge_on_pick` −0.9%) and the channel got
  "🔒 TONIGHT'S №1 PLAY … **Don't take worse than -130.**" beside a quoted
  price of **-145** — a price limit the quoted price already violates,
  under a headline naming it the night's play. The stake line was absent
  rather than zero because `if stake:` reads 0.0 as missing, so the only
  signal it was not a bet was "−0.9%" mid-sentence.
  **This is T8.18 in the two places it was never applied.**
  `build_board` grew the "This is not a bet" branch on 2026-08-06;
  `build_top_pick` and `build_top_pick_settled` did not. The settle ping
  was ~40 minutes from publishing "✅ THE №1 WON" over a running record
  that **excludes** the game — `select_top_picks` and
  `dashboard/lib/top-pick.ts` both drop a zero-stake night — implying an
  inclusion that never happened; on a loss it reports a loss the record
  never absorbs.
  **Fix:** `is_refused()` separates the three states callers were
  collapsing into two (>0 staked / ==0 refused / None unpriced, which is
  the ladder path and NOT a refusal); a refused No.1 routes to a new
  `discord_noplay` broadcast instead of a softened play message
  (operator's call — a headline naming the night's play should exist only
  when there is one); the settle ping routes to NO ACTION and states the
  record is unchanged; `_fmt_units()` stops a floored 0.5u stake printing
  as "Stake 0 units". `discord_noplay` registered in
  `tracker._DEDUP_WINDOW_M` in the same commit — an unregistered type
  inherits the 5-minute fallback and republishes ~12×/hour (2026-08-06).
  16 regression tests in `tests/test_refused_top_pick.py`.
  **Ledger untouched and already correct:** `tools/pl_calc.py --top-pick`
  reads 47-21 / +88.89u with the night correctly absent.
  **Also measured, and it answers the operator's second question — "should
  we lock earlier to capture the edge?" No.** DraftKings does not post
  first-inning lines early: across 263 placed STRONG YRFI bets the median
  gap between the first captured price and the bet price is **6 minutes**,
  and only **17 (6%)** ever had a price more than 2h before lock. On those
  17, locking at the first price would have gained **+0.26pp** of edge and
  12 of them never moved at all. Tonight's game was the rare exception
  (−120 → −145), and roughly 45% of its lost edge was the model correctly
  revising itself once lineups posted — which is exactly what the T-60
  lock exists to wait for.

- [x] **T8.29 — a model gate that says what it actually proves** ✅ 2026-08-10
  Closes the hole T8.28 exposed: from 2026-05-06 nothing checked model
  quality, and `tests.yml` only proves the money PLUMBING is
  self-consistent — a change that worsens predictions passes it green.
  `tools/model_gate.py` re-scores a committed 524-game 2026 holdout
  before and after a push and reports whether predictions moved; the
  parity answer ("UNCHANGED") is the valuable one, since refactors and
  ops work should never move the model and one that does is telling you
  something.
  **Its own workflow deliberately.** `tests.yml` has
  `paths-ignore: data/**` for the ~30 daily data commits, but the WEIGHTS
  live under `data/` — a weights change would have matched the ignore and
  triggered nothing. `model_gate.yml` uses an allowlist naming the
  artifacts explicitly.
  **Warn-only** (operator, 2026-08-10): ~30 pushes/day and a red gate
  during a live slate could block a real fix. `|| true` on the compare
  step is the switch; the header says so.
  **The instrument is held constant, the model varies:** the current
  gate script is copied INTO the baseline worktree rather than running
  the baseline's own copy, so a change to the gate cannot masquerade as a
  model change and a baseline predating the gate still scores.
  **Coverage floor, because a guard that quietly stops guarding is
  T8.28 again.** `_feats` skips rows with missing features, so a renamed
  feature would shrink the holdout silently and a 6-game gate would still
  report "unchanged". 524/735 today (the 211 skips are a missing
  `wx_wind_kmh` column, a data hole not a model fault); below 450 emits
  `::error::`, and 0 hard-stops. Verified by simulating a rename.
  **Limits stated in the tool, not just here:** 2026 only, because the
  repaired 2024/25 files are not in git and cannot be rebuilt in CI from
  a 232MB gitignored cache. Committing them is 13MB and safe from the
  Vercel bundle (`copy-data.mjs` allowlists and never copies
  `data/backtests/`) — an upgrade path, not a wall.
  Also fixed: PLAYBOOK 1.2 and 3 still routed to `daily_shadow_report.py`
  / `shadow_summary.csv`, deleted 2026-05-06 and missed by the T8.28
  sweep.
  **Generalises:** the useful question for a CI guard is not "did it
  pass" but "what would it have caught". State the limits in the tool's
  own output, or the next reader inherits a false assurance.
  **UPGRADED same day:** operator added the repaired 2024/2025 files, so
  the holdout is now 3,728 games across all three splits, reported PER
  SEASON. Justified immediately — on a synthetic 0.1% shift the aggregate
  Brier said BETTER while 2025 alone said WORSE, and the gate printed
  `!! MIXED ACROSS SEASONS`. The 2026-only version would have said
  "better, ship it", which is precisely the cross-year failure three
  splits exist to catch.
  `_ptfix` verified on disk, not trusted: ERA within-season variation is
  0.0%/0.0% for `truepit` AND for `truepit_pit` (the name is the trap),
  62.6%/64.8% for `truepit_ptfix`. 13MB, and unreachable by the Vercel
  bundle because `copy-data.mjs` allowlists and never copies
  `data/backtests/`. The 2024/25 files carry no `actual_result` (outcome
  derived from `fi_*_runs`) and no umpire column (imputed at the same
  0.50 the trainer falls back to). Floor raised 450 -> 3300.

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

- [x] **T8.4** Vercel rebuilt the entire site on every `auto:` data push
  — 188 of 242 commits since 2026-08-01 (78%), 99h 28m of Build CPU
  Minutes (51.6% of plan). Added `ignoreCommand` +
  `dashboard/scripts/should-build.sh`, which fails toward BUILDING on any
  uncertainty. Gated on proving Supabase (not the build bundle) serves
  the board: `generatedAt` advanced twice with no deployment. Replay over
  25 commits: 14 SKIP / 11 BUILD, 0 misclassifications. VERIFIED IN
  PRODUCTION: first auto: commit after the fix (`ccf19bb`) shows Canceled
  at 6s against 43s for a real build. Also added
  `.gitattributes` (`*.sh text eol=lf`) so a Windows CRLF checkout cannot
  kill the script on Vercel's Linux runner. ✅ 2026-08-07

- [x] **T8.5** After T8.4 stopped rebuilding on data
  commits, the build-time CSV fallback could go days stale AND was
  served silently — a Supabase outage looked identical to a healthy
  dashboard. Rejected "rebuild on the daily backup commit" after
  measuring that it lands 05:51-08:12 ET with every game LINEUP PENDING,
  i.e. it would bundle a pick-less board for a build a day. Shipped
  visibility instead: `BoardResponse.source`, `boardSource` in
  /api/health with a DEGRADED reason, and `watchdog.check_board_source()`
  paging during game hours. Verified by rebuilding with the Supabase env
  blanked. ✅ 2026-08-07

- [x] **T8.6** T8.4's skip check compared `HEAD^ HEAD`, but Vercel builds
  once per PUSH at the tip — so a code commit pushed together with a
  later data commit would be silently skipped and never deploy.
  Reproduced on real history (last build `34167933`, tip `aa21ba8a`:
  old=SKIP, new=BUILD). Now compares against `VERCEL_GIT_PREVIOUS_SHA`,
  with a targeted `git fetch --depth=1` for the shallow-clone case, which
  gets more likely the more builds we skip. Five edge cases exercised;
  replay still 0 misclassifications. ✅ 2026-08-07

- [x] **T8.7** `/api/health` measured BUILD age, not data age, after T8.4
  stopped rebuilding on data commits — it reported lastPredictAt 17:44
  (bundled) while the predictor had run at 18:16. `watchdog.check_dashboard()`
  PAGES on BROKEN at >240 min, so a false "Dashboard BROKEN" alert was ~4h
  from firing after every code push. Now Supabase-first for both the
  timestamp and the 24h error window, with a `freshnessSource` field naming
  which answered. Both branches forced and verified. ✅ 2026-08-07
- [x] **T8.8** `watchdog` was unregistered in `_DEDUP_WINDOW_M` and inherited
  the 5-min fallback against a 5-min loop — ~12 Telegram messages an hour on
  a persistent fault, against the file's documented "~3, not 72".
  `_ESCALATION_MIN` was defined and never read; deleted and the docstring
  corrected to the mechanism that exists. Simulated 12 cycles: 1 send.
  `transport_check` registered explicitly. ✅ 2026-08-07

- [x] **T8.9** The dashboard ranked the №1 on `nrfiPct` (1 decimal, for
  display) while Python ranks at full precision — so the board, hero and
  /brief named a DIFFERENT GAME than Discord, Telegram and the published
  record on 3 of 114 slates (2026-06-15, 06-20, 07-08). `BoardRow.nrfiP`
  added for deciding; `nrfiPct` documented display-only. Verified with the
  compiled `top-pick-rank.ts` over every slate: 3 → 0. ✅ 2026-08-07
- [x] **T8.10** "Don't take worse than" disagreed between surfaces: Discord
  −165, dashboard −162 for the same bet. Python walked a 5-cent grid on the
  ROUNDED stake and published limits past the full-unit line the operator
  decided not to cross; it now uses the dashboard's analytic solve. Swept
  2,497 pairs: 0 mismatches. Dead `price_ladder()` removed. Parity guard
  extended to the pass price and verified to catch a ceil→round
  regression. ✅ 2026-08-07

- [x] **T8.11** The money path had no tests and the repo had no CI on push.
  Added `tests/` (34 passing, 2 xfailed) covering kelly_stake_units, _calc_pnl,
  the caps, the No.1 rank tuple, the gate and the locks -- every expected value
  EXECUTED against the real code, weighted toward regressions of shipped bugs.
  Added `.github/workflows/tests.yml` (pytest + fixture parity + both dashboard
  guards) on every non-data push. OK 2026-08-07
- [x] **T8.12** `check-kelly-parity.mjs` never invoked Python, so it proved the
  dashboard matched a frozen FIXTURE rather than today's tracker. Demonstrated
  with NRFI_KELLY_ROUNDING=0.5: a real stake moved 4.0u -> 3.5u while the guard
  printed "ok". `tools/parity_fixtures.py --check` now closes the loop in CI.
  OK 2026-08-07
- [x] **T8.13** ORANGE `_row_is_nights_top_pick` self-excludes by AWAY@HOME
  name, not game_pk, so BOTH halves of a doubleheader are called No.1 -- two
  alerts under a No.1-only policy. Pinned xfail in tests/test_selection.py.
- [x] **T8.14** ORANGE The No.1 rival scan never checks `bet_placed`, so a
  cluster-demoted no-bet row competes for No.1 and wins, silencing the alert for
  the game the money is on. Live on 1 of 123 slates (2026-04-29 TB@CLE). Pinned
  xfail in tests/test_selection.py.

- [x] **T8.15** The published No.1 record substituted the runner-up when the
  night's top play did not settle: 2026-06-11 ATL@CWS (p 0.3219) was POSTPONED
  and the record counted CHC@COL (0.3543), which LOST. Both pl_calc and
  lib/top-pick.ts now rank FIRST and require the winner to have settled;
  unsettled nights are excluded and counted. 46-21/+84.72u -> 46-20/+87.72u,
  verified identical from Python and the dashboard. The NRFI exclusion (3 of the
  4 mismatches) is the operator's 2026-08-03 rule and is KEPT -- but the Discord
  ledger claimed "every night's top play" without disclosing the re-pick, and now
  states the population. OK 2026-08-07

  T8.13/T8.14 FIXED 2026-08-07: identity is now game_pk (falling back to
  name#game_number), and rows explicitly bet_placed='N' are excluded as both
  rivals and candidates -- empty is NOT excluded, since every row is pending
  before the odds import. Exactly one No.1 on all 123 real slates; suite 37
  passing, 0 xfailed.

- [ ] **T8.18** ⚠️ 2026-08-09 — Stake sized once at first price capture and never
  re-derived, so it froze against a probability the model had already replaced.
  LAD@ARI 2026-08-09: sized 2u from p=0.5831 at 02:00 ET; the model revised to
  0.6288 by 14:40 and the pick locked at 15:10 on the newer number while the
  stake stayed at the older one (`kelly_stake_units(0.6288, "-120")` = 5.0).
  The dashboard hero/board/Discord read `units_risked` (2u) while `/history`
  and the reconcile panel recompute (5u) — same bet, two published numbers.
  Root cause: `units_risked`/`edge_on_pick` only recompute when a NEW price
  arrives (`tracker.py:901`), while the probability freeze is gated on
  `bet_placed="Y"` which does not fire until T-60 (`tracker.py:923`), so the
  gap between first capture and lock is a free-drift window — 13 h here.
  `end_of_day_check.py:285` then stamps `bet_placed="Y"` post-game preserving
  the stale stake. Fingerprint: `edge_on_pick` ≠ `p − implied_p`; present on
  4 of 25 Kelly-era STRONG bets (07-27, 08-02, 08-04, 08-09).
  **BUILT, SHIPPED DARK** — 08-09 row corrected in CSV + Supabase (2u→5.0u,
  P&L 1.667→4.167, no DRIFT). The three-part fix is now in the tree behind two
  env flags, both default-off, so pushing it is a provable no-op until the
  operator flips them on Railway:
    * PART 1 `tracker._rederive_pre_lock_stake` (`NRFI_STAKE_REDERIVE`) — the
      stake tracks the model until lock. A pre-lock figure is a PROJECTION and
      never passes `game_date`, so it cannot allocate against the daily budget
      and cannot become order-dependent.
    * PART 2 `tools/lock_commit.py` (`NRFI_LOCK_COMMIT`) — a sweep AFTER
      import-odds that commits rows whose lock window opened with no fresh
      price. Three-predicate gate (in-window AND not started AND not terminal),
      best-bet-first allocation, one reset per batch, reuses `strong_locked`.
    * PART 3 `tools/stake_drift.py` — per-day Kelly REPLAY (a per-row recompute
      is unimplementable: it flags every cap-trimmed row). Wired into
      `pl_calc.py` (exit 1) and `reconcile.py` as I5, REPORT-ONLY, never heals.
  Supporting: `_size_row_stake` is the single writer of the
  (bet_placed, units_risked) pair; a batch-epoch guard makes the reset
  discipline self-enforcing; `_committed_on` no longer caches a failed budget
  read; `verify_kelly_wiring` CHECK 7/8. 76 tests pass (44 pre-existing
  untouched + 32 new), parity fixtures 21402/121 ok.
  Live drift today: 08-02 DET@OAK (7u vs 1u) and 08-04 SD@ARI (9u vs 8u) still
  flagged, both WINS, correcting them LOWERS recorded P&L by ~4.97u — awaiting
  operator decision. 07-31 correctly classified cap-order, not drift.

- [x] **T8.23** ✅ 2026-08-09 — THIRTEEN COLUMNS WIPED ON EVERY PREDICT TICK,
  and it is the answer to "why is CLV unmeasurable" (open since 2026-07-27).
  `log_picks` rebuilds each row from a dict literal + a short `preserve` list;
  `csv.DictWriter` writes `""` for any FIELDS key in neither, ~12x/day. Twelve
  columns just lost data. The thirteenth self-repaired with the WRONG value,
  which hid it: `_apply_odds_to_row` re-seeds `opened_*_odds` only when blank,
  so the wipe made the "opening" price re-seed from the CURRENT scrape every
  cycle. **1191 of 1277 priced rows (93.3%) have opened == market**, still
  93.3% in August. Not a capture gap — this. The other nine belong to the v21
  shadow model and the last-10 top-3 splits and were erased by a process that
  does not own them. Only the pre-game branch leaked; the locked/graded branch
  already copies everything outside `allow_update`.
  **NOT RETROACTIVELY FIXABLE** — the true opening price was never stored, so
  the 1191 rows are unrecoverable. 86 rows carry real movement. CLV is
  measurable on new rows only; the first honest read is ~a month out, and any
  figure spanning earlier dates compares the price to itself.
  Guarded by `tests/test_preserve_columns.py`. Closes the CLV item in
  `open_items_2026_07_27`.

- [x] **T8.22** ✅ 2026-08-09 — THE №1-ONLY ALERT CROWNED EVERY PICK. Measured
  against `notifications_log`, not reasoned about: both multi-pick slates since
  the policy shipped 2026-08-05 fired a "BET LOCKED" alert for EVERY pick
  (08-05 TB@COL + WSH@PHI; 08-06 WSH@PHI + SD@ARI). Two for two. On 08-06 the
  first to ping was 4 confidence points WORSE than the play that pinged later,
  and Discord published the correct №1 that night — so the surfaces disagreed.
  Cause: `bet_placed="N"` means both DECLINED (edge gate / daily cap / cluster
  demotion) and T2.58 PENDING ("commits at its own lock"), and the rival scan
  discarded both. Games lock at their own T-60, so when one flipped to "Y"
  every rival was still "N" and each game in turn saw an empty field.
  Fixed by `tracker._is_declined_not_pending`: skip a rival only when it is
  `N` with no positive stake — the same discriminator `end_of_day_check` uses,
  so the two agree by construction. Fails open on an unparseable stake.
  Replayed on the real ledger: 08-05 → TB@COL only, 08-06 → SD@ARI only; one
  ping, correct game, both lock orders. No dashboard change — `top-pick-rank.ts`
  never had a commit-state filter, so this moves tracker INTO line with it.
  Pinned by `tests/test_top_pick_gate.py` (12 tests).

- [x] **T8.20** ✅ 2026-08-09 — THE TEST SUITE WROTE TO PRODUCTION. Twice in one
  session a test run inserted fabricated rows into the live Supabase
  `picks_2026` table and they rendered on the public dashboard — once as
  "THE №1 PLAY · CCC at DDD · STAKE 10u", once as four plausible-looking games
  (CWS@TB 8u, KC@COL 5u, MIL@LAA 2u, DET@OAK 0u). They also consumed 11u of the
  daily budget, producing a false drift report against the real LAD@ARI pick.
  Mechanism: `log_picks` and `import_odds` both end in
  `_mirror_picks_to_supabase`, a no-op with the Supabase env vars unset and a
  live production write with them set — and they ARE set on the operator's
  machine, because that is how the real predictor runs. Rows deleted (never
  reached the CSV; Discord unaffected, it reads the CSV). Fixed by
  `tests/conftest.py`: three `autouse` guards blocking Supabase (env unset AND
  mirror replaced), Telegram, and `_write_rows` against the repo's `data/`.
  Suite Supabase traffic 12 → 0.

- [x] **T8.19** ✅ 2026-08-09 — `verify_kelly_wiring` CHECK 7 measures, for the
  first time, that the daily 15u budget is allocated FIRST COME FIRST SERVED, so
  the same slate sizes differently depending on row order: 2026-07-31 gives
  CWS@TB 8u/KC@COL 5u/MIL@LAA 2u/DET@OAK 0u in file order and
  4u/5u/5u/0.5u reversed. A pick's PUBLISHED stake therefore depends on where it
  sits in the CSV. Pre-existing and NOT caused by T8.18 — verified by running the
  identical allocation against an unmodified worktree at HEAD (byte-identical
  vectors) — and already named as a KNOWN LIMITATION in `kelly_stake_units`'
  docstring. `tools/lock_commit.py` fixes it for its own sweep by sorting
  best-bet-first; `import_odds` still allocated in DK-file order.
  **FIXED 2026-08-09 (operator: "lets fix this").** `import_odds` now splits
  matching from sizing: matching keeps DK-file order, sizing sorts by
  `_top_pick_rank_tuple` before allocating, so both writers use one canonical
  order and it is the same order the №1 rule and `top-pick-rank.ts` use. CHECK 7
  now passes with identical vectors in both directions, and
  `tests/test_allocation_order.py` drives the real `import_odds` (not a
  reimplementation of its allocator) to pin it: same allocation either way, cap
  respected, strongest play funded first, three consecutive batches identical.

- [x] **T8.21** ✅ 2026-08-09 — `tools/apply_manual_odds.py` was a second T8.18
  in the same column: it stamped `bet_placed="Y"` with NO lock-window check and
  a flat `"1"` stake, writing the pair independently. Any row it touched was
  then frozen out of the T8.18 re-derive forever by the T2.23 lock. Now routed
  through `_size_row_stake`, which decides commit-vs-pending from the lock
  window and writes both columns together; a row already committed at a real
  stake is left alone. Dormant (override file empty) but it would have silently
  undone the fix.

- [x] **T8.17** ✅ 2026-08-08 — THE BOARD printed a committed stake for a pick
  that had not locked. Published `CLE@CWS · Stake 6 units` at 2:07 PM for a
  7:15 PM game locking at 6:15 PM; the model reversed it (and TOR@PHI) to LEAN
  before the lock, leaving no STRONG pick while a 6-unit instruction stood in
  the channel. A stake is now only printed as an instruction once the pick can
  no longer change; before that it reads `Projected … NOT LOCKED` with the lock
  time. Sibling of T8.16 (same root: board speaks at slate time, picks decide
  at game time). Also fixed a self-inflicted time bomb: the T8.16 lock tests
  hardcoded a slate date that aged past `_pick_is_locked`'s 24 h defensive lock
  and went red overnight.
- [x] **T8.16** THE BOARD said "the model looked at every game and declined them
  all" while its own PASSING list showed four games as `Lineup Pending` -- one of
  which (LAD@ARI) became a 3-unit No.1 three hours later. The board fires at T-60
  before the FIRST game; picks commit 60 min before EACH game, so late games are
  structurally unjudged at board time. Added `is_undecided()` and split the
  no-plays branch into NOT SET YET (pending) vs NO PLAY TONIGHT (all judged);
  a board WITH a play now also discloses outstanding games. 5 tests, suite 41
  passing. OK 2026-08-07

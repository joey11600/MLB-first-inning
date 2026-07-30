# NRFI Terminal — Agent Rules

Read this first. These are the operating rules for any Codex (or other
agent) working on this repo. Violating any of them costs the user real
time and money. Read [docs/KB.md](./docs/KB.md) for the system overview;
this file is just the rules.

## Talking to the user — IMPORTANT

The user is **not well-versed with developer terminology**. They have
explicitly asked to be spoken to like a complete novice / taught.
Default tone is "explaining to a smart person who hasn't done
programming before," not "explaining to a peer engineer."

Concrete rules:

- **No bare acronyms.** First time using API / HTTP / CSS / JSON / SSR /
  CLI / env var, briefly explain what it is, then keep using the term.
- **Translate error output.** If you show them a `401 Unauthorized` or a
  stack trace, follow with one plain sentence: "this means the server
  rejected the request because the password header was missing."
- **Click-by-click steps for anything they run themselves.** Don't say
  "run `vercel env add CRON_SECRET`." Say: "open the Vercel dashboard,
  go to your project, click Settings, then Environment Variables, then
  the Add button..." with the exact field values to type.
- **Don't dump walls of jargon.** If you find yourself writing a
  paragraph with five new technical terms, stop and rewrite.
- **Be patient with re-explanations.** They may ask the same conceptual
  question across sessions. Re-explain freshly each time; never tell
  them "as I said before."

This applies to the SYSTEM-FACING work too — code comments, commit
messages, CHANGELOG entries — should still be written normally for
future agents. But everything that goes BACK TO THE USER in chat
should follow the rule above.

This file mirrors `CLAUDE.md` (the equivalent rule file for Claude
agents). When you change rules in one, change them in the other so the
two stay in sync.

## Deploy rules — read before touching the dashboard

The Vercel project is auto-wired to deploy on any push to
`claude/mlb-inning-run-predictor-QyazL`. The GitHub Actions cron pushes
~12 commits per day to this branch (every hourly `auto: predict` run).
**Each cron push triggers a Vercel auto-deploy.** That means:

> Any uncommitted local change that you deploy via the Vercel CLI will be
> SILENTLY OVERWRITTEN by the next cron commit's auto-deploy, which builds
> from the remote branch source — *without* your uncommitted change.

This is exactly what happened on 2026-05-01 (T2.17/T2.18 incident — see
AUDIT.md). The user lost ~30 minutes verifying a fix, only to see it
overwritten by a cron-triggered auto-deploy. Don't repeat that.

### The only correct deploy procedure

For **code changes** (anything under `dashboard/`, `scrape_dk_odds.py`,
`mlb_first_inning_predictor.py`, `tracker.py`, workflows, configs):

```bash
# 1. Make your change
# 2. Commit it
git add <files>
git commit -m "..."

# 3. Push to the production branch
git push origin claude/mlb-inning-run-predictor-QyazL

# That's it. Vercel auto-deploys from the push within ~60 seconds.
# You can verify by polling the live URL for an expected marker:
#    curl -sL https://nrfi-terminal.vercel.app/ | grep -q "<marker>"
```

**Do not run `vercel --prod` or `npx vercel --prod` directly for code
changes.** It deploys local files, then a cron commit will overwrite it
within the hour. If you absolutely must deploy via CLI (env var test,
emergency rollback, etc.) — use `cd dashboard && npm run deploy`, which
runs `scripts/safe-deploy.sh`. That script aborts if the working tree is
dirty or the local branch is behind origin, preventing the failure mode.

### Verifying a deploy is live

Before reporting "deployed" to the user, confirm the live URL actually
serves your change. Vercel deploy success ≠ alias actually pointing at
your build (an interleaved cron push can move the alias). Cheap check:

```bash
# Look for a CSS class or string that only exists in your new code:
curl -sL https://nrfi-terminal.vercel.app/ | grep -c "<my-marker>"
# Should be > 0.  If it's 0, something raced you.
```

If a poll loop is needed (the build is in flight), use Bash with
`run_in_background: true` and an `until` loop, not chained sleeps.

## Data integrity rules

The CSV ledger at `data/picks_2026.csv` is append-mostly. Specific rules:

- **Atomic writes only** (`tracker._write_rows` does this via tempfile
  + `os.replace`). Don't bypass it.
- **Never delete rows.** Even `POSTPONED` rows stay; they just get
  re-graded if the game resumes. See `tracker.grade_picks` (T1.5).
- **Locked picks freeze.** `_pick_is_locked` has 3 defensive locks
  (graded terminal / >24h past / `created_at` >12h stale). Don't soften
  these without understanding T2.2 + T2.12.
- **Pick changes are journaled.** Every flip writes to `pick_changes.csv`.
  90-day rolling retention (T3.5). Never truncate this file manually.

## Quoting P&L numbers — use the calculator, never math in your head

Before stating any P&L figure to the user (chat, summary, commit
message, anywhere), run `python tools/pl_calc.py` for the relevant
date / window and copy the number it prints.  Do NOT add up the
column in your head -- on 2026-05-05 a mental-math error in chat
("+3.22u") combined with a backfill mirror bug to make the user see
THREE different numbers for the same slate within ten minutes.  The
calculator is the canonical answer.

Quick reference:
- Today's slate (ET):           `python tools/pl_calc.py`
- Specific date:                `python tools/pl_calc.py --date 2026-05-04`
- Trailing 7d / 30d / season:   `python tools/pl_calc.py --window 7d`
- Include LEAN bets too:        `python tools/pl_calc.py --include-lean`

The script also runs a consistency check: every row's stored
`profit_loss_units` is recomputed against `tracker._calc_pnl` and
flagged "DRIFT" if they disagree.  A drift means something modified
the row without going through `_calc_pnl` (e.g. a backfill mirror
that overwrote real odds with blanks).  Fix any drift before
quoting numbers.

## Money rules

- **Flat 1u plays only.** User explicitly rejected Kelly / fractional /
  bankroll-aware sizing (T4.25-27 skipped per user preference). Don't
  introduce per-bet sizing variation without checking with the user.
- **Min edge threshold is 2% — applies to LEAN ONLY.** STRONG picks
  auto-Y regardless of edge (T2.24). User's policy: "if the model
  commits STRONG, we bet at whatever odds DK has." Don't add an edge
  gate to STRONG without explicit user permission.
- **`profit_loss_units` only fills for `bet_placed=Y`** (real bets at
  real prices). The dashboard's TOTAL P&L falls back to flat -110 for
  rows without imported odds — see `dashboard/lib/roi.ts:248-260`. The
  TOTAL is the right number to point at when judging the model.
- **Once `bet_placed=Y`, market_*_odds is LOCKED** (T2.23). The user
  is already in the bet at that price; subsequent DK line movement
  doesn't update the row. This is intentional — don't "fix" it by
  re-enabling closing-line tracking unless the user explicitly asks.
  `opened_*_odds` still tracks the first scrape, so "open → bet"
  CLV is preserved.
- **Never fabricate odds.** If the scraper missed a game, the row stays
  un-priced. Don't synthesize "what DK probably had" — leads to
  fabricated CLV.

## Test methodology rules

- **Out-of-sample validation is non-negotiable** for any model change.
  Run `test_*.py`-style 3-split (2024→2025, 2025→2024, 2024+2025→2026).
  Reject any feature that helps in only one direction. See
  `nrfi_model_architecture.md` in user memory for the full retraining
  procedure.
- **Holdout leakage guard** (T4.7): `two_stage_model.py` refuses to
  train if `--test` file is also in `--train` list. Don't bypass this.
- **Don't add 2022/2023 backtest data** to training — pre-pitch-clock
  distribution shift makes those seasons hurt the model. Documented in
  `nrfi_model_architecture.md`.

## Documentation rules

- **CHANGELOG.md** at repo root — dated log of shipped changes. Add a
  section every time you ship something user-visible. Keep the
  performance snapshot current.
- **ROADMAP.md** at repo root — forward-looking upgrade list (Tier 1-5),
  status tracking, recommended sequence. When you ship a roadmap
  item, move its row from the relevant tier into the **Recently
  shipped** table at the top of ROADMAP.md and check the box.
- **AUDIT.md** at repo root — running checkbox list of audit items.
  Mark items with `✅ <date>` when complete. Don't recycle audit IDs.
- **docs/KB.md** — single-page system overview. Update when major
  architecture changes ship.
- **User memory** (`~/.Codex/projects/.../memory/*.md`) — model
  internals, dashboard architecture, feature backlog. **Verify against
  current code before quoting** — system reminders flag these as
  potentially stale (point-in-time observations).

### When you ship anything (the "every fix and addition" rule)

The user explicitly asked for every fix/addition to be logged to the
knowledge base. Follow this checklist for *every* shipped change, not
just the big ones:

1. **CHANGELOG.md** — add a row under today's date section
   (Added / Changed / Fixed / Deferred). Cross-reference an audit
   ID `T<x.y>` if relevant. Include the commit SHA.
2. **ROADMAP.md** — if the change is a roadmap item, check it off
   `[x]` and move it to the "Recently shipped" table at the top.
3. **AUDIT.md** — if the change closes an audit item, mark it
   `✅ <date>`.
4. **docs/KB.md** — update the architecture diagram, file map, or
   "daily ops" table only if those facts changed. Skip if the change
   is internal-only.
5. **Commit all four (when relevant) in the same commit as the code
   change.** This way `git log -p` shows code + doc together; future
   readers don't have to chase across commits.

## Notifications

The cron pings `@nrfi_terminal_bot` (Telegram) when picks flip to/from
actionable states (T2.22). Configured via `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID` GHA secrets; silent no-op without them. Code lives
in `tracker._notify_pick_flip_telegram`; called from
`tracker.log_picks` alongside the `pick_changes.csv` write. Failures
are caught and logged but never break the predictor.

If a user reports "I didn't get a Telegram ping for X game" — first
check `pick_changes.csv` for the flip (proves it was logged); then
check workflow logs for any `[telegram] notify failed:` stderr; then
check whether the GHA secrets are present (`gh secret list -R <repo>`).
Don't rotate the token without explicit user permission — that
invalidates their bot config.

## Working with the user

- The user runs production from this branch. **Don't break working
  state.** When in doubt, do less.
- The user prefers **flat 1u plays, no Kelly sizing** (asked + answered).
- **PALETTE REVERSED 2026-07-29.** The user now wants **bright colours
  on a cool deep-ink page** and said so plainly: *"i hate the orange
  colors. it needs to be bright colors that look great."* The
  warm-brown / peach palette this file used to mandate is RETIRED. Do
  not restore it. Current tokens: `--primary` cyan `#22d3ee`,
  `--secondary` amber `#fbbf24`, `--destructive` rose `#fb5c78`, on
  cool ink surfaces (`--background` `#0a0e14`).
  **Terminal green/red is still rejected** — that was a separate,
  earlier decision and the repalette did not reopen it.
  Always work through the semantic aliases `--gain` / `--loss` /
  `--attn`, never the raw token names, so the meaning is readable at
  the call site.
- The user **does not want odds fabricated** for unmatched games.
- The user gets frustrated when picks appear to disappear (incident
  history: 4/30 grade reset, 5/01 odds-chip-hidden, 5/01 deploy-overwrite).
  Default to verifying data integrity AND deployed state when they
  report something missing.

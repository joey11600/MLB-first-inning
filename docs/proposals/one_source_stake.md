# PROPOSAL — one source for the stake

**Status: DRAFT, nothing shipped.** Written 2026-08-13 for operator
review. No code in this document has been applied. The money path needs
explicit approval before any of it moves.

Trigger: 2026-08-13 CIN@CWS, the night's No.1, published at 66.87% and
staked at 2u where the rule says 7u. Won. Corrected to 7u by
`tools/heal_2026_08_13_split_brain_stake.py` — that heal fixed the ROW,
not the CAUSE. This document is the cause.

---

## The problem in one picture

Two machines run the model. Neither knows the other exists.

```
  GitHub Actions  ──model run 15:58:04Z──►  YRFI 66.87%  ──► probability, board,
   (hourly)                                                    dashboard, Discord

  Railway         ──model run 16:11:31Z──►  YRFI 58.6%   ──► bet_placed,
   (every 5 min)        │                                     units_risked, edge_*
                        └── only host that can reach DraftKings
```

The published row is a splice of the two: **GHA's probability next to
Railway's stake.** Quarter-Kelly is correct in both — 0.586 → 2u,
0.6687 → 7u. Nothing is broken arithmetically. The row is just describing
two different beliefs at once, and the reader cannot tell.

This is not the T8.18 pre-lock freeze. `NRFI_STAKE_REDERIVE` is enabled
and worked — it re-derived every five minutes, to 2u each time, because it
re-reads the probability *on the host it runs on*. **A re-derive cannot
outrun a bad input.** T8.18 PART 3 (`tools/stake_drift.py`) did catch it,
which is the system working; it just catches it after the fact.

### Why the numbers differed today

Both hosts agreed on `combined_lambda` (1.0369 everywhere), so the park /
scoring-rate half is fine. The gap is the lineup-driven half. GHA climbed
55.8 → 56.6 → 57.5 → 60.0 → **66.9** as the real batting order posted.
Railway sat frozen at **58.6** across nine consecutive cycles — roughly
GHA's ~14:40Z state.

**Railway, despite running 12× more often, was working from staler lineup
data.** That sub-problem is unexplained and is a hard prerequisite below.

---

## Options

### Option A — Railway sizes from the ledger's probability, not its own

Smallest diff. In `_size_row_stake`, take the probability from the
authoritative stored row when that row is newer than the local model run,
instead of unconditionally reading `row[prob_col]` off the freshly
computed dict.

- **Pro:** contained, reversible, no cadence change.
- **Con:** "newer" needs a trustworthy clock, and `created_at` is written
  by whichever host last touched the row — the same ambiguity that caused
  the bug. It makes the failure rarer without making it impossible, and it
  adds a Supabase read to the sizing path (a network call inside the money
  path, which is its own risk).
- **Verdict:** a mitigation, not a fix.

### Option B — GHA is the only model runner; Railway only prices and sizes

- **Pro:** one probability by construction.
- **Con:** the probability then refreshes **hourly instead of every five
  minutes**. Lineups land in the last 90 minutes before first pitch; this
  is exactly the window that matters, and today's own numbers show the
  probability moving 57.5 → 66.9 inside it. This trades a rare splice for
  a permanent staleness. **Recommend against.**

### Option C — Railway is the only model runner; GHA becomes commit-only ✅

Railway already runs every 5 minutes, already owns the money columns, and
is already the only host that can reach DraftKings. Make it the only host
that computes a probability. GHA's predict step is replaced by: pull the
current state from Supabase, write the CSV, commit, push.

- **Pro:** one brain. Probability, edge and stake are computed in the same
  process from the same inputs, so they cannot disagree. Keeps the
  5-minute cadence. Removes a whole class of races rather than narrowing
  it.
- **Con:** Railway becomes a single point of failure for predictions — if
  it is down, the board goes stale rather than degrading to hourly. Needs
  the existing heartbeat to alarm on staleness, not just on errors.
- **Blocker:** *only correct once the lineup-staleness sub-problem is
  understood.* Unifying on Railway today would have published 58.6% and
  staked 2u — consistent, and consistently worse.

### Option D — coherence guard at commit (complements any of the above)

At the moment a bet commits, compare the probability that produced the
stake against the probability stored on the row. If they differ by more
than a small tolerance, **refuse to commit** and alarm.

- Fails safe: no bet is placed on a number we cannot reconcile.
- Independent of which host wins, so it holds even if A or C regresses.
- Cheap: one comparison in `_size_row_stake`, no network.
- **Caveat:** a refusal is a no-bet, and a no-bet on the No.1 is its own
  kind of loss. Alarm must be loud enough that the operator can place it
  manually inside the window.

### Option E — make the splice visible (diagnostic only, no behaviour change)

Stamp the probability that actually sized the bet into its own column
(`sizing_prob`), written by the same code path that writes `units_risked`.

- Today's row would have read `yrfi_prob=0.6687, sizing_prob=0.5864` and
  the contradiction would have been obvious on the dashboard immediately,
  instead of needing Railway log forensics.
- **Cost:** a ledger schema change (CSV column + Supabase column + the
  dashboard's readers). Additive and low-risk, but it IS a ledger change,
  so it needs approval on those grounds alone.

---

## Recommended sequence

1. **Diagnose Railway's lineup staleness first.** Nothing else is safe to
   pick until we know why the 5-minute host had older lineups than the
   hourly one. This is investigation only, no writes.
2. **Ship Option D (coherence guard).** It is the smallest change that
   makes today's failure *impossible to publish silently*, and it is
   correct regardless of which architecture we land on.
3. **Ship Option E (`sizing_prob`)** if the operator accepts the ledger
   column — it turns the next occurrence into something visible on the
   dashboard rather than something found by squinting at logs.
4. **Then Option C**, once step 1 has an answer. This is the actual fix;
   1–3 are the safety net that makes it safe to attempt.

Deliberately **not** recommended: widening `stake_drift`'s tolerance, or
adding retries to the re-derive. Both make the alarm quieter without
touching the cause.

## Testing bar

Per the repo's methodology rules, none of this is a model change, so the
3-split out-of-sample procedure does not apply. What does apply:

- `tools/verify_kelly_wiring.py` must still pass unchanged — the sizing
  formula is not being touched, only the input to it.
- `tools/stake_drift.py --all` before and after, expecting an identical
  violation set (this work must not silently reclassify history).
- A replay of every settled slate since 2026-07-30 showing **0 stake
  changes**, the same bar T8.32 was held to.
- For Option D specifically: a forced-divergence test proving the guard
  refuses, alarms, and leaves the row exactly as it found it.

## What this does not address

`stake_drift.py`'s known blind spot is unchanged: on a day where the 15u
cap binds, the per-row split depends on allocation order, and the replay
allocates best-bet-first while the ledger is first-come-first-served. That
is documented in its own docstring and is a separate problem.

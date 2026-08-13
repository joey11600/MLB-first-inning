# PROPOSAL — one source for the stake (v2, mechanism corrected)

**Status: DRAFT, nothing shipped.** Written 2026-08-13, rewritten the
same day after the root-cause investigation completed. No code in this
document has been applied. Model-input and money-path changes need
explicit operator approval before any of it moves.

Trigger: 2026-08-13 CIN@CWS, the night's No.1, published at 66.87% and
staked 2u where the rule says 7u. Won. Row corrected to 7u by
`tools/heal_2026_08_13_split_brain_stake.py`. That fixed the ROW; this
document is about the CAUSE.

---

## v1 of this document was wrong about the mechanism

v1 framed the defect as host split-brain: "GHA computed 66.87%, Railway
computed 58.6%, the sizing host won." The completed investigation
disproved that:

- Railway's 15:04–15:58Z deployment printed **66.9% every cycle**.
- GHA's own 16:47Z run computed **58.6% fresh** (its log prints the fresh
  verdict beside the frozen board line).
- **The hosts agreed at every instant.** What split was TIME.

## The actual mechanism

MLB's `schedule?hydrate=lineups` feed **withdrew the CWS lineup card**
for roughly an hour, exactly across the lock:

| UTC | event |
|---|---|
| ~15:03 | CWS card posts → model 66.9% (both hosts) |
| 15:58 | game-time correction 2:10→1:10 ET → **lock moves an hour earlier**, to 16:10 |
| 15:58→16:06 | **card withdrawn** → home side silently regresses to team_fallback → 58.6% (both hosts) |
| 16:11:33 | bet commits 90s into the window, sized on the live 58.6% → **2u**; T2.23 freezes it |
| ~17:03 | card returns, unchanged; actual first-pitch top-3 = the withdrawn card |

The published row spliced two moments: probability frozen from 15:58
(pre-withdrawal, via T2.25), stake sized at 16:11 (mid-withdrawal).

Two design facts made it silent:

1. `fetch_top3_batters` has **no memory** — a card seen 10 minutes ago
   that vanishes is indistinguishable from a card never posted. No alarm.
2. STRONG deliberately skips the lineup-pending guard (operator call,
   T-V21-2026-05-08e), so the degraded state doesn't block the bet — it
   just sizes it small.

## Why host consolidation is NOT the fix for this class

v1's Option C (Railway becomes the only model runner) would have produced
a COHERENT row — 2u sitting next to a published 58.6% — and the No.1
would still have been under-staked on a winner whose real lineup matched
66.9%. Consolidation fixes row coherence and operational simplicity; it
does not defend against a flapping upstream feed. It stays on the list,
demoted to ops hygiene.

---

## Options (layered; they compose)

### Layer 1 — Sticky lineup cards (input robustness) ⭐ the targeted fix

Once a side's posted card has been seen for a game, never regress that
side to team_fallback. A DIFFERENT card replaces the old one (a real
scratch updates honestly); an empty response retains the last-seen card.

- Defends exactly against what happened: withdrawal ≠ un-knowing the
  batters.
- **This is a model-INPUT change** — it alters live predictions in
  withdrawal windows. Backtests can't test it (they read post-game
  boxscores, which are never withdrawn), so the bar is: a replay of
  today's incident producing 66.9→7u, plus proof that a genuinely revised
  card still propagates (synthetic test), plus `verify_kelly_wiring.py`
  unchanged.
- Persistence question for review: per-host cache stickiness (small,
  survives within a deploy) vs. reading the row's own stored
  `*_top3c_source`/`lineup_json` as the memory (host-independent, but
  touches the ledger-read path). Recommend starting per-host: today's
  incident would have been covered by Railway's own 15:0x sighting.

### Layer 2 — Size from the value being published (coherence by construction)

At commit, derive the stake from the probability THE ROW PUBLISHES, not
from a separate fresh compute. `stake_drift.py`'s invariant — stake ==
rule(published p, price) — becomes structurally true instead of checked
after the fact.

- Would have staked ~7u on 2026-08-13 (the row carried 66.87% at commit
  on the host that had last written it — see the caveat).
- **Caveat stated plainly:** each host stores its own copy of the row, so
  "the published value" is only single-valued if the probability column
  has ONE writer (or the sizer reads it from Supabase at commit — a
  network call in the money path, which needs its own failure-mode
  review). This is where a narrow slice of v1's one-writer idea survives:
  one writer FOR THE PROBABILITY FIELD, not one host for everything.
- On 2026-08-13 the two copies straddled the withdrawal (GHA's 15:58
  copy pre-outage, Railway's 16:06 copy mid-outage) — which copy was
  "right" was luck of refresh timing. Layer 2 without Layer 1 fixes
  coherence, not sizing quality. Together they fix both.

### Layer 3 — Lineup-regression alarm (operator visibility)

When a side that was lineup-sourced regresses pre-lock, fire an ops
Telegram (registered in `_DEDUP_WINDOW_M` in the same commit, per the
Discord/notify rule). On 2026-08-13 it would have fired ~16:06 — four
minutes before lock — naming the game, the side, and the probability
delta. Cheap, no behaviour change, ships independently of 1 and 2.

### Demoted — host consolidation (ops hygiene, later)

One model runner (Railway, 5-min cadence; GHA commit-only) still removes
a whole class of copy-divergence and simplifies reasoning. Do it after
Layers 1–3, if at all. Prerequisite unchanged: heartbeat must alarm on
staleness since the board then has a single point of failure.

### Explicitly not proposed

Widening `stake_drift` tolerances, retry loops in the re-derive, or an
edge gate on STRONG (T2.24 requires operator sign-off and nothing here
justifies one). All of these quiet the alarm without touching the cause.

## Recommended sequence

1. **Layer 3 (alarm)** — smallest, no behaviour change, immediate eyes.
2. **Layer 1 (sticky cards)** — the targeted fix, behind an env flag on
   Railway first (same rollout pattern as T8.18 PART 1), with the replay
   test above.
3. **Layer 2 (size-from-published)** — after 1 proves stable, since its
   value is highest when the published number is itself robust.
4. Host consolidation — optional, later, on its own merits.

## Testing bar (unchanged from v1 where applicable)

- `tools/verify_kelly_wiring.py` passes unchanged at every layer.
- `tools/stake_drift.py --all` before/after each layer: identical
  violation set (no silent reclassification of history).
- Replay of every settled slate since 2026-07-30: **0 stake changes**
  from Layers 2–3 (Layer 1 changes stakes ONLY in withdrawal windows —
  enumerate them explicitly and show each is an improvement or neutral).
- Layer 3: forced-regression test proves it alarms and changes nothing.

## What this does not address

`stake_drift.py`'s cap-day allocation-order blind spot (documented in its
own docstring) is a separate problem and untouched here.

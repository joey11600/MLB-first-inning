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

### Layer 1 — Sticky lineup cards (input robustness) — ✅ SHIPPED 2026-08-13

Shipped with operator approval. A card once seen is only ever REPLACED,
never forgotten: empty fetch sides refill from the ledger row's own
`*_lineup_json`; a non-empty fetch always wins (real scratches replace
honestly). Sticky sides tag `lineup_sticky`; the pending guard accepts
both tags; the layer-3 alarm reports "bridged" vs "regressed".

The persistence question resolved itself during implementation: the v1
recommendation (per-host cache stickiness) was WRONG — the cache dies
with its host (gitignored → GHA starts empty every run; Railway resets
on every hourly auto-deploy, proven on the incident: the 11:58 ET
container started with an empty cache inside the outage). The ledger row
is the only memory that survives both lifecycles, and it is the same
memory on every host.

Flag: `NRFI_STICKY_LINEUPS=enabled` on BOTH hosts (Railway variable +
`daily.yml` predict-step env) — both, because a non-sticky host writing
team_fallback into the row mid-outage destroys the chain. Kill switch:
delete both; rows self-heal on the next successful fetch.

Acceptance evidence: `tests/test_sticky_lineups.py` replays the 12:06 ET
incident state with the real batter IDs — the withdrawn CWS card
(Meidroth/Grichuk/Vargas) restores exactly; scratch-replacement, chain
persistence, and malformed-memory refusals pinned alongside. Suite 170
passed; `verify_kelly_wiring.py` untouched.

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

### Layer 3 — Lineup-regression alarm (operator visibility) — ✅ SHIPPED 2026-08-13

Shipped same-day with operator approval, in two parts:
- `tracker._notify_lineup_regression_telegram` — fires on the
  lineup→fallback transition for pre-lock STRONG rows, quoting the
  probability and projected-stake shift. On 2026-08-13 it would have
  fired ~12:06 ET, four minutes before lock. Registered in
  `_DEDUP_WINDOW_M` (12h) in the same commit.
- `tools/stake_drift.py --notify` wired into the nightly grade cron —
  the PART 3 replay now runs unattended and pings on any violation
  surviving the exemptions, so a mis-sized stake surfaces the same
  night instead of when a human squints at the board.
Tests: `tests/test_lineup_regression_alert.py` (13 tests).

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

1. ~~**Layer 3 (alarm)**~~ — ✅ shipped 2026-08-13.
2. ~~**Layer 1 (sticky cards)**~~ — ✅ shipped 2026-08-13, flag on both
   hosts.
3. **Layer 2 (size-from-published)** — the remaining open decision.
   Value is lower now that layer 1 defends the input and layer 3 makes
   any residual splice loud the same night; the honest framing is
   "coherence by construction vs. two more moving parts in the money
   path". No urgency; revisit after layer 1's first real-world bridge.
4. Host consolidation — optional, later, on its own merits.

Two further candidates surfaced by the incident, listed for the
operator's consideration (both small, neither shipped):

- **Game-time-change alert** — the 2:10→1:10 correction silently moved
  the lock window an hour earlier, shrinking the runway to lock from ~2h
  to 12 minutes. An ops ping when a slate row's `game_time_et` changes
  would make the next such compression visible. Cheap; deferred rather
  than shipped because alert volume is a real cost here (see
  `_DEDUP_WINDOW_M`'s history) and the operator should choose the
  threshold (any change vs. changes that move a lock earlier).
- **`sizing_prob` ledger column** — stamp the probability that actually
  sized the bet next to `units_risked` (written by the same code path).
  Today's row would have read `yrfi_prob=0.6687, sizing_prob=0.5864` on
  the dashboard — the splice visible instantly instead of via log
  forensics. Ledger schema change (CSV + Supabase + dashboard readers),
  so it needs approval on those grounds alone.

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

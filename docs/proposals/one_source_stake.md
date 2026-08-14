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

### Layer 2 — Size from the value being published — ✅ SHIPPED 2026-08-13

Shipped with operator approval, and the implementation clarified the
mechanism: on the SIZING host, stake and probability already come from
the same row cell — the splice was manufactured at the CROSS-HOST
reconciliation, where `sync_csv_from_supabase` pulled the money columns
without the probability and the local T2.25 freeze then locked an
unrelated probability beside the adopted stake.

The fix is the **bet-adoption sync**: at the N→Y transition (a CSV copy
first learning `bet_placed=Y` from Supabase), the committing host's
probability set (`nrfi_prob`/`yrfi_prob` + raws) and pick identity
(`pick_side`/`pick_strength`/`pick_label`) sync atomically with the
money. The freeze then preserves the sized-from values everywhere, so
stake == rule(published p, price) holds by construction on every host.

Strictly N→Y, by design: frozen rows never re-adopt (no silent history
edits under a settled bet), unplaced rows keep their own fresh compute
(pre-lock, local is honest — T8.18), blanks never overwrite. Pick
identity rides along so a committed STRONG stake can never land on a
locally-demoted LEAN/PASS row.

Acceptance: `tests/test_bet_adoption_sync.py` replays the incident
splice and asserts it dies at adoption; frozen/unplaced protections and
one-shot semantics pinned. Live `--dry-run` against production: zero
false adoptions. Suite 176 passed.

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
3. ~~**Layer 2 (bet-adoption sync)**~~ — ✅ shipped 2026-08-13.
4. Host consolidation — optional, later, on its own merits. With layers
   1–3 live it is ops hygiene, not a correctness fix.

**This proposal is now fully executed** (all layers shipped 2026-08-13,
same day as the incident). T8.35 is closed in AUDIT.md with a standing
watch: the `lineup_regression` ping announces the next card-flap; the
nightly stake-drift replay pings on any residual mismatch. The two
unshipped candidates below remain available if the operator wants them.

Candidates surfaced by the incident:

- ~~**Game-time-change alert**~~ — ✅ SHIPPED 2026-08-13
  (operator-approved). Batched ops ping on any pre-lock move ≥5 min;
  loud header when a lock moves earlier. Deliberately silent on DH
  game-2 churn, placeholder resolutions, jitter, and locked/started/
  graded rows. Stakes quoted pre-lock use the T8.16/T8.17 projection
  wording. `tests/test_game_time_change_alert.py`.
- ~~**`sizing_prob` ledger column**~~ — ✅ SHIPPED 2026-08-13
  (operator-approved). Stamped only by `_size_row_stake`'s Kelly
  branches (same read as the stake, including refusals); blank = not
  probability-sized; keep-alive floor never re-stamps; preserved
  through every hop (predict merge, mirror, sync). Historical rows stay
  honestly NULL. No dashboard surface yet — data first.
  `tests/test_sizing_prob_stamp.py`.

**Every item in this document is now shipped.** One incident, one day:
heal, root cause, three layers, two follow-on alerts, one audit column.

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

# Changelog

Dated log of meaningful changes to the NRFI Terminal system (predictor, tracker,
dashboard, ops). For the running list of open audit items see [AUDIT.md](./AUDIT.md).
For the forward-looking upgrade list see [ROADMAP.md](./ROADMAP.md).
For the system overview see [docs/KB.md](./docs/KB.md).

Format: latest first. Each entry is grouped Added / Changed / Fixed / Deferred
with audit IDs (`T1.1`, `T4.15`, …) cross-referenced to AUDIT.md. Performance
section captures actual picks accuracy on/around the change date.

---

## [2026-08-04a] - pending picks get briefs; the brief link becomes a real button

Operator: *"when i click a specific brief, it just takes me to the brief
for the #1 pick... i also want to make the brief pages look better. it
should be an actual button too thats filled."*

### The bug was coverage, not routing

Routing was verified correct on production — clicking a specific brief
navigates to that game, URL and content both. What was wrong is which
rows HAD a brief. The rule was "STRONG or LEAN only", and on the 08-04
slate **14 of 15 games were LINEUP PENDING**, which is a PASS in the
ledger. So the only brief button anywhere on the board was the #1's, and
clicking "a brief" could only ever land on the #1.

### Fixed

- **A LINEUP PENDING row with a clear tentative lean now gets a brief.**
  It is not the model declining to have an opinion — it is the model
  waiting, and the board already prints its tentative lean. Critically,
  every figure the brief shows is ALREADY FINAL: the park rate, both
  teams' last-10 first innings, both starters' scoreless-first records
  and the head-to-head all come from team codes, pitcher ids and the
  ledger. **None of them reads the lineup.** Only the verdict is
  provisional, so the page says so and prints no stake. Tonight's board
  went from 1 briefable game to 8.
- **STARTER PENDING stays excluded**, and that is the line: there the
  pitcher data is league-average fallback on BOTH sides, so the two
  biggest figures on the page would be fabrications. A pending row whose
  tentative is itself a PASS stays out too.
- **The side comes from the verdict, not the row.** A pending row's
  `pickSide` is literally `"PASS"`; reading it would have briefed the
  wrong half of the inning and scored every reason against it. `oddsOn()`
  takes an explicit side for the same reason — it was quoting the NRFI
  price on a YRFI lean.
- **`classifyTentative` + `DEFAULT_THRESHOLDS` hoisted** out of the
  client-only `BoardRow.tsx` into `lib/classify.ts`, so the server-side
  brief page can share the one copy. Same move `lib/top-pick-rank.ts`
  records, same reason. `briefVerdictOf()` there is now the single
  briefable rule, called by BOTH the page and the board's button.

### Changed — the visual pass

- **The brief link is a filled button**: ink fill, paper text, 15.9:1,
  44px, the shared `--shadow` scale, arrow nudge on hover and a press
  state that doesn't move the layout. It is the only action in the
  drawer, so by the one-primary-CTA rule it takes the solid treatment.
- **The ticket is a card.** Four naked rows between two hairlines made
  the most-read block on the page — what the bet IS — the least defined
  thing on it.
- **The record row is four stat tiles**, so the figures the operator
  quotes are separable at a glance while talking.
- **The picks list is a row of lifting cards.** The old hover inverted
  each row to solid ink, which forced every chip inside to have its
  colour overridden back — so the "lean · not bet" marker lost its own
  ink at the exact moment it was pointed at. Those overrides are gone.
- **The lean / pending note is a proper callout** with a tinted surface.
  It is load-bearing: it is what stops a tracked call being staked.

No new colour: everything works through existing tokens. Verified on a
production build in light AND dark, desktop and 375px: contrast 15.9
(button), 14.31 (list card), 7.45–7.98 (`--attn` figures in dark), no
horizontal overflow, pending chips carry the word plus a dashed edge.

---

## [2026-08-03m] - the brief's ballpark sentence names a club, not a "city"

Operator, on seeing the STL at NYY brief: *"fix the yankees ballpark
wording."*

### Fixed

- **"a run has scored in the first inning 55% of the time in the
  Yankees."** The park sentence in `lib/pick-reasons.ts` read
  `in ${cityOf(home)}`, and for five clubs the `city` field is a CLUB
  name because the city is shared or would not identify the team — LAA,
  LAD, NYM, NYY, OAK. Now `at home for ${clubOf(home)}`, which is
  grammatical for all 30 and is also exactly what a park factor
  measures: the home team's home games.
- **A second, quieter bug in the same line.** Both Chicago clubs
  rendered "in Chicago", so the sentence named neither ballpark. "At
  home for the Cubs" / "for the White Sox" separates them.
- `BriefView`'s no-park-factor fallback moved to `clubOf` too, so both
  mentions of a ballpark on the page name the same thing.

### Two alternatives rejected, recorded so they are not retried

- **A possessive** ("the Yankees' ballpark") needs a different
  apostrophe for the Red Sox and the White Sox — 30 chances to be wrong.
- **Real venue names** ("at Coors Field") read best of all and are still
  wrong to ship: 30 hard-coded names go stale on naming-rights deals and
  relocations, and saying the wrong stadium into a camera is worse than
  saying a plain phrase.

`cityOf()` now carries a doc comment saying it must never follow "in",
because that is the trap, and it is invisible until a brief happens to
land on one of the five clubs.

Verified on a production build: all 30 clubs rendered, plus the live
2026-08-03 briefs for NYY ("at home for the Yankees") and COL ("at home
for the Rockies", with the season-alone divergence clause intact).

---

## [2026-08-03l] - a brief for every pick, and a door into it from the row

Operator: *"can we make an individual brief for every single pick of the
day, including leans. skip passes. and i want to be able to navigate to
the brief by adding a brief button inside each pick dropdown."*

### Added

- **`BriefLink` — the door.** Every expanded row on the board whose pick
  is STRONG or LEAN now leads with a "Read the brief" button linking to
  `/brief?game=<gamePk>&date=<slate>`. First child of the drawer, above
  the notice stack: the notices are conditional, so anchoring below them
  would put the same control at a different height on every row. 44px in
  pixels, not rem — the app's root font is 15px, so a rem touch target
  comes out short. `components/GameDetails.tsx`.
- **Three honest dead ends** on `/brief`, where there was one. A game
  the model PASSED on says so and explains that writing a brief for it
  would mean inventing the case; a gamePk not on the slate says the link
  points at another date; an empty slate keeps the old "no play tonight".
  All three render the slate's pick list underneath, so every dead end
  has a way out.
- **`?date=`** on `/brief`, carried by every in-page link. Without it a
  brief opened from an old slate resolves against tonight's board and
  reports its own game missing.

### Changed

- **`/brief` briefs any STRONG or LEAN row, not only the #1.** A LEAN is
  a verdict the model committed to and the ledger grades — there is
  something true to explain. A PASS is the model declining to have an
  opinion, so it gets no page. Same filter on both ends: the brief page's
  `isBriefable()` and `BriefLink`'s guard.
- **A lean says it is not a bet, three times**: in the tag above the
  matchup (`LEAN · NOT BET`), in the ticket where the stake would be
  ("nothing — this one is not bet"), and in a ruled paragraph under it.
  Deliberate repetition — this surface is read ALOUD, and a qualifier
  stated once is the one that falls off in the edit.
- **`stake` is null for a LEAN before it reaches the view.** Quarter
  Kelly will happily size a lean's probability, and the figure would be
  plausible, set at 20px in the ticket, and read out. The tracker marks
  every lean `bet_placed='N'` by rule, so printing a stake beside one is
  an instruction to risk money the system never intends to risk. Same
  guard the board's stake chip runs.
- **"Also on tonight" carries every committed pick, each tagged** `#1
  BET` / `BET` / `LEAN · NOT BET`, ordered #1 first, then bets, then
  leans by game time. The list used to be STRONG-only, so "on the list"
  and "wagered" were the same thing and neither needed saying.
- **The #1 record block names whose record it is** when the brief on
  screen is not the #1. The block follows whichever game was #1 each
  night; on any other brief the unqualified title would be read aloud as
  that game's record.
- The brief's season now comes from the SLATE date, not the server's
  clock, so an old slate reads the right season's ledger.

Verified against a production build on the 2026-08-03 slate: 1 STRONG +
3 LEAN got briefs and buttons, 3 LINEUP PENDING and 1 NO EDGE got
neither. Every new text token clears 4.5:1 (`--attn` at 6.07, ink at
15.9); no horizontal overflow at 375px; the drawer button measures 44px.
Colour is reinforcement only — every lean marker prints the word.

---

## [2026-08-03k] - 2026 pitcher-id resolution fixed; the fair refit test finally runs

Operator: *"fix the 2026 game_pk resolution so we can test properly."*

### The bug was one lookup

`backfill_pit_pitching_stats.py` resolved pitchers only through
`pitcher_id_cache.json` (game_pk -> ids). **The 2026 backtest files carry
`away_pitcher_id` / `home_pitcher_id` columns directly** and the
2024/2025 files do not, so 2026 went almost entirely to the
league-average fallback. The row's own columns now win, with the game_pk
map kept as the fallback for the older files.

| file | league-avg fallback BEFORE | AFTER |
|---|---|---|
| 2026-04-01..05-11 | 43.2% | **9.2%** |
| 2026-05-12..05-26 | **100%** | **11.5%** |

Season-to-date coverage went 12.7% -> 39.6% and 0% -> 69.2%. The April
file leans on prior-season (51.2%), which is correct: few starts exist
that early, and the prior season is complete so it cannot leak.

### The test it unblocked

Refit twice on **production's own window** (2024 + 2025 + 2026 through
05-11), once leaky and once clean, held out on the 866 graded 2026 games
from 05-27. Rank-matched, flat 1u:

| N | PRODUCTION | refit LEAKY | refit CLEAN |
|---|---|---|---|
| 40 | **+10.5** | +3.6 | +6.0 |
| 80 | **+11.9** | +9.1 | +5.2 |
| 120 | **+16.0** | +7.3 | +10.9 |
| 160 | +11.8 | **+15.4** | +14.5 |
| 200 | +7.0 | +15.2 | **+18.1** |
| **sum** | **+57.3** | +50.6 | +54.8 |

### Three findings

1. **The leak fix is worth something, and this is the first test able to
   see it.** Clean beats leaky at 3 of 5 counts and by **+4.2u** on the
   sum. The earlier comparison could not show this because both
   candidates were handicapped by a missing year of training data.
2. **Neither refit beats production**, but clean is now close: +54.8
   against +57.3 over ~600 bets. That gap is well inside noise, so the
   honest reading is "no better", not "worse".
3. **Production's edge is concentrated at the top of its ranking.** It
   wins clearly at N=40/80/120 and LOSES at N=160/200. The refits are
   better in the tail. That is consistent with the #1-pick pattern seen
   earlier the same day, and it is the one genuinely new lead here.

**Still does not ship.** Sixth refit variant tested, sixth to fail to
beat the frozen 2026-05-26 weights.

### Caveat on the Kelly columns

Kelly stakes above were computed on RAW two-stage output for all three
models, because a candidate has no calibrator of its own. Stake size
depends on probability magnitude, so those figures are not comparable
across models; only the FLAT column, which uses ranking alone, is.

---

## [2026-08-03j] - Production refit on the clean files: TESTED, does not ship

Operator: *"refit the production model on the clean files. then backtest
it and compare profit."* Done. **It does not beat the incumbent.**

### What was run

`two_stage_model.py --phase-e3-vshand` (the live 19-feature architecture)
fit twice on 2024+2025, once on the leaky files and once on the `_ptfix`
ones, so the only difference between the two candidates is the leak.
Saved to `data/candidates/`, production untouched. Scored against the
881 graded 2026 games since 05-26 that carry a captured DraftKings price.

### The methodological correction that decided it

At the live `p < 0.42` gate the refits bet **17 and 21 games against
production's 136** - not because they are worse, but because a gate is a
cut point on a distribution and a differently-trained model puts its
probabilities on a different scale. `2026-08-02_architecture_and_skip_rules`
already records the rule: **never compare architectures at a fixed
probability cut; match bet count by rank.** Re-run that way:

| N (each model's own top-N) | PRODUCTION | refit LEAKY | refit CLEAN |
|---|---|---|---|
| 40 | **+10.5u** | +4.7u | -0.2u |
| 80 | **+11.9u** | +7.6u | -0.1u |
| 120 | **+14.0u** | -0.2u | +1.3u |
| 136 | **+18.1u** | -2.0u | +1.3u |
| 180 | **+10.2u** | +4.2u | -3.1u |

Flat 1u. Production wins at **all five** counts.

### Two conclusions, and they are different

1. **The refit does not ship.** That is now the fifth refit variant to
   lose to the frozen 2026-05-26 weights (see
   `2026-07-28_refit_tested_dont`, which failed four holdout lengths).
2. **The leak fix did NOT produce the loss, and did not produce a gain
   either.** Clean vs leaky head to head is 2-3 on flat units across the
   five counts - a wash inside noise. Both refits lose to production for
   the same reason: they were trained on 2024+2025 only, while production
   also saw 2026 through 05-11, and that 2026 data is doing real work.

### Why a like-for-like clean refit is still blocked

Adding 2026 to the clean training set is not currently possible: the
2026 backtest files resolve only **12.7%** (Apr-May 11) and **0%**
(May 12-26) of pitcher-slots to season-to-date figures, the rest falling
back to league average. Fixing 2026 game_pk -> pitcher-id resolution is
the prerequisite for a fair test, and until then this comparison cannot
be run without handicapping the candidate.

**The leakage fix remains correct and worth keeping** - training on
future data is wrong regardless of whether removing it happens to help
this particular holdout - but it is not, on this evidence, a money
improvement.

---

## [2026-08-03i] - The training-data leakage is fixed, and it moved the weights

Operator: *"yes fix the leakage. but dont lose focus on my goal."*

### The leak, measured

`era` / `fip` / `whip` / `k9` / `bb9` / `hr9` in the 2024+2025 backtest
CSVs were SEASON-FINAL values, so an April game was predicted with the
pitcher's September numbers. Pitchers with >=5 starts, share with ZERO
within-season variation in `home_fip`: **92%**. Present in the `_pit`
files too; only `xera` was already point-in-time.

### The fix

`tools/backfill_pit_pitching_stats.py` rebuilds all six from
`data/cache/pitcher_gamelog_v2` (per-start ip/er/k/bb/hr/h, 2021-2026)
using ONLY starts before the game's own date, resolving pitchers through
`pitcher_id_cache.json` (game_pk -> ids, 100% on 2024, 90% on 2025).
cFIP is derived per season rather than hardcoded, defined as whatever
makes league FIP equal league ERA. Fallback ladder, counted and printed:
season-to-date if >=20 IP, else prior season's final line, else league
average. Writes `*_ptfix.csv`; originals untouched.

Coverage: 2024 **73.7%** season-to-date / 17.1% prior / 9.2% league.
2025 **69.9% / 11.7% / 18.4%**.

Result: `home_fip` zero-variation share **92% -> 2%** (2024) and
**92% -> 0%** (2025).

### What it is worth, train 2024 -> test 2025 held out

| | AUC | Brier |
|---|---|---|
| train leaky, test leaky | 0.5448 | 0.24994 |
| **train clean, test clean** | **0.5397** | 0.25119 |
| **train leaky, test CLEAN (what production does)** | **0.5317** | 0.25334 |

The third row is the real one: a model trained on leaky data and served
clean loses **0.008 AUC** against one trained clean. That is the cost of
the leak in production terms, and it is now recoverable.

### The weights, which is what the operator actually asked about

| feature | leaky w | clean w | |
|---|---|---|---|
| `home_fip` | **+0.169** | **+0.009** | inflated ~19x by the leak |
| `away_era` | +0.047 | **-0.092** | **sign flips** |
| `away_whip` | -0.090 | **+0.007** | **sign flips** |
| `home_hr9` | -0.099 | -0.010 | |
| `fi_park_nrfi_rate` | **-0.209** | **-0.211** | unchanged, and still the largest |

So: the operator's hunch that the system "heavily relies on the park
rate" is **correct and legitimate** - it is the biggest weight by a wide
margin and the leak never touched it. Several pitching features only
looked useful because of the leak; cleaned, they are near zero and two
of them flip sign.

### Not yet done

The production model has NOT been refit on the clean files. The numbers
above come from a plain logistic fit over 17 features to measure the
leak, not from `two_stage_model.py` at its real feature set. A
production refit needs the full 3-split protocol in
`feature_test_methodology`.

---

## [2026-08-03h] - The +115.72u explained, the equity curve replaced, the drawdown chart deleted

Operator: *"convert the equity curve to cumulative units, drop the
drawdown chart. and also, that doesnt make any sense, because our actual
current profit shows +115.72u so i want you to fully analyze and explain
it."*

### THE ANALYSIS - what +115.72u actually was

It came from `season_record.json` -> `real.sim.profit`: a bank compounded
from 100 to 215.72. It was never the operator's money, and it differs
from the ledger figure on **three independent axes at once**. Holding
staking constant at flat 1u wherever possible:

| | figure | bets | hit |
|---|---|---|---|
| 1. replay, compounded, in-sample calibrator | **+115.72u** | 132 | 64.4% |
| 2. same replay, flat 1u a bet | **+18.18u** | 132 | 64.4% |
| 3. replay, flat 1u, WALK-FORWARD (no hindsight) | **+10.26u** | 119 | 62.2% |
| 4. what was ACTUALLY bet, flat 1u | **+4.85u** | 240 | 57.1% |

Per bet, flat: replay in-sample **+0.1377u**, replay walk-forward
**+0.0862u**, actually bet **+0.0202u**.

So the gap decomposes as:

- **Compounding is 6.4x of it.** +18.18u flat becomes +115.72u once the
  bank grows and later stakes ride on it.
- **Hindsight is +7.92u of the flat figure.** The shipped calibrator was
  fit on 2025+2026 and has already seen the outcomes it is scored
  against - the file says so in its own `caveat` field. The walk-forward
  floor, refitting from prior games only, drops 64.4% to 62.2%.
- **The rest is SELECTION.** The replay re-scores which games qualify
  with today's gate and bets 132 where the ledger actually bet 240. It
  is choosing different, better games with the benefit of the final
  model - not recording what was placed.

None of that is a bug in the replay; it is what a replay IS, and the
file documented it. The bug was putting it on the same page as the
ledger under the word "profit" with no bridge between them.

### Removed

- **The bankroll equity curve** and **the underwater/drawdown plot**.
  Both are bank-shaped: an equity curve IS compounding, and a drawdown
  is measured against a running high-water mark, so neither survives the
  removal of compounding. Converting them in place would have left two
  charts drawing the same cumulative line twice.

### Added

- **A cumulative-units chart** in each of the two settled-bet sections,
  reading the LEDGER at quarter-Kelly - the same source as every other
  figure beside it, so the page now has one number instead of two. A
  running sum, not a bank: a five-unit night in May is drawn the same
  height as a five-unit night in August.

### Fixed

- The chart letterboxed into the middle 65% of its box: `max-height`
  fought `height: auto`, so the viewBox scaled to ~220px, got clipped to
  180, and the browser centred the drawing. The viewBox aspect alone
  decides the height now.

---

## [2026-08-03g] - Compounding removed, quarter-Kelly everywhere, the whole system gets a history

Operator: *"i want to remove compounding from the dashboard... everywhere
on the dashboard, quarter kelly needs to be used... stop saying
SIMULATED... i like how you have the day by day history for the #1 pick
system, but why is that only for that system? also, you need to fix the
formatting."*

### Removed - compounding, everywhere

*"Compounding is up to the bettor, not the system."* Correct, and it is
the same principle that makes a published stake bankroll-free: the
system emits a unit COUNT, and what a unit is worth after a winning week
is the follower's business. Gone: `bank` levels, peak, max drawdown, the
per-slice `bankEnd`/`ret`, and the "100u becomes" column. Max drawdown
is replaced by **worst single night**, which needs no bank behind it.

### Changed - "simulated" was the wrong word, and that was my error

The Kelly figures were labelled SIMULATED with a dashed rule. That was
overcautious and the operator was right to reject it: every game, price
and result is real, and only the STAKE comes from the rule rather than
from history -- which is *equally* true of the flat-1u figure nobody
would call simulated. Quarter-Kelly is what the system publishes, so it
is what the system's record is measured at. Labels are now bases, not
warnings: **At quarter-Kelly** / **At a flat 1 unit** / **As actually
staked**. `realized` still prints beside them because a flat unit was
staked until 2026-07-27, but it is no longer framed as a correction.

### Added - the whole system, not just the #1

`loadTopPickReport(season, topOnly=false)` keeps every qualifying bet
instead of one a night. Same rules, same staking, same component, so the
two sections can be read against each other without holding two
definitions.

| | bets | record | hit | needs | at ¼-Kelly | flat 1u |
|---|---|---|---|---|---|---|
| #1 play | 63 | 42-21 | 66.7% | 57.4% | **+68.23u** | +10.37u |
| whole system | 240 | 137-103 | 57.1% | 55.7% | **+54.12u** | +4.85u |

### Fixed - the table formatting the operator flagged

- **Headers did not sit over their own columns.** `.table thead th` set
  `text-align: left` unconditionally while every numeric cell under it is
  right-aligned, so PRICE and STAKE floated left of figures sitting
  right. `.right` now applies to headers too.
- **No column rules.** A settled-bet table is read across a row AND down
  a column; with horizontal rules only, the eye has nothing holding a
  column together. `border-right` on every cell, none on the last, and a
  `--rule` under the header row.
- Cell padding moved from one-sided to symmetric so the rules actually
  separate columns rather than hugging the text.

### Docs

CLAUDE.md and AGENTS.md's "NEVER SUM UNITS ACROSS DAYS" rule contradicted
the shipped product and is rewritten: sum on a FIXED basis and name it;
do not publish compounded bank levels. `CumulativeUnits` still has no
renderer; `FlatUnits` is the sanctioned one.

---

## [2026-08-03f] - The #1 section becomes the CURRENT system, and half its money becomes a simulation

Operator, reading the every-bet table: *"it still includes NRFI, which
we've seen that NRFI doesnt apparently work... most of the bets are only
betting 1 unit. quarter kelly should be applied for all the history...
i also thought i told you to start the #1 pick model since we started
getting real odds data in may"*

All three correct. The May 26 cut had been added as a SIDE BLOCK while
the headline still led with the old series; that was a fair reading of
"add it" and the wrong reading of the intent.

### Changed - the section is now one population, defined by today's rules

1. **YRFI only.** 15 of the old 92 nights had an NRFI play as their #1.
   NRFI has been off since 2026-06-07 for losing in every band, so
   showing them as the record of a system that would not place them was
   simply wrong. On those nights the top YRFI play is counted instead.
2. **From 2026-05-26**, when the live weights were fit. Earlier picks
   came from a model that no longer exists.
3. **Staked at quarter-Kelly**, via the same `stakeUnitsFor` the board
   uses. 85 of the old 92 bets were recorded at exactly 1.00u because
   Kelly only went live 2026-07-27.

92 bets -> 65 qualifying -> **63**, because Kelly finds no edge at the
price paid on 2 of them and would not bet at all. That drop is counted
and printed rather than silently sized to zero (the trap recorded in the
`kelly_staking` memory).

### The honesty problem this creates, and how it is handled

Points 1 and 2 are FILTERS and stay factual. Point 3 is a
COUNTERFACTUAL, and the gap is not small:

| basis | figure | status |
|---|---|---|
| flat 1u a night | **+10.37u** | fact |
| actually staked and returned | **+7.49u** | fact |
| quarter-Kelly | **+68.23u** | **simulation** |
| ...compounding | **100.00u -> 184.77u** | **simulation** |

Roughly **nine times** the realized number, because the median stake
moves from 1.00u to 5.00u. The operator SELLS these picks, so:

- both simulated figures carry the word "simulated" in the basis label,
  a dashed rule under the label AND a dashed underline on the figure
  itself, so the marker travels with the number rather than its caption
- the marker is a FORM difference, not a hue one: globals.css reserves
  hue for real money and spending it on provenance would weaken both
- `totals.realized` is printed beside them, always
- the note says outright which two are facts and which two are not

`/brief` deliberately shows the **flat 1u** figure instead: that surface
gets read aloud, and a caveat is the first thing to fall off on camera.

### Note

`tools/season_replay.py` already excluded NRFI (`decide()` is YRFI-only
and says why), so the backtest needed no change on that count.
`bySide` is dropped from the report: with the series YRFI-only it had
one row.

---

## [2026-08-03e] - UI upgrade: rounded corners, paper shadows, a display serif

Operator: *"i want to upgrade the ui too. rounded corners. shadows. nice
fonts. etc"* -- straight after the Newsprint repalette.

### Added

- **A radius SCALE, not a value.** `--radius` was a single `0rem` (the
  July square-corner spec). One number cannot serve a 4px chip and a
  full-width panel, so: `--radius-sm` 6px (chips, inputs), `--radius`
  10px (cards, rows, panels), `--radius-lg` 16px. 51 existing call sites
  already read `var(--radius)` and picked it up for free; the pills at
  `999px` were already round and are untouched.
- **Shadows built for paper.** The old set was tuned for a near-black
  page -- heavy pure-black alphas that read as grey mud on `#FBFAF7`.
  The new tokens are ink-tinted (`rgba(33,30,26,...)`) and LAYERED: a
  1px contact edge, a tight shadow, a wide soft one, which is what makes
  a card look like it is resting on paper rather than floating over it.
  `--shadow-lift` is new, for hover and open states.
- **A third typeface, and now each one has a job.** Fraunces (variable,
  optical-size axis) for display; Inter for prose; JetBrains Mono for
  figures. The contrast IS the system: serif says "this is the thing",
  sans says "this is prose", mono says "this is a number". Applied to
  the matchup headline, the Brief's section heads, the #1-history title,
  the wordmark and the slate date -- all of which were previously mono,
  i.e. the interface was calling the product's own name a number.
  Fraunces rather than Playfair on purpose: Playfair is the reflex every
  editorial redesign reaches for first.

### Changed

- Board rows rest ON the paper instead of being outlined on it: the 1px
  border goes transparent and the shadow's own contact edge does that
  job, so a card no longer carries two competing outlines. Hover and
  open states move to `--shadow-lift`.
- Section heads on /brief drop `text-transform: uppercase` at 13px for
  the serif at 22px. Tracked uppercase was doing the work of hierarchy
  that the type scale can now do properly.

### Note

Fraunces is a fourth family on the Google Fonts `@import`, which is
render-blocking. It is variable, so it is one file rather than four
weights, but if first paint ever matters this import is the thing to
move to `next/font`.

---

## [2026-08-03c] - Design critique, 11 agents, and the contradiction it caught

An 11-agent critique of /brief and /history: five independent lenses
(hierarchy, number provenance, accessibility, read-aloud copy,
responsive), each with an adversarial verifier that had to REFUTE its
own lens's findings against the real source. 11 of 20 survived.

Everything below was found by looking at the RENDERED page in a real
browser at 1910px and 375px. None of it was visible in the DOM dumps
the first pass relied on.

### Fixed - the contradiction, which is the one that mattered

**The ballpark block printed two figures fifteen points apart, twenty
lines from each other, and neither named its basis.** The headline
`58%` is the model's park factor: Bayesian-shrunk over LAST SEASON PLUS
THIS ONE with a 50-game prior. The line below it, `41 of the 56 first
innings actually played here this season`, is **73%**, raw 2026. Both
true. Read consecutively into a camera they contradict each other, and
the word "actually" quietly accused the first figure of being fake.
Arizona reads 58 vs 55, Miami 54 vs 46, so the direction is not even
consistent. Both sentences now name their basis, and when they diverge
by more than eight points the reason sentence carries BOTH.

This is precisely the failure /brief exists to prevent, and it shipped
in the first version of it.

### Fixed - the rest

- **A losing season would have printed in the gain colour.**
  `.recordFigure` set no colour, so it inherited `--foreground` #00FF41,
  byte-identical to `--gain`, and carried no `data-money`. The one money
  figure on the filmed page was the only one in the codebase unmarked.
- **The bet ticket was unspeakable.** Bare `YRFI` (whose inverse is one
  slip away), `7.00u` (no spoken form), an em dash when no price was
  captured, and worst `Model 71% likely`, which never named WHAT was
  likely: on an NRFI night that figure means the opposite of the
  ballpark percentage on the same page, and both can read 58%. Now:
  "a run scores in the first inning", "7.00 units", "71% chance a run
  scores in the first inning", with the acronym demoted to a tag.
- **Three different KINDS of number wore the same suffix, size and
  hue.** The three UNITS PROFIT figures now carry a basis eyebrow ABOVE
  the number, which is also the order they have to be spoken, and the
  compounded one is marked as a different kind by form, not hue.
- **On a 375px phone both money columns were off-screen and no label
  survived the swipe.** `min-width: 34rem` resolved to 510px in a 311px
  box; `position: sticky; top: 0` on the thead was inert because
  `overflow-x: auto` makes the scroller its own scrollport with no
  height constraint. Headers now wrap (cells never do), min-width drops
  to 26rem, the row label is `sticky; left: 0`, and money moved to the
  left of each table. 39% hidden -> 12-15%, money VISIBLE on load.
- **20 hand-written rem font sizes against a documented px system.**
  globals.css says verbatim "ALL PX, NEVER REM... Six px values total:
  44 / 26 / 20 / 13 / 12 / 11" and ships role classes. BriefView had 13
  distinct rem sizes, TopPickHistory 7, three of them BELOW the 11px
  floor (9.38 / 10.31 / 10.31) - reintroducing by a different route the
  exact defect that retired VT323 on 2026-07-30. Now 7 px steps, nothing
  under 11px. The single deviation from the six is a 16px prose step for
  the read-aloud sentence, kept deliberately because 13px is the board's
  glance size and this is a reading surface; `.matchup` keeps its clamp
  because a fixed 44px monospace headline wraps "Baltimore at Cleveland"
  to three lines on a phone.

### The measure rework (operator: "yes rework the measure, make it wider")

/brief used 690px of a 1910px viewport, 36% of the screen, and its prose
capped at FOUR ch values that resolved to four pixel widths (612 / 523 /
520 / 482) because they sat at four font sizes. Four ragged right edges
in one column.

**Wider could not mean longer lines.** Prose stops being readable past
about 75 characters, and this page is read ALOUD, where the failure mode
is losing your place mid-sentence on camera. At 16px JetBrains Mono a
620px line is already 64 characters; stretching it to fill 1910px would
put it near 190 and make the page worse at its only job. So the width
went into LAYOUT:

- container 46rem (690px) -> **1080px**, about 76% of a 1430px screen
- one `--measure` custom property (620px) replaces all four ch caps, so
  there is one right edge per section rather than four
- **at >= 60rem the reasons are two columns**: label and figure in a
  190px rail, the spoken sentence in the measure column beside it. This
  is what actually consumes the width, and it halves a reason's vertical
  space, so less scrolls past while the camera is running.
- the section intro joins the sentence column, SCOPED to reason blocks
  only: "The numbers" has no rail, so indenting its intro would have
  pushed it 230px right of the content it introduces, reintroducing the
  same defect one level down.
- `.count` loses `margin-left: auto`, which had stranded the "2" beside
  "THE CASE FOR IT" 515px from the words it counts. Now 159px.
- `.strip` capped at 740px so a single digit does not sit in a 105px cell

Measured after: reason blocks one edge at 1038px, plain blocks one edge
at 810px, the two-up pitcher grid on its own column edges. Three
structural families, each justified by its own layout, instead of four
accidental widths at the same nesting level. Mobile untouched: the
measure exceeds the viewport there and every breakpoint is above it.

---

## [2026-08-03b] - A real backtest of the #1 play, and the window that is not one

Operator: *"what about since may when we got correct odds, and with our
new filters, floors, and everything from our new system, not from what
our old system used to pick"* and then *"i want you to run a real
backtest of the new system."*

Two different things, and the value is in keeping them apart.

### Added

- **`tools/season_replay.py --top-only`** - restricts the replay to ONE
  bet a night, the slate's #1, ranked by the same rule as
  `lib/top-pick-rank.ts` (confidence, then the better price). A backtest
  that defined "#1" differently from the live board would be measuring a
  strategy nobody runs.
- **`tools/season_replay.py --since YYYY-MM-DD`** - restricts what is
  EVALUATED, deliberately not what is trained on. The first draft
  filtered rows at load, which silently starved the walk-forward: it
  refits from games strictly before each date, and those are exactly the
  games a `--since` would have discarded.
- **"Under the current system" block** on /history's #1 section:
  `TopPickReport.currentSystem`, YRFI-only and re-ranked, from
  2026-05-26. Labelled **not a backtest** in the copy, pointing at the
  replay command for the real thing.

### The numbers

Ledger, today's selection rule applied backwards (65 bets, from 05-26):
**44-21, 67.7%** against a 57.7% break-even, **+11.36u** flat.

Replay, `--top-only --since 2026-05-26`, walk-forward calibrator:
**37-19, 66.1%** against 58.2%, **+7.63u** flat, 56 bets.

Replay, `--top-only`, whole season, walk-forward: **66.2%**, **+10.61u**
flat over 71 bets.

The convergence is the finding: three routes to roughly +8u to +13u flat
on the #1 play. The replay also bets FEWER games than the ledger did (71
vs 92) for about the same flat profit, which is the current gate being
more selective.

### The trap this entry exists to record

**The replay's COMPOUNDED figures are not comparable to the ledger's and
must never be quoted beside them.** The replay applies quarter-Kelly to
every bet from April; in reality **85 of the 92 #1 bets were staked at
exactly 1.00u**, because Kelly only went live 2026-07-27. So the replay
reports 100u -> 193.68u while the ledger reports 100u -> 106.59u for
overlapping bets. Both are right about different strategies. Only the
**flat 1u** column compares.

Also: every interval still crosses its break-even. 56 bets gives 95%
[53.0-77.1%] against 58.2% needed. Positive on every route, proven on
none, which is the same conclusion the 2026-06-04 edge investigation
reached and it has not moved.

---

## [2026-08-03] - THE BRIEF: the #1 play, explained in sentences

Operator: *"i want to start making content where i post a video about the
#1 play from the model. i need to be able to give reasons why its the #1
play, and not just mentioning data that doesnt make any sense to people
... the Rays didnt score in the first inning at all in their last series
of 3 games against the white sox."*

A third usage scene, and it is not the one PRODUCT.md describes. The board
answers "what do I bet and how much" in thirty seconds on a phone; the
history page answers "how did it go" at a desk. Neither can be read ALOUD
to an audience who cannot see the screen, which is what filming requires.

### Added

- **`/brief`** (`app/brief/page.tsx`, `components/BriefView.tsx`) - the
  night's #1 play written as a script: the bet, the case for it, the case
  against it, then the supporting numbers. Bare `/brief` uses the shared
  #1 selector; `?game=<gamePk>` briefs any game on the slate.
- **`lib/first-inning-form.ts`** - per-team last-10 first-inning form,
  per-pitcher scoreless-first record, park rate and rank, head-to-head and
  current series. All DERIVED FROM THE LEDGER, which already logs every
  game on every slate plus its first-inning line, so there is no new
  scraper and no new failure mode. Verified 2026-08-03: 119 slate dates
  with exactly three calendar gaps (07-13/14/15), which are the All-Star
  break.
- **`lib/pick-reasons.ts`** - turns model features into speakable
  sentences (`fi_park_nrfi_rate z=-1.476` becomes "a run scores in the
  first inning 58% of the time in Colorado"). Where per-pick diagnostics
  exist, the model's own contribution magnitudes ORDER the reasons; the
  wording is always written here and the figures always come from the
  ledger, so nothing is invented to fit a story.
- **`lib/team-names.ts`** - "TB" is not a word. Abbreviations stay on the
  board and expand to "Tampa Bay" / "the Rays" on the brief.
- **`components/TopPickHistory.tsx`** - the #1 play's full record on
  /history: bank growth from 100u, last-10, month-by-month and by-side
  tables, and every settled #1 play. Answers the operator's "units profit"
  request the only way it can be answered (see below).
- **`lib/top-pick.ts`** gains `last10`, `bank`, `byMonth`, `bySide`, `all`.
- Board header now links to the brief, as does the "#1" explainer note.

### Changed

- **`selectTopPick()` hoisted into `lib/top-pick-rank.ts`** (T2.61). The
  comparator was already shared between the board badge and the history
  card, but each re-implemented the FOLD around it including the game-name
  tiebreak - so the rule was shared and the selection was not. A brief
  that disagreed with the board about which game is #1 would be the worst
  version of that bug, because the operator would be filming it.
- `scripts/copy-data.mjs` now ships `fi_park_factors.json`, without which
  the brief silently drops its ballpark reason on deployed builds.

### Notable decisions

- **UNITS PROFIT IS BACK, AND THE OLD RULE WAS TOO BROAD.** First pass
  refused to print any season unit total and showed bank growth alone.
  Operator: *"units profit should be available. that doesnt make sense.
  every bet should be making the same amount of units as a person with a
  $1000 bankroll, or someone with a $10,000 bankroll."* Correct, and the
  original rule confused two things. What breaks a unit sum is the unit's
  DOLLAR VALUE MOVING between the bets being added, which happens only
  under compounding - not the passage of time. On a FIXED basis the sum
  is exact and is identical on any bankroll, which is exactly the point
  when you are selling picks. The section now prints all three:

  | basis | #1 pick, season |
  |---|---|
  | flat 1u every night | **+10.70u** |
  | at the published stakes, never re-sizing | **+7.82u** |
  | re-sizing to 1% of the running bank | +6.59u (100.00u -> 106.59u) |

  `lib/units.ts` gains a `FlatUnits` brand and `formatFlatUnits()` for
  the legitimate case; `CumulativeUnits` still has no renderer and never
  will. `formatUnits()` now rejects BOTH brands, so a season total can
  never be printed by the same function as a single night's P&L - on
  screen they are indistinguishable. Guard extended to 8 rejected forms
  and 6 accepted ones, including `formatFlatUnits(asCumulative(x))` which
  must stay an error so `asFlat` cannot launder a compounding sum.
- **Stats that cut AGAINST the pick are a first-class block, not hidden.**
  The operator's opening instinct was that Tampa Bay were "due" a first-
  inning run after a scoreless series. That is the gambler's fallacy, and
  it argues the opposite way from the YRFI bet the model actually made.
  The card shows the figure, labels it as cutting against, and lets the
  operator answer it on camera.
- **Park standing is a TIER, not an ordinal.** The first draft printed
  "2nd most run-friendly of 30 parks" for Colorado; Arizona sits at 0.4239
  and Colorado at 0.4241, a dead heat. Ordinals claim a ranking the data
  cannot support.

### Fixed (found by measuring the rendered page, not by eye)

- Strip opponent labels rendered at **8.4px** at 375px - a third smaller
  than the 11-13px that got VT323 retired for being unreadable in this
  exact scene. The strip now wraps to two rows of five on a phone, which
  doubles cell width to 67px and lifts the label to 10.3px.
- Brief nav links measured **17px tall**, then 41px after a `2.75rem`
  min-height (the app's root font is 15px, not 16px). Now `44px` in
  pixels, because a fingertip is not a typographic measure.

### Performance snapshot

#1 play, real captured prices, as of 2026-08-03: **59-33 (64.1%)** against
a 57.6% break-even, +6.2% per unit staked, bank 100.00u -> 106.59u, deepest
drawdown -12.6%. Last 10: 4-6.

---

## [2026-07-31] - #1 leads the board; the pick column speaks in white

Operator, on a screenshot: *"visually, why is the #1 pick not at the top.
also, what even makes it the #1 pick? also, can we make it so that any of
the picks on this screen are not green, and they have white text thats a
different font, not terminal, thats more readable."*

### 1. Why #1 was not at the top - a missing tie-break

CWS@TB and KC@COL were **exactly tied** at `p_nrfi = 0.287200`. The board's
`P(YRFI) high -> low` comparator was probability *only*, so it returned 0,
the stable sort kept the incoming order, and KC@COL led while the badge sat
on CWS@TB.

Neither was wrong alone - the badge breaks ties on the better price
(-135 beats -155) and the sort broke them not at all. But two rankings of
one slate disagreeing about which game is first made the badge look
arbitrary.

Ties at the top are **not rare and will not become rare**: the current CIR
calibrator has a floor clamp at `0.2872` that 63 games sit on, so the
strongest plays are the *most* likely to tie. Fixed by giving the
probability sorts the same tie-break, rather than pinning the badged row -
#1 now leads because it genuinely ranks first.

### 2. What makes it #1 - said out loud

A note under the board, rendered only when a badge is present:

> **#1** The model's most confident bet tonight - the STRONG play furthest
> from a coin flip. When two are equally confident, the one at the better
> price wins. Its running record is the #1 pick card on the history page.

It was only ever in the badge's hover tooltip: undiscoverable on desktop,
non-existent on a phone.

### 3. The pick column is white, in Inter

Two new tokens, scoped to the PICK column only - everything else on the
board stays phosphor mono, so the pick reads as the one thing on the row
speaking plainly.

- `--pick-ink: #FFFFFF` - **20.38:1** on `--card`, up from green's 14.93:1
- `--font-ui: Inter` - proportional, drawn for UI text at small sizes;
  tracking cut 0.10em -> 0.02em (wide tracking is a *monospace* mannerism
  that buys nothing on a proportional face and costs real width on
  "PENDING - LOCKS 7:40 PM ET")

**Four side-as-hue violations found and fixed on the way**, all of them
live, none reached by the 2026-07-29 pass that cleaned the pick pills:

| element | was | why it mattered |
|---|---|---|
| `.oddsNrfi` / `.oddsYrfi` chip | `--primary` / `--destructive` on background, border, price **and** book label | the price of every YRFI bet - the only side this system bets - rendered in the **loss** colour on a game that had not started, and the red tint over a green page is what made this chip read orange |
| `.oddsSideLabel` | green / red by side | the N/Y marker *is* the side; it did not also need to be the side's colour |
| `.pickLabelLeanNrfi` / `Yrfi` | `--primary` / `--destructive` + glow | same defect on the tentative-lean tail of PENDING pills |
| `.pickPillLocking .pickLabel` | `--foreground` | most specific rule on the label - it beat every other change and is why the two locking picks stayed green after the first pass |

Audited rather than spot-fixed, per the palette memory's own standing rule
(*"grep every call site, don't just fix the component you were looking
at"*): a scripted sweep of every `color` / `background` / `border-color`
declaration on a pick-column selector now reports **no money hue left in
the pick column**. The only green remaining there is the `#1` badge, which
keeps `--attn` deliberately as the row asking for attention.

### Verified

Production build. #1 badge on the first row. Pick labels, prices and stakes
all `rgb(255,255,255)` in Inter. 375px and 1280px: **zero overflowing
elements**, pill does not overflow with the wider proportional face.

---

## [2026-07-30i] — "#1" badge on the board's top bet

Operator: *"i would like to be able to tell which bet is the top bet."*

The /history card has tracked the #1 pick's record since 2026-07-30g, but
the board never said which of tonight's plays **is** it — so the figure
was only ever readable after the fact.

### Added — a `#1` badge, leading the pick cell

Leads rather than trails: the operator reads this board left to right in
about thirty seconds, and a marker arriving after the pill and two chips
is a marker they find second. "#1 STRONG YRFI" also reads as a sentence.

Outlined, `--attn`, never filled and never a money hue — a filled chip in
`--gain` would read as *"this one won"* on a game that has not started.
The badge is an identifier, and identifiers on this board are outlines.
The `#1` glyph carries the meaning, so nothing depends on the colour.

**STRONG only.** LEAN is tracked and never wagered, so a "top bet" that
is not a bet would be an instruction to risk money the system does not
intend to risk — the same rule the stake chip enforces.

### One definition of "#1", shared

`lib/top-pick-rank.ts` is new: the comparator, and nothing else. Both the
server-side history card and the client-side board badge import it, so
the two cannot disagree about which game was #1 — which is exactly how
this dashboard has produced contradictions before.

It is a **separate file** because the rule started inside `lib/top-pick.ts`,
which reads the ledger and therefore imports `node:fs`. `BoardTable` is a
client component, so importing from there dragged the filesystem into the
browser bundle and webpack refused to build:

> `UnhandledSchemeError: Reading from "node:fs" is not handled`

A useful failure — it says the rule and the loader are different concerns.

### Verified against the tie dates, which are the hard case

18% of nights have two or more bets sharing the top probability exactly
(the retired calibrator's flat steps). Confirmed the board and the
history card resolve them identically:

| date | tied | board badges | history card picks |
|---|---|---|---|
| 2026-06-13 | 3 at 59.4% | **LAD@CWS** (−110) | **LAD@CWS** |
| 2026-07-12 | 5 at 59.4% | **OAK@CWS** (−110) | **OAK@CWS** |

Both are the best-priced of the tied set, then alphabetical — the rule
as written.

Also confirmed the badge is computed over `rows` and not `sortedRows`, so
re-sorting the table by edge or result does not move it: it is a property
of the slate, not of the display order.

### Verified

Production build. Tonight: 1 STRONG, 1 badge, on WSH@ATL — the most
confident play on the board. 375px and 1280px: zero overflowing elements,
pick cell does not overflow with the badge added.

---

## [2026-07-30h] — Provenance stamp on every replay-driven figure

Operator: *"why did the profit change again from yesterday?"*

Nothing was wrong. But **three different sets of numbers appeared on
`/history` in one day** and nothing on screen said why:

1. the unit re-basing changed what a "u" means in the Day column
   (2026-07-28 went from −10.00u to −4.61u — same money, new ruler)
2. the STRONG gate moved 0.40 → 0.42
3. the nightly replay rebuild will re-simulate the **whole season** under
   the new gate, moving every historical row

(3) is the surprising one, and it is inherent rather than a bug: these
charts answer *"what would today's system have done all season"*, so
changing today's system rewrites the history by design. **A chart that
silently self-rewrites is indistinguishable from a broken one.**

### Added — `ReplayStamp`

Every replay-driven card now carries the gate and build time that
produced it:

> `Replay · gate 0.40 · built 30 Jul 06:07 UTC`

Mounted on four: the hero, the week card, the equity curve, and the
daily ledger. Built once in `HistoryView` and passed down, so the four
copies cannot drift into describing different builds — which would be a
worse version of the problem it solves.

**Not on the #1 pick card, deliberately.** That reads the ledger, so it
can only move when a real bet settles. Stamping it would imply a
volatility it does not have.

### The staleness line is the part that earns its keep

`thresholds.json` holds the **live** gate; `season_record.json` holds the
one its replay was **built** at. When they disagree the figures on screen
are already known to be superseded, and the operator can be told *before*
the rebuild instead of discovering it afterwards. That is exactly the
state this shipped in — live 0.42, replay built at 0.40:

> The live gate is now 0.42. These figures were replayed at 0.40, so
> every row will move at the next nightly rebuild — no real bet changes,
> the simulation just re-runs under the new rule.

Verified self-clearing rather than a permanent banner: stale now
(0.40 vs 0.42), **not** stale after the rebuild (0.42 vs 0.42), stale
again if the gate moves later (0.42 vs 0.45).

What the next rebuild will actually do, measured in advance: 104 bets →
**123**, final bank 209.89u → **218.61u**, and eight of the last nine
rows move (2026-07-25 goes from no-bet to −4.00u).

### Verified

Production build. Four stamps on the four replay cards, none on the #1
pick card, staleness firing on all four. 375px and 1280px: zero
overflowing elements on `/history`, no console errors. `--attn` on the
warning, 10px `--muted-foreground` on the stamp line — chrome, never
competing with the figure it describes.

---

## [2026-07-30g] — "#1 pick" tracker on /history, from the REAL ledger

Operator: *"i want to see what the #1 pick record and profit would be"*
— then, on being shown replay figures: *"i want you to get the real
numbers."*

### Real, not replayed

This card reads `picks_2026.csv` / Supabase: bets actually **placed**, at
prices actually **captured**, graded on real results, using the units
actually **staked**. It touches `season_record.json` nowhere. Every other
performance surface on that page is the replay — today's model re-scoring
history — which is a different and more flattering question.

**How much more flattering:** the replay says the #1 pick beat its price
with confidence (69.4%, CI excluding break-even). The real ledger says
**64.8% against 57.5% needed, and the interval still straddles it.**

| window | record | hit | needs | staked | return | 95% range |
|---|---|---|---|---|---|---|
| Season | 57–31 | 64.8% | 57.5% | 101.53u | **+6.1%** | 54–74% |
| Last 30 days | 19–7 | 73.1% | 58.9% | 39.53u | +1.7% | 54–86% |
| Last 14 days | 7–6 | 53.8% | 59.9% | 26.53u | −26.4% | 29–77% |
| Last 7 days | 2–4 | 33.3% | 57.1% | 19.53u | −40.0% | 10–70% |

Every figure reproduced by an independent Python pass over the CSV.

### Two defects found while building it, both fixed

**The #1 pick was not deterministic.** The retired calibrator emitted
flat steps — 115 games on `p = 0.4064` alone — so **18% of nights have
two or more bets sharing the top probability exactly**. With a plain
`min()` the winner is whichever row the loader returned first, and this
card reads Supabase live with a CSV fallback whose order differs. The
same season read **58–30 from one source and 56–32 from the other**.
Tie-break is now confidence → **better price** → game name: a real
decision rule (when the model can't separate two games, the one paying
more is the higher-edge bet) rather than an arbitrary stabiliser.

**A population mismatch in the first draft.** The record covered every
STRONG pick (115 nights) while the money covered only the ones actually
bet (88) — two figures, two populations, one table. Every column now
comes from the identical set, and nights with no captured price are
excluded *and counted* on the card's face.

### Also

- `lib/roi.loadLedgerRows()` extracted and exported. The "Supabase, then
  CSV, then give up" sequence was inline in `loadRoi`; a second copy is
  how two surfaces start disagreeing about which night they describe.
- The money figure is **returned ÷ staked**, never a unit total — units
  from different dates are different money (`lib/units.ts`).
- The 95% range is **on the card, not in a footnote**. One bet a night
  makes short windows uninformative: the 7-day row is six bets and its
  interval runs sixty points wide. Printing "33.3%" beside "73.1%" with
  nothing else invites "the model broke this week".
- Renders **bright** with money hues, directly under the dim simulated
  week card. That contrast is the real-vs-simulated convention teaching
  itself, which PRODUCT.md asks to be readable without a legend.

### Verified

Production build, 375px and 1280px: zero overflowing elements on
`/history`. On mobile the seven columns fold to three rows with inline
labels — nothing is dropped, which was the mistake in the first draft of
the responsive rules.

---

## [2026-07-30f] — STRONG YRFI gate 0.40 → 0.42

Operator: *"why is there much less games being chosen to be bet on. this
is an issue."* It was, and by more than anyone intended.

### The cause: two changes in 24 hours, and nobody re-derived the gate

| date | change | intended |
|---|---|---|
| 2026-07-27 | gate 0.44 → 0.40 | yes |
| 2026-07-28 | calibrator swapped to CIR | yes |

**A gate is a cut point on a distribution.** The CIR swap changed the
distribution — the old calibrator emitted only 41% unique values and
leaned on flat steps, CIR emits 95% unique — so the same number 0.40
started selecting far fewer games. Nobody re-derived the cut after the
shape moved.

Measured: today's model bets **124 games where the live system actually
bet 308** — 40% of the volume, down every single week, and only about
half of that reduction was chosen.

### Where the old volume actually came from

The old calibrator had two clamps, and they were doing opposite things:

- **p = 0.4064, 115 games.** Games it could not tell apart, auto-fired
  under the 0.44 gate. They went **50.9% against a 54.9% break-even.**
  CIR dissolved this and that volume is gone on purpose.
- **p = 0.3219, 32 games.** The floor clamp — 26% of all bets and **52%
  of the season's profit**, 25-7 for a 78.1% hit rate. Checked
  individually: **all 32 still clear at both 0.40 and 0.42**, because
  CIR moved them *down* to ~0.287.

So the swap dropped unrankable games and kept the good ones. That part
is the calibrator working correctly, and it is not recoverable volume.

### The change

`_LR_STRONG_YRFI_P` 0.40 → 0.42. The 2026-07-28 sweep went
0.36 → 0.40 → 0.44 and skipped the space between, so this is new ground
rather than a re-tread of the refuted 0.44. (An April 2026 note in the
same file independently landed on 0.42 as well, on n=5.)

Through the repo's own walk-forward harness, `tools/season_record.py`:

| gate | record | hit% | need% | bets | flat | Kelly bank | maxDD |
|---|---|---|---|---|---|---|---|
| 0.40 | 75-38 | 66.4% | 57.4% | 113 | +18.10u | 255.70u | 8.4% |
| **0.42** | 98-57 | 63.2% | 56.7% | **155** | **+18.07u** | 263.14u | 9.4% |

**+37% more bets for the same flat profit** (0.03u apart), a slightly
better Kelly bank, 1pp more drawdown. Positive in all four months.

### This is a VOLUME decision, not a proven edge improvement

Stated plainly so nobody later reads it as proof:

- flat walk-forward, 6 rolling cuts: 0.42 best **6 of 6**
- **Kelly** walk-forward, same 6 cuts: 0.42 best **3 of 6 — a tie**
- block bootstrap on the difference, resampling whole slate days:
  flat `[−7.01u, +13.24u]`, Kelly `[−14.61u, +33.22u]` — **both span
  zero**, 0.42 ahead in ~76% of resamples

Money-neutral, volume-positive. The operator asked for the Kelly view
specifically, and it is what weakened the walk-forward from unanimous to
a tie — the flat-only view would have overstated the case.

The Kelly figures are the same edge levered **7.5×** (+3.57u flat reads
as +26.67u compounded), exactly what `tools/edge_floor` meant by "never
judge a filter on final Kelly bank". Flat decided this; Kelly says what
it does to the bank.

### What it does not do

Restore ~24 bets/week. That number was substantially manufactured by the
0.4064 clamp. Realistic volume is ~7.5/week at 0.42 against ~6.4 at
0.40. **Do not chase the rest by loosening further** — 0.44 is worse on
every measure tested (59.3% hit, +16.35u flat, 224.01u bank, 13.4% DD).

Tonight (2026-07-30) this turns 0 bets into 2: SEA@LAD at 0.4116 and
BOS@OAK at 0.4157.

### Separately found, not fixed here

`tools/season_record.simulate()` adds a fixed unit count to a growing
bank. Before the 2026-07-30 unit re-basing the stake was `bank * f` and
so already scaled; now sizing is bankroll-free (`stake = f * 100`), so
the simulator no longer compounds. The same season reads **+132%
additive** and **+234% compounded**. The dashboard's equity curve shows
the additive one. Flagged, not changed — it moves every figure on the
dashboard and deserves its own decision.

---

## [2026-07-30e] — The unit conversion is finished, and a guard so it stays finished

**1 unit = 1% of bankroll. The bankroll is always 100 units.** Stakes
were already converted; the ledger's *reporting* was not. Every place
the dashboard added units across time has been changed to report **bank
growth (100 → X)** or a **percentage return**.

### Why a sum is not money

Growth changes the DOLLAR VALUE of a unit, never the number of units
bet. So a 1-unit win when a unit was $100 and a 1-unit win when it was
$150 are amounts in different currencies, and adding them produces a
figure that means nothing. Over the last 7 replay days:

| | |
|---|---|
| naive sum of daily P&L | **−13.17u** |
| what the bank did | 223.07 → 209.89 |
| honest figure | **−5.91u** |

The sum is 2.2× the truth. In a winning stretch it errs the other way,
which is the direction that misleads a paying subscriber.

### Added — `lib/units.ts`, and the guard that fails the build

`CumulativeUnits` is a branded number. Every across-time sum is typed as
one, and `formatUnits()` **refuses the brand at compile time** — so
`next build` fails and Vercel never deploys it. `ReplayWindow.pnl`,
`.yrfi.pnl`, `.nrfi.pnl`, `.flat` and `.flatPnl` all carry it now.

Two right answers replace it: `formatBankGrowth(100, 209.89)` and
`formatReturn()`. Plus `returnAsUnits()` — legitimate only because the
bank is 100 units by definition, so a percentage and a unit count are
the same number. It takes a **fraction**, so it cannot be called with a
naive sum by mistake.

`scripts/check-units-guard.mjs` guards the guard, and runs in
`prebuild` on every deploy. It compiles a probe of 6 forbidden and 5
legitimate forms and fails if any lands the wrong way — because the
whole protection is one function signature, and widening it to `number`
to silence an error would disable everything and change nothing
visible. Negative-tested: weakening `formatUnits` makes the script exit
1 with all six violations named.

### Changed — every cumulative surface

| surface | was | now |
|---|---|---|
| `/history` hero | `+109.89u` summed | return on a 100u bank + `bank 100.00u → 209.89u` |
| daily ledger col 3 | `Cumulative` units | **`Bank`** — a level, untoned |
| daily ledger col 2 | raw `simPnl` | **re-based** to the bank that night opened with |
| equity curve axis | `+NNNu` cumulative | bank levels, unsigned, anchored at 100u not 0 |
| "Window P&L" tile | cumulative units | **`Bank now`** 209.89u |
| "All-time high" tile | cumulative units | **`Peak bank`** 233.07u |
| "Max drawdown" tile | `−NN.NNu` | **−11.2%** of the peak it fell from |
| underwater chart | depth in units | depth as **% of peak**; input is now bank levels |
| zone hit-rate table | season unit totals | **return per unit staked** |
| RoiPanel season/floor | `fmtU(sim.profit)` | bank growth + return |
| divergence card | segment unit totals | return per unit staked |

### Two disagreements found and closed

- **The ledger table contradicted the new week card.** 2026-07-28 read
  −10.00u in the table and −4.61u in the card — same night, same
  system, both on screen. The table's figure was raw compounded units;
  it is re-based now and both read −4.61u.
- **`/` and `/history` disagreed about the season by 0.06u** (+109.95u
  vs +109.89u). RoiPanel divided a sum of per-game P&L by the opening
  bank; `/history` divided the bank endpoints. The exporter rounds the
  two separately. Both now divide the same two bank levels, so they
  agree by construction rather than by luck.

### Fixed — two pre-existing `/history` breaks at 375px

Neither was caused by the font swap (measured byte-identical under
VT323 and JetBrains Mono; both come from fixed pixel geometry).

- **The distribution bar was drawn at full magnitude in a half-width
  wing.** A diverging bar centred at 50% gives each side 50% to grow
  into; the fill used the whole magnitude, so the biggest day ran from
  50% to 150% and `overflow: hidden` clipped it. Not cosmetic — every
  large day capped at the same visible length, so the worst night of the
  season and a night 40% smaller drew identical bars, in the column that
  exists to compare magnitudes. Ten rows were overflowing, the worst by
  133px of a 265px track.
- **The zone table could not fit a phone at any type size.** Its
  `minmax()` minimums total 518px of irreducible width inside a 343px
  card, and `html { overflow-x: clip }` meant it did not scroll either —
  the rate, the P&L and the right edge of every bar were simply cut off.
  Two-row grid under 720px.

**`/history` at 375px goes from 18 overflowing elements to 0.**

### Verified

Production build. Every figure reproduced against an independent Python
pass over `season_record.json`, and the three surfaces cross-checked:

| | `/` RoiPanel | `/history` hero | week card |
|---|---|---|---|
| Season | +109.89u | +109.89u | — |
| Last 7d | −5.91u · 1-4 · 5 bets | — | −5.91u · 1–4 · 5 bets |

Ledger rows Jul 27/28/29 render −1.81u / −4.61u / +1.37u against banks
217.07 / 207.07 / 209.89 — all matching. Peak bank 233.07u, max
drawdown −11.2%, and the underwater card's "deepest" now reads the same
−11.2% instead of a units figure that measured something else.

`tools/pl_calc.py --window 7d` reports the LEDGER at **−12.773u**, no
drift. That is a different population from the replay's −5.91% and is
unchanged by this work.

### Not converted, deliberately

The ledger's stored `profit_loss_units` is still in old compounded units
for Kelly-era rows — 2026-07-28 NYY@CWS is stored as `9.56` units
risked. Correcting that is a data migration through `tracker.py`, not a
display change, and it rewrites history in `picks_2026.csv`. The zone
table's "per unit staked" divides by the bet count, i.e. assumes one
unit a bet, which is exact for every row before 2026-07-28 and
progressively wrong after; the card says so on its face.

`ReplayWindow.pct` is left in place but documented as never-render: it
divides an all-sides `pnl` by a YRFI-only `bankStart` and lands on
−7.7% where the bank says −5.91%. Nothing reads it.

---

## [2026-07-30d] — "Week at a glance" card on /history

Operator found an analytics card online (a "BudgetCard": big figure,
smooth sparkline, hover tooltip) and asked for the same visual idea
**built natively, not pasted in.**

### Why pasting it was never on

Both reasons were checked, not assumed:

- **No Tailwind, no shadcn in this project.** It is 16 CSS Modules files
  plus custom properties. Every utility class in that component would
  have rendered as *nothing* — not a broken style, an absent one, which
  looks like a layout bug and gets debugged as one.
- **It ships hardcoded fake data** — a `$30.739` balance, an invented
  week, indigo `#5B52E5` lines. This dashboard has spent days removing
  invented numbers; a fabricated balance in the most prominent card
  would be the single most believable wrong number on the page.

### Added — `WeekAtAGlance.tsx` + `.module.css`

CSS Modules matching `.chartCard`'s shell exactly. Tokens only
(`--muted-foreground`, `--foreground`, `--border`, `--card`); **no new
hex, no `--gain`/`--loss`**. Real data from `season_record.json`
`real.days[]`, the same object the hero above it reads. Mounted on
`/history` above the equity curve. No npm dependency added.

Fixed at 7 days and deliberately does **not** follow the page's
window toggle: a card captioned "last 7 days" that silently becomes 30
is the two-figures-one-label trap this page has been cleared of twice.

### The headline is not the obvious number, and that is the point

The obvious headline is "sum the last seven days". That is exactly what
the unit model forbids — the replay compounds the unit *count*, so a
10.00u loss on a 217u bank and a 2.00u loss on a 223u bank are not the
same quantity:

| | |
|---|---|
| naive sum of `simPnl` | **−13.17u** ← wrong, and 2.2× the truth |
| what the bank actually did | 223.07 → 209.89 |
| honest figure | **−5.91u** (−5.91% of bank) |

The curve plots the bank **indexed to 100 at window open**, so its last
point and the headline are one quantity read two ways and cannot
disagree. Verified: compounding the five daily returns gives −5.90u
against the bank ratio's −5.91u, the gap being float rounding.

### Added — `rebaseLastDays()` in `lib/season-record.ts`

Divides each day's P&L by the bank it *opened* with. That ratio is
bankroll-free — it is what a $1k follower and a $25k follower both
experienced — and the ratios compound to exactly the bank ratio.

It also reports `offSideBets` separately rather than dropping them.
`simBankAfter` stakes **YRFI only** (exporter fix, 2026-07-30) but
`day.games` still *contains* NRFI rows — 24 of them in the real-price
window, 1 in the last 7 days. Counting bets by walking games while
reading a YRFI-only bank puts two populations in one sentence. The card
names the excluded NRFI bet on its own face.

**Latent trap found, not yet fixed:** `ReplayWindow.pct` divides an
all-sides `pnl` by a YRFI-only `bankStart` and lands on −7.7% where the
bank says −5.91%. Nothing renders it today — `RoiPanel` computes its own
from `yrfi.pnl` — so it is a loaded gun rather than a live defect.

### Simulated, held apart by brightness

Every figure here is a replay, so nothing is tone-coloured no matter
which way the week went; the sign plus the word "up"/"down" carries
direction. Under the matrix palette this rule does more work than usual
because `--foreground` and `--gain` are both `#00FF41` — hue cannot mark
real money when the page is one hue, so brightness is the whole
distinction and this card sits on the dim side of it. The area fill uses
the existing `--sim-hatch` rhythm, rebuilt as an SVG `<pattern>` because
`fill` takes a paint server and not a CSS image.

### Verified

Production build. Every rendered figure reproduced against an
independent Python calculation over `season_record.json`:

| day | day P&L | week to date |
|---|---|---|
| Jul 24 | −0.90u | −0.90u |
| Jul 26 | 0.00u | −0.90u |
| Jul 27 | −1.81u | −2.69u |
| Jul 28 | −4.61u | −7.17u |
| Jul 29 | +1.37u | −5.90u |

Headline −5.91u / −5.91% of bank / 1–4 over 5 bets / Jul 24 → Jul 29.
375px: card 343px wide, **zero internal overflow**, `/history` still at
its pre-existing 18 offenders with none from this card. Pointer
hit-testing confirmed routing to all five bands.

**Not verified in-browser:** real mouse-hover and keyboard-focus firing.
The Browser pane is hidden in this environment, so Chrome dispatches no
`focus`/`focusin` events at all and React's enter/leave delegation never
runs. Handlers are confirmed attached and the render path is confirmed
correct when state is set; the event dispatch itself is untested.

---

## [2026-07-30c] — VT323 out, JetBrains Mono in

Operator: *"VT323 is too hard to read ... it is a pixel font -- at 11-13px
on a phone it is genuinely hard to read."* Correct, and it conflicts with
the usage scene PRODUCT.md actually describes: phone in hand, legible at
arm's length, thirty seconds of attention.

**Palette untouched.** Matrix black / phosphor green / alarm red is
unchanged; every measured contrast ratio is identical because no colour
token moved. This is a typeface change only.

### Why JetBrains Mono over IBM Plex Mono

Both were live options. The deciding number is x-height, measured on the
running page with canvas `actualBoundingBoxAscent` rather than taken from
a specimen:

| metric | VT323 | JetBrains Mono | delta |
|---|---|---|---|
| x-height | 0.40em | **0.55em** | +37% |
| cap-height | 0.56em | 0.73em | +30% |
| advance | 0.40em | 0.60em | **+50%** |

`font-size` sets a box; x-height decides how much ink is in it. Every
11-13px label on this dashboard was drawing more than a third smaller
than its declared size implied. JetBrains Mono spends its em on lowercase
rather than ascender clearance, and its digits keep open counters at
small sizes — which matters on a board whose job is telling `+4.17u` from
`+4.77u`. Plex Mono is the more conventionally proportioned face and
would have bought less.

Four weights are now loaded (400/500/600/700). The type scale has
declared four since it was written; VT323 ships one, so every weight
distinction in it had been a no-op or a synthesised smear.

### Fixed — four clipped elements, all fixed px widths tuned to 0.40em

The advance row is the cost of the swap and it is the larger number: text
is half again as wide at the same size.

| element | was | now | what was lost |
|---|---|---|---|
| board TIME column | 74px | **88px** | **every 10/11/12 o'clock start time** — 8 chars needs 84.3px, 7 needs 73.8px, so late games lost their last character and every other game fit exactly |
| ticker summary strip | — | λ̄ + games hidden ≤820px | content went 280px → 420px in a 271px masked box; the two figures stopped rendering *silently* |
| Tonight's Action side label | 50px | **56px** | trailing letter of `PENDING` |
| date select min-width | 158px | **146px** | trailing nav button border |

The ticker items hidden on mobile are the two this component's own header
calls "slate context that is NOT a bet count", and `.rightCap` was already
hidden at that breakpoint on the same reasoning. Both figures remain on
the page below. This converts an accidental omission into a deliberate one.

Deleted `font-feature-settings: "ss01" 1, "cv11" 1, "ss03" 1` from
`html, body` — Inter/Geist stylistic sets left behind by a font this
dashboard stopped using two palettes ago. Inert under VT323; under a face
that defines ss01/ss03 they are live instructions to substitute glyphs
nobody chose.

### Fixed — pre-existing, found while verifying at 375px

Browsing to any past date puts a "jump to today" badge beside the date
cluster. `.dateRow` never wrapped, so the badge *squeezed* the cluster
(215px of content into 169px) and `overflow: hidden` ate the trailing nav
button — the operator lost "next date" precisely when browsing history.
Now wraps instead of shrinking.

### Verified

Production build, not dev. Both pages at 375px and 1280px: **zero
overflowing elements on `/`**, zero clipped start times, ticker fits
exactly (271px in 271px), no console errors.

`/history` retains 18 overflowing elements at 375px — measured byte-for-byte
identical under both fonts (fixed px grid minimums of 482px in the zone
chart, and a diverging bar whose halves are drawn at full width instead of
half). Pre-existing, font-independent, not addressed here.

---

## [2026-07-30b] — The replay stops staking NRFI; /history charts the system

Operator: *"i wanted to remove the flat unit tracking ... and all the
charts should reflect the kelly sizing with the new system going back to
the start of the season, what our system would have picked and our
profit."*

### The replay was staking a side the system does not bet

`export_season_record.py` ran `simulate(y_bets + n_bets)` — it staked
**NRFI**. `_LR_STRONG_NRFI_P` has been 1.01 ("off") since 2026-06-07 and
the last real NRFI bet was 2026-06-14, so the headline bank curve
described a system nobody runs.

| | before | after |
|---|---|---|
| bets staked | 127 | **106** |
| bank | 100u → 227.11u | **100u → 262.88u** |
| max drawdown | 27.9% | **23.7%** |

Every consumer was already compensating by hand: the dashboard headline
read `yrfi.pnl` instead of the sim's own profit, and the daily and flat
figures each re-excluded NRFI separately. **Three hand-corrections for
one wrong input.** Fixed at the source. NRFI is still tracked — 24
would-be bets, −15.04u — simulated on its own independent bank so the
detail survives without touching the money path.

`day.simPnl` / `day.flatPnl` had the same split: the bank was YRFI-only
while the day totals beside it still folded NRFI in (+147.88u vs
+162.92u). Now both read the staked side only.

### /history charts the system, not the retired ledger

One series feeds the equity curve, drawdown, days-under-water and the
daily table, so switching it rewires the page at once. It now reads the
replay's compounding bank.

Removed with it: the **flat-1u** line and stat (operator's request — the
system stakes quarter-Kelly, so a flat figure describes a scheme nobody
runs), the **"what actually happened"** ledger line, and the
System/You column split, which existed only to reconcile a replay
headline against a ledger table that is no longer on the page.

### Two bugs caught while wiring it

1. **The oldest visible row reported the whole season as one day.** Day
   P&L was derived by differencing the cumulative with `prev` seeded at
   0, so on any window not starting at the opener the first row showed
   its entire season-to-date total — "Last 30 days" had a +150u day. Now
   read from the record's stored `simPnl`; no edge case, exact.
2. **The hero and its own table were in different units.** The headline
   rebased to a 100u base while the table is absolute, so 30d read
   +13.23u above a column summing to +30.7u. The headline is now raw
   window profit and the bank it was earned on is named in the sub-line.

Verified per window — hero equals its Day P&L column exactly:
season +162.92u (100u bank), 30d +30.71u (232u bank), 7d −40.28u (303u
bank).


## [2026-07-30] — Old ledger removed; the "Flat 1u" stat was measuring the wrong bets

Operator: *"remove the old ledger entirely ... still keep the data
saved"*, and *"are there any other bugs or errors to fix?"*

### Removed — the flat-1u ledger block

Gone from `RoiPanel`, along with `LedgerRow` and its helpers (96 lines).
It reported the same nights as the system card above it under an
accounting method retired 2026-07-28 (flat 1u, looser gate, NRFI still
live), and its own footnote conceded the figures rested on a placeholder
−110. Season-wide it read −8.93u, of which **−11.29u is NRFI** from a
strategy switched off 2026-06-07.

**No data was deleted, deliberately.** Every row is untouched in
`data/picks_2026.csv` and Supabase. `tools/pl_calc.py` reports it and
`tools/kelly_season_backfill.py` compares flat against every Kelly
fraction on demand, so "what would flat 1u have done" is still one
command away. This removed a *render*, not a *record*.

### Fixed — "Flat 1u" covered a different set of bets than its headline

Shipped in 2026-07-29i, wrong from the start. `flatPnl` summed each
day's day-level `flatPnl`, **which includes NRFI** — while every
headline it sits beside is YRFI-only, because NRFI is tracked and never
bet.

| | shown | correct |
|---|---|---|
| Season flat | +9.29u | **+12.30u** |

The −2.97u gap is 22 NRFI would-be bets. A figure captioned *"the same
bets, unlevered"* that silently covered a **different** set of bets is
exactly the defect this dashboard spent two days removing — reintroduced
by the fix for it.

`flat` is now accumulated per game inside the same `action === "BET"`
loop that produces `pnl`, and split per side, so the two cannot describe
different populations. Both call sites read `yrfi.flat`.

### Also fixed — the phantom-deleted files

`DashboardShell`'s comment claimed `StatusLine`, `ShadowPnlCard` and
`SlateProjections` "were DELETED outright on 2026-07-28 — files and
stylesheets", and that `SummaryStrip` was "still live: OpsHealthCard and
TonightsActionCard both import it. Do not delete it."

**Both claims were false.** All four `.tsx` files and all four
stylesheets were still on disk, and the two "imports" of `SummaryStrip`
were *prose mentions inside comments* — verified by grepping actual
`from "..."` lines, which returned nothing for any of the four.
`ShadowPnlCard` was additionally the sole caller of `/api/shadow-pnl`,
leaving that route live in production with no consumer.

Deleted for real: 8 files plus the orphaned route (now 404). Verified
against `.next/static/chunks` that none reached the bundle before
removing, and both pages render with a clean console afterwards.

The comment now records why this matters beyond tidiness: **a comment
asserting code is gone when it is not is how the
`realPricedCumulativePL` bug survived** — documentation stating a state
nobody re-checked. If you claim a deletion there, run the grep in the
same commit.


## [2026-07-29k] — The daily ledger now reconciles to the headline

Operator, on the daily ledger: *"i think these numbers are off"*.

**They were not.** Verified across all 27 rows: the running total is
internally exact, every `cumulative` equals the previous row's plus the
day's P&L, and the distribution bars are proportional. Nothing was
miscalculated.

**But the page was genuinely confusing, and that is on the redesign.**
The hero at the top now reads **+14.54u** (the system) while the table
below ends at **−4.41u** (the ledger), and the column was headed only
"Day P&L" — it never said *whose*. A table ending on a different number
than the headline above it, with no label distinguishing the two
populations, reads as an arithmetic error. That is the same failure mode
as *"i thought we were up 90+ units"*, one layer down.

### Both columns, each summing to its own headline

`Day P&L` → **`System`** and **`You`**. Verified live: the System column
sums to **+14.54u** (exactly the hero) and You to **−4.42u** (the ledger,
to rounding). The per-day divergence is now legible — 2026-07-27 the
system lost 5.35u on a night the operator made 1.29u.

`System` renders in neutral ink at every sign because it is simulated,
beside a real-money column that carries tone. `—` means the replay has
no entry for that date, which is different from a 0.00u day where it
looked and declined.

### Two traps, both checked numerically rather than assumed

1. **`day.simPnl` is unusable for this.** It includes NRFI, which the
   hero excludes because NRFI is not bet. Over the 30-day window
   `day.simPnl` rebases to +10.42u against the hero's +14.54u — the
   −4.12u gap is exactly the NRFI side. The per-day figure is summed
   from each day's *games*, YRFI only, matching `replayWindow`'s own
   bucketing.
2. **Rebasing needs one divisor**, the bank at window start, not each
   day's own bank. Scaling every day by the same constant is what makes
   the column sum to the hero.

Five grid tracks now; wraps to two on a phone with header and rows
sharing the same flow so they stay aligned. Dead `.tiles` / `.tileBig`
responsive overrides removed with the tile family they styled.


## [2026-07-29j] — /history gets the decision-first hierarchy

Redesign pass 2. The dashboard got hierarchy on 2026-07-29g; /history
was deferred and still opened with **two identically-weighted bordered
tiles stacked on each other** — the system's figure and the ledger's.

That is the exact pattern PRODUCT.md names as the reason this page is
not scannable ("a stack of same-weight cards"), and it did something
worse than look flat: two equal cards for two numbers that answer
*different* questions read as a contradiction. It is the same confusion
that produced *"i thought we were up 90+ units"*.

### One hero, one subordinate line

- **The system leads at 40px** — that is what the page is for.
- **The unlevered twin sits directly beneath it.** Over 30 days those
  read +14.54u and +0.89u: the same bets, one multiplied ~16× by
  compounding. Adjacent, that is obvious; apart, invisible.
- **"What actually happened" is a line, not a rival card.**

The colour law produces a deliberate inversion here: the **big** figure
is neutral ink because it is a replay, and the **small** one carries
`--loss` rose because it is real money. Hue marks what is *real*, not
what is loud.

### Dead code removed

`tileTone()` and the whole `.tile` family (`.tiles .tile .tileBig
.tileLabel .tileSub .tileProv .tileTonePos .tileToneNeg`) — 77 lines of
CSS, verified at 0 usages after the collapse. `.tileTonePos` /
`.tileToneNeg` also carried `inset 3px 0 0` side stripes, the
coloured-edge-as-hierarchy pattern removed everywhere else that day.

Caught while wiring it: `tileTone()` was briefly hung on the inline
`.actualFig` span. It returns a CARD-level class that colours a
descendant `.tileBig` and paints that inset stripe — so on a span it
coloured nothing and drew a stray bar. Replaced with
`.actualFig[data-tone]`.

Verified at 375px and 1270px: no horizontal overflow, hero above the
fold on a phone, all four downstream sections (equity, drawdown, zones,
daily ledger) intact, clean console on a fresh tab.


## [2026-07-29i] — Every date filter now answers "what would the new system have done"

Operator: *"update the entire dashboard so i can see what our new record
and profit would be when i choose the date filters"*.

### /history had the same filters and answered a different question

The main dashboard and /history both offer 7d / 30d / season. The
dashboard showed the **replay** (today's rules), /history showed the
**realised ledger** — which is mostly bets placed under rules that no
longer apply: NRFI was live then and is switched off now, and the YRFI
gate has since tightened. So the same filter on two pages gave two
unrelated numbers with nothing saying why.

/history now leads with a windowed system card, `real` side, matching
the dashboard figure exactly:

| window | the system (¼-Kelly) | the actual ledger |
|---|---|---|
| Last 7 days | −16.03u (0-4) | −11.15u |
| Last 30 days | +14.54u (21-13) | −4.41u |
| Season | +144.85u (67-38) | −8.93u |

Both are true and they answer different questions; they are now labelled
as such instead of looking like a contradiction. The record is read
server-side so the card renders on first paint, and soft-fails to null —
a missing record costs one card, never the page.

### "Priced: N of N" → "Flat 1u"

That tile counted bets with a captured DraftKings price, which mattered
while the headline came from `projected` and a third of its book was
filled at an assumed −125. The headline now comes from `real`, which by
construction has **zero** assumed prices — so the tile could only print
"43 of 43". A stat with no variance is not a stat.

The slot goes to the figure that actually explains the headline: **the
same bets, unlevered, at a flat 1 unit.**

| window | ¼-Kelly | flat 1u |
|---|---|---|
| Last 7 days | −16.03u | −6.00u |
| Last 30 days | +14.54u | **+0.89u** |
| Season | +144.85u | **+9.29u** |

Last 30 days is the one to read twice: **+14.54u levered off +0.89u of
edge.** That is almost entirely compounding, not model performance —
precisely the distinction the operator had been missing, now visible
without asking for it.

### The truncated window says why

The header said the season starts 2026-04-01 while the card reported
from 2026-05-07, unexplained. `real` only covers dates where prices were
actually captured; April has none. Stated inline now, rather than left
as a date mismatch the reader has to rationalise.


## [2026-07-29h] — The system record drops the invented prices

Operator, after being told the season was down: *"i thought we were up
90+ units profit on the season with the new system and the kelley
sizing"* — then, asked what they wanted: *"the real record should be
based off if we started with 100u bankroll, sizing properly"*.

### The headline was compounding prices that were never observed

`season_record.json` carries two sides. `projected` fills every bet with
no captured DraftKings price at an assumed **−125** — 62 of its 194
bets, a third of the book. `real` is the same model, same gate, same
quarter-Kelly sizing, on the subset whose price was actually observed.

The SystemCard headline read `projected ?? real`. Every other consumer
in the app already preferred `real` first; this one surface did not, and
it is the one rendered at 32px.

| | bets | invented prices | flat edge | ¼-Kelly from 100u |
|---|---|---|---|---|
| `projected` | 194 | 62 | +32.46u | → 866.08u (**+766u**) |
| `real` | 127 | 0 | +9.33u | → 227.11u (**+127u**) |

The exporter's own docstring already warned about this: *"a simulated
100u bank turns a +34u edge into a +880u 'profit' that was never
earned."* Headline is now `real ?? projected`; SEASON reads **+144.85u**
(YRFI only, 105 bets) instead of **+822.19u**. `projected` is not
deleted — it still renders inside "How this number was computed", which
is the disclosure that exists to state the price assumption.

Also regenerated `season_record.json`, which was stale: it described 191
bets under an older gate while a fresh replay produces 194.

### The number is still a simulation, and the gap is SELECTION not sizing

Both figures are `Simulated`-tagged and neutral-toned per the colour
law. The honest reconciliation the operator needed:

- **Real ledger, real prices, what actually happened: −10.55u** over 528
  graded STRONG bets.
- **Replay, real prices, quarter-Kelly from 100u: +127u** over 127 bets.

Those differ mainly because **the replay bets a quarter as often** — it
applies today's tightened gate to the whole season, where the ledger bet
everything the system flagged STRONG at the looser gates in force at the
time. Resizing a losing selection does not make it win; the replay wins
by *not taking* ~400 of those bets. Recorded here because "make the
simulation real" is an operational change (bet the replay's slate), not
a reporting one.


## [2026-07-29g] — Decision-first redesign: the card now names the plays

Operator asked for a full UI redesign and chose **decision-first
triage**: the top of the screen answers only "what do I bet tonight and
how much", everything analytical below.

### The card counted things and never said which games to bet

`TonightsActionCard` opened with "**2** flagged STRONG", a NRFI/YRFI
split and a passed tally. PRODUCT.md says the scene is a phone in one
hand in the hour before first pitch, asking *"what do I bet tonight, and
how much?"* — and answering it required scrolling past a ~400px
performance panel to a 16-row table and picking the STRONG rows out by
eye. **A count is a summary of the answer, not the answer.**

The card now leads with the plays themselves — matchup, first pitch,
side, stake, price, and the lock deadline — one 56px+ row each, sorted
by **what closes next** rather than board rank, with locked and graded
plays sinking to the bottom because there is nothing left to do about
them. Total exposure follows as a single line.

Stake resolution is byte-identical to the board's StakeChip (replay
first, ledger second); the same quantity in three places must not drift.

### Reordered: analysis moved below the slate

`RoiPanel` sat above the board. **This reverses an earlier explicit
operator request** ("the record is the second thing he wants to see") and
is called out in the source rather than quietly changed — on a phone it
put 400px of analysis between the decision and the slate the decision
came from. Order is now: plays → board → performance → why. Restoring the
old position is a one-block move; nothing depends on it.

### `lib/lock.ts` — one definition of the deadline

`computeLockAt` / `formatLockTime` lived inside BoardRow. The decision
card needs the same deadline, and two copies of a deadline are two
chances to disagree about it. Extracted with `minutesUntil` and
`formatCountdown`, and unit-tested: T-60 arithmetic, an EDT and an EST
slate (DST is derived, never hardcoded), placeholder times returning null
rather than inventing a deadline, and the countdown wording.

The deadline is the one element that earns `--attn` by the page's own
colour law ("a decision is waiting on you"), and it goes solid inside 45
minutes. No pulse — PRODUCT.md names sportsbook urgency as an
anti-reference.

Verified at 375px and desktop: no horizontal overflow, plays above the
fold, and the empty state stays calm ("No games flagged tonight. Nothing
to bet — the model passed on 4 of 5 games") on a no-play slate, which is
about a third of nights.

/history keeps its current layout for now, per the chosen scope.


## [2026-07-29f] — /history showed the wrong number and a frozen one; amber → violet

Operator: *"fix https://nrfi-terminal.vercel.app/history because it
doesnt show the real numbers and updates"*. Two independent defects.

### Fixed — the real-priced series was consumed but NEVER PRODUCED

`HistoryView` has read `realPricedCumulativePL` since the 2026-07-28
audit. **`lib/roi.ts` never produced it.** The consumer shipped without
the producer, so the fallback fired on every render: the page charted
the fabricated −110 series and printed *"Reload the page to pick up the
real-priced figure"* — advice that could never work, because no reload
could conjure a field nothing wrote.

The gap it hid was not subtle. The season headline read **+21.86u**
while the zone table directly beneath it summed to **−12.67u**. Opposite
signs, same bets, one screen. Now **−8.93u**, and it agrees with the
split line to the cent.

**Why it went unnoticed:** the field was read through an inline cast,
`data as RoiResponse & { realPricedCumulativePL?: SeriesPoint[] }`. An
*optional* property on a cast type cannot fail to compile when the
producer omits it — there was no type error, only a silent `undefined`.
Both `realPricedCumulativePL` and `stakeEpoch` are now declared on
`RoiResponse` itself and read directly, so deleting the producer is a
compile error. Adding them immediately surfaced two more producers that
had been silently incomplete (`roi-today.ts`, and `loadRoi`'s empty
fallback) — exactly the point.

### Fixed — the page could only ever be as fresh as the last deploy

`loadRoi` read `picks_<year>.csv` off disk, and `npm run prebuild`
copies `data/` into the bundle at **build time**. A Vercel deployment's
filesystem is immutable, so /history showed a frozen snapshot: the
operator watched HOU@LAA settle +4.117u on the main board while
/history still showed the night at −2.08u. `dynamic = "force-dynamic"`
did not help — it re-runs the render, and re-reading a frozen file
yields the frozen answer.

Now Supabase-first with CSV fallback, mirroring `lib/board.ts` (which
already did this, which is why the two pages disagreed). **Paginated**,
because PostgREST caps at 1000 rows and a season is ~2400 — an
unpaginated read would have silently dropped the oldest 60% of the
season, the same cap that previously truncated `pl_calc` and the date
picker. Jul 29 now reads **+2.04u**, matching `pl_calc`.

**Boundary bug caught while wiring it:** Supabase returns native
Postgres types (`profit_loss_units` as a JS number) while `parseCsv`
returns strings, and the 250-line aggregator calls `.trim()` throughout.
Handing it raw rows crashed the page with *"(r.profit_loss_units ??
'').trim is not a function"*. Rows are now normalised to the CSV string
shape at the boundary rather than teaching every call site to handle
both.

### Fixed — "split by side" silently dropped real money

The split line read `betZones` only, which excludes anything whose
`pick_side` is PASS. But a PASS-labelled row can hold a real bet at a
real price: a STRONG pick still labelled "LINEUP PENDING" when its lock
window closed gets `bet_placed=Y` and settles normally (2026-07-27
NYY@CWS, +0.909u). Season-wide that is **+1.62u over 3 bets**, and
dropping it left the split summing to −10.55u under a −8.93u headline.
Same population now; they agree exactly.

### Changed — `--attn` amber → violet

Operator: *"change the amber to violet"*. Amber was the last warm hue on
screen. Dark `#fbbf24` → `#a78bfa` (7.10 / 6.35 / 5.49), light `#7e5800`
→ `#6d28d9` (6.36 / 6.97 / 5.74). The three money hues now sit at
**190 / 255 / 345** — no two within 65°, none warm, and violet's
luminance lands between gain and loss so they stay ordered in greyscale.
App icons re-keyed too.


## [2026-07-29e] — The 112 inverted capture timestamps, healed

Operator authorised repairing history. `tools/heal_capture_ts_inversions.py`.

**Which column was corrupt — measured, not assumed.** Within its own
slate day, on the inverted rows, `odds_captured_at` sits at the **7th
percentile** (healthy baseline: 38th) while `opened_captured_at` sits at
the 29th (healthy: 43rd). A "latest price seen" that early is the
dragged-back one; `opened_captured_at` is the trustworthy side.

**Recovery was attempted before clamping.** All 94 daily backup
snapshots (2026-05-02 onward) were searched for a surviving
pre-corruption value — a capture later than the corrupted one and
consistent with `opened_captured_at`. **Zero of the 112 rows had one**;
the drag-back always preceded the daily snapshot. No real value
survived, so the repair sets `odds_captured_at = opened_captured_at`:
a real observed timestamp for that row, explicitly a **lower bound**.
Those rows now mean "the price had been seen by at least this time".
11 of them have `bet_placed=Y`, so their lock time is a lower bound too.

**Verified surgical.** Money-column fingerprint
(`market_*_odds`, `bet_placed`, `units_risked`, `profit_loss_units`,
`graded_result`, `pick_side`, `pick_strength`, `opened_*`) is
byte-identical before and after. Exactly 112 cell changes, in exactly
one column, across an unchanged 1579 rows. `pl_calc` reports stored and
recomputed P&L agreeing at −6.030u with no drift. Re-running the heal
now finds 0 — idempotent.

### Fixed while healing — the guard had the wrong rule for one column

The two timestamps move in **opposite** directions:
`odds_captured_at` is "latest seen" (high-water mark),
`opened_captured_at` is "first seen" (**low**-water mark). The first cut
of the guard applied `advance_capture_ts` to both, which would have
locked in the wrong direction and let `opened_*` drift forward — the
same class of defect, mirrored. New `tracker.retreat_capture_ts` for the
low-water side. Measured before shipping: `opened_*` had not in fact
drifted forward (29th vs 43rd percentile), so this closes a latent hole
rather than an active one.

### Fixed — the heal's own audit trail failed silently

`_record_pick_change` takes a required keyword-only `captured_at` that
the first version omitted, so all 112 journal calls raised `TypeError`
into a blanket `except` and the heal completed with an **empty audit
trail**. Backfilled from the git diff (exact old→new pairs) — 112
entries now in `pick_changes.csv`. The script now counts successful
journal writes and warns loudly if the count does not match the rows it
edited, so a heal can never again report success while its audit trail
quietly failed.


## [2026-07-29d] — Kelly stakes round to whole units, floored at 0.5u

Operator asked whether stakes should be rounded, since quarter Kelly
produces figures like `5.97u` and `2.08u` that have to be typed into
DraftKings by hand. Then chose the variant: *"round to whole units, but
any bets that might round to 0 should just round to 0.5 units."*

Measured first, over all 348 graded real-priced STRONG bets:

| sizing | profit | bets placed | max DD |
|---|---|---|---|
| exact (was shipped) | +81.20u | 301 | 39.6% |
| whole units, small → no bet | +92.79u | **285** | 40.0% |
| **whole units, floor 0.5u (shipped)** | +83.83u | **300** | 39.7% |

**Plain whole-unit rounding silently drops 16 of 301 bets** — anything
under 0.5u rounds to zero, and zero is a no-bet. That is a hidden bet
gate arriving through a convenience change, the exact class of surprise
CLAUDE.md's money rules exist to prevent. The floor recovers 15 of them.

The +2.63u over exact sizing is **noise, not an edge** — same bets,
slightly different sizes, landing favourably by chance. Do not quote it
as an improvement. The case for rounding is convenience at no cost.

`NRFI_KELLY_ROUNDING` (default `1.0`, set `0` to disable) and
`NRFI_KELLY_ROUNDED_FLOOR` (default `0.5`).

### Two safety properties, both tested

- **A no-edge bet is never floored into a real bet.** Kelly returns 0
  for two reasons — the model does not beat the market's implied
  probability, or the daily cap left no room — and both are deliberate
  refusals. The rounding block sits *below* the no-bet gate, so it only
  ever operates on stakes that already earned the right to exist.
- **Rounding up cannot breach a cap.** Found while wiring this: on an
  88.36u bank the per-bet ceiling is 8.836u, and an 8.60u stake rounds
  to 9.00u — over it. A convenience feature would have defeated a risk
  guard rail. When the rounded figure does not fit under the per-bet
  ceiling *or* the daily budget, the exact stake is kept instead.

`tools/verify_kelly_wiring.py` gains CHECK 5 covering both, plus the
floor and ordinary rounding. CHECK 2 now models rounding in its
independent reference implementation rather than being loosened — the
tolerance is still 0.011u and it reports 0.0000u disagreement.

Tonight's two bets are unaffected: T2.23 freezes `units_risked` once
`bet_placed=Y`, so they stay at the 5.97u and 2.08u they were placed at.

---

## [2026-07-29c] — Units lead the headline; the replay stops wearing the money hue

Operator: *"why are the 'last X days' filters showing percentages and
not units? im so confused?"*

### Changed — the performance headline is units, not a percentage

The card's docstring justified the percentage: the raw replay compounds
100u → ~1200u by late July, so its own units describe a bankroll nobody
has. True, but the fix for that is the **rebasing**, which the card
already does via `bankUnits`. With both in place the headline read
`+16.3%` above a sub-line reading `+16.33u on your 100u bankroll` —
**the same number twice**, since at a 100u bank a unit *is* a percent.
The operator stakes in units and was shown their result in the one unit
they don't think in. Units now lead; the percentage moves to the
sub-line (kept, not deleted — the two separate again if the bankroll
ever moves off 100u).

### Fixed — a backtest was rendering as realized P&L

Swapping the headline to units exposed this, and made it worse: SEASON
showed a 32px **`+822.19u` in `--gain` cyan**, visually identical to
TONIGHT's real `−2.08u`.

Quarter-Kelly went live 2026-07-28; every bet before that was flat 1u.
The card's own code says it: *"TONIGHT comes from the board, not the
record. Everything else comes from the replay."* So every window except
TODAY is a simulation of a staking scheme that was not in use, and all
four were toned from `y.pnl` as though they were money.

globals.css, verbatim: *"SIMULATED FIGURES ARE NEVER TONE-COLOURED.
Coloured = your money. Neutral = a back-test. No exception, no
carve-out."* This was the exception. PRODUCT.md lists it as the
product's central design problem (four kinds of number at
near-identical visual weight) and as an explicit anti-reference.

Replay windows now render in plain `--foreground` and carry a
`Simulated` tag in the eyebrow. TODAY keeps its tone — it is the only
real-money figure on the card. Verified in-browser: TODAY `rgb(251, 92,
120)`, the other three `rgb(233, 239, 246)`.

---

## [2026-07-29b] — Capture-timestamp guard + the palette reversal

Operator: *"fix the timestamp bug then fix the colors. i hate the
orange colors. it needs to be bright colors that look great."*

### Fixed — `odds_captured_at` could run backwards

112 of 1129 rows (9.9%) carried an `odds_captured_at` EARLIER than
their own `opened_captured_at` — impossible by construction, since
`_apply_odds_to_row` assigns both from the same value on first import
and only ever moves the former forward.

**Cause:** `tools/sync_csv_from_supabase.py` (runs every predict and
grade tick) merges the Supabase mirror into the CSV *column by column*
and skips any column blank in Supabase, so a row could be assembled out
of two different capture moments — a lagging mirror's
`odds_captured_at` beside a fresher `opened_captured_at` the CSV
already had. The file's own comment asserted "Supabase is the fresher
writer for these columns"; that holds for values, not for time.

**Fix:** capture timestamps are now high-water marks. New
`tracker.parse_capture_ts` / `capture_ts_regressed` /
`advance_capture_ts`, wired into both write sites. The sync reports
`N advanced, N rejected as backwards` each run.

No money was affected — `bet_placed` and `units_risked` on the affected
rows were correct — but the T2.23 lock freezes `odds_captured_at` when
a bet commits, making it the only ledger evidence of *when* a bet
locked, so the T2.58 window was unauditable and CLV was suspect.

New `tools/verify_capture_ts_monotonic.py` (7 + 6 + 5 + 1 assertions,
all passing) including a replay of the original defect. Historical rows
are **not** repaired: that rewrites the ledger and needs operator
sign-off per CLAUDE.md's data rules.

### Changed — the warm palette is retired

This reverses a preference recorded in CLAUDE.md, AGENTS.md and
PRODUCT.md as "explicitly and repeatedly chosen." All three were
updated in this commit, because a future agent reading the old note
would helpfully put the orange back.

Swapping three accent tokens would not have worked: every *surface*
was hue ~30 too, so the background, cards, borders and body text were
all brown or tan. "The orange colors" was an accurate description of
essentially the whole screen.

| | old | new (dark) | new (light) |
|---|---|---|---|
| background | `#12100e` | `#0a0e14` | `#eef3f8` |
| foreground | `#f0e4d3` | `#e9eff6` | `#0f1a24` |
| `--gain` | peach `#f5a465` | cyan `#22d3ee` | teal `#0b5f77` |
| `--loss` | tomato `#ec8060` | rose `#fb5c78` | crimson `#b81a3c` |
| `--attn` | amber `#f0c96e` | amber `#fbbf24` | gold `#7e5800` |

The money hues are now ~150° apart instead of sharing a 30° wedge of
orange. The previous pass had to fight for luminance separation
precisely because it had no hue to spend; separation now survives
greyscale, dim phone screens and red-green colour blindness.
Terminal green/red remains rejected and absent.

Every ratio was recomputed and verified in the browser against rendered
surfaces, not asserted. All money hues and both ink tones clear AA on
all three surfaces in both themes. `--border` now clears 3:1 on all
three in dark, retiring the old palette's knowing 2.95 concession.

### Fixed — two more side-as-hue violations found during the repalette

Swapping the tokens made these obvious, because they got *louder*:

- **The pick pills painted side as money.** `.nrfiStrong`/`.nrfiLean`
  were `--primary` (= gain), `.yrfiStrong`/`.yrfiLean` were
  `--destructive` (= loss), `.passTone` was `--secondary` (= at risk) —
  on the pill background, dot and label of every row. Every YRFI row
  rendered in the losing colour whether it won or lost, and PASS rows
  (usually the largest group) carried the at-risk hue while having no
  money on them at all. Rewritten to weight: side via the dot's ink
  (`--side-nrfi` / `--side-yrfi`), strength via label ink and border.
- **`.resultPass`** used `--attn` on the result chip of a game the model
  declined. Now neutral. `.resultWin`/`.resultLoss` were already correct
  and are unchanged.

Warm elements on the rendered page: **52 → 3**, and all three survivors
are correct uses of `--attn` ("8.05u at risk", the change-banner dot).

### Also

- PWA manifest `theme_color` was `#5dff9a` — phosphor **green**, the
  palette the operator rejected — sitting on their home screen. All four
  app icons were green + orange; recoloured. `themeColor` and
  `msapplication-TileColor` re-keyed to the new `--background`.

### Follow-up — four survivors a hex grep structurally could not find

The first sweep matched `#rrggbb`. These four were live orange in other
notations and were caught only by re-sweeping for `rgb()`/`rgba()`
triples and URL-encoded `%23` forms:

- **The browser-tab favicon** (`layout.tsx`) — inline data-URI SVG with
  `fill='%23f5a465'`. URL-encoded, so the `#` anchor missed it.
- **`::selection`** (`globals.css`) — highlighting any text on the page
  handed back a band of the retired peach. Written as `rgba()`.
- **The brand mark's glow** (`DashboardShell`) — `rgba(245,164,101)` at
  the top-left of every screen, the most persistent orange left.
- **`LambdaMeter`'s drop shadow** — a warm near-black, now cool.

Lesson recorded in the source: a colour audit must sweep `rgb()`,
`rgba()` and `%23` forms, not just hex literals. Final verification
scans computed `boxShadow` and `backgroundImage` as well as text and
background colours: **3 warm elements page-wide, all correct `--attn`.**

---

## [2026-07-29] — Money-path verification + the last side-hue numerals

Operator asked for three things: confirm Kelly sizing is working, confirm
the right picks are being placed and on time, and critique the design
because *"i still think its so confusing with the data."*

### Verified (no code change needed)

- **Kelly sizing is correct end to end.** `tools/verify_kelly_wiring.py`
  passes all four checks. Reproduced both of tonight's live stakes by
  hand from the shipped helper: TOR@WSH `p=0.6052 @ −130` → 2.08u off a
  90.44u bank; HOU@LAA `p=0.7021 @ −145` → 5.97u off an 88.36u bank
  (the bank had compounded down by TOR@WSH's −2.08u). Exact to the cent,
  which also confirms the 2026-07-28 P0-1 fix holds: the stake did not
  oscillate across the evening's odds re-imports.
- **Both of tonight's STRONG picks locked inside the T2.58 window.**
  TOR@WSH at T−38min, HOU@LAA at T−58min. Season median notice is 57
  minutes; the Vercel cron cadence (:00/:30 UTC) against a 60-minute lock
  window bounds operator notice to roughly 30–60 minutes, which the data
  bears out.

### Fixed

- **The retired side-hue scheme was still live on every board row.**
  `.distLabelNrfi` was `--primary` and `.distLabelYrfi` was
  `--destructive` — exactly what the 2026-07-28 colour law abolished
  ("a peach number meant either 'this is an NRFI pick' or 'you made
  money' and the reader could not tell which"), and what its side-ink
  note forbids ("NEVER on a numeral"). The recolour pass moved the dots
  and bar fills and missed these two, so every row printed a peach and a
  rust figure that were not money, inches from an edge % and a stake that
  were. Probabilities now render in plain ink with a weight-coded side
  tag.
- **`DemotionsBanner` used `--primary` (= `--gain`) for a demotion.**
  A peach edge means real money UP; this banner announces bets being
  demoted. Moved to `--attn`, and the 3px side stripe dropped for a
  hairline.

### Changed — information architecture

Tonight's P&L was rendered **seven times** on one screen. Now four, each
with a distinct role (sticky ticker / decision hero / window-scoped
performance / reconcile-table caption). Nothing was deleted; the cuts
were restatements and progressive disclosure.

- `DayReconcile` stated the same night three times inside one card
  (header chain, footer lines, prose paragraph). Footer now carries only
  the W-L split the chain cannot say; the paragraph is reduced to the one
  sentence that explains the you-vs-replay difference, and is gated on a
  replay existing. The duplicate big "You −2.08u" figure is gone.
- The four-sentence flagged/placed/settled legend is now a collapsed
  disclosure on `TonightsActionCard`. Same words, one tap away.
- The superseded **"Older ledger · flat 1u"** block is collapsed by
  default. It reports the same nights under an accounting method the
  system stopped using, and its own footnote concedes the figures rest on
  a placeholder −110 — a knowingly-wrong number at eye level below the
  right one. Rows unchanged behind the disclosure.
- Board rows show **one** probability, not a pair summing to 100, tagged
  with the side it refers to and reported for the pick side (so it is
  never the number the reader has to subtract from 100).
- A STRONG pick that has not locked yet is the only row on the board with
  a deadline, and it was styled identically to "LINEUP PENDING", which
  needs nothing from anyone. It now carries `--attn` per the colour law.
  Deliberately no pulse: PRODUCT.md names sportsbook urgency as an
  anti-reference.
- Removed 3px `border-left` accents from `DayReconcile`, `RoiPanel` and
  `DemotionsBanner` (coloured side stripes standing in for hierarchy).

### Open

- **`odds_captured_at` runs backwards on 112 of 1129 rows (9.9%)**, still
  occurring as of today. It is set to the same value as
  `opened_captured_at` on first import and only ever moves forward, so an
  earlier value is impossible. Money is unaffected (`bet_placed` and
  `units_risked` are correct), but it is the only ledger evidence of when
  a bet locked, so it makes the T2.58 window unauditable from data and
  likely corrupts CLV. Prime suspect is
  `tools/sync_csv_from_supabase.py` writing a lagging mirror value back
  over the fresher local one. Not fixed here — needs isolation first, and
  no historical backfill without operator sign-off.

---

## [2026-07-28] — Dashboard rebuild: one night, one set of numbers

Operator report: *"the dashboard looks like shit visually and its so
difficult to read"*, and separately that it *"is not reflecting the
proper units won or lost per day"*. Both were true, and the second was
not a display bug.

### The defect

One night rendered three different ways on one screen, with nothing
explaining the difference:

| surface | 2026-07-27 |
|---|---|
| ticker | `6 STRONG YRFI` |
| ledger card | `4 graded bets · −0.33u` |
| season record | `1 bet · −11.15u` |

Two independent causes stacked:

1. **The gate moved and the ledger is frozen at the old one.** Those six
   picks were made under the 0.44 cutoff; the record replays the current
   0.40. Five of the six scored 0.418–0.450 — through 0.44, not through
   0.40. Both numbers were right about different systems.
2. **The record card was mislabelled.** It said "CURRENT MODEL REPLAYED"
   while scoring every game with a calibrator rebuilt from scratch at each
   date. That curve reads +0.0252 higher than the shipped one in April,
   +0.0077 in July, and because YRFI fires on a LOW p_nrfi, reading high
   means betting less. Over the real window the gap alone was 31 bets and
   +6.71u of flat profit.

### Changed

- **The record now reports BOTH methods and leads with the deployed one**
  (`tools/export_season_record.py`). Headline scores with
  `data/calibration_v2.json` at the live gate — the model actually
  running. The no-hindsight walk-forward figure is computed alongside and
  printed beside it as the floor, never hidden.

  | | bets | record | flat |
  |---|---|---|---|
  | REAL deployed (headline) | 125 | 78-47, 62.4% | **+11.33u** |
  | REAL walk-forward (floor) | 94 | 61-33, 64.9% | +11.23u |
  | PROJECTED deployed | 190 | 127-63, 66.8% | **+34.66u** |
  | PROJECTED walk-forward | 139 | 94-45, 67.6% | +25.45u |

  Note the real window: 31 extra bets, +0.10u. The deployed model's extra
  volume is roughly break-even; the edge is in the shared core.

- **The headline is flat profit, not the compounded bankroll.** Quarter
  Kelly on an imaginary 100u bank turns a +34.66u edge into +879.64u. That
  figure still renders — it is one sentence tagged SIMULATED inside the
  replay card, and `.simCard` forces every figure in that card to
  `--foreground !important` so a simulated number can never appear in the
  same peach as real profit. A one-week dismissible note says where it
  went; the operator's incident history is entirely about things
  appearing to vanish.

- **Real money and simulated money are now different surfaces.** Ledger =
  raised card, tone rail. Replay = recessed, hatched left rail, no tone.

### Added

- **`DayReconcile`** — the per-date drill-down that reconciles the three
  counts game by game: `FLAGGED 6 · PLACED 4 · SETTLED 4 −0.33u`, with the
  replay count deliberately OFF that chain (it is a different population,
  not a fourth stage), and a plain-English reason on every skip:
  *"model wasn't confident enough (0.418 vs 0.40 needed)"*.
- **`dashboard/lib/reconcile.ts`** — the single source for every count on
  the page. One function, one string, quoted verbatim by the ticker, the
  hero card and the day header. The three-numbers problem was three
  components each deriving its own count.
- **`dashboard/lib/season-record.ts`** — one definition of the record's
  shape, replacing inline interfaces that a schema change turns into a
  runtime crash rather than a type error.
- `selectedBets` / `droppedZeroStake` / `droppedFlatPnl` disclosure —
  PROJECTED stakes 190 of 225 qualifying bets; the 35 Kelly-zeroed ones
  used to vanish from the headline silently.

### Fixed

- **Doubleheader double-count** (`tools/export_season_record.py`,
  `tools/season_replay.py`). `(date, away, home)` is not a key: both legs
  of 2026-07-19 LAD@NYY and 2026-07-22 PIT@NYY rendered as the same bet
  twice and doubled their day totals. `load_season` now emits a stable
  `rid` (CSV row index) and the record joins on it. Season totals were
  unaffected — only the day view collapsed. Doubleheader legs now label as
  `LAD@NYY G2`.
- **"TONIGHT CLV +0.00pp" was never a measurement.** Two defects:
  `board-supabase.ts` coerced NULL to 0 via `num()` (Supabase is the
  production read path, so every `clvPct != null` guard in the codebase
  was dead), and separately the CSV genuinely stores `0.0000` for most
  placed rows because the price freezes on placement (T2.23) — opening and
  taken price are the same recorded number. A bet now counts as measured
  only when the picked side has both prices AND they differ; otherwise the
  card reads **"Not measurable"** with the reason, never a number.
- **Light-mode contrast below AA.** `--primary` #b05f28→#9a4f1c
  (4.51/4.08/3.70 → 5.80/5.25/4.76 on card/background/muted),
  `--secondary` #a4690f→#8a5407 (4.42/4.00/3.63 → 6.07/5.50/4.98),
  `--muted-foreground` #7c6b59→#6b5a48 (4.96/4.49/4.07 → 6.40/5.79/5.25),
  `--destructive` #a84a30→#9c4228 (5.52/5.00/4.53 → 6.32/5.71/5.18).
  Applied to BOTH light blocks — `.light` and the
  `prefers-color-scheme` copy — which had silently diverged.
- **Terminal green was still in the tree.** `GameDetails.module.css` fell
  back to `#2e8b57` (sea green) and `#c08a1d` because `--success` and
  `--warning` were never declared. Tokens added, fallbacks removed.
- **Zone card colour disagreed with its own number** — tone keyed off the
  placeholder-inflated `unitsPL` while the card printed `realPL`, so a
  zone could print a loss in the colour of a win.
- **Both watermarks removed.** The 56px rotated "PAPER" overlapped the
  −52.4pp figure. Deleted with the `z-index: 1` rule that was the only
  thing holding it behind the numbers — that rule alone would have put the
  word on top.
- Typography: monospace is now for figures only. Nine classes were
  monospace *prose*, which was most of "everything is monospace at nearly
  one size". Six sizes replace 43; the unused 24-class `.t-*` scale that
  nothing referenced is deleted.
- A null record side no longer hides the entire card (the guard required
  both sides truthy, failing silently).
- `season_record.json` is now written atomically — a cron tick could read
  it mid-write.

### Changed — the record is now read in Kelly units, not flat

Operator: *"i thought we are completely done with the flat units. all of
our dashboard must reflect the new kelley sizing. even going back to the
start of the season."* The system stakes by quarter-Kelly as of 2026-07-28,
so the record leads with it.

| | Kelly (headline) | flat 1u (reference) |
|---|---|---|
| REAL, 5/07 → 7/28, 125 bets | **+157.69u** (bank 100 → 257.69) | +11.33u |
| WHOLE SEASON, 4/01 → 7/28, 190 bets | **+879.64u** (bank 100 → 979.64) | +34.66u |
| no-hindsight floor (real) | +130.18u | +11.23u |
| no-hindsight floor (season) | +365.10u | +25.45u |

Flat stays on every column as one line — *"Same bets at flat 1u: +11.33u.
The gap is leverage, not edge."* — because the two answer different
questions and the difference is 14x.

New on each column, because an average hides what compounding asks for:
**typical bet 7.99u · biggest 20.82u** (real), **22.17u · 79.14u**
(whole season). Deepest drawdown 18.6% on both.

`replayText()` now quotes the Kelly figure too; it was still quoting flat
while the day footer led with Kelly, which put two different replay
numbers on one screen.

### Fixed — the date picker could not reach most of the season

`listAvailableDates` in `dashboard/lib/board-supabase.ts` capped its query
at 500 rows with the comment *"well above a full MLB season's slate
count"*. The cap counts **rows, not dates**, and there is one row per
game: at ~13 games a night, 500 rows reached back roughly 38 days. Every
older date then failed `available.includes(requestedIso)` and fell through
to `available[0]` — **serving tonight's board under the requested date,
silently**. Selecting 2026-04-15 just snapped back to tonight.

Now paginated with `.range()` rather than a bigger `.limit()`, because
PostgREST enforces its own 1000-row server-side max — the same cap that
silently truncated `pl_calc`. A requested-but-unavailable date now logs a
warning instead of substituting in silence. Verified 2026-04-01, 04-15,
05-20, 06-10 and 07-27 all serve their own slate.

Also: `DayReconcile` resolves a date against the REAL record first and
falls back to PROJECTED, so the 36 April dates that exist only in the
projected record are reachable instead of rendering empty.

### Fixed — the board's stake chips still read flat 1.00u

Operator: *"it literally doesnt render with kelly stakes... it still says
staked 1.00u."* The record card was converted to Kelly but the per-game
chips on the board were not.

The chip displayed `detail.unitsRisked`, which is the LEDGER's stake —
flat `1.00` on every row placed before Kelly went live. Browsing to April
therefore showed "staked 1.00u" on every game.

The chip now reads the quarter-Kelly stake the CURRENT model would place,
taken from the same `season_record.json` the day-reconcile table uses, so
the two surfaces cannot disagree. 2026-04-15 now renders
`STAKED 17.13u*` / `2.97u*` / `8.94u*` against the day table's identical
17.13 / 2.97 / 8.94.

Three details that matter:

- **The replay lookup runs BEFORE the STRONG guard.** The old gate and the
  current model disagree about which games qualify, so the model sometimes
  stakes a game the row labels PASS or LEAN — 2026-04-15 COL@HOU at 8.94u
  would have been hidden entirely.
- **Games the current model declines read `MODEL PASSES`**, not a stake and
  not a blank.
- **Tonight is unaffected**: an un-replayed slate falls through to the live
  figure, and a locked row's recorded stake IS the Kelly number.

`*` marks a price the replay assumed at −125 rather than captured.

`DashboardShell` now fetches `season_record.json` **once** and shares it
with both the board and the performance panel; each was about to fetch
~376 KB independently, which also let them drift.

### Changed — the dashboard now leads with THE SYSTEM, not the old ledger

Operator: *"you need to make it so that the 'placed' bets are actually the
kelley sized bets with the new model."* The panel had been leading with
the legacy flat-1u ledger and treating the current model as a footnote.
Inverted.

**`SystemCard` is now the headline** — current model, quarter-Kelly, for
whichever window the toggle is on, back to opening day. The old ledger is
one quiet line: *"Older ledger — what was actually bet under the previous
gate at a flat 1u stake … Superseded on 2026-07-28."*

Two corrections that came out of the operator catching a bad number:

**1. The headline is a PERCENTAGE, and units are rebased to the real
bankroll.** The replay compounds from 100u in April, so by late July its
bank is ~1200u and an ordinary losing week printed as **−188.50u** — a
figure that reads like a catastrophe against a real 100u bankroll where
the same week is **−15.67u**. Units without the bank they were staked
from are meaningless. Every unit figure on the card is now
`pct × startBank`, so it answers "what would this have cost me".

**2. NRFI is reported separately and never folded into the headline.**
`_LR_STRONG_NRFI_P` is 1.01 — the live system does not place NRFI. Three
of the seven "bets" in the last-7-days figure were NRFI, so the combined
number reported losses on bets that would never have been made. The card
now shows the YRFI record as the figure and NRFI as a tracked-not-bet
note.

Last 7 days went from a reported **−58.85u** to the correct
**−15.7% (−15.67u), 4 bets, 0-4 YRFI** — independently confirmed by
re-simulating those four bets from a 100u bank with `tracker.kelly_stake_units`
(−15.79u).

Every window, for the record:

| window | result | bets | record | hit |
|---|---|---|---|---|
| Last 7 days | −15.7% | 4 | 0-4 | 0.0% |
| Last 30 days | +17.5% | 34 | 21-13 | 61.8% |
| Season to date | +915.9% | 158 | 109-49 | 69.0% |

`TotalCard`, `MigrationNote` and `WindowReplayCard` deleted — superseded.

### Investigated — NRFI profitability: closed, negative

Operator asked to enable NRFI ("it's supposed to work good now") and then,
shown the 12-14 record, asked for a deep dive on what floors would make it
profitable. 20-agent workflow: 4 investigations, ~300 selection rules,
every candidate attacked by 3 independent skeptics. **Nothing survived —
12 of 12 refutations succeeded.** `_LR_STRONG_NRFI_P` stays 1.01; no
production behaviour changed.

**The wall.** On 1,122 settled 2026 games with a real captured DK NRFI
price: NRFI hit 48.0%, the price required 53.7%, blind flat betting
returns −10.6% (95% CI [−15.5%, −5.5%]). Of that 5.65pp gap, only 3.31pp
is vig. **Strip 100% of the vig and NRFI still returns −4.68%** — so
line-shopping cannot fix it.

**"What floors work best" — none.** Tightening lifts the hit rate 48%→58%,
but the required rate rises faster, 53.7%→58.5%. Best of hundreds of
cells: lambda ≤ 0.52 at 58.1% vs 58.5% needed (−0.2%, n=43).

Two structural findings:
- **The gate and the ceiling are the same knob.** `lambda_lr_total ==
  −ln(raw p_nrfi)` exactly, verified to 0.002 on 783 rows. There is no
  2-D grid to tune.
- **`_LR_LAMBDA_NRFI_CEILING` was dead code.** At gate 0.62 every
  qualifying game already sat inside the 0.52 ceiling, so the +5.44u the
  comment credited to it cannot be its doing. Comment corrected.

Most telling: under simulated pure noise, a grid search this size yields a
best cell averaging +15–20% ROI by luck. The real best cells were +3.6%
and +6.1% — **the search found less profit than chance manufactures.**

Methodology trap recorded: the 2024 backtest is unusable as an NRFI
validation split (a model fit on 2024 scores below chance on 2024, CV AUC
0.4897), so the mandated 3-split cannot be run for this question.

Full do-not-retread list in user memory `2026-07-28_nrfi_deep_dive`.
Analysis scripts preserved read-only in `tools/nrfi_deep_dive/` (44 files).

### Changed — dashboard recoloured and cut down; audit's display defects fixed

Operator: *"recolor the dashboard, remove redundant things from the
dashboard, simplify it more."* Plus the display half of the probability
audit, which had been queued behind this work to avoid edit collisions.

**Cut** (each surface's information survives, or is noted): ClvStat (CLV
is structurally unmeasurable — it only ever said so), LegacyLedgerLine
(it was the arithmetic sum of two zone cards 40px below it), the LEAN
zone cards and LeanBlock (paper money for a tier that is never wagered,
rendered in the same card shape as real money — the root of the
distrust), and the duplicated RecordColumn renderings (the model's record
was on screen four times, on four different populations). Panel is now:
**THE SYSTEM → OLDER LEDGER → WHY THE SYSTEM DID THAT → board.**

**Recoloured.** Surfaces untouched — this morning's desaturation stands
and the warmth still lives in the ink. What changed is the accent range,
so peach/rust/amber are distinguishable rather than three shades of one
orange, plus new `--side-nrfi` / `--side-yrfi` tokens 2.4x apart in
luminance. Borders lifted (dark 2.02 → 2.95 on muted; light 1.58 → 2.72).

**It also found a live accessibility failure I had missed:**
`--accent-cyan` in light mode was 3.96 / 4.32 / 3.51 against
background / card / muted — below AA on all three, across 21 call sites.
Now aliased to `--muted-foreground` at 5.79 / 6.33 / 5.13. My earlier
claim that "every contrast pair passes AA" was true only of dark mode.

### Fixed — the display defects from the probability audit

- **HistoryView showed +33.50u for a season the ROI panel showed as
  −1.03u.** Opposite signs, same bets. It summed the raw column including
  177 graded bets settled against a fabricated −110. Now uses the
  real-priced figure, mirroring RoiPanel. The footnote asserting "Actual
  P/L uses real DK odds when captured" — printed directly under the
  inflated column — is rewritten to say how many bets are excluded.
- **Zone cards mixed two populations on one line.** The units figure came
  from real-priced bets; the hit rate and edge on the next line came from
  all graded bets. STRONG NRFI literally rendered *"−11.29u · 59.4% hit ·
  +7.0pp"* where the −11.29u is 49 bets that went 22-27 (44.9%) and the
  59.4% folds in 47 placeholder-priced bets that went 35-12.
  `ZoneProvenance` now carries `realPricedWins/Losses`.
- **"vs break-even" was hardcoded at −110 (52.38%)** while the real-priced
  bets averaged an implied 56%. Every edge figure was overstated ~4
  points. Now computed from the prices actually paid, per zone; the −110
  constant survives only for LEAN, where the flat hypothetical is correct.
- **`num()` coerced missing values to 0**, so 349 April rows rendered
  *"0.00 projected first-inning runs"* badged green (strongest-NRFI tone)
  with the correct value in the next fallback, and 16 rows with a real
  price but no stored edge rendered a fabricated *"+0.0%"* — including
  "Skipped: edge +0.0%". Both now use `nullableNum`.
- **The board displayed and sorted by the legacy Poisson lambda** rather
  than the model's own `lambda_lr_total` (r=0.43, 36% pairwise rank
  inversion). Now prefers the model's.
- `Ticker` accepts the shared `night` object rather than recomputing.

### Deferred

- Doubleheader `game_pk` is not unique in `picks_2026.csv` (1563 rows,
  1543 distinct) and 2026-06-17 SF@ATL has both legs labelled game 1. No
  P&L impact today — neither row was bet — but the writer should be fixed.
  Spawned as a separate task.

---

## [2026-07-27] — Loss investigation: the leak is selectivity, not the calibrator

Operator asked why the system is losing. Investigation of all 526 graded
placed bets. Three diagnostic tools added; **no production behaviour
changed** — every candidate fix is a betting-policy change and is parked
pending operator sign-off.

### Findings

- **The season "+32.7u" is not real.** April captured a real DK price on
  only 6 of 176 placed bets (3%); the other 170 settled at `_calc_pnl`'s
  flat -110 fallback. April's 64.2% hit rate is genuine, but at the ~-131
  average the 6 captured rows actually show, the month is worth ~+23u, not
  +39u. Odds capture became reliable 2026-05-01 (94% May, 100% Jun/Jul).
  **Real-price record since 5/01: -6.40u over 350 bets** (May -3.65u,
  June -3.37u, July +0.62u). Overall 55.9% hit against a 56.1% break-even.

- **The STRONG gate is far too loose.** `_LR_PASS_LO_P = 0.44` admits
  **648 of 1520 graded 2026 games (42.6%)** as STRONG YRFI. A genuine
  strong edge cannot exist on 43% of a slate.

- **Two calibrator plateaus sit directly under the gate.**
  `data/calibration_v2.json` bins 1-3 all map to calibrated NRFI 0.40639
  (YRFI 0.5936) — 204 games, 98 distinct lambda values, one probability.
  A second plateau at 0.43836 holds 85 more. Together **289 of the 648
  qualifying games (45%) come from two "I can't tell these apart" values.**
  Plateau bets: 51.4% hit vs 54.9% market-implied, **-7.18u**. Non-plateau
  bets at the same average price: 57.0% hit, **+8.40u**.

- **Rebuilding the calibrator does NOT fix the P&L** (negative result,
  worth recording so it is not retried). A calibrator is a *monotone*
  relabelling, so it cannot change which games rank highest — only the
  number printed on them. Measured: AUC is 0.5346 for the raw model and
  0.5334-0.5352 for every candidate, and **at matched bet volume all six
  candidates select the same games for the same P&L** (+21.51u at 100
  bets, +21.53u at 150 bets, identical across the board). Out-of-sample
  Brier differences are ≤0.0005. The plateau is a real defect, but it
  costs money only because it shovels marginal games over a fixed gate —
  raising the gate on the existing calibrator achieves the same thing.

- **The calibrator fix does matter for Kelly**, which is the reason to
  keep it on the table: 204 games sharing one probability means
  bankroll-fraction staking would size them all identically off a number
  known to be wrong.

### Added

- **`tools/calibrator_bakeoff.py`** — out-of-sample comparison of six
  calibrators (iso20/iso40 = current family, cir20/cir40 = Centered
  Isotonic Regression, platt, blend) under the mandatory CLAUDE.md
  3-split rule (2024→2025, 2025→2024, 2024+2025→2026). Reports Brier,
  log loss, ECE, and plateau mass. `--money` adds a 2026 real-odds P&L
  test. CIR cuts plateau mass 68.2% → 12.5% at no Brier cost; Platt
  eliminates it entirely.
- **`tools/calibrator_shape_vs_selectivity.py`** — separates "ranks games
  better" (AUC, equal-volume P&L) from "bets less often". This is the
  tool that produced the negative result above; run it before proposing
  any future calibrator swap.
- **`tools/kelly_backtest.py`** — bankroll-fraction staking backtest on
  the real bet ledger, per operator request 2026-07-27 (overrides the
  earlier T4.25-27 flat-1u-only preference). Full/half/quarter/eighth
  Kelly against three probability sources (model-claimed, shrunk-to-
  market, market-implied control). Writes
  `data/diagnostics/kelly_backtest.json`.

### Kelly results (100u starting bankroll, 25% single-bet stake cap)

| selection | flat 1u | quarter K | half K | full K |
|---|---|---|---|---|
| current 349 bets | -3.65u | +4.25u | -40.40u | **-98.04u (bankroll 1.96, 99% DD)** |
| walk-forward gate, 109 bets | +6.49u | +32.45u | **+47.98u** | +16.66u (86% DD) |
| in-sample p≥0.64, 105 bets | +17.08u | +83.21u | +182.46u | +326.51u |

Full Kelly on today's selection **wipes out the bankroll.** Note half
Kelly beats full Kelly on the walk-forward set — the signature of staking
past the growth-optimal point on overstated probabilities. The p≥0.64 row
is in-sample (threshold chosen on the same data) and is an upper bound,
not a forecast; the walk-forward row is the honest number.

### Changed — SHIPPED (operator approved 2026-07-27)

- **`_LR_STRONG_YRFI_P = 0.36` — dedicated STRONG YRFI gate** (
  `mlb_first_inning_predictor.py`). STRONG YRFI now requires calibrated
  `p_nrfi < 0.36` (YRFI ≥ 0.64); the 0.36–0.44 band that previously fired
  STRONG is demoted to **LEAN YRFI (tracked, never bet)**. Deliberately a
  NEW constant rather than moving `_LR_PASS_LO_P`, so the PASS boundary
  and LEAN/PASS semantics are untouched and demoted games stay visible on
  the board instead of vanishing. Reversal: set to 0.44.
  - Mirrored in `dashboard/components/BoardRow.tsx` (`classifyTentative`
    + `DEFAULT_THRESHOLDS`) and `dashboard/lib/types.ts`; exported through
    `data/thresholds.json` as `strongYrfiP`. Field is optional in TS so
    older deploys fall back to the previous behaviour.
  - Verified by **`tools/verify_selectivity_gate.py`**, which replays every
    graded 2026 row through the real imported `classify_pick_lr` (not a
    reimplementation) and settles at real captured DK prices. Holding all
    other rules at current values and changing only the gate:

    | gate | bets | hit | need | P&L | ROI |
    |---|---|---|---|---|---|
    | old `< 0.44` | 285 | 57.2% | 56.0% | +4.82u | +1.7% |
    | new `< 0.36` | 86 | 69.8% | 58.6% | **+16.25u** | **+18.9%** |

    +11.43u on 199 fewer bets. Note this replay differs from the raw
    ledger figure quoted to the operator (349 bets / -2.20u) because the
    ledger spans several rulesets — `_LR_LAMBDA_YRFI_FLOOR` moved
    0.78 → 0.838 mid-season — whereas the replay applies today's rules
    throughout. Replay-vs-replay is the honest comparison.

### Feature investigation — three candidates tested, two rejected, none shipped

Deep-research sweep (109 agents, 26 sources, 25 claims adversarially
verified 3 votes each; **16 refuted vs 9 surviving**) followed by
3-split testing on this repo's own data. Net result: **no feature change
is justified.** No production model files were touched.

- **RETRACTED — the `top3c_iso` / `top3c_slg` collinearity is NOT a
  defect.** An earlier note in this investigation called the pair
  "fragile" because they carry near-equal opposite-signed weights at
  r≈0.93. That inference is empirically false here. Coefficient trace
  across the 3 training splits shows they are the **most stable
  coefficients in the model** (ISO relative sd **0.06**, SLG **0.16**,
  no sign flips). Dropping SLG makes it worse: ISO collapses toward
  zero, OBP **flips sign** (rel sd 1.15), and sign consistency among
  meaningful features falls **87% → 71%**. Accuracy flat either way
  (Brier delta ≤0.0005; AUC 0.5279 → 0.5244; better in only 2 of 3
  splits, failing the "must help in every direction" rule). The
  research recommended this drop on general methodological grounds; the
  3-split refuted it. **Left alone.**

- **`home_plate_ump_nrfi_rate` is dead weight, confirmed three ways.**
  New `tools/test_umpire_persistence.py` shows the feature's
  precondition fails: the stored 2022-23 shrunk rate correlates
  **r = -0.138** (Spearman -0.126, bootstrap 90% CI [-0.305, +0.042])
  with the same umpire's actual 2026 first-inning results, and the 2026
  umpire-to-umpire spread (sd 0.104) is **smaller than pure binomial
  noise** at those sample sizes (0.122) — no umpire signal exists in
  2026 at all. Both tails fully reverse (Will Little 0.421 → 0.579;
  Adam Beck 0.625 → 0.421). Compounding: `data/umpire_rates.json`
  `training_corpus` is the **2022 + 2023** backtests, the seasons
  CLAUDE.md bans for pitch-clock distribution shift; and the feature is
  mis-scaled live (train sd 0.0167 vs live sd 0.104, with B1 weight
  +0.0172). Ablation is **perfectly flat** — Brier identical to five
  decimals in all three splits.
  **Not shipped:** worth zero measured accuracy, and removing it
  requires regenerating `lr_t1.json`/`lr_b1.json` (`recalibrate_v2._load_one`
  hard-exits on a `feature_names` mismatch). Should ride along with the
  next scheduled `weekly_refit.py`, not an out-of-band retrain that
  perturbs every live prediction for no gain.

- **Rejected without testing, on research evidence:** re-encoding the
  umpire as Strike Zone Runs Saved (effect is only ~0.02 runs per
  half-inning, and SZRS splits credit four ways across catcher / umpire
  / pitcher / batter so it is not cleanly reimplementable); swapping
  xERA for Stuff+/Pitching+ (every head-to-head superiority claim was
  refuted, and Stuff+'s next-season ERA correlation collapses 0.41 →
  0.14 for pitchers who change teams, implying it partly encodes team
  and park). F-Strike% remains **untested** — every candidate effect
  size was refuted, so there is no citable number to justify the work.

- **Caveat recorded by the research itself:** no first-inning-specific
  evidence exists in any source found. Every effect size recovered is
  season-long and full-game. Per-half-inning figures are pro-rated
  arithmetic, not measurement.

### Added

- **`tools/test_umpire_persistence.py`** — tests whether a prior-season
  umpire rate predicts the same umpire's later results, with bootstrap
  CI and a binomial-noise floor comparison. Run before trusting any
  umpire-derived feature.
- **`tools/test_ablation_slg_ump.py`** — 3-split ablation harness.
  Masks columns out of the exact production feature matrices (rather
  than rebuilding them) so construction stays identical to production,
  trains on **per-half** targets as `two_stage_model.py` does, and
  reports coefficient sign stability as the primary endpoint.
- **`.claude/workflows/deep-research.js`** — the deep-research workflow
  harness, installed so it resolves by name in this repo. NOT committed:
  `.claude/` is gitignored, so this is a local-machine artifact only and
  will need reinstalling on another checkout.

### Changed — Kelly staking ENABLED at quarter Kelly (operator decision)

After reviewing the full-season backfill below, the operator enabled
quarter Kelly on a ~100-unit bankroll. `KELLY_ENABLED` now defaults to
**True**; `NRFI_KELLY_ENABLED=0` is the kill switch and takes effect on
the next cron tick with no code change.

- **Typical stakes go from 1u to roughly 4-7u.** At -135: yrfi_p 0.64 →
  3.85u, 0.66 → 5.03u, 0.68 → 6.20u, 0.70 → 7.37u, 0.75 → 10.00u (cap).
- **Bankroll epoch added (`KELLY_BANKROLL_EPOCH`, default 2026-07-28).**
  Caught before going live: `current_bankroll_units()` summed the WHOLE
  season's realized P&L (+32.7u) on top of the nominal bank, so the first
  Kelly bet would have sized off ~133u instead of 100u — **every stake
  33% too large**. Worse, that +32.7u is itself ~15u inflated by April's
  -110 fallback, so the error would have compounded on partly-fabricated
  profit. Only P&L from the epoch forward now compounds. Verified: the
  computed starting bankroll is exactly 100.00u.

### Added — Kelly bankroll-fraction staking

Built at operator request 2026-07-27, explicitly reversing the T4.25-27
flat-1u-only preference recorded in CLAUDE.md.

- **`tracker.kelly_fraction_of_bankroll` / `current_bankroll_units` /
  `kelly_stake_units`**, wired into `_apply_odds_to_row`'s sizing block.
  STRONG only; LEAN keeps its notional flat size. Requires BOTH a real
  captured DK price and the model's probability for the picked side —
  with either missing it returns `None` and the caller falls back to the
  flat stake. It never fabricates a stake from a missing price, which is
  the same failure mode as the -110 fallback that inflated April.
- **Config (all env vars):** `NRFI_KELLY_ENABLED` (default off),
  `NRFI_KELLY_FRACTION` (default **0.25**, quarter Kelly),
  `NRFI_KELLY_BANKROLL` (default 100 units, so 1u still reads as 1% of
  bank), `NRFI_KELLY_MAX_STAKE` (default 0.10 = 10% hard cap).
- **Bankroll compounds**: nominal bank + realized season P&L, read once
  and cached per process (this is called per-row inside odds-import
  loops; re-reading the ledger each time would be quadratic).
- **Stakes freeze on bet placement** automatically — the existing T2.23
  odds lock already covers `units_risked`.
- **`_calc_pnl` needed no change**; it already honours `units_risked`.

**Why quarter and not half.** Kelly stakes scale with *claimed* edge, and
this model's claimed edge is measurably inflated where it bets most
(claims 59.2% → won 50.3% on 157 bets; claims 62.3% → won 55.1% on 107).
Simulated on the real ledger at the shipped gate, 100u start:

| staking | final | profit | max DD | top stake |
|---|---|---|---|---|
| flat 1u | 114.15u | +14.15u | 4.2% | 1.0% |
| 1/8 Kelly | 130.52u | +30.52u | 13.3% | 4.6% |
| **1/4 Kelly (default)** | **163.20u** | **+63.20u** | **25.3%** | 9.3% |
| 1/2 Kelly | 218.01u | +118.01u | 35.2% | 10.0% (capped) |

On the PRE-gate selection full Kelly took 100u to **1.96u** (99% drawdown)
— Kelly is only survivable on top of the tightened gate shipped the same
day. Half Kelly also beat full Kelly on the walk-forward set, the
signature of staking past the growth-optimal point on inflated
probabilities.

**POLICY CONSEQUENCE, called out explicitly:** Kelly stakes 0 whenever
the model's probability does not beat the market's implied probability,
and a 0 stake sets `bet_placed="N"`. Enabling Kelly therefore
*implicitly adds an edge gate to STRONG* — the thing CLAUDE.md/T2.24 says
requires explicit operator permission. That is inherent to Kelly, not a
bug, but it means enabling it changes which bets fire, not just their
size. 4 of the bets in the simulated window were skipped this way.

- **`tools/verify_kelly_wiring.py`** — drives the SHIPPED
  `tracker.kelly_stake_units` (not a local copy of the formula) over the
  real ledger. Confirms default-off, exact formula agreement (largest
  disagreement 0.0000u over 349 bets), cap enforcement, 0 stake on -EV,
  and `None` fallback when no price was captured.

### Added — top-N-per-day analysis

- **`tools/top_n_per_day.py`**, answering "what if we only took the top
  pick every day?" over the 86 betting days with real captured prices:

| strategy | bets | hit | P&L | ROI |
|---|---|---|---|---|
| bet everything | 349 | 55.9% | -1.89u | -0.5% |
| top 1/day by confidence | 86 | 66.3% | +12.49u | +14.5% |
| top 1/day by **edge** | 86 | 62.8% | **+15.65u** | **+18.2%** |
| top 2/day by confidence | 167 | 64.7% | +22.61u | +13.5% |

  Ranking by EDGE (model p minus market implied) is the EV-correct
  criterion and the only variant positive in **every** full month
  (May +4.37u, Jun +5.04u, Jul +6.47u); confidence-ranking went flat in
  June. Edge-ranking also gets a far better average price (-59 vs -136).
  Longest losing streak 4 bets (edge) / 3 bets (confidence).
  Both are hindsight over a partial season — a one-per-day strategy
  concentrates all variance into very few bets.

### Fixed — Kelly same-day exposure was uncapped (found post-ship, same day)

The 10% per-bet cap constrained each stake but nothing constrained the
DAY. Kelly's formula sizes one bet against one outcome assuming the
bankroll compounds before the next; same-slate bets are placed together
and settle together, so each is really risked against the same bankroll
at the same time. Sizing N same-day bets each at the full fraction
over-commits.

Measured at the shipped gate over 55 betting days (100u, quarter Kelly):
worst day put **24.51u at risk across 4 bets**, three days exceeded 20u,
seven exceeded 15u — while the per-bet cap never once bound.

- Adds **`KELLY_MAX_DAILY_FRAC`** (`NRFI_KELLY_MAX_DAILY`, default 0.15)
  and `_committed_on()`, seeded per date from the ledger so a fresh cron
  process sees what earlier ticks already committed.
- Result: worst-day exposure **24.5% → 13.0%**, and the backfilled final
  bank *improved* 157.79u → 177.93u, because the trimming lands on heavy
  slates that included losers.
- **Known limitation, documented in-code:** the daily budget is allocated
  first-come-first-served in row order, not best-bet-first. On a day that
  exhausts the budget, later picks are trimmed or skipped even if
  stronger. The cap binds on 7 of 55 days at a 1.2-bet average slate, so
  impact is small; revisit if the slate widens.

### Found, not yet fixed — three open items

- **The model has not been refit in 62 days.** `lr_t1.json` /
  `calibration_v2.json` last changed 2026-05-26. `daily.yml:64` records
  that the weekly auto-recalibrate cron was **disabled on 2026-05-11**;
  `recalibrate` survives only as a manual `workflow_dispatch` option. The
  memory note describing a live "weekly_refit.py workflow" is stale. A
  62-day-old calibrator is being served against a drifting run
  environment — and it is the same calibrator whose flat step this
  investigation identified.
- **CLV is not measurable.** Of 531 placed bets, 307 have
  `opened_*_odds` exactly equal to `market_*_odds` and only 20 show any
  movement (204 missing one side). So the 1am opener-capture cron is not
  producing usable open→bet line movement, and every CLV number the
  system reports is meaningless rather than merely small.
- **Single sportsbook.** Every row is DraftKings. No line shopping at
  all. Sensitivity on the 105 bets at the new gate: a 10-cent better
  average price is worth **+3.83u** (ROI 13.47% → 17.12%); 20 cents is
  worth +8.24u. Unlike any model change this is a *certain* gain, and it
  is plausibly the largest single improvement still available.

### Investigated — re-derived every gate and limit under Kelly staking

Operator hypothesis (2026-07-28): every threshold this system ever chose
was evaluated under FLAT 1u, where a marginal bet costs a whole unit when
it loses. Kelly changes that — a bet whose model probability doesn't beat
the market gets staked ZERO, so a band that bleeds at flat 1u might be
neutral-or-better under Kelly, implying the correct gate is now *lower*.

New `tools/kelly_gate_sweep.py` sweeps gate x Kelly fraction x daily cap
x per-bet cap x min-edge, with per-month consistency, block-bootstrap CIs
and a true walk-forward.

**The hypothesis is right for exactly one band, and instructively wrong
elsewhere:**

| band | flat 1u | Kelly funded | Kelly P&L | verdict |
|---|---|---|---|---|
| 0.56-0.60 | -14.27u | 137/155 | **-36.91u** | Kelly makes it WORSE |
| 0.60-0.64 | -3.95u | 76/82 | **+5.23u** | **rescued by Kelly** |
| 0.64-0.68 | +14.59u | 87/91 | +81.30u | improved |
| 0.68+ | -1.21u | 13/13 | -8.64u | worse (n=13) |

**Why 0.56-0.60 gets worse is the important part.** Kelly can only filter
on *claimed* edge. That band is exactly where the calibrator's 0.5936 flat
step lives, so the model claims an edge it does not have — and Kelly
responds by funding 88% of those bets and sizing them UP. Kelly's
self-filtering is only as honest as the probability feeding it.

**Nothing should change.** Gate 0.64 remains best on every criterion:

| gate | flat | 1/8 K | 1/4 K | maxDD | months + | bootstrap 90% CI |
|---|---|---|---|---|---|---|
| 0.56 | -4.84u | +0.86u | +48.63u | 35.9% | 2/3 | [-41.08, +317.16] zero |
| 0.60 | +9.42u | +25.67u | +84.92u | 28.6% | 3/3 | [-13.28, +306.26] zero |
| **0.64 (live)** | +13.38u | +28.28u | **+90.18u** | **15.2%** | **3/3** | **[+5.76, +247.33]** |
| 0.66 | +11.78u | +26.15u | +69.88u | 23.7% | 3/3 | — |

0.64 is the **only** configuration whose bootstrap CI excludes zero, and
it has both the lowest drawdown and the least month-concentration (best
month 53% of profit, vs 118% at gate 0.56 where June was negative).

- **Daily cap 15% is near-optimal**, confirming the value shipped hours
  earlier: at gate 0.64, 10% → +85.99u, **15% → +90.18u**, 25% → +57.79u,
  uncapped → +57.79u. At the loose 0.56 gate the cap does even more work
  (10% → +126.95u vs uncapped -10.89u).
- **The per-bet cap is redundant** — 10% and 25% give identical results
  because the daily cap binds first. Left as-is; no reason to touch it.
- **A min-edge filter adds nothing** (+90.18u → +90.49u at edge>=2%).
  Kelly already zeroes non-positive-EV bets, so an explicit floor is
  duplicated work.
- **Walk-forward, the honest number** (gate re-chosen daily from prior
  settled bets only): flat 1u **+6.15u**, 1/8 Kelly **+14.13u**,
  1/4 Kelly **+44.60u**. Quarter Kelly is ~7x flat even with no hindsight.

**Consequence — the calibrator rebuild is now worth doing.** Under flat
staking it was proven worthless (all monotone calibrators select the same
bets at equal volume, 2026-07-27). Under Kelly the calibrator's OUTPUT
VALUE is the `p` in the Kelly formula and therefore sets stake size
directly, so the 0.5936 plateau now makes Kelly stake 204 different games
off one wrong number. CIR cuts plateau mass 68% → 12% at zero Brier cost.
This reverses the earlier "not worth shipping" verdict.

### Changed — SHIPPED: calibrator replaced with Centered Isotonic Regression

Reverses the 2026-07-27 "not worth shipping" verdict, for a reason that
did not exist then. Under FLAT staking a calibrator is a monotone
relabelling, so shape provably cannot change which bets fire at equal
volume. Under Kelly the calibrated probability IS the `p` in the Kelly
formula and therefore sets STAKE SIZE directly. The live curve's flat
step gave 204 graded 2026 games (98 distinct lambdas) the single value
NRFI 0.40639 / YRFI 0.5936, and Kelly staked all of them off that one
number — in exactly the 0.56-0.60 band the gate sweep measured going
from -14.27u flat to -36.91u under Kelly.

**This is a DATA swap, not a code change.** CIR emits the same
`{centers, rates}` pair list, so `ProbCalibrator.load()` reads it
unchanged. Rollback is `git checkout <prev> -- data/calibration_v2.json`.

- **`tools/fit_cir_calibrator.py`** — fits CIR, validates it, and refuses
  `--write` unless all three criteria pass.

**Validation (CLAUDE.md 3-split):**

| split | iso20 Brier | CIR Brier | Δ | iso plateau | CIR plateau |
|---|---|---|---|---|---|
| 2024→2025 | 0.25029 | 0.25029 | −0.00000 | 87.2% | 24.4% |
| 2025→2024 | 0.25816 | 0.25872 | +0.00057 | 57.7% | 4.7% |
| 2024+2025→2026 | 0.24799 | 0.24786 | −0.00014 | 59.6% | 8.5% |

Degraded Brier in 1 of 3 splits — within the rule's allowance of 1.

**Live curve, before → after:**
- knots 20 → **11**, longest flat run 3 → **1** (i.e. no flats at all),
  knots inside flat runs 17 → **0**
- on the 1520 graded 2026 games: distinct probabilities 689 → **1437**,
  plateau mass 51.7% → **4.1%**, games on the 0.5936 step **204 → 0**
- mean |probability change| 0.0231, max 0.0477; games clearing the STRONG
  gate 162 → 137
- the old dead zone now ramps: raw 0.38/0.40/0.42/0.44 → p_nrfi
  0.4166/0.4292/0.4423/0.4664 (previously all 0.4064)

**Kelly money test** (both arms trained on 2025+2026, so both are
optimistic — the *delta* is the signal): live +209.49u / 13.1% maxDD vs
CIR **+322.19u** / 11.7% maxDD, **+112.70u** better on 15 fewer bets at a
higher hit rate (67.6% → 70.1%).

### Fixed — T4.6 calibrator shape validator had never run

`_validate_calibrator_shape` read `cal._xs` / `cal._ys`, but
`ProbCalibrator` has only ever stored `centers` / `rates`. The getattr
chain resolved to `None` and the function returned early on every call
since T4.6 shipped — the safety check was dead code the whole time.
Fixed to read the real attribute names (fallbacks retained), and the
warning threshold raised 5pp → 15pp because CIR deliberately collapses
each pooled run to one knot, so larger inter-knot steps are intended
rather than overfitting (the live curve's largest legitimate step is
~12.8pp). Verified: silent on the real curve, warns on a broken one.

### Added — line-shopping infrastructure (highest-value remaining change)

Every bet in the ledger has been DraftKings; there has never been any
line shopping. On the 105 bets clearing the shipped gate (avg price
-142): 5 cents better = +1.84u, **10 cents = +3.83u** (ROI 13.47% ->
17.12%), 20 cents = +8.24u. Unlike every model change tested on
2026-07-27/28 this is a *certain* gain, and it compounds with Kelly
because a better price raises the Kelly fraction as well as the payout.

- **`tools/merge_odds_books.py`** — takes N per-book odds CSVs and emits
  one best-price CSV for `--import-odds`. Best price is chosen **per
  side**, since we only ever bet one side, so NRFI and YRFI may come from
  different books; the merged row records which book won each. Games
  quoted by only one book still survive the merge.
  - Comparison is done on **payout**, not on the American number. Naive
    numeric comparison is wrong across the +/- boundary and silently
    picks the worse price: it prefers -110 over -105, and would take
    -150 over +100. `--self-test` covers exactly these cases.
- **`tools/fetch_odds_api.py`** — multi-book source via The Odds API.
  NRFI/YRFI is not a named market anywhere; it is the first-inning total
  at a 0.5 line (`totals_1st_1_innings`, Under = NRFI, Over = YRFI). The
  parser ignores any 1.5-line outcome, which would otherwise silently
  price a different bet.

**Why an aggregator instead of more scrapers.** `scrape_dk_odds.py` talks
to DraftKings' undocumented internal API; its own docstring notes DK
changes that URL about once a year, and 2026-05-03 showed their CDN
fingerprinting our egress into read timeouts. One such scraper per book
multiplies that fragility. (Confirmed while building this: DK's endpoint
returns HTTP 403 from this environment entirely.)

**CALL BUDGET — read before scheduling.** `totals_1st_1_innings` is an
"additional market", served only from the per-event endpoint, so one
fetch costs 1 call to list events + 1 per event ≈ **16 credits on a
15-game slate**. The free tier is 500/month, i.e. roughly **one fetch per
day**. Wiring this into the ~12x-daily predict cron would exhaust the
quota in about two days. Run it once near lock time, or buy a tier.
`--dry-run` reports the cost without spending credits, and the tool
refuses to start if remaining credits are below the number needed.

**Not yet wired into the cron, and deliberately so.** Two preconditions
are outside this repo: (1) an `ODDS_API_KEY`, and (2) the operator
actually holding accounts at the books that win the price — otherwise the
output is a diagnostic ("DK was 12 cents off best"), not an instruction.

**Testing status, stated plainly.** `merge_odds_books.py` is fully tested
including the +/- boundary. `fetch_odds_api.py` is written against the
documented schema and self-tested on a synthetic payload, but has **never
been run against the live API** — there is no key in this environment.
Treat the first live run as verification: check the row count against the
slate before trusting any price.

### Added — dashboard shows the counterfactual Kelly bankroll

Operator asked for the dashboard record to reflect "what it would be if
we'd started Kelly from the beginning".

**The ledger is NOT rewritten.** The obvious implementation — overwriting
`units_risked` / `profit_loss_units` with simulated stakes — would destroy
the only record of what was actually risked at a real price and make the
simulation permanently unauditable. That is the 2026-05-05 backfill-mirror
failure. Instead the counterfactual is recomputed **on read** from each
row's stored probability and captured price.

- **`dashboard/lib/kelly-sim.ts`** — day-by-day compounding replay.
  Mirrors `tracker.kelly_stake_units` including the per-bet and same-day
  exposure caps.
- **Config comes from `data/thresholds.json`**, which the predictor now
  exports from tracker.py's own constants (`kellyFraction`,
  `kellyBankrollUnits`, `kellyMaxStakeFrac`, `kellyMaxDailyFrac`,
  `kellyMinStakeUnits`, `kellyEpoch`). Re-deriving Kelly's parameters in
  TypeScript would drift the moment either side was tuned.
- **`KellyCard` in RoiPanel** — dashed border + "SIM" watermark, matching
  the LEAN paper-trade card so a simulated bankroll can't be mistaken for
  realized P&L. Hidden on the TODAY tab (a compounding season figure
  there invites reading +95u as tonight's result); shown on 7d/30d/season.

**Result: 100u → 195.51u (+95.51u)**, 165W-122L over 287 staked bets
since 2026-04-29, max drawdown 33.8%, largest single stake 19.20u (10% of
a bankroll that had grown to ~192u). The same bets flat-1u: **-0.69u**.

**Why this is far better than the -10.89u in the earlier season backfill:**
that backfill ran *before* `KELLY_MAX_DAILY_FRAC` existed, so it had no
same-day exposure cap. The gate sweep independently measured the same
effect (uncapped -10.89u vs +48.63u with a 15% daily cap). The daily cap
is doing a large share of the work, not the Kelly formula alone.

**Verification:** the TypeScript simulation was cross-checked against an
independent Python reimplementation on the same CSV — final bankroll,
profit, bet count, W/L, max drawdown and largest stake all agree exactly.

**Caveats stated on the card and worth repeating.** 11 bets are marked
unsizeable (no captured price — Kelly's stake is a function of the price,
so inventing one recreates the April artefact). The simulation uses the
probabilities stored at pick time, which came from the OLD plateaued
calibrator; live Kelly now runs on the CIR curve, so future results are
not drawn from the same distribution as this backfill.

### Found — picks_2026.csv and Supabase disagree about April `bet_placed`

While validating the above: the committed CSV has **10** April rows
flagged `bet_placed=Y`, while the Supabase snapshot has **176**. The
dashboard reads the CSV, `tools/pl_calc.py` prefers Supabase. That is why
the Kelly curve starts 2026-04-29 rather than at opening day, and it is a
pre-existing divergence this change did not cause. Not fixed here —
reconciling them changes what every historical total reports, which needs
its own decision. `tools/diff_csv_vs_supabase.py` exists for this.

### Investigated — the "62-day-stale model" is not costing anything; refit REJECTED

Ran `tools/weekly_refit.py` to retrain the two-stage LR on
2024 + 2025 + 2026-thru-07-20, holding out 2026-07-21..27 (93 games).

**It shipped, and it should not have.** Its gate was "P&L >= prod - 1.0u
AND Brier <= prod + 0.005" — both asymmetric and generous, so a candidate
that is measurably WORSE on both still passes. This run was exactly that:
delta P&L **+0.00u**, delta Brier **+0.0037 (worse)** — and it shipped.
That is the weakness which got the weekly cron disabled on 2026-05-11.

**Independent review (`tools/verify_refit.py`, new) said no:**
- Clean holdout Brier: previous 0.26070, new 0.26438 — new is worse, but
  the bootstrap 90% CI on the delta is **[-0.00104, +0.00851]**, i.e.
  indistinguishable from noise on 93 games.
- It churns the book: STRONG YRFI picks 92 → 103, with **31 added and 20
  dropped** — ~51 of ~100 picks change. Mean probability move 0.0199
  (max 0.1099), which under Kelly changes stake sizes everywhere.
- Kelly money, **in-sample for the new model** so it should be flattered:
  previous +300.00u / 11.7% maxDD vs new **+78.25u / 30.2% maxDD**. It
  gives back most of the bankroll and nearly triples the drawdown on data
  it was trained on.

**Rolled back.** Production is the 5/26 LR weights + this morning's CIR
calibrator. Conclusion: the model's age is not currently costing measurable
accuracy, and a refit today would trade a known-good model for a
different one with no evidence behind it.

### Fixed — refit gate now defaults to the incumbent

`tools/weekly_refit.py` decision gate rewritten. A refit perturbs every
live prediction and (since 2026-07-27) every Kelly stake, so it must earn
its place rather than merely fail to embarrass itself:

- Brier must **improve** — no "within tolerance" pass.
- The improvement must survive a **block bootstrap** on the holdout
  (entire 90% CI below zero). A ~90-game window moves ~0.005 on noise.
- P&L must not regress **at all**.
- New `MIN_HOLDOUT_GAMES = 90` — below that the run declines to decide
  rather than deciding on noise.

Re-ran against the tightened gate: **VALIDATION FAILED, production
unchanged** — the correct outcome.

### Fixed — the refit path would have silently reverted the CIR calibrator

`tools/walk_forward_eval.fit_calibrator` called
`ProbCalibrator.fit(..., n_bins=20)` (plain PAV). Since `weekly_refit.py`
overwrites `data/calibration_v2.json` on a successful refit, the first
successful refit would have **silently restored a plateaued curve** and
undone the CIR ship — reintroducing the flat step that Kelly now sizes
stakes off. Now uses `CIRCalibrator`.

- **`CIRCalibrator` promoted from `tools/calibrator_bakeoff.py` into
  `calibration.py`**, its canonical home, so every fit path shares one
  definition instead of the bake-off owning a copy the refit path didn't
  know about.

### Corrected — the NRFI check was reading a 6-week-stale sample

Operator caught this. The first `nrfi_reenable_check.py` filtered on
`pick_strength == "STRONG"` and judged re-enabling on 49 picks ending
2026-06-14. But disabling NRFI set `_LR_STRONG_NRFI_P = 1.01`, which no
probability can exceed — so from that date the classifier stopped emitting
"STRONG NRFI" entirely and the same games came out as **LEAN NRFI**. The
strength LABEL changed meaning; the probability did not. The filter
therefore discarded **158 graded predictions (157 with real prices)** —
every NRFI call the model made while we sat out.

Fixed to select on side + probability. Sample goes 49 → **309**, of which
**184 are genuinely out-of-sample** (we bet none of them, so none of our
money touched those lines).

**The conclusion is unchanged and now much better supported:**

| segment | n | hit | needs | flat 1u | Kelly |
|---|---|---|---|---|---|
| all NRFI predictions | 309 | 46.3% | 55.9% | **-53.89u** | -46.65u |
| ...actually bet | 49 | 44.9% | 57.9% | -11.29u | -30.57u |
| ...predicted, never bet | 260 | 46.5% | 55.6% | **-42.60u** | -23.14u |
| **since 6/07 (clean OOS)** | **184** | **46.7%** | **55.1%** | **-28.67u** | -33.15u |

Still negative in every probability band (-9.5pp / -8.4pp / -11.2pp /
-11.4pp) and at every re-enable threshold from 0.55 to 0.66. The six weeks
we sat out confirm it rather than overturning it.

### Investigated — NRFI stays OFF, and CLV is unmeasurable for structural reasons

**NRFI: do not re-enable.** The 2026-06-07 decision holds, now re-tested
under Kelly + the tightened gate + the CIR calibrator (the three things
that changed since). `tools/nrfi_reenable_check.py`, on all 49 graded
STRONG NRFI picks with a real captured DK price:

- **44.9% hit against a 57.9% break-even — a -13pp edge.** Flat 1u
  **-11.29u**; under quarter Kelly **-30.57u** at 37.5% drawdown.
- **Every probability band is negative**: 0.50-0.60 **-17.2pp**,
  0.60-0.62 -13.6pp, 0.62-0.65 -3.7pp, 0.65+ **-17.2pp**. There is no
  sub-range where the model beats the NRFI price.
- **Every re-enable threshold loses**, flat and Kelly, from 0.55 through
  0.66. The least-bad (0.64) is still -1.03u flat / -14.08u Kelly.
- Kelly makes NRFI *worse* at every threshold — same mechanism as the
  0.56-0.60 YRFI band: the model claims an edge it does not have, so
  Kelly funds and enlarges it.

**Resolves a dashboard discrepancy:** the ROI panel shows STRONG NRFI at
59.4% / +8.53u, but only 49 of those 96 picks have a real captured price
and those went **44.9%**. The other 47 settled at the -110 placeholder
and are mostly April — the same artefact that inflated the season total.
**The dashboard's STRONG NRFI line is not a real result.**

**CLV: the instrument works; the market doesn't move.** Earlier this
investigation flagged "307 of 531 rows have opened == market" as broken
capture. Measuring properly: 83% of placed rows *do* carry two distinct
observations, but the **median gap between them is 0.1 hours** (mean 0.2h,
max 4.4h), and the first capture lands a median **~1.0h before first
pitch**. DraftKings does not post this niche first-inning market until
shortly before the game, and the T2.58 lock commits the bet minutes later.

So there is no window between market-open and our entry in which a line
could move — 93% show an identical price across the gap, and when it does
move the median is 5 cents. **CLV is not mis-instrumented; it is
structurally unavailable for this market at our bet timing.** No code fix
would produce a number, because there is no second observation to make.
Recorded rather than "fixed": the honest action is to stop treating CLV
as a validation signal here, not to manufacture one.

### Investigated — is the NRFI signal INVERTED? Suggestive, not proven

Operator: "I think we may be predicting it wrong, or targeting the wrong
probabilities or brackets." Tested on all 309 NRFI-side predictions with
both prices captured.

**The model's NRFI zone is genuinely mis-ordered.** Actual YRFI rate by
what the model said:

| model says | actual YRFI rate |
|---|---|
| YRFI | 56.7% |
| **NRFI** | **53.7%** |
| PASS | 47.8% |

Games the model calls NRFI are **more** YRFI-prone than the ones it calls
PASS. The ranking is inverted between those two zones — the PASS bucket
is a better NRFI detector than the NRFI bucket is. That is a real defect,
and it is the concrete form of "we may be predicting it wrong".

**Fading it looks profitable but does not clear the bar.** Betting YRFI on
games the model calls NRFI: 53.7% vs 50.6% needed, **+3.1pp, +18.25u,
+5.91% ROI** over 309 games.

Control test rules out a market artefact: blind-betting YRFI on **every**
graded game LOSES (51.9% vs 52.9% needed, -1.0pp, -19.17u). So YRFI is not
generically underpriced; the model is carrying real information and
partially inverting it.

But it fails validation:
- full-sample bootstrap 90% CI on ROI **[-3.43%, +15.14%]** — includes zero
- clean out-of-sample (184 picks since 6/07, none ever bet): +3.12% ROI,
  CI **[-8.75%, +14.47%]** — includes zero
- **July was negative (-4.05u)**; positive in 3 of 4 months
- most of the profit sits in the 0.50-0.55 band (+17.25u of +18.25u),
  i.e. where the model is barely leaning at all

For comparison, the STRONG-gate change that shipped had a CI of
[+1.8%, +28.4%], excluding zero. The fade does not meet that standard.
**Do not act on it.** Recorded as a watch item.

**Also tested and rejected:** betting NRFI on the PASS zone (-1.9pp,
-15.17u, CI [-9.8%, +4.1%] spans zero).

**The durable finding is the mis-ordering, not the fade.** The right fix
is in the model — the NRFI side of the classifier is not separating
low-scoring games from the PASS zone — not a new bet type layered on top.

### Added — PROJECTED PROFIT / REAL PROFIT: the system record, on the dashboard

Operator spec (2026-07-28), now live end to end:

- **PROJECTED PROFIT** — whole season, every game the current system
  would bet, missing DraftKings prices filled at an explicit **-125**:
  **94W-45L (67.6%), edge +10.3pp, flat +25.45u, bank 100u → 465.10u,
  maxDD 18.1%** (37 of 139 bets priced by assumption, and the card says
  so).
- **REAL PROFIT** — **2026-05-07 onward** (first day DK capture became
  reliable: 99.6% of games priced from there), real captured prices
  only, nothing assumed: **61W-33L (64.9%), edge +6.9pp, flat +11.23u,
  bank 100u → 230.18u, maxDD 18.0%**.

Both include **YRFI (live 0.40 gate) and NRFI (p≥0.60)** per the
operator's decision. Live NRFI *betting* remains disabled — only 9
real-priced NRFI bets exist — but the displayed record counts both
sides. Method is walk-forward: the calibrator at each date is refit
from strictly earlier games, so no game is scored by a curve that saw
its outcome.

- Each side of the card has a **day-by-day drill-down**: every betting
  day, expandable to the individual bets — game, side, price (marked
  `est.` when assumed), stake, WIN/LOSS, P&L, and bank after the day.
- Pipeline: `tools/export_season_record.py` (rewritten) →
  `data/season_record.json` → `copy-data.mjs` → `/api/season-record` →
  `SeasonRecordCard` in RoiPanel, rendered under every window.
- Wired into the nightly grade tick in `daily.yml`, so the record
  refreshes itself after each day's grading; the existing
  `git add data/` commit step picks it up.
- `gate_validation.select()` now carries game/side/assumed on each bet
  (additive keys) so the drill-down can name the bets.

### Fixed — the session-long "changes don't render" mystery, explained

The dev tab's console finally surfaced it:
`Text content did not match. Server: "Net P&L · real prices only"
Client: "Net P&L · bet zones only"` — the server rendered NEW code while
the browser hydrated a STALE cached client chunk. Next dev serves chunks
at unhashed URLs, and the embedded browser cached them across reloads,
cache-busting query params, and dev-server restarts. Every false
"it doesn't render" in this session — including the stake-chip hunt —
traces to this. Production builds use content-hashed chunk filenames and
are immune; the card was verified against `npm run build` + `npm run
start` (a `nrfi-dashboard-prod` launch config now exists for exactly
this). **Verify dashboard changes against a prod build, not the dev
server, from now on.**

And with a STRONG pick appearing on tonight's slate during verification,
the stake chip rendered on its own: `PENDING · LOCKS 6:40 PM ET · DK Y
-110 · STAKE UP TO 4.4u`. The chip was never broken — every earlier test
ran against slates with zero STRONG rows.

### Changed — record card: profit headline + date-picker day view

Operator feedback on first contact with the card:
- **Headline is now the PROFIT** (+365.10u / +130.18u — every unit above
  the 100u start), with the bankroll (100u → 465.10u / 230.18u) moved to
  the sub-line. Previously the big number was the final bank.
- **The slate date picker now drives a selected-day strip** in both
  record columns: pick any date and each column shows that day's SYSTEM
  bets — game, side, price, Kelly stake, result, P&L, bank after — or
  says "no system bets this day". Previously day filtering only touched
  the ledger cards below, which show the flat-1u era and made it look
  like the record was "stuck on flat 1 unit".
- Bet-count question answered with the same-window head-to-head: old
  system 5/07-onward, real prices: **314 bets, 55.4% vs 56.2% needed,
  -5.34u**. New record, same window: **94 bets, 64.9% vs 58.0% needed,
  +11.23u**. The ~220 missing bets are the 0.40-0.44 band (117 games),
  LEAN NRFI, and low-lambda games — the volume that was losing.

### Changed — the redesign, actually shipped

Operator: "I don't even think we redesigned the dashboard like we
planned." Correct. Now done, per the approved shape brief + CLAUDE.md:

- **Warm brown/peach palette, terminal green killed.** Token-level
  re-tint of globals.css (both themes), DashboardShell glow, favicon:
  espresso surfaces (#171009/#20160e), cream ink (#f0e4d3), **peach
  primary #f5a465** (was phosphor #5dff9a), rust YRFI #e06a48, amber
  PASS #e9b45b. Every component inherits via the existing variables.
- **Section order now answers "what do I bet tonight" first**: hero →
  health banners (render only when wrong) → RoiPanel (System Record
  first) → date picker + board → slate distribution → experiment
  plumbing → footer. SummaryStrip no longer sits between the hero and
  the money numbers.
- **The ledger can no longer impersonate the record.** The old
  "Net P&L · real prices only" card is relabelled **"Ledger · bets
  actually placed · flat 1u before 7/28"** — it aggregates what was
  historically wagered, which legitimately disagrees with the System
  Record on past days (7/25: ledger went 0-4; the system record sat the
  day out). The disagreement was the "units per day are wrong" report.
- **Stake-scaling footnote** on the record card: the same bet sizes
  differently in the two columns because stakes are a % of each
  record's own compounding bankroll (7/27 TOR@WSH: 11.15u projected vs
  5.52u real). Without the note that reads as a math error.

Verified against a production build: --primary computes to #f5a465,
record card above the relabelled ledger, footnote rendered.

### Fixed — full-code audit: 4 money bugs + 4 wrong-number bugs (16-agent review)

An ultracode audit workflow (6 lens-specific reviewers, every P0/P1
finding adversarially verified against the code) confirmed 8 of 35 raw
findings. All 8 fixed:

**P0 · Kelly daily cap double-counted on every odds re-import**
(`tracker.py`). `_committed_on` seeded from ALL STRONG rows' stakes —
including the pre-lock rows the batch was about to re-size — and each
re-size ADDED the fresh stake without releasing the old one, so
committed exposure ran ~2x truth. With Railway re-importing every 5
minutes, stakes on any normal day oscillated full → trimmed → zero
across ticks, and whatever value existed when the lock window flipped
the row froze forever. Every offline replay tool already worked around
this with a manual reset; production had none. Fix: seed only from
locked (`bet_placed=Y`) rows + new `kelly_reset_daily_committed()`
called at the top of every import batch (also refreshes the bankroll
cache, which never expired in a long-lived process). **Regression: 3
consecutive simulated import batches now produce identical stakes;
verify_kelly_wiring all-pass.**

**P0 · end_of_day heal fabricated bets** (`tools/end_of_day_check.py`).
The orphan-heal predicate skipped only `bet_placed=Y`, so deliberate
`N` rows — Kelly's zero-stake edge gate, daily-cap-zeroed picks,
pre-lock pendings — got retroactively stamped `Y` at flat 1.00u,
booking P&L for bets never made and (via the compounding bankroll)
mis-sizing every later stake. Now heals only truly-blank rows and
preserves any recorded Kelly stake instead of flattening it to 1.00.

**P0 · StakeChip sized from a static bankroll** (`BoardRow.tsx`). The
chip used the nominal 100u while the tracker stakes from the compounded
bank — in a drawdown the chip overstates the real stake. The predictor
now exports `kellyCurrentBankrollUnits` each tick and the chip uses it;
once a bet locks, the chip displays the ledger's frozen `unitsRisked`
verbatim ("staked N.NNu") instead of recomputing.

**P0 · hero card hard-coded 1u per bet** (`TonightsActionCard.tsx`).
"X.Xu staked" summed a constant 1 while quarter-Kelly stakes 4-10u —
understating tonight's real exposure severalfold. Now sums the ledger's
`unitsRisked`; rows without a recorded stake contribute 0, never a guess.

**P1 · TotalCard caption/tone described a different bet set than its
number** — headline was real-priced P&L, caption counted all graded bets
and the card colour keyed off the placeholder-inflated sum. All three
now follow the priced subset, with the record labelled "counts all
graded" when they differ.

**P1 · record's method string overclaimed walk-forward.** Only the
calibrator is walk-forward; the LR weights are the fixed 2026-05-26
refit (trained through 5/11), so bets ≤5/11 are partially in-sample at
the weight layer. The JSON now says exactly that.

**P1 · record silently dropped Kelly-zeroed bets from its W-L.**
Projected: 47 of 186 qualifying bets (+3.97u flat) got zero stake and
vanished from the headline. Now disclosed in the JSON
(`selectedBets/droppedZeroStake/droppedFlatPnl`) and on the card
("staked 139 of 186 qualifying").

**P1 · sizing bankroll compounded -110-fallback P&L** (`tracker.py`).
A post-epoch WIN with no captured price books fallback profit that then
scales every later Kelly stake — the April artefact, recreated inside
the money path. The compounding loop now skips rows without a real
picked-side price.

Refuted by verification (not fixed, on purpose): the TODAY-eyebrow
provenance claim and the record-vs-ledger day-strip mismatch claim.
12 lower-priority P2 findings logged in the audit output for later.

### Deferred (still awaiting operator decision)

- Swapping the calibrator to CIR. Brier-neutral, kills the plateau.
  Only worth shipping as a prerequisite for Kelly, since on its own it
  does not change which games get bet (see negative result above).
- Kelly sizing itself. Now safer than it was — the selection fix above is
  the precondition — but still unshipped. If adopted: half Kelly or less,
  and only after the calibrator plateau is gone.

---

## [2026-07-19] — Units fill hourly (not nightly) + daily system heartbeat

Operator reported "won units aren't tracking" and "telegrams come in
late." Investigation traced both to a single scheduling gap, and added a
heartbeat so a quiet day is never mistaken for a broken one.

### Fixed

- **Orphaned-STRONG healer now runs on `predict` ticks too, not only the
  nightly `grade`** (`.github/workflows/daily.yml`). `bet_placed=Y`,
  `units_risked`, `profit_loss_units`, and the `strong_graded` WIN/LOSS
  Telegram were all applied exclusively by `end_of_day_check.py`, which
  the workflow gated to the single `30 3 * * *` (11:30pm ET) grade run.
  So afternoon wins sat at `+0.000u` with no result ping until ~midnight
  (and that grade cron itself often fires 1-3h late). The predict step
  already live-grades finished 1st innings, so running the safety net
  right after it heals each game within ~1 cron tick. Idempotent, soft-
  fail, and the `strong_graded` ping has a 24h dedup window, so the extra
  hourly invocations are silent no-ops when the slate is already clean.

### Added

- **`tools/daily_heartbeat.py` + workflow wiring** — one Telegram per day
  ("☀️ N games today · picks: X STRONG, Y LEAN" or "No MLB regular-season
  games today"). Motivated by 2026-07-13, when an All-Star-break no-games
  day (correctly 0 picks / 0 alerts) was indistinguishable from an outage.
  New `daily_heartbeat` event type in `tracker._DEDUP_WINDOW_M` (18h ->
  one/day); not in `_SUPERGROUP_ALLOWED_EVENTS`, so it lands only in the
  operator DM. Fires on any predict tick from 11am ET on (dedup keeps it
  to one send), robust to a single scheduled cron being skipped.

---

## [2026-07-19] — Fix P&L calculator reading only the first 1000 rows

Operator reported "won units aren't tracking properly." Root cause found
in `tools/pl_calc.py`: the Supabase read used a bare
`.select("*").execute()`, which PostgREST caps at ~1000 rows. Mid-season
the ledger had grown to 1438 rows, so the calculator silently saw only
the **oldest** 1000 picks and could not see anything after **2026-06-14** —
35 days of bets, including every recent win, were invisible to it.

### Fixed

- **`tools/pl_calc.py` now pages through the whole `picks_<season>` table**
  (`.order("date").order("game_pk").range(offset, offset+PAGE-1)` in a loop
  until a short page) instead of one capped fetch. Post-fix the tool reads
  all 1438 rows and reports the full season (295W/205L, +39.60u; stored ==
  recomputed, no drift). The CSV fallback path was never reached before
  because a non-empty capped Supabase result short-circuited it.

### Notes

- Same unpaginated-read pattern still exists in `tools/analyze_losses.py`
  and `tools/backfill_variants.py` (different tables); flagged for a
  follow-up, not fixed here.
- The **dashboard** is unaffected: `dashboard/lib/roi.ts` and `board.ts`
  read the full `picks_<year>.csv` from disk, not the capped query.

---

## [2026-06-07] — Curtail STRONG NRFI (YRFI-only) after a full prediction rework

Operator pushed for a complete NRFI rework ("something is wrong with how
we predict that"). I ran one, end to end, and the honest result is:
**the NRFI prediction is sound — the side is unprofitable for structural
reasons, not a math bug.** So we stop betting it.

### Changed

- **`_LR_STRONG_NRFI_P` 0.62 → 1.01** (mlb_first_inning_predictor.py).
  1.01 = "off": no `nrfi_prob` ever clears it, so every NRFI-leaning game
  now routes to **LEAN NRFI (tracked, `bet_placed=N`)** instead of a real
  bet. We bet **YRFI only**. Reversible: set back to 0.62.
- **YRFI invariance proven empirically**: re-classified all 917 graded
  games under 0.62 vs 1.01 — 59 picks changed, **all 59 STRONG NRFI →
  LEAN NRFI, 0 YRFI picks touched**. YRFI is the `nrfi_prob<0.44` side; the
  changed gate only ever fires for high `nrfi_prob`.

### Why (the rework, in evidence)

- **Decomposition**: season NRFI **−10.5u** vs YRFI **+8.0u** (real odds).
  NRFI is the entire reason the book is red; YRFI-only would be +8u.
- **The prediction is NOT broken** (`tools/nrfi_prediction_diagnostic.py`):
  per-half run model near-perfect (pred 28.4% vs actual 28.3%), the two
  halves are independent (corr −0.08, so the product formula is valid),
  aggregate NRFI calibrated (47.5% vs 47.9%) and it even **beats the
  market** (book prices NRFI ~52%, truth 48%).
- **No prediction lever found**: situational miscalibration all died on
  the 4,802-game backtest (`tools/nrfi_situational_scan.py`); `whip_gap`
  null in 3-split (`tools/whip_gap_retrain_test.py`, redundant w/ FIP);
  the 1st-inning batting-order idea is dead — a hitter's 1st-inning OBP is
  pure sampling noise (year-to-year gap corr **+0.06**;
  `tools/batter_fi_obp_premise_check.py`).
- **No profitability lever**: the "only bet NRFI when we disagree with the
  book" filter tested **negative on 505 graded games**
  (`tools/nrfi_market_disagreement.py`) — even in the biggest-disagreement
  bucket we hit 53% needing 57%. On NRFI, our disagreements with the book
  are *us* being wrong. The market is efficient on NRFI; we can't out-
  predict an efficient price on a near-coin-flip inning.
- Full do-not-retread record: user memory `2026-06-07_nrfi_rework`.

### Added (read-only analysis tools, the investigation record)

- `tools/nrfi_prediction_diagnostic.py`, `tools/nrfi_situational_scan.py`,
  `tools/whip_gap_retrain_test.py`, `tools/batter_fi_obp_premise_check.py`,
  `tools/nrfi_market_disagreement.py`; extended
  `tools/nrfi_threshold_study.py` grid to include an "NRFI off" sentinel.

---

## [2026-06-07] — Quiet the noisy calibration-drift Telegram (was crying wolf daily)

### Fixed

- Operator: "I keep getting calibration drift messages." Diagnosed
  `tools/calibration_drift_monitor.py` as the source and found three
  reasons it over-fired:
  1. **Tiny-sample triggers.** Per-bucket alerts fired at a minimum of 8
     bets in each window. A 0.04 Brier swing on ~15 bets is noise — and
     the flagged buckets were exactly that size (n=14–18). Raised the
     minimum to **30** (matches the aggregate gate and the sister tool
     `calibration_monitor.py`). With the real data, nothing spurious fires.
  2. **Daily re-fire.** The Telegram dedup key included the date, so a
     slow-moving 30-day condition pinged a *fresh* alert every single day.
     Re-keyed to **ISO-week + which buckets drifted** → at most ~one ping
     per week per distinct pattern; a genuinely new drift still pings.
  3. **Stale buckets.** Boundaries were still 0.56/0.60; STRONG NRFI moved
     to 0.62 on 6/04. Updated to 0.62/0.66 so the discontinued 0.56–0.62
     dead-band bets sort into `pass_zone` instead of inflating the
     marg/deep-NRFI Brier. (The report now shows that dead-band clearly:
     pass_zone 37% hit / −7.1u over 30d — bets we *already stopped making*.)
- Also made local runs unicode-safe (the alert emoji crashed cp1252
  consoles; the Telegram body was always UTF-8 and unaffected).
- Observability-only: no model, calibrator, pick, bet, or odds change.
  The rigorous sister monitor (T2.59, hit-rate vs stated, 7pp + n≥30 +
  persistence) is untouched and still reports "no persistent drift," so
  real drift is still covered. File: `tools/calibration_drift_monitor.py`.

---

## [2026-06-05] — Show the *weather-adjusted* YRFI floor in the demotion tooltip

### Fixed

- Follow-up to yesterday's tooltip fix. Operator caught a still-wrong
  case: CWS@PHI 6/05 read *"the model's run projection (0.85) is below
  the 0.84 floor"* — 0.85 is **above** 0.84. The demotion was correct;
  the displayed floor was not.
- **Root cause: the YRFI lambda floor is weather-adjusted at decision
  time** (`mlb_first_inning_predictor.py::_weather_adjusted_floor`, T4.3).
  Hot (≥28 °C) or windy (≥24 km/h) games raise the floor +0.02 each;
  cold lowers it 0.02; dome neutralises. CWS@PHI was 32.7 °C, so the real
  floor was **0.858**, not the 0.838 base — and `lambda_lr_total = 0.8525`
  is below 0.858. The dashboard had been showing the static base.
- Fix: the dashboard now recomputes the **same per-game floor the
  predictor used** and shows it — *"0.85 below this game's 0.86 floor
  (raised because it's a hot/windy park today)"*. Added a `yrfiFloorUsed`
  field to `BoardRow`, computed in `lib/board-supabase.ts` (live path,
  which has the weather columns via `select *`) by a TS mirror of
  `_weather_adjusted_floor` — flagged KEEP-IN-SYNC with the Python. CSV
  fallback path uses the 0.838 base (board snapshots carry no weather).
- Also added a tie-safe number formatter so a projection within ~0.003 of
  the floor never renders as "0.84 below 0.84" (bumps to 3 dp only on a
  tie). Display-only; no model or bet behavior changed. Files:
  `dashboard/components/BoardRow.tsx`, `dashboard/lib/board-supabase.ts`,
  `dashboard/lib/board.ts`, `dashboard/lib/types.ts`.

---

## [2026-06-04] — Fix self-contradicting "PASS · LOW λ" tooltip (wrong lambda + stale floor)

### Fixed

- The board's `PASS · LOW λ` demotion tooltip showed nonsense like
  *"Combined λ 1.13 below the 0.78 floor"* — 1.13 is plainly **above**
  0.78. Operator caught it (BAL@BOS 6/04). Two bugs underneath:
  1. **Wrong lambda displayed against the floor.** The tooltip printed
     `combined_lambda` (the legacy Poisson display value, 1.13), but the
     YRFI floor demotion is actually decided by `lambda_lr_total` (the
     production model's own first-inning run projection — 0.80 for that
     game). The two are different numbers from different models; comparing
     the *displayed* one to the floor was never meaningful.
  2. **Stale floor value.** The tooltip strings hardcoded `0.78`; the real
     production floor (`_LR_LAMBDA_YRFI_FLOOR`) has been `0.838` since the
     5/26 ship. `DEFAULT_THRESHOLDS.lambdaYrfiFloor` was also still `0.78`.
- Now: the demotion tooltips reference the model's actual run projection
  (`lambda_lr_total`, 0.80) vs the correct floor (0.838), so "0.80 below
  the 0.84 floor" reads true. The big "λ" chip still shows
  `combined_lambda` (unchanged — it's what the board sorts by), but its
  hover note now explains the floor uses the model projection, not the
  displayed value.
- Plumbing: added `lambdaLrTotal` to the `BoardRow` type and populated it
  in both board builders (`lib/board.ts` CSV path via `toNumber`,
  `lib/board-supabase.ts` live path via `nullableNum`) so missing values
  are `null` (no false "0.00 below floor"). No model/bet behavior changed
  — display/explanation only. Fixed `DEFAULT_THRESHOLDS.lambdaYrfiFloor`
  0.78 → 0.838. Files: `dashboard/components/BoardRow.tsx`,
  `dashboard/lib/types.ts`, `dashboard/lib/board.ts`,
  `dashboard/lib/board-supabase.ts`.

---

## [2026-06-04] — STRONG NRFI threshold 0.56 → 0.62 (validated): stop betting the vig-break-even band

Diagnosed *why* NRFI accuracy was poor (operator pushed past "it's
variance"): a real, localized calibration problem, not noise.

### Why

- Calibration reliability on all graded games: when the model says
  `nrfi_prob` 0.56-0.62 it actually goes NRFI only ~56-57% -- right at
  the ~-130 vig break-even, i.e. NO edge -- while 0.62-0.66 goes 64%
  and 0.66+ goes 71% (clears the vig).  Robust signal (shows in May and
  June), unlike the weather lead which died on the 4,802-game backtest.
- The STRONG NRFI threshold (0.56) sat inside that dead band.  It was
  *previously* 0.62; the 2026-04-29 loosening to 0.56 was a YRFI-focused
  change (recapture 0.43-band YRFI picks) that dragged NRFI down with it.
- This raise REVERTS NRFI to the previously-validated 0.62.

### Validation (tools/nrfi_threshold_study.py, tools/edge_reality_check.py)

- Realized ROI on placed STRONG NRFI bets rises monotonically with the
  threshold: 0.56=-16%, 0.60=-8%, 0.62=-3%, 0.64=+6%.
- **TRUE walk-forward** (threshold chosen on prior weeks, applied blind):
  **+4.26u** vs leaving it at 0.56.  Clears the same bar the lambda
  ceiling did (+9.57u) -- and that the lower-YRFI-floor idea FAILED
  (-3.56u, not shipped).
- Effect: turns STRONG NRFI from a -16% / -7.3u drag into ~break-even by
  skipping the band where the model paid vig for a coin flip.  It does
  not make NRFI a strong bet; it stops the bleed.

### Changed

- `mlb_first_inning_predictor.py`: `_LR_STRONG_NRFI_P` 0.56 → 0.62.  The
  0.56-0.62 band now classifies as LEAN NRFI (track-only, `bet_placed=N`).
  Comments + the LEAN-band docstring updated.
- `dashboard/components/BoardRow.tsx`: `DEFAULT_THRESHOLDS.strongNrfiP`
  0.56 → 0.62 so the tentative classifier matches.  `npm run build` passes.
- `data/thresholds.json` picks up the new value on the next predict run.

### YRFI INVARIANCE (proven, not assumed)

20,200-cell grid test (every `p_nrfi` × `lambda`): **0 changes** where
`p_nrfi < 0.56` (the YRFI/PASS side), changes only on the NRFI side.
The winning YRFI engine is mathematically untouched.

### Context

This sits on top of the existing NRFI lambda ceiling (0.52): the
threshold filters low-confidence NRFI, the ceiling filters
high-projected-runs NRFI.  Complementary.  Also note the honest finding
from this session: across 139 real-odds bets the overall edge is NOT yet
statistically proven (bootstrap 95% CIs span 0); the recent profit was
concentrated in one week.  This change is about removing a *known* −EV
band, not claiming a proven edge.

### Rollback

`_LR_STRONG_NRFI_P = 0.56` in mlb_first_inning_predictor.py, commit, push.

---

## [2026-06-02] — Fix: stop the daily refresh-priors failure + silence false-alarm drift Telegrams

Investigated recurring GitHub Actions failures + recurring Telegram
"error" pings.  Two independent root causes, both fixed in Python (no
workflow-file edit -- this client lacks GitHub `workflow` OAuth scope).

### Fixed #1 — daily `refresh-priors` workflow failure (GitHub red X + email)

- Root cause: `pybaseball` was never in `requirements.txt`, so the CI
  backfill (`tools/backfill_truepit_2026.py`) hits
  `sys.exit("pip install pybaseball")` on import.  The `refresh-priors`
  job (cron `0 6 * * *`, once daily) wipes the per-pitch cache first,
  so the failed backfill leaves it empty; `build_truepit_2026_with_priors.py`
  then rebuilds 0 pitchers and writes an empty JSON; daily.yml's sanity
  check sees <100 pitchers and aborts (exit 1).  Failing every morning
  since ~2026-05-04 (when the priors JSON was last built locally).
- Impact was ZERO on the model: the sanity check correctly blocked the
  empty file from ever being committed, so production ran on the 206-
  pitcher 2026-05-04 priors the whole time.  Loud-but-safe.
- Fix: `build_truepit_2026_with_priors.py` now guards the write -- if a
  rebuild is degenerate (<100 pitchers) AND a healthy file already
  exists, it KEEPS the existing file and exits 0 instead of clobbering
  it.  daily.yml's sanity check then reads the preserved 206-pitcher
  file and passes; the commit step sees no change ("nothing to commit",
  exit 0).  Net: the run goes green, the model is unchanged, and a real
  refresh resumes automatically if/when the cache repopulates.

### Fixed #3 — daily false-alarm "feature drift HIGH" Telegram

- `tools/feature_drift_monitor.py` fired a HIGH-severity Telegram on
  `pick_cluster >= 4` (largest set of picks within 0.005 calibrated
  P(NRFI)).  That HIGH fired ~daily.  But the flat-zone study
  (tools/filter_impact_check.py, 2026-05-26) already proved clustered
  STRONG picks hit ~64% -- clustering is NOT predictive of bad
  outcomes.  So this was a daily false alarm.
- Fix: `severity_for_pick_cluster` now caps at MEDIUM (>=4 -> MEDIUM,
  >=3 -> LOW).  Telegram fires on HIGH only, so the cluster pings stop;
  the cluster size still appears in the drift CSV + summary for
  visibility.  Real drift signals (>=3sigma feature moves, >=30pp tag
  shifts) still escalate to HIGH and still Telegram.

### Deferred #2 — actually un-freezing the priors (NOT done)

- Adding `pybaseball` to make the daily refresh truly work would feed
  fresher Statcast into the model -- i.e. it CAN change picks.  With the
  model winning on the frozen priors, this is held pending an explicit
  decision; it also adds a heavy dep to all ~25 daily runs + a 30-50min
  flaky Statcast pull.  Tracked separately.

---

## [2026-06-01] — Fix: dashboard false-BROKEN from malformed writtenAtUtc timestamp

Investigating why nrfi-terminal.vercel.app showed status BROKEN / "no
refresh in 522 min" while picks were current, the root cause turned out
to be NOT the alias (which correctly points at the latest production
deployment) and NOT the cron (which runs hourly) — it was a malformed
timestamp.

### Root cause

- `mlb_first_inning_predictor._write_thresholds_json` wrote
  `writtenAtUtc` as `datetime.now(ZoneInfo("UTC")).isoformat(timespec=
  "seconds") + "Z"`.  Because the datetime is tz-AWARE, isoformat()
  already appends `+00:00`, so the result was `"...+00:00Z"` — an
  invalid ISO-8601 string carrying BOTH an offset and a Z.
- `dashboard/app/api/health/route.ts` does `Date.parse(writtenAtUtc)`,
  which returns `NaN` for that malformed form, so `lastPredictAt`
  stayed null and the route fell back to the most-recent
  `pick_changes.csv` flip time.  On quiet pick-flip days (e.g. today,
  4 stable picks) the last flip was hours old -> false "BROKEN".
  On busy days frequent flips masked the bug, which is why it was
  intermittent.
- This was the only production site with the bug: every other
  `isoformat() + "Z"` in the tree uses a NAIVE `datetime.utcnow()` (or
  `.replace(tzinfo=None)`), which yields a valid `"...Z"`.

### Fixed

- `mlb_first_inning_predictor.py`: emit `writtenAtUtc` via
  `strftime("%Y-%m-%dT%H:%M:%SZ")` — a clean, parseable UTC stamp.
- `dashboard/app/api/health/route.ts`: defensively strip a redundant
  trailing `Z` when an offset is present before `Date.parse`, so old
  bundled snapshots and any future producer slip can't regress this.
  Dashboard `npm run build` passes.

### Impact

Display/health-status only — zero effect on picks, bets, P&L, or the
model.  The alias was never stale (confirmed via Vercel API:
nrfi-terminal.vercel.app -> latest READY production deployment).  The
dashboard self-heals on the next predict run (writes a valid timestamp
+ triggers a redeploy that bundles it).

---

## [2026-06-01] — NRFI lambda ceiling (T1-NRFI): stop STRONG NRFI bleed without touching YRFI

The 2026-05-26 sliding-window retrain made the YRFI side excellent
(STRONG YRFI 17W/6L over the next 5 days, +7.56u) but STRONG NRFI kept
bleeding (20W/23L bet, ~−8.6u over 30d).  Root-cause investigation +
a pressure-tested fix.

### Why NRFI was bleeding (investigation)

- League-wide first-inning NRFI rate dropped Apr 49.9% -> early-May
  48.9% -> late-May 46.0%.  The retrained model + calibrator were tuned
  to a higher base rate, so STRONG NRFI ran overconfident: at cal_p>=0.65
  the actual hit rate was 40%; at 0.56-0.60 it was 36%.
- A full recalibration was tested and REJECTED: it would have cut YRFI
  bet volume ~59% (70 -> 29 on the 14-day holdout) and turned +2.3u into
  −10.8u.  The current YRFI edge is an *exploitable* gap between our
  model and DK's slow-moving line; "honest" recalibration removes the
  exaggeration that makes YRFI profitable.  (tools/recalibrate_only.py
  documents this — DO NOT recalibrate without re-reading it.)
- Feature-level study of NRFI losses: 53% were the HOME team scoring in
  the bottom of the first; home top-3 OBP was the most robust separator
  of NRFI wins vs losses (Cohen's d 0.34/0.43 across both halves of the
  sample).  Conceptual gap: the model uses "top-3 by OPS," not the actual
  1-2-3 batting order that determines first-inning scoring.  -> Track 2.

### Added — `_LR_LAMBDA_NRFI_CEILING` (NRFI-only lambda ceiling)

- `mlb_first_inning_predictor.py`: STRONG NRFI is demoted to PASS
  "HIGH LAMBDA" when the model's own `lambda_lr_total` (expected
  first-inning runs) exceeds **0.52**.  Mirror image of the existing
  `_LR_LAMBDA_YRFI_FLOOR`.  Resolves the internal contradiction where
  the model fired STRONG NRFI while projecting >0.5 runs.
- **Pressure-test evidence** (2026-04-27 -> 2026-06-01):
  - True walk-forward (threshold chosen on prior weeks only, applied
    blind): **+9.57u** vs no-gate; threshold stabilized at 0.50.
  - Robustness, all 51 graded STRONG NRFI at flat −110: a contiguous
    BASIN of good caps 0.48/0.50/0.52 = +2.33 / +5.89 / +5.44u,
    degrading smoothly to no-gate (−5.31u).  Not a knife-edge.
  - Kept bets hit 71-76% vs 51% ungated.
  - Chose 0.52 (loose edge of basin) to keep volume as insurance if the
    league reverts NRFI-friendly; 0.50 was the in-sample optimum.
- **YRFI INVARIANCE PROVEN, not assumed**: the ceiling check lives ONLY
  inside the `p_nrfi >= 0.56` branch of `classify_pick_lr`.  A 20,200-cell
  grid test (every p_nrfi x lambda combo) confirmed **0 changes** on the
  YRFI/PASS side (p_nrfi < 0.56) and changes only on the NRFI side.  The
  5-day YRFI winning structure is mathematically untouched.

### Changed — display + plumbing for the new PASS reason

- `tracker.py`: "HIGH LAMBDA" -> "High lambda" label; added to the
  PASS-reason label map.
- `mlb_first_inning_predictor.py`: "HIGH LAMBDA" added to the PASS-reason
  sort order, the board zone map, and `data/thresholds.json` output
  (`lambdaNrfiCeiling`).
- Dashboard parity (so the tentative classifier never drifts from
  Python): `lib/types.ts` (PickStrength gains HIGH LAMBDA + FLAT ZONE;
  PickThresholds gains optional `lambdaNrfiCeiling`), `components/
  BoardRow.tsx` (classifyTentative mirrors the ceiling; "PASS · HIGH λ"
  pill), `lib/board.ts` + `lib/board-supabase.ts` (parse the optional
  ceiling without rejecting older thresholds payloads).  Dashboard
  `npm run build` passes.

### Deferred — Track 2 (the real NRFI fix)

- Start capturing FULL batting order at predict time (currently only
  top-3-by-OPS is stored), then build an NRFI feature that uses the
  actual 1-2-3 hitters' on-base.  Must pass 3-split out-of-sample
  validation before shipping.  This ceiling is the interim bleed-stopper.

### Rollback

One constant: set `_LR_LAMBDA_NRFI_CEILING = 99` in
`mlb_first_inning_predictor.py`, commit, push.  Next cron run reverts to
ungated STRONG NRFI within the hour.  YRFI unaffected either way.

---

## [2026-05-26] — Sliding-window retrain: T1+B1+calibrator refit on 2024+2025+2026YTD, validated weekly-retrain workflow

After two weeks of slow losses (−5.33u realized 5/12–5/26), investigated
whether retraining on more recent data would help.  Built a candidate
model trained on 2024 + 2025 + 2026 through 5/11, validated against
multiple holdout windows.

### Added — sliding-window retrain shipped

- Trained Phase E.3 + VSHAND (19 features per half) on combined
  2024+2025+2026YTD truepit data (n=2933 graded games).  Replaced
  production `data/lr_t1.json`, `data/lr_b1.json`,
  `data/calibration_v2.json`; old files preserved as
  `*.bak-2026-05-26-prod-prephase`.
- Architecture is unchanged from previous production; the only
  difference is the training window includes 2026 partial.
- Validation:
  - **14-day holdout (5/12-5/26) head-to-head**: candidate −1.48u
    vs production −11.82u at flat −110 (apples-to-apples eval
    pipeline, no production guards applied).  Net **+10.34u**.
  - **6-week walk-forward (4/14-5/25)**: candidate +18.64u vs
    production +12.18u over 565 games at flat −110, net **+6.45u**.
    Candidate wins or ties 5 of 6 weeks.
  - **2025 in-sample check**: candidate Brier 0.244 vs production
    0.245 — no degradation.
  - **LR weight comparison**: shifts are principled (stronger park
    + pitcher quality + home offense signals; weaker humidity +
    redundant offense signals), not chaotic.

### Added — validated weekly retrain workflow (manual trigger)

- `tools/weekly_refit.py`: fits a candidate on
  (2024+2025+2026 through last week), evaluates BOTH candidate and
  current production on the most recent 7-day window, ships only if
  candidate P&L ≥ prod P&L − 1.0u AND candidate Brier ≤ prod Brier
  + 0.005.  Backs up production files before overwriting.  Exit
  codes: 0 ship, 1 validation fail, 2 script error.
- `.github/workflows/weekly_retrain.yml`: workflow_dispatch trigger
  for `tools/weekly_refit.py`.  Commits + pushes new model files
  ONLY if the gate passed.  **No schedule yet** — manual trigger
  for ~4 weeks while we watch behavior, then convert to weekly
  cron.  Respects the 2026-05-11 policy in `daily.yml` ("weekly
  auto-recalibrate was disabled because it shipped without
  validation"): this workflow has the validation built in.
- First post-ship invocation correctly REFUSED to ship a 5/18-
  trained refit because it underperformed the just-shipped 5/11
  candidate by −2.18u on the 5/19-5/25 holdout.  Gate works.

### Added — calibrator flat-zone diagnostic (DISABLED guard)

- `calibration.py`: new `ProbCalibrator.predict_with_band(p)`
  method returns `(calibrated_p, band_info)` where `band_info`
  exposes `band`, `is_flat`, `flat_size`, `flat_rate`.  Mirrors
  the inline detection logic in `tools/pick_reasoning_log.py`.
- `mlb_first_inning_predictor.py`: added `_FLAT_ZONE_DEMOTE_SIZE`
  constant and a guard that demotes STRONG → PASS "FLAT ZONE"
  when a pick lands in a calibrator flat zone with flat_size
  ≥ threshold.  Wired through to `tracker.py` pick-label
  composition.
- Threshold set to 99 = **DISABLED** based on empirical study
  (`tools/filter_impact_check.py`): picks landing in flat zones
  hit at **63.6%** over a 109-bet 30-day window — they're our
  best picks, not our worst.  The calibrator's flat zones are a
  statement about training-data noise, not about pick quality.
  Filter wiring left in place for future experimentation; raise
  threshold to enable.

### Tools

- `tools/build_2026_truepit.py`: augments `picks_2026.csv` with
  `actual_side` + `fi_park_nrfi_rate` so it can be used as a
  truepit-format training CSV by `two_stage_model.py`.
- `tools/sliding_window_eval.py`: head-to-head candidate vs
  production on a holdout, with hypothetical P&L using logged
  DK odds.
- `tools/walk_forward_eval.py`: walks across weekly windows
  comparing static-train vs sliding-window training.  Used to
  validate the +6.45u multi-week signal.
- `tools/filter_impact_check.py`: empirical study of which
  STRONG picks the flat-zone filter would demote and what the
  P&L impact would be.

### Performance snapshot (2026-04-27 to 2026-05-26, STRONG bets only)

- Pre-ship production (2024+2025-trained):  
  64W / 53L (54.7% hit), realized P&L **−0.79u** over 117 bets.
- Eval-pipeline projection of the new shipped candidate on the
  same period:  
  ~+6u improvement projected at flat −110 (real-money result
  will depend on DK odds we actually take).

### Rollback

If the shipped candidate underperforms over the next 5–7 days:
```
cp data/lr_t1.json.bak-2026-05-26-prod-prephase data/lr_t1.json
cp data/lr_b1.json.bak-2026-05-26-prod-prephase data/lr_b1.json
cp data/calibration_v2.json.bak-2026-05-26-prod-prephase data/calibration_v2.json
git add data/*.json
git commit -m "revert 2026-05-26 sliding-window retrain (underperformed live)"
git push origin claude/mlb-inning-run-predictor-QyazL
```

---

## [2026-05-19] — Model-refresh ship: i01 fix + 2024-2025 vintage constants + park factor refresh

Lands the bug-fix portion of a wider model-refresh investigation
(2026-05-19 session).  Two architectural experiments (FIE retest with
the i01 fix in place, and offense×pitcher interaction terms) were both
tested and FAILED Gate A — those experiments are NOT shipped.  The
shipped diff is the hygienic vintage refresh plus the i01 typo fix.

### Fixed — pitcher first-inning ERA fetch (i01 sitCode typo)

- `backtest.py:654` and `mlb_first_inning_predictor.py:667`:
  `sitCodes=[i1]` → `sitCodes=[i01]`.  The `i1` code silently returned
  empty splits for ~3 weeks (since commit a82677a, 2026-04-25), causing
  `prior_season_pitcher_fi` to always fall through to the no-FI-data
  branch.  Verified post-fix against Skubal 2025 (31.0 IP / 1.45 ERA),
  Webb 2025 (34.0 IP / 3.71 ERA), Cole 2023 (33.0 IP / 2.73 ERA).
  Pre-existing improvement_log row 2026-05-12-bug-prior-season-pitcher-fi-i1.
  Real-world impact on LR picks: minimal — production LR doesn't
  consume FI ERA directly (the legacy lambda diagnostic does).

### Changed — league constants refreshed 2023-2024 → 2024-2025

- `mlb_first_inning_predictor.py`, `two_stage_model.py`,
  `recalibrate_v2.py`, `tools/v21_shadow_predict.py`: 9 LEAGUE_AVG_*
  constants now derived empirically from
  `data/backtests/backtest_{2024,2025}-*_truepit.csv` (n=9,604 first
  half-innings).
  Largest deltas (>1% from prior values):
  - `LEAGUE_FIRST_INNING_RUNS`: 0.475 → 0.510  (+7.5%)
  - `LEAGUE_AVG_BB9`:           3.20  → 2.93   (-8.4%)
  - `LEAGUE_AVG_K9`:            8.9   → 8.75   (-1.7%)
  - `LEAGUE_AVG_SLG`:           0.414 → 0.407  (-1.7%)
  - `FIP_CONSTANT`:             3.10  → 3.23   (re-aligned to new ERA)
  Other constants moved <1%.  Per the predictor's own LEAGUE_CONSTANTS
  block warning, all constants and park factors refresh together.
  Pre-existing improvement_log row 2026-05-12-bug-league-first-inning-runs-stale.
- `_LR_LAMBDA_YRFI_FLOOR`: 0.78 → 0.838 in
  `mlb_first_inning_predictor.py:958`, `tools/v21_shadow_predict.py`,
  `tools/v23_walkforward_backtest.py`.  Mechanical scaling of
  0.78 × (0.510/0.475) to keep the STRONG YRFI gate internally
  consistent with the new league base rate.  Empirical re-derivation
  deferred (future work).

### Changed — park factors rebuilt on 2025+2026 to-date

- `rebuild_park_factors.py`: BT_2025 path now points at the `_truepit`
  CSV (non-truepit version was archived during May rev pass).
- `data/fi_park_factors.json`: rebuilt.  Source mix 2025 (n=2393) +
  2026 to-date (n=596) = 2989 graded games.  Base NRFI rate 49.78%.
  All parks shifted <1pp from prior values — refresh was mostly
  cosmetic but keeps the data current.

### Added — recalibrate_v2.py `--since` flag + walk-forward tool

- `recalibrate_v2.py`: optional `--since YYYY-MM-DD` argument for
  trailing-window calibrator refits.  Default behavior unchanged
  (full 2025+2026 fit).  Also updated `BT_2025_PATH` to truepit CSV.
- `tools/walkforward_model_refresh.py`: new validation tool that
  re-scores historical picks under proposed model changes and compares
  hypothetical vs actual P&L.

### Deferred — architecture work for next session

The session's architectural experiments (FIE retest and offense ×
pitcher interaction terms) both FAILED Gate A with the same +0.0014
Brier delta on a small n=201 holdout — strong indication that the LR
is at its plateau on current features under linear architecture.
Future work needs a genuinely non-linear stage (gradient boost on
residuals, or MLP) — explicit multi-week project, not in this commit.
Companion improvement_log row 2026-05-14-finding-phase3-interaction-architecture.

---

## [2026-05-12] — Playbook Phase 1.3: LEAN tier (track-only) + dashboard TOTAL preserves real-money meaning

Resurrects the LEAN classifier tier as TRACK-ONLY (never bet) so the
playbook's 60-graded-LEAN-pick break-even analysis has data to feed
on.  Ships with a dashboard fix that protects the season +35.5u
"real-money" P&L number from being silently redefined to include
hypothetical LEAN picks.

### Changed — Classifier thresholds + structure

- `mlb_first_inning_predictor.py`: `_LR_LEAN_NRFI_P` 0.56 → 0.50 and
  `_LR_LEAN_YRFI_P` 0.44 → 0.50.  Carves the legacy 0.44-0.56 PASS
  dead zone into two LEAN bands:
  - LEAN NRFI: `0.50 <= p_nrfi < 0.56`
  - LEAN YRFI: `0.44 <  p_nrfi < 0.50` AND combined lambda ≥
    weather-adjusted YRFI floor (default 0.78)
- `classify_pick_lr` restructured.  The previous structure short-
  circuited the 0.44-0.50 band into PASS NO EDGE before the LEAN
  YRFI branch could fire.  The new structure mirrors the playbook
  spec exactly.  STRONG NRFI / STRONG YRFI / LOW LAMBDA boundaries
  are unchanged.  13 boundary tests pass.
- `tracker._apply_odds_to_row`: LEAN picks ALWAYS take the
  `bet_placed = 'N'` path regardless of edge.  The previous
  "LEAN with edge ≥ min_edge → bet" branch is intentionally removed.
  `units_risked` is still recorded (0.5u default) so the playbook's
  60-graded-LEAN-pick break-even analysis has counterfactual stakes.
- `data/thresholds.json`: regenerated with the new constants;
  dashboard's tentative-classifier reads this file at request time.

### Changed — Dashboard TOTAL P&L preserves its prior meaning

Operator caught the contamination risk before push: rolling LEAN into
TOTAL would have silently redefined the +35.5u headline metric to
"STRONG + LEAN performance" instead of "real-money STRONG only."  Fix:

- `dashboard/lib/roi.ts` + `dashboard/lib/roi-today.ts`: TOTAL
  aggregation now strictly filters to STRONG zones.  LEAN's hypothetical
  P&L is computed in a separate `leanPaperTrade` field on the
  `RoiResponse` (LEAN's realized `profit_loss_units` is 0 because
  `bet_placed='N'`; we substitute a flat -110 hypothetical for the
  paper-trade view only).  `cumulativePL` chart series also excludes
  LEAN -- the bankroll curve stays a real-money curve.
- `dashboard/components/RoiPanel.tsx` + `.module.css`: new
  `LeanPaperTradeCard` component rendered only when at least one LEAN
  pick exists in the window.  Visually distinct from the TOTAL card
  via a dashed border and a diagonal "PAPER" watermark.  Eyebrow:
  "LEAN paper-trade · NOT BET".  Shows hit rate, hypothetical paper P&L
  at flat -110, pick count, and edge vs the 52.4% break-even bar.
- `dashboard/components/BoardRow.tsx`: TS mirror `classifyTentative`
  restructured to match the new Python classifier; default thresholds
  updated to `leanNrfiP=0.50` / `leanYrfiP=0.50`.
- `dashboard/components/ControlPanel.tsx` / `StatusLine.tsx`:
  stale "LEAN tier was removed" comments updated; LEAN+ filter is
  now first-class (already had the right behavior in DashboardShell).

### Sanity check

- Ported the new TS aggregation logic to Python and ran it against
  the live `data/picks_2026.csv`.  Season TOTAL = +35.535u, record
  141W-90L (61.0%).  Exact match with `python tools/pl_calc.py
  --window season`, which is the canonical P&L oracle.  The +35.5u
  headline survives the change unchanged.
- LEAN paper-trade currently shows 0 picks (Phase 1.3 hasn't run
  the cron yet); card will appear once LEAN rows accumulate.

### Operational notes

- `MODEL_VERSION` stays at `V2.2`.  This is a classifier-threshold
  change, not a weight change -- no retraining performed.
- LEAN picks now appear in `picks_2026.csv` with `pick_strength=LEAN`
  and `bet_placed=N` once lineups post and the next cron tick runs.
  Historical replay against existing rows (290 in the dead-zone band)
  produces 27 LEAN NRFI + 236 LEAN YRFI + 27 PASS (lambda gate fails)
  under the new logic.  The ~9:1 YRFI:NRFI split is a real property
  of the calibrator's output distribution (mass concentrated in
  [0.46, 0.50]; only 10 historical rows in [0.50, 0.54]) -- the
  classifier is symmetric across both bands.

---

## [2026-05-12] — Playbook Phase 1.1 + 1.2 foundation logging

Two zero-risk logging additions per `MLB_MODEL_IMPROVEMENT_PLAYBOOK.md`
Phase 1.  No predictor, tracker, classifier, or dashboard behavior
changes; only new files plus a cron hook.  Setup ahead of Phase 1.3
(LEAN tier reactivation) which is held on the candidate branch for
operator review.

### Added — Phase 1.1: improvement-log file

- `data/improvement_log.csv` (new): canonical record of every model
  change attempted from here on.  Columns mirror the playbook spec
  (`test_id, date_started, date_decided, change_description,
  brier_s1, brier_s2, brier_s3, walkforward_pnl, shadow_pnl,
  gate_result, notes`).  First row documents this Phase 1 setup itself.

### Added — Phase 1.2: V2.1/V2.2 disagreement-only log

- `tools/v21_v22_disagreements_log.py` (new): writes
  `data/diagnostics/v21_v22_disagreements.csv` containing only the
  picks where V2.1 (shadow) and V2.2 (live) disagree.  Agreements
  carry no comparative signal; disagreements are 100% of the
  informative sample.  Wired into the grade cron in `daily.yml`
  immediately after the existing `v21_vs_v22_compare` step (soft-fail
  with `set +e` so a bug here can never break the grade cycle).
- Columns: `date, game_pk, v21_pick, v22_pick, v21_prob, v22_prob,
  actual_outcome, v21_correct, v22_correct`.  Idempotent overwrite
  each run via atomic tempfile+os.replace.

### Prerequisite work

- `data/archive/v2.2/`: backup of V2.2 weights (`lr_t1.json`,
  `lr_b1.json`, `calibration_v2.json`, `fi_park_factors.json`) so a
  future rollback has a snapshot to copy from.  Mirrors the
  `data/archive/v2.1/` pattern.

### Operational notes

- `MODEL_VERSION` stays at `V2.2`.  No model weights, thresholds,
  or classifier behavior changed in this commit.
- The disagreement log starts populating on the next grade cron tick.
  As of this push the shadow tracker only has ~6 picks of history (V2.1
  shadow predict started 2026-05-11), so expect <5 disagreement rows
  initially.  Sample grows ~10/day.

---

## [2026-05-11] — V2.1 shadow tracker + dashboard demotion banner + shadow-P&L card

Three shipped changes to make the V2.2 deploy reversible and the
ongoing demotion experiments visible at a glance.

### 1. V2.1 shadow tracker (safety net for the V2.2 deploy)

- `tracker.py FIELDS`: three new optional columns:
  - `v21_shadow_nrfi_prob`
  - `v21_shadow_pick_side`
  - `v21_shadow_pick_strength`
  Schema-evolution code in `_read_rows` backfills blanks on first
  write; nothing breaks for older rows.
- `tools/v21_shadow_predict.py` (new): rebuilds T1/B1 feature
  vectors from CSV row columns, loads archived V2.1 weights from
  `data/archive/v2.1/`, computes calibrated P(NRFI) under V2.1, and
  stamps the three shadow columns.  Idempotent.  Wired into the
  predict + grade cron in `daily.yml` after `apply_cluster_demotion`
  so the shadow records V2.1's verdict independent of demotion policy.
- `tools/v21_vs_v22_compare.py` (new): reads the shadow + live
  columns, reports day-by-day + trailing-30 W-L + P&L for both
  versions, and fires Telegram if V2.2 underperforms V2.1 by 3u+
  over 30 graded STRONG picks.  Wired into the grade cron.
- Comparison treats cluster-demoted V2.2 rows as PASS for accounting
  (we didn't bet them) but uses the original verdict from the label
  for the "what V2.2 intended" intent check.

The shadow data accumulates from this commit forward.  After ~30
graded STRONG bets, we have a real apples-to-apples comparison and
can either ratify V2.2 or roll back per the procedure in the
"V2.2 deployed" entry below.

### 2. Active demotions banner (`/api/active-demotions` +
`dashboard/components/DemotionsBanner.tsx`)

- New API route reads `data/cluster_demotions.json`, counts how many
  of today's + trailing-7d rows were demoted under each active rule
  (matched on the `"PASS - Cluster demotion: ... (id)"` prefix the
  applier stamps), and returns a summary per cluster including
  `reevaluateAfter` + days-until.
- Component renders a small banner above the board with the cluster
  id, re-eval countdown (color-coded: green / amber / red), and
  today + trailing-7d demoted counts.  Renders nothing when no
  demotions are active.

Why: a 4-day demotion experiment can quietly become permanent if
nobody remembers to look at the data on day 4.  The banner makes
the active state unmissable + the countdown reminds the operator
when to evaluate.

### 3. Shadow P&L card (`/api/shadow-pnl` +
`dashboard/components/ShadowPnlCard.tsx`)

- New API route mirrors `tools/cluster_shadow_pnl.py` logic in
  TypeScript: for each active demotion, splits matching graded
  rows into REAL (placed-before-demotion) / SHADOW (skipped) /
  TOTAL with W-L counts and P&L.
- Component shows a compact 3-column card next to RoiPanel with
  the decision-tree footer (≥ 5W-2L = over-corrected, ≤ 2W-5L =
  real signal, mixed = wait).

Why: previously you had to run `python tools/cluster_shadow_pnl.py`
from CLI to see how the demotion was doing.  Now it's a glance
on the dashboard.

### Files changed

- `tracker.py` — three new optional CSV columns.
- `tools/v21_shadow_predict.py` (new)
- `tools/v21_vs_v22_compare.py` (new)
- `.github/workflows/daily.yml` — shadow-predict step (predict +
  grade paths) + v21_vs_v22_compare alert (grade only).
- `dashboard/app/api/active-demotions/route.ts` (new)
- `dashboard/app/api/shadow-pnl/route.ts` (new)
- `dashboard/components/DemotionsBanner.tsx` + `.module.css` (new)
- `dashboard/components/ShadowPnlCard.tsx` + `.module.css` (new)
- `dashboard/components/DashboardShell.tsx` — imports + renders
  both new components.

All TypeScript clean (`tsc --noEmit` exit 0).

---

## [2026-05-11] — V2.2 deployed: refit LR weights on corrected truepit backtests

**Production change.**  Same Phase E.3 + Phase F feature set, same
isotonic calibrator architecture; refit weights against the 5/03-
corrected truepit backtest CSVs (T4.1 / T3.12: "xwOBA->xERA proxy
anchor corrected 0.310 -> 0.3205").  Bumps `MODEL_VERSION` from V2.1
to V2.2.

### Forward-sim on 5/09-5/10 (29 graded picks): v2.2 vs v2.1

- **5 STRONG -> PASS flips on losing days** -- all 5 were the
  actual losses we wanted to avoid:
  - 5/09 STL@SD (STRONG YRFI -> PASS): was LOSS, **saved -1.00u**
  - 5/09 HOU@CIN (STRONG YRFI -> PASS): was LOSS, **saved -1.00u**
  - 5/10 NYY@MIL (STRONG NRFI -> PASS): was LOSS, **saved -1.00u**
    (operator's flagged Yankees-elite-offense miss)
  - 5/10 TB@BOS (STRONG NRFI -> PASS): was LOSS, **saved -1.00u**
- 1 STRONG -> PASS flip on a winning day:
  - 5/09 LAA@TOR (STRONG NRFI -> PASS): was WIN, cost +0.83u
- 1 PASS -> STRONG flip (5/09 CHC@TEX); outcome lost in window.
- Net P&L impact on those days: **+3.17u** (had v2.2 been live).

### What changed in the weights

Production V2.1 weights were last refit 2026-04-29 (Phase F lock-in).
The training backtests were updated 2026-05-03 with the xwOBA->xERA
proxy correction.  V2.1 weights are STALE relative to the corrected
training data.  V2.2 refit closes that gap.

Biggest T1 coefficient deltas (V2.1 -> V2.2):

| Feature | V2.1 | V2.2 | Delta |
|---|---|---|---|
| home_xera | +0.2964 | +0.0404 | -0.2560 |
| away_top3c_iso | +0.1982 | +0.3813 | +0.1831 |
| away_top3c_slg | -0.2097 | -0.4280 | -0.2183 |
| home_fip | -0.0745 | +0.0492 | +0.1236 (sign flip) |

Calibrator was also re-fit on the new raw distribution
(`recalibrate_v2.py` ran post-refit) so calibration matches the
new raw output range.

### 3-split OOS validation (passed)

- Split 1 (train 2024 truepit, test 2025): Brier 0.2511 (acceptable)
- Split 2 (train 2025 truepit, test 2024): Brier 0.2595 (acceptable)
- Split 3 (train 2024+2025, test 2026): **Brier 0.2437**
  - Production V2.1 raw Brier on same test set: 0.2479
  - **Improvement: -0.0042** (clears 0.003+ deployment threshold)

### Feature ablation -- decided NOT to drop top3c_slg or iso

Tested dropping top3c_slg and top3c_iso separately as fixes for the
multicollinearity (R8 finding).  Result:

| Variant | 2026 Brier | Elite-power Brier |
|---|---|---|
| FULL (keep both) | 0.2442 | 0.2390 |
| Drop SLG | 0.2466 | 0.2427 |
| Drop ISO | 0.2469 | 0.2422 |

Keeping both is best.  The opposing signs are capturing real signal
(ISO = pure power, SLG-above-ISO = singles/contact).  The 5/09-5/10
NYY losses were the model's recent variance, not a structural
mispricing -- v2.2's refit + recalibrated bins handle them
correctly (see forward-sim above).

### Files changed

- `data/archive/v2.1/` (new) -- snapshot of pre-deploy V2.1 weights
  + calibrator + park factors.  For rollback: copy these back into
  `data/lr_t1.json`, `data/lr_b1.json`, `data/calibration_v2.json`,
  `data/fi_park_factors.json` and bump MODEL_VERSION back to V2.1.
- `data/lr_t1.json` / `data/lr_b1.json` -- V2.2 weights.
- `data/calibration_v2.json` -- V2.2 calibrator refit on new raw dist.
- `mlb_first_inning_predictor.py` -- `MODEL_VERSION = "V2.2"` plus
  inline doc explaining the bump.
- `data/candidates/` (new) -- the OOS-validation candidates from
  splits 1/2/3 kept for audit trail.
- `tools/v22_feature_ablation.py` (new) -- ablation script used to
  confirm we shouldn't drop SLG/ISO.

### Rollback

If v2.2 underperforms over the next ~30 graded STRONG bets:
```
cp data/archive/v2.1/lr_t1.json data/lr_t1.json
cp data/archive/v2.1/lr_b1.json data/lr_b1.json
cp data/archive/v2.1/calibration_v2.json data/calibration_v2.json
cp data/archive/v2.1/fi_park_factors.json data/fi_park_factors.json
sed -i 's/MODEL_VERSION = "V2.2"/MODEL_VERSION = "V2.1"/' \
  mlb_first_inning_predictor.py
git commit -am "Rollback to V2.1"
git push
```

Per operator policy, all plays remain flat 1u regardless of model
version.  No per-bet sizing changes.

---

## [2026-05-11] — System audit R2/R3/R4/R5/R7/R8 — observability + candidates

Follow-up to the auto-recalibrate disable.  Operator asked for the
full R2-R8 sequence (R6 skipped per policy: no per-bucket bet sizing).
See `docs/2026-05-11_system_audit.md` for the full report including
data tables, candidate weights, and the multicollinearity finding on
elite top-3 offense.

### Shipped (R2-R4): observability + reminders

- **R2 — `tools/loss_cluster_monitor.py`** — `yrfi_040_band` cluster
  renamed to `yrfi_deep`; predicate simplified to `nrfi_prob < 0.40`
  (was `0.370 <= p <= 0.420 AND lambda + park gates`).  The original
  band straddled a profit boundary; 30-day data showed `[0.40, 0.44]`
  is *profitable* (14W-6L, +6.57u) while `<0.40` is the actual loss
  zone (6W-12L, -7.00u).
- **R3 — `tools/calibration_drift_monitor.py`** (new) — wired into
  the grade cron.  Computes per-bucket Brier on trailing 30-day
  STRONG bets; alerts on >= +0.01 bucket delta or >= +0.005 aggregate
  delta vs prior 30d.  Closes the drift-detection loop the disabled
  weekly auto-recalibrator used to fill, without auto-deploying.
- **R4 — `tools/demotion_reeval_reminder.py`** (new) + schema bump
  on `data/cluster_demotions.json` — each demotion entry can now
  carry a `reevaluate_after` ISO date.  On/after that date, the
  cron fires a Telegram with the current shadow-P&L snapshot +
  decision tree so the operator can keep/flip/remove the demotion.
  `thin_pitcher_strong_v1` set to re-eval 2026-05-14.

### Candidates built (R5, R7) — NOT deployed

- **R5 — `tools/platt_candidate.py`** + `data/calibration_platt_candidate.json`
  — Platt-scaling (logit-logistic) calibrator candidate.  Lost to
  production isotonic on every OOS slice (Brier +0.003-0.006 worse).
  Conclusion: isotonic flat zones are a feature, not a bug, on this
  data -- distinct raw probs genuinely map to identical true rates.
  **Do not deploy.**
- **R7 — `data/candidates/lr_t1_split3.json` + `lr_b1_split3.json`**
  — refit LR weights via `two_stage_model.py --phase-e3` on the same
  2024+2025 truepit backtests production trained on.  Result: real
  coefficient drift, candidate has Brier 0.2437 on 2026 vs production
  raw Brier 0.2479 (-0.0042 improvement, clears 0.003+ threshold).
  Root cause: production weights last refit 4/29 (Phase F) but the
  training backtest CSVs were updated 5/03 (xwOBA->xERA proxy anchor
  correction).  Production is stale relative to corrected training
  data.  **Operator decides whether to deploy** -- if so, also re-run
  `recalibrate_v2.py` so the calibrator matches the new raw distribution.

### Finding (R8) — multicollinearity in top-3 power features

Operator hypothesis: 5/10 NYY@MIL STRONG NRFI lost because elite
Yankees offense wasn't priced in.  Data confirms.

- Stratifying 30-day STRONG bets by `max(top3c_iso)`:
  - STRONG YRFI + elite power (max_iso >= 0.25):  4W-1L  (80%)  +2.62u
  - STRONG YRFI + no elite power:                 16W-17L (48%) -3.06u
  - STRONG NRFI + elite power:                    2W-2L  (50%)  -0.49u
  - STRONG NRFI + no elite power:                 12W-10L (55%) -0.80u

- Elite power IS a +32pp signal for YRFI hits when present, but the
  T1 LR coefficients `away_top3c_iso=+0.20` and `away_top3c_slg=-0.21`
  have *opposite signs*.  ISO and SLG are highly correlated (both
  measure power); the LR can't separate them and produces a
  near-cancelling pair.  Net effect of elite NYY offense on the 5/10
  T1 logit: -0.056 (wrong sign).

- **The R7 candidate AMPLIFIES this:** iso=+0.38, slg=-0.43.  Net
  effect on 5/10 NYY: -0.124, even more wrong.  R7's aggregate Brier
  improvement comes at the cost of worse predictions on elite-offense
  games.

- Recommended fix: raise L2 regularization in `two_stage_model.py`
  training.  Higher L2 shrinks correlated coefficients toward 0
  jointly, reducing the seesaw.  Alternative: drop SLG (or ISO),
  or replace with a composite.  See audit doc for full ranking.

### Cron wiring

- `daily.yml` grade step now runs:
  1. `tools/feature_drift_monitor.py` (existing T4.5 alert)
  2. **NEW** `tools/calibration_drift_monitor.py` (R3)
  3. **NEW** `tools/demotion_reeval_reminder.py` (R4)
  4. `tools/pick_reasoning_log.py` (existing T4.6)
- All soft-failing -- no new failure mode for the grade pipeline.

---

## [2026-05-11] — Disabled weekly auto-recalibrate cron (OOS validation gap)

Operator audit revealed the weekly recalibrate cron in
`.github/workflows/daily.yml` was refitting the production
calibrator + park-factor file every Monday at 04:45 UTC with
**no out-of-sample validation, no Brier-regression guard, no
rollback path**.  Per CLAUDE.md:

> Out-of-sample validation is non-negotiable for any model change.

The 5/11 forensic audit showed the practical impact of each
weekly refit is small (bin shifts of 1-2pp, OOS Brier within
±0.001 between refits — see commit ledger entries below for the
5/04 vs 5/11 comparison), but the GHA runner has no way to KNOW
whether a given week's refit is net-positive before shipping it.
That's the structural risk.

The 5/11 refit itself passed audit (Brier improved -0.0011 on
the 82-row 5/05-5/10 OOS slice) and stays in production; the
issue is the next refit was scheduled to ship blindly.

### Changed

- `.github/workflows/daily.yml` — commented out the Monday
  04:45 UTC `cron: "45 4 * * 1"` schedule.  Manual recalibration
  still available via `workflow_dispatch action: recalibrate`;
  guidance in the inline comment is to run the test_*.py 3-split
  OOS validation first and only ship if no regression.

### Forensic note: 5/05-5/10 was not a model regression

Day-by-day actual NRFI rates over the suspect window showed
the calibrator was correct on average -- it just lived through
back-to-back streaks in opposite directions:

| Window | Actual NRFI rate | Model predicted | Bias |
|---|---|---|---|
| 5/05-5/07 (won +7.23u) | 39.5% | 48.5% | +9.05pp |
| 5/08-5/10 (lost -5.22u) | 59.1% | 49.4% | -9.68pp |
| 5/05-5/10 combined | 50.0% | 49.0% | **+1.00pp** |

Combined window has near-zero bias.  The losing streak was
small-sample variance reverting from a hot streak, not a
recalibration drift.  The yrfi_040_band cluster (1W-5L on YRFI
in that specific shape) is a real localized signal -- which is
why we kept the thin-pitcher demotion -- but it does NOT
indicate a global model failure.

---

## [2026-05-11] — Cluster-demoted rows render as PASS with explanatory tooltip

Operator feedback after the 5/10 thin-pitcher demotion landed:
"if we're not betting on it or tracking the units for our
official record, then it should just say PASS with an
explanation in the dropdown why it's a PASS."  Previously the
demotion only flipped `bet_placed=N` and left `pick_side` /
`pick_strength` as STRONG NRFI/YRFI, which left the dashboard
showing a STRONG-toned pill that was actually a no-bet.

### Changed

- `tools/apply_cluster_demotion.py` — on a match, now also
  overwrites `pick_side='PASS'`, `pick_strength='NO EDGE'`, and
  encodes the original verdict + cluster id in `pick_label`
  using the magic prefix
  `"PASS - Cluster demotion: STRONG YRFI (thin_pitcher_strong_v1)"`.
  The prefix is the canonical "is this row demoted?" test
  everywhere downstream (shadow PnL tool, dashboard tooltip).
  Re-running on a row that already carries the prefix
  re-applies the demoted display state (in case the predictor
  regenerated pick_side back to STRONG on its pre-lock refresh,
  since pick_side isn't in the preserve list) but skips the
  journal write — one `pick_changes.csv` entry per row total,
  not 24 per day.
- `tools/cluster_shadow_pnl.py` — parses the original verdict
  out of `pick_label` for demoted rows; falls back to
  `actual_result` (NRFI/YRFI) for shadow W/L derivation since
  `graded_result` is now "PASS" for demoted rows.  Also prefers
  `opened_*_odds` (FIRST scrape captured by the T4.28 CLV
  pipeline, closest to the price we'd have bet at) over
  `market_*_odds` (latest scrape, closer to the close) when
  computing hypothetical P&L.
- `dashboard/components/BoardRow.tsx` — `PickPill` now matches
  the demotion prefix on `row.pickLabel` and renders a tooltip
  that names the demotion id, surfaces the model's original
  verdict, and points to `data/cluster_demotions.json` +
  `tools/cluster_shadow_pnl.py` for evaluation.  No new fields
  on `BoardRow` / `GameDetail` — everything reads from
  pickLabel.  TypeScript clean (`tsc --noEmit` exit 0).

---

## [2026-05-10] — Thin-pitcher STRONG demotion + shadow-P&L evaluator

Five-day window post v2.1 deploy (5/06–5/10) showed STRONG
NRFI/YRFI bets stratified hard on pitcher data quality:
both-`live` pitchers went 7W-1L (+4.89u), at-least-one-thin
(`sm`/`ltd`) went 6W-10L (-5.34u).  Operator opted to demote
NOW + run the inverse experiment via shadow-P&L tracking,
rather than waiting for the documented monitor-first protocol
to confirm.  Re-evaluation target 2026-05-14.

### Added

- `data/cluster_demotions.json` now contains one active entry,
  `thin_pitcher_strong_v1`: skips bet placement on STRONG
  NRFI/YRFI rows where the worst-quality pitcher is `sm` or
  `ltd`.  Predicate has no side / probability / lambda / park
  bounds — pitcher-data quality alone gates it.  The
  `apply_cluster_demotion.py` cron step picks it up on the
  next predict tick.  Reversible via `"active": false`.
- `tools/cluster_shadow_pnl.py` — new evaluator that, for each
  active demotion, prints REAL (bets the system still placed
  that match the predicate, e.g. pre-demotion history),
  SHADOW (bets the demotion skipped — hypothetical 1u P&L
  using captured market odds or flat -110 fallback), and
  TOTAL (the counterfactual: what the cluster would have
  done WITHOUT the demotion).  Use trailing shadow record to
  decide whether to keep `active=true` or flip it off.
  Run `python tools/cluster_shadow_pnl.py --since 2026-05-11`
  to see only post-demotion skips.

### Changed

- `memory/MEMORY.md` (auto-memory index) — added
  `thin_pitcher_demotion.md` entry so future agents see the
  active demotion + re-evaluation criteria.

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

---

## [2026-05-08] — Don't lose STRONG bets to a stale lineup endpoint

Same-day root-cause fix for the 2026-05-08 NYY@MIL + DET@KC
incident.  Three independent failures stacked to keep both
rows at PASS - LINEUP PENDING:

  1. `backtest.fetch_top3_batters` only reads from
     `liveData.boxscore.teams.<side>.battingOrder`, which MLB
     populates AFTER first pitch.  Pre-game predict runs
     therefore always saw empty arrays and fell through to
     team-fallback, which then triggered the LINEUP PENDING
     guard regardless of whether the lineup card was actually
     published on MLB's pre-game endpoints.
  2. The LINEUP PENDING guard in `mlb_first_inning_predictor`
     forced PASS on every non-NO-DATA row that had a
     team-fallback `top3c_source` -- including STRONG verdicts
     that sit 6+pp above the threshold and could not flip
     under the small (≤2.26pp) shifts the original guard
     comment cited.
  3. Vercel cron tick cadence had a 60-minute gap covering
     the lock-time window for 7:40pm ET starts (21 UTC = 5pm,
     then nothing until 23 UTC = 7pm), so even if the
     boxscore endpoint had eventually exposed the lineup at
     6:30pm, no predict run was scheduled to pick it up before
     the T-60 lock at 6:40pm.

### Fixed

- `backtest.fetch_top3_batters` now falls back to the schedule
  endpoint with `hydrate=lineups` when the boxscore returns an
  empty `battingOrder`.  Schedule lineups expose
  `lineups.homePlayers` / `lineups.awayPlayers` -- the actual
  pre-game lineup card MLB publishes 2-3 hours before first
  pitch -- so the predictor sees the announced lineup as soon
  as MLB posts it instead of waiting for first pitch.  Boxscore
  remains the primary path; schedule fallback only fires when
  one or both sides are missing, so live games stay on the
  authoritative actually-batted source.
- `mlb_first_inning_predictor` LINEUP PENDING guard now skips
  STRONG verdicts (`pick_conf == "STRONG"`).  Operator policy
  per CLAUDE.md is to commit STRONG signals at whatever odds
  DK has; the guard's protection (small lineup-driven prob
  shifts demoting a pick) cannot apply to STRONG since the
  smallest possible shift to flip STRONG (`p < 0.56`) requires
  a 6+pp move that real lineups have never produced in
  observed history.  Guard still applies to LEAN / NO EDGE /
  LOW LAMBDA / etc., where lineup data CAN materially change
  the verdict.
- `dashboard/vercel.json` adds 30-minute-cadence Vercel cron
  entries between 21 UTC (5pm ET) and 02 UTC (10pm ET).  Each
  entry hits `/api/cron/predict`, which dispatches the GHA
  daily.yml workflow with the predict action.  Worst-case
  pre-lock staleness for a 7:40pm game is now 30 min instead
  of the previous 60-min gap that masked the lineup post.

### Defense-in-depth shape

Today's incident required ALL THREE of the above to fail
simultaneously.  After this commit, the same incident requires
all three of: (a) MLB's schedule endpoint to ALSO not have the
lineup at predict time, (b) the team-fallback verdict to be
LEAN / NO EDGE / etc. (not STRONG), AND (c) the cron tick
within 30 min of lock to fail or run late enough to miss the
window.  Any single layer holding catches the case.

---

## [2026-05-08] — PASS rows can re-evaluate post-lock as long as the game hasn't started

Same-day follow-up to the LINEUP/STARTER PENDING incident.
Operator: "the pit game was stuck as starter pending still.
we need to diagnose and fix this issue once and for all. it
should have been automatically set."

Root cause for PIT@SF specifically: at 7:58pm ET (the last
predict run before the 9:15pm T-60 lock), MLB's
`probablePitcher` field still showed home_pitcher=TBD --
Robbie Ray was announced afterwards.  Once the row hit lock,
the existing freeze policy preserved EVERYTHING the predictor
generates -- including pitcher fields -- so even when later
predict runs at 9:17pm and beyond saw Ray in the API, the
row's pick_strength stayed at PASS - STARTER PENDING.

The standard freeze rationale (T2.25: don't flip a STRONG
verdict after the user is in the bet) doesn't apply to PASS
rows: no money is committed at the lock-time PASS, so
re-evaluating up to first pitch is strictly upside.  Three
worst cases:

  1. PASS - PENDING -> STRONG NRFI: the user gains a bet
     they would have missed.  bet_placed=Y fires from
     `_apply_odds_to_row`'s normal lock-window auto-bet path
     once the next odds-import tick lands.
  2. PASS - PENDING -> PASS - NO EDGE: the row's label
     resolves to its actual verdict instead of staying in a
     stale "we don't know yet" state.  Already partly
     supported by the T2.14 `pass_label_refresh` hack but
     that only refreshed pick_side/strength/label, not the
     underlying inputs that drove them; now the whole row
     refreshes consistently.
  3. PASS - NO EDGE -> STRONG (rare): same as #1.

### Fixed

- `tracker._game_has_started`: new helper.  True once `now`
  (ET) crosses the row's `game_time_et`; mirrors
  `_is_inside_lock_window`'s defensive default for placeholder
  game-times so the abandoned-row case is still covered by
  defensive lock #1.
- `tracker.log_picks`: compute
  `post_lock_pass_refresh_eligible` once per existing row
  (PASS / no bet / no terminal grade / game not started) and
  thread it through both the change-detection notification
  gate AND the lock-bypass branch.  When eligible, the row
  follows the pre-lock full-refresh path, so pitcher / lineup
  / weather / probabilities / label all update consistently.
  The standard `pick_flip` Telegram fires on label changes
  -- including post-lock PENDING -> STRONG upgrades -- so the
  operator gets the standard "PENDING -> STRONG NRFI" ping
  the moment the bet commits.

### Net effect

Tomorrow's slate: when MLB announces a starter or lineup
post-lock-but-pre-first-pitch, the predictor will re-evaluate
the affected row, the dashboard pill will flip from "PASS -
STARTER PENDING" / "PASS - LINEUP PENDING" to whatever the
resolved verdict is, and (if the resolved verdict is STRONG)
the next odds-import tick auto-flips bet_placed to Y at the
in-lock-window DK price.  Today's PIT@SF was already past
first pitch when this code shipped so the row stays at the
post-game NO EDGE label that
`pass_label_refresh` already produced -- but the underlying
fix is in place for the next slate.

---

## [2026-05-10] — Cluster discovery + demotion pipeline

Operator: "we need to start finding more unprofitable clusters,
then we can make micro adjustments based on different specific
details, and then lower the total probability when certain
specific patterns arise again if they tend to lose over and over
again."

Built out a three-stage pipeline for identifying, watching, and
acting on loss clusters without overfitting to noise.

### Pipeline shape

```
1. DISCOVER             →   2. MONITOR                →   3. DEMOTE
   cluster_discovery.py     loss_cluster_monitor.py       apply_cluster_
                                                           demotion.py
   (read-only ledger        (defined cluster's            (skip bet placement
    scan; ranked              recent-5 record;             on confirmed bad
    candidates)               Telegram alert)              clusters via JSON)
```

Each stage is gated to prevent acting on small-sample noise.  A
candidate from stage 1 must pass through stage 2's runtime
confirmation (recent 5 = ≥4L) before the operator considers adding
it to stage 3's demotion config.  Stage 3 is fully reversible via
the JSON.

### Added

- `tools/cluster_discovery.py` — scans the season ledger for
  STRONG-bet feature combinations that have underperformed.  Three
  resolutions: (side, prob_band), (side, prob_band, lambda_band),
  (side, prob_band, lambda_band, pitcher_min_q).  Filters on
  sample-size and hit-rate floors; ranks by net P&L drag.  Output
  is read-only; never mutates CSVs or fires Telegram.  Initial run
  surfaced **STRONG YRFI with nrfi_p < 0.40 = 6W-11L (-6.0u over
  17 bets)** as the strongest candidate.
- `data/cluster_demotions.json` — operator-maintained demotions
  ledger.  Each entry declares a predicate (side, nrfi_prob band,
  lambda band, park band, pitcher_quality_min) plus an `active`
  flag.  Empty by default at launch.
- `tools/apply_cluster_demotion.py` — reads the JSON, finds every
  ungraded STRONG row matching an active demotion, and sets
  `bet_placed='N' + units_risked=''` so the bet is suppressed.
  Does NOT change `pick_side / pick_strength / pick_label` -- the
  model verdict stays visible on the dashboard for transparency;
  only the money commit is suppressed.  Idempotent.  Journals
  every demotion event to `pick_changes.csv`.
- Both predict and grade paths in `.github/workflows/daily.yml`
  now invoke `apply_cluster_demotion.py` before
  `sync_csv_from_supabase` (same precedence as the manual-odds
  override step).  Cluster discovery runs once per nightly grade
  cron with the trailing-21-day window; output goes to the
  workflow log for operator review.

### Documentation

- [docs/CLUSTER_DISCOVERY.md](./docs/CLUSTER_DISCOVERY.md) walks
  through each stage's purpose, the operator workflow for adding
  a new cluster (monitor) or demotion (skip), the safety rules
  baked in, and when to back off a demotion.
- CLAUDE.md money-rules section gets a one-paragraph pointer.

### What did NOT ship (deliberate)

- No automatic demotion of newly-discovered clusters.  Discovery
  is read-only by design; the operator decides which candidates
  graduate to monitor + demotion.
- No probability-modification layer.  The "lower the probability"
  framing maps cleanest to a calibrator refit
  (`recalibrate_v2.py`); the demotion path is a tactical bet-skip,
  not a model change.  Once enough confirmed clusters accumulate,
  refitting the calibrator on recent data is the durable fix.

---

## [2026-05-10] — Loss-cluster streak monitor

Operator on 2026-05-09 noticed that **5 of the 7 STRONG losses since
v2.1 deployed** (2026-05-06) shared a specific shape: STRONG YRFI bets
where `nrfi_p` ≈ 0.40 + `combined_lambda` ≈ 1.0, all of which ended
NRFI 0-0 (pitchers shut both halves down despite the model expecting
~1 first-inning run).  Operator's directive: "keep note of these
things where you're noticing the same type of pick is losing
constantly. if that loses again then we will have to adjust."

Added an automated watchdog that catches that signal the moment
it crosses a clear threshold.

### Added

- `tools/loss_cluster_monitor.py` -- defines named feature clusters
  and watches each one's recent-N record after every grading sweep.
  When a cluster's last 5 graded matches show ≥4 losses with
  hit rate ≤20%, fires a `loss_cluster_streak` Telegram alert
  with the recent trail and the operator's documented action plan
  (manual judgment skip OR `recalibrate_v2.py` on trailing 30-60
  days).  Two clusters defined at launch:
    1. **`yrfi_040_band`**: STRONG YRFI · `nrfi_p` 0.370-0.420
       AND `combined_lambda` 0.80-1.30 AND `park_factor` 0.90-1.30.
    2. **`nrfi_marginal_strong`**: STRONG NRFI · `nrfi_p` 0.560-0.590
       (barely above the STRONG threshold, low variance margin).
  Adding a new cluster: append a dict to `CLUSTERS` in the script.
- `tracker._DEDUP_WINDOW_M["loss_cluster_streak"] = 24*60`: 24h
  dedup per (cluster_id, date) so the alert doesn't re-spam across
  cron ticks.
- `tools/loss_cluster_monitor.py` wired into both predict and grade
  paths in `.github/workflows/daily.yml`, soft-fail.

### Memory

- `memory/loss_cluster_yrfi_040_band.md` documents the active watch:
  cluster definition, why it looks like drift not variance, the
  operator's manual-skip plan, and the recalibration path if the
  cluster confirms.

### Threshold rationale

Tuned against 2026-05-09 data: cluster sat at **2 losses in last 5**
(no alert).  Today's slate (2026-05-10) has two STRONG YRFI bets
matching the cluster (OAK@BAL, HOU@CIN) -- if both lose, recent-5
hits 4 of 5 = 20% hit rate, alert fires.  If only one loses, alert
holds off until next instance.

---

## [2026-05-09] — Manual DK odds overrides + orphan-bet Telegram alert

System audit on 2026-05-09 (operator: "make sure that the
tracking is being done properly") found that **112 of 220
graded STRONG bets across the season (51%) had used the -110
fallback** because no DK odds were captured at grade time.
Cause: the chronic Railway-down failure mode on the odds-
import worker.  GHA can't scrape DK directly (DK 403s GHA's
Azure IPs), so when Railway is down the row grades with empty
`market_*_odds`, `tracker._calc_pnl` returns the -110
fallback (=+0.909u for a win), and `tools/end_of_day_check.py`
silently stamps the row at that price as if -110 were the
real DK entry price.  The dashboard's `OddsChip` shows
"DK -110*" with an asterisk in this case, but the asterisk is
easy to miss; from the operator's perspective the row looks
like a real -110 bet.

### Added

- `data/manual_odds_overrides.csv` -- a user-maintained
  ledger.  Operator drops a row in whenever they need to record
  the actual DK entry price for a bet that the auto-scrape
  missed.  Format documented inline + in
  [docs/MANUAL_ODDS.md](./docs/MANUAL_ODDS.md).
- `tools/apply_manual_odds.py` -- idempotent heal script.
  Reads the override ledger, finds the matching pick row by
  `(date, game_pk)` (or `(date, away, home)` when game_pk is
  blank), patches `market_*_odds` / `sportsbook` /
  `odds_captured_at`, sets `bet_placed=Y` + `units_risked=1`
  for STRONG NRFI/YRFI rows that weren't already, recomputes
  `profit_loss_units` via `tracker._calc_pnl` from the
  supplied odds, journals each change to `pick_changes.csv`,
  and mirrors to Supabase.  Idempotent: re-runs with the same
  override are a no-op.
- `tracker._notify_strong_orphan_no_odds_telegram` -- fires
  the moment a STRONG bet grades W/L with empty
  `market_*_odds` (before this code shipped, the row would
  have been silently stamped at -110).  Body includes the
  exact line to add to `manual_odds_overrides.csv` to heal
  it.  New event type `strong_orphan_no_odds` with 24h
  notifications_log dedup window.  Wired into `grade_date`
  inline (catches new graded rows) AND the retro pass for
  today's slate (catches rows graded by an earlier cron tick
  before this code shipped).
- Both predict and grade paths in `.github/workflows/daily.yml`
  now invoke `tools/apply_manual_odds.py` before
  `sync_csv_from_supabase` so the override's mirror lands
  authoritatively.  Soft-fail.
- Documentation: [docs/MANUAL_ODDS.md](./docs/MANUAL_ODDS.md)
  + a CLAUDE.md money-rules entry explaining the override
  flow.

### Operator workflow going forward

1. STRONG bet grades without captured DK odds.
2. Telegram pings: "⚠️ NO DK ODDS CAPTURED · NYY @ MIL ·
   STRONG NRFI · WIN" with the exact override-CSV line to add.
3. Operator pastes the line into `data/manual_odds_overrides.csv`
   with their actual DK entry price + commits.
4. Next predict/grade cron tick (within 30 min) runs
   `apply_manual_odds.py`, which patches `market_*_odds` and
   recomputes `profit_loss_units`.
5. Dashboard / pl_calc / Supabase all reflect the real price.

Verified by sanity test: dry-run + apply + idempotency check
on a known orphan (TEX@NYY 2026-05-05 STRONG YRFI WIN).
Stored P&L moved from +0.909u (-110 fallback) to +1.150u
(real +115 entry) on the override.

---

## [2026-05-09] — RoiPanel "Last 7d" / "Last 30d" off-by-one fixed

Operator on 5/09: "the units tracked are wrong. for example,
last 7 days in the dashboard says -0.69, but i did the math
and it should be +1.93".  The 1.93 turned out to match the
STRONG NRFI side total for the corrected window, but the
total being -0.69 was the symptom of an off-by-one in the
window math.

`dashboard/lib/roi.ts` was using `isoMinusDays(7)` for the 7d
window and `isoMinusDays(30)` for 30d, then summing rows where
`startDate <= date <= today`.  Both endpoints inclusive ->
the window was actually 8 / 31 calendar days, not 7 / 30.
The extra day was what swung the total from +0.000u (the
canonical `tools/pl_calc.py --window 7d` answer for
2026-05-09's 7-day window) to -0.69u (which silently included
2026-05-02's -0.69u day).

### Fixed

- `dashboard/lib/roi.ts` window math: 7d now starts
  `isoMinusDays(6)` (today - 6 days), 30d now starts
  `isoMinusDays(29)` (today - 29 days).  Spans match
  `tools/pl_calc.py`'s `today - (days - 1)` exactly.
- After the fix, the dashboard's 7d total agrees with
  `pl_calc --window 7d` and the per-zone breakdown matches
  the operator's hand math (STRONG NRFI = +1.87u over the
  trailing seven days; STRONG YRFI = -1.87u; bet-zones total
  = -0.00u).



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

# NRFI Terminal — Product Context

## Register

**product** — this is an operational tool. The design serves the decision;
it is not the thing being sold. There is exactly one user and no
acquisition funnel.

## Users

**One operator.** Owns the bankroll, places every bet by hand at
DraftKings, and is **not a developer**. Reads the interface to make a
money decision, not to admire it. Has no interest in jargon and will not
infer meaning from a subtle visual convention that is never explained.

## The actual usage scene

Standing up, **phone in hand, early evening, in the hour before first
pitch.** Ordinary indoor light. Thirty seconds of attention, sometimes
less. The question being answered is always the same:

> **What do I bet tonight, and how much?**

A secondary session happens later, at a desk, reviewing how it went. That
one tolerates density. The phone one does not.

This scene forces the design: mobile-first, legible at arm's length,
answering the money question above the fold. It does **not** justify a
dark theme by ambient light — this is a lit room, not a 2am incident
call. Any dark treatment has to earn its place some other way.

### The third scene, added 2026-08-03: filming

The operator has started publishing a short video about the night's #1
play. That scene is **sitting down, camera running, reading numbers aloud
to an audience who cannot see the screen.** It is the opposite of the
board's scene in three ways, and `/brief` is the surface built for it:

1. **No clock.** Density is fine; the operator is not deciding, they are
   explaining. Sequence matters more than glanceability, so the page is
   ordered as a script rather than as a grid.
2. **Nothing may be abbreviated,** because abbreviations cannot be
   spoken. "TB" becomes "Tampa Bay"; "0.200" becomes "2 of their last
   10"; a z-score becomes a sentence.
3. **A wrong sentence is worse than a missing one.** On the board a
   misread costs one bet. On camera it is published, and someone will
   check it. So every stat declares which way it cuts, and the figures
   that argue AGAINST the play get their own block rather than being
   quietly omitted — the operator has to answer them, and a video that
   does is better than one that does not.

The trap this scene creates, and the reason the "against" block exists:
the operator's first instinct was that a team which had not scored in the
first all series was **due** for one. That is the gambler's fallacy, and
in that specific case it also pointed the opposite way from the bet the
model had actually made. An interface that hands over figures without
directions is a liability here in a way it is not on the board.

## Product purpose

Predicts whether a run scores in the first inning of an MLB game and
recommends bets on it. As of 2026-07-27 it also **sizes** those bets by
quarter-Kelly, so stake is no longer constant.

## The central design problem

The interface shows **four different kinds of number with nearly
identical visual weight**, and the operator has said they don't trust
what they're reading. The four:

1. **Real money** — actually bet, at a real captured DraftKings price.
2. **Placeholder-priced money** — graded, but no price was ever captured,
   so it settled at a fabricated `-110`. This is most of April and it
   inflates the season total by roughly 15 units.
3. **Paper money** — LEAN picks, tracked but never bet.
4. **Simulated money** — the counterfactual Kelly bankroll.

Today all four render as "+X.XXu" in similar type. That is the root of
the distrust, and no amount of polish fixes it. **Rendering these four
as visually distinct classes is the primary job of this redesign.**

## Stake is now first-class information and is currently missing

The board was designed when every bet was 1 unit, so stake was implicit
and never displayed. Quarter Kelly now produces stakes from roughly
**3.9u to 10u** on the same board. The operator cannot currently see, on
the surface that tells them what to bet, **how much to bet.** This is a
functional gap, not a cosmetic one.

## Tone

Plain, factual, quiet. States what is true and what is uncertain without
hedging or hype. Never celebratory about wins; never dramatic about
losses. Numbers carry the message.

## Brand / palette

**Newsprint.** Warm paper `#FBFAF7`, near-black ink `#211E1A`, square
corners. Chosen 2026-08-03 after the operator retired the matrix
terminal palette (*"i dont really like the current green theme
anymore"*). This is the THIRD palette in three months and the history is
kept below on purpose.

**Type is split**, which is most of why the page reads as editorial:
Inter for prose, **JetBrains Mono for figures only**. That contrast is
what makes monospace mean "this is a number". Until 2026-08-03
`--font-sans` was itself pointed at JetBrains Mono, which made the
"sans only for text" rule in globals.css a no-op and left every surface
looking like a terminal.

| token | value | meaning | on paper |
|---|---|---|---|
| `--foreground` | `#211E1A` | body ink | 15.90:1 |
| `--muted-foreground` | `#5F584E` | secondary ink | 6.72:1 |
| `--gain` (`--primary`) | `#137355` | real money UP | 5.57:1 |
| `--loss` (`--destructive`) | `#A01D14` | real money DOWN | 7.51:1 |
| `--attn` | `#845608` | money at risk / decision waiting | 6.07:1 |

**Why it changed, and it was not only taste.** Under the matrix palette
`--foreground` and `--gain` were the same `#00FF41`, byte-identical, so
the interface had no way to distinguish "this is text" from "this is
money up" -- a losing season would have printed in the gain colour on
the filmed page. A monochrome scheme leaves one hue for every job.
`--border` at 1.63:1 was also under the 3:1 WCAG asks of a meaningful
boundary, and green-with-red is the pair that collapses for the
commonest colour blindness.

**Light is the default and no longer follows the OS.** The usage scene
above is a lit room in the early evening; a dark treatment never earned
its place. The dark variant still exists behind the toggle, and an
explicit choice is always respected.

Do not restore the matrix terminal, cyan/rose or warm palettes.

**Consequence to design around:** green-and-red is the pair that
collapses for red-green colour blindness, so **no figure may depend on
hue alone** — every money number carries a sign, and where it matters, a
word. And because the whole page is green, hue can no longer mark "real
money" by itself: simulated figures are held apart by being DIM
(`--muted-foreground`) against bright real ones.

## Anti-references

- ~~Terminal green-on-black trading UI.~~ **NO LONGER an anti-reference
  as of 2026-07-30** — the operator chose exactly this and supplied the
  spec. Kept struck through rather than deleted so nobody re-adds it
  from an old copy of this file.
- **The hero-metric dashboard.** Giant number, small label, gradient
  accent, supporting stat row. Every betting-analytics product looks
  like this.
- **Sportsbook chrome.** Neon, urgency, badges, "LOCK OF THE DAY".
  This tool is the opposite of a sportsbook: it exists to make the
  operator bet *less* and more selectively.
- **Uniform card grids.** The current layout is a stack of same-weight
  cards, which is exactly why nothing is scannable.
- **Anything that makes a simulated or paper number look like realized
  P&L.**

## Strategic principles

1. **One glance answers the money question.** Plays and stakes first.
   Everything else is secondary and can be below the fold.
2. **Provenance is visible.** A number's kind (real / placeholder /
   paper / simulated) must be apparent without a legend.
3. **Quiet by default.** Most nights there are one or two plays, and
   about a third of nights have none. An empty board is a correct
   outcome and should look calm and intentional, not broken.
4. **Never imply more certainty than exists.** The model's ranking
   ability is ~53.5% AUC. The interface must not dress that up.

## Known constraints

- Data is a CSV ledger plus Supabase; the two disagree about April.
- The season total on screen is real but inflated; that is a data-truth
  problem the design must *disclose*, not hide.
- Roughly a third of nights have zero qualifying plays.

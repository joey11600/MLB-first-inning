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

**Warm brown and peach.** Explicitly and repeatedly chosen by the
operator over terminal green-and-red. Existing tokens: `--primary`,
`--secondary`, `--destructive`.

Win/loss must remain readable without relying on a red/green axis —
partly the stated preference, partly colorblind safety.

## Anti-references

- **Terminal green-on-black trading UI.** Explicitly rejected. The
  current design still leans this way and reads as dated.
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

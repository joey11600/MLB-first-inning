/**
 * THE UNIT MODEL, and the type that stops it being broken again.
 *
 * ONE UNIT IS ONE PERCENT OF BANKROLL. The bankroll is always 100
 * units. Operator decision, 2026-07-30.
 *
 * Growth changes the DOLLAR VALUE of a unit, never the number of units
 * bet. That is what makes a published stake work: the Kelly fraction is
 * bankroll-independent, so "6.8u" means 6.8% of your own bank whether
 * your bank is $1,000 or $25,000, and every follower stakes the same
 * number. The operator is selling these picks; a stake that depended on
 * the operator's bankroll would be useless to everyone else.
 *
 * ============================================================
 * THE CONSEQUENCE: CUMULATIVE UNITS ARE NOT MONEY
 * ============================================================
 *
 * A 1-unit win when the unit is worth $100 is not the same money as a
 * 1-unit win when the unit is worth $150. So units from different dates
 * are quantities in different currencies, and adding them produces a
 * number that means nothing.
 *
 * It is not a small error. Over the last seven days of the replay:
 *
 *     naive sum of daily P&L      -13.17u
 *     what the bank actually did  223.07 -> 209.89, i.e. -5.91%
 *     honest figure                -5.91u
 *
 * The sum is 2.2x the truth, and it is the number that every "just add
 * the column" instinct produces. Published to a subscriber it would
 * overstate their loss; in a winning stretch it overstates their gain,
 * which is the direction that gets someone sued.
 *
 * WHAT IS STILL FINE, and most figures are:
 *
 *   - one bet's stake            6.80u
 *   - one bet's result          +5.44u
 *   - one night's P&L           -4.61u
 *   - one night's exposure      12.00u   (the daily cap is 15 UNITS)
 *
 * Anything confined to a single point in time is a real quantity in a
 * single currency. The rule is about ADDING ACROSS TIME, not about
 * units.
 *
 * WHAT TO PRINT INSTEAD, and there are exactly two right answers:
 *
 *   1. BANK GROWTH   `formatBankGrowth(100, 209.89)` -> "100u → 209.89u"
 *   2. A RETURN      `formatReturn(1.0989)`          -> "+109.89%"
 *
 * and one convenience that is both at once: `returnAsUnits()`. Because
 * the bank is 100 units by definition, a return of -5.91% IS -5.91
 * units, so the percentage can be printed with a "u" and stay true.
 * That is the only way a unit figure may span more than one day.
 *
 * ============================================================
 * HOW THIS FILE MAKES THE MISTAKE FAIL LOUDLY
 * ============================================================
 *
 * `CumulativeUnits` is a branded number. Any quantity produced by
 * adding units across time is typed as one, and `formatUnits()` REFUSES
 * to accept it -- not at runtime, at COMPILE time, so `next build`
 * fails and Vercel never deploys it. The conditional-type parameter
 * below collapses to `never` when the argument carries the brand, which
 * is what produces the error.
 *
 * The failure message a future author sees is the point. There is no
 * renderer for `CumulativeUnits` anywhere in this codebase and there
 * must never be one: the fix is always to reach for the bank endpoints
 * or the return, never to cast the brand away. If you are here because
 * the compiler stopped you, `asCumulative()` is not your escape hatch
 * -- it is the label you put ON the thing you must not print.
 */

/* ============================================================
 * CORRECTION, 2026-08-03: THE RULE WAS TOO BROAD
 * ============================================================
 *
 * Operator: *"units profit should be available. that doesnt make sense.
 * every bet should be making the same amount of units as a person with a
 * $1000 bankroll, or someone with a $10,000 bankroll."*
 *
 * They are right, and the original framing above conflated two things.
 * What actually breaks a unit sum is not the passage of time, it is the
 * unit's DOLLAR VALUE MOVING between the bets being added. That happens
 * only under compounding.
 *
 *   FIXED BASIS — the unit is worth the same on every bet, because the
 *   bettor never re-sizes. 1u is 1% of the bank they STARTED with. Sums
 *   are exact, and identical for a $1,000 and a $10,000 bankroll, which
 *   is precisely the operator's point. This is also what most people
 *   selling picks publish, and what a subscriber who does not re-size
 *   actually experiences. LEGITIMATE. See `FlatUnits`.
 *
 *   MOVING BASIS — the unit is 1% of the CURRENT bank, so it is worth
 *   more after a win and less after a loss. Adding those is adding
 *   amounts in different currencies. STILL FORBIDDEN. See
 *   `CumulativeUnits`.
 *
 * So there are three honest season figures and the #1-pick section now
 * prints all three side by side:
 *
 *   flat 1u a bet          +6.71u    the model's edge, size removed
 *   at the published stakes +0.58u   what a non-compounding follower made
 *   compounding            -0.31%    what the operator's own bank did
 *
 * They disagree in SIGN, which is exactly why the basis has to be named
 * on screen every time rather than left for the reader to assume.
 * `formatUnits` now rejects BOTH brands, so no summed figure of either
 * kind can reach the page without its basis stated at the call site.
 * ============================================================ */

declare const CUMULATIVE_BRAND: unique symbol;
declare const FLAT_BRAND: unique symbol;

/**
 * Units added while the unit's value was MOVING: a compounding bank.
 * NOT a money quantity, has no formatter, and cannot be passed to
 * `formatUnits`. Use the bank endpoints or a return instead.
 */
export type CumulativeUnits = number & { readonly [CUMULATIVE_BRAND]: true };

/**
 * Units added on a FIXED basis: every unit in the sum was worth the same
 * money, because the stake was never re-sized against a moving bank.
 *
 * This IS a quantity and it IS additive. It is bankroll-independent in
 * the sense that matters commercially: 1 unit is 1% of the bank the
 * follower started with, so "+6.71u" means +6.71% of starting bankroll
 * whether that was $1,000 or $10,000.
 *
 * It has its own formatter rather than sharing `formatUnits`, so that
 * the basis is declared at every call site. A season total and a single
 * night's P&L must never be printed by the same function: they look
 * identical on screen and mean different things.
 */
export type FlatUnits = number & { readonly [FLAT_BRAND]: true };

/**
 * Mark a sum as taken on a FIXED unit value.
 *
 * Only correct when nothing in the sum was sized against a compounding
 * bank. Summing `profit_loss_units` from Kelly-sized bets qualifies ONLY
 * if you are reporting what a follower who never re-sizes experienced,
 * which is a real question and the one this exists to answer. It does
 * NOT describe the operator's own bank; that compounds, and its answer
 * is `formatBankGrowth`.
 */
export function asFlat(n: number): FlatUnits {
  return n as FlatUnits;
}

/**
 * A unit total on a fixed basis, e.g. "+6.71u".
 *
 * Always label the basis in the surrounding copy. The three season
 * figures for one series can differ in sign (see the note above), so an
 * unlabelled "+6.71u" is not an answer to any question.
 */
export function formatFlatUnits(n: FlatUnits): string {
  if (!Number.isFinite(n)) return EM_DASH;
  if (Math.abs(n) < 0.005) return "0.00u";
  return `${n > 0 ? "+" : MINUS}${Math.abs(n).toFixed(2)}u`;
}

/**
 * Mark a figure as "produced by adding units across time".
 *
 * Call this at the point of the SUM, not at the point of display. The
 * whole value of the brand is that it travels with the number, so a
 * caller three files away is stopped without having to know where it
 * came from.
 */
export function asCumulative(n: number): CumulativeUnits {
  return n as CumulativeUnits;
}

/**
 * A unit figure for ONE point in time: one bet, or one night.
 *
 * The conditional collapses the parameter to `never` when `T` carries
 * EITHER brand, so passing any summed figure is a type error. Plain
 * numbers are unaffected and need no cast.
 *
 * `FlatUnits` is rejected here even though it is a legitimate quantity:
 * it has its own formatter so that a season total can never be printed
 * by the same function as a single night's P&L. On screen they are
 * indistinguishable, and the whole point of this module is that the
 * reader can tell what kind of number they are looking at.
 */
export function formatUnits<T extends number>(
  n: T & (T extends CumulativeUnits | FlatUnits ? never : unknown),
): string {
  if (!Number.isFinite(n)) return EM_DASH;
  if (Math.abs(n) < 0.005) return "0.00u";
  // U+2212 MINUS, not a hyphen, so a negative reads as a number.
  return `${n > 0 ? "+" : MINUS}${Math.abs(n).toFixed(2)}u`;
}

/** A stake or a level -- never signed, because nobody risks -6.80u. */
export function formatLevel(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return EM_DASH;
  return `${n.toFixed(digits)}u`;
}

/**
 * THE FIRST RIGHT ANSWER: where the bank started and where it ended.
 *
 * Two levels, not a sum. Immune to the re-basing problem because a bank
 * level is a fact about one instant.
 */
export function formatBankGrowth(start: number, end: number, digits = 2): string {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return EM_DASH;
  return `${start.toFixed(digits)}u → ${end.toFixed(digits)}u`;
}

/** THE SECOND RIGHT ANSWER: a fraction, e.g. 0.1098 -> "+10.98%". */
export function formatReturn(fraction: number, digits = 2): string {
  if (!Number.isFinite(fraction)) return EM_DASH;
  if (Math.abs(fraction) < 0.00005) return "0.00%";
  return `${fraction > 0 ? "+" : MINUS}${Math.abs(fraction * 100).toFixed(digits)}%`;
}

/**
 * A return, printed in units.
 *
 * Legitimate ONLY because the bank is 100 units by definition, so a
 * percentage and a unit count are the same number. This is the single
 * sanctioned way to show a multi-day figure with a "u" on it, and it is
 * why it takes a FRACTION rather than a unit total -- there is no way
 * to call it with a naive sum by mistake.
 */
export function returnAsUnits(fraction: number): string {
  if (!Number.isFinite(fraction)) return EM_DASH;
  const u = fraction * 100;
  if (Math.abs(u) < 0.005) return "0.00u";
  return `${u > 0 ? "+" : MINUS}${Math.abs(u).toFixed(2)}u`;
}

/**
 * The return between two bank levels. `null` when the opening bank is
 * unusable, rather than an Infinity that would render as garbage.
 */
export function bankReturn(start: number, end: number): number | null {
  if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0) return null;
  return end / start - 1;
}

export const MINUS = "−";
export const EM_DASH = "—";

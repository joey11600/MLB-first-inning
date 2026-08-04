/**
 * THE PRICE LADDER — "bet up to ‑XXX", and how much at each step.
 *
 * WHY A LADDER AND NOT ONE NUMBER. Operator, 2026-08-04: *"shouldn't it
 * be 'Bet up to -XXX' so people know to not bet over that number?"*
 * Right — the limit is the instruction, and the card never carried it.
 * But a single limit is an incomplete instruction, because the stake is
 * a FUNCTION of the price: an 8u play at ‑130 is a 3u play at ‑200 and
 * no play at all past ‑234. Publishing only the limit invites someone
 * to lay ‑200 for the full eight units, which is a bigger error than
 * the one the limit fixes. So every rung carries its own stake.
 *
 * WHERE THE LIMIT COMES FROM, AND WHY IT IS NOT BREAK-EVEN. The obvious
 * limit is the price where the edge hits zero (‑248 on the 2026-07-31
 * CWS@TB play). That is the price at which the bet becomes WORTHLESS,
 * so publishing it tells a subscriber to lay a number with nothing left
 * in it. `passAt` is instead the worst price still worth a FULL UNIT of
 * quarter-Kelly, which on that play is ‑234.
 *
 * THAT DELIBERATELY STOPS SHORT OF THE SHIPPED STAKE RULE, and the gap
 * is not an oversight — do not "fix" it. `stakeUnitsFor` keeps betting
 * past `passAt`, because KELLY_ROUNDED_FLOOR lifts a sub-half-unit
 * stake to 0.5u rather than discarding it (that floor recovers 15 of 16
 * bets plain rounding would have dropped, see tracker.py). The
 * consequence is real 0.5u money on an edge of almost nothing: at
 * ‑140/‑141/‑142 a 58.9% pick wants 0.34u / 0.24u / 0.13u and stakes
 * 0.5u at all three. Those are exactly the bets a subscriber would be
 * taking AT the published limit, so the ladder ends at the last full
 * unit. Operator's call, 2026-08-04.
 *
 * The rungs themselves come from `stakeUnitsFor`, not from a private
 * formula, so a rung can never disagree with the stake chip beside it.
 * That is the same one-rule-one-answer discipline as lib/top-pick-rank.
 *
 * Pure arithmetic, and it must stay that way: BoardRow is a CLIENT
 * component, so anything reachable from here ends up in the browser
 * bundle. lib/kelly-sim has no imports of its own, which is why it is
 * safe to pull `stakeUnitsFor` from it.
 */
import { stakeUnitsFor, UNIT_RULE } from "./kelly-sim";

export interface LadderRung {
  /** American price. Bet this many units at this price or better. */
  odds: number;
  /** Units to stake — exactly what `stakeUnitsFor` returns here. */
  units: number;
}

export interface PriceLadder {
  /** The price the card was written at. */
  cardOdds: number;
  /** Stake at the card price. */
  cardUnits: number;
  /** Card price first, then each whole-unit step down. */
  rungs: LadderRung[];
  /** Worst price still worth a full unit. Anything worse: no bet. */
  passAt: number;
  /**
   * True when the card price is ALREADY past the full-unit line, i.e.
   * the system is staking under 1u. There is no room to chase: take it
   * at the card price or better, or not at all. Two of the ten plays in
   * the 2026-08-04 sample were like this (0.5u and 1.0u cards), so this
   * is a normal state, not an error.
   */
  noRoom: boolean;
}

/** Profit per unit risked. */
function payoutOf(american: number): number {
  return american > 0 ? american / 100 : 100 / Math.abs(american);
}

/**
 * Quarter-Kelly units BEFORE the 0.5u floor and whole-unit rounding.
 * `passAt` is derived from this rather than from `stakeUnitsFor`,
 * because the floor is precisely what we are declining to publish.
 */
function rawQuarterKellyUnits(p: number, american: number): number {
  const b = payoutOf(american);
  if (!(b > 0) || !(p > 0 && p < 1)) return 0;
  const full = Math.max((p * b - (1 - p)) / b, 0);
  return Math.min(full * UNIT_RULE.fraction, UNIT_RULE.maxStakeUnits / 100) * 100;
}

/**
 * Worst American price at which quarter-Kelly still wants `units`.
 *
 *   raw = 100 · f/4  and  f = p − (1−p)/b   ⟹   b = (1−p)/(p − units/25)
 *
 * Solved rather than scanned so the limit is exact. Returns null when
 * the model probability cannot support that stake at any price.
 */
function worstPriceFor(p: number, units: number): number | null {
  const denom = p - units / (100 * UNIT_RULE.fraction);
  if (!(denom > 0)) return null;          // p too low to ever want this much
  const b = (1 - p) / denom;
  if (!Number.isFinite(b) || b <= 0) return null;
  return b >= 1 ? 100 * b : -100 / b;
}

/** Step one cent worse for the bettor, hopping the ±100 discontinuity. */
function nextWorse(american: number): number {
  if (american > 100) return american - 1;
  if (american === 100) return -101;      // +100 and -100 are the same price
  if (american <= -100) return american - 1;
  return -101;                            // -100 < o < 100 is not a real price
}

/**
 * Build the ladder for a pick, or null when there is nothing to show
 * (bad inputs, or the price already carries no edge at all).
 *
 * @param p     model probability of the side being BET (not p(no run) —
 *              callers on a YRFI pick must pass the complement, exactly
 *              as StakeChip does).
 * @param cardOdds  the price the card was written at.
 */
export function buildPriceLadder(p: number, cardOdds: number): PriceLadder | null {
  if (!Number.isFinite(p) || p <= 0 || p >= 1) return null;
  if (!Number.isFinite(cardOdds) || cardOdds === 0) return null;
  if (cardOdds > -100 && cardOdds < 100) return null;

  const cardUnits = stakeUnitsFor(p, cardOdds);
  if (cardUnits <= 0) return null;

  const oneUnitPrice = worstPriceFor(p, 1);
  if (oneUnitPrice == null) return null;
  // CEIL, NEVER ROUND. The solve returns a fractional price (‑133.6);
  // rounding it to ‑134 publishes a limit one cent PAST the full-unit
  // line, so the worst price we tell people to take is one we would not
  // take ourselves. Math.ceil lands on the worst ACCEPTABLE integer in
  // both directions: ceil(-133.6) = -133 (better price, still ≥1u) and
  // ceil(120.4) = 121 (bigger payout, still ≥1u).
  const passAt = Math.ceil(oneUnitPrice);

  // A card priced at or beyond the full-unit line has no room to chase.
  const noRoom = cardUnits < 1 || payoutOf(cardOdds) <= payoutOf(passAt);
  if (noRoom) {
    return { cardOdds, cardUnits, rungs: [{ odds: cardOdds, units: cardUnits }], passAt, noRoom: true };
  }

  const rungs: LadderRung[] = [{ odds: cardOdds, units: cardUnits }];
  let seen = cardUnits;
  let o = cardOdds;
  // Bounded walk: the ±100 hop plus a deep tail is a few hundred steps,
  // and the payout comparison below terminates well before the cap. The
  // cap only exists so a bad input cannot spin the browser's main thread.
  for (let i = 0; i < 5000; i++) {
    o = nextWorse(o);
    if (payoutOf(o) < payoutOf(passAt)) break;
    const u = stakeUnitsFor(p, o);
    if (u < 1) break;                     // the 0.5u tail is not published
    if (u < seen) {
      rungs.push({ odds: o, units: u });
      seen = u;
    }
  }
  return { cardOdds, cardUnits, rungs, passAt, noRoom: false };
}

/** "-234" / "+120", the way a sportsbook prints it. */
export function formatAmerican(american: number): string {
  const n = Math.round(american);
  return n > 0 ? `+${n}` : `${n}`;
}

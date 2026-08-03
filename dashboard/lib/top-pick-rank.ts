/**
 * THE ONE DEFINITION OF "#1", and nothing else.
 *
 * WHY THIS IS ITS OWN FILE. The comparator started life inside
 * lib/top-pick.ts, which reads the ledger and therefore imports
 * `node:fs`. `BoardTable` is a CLIENT component, so importing the
 * comparator from there dragged the filesystem into the browser bundle
 * and webpack refused to build:
 *
 *     UnhandledSchemeError: Reading from "node:fs" is not handled
 *
 * That failure is the useful kind -- it says the rule and the loader
 * are different concerns. This module is pure arithmetic with no
 * imports at all, so both the server-side history card and the
 * client-side board badge can share it. Sharing is the whole point:
 * two surfaces answering "which game was #1" with their own private
 * rule is exactly how this dashboard has produced contradictions
 * before.
 */

/** Anything rankable: the side bet, the model's p(no run), and the
 *  price -- used only to break ties, so it may be null on a board row
 *  whose odds have not been captured yet. */
export interface RankableBet {
  side: string;
  /** p(no run in the 1st) as the system printed it. */
  modelP: number;
  odds: number | null;
}

/** Break-even win rate the price demands, vig included. */
export function impliedFromOdds(odds: number): number {
  return odds < 0 ? -odds / (-odds + 100) : 100 / (odds + 100);
}

/** Confidence in the direction actually bet: smaller = stronger play. */
export function rankOf(b: RankableBet): number {
  return b.side === "NRFI" ? 1 - b.modelP : b.modelP;
}

/**
 * Negative when `a` outranks `b`. Confidence first, then the BETTER
 * PRICE. Returns 0 on a full tie -- callers needing total determinism
 * add their own final key (both current callers use the game name).
 *
 * WHY THE PRICE TIE-BREAK IS NOT COSMETIC. The retired calibrator
 * emitted flat steps -- 115 games landed on p = 0.4064 alone -- so 18%
 * of nights have two or more bets sharing the top probability exactly.
 * Without a rule the winner is whichever row the loader happened to
 * return first, which made the same season read 58-30 from Supabase and
 * 56-32 from the CSV. When the model genuinely cannot separate two
 * games, the one paying more is the higher-edge bet -- so this is a
 * real decision rule rather than an arbitrary stabiliser.
 */
export function compareForTopPick(a: RankableBet, b: RankableBet): number {
  const ra = rankOf(a), rb = rankOf(b);
  if (Math.abs(ra - rb) > 1e-9) return ra - rb;
  // A missing price cannot win a tie-break it has no information for.
  const ia = a.odds == null ? 1 : impliedFromOdds(a.odds);
  const ib = b.odds == null ? 1 : impliedFromOdds(b.odds);
  if (Math.abs(ia - ib) > 1e-9) return ia - ib;
  return 0;
}

/** A candidate carrying whatever key its surface uses to identify a game. */
export interface TopPickCandidate<K> extends RankableBet {
  key: K;
  /** Settles a full tie. Both existing callers pass "AWAY@HOME". */
  name: string;
}

/**
 * THE #1 OF A SLATE, for any surface holding board-shaped rows.
 *
 * Hoisted here on 2026-08-03 when the brief page became the THIRD
 * surface needing it. BoardTable and lib/top-pick already shared the
 * comparator but each re-implemented the fold around it, including the
 * game-name tiebreak -- so the rule was shared and the SELECTION was
 * not, which is the same defect one level up. A brief that disagreed
 * with the badge on the board about which game is #1 would be the worst
 * possible version of this bug, because the operator would be filming
 * the disagreement.
 *
 * Callers filter to STRONG before calling: LEAN is tracked and never
 * wagered, so a "top bet" that is not a bet would point at money the
 * system does not intend to risk.
 */
export function selectTopPick<K>(
  candidates: TopPickCandidate<K>[],
): TopPickCandidate<K> | null {
  let best: TopPickCandidate<K> | null = null;
  for (const c of candidates) {
    if (c.side !== "YRFI" && c.side !== "NRFI") continue;
    if (!best) { best = c; continue; }
    const cmp = compareForTopPick(c, best);
    if (cmp < 0 || (cmp === 0 && c.name < best.name)) best = c;
  }
  return best;
}

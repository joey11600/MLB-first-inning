/**
 * lib/reconcile.ts -- THE single source for every bet count on the page.
 *
 * WHY THIS FILE EXISTS (2026-07-28)
 * ---------------------------------
 * The operator looked at one screen and saw his 2026-07-27 slate
 * described three different ways:
 *
 *     ticker                "6 STRONG YRFI"
 *     ledger card           "4 graded bets (2W-2L)  -0.33"
 *     season record day     "1 bet  -11.15u"
 *
 * All three were arithmetically defensible and nothing on screen said
 * why they differed, so it read as "my bets are missing". Three separate
 * components each derived its own count from a different source with a
 * different filter.
 *
 * The fix is not better wording. It is that there is now exactly ONE
 * function that produces these numbers and exactly ONE string that
 * renders them, quoted verbatim wherever the chain appears. If a count
 * needs to change, it changes here and every surface moves together.
 *
 * THE THREE NUMBERS ARE NOT A SEQUENCE. flagged -> placed -> settled is
 * one population narrowing (the live system). `replay` is a SEPARATE
 * population -- what today's model would do over the same night -- and
 * must never be rendered as a fourth step on the same arrow chain, or
 * the reader subtracts and concludes bets vanished.
 */

import type { BoardRow, GameDetail } from "./types";
import type { RecDay } from "./season-record";

export interface NightCounts {
  date: string;
  /** Games the live system called STRONG. */
  flagged: number;
  /** Of those, ones actually bet (bet_placed = Y). */
  placed: number;
  /** Of those, ones that have graded. */
  settled: number;
  /** Real money, real prices: the ledger's P&L for the night. */
  ledgerPL: number;
  /** What the CURRENT model would bet. null = this date has not been
   *  replayed yet (tonight). NEVER 0 -- a fabricated zero here recreates
   *  the exact "my bets disappeared" problem this file exists to fix. */
  replay: number | null;
  /** Flat-stake P&L of those replay bets. null when not replayed. */
  replayPL: number | null;
  /** Simulated compounded bank after this date. null when not replayed. */
  replayBank: number | null;
}

/** Minus sign U+2212, not a hyphen: it aligns with digits in tabular
 *  figures and reads as a minus rather than a dash. */
export function fmtU(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "--";
  if (Math.abs(n) < 0.005) return "0.00u";          // no signed zero
  const s = n < 0 ? "−" : "+";
  return `${s}${Math.abs(n).toFixed(2)}u`;
}

export function fmtOdds(o: number | null | undefined): string {
  if (typeof o !== "number" || !Number.isFinite(o)) return "--";
  // U+2212 for the negative, matching fmtU -- a row reading
  // "−1.00u @ -140" mixes a true minus with a hyphen on one line.
  return o > 0 ? `+${o}` : `−${Math.abs(o)}`;
}

/** A graded night, from the season record's per-day reconciliation. */
export function nightFromRecord(day: RecDay | null | undefined): NightCounts | null {
  if (!day) return null;
  // ledgerPL sums the LEDGER's own P&L, never day.flatPnl -- that is the
  // replay's flat figure and differs (7/27: replay -1.00, ledger -0.33).
  let settled = 0;
  let ledgerPL = 0;
  for (const g of day.games) {
    const l = g.ledger;
    if (!l?.placed) continue;
    if (typeof l.pnl === "number" && Number.isFinite(l.pnl)) {
      settled += 1;
      ledgerPL += l.pnl;
    }
  }
  return {
    date: day.date,
    flagged: day.flagged,
    placed: day.placed,
    settled,
    ledgerPL,
    replay: day.bet,
    replayPL: day.flatPnl,
    replayBank: day.simBankAfter,
  };
}

function lookupDetail(
  r: BoardRow,
  details: Record<string, GameDetail>,
): GameDetail | undefined {
  return (
    (r.gamePk && details[r.gamePk]) ||
    details[`${r.away}@${r.home}#${r.gameNumber || 1}`] ||
    details[`${r.away}@${r.home}`]
  );
}

/** Tonight, straight off the board. The replay fields are null because
 *  the season record only covers graded days -- tonight has not been
 *  replayed and saying "0" would be a lie. */
export function nightFromBoard(
  rows: BoardRow[],
  details: Record<string, GameDetail>,
  date: string,
): NightCounts {
  let flagged = 0, placed = 0, settled = 0, ledgerPL = 0;
  for (const r of rows) {
    if (r.pickStrength !== "STRONG") continue;
    flagged += 1;
    const d = lookupDetail(r, details);
    if (d?.betPlaced !== "Y") continue;
    placed += 1;
    if (d.gradedResult && typeof d.profitLossUnits === "number") {
      settled += 1;
      ledgerPL += d.profitLossUnits;
    }
  }
  return { date, flagged, placed, settled, ledgerPL,
           replay: null, replayPL: null, replayBank: null };
}

/** The ONE string. Rendered verbatim in the ticker, the hero card and
 *  the day header -- never recomputed, never reworded per surface. */
export function chainText(n: NightCounts | null): string {
  if (!n) return "";
  return `FLAGGED ${n.flagged} · PLACED ${n.placed} · SETTLED ${n.settled} ${fmtU(n.ledgerPL)}`;
}

/** The replay count, kept deliberately OFF the chain above. */
export function replayText(n: NightCounts | null): string {
  if (!n) return "";
  if (n.replay == null) return "MODEL REPLAY  not replayed yet";
  return `MODEL REPLAY ${n.replay} ${fmtU(n.replayPL)}`;
}

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
import { stakeUnitsFor } from "./kelly-sim";
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
  /** Flat-stake P&L of those replay bets. null when not replayed. The
   *  un-leveraged reference, no longer the figure surfaces lead with. */
  replayPL: number | null;
  /** Quarter-Kelly P&L of those replay bets -- the sizing the system
   *  actually stakes by, so this is what replayText() shows. */
  replaySimPL: number | null;
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
    replaySimPL: day.simPnl,
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
           replay: null, replayPL: null, replaySimPL: null, replayBank: null };
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
  // Quarter-Kelly, matching the footer and the record card. Quoting the
  // flat figure here while the footer led with Kelly put two different
  // replay numbers on one screen -- the exact defect this file exists
  // to prevent.
  return `MODEL REPLAY ${n.replay} ${fmtU(n.replaySimPL ?? n.replayPL)}`;
}


/** Tonight's STRONG picks, straight off the board.
 *
 *  WHY (2026-07-28): the system card reads season_record.json, which the
 *  nightly export only writes for FULLY GRADED days. So on the live slate
 *  it rendered "No bets settled in this window yet" even after tonight's
 *  STRONG pick had already graded -- the operator watched a 9.56u sized
 *  bet lose and the card show nothing. Tonight has to come from the board.
 *
 *  This reports what THE SYSTEM did: every STRONG pick and the stake it
 *  was sized at. Whether the ledger committed it (bet_placed) is the
 *  older-ledger line's business, not this card's -- a pick that was sized
 *  and graded is part of the system's record either way.
 */
export interface TonightSystem {
  bets: number; wins: number; losses: number; pending: number;
  staked: number;      // units the system sized across those picks
  pnl: number;         // realised, at the sized stake
  committed: number;   // how many the ledger actually placed
}

export function tonightFromBoard(
  rows: BoardRow[],
  details: Record<string, GameDetail>,
): TonightSystem | null {
  let bets = 0, wins = 0, losses = 0, pending = 0;
  let staked = 0, pnl = 0, committed = 0;
  for (const r of rows) {
    if (r.pickStrength !== "STRONG") continue;
    if (r.pickSide !== "NRFI" && r.pickSide !== "YRFI") continue;
    const d = lookupDetail(r, details);
    if (!d) continue;
    bets += 1;
    // THE NEW SYSTEM, COMPUTED LIVE (2026-07-30).
    //
    // This read `d.unitsRisked` -- the stake the LEDGER recorded, which
    // for anything placed before 2026-07-30 was sized the old way
    // (bankroll x Kelly%). That made the Today tab disagree with every
    // other surface: 2026-07-29 showed +2.04u here and +2.83u on
    // /history, for the same two games.
    //
    // Because 1 unit = 1% of bankroll, the stake depends only on the
    // model probability and the price -- so tonight can be sized under
    // the new rule without waiting for the nightly replay export, which
    // has no entry for a live slate. stakeUnitsFor() is the same rule
    // as tracker.kelly_stake_units and the same one /history reads.
    const raw = r.pickSide === "NRFI" ? d.marketNrfiOdds : d.marketYrfiOdds;
    const american = Number.parseFloat((raw ?? "").trim());
    const modelP = (r.pickSide === "NRFI" ? r.nrfiPct : r.yrfiPct) / 100;
    const u = Number.isFinite(american) && american !== 0
      ? stakeUnitsFor(modelP, american)
      : 0;
    staked += u;
    if (d.betPlaced === "Y") committed += 1;
    const g = (d.gradedResult || "").toUpperCase();
    if (g === "WIN") {
      wins += 1;
      const b = Number.isFinite(american) && american !== 0
        ? (american > 0 ? american / 100 : 100 / Math.abs(american))
        : 100 / 110;
      pnl += u * b;
    } else if (g === "LOSS") {
      losses += 1;
      pnl -= u;
    } else {
      pending += 1;
    }
  }
  return bets === 0 ? null : { bets, wins, losses, pending, staked, pnl, committed };
}

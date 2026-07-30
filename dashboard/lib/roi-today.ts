/**
 * Client-side ROI aggregator for the "today" window.
 *
 * The server-side loadRoi() in lib/roi.ts reads picks_<year>.csv from
 * disk and aggregates over a date range -- it imports node:fs and
 * cannot be bundled into client components.  For the consolidated
 * performance card (T3.21) we want the same RoiResponse shape, but
 * computed live from the BoardResponse data DashboardShell already
 * receives via SSR + Supabase realtime.
 *
 * This file is tree-shaken into the client bundle.  No node imports.
 *
 * Aggregation semantics MATCH loadRoi() so the user sees consistent
 * numbers when toggling between TODAY and 7d/30d/season:
 *   - Buckets keyed by `${pickSide}|${pickStrength}` (incl. PASS variants)
 *   - WIN/LOSS counts feed the bet-zone aggregates
 *   - PASS / POSTPONED / SUSPENDED tracked separately
 *   - profit_loss_units (from real DK odds) is the source of truth
 *     for unitsPL; falls back to flat -110 odds only when the column
 *     is missing (rare, ~0.5% of placed bets per the audit)
 *   - hitRate, edgeVsBreakEven, totals computed identically
 *   - When v3 mode is on AND the row+detail have v3 data, pull pick
 *     verdict + graded outcome + P/L from the v3 fields; else fall
 *     back to v2 transparently (matches the BoardRow toggle behavior)
 */

import type { BoardRow, GameDetail, PickSide, PickStrength } from "./types";
import type { LeanPaperTrade, RoiResponse, ZoneRoi } from "./roi";
import { simulateKelly, KELLY_FALLBACK, type KellySim } from "./kelly-sim";

const DEFAULT_WIN_PROFIT_UNITS = 100 / 110;       // 0.9091
const DEFAULT_LOSS_UNITS       = -1.0;
const DEFAULT_BREAK_EVEN_RATE  = 110 / 210;       // 0.5238

function emptyZone(label: string, side: PickSide, strength: PickStrength): ZoneRoi {
  return {
    label,
    side,
    strength,
    picks: 0,
    wins: 0,
    losses: 0,
    postponed: 0,
    passes: 0,
    ungraded: 0,
    bets: 0,
    hitRate: NaN,
    unitsPL: 0,
    edgeVsBreakEven: NaN,
    // The today-view aggregator works from in-memory board rows, which do
    // not carry the market_* price columns, so it cannot tell a real
    // price from the -110 fallback. Report zero counts rather than
    // guessing; the UI treats an all-zero provenance as "unknown" and
    // omits the qualifier instead of asserting something false.
    provenance: {
      realPricedBets: 0,
      placeholderBets: 0,
      realPricedPL: 0,
      realPricedWins: 0,
      realPricedLosses: 0,
      realBreakEven: NaN,
      paperOnly: strength === "LEAN",
      realShare: NaN,
    },
  };
}

function finalize(z: ZoneRoi, plOverride?: number): ZoneRoi {
  const bets = z.wins + z.losses;
  const hitRate = bets > 0 ? z.wins / bets : NaN;
  const unitsPL =
    plOverride !== undefined
      ? plOverride
      : z.wins * DEFAULT_WIN_PROFIT_UNITS + z.losses * DEFAULT_LOSS_UNITS;
  return {
    ...z,
    bets,
    hitRate,
    unitsPL,
    edgeVsBreakEven: bets > 0 ? hitRate - DEFAULT_BREAK_EVEN_RATE : NaN,
  };
}

function zoneLabel(side: PickSide, strength: PickStrength): string {
  if (side === "PASS") {
    if (strength === "NO DATA")          return "NO DATA";
    if (strength === "STARTER PENDING")  return "STARTER PENDING";
    if (strength === "LINEUP PENDING")   return "LINEUP PENDING";
    if (strength === "LOW LAMBDA")       return "LOW LAMBDA";
    return "PASS";
  }
  return `${strength} ${side}`;
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

/** Aggregate today's row data into an RoiResponse-shaped object so
 *  the same RoiPanel rendering can show today's window without a
 *  server round-trip.  `today` should be a YYYY-MM-DD ISO date in
 *  ET-local terms (matches BoardResponse.date convention). */
export function aggregateTodayRoi(
  rows:    BoardRow[],
  details: Record<string, GameDetail>,
  today:   string,
): RoiResponse {
  const buckets = new Map<string, ZoneRoi>();
  const zonePL  = new Map<string, number>();
  let totalPicks  = 0;
  let gradedPicks = 0;
  // For today the cumulative-PL chart degenerates to a single point;
  // we still emit it for type-shape parity with the server response.
  let dayPL = 0;
  // Same figure restricted to bets with a captured DraftKings price.
  let realDayPL = 0;

  for (const r of rows) {
    const label = zoneLabel(r.pickSide, r.pickStrength);
    const key   = `${r.pickSide}|${r.pickStrength}`;
    let z = buckets.get(key);
    if (!z) {
      z = emptyZone(label, r.pickSide, r.pickStrength);
      buckets.set(key, z);
    }

    z.picks += 1;
    totalPicks += 1;

    const d      = lookupDetail(r, details);
    const graded = d?.gradedResult;
    const plRaw  = d?.profitLossUnits;

    if (graded === "WIN" || graded === "LOSS" || graded === "PASS") {
      gradedPicks += 1;
    }
    if (graded === "WIN" || graded === "LOSS") {
      if (graded === "WIN") z.wins += 1;
      else                  z.losses += 1;

      // Phase 1.3 (2026-05-12): LEAN tier is track-only.  Its
      // profit_loss_units in the CSV is 0 (bet_placed='N'); compute
      // a hypothetical at flat -110 from the W/L grade so the LEAN
      // zone display is informative.  STRONG keeps the realized number.
      let pl: number;
      if (r.pickStrength === "LEAN" && (r.pickSide === "NRFI" || r.pickSide === "YRFI")) {
        pl = graded === "WIN" ? DEFAULT_WIN_PROFIT_UNITS : DEFAULT_LOSS_UNITS;
      } else if (typeof plRaw === "number" && Number.isFinite(plRaw)) {
        pl = plRaw;
      } else {
        pl = graded === "WIN" ? DEFAULT_WIN_PROFIT_UNITS : DEFAULT_LOSS_UNITS;
      }
      zonePL.set(key, (zonePL.get(key) ?? 0) + pl);
      // Phase 1.3: LEAN paper-trade does NOT move the real bankroll curve.
      if (r.pickStrength !== "LEAN") {
        dayPL += pl;
        // Real-priced twin (2026-07-29), matching lib/roi.ts so the two
        // aggregators cannot disagree about what "the money series"
        // means depending on which window is selected.
        const priced = (r.pickSide === "NRFI"
          ? d?.marketNrfiOdds : d?.marketYrfiOdds) ?? "";
        if (priced.trim()) realDayPL += pl;
      }
    } else if (graded === "POSTPONED" || graded === "SUSPENDED") {
      z.postponed += 1;
    } else if (graded === "PASS") {
      z.passes += 1;
    } else {
      z.ungraded += 1;
    }
  }

  // Build sorted zone arrays in canonical order (matches loadRoi).
  const order = [
    "NRFI|STRONG",
    "NRFI|LEAN",
    "PASS|NO EDGE",
    "PASS|NO DATA",
    "PASS|STARTER PENDING",
    "PASS|LINEUP PENDING",
    "PASS|LOW LAMBDA",
    "YRFI|LEAN",
    "YRFI|STRONG",
  ];
  const finalized: ZoneRoi[] = [];
  for (const k of order) {
    const z = buckets.get(k);
    if (z) finalized.push(finalize(z, zonePL.get(k)));
  }
  for (const [k, z] of buckets.entries()) {
    if (!order.includes(k)) finalized.push(finalize(z, zonePL.get(k)));
  }

  const betZones  = finalized.filter((z) => z.side !== "PASS");
  const passZones = finalized.filter((z) => z.side === "PASS");

  // Phase 1.3 (2026-05-12): TOTAL = STRONG zones only -- matches loadRoi.
  const strongBetZones = betZones.filter((z) => z.strength === "STRONG");
  const total = emptyZone("TOTAL", "NRFI", "STRONG");
  total.label = "TOTAL";
  let totalPL = 0;
  for (const z of strongBetZones) {
    total.picks     += z.picks;
    total.wins      += z.wins;
    total.losses    += z.losses;
    total.postponed += z.postponed;
    total.ungraded  += z.ungraded;
    totalPL         += z.unitsPL;
  }
  const totalFinal = finalize(total, totalPL);

  // Phase 1.3: LEAN paper-trade summary (hypothetical at flat -110).
  const leanZones = betZones.filter((z) => z.strength === "LEAN");
  const leanPaperTrade: LeanPaperTrade = {
    picks: 0, wins: 0, losses: 0, bets: 0,
    hitRate: NaN, paperPL: 0, edgeVsBreakEven: NaN,
  };
  for (const z of leanZones) {
    leanPaperTrade.picks   += z.picks;
    leanPaperTrade.wins    += z.wins;
    leanPaperTrade.losses  += z.losses;
    leanPaperTrade.paperPL += z.unitsPL;
  }
  leanPaperTrade.bets = leanPaperTrade.wins + leanPaperTrade.losses;
  if (leanPaperTrade.bets > 0) {
    leanPaperTrade.hitRate         = leanPaperTrade.wins / leanPaperTrade.bets;
    leanPaperTrade.edgeVsBreakEven = leanPaperTrade.hitRate - DEFAULT_BREAK_EVEN_RATE;
  }

  // Single-point cumulative PL for shape parity.  HistoryView won't
  // render a chart from this; the dashboard hides the equity curve
  // when window === "today" (only one data point would be drawn).
  const cumulativePL = totalPicks > 0
    ? [{ date: today, units: dayPL }]
    : [];
  const realPricedCumulativePL = totalPicks > 0
    ? [{ date: today, units: realDayPL }]
    : [];

  // Kelly bankroll is a season-long running quantity, and this
  // client-side aggregator only ever sees TODAY's rows -- simulating
  // from them would restart the bankroll at 100u every morning and
  // report a meaningless number.  Return an explicitly unavailable sim;
  // the UI renders the Kelly card only from the server-side season view.
  const kelly: KellySim = {
    ...simulateKelly([], KELLY_FALLBACK, false),
    available: false,
  };

  return {
    window:       "today",
    startDate:    today,
    endDate:      today,
    totalPicks,
    gradedPicks,
    daysIncluded: totalPicks > 0 ? 1 : 0,
    betZones,
    passZones,
    total:        totalFinal,
    leanPaperTrade,
    cumulativePL,
    realPricedCumulativePL,
    // Today alone cannot know when stakes first left flat 1u -- that is
    // a season-long fact and this aggregator only sees one slate. Null,
    // not a guess: HistoryView draws no epoch marker rather than a
    // wrong one.
    stakeEpoch: null,
    kelly,
  };
}

/** Helper: today's CLV summary (avg pp delta on STRONG bets that
 *  have both opened + closing odds).  Matches the SummaryStrip
 *  computation that's being moved into RoiPanel's today view.
 *  Returns null when no STRONG bets have CLV data yet.
 *
 *  DEPRECATED (2026-07-28): trusts clv_pct on its own, which cannot
 *  distinguish "the line never moved" from "we never measured it".
 *  Kept only so existing callers keep compiling; new call sites must
 *  use aggregateTodayClvMeasured below. */
export function aggregateTodayClv(
  rows:    BoardRow[],
  details: Record<string, GameDetail>,
): { avgPp: number; n: number } | null {
  let sum = 0;
  let n   = 0;
  for (const r of rows) {
    if (!(r.pickSide === "NRFI" || r.pickSide === "YRFI")) continue;
    if (r.pickStrength !== "STRONG") continue;
    const d = lookupDetail(r, details);
    if (!d) continue;
    if (typeof d.clvPct !== "number" || !Number.isFinite(d.clvPct)) continue;
    sum += d.clvPct;
    n   += 1;
  }
  return n > 0 ? { avgPp: (sum / n) * 100, n } : null;
}

/** Result of aggregateTodayClvMeasured.
 *
 *  `avgPp` is null EXACTLY when `measured === 0`.  That is deliberate: the
 *  type makes it impossible to print an average that was never computed,
 *  which is how the dashboard came to show "TONIGHT CLV +0.00pp" on nights
 *  where nothing was measured at all.  Render the "Not measurable" copy in
 *  that branch, never a number. */
export interface TodayClvMeasured {
  /** Average closing-line value in percentage points, over `measured`
   *  bets.  null when measured === 0 -- there is no number to show. */
  avgPp:    number | null;
  /** How many placed bets actually saw the price move (the sample size
   *  behind avgPp).  Must always be shown next to avgPp. */
  measured: number;
  /** How many bets were placed at all today.  Lets the caption say
   *  "0 of 4 placed bets saw the line move" instead of going silent. */
  placed:   number;
}

/** Today's closing-line value, computed from the PRICES rather than from
 *  the stored clv_pct column.
 *
 *  Why not just read clv_pct?  Two independent reasons, and fixing only one
 *  leaves the bug in place:
 *
 *   1. board-supabase.ts used to coerce a NULL clv_pct to 0, and Supabase is
 *      the production read path.  That is fixed now, but it means no code
 *      that ran against production has ever seen a null here.
 *   2. Even with that fixed, the ledger genuinely stores 0.0000 for the vast
 *      majority of placed bets: we bet the first price we see and lock it
 *      (T2.23), so opened_*_odds and market_*_odds hold the SAME captured
 *      number and the difference is a true zero.  Verified 2026-07-28 on
 *      data/picks_2026.csv from 2026-05-07: of 314 placed bets that have
 *      both prices on the picked side, 295 are identical and only 19 moved.
 *      A stored 0.0000 therefore means "no closing price to compare
 *      against", not "we measured no edge".
 *
 *  So a bet counts as MEASURED only when the picked side has both an opened
 *  and a current price AND the two differ -- the same guard the line-drift
 *  notice already applies in GameDetails.tsx -- and the row carries a usable
 *  clv_pct to average.
 *
 *  Returns null only when nothing was placed today (nothing to report at
 *  all).  Otherwise always returns an object, so the caller can say how many
 *  of the placed bets moved even when the answer is zero. */
export function aggregateTodayClvMeasured(
  rows:    BoardRow[],
  details: Record<string, GameDetail>,
): TodayClvMeasured | null {
  let sum      = 0;
  let measured = 0;
  let placed   = 0;

  for (const r of rows) {
    if (!(r.pickSide === "NRFI" || r.pickSide === "YRFI")) continue;
    const d = lookupDetail(r, details);
    if (!d) continue;
    // "Placed" is the ledger fact, not the model's opinion: a STRONG call
    // that was demoted to bet_placed='N' is not a bet and must not appear
    // in the denominator of "N of M placed bets saw the line move".
    if (d.betPlaced !== "Y") continue;
    placed += 1;

    // Trim before comparing so a stray space in one column can't read as
    // a price move.  Otherwise this mirrors GameDetails' drift guard
    // exactly: raw captured strings, picked side only.
    const opened  = (r.pickSide === "NRFI" ? d.openedNrfiOdds : d.openedYrfiOdds).trim();
    const current = (r.pickSide === "NRFI" ? d.marketNrfiOdds : d.marketYrfiOdds).trim();
    if (!opened || !current || opened === current) continue;

    // The price moved, so a CLV exists -- but we still need the stored
    // number to state it.  Without it we know the line moved and nothing
    // more, which is not a figure we can average.
    if (typeof d.clvPct !== "number" || !Number.isFinite(d.clvPct)) continue;

    sum      += d.clvPct;
    measured += 1;
  }

  if (placed === 0) return null;
  return {
    avgPp: measured > 0 ? (sum / measured) * 100 : null,
    measured,
    placed,
  };
}

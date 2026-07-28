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
    kelly,
  };
}

/** Helper: today's CLV summary (avg pp delta on STRONG bets that
 *  have both opened + closing odds).  Matches the SummaryStrip
 *  computation that's being moved into RoiPanel's today view.
 *  Returns null when no STRONG bets have CLV data yet. */
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

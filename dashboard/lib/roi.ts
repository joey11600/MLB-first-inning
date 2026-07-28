/**
 * Aggregate W-L record and P&L from the season picks CSV.
 *
 * Source of truth for P&L: the `profit_loss_units` column on each row,
 * which the predictor + tracker compute from REAL DraftKings odds at
 * bet-placement time (T2.27).  Audit 2026-05-04: 99.5% of placed bets
 * have profit_loss_units populated -- the -110 fallback below is only
 * used for the rare row whose DK odds weren't captured (e.g. very early
 * morning slates before the odds-scrape runs, or postponed games).
 *
 * The 52.38% break-even rate is kept as an INFORMATIONAL THRESHOLD --
 * the hit rate at which a -110-equivalent bet would break even -- so
 * the dashboard's "hit-rate-by-zone" chart can show whether each zone
 * clears that bar.  It is NOT a P/L assumption.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { parseCsv } from "./csv";
import { getServerSupabase } from "./supabase";
import type { PickSide, PickStrength } from "./types";
import { readKellyConfig, simulateKelly, type KellySim } from "./kelly-sim";

// ---------------------------------------------------------------------------
// Constants -- fallbacks used only when profit_loss_units is missing.
// In practice 99.5%+ of placed bets have real DK-derived P/L stored.
// ---------------------------------------------------------------------------

export const DEFAULT_ODDS_AMERICAN     = -110;
export const DEFAULT_WIN_PROFIT_UNITS  = 100 / 110;       // = 0.9091
export const DEFAULT_LOSS_UNITS        = -1.0;
// Break-even hit rate threshold (informational reference for the
// hit-rate-by-zone chart, NOT a P/L computation).  At -110 odds the
// bet breaks even when hit rate = 52.38%.
export const DEFAULT_BREAK_EVEN_RATE   = 110 / 210;       // = 0.5238

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** RoiWindow -- which time slice the panel is showing.  T3.21 added
 *  "today" so the consolidated performance card can show today's
 *  realized P/L alongside 7d / 30d / season.  The server-side
 *  loadRoi ignores "today" (it's computed client-side from rows +
 *  details that DashboardShell already has in memory); see
 *  lib/roi-today.ts for the client-side aggregator. */
export type RoiWindow = "today" | "7d" | "30d" | "season";

export interface ZoneRoi {
  /** "STRONG NRFI" | "LEAN NRFI" | "PASS" | "LEAN YRFI" | "STRONG YRFI" */
  label: string;
  side: PickSide;
  strength: PickStrength;
  /** total picks in this zone over the window (includes PP, PASS, ungraded) */
  picks: number;
  wins: number;
  losses: number;
  postponed: number;
  passes: number;
  ungraded: number;
  /** wins + losses (the actual bets that resolved) */
  bets: number;
  /** wins / bets, NaN when bets == 0 */
  hitRate: number;
  /** P&L in units at the assumed odds */
  unitsPL: number;
  /** hitRate - DEFAULT_BREAK_EVEN_RATE; positive = profitable */
  edgeVsBreakEven: number;
  /** PROVENANCE (2026-07-28).  Four different kinds of number were being
   *  rendered with identical weight, which is why the operator stopped
   *  trusting the panel.  These counters let the UI say which kind a
   *  figure is instead of leaving it to be inferred. */
  provenance: ZoneProvenance;
}

/** How much of a zone's P&L is real money at an observed price.
 *
 *  `placeholderBets` is the important one: those rows had NO captured DK
 *  price and settled against a fabricated -110, which is what inflates
 *  the season total by roughly 15 units and makes STRONG NRFI read 59.4%
 *  when its priced subset went 44.9%.  A number built mostly from these
 *  is not a result and must not be displayed as one. */
export interface ZoneProvenance {
  /** graded bets settled at a REAL captured DraftKings price */
  realPricedBets: number;
  /** graded bets settled at the fabricated -110 fallback */
  placeholderBets: number;
  /** P&L attributable to real-priced bets only */
  realPricedPL: number;
  /** true when this zone is track-only (LEAN): nothing was ever wagered */
  paperOnly: boolean;
  /** share of graded bets that carry a real price, 0..1 (NaN when none) */
  realShare: number;
}

/** Aggregate paper-trade performance for the LEAN tier (Phase 1.3,
 *  2026-05-12).  LEAN picks are TRACK-ONLY -- bet_placed='N' so they
 *  contribute 0 to real P&L.  This struct surfaces what they WOULD
 *  have made at flat -110, computed from graded W/L counts, so the
 *  operator can compare LEAN hit rate to the 52.4% break-even bar
 *  after 60 graded LEAN picks per the playbook's tier-expansion
 *  acceptance criterion.  Hypothetical only -- never rolled into
 *  the real-money TOTAL. */
export interface LeanPaperTrade {
  picks:   number;
  wins:    number;
  losses:  number;
  bets:    number;
  hitRate: number;
  /** Hypothetical P&L at flat -110, computed from W/L counts. */
  paperPL: number;
  edgeVsBreakEven: number;
}

export interface RoiResponse {
  window: RoiWindow;
  startDate: string;     // ISO yyyy-mm-dd, inclusive
  endDate:   string;     // ISO yyyy-mm-dd, inclusive
  /** every pick row in the window (NRFI + YRFI + PASS, graded + ungraded) */
  totalPicks: number;
  /** picks that have a final outcome (W/L/PASS) -- excludes ungraded + PP */
  gradedPicks: number;
  /** distinct slate dates included in the window */
  daysIncluded: number;
  /** zones we'd actually bet on (excludes PASS).  LEAN zones display
   *  HYPOTHETICAL P&L at flat -110, not realized P&L (which is 0 for
   *  bet_placed='N'); STRONG zones display the realized number from
   *  the CSV's `profit_loss_units` column. */
  betZones:  ZoneRoi[];
  /** PASS zones (informational; never bet) */
  passZones: ZoneRoi[];
  /** Aggregate over STRONG bet zones ONLY -- the real-money performance
   *  metric.  Phase 1.3 (2026-05-12): LEAN tier is intentionally excluded
   *  from TOTAL so the "+X.Yu" headline figure keeps its prior meaning
   *  (real bets only).  Use `leanPaperTrade` for the hypothetical LEAN
   *  paper-trade summary. */
  total:     ZoneRoi;
  /** Hypothetical paper-trade summary for LEAN picks (Phase 1.3). */
  leanPaperTrade: LeanPaperTrade;
  /** rolling cumulative P&L by date for STRONG bet zones only (LEAN
   *  excluded -- paper-trade does not move the bankroll curve). */
  cumulativePL: { date: string; units: number }[];
  /** Counterfactual "what if we'd staked by Kelly from the start"
   *  bankroll.  Recomputed on read from each row's probability + real
   *  captured price -- the ledger is NEVER rewritten with simulated
   *  stakes (see lib/kelly-sim.ts for why).  Always spans the whole
   *  season regardless of `window`, because a bankroll is a running
   *  quantity and a 7-day bankroll is meaningless. */
  kelly: KellySim;
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function dataDir(): string {
  const local = path.resolve(process.cwd(), "data");
  const parent = path.resolve(process.cwd(), "..", "data");
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fs = require("node:fs") as typeof import("node:fs");
    if (fs.existsSync(path.join(parent, "boards"))) return parent;
    if (fs.existsSync(path.join(local, "boards"))) return local;
  } catch {
    /* ignore */
  }
  return parent;
}

async function safeRead(p: string): Promise<string | null> {
  try {
    return await fs.readFile(p, "utf8");
  } catch {
    return null;
  }
}

// MLB slates are organized by ET calendar day.  Using UTC here would skip
// the current ET slate after 8 PM ET (when UTC has already rolled to
// tomorrow), so the ROI window misses live results until ~midnight ET.
const ET_DATE_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function isoToday(): string {
  return ET_DATE_FMT.format(new Date());
}

function isoMinusDays(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return ET_DATE_FMT.format(d);
}

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
  const prov = z.provenance;
  const provTotal = prov.realPricedBets + prov.placeholderBets;
  prov.realShare = provTotal > 0 ? prov.realPricedBets / provTotal : NaN;
  // Prefer the explicit P&L sum (which tracks actual realized prices via
  // profit_loss_units).  Fall back to flat-odds estimate when not provided.
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
    return strength === "NO DATA"
      ? "NO DATA"
      : strength === "STARTER PENDING"
        ? "STARTER PENDING"
        : strength === "LINEUP PENDING"
          ? "LINEUP PENDING"
          : strength === "LOW LAMBDA"
            ? "LOW LAMBDA"
            : "PASS";
  }
  return `${strength} ${side}`;
}

// ---------------------------------------------------------------------------
// Public: load + compute
// ---------------------------------------------------------------------------

export async function loadRoi(
  window: RoiWindow,
  refDateIso?: string,
): Promise<RoiResponse> {
  const today = (refDateIso || isoToday()).slice(0, 10);
  // T3.21: "today" is normally computed client-side via aggregateTodayRoi
  // (RoiPanel routes to the local aggregator when window === "today"),
  // but if the server is called with it we honor it as a 1-day window
  // ending today so /api/roi?window=today still produces sensible data.
  //
  // T-V21-2026-05-09: window math is INCLUSIVE on both ends.  "Last 7
  // days" must therefore start `today - 6 days` so the resulting span
  // (start..today) covers exactly 7 calendar days.  The previous code
  // used `isoMinusDays(7)` which gave an 8-day window (5/02..5/09 for
  // a 5/09 reference) and disagreed with `tools/pl_calc.py --window 7d`
  // (which already starts at `today - (days-1)`).  Operator on
  // 2026-05-09 caught this when the dashboard's 7d card said -0.69u
  // for what they expected to be the last seven days; the extra day
  // (5/02 = -0.69u) was the entire delta.  Same fix applied to 30d
  // for symmetry.
  const startDate =
    window === "today"
      ? today
      : window === "7d"
        ? isoMinusDays(6)
        : window === "30d"
          ? isoMinusDays(29)
          : `${today.slice(0, 4)}-01-01`;

  const empty: RoiResponse = {
    window,
    startDate,
    endDate: today,
    totalPicks:  0,
    gradedPicks: 0,
    daysIncluded: 0,
    betZones:  [],
    passZones: [],
    total: emptyZone("TOTAL", "NRFI", "STRONG"),
    leanPaperTrade: {
      picks: 0, wins: 0, losses: 0, bets: 0,
      hitRate: NaN, paperPL: 0, edgeVsBreakEven: NaN,
    },
    cumulativePL: [],
    kelly: simulateKelly([], readKellyConfig(null).cfg, false),
  };

  const year = today.slice(0, 4);
  const csvPath = path.join(dataDir(), `picks_${year}.csv`);
  const raw = await safeRead(csvPath);
  if (!raw) return empty;

  const rows = parseCsv(raw);

  // Group buckets keyed by `${side}|${strength}`.
  const buckets = new Map<string, ZoneRoi>();
  // Per-day P&L list (only counts wins/losses on bet zones)
  const dayPL = new Map<string, number>();
  // Per-zone realized P&L (uses actual prices when profit_loss_units column populated)
  const zonePL = new Map<string, number>();
  // Aggregate counters across the whole window (regardless of zone)
  let totalPicks  = 0;
  let gradedPicks = 0;
  const daysSet = new Set<string>();

  for (const r of rows) {
    const date = (r.date ?? "").slice(0, 10);
    if (!date) continue;
    if (date < startDate || date > today) continue;

    const sideRaw     = (r.pick_side     ?? "").toUpperCase();
    const strengthRaw = (r.pick_strength ?? "").toUpperCase();
    const side: PickSide =
      sideRaw === "NRFI" || sideRaw === "YRFI" ? sideRaw : "PASS";
    const strength: PickStrength =
      ["STRONG", "LEAN", "NO EDGE", "NO DATA", "STARTER PENDING", "LINEUP PENDING", "LOW LAMBDA"].includes(
        strengthRaw,
      )
        ? (strengthRaw as PickStrength)
        : "NO EDGE";

    const label = zoneLabel(side, strength);
    const key   = `${side}|${strength}`;
    let z = buckets.get(key);
    if (!z) {
      z = emptyZone(label, side, strength);
      buckets.set(key, z);
    }

    z.picks += 1;
    totalPicks += 1;
    daysSet.add(date);

    const graded = (r.graded_result ?? "").toUpperCase();
    if (graded === "WIN" || graded === "LOSS" || graded === "PASS") {
      gradedPicks += 1;
    }
    if (graded === "WIN" || graded === "LOSS") {
      if (graded === "WIN") z.wins += 1;
      else                  z.losses += 1;

      // Phase 1.3 (2026-05-12): LEAN tier is track-only (bet_placed='N'),
      // so the CSV's `profit_loss_units` for LEAN rows is "0.000".  Using
      // that here would zero out the LEAN zone display and hide the whole
      // point of the paper-trade analysis -- so for LEAN we COMPUTE a
      // hypothetical P&L at flat -110 from the graded W/L instead.
      // STRONG rows still use realized profit_loss_units (real DK odds
      // when imported, flat -110 fallback when not).
      let pl: number;
      if (strength === "LEAN" && (side === "NRFI" || side === "YRFI")) {
        pl = graded === "WIN" ? DEFAULT_WIN_PROFIT_UNITS : DEFAULT_LOSS_UNITS;
      } else {
        const plRaw = (r.profit_loss_units ?? "").trim();
        let parsedPL = NaN;
        if (plRaw) {
          const parsed = Number.parseFloat(plRaw);
          if (Number.isFinite(parsed)) parsedPL = parsed;
        }
        pl = Number.isFinite(parsedPL)
          ? parsedPL
          : (graded === "WIN" ? DEFAULT_WIN_PROFIT_UNITS : DEFAULT_LOSS_UNITS);
      }

      // PROVENANCE (2026-07-28).  A graded bet counts as real-priced only
      // when the picked side actually has a captured DraftKings number.
      // Without one, _calc_pnl settled it against a fabricated -110 --
      // that is 170 of April's 176 bets, and it is why the season total
      // reads ~15u higher than anything that really happened.
      if (strength !== "LEAN") {
        const priceCol = side === "NRFI" ? r.market_nrfi_odds : r.market_yrfi_odds;
        const hasRealPrice = Boolean((priceCol ?? "").trim());
        if (hasRealPrice) {
          z.provenance.realPricedBets += 1;
          z.provenance.realPricedPL += pl;
        } else {
          z.provenance.placeholderBets += 1;
        }
      }

      // Track realized P&L at both zone (for breakdown) and day (for chart) level.
      const zKey = `${side}|${strength}`;
      zonePL.set(zKey, (zonePL.get(zKey) ?? 0) + pl);
      // Phase 1.3: cumulativePL must remain the real-money bankroll
      // curve.  LEAN paper-trade P&L feeds zonePL only -- the chart
      // and TOTAL aggregate stay STRONG-only.
      if (strength !== "LEAN") {
        const prev = dayPL.get(date) ?? 0;
        dayPL.set(date, prev + pl);
      }
    } else if (graded === "POSTPONED" || graded === "SUSPENDED") {
      z.postponed += 1;
    } else if (graded === "PASS") {
      z.passes += 1;
      // All-PASS days (every pick on the slate was "no edge" / pending)
      // were silently dropping out of cumulativePL because dayPL only
      // tracked dates with a WIN or LOSS.  Seed the date with a 0 delta
      // so it still appears as a flat point on the bankroll chart --
      // otherwise the user sees a gap on days the model chose to sit
      // out.  Only seed; never overwrite an actual P&L.
      if (!dayPL.has(date)) dayPL.set(date, 0);
    } else {
      z.ungraded += 1;
    }
  }

  // Build sorted zone arrays in canonical order.
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
  // Append any remaining buckets in iteration order (defensive)
  for (const [k, z] of buckets.entries()) {
    if (!order.includes(k)) finalized.push(finalize(z, zonePL.get(k)));
  }

  const betZones  = finalized.filter((z) => z.side !== "PASS");
  const passZones = finalized.filter((z) => z.side === "PASS");

  // Phase 1.3 (2026-05-12): TOTAL aggregates STRONG zones only.  LEAN
  // is track-only (bet_placed='N') and rolling it into TOTAL would
  // silently redefine the operator's real-money performance metric
  // (e.g. "+35.5u" picks up unrelated LEAN W/L counts).  LEAN summary
  // lives in `leanPaperTrade` below.
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
    total.provenance.realPricedBets  += z.provenance.realPricedBets;
    total.provenance.placeholderBets += z.provenance.placeholderBets;
    total.provenance.realPricedPL    += z.provenance.realPricedPL;
  }
  total.provenance.paperOnly = false;
  const totalFinal = finalize(total, totalPL);

  // Phase 1.3: LEAN paper-trade aggregate.  Sums hypothetical-at-flat-110
  // P&L across LEAN NRFI + LEAN YRFI so the dashboard can render a
  // dedicated paper-trade card.  zonePL already carries the flat-110
  // numbers because the per-row loop above used DEFAULT_WIN_PROFIT_UNITS /
  // DEFAULT_LOSS_UNITS for LEAN strength.
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

  // Cumulative P&L sorted by date
  const dates = Array.from(dayPL.keys()).sort();
  const cumulativePL: { date: string; units: number }[] = [];
  let cum = 0;
  for (const d of dates) {
    cum += dayPL.get(d) ?? 0;
    cumulativePL.push({ date: d, units: cum });
  }

  // Kelly counterfactual.  Deliberately fed `rows` (the whole season)
  // rather than the windowed subset: a bankroll compounds, so slicing it
  // to "last 7 days" would restart it at 100u and report a number that
  // means nothing.
  const thresholdsRaw = await safeRead(path.join(dataDir(), "thresholds.json"));
  let thresholds: Record<string, unknown> | null = null;
  if (thresholdsRaw) {
    try {
      thresholds = JSON.parse(thresholdsRaw) as Record<string, unknown>;
    } catch {
      thresholds = null;   // malformed file -> fall back, never throw
    }
  }
  const { cfg: kellyCfg, available: kellyAvailable } = readKellyConfig(thresholds);
  const kelly = simulateKelly(rows, kellyCfg, kellyAvailable);

  return {
    window,
    startDate,
    endDate: today,
    totalPicks,
    gradedPicks,
    daysIncluded: daysSet.size,
    betZones,
    passZones,
    total: totalFinal,
    leanPaperTrade,
    cumulativePL,
    kelly,
  };
}


// T-V21-LOCKIN-2026-05-06: removed loadV3Roi (Variant K shadow ROI).
// V2.1 is the only production model surfaced; the V3 history page and
// shadow ROI feed were archived along with the model toggle.

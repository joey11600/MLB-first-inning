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
import { getServerSupabase, isSupabaseConfigured } from "./supabase";
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
  /** W-L over the real-priced subset ONLY. 2026-07-28 audit: the cards
   *  printed realPricedPL next to a hit rate computed over ALL graded
   *  bets -- two populations on one line. STRONG NRFI rendered
   *  "-11.29u · 59.4% hit" where the -11.29u is 49 real-priced bets that
   *  went 22-27 (44.9%) and the 59.4% folds in 47 placeholder-priced
   *  bets that went 35-12. */
  realPricedWins: number;
  realPricedLosses: number;
  /** Mean implied probability of the prices actually paid on the
   *  real-priced subset -- the TRUE break-even for this zone. The
   *  hardcoded 110/210 (52.38%) assumes every bet was -110; the real
   *  average is ~56%, so every "vs break-even" figure was overstated by
   *  about 4 points. */
  realBreakEven: number;
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
  /** THE MONEY SERIES.  Same accumulator as `cumulativePL` but only over
   *  bets whose picked side had a captured DraftKings price, so it is
   *  free of the fabricated -110 settlements that make the raw column
   *  read ~35u high.  HistoryView charts THIS; `cumulativePL` survives
   *  as one dashed, explicitly-labelled comparison line.
   *
   *  Declared on the interface (2026-07-29) rather than being cast in at
   *  the call site.  It was consumed via
   *  `data as RoiResponse & { realPricedCumulativePL?: ... }` for a day,
   *  and that cast is exactly why nobody noticed the field was never
   *  produced -- an optional property on a cast type cannot fail to
   *  compile when it is missing. */
  realPricedCumulativePL: { date: string; units: number }[];
  /** First date whose stake left flat 1u, i.e. when Kelly went live.
   *  Read off the ledger's `units_risked`, not the config constant, so
   *  it reports what happened rather than what was configured.  Null
   *  before any variable stake exists. */
  stakeEpoch: string | null;
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

/** Every pick row for `season`, LIVE from Supabase, or null.
 *
 *  2026-07-29 -- WHY THIS EXISTS.  /history read `picks_<year>.csv` off
 *  disk, and on Vercel that file is whatever `npm run prebuild`
 *  (scripts/copy-data.mjs) copied into the bundle AT BUILD TIME.  A
 *  deployment's filesystem is immutable, so the page could only ever be
 *  as fresh as the last deploy: the operator watched HOU@LAA settle
 *  +4.117u on the main board while /history still showed the night at
 *  -2.08u, because the board reads Supabase and this did not.
 *  `export const dynamic = "force-dynamic"` did not help -- it re-runs
 *  the render, but re-reading a frozen file yields the frozen answer.
 *
 *  Mirrors `lib/board.ts`: Supabase first, CSV on any failure, never an
 *  error.  A Supabase outage downgrades to "as fresh as the last build",
 *  which is exactly where this page already was.
 *
 *  PAGINATED, because PostgREST enforces a server-side 1000-row max and
 *  a bigger .limit() is silently truncated -- the same cap that
 *  truncated pl_calc and the date picker.  A season is ~2400 rows, so an
 *  unpaginated read would quietly drop the oldest ~60% of the season. */
async function loadRoiRowsFromSupabase(
  season: number,
): Promise<Record<string, string>[] | null> {
  if (!isSupabaseConfigured()) return null;
  const sb = getServerSupabase();
  if (!sb) return null;
  const PAGE = 1000;
  const out: Record<string, string>[] = [];
  try {
    for (let from = 0; from < 20_000; from += PAGE) {
      const { data, error } = await sb
        .from(`picks_${season}`)
        .select("*")
        .order("date", { ascending: true })
        .range(from, from + PAGE - 1);
      if (error) {
        console.warn("[roi] supabase page failed", from, error);
        return null;              // fall back to CSV rather than serve a partial season
      }
      if (!data || data.length === 0) break;
      // COERCE TO THE CSV SHAPE AT THE BOUNDARY.
      //
      // Supabase returns NATIVE Postgres types -- `profit_loss_units`
      // arrives as a JS number, `wx_is_dome` as a boolean, nulls as
      // null.  `parseCsv` returns every cell as a string, and the whole
      // 250-line aggregator below is written against that: it calls
      // `.trim()` on a dozen columns and `Number.parseFloat` on
      // strings.  Handing it raw Supabase rows crashed the page with
      // "(r.profit_loss_units ?? '').trim is not a function".
      //
      // Normalising here keeps the aggregator single-shaped rather than
      // teaching every call site to handle both. null/undefined become
      // "" so the existing `(x ?? "").trim()` idiom keeps working.
      for (const row of data as Record<string, unknown>[]) {
        const norm: Record<string, string> = {};
        for (const [k, v] of Object.entries(row)) {
          norm[k] = v === null || v === undefined ? "" : String(v);
        }
        out.push(norm);
      }
      if (data.length < PAGE) break;
    }
  } catch (err) {
    console.warn("[roi] supabase read threw; falling back to CSV", err);
    return null;
  }
  return out.length > 0 ? out : null;
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
  const prov = z.provenance;
  const provTotal = prov.realPricedBets + prov.placeholderBets;
  prov.realShare = provTotal > 0 ? prov.realPricedBets / provTotal : NaN;
  // realBreakEven accumulated a SUM above; turn it into the mean.
  prov.realBreakEven = prov.realPricedBets > 0 && Number.isFinite(prov.realBreakEven)
    ? prov.realBreakEven / prov.realPricedBets
    : NaN;
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
    // 2026-07-28 audit: measure the edge against the prices actually
    // PAID, not against a -110 that most of these bets never had. Falls
    // back to the -110 constant only when no real price exists (LEAN
    // zones, where the flat -110 hypothetical is the correct reference).
    edgeVsBreakEven: (() => {
      if (bets === 0) return NaN;
      const rp = prov.realPricedBets;
      if (rp > 0 && Number.isFinite(prov.realBreakEven)) {
        const realHit = (prov.realPricedWins + prov.realPricedLosses) > 0
          ? prov.realPricedWins / (prov.realPricedWins + prov.realPricedLosses)
          : NaN;
        return Number.isFinite(realHit) ? realHit - prov.realBreakEven : NaN;
      }
      return hitRate - DEFAULT_BREAK_EVEN_RATE;
    })(),
  };
}

/** American odds string -> implied probability, or null. */
function impliedFromAmerican(v: string | null | undefined): number | null {
  const t = (v ?? "").trim();
  if (!t) return null;
  const n = Number.parseFloat(t);
  if (!Number.isFinite(n) || n === 0) return null;
  return n > 0 ? 100 / (n + 100) : Math.abs(n) / (Math.abs(n) + 100);
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
    realPricedCumulativePL: [],
    stakeEpoch: null,
    kelly: simulateKelly([], readKellyConfig(null).cfg, false),
  };

  const year = today.slice(0, 4);
  // LIVE FIRST, build-time snapshot second.  See loadRoiRowsFromSupabase
  // for why: on Vercel the CSV is frozen at deploy time, so /history
  // could only ever be as fresh as the last build while the main board
  // -- which already reads Supabase -- showed the current night.
  let rows = await loadRoiRowsFromSupabase(Number(year));
  if (!rows) {
    const csvPath = path.join(dataDir(), `picks_${year}.csv`);
    const raw = await safeRead(csvPath);
    if (!raw) return empty;
    rows = parseCsv(raw);
  }

  // Group buckets keyed by `${side}|${strength}`.
  const buckets = new Map<string, ZoneRoi>();
  // Per-day P&L list (only counts wins/losses on bet zones)
  const dayPL = new Map<string, number>();
  // Same shape as dayPL but only bets with a captured price -- see the
  // accumulation site below for why this is the series that matters.
  const realDayPL = new Map<string, number>();
  // Earliest date carrying a non-1u stake, i.e. when Kelly went live.
  // HistoryView marks it on the drawdown chart so a step change in
  // volatility is attributable rather than mysterious.
  let stakeEpoch: string | null = null;
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
      // First date a stake left flat 1u.  Read off units_risked rather
      // than the Kelly epoch constant so it reflects what the ledger
      // actually did, not what config says it should have done.
      if (strength !== "LEAN") {
        const ur = Number.parseFloat((r.units_risked ?? "").trim());
        if (Number.isFinite(ur) && Math.abs(ur - 1) > 0.005) {
          if (!stakeEpoch || date < stakeEpoch) stakeEpoch = date;
        }
      }

      if (strength !== "LEAN") {
        const priceCol = side === "NRFI" ? r.market_nrfi_odds : r.market_yrfi_odds;
        const hasRealPrice = Boolean((priceCol ?? "").trim());
        if (hasRealPrice) {
          z.provenance.realPricedBets += 1;
          z.provenance.realPricedPL += pl;
          if (graded === "WIN") z.provenance.realPricedWins += 1;
          else if (graded === "LOSS") z.provenance.realPricedLosses += 1;
          // Accumulate the implied probability of the price actually paid.
          const impl = impliedFromAmerican(priceCol);
          if (impl != null) {
            z.provenance.realBreakEven =
              (Number.isFinite(z.provenance.realBreakEven) ? z.provenance.realBreakEven : 0) + impl;
          }
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
        // THE REAL-MONEY SERIES (2026-07-29).  Same accumulator, but
        // only over bets whose picked side had a captured DraftKings
        // price.  HistoryView has read `realPricedCumulativePL` since
        // the 2026-07-28 audit -- but NOTHING EVER PRODUCED IT.  The
        // consumer shipped without the producer, so the page's fallback
        // fired on every single render, it charted the fabricated -110
        // series, and it printed "Reload the page to pick up the
        // real-priced figure" -- advice that could never work.
        //
        // The gap it was hiding is not subtle: the season headline read
        // +21.86u while the zone table directly below it, which DID use
        // provenance, summed to -12.67u. Opposite signs, same bets, one
        // screen.
        const priceColD = side === "NRFI" ? r.market_nrfi_odds : r.market_yrfi_odds;
        if ((priceColD ?? "").trim()) {
          realDayPL.set(date, (realDayPL.get(date) ?? 0) + pl);
        }
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

  // THE REAL-MONEY CURVE.  Runs over the SAME date axis as
  // cumulativePL -- including days with no real-priced bet, which
  // contribute a flat 0 -- so the two series can be overlaid on one
  // chart without their x-axes drifting apart.
  const realPricedCumulativePL: { date: string; units: number }[] = [];
  let realCum = 0;
  for (const d of dates) {
    realCum += realDayPL.get(d) ?? 0;
    realPricedCumulativePL.push({ date: d, units: realCum });
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
    realPricedCumulativePL,
    stakeEpoch,
    kelly,
  };
}


// T-V21-LOCKIN-2026-05-06: removed loadV3Roi (Variant K shadow ROI).
// V2.1 is the only production model surfaced; the V3 history page and
// shadow ROI feed were archived along with the model toggle.

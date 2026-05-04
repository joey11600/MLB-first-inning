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
  /** zones we'd actually bet on (excludes PASS) */
  betZones:  ZoneRoi[];
  /** PASS zones (informational; never bet) */
  passZones: ZoneRoi[];
  /** aggregate over all bet-eligible picks (zones that aren't PASS) */
  total:     ZoneRoi;
  /** rolling cumulative P&L by date for the bet zones */
  cumulativePL: { date: string; units: number }[];
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

function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function isoMinusDays(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
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
  };
}

function finalize(z: ZoneRoi, plOverride?: number): ZoneRoi {
  const bets = z.wins + z.losses;
  const hitRate = bets > 0 ? z.wins / bets : NaN;
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
  const startDate =
    window === "today"
      ? today
      : window === "7d"
        ? isoMinusDays(7)
        : window === "30d"
          ? isoMinusDays(30)
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
    cumulativePL: [],
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

      // Prefer the realized profit_loss_units column (populated by
      // tracker._calc_pnl using actual market odds when imported, or
      // flat -110 as fallback).  Recompute from graded_result only when
      // the column is blank (legacy rows or pre-odds-system data).
      const plRaw = (r.profit_loss_units ?? "").trim();
      let pl = NaN;
      if (plRaw) {
        const parsed = Number.parseFloat(plRaw);
        if (Number.isFinite(parsed)) pl = parsed;
      }
      if (!Number.isFinite(pl)) {
        pl = graded === "WIN" ? DEFAULT_WIN_PROFIT_UNITS : DEFAULT_LOSS_UNITS;
      }

      // Track realized P&L at both zone (for breakdown) and day (for chart) level.
      const zKey = `${side}|${strength}`;
      zonePL.set(zKey, (zonePL.get(zKey) ?? 0) + pl);
      const prev = dayPL.get(date) ?? 0;
      dayPL.set(date, prev + pl);
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

  // Total = aggregate of all bet zones (sum realized P&L from each zone so
  // it matches the per-zone breakdown when actual odds are imported)
  const total = emptyZone("TOTAL", "NRFI", "STRONG");
  total.label = "TOTAL";
  let totalPL = 0;
  for (const z of betZones) {
    total.picks     += z.picks;
    total.wins      += z.wins;
    total.losses    += z.losses;
    total.postponed += z.postponed;
    total.ungraded  += z.ungraded;
    totalPL         += z.unitsPL;
  }
  const totalFinal = finalize(total, totalPL);

  // Cumulative P&L sorted by date
  const dates = Array.from(dayPL.keys()).sort();
  const cumulativePL: { date: string; units: number }[] = [];
  let cum = 0;
  for (const d of dates) {
    cum += dayPL.get(d) ?? 0;
    cumulativePL.push({ date: d, units: cum });
  }

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
    cumulativePL,
  };
}


// ===========================================================================
// loadV3Roi -- T3.23 server-side aggregator for the v3 calibrator (Variant K).
//
// Mirrors loadRoi's semantics exactly (same bucketing, finalize, totals,
// cumulative P&L) but reads from the Supabase pick_variants table where
// variant_name='K' instead of picks_<year>.csv.
//
// pick_variants is populated by tools/backfill_variants.py (T3.19) which
// recomputes raw P(NRFI) from the persisted feature vectors and applies
// the v3 calibrator (data/calibration_v3.json -- leak-free 2024+2025
// truepit corpus) to produce the v3 verdict.  Each row has its own
// graded_result + profit_loss_units derived from real DK odds at the
// v3-picked side, so the W/L counts and P&L numbers reflect the actual
// outcome v3 would have produced if it had been the production model.
//
// When Supabase isn't configured (local dev without env vars) this
// returns an empty RoiResponse rather than falling back to CSV --
// pick_variants only lives in Supabase, so there's no CSV to fall back
// to.  The frontend already renders empty zones gracefully.
// ===========================================================================

interface PickVariantRow {
  date:                string;
  game_pk:             string;
  pick_side:           string | null;
  pick_strength:       string | null;
  graded_result:       string | null;
  profit_loss_units:   number | null;
  would_be_units:      number | null;
}

function normalizeV3Side(s: string | null): PickSide {
  const up = (s ?? "").toUpperCase();
  if (up === "NRFI" || up === "YRFI") return up;
  return "PASS";
}

function normalizeV3Strength(s: string | null): PickStrength {
  const up = (s ?? "").toUpperCase();
  if (
    up === "STRONG" || up === "LEAN" ||
    up === "NO EDGE" || up === "NO DATA" ||
    up === "STARTER PENDING" || up === "LINEUP PENDING" ||
    up === "LOW LAMBDA"
  ) return up as PickStrength;
  return "NO EDGE";
}

export async function loadV3Roi(
  window: RoiWindow,
  refDateIso?: string,
): Promise<RoiResponse> {
  const today = (refDateIso || isoToday()).slice(0, 10);
  const startDate =
    window === "today"
      ? today
      : window === "7d"
        ? isoMinusDays(7)
        : window === "30d"
          ? isoMinusDays(30)
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
    cumulativePL: [],
  };

  const sb = getServerSupabase();
  if (!sb) return empty;

  // 10000-row limit safely covers a full season of v3 verdicts (~15
  // games * 200 days = 3000 rows) with headroom for doubleheaders.
  // Using gte/lte on date inclusively matches loadRoi's inclusive
  // [startDate, today] window.
  const { data, error } = await sb
    .from("pick_variants")
    .select("date, game_pk, pick_side, pick_strength, graded_result, profit_loss_units, would_be_units")
    .eq("variant_name", "K")
    .gte("date", startDate)
    .lte("date", today)
    .limit(10000);

  if (error || !data) return empty;

  const buckets = new Map<string, ZoneRoi>();
  const dayPL   = new Map<string, number>();
  const zonePL  = new Map<string, number>();
  let totalPicks  = 0;
  let gradedPicks = 0;
  const daysSet = new Set<string>();

  for (const r of data as PickVariantRow[]) {
    const date = (r.date ?? "").slice(0, 10);
    if (!date) continue;
    if (date < startDate || date > today) continue;

    const side     = normalizeV3Side(r.pick_side);
    const strength = normalizeV3Strength(r.pick_strength);
    const label    = zoneLabel(side, strength);
    const key      = `${side}|${strength}`;

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

      let pl: number = NaN;
      if (typeof r.profit_loss_units === "number" && Number.isFinite(r.profit_loss_units)) {
        pl = r.profit_loss_units;
      }
      if (!Number.isFinite(pl)) {
        pl = graded === "WIN" ? DEFAULT_WIN_PROFIT_UNITS : DEFAULT_LOSS_UNITS;
      }
      zonePL.set(key, (zonePL.get(key) ?? 0) + pl);
      const prev = dayPL.get(date) ?? 0;
      dayPL.set(date, prev + pl);
    } else if (graded === "POSTPONED" || graded === "SUSPENDED") {
      z.postponed += 1;
    } else if (graded === "PASS") {
      z.passes += 1;
      // Mirror loadRoi: seed zero-delta for all-PASS days so the
      // bankroll chart renders a flat point instead of a gap.
      if (!dayPL.has(date)) dayPL.set(date, 0);
    } else {
      z.ungraded += 1;
    }
  }

  // Same canonical bucket order as loadRoi.
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

  const total = emptyZone("TOTAL", "NRFI", "STRONG");
  total.label = "TOTAL";
  let totalPL = 0;
  for (const z of betZones) {
    total.picks     += z.picks;
    total.wins      += z.wins;
    total.losses    += z.losses;
    total.postponed += z.postponed;
    total.ungraded  += z.ungraded;
    totalPL         += z.unitsPL;
  }
  const totalFinal = finalize(total, totalPL);

  const dates = Array.from(dayPL.keys()).sort();
  const cumulativePL: { date: string; units: number }[] = [];
  let cum = 0;
  for (const d of dates) {
    cum += dayPL.get(d) ?? 0;
    cumulativePL.push({ date: d, units: cum });
  }

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
    cumulativePL,
  };
}

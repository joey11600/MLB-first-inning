/**
 * Aggregate W-L record and P&L from the season picks CSV.
 *
 * Phase 1: assumes a flat -110 line (the standard MLB juice baseline).  At
 * -110 you risk 1 unit to win 0.909, so:
 *   WIN  -> +0.909 units
 *   LOSS -> -1.000 units
 *   PASS / POSTPONED / ungraded -> 0 units (no bet placed)
 *
 * Break-even hit rate at -110 is 52.38%.  Anything above that is profit.
 *
 * Phase 2 (future) will read the per-row market_nrfi_odds / market_yrfi_odds
 * columns when populated and use the actual bet price instead of -110.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { parseCsv } from "./csv";
import type { PickSide, PickStrength } from "./types";

// ---------------------------------------------------------------------------
// Constants -- payout assumptions for Phase 1
// ---------------------------------------------------------------------------

export const DEFAULT_ODDS_AMERICAN = -110;
export const DEFAULT_WIN_PROFIT_UNITS = 100 / 110;       // = 0.9091
export const DEFAULT_LOSS_UNITS       = -1.0;
export const DEFAULT_BREAK_EVEN_RATE  = 110 / 210;       // = 0.5238

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type RoiWindow = "7d" | "30d" | "season";

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

function finalize(z: ZoneRoi): ZoneRoi {
  const bets = z.wins + z.losses;
  const hitRate = bets > 0 ? z.wins / bets : NaN;
  const unitsPL =
    z.wins * DEFAULT_WIN_PROFIT_UNITS + z.losses * DEFAULT_LOSS_UNITS;
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
  const startDate =
    window === "7d"
      ? isoMinusDays(7)
      : window === "30d"
        ? isoMinusDays(30)
        : `${today.slice(0, 4)}-01-01`;

  const empty: RoiResponse = {
    window,
    startDate,
    endDate: today,
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

  for (const r of rows) {
    const date = (r.date ?? "").slice(0, 10);
    if (!date) continue;
    if (date < startDate || date > today) continue;

    const sideRaw     = (r.pick_side     ?? "").toUpperCase();
    const strengthRaw = (r.pick_strength ?? "").toUpperCase();
    const side: PickSide =
      sideRaw === "NRFI" || sideRaw === "YRFI" ? sideRaw : "PASS";
    const strength: PickStrength =
      ["STRONG", "LEAN", "NO EDGE", "NO DATA", "STARTER PENDING"].includes(
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

    const graded = (r.graded_result ?? "").toUpperCase();
    if (graded === "WIN") {
      z.wins += 1;
      const prev = dayPL.get(date) ?? 0;
      dayPL.set(date, prev + DEFAULT_WIN_PROFIT_UNITS);
    } else if (graded === "LOSS") {
      z.losses += 1;
      const prev = dayPL.get(date) ?? 0;
      dayPL.set(date, prev + DEFAULT_LOSS_UNITS);
    } else if (graded === "POSTPONED" || graded === "SUSPENDED") {
      z.postponed += 1;
    } else if (graded === "PASS") {
      z.passes += 1;
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
    "YRFI|LEAN",
    "YRFI|STRONG",
  ];
  const finalized: ZoneRoi[] = [];
  for (const k of order) {
    const z = buckets.get(k);
    if (z) finalized.push(finalize(z));
  }
  // Append any remaining buckets in iteration order (defensive)
  for (const [k, z] of buckets.entries()) {
    if (!order.includes(k)) finalized.push(finalize(z));
  }

  const betZones  = finalized.filter((z) => z.side !== "PASS");
  const passZones = finalized.filter((z) => z.side === "PASS");

  // Total = aggregate of all bet zones
  const total = emptyZone("TOTAL", "NRFI", "STRONG");
  total.label = "TOTAL";
  for (const z of betZones) {
    total.picks     += z.picks;
    total.wins      += z.wins;
    total.losses    += z.losses;
    total.postponed += z.postponed;
    total.ungraded  += z.ungraded;
  }
  const totalFinal = finalize(total);

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
    betZones,
    passZones,
    total: totalFinal,
    cumulativePL,
  };
}

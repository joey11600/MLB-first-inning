/**
 * dashboard/lib/board-supabase.ts — Supabase read path for the board.
 *
 * Phase 2: returns the same BoardResponse shape that the CSV reader
 * (lib/board.ts) returns, but populated from the Supabase picks_<season>
 * + pick_changes tables instead of CSV files.  loadBoard() in board.ts
 * tries this first and falls back to the CSV path if Supabase is
 * unconfigured / unreachable / has no rows for the requested date.
 *
 * Behaviour parity with the CSV path:
 *   - rows are sorted by combined_lambda DESC so the highest-action
 *     games come first (mirrors the predictor's board CSV ordering),
 *     and the BoardRow.lambda value is read from combined_lambda
 *     (with lambda_lr_total fallback for legacy rows)
 *   - details map keys: game_pk (canonical), away@home#N (DH-aware
 *     fallback), away@home (legacy single-game fallback)
 *   - empty array for missing JSON columns (lineup, top factors)
 *   - thresholds are still loaded from data/thresholds.json (a tiny
 *     static file copied at build time) — not migrated to Supabase yet
 *
 * What's NOT here: the Realtime subscription.  That lives in the
 * browser hook (lib/useSupabaseRealtime.ts).  This module is purely
 * server-side; it answers a single GET /api/board request.
 */

import path from "node:path";
import fs from "node:fs/promises";
import { getServerSupabase } from "./supabase";
import type {
  BoardResponse,
  BoardRow,
  GameDetail,
  PickChange,
  PickSide,
  PickStrength,
  DataQuality,
  ActualSide,
  GradedResult,
  BatterLine,
  FactorContribution,
  PickThresholds,
} from "./types";


// ---------------------------------------------------------------------------
// Type helpers (mirror lib/board.ts exactly so the result is interchangeable)
// ---------------------------------------------------------------------------

function normalizePickSide(s: string | null | undefined): PickSide {
  const up = (s ?? "").toUpperCase();
  if (up === "NRFI" || up === "YRFI") return up;
  return "PASS";
}

function normalizePickStrength(s: string | null | undefined): PickStrength {
  const up = (s ?? "").toUpperCase();
  if (
    up === "STRONG" || up === "LEAN" ||
    up === "NO EDGE" || up === "NO DATA" ||
    up === "STARTER PENDING" || up === "LINEUP PENDING" ||
    up === "LOW LAMBDA" || up === "HIGH LAMBDA" || up === "FLAT ZONE"
  ) return up as PickStrength;
  return "NO EDGE";
}

function normalizeQuality(s: string | null | undefined): DataQuality {
  const v = (s ?? "").toLowerCase();
  if (v === "live" || v === "ltd" || v === "sm" || v === "avg") return v;
  return "";
}

function normalizeActualSide(s: string | null | undefined): ActualSide {
  const up = (s ?? "").toUpperCase();
  if (up === "NRFI" || up === "YRFI" || up === "POSTPONED" || up === "SUSPENDED") return up;
  return null;
}

function normalizeGradedResult(s: string | null | undefined): GradedResult {
  const up = (s ?? "").toUpperCase();
  if (up === "WIN" || up === "LOSS" || up === "PASS" || up === "POSTPONED" || up === "SUSPENDED") return up;
  return null;
}

function num(v: unknown): number {
  if (v === null || v === undefined || v === "") return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function nullableNum(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function str(v: unknown): string {
  return v == null ? "" : String(v);
}

/** Postgres JSONB columns come back as already-parsed JS values (lists
 *  for our schema).  Some legacy rows / csv-migrated rows may still have
 *  string-encoded JSON, so accept either. */
function toLineup(v: unknown): BatterLine[] {
  let arr: unknown[];
  if (Array.isArray(v)) arr = v;
  else if (typeof v === "string") {
    try { const p = JSON.parse(v); arr = Array.isArray(p) ? p : []; }
    catch { return []; }
  } else return [];
  return arr
    .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
    .slice(0, 3)
    .map((row) => {
      const bats = String(row.bats ?? "");
      const safeBats: BatterLine["bats"] =
        bats === "L" || bats === "R" || bats === "S" ? bats : "";
      return {
        id:   Number(row.id) || 0,
        name: typeof row.name === "string" ? row.name : "",
        bats: safeBats,
        obp:  typeof row.obp === "number" && Number.isFinite(row.obp) ? row.obp : null,
        slg:  typeof row.slg === "number" && Number.isFinite(row.slg) ? row.slg : null,
        iso:  typeof row.iso === "number" && Number.isFinite(row.iso) ? row.iso : null,
        ab:   typeof row.ab  === "number" && Number.isFinite(row.ab)  ? row.ab  : null,
      };
    });
}

function toFactors(v: unknown): FactorContribution[] {
  let arr: unknown[];
  if (Array.isArray(v)) arr = v;
  else if (typeof v === "string") {
    try { const p = JSON.parse(v); arr = Array.isArray(p) ? p : []; }
    catch { return []; }
  } else return [];
  return arr
    .filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null)
    .map((r) => ({
      name:         typeof r.name === "string" ? r.name : "",
      value:        typeof r.value === "number" && Number.isFinite(r.value) ? r.value : 0,
      contribution: typeof r.contribution === "number" && Number.isFinite(r.contribution) ? r.contribution : 0,
    }))
    .filter((f) => f.name.length > 0);
}


// ---------------------------------------------------------------------------
// Pick row -> { BoardRow, GameDetail }
// ---------------------------------------------------------------------------
//
// Each row of picks_<season> contains both:
//   - the data the dashboard needs to render the row "above the fold"
//     (BoardRow: rank, away, home, pickLabel, %s)
//   - the data the dashboard needs to render the expanded GameDetails
//     panel (GameDetail: pitcher / offense / lineup / weather / odds / ...)
//
// We split the same row into both shapes here.

interface PickRow {
  // Postgres returns column names exactly as defined in schema.sql.
  // Listing every column we touch keeps TS honest -- if a column is
  // dropped from the schema, this fails at compile time.
  [k: string]: unknown;
}

function rowToBoardRow(r: PickRow, rank: number): BoardRow {
  return {
    rank,
    away:           str(r.away_team),
    home:           str(r.home_team),
    // Parity with the CSV board path: the predictor writes
    // `combined_lambda` into the board CSV's "lambda" column for locked
    // rows (see mlb_first_inning_predictor.py: `lam_lk =
    // locked.get("combined_lambda")`).  The previous implementation
    // preferred lambda_lr_total here, so the live (Supabase) dashboard
    // disagreed with archived board CSVs and the CSV-fallback path.
    // Stick to combined_lambda; fall back to lambda_lr_total only for
    // legacy rows that pre-date combined_lambda.
    lambda:         num(r.combined_lambda ?? r.lambda_lr_total),
    pickSide:       normalizePickSide(str(r.pick_side)),
    pickStrength:   normalizePickStrength(str(r.pick_strength)),
    pickLabel:      str(r.pick_label),
    nrfiPct:        Math.round(num(r.nrfi_prob) * 1000) / 10,   // 0.582 -> 58.2
    yrfiPct:        Math.round(num(r.yrfi_prob) * 1000) / 10,
    gamePk:         str(r.game_pk),
    gameNumber:     Number(r.game_number) || 1,
    doubleHeader:   str(r.double_header) || "N",
    gameTimeEt:     str(r.game_time_et),
  };
}

function rowToGameDetail(r: PickRow): GameDetail {
  return {
    gamePk:        str(r.game_pk),
    gameTimeEt:    str(r.game_time_et),
    topFactorsT1:  toFactors(r.top_factors_t1_json),
    topFactorsB1:  toFactors(r.top_factors_b1_json),
    parkFactor:    num(r.park_factor),
    awayProj:      num(r.away_proj_runs),
    homeProj:      num(r.home_proj_runs),
    combinedLambda:num(r.combined_lambda),
    lambdaLrT1:    num(r.lambda_lr_t1),
    lambdaLrB1:    num(r.lambda_lr_b1),
    lambdaLrTotal: num(r.lambda_lr_total),
    overProb:      num(r.over_1_5_prob),
    underProb:     num(r.under_1_5_prob),
    blendedInputs: num(r.blended_inputs),
    actualSide:    normalizeActualSide(str(r.actual_result)),
    gradedResult:  normalizeGradedResult(str(r.graded_result)),
    fiAwayRuns:    nullableNum(r.fi_away_runs),
    fiHomeRuns:    nullableNum(r.fi_home_runs),
    fiTotalRuns:   nullableNum(r.fi_total_runs),
    away: {
      team: str(r.away_team),
      pitcher: {
        name:    str(r.away_pitcher) || "TBD",
        mlbId:   nullableNum(r.away_pitcher_id),
        era:     num(r.away_era),
        whip:    num(r.away_whip),
        fip:     num(r.away_fip),
        bb9:     num(r.away_bb9),
        hr9:     num(r.away_hr9),
        k9:      num(r.away_k9),
        quality: normalizeQuality(str(r.away_pitcher_q)),
      },
      offense: {
        obp:     num(r.away_obp),
        slg:     num(r.away_slg),
        rpg:     num(r.away_rpg),
        quality: normalizeQuality(str(r.away_batting_q)),
      },
      lineup: toLineup(r.away_lineup_json),
    },
    home: {
      team: str(r.home_team),
      pitcher: {
        name:    str(r.home_pitcher) || "TBD",
        mlbId:   nullableNum(r.home_pitcher_id),
        era:     num(r.home_era),
        whip:    num(r.home_whip),
        fip:     num(r.home_fip),
        bb9:     num(r.home_bb9),
        hr9:     num(r.home_hr9),
        k9:      num(r.home_k9),
        quality: normalizeQuality(str(r.home_pitcher_q)),
      },
      offense: {
        obp:     num(r.home_obp),
        slg:     num(r.home_slg),
        rpg:     num(r.home_rpg),
        quality: normalizeQuality(str(r.home_batting_q)),
      },
      lineup: toLineup(r.home_lineup_json),
    },
    marketNrfiOdds:  str(r.market_nrfi_odds),
    marketYrfiOdds:  str(r.market_yrfi_odds),
    sportsbook:      str(r.sportsbook),
    oddsCapturedAt:  str(r.odds_captured_at),
    edgeOnPick:      num(r.edge_on_pick),
    betPlaced:       (() => {
      const up = str(r.bet_placed).trim().toUpperCase();
      return up === "Y" ? "Y" : up === "N" ? "N" : "";
    })() as "" | "Y" | "N",
    unitsRisked:     num(r.units_risked),
    profitLossUnits: num(r.profit_loss_units),
    // T2.54: opened odds + CLV for line-drift chip
    openedNrfiOdds:   str(r.opened_nrfi_odds),
    openedYrfiOdds:   str(r.opened_yrfi_odds),
    openedCapturedAt: str(r.opened_captured_at),
    clvPct:           num(r.clv_pct),
  };
}


// ---------------------------------------------------------------------------
// Available dates list
// ---------------------------------------------------------------------------

async function listAvailableDates(season: number): Promise<string[]> {
  const sb = getServerSupabase();
  if (!sb) return [];
  // Distinct dates in the season, newest first.  500-row safety cap is
  // well above a full MLB season's slate count.
  const { data, error } = await sb
    .from(`picks_${season}`)
    .select("date")
    .order("date", { ascending: false })
    .limit(500);
  if (error || !data) return [];
  // Postgres returns a row per CSV row so we have to dedupe + sort.
  const seen = new Set<string>();
  const out: string[] = [];
  for (const row of data as { date: string }[]) {
    const d = (row.date || "").slice(0, 10);
    if (d && !seen.has(d)) {
      seen.add(d);
      out.push(d);
    }
  }
  return out;
}


// ---------------------------------------------------------------------------
// Pick-change journal
// ---------------------------------------------------------------------------

async function loadPickChanges(iso: string): Promise<PickChange[]> {
  const sb = getServerSupabase();
  if (!sb) return [];
  const { data, error } = await sb
    .from("pick_changes")
    .select("captured_at_utc, date, game_pk, away_team, home_team, game_time_et, old_pick_label, new_pick_label")
    .eq("date", iso)
    .order("captured_at_utc", { ascending: false })
    .limit(200);
  if (error || !data) return [];
  return (data as PickRow[]).map((r) => ({
    capturedAtUtc: str(r.captured_at_utc),
    date:          str(r.date).slice(0, 10),
    gamePk:        str(r.game_pk),
    awayTeam:      str(r.away_team),
    homeTeam:      str(r.home_team),
    gameTimeEt:    str(r.game_time_et),
    oldPickLabel:  str(r.old_pick_label),
    newPickLabel:  str(r.new_pick_label),
  }));
}


// ---------------------------------------------------------------------------
// Thresholds (still file-based; tiny static JSON)
// ---------------------------------------------------------------------------

async function loadThresholds(): Promise<PickThresholds | undefined> {
  // Same fallback path as the CSV reader — the file lives in /data
  // (or ../data) depending on whether copy-data.mjs has run.
  const candidates = [
    path.resolve(process.cwd(), "data", "thresholds.json"),
    path.resolve(process.cwd(), "..", "data", "thresholds.json"),
  ];
  for (const p of candidates) {
    try {
      const raw = await fs.readFile(p, "utf8");
      const obj = JSON.parse(raw) as Record<string, unknown>;
      const n = (v: unknown): number | null =>
        typeof v === "number" && Number.isFinite(v) ? v : null;
      const t = {
        strongNrfiP:     n(obj.strongNrfiP),
        leanNrfiP:       n(obj.leanNrfiP),
        passLoP:         n(obj.passLoP),
        leanYrfiP:       n(obj.leanYrfiP),
        lambdaYrfiFloor: n(obj.lambdaYrfiFloor),
      };
      if (Object.values(t).some((v) => v == null)) continue;
      // lambdaNrfiCeiling is optional (older deploys omit it); attach
      // only when present so its absence never rejects the core five.
      const ceiling = n(obj.lambdaNrfiCeiling);
      return {
        ...t,
        ...(ceiling != null ? { lambdaNrfiCeiling: ceiling } : {}),
      } as PickThresholds;
    } catch { /* try next */ }
  }
  return undefined;
}


// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/** Try to load the board from Supabase.  Returns null when:
 *    - Supabase isn't configured (env vars missing)
 *    - the network call fails
 *    - the requested date has zero rows in Supabase yet
 *      (e.g. today's slate before the next cron firing populates it)
 *  Caller should fall back to the CSV path in those cases. */
export async function loadBoardFromSupabase(
  requestedIso: string | null,
  season: number = 2026,
): Promise<BoardResponse | null> {
  const sb = getServerSupabase();
  if (!sb) return null;

  // Resolve the slate date.  If the caller didn't request one, pick the
  // most recent date that has rows.
  let available: string[];
  let iso: string;
  try {
    available = await listAvailableDates(season);
  } catch {
    return null;
  }
  if (available.length === 0) return null;

  iso = requestedIso && available.includes(requestedIso)
    ? requestedIso
    : available[0];

  // Pull every pick row for the slate.  Order by combined_lambda DESC so
  // the row order matches the predictor's board.csv "rank" output (which
  // also uses combined_lambda for the lambda column on locked rows).
  // Previous code ordered by lambda_lr_total which produced different
  // ranks on slates where combined_lambda and lambda_lr_total diverge.
  const { data, error } = await sb
    .from(`picks_${season}`)
    .select("*")
    .eq("date", iso)
    .order("combined_lambda", { ascending: false, nullsFirst: false });
  if (error) {
    console.warn("[supabase] picks select failed", error);
    return null;
  }
  if (!data || data.length === 0) {
    // This date has no rows in Supabase yet — caller should fall back
    // to the CSV path which may have it.
    return null;
  }

  const pickRows = data as PickRow[];

  // T-V21-LOCKIN-2026-05-06: removed Variant K (v3 calibrator shadow)
  // splice.  V2.1 is the only production model surfaced; the V3 toggle
  // and shadow data flow were archived.

  // Build BoardRow + GameDetail in one pass.  rank is the 1-based index
  // after the SQL ordering above (matches what board.csv encodes).
  const boardRows: BoardRow[] = [];
  const details:   Record<string, GameDetail> = {};
  pickRows.forEach((r, idx) => {
    const baseRow    = rowToBoardRow(r, idx + 1);
    const baseDetail = rowToGameDetail(r);
    const pk         = str(r.game_pk);

    boardRows.push(baseRow);
    const teamKey = `${str(r.away_team)}@${str(r.home_team)}`;
    const dhKey   = `${teamKey}#${Number(r.game_number) || 1}`;
    if (pk) details[pk] = baseDetail;
    details[dhKey] = baseDetail;
    if (!(teamKey in details)) details[teamKey] = baseDetail;
  });

  // Use the most-recently-updated row's updated_at as the "generated at"
  // timestamp — closest analogue to the CSV path's mtime, and tells the
  // dashboard's heartbeat indicator when fresh data last landed.
  const generatedAt = pickRows.reduce<string>((max, r) => {
    const t = str(r.updated_at) || str(r.created_at);
    return t > max ? t : max;
  }, "") || null;

  const [pickChanges, thresholds] = await Promise.all([
    loadPickChanges(iso),
    loadThresholds(),
  ]);

  return {
    date: iso,
    availableDates: available,
    rows: boardRows,
    details,
    generatedAt,
    pickChanges,
    thresholds,
  };
}

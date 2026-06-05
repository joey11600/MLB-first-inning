import fs from "node:fs/promises";
import path from "node:path";
import { parseCsv, toNumber } from "./csv";
import { loadBoardFromSupabase } from "./board-supabase";
import { isSupabaseConfigured } from "./supabase";
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
} from "./types";

/**
 * Resolve the repo-level `data/` directory relative to the dashboard.
 * Dashboard lives at /dashboard, CSVs at /data/boards and /data/picks_*.csv.
 * On Vercel, scripts/copy-data.mjs mirrors ../data into ./data before build
 * so the CSVs ship with the function bundle. Locally we still read ../data
 * so regenerating a board is immediately visible without a rebuild.
 */
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

async function listBoardDates(): Promise<string[]> {
  const dir = path.join(dataDir(), "boards");
  let files: string[] = [];
  try {
    files = await fs.readdir(dir);
  } catch {
    return [];
  }
  const iso: string[] = [];
  for (const f of files) {
    const m = f.match(/^board_(\d{4})_(\d{2})_(\d{2})\.csv$/);
    if (m) iso.push(`${m[1]}-${m[2]}-${m[3]}`);
  }
  iso.sort().reverse();
  return iso;
}

function isoToBoardFilename(iso: string): string {
  return `board_${iso.replace(/-/g, "_")}.csv`;
}

function normalizePickSide(s: string): PickSide {
  const up = s.toUpperCase();
  if (up === "NRFI" || up === "YRFI") return up;
  return "PASS";
}

function normalizePickStrength(s: string): PickStrength {
  const up = s.toUpperCase();
  if (
    up === "STRONG" ||
    up === "LEAN" ||
    up === "NO EDGE" ||
    up === "NO DATA" ||
    up === "STARTER PENDING" ||
    up === "LINEUP PENDING" ||
    up === "LOW LAMBDA" ||
    up === "HIGH LAMBDA" ||
    up === "FLAT ZONE"
  )
    return up as PickStrength;
  return "NO EDGE";
}

function normalizeQuality(s: string | undefined): DataQuality {
  const v = (s ?? "").toLowerCase();
  if (v === "live" || v === "ltd" || v === "sm" || v === "avg") return v;
  return "";
}

function normalizeActualSide(s: string | undefined): ActualSide {
  const up = (s ?? "").toUpperCase();
  if (up === "NRFI" || up === "YRFI" || up === "POSTPONED" || up === "SUSPENDED") return up;
  return null;
}

function normalizeGradedResult(s: string | undefined): GradedResult {
  const up = (s ?? "").toUpperCase();
  if (up === "WIN" || up === "LOSS" || up === "PASS" || up === "POSTPONED" || up === "SUSPENDED") return up;
  return null;
}

function nullableNumber(s: string | undefined): number | null {
  if (s == null || s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Parse a JSON-encoded list of feature contributions (T4.15).  Each
 *  entry: {name, value, contribution}.  Returns [] for empty/invalid. */
function parseFactorsJson(s: string | undefined): import("./types").FactorContribution[] {
  const txt = (s ?? "").trim();
  if (!txt || txt === "[]") return [];
  try {
    const parsed = JSON.parse(txt) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
      .map((row) => ({
        name:         typeof row.name === "string" ? row.name : "",
        value:        typeof row.value === "number" && Number.isFinite(row.value) ? row.value : 0,
        contribution: typeof row.contribution === "number" && Number.isFinite(row.contribution) ? row.contribution : 0,
      }))
      .filter(f => f.name.length > 0);
  } catch {
    return [];
  }
}


/** Parse a JSON-encoded list of top-3 batters out of a CSV cell.
 *  Returns [] for blank/invalid cells (e.g. older rows without the column,
 *  or pre-lineup snapshots).  Each entry is normalized so optional stat
 *  fields read consistently as `null` instead of `undefined`. */
function parseLineupJson(s: string | undefined): BatterLine[] {
  const txt = (s ?? "").trim();
  if (!txt || txt === "[]") return [];
  try {
    const parsed = JSON.parse(txt) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
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
  } catch {
    return [];
  }
}

/**
 * Build detail map from the season picks CSV for a given date.
 * The picks CSV has the same schema as tracker.FIELDS.
 */
async function loadDetails(iso: string): Promise<Record<string, GameDetail>> {
  const year = iso.slice(0, 4);
  const picksPath = path.join(dataDir(), `picks_${year}.csv`);
  const raw = await safeRead(picksPath);
  if (!raw) return {};
  const rows = parseCsv(raw);

  // Key by game_pk (uniquely identifies a game, including doubleheader split).
  // Also fall back to "${away}@${home}" so older board CSVs without gamePk
  // can still find their detail row.
  const out: Record<string, GameDetail> = {};
  for (const r of rows) {
    if (r.date !== iso) continue;
    const pk      = r.game_pk ?? "";
    const teamKey = `${r.away_team}@${r.home_team}`;
    const detail = {
      gamePk: r.game_pk ?? "",
      gameTimeEt: r.game_time_et ?? "",
      // T4.15: top contributing LR features per half
      topFactorsT1: parseFactorsJson(r.top_factors_t1_json),
      topFactorsB1: parseFactorsJson(r.top_factors_b1_json),
      parkFactor: toNumber(r.park_factor),
      awayProj: toNumber(r.away_proj_runs),
      homeProj: toNumber(r.home_proj_runs),
      combinedLambda: toNumber(r.combined_lambda),
      lambdaLrT1:    toNumber(r.lambda_lr_t1),
      lambdaLrB1:    toNumber(r.lambda_lr_b1),
      lambdaLrTotal: toNumber(r.lambda_lr_total),
      overProb: toNumber(r.over_1_5_prob),
      underProb: toNumber(r.under_1_5_prob),
      blendedInputs: toNumber(r.blended_inputs),
      actualSide:    normalizeActualSide(r.actual_result),
      gradedResult:  normalizeGradedResult(r.graded_result),
      fiAwayRuns:    nullableNumber(r.fi_away_runs),
      fiHomeRuns:    nullableNumber(r.fi_home_runs),
      fiTotalRuns:   nullableNumber(r.fi_total_runs),
      away: {
        team: r.away_team,
        pitcher: {
          name: r.away_pitcher || "TBD",
          mlbId: nullableNumber(r.away_pitcher_id),
          era: toNumber(r.away_era),
          whip: toNumber(r.away_whip),
          fip: toNumber(r.away_fip),
          bb9: toNumber(r.away_bb9),
          hr9: toNumber(r.away_hr9),
          k9: toNumber(r.away_k9),
          quality: normalizeQuality(r.away_pitcher_q),
        },
        offense: {
          obp: toNumber(r.away_obp),
          slg: toNumber(r.away_slg),
          rpg: toNumber(r.away_rpg),
          quality: normalizeQuality(r.away_batting_q),
        },
        // Top-3 of the away team's batting order (the lineup the HOME
        // pitcher faces in T1).  Empty array when lineup hasn't posted.
        lineup: parseLineupJson(r.away_lineup_json),
      },
      // Live odds + edge (populated by tracker.import_odds; blank when no odds yet)
      marketNrfiOdds: r.market_nrfi_odds ?? "",
      marketYrfiOdds: r.market_yrfi_odds ?? "",
      sportsbook:     r.sportsbook ?? "",
      oddsCapturedAt: r.odds_captured_at ?? "",
      edgeOnPick:     toNumber(r.edge_on_pick),
      betPlaced:      ((r.bet_placed ?? "").trim().toUpperCase() === "Y" ? "Y"
                      : (r.bet_placed ?? "").trim().toUpperCase() === "N" ? "N"
                      : "") as "" | "Y" | "N",
      unitsRisked:    toNumber(r.units_risked),
      profitLossUnits: toNumber(r.profit_loss_units),
      // T2.54: opened odds + CLV for line-drift display
      openedNrfiOdds:   r.opened_nrfi_odds ?? "",
      openedYrfiOdds:   r.opened_yrfi_odds ?? "",
      openedCapturedAt: r.opened_captured_at ?? "",
      clvPct:           toNumber(r.clv_pct),
      home: {
        team: r.home_team,
        pitcher: {
          name: r.home_pitcher || "TBD",
          mlbId: nullableNumber(r.home_pitcher_id),
          era: toNumber(r.home_era),
          whip: toNumber(r.home_whip),
          fip: toNumber(r.home_fip),
          bb9: toNumber(r.home_bb9),
          hr9: toNumber(r.home_hr9),
          k9: toNumber(r.home_k9),
          quality: normalizeQuality(r.home_pitcher_q),
        },
        offense: {
          obp: toNumber(r.home_obp),
          slg: toNumber(r.home_slg),
          rpg: toNumber(r.home_rpg),
          quality: normalizeQuality(r.home_batting_q),
        },
        // Top-3 of the home team's batting order (the lineup the AWAY
        // pitcher faces in B1).  Empty array when lineup hasn't posted.
        lineup: parseLineupJson(r.home_lineup_json),
      },
    };
    // Primary key: game_pk (uniquely identifies even doubleheader splits)
    if (pk) out[pk] = detail;
    // Compatibility key for old board CSVs without game_pk.  We DH-disambiguate
    // by appending the game_number so DH-1 and DH-2 don't collide; previously
    // the second insert was gated by `if (!(teamKey in out))` which silently
    // dropped DH-2's detail and rendered DH-2 with DH-1's pitcher / lineup.
    const gameNum  = Number(r.game_number) || 1;
    const dhKey    = `${r.away_team}@${r.home_team}#${gameNum}`;
    out[dhKey]     = detail;
    // Single-game key only set when no DH collision (one-game days).
    if (!(teamKey in out)) out[teamKey] = detail;
  }
  return out;
}

export async function loadBoard(requestedIso: string | null): Promise<BoardResponse> {
  // T2.31 — Phase 2 read-side cutover.  Try Supabase first; fall back
  // to CSV reads when Supabase is unconfigured / unreachable / has no
  // rows yet for the requested date (e.g. today's slate before the
  // first cron run after secrets land).  This means:
  //   - Local dev w/o env vars:           CSV (unchanged)
  //   - Production w/ Supabase configured: Supabase for any date
  //     migrated or written by the dual-write; CSV otherwise
  //   - Today's slate before next cron:   CSV (graceful)
  //
  // Failures are silent — the function just falls through to CSV so a
  // Supabase outage downgrades to "as fresh as the last Vercel build"
  // rather than serving an error.  Same blast-radius as a stale CSV.
  if (isSupabaseConfigured()) {
    try {
      const sb = await loadBoardFromSupabase(requestedIso);
      if (sb && sb.rows.length > 0) {
        // T2.31: merge availableDates from BOTH Supabase and the
        // local CSVs so the date picker still includes any dates
        // that exist on disk but haven't been mirrored yet (rare;
        // mostly a Phase-1.5 transition concern).
        const csvDates = await listBoardDates();
        const seen = new Set<string>(sb.availableDates);
        const merged = [...sb.availableDates];
        for (const d of csvDates) {
          if (!seen.has(d)) {
            merged.push(d);
            seen.add(d);
          }
        }
        merged.sort().reverse();
        return { ...sb, availableDates: merged };
      }
    } catch (err) {
      // Log + fall through.  Matches the existing CSV path's "best
      // effort" stance — never let the dashboard break on a data-
      // layer hiccup.
      console.warn("[board] Supabase read failed, falling back to CSV:", err);
    }
  }

  const available = await listBoardDates();
  const iso = requestedIso && available.includes(requestedIso)
    ? requestedIso
    : available[0] ?? "";

  const empty: BoardResponse = {
    date: iso,
    availableDates: available,
    rows: [],
    details: {},
    generatedAt: null,
    pickChanges: [],
  };

  if (!iso) return empty;

  const boardPath = path.join(dataDir(), "boards", isoToBoardFilename(iso));
  const raw = await safeRead(boardPath);
  if (!raw) return empty;

  const rows = parseCsv(raw);
  const parsed: BoardRow[] = rows.map((r) => ({
    rank: Number(r.rank) || 0,
    away: r.away,
    home: r.home,
    lambda: Number(r.lambda) || 0,
    lambdaLrTotal: toNumber(r.lambda_lr_total),
    // CSV board snapshots don't carry weather columns, so we can't recompute
    // the weather-adjusted floor here; fall back to the base. The live
    // (Supabase) path computes the true per-game floor. Base must match
    // mlb_first_inning_predictor.py `_LR_LAMBDA_YRFI_FLOOR`.
    yrfiFloorUsed: 0.838,
    pickSide: normalizePickSide(r.pick_side),
    pickStrength: normalizePickStrength(r.pick_strength),
    pickLabel: r.pick_label,
    nrfiPct: Number(r.nrfi_pct) || 0,
    yrfiPct: Number(r.yrfi_pct) || 0,
    // Doubleheader fields (empty for pre-fix board CSVs)
    gamePk:       r.game_pk ?? "",
    gameNumber:   Number(r.game_number) || 1,
    doubleHeader: r.double_header ?? "N",
    gameTimeEt:   r.game_time_et ?? "",
  }));

  let generatedAt: string | null = null;
  try {
    const stat = await fs.stat(boardPath);
    generatedAt = stat.mtime.toISOString();
  } catch {
    /* ignore */
  }

  const details = await loadDetails(iso);
  const pickChanges = await loadPickChanges(iso);
  const thresholds = await loadThresholds();

  return {
    date: iso,
    availableDates: available,
    rows: parsed,
    details,
    generatedAt,
    pickChanges,
    thresholds,
  };
}


/** Read data/thresholds.json so the dashboard's TS tentative-classifier
 *  reads from the Python source of truth rather than duplicating
 *  hardcoded constants.  Returns undefined if the file is missing or
 *  malformed -- TentativeChip falls back to its own defaults. */
async function loadThresholds(): Promise<import("./types").PickThresholds | undefined> {
  const p = path.join(dataDir(), "thresholds.json");
  const raw = await safeRead(p);
  if (!raw) return undefined;
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    const num = (v: unknown): number | null =>
      typeof v === "number" && Number.isFinite(v) ? v : null;
    const t = {
      strongNrfiP:     num(obj.strongNrfiP),
      leanNrfiP:       num(obj.leanNrfiP),
      passLoP:         num(obj.passLoP),
      leanYrfiP:       num(obj.leanYrfiP),
      lambdaYrfiFloor: num(obj.lambdaYrfiFloor),
    };
    // The five core fields must be present and numeric or we treat as
    // missing.  lambdaNrfiCeiling is OPTIONAL (older deploys omit it) --
    // add it separately so its absence never invalidates the core five.
    if (Object.values(t).some((v) => v == null)) return undefined;
    const ceiling = num(obj.lambdaNrfiCeiling);
    return {
      ...t,
      ...(ceiling != null ? { lambdaNrfiCeiling: ceiling } : {}),
    } as import("./types").PickThresholds;
  } catch {
    return undefined;
  }
}


/* -------------------------------------------------------------------------
   Pick-change journal (data/pick_changes.csv)

   The predictor's tracker.log_picks() appends a row every time an intraday
   refresh flips a pre-game pick (e.g. STARTER PENDING -> STRONG YRFI as
   lineups post, or STRONG YRFI -> PASS as the new lambda floor demotes a
   borderline call).  Surfaced on the dashboard above the board so the
   user knows when something they were watching has changed.
   ------------------------------------------------------------------------- */

async function loadPickChanges(iso: string): Promise<PickChange[]> {
  const p = path.join(dataDir(), "pick_changes.csv");
  const raw = await safeRead(p);
  if (!raw) return [];
  const all = parseCsv(raw);
  // Keep only changes for the displayed slate date, newest first.
  return all
    .filter((r) => (r.date ?? "").slice(0, 10) === iso)
    .map((r) => ({
      capturedAtUtc: (r.captured_at_utc ?? "").trim(),
      date:          (r.date ?? "").slice(0, 10),
      gamePk:        (r.game_pk ?? "").trim(),
      awayTeam:      (r.away_team ?? "").trim(),
      homeTeam:      (r.home_team ?? "").trim(),
      gameTimeEt:    (r.game_time_et ?? "").trim(),
      oldPickLabel:  (r.old_pick_label ?? "").trim(),
      newPickLabel:  (r.new_pick_label ?? "").trim(),
    }))
    .sort((a, b) => b.capturedAtUtc.localeCompare(a.capturedAtUtc));
}

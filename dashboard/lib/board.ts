import fs from "node:fs/promises";
import path from "node:path";
import { parseCsv, toNumber } from "./csv";
import type {
  BoardResponse,
  BoardRow,
  GameDetail,
  PickSide,
  PickStrength,
  DataQuality,
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
  if (up === "STRONG" || up === "LEAN" || up === "NO EDGE" || up === "NO DATA")
    return up as PickStrength;
  return "NO EDGE";
}

function normalizeQuality(s: string | undefined): DataQuality {
  const v = (s ?? "").toLowerCase();
  if (v === "live" || v === "ltd" || v === "sm" || v === "avg") return v;
  return "";
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

  const out: Record<string, GameDetail> = {};
  for (const r of rows) {
    if (r.date !== iso) continue;
    const key = `${r.away_team}@${r.home_team}`;
    out[key] = {
      gamePk: r.game_pk ?? "",
      gameTimeEt: r.game_time_et ?? "",
      parkFactor: toNumber(r.park_factor),
      awayProj: toNumber(r.away_proj_runs),
      homeProj: toNumber(r.home_proj_runs),
      combinedLambda: toNumber(r.combined_lambda),
      overProb: toNumber(r.over_1_5_prob),
      underProb: toNumber(r.under_1_5_prob),
      blendedInputs: toNumber(r.blended_inputs),
      away: {
        team: r.away_team,
        pitcher: {
          name: r.away_pitcher || "TBD",
          era: toNumber(r.away_era),
          whip: toNumber(r.away_whip),
          fip: toNumber(r.away_fip),
          bb9: toNumber(r.away_bb9),
          hr9: toNumber(r.away_hr9),
          k9: toNumber(r.away_k9),
          fiEra: toNumber(r.away_fi_era),
          fiWhip: toNumber(r.away_fi_whip),
          fiIp: toNumber(r.away_fi_ip),
          quality: normalizeQuality(r.away_pitcher_q),
        },
        offense: {
          obp: toNumber(r.away_obp),
          slg: toNumber(r.away_slg),
          rpg: toNumber(r.away_rpg),
          quality: normalizeQuality(r.away_batting_q),
        },
      },
      home: {
        team: r.home_team,
        pitcher: {
          name: r.home_pitcher || "TBD",
          era: toNumber(r.home_era),
          whip: toNumber(r.home_whip),
          fip: toNumber(r.home_fip),
          bb9: toNumber(r.home_bb9),
          hr9: toNumber(r.home_hr9),
          k9: toNumber(r.home_k9),
          fiEra: toNumber(r.home_fi_era),
          fiWhip: toNumber(r.home_fi_whip),
          fiIp: toNumber(r.home_fi_ip),
          quality: normalizeQuality(r.home_pitcher_q),
        },
        offense: {
          obp: toNumber(r.home_obp),
          slg: toNumber(r.home_slg),
          rpg: toNumber(r.home_rpg),
          quality: normalizeQuality(r.home_batting_q),
        },
      },
    };
  }
  return out;
}

export async function loadBoard(requestedIso: string | null): Promise<BoardResponse> {
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
    pickSide: normalizePickSide(r.pick_side),
    pickStrength: normalizePickStrength(r.pick_strength),
    pickLabel: r.pick_label,
    nrfiPct: Number(r.nrfi_pct) || 0,
    yrfiPct: Number(r.yrfi_pct) || 0,
  }));

  let generatedAt: string | null = null;
  try {
    const stat = await fs.stat(boardPath);
    generatedAt = stat.mtime.toISOString();
  } catch {
    /* ignore */
  }

  const details = await loadDetails(iso);

  return {
    date: iso,
    availableDates: available,
    rows: parsed,
    details,
    generatedAt,
  };
}
